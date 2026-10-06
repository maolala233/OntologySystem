// src/pages/QaPage.tsx - 本体问答独立页面（R8：docs/design/05 §6 / 07 §4）
// 左侧：新建对话 + 历史对话列表（按会话分组）；右侧：项目/模型选择 + 流式对话 + 引用溯源。
// 与 QaDrawer 共用 MarkdownAnswer（[n] 角标点击定位）与 SourceCard。
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, Popover, Select, Spin, Tag, Tooltip, message } from 'antd';
import {
    ClearOutlined,
    DeleteOutlined,
    LoadingOutlined,
    PlusOutlined,
    SendOutlined,
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

interface QaTurn {
    question: string;
    answer: string;
    sources: QaSource[];
    done?: QaDonePayload;
    error?: string;
    streaming: boolean;
}

const CONV_ID_KEY = 'qa_last_project_id';

export default function QaPage() {
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [projectId, setProjectId] = useState<number | null>(null);
    const [models, setModels] = useState<QaModelItem[]>([]);
    const [modelId, setModelId] = useState<number | null>(null);

    const [conversations, setConversations] = useState<QaConversationItem[]>([]);
    const [activeConvId, setActiveConvId] = useState<string | null>(null);

    const [turns, setTurns] = useState<QaTurn[]>([]);
    const [question, setQuestion] = useState('');
    const [busy, setBusy] = useState(false);
    const [highlightSource, setHighlightSource] = useState<number | null>(null);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const streamEndRef = useRef<HTMLDivElement>(null);

    // 项目选择：URL 无项目段，回落 localStorage / 第一个项目
    useEffect(() => {
        (async () => {
            try {
                const list = await projectsApi.getSelectableProjects();
                setProjects(list);
                const saved = Number(localStorage.getItem(CONV_ID_KEY));
                const initial = list.find(p => p.id === saved)?.id ?? list[0]?.id ?? null;
                setProjectId(initial);
            } catch {
                message.error('加载项目列表失败');
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

    useEffect(() => {
        streamEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [turns]);

    const handleCite = useCallback((n: number) => {
        setHighlightSource(n);
        document.getElementById(`qa-source-${n - 1}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = setTimeout(() => setHighlightSource(null), 2500);
    }, []);

    const newConversation = useCallback(() => {
        abortRef.current?.abort();
        setTurns([]);
        setActiveConvId(null);
        setQuestion('');
    }, []);

    const openConversation = useCallback(async (convId: string) => {
        if (projectId == null) return;
        abortRef.current?.abort();
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
            convId = crypto.randomUUID();
            setActiveConvId(convId);
        }
        setQuestion('');
        setBusy(true);
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
            }
        } finally {
            setBusy(false);
            abortRef.current = null;
        }
    }, [question, projectId, activeConvId, modelId, loadConversations]);

    const canSend = !busy && question.trim().length > 0 && projectId != null;
    const activeProject = projects.find(p => p.id === projectId);

    return (
        <div className="flex h-full">
            {/* 左侧：对话列表 */}
            <div className="w-64 shrink-0 border-r border-gray-200 bg-white flex flex-col">
                <div className="p-3">
                    <Button type="primary" icon={<PlusOutlined />} block onClick={newConversation}>
                        新建对话
                    </Button>
                </div>
                <div className="px-3 pb-1 text-xs text-gray-400">历史对话（{conversations.length}）</div>
                <div className="flex-1 overflow-auto px-2 pb-2">
                    {conversations.length === 0 ? (
                        <div className="text-xs text-gray-400 text-center py-6">暂无历史对话</div>
                    ) : (
                        conversations.map(c => (
                            <div
                                key={c.id}
                                onClick={() => openConversation(c.id)}
                                className={`group px-2 py-2 mb-1 rounded-lg cursor-pointer border transition-all ${
                                    activeConvId === c.id
                                        ? 'border-blue-400 bg-blue-50'
                                        : 'border-transparent hover:bg-gray-50'
                                }`}
                            >
                                <div className="flex items-center justify-between gap-1">
                                    <span className="text-sm text-gray-800 truncate flex-1">{c.title}</span>
                                    <Tooltip title="删除对话">
                                        <Button
                                            size="small" type="text" className="!px-1 opacity-0 group-hover:opacity-100"
                                            icon={<DeleteOutlined className="text-gray-400 hover:text-red-500" />}
                                            onClick={e => { e.stopPropagation(); removeConversation(c.id); }}
                                        />
                                    </Tooltip>
                                </div>
                                <div className="text-[11px] text-gray-400 mt-0.5 flex justify-between">
                                    <span>{c.message_count} 轮</span>
                                    <span>{c.last_time?.slice(0, 16).replace('T', ' ')}</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>

            {/* 右侧：对话区 */}
            <div className="flex-1 flex flex-col min-w-0 bg-gray-50">
                {/* 顶栏：项目 + 模型 */}
                <div className="h-14 px-4 bg-white border-b border-gray-200 flex items-center gap-3 shrink-0">
                    <Select
                        value={projectId ?? undefined}
                        onChange={v => setProjectId(v)}
                        placeholder="选择项目"
                        className="min-w-52"
                        showSearch
                        optionFilterProp="label"
                        options={projects.map(p => ({ value: p.id, label: p.name }))}
                    />
                    {activeProject && (
                        <Tag color={activeProject.is_published ? 'green' : 'orange'} className="mr-0">
                            {activeProject.is_published ? '已发布' : '草稿'}
                        </Tag>
                    )}
                    <div className="flex-1" />
                    <span className="text-xs text-gray-400">对话模型</span>
                    <Select
                        value={modelId ?? undefined}
                        onChange={v => setModelId(v)}
                        placeholder="默认模型"
                        className="min-w-56"
                        allowClear
                        onClear={() => setModelId(null)}
                        options={models.map(m => ({
                            value: m.id,
                            label: `${m.name}${m.is_default ? ' · 默认' : ''}`,
                        }))}
                        notFoundContent={<span className="text-xs text-gray-400">暂无可用模型，请在管理后台「模型配置」添加</span>}
                    />
                </div>

                {/* 消息区 */}
                <div className="flex-1 overflow-auto px-6 py-4">
                    {turns.length === 0 && (
                        <div className="h-full flex flex-col items-center justify-center text-gray-400">
                            <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-2xl mb-4">O</div>
                            <p className="text-gray-600">基于本体知识图谱的检索问答</p>
                            <p className="text-xs mt-1">回答带 [n] 引用角标，点击角标可定位到对应溯源引用卡</p>
                        </div>
                    )}
                    <div className="max-w-3xl mx-auto">
                        {turns.map((turn, i) => (
                            <div key={i} className="mb-6">
                                <div className="flex justify-end mb-2">
                                    <div className="max-w-[85%] px-3 py-2 rounded-2xl bg-blue-600 text-white text-sm whitespace-pre-wrap">
                                        {turn.question}
                                    </div>
                                </div>
                                <div>
                                    {turn.error ? (
                                        <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-600">
                                            问答失败：{turn.error}
                                        </div>
                                    ) : (
                                        <div className="px-4 py-3 rounded-2xl bg-white border border-gray-100 shadow-sm">
                                            {turn.answer ? (
                                                <>
                                                    <MarkdownAnswer
                                                        content={turn.answer}
                                                        sourceCount={turn.sources.length}
                                                        onCite={handleCite}
                                                    />
                                                    {turn.streaming && <LoadingOutlined className="ml-1 text-gray-400 text-xs" />}
                                                </>
                                            ) : (
                                                <div className="text-sm text-gray-800">
                                                    {turn.streaming ? <Spin size="small" /> : '（模型未返回内容）'}
                                                </div>
                                            )}
                                        </div>
                                    )}
                                    {turn.done && (
                                        <div className="flex items-center gap-2 mt-1.5 text-[11px] text-gray-400 flex-wrap">
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
                                        <div className="mt-2">
                                            <div className="text-xs text-gray-500 mb-1">
                                                溯源引用（{turn.sources.length}）
                                            </div>
                                            <div className="space-y-1.5">
                                                {turn.sources.map((s, k) => (
                                                    <div key={k} id={`qa-source-${k}`}>
                                                        <SourceCard index={k} source={s} highlighted={highlightSource === k + 1} />
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                        <div ref={streamEndRef} />
                    </div>
                </div>

                {/* 输入区 */}
                <div className="p-4 bg-white border-t border-gray-200 shrink-0">
                    <div className="max-w-3xl mx-auto">
                        <Input.TextArea
                            value={question}
                            onChange={e => setQuestion(e.target.value)}
                            placeholder="请输入问题，Ctrl+Enter 发送"
                            rows={2}
                            disabled={busy}
                            onPressEnter={e => {
                                if (!e.shiftKey) {
                                    e.preventDefault();
                                    if (canSend) send();
                                }
                            }}
                        />
                        <div className="flex items-center justify-between mt-2">
                            <Tooltip title="清空当前对话（不删除历史）">
                                <Button
                                    size="small" type="text" icon={<ClearOutlined />}
                                    onClick={newConversation} disabled={busy || turns.length === 0}
                                />
                            </Tooltip>
                            <Button
                                type="primary"
                                icon={<SendOutlined />}
                                onClick={send}
                                loading={busy}
                                disabled={!canSend}
                            >
                                {busy ? '生成中…' : '发送'}
                            </Button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
