// src/pages/QaPage.tsx - 本体问答独立页面（R8：docs/design/05 §6 / 07 §4）
// 左侧：新建对话 + 历史对话列表（按会话分组）；右侧：项目/模型选择 + 流式对话 + 引用溯源。
// 复用 features/qa 下的 MarkdownAnswer（[n] 角标点击定位）与 SourceCard。
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { message } from '../shared/feedback';
import { Button, Input, Popconfirm, Select, Tag } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { QA_LAST_PROJECT_ID_KEY } from '../shared/keys';
import BrandMark from '../components/BrandMark';
import Navbar from '../components/Layout/Navbar';
import { projectStatusMeta } from '../shared/projectStatus';
import {
    ArrowDownOutlined,
    ArrowRightOutlined,
    BulbOutlined,
    DeleteOutlined,
    DownOutlined,
    ExclamationCircleOutlined,
    FileSearchOutlined,
    LoadingOutlined,
    MessageOutlined,
    PlusOutlined,
    SendOutlined,
    StopOutlined,
    UpOutlined,
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
    type QaTraceStep,
} from '../api/qa';
import { projectsApi } from '../api/projects';
import type { ProjectData } from '../types/ontology';
import MarkdownAnswer from '../features/qa/MarkdownAnswer';
import SourceCard from '../features/qa/SourceCard';
import RetrievalTraceCard from '../features/qa/RetrievalTraceCard';
import { uuid } from '../utils/uuid';
import Tip from '../shared/Tip';

interface QaTurn {
    question: string;
    answer: string;
    sources: QaSource[];
    trace?: QaTraceStep[];
    done?: QaDonePayload;
    error?: string;
    streaming: boolean;
    /** 用户手动停止生成（保留已产出的部分内容） */
    stopped?: boolean;
    /** 溯源引用是否整块展开（5~6 条时逐卡点开太累，见下方头部开关） */
    sourcesExpanded?: boolean;
    /** 溯源引用整块是否展开（默认收起，避免回答下面拖很长） */
    sourcesOpen?: boolean;
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
    '这些实体之间是如何关联的？',
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
    // 打开历史会话的加载态：延迟 180ms 才显示，避免本地快请求时闪一下提示
    const [convOpening, setConvOpening] = useState(false);
    const convOpenTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // 连点两条会话时，只让最后一次请求收掉加载态（先返回的旧请求不碰它）
    const convOpenSeqRef = useRef(0);

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

    /** 回到最新：用户滚上去看历史后，输入框上方浮出的圆形按钮（参考 aiBank 的 return-current-btn）。
        点一下恢复"贴底跟随"，并把视图平滑滚到底 */
    const jumpToLatest = useCallback(() => {
        const el = listRef.current;
        if (!el) return;
        stickBottomRef.current = true;
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
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

    // 流式输出逐 token 更新：在容器内贴底滚，比每个 token 重启动一次 smooth scrollIntoView 稳得多。
    // 必须是 useLayoutEffect（2026-11-19）：原先用 useEffect，它在浏览器绘制之后才执行——
    // 每 50ms 一次 token 冲刷，新文字先按旧的滚动位置画在屏幕底部（正好压在「正在生成」那一行上），
    // 下一帧才被滚下去，看起来就是文字时不时"糊"住状态行。改成绘制前滚动，这一帧就不存在了。
    useLayoutEffect(() => {
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
        if (convOpenTimerRef.current) clearTimeout(convOpenTimerRef.current);
        if (flushTimerRef.current !== null) clearTimeout(flushTimerRef.current);
    }, []);

    /** 整块展开/收起某一轮的全部引用卡；单卡之后仍可各自切换 */
    const toggleAllSources = useCallback((turnIdx: number) => {
        setTurns(prev => prev.map((t, k) => (k === turnIdx ? { ...t, sourcesExpanded: !t.sourcesExpanded } : t)));
    }, []);

    const toggleSourcesSection = useCallback((turnIdx: number) => {
        setTurns(prev => prev.map((t, k) =>
            (k === turnIdx ? { ...t, sourcesOpen: !t.sourcesOpen } : t)));
    }, []);

    const handleCite = useCallback((turnIdx: number, n: number) => {
        setHighlightSource({ turn: turnIdx, n });
        // 引用区默认收起；点正文角标时先展开引用区，再滚动到对应卡片
        setTurns(prev => prev.map((t, k) =>
            (k === turnIdx ? { ...t, sourcesOpen: true } : t)));
        // 定位过去的同时那条证据会自动展开（见 SourceCard），卡片高度要等这一帧
        // 渲染完才定下来；立刻滚只会停在折叠态的位置，所以延后一拍再滚
        const targetId = `qa-source-${turnIdx}-${n - 1}`;
        window.setTimeout(() => {
            document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }, 180);
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
        // 加载态延迟出现（2026-11-19）：本地点开只是几十毫秒，立刻弹转圈会闪；
        // 超过 180ms 还没回来才提示，慢的时候用户不至于"点了没反应"
        const openSeq = ++convOpenSeqRef.current;
        if (convOpenTimerRef.current) clearTimeout(convOpenTimerRef.current);
        convOpenTimerRef.current = setTimeout(() => setConvOpening(true), 180);
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
                    retrieval_trace: m.retrieval_trace ?? [],
                    model: m.model,
                    latency_ms: m.latency_ms ?? 0,
                    history_id: m.id,
                },
                streaming: false,
            })));
        } catch {
            message.error('加载对话失败');
        } finally {
            if (openSeq === convOpenSeqRef.current) {
                if (convOpenTimerRef.current) {
                    clearTimeout(convOpenTimerRef.current);
                    convOpenTimerRef.current = null;
                }
                setConvOpening(false);
            }
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
        setTurns(prev => [...prev, { question: q, answer: '', sources: [], trace: [], streaming: true }]);
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
                    onTrace: trace => {
                        setTurns(prev => {
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = { ...cur, trace: trace ?? [] };
                            return next;
                        });
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
    // 欢迎语里的项目名最多显示 20 字（2026-11-19）：再长会把这行居中文字撑成两三行；
    // 超出部分用省略号收掉，完整名字仍可在上方选择器里查看
    const welcomeProjectName = activeProjectName && activeProjectName.length > 20
        ? `${activeProjectName.slice(0, 20)}…`
        : activeProjectName;
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
                   外框与内部控件的通用样式走全站 .tk-query-bar；
                   这里只保留「未选项目」的琥珀警示态 */
                /* 还没选项目：把"必须先选项目"从下方那行黄字提到控件本身，
                   否则选择器看起来一切正常，用户会直接开始输入然后发现发不出去 */
                .qa-config .qa-select-warn:not(.ant-select-focused) .ant-select-selector {
                    background-color: rgba(240, 178, 122, 0.12) !important;
                    border-color: rgba(240, 178, 122, 0.55) !important;
                }
                /* 空对话的示例问题卡：和表单字段同一支浅冷蓝，不再用半透明白 */
                .qa-sample-btn {
                    border-color: var(--field-border);
                    background: var(--field-bg);
                    color: #3D4B66;
                }
                .qa-sample-btn:not(:disabled):hover {
                    border-color: var(--field-border-focus);
                    background: var(--field-bg-hover);
                    color: #2A57C6;
                }
                .qa-sample-btn:disabled {
                    cursor: not-allowed;
                    border-color: var(--field-disabled-border);
                    background: var(--field-disabled-bg);
                    color: #B7C1D4;
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
                /* 回答卡（2026-11-19）：原来是一张纯白 + 细描边的平面纸，压在冷蓝画布上
                   像从别处抠来的一块控件。改成和 .ant-card 同一族的冷白纸面
                   ——冷蓝渐变底 + 上内高光 + 下内阴影 + 近距投影，
                   与「我的项目」卡片同一档材质，卡片因此有厚度而不是一张贴纸 */
                .qa-answer {
                    /* 不用 --surface-paper：那支纸面右上角带一大片品牌柔光，
                       放在整页抽屉/卡片上好看，缩到一张回答卡上会变成角落里的一块蓝斑。
                       2026-11-19 再提一档：原来底部收到 #F3F8FF，和画布（#EAF1FB 附近）
                       只差几个色阶，整张卡"贴"在底图上；现在改成冷白→浅蓝的纸面，
                       底部比画布亮一档，卡才有离地的感觉 */
                    background: linear-gradient(180deg, #FFFFFF 0%, #FBFCFF 58%, #F5F9FF 100%);
                    border: 1px solid rgba(91, 141, 239, 0.20);
                    box-shadow:
                        inset 0 1px 0 rgba(255, 255, 255, 0.96),
                        inset 0 -1px 0 rgba(219, 228, 242, 0.5),
                        0 14px 34px -26px rgba(15, 23, 42, 0.36),
                        0 2px 6px rgba(15, 23, 42, 0.05);
                }
                /* 回到最新（2026-11-19，参考 aiBank 的 return-current-btn）：
                   36px 冷白圆片 + 品牌描边 + 柔投影，hover 加深描边并转纯白；
                   出现时 180ms 上浮淡入，不打断阅读 */
                .qa-return-btn {
                    display: grid;
                    place-items: center;
                    width: 36px;
                    height: 36px;
                    padding: 0;
                    border: 1px solid rgba(91, 141, 239, 0.28);
                    border-radius: 50%;
                    background: rgba(251, 253, 255, 0.94);
                    color: #2A57C6;
                    font-size: 16px;
                    cursor: pointer;
                    box-shadow: 0 6px 18px rgba(15, 23, 42, 0.16);
                    transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
                }
                .qa-return-btn:hover {
                    border-color: rgba(91, 141, 239, 0.55);
                    background: #FFFFFF;
                    box-shadow: 0 8px 22px rgba(15, 23, 42, 0.20);
                }
                .qa-return-btn:focus-visible {
                    outline: 2px solid rgba(91, 141, 239, 0.50);
                    outline-offset: 2px;
                }
                .qa-return-in {
                    animation: qa-return-in 180ms ease-out both;
                }
                @keyframes qa-return-in {
                    from { opacity: 0; transform: translateY(6px); }
                    to { opacity: 1; transform: translateY(0); }
                }
                @media (prefers-reduced-motion: reduce) {
                    .qa-composer::after { animation: none; opacity: 0.35; }
                    .qa-return-in { animation: none; }
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
                            {/* 项目名在顶部选择器里本来就完整可见，这里不再挂悬停提示 */}
                            {activeProjectName && (
                                <span className="truncate text-[11px] text-[#A9B4C9]">
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
                                            {/* 删除是低频操作：非当前会话只在 hover/键盘聚焦时出现，列表更清爽；
                                                图标本身就是删除（垃圾桶）+ aria-label，不再挂悬停提示 */}
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
                <div className="relative flex-1 flex flex-col min-w-0">
                    {/* 二级工具条：项目 + 模型。放在画布上（不自建白色页头），避免与全站顶栏叠成两条白条。
                        两者是同一组上下文配置（这次问答用哪个项目、哪个模型），
                        收进一条浅色胶囊并用一条淡竖线分隔，不再用 flex-1 推到屏幕两端 */}
                    {/* 配置条与下方消息区、输入框共用同一条 768px 轴：
                        原来贴内容区左缘，1920 屏上会和居中的对话列错开 400 多像素。
                        胶囊在列内保持左对齐——居中会让它浮在空白中间，
                        它读作「工具条」，不读作「标题」 */}
                    <div className="shrink-0 px-6 pt-3 pb-1">
                        <div className="max-w-3xl mx-auto">
                            <div className="tk-query-bar qa-config inline-flex max-w-full flex-wrap items-center gap-1 p-1">
                                <div className="flex min-w-0 items-center gap-1.5 pl-2.5">
                                    <span className="shrink-0 text-[13px] font-semibold text-[#3D4B66]">项目</span>
                                    <Select
                                        value={projectId ?? undefined}
                                        onChange={v => setProjectId(v)}
                                        placeholder="选择项目"
                                        showSearch
                                        optionFilterProp="label"
                                        /* 项目列表还在加载时不要挂警告色（2026-11-17）：
                                           进页面瞬间 projectId 还是 null，选择器会先闪一下
                                           琥珀底（.qa-select-warn），列表回来后自动选中第一个
                                           又变回白色。只有"加载完了确实没选中"才提示 */
                                        className={!projectsLoading && projectId == null ? 'qa-select-warn' : undefined}
                                        /* 固定宽度（2026-11-19）：原来是 auto + 270 上限，长项目名会把整条
                                           「项目 / 对话模型」工具条撑长；定宽后名字只在选择器里省略号截断，
                                           工具条长度稳定，右侧「对话模型」不会被挤走 */
                                        style={{ width: 240 }}
                                        options={projects.map(p => ({ value: p.id, label: p.name }))}
                                    />
                                    {/* 状态标签固定槽位（2026-11-19）：
                                        ① 文案只有「草稿」(2 字) 和其余 3 字两档，不定宽会让后面的
                                           竖线和「对话模型」随项目状态横移；
                                        ② 槽位常驻——项目列表回来前还没有状态，如果这时不占位，
                                           数据到达的瞬间整条工具条会突然变长（进入页面时的那下跳动）。
                                           54px 兜住 3 字（约 52px），短文案居中 */}
                                    <span className="inline-flex w-[54px] shrink-0 items-center justify-center">
                                        {activeStatusMeta && (
                                            <Tag color={activeStatusMeta.color} className="!mr-0 min-w-[54px] text-center">
                                                {activeStatusMeta.text}
                                            </Tag>
                                        )}
                                    </span>
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
                                        style={{ width: 240 }}
                                        options={models.map(m => ({
                                            value: m.id,
                                            label: `${m.name}${m.is_default ? ' · 默认' : ''}`,
                                        }))}
                                        notFoundContent={<span className="text-xs text-[#66738F]">暂无可用模型，请在管理后台「模型配置」添加</span>}
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
                        className="flex-1 overflow-auto px-6 pb-2 [overflow-anchor:none] [scrollbar-gutter:stable_both-edges]"
                    >
                        {turns.length === 0 && (
                            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-3"
                                  style={{ background: 'linear-gradient(135deg, #5B8DEF, #8B5CF6)' }}>
                                    <BrandMark size={32} strokeWidth={3.5} />
                                </div>
                                <p className="tk-mono text-[10px] tracking-[0.24em] text-[#9AA6BF]">ONTOLOGY Q&amp;A</p>
                                <h3 className="mt-2 text-[17px] font-semibold text-[#0F172A]">基于本体知识图谱的检索问答</h3>
                                {/* 上下文行：告诉用户这次问答落在哪个图谱上，引用还能溯源到原文。
                                    项目名超过 20 字在 welcomeProjectName 里已经截断，句长可控 */}
                                <p className="mt-1.5 text-[12.5px] leading-5 text-[#66738F]">
                                    {activeProjectName
                                        ? <>将从 <span className="font-medium text-[#2F62D8]">《{welcomeProjectName}》</span> 的本体图谱中检索答案，引用可溯源到原文</>
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
                                    比原来三个长短不一的灰胶囊更容易点中；
                                    底色跟表单字段统一成浅冷蓝，不再用半透明白 */}
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
                                                    className="qa-sample-btn group flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-2 text-left text-[12.5px] transition-colors"
                                                >
                                                    <span className="truncate">{q}</span>
                                                    <ArrowRightOutlined
                                                        className={`shrink-0 text-[10px] ${
                                                            disabled
                                                                ? 'opacity-40'
                                                                : 'text-[#9DB4D8] transition-all duration-200 group-hover:translate-x-0.5 group-hover:text-[#5B8DEF]'
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
                                return (
                                <div key={i} className="pt-4">
                                    {/* 提问气泡：品牌淡蓝底 + 深墨字。原来是品牌渐变 + 发光投影——
                                        整页一直在往下压，只有这里还在喊；多轮之后右侧是一屏蓝块，
                                        反而把真正要读的「回答」压成了背景。
                                        2026-11-19 再收一档：平涂切成同族渐变 + 上内高光，
                                        和回答卡同一套「有厚度」的材质语法；右上角收一个小圆角当气泡折角，
                                        一眼能分出「我问的」和「它答的」 */}
                                    <div className="flex justify-end mb-4">
                                        <div
                                            className="max-w-[85%] rounded-2xl rounded-tr-[6px] px-3.5 py-2 text-[14px] leading-[1.7] whitespace-pre-wrap text-[#1F2A44]"
                                            style={{
                                                background: 'linear-gradient(180deg, rgba(91,141,239,0.15) 0%, rgba(91,141,239,0.10) 100%)',
                                                border: '1px solid rgba(91,141,239,0.22)',
                                                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.55)',
                                            }}
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
                                            <div className="qa-answer overflow-hidden rounded-2xl">
                                                <div className={`px-4 pt-3 ${turn.streaming ? '' : 'pb-3'}`}>
                                                    {/* 检索过程先行：提问后立即逐步展示"怎么查"（流式期间最后一步进行中），
                                                        答案在过程之下出现，溯源收尾——"一步步处理 → 得到结果 → 溯源" */}
                                                    {(turn.trace?.length || meta?.retrieval_trace?.length) ? (
                                                        <RetrievalTraceCard
                                                            trace={(turn.trace?.length ? turn.trace : meta?.retrieval_trace) as QaTraceStep[]}
                                                            streaming={turn.streaming && !turn.answer}
                                                        />
                                                    ) : null}
                                                    <div className={turn.trace?.length || meta?.retrieval_trace?.length ? 'mt-2.5 border-t border-dashed border-[rgba(91,141,239,0.16)] pt-2.5' : ''}>
                                                        {turn.answer && (
                                                            <MarkdownAnswer
                                                                content={turn.answer}
                                                                sourceCount={turn.sources.length}
                                                                onCite={n => handleCite(i, n)}
                                                            />
                                                        )}
                                                        {!turn.streaming && !turn.answer && (
                                                            <div className="text-sm text-[#8A94AB]">（模型未返回内容）</div>
                                                        )}
                                                    </div>
                                                </div>
                                                {/* 生成状态单独一行、放在正文容器之外（2026-11-19 用户反馈）：
                                                    正文再怎么一坨坨往外冒，也只会把它往下顶，不会和它叠在一起。
                                                    间距按旧版口径给——有正文时 pt-2.5（等同原来的 mt-2.5），
                                                    行下一律 pb-3，和正文容器的 py-3 收在同一个位置，
                                                    不会像绝对定位那版在卡片底部多垫一截空白 */}
                                                {turn.streaming && (
                                                    <div
                                                        role="status"
                                                        aria-live="polite"
                                                        className={`flex items-center gap-2 px-4 pb-3 text-xs text-[#8A94AB] ${turn.answer ? 'pt-2.5' : ''}`}
                                                    >
                                                        <LoadingOutlined className="text-[#5B8DEF]" />
                                                        <span>{turn.answer ? '正在生成…' : (turn.trace?.length ? '正在综合检索结果…' : '正在检索本体图谱…')}</span>
                                                    </div>
                                                )}
                                                {/* 元信息收进卡片底部：模型 / 延迟是这条回答自身的属性，
                                                    不该浮在卡外和下面的溯源区抢位置。
                                                    置信度胶囊已去掉：一个百分数看完就过，
                                                    却和耗时并列在同一行，白占一格的注意力 */}
                                                {(meta || turn.stopped) && (
                                                    <div className="flex flex-wrap items-center justify-end gap-x-2.5 gap-y-1 border-t border-[rgba(91,141,239,0.10)] px-4 py-2 text-[11px] text-[#A3ADC2]">
                                                        {turn.stopped && <span className="mr-auto">已停止生成</span>}
                                                        {meta?.model && <span className="tk-mono">{meta.model}</span>}
                                                        {meta && <span className="tk-mono">{meta.latency_ms}ms</span>}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        {turn.sources.length > 0 && (
                                            /* 溯源证据区：把「引用」和「答案正文」分成两层。
                                               2026-11-19 两次收敛：①底面和「检索过程」统一；②压深一档到 #EDF3FE
                                               （表单控件 --field-bg 同一族），并补一条顶部内高光——
                                               原来 #F4F8FE 和回答卡底部几乎同色，这层读起来像卡片拖出来的尾巴，
                                               现在它是卡片里一块压下去的凹槽，引用行浮在里面 */
                                            <div className="mt-4 rounded-xl border border-[rgba(91,141,239,0.16)] bg-[#EDF3FE] p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.7)]">
                                                <div className="mb-2 flex items-center gap-2 text-[11.5px] text-[#66738F]">
                                                    <span className="tk-mark" aria-hidden />
                                                    <span className="font-medium text-[#3B4A63]">溯源引用</span>
                                                    <span className="tk-mono text-[10.5px] text-[#8A94AB]">{turn.sources.length} 条证据</span>
                                                    {/* 引用区默认收起：回答下面不再一开始就拖出十几张长卡；
                                                        点正文 [n] 会自动展开并滚到对应证据 */}
                                                    <button
                                                        type="button"
                                                        className="ml-auto -mr-1 flex items-center gap-1 rounded-[7px] px-1.5 py-0.5 text-[11px] text-[#2A57C6] transition-colors hover:bg-[rgba(91,141,239,0.12)] hover:text-[#1F47A8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)]"
                                                        onClick={() => toggleSourcesSection(i)}
                                                    >
                                                        {turn.sourcesOpen ? <UpOutlined /> : <DownOutlined />}
                                                        <span>{turn.sourcesOpen ? '收起引用' : '展开引用'}</span>
                                                    </button>
                                                    {turn.sourcesOpen && (
                                                        <button
                                                            type="button"
                                                            className="flex items-center gap-1 rounded-[7px] px-1.5 py-0.5 text-[11px] text-[#2A57C6] transition-colors hover:bg-[rgba(91,141,239,0.12)] hover:text-[#1F47A8] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(91,141,239,0.35)]"
                                                            onClick={() => toggleAllSources(i)}
                                                        >
                                                            {turn.sourcesExpanded ? <UpOutlined /> : <DownOutlined />}
                                                            <span>{turn.sourcesExpanded ? '全部收起' : '全部展开'}</span>
                                                        </button>
                                                    )}
                                                </div>
                                                {turn.sourcesOpen && (
                                                <div className="divide-y divide-[rgba(91,141,239,0.12)]">
                                                    {turn.sources.map((s, k) => (
                                                        <div key={k} id={`qa-source-${i}-${k}`} className="py-0.5 first:pt-0 last:pb-0">
                                                            <SourceCard
                                                                index={k}
                                                                source={s}
                                                                highlighted={highlightSource?.turn === i && highlightSource.n === k + 1}
                                                                expandAll={turn.sourcesExpanded}
                                                            />
                                                        </div>
                                                    ))}
                                                </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* 打开历史会话的加载态（2026-11-19）：只在慢请求时出现，
                        不遮整页、也不换掉旧对话——数据回来后由下面的列表整体替换 */}
                    {convOpening && (
                        <div className="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center">
                            <div className="flex items-center gap-2 rounded-full border border-[rgba(91,141,239,0.22)] bg-[rgba(251,253,255,0.94)] px-3 py-1.5 text-[12px] text-[#52607A] shadow-[0_10px_26px_-18px_rgba(15,23,42,0.5)] backdrop-blur">
                                <LoadingOutlined className="text-[#5B8DEF]" />
                                正在载入对话…
                            </div>
                        </div>
                    )}

                    {/* 输入区：一体化 composer（玻璃底 + 细描边 + 柔光；聚焦转品牌蓝；
                        等待输入时外圈有缓慢旋转的高光，参考 aiBank demo） */}
                    <div className="shrink-0 px-6 pt-3 pb-4">
                        <div className="relative max-w-3xl mx-auto">
                            {/* 回到最新：只有「有内容」且「视图没贴底」时才出现，
                                浮在输入框正上方居中，和 composer 共用同一条 768px 轴 */}
                            {turns.length > 0 && edgeFade.bottom && (
                                <div className="pointer-events-none absolute bottom-[calc(100%+10px)] left-1/2 z-[2] -translate-x-1/2">
                                    <Tip text="回到最新">
                                        <button
                                            type="button"
                                            onClick={jumpToLatest}
                                            aria-label="回到最新消息"
                                            className="qa-return-btn qa-return-in pointer-events-auto"
                                        >
                                            <ArrowDownOutlined />
                                        </button>
                                    </Tip>
                                </div>
                            )}
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
                                    <span className="flex items-center gap-1.5 text-[11px] text-[#A3ADC2] select-none">
                                        <FileSearchOutlined className="text-[#8A94AB]" />
                                        Enter 发送 · Shift + Enter 换行
                                    </span>
                                    {busy ? (
                                        <Button icon={<StopOutlined />} onClick={stop} className="qa-stop-btn">
                                            停止
                                        </Button>
                                    ) : (
                                        // 纸飞机图标 + 下方 Enter 发送说明已经够用，不再叠悬停提示
                                        <button
                                            type="button"
                                            aria-label="发送"
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
