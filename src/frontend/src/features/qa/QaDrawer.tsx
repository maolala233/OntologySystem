// src/features/qa/QaDrawer.tsx - 本体问答抽屉（M5，docs/design/05 §6 / 07 §4）
// SSE 流式对话（打字机渲染）+ 引用溯源列表（evidence 原句 + char 定位 + 文档预签名链接）
// + 置信度/推理路径展示 + 问答历史。项目选择器内置（默认取 URL 中的项目）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Drawer, Empty, Input, Popover, Spin, Tag, Tooltip, message } from 'antd';
import {
    BookOutlined,
    CaretRightOutlined,
    ClearOutlined,
    HistoryOutlined,
    LoadingOutlined,
    SendOutlined,
} from '@ant-design/icons';
import {
    askQuestionStream,
    getQaHistory,
    getQaSourceDetail,
    type QaDonePayload,
    type QaHistoryItem,
    type QaSource,
} from '../../api/qa';
import { projectsApi } from '../../api/projects';
import type { ProjectData } from '../../types/ontology';
import MarkdownAnswer from './MarkdownAnswer';
import SourceCard from './SourceCard';

interface QaTurn {
    question: string;
    answer: string;
    sources: QaSource[];
    done?: QaDonePayload;
    error?: string;
    streaming: boolean;
}

interface QaDrawerProps {
    open: boolean;
    onClose: () => void;
    /** 初始项目（可选；未提供时用 URL 末段数字或项目选择器） */
    projectId?: number | null;
}

export default function QaDrawer({ open, onClose, projectId: initialProjectId }: QaDrawerProps) {
    const [projectId, setProjectId] = useState<number | null>(initialProjectId ?? null);
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [showSelector, setShowSelector] = useState(false);
    const [question, setQuestion] = useState('');
    const [turns, setTurns] = useState<QaTurn[]>([]);
    const [busy, setBusy] = useState(false);
    const [historyOpen, setHistoryOpen] = useState(false);
    const [history, setHistory] = useState<QaHistoryItem[] | null>(null);
    // 正文 [n] 角标点击后：定位并高亮第 n 张引用卡（2.5s 后消退）
    const [highlightSource, setHighlightSource] = useState<number | null>(null);
    const highlightTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const abortRef = useRef<AbortController | null>(null);
    const streamEndRef = useRef<HTMLDivElement>(null);

    // 点击答案正文中的 [n]：滚动到对应引用卡 + 高亮（03 §15 引用溯源）
    const handleCite = useCallback((n: number) => {
        setHighlightSource(n);
        const el = document.getElementById(`qa-source-${n - 1}`);
        el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
        highlightTimerRef.current = setTimeout(() => setHighlightSource(null), 2500);
    }, []);

    useEffect(() => {
        if (open) {
            if (initialProjectId != null) {
                setProjectId(initialProjectId);
            } else {
                // URL 末段是数字时默认取当前项目（/projects/3/graph → 3）
                const parts = window.location.pathname.split('/');
                const last = parts[parts.length - 1];
                setProjectId(last && !isNaN(Number(last)) ? Number(last) : null);
            }
        }
    }, [open, initialProjectId]);

    useEffect(() => {
        streamEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [turns]);

    const loadProjects = useCallback(async () => {
        try {
            const list = await projectsApi.getMyProjects();
            setProjects(list);
            setShowSelector(true);
        } catch {
            message.error('加载项目列表失败');
        }
    }, []);

    const send = useCallback(async () => {
        const q = question.trim();
        if (!q) {
            message.warning('请输入问题');
            return;
        }
        if (!projectId) {
            message.warning('请先选择项目');
            return;
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
                                // done.answer 为服务端聚合全文（流中断时更完整）
                                answer: done.answer || cur.answer,
                            };
                            return next;
                        });
                        enrichSources(done.history_id);
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
                { signal: controller.signal },
            );
            // fetch 正常结束但未发 done（连接中断）时收尾
            setTurns(prev => {
                const next = [...prev];
                const cur = next[next.length - 1];
                if (cur.streaming) next[next.length - 1] = { ...cur, streaming: false };
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
    }, [question, projectId]);

    const clear = useCallback(() => {
        abortRef.current?.abort();
        setTurns([]);
        setHistory(null);
        setHighlightSource(null);
    }, []);

    const toggleHistory = useCallback(async () => {
        if (historyOpen || !projectId) {
            setHistoryOpen(false);
            return;
        }
        try {
            const items = await getQaHistory(projectId, 20);
            setHistory(items);
            setHistoryOpen(true);
        } catch {
            message.error('加载问答历史失败');
        }
    }, [historyOpen, projectId]);

    // 用 history 引用详情补齐文档预签名链接（点击引用卡跳转原文定位，03 §15）
    const enrichSources = useCallback(async (historyId: number) => {
        if (!projectId) return;
        try {
            const detail = await getQaSourceDetail(projectId, historyId);
            setTurns(prev => {
                const next = [...prev];
                const cur = next[next.length - 1];
                if (!cur || !cur.done || cur.done.history_id !== historyId) return prev;
                const byKey = (s: QaSource) => `${s.doc_file}|${s.quote?.slice(0, 50)}`;
                const detailMap = new Map((detail.sources ?? []).map(ds => [byKey(ds), ds]));
                next[next.length - 1] = {
                    ...cur,
                    sources: cur.sources.map(s => {
                        const hit = detailMap.get(byKey(s));
                        return hit ? { ...s, doc_url: hit.doc_url, doc_name: hit.doc_name } : s;
                    }),
                };
                return next;
            });
        } catch { /* 预签名失败不影响引用展示 */ }
    }, [projectId]);

    const canSend = !busy && question.trim().length > 0 && projectId != null;
    const body = useMemo(() => (
        <div className="flex flex-col h-full">
            {/* 项目条 */}
            <div className="px-4 py-2 border-b border-gray-100 flex items-center justify-between shrink-0">
                {projectId != null ? (
                    <div className="flex items-center gap-2 text-sm text-gray-600">
                        <BookOutlined className="text-blue-500" />
                        <span>项目 #{projectId}</span>
                        <Button size="small" type="text" onClick={loadProjects}>更换</Button>
                    </div>
                ) : (
                    <Button size="small" onClick={loadProjects}>选择项目</Button>
                )}
                <div className="flex items-center gap-1">
                    <Tooltip title="问答历史">
                        <Button size="small" type="text" icon={<HistoryOutlined />} onClick={toggleHistory} />
                    </Tooltip>
                    <Tooltip title="清空会话">
                        <Button size="small" type="text" icon={<ClearOutlined />} onClick={clear} disabled={busy} />
                    </Tooltip>
                </div>
            </div>

            {/* 项目选择器 */}
            {showSelector && (
                <div className="px-4 py-3 border-b border-gray-100 max-h-56 overflow-auto shrink-0">
                    <div className="text-xs text-gray-500 mb-2">选择问答项目</div>
                    {projects.length === 0 ? (
                        <div className="text-sm text-gray-400 py-4 text-center">暂无可用项目</div>
                    ) : (
                        projects.map(p => (
                            <div
                                key={p.id}
                                className={`p-2 rounded-lg mb-1 cursor-pointer border transition-all ${
                                    projectId === p.id
                                        ? 'border-blue-400 bg-blue-50'
                                        : 'border-gray-100 hover:border-blue-300 hover:bg-gray-50'
                                }`}
                                onClick={() => { setProjectId(p.id); setShowSelector(false); setTurns([]); setHistory(null); }}
                            >
                                <div className="flex items-center justify-between">
                                    <span className="text-sm font-medium text-gray-800">{p.name}</span>
                                    <Tag color={p.is_published ? 'green' : 'orange'}>
                                        {p.is_published ? '已发布' : (p as any).status === 'published' ? '已发布' : '草稿'}
                                    </Tag>
                                </div>
                                {p.description && (
                                    <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">{p.description}</div>
                                )}
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* 历史面板 */}
            {historyOpen && (
                <div className="px-4 py-3 border-b border-gray-100 max-h-64 overflow-auto shrink-0 bg-gray-50">
                    <div className="text-xs text-gray-500 mb-2">问答历史（最近 20 条）</div>
                    {history === null ? (
                        <Spin size="small" />
                    ) : history.length === 0 ? (
                        <div className="text-sm text-gray-400 py-2">暂无历史</div>
                    ) : (
                        history.map(h => (
                            <div key={h.id} className="py-1.5 border-b border-gray-100 last:border-0">
                                <div className="text-sm text-gray-800">Q：{h.question}</div>
                                <div className="text-xs text-gray-500 line-clamp-2 mt-0.5">A：{h.answer}</div>
                                <div className="text-[11px] text-gray-400 mt-0.5">
                                    {h.model || '-'} · {h.latency_ms}ms · 引用 {h.sources?.length ?? 0} 条
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* 对话区 */}
            <div className="flex-1 overflow-auto px-4 py-3">
                {turns.length === 0 && !showSelector && !historyOpen && (
                    <div className="h-full flex flex-col items-center justify-center text-gray-400">
                        <BookOutlined className="text-4xl mb-3" />
                        <p>基于本体知识图谱的检索问答</p>
                        <p className="text-xs mt-1">回答带 [n] 引用角标，点击角标可定位到对应溯源引用卡</p>
                    </div>
                )}
                {turns.map((turn, i) => (
                    <div key={i} className="mb-5">
                        <div className="flex justify-end mb-2">
                            <div className="max-w-[85%] px-3 py-2 rounded-2xl bg-blue-600 text-white text-sm whitespace-pre-wrap">
                                {turn.question}
                            </div>
                        </div>
                        <div className="max-w-[92%]">
                            {turn.error ? (
                                <div className="px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-600">
                                    问答失败：{turn.error}
                                </div>
                            ) : (
                                <div className="px-3 py-2 rounded-2xl bg-gray-100">
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
                                            {turn.streaming ? <LoadingOutlined /> : '（模型未返回内容）'}
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
                                            <Button size="small" type="link" className="!px-0 !text-[11px]">
                                                <CaretRightOutlined />推理路径
                                            </Button>
                                        </Popover>
                                    )}
                                </div>
                            )}
                            {turn.sources.length > 0 && (
                                <div className="mt-2">
                                    <div className="text-xs text-gray-500 mb-1">
                                        溯源引用（{turn.sources.length}）
                                    </div>
                                    <div className="space-y-1.5 max-h-64 overflow-auto">
                                        {turn.sources.map((s, k) => (
                                            <div key={k} id={`qa-source-${k}`}>
                                                <SourceCard
                                                    index={k}
                                                    source={s}
                                                    highlighted={highlightSource === k + 1}
                                                />
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

            {/* 输入区 */}
            <div className="p-3 border-t border-gray-100 shrink-0">
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
                <div className="flex justify-end mt-2">
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
    ), [projectId, projects, showSelector, historyOpen, history, turns, busy, question,
        canSend, send, clear, toggleHistory, loadProjects, enrichSources, highlightSource, handleCite]);

    return (
        <Drawer
            title={
                <div className="flex items-center gap-2">
                    <BookOutlined className="text-blue-500" />
                    <span>本体问答</span>
                    <Tag color="blue" className="ml-1">流式 · 引用溯源</Tag>
                </div>
            }
            placement="right"
            width={560}
            open={open}
            onClose={onClose}
            destroyOnClose={false}
            styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
        >
            {body}
        </Drawer>
    );
}

// ───────────────────────── 溯源引用卡已抽至 ./SourceCard（QaDrawer / QaPage 共用）
