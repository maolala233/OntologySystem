// src/components/Copilot/CopilotWidget.tsx - 全局悬浮 Copilot 小窗（O5-F12，docs/design/14 §5）
// 挂在 AppLayout 层：右下角悬浮球 → 可拖动小窗（默认 380×560，可收起为球），
// 任意页面常驻；项目上下文自动跟随路由（构建器内选中元素经 copilotBus 随消息携带）。
// 消息渲染复用问答页 MarkdownAnswer；SSE 契约同问答（stage/token/done/error）。
import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useParams } from 'react-router-dom';
import { Button, Popover, Select, Tooltip, message } from 'antd';
import {
    CaretUpOutlined,
    ClearOutlined,
    CloseOutlined,
    HistoryOutlined,
    PlusOutlined,
    RobotOutlined,
    SendOutlined,
    StopOutlined,
} from '@ant-design/icons';
import {
    deleteCopilotSession,
    getCopilotSession,
    listCopilotModels,
    listCopilotProposals,
    listCopilotSessions,
    sendCopilotMessage,
    type CopilotClarify,
    type CopilotModelItem,
    type CopilotProposalItem,
    type CopilotSessionItem,
    type CopilotStage,
    type CopilotToolEvent,
} from '../../api/copilot';
import { projectsApi } from '../../api/projects';
import type { ProjectData } from '../../types/ontology';
import type { CopilotVerifyReport } from '../../api/copilot';
import { getCopilotSelection } from '../../shared/copilotBus';
import MarkdownAnswer from '../../features/qa/MarkdownAnswer';
import ProposalCard from './ProposalCard';
import VerifyReportCard from './VerifyReportCard';
import ToolSteps, { stepsFromCalls, stepsFromEvents, type ToolStep } from './ToolSteps';
import ClarifyOptions from './ClarifyOptions';

const POS_KEY = 'copilot_widget_pos';
const OPEN_KEY = 'copilot_widget_open';
const SIZE_KEY = 'copilot_widget_size';
const DEFAULT_POS = { right: 24, bottom: 24 }; // 右下角锚定，拖动后改为 left/top
const DEFAULT_SIZE = { w: 380, h: 560 };
// 缩放上下界：下界保证头部/输入区可用，上界不铺满全屏（留出边距可见页面）
const MIN_W = 320;
const MIN_H = 420;
const MAX_W_RATIO = 0.9;
const MAX_H_RATIO = 0.95;

interface Msg {
    role: 'user' | 'assistant';
    content: string;
    stages?: CopilotStage[];
    error?: string;
    streaming?: boolean;
    latency_ms?: number | null;
    proposal?: CopilotProposalItem | null;
    /** S14：一句话多操作 → 多提案卡（按依赖顺序渲染，逐卡应用） */
    proposals?: CopilotProposalItem[] | null;
    /** B26：澄清轮（问题 + ≤3 选项含"其他"，F14/F16 渲染确认式选项组） */
    clarify?: CopilotClarify | null;
    /** B28：验证报告（F15 渲染三段报告卡）；msgId 供存档按钮定位 */
    report?: CopilotVerifyReport | null;
    /** B31/F16：工具调用步骤（名称+耗时+可展开输出） */
    toolSteps?: ToolStep[];
    /** S6：只读项目编辑诉求 + 用户有取消发布权 → 渲染「取消发布并调整」按钮 */
    unpublishOffer?: boolean;
    msgId?: number;
}

function loadPos(): { right: number; bottom: number } | { left: number; top: number } {
    try {
        const raw = localStorage.getItem(POS_KEY);
        if (raw) return JSON.parse(raw);
    } catch { /* 忽略脏数据 */ }
    return { ...DEFAULT_POS };
}

const sizeBounds = () => ({
    maxW: Math.max(MIN_W, Math.floor(window.innerWidth * MAX_W_RATIO)),
    maxH: Math.max(MIN_H, Math.floor(window.innerHeight * MAX_H_RATIO)),
});

const clampSize = (w: number, h: number) => {
    const { maxW, maxH } = sizeBounds();
    return {
        w: Math.min(maxW, Math.max(MIN_W, Math.round(w))),
        h: Math.min(maxH, Math.max(MIN_H, Math.round(h))),
    };
};

function loadSize(): { w: number; h: number } {
    try {
        const raw = localStorage.getItem(SIZE_KEY);
        if (raw) {
            const s = JSON.parse(raw);
            if (Number.isFinite(s?.w) && Number.isFinite(s?.h)) return clampSize(s.w, s.h);
        }
    } catch { /* 忽略脏数据 */ }
    return { ...DEFAULT_SIZE };
}

export default function CopilotWidget() {
    const location = useLocation();
    const params = useParams<{ projectId?: string }>();
    // 项目上下文自动跟随：路由有 :projectId 即为当前项目（构建器/记分卡等）
    const routePid = Number((params.projectId as string) || location.pathname.match(/\/projects\/(\d+)/)?.[1]);
    // 仅项目构建流程（构建器五 Tab）可呼出；非构建页 return null 隐藏——
    // 组件仍保持挂载（单例）：离开再回来聊天记录/输入不丢
    const isBuilderRoute = /^\/projects\/\d+\/(documents|schema|graph|timeline|reviews)(\/|$)/.test(location.pathname);

    const [open, setOpen] = useState(() => localStorage.getItem(OPEN_KEY) === '1');
    const [pos, setPos] = useState(loadPos);
    const [size, setSize] = useState(loadSize);
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [projectId, setProjectId] = useState<number | null>(
        Number.isFinite(routePid) && routePid > 0 ? routePid : null,
    );
    const [models, setModels] = useState<CopilotModelItem[]>([]);
    const [modelId, setModelId] = useState<number | null>(null);
    const [sessions, setSessions] = useState<CopilotSessionItem[]>([]);
    const [sessionId, setSessionId] = useState<number | null>(null);
    const [msgs, setMsgs] = useState<Msg[]>([]);
    const [input, setInput] = useState('');
    const [busy, setBusy] = useState(false);
    const [stage, setStage] = useState<string>('');
    const abortRef = useRef<AbortController | null>(null);
    const bodyRef = useRef<HTMLDivElement>(null);
    const rootRef = useRef<HTMLDivElement>(null);
    const dragRef = useRef<{ dx: number; dy: number } | null>(null);
    const resizeRef = useRef<{
        dir: string; sx: number; sy: number;
        rect: { left: number; top: number; w: number; h: number };
    } | null>(null);

    // 路由切换 → 项目上下文跟随（14 §5 F12：上下文自动跟随当前路由）
    useEffect(() => {
        if (Number.isFinite(routePid) && routePid > 0) setProjectId(routePid);
    }, [routePid]);

    useEffect(() => {
        (async () => {
            try {
                setProjects(await projectsApi.getSelectableProjects());
            } catch { /* 项目列表失败不阻断小窗 */ }
        })();
    }, []);

    // 工具栏按钮开合（BuilderPage 头部 CopilotToggle 派发；悬浮球已取消——
    // 用户反馈固定悬浮在页角与布局不搭，入口收进构建器顶栏）
    useEffect(() => {
        const onToggle = () => setOpen(v => !v);
        window.addEventListener('copilot-toggle', onToggle);
        return () => window.removeEventListener('copilot-toggle', onToggle);
    }, []);

    const refreshSessions = useCallback(async (pid: number) => {
        try { setSessions(await listCopilotSessions(pid, 50)); } catch { /* 忽略 */ }
    }, []);

    useEffect(() => {
        if (projectId == null) { setModels([]); setSessions([]); setMsgs([]); setSessionId(null); return; }
        (async () => {
            try {
                const items = await listCopilotModels(projectId);
                setModels(items);
                // 默认选中：显式默认项 > 第一个可用模型（legacy 默认链不可用时保证开箱即用）
                setModelId(items.find(m => m.is_default)?.id ?? items[0]?.id ?? null);
            } catch { setModels([]); }
        })();
        setMsgs([]);
        setSessionId(null);
        setStage('');
        refreshSessions(projectId);
    }, [projectId, refreshSessions]);

    useEffect(() => {
        bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' });
    }, [msgs, stage]);

    const toggleOpen = useCallback(() => {
        setOpen(v => {
            localStorage.setItem(OPEN_KEY, v ? '0' : '1');
            return !v;
        });
    }, []);

    // 拖动（header 按下 → window move/up）
    const onDragStart = (e: React.MouseEvent) => {
        const el = (e.currentTarget.parentElement as HTMLElement);
        const rect = el.getBoundingClientRect();
        dragRef.current = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
        const move = (ev: MouseEvent) => {
            if (!dragRef.current) return;
            const left = Math.max(0, Math.min(window.innerWidth - 120, ev.clientX - dragRef.current.dx));
            const top = Math.max(0, Math.min(window.innerHeight - 80, ev.clientY - dragRef.current.dy));
            setPos({ left, top });
        };
        const up = () => {
            dragRef.current = null;
            window.removeEventListener('mousemove', move);
            window.removeEventListener('mouseup', up);
            setPos(p => {
                localStorage.setItem(POS_KEY, JSON.stringify(p));
                return p;
            });
        };
        window.addEventListener('mousemove', move);
        window.addEventListener('mouseup', up);
    };

    // 边/角缩放（8 向）：反向边锚定，宽度高度夹在 [MIN, 视口占比上限] 内，且整体不出视口
    const onResizeStart = (dir: string) => (e: React.MouseEvent) => {
        e.preventDefault();
        e.stopPropagation();
        const el = rootRef.current;
        if (!el) return;
        const r = el.getBoundingClientRect();
        // 统一转 left/top 锚定，避免 right/bottom 锚定与西/北向缩放互相打架
        setPos({ left: r.left, top: r.top });
        resizeRef.current = { dir, sx: e.clientX, sy: e.clientY, rect: { left: r.left, top: r.top, w: r.width, h: r.height } };
        const move = (ev: MouseEvent) => {
            const st = resizeRef.current;
            if (!st) return;
            const dx = ev.clientX - st.sx;
            const dy = ev.clientY - st.sy;
            const { maxW, maxH } = sizeBounds();
            let { left, top, w, h } = st.rect;
            if (st.dir.includes('e')) w = Math.min(maxW, Math.max(MIN_W, st.rect.w + dx));
            if (st.dir.includes('s')) h = Math.min(maxH, Math.max(MIN_H, st.rect.h + dy));
            if (st.dir.includes('w')) {
                w = Math.min(maxW, Math.max(MIN_W, st.rect.w - dx));
                left = st.rect.left + (st.rect.w - w); // 右缘锚定
            }
            if (st.dir.includes('n')) {
                h = Math.min(maxH, Math.max(MIN_H, st.rect.h - dy));
                top = st.rect.top + (st.rect.h - h); // 下缘锚定
            }
            left = Math.max(0, Math.min(window.innerWidth - MIN_W, left));
            top = Math.max(0, Math.min(window.innerHeight - MIN_H, top));
            setSize({ w, h });
            setPos({ left, top });
        };
        const up = () => {
            resizeRef.current = null;
            window.removeEventListener('mousemove', move);
            window.removeEventListener('mouseup', up);
            setSize(s => { localStorage.setItem(SIZE_KEY, JSON.stringify(s)); return s; });
            setPos(p => { localStorage.setItem(POS_KEY, JSON.stringify(p)); return p; });
        };
        window.addEventListener('mousemove', move);
        window.addEventListener('mouseup', up);
    };

    const newSession = useCallback(() => {
        abortRef.current?.abort();
        setMsgs([]);
        setSessionId(null);
        setStage('');
    }, []);

    const openSession = useCallback(async (sid: number) => {
        if (projectId == null) return;
        abortRef.current?.abort();
        try {
            const [detail, props] = await Promise.all([
                getCopilotSession(projectId, sid),
                listCopilotProposals(projectId, sid).catch(() => []),
            ]);
            const statusByPid = new Map(props.map(p => [p.proposal_id, p]));
            setSessionId(detail.id);
            // 回放：user/assistant 消息配对还原为气泡；提案消息对齐服务端状态（F13）；
            // 澄清消息从 tool_calls 轨迹还原选项组（F14）
            const out: Msg[] = [];
            for (const m of detail.messages) {
                if (m.role === 'user') out.push({ role: 'user', content: m.content || '' });
                else {
                    const obs = (m.tool_calls || []).find(t => t.tool === 'generate_proposal')
                        ?.observation as { proposal?: CopilotProposalItem } | undefined;
                    const clrObs = (m.tool_calls || []).find(t => t.tool === 'generate_clarification')
                        ?.observation as { clarify?: CopilotClarify } | undefined;
                    const live = obs?.proposal ? statusByPid.get(obs.proposal.proposal_id) : null;
                    out.push({
                        role: 'assistant', content: m.content || '', stages: m.stages,
                        latency_ms: m.latency_ms,
                        error: m.status === 'failed' ? '该轮回答失败' : undefined,
                        clarify: clrObs?.clarify ?? undefined,
                        report: m.report ?? undefined,
                        // B31/F16：回放从 tool_calls 还原工具步骤（含耗时与输出）
                        toolSteps: stepsFromCalls(m.tool_calls || []),
                        msgId: m.id,
                        proposal: obs?.proposal
                            ? { ...obs.proposal, ...(live ? {
                                  status: live.status, result: live.result,
                                  base_version: live.base_version } : {}) }
                            : undefined,
                    });
                }
            }
            setMsgs(out);
        } catch { message.error('加载会话失败'); }
    }, [projectId]);

    const removeSession = useCallback(async (sid: number) => {
        if (projectId == null) return;
        try {
            await deleteCopilotSession(projectId, sid);
            if (sessionId === sid) newSession();
            refreshSessions(projectId);
        } catch { message.error('删除会话失败'); }
    }, [projectId, sessionId, newSession, refreshSessions]);

    const send = useCallback(async (overrideText?: string) => {
        const text = (overrideText ?? input).trim();
        if (!text || busy) return;
        if (projectId == null) { message.warning('请先选择项目'); return; }
        setInput('');
        setBusy(true);
        setStage('整理画布上下文…');
        const sel = getCopilotSelection();
        // 构建器内选中元素随消息携带（F12：针对选中的东西说话）
        const prefix = sel && (sel.nodes.length || sel.edges.length)
            ? `（我当前选中了：${[...sel.nodes.map(n => `类/实例「${n.label}」`),
                ...sel.edges.map(e => `关系「${e.label}」`)].join('、')}）\n`
            : '';
        setMsgs(prev => [...prev, { role: 'user', content: prefix + text },
                                   { role: 'assistant', content: '', streaming: true }]);
        const controller = new AbortController();
        abortRef.current = controller;
        let acc = '';
        const toolEvents: CopilotToolEvent[] = [];
        const patchLast = (patch: Partial<Msg>) => setMsgs(prev => {
            const next = [...prev];
            next[next.length - 1] = { ...next[next.length - 1], ...patch };
            return next;
        });
        try {
            await sendCopilotMessage(projectId, text, {
                onMeta: meta => { if (!sessionId) setSessionId(meta.session_id); },
                onStage: s => setStage(s.detail || s.stage),
                onTool: ev => {
                    // B31/F16：工具步骤实时追加（start 执行中 → end 落耗时与输出）
                    toolEvents.push(ev);
                    patchLast({ toolSteps: stepsFromEvents([...toolEvents]) });
                },
                onToken: delta => {
                    acc += delta;
                    patchLast({ content: acc });
                },
                onDone: done => {
                    patchLast({
                        content: done.answer || acc, streaming: false,
                        latency_ms: done.latency_ms,
                        // F13：编辑提案随 done 返回 → 消息挂提案卡
                        proposal: done.proposal ?? undefined,
                        proposals: (done as any).proposals ?? undefined,
                        // B26：澄清态随 done 返回 → 消息挂确认式选项组（F14/F16）
                        clarify: done.clarify?.exhausted ? undefined : (done.clarify ?? undefined),
                        // B28：验证报告随 done 返回 → 消息挂三段报告卡（F15；实时流暂无 msgId，
                        // 存档从历史会话进入或下轮回放后可用）
                        report: done.verify ?? undefined,
                        // B31/F16：done.tool_calls 为准（防丢事件），与实时步骤合并去重
                        toolSteps: stepsFromCalls(done.tool_calls || []).length
                            ? stepsFromCalls(done.tool_calls || [])
                            : stepsFromEvents([...toolEvents]),
                        // S6：只读编辑诉求 + 有取消发布权 → 挂「取消发布并调整」按钮
                        unpublishOffer: done.unpublish_offer ?? undefined,
                    });
                    setStage('');
                    refreshSessions(projectId);
                },
                onError: msg => { patchLast({ streaming: false, error: msg }); setStage(''); },
            }, {
                session_id: sessionId,
                model_config_id: modelId,
                selected: sel,
                signal: controller.signal,
            });
            patchLast({ streaming: false });
        } catch (e: any) {
            if (e?.name !== 'AbortError') {
                patchLast({ streaming: false, error: e?.message || 'Copilot 请求失败' });
            } else {
                patchLast({ streaming: false });
            }
        } finally {
            setBusy(false);
            setStage('');
            abortRef.current = null;
        }
    }, [input, busy, projectId, sessionId, modelId, refreshSessions]);

    // S6：一键「取消发布并调整」——点按钮=用户确认；取消发布（接口自身有 owner 权限闸+审计）
    // 成功后同步本地项目状态，并自动重发原编辑诉求（服务端重新判权 → 走正常提案/澄清流）。
    // 原消息可能已带「（我当前选中了：…）」前缀，重发前剥掉，避免 send() 再拼一次出现两遍。
    const unpublishAndAdjust = useCallback(async (msgIdx: number) => {
        if (projectId == null) return;
        const raw = [...msgs.slice(0, msgIdx)].reverse().find(m => m.role === 'user')?.content;
        const original = raw?.replace(/^(?:（我当前选中了：[^）]*）\n)+/, '');
        try {
            await projectsApi.unpublishProject(projectId);
            setProjects(prev => prev.map(p => p.id === projectId ? { ...p, is_published: false } : p));
            message.success('已取消发布，继续处理你的调整请求');
            if (original) send(original);
        } catch (e: any) {
            message.error(e?.response?.data?.detail || e?.message || '取消发布失败');
        }
    }, [projectId, msgs, send]);

    const canSend = !busy && input.trim().length > 0 && projectId != null;
    const project = projects.find(p => p.id === projectId);

    const sessionList = (
        <div className="w-64 max-h-80 overflow-auto">
            {/* 未选项目时会话无从查起：给指路提示而不是空白（历史会话按项目隔离） */}
            {projectId == null && (
                <div className="text-xs text-gray-500 text-center py-4 leading-5">
                    请先在上方选择项目，<br />再查看该项目的历史会话
                </div>
            )}
            {projectId != null && sessions.length === 0 && (
                <div className="text-xs text-gray-500 text-center py-4">暂无历史会话</div>
            )}
            {sessions.map(s => (
                <div key={s.id}
                     className={`group flex items-center gap-1 px-2 py-1.5 rounded-md cursor-pointer text-sm ${sessionId === s.id ? 'bg-blue-50 text-blue-700' : 'hover:bg-gray-50'}`}
                     onClick={() => openSession(s.id)}>
                    <span className="truncate flex-1">{s.title}</span>
                    <button className="text-[10px] text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100"
                            onClick={e => { e.stopPropagation(); removeSession(s.id); }}>删</button>
                </div>
            ))}
        </div>
    );

    if (!isBuilderRoute) {
        return null;
    }

    if (!open) {
        return null;
    }

    const posStyle: React.CSSProperties = 'left' in pos
        ? { left: pos.left, top: pos.top }
        : { right: pos.right, bottom: pos.bottom };

    // 8 向缩放手柄：4px 边 + 12px 角（贴内缘放置，不与 overflow-hidden 冲突）
    const RESIZE_DIRS = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const;
    const HANDLE_CURSOR: Record<string, string> = {
        n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
        ne: 'nesw-resize', sw: 'nesw-resize', nw: 'nwse-resize', se: 'nwse-resize',
    };
    const handleStyle = (d: string): React.CSSProperties => {
        const edge = { position: 'absolute' as const, zIndex: 30 };
        if (d === 'n') return { ...edge, top: 0, left: 10, right: 10, height: 4, cursor: HANDLE_CURSOR[d] };
        if (d === 's') return { ...edge, bottom: 0, left: 10, right: 10, height: 4, cursor: HANDLE_CURSOR[d] };
        if (d === 'e') return { ...edge, right: 0, top: 10, bottom: 10, width: 4, cursor: HANDLE_CURSOR[d] };
        if (d === 'w') return { ...edge, left: 0, top: 10, bottom: 10, width: 4, cursor: HANDLE_CURSOR[d] };
        const corner: React.CSSProperties = { width: 12, height: 12 };
        if (d === 'ne') return { ...edge, ...corner, top: 0, right: 0, cursor: HANDLE_CURSOR[d] };
        if (d === 'nw') return { ...edge, ...corner, top: 0, left: 0, cursor: HANDLE_CURSOR[d] };
        if (d === 'se') return { ...edge, ...corner, bottom: 0, right: 0, cursor: HANDLE_CURSOR[d] };
        return { ...edge, ...corner, bottom: 0, left: 0, cursor: HANDLE_CURSOR[d] }; // sw
    };

    return (
        <div ref={rootRef}
             className="fixed z-[1100] bg-white rounded-xl shadow-2xl border border-gray-200
                        flex flex-col overflow-hidden"
             style={{ ...posStyle, width: size.w, height: size.h }}>
            {/* 8 向缩放手柄 */}
            {RESIZE_DIRS.map(d => (
                <div key={d} style={handleStyle(d)} onMouseDown={onResizeStart(d)} />
            ))}
            {/* 头部：项目 + 模型 + 会话（渐变走内联 style 兜底——用户环境曾出现
                Tailwind 渐变类不生效导致白底白字、头部整条看不见的问题） */}
            <div className="h-11 px-3 bg-gradient-to-r from-blue-600 to-purple-600 text-white flex items-center gap-2 shrink-0 cursor-move"
                 style={{ background: 'linear-gradient(90deg, #2563eb, #9333ea)' }}
                 onMouseDown={onDragStart}>
                <RobotOutlined />
                <span className="text-sm font-medium">本体协同助手</span>
                {/* 下拉只拦自身 mousedown（防拖拽吞掉 Select 触发）；外圈空白归还头部拖拽热区 */}
                <div className="flex-1 flex justify-end min-w-0">
                    {/* S15：项目跟随当前页面（构建器路由上下文），不可在小窗内切换——
                        避免在 A 项目画布上操作 B 项目的画布造成误导与误改 */}
                    <div onClick={e => e.stopPropagation()} onMouseDown={e => e.stopPropagation()}>
                        <Tooltip title="助手跟随当前进入的项目，如需切换请从「我的项目」进入对应项目">
                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-xs
                                             bg-white/20 border border-white/30 text-white max-w-[150px] truncate">
                                {projects.find(p => p.id === projectId)?.name || `项目 #${projectId ?? '-'}`}
                            </span>
                        </Tooltip>
                    </div>
                </div>
                {/* overlayStyle：小窗面板 z-[1100] 高于 antd 弹层默认 1050，不抬层弹层会被面板盖住（同头部下拉的坑） */}
                <Popover content={sessionList} title="历史会话" trigger="click" placement="bottomRight"
                         overlayStyle={{ zIndex: 1200 }}>
                    <Button size="small" type="text" className="!text-white" icon={<HistoryOutlined />} />
                </Popover>
                <Button size="small" type="text" className="!text-white" icon={<CloseOutlined />} onClick={toggleOpen} />
            </div>

            {/* 消息区 */}
            <div ref={bodyRef} className="flex-1 overflow-auto px-3 py-3 bg-gray-50">
                {msgs.length === 0 && (
                    <div className="flex flex-col items-center text-gray-500 text-center px-4 pt-12">
                        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center text-white text-2xl mb-3"
                             style={{ background: 'linear-gradient(135deg, #3b82f6, #9333ea)' }}>
                            <RobotOutlined />
                        </div>
                        <p className="text-sm text-gray-600">
                            {project ? `正在协同项目「${project.name}」` : '请先选择项目'}
                        </p>
                        <p className="text-xs mt-1">问我本体的结构、质量问题、怎么改——分析、建议、验证都在这个小窗完成</p>
                    </div>
                )}
                {msgs.map((m, i) => (
                    <div key={i} className={`mb-3 flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                        {m.role === 'user' ? (
                            <div className="max-w-[85%] px-3 py-2 rounded-2xl bg-blue-600 text-white text-sm whitespace-pre-wrap">
                                {m.content}
                            </div>
                        ) : m.error ? (
                            <div className="max-w-[90%] px-3 py-2 rounded-lg bg-red-50 border border-red-200 text-sm text-red-600">
                                {m.error}
                            </div>
                        ) : (
                            <div className="max-w-[90%] px-3 py-2 rounded-2xl bg-white border border-gray-100 shadow-sm">
                                {/* S15：工具步骤在回答之前（事件顺序=先工具后回答，展示对齐） */}
                                {m.toolSteps && m.toolSteps.length > 0 && (
                                    <ToolSteps steps={m.toolSteps} />
                                )}
                                {m.content ? (
                                    <MarkdownAnswer content={m.content} sourceCount={0} onCite={() => {}} />
                                ) : m.streaming ? (
                                    <span className="text-sm text-gray-500">{stage || '思考中…'}</span>
                                ) : (
                                    <span className="text-sm text-gray-500">（模型未返回内容）</span>
                                )}
                                {/* B26/F16：澄清轮确认式选项组（其他=自输入；选中后点确认才发送）。
                                    仅当澄清轮仍是最后一条消息时可交互——已回答后再点会重复提交同一答案 */}
                                {m.clarify && m.clarify.options?.length > 0 && !m.streaming
                                    && i === msgs.length - 1 && (
                                    <ClarifyOptions options={m.clarify.options} busy={busy}
                                                    onSend={t => send(t)} />
                                )}
                                {/* S6：只读项目编辑诉求（负责人/超管）→ 一键「取消发布并调整」，
                                    同样仅挂最后一条消息，点击后自动取消发布并重发原诉求 */}
                                {m.unpublishOffer && i === msgs.length - 1 && !m.streaming && projectId != null && (
                                    <div className="mt-2">
                                        <Button size="small" type="primary" ghost disabled={busy}
                                                onClick={() => unpublishAndAdjust(i)}>
                                            取消发布并调整
                                        </Button>
                                    </div>
                                )}
                                {m.latency_ms != null && (
                                    <div className="text-[10px] text-gray-400 mt-1">{(m.latency_ms / 1000).toFixed(1)}s</div>
                                )}
                                {/* B28/F15：验证报告卡（三段折叠 + 样例展开 + 上一版对比 + 存档） */}
                                {m.report && projectId != null && (
                                    <div className="mt-2">
                                        <VerifyReportCard projectId={projectId} report={m.report} messageId={m.msgId} />
                                    </div>
                                )}
                                {m.proposal && projectId != null && !(m.proposals && m.proposals.length > 1) && (
                                    <ProposalCard
                                        projectId={projectId}
                                        proposal={m.proposal}
                                        onAdjust={draft => { setInput(draft); }}
                                        onStatusChange={(st, res) => setMsgs(prev => {
                                            const next = [...prev];
                                            next[i] = { ...next[i], proposal: { ...m.proposal!, status: st, result: res ?? m.proposal!.result } };
                                            return next;
                                        })}
                                    />
                                )}
                                {m.proposals && m.proposals.length > 1 && projectId != null && (
                                    <div className="mt-2 space-y-2">
                                        {m.proposals.map((pr, pi) => (
                                            <ProposalCard
                                                key={pr.proposal_id}
                                                projectId={projectId}
                                                proposal={pr}
                                                onAdjust={draft => { setInput(draft); }}
                                                onStatusChange={(st, res) => setMsgs(prev => {
                                                    const next = [...prev];
                                                    const arr = [...(next[i].proposals || [])];
                                                    arr[pi] = { ...arr[pi], status: st, result: res ?? arr[pi].result };
                                                    next[i] = { ...next[i], proposals: arr };
                                                    return next;
                                                })}
                                            />
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                ))}
                {busy && stage && msgs.length > 0 && (
                    <div className="text-xs text-gray-500 flex items-center gap-1">
                        <CaretUpOutlined className="animate-pulse" /> {stage}
                    </div>
                )}
            </div>

            {/* 输入区 */}
            <div className="p-2.5 bg-white border-t border-gray-200 shrink-0">
                <textarea
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={e => {
                        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (canSend) send(); }
                    }}
                    placeholder={projectId == null ? '先在上方选择项目…' : '问本体结构、质量问题、怎么改…（Ctrl+Enter 换行）'}
                    rows={2}
                    disabled={busy}
                    className="w-full text-sm px-2 py-1.5 rounded-lg border border-gray-200 focus:outline-none focus:border-blue-400 resize-none"
                />
                <div className="flex items-center justify-between mt-1.5">
                    <div className="flex items-center gap-1">
                        <Tooltip title="新建会话">
                            <Button size="small" type="text" icon={<PlusOutlined />} onClick={newSession} disabled={busy} />
                        </Tooltip>
                        <Tooltip title="清屏（保留会话）">
                            <Button size="small" type="text" icon={<ClearOutlined />} onClick={() => setMsgs([])} disabled={busy} />
                        </Tooltip>
                        <Select
                            value={modelId ?? undefined}
                            onChange={v => setModelId(v)}
                            placeholder="默认模型"
                            allowClear
                            onClear={() => setModelId(null)}
                            dropdownStyle={{ zIndex: 1200 }}
                            size="small"
                            className="min-w-0"
                            style={{ width: 110 }}
                            options={models.map(m => ({ value: m.id, label: m.name }))}
                            notFoundContent={<span className="text-xs text-gray-500">无可用模型</span>}
                        />
                    </div>
                    {busy ? (
                        <Button size="small" danger icon={<StopOutlined />}
                                onClick={() => abortRef.current?.abort()}>停止</Button>
                    ) : (
                        <Button size="small" type="primary" icon={<SendOutlined />}
                                onClick={() => send()} disabled={!canSend}>发送</Button>
                    )}
                </div>
            </div>
        </div>
    );
}


/**
 * 助手开合按钮（放构建器 Tab 栏右侧，用户指定位置）：蓝紫渐变方圆 + 呼吸光晕。
 * 与 CopilotWidget 通过 window CustomEvent('copilot-toggle') 通信，保持小窗单例挂载。
 * 样式自包含（组件内 <style>），不依赖 Tailwind 渐变类（用户环境曾出现类失效）。
 */
export function CopilotToggle() {
    return (
        <Tooltip title="本体协同助手（AI）" placement="left">
            <button onClick={() => window.dispatchEvent(new CustomEvent('copilot-toggle'))}
                    className="copilot-fab" aria-label="打开本体协同助手">
                <style>{`
                    .copilot-fab {
                        display: inline-flex; align-items: center; gap: 6px;
                        padding: 0 14px; height: 36px; white-space: nowrap;
                        border: none; cursor: pointer; color: #fff;
                        border-radius: 18px;
                        background: linear-gradient(135deg, #3b82f6 0%, #7c7cf0 52%, #9333ea 100%);
                        box-shadow: inset 0 1px 0 rgba(255,255,255,.42),
                                    inset 0 -1px 0 rgba(15,23,42,.06),
                                    0 2px 6px rgba(15,23,42,.16),
                                    0 10px 24px -8px rgba(91,141,239,.55);
                        transition: transform .18s cubic-bezier(.2,.8,.3,1), box-shadow .18s ease;
                        animation: copilot-fab-breathe 3.4s ease-in-out infinite;
                    }
                    .copilot-fab:hover {
                        transform: translateY(-2px);
                        box-shadow: inset 0 1px 0 rgba(255,255,255,.42),
                                    inset 0 -1px 0 rgba(15,23,42,.06),
                                    0 4px 10px rgba(15,23,42,.16),
                                    0 14px 30px -8px rgba(91,141,239,.7);
                    }
                    .copilot-fab:active { transform: translateY(0) scale(.96); }
                    .copilot-fab:focus-visible {
                        outline: 2px solid #93c5fd; outline-offset: 3px;
                    }
                    .copilot-fab .copilot-fab-icon {
                        font-size: 16px;
                        filter: drop-shadow(0 1px 1.5px rgba(15,23,42,.25));
                    }
                    .copilot-fab .copilot-fab-text {
                        font-size: 13px; font-weight: 500; letter-spacing: .02em;
                    }
                    @keyframes copilot-fab-breathe {
                        0%, 100% {
                            box-shadow: inset 0 1px 0 rgba(255,255,255,.42),
                                        inset 0 -1px 0 rgba(15,23,42,.06),
                                        0 2px 6px rgba(15,23,42,.16),
                                        0 10px 24px -8px rgba(91,141,239,.55);
                        }
                        50% {
                            box-shadow: inset 0 1px 0 rgba(255,255,255,.42),
                                        inset 0 -1px 0 rgba(15,23,42,.06),
                                        0 2px 6px rgba(15,23,42,.16),
                                        0 12px 30px -6px rgba(147,51,234,.45);
                        }
                    }
                    @media (prefers-reduced-motion: reduce) {
                        .copilot-fab { animation: none; transition: none; }
                    }
                `}</style>
                <RobotOutlined className="copilot-fab-icon" />
                <span className="copilot-fab-text">AI 辅助</span>
            </button>
        </Tooltip>
    );
}
