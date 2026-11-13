// src/pages/QaPage.tsx - 本体问答独立页面（R8：docs/design/05 §6 / 07 §4）
// 左侧：新建对话 + 历史对话列表（按会话分组）；右侧：项目/模型选择 + 流式对话 + 引用溯源。
// 与 QaDrawer 共用 MarkdownAnswer（[n] 角标点击定位）与 SourceCard。
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { message } from '../shared/feedback';
import { Button, Input, Popconfirm, Popover, Select, Tag, Tooltip } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { QA_LAST_PROJECT_ID_KEY } from '../shared/keys';
import BrandMark from '../components/BrandMark';
import Navbar from '../components/Layout/Navbar';
import { projectStatusMeta } from '../shared/projectStatus';
import {
    ArrowRightOutlined,
    BulbOutlined,
    DeleteOutlined,
    ExclamationCircleOutlined,
    FileSearchOutlined,
    LoadingOutlined,
    MessageOutlined,
    PlusOutlined,
    SendOutlined,
    StopOutlined,
} from '@ant-design/icons';
import {
    askQuestionStream,
    deleteQaConversation,
    getQaConversation,
    listQaConversations,
    listQaModels,
    type QaConversationItem,
    type QaDonePayload,
    type QaModelItem,
    type QaSource,
} from '../api/qa';
import { projectsApi } from '../api/projects';
import type { ProjectData } from '../types/ontology';
import MarkdownAnswer from '../features/qa/MarkdownAnswer';
import SourceCard from '../features/qa/SourceCard';
import { uuid } from '../utils/uuid';

interface QaTurn {
    question: string;
    answer: string;
    sources: QaSource[];
    done?: QaDonePayload;
    error?: string;
    streaming: boolean;
    /** 用户手动停止生成（保留已产出的部分内容） */
    stopped?: boolean;
}

/** 会话列表时间：今天/昨天显示时刻，更早只显示日期，避免每行都挤一长串时间戳 */
function formatConvTime(s?: string | null): string {
    if (!s) return '';
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s.slice(0, 16).replace('T', ' ');
    const pad = (v: number) => String(v).padStart(2, '0');
    const hhmm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return `今天 ${hhmm}`;
    const yest = new Date(now);
    yest.setDate(now.getDate() - 1);
    if (d.toDateString() === yest.toDateString()) return `昨天 ${hhmm}`;
    const md = `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}-${md}`;
}

/** 空对话时的示例问题：点击直接填入输入框，降低首次使用的空白感 */
const SAMPLE_QUESTIONS = [
    '这个项目里有哪些核心实体和关系？',
    '梳理主要概念之间的层级结构',
    '图谱中有哪些孤立节点或缺失关系？',
];

const CONV_ID_KEY = QA_LAST_PROJECT_ID_KEY;

// 会话列表模块级缓存（按项目隔离）：进入页面先用上次的列表立即渲染、后台静默刷新，
// 返回再进入时左侧完全没有加载痕迹；首次进入则在数据回来前留白。删除/新会话后随刷新更新。
let convCache: Record<number, QaConversationItem[]> = {};

export default function QaPage() {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    // 项目列表未回来前不展示「还没有可用项目」，否则空状态先错后对、整体跳一下
    const [projectsLoading, setProjectsLoading] = useState(true);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [models, setModels] = useState<QaModelItem[]>([]);
    const [modelId, setModelId] = useState<number | null>(null);

    const [conversations, setConversations] = useState<QaConversationItem[]>([]);
    // 会话列表是否已就绪：未就绪时列表区留白（不画骨架/空状态），就绪后才下「暂无」的结论
    const [convsReady, setConvsReady] = useState(false);
    // 切项目时作废旧请求的响应，防止慢请求回来覆盖新项目的列表
    const convSeqRef = useRef(0);
    const [activeConvId, setActiveConvId] = useState<string | null>(null);

    const [turns, setTurns] = useState<QaTurn[]>([]);
    const [question, setQuestion] = useState('');
    const [busy, setBusy] = useState(false);
    // 引用角标按「第几轮 + 第几条」定位：溯源卡 id 跨轮次不能重复
    const [highlightSource, setHighlightSource] = useState<{ turn: number; n: number } | null>(null);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    // 流式 token 渲染节流：SSE 可能一次推来一大段，逐事件 setState 会让 ReactMarkdown
    // 整篇重解析 + 布局重排，文字呈现「一蹦一蹦」；这里先攒进缓冲区，每 50ms 合并刷新一次
    const pendingTokensRef = useRef('');
    const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // 对话滚动区边缘渐隐：滚到中间时上下内容不直接切在页头/输入卡的边界上
    const listRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<TextAreaRef>(null);
    // 只有视图仍贴在底部时才跟随流式输出下滚；用户往上翻历史时不抢滚动
    const stickBottomRef = useRef(true);
    const [edgeFade, setEdgeFade] = useState({ top: false, bottom: false });
    const updateEdgeFade = useCallback(() => {
        const el = listRef.current;
        if (!el) return;
        stickBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        const top = el.scrollTop > 2;
        const bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
        setEdgeFade((prev) => (prev.top === top && prev.bottom === bottom ? prev : { top, bottom }));
    }, []);

    /** 把缓冲区里攒下的 token 合并进最后一条回答（节流后的实际渲染点） */
    const flushTokens = useCallback(() => {
        const delta = pendingTokensRef.current;
        if (!delta) return;
        pendingTokensRef.current = '';
        setTurns(prev => {
            if (prev.length === 0) return prev;
            const next = [...prev];
            const cur = next[next.length - 1];
            next[next.length - 1] = { ...cur, answer: cur.answer + delta };
            return next;
        });
    }, []);

    const scheduleFlush = useCallback(() => {
        if (flushTimerRef.current !== null) return;
        flushTimerRef.current = setTimeout(() => {
            flushTimerRef.current = null;
            flushTokens();
        }, 50);
    }, [flushTokens]);

    /** 丢弃缓冲并停掉待触发的刷新（切会话/切项目/卸载时用） */
    const clearTokenFlush = useCallback(() => {
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }
        pendingTokensRef.current = '';
    }, []);

    // 项目选择：URL 无项目段，回落 localStorage / 第一个项目
    useEffect(() => {
        (async () => {
            try {
                // 问答读取项目内的图谱/对话数据，后端按项目成员权限校验（require_project_role viewer），
                // 公共资产只读、非成员访问会 403「无该项目访问权限」——因此这里只列自己的项目，
                // 不再把已发布公共体混进选项（选了也进不去）。2026-11-11
                const list = await projectsApi.getMyProjects();
                setProjects(list);
                // ?projectId= 直达（项目详情页「去问答」带参）> localStorage 记忆 > 第一个
                const fromUrl = Number(new URLSearchParams(location.search).get('projectId'));
                const saved = Number(localStorage.getItem(CONV_ID_KEY));
                const initial = list.find(p => p.id === fromUrl)?.id
                    ?? list.find(p => p.id === saved)?.id ?? list[0]?.id ?? null;
                setProjectId(initial);
            } catch {
                message.error('加载项目列表失败');
            } finally {
                setProjectsLoading(false);
            }
        })();
    }, []);

    const loadConversations = useCallback(async (pid: number) => {
        const seq = ++convSeqRef.current;
        try {
            const list = await listQaConversations(pid, 50);
            if (seq === convSeqRef.current) {
                convCache[pid] = list; // 供下次进入页面立即渲染
                setConversations(list);
            }
        } catch { /* 列表失败不阻断对话区 */ }
        finally {
            if (seq === convSeqRef.current) setConvsReady(true);
        }
    }, []);

    const loadModels = useCallback(async (pid: number) => {
        try {
            const items = await listQaModels(pid);
            setModels(items);
            // 默认选中 is_default 的配置；没有可用模型则置空（发送时提示）
            setModelId(items.find(m => m.is_default)?.id ?? null);
        } catch {
            setModels([]);
        }
    }, []);

    useEffect(() => {
        if (projectId == null) return;
        localStorage.setItem(CONV_ID_KEY, String(projectId));
        clearTokenFlush();
        setTurns([]);
        setActiveConvId(null);
        const cached = convCache[projectId];
        setConversations(cached ?? []);
        setConvsReady(!!cached);
        loadConversations(projectId);
        loadModels(projectId);
    }, [projectId, loadConversations, loadModels, clearTokenFlush]);

    // 流式输出逐 token 更新：在容器内贴底滚，比每个 token 重启动一次 smooth scrollIntoView 稳得多
    useEffect(() => {
        const el = listRef.current;
        if (!el || !stickBottomRef.current) return;
        el.scrollTop = el.scrollHeight;
    }, [turns]);

    // 切换会话、流式回答等不产生 scroll 事件的场景，也要重算边缘渐隐状态
    useEffect(() => {
        updateEdgeFade();
    }, [turns, updateEdgeFade]);

    // 卸载时中断进行中的流式请求和高亮计时器，避免对已卸载组件继续 setState
    useEffect(() => () => {
        abortRef.current?.abort();
        if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
        if (flushTimerRef.current !== null) clearTimeout(flushTimerRef.current);
    }, []);

    const handleCite = useCallback((turnIdx: number, n: number) => {
        setHighlightSource({ turn: turnIdx, n });
        document.getElementById(`qa-source-${turnIdx}-${n - 1}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = setTimeout(() => setHighlightSource(null), 2500);
    }, []);

    const newConversation = useCallback(() => {
        abortRef.current?.abort();
        clearTokenFlush();
        stickBottomRef.current = true;
        setTurns([]);
        setActiveConvId(null);
        setQuestion('');
    }, [clearTokenFlush]);

    const openConversation = useCallback(async (convId: string) => {
        if (projectId == null) return;
        abortRef.current?.abort();
        clearTokenFlush();
        stickBottomRef.current = true;
        try {
            const detail = await getQaConversation(projectId, convId);
            setActiveConvId(detail.id);
            setTurns(detail.messages.map(m => ({
                question: m.question,
                answer: m.answer,
                sources: m.sources ?? [],
                done: {
                    answer: m.answer,
                    confidence: m.confidence ?? 0,
                    reasoning_path: [],
                    model: m.model,
                    latency_ms: m.latency_ms ?? 0,
                    history_id: m.id,
                },
                streaming: false,
            })));
        } catch {
            message.error('加载对话失败');
        }
    }, [projectId, clearTokenFlush]);

    const removeConversation = useCallback(async (convId: string) => {
        if (projectId == null) return;
        try {
            await deleteQaConversation(projectId, convId);
            if (activeConvId === convId) newConversation();
            loadConversations(projectId);
        } catch {
            message.error('删除对话失败');
        }
    }, [projectId, activeConvId, newConversation, loadConversations]);

    const send = useCallback(async () => {
        const q = question.trim();
        if (!q) {
            message.warning('请输入问题');
            return;
        }
        if (projectId == null) {
            message.warning('请先选择项目');
            return;
        }
        // 新对话：客户端生成 uuid，首问后即成会话（后端按此分组落库）
        let convId = activeConvId;
        if (!convId) {
            convId = uuid();
            setActiveConvId(convId);
        }
        setQuestion('');
        setBusy(true);
        stickBottomRef.current = true;
        clearTokenFlush();
        setTurns(prev => [...prev, { question: q, answer: '', sources: [], streaming: true }]);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
            await askQuestionStream(
                projectId,
                q,
                {
                    onToken: delta => {
                        // 先入缓冲区，由 scheduleFlush 每 50ms 合并渲染一次
                        pendingTokensRef.current += delta;
                        scheduleFlush();
                    },
                    onSources: sources => {
                        setTurns(prev => {
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = { ...cur, sources: sources ?? [] };
                            return next;
                        });
                    },
                    onDone: done => {
                        // 收尾：把缓冲区剩余内容并入本次回答，再以服务端的完整答案为准
                        const pending = pendingTokensRef.current;
                        pendingTokensRef.current = '';
                        if (flushTimerRef.current !== null) {
                            clearTimeout(flushTimerRef.current);
                            flushTimerRef.current = null;
                        }
                        setTurns(prev => {
                            if (prev.length === 0) return prev;
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = {
                                ...cur, done, streaming: false,
                                answer: done.answer || (cur.answer + pending),
                            };
                            return next;
                        });
                        loadConversations(projectId);
                    },
                    onError: msg => {
                        clearTokenFlush();
                        setTurns(prev => {
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = { ...cur, streaming: false, error: msg };
                            return next;
                        });
                        setQuestion(q);  // 失败回填：用户改都不用改，直接再点发送
                    },
                },
                {
                    signal: controller.signal,
                    conversation_id: convId,
                    model_config_id: modelId,
                },
            );
            // 正常结束但没有 done 帧：补刷缓冲区尾部
            flushTokens();
            setTurns(prev => {
                const next = [...prev];
                const cur = next[next.length - 1];
                if (cur?.streaming) next[next.length - 1] = { ...cur, streaming: false };
                return next;
            });
        } catch (e: any) {
            if (e?.name !== 'AbortError') {
                setTurns(prev => {
                    const next = [...prev];
                    const cur = next[next.length - 1];
                    next[next.length - 1] = { ...cur, streaming: false, error: e?.message || '问答失败' };
                    return next;
                });
                setQuestion(q);  // 失败回填，便于直接重试
            }
        } finally {
            clearTokenFlush();
            setBusy(false);
            abortRef.current = null;
        }
    }, [question, projectId, activeConvId, modelId, loadConversations, scheduleFlush, flushTokens, clearTokenFlush]);

    // 停止生成：中断 SSE 请求；已产出的部分回答保留，本轮标记「已停止」
    const stop = useCallback(() => {
        abortRef.current?.abort();
        abortRef.current = null;
        // 先把节流缓冲里的尾部补上，再结束本轮
        if (flushTimerRef.current !== null) {
            clearTimeout(flushTimerRef.current);
            flushTimerRef.current = null;
        }
        flushTokens();
        setBusy(false);
        setTurns(prev => {
            const next = [...prev];
            const cur = next[next.length - 1];
            if (cur?.streaming) next[next.length - 1] = { ...cur, streaming: false, stopped: true };
            return next;
        });
    }, [flushTokens]);

    const canSend = !busy && question.trim().length > 0 && projectId != null;
    // 只在「这一侧确实还有被卷起来的内容」时给对应边缘做渐隐
    const edgeMask = (() => {
        const f = 20;
        if (edgeFade.top && edgeFade.bottom) {
            return `linear-gradient(to bottom, transparent 0, #000 ${f}px, #000 calc(100% - ${f}px), transparent 100%)`;
        }
        if (edgeFade.top) return `linear-gradient(to bottom, transparent 0, #000 ${f}px)`;
        if (edgeFade.bottom) return `linear-gradient(to bottom, #000 calc(100% - ${f}px), transparent 100%)`;
        return undefined;
    })();
    const activeProject = projects.find(p => p.id === projectId);
    const activeProjectName = activeProject?.name;
    const activeStatusMeta = activeProject ? projectStatusMeta(activeProject) : null;
    // 列表加载中留白（不画骨架、不判「暂无」）；确定没有任何可用项目、或列表已就绪时才下结论
    const convsReadyForUi = convsReady || (!projectsLoading && projectId == null);

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                /* 问答输入区（参考 aiBank demo 的 composer）：
                   玻璃底 + 内高光 + 近距投影；聚焦转品牌蓝；
                   等待用户输入时，外圈有一条缓慢旋转的锥形高光 */
                .qa-composer {
                    /* 左上浅、右下深。原来的 #F7FAFF 起手比画布本身还亮，
                       输入框看着像浮起来的一块白玻璃，和页面底色拉不开；
                       整体往深压一档，做成"凹进去的输入井"，比画布深、比白色消息卡浅，
                       右下再叠一层品牌蓝增强纵深 */
                    background:
                        radial-gradient(240px 160px at 100% 112%, rgba(91,141,239,0.16), transparent 72%),
                        linear-gradient(135deg, #F0F5FE 0%, #E6EEFB 46%, #D8E4F6 100%);
                    box-shadow:
                        inset 0 -1px 0 rgba(203,215,235,0.55),
                        0 12px 32px -20px rgba(15,23,42,0.34);
                }
                /* 二级配置条（2026-11-13）：项目 / 对话模型收进一条浅色容器，
                   与资产中心、我的项目的查询条同一套做法——内部控件透明去边框，
                   hover 浮白、聚焦转品牌蓝，整条读成一个"本次问答的上下文" */
                .qa-config .ant-select .ant-select-selector {
                    background-color: transparent !important;
                    border-color: transparent !important;
                    box-shadow: none !important;
                }
                .qa-config .ant-select:hover .ant-select-selector {
                    background-color: #FFFFFF !important;
                    border-color: #DCE7F7 !important;
                }
                .qa-config .ant-select-focused .ant-select-selector {
                    background-color: #FFFFFF !important;
                    border-color: #5B8DEF !important;
                    box-shadow: none !important;
                }
                /* 还没选项目：把"必须先选项目"从下方那行黄字提到控件本身，
                   否则选择器看起来一切正常，用户会直接开始输入然后发现发不出去 */
                .qa-config .qa-select-warn:not(.ant-select-focused) .ant-select-selector {
                    background-color: rgba(240, 178, 122, 0.12) !important;
                    border-color: rgba(240, 178, 122, 0.55) !important;
                }
                @property --qa-orbit-angle {
                    syntax: '<angle>';
                    inherits: false;
                    initial-value: 0deg;
                }
                .qa-composer::after {
                    content: '';
                    position: absolute;
                    inset: -1px;
                    padding: 1.5px;
                    border-radius: inherit;
                    pointer-events: none;
                    background: conic-gradient(
                        from var(--qa-orbit-angle, 0deg),
                        rgba(91,141,239,0.03) 0deg,
                        rgba(91,141,239,0.03) 260deg,
                        rgba(91,141,239,0.22) 305deg,
                        #5B8DEF 335deg,
                        rgba(91,141,239,0.03) 360deg
                    );
                    -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
                    -webkit-mask-composite: xor;
                    mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
                    mask-composite: exclude;
                    opacity: 0;
                    transition: opacity 0.25s ease;
                    animation: qa-orbit 4.2s linear infinite paused;
                }
                .qa-composer--awaiting::after {
                    opacity: 1;
                    animation-play-state: running;
                }
                @keyframes qa-orbit {
                    to { --qa-orbit-angle: 360deg; }
                }
                /* 发送：图标方块，品牌渐变 + 柔光投影；禁用态降透明度 */
                .qa-send-btn {
                    display: grid;
                    place-items: center;
                    width: 36px;
                    height: 36px;
                    flex-shrink: 0;
                    border: 0;
                    border-radius: 12px;
                    background: linear-gradient(135deg, #5B8DEF, #7C6CF0);
                    color: #fff;
                    box-shadow: 0 8px 20px -10px rgba(91,141,239,0.75);
                    cursor: pointer;
                    transition: box-shadow 0.15s ease, opacity 0.15s ease;
                }
                .qa-send-btn:hover:not(:disabled) {
                    box-shadow: 0 10px 24px -10px rgba(91,141,239,0.85);
                }
                .qa-send-btn:disabled {
                    opacity: 0.45;
                    cursor: not-allowed;
                    box-shadow: none;
                }
                .qa-send-btn:focus-visible {
                    outline: 2px solid rgba(91,141,239,0.5);
                    outline-offset: 2px;
                }
                /* 停止：与本页品牌色同源的淡蓝描边按钮，不用刺眼的红色 */
                .qa-stop-btn.ant-btn {
                    border-color: rgba(91,141,239,0.25);
                    background: rgba(91,141,239,0.08);
                    color: #2A57C6;
                    box-shadow: none;
                }
                .qa-stop-btn.ant-btn:hover {
                    border-color: rgba(91,141,239,0.45) !important;
                    background: rgba(91,141,239,0.12) !important;
                    color: #2A57C6 !important;
                }
                /* 新建对话：淡蓝品牌按钮，替代实心蓝块，避免在透明侧栏里显得过重 */
                .qa-new-conv {
                    color: #2A57C6;
                    background: rgba(91, 141, 239, 0.10);
                    border: 1px solid rgba(91, 141, 239, 0.22);
                    transition: background-color 0.18s ease, border-color 0.18s ease;
                }
                .qa-new-conv:hover {
                    background: rgba(91, 141, 239, 0.16);
                    border-color: rgba(91, 141, 239, 0.36);
                }
                .qa-new-conv:active {
                    background: rgba(91, 141, 239, 0.20);
                }
                .qa-new-conv:focus-visible {
                    outline: 2px solid rgba(91, 141, 239, 0.45);
                    outline-offset: 2px;
                }
                @media (prefers-reduced-motion: reduce) {
                    .qa-composer::after { animation: none; opacity: 0.35; }
                }
            `}</style>
            {/* 全站顶栏：面包屑 + 账号（与工作台/我的项目/资产中心共用，切页不再「失去顶栏」） */}
            <Navbar breadcrumbs={[{ title: '首页', path: '/' }, { title: '工具' }, { title: '本体问答' }]} />

            <div className="flex flex-1 min-h-0">
                {/* 左侧：会话历史。整列透明，让内容区底部的静态背景图透上来；
                    只保留一条右侧分隔线，列内条目用白色/品牌蓝药丸保持可读性 */}
                <aside className="w-64 shrink-0 border-r border-[#E4EBF7]/70 bg-transparent flex flex-col">
                    <div className="p-3 pb-2">
                        <button
                            type="button"
                            onClick={newConversation}
                            className="qa-new-conv w-full h-9 rounded-xl flex items-center justify-center gap-2 text-[13px] font-medium"
                        >
                            <PlusOutlined />
                            新建对话
                        </button>
                    </div>
                    <div className="px-4 pb-2 select-none">
                        <div className="flex items-baseline gap-1">
                            <span className="shrink-0 text-[11px] font-medium tracking-wide text-[#8A94AB]">
                                历史对话{convsReadyForUi ? `（${conversations.length}）` : ''}
                            </span>
                            {/* 会话按项目隔离，切项目后整列会换掉；标题里带上项目名，列表突然变化时不至于莫名其妙 */}
                            {activeProjectName && (
                                <span className="truncate text-[11px] text-[#A9B4C9]" title={activeProjectName}>
                                    · {activeProjectName}
                                </span>
                            )}
                        </div>
                    </div>
                    <div className="flex-1 overflow-auto px-2 pb-2">
                        {!convsReadyForUi ? null : conversations.length === 0 ? (
                            <div className="flex flex-col items-center justify-center text-center py-10 px-3">
                                <MessageOutlined className="text-2xl text-[#C3CCDD] mb-2" />
                                <div className="text-xs text-[#8A94AB]">暂无历史对话</div>
                                <div className="text-[11px] text-[#A9B4C9] mt-1">提问后会按会话归档在这里</div>
                            </div>
                        ) : (
                            conversations.map(c => (
                                <div
                                    key={c.id}
                                    role="button"
                                    tabIndex={0}
                                    onClick={() => openConversation(c.id)}
                                    onKeyDown={e => {
                                        // 行内的删除按钮也有 Enter/Space，别让它顺带上浮到整行触发打开会话
                                        if (e.target !== e.currentTarget) return;
                                        if (e.key === 'Enter' || e.key === ' ') {
                                            e.preventDefault();
                                            openConversation(c.id);
                                        }
                                    }}
                                    className={`group px-2.5 py-2 mb-1 rounded-lg cursor-pointer border transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)] ${
                                        activeConvId === c.id
                                            ? 'border-[rgba(91,141,239,0.40)] bg-[rgba(91,141,239,0.16)]'
                                            : 'border-transparent hover:bg-[rgba(91,141,239,0.07)]'
                                    }`}
                                >
                                    {/* 当前会话：填充胶囊 + 描边，不挂左侧竖条——
                                        「竖条贴边」这个手法留给左侧主导航（表示所在板块），
                                        会话是列表内的一条记录，用填充区分的语义更准确，
                                        两处也不会在同一屏相邻出现两个几乎一样的选中态 */}
                                    <div className="flex items-center justify-between gap-1">
                                        <span className={`text-sm truncate flex-1 ${activeConvId === c.id ? 'text-[#2A57C6] font-medium' : 'text-[#1F2A44]'}`}>
                                            {c.title}
                                        </span>
                                        <Popconfirm
                                            title="删除这条对话？"
                                            description="删除后不可恢复"
                                            okText="删除"
                                            okButtonProps={{ danger: true }}
                                            cancelText="取消"
                                            onConfirm={e => { e?.stopPropagation(); removeConversation(c.id); }}
                                            onCancel={e => e?.stopPropagation()}
                                        >
                                            <Tooltip title="删除对话">
                                                {/* 删除是低频操作：非当前会话只在 hover/键盘聚焦时出现，列表更清爽 */}
                                                <Button
                                                    size="small" type="text"
                                                    aria-label="删除对话"
                                                    className={`!px-1 transition-opacity ${
                                                        activeConvId === c.id
                                                            ? 'opacity-100'
                                                            : 'opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto focus-visible:opacity-100'
                                                    }`}
                                                    icon={<DeleteOutlined className="text-[#A3ADC2] hover:text-red-500" />}
                                                    onClick={e => e.stopPropagation()}
                                                />
                                            </Tooltip>
                                        </Popconfirm>
                                    </div>
                                    <div className="text-[11px] text-[#A3ADC2] mt-0.5 flex justify-between">
                                        <span>{c.message_count} 轮</span>
                                        <span>{formatConvTime(c.last_time)}</span>
                                    </div>
                                </div>
                            ))
                        )}
                    </div>
                </aside>

                {/* 右侧：对话区 */}
                <div className="flex-1 flex flex-col min-w-0">
                    {/* 二级工具条：项目 + 模型。放在画布上（不自建白色页头），避免与全站顶栏叠成两条白条。
                        两者是同一组上下文配置（这次问答用哪个项目、哪个模型），
                        收进一条浅色胶囊并用一条淡竖线分隔，不再用 flex-1 推到屏幕两端 */}
                    {/* 配置条与下方消息区、输入框共用同一条 768px 轴：
                        原来贴内容区左缘，1920 屏上会和居中的对话列错开 400 多像素。
                        胶囊在列内保持左对齐——居中会让它浮在空白中间，
                        它读作「工具条」，不读作「标题」 */}
                    <div className="shrink-0 px-6 pt-3 pb-1">
                        <div className="max-w-3xl mx-auto">
                            <div className="qa-config inline-flex max-w-full flex-wrap items-center gap-1 rounded-[12px] border border-[#E7EEF9] bg-white/70 p-1">
                                <div className="flex min-w-0 items-center gap-1.5 pl-2.5">
                                    <span className="shrink-0 text-[13px] font-semibold text-[#3D4B66]">项目</span>
                                    <Select
                                        value={projectId ?? undefined}
                                        onChange={v => setProjectId(v)}
                                        placeholder="选择项目"
                                        showSearch
                                        optionFilterProp="label"
                                        className={projectId == null ? 'qa-select-warn' : undefined}
                                        style={{ width: 'auto', minWidth: 200, maxWidth: 270 }}
                                        options={projects.map(p => ({ value: p.id, label: p.name }))}
                                    />
                                    {activeStatusMeta && (
                                        <Tag color={activeStatusMeta.color} className="!mr-0 shrink-0">{activeStatusMeta.text}</Tag>
                                    )}
                                </div>
                                <span className="mx-1 h-4 w-px shrink-0 bg-[#E4EBF7]" aria-hidden />
                                <div className="flex min-w-0 items-center gap-1.5 px-1">
                                    <span className="shrink-0 text-[13px] font-semibold text-[#3D4B66]">对话模型</span>
                                    <Select
                                        value={modelId ?? undefined}
                                        onChange={v => setModelId(v)}
                                        placeholder="跟随默认模型"
                                        allowClear
                                        onClear={() => setModelId(null)}
                                        style={{ width: 'auto', minWidth: 200, maxWidth: 270 }}
                                        options={models.map(m => ({
                                            value: m.id,
                                            label: `${m.name}${m.is_default ? ' · 默认' : ''}`,
                                        }))}
                                        notFoundContent={<span className="text-xs text-gray-500">暂无可用模型，请在管理后台「模型配置」添加</span>}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 消息区 */}
                    <div
                        ref={listRef}
                        onScroll={updateEdgeFade}
                        style={{ WebkitMaskImage: edgeMask, maskImage: edgeMask }}
                        className="flex-1 overflow-auto px-6 pb-2 [scrollbar-gutter:stable_both-edges]"
                    >
                        {turns.length === 0 && (
                            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3"
                                  style={{ background: 'linear-gradient(135deg, #5B8DEF, #8B5CF6)' }}>
                                    <BrandMark size={32} strokeWidth={3.5} />
                                </div>
                                <p className="tk-mono text-[10px] tracking-[0.24em] text-[#9AA6BF]">ONTOLOGY Q&amp;A</p>
                                <h3 className="mt-2 text-[17px] font-semibold text-[#0F172A]">基于本体知识图谱的检索问答</h3>
                                {/* 上下文行：告诉用户这次问答落在哪个图谱上，引用还能溯源到原文 */}
                                <p className="mt-1.5 text-[12.5px] leading-5 text-[#66738F]">
                                    {activeProjectName
                                        ? <>将从 <span className="font-medium text-[#2F62D8]">《{activeProjectName}》</span> 的本体图谱中检索答案，引用可溯源到原文</>
                                        : '选择一个项目后即可开始提问'}
                                </p>

                                {/* 没项目时：一条能直接操作的提示，替代原来那行不起眼的黄字 */}
                                {!projectsLoading && projectId == null && (
                                    <div className="mt-4 flex items-center gap-2 rounded-xl border border-[rgba(240,178,122,0.45)] bg-[rgba(240,178,122,0.10)] px-3 py-2 text-[12px] text-[#8A5A12]">
                                        <ExclamationCircleOutlined className="shrink-0 text-[13px] text-[#C98A2E]" />
                                        <span>还没有可用项目，请先在「我的项目」创建并完成建模</span>
                                        <Button
                                            type="link" size="small"
                                            className="!h-auto !px-1 !text-[12px] !text-[#2A57C6]"
                                            onClick={() => navigate('/my-projects')}
                                        >
                                            去我的项目
                                        </Button>
                                    </div>
                                )}

                                {/* 示例问题：空状态下唯一能一键开始的动作，做成竖排卡片，
                                    比原来三个长短不一的灰胶囊更容易点中 */}
                                <div className="mt-5 w-full max-w-lg text-left">
                                    <div className="mb-2 flex items-center gap-1.5 text-[12px] text-[#8A94AB]">
                                        <BulbOutlined className="text-[#5B8DEF]" />
                                        <span>试试这样问</span>
                                    </div>
                                    <div className="space-y-1.5">
                                        {SAMPLE_QUESTIONS.map(q => {
                                            const disabled = projectId == null;
                                            return (
                                                <button
                                                    key={q}
                                                    type="button"
                                                    disabled={disabled}
                                                    onClick={() => {
                                                        setQuestion(q);
                                                        requestAnimationFrame(() => inputRef.current?.focus());
                                                    }}
                                                    className={`group flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left text-[12.5px] transition-colors ${
                                                        disabled
                                                            ? 'cursor-not-allowed border-[#EDF1F8] bg-white/40 text-[#B7C1D4]'
                                                            : 'border-[#E7EEF9] bg-white/70 text-[#3D4B66] hover:border-[#5B8DEF] hover:bg-white hover:text-[#2A57C6]'
                                                    }`}
                                                >
                                                    <span className="truncate">{q}</span>
                                                    <ArrowRightOutlined
                                                        className={`shrink-0 text-[10px] ${
                                                            disabled
                                                                ? 'opacity-40'
                                                                : 'text-[#B7C1D4] transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-[#5B8DEF]'
                                                        }`}
                                                    />
                                                </button>
                                            );
                                        })}
                                    </div>
                                </div>
                            </div>
                        )}
                        <div className="max-w-3xl mx-auto">
                            {turns.map((turn, i) => {
                                const meta = turn.done;
                                const confidence = meta?.confidence ?? 0;
                                const confHigh = confidence >= 0.6;
                                return (
                                <div key={i} className="pt-4">
                                    {/* 提问气泡：品牌淡蓝底 + 深墨字。原来是品牌渐变 + 发光投影——
                                        整页一直在往下压，只有这里还在喊；多轮之后右侧是一屏蓝块，
                                        反而把真正要读的「回答」压成了背景 */}
                                    <div className="flex justify-end mb-2">
                                        <div
                                            className="max-w-[85%] rounded-2xl border border-[rgba(91,141,239,0.28)] px-3.5 py-2 text-[14px] leading-[1.7] whitespace-pre-wrap text-[#1F2A44]"
                                            style={{ background: 'rgba(91, 141, 239, 0.12)' }}
                                        >
                                            {turn.question}
                                        </div>
                                    </div>
                                    <div>
                                        {turn.error ? (
                                            <div
                                                className="rounded-xl border border-[#F2C6CE] px-3.5 py-2.5 text-sm text-[#B42318]"
                                                style={{ background: '#FDF2F4' }}
                                            >
                                                问答失败：{turn.error}
                                            </div>
                                        ) : (
                                            <div className="overflow-hidden rounded-2xl border border-[#E7EEF9] bg-white shadow-[0_2px_10px_-8px_rgba(15,23,42,0.18)]">
                                                <div className="px-4 py-3">
                                                    {/* 流式状态统一收进卡片内的独立状态行：
                                                        不再把 spinner 直接跟在 Markdown 块后面（块级元素后单独换行，
                                                        贴着卡片左缘偶尔会被滚动区底部渐隐/圆角裁掉，看着像"跑出卡片"） */}
                                                    {turn.answer && (
                                                        <MarkdownAnswer
                                                            content={turn.answer}
                                                            sourceCount={turn.sources.length}
                                                            onCite={n => handleCite(i, n)}
                                                        />
                                                    )}
                                                    {turn.streaming ? (
                                                        <div className={`flex items-center gap-2 text-xs text-[#8A94AB] ${turn.answer ? 'mt-2.5' : ''}`}>
                                                            <LoadingOutlined className="text-[#5B8DEF]" />
                                                            <span>{turn.answer ? '正在生成…' : '正在检索本体图谱…'}</span>
                                                        </div>
                                                    ) : !turn.answer && (
                                                        <div className="text-sm text-[#8A94AB]">（模型未返回内容）</div>
                                                    )}
                                                </div>
                                                {/* 元信息收进卡片底部：置信度 / 模型 / 延迟 / 推理路径
                                                    是这条回答自身的属性，不该浮在卡外和下面的溯源区抢位置。
                                                    置信度不再用 antd 的绿/橙 Tag——那是一套和冷调蓝紫无关的语义色 */}
                                                {(meta || turn.stopped) && (
                                                    <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-1 border-t border-[#F0F4FB] px-4 py-2 text-[11px] text-[#A3ADC2]">
                                                        {turn.stopped && <span className="mr-auto">已停止生成</span>}
                                                        {confidence > 0 && (
                                                            <span
                                                                className="inline-flex items-center gap-1.5 rounded-full px-2 py-[3px]"
                                                                style={{ background: confHigh ? 'rgba(91, 141, 239, 0.10)' : 'rgba(242, 185, 80, 0.16)' }}
                                                            >
                                                                <span
                                                                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                                                                    style={{ background: confHigh ? '#2F62D8' : '#B8790F' }}
                                                                />
                                                                置信度
                                                                <span
                                                                    className="tk-mono font-semibold"
                                                                    style={{ color: confHigh ? '#2F62D8' : '#B8790F' }}
                                                                >
                                                                    {(confidence * 100).toFixed(0)}%
                                                                </span>
                                                            </span>
                                                        )}
                                                        {meta?.model && <span>{meta.model}</span>}
                                                        {meta && <span>{meta.latency_ms}ms</span>}
                                                        {meta && meta.reasoning_path?.length > 0 && (
                                                            <Popover
                                                                content={(
                                                                    <ol className="list-decimal pl-4 text-xs max-w-xs">
                                                                        {meta.reasoning_path.map((s, k) => <li key={k}>{s}</li>)}
                                                                    </ol>
                                                                )}
                                                                title="推理路径"
                                                            >
                                                                <Button size="small" type="link" className="!h-auto !px-0 !text-[11px]">推理路径</Button>
                                                            </Popover>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        {turn.sources.length > 0 && (
                                            /* 溯源证据区：浅底容器把「引用卡」和「答案正文」分成两层，白卡不再直接叠白卡 */
                                            <div className="mt-2 rounded-xl border border-[#E7EEF9] bg-[#F7F9FD] p-2.5">
                                                <div className="mb-2 flex items-center gap-1.5 text-[11.5px] text-[#66738F]">
                                                    <FileSearchOutlined className="text-[#5B8DEF]" />
                                                    <span>溯源引用</span>
                                                    <span className="tk-mono rounded-full bg-white px-1.5 py-[1px] text-[10.5px] text-[#52607A]">
                                                        {turn.sources.length}
                                                    </span>
                                                </div>
                                                <div className="space-y-1.5">
                                                    {turn.sources.map((s, k) => (
                                                        <div key={k} id={`qa-source-${i}-${k}`}>
                                                            <SourceCard
                                                                index={k}
                                                                source={s}
                                                                highlighted={highlightSource?.turn === i && highlightSource.n === k + 1}
                                                            />
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* 输入区：一体化 composer（玻璃底 + 细描边 + 柔光；聚焦转品牌蓝；
                        等待输入时外圈有缓慢旋转的高光，参考 aiBank demo） */}
                    <div className="shrink-0 px-6 pt-3 pb-4">
                        <div className="max-w-3xl mx-auto">
                            <div className={`qa-composer relative rounded-2xl border border-[#D7E3F3] px-3.5 pt-3 pb-2.5 transition-[border-color,box-shadow] duration-200 focus-within:border-[#5B8DEF] focus-within:shadow-[0_16px_38px_-18px_rgba(91,141,239,0.42)] ${!busy && projectId != null ? 'qa-composer--awaiting' : ''}`}>
                                <Input.TextArea
                                    ref={inputRef}
                                    autoFocus
                                    value={question}
                                    onChange={e => setQuestion(e.target.value)}
                                    placeholder={projectId == null ? '请先选择一个项目…' : '向本体知识图谱提问…'}
                                    variant="borderless"
                                    autoSize={{ minRows: 2, maxRows: 6 }}
                                    disabled={busy}
                                    className="!p-0 !text-[15px] !leading-7 resize-none"
                                    onPressEnter={e => {
                                        // 中文输入法确认候选词的 Enter 也会走 keydown，不能当发送
                                        if (e.nativeEvent.isComposing) return;
                                        if (!e.shiftKey) {
                                            e.preventDefault();
                                            if (canSend) send();
                                        }
                                    }}
                                />
                                <div className="mt-2 flex items-center justify-between gap-3">
                                    <span className="flex items-center gap-1.5 text-[11px] text-gray-400 select-none">
                                        <FileSearchOutlined className="text-[#8A94AB]" />
                                        Enter 发送 · Shift + Enter 换行
                                    </span>
                                    {busy ? (
                                        <Button icon={<StopOutlined />} onClick={stop} className="qa-stop-btn">
                                            停止
                                        </Button>
                                    ) : (
                                        <button
                                            type="button"
                                            aria-label="发送"
                                            title="发送"
                                            onClick={send}
                                            disabled={!canSend}
                                            className="qa-send-btn"
                                        >
                                            <SendOutlined />
                                        </button>
                                    )}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
