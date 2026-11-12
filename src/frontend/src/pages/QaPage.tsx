// src/pages/QaPage.tsx - 本体问答独立页面（R8：docs/design/05 §6 / 07 §4）
// 左侧：新建对话 + 历史对话列表（按会话分组）；右侧：项目/模型选择 + 流式对话 + 引用溯源。
// 与 QaDrawer 共用 MarkdownAnswer（[n] 角标点击定位）与 SourceCard。
import { useCallback, useEffect, useRef, useState } from 'react';
import { message } from '../shared/feedback';
import { Button, Input, Popconfirm, Popover, Select, Tag, Tooltip } from 'antd';
import type { TextAreaRef } from 'antd/es/input/TextArea';
import { QA_LAST_PROJECT_ID_KEY } from '../shared/keys';
import BrandMark from '../components/BrandMark';
import Navbar from '../components/Layout/Navbar';
import { projectStatusMeta } from '../shared/projectStatus';
import {
    DeleteOutlined,
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

export default function QaPage() {
    const [projects, setProjects] = useState<ProjectData[]>([]);
    // 项目列表未回来前不展示「还没有可用项目」，否则空状态先错后对、整体跳一下
    const [projectsLoading, setProjectsLoading] = useState(true);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [models, setModels] = useState<QaModelItem[]>([]);
    const [modelId, setModelId] = useState<number | null>(null);

    const [conversations, setConversations] = useState<QaConversationItem[]>([]);
    const [activeConvId, setActiveConvId] = useState<string | null>(null);

    const [turns, setTurns] = useState<QaTurn[]>([]);
    const [question, setQuestion] = useState('');
    const [busy, setBusy] = useState(false);
    // 引用角标按「第几轮 + 第几条」定位：溯源卡 id 跨轮次不能重复
    const [highlightSource, setHighlightSource] = useState<{ turn: number; n: number } | null>(null);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);
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
        try {
            setConversations(await listQaConversations(pid, 50));
        } catch { /* 列表失败不阻断对话区 */ }
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
        setTurns([]);
        setActiveConvId(null);
        loadConversations(projectId);
        loadModels(projectId);
    }, [projectId, loadConversations, loadModels]);

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
    }, []);

    const handleCite = useCallback((turnIdx: number, n: number) => {
        setHighlightSource({ turn: turnIdx, n });
        document.getElementById(`qa-source-${turnIdx}-${n - 1}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = setTimeout(() => setHighlightSource(null), 2500);
    }, []);

    const newConversation = useCallback(() => {
        abortRef.current?.abort();
        stickBottomRef.current = true;
        setTurns([]);
        setActiveConvId(null);
        setQuestion('');
    }, []);

    const openConversation = useCallback(async (convId: string) => {
        if (projectId == null) return;
        abortRef.current?.abort();
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
    }, [projectId]);

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
        setTurns(prev => [...prev, { question: q, answer: '', sources: [], streaming: true }]);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
            await askQuestionStream(
                projectId,
                q,
                {
                    onToken: delta => {
                        setTurns(prev => {
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = { ...cur, answer: cur.answer + delta };
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
                        setTurns(prev => {
                            const next = [...prev];
                            const cur = next[next.length - 1];
                            next[next.length - 1] = {
                                ...cur, done, streaming: false,
                                answer: done.answer || cur.answer,
                            };
                            return next;
                        });
                        loadConversations(projectId);
                    },
                    onError: msg => {
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
            setBusy(false);
            abortRef.current = null;
        }
    }, [question, projectId, activeConvId, modelId, loadConversations]);

    // 停止生成：中断 SSE 请求；已产出的部分回答保留，本轮标记「已停止」
    const stop = useCallback(() => {
        abortRef.current?.abort();
        abortRef.current = null;
        setBusy(false);
        setTurns(prev => {
            const next = [...prev];
            const cur = next[next.length - 1];
            if (cur?.streaming) next[next.length - 1] = { ...cur, streaming: false, stopped: true };
            return next;
        });
    }, []);

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
    const activeStatusMeta = activeProject ? projectStatusMeta(activeProject) : null;

    return (
        <div className="h-full flex flex-col app-canvas">
            {/* 全站顶栏：面包屑 + 账号（与工作台/我的项目/资产中心共用，切页不再「失去顶栏」） */}
            <Navbar breadcrumbs={[{ title: '首页', path: '/' }, { title: '工具' }, { title: '本体问答' }]} />

            <div className="flex flex-1 min-h-0">
                {/* 左侧：会话历史 */}
                <aside className="w-64 shrink-0 border-r border-[#E6EBF5] bg-white flex flex-col">
                    <div className="p-3 pb-2">
                        <Button type="primary" icon={<PlusOutlined />} block onClick={newConversation}>
                            新建对话
                        </Button>
                    </div>
                    <div className="px-4 pb-2 text-[11px] font-medium tracking-wide text-gray-400 select-none">
                        历史对话（{conversations.length}）
                    </div>
                    <div className="flex-1 overflow-auto px-2 pb-2">
                        {conversations.length === 0 ? (
                            <div className="flex flex-col items-center justify-center text-center py-10 px-3">
                                <MessageOutlined className="text-2xl text-gray-300 mb-2" />
                                <div className="text-xs text-gray-400">暂无历史对话</div>
                                <div className="text-[11px] text-gray-300 mt-1">提问后会按会话归档在这里</div>
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
                                            ? 'border-[rgba(91,141,239,0.45)] bg-[rgba(91,141,239,0.08)]'
                                            : 'border-transparent hover:bg-[#F5F8FD]'
                                    }`}
                                >
                                    <div className="flex items-center justify-between gap-1">
                                        <span className={`text-sm truncate flex-1 ${activeConvId === c.id ? 'text-[#3A5FB8] font-medium' : 'text-gray-800'}`}>
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
                                                    icon={<DeleteOutlined className="text-gray-400 hover:text-red-500" />}
                                                    onClick={e => e.stopPropagation()}
                                                />
                                            </Tooltip>
                                        </Popconfirm>
                                    </div>
                                    <div className="text-[11px] text-gray-400 mt-0.5 flex justify-between">
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
                    {/* 二级工具条：项目 + 模型。放在画布上（不自建白色页头），避免与全站顶栏叠成两条白条 */}
                    <div className="shrink-0 px-6 pt-3 pb-1 flex flex-wrap items-center gap-x-3 gap-y-2">
                        <span className="text-xs text-gray-500 shrink-0">项目</span>
                        <Select
                            value={projectId ?? undefined}
                            onChange={v => setProjectId(v)}
                            placeholder="选择项目"
                            className="w-52"
                            showSearch
                            optionFilterProp="label"
                            options={projects.map(p => ({ value: p.id, label: p.name }))}
                        />
                        {activeStatusMeta && (
                            <Tag color={activeStatusMeta.color} className="mr-0">{activeStatusMeta.text}</Tag>
                        )}
                        <div className="flex-1" />
                        <span className="text-xs text-gray-500 shrink-0">对话模型</span>
                        <Select
                            value={modelId ?? undefined}
                            onChange={v => setModelId(v)}
                            placeholder="默认模型"
                            className="w-56"
                            allowClear
                            onClear={() => setModelId(null)}
                            options={models.map(m => ({
                                value: m.id,
                                label: `${m.name}${m.is_default ? ' · 默认' : ''}`,
                            }))}
                            notFoundContent={<span className="text-xs text-gray-500">暂无可用模型，请在管理后台「模型配置」添加</span>}
                        />
                    </div>

                    {/* 消息区 */}
                    <div
                        ref={listRef}
                        onScroll={updateEdgeFade}
                        style={{ WebkitMaskImage: edgeMask, maskImage: edgeMask }}
                        className="flex-1 overflow-auto px-6 pb-2 [scrollbar-gutter:stable_both-edges]"
                    >
                        {turns.length === 0 && (
                            <div className="h-full flex flex-col items-center justify-center text-center px-6">
                                <div className="w-14 h-14 rounded-2xl flex items-center justify-center mb-4"
                                  style={{ background: 'linear-gradient(135deg, #5B8DEF, #8B5CF6)' }}>
                                    <BrandMark size={32} strokeWidth={3.5} />
                                </div>
                                <p className="text-gray-600 font-medium">基于本体知识图谱的检索问答</p>
                                <p className="text-xs text-gray-400 mt-1">回答带 [n] 引用角标，点击角标可定位到对应溯源引用卡</p>
                                {/* 提示行固定占位、示例问题常驻：加载前后结构不变，避免空状态整体上移 */}
                                <p className="mt-4 h-5 text-xs text-amber-600">
                                    {!projectsLoading && projectId == null
                                        ? '还没有可用项目，请先在「我的项目」创建并完成建模'
                                        : ''}
                                </p>
                                <div className="mt-3 flex flex-wrap justify-center gap-2 max-w-lg">
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
                                                className={`px-3 py-1.5 rounded-full border text-xs transition-colors ${disabled
                                                    ? 'border-[#EDF1F8] bg-white/40 text-gray-300 cursor-not-allowed'
                                                    : 'border-[#E6EBF5] bg-white/70 text-gray-600 hover:border-[#5B8DEF] hover:text-[#4C7DE7]'
                                                    }`}
                                            >
                                                {q}
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                        <div className="max-w-3xl mx-auto">
                            {turns.map((turn, i) => (
                                <div key={i} className="pt-4">
                                    <div className="flex justify-end mb-2">
                                        <div className="max-w-[85%] px-3 py-2 rounded-2xl text-white text-sm whitespace-pre-wrap shadow-[0_8px_20px_-12px_rgba(91,141,239,0.55)]" style={{ background: 'linear-gradient(135deg, #5B8DEF, #6C7CF5)' }}>
                                            {turn.question}
                                        </div>
                                    </div>
                                    <div>
                                        {turn.error ? (
                                            <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-600">
                                                问答失败：{turn.error}
                                            </div>
                                        ) : (
                                            <div className="px-4 py-3 rounded-2xl bg-white border border-[#E7ECF6] shadow-sm">
                                                {turn.answer ? (
                                                    <>
                                                        <MarkdownAnswer
                                                            content={turn.answer}
                                                            sourceCount={turn.sources.length}
                                                            onCite={n => handleCite(i, n)}
                                                        />
                                                        {turn.streaming && <LoadingOutlined className="ml-1 text-gray-500 text-xs" />}
                                                    </>
                                                ) : (
                                                    <div className="flex items-center gap-2 text-sm text-gray-500">
                                                        {turn.streaming ? (
                                                            <>
                                                                <LoadingOutlined className="text-[#5B8DEF]" />
                                                                <span>正在检索本体图谱…</span>
                                                            </>
                                                        ) : '（模型未返回内容）'}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                        {turn.stopped && (
                                            <div className="mt-1 text-[11px] text-gray-400">已停止生成</div>
                                        )}
                                        {turn.done && (
                                            <div className="flex items-center gap-2 mt-1.5 text-[11px] text-gray-500 flex-wrap">
                                                {turn.done.confidence > 0 && (
                                                    <Tag color={turn.done.confidence >= 0.6 ? 'green' : 'orange'} className="mr-0">
                                                        置信度 {(turn.done.confidence * 100).toFixed(0)}%
                                                    </Tag>
                                                )}
                                                {turn.done.model && <span>{turn.done.model}</span>}
                                                <span>{turn.done.latency_ms}ms</span>
                                                {turn.done.reasoning_path?.length > 0 && (
                                                    <Popover
                                                        content={(
                                                            <ol className="list-decimal pl-4 text-xs max-w-xs">
                                                                {turn.done.reasoning_path.map((s, k) => <li key={k}>{s}</li>)}
                                                            </ol>
                                                        )}
                                                        title="推理路径"
                                                    >
                                                        <Button size="small" type="link" className="!px-0 !text-[11px]">推理路径</Button>
                                                    </Popover>
                                                )}
                                            </div>
                                        )}
                                        {turn.sources.length > 0 && (
                                            /* 溯源证据区：浅底容器把「引用卡」和「答案正文」分成两层，白卡不再直接叠白卡 */
                                            <div className="mt-2 rounded-xl border border-[#EAF0F9] bg-[#F7F9FD] p-2.5">
                                                <div className="flex items-center gap-1.5 text-xs text-gray-500 mb-2">
                                                    <FileSearchOutlined className="text-[#5B8DEF]" />
                                                    <span>溯源引用（{turn.sources.length}）</span>
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
                            ))}
                        </div>
                    </div>

                    {/* 输入区：一体化 composer（外框圆角 + 细描边 + 柔和投影，聚焦转品牌蓝；
                        输入框无边框随内容增高，底部一行放快捷键提示与发送/停止） */}
                    <div className="shrink-0 px-6 pt-3 pb-4">
                        <div className="max-w-3xl mx-auto">
                            <div className="rounded-2xl border border-[#E6EBF5] bg-white px-3.5 pt-3 pb-2.5 shadow-[0_14px_34px_-20px_rgba(15,23,42,0.32)] transition-[border-color,box-shadow] duration-200 focus-within:border-[#5B8DEF] focus-within:shadow-[0_16px_38px_-18px_rgba(91,141,239,0.42)]">
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
                                    <span className="text-[11px] text-gray-400 select-none">
                                        Enter 发送 · Shift + Enter 换行
                                    </span>
                                    {busy ? (
                                        <Button danger icon={<StopOutlined />} onClick={stop}>
                                            停止
                                        </Button>
                                    ) : (
                                        <Button
                                            type="primary"
                                            icon={<SendOutlined />}
                                            onClick={send}
                                            disabled={!canSend}
                                        >
                                            发送
                                        </Button>
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
