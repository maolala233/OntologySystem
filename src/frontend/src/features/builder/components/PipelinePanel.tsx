/**
 * 一键构建管线进度面板（2026-11 新手简化；2026-11-16 按项目详情原型重排）。
 *
 * 展示形式收敛为一层：「阶段时间线 + 单一详情区」——
 * - 八阶段时间线可点开临时查看，默认跟随当前运行阶段；阶段一推进就自动回到实时；
 * - 详情区展示该阶段主进度、阶段内子步骤（后端 build_sub_steps 下发 steps）、
 *   实时计数与耗时；构建结束后默认落在「本次构建」汇总（各阶段耗时）；
 * - 原来的「三步清单 + 主进度条 + 阶段格子 + 子进度框」四层表达合并掉。
 *
 * 数据依赖后端 8c3f0ca：stages 带 started_at/finished_at/duration_seconds，
 * status 与 SSE 的 sub 帧带 status/steps（旧任务没有这些字段，显示「—」）。
 * - 不展示预估时间（用户反馈估算不准反成噪音，已移除）；
 * - 任务 id 存 Redis 锁 + localStorage，切页/刷新不丢（BuilderPage 常驻挂载）；
 * - 完成后显示「还剩 N 个高优先级结果等你确认」并直跳审核 Tab。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message, confirmModal } from '../../../shared/feedback';
import InlineNotice from '../../../components/InlineNotice';
import { Button, Card, Popconfirm, Space, Tag } from 'antd';
import {
    CheckCircleOutlined, CheckOutlined, CloseCircleOutlined, CloseOutlined,
    MinusCircleOutlined, MinusOutlined,
    SyncOutlined, RetweetOutlined, PauseCircleOutlined, ClockCircleOutlined,
} from '@ant-design/icons';
import { fmtSchedule } from '../../../shared/schedule';

import { PipelineStageState, PipelineSubStep, PipelineStatus, pipelineApi } from '../../../api/pipeline';

/** 最近一次管线的 task_id（localStorage，锁过期后仍可查 24h 内历史状态） */
const lastTaskKey = (pid: number) => `pipeline:last:${pid}`;

/**
 * 最后一轮构建快照（2026-11-16）：服务端进度键 24h 过期后，阶段时间线仍要能展示。
 * 只持久化阶段快照本身，审核统计等实时数据仍以接口为准。
 */
const snapKey = (pid: number) => `pipeline:snap:${pid}`;

interface PipelineSnap {
    status: string;
    task_id: string;
    stage?: string;
    percent?: number;
    message?: string;
    stages: PipelineStageState[];
    /** 运行中真实收到过的各阶段 steps（后端数据，仅做回放，不由前端生成） */
    stepsByStage?: Record<string, PipelineSubStep[]>;
    saved_at: number;
}

const readSnap = (pid: number): PipelineSnap | null => {
    try {
        const raw = localStorage.getItem(snapKey(pid));
        if (!raw) return null;
        const snap = JSON.parse(raw);
        return snap && Array.isArray(snap.stages) && snap.stages.length ? snap : null;
    } catch {
        return null;
    }
};

interface Props {
    projectId: number;
    /** 项目已发布（历史保留，当前面板不再渲染三步清单） */
    published?: boolean;
    /** 已有构建结果：决定主按钮叫「一键构建」还是「重新构建」 */
    built?: boolean;
    /** 外部触发重查（上传完成等） */
    signal?: number;
    /** 跳转指定 Tab（如 reviews） */
    onNavigate?: (tab: string) => void;
    /** 状态上抛（SSE/refresh 每次更新都回调）：父页面据此做跃迁刷新，免自建轮询 */
    onPipeStatus?: (status: PipelineStatus | null) => void;
}

// 状态图标配色收敛到冷调（2026-11-16）：原来是 Tailwind 语义色（绿 #10b981 /
// 红 #ef4444 / 橙 #f59e0b），饱和度比全站的其他颜色高一档。现按「品牌蓝=进行中、
// 冷青=已完成、冷调红=失败、琥珀=中止」重排
const STATUS_ICON: Record<string, React.ReactNode> = {
    done: <CheckCircleOutlined style={{ color: '#1B87AD' }} />,
    failed: <CloseCircleOutlined style={{ color: '#B42318' }} />,
    running: <SyncOutlined spin style={{ color: '#5B8DEF' }} />,
    skipped: <MinusCircleOutlined style={{ color: '#A3ADC2' }} />,
    cancelled: <PauseCircleOutlined style={{ color: '#B8790F' }} />,
    pending: <span style={{ color: '#CBD5E5' }}>○</span>,
};

const STAGE_STATUS_TEXT: Record<string, string> = {
    done: '完成', running: '进行中', pending: '待开始',
    failed: '失败', skipped: '已跳过', cancelled: '已中止',
};

// 时间线圆点底色（冷调）：进行中实心蓝 + 脉冲，完成淡青，失败淡红，中止淡琥珀
const DOT_TONE: Record<string, { bg: string; color: string }> = {
    done: { bg: 'rgba(27,135,173,0.12)', color: '#1B87AD' },
    running: { bg: '#5B8DEF', color: '#FFFFFF' },
    failed: { bg: 'rgba(180,35,24,0.10)', color: '#B42318' },
    cancelled: { bg: 'rgba(242,185,80,0.18)', color: '#B8790F' },
    skipped: { bg: '#F3F5F9', color: '#A3ADC2' },
    pending: { bg: '#F3F5F9', color: '#C2C7CF' },
};

const STATUS_TAG_STYLE: Record<string, React.CSSProperties> = {
    done: { background: 'rgba(27,135,173,0.10)', color: '#1B87AD', borderColor: 'rgba(27,135,173,0.24)' },
    running: { background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' },
    failed: { background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' },
    cancelled: { background: 'rgba(242,185,80,0.20)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.45)' },
    skipped: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' },
    pending: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' },
};

const BAR_BG: Record<string, string> = {
    running: 'linear-gradient(90deg,#84ACF7,#5B8DEF)',
    done: 'linear-gradient(90deg,#6FC3CF,#1B87AD)',
    failed: 'linear-gradient(90deg,#E88A80,#B42318)',
    cancelled: 'linear-gradient(90deg,#F2CB87,#B8790F)',
    skipped: '#D7DDE8',
    pending: '#D7DDE8',
};

/**
 * 阶段子步骤只认后端数据（2026-11-16 用户要求：前端不写死）。
 *
 * - 运行中：显示当前阶段的 sub.steps（后端 build_sub_steps / 各任务显式写入）；
 * - 完成后：后端只保留「当前阶段」的子进度键，不再下发历史阶段的 steps，这里回放
 *   运行中真实收到过的那份（按阶段缓存进本机快照）；没有采集到的阶段明确显示未下发，
 *   不补任何前端臆造的清单。
 */

/** 秒 → 「3 分 12 秒」；空值「—」 */
const fmtDuration = (sec?: number | null): string => {
    if (typeof sec !== 'number' || !Number.isFinite(sec) || sec < 0) return '—';
    const t = Math.round(sec);
    if (t < 60) return `${t} 秒`;
    const m = Math.floor(t / 60);
    const r = t % 60;
    if (m < 60) return r ? `${m} 分 ${String(r).padStart(2, '0')} 秒` : `${m} 分`;
    return `${Math.floor(m / 60)} 小时 ${m % 60} 分`;
};

const tsOf = (iso?: string | null): number | null => {
    if (!iso) return null;
    const t = new Date(iso).getTime();
    return Number.isNaN(t) ? null : t;
};

/** 子进度 stats → 统计胶囊（只挑用户看得懂的计数，ETA 不展示） */
const statChips = (stats?: Record<string, any>): Array<[string, string]> => {
    if (!stats) return [];
    const num = (v: any): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
    const chips: Array<[string, string]> = [];
    const done = num(stats.chunks_done);
    const total = num(stats.chunks_total);
    if (done != null && total != null) chips.push(['切片', `${done.toLocaleString()} / ${total.toLocaleString()}`]);
    const batchNo = num(stats.batch_no);
    const batches = num(stats.batches_total);
    if (batchNo != null && batches != null) chips.push(['批次', `${batchNo} / ${batches}`]);
    const cacheHits = num(stats.cache_hits);
    if (cacheHits != null && cacheHits > 0) chips.push(['缓存命中', cacheHits.toLocaleString()]);
    const phaseDone = num(stats.phase_done);
    const phaseTotal = num(stats.phase_total);
    if (phaseDone != null && phaseTotal != null) {
        chips.push([stats.phase === 'coref' ? '指代消解' : '已处理', `${phaseDone} / ${phaseTotal}`]);
    }
    const seg = num(stats.stitch_segment);
    const segs = num(stats.stitch_segments);
    if (seg != null && segs != null) chips.push(['缝合', `${seg} / ${segs}`]);
    return chips;
};

const PipelinePanel: React.FC<Props> = ({ projectId, signal = 0, onNavigate, built, onPipeStatus }) => {
    const [status, setStatus] = useState<PipelineStatus | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [starting, setStarting] = useState(false);
    // 临时查看的阶段（null=跟随实时进度 / 构建结束后落在汇总）。
    // 纯跟随（2026-11-16 用户确认不要「固定查看」）：点一下就只是看一眼，
    // 运行阶段一推进/构建结束就自动回到实时，不长期钉住详情区
    const [peekKey, setPeekKey] = useState<string | null>(null);
    // 点开临时查看时后端所处阶段：阶段推进后据此自动收回
    const peekLiveKeyRef = useRef<string | null>(null);
    // SSE 订阅（2026-11-07）：后端运行锁为事实源推送进度，3s 轮询降级为断流兜底
    const esRef = useRef<EventSource | null>(null);
    // 订阅代际：换票（eventsUrl）是异步的，等待期间组件可能已卸载/已重订；
    // 旧代际回来时若仍建 EventSource，该连接无人关闭，会永久占住浏览器对该域的
    // 连接名额（Chrome 每域 6 条 HTTP/1.1），泄漏几条后所有新请求卡 pending
    // （2026-11-10 实测：项目页反复进出后上传/状态接口全挂）
    const subGenRef = useRef(0);
    const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [sseUp, setSseUp] = useState(false);

    // 最新状态快照：SSE 的 idle 帧要据此判断"是不是正处在定时等待中"（2026-11-16）
    const statusRef = useRef<PipelineStatus | null>(null);
    useEffect(() => { statusRef.current = status; }, [status]);

    // 定时状态复核节流：SSE 每秒都在推 idle 帧，不能每帧都打 REST
    const schedCheckAtRef = useRef(0);
    // 运行锁释放后的 idle 帧复核节流（同上面，只在本地仍显示运行时复核）
    const idleCheckAtRef = useRef(0);
    // 快照落盘节流：运行中最多 5s 写一次 localStorage
    const snapAtRef = useRef(0);
    // 最近一次收到 SSE 数据帧的时间：半开连接（无 onerror 也不再推帧）时靠它兜底轮询
    const lastFrameAtRef = useRef(Date.now());

    // 运行中真实收到过的各阶段 steps（纯后端数据）：后端只对「当前阶段」下发 steps，
    // 完成后不再提供历史阶段的明细，这里按阶段缓存进本机快照做回放（2026-11-16）。
    const stepsCacheRef = useRef<Record<string, PipelineSubStep[]>>({});
    const [stepsByStage, setStepsByStage] = useState<Record<string, PipelineSubStep[]>>({});
    // 上一次看到的 task_id：换任务时清空步骤缓存（首屏首次加载不清，避免把快照回放的清掉）
    const lastTaskSeenRef = useRef<string | null>(null);
    const rememberSteps = useCallback((stageKey?: string | null, steps?: PipelineSubStep[]) => {
        if (!stageKey || !steps || !steps.length) return;
        const prev = stepsCacheRef.current[stageKey];
        const same = !!prev && prev.length === steps.length && prev.every((p, i) => (
            p.key === steps[i].key && p.state === steps[i].state && p.percent === steps[i].percent));
        if (same) return;
        const next = { ...stepsCacheRef.current, [stageKey]: steps };
        stepsCacheRef.current = next;
        setStepsByStage(next);
    }, []);

    const refresh = useCallback(async () => {
        try {
            // 先查项目当前任务（读运行锁）：非 idle 直接采用——否则 localStorage 的
            // lastTask 会把面板钉死在上一次的终态，API 侧/定时触发的重建永远刷不出来
            // （2026-11-06 项目 258 实测：面板一直显示上一轮 failed）。idle 时才用
            // lastTask 兜底展示 24h 内历史（锁过期后仍可见）。
            let s = await pipelineApi.status(projectId);
            if (s.status === 'idle') {
                const lastTask = localStorage.getItem(lastTaskKey(projectId)) || undefined;
                if (lastTask) s = await pipelineApi.status(projectId, lastTask);
            }
            // 服务端进度键 24h 过期 / 换了浏览器：用本机最后一次终态快照兜底，
            // 构建结束后阶段时间线也保留（用户要求「步骤条永远不消失」，2026-11-16）
            const snap = readSnap(projectId);
            if (s.status === 'idle' && !(s.stages || []).length) {
                if (snap && snap.status !== 'running' && snap.status !== 'queued') {
                    s = {
                        ...s,
                        status: snap.status as PipelineStatus['status'],
                        task_id: snap.task_id,
                        stage: snap.stage,
                        percent: snap.percent,
                        message: snap.message,
                        stages: snap.stages,
                    };
                }
            }
            // 同一轮任务的子步骤缓存（本机快照里保存的是运行中真实收到过的后端 steps）。
            // 只在内存缓存为空时回填，避免刷新把运行中更新过的缓存覆盖回旧快照
            if (snap?.task_id && snap.task_id === s.task_id && snap.stepsByStage
                && !Object.keys(stepsCacheRef.current).length) {
                stepsCacheRef.current = snap.stepsByStage;
                setStepsByStage(snap.stepsByStage);
            }
            if (s.stage && s.sub?.steps?.length) rememberSteps(s.stage, s.sub.steps);
            setStatus(s);
            if (s.task_id && s.status !== 'idle') {
                localStorage.setItem(lastTaskKey(projectId), s.task_id);
            }
        } catch {
            /* 状态查询失败不打扰用户（模块未开通等） */
        } finally {
            setLoaded(true);
        }
    }, [projectId, rememberSteps]);

    // 项目切换（路由参数变化、组件不重挂载）时清掉上一个项目的进度快照，
    // refresh 返回前不显示旧项目的阶段/进度
    useEffect(() => {
        setStatus(null); setLoaded(false); setPeekKey(null);
        stepsCacheRef.current = {};
        setStepsByStage({});
        lastTaskSeenRef.current = null;
    }, [projectId]);

    useEffect(() => { refresh(); }, [refresh, signal]);

    // 状态上抛：父页面（ProjectOverviewPage）据此做跃迁刷新，免自建 5s 轮询
    useEffect(() => { onPipeStatus?.(status); }, [status, onPipeStatus]);

    // 换了新一轮构建：收回临时查看，并清掉上一轮的步骤缓存（同一任务的首屏加载不清）
    useEffect(() => {
        const tid = status?.task_id || null;
        if (lastTaskSeenRef.current && tid && tid !== lastTaskSeenRef.current) {
            setPeekKey(null);
            stepsCacheRef.current = {};
            setStepsByStage({});
        }
        lastTaskSeenRef.current = tid;
    }, [status?.task_id]);

    // 纯跟随：临时查看只在打开时的那个阶段内有效——后端阶段推进（status.stage 变化）
    // 就自动回到实时；构建结束（完成/失败/中止）也收回，回到「本次构建」汇总
    useEffect(() => {
        if (!peekKey) return;
        const live = status?.stage || null;
        if (live && live !== peekLiveKeyRef.current) setPeekKey(null);
    }, [peekKey, status?.stage]);

    useEffect(() => {
        const st = status?.status;
        if (st === 'completed' || st === 'failed' || st === 'cancelled') setPeekKey(null);
    }, [status?.status]);

    // 落一份阶段快照：终态立即写，运行中最多 5s 写一次（阶段时间线的兜底数据源）
    useEffect(() => {
        const st = status;
        if (!st?.task_id || !st.stages?.length) return;
        const terminal = st.status === 'completed' || st.status === 'failed' || st.status === 'cancelled';
        const now = Date.now();
        if (!terminal && now - snapAtRef.current < 5000) return;
        snapAtRef.current = now;
        try {
            localStorage.setItem(snapKey(projectId), JSON.stringify({
                status: st.status, task_id: st.task_id, stage: st.stage || '',
                percent: st.percent || 0, message: st.message || '',
                stages: st.stages, stepsByStage: stepsCacheRef.current, saved_at: now,
            }));
        } catch {
            /* 配额满 / 隐私模式：忽略，下次刷新再试 */
        }
    }, [status, projectId]);

    // SSE 订阅：服务端每秒看运行锁 diff 推帧、idle→running 跃迁也推——
    // 坐在页面上等构建全程零轮询；断流/超时才轮询兜底 + 8s 换票重订
    const subscribe = useCallback(async () => {
        const gen = ++subGenRef.current;
        esRef.current?.close();
        esRef.current = null;
        try {
            const lastTask = localStorage.getItem(lastTaskKey(projectId)) || undefined;
            const url = await pipelineApi.eventsUrl(projectId, lastTask);
            if (gen !== subGenRef.current) return;  // 已卸载/已重订：丢弃，避免遗留孤儿连接
            const es = new EventSource(url);
            es.onopen = () => setSseUp(true);
            es.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    lastFrameAtRef.current = Date.now();
                    if (data.stage && data.sub?.steps?.length) {
                        rememberSteps(data.stage, data.sub.steps);
                    }
                    const noReviews = {
                        pending_by_priority: {}, pending_total: 0, need_human: 0, auto_handled: 0,
                    };
                    // SSE 帧是 Redis 原始进度键：stages 嵌在 stats.stages 里
                    // （摊平到顶层是 REST status 端点的事）——不取的话格子永远
                    // 停在挂载时的快照，消息行在动、格子不动（2026-11-08 实测）
                    const incomingStages = (data.stats && data.stats.stages) || data.stages;
                    // SSE 的 idle 帧只看运行锁，不知道有没有定时登记（2026-11-16 实测：
                    // 打开已定时的项目 → 首屏 REST 显示「待构建」→ 紧随其后的 idle 帧
                    // 把状态清成 idle，提示条一闪就没）。本地认为在等定时时不吃 idle 帧，
                    // 改为节流回 REST 复核：定时若刚被取消，REST 会返回 idle，状态随后落地。
                    if (data.status === 'idle' && statusRef.current?.status === 'scheduled') {
                        const now = Date.now();
                        if (now - schedCheckAtRef.current > 10000) {
                            schedCheckAtRef.current = now;
                            void refresh();
                        }
                        return;
                    }
                    // 构建结束、运行锁释放后，SSE 只会推不带 stages 的 idle 帧。
                    // 本地已有上一轮任务（刚完成 / 首屏读到的历史终态）时不吃这些帧，
                    // 否则阶段时间线与详情区会在构建完成的一瞬间被清空（2026-11-16 实测：
                    // 用户反馈「构建到最后步骤条全消失」）。判断同时看状态快照与
                    // localStorage 里登记的任务 id——帧间隔可能短于一次 re-render，
                    // 状态 ref 还没追上时，任务 id 已经在收到终态帧时同步落盘。
                    // 本地还停在运行中时（没收到终态帧）回 REST 复核——worker 死亡的
                    // 清锁判定在服务端。
                    if (data.status === 'idle'
                        && (statusRef.current?.task_id || localStorage.getItem(lastTaskKey(projectId)))) {
                        const local = statusRef.current?.status;
                        if (local === 'running' || local === 'queued') {
                            const now = Date.now();
                            if (now - idleCheckAtRef.current > 10000) {
                                idleCheckAtRef.current = now;
                                void refresh();
                            }
                        }
                        return;
                    }
                    // idle 帧不带 stages（保留旧 stages 会把 runningNow 卡在转圈）
                    setStatus((prev) => (data.status === 'idle'
                        ? { ...data, stages: [], reviews: prev?.reviews ?? noReviews }
                        : {
                            ...prev, ...data,
                            stages: incomingStages || prev?.stages || [],
                            reviews: data.reviews ?? prev?.reviews ?? noReviews,
                            // sub 帧只在当前阶段有效：后端只在本阶段有子进度键时才注入，
                            // 帧里不带 sub 时要丢掉上一阶段残留的 100%，否则会误判新阶段已完成
                            sub: data.sub ?? (data.stage && prev?.stage === data.stage ? prev?.sub : undefined),
                        }));
                    if (data.task_id && data.status && data.status !== 'idle') {
                        localStorage.setItem(lastTaskKey(projectId), data.task_id);
                    }
                    if (data.status === 'completed' || data.status === 'failed'
                        || data.status === 'cancelled') {
                        refresh();  // 补带审核统计的完整终态帧
                    }
                } catch { /* 单帧解析失败忽略，下一帧自愈 */ }
            };
            es.onerror = () => {
                es.close();
                if (esRef.current === es) {
                    esRef.current = null;
                    setSseUp(false);
                }
                refresh();
                if (retryRef.current) clearTimeout(retryRef.current);
                retryRef.current = setTimeout(() => { retryRef.current = null; subscribe(); }, 8000);
            };
            esRef.current = es;
        } catch {
            /* ticket 获取失败：一次性 refresh + 轮询兜底照常工作 */
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, refresh, rememberSteps]);

    useEffect(() => {
        subscribe();
        return () => {
            subGenRef.current += 1;  // 作废在途换票，防止清理后才建立的连接泄漏
            if (retryRef.current) {
                clearTimeout(retryRef.current);
                retryRef.current = null;
            }
            esRef.current?.close();
            esRef.current = null;
            setSseUp(false);
        };
    }, [subscribe]);

    const s = status;
    const rawStages: PipelineStageState[] = s?.stages || [];
    // 子任务已跑到 100%（finish_task_progress 置 percent=100）但主阶段还没被置 done：
    // 后端在阶段收尾（如骨架索引同步）期间会有数秒到数十秒的间隙，界面会卡在「转圈 +
    // 完成文案」——这里按子进度 100% 提前把该阶段显示为完成，视觉上立即进入下一项
    const subDone = typeof s?.sub?.percent === 'number' && s.sub.percent >= 100
        && (!!s?.sub?.stage && s.sub.stage === s?.stage);
    const stages: PipelineStageState[] = subDone
        ? rawStages.map((st) => (st.status === 'running' ? { ...st, status: 'done' as const } : st))
        : rawStages;
    // 总进度只认后端下发的 percent（2026-11-16 用户要求：前端不自己算）。
    // 原来在长阶段内按「阶段序号 + 子百分比」折算过平滑值——那是前端估算，已去掉；
    // 主键在阶段边界更新，阶段内的细粒度进度以上方子步骤/子进度为准
    const subSameStage = typeof s?.sub?.percent === 'number'
        && !!s?.sub?.stage && s.sub.stage === s?.stage;
    const subPercent = s?.sub?.percent;
    const mainPercent = typeof s?.percent === 'number' ? s.percent : undefined;
    // 运行中 3s 轮询；其余状态不轮询（事件驱动：signal / 手动刷新）
    const runningNow = !!s && (s.status === 'running' || s.status === 'queued'
        || (!!stages.length && s.stages.some((x) => x.status === 'running')
            && s.status !== 'completed' && s.status !== 'failed' && s.status !== 'cancelled'));
    // 断流兜底：SSE 未接通时运行中每 3s 轮询；SSE 在线则全程零轮询
    useEffect(() => {
        if (!runningNow || sseUp) return;
        const t = setInterval(refresh, 3000);
        return () => clearInterval(t);
    }, [refresh, runningNow, sseUp, s?.task_id]);

    // SSE 半开兜底（2026-11-16 反馈）：连接看起来在线、却超过 30s 没收到任何数据帧
    // （后端每秒读一次进度、15s 一条心跳注释，正常不会这么久），主动 REST 复核一次，
    // 防止界面停在旧阶段不动
    useEffect(() => {
        if (!runningNow || !sseUp) return;
        const t = setInterval(() => {
            if (Date.now() - lastFrameAtRef.current > 30000) {
                lastFrameAtRef.current = Date.now();
                void refresh();
            }
        }, 10000);
        return () => clearInterval(t);
    }, [refresh, runningNow, sseUp]);

    // 运行中每秒走一次时钟：阶段耗时由 started_at 推算，SSE 静默期（长批次）也能走动
    const [, setTick] = useState(0);
    useEffect(() => {
        if (!runningNow) return;
        const t = setInterval(() => setTick((n) => n + 1), 1000);
        return () => clearInterval(t);
    }, [runningNow]);

    const start = async (restart = false) => {
        setStarting(true);
        try {
            const r = await pipelineApi.run(projectId, restart ? { restart: true } : undefined);
            if (!r.task_id && r.mode === 'scheduled') {
                message.success('构建已定时，将在指定时间自动开始');
                await refresh();
                return;
            }
            if (r.already_running) {
                message.info('构建已在进行中，下方进度条实时更新');
            } else if (restart) {
                message.success('重头开始：全部文档重新解析并重新抽取');
            } else {
                message.success('一键构建已启动：解析 → AI 抽取本体 → 低风险结果由 AI 结果审查，完成后确认高优先级结果即可');
            }
            if (r.task_id) {
                localStorage.setItem(lastTaskKey(projectId), r.task_id);
            }
            await refresh();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '启动失败');
        } finally {
            setStarting(false);
        }
    };

    const cancel = async () => {
        try {
            const r = await pipelineApi.cancel(projectId);
            message.success(r.kind === 'scheduled'
                ? '已取消定时构建（到点不再自动开始）'
                : '正在停止：当前这次 AI 调用返回后立即中断');
            refresh();
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '取消失败');
        }
    };

    if (!loaded) return null;

    const finished = s && (s.status === 'completed' || s.status === 'failed'
        || s.status === 'cancelled');
    const hasRun = !!s?.task_id || stages.length > 0;
    // 从未构建过：主按钮叫「一键构建」，点了直接跑（不再先弹「重新构建」确认）
    const restartMode = !!built || hasRun;

    const runningStage = stages.find((st) => st.status === 'running') || null;
    const failedStage = stages.find((st) => st.status === 'failed') || null;
    const cancelledStage = stages.find((st) => st.status === 'cancelled') || null;
    const doneCount = stages.filter((st) => st.status === 'done').length;
    const lastActionableStage = [...stages].reverse().find(
        (st) => st.status === 'done' || st.status === 'failed' || st.status === 'cancelled') || null;

    // 默认详情：运行中跟随当前阶段；失败/中止落在出问题的阶段（先看原因）；
    // 正常结束后停在「本次构建」汇总。
    // 阶段切换的空窗（上一阶段已 done、下一阶段还没置 running）沿用上一个有结果的阶段
    const fallbackKey = runningNow
        ? (runningStage?.key ?? lastActionableStage?.key ?? 'summary')
        : s?.status === 'failed'
            ? (failedStage?.key ?? 'summary')
            : s?.status === 'cancelled'
                ? (cancelledStage?.key ?? 'summary')
                : 'summary';
    const activeKey = peekKey ?? fallbackKey;
    const activeStage = stages.find((st) => st.key === activeKey) || null;

    // 阶段快照里的 started/finished 是 UTC ISO；运行中的阶段用当前时间补终点
    const nowMs = Date.now();
    const stageDuration = (st?: PipelineStageState | null): number | null => {
        if (!st) return null;
        if (typeof st.duration_seconds === 'number') return st.duration_seconds;
        const t0 = tsOf(st.started_at);
        if (t0 == null) return null;
        const t1 = tsOf(st.finished_at) ?? nowMs;
        return Math.max(0, (t1 - t0) / 1000);
    };
    const totalDuration = stages.reduce(
        (n, st) => n + (typeof st.duration_seconds === 'number' ? st.duration_seconds : 0), 0);
    const starts = stages.map((st) => tsOf(st.started_at)).filter((t): t is number => t != null);
    const ends = stages.map((st) => tsOf(st.finished_at)).filter((t): t is number => t != null);
    const overallElapsed = starts.length
        ? Math.max(0, ((runningNow ? nowMs : (ends.length ? Math.max(...ends) : nowMs))
            - Math.min(...starts)) / 1000)
        : null;

    const overallPercent = s?.status === 'completed' ? 100
        : typeof mainPercent === 'number' ? mainPercent
            : finished ? 100 : 0;
    const overallState = s?.status === 'completed' && !runningNow ? 'done'
        : s?.status === 'failed' ? 'failed'
            : s?.status === 'cancelled' ? 'cancelled' : 'running';
    const overallMeta = runningNow
        ? `已完成 ${doneCount}/${stages.length} 个阶段`
        : s?.status === 'completed'
            ? (totalDuration > 0 ? `本次构建用时 ${fmtDuration(totalDuration)}` : '本次构建已完成')
            : s?.status === 'failed'
                ? `构建未完成${failedStage ? ` · 停在「${failedStage.label}」` : ''}`
                : s?.status === 'cancelled'
                    ? `已中止 · 完成 ${doneCount}/${stages.length || 8} 个阶段`
                    : '尚未构建';

    // 详情区的阶段百分比也只认后端（2026-11-16 用户要求：前端不自己算）：
    // - 有本阶段子进度（sub.percent）直接用；
    // - 完成后 100、待开始 0（阶段真实终态，不是估算）；
    // - 已跳过/失败/中止或运行中但后端还没给子进度 → null，界面显示不确定态而不是编个数字
    const stagePercentOf = (st: PipelineStageState): number | null => {
        if (st.status === 'done') return 100;
        if (st.status === 'pending') return 0;
        if (activeKey === st.key && subSameStage && typeof subPercent === 'number') return subPercent;
        return null;
    };

    // 段头只补充运行中的上下文：完成态的总用时已经在总进度行与汇总区表达，不再重复第三遍
    const headSub = runningNow
        ? `${runningStage ? `当前阶段：${runningStage.label}` : '构建进行中'} · 已用 ${fmtDuration(overallElapsed)}`
        : '';

    const chips = activeKey === s?.stage ? statChips(s?.sub?.stats) : [];

    const detailMessage = (st: PipelineStageState, shown: string): string => {
        if (shown === 'running') return s?.sub?.message || s?.message || st.detail || '正在执行…';
        if (shown === 'failed') return st.detail || s?.message || '阶段失败';
        if (shown === 'cancelled') return st.detail || '已中止';
        if (shown === 'skipped') return '本档位跳过该阶段';
        if (shown === 'pending') return '等待前置阶段完成';
        return st.detail || '已完成';
    };

    const summaryTagState = s?.status === 'completed' ? 'done'
        : s?.status === 'failed' ? 'failed'
            : s?.status === 'cancelled' ? 'cancelled' : 'running';

    return (
        <Card
            size="small"
            className="mb-3"
            title={(
                <Space>
                    {runningNow && <SyncOutlined spin style={{ color: '#5B8DEF' }} />}
                    <span>构建</span>
                    {headSub && (
                        <span className="hidden text-[11.5px] text-[#8A94AB] font-normal md:inline">
                            {headSub}
                        </span>
                    )}
                </Space>
            )}
            extra={(
                <Space split={<span className="text-[#DCE3F0]">|</span>}>
                    {runningNow && (
                        <Popconfirm title="停止当前构建？"
                                    description={(
                                        <div className="max-w-[260px] text-[13px]">
                                            立即终止本次构建（含挂起中的 AI 调用）；
                                            已完成的阶段结果保留。
                                        </div>
                                    )}
                                    okText="停止" cancelText="继续"
                                    onConfirm={cancel}>
                            <Button size="small" icon={<PauseCircleOutlined />}>停止</Button>
                        </Popconfirm>
                    )}
                    {!runningNow && (
                        <Button size="small" type="primary" loading={starting}
                                icon={<RetweetOutlined />}
                                onClick={() => (restartMode ? confirmModal({
                                    title: '重新构建？',
                                    content: '全部文档将重新解析，骨架与实例全部重新抽取。',
                                    okText: '重新构建', cancelText: '取消',
                                    onOk: () => start(true),
                                }) : start(false))}>
                            {restartMode ? '重新构建' : '一键构建'}
                        </Button>
                    )}
                </Space>
            )}
        >
            <style>{`
                /* 阶段时间线（2026-11-16 按原型）：圆点 + 连接线，点开临时查看 */
                .pl-tl { display: flex; align-items: flex-start; gap: 2px; padding: 4px 2px 10px; overflow-x: auto; overflow-y: hidden; }
                .pl-tl-item {
                    /* basis 0 + min-width：宽屏按等分撑满、不因分数宽度多出 1px 触发滚动条
                       （原来 basis 92px 在 1440 宽下八等分出现 .5px 溢出，底部会冒横条） */
                    position: relative; flex: 1 1 0; min-width: 92px;
                    display: flex; padding: 0; border: 0;
                    background: transparent; cursor: pointer; font: inherit;
                }
                /* hover/选中底色放在内层：连接线是 ::after（定位层），必须压在内层底色之上，
                   否则后一个格子的 hover 底色会盖住前一段线（2026-11-16 实测） */
                .pl-tl-inner {
                    display: flex; flex-direction: column; align-items: center; gap: 5px;
                    width: 100%; padding: 4px 2px 3px; border-radius: 8px;
                }
                .pl-tl-item:hover .pl-tl-inner { background: #F3F7FF; }
                .pl-tl-item:hover .pl-tl-label { color: #2F62D8; }
                /* 未走到的阶段不可点：不响应 hover，标签压暗 */
                .pl-tl-item:disabled { cursor: default; }
                .pl-tl-item:disabled:hover .pl-tl-inner { background: transparent; }
                .pl-tl-item:disabled:hover .pl-tl-label { color: #8A94AB; }
                .pl-tl-item.pending .pl-tl-label { color: #8A94AB; }
                .pl-tl-item.selected .pl-tl-inner {
                    background: #F7FAFF;
                    box-shadow: inset 0 0 0 1px rgba(91,141,239,0.35);
                }
                /* 连接线用正的 z-index：阶段格子是定位元素（position: relative），
                   它内部的一切（含 hover/选中底色）都会在"定位层"按其树序绘制，
                   仅靠把底色放内层仍会盖住前一个格子的线（2026-11-16 实测：左侧
                   那段线还是被遮）。线抬到内容之上、圆点再压在线上，
                   线本身只占圆点之间的空隙，不会压到文字 */
                .pl-tl-item::after {
                    content: ''; position: absolute; top: 17px;
                    left: calc(50% + 17px); right: calc(-50% + 17px);
                    height: 2px; background: #E7EEF9; z-index: 2;
                }
                .pl-tl-item:last-child::after { display: none; }
                .pl-tl-item.done::after { background: rgba(27,135,173,0.30); }
                .pl-tl-dot {
                    position: relative; z-index: 3; width: 26px; height: 26px;
                    border-radius: 50%; display: flex; align-items: center;
                    justify-content: center; font-size: 13px;
                }
                .pl-tl-item.running .pl-tl-dot { animation: pl-pulse 1.8s infinite; }
                .pl-tl-label { font-size: 12px; color: #52607A; white-space: nowrap; }
                .pl-tl-item.running .pl-tl-label { color: #2A57C6; font-weight: 600; }
                .pl-tl-state { font-size: 11px; color: #A3ADC2; white-space: nowrap; }
                .pl-tl-item.done .pl-tl-state { color: #1B87AD; }
                .pl-tl-item.failed .pl-tl-state { color: #B42318; }
                .pl-tl-item.cancelled .pl-tl-state { color: #B8790F; }
                .pl-tl-item.running .pl-tl-state { color: #2A57C6; }
                .pl-tl-item.selected .pl-tl-label { color: #2F62D8; font-weight: 600; }
                @keyframes pl-pulse {
                    0%, 100% { box-shadow: 0 0 0 4px rgba(91,141,239,0.16); }
                    50% { box-shadow: 0 0 0 7px rgba(91,141,239,0.06); }
                }
                .pl-live-dot {
                    width: 7px; height: 7px; border-radius: 50%; background: #5B8DEF;
                    flex: none; animation: pl-blink 1.4s infinite;
                }
                @keyframes pl-blink { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
                /* 后端还没给阶段内百分比时的「不确定态」：一条往返的细条，不显示具体数字 */
                .pl-ind {
                    position: absolute; top: 0; bottom: 0; width: 35%;
                    border-radius: 9999px; display: block;
                    background: linear-gradient(90deg,#84ACF7,#5B8DEF);
                    animation: pl-ind 1.4s ease-in-out infinite;
                }
                @keyframes pl-ind { 0% { left: -35%; } 100% { left: 100%; } }
            `}</style>

            {/* 总进度（阶段边界才更新的后端主键 + 子进度折算，平滑但只增不减） */}
            {stages.length > 0 && (
                <div className="mt-1 flex items-center gap-3.5">
                    <div className="shrink-0">
                        <div className="text-[26px] font-bold leading-none"
                             style={{ color: overallState === 'done' ? '#1B87AD'
                                 : overallState === 'failed' ? '#B42318'
                                     : overallState === 'cancelled' ? '#B8790F' : '#5B8DEF' }}>
                            {overallPercent}%
                        </div>
                        <div className="mt-[3px] whitespace-nowrap text-[11.5px] text-[#A3ADC2]">
                            {overallMeta}
                        </div>
                    </div>
                    <div className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-[#EEF0F3]">
                        <div className="h-full rounded-full transition-[width] duration-500"
                             style={{ width: `${overallPercent}%`, background: BAR_BG[overallState] || BAR_BG.running }} />
                    </div>
                </div>
            )}

            {/* 阶段时间线：默认跟随运行中的阶段；点已完成阶段只是临时看一眼，
                运行阶段推进后自动回到实时（纯跟随，无固定） */}
            {stages.length > 0 && (
                <div className="pl-tl mt-2">
                    {stages.map((st) => {
                        const shown = st.status === 'running' && !runningNow ? 'cancelled' : st.status;
                        const tone = DOT_TONE[shown] || DOT_TONE.pending;
                        return (
                            <button type="button" key={st.key}
                                    /* 未走到的阶段不可点（2026-11-16）：还没开始就没有明细可看，
                                       点开只会是空的「等待前置阶段完成」，徒增困惑 */
                                    disabled={st.status === 'pending'}
                                    /* 点「当前阶段」＝回到跟随；点别的已完成阶段＝临时查看，
                                       再点一次也收回 */
                                    onClick={() => {
                                        if (st.key === fallbackKey || peekKey === st.key) {
                                            setPeekKey(null);
                                            peekLiveKeyRef.current = null;
                                            return;
                                        }
                                        setPeekKey(st.key);
                                        peekLiveKeyRef.current = status?.stage || null;
                                    }}
                                    className={`pl-tl-item ${shown} ${activeKey === st.key ? 'selected' : ''}`}
                                    title={`${st.label} · ${STAGE_STATUS_TEXT[shown] || '待开始'}`}>
                                <span className="pl-tl-inner">
                                    <span className="pl-tl-dot" style={{ background: tone.bg, color: tone.color }}>
                                        {shown === 'running'
                                            ? <SyncOutlined spin />
                                            : (STATUS_ICON[shown] || STATUS_ICON.pending)}
                                    </span>
                                    <span className="pl-tl-label">{st.label}</span>
                                    <span className="pl-tl-state">{STAGE_STATUS_TEXT[shown] || '待开始'}</span>
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}

            {/* 单一详情区：当前阶段明细 / 构建完成后的阶段耗时汇总 */}
            {stages.length > 0 && (
                <div className="rounded-[9px] border border-[rgba(91,141,239,0.26)] bg-[#F9FBFF] px-3.5 py-3">
                    {activeKey === 'summary' || !activeStage ? (
                        <>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-[15px] font-semibold text-[#0F172A]">本次构建</span>
                                <Tag style={{ ...STATUS_TAG_STYLE[summaryTagState], marginInlineEnd: 0 }}
                                     className="!text-[11.5px]">
                                    {STAGE_STATUS_TEXT[summaryTagState] || '进行中'}
                                </Tag>
                            </div>
                            <div className="mt-2.5 grid grid-cols-2 gap-2 md:grid-cols-4">
                                {stages.map((st) => (
                                    <button type="button" key={st.key}
                                            onClick={() => {
                                                setPeekKey((prev) => (prev === st.key ? null : st.key));
                                                peekLiveKeyRef.current = status?.stage || null;
                                            }}
                                            className="rounded-lg border border-[#E7EEF9] bg-white px-2.5 py-2 text-left transition-colors hover:border-[rgba(91,141,239,0.45)]">
                                        <div className="truncate text-[11.5px] text-[#8A94AB]">{st.label}</div>
                                        <div className={`mt-0.5 text-[12.5px] font-semibold ${
                                            st.status === 'skipped' || st.status === 'pending'
                                                ? 'text-[#A3ADC2]'
                                                : st.status === 'failed' ? 'text-[#B42318]'
                                                    : st.status === 'cancelled' ? 'text-[#B8790F]'
                                                        : 'text-[#1B87AD]'}`}>
                                            {st.status === 'skipped' ? '已跳过'
                                                : st.status === 'pending' ? '—'
                                                    : fmtDuration(st.duration_seconds)}
                                        </div>
                                    </button>
                                ))}
                            </div>
                            <div className="mt-2.5 text-[11.5px] text-[#A3ADC2]">
                                点击上方任一阶段，可查看该阶段的明细
                            </div>
                        </>
                    ) : (
                        (() => {
                            const shown = activeStage.status === 'running' && !runningNow
                                ? 'cancelled' : activeStage.status;
                            const pct = stagePercentOf(activeStage);
                            const live = stageDuration(activeStage);
                            // 子步骤只用后端数据（2026-11-16 用户要求前端不写死）：
                            // 当前阶段优先取实时 sub.steps，其次取运行中真实采集到的缓存
                            // （完成态回放）；都没有就不展示，由下面的占位文案说明
                            const observed: PipelineSubStep[] = (activeKey === s?.stage && s?.sub?.steps?.length)
                                ? s.sub.steps
                                : (stepsByStage[activeStage.key] || []);
                            const detailSteps: PipelineSubStep[] = observed.map((st) => (
                                shown === 'done' ? { ...st, state: 'done' as const, percent: 100 }
                                    : shown === 'skipped' ? { ...st, state: 'skipped' as const, percent: 0 }
                                        : st
                            ));
                            return (
                                <>
                                    {/* 临时查看与实时进度不一致时给出说明：这只是看一眼，
                                        运行阶段一推进就会自动回到实时（2026-11-16 纯跟随） */}
                                    {peekKey && runningNow && runningStage
                                        && activeStage.key !== runningStage.key && (
                                        <div className="mb-2 flex items-center gap-2 rounded-md border border-[rgba(91,141,239,0.35)] bg-[rgba(91,141,239,0.08)] px-2.5 py-[5px] text-[11.5px] text-[#2A57C6]">
                                            <span className="min-w-0 flex-1 truncate">
                                                临时查看「{activeStage.label}」· 当前进行「{runningStage.label}」，阶段推进后自动回到实时
                                            </span>
                                            <Button type="link" size="small"
                                                    className="!h-auto !p-0 !text-[11.5px]"
                                                    onClick={() => setPeekKey(null)}>
                                                立即回到实时
                                            </Button>
                                        </div>
                                    )}
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="text-[15px] font-semibold text-[#0F172A]">
                                            {activeStage.label}
                                        </span>
                                        <Tag style={{ ...STATUS_TAG_STYLE[shown], marginInlineEnd: 0 }}
                                             className="!text-[11.5px]">
                                            {STAGE_STATUS_TEXT[shown] || '待开始'}
                                        </Tag>
                                        <span className="ml-auto flex items-center gap-2 whitespace-nowrap text-[11.5px] text-[#A3ADC2]">
                                            {/* 构建已结束（或正处于阶段切换的间隙）时，顶部没有那条
                                                自动收回的提示带，这里给一个明确的返回入口 */}
                                            {peekKey && !(runningNow && runningStage) && (
                                                <span>
                                                    临时查看 ·{' '}
                                                    <Button type="link" size="small"
                                                            className="!h-auto !p-0 !text-[11.5px]"
                                                            onClick={() => setPeekKey(null)}>
                                                        {runningNow ? '回到实时进度' : '返回本次构建'}
                                                    </Button>
                                                </span>
                                            )}
                                            <span>
                                                用时{' '}
                                                <b className="ml-0.5 text-[12.5px] text-[#1F2A44]">
                                                    {shown === 'pending' ? '—' : fmtDuration(live)}
                                                </b>
                                            </span>
                                        </span>
                                    </div>
                                    <div className="mt-2 flex items-center gap-2.5">
                                        <div className="relative h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-[#EEF0F3]">
                                            {/* 有后端百分比才画实际宽度；拿不到就显示不确定态/
                                                终态底色，不编一个数字（2026-11-16） */}
                                            {pct != null && (
                                                /* key=阶段：切阶段时重建，避免宽度从上一阶段的 100%
                                                   动画收缩到 0（看起来像进度条"闪没了"） */
                                                <div key={activeStage.key}
                                                     className="h-full rounded-full transition-[width] duration-500"
                                                     style={{
                                                         width: `${Math.max(0, Math.min(100, pct))}%`,
                                                         background: BAR_BG[shown] || BAR_BG.running,
                                                     }} />
                                            )}
                                            {pct == null && shown === 'running' && <i className="pl-ind" />}
                                            {pct == null && shown === 'skipped' && (
                                                <div className="h-full w-full rounded-full"
                                                     style={{ background: BAR_BG.skipped }} />
                                            )}
                                        </div>
                                        <span className="min-w-[38px] text-right text-[12.5px] font-semibold"
                                              style={{ color: shown === 'failed' ? '#B42318'
                                                  : shown === 'done' ? '#1B87AD'
                                                      : shown === 'running' ? '#5B8DEF' : '#8A94AB' }}>
                                            {pct != null
                                                ? `${pct}%`
                                                : (shown === 'skipped' ? '已跳过'
                                                    : STAGE_STATUS_TEXT[shown] || '—')}
                                        </span>
                                    </div>

                                    {/* 阶段内子步骤（后端 build_sub_steps）：进行中的步骤带自己的进度条 */}
                                    {detailSteps.length > 0 && (
                                        <div className="mt-2.5 flex flex-col gap-1.5">
                                            {detailSteps.map((step) => {
                                                const isRun = step.state === 'running';
                                                const isDone = step.state === 'done';
                                                const isFail = step.state === 'failed';
                                                const isSkip = step.state === 'skipped' || step.state === 'cancelled';
                                                const stepWidth = isDone ? 100 : (isSkip ? 0 : step.percent);
                                                return (
                                                    <div key={step.key}
                                                         className={`grid grid-cols-[16px_160px_1fr_auto] items-center gap-2 rounded-lg border px-2 py-[6px] text-[12.5px] ${
                                                             isRun ? 'border-[rgba(91,141,239,0.35)] bg-[#F5F9FF]'
                                                                 : isDone ? 'border-[#E6F0F6] bg-white'
                                                                     : 'border-[#EEF0F5] bg-white'}`}>
                                                        <span className="text-center"
                                                              style={{ color: isRun ? '#5B8DEF' : isDone ? '#1B87AD'
                                                                  : isFail ? '#B42318' : '#C2C7CF' }}>
                                                            {isRun ? <SyncOutlined spin /> : isDone ? <CheckOutlined />
                                                                : isFail ? <CloseOutlined />
                                                                    : isSkip ? <MinusOutlined /> : '○'}
                                                        </span>
                                                        <span className={`truncate ${isRun ? 'font-semibold text-[#2A57C6]'
                                                            : isDone ? 'text-[#52607A]' : 'text-[#8A94AB]'}`}
                                                              title={step.label}>
                                                            {step.label}
                                                        </span>
                                                        <span className="h-1.5 min-w-0 overflow-hidden rounded-full bg-[#EEF0F3]">
                                                            <span className="block h-full rounded-full transition-[width] duration-500"
                                                                  style={{
                                                                      width: `${stepWidth}%`,
                                                                      background: isFail ? BAR_BG.failed
                                                                          : isDone ? BAR_BG.done
                                                                              : BAR_BG.running,
                                                                  }} />
                                                        </span>
                                                        <span className={`text-right tabular-nums ${
                                                            isRun ? 'font-semibold text-[#2A57C6]'
                                                                : isDone ? 'text-[#1B87AD]'
                                                                    : isFail ? 'text-[#B42318]' : 'text-[#C2C7CF]'}`}>
                                                            {isRun ? `${step.percent}%`
                                                                : isDone ? '完成'
                                                                    : isFail ? '失败'
                                                                        : step.state === 'cancelled' ? '已中止'
                                                                            : step.state === 'skipped' ? '已跳过' : '待开始'}
                                                        </span>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    )}
                                    {detailSteps.length === 0 && (
                                        <div className="mt-2.5 text-[11.5px] text-[#A3ADC2]">
                                            {shown === 'running'
                                                ? '等待本阶段子步骤进度…'
                                                : '后端未下发该阶段的分步明细'}
                                        </div>
                                    )}

                                    {/* 实时计数：切片 / 批次 / 缓存命中 / 指代消解 / 缝合 */}
                                    {chips.length > 0 && (
                                        <div className="mt-2.5 flex flex-wrap gap-1.5">
                                            {chips.map(([k, v]) => (
                                                <span key={k}
                                                      className="rounded-md border border-[#E7EEF9] bg-white px-2 py-[2px] text-[11.5px] text-[#66738F]">
                                                    {k} <b className="font-semibold text-[#1F2A44]">{v}</b>
                                                </span>
                                            ))}
                                        </div>
                                    )}

                                    <div className="mt-2.5 flex items-center gap-1.5 text-[11.5px]"
                                         style={{ color: shown === 'running' ? '#2A57C6'
                                             : shown === 'failed' ? '#B42318'
                                                 : shown === 'cancelled' ? '#B8790F' : '#8A94AB' }}>
                                        {shown === 'running' && <span className="pl-live-dot" />}
                                        <span className="min-w-0">{detailMessage(activeStage, shown)}</span>
                                    </div>
                                </>
                            );
                        })()
                    )}
                </div>
            )}

            {/* 从未构建 / Redis 里已无明细：给一句下一步，而不是空白卡 */}
            {stages.length === 0 && s?.status !== 'scheduled' && (
                <div className="mt-1 rounded-lg border border-dashed border-[#E2E9F5] bg-[#FBFCFE] px-3 py-4 text-center text-[12.5px] text-[#8A94AB]">
                    {finished
                        ? '暂无可展示的构建明细（进度快照已过期），重新构建后可查看各阶段耗时'
                        : restartMode
                            ? '暂无进行中的构建，点击右上角「重新构建」重新生成'
                            : '尚未构建：上传文档后点击右上角「一键构建」'}
                </div>
            )}

            {/* 状态提示统一走一行式提示条（Alert 带 description 会渲染成一大块） */}
            {finished && s?.status === 'failed' && (
                <InlineNotice tone="error" className="mt-2">
                    {`构建未完成${s.message ? `：${s.message}` : ''}`}
                </InlineNotice>
            )}
            {s?.status === 'cancelled' && (
                <InlineNotice tone="warning" className="mt-2">
                    构建已停止，点击「重新构建」将全部重新解析与抽取
                </InlineNotice>
            )}
            {/* 待构建（定时）：把"什么时候开始"和"取消"放在一起（2026-11-16） */}
            {s?.status === 'scheduled' && (
                <InlineNotice
                    tone="warning"
                    className="mt-2"
                    icon={<ClockCircleOutlined />}
                    action={(
                        <Button type="link" size="small" className="!h-auto !px-0 !text-[12px]"
                                onClick={cancel}>
                            取消定时
                        </Button>
                    )}
                >
                    {s.scheduled_at
                        ? `定时构建：${fmtSchedule(s.scheduled_at)} 自动开始`
                        : '已登记定时构建，等待到点自动开始'}
                </InlineNotice>
            )}
            {!runningNow && !finished && s?.reviews.pending_total ? (
                <InlineNotice
                    className="mt-2"
                    action={(
                        <Button type="link" size="small" className="!h-auto !px-0 !text-[12px]"
                                onClick={() => onNavigate?.('reviews')}>
                            查看明细
                        </Button>
                    )}
                >
                    {`有 ${s.reviews.pending_total} 个待审结果将由大模型裁决，完成后自动留痕，无需人工确认`}
                </InlineNotice>
            ) : null}
        </Card>
    );
};

export default PipelinePanel;
