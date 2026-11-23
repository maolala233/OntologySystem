/**
 * 一键构建管线进度面板（2026-11 新手简化；2026-11-16 按项目详情原型重排）。
 *
 * 展示形式收敛为一层：「阶段时间线 + 单一详情区」——
 * - 八阶段时间线可点开临时查看，默认跟随当前运行阶段；阶段一推进就自动回到实时；
 * - 详情区展示该阶段主进度、阶段内子步骤（后端 build_sub_steps 下发 steps）、
 *   实时计数与耗时；构建结束后默认落在「构建汇总」（各阶段耗时）；
 * - 原来的「三步清单 + 主进度条 + 阶段格子 + 子进度框」四层表达合并掉。
 *
 * 数据依赖后端 8c3f0ca：stages 带 started_at/finished_at/duration_seconds，
 * status 与 SSE 的 sub 帧带 status/steps（旧任务没有这些字段，显示「—」）。
 * - 不展示预估时间（用户反馈估算不准反成噪音，已移除）；
 * - 任务 id 存 Redis 锁 + localStorage，切页/刷新不丢（BuilderPage 常驻挂载）；
 * - 完成后显示「还剩 N 个高优先级结果等你确认」并直跳审核 Tab。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message, confirmModal, notifyError } from '../../../shared/feedback';
import InlineNotice from '../../../components/InlineNotice';
import DecoConstellation from '../../../components/DecoConstellation';
import { Button, Card, Popconfirm, Space, Tag } from 'antd';
import {
    CheckCircleOutlined, CloseCircleOutlined,
    MinusCircleOutlined, NodeIndexOutlined,
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
    stopping: <SyncOutlined spin style={{ color: '#B8790F' }} />,
    pending: <span style={{ color: '#CBD5E5' }}>○</span>,
};

const STAGE_STATUS_TEXT: Record<string, string> = {
    done: '完成', running: '进行中', pending: '待开始',
    failed: '失败', skipped: '已跳过', cancelled: '已中止', stopping: '停止中',
};

/** 面向业务用户的阶段名：内部仍用 key，展示不出现“抽取/消解”这类实现词 */
const STAGE_LABEL_SIMPLE: Record<string, string> = {
    parse: '资料归集',
    schema: '语义提炼',
    instances: '角色识别',
    resolution: '同义收敛',
    conflicts: '分歧调和',
    selfcheck: '语义编排',
    axioms: '逻辑增补',
    adjudicate: '结果核检',
};

function stageLabel(st?: { key?: string; label?: string } | null): string {
    if (!st) return '';
    return STAGE_LABEL_SIMPLE[st.key || ''] || st.label || '';
}

// 时间线圆点底色（冷调）：进行中实心蓝 + 脉冲，完成淡青，失败淡红，中止淡琥珀
const DOT_TONE: Record<string, { bg: string; color: string }> = {
    done: { bg: 'rgba(27,135,173,0.12)', color: '#1B87AD' },
    running: { bg: '#5B8DEF', color: '#FFFFFF' },
    failed: { bg: 'rgba(180,35,24,0.10)', color: '#B42318' },
    cancelled: { bg: 'rgba(242,185,80,0.18)', color: '#B8790F' },
    stopping: { bg: 'rgba(242,185,80,0.22)', color: '#B8790F' },
    skipped: { bg: '#F3F5F9', color: '#A3ADC2' },
    pending: { bg: '#F3F5F9', color: '#C2C7CF' },
};

const STATUS_TAG_STYLE: Record<string, React.CSSProperties> = {
    done: { background: 'rgba(27,135,173,0.10)', color: '#1B87AD', borderColor: 'rgba(27,135,173,0.24)' },
    running: { background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' },
    failed: { background: 'rgba(180,35,24,0.10)', color: '#B42318', borderColor: 'rgba(180,35,24,0.22)' },
    cancelled: { background: 'rgba(242,185,80,0.20)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.45)' },
    stopping: { background: 'rgba(242,185,80,0.24)', color: '#B8790F', borderColor: 'rgba(242,185,80,0.50)' },
    skipped: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' },
    pending: { background: 'rgba(15,23,42,0.05)', color: '#8A94AB', borderColor: 'rgba(15,23,42,0.08)' },
};

// 阶段汇总格的圆点色（2026-11-17）：与时间线同源，浅底上取深一档的颜色，
// 外圈用同色相 12% 的柔光——和资产详情 / 图谱页「标签前荧光小圆点」同一套装饰件
const STAGE_TONE: Record<string, { c: string; t: string }> = {
    done: { c: '#1B87AD', t: 'rgba(27,135,173,0.14)' },
    running: { c: '#5B8DEF', t: 'rgba(91,141,239,0.16)' },
    failed: { c: '#B42318', t: 'rgba(180,35,24,0.12)' },
    cancelled: { c: '#B8790F', t: 'rgba(242,185,80,0.22)' },
    stopping: { c: '#B8790F', t: 'rgba(242,185,80,0.26)' },
    skipped: { c: '#C2C7CF', t: 'rgba(15,23,42,0.04)' },
    pending: { c: '#CBD5E5', t: 'rgba(15,23,42,0.04)' },
};

const BAR_BG: Record<string, string> = {
    running: 'linear-gradient(90deg,#84ACF7,#5B8DEF)',
    done: 'linear-gradient(90deg,#6FC3CF,#1B87AD)',
    failed: 'linear-gradient(90deg,#E88A80,#B42318)',
    cancelled: 'linear-gradient(90deg,#F2CB87,#B8790F)',
    stopping: 'linear-gradient(90deg,#F2CB87,#B8790F)',
    skipped: '#D7DDE8',
    pending: '#D7DDE8',
};

/** 方案八（feat_simplify_tsz_08）：52px 环形百分比；percent 为空时环内显示状态文字 */
const PctRing: React.FC<{ percent: number | null; color: string; label: string }> = ({
    percent, color, label,
}) => {
    const r = 22;
    const c = 2 * Math.PI * r;
    const frac = percent == null ? 0 : Math.max(0, Math.min(100, percent)) / 100;
    return (
        <svg width="52" height="52" viewBox="0 0 52 52" className="shrink-0">
            <circle cx="26" cy="26" r={r} fill="none" stroke="#EEF0F3" strokeWidth="5" />
            {percent != null && (
                <circle cx="26" cy="26" r={r} fill="none" stroke={color} strokeWidth="5"
                        strokeLinecap="round"
                        strokeDasharray={`${c}`}
                        strokeDashoffset={`${c * (1 - frac)}`}
                        transform="rotate(-90 26 26)"
                        style={{ transition: 'stroke-dashoffset .5s' }} />
            )}
            <text x="26" y="30" textAnchor="middle"
                  fontSize={percent != null ? 12.5 : 10.5}
                  fontWeight={600}
                  fill={percent != null ? color : '#8A94AB'}>
                {percent != null ? `${percent}%` : label}
            </text>
        </svg>
    );
};

/**
 * 阶段子步骤只认后端数据（2026-11-16 用户要求：前端不写死）。
 *
 * - 运行中：显示当前阶段的 sub.steps（后端 build_sub_steps / 各任务显式写入）；
 * - 完成后：后端只保留「当前阶段」的子进度键，不再下发历史阶段的 steps，这里回放
 *   运行中真实收到过的那份（按阶段缓存进本机快照）；没有采集到的阶段明确显示未下发，
 *   不补任何前端臆造的清单。
 */

const pad2 = (n: number): string => String(n).padStart(2, '0');

/**
 * 秒 → 计时器写法（2026-11-19 按用户反馈统一口径）：
 * - 不足 1 秒一律写「<1s」，亚秒耗时不再用小数表达（0.43 这种读数扫不出来，
 *   同一列数字也会一会儿宽一会儿窄）；
 * - 其余四舍五入到整秒，秒以下完全不出现小数点：59.6 进位成「01:00」，不会停在「00:59」；
 * - 分与小时都补零，等宽字体下宽度恒定：「00:07」/「01:23」/「01:02:05」。
 */
const fmtClock = (sec?: number | null): string => {
    if (typeof sec !== 'number' || !Number.isFinite(sec) || sec < 0) return '';
    if (sec < 1) return '<1s';
    const t = Math.round(sec);
    const h = Math.floor(t / 3600);
    const m = Math.floor((t % 3600) / 60);
    const s = t % 60;
    return h > 0
        ? `${pad2(h)}:${pad2(m)}:${pad2(s)}`
        : `${pad2(m)}:${pad2(s)}`;
};

/** 阶段用时：与汇总表同一个计时器写法；只有拿不到耗时（null / 非法值）才显示「—」 */
const fmtStageTime = (sec?: number | null): string => fmtClock(sec) || '—';

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
    // 最近一次有效的主进度百分比：终止帧缺字段时用它兜底（见下方 overallPercent）
    const lastPercentRef = useRef(0);
    useEffect(() => {
        const pct = status?.percent;
        if (typeof pct === 'number') lastPercentRef.current = pct;
    }, [status]);

    // 定时状态复核节流：SSE 每秒都在推 idle 帧，不能每帧都打 REST
    const schedCheckAtRef = useRef(0);
    // 运行锁释放后的 idle 帧复核节流（同上面，只在本地仍显示运行时复核）
    const idleCheckAtRef = useRef(0);
    // 异任务帧复核节流：SSE 连接时固化的 last_task 会把上一轮构建的帧推进来，
    // 丢弃这类帧后回一次 REST 复核即可（见 onmessage 里的任务一致性判定）
    const foreignFrameAtRef = useRef(0);
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
    /**
     * 后端按阶段回放子步骤（2026-11-17 stage_subs）：定时构建晚进入、刷新、换浏览器时，
     * 早期阶段的 SSE 帧前端从来没收到过。这里一次性补进缓存——只补缓存里还没有的阶段，
     * 在线期间真实收到过的帧更新（不要被首屏快照覆盖回旧值）。
     */
    const applyStageSubs = useCallback((subs?: PipelineStatus['stage_subs']) => {
        if (!subs) return;
        const cache = stepsCacheRef.current;
        let next: Record<string, PipelineSubStep[]> | null = null;
        for (const [key, v] of Object.entries(subs)) {
            if (cache[key] || !v?.steps?.length) continue;
            next = next || { ...cache };
            next[key] = v.steps;
        }
        if (next) {
            stepsCacheRef.current = next;
            setStepsByStage(next);
        }
    }, []);

    const refresh = useCallback(async () => {
        try {
            // 只查一次（2026-11-19 接口收敛）：服务端 /status 在运行锁为空时，
            // 已经自己回落「最近一次构建登记」（pipeline:last，7d）——这正是当年
            // 前端用 localStorage 的 lastTask 二次补查想解决的问题。进度键 24h 过期后，
            // 那次补查必然还是 idle（项目 76 实测：两次响应完全一样），纯浪费一个往返。
            // 本地快照在下面「补历史」里继续用，但不再参与发请求。
            let s = await pipelineApi.status(projectId, undefined, { lean: true });
            // 服务端进度键 24h 过期 / 换了浏览器：用本机最后一次终态快照兜底，
            // 构建结束后阶段时间线也保留（用户要求「步骤条永远不消失」，2026-11-16）
            const snap = readSnap(projectId);
            const live = statusRef.current;
            // 当前正在跟踪的任务：内存里的状态，或（内存清空后）本机登记的任务 id
            const liveTask = live?.task_id || localStorage.getItem(lastTaskKey(projectId)) || '';
            // 本机快照只用于「补历史」：当前正跟踪某个任务时不再回填——
            // 否则上一轮构建留下的快照（例如 completed / 100%）会把刚收到的
            // cancelled / 76%（或停止中的 76%）顶掉，进度条先跳 100% 再跳回来
            //（2026-11-19 停止流程实测）
            if (s.status === 'idle' && !(s.stages || []).length
                && (!liveTask || snap?.task_id === liveTask)) {
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
            // 后端回放各阶段子步骤（2026-11-17）：晚进入的定时构建也能看到早期阶段的明细
            applyStageSubs(s.stage_subs);
            if (s.stage && s.sub?.steps?.length) rememberSteps(s.stage, s.sub.steps);
            setStatus((prev) => {
                // 迟到的 REST 响应（例如点停止后立刻发出的那一次，读完 Redis 时
                // worker 还没收尾）不能把已经收到的同一任务终态顶回「停止中/进行中」，
                // 否则界面会卡在停止中直到下一次复核（2026-11-19 反馈）
                const terminal = (st?: string) => st === 'completed'
                    || st === 'failed' || st === 'cancelled';
                if (prev && prev.task_id && prev.task_id === s.task_id
                    && terminal(prev.status) && !terminal(s.status)) {
                    return prev;
                }
                return s;
            });
            if (s.task_id && s.status !== 'idle') {
                localStorage.setItem(lastTaskKey(projectId), s.task_id);
            }
        } catch {
            /* 状态查询失败不打扰用户（模块未开通等） */
        } finally {
            setLoaded(true);
        }
    }, [projectId, rememberSteps, applyStageSubs]);

    // 项目切换（路由参数变化、组件不重挂载）时清掉上一个项目的进度快照，
    // refresh 返回前不显示旧项目的阶段/进度
    useEffect(() => {
        setStatus(null); setLoaded(false); setPeekKey(null);
        stepsCacheRef.current = {};
        setStepsByStage({});
        lastTaskSeenRef.current = null;
        foreignFrameAtRef.current = 0;
        lastPercentRef.current = 0;
    }, [projectId]);

    useEffect(() => { refresh(); }, [refresh, signal]);

    // 状态上抛：父页面（ProjectOverviewPage）据此做跃迁刷新，免自建 5s 轮询
    useEffect(() => { onPipeStatus?.(status); }, [status, onPipeStatus]);

    // 换了新一轮构建：收回临时查看，并清掉上一轮的步骤缓存（同一任务的首屏加载不清）。
    // 清空后立刻把新任务自带的后端回放（stage_subs）补回来——定时构建开跑时
    // status 首帧就带着早期阶段的明细，不能因为这次清缓存又丢一拍
    useEffect(() => {
        const tid = status?.task_id || null;
        if (lastTaskSeenRef.current && tid && tid !== lastTaskSeenRef.current) {
            setPeekKey(null);
            stepsCacheRef.current = {};
            setStepsByStage({});
            applyStageSubs(status?.stage_subs);
        }
        lastTaskSeenRef.current = tid;
    }, [status?.task_id, applyStageSubs]);

    // 纯跟随：临时查看只在打开时的那个阶段内有效——后端阶段推进（status.stage 变化）
    // 就自动回到实时；构建结束（完成/失败/中止）也收回，回到「构建汇总」
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
            // 让出一次微任务再换票（2026-11-19）：StrictMode 首挂载会立刻「卸载再挂载」，
            // 被作废的这一代不必再换一张一次性票据（开发模式每次进页面省 1 个请求）
            await Promise.resolve();
            if (gen !== subGenRef.current) return;
            const lastTask = localStorage.getItem(lastTaskKey(projectId)) || undefined;
            const url = await pipelineApi.eventsUrl(projectId, lastTask);
            if (gen !== subGenRef.current) return;  // 已卸载/已重订：丢弃，避免遗留孤儿连接
            const es = new EventSource(url);
            // 新连接从"现在"开始计静默：否则刚从后台切回来时，上一次连接留下的
            // 旧时间戳会让半开检测立刻补一次多余的 REST（2026-11-19）
            lastFrameAtRef.current = Date.now();
            es.onopen = () => setSseUp(true);
            es.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    lastFrameAtRef.current = Date.now();
                    // 任务一致性（2026-11-19 停止流程反馈）：SSE 的 last_task 是建立
                    // 连接时固化的兜底——本轮构建结束、运行锁一释放，服务端就回落到
                    // 它继续推帧，上一轮构建的 stages/percent 会整条顶掉本轮。界面表现
                    // 就是「停止中」一变终态，阶段时间线整块挪位、百分比从 25% 跳到上
                    // 一轮的 78% 再跳回本轮的 42%。帧里带 task_id：与本地正在跟踪的
                    // 任务不一致就丢弃，改回 REST 复核（REST 以运行锁 / 最近一次发起
                    // 登记为准，不会拿旧任务顶包）。
                    const trackedTask = statusRef.current?.task_id
                        || localStorage.getItem(lastTaskKey(projectId)) || '';
                    if (data.status && data.status !== 'idle' && data.task_id
                        && trackedTask && data.task_id !== trackedTask) {
                        const now = Date.now();
                        if (now - foreignFrameAtRef.current > 3000) {
                            foreignFrameAtRef.current = now;
                            void refresh();
                        }
                        return;
                    }
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
                        // 终态帧本身已带 stages / percent / message（stats.stages 摊平后），
                        // 这个面板用到的字段都在里面；只有帧里没带 stages 时才回 REST 补齐。
                        // 原来无条件补刷，等于每次构建结束都多打一个 status（2026-11-19）
                        if (!(incomingStages && incomingStages.length)) refresh();
                    }
                } catch { /* 单帧解析失败忽略，下一帧自愈 */ }
            };
            es.onerror = () => {
                es.close();
                if (esRef.current === es) {
                    esRef.current = null;
                    setSseUp(false);
                }
                // 断流不再额外补 REST（2026-11-19）：重订成功后 SSE 首帧就是当前状态；
                // 若流一直连不上，下面的 3s 兜底轮询会接管。每次重连多打一个 status
                // 只是让日志里多出一条"像轮询"的请求
                if (retryRef.current) clearTimeout(retryRef.current);
                retryRef.current = setTimeout(() => {
                    retryRef.current = null;
                    if (streamWantedRef.current) subscribe();
                }, 8000);
            };
            // 服务端 30 分钟上限主动收流（pipeline.py 的「event: timeout」）：不接的话
            // 浏览器会拿已经用掉的票据自动重连，必然 401，再走 onerror + 8s 重试。
            // 这里直接作废旧连接、1s 后换新票重订，省掉那次 401 和 8s 空窗
            es.addEventListener('timeout', () => {
                es.close();
                if (esRef.current === es) {
                    esRef.current = null;
                    setSseUp(false);
                }
                if (retryRef.current) clearTimeout(retryRef.current);
                retryRef.current = setTimeout(() => {
                    retryRef.current = null;
                    if (streamWantedRef.current) subscribe();
                }, 1000);
            });
            esRef.current = es;
        } catch {
            /* ticket 获取失败：一次性 refresh + 轮询兜底照常工作 */
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, refresh, rememberSteps]);

    // 标签页可见性（2026-11-19）：隐藏时关掉 SSE、停掉兜底轮询，切回来重订 + 补一次 REST。
    // 隐藏的标签页没人看，没必要让后端每秒读一次 Redis；回到前台时先 REST 复核，
    // 再把流订回来——后台期间定时构建到点、别的标签页发起的构建都不会漏。
    const [visible, setVisible] = useState(() => !document.hidden);
    useEffect(() => {
        const onVisibility = () => setVisible(!document.hidden);
        document.addEventListener('visibilitychange', onVisibility);
        return () => document.removeEventListener('visibilitychange', onVisibility);
    }, []);
    // 只在"可能在跑"时持有 SSE（2026-11-19 用户反馈）：构建结束/空闲/草稿都没有实时
    // 信息可推，继续挂着连接只会在每次切回标签页时重换票重连，看着像在轮询。
    // 终态一到就断开；点「一键构建」后 refresh 会把 status 置为 running，自动重新订阅。
    const streamWanted = ['running', 'queued', 'cancelling', 'scheduled']
        .includes(status?.status || '');
    const streamWantedRef = useRef(false);
    useEffect(() => { streamWantedRef.current = streamWanted; }, [streamWanted]);

    useEffect(() => {
        // 隐藏时不建连；非运行态也不建连（再次可见/开跑后由依赖变化重订）
        if (!visible || !streamWanted) return;
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
    }, [subscribe, visible, streamWanted]);

    const s = status;
    // 停止中：后端收到取消后保留运行锁，等 worker 在检查点退出（或超时兜底）
    // 才落 cancelled。这段时间既不是「运行中」也不是「已中止」——按钮要禁用、进度要留在原地
    const stopping = s?.status === 'cancelling';
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
        || s.status === 'cancelling'
        || (!!stages.length && s.stages.some((x) => x.status === 'running')
            && s.status !== 'completed' && s.status !== 'failed' && s.status !== 'cancelled'));
    // 断流兜底：SSE 未接通时运行中每 3s 轮询；SSE 在线则全程零轮询
    useEffect(() => {
        // 隐藏的标签页也不轮询（2026-11-19）：切回前台时会先补一次 REST
        if (!runningNow || sseUp || !visible) return;
        const t = setInterval(refresh, 3000);
        return () => clearInterval(t);
    }, [refresh, runningNow, sseUp, s?.task_id, visible]);

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
                message.success('已重新开始，全部文档将重新解析并抽取');
            } else {
                message.success('一键构建已启动，进度见下方');
            }
            if (r.task_id) {
                localStorage.setItem(lastTaskKey(projectId), r.task_id);
                // worker 可能还没写进度键，SSE 首帧会是 idle：把"idle 帧复核"的节流基准
                // 推到当下，避免刚开跑就为一次未落地的 idle 帧补一个 status
                idleCheckAtRef.current = Date.now();
                // 乐观置为运行中（2026-11-19）：让 SSE 立刻订上，省掉一次 status 往返；
                // 阶段与进度由首帧（stats.stages）和后续帧补齐，不需要先问一次 REST
                setStatus((prev) => (prev
                    ? { ...prev, status: 'running', task_id: r.task_id }
                    : {
                        status: 'running', task_id: r.task_id, stages: [],
                        reviews: {
                            pending_by_priority: {}, pending_total: 0,
                            need_human: 0, auto_handled: 0,
                        },
                    } as PipelineStatus));
            } else {
                await refresh();
            }
        } catch (e: any) {
            notifyError(e, '启动失败');
        } finally {
            setStarting(false);
        }
    };

    const cancel = async () => {
        try {
            const r = await pipelineApi.cancel(projectId);
            message.success(r.kind === 'scheduled'
                ? '已取消定时构建（到点不再自动开始）'
                : '已发出停止请求，后台任务退出后转为「已中止」');
            refresh();
        } catch (e: any) {
            notifyError(e, '取消失败');
        }
    };

    if (!loaded) {
        // 首屏骨架（2026-11-19）：原来直接 return null，数据回来时整块突然插入，
        // 下方文档段要往下蹿一大截，观感就是"好几个 loading 陆续冒出来"。
        // 用和正式版式同序同高的骨架占住位置，加载只表现为"往里填内容"
        return (
            <Card size="small" className="mb-3" title={<span>构建</span>}>
                <div className="flex items-center gap-4">
                    <div className="tk-skel h-[26px] w-[64px]" />
                    <div className="tk-skel h-[7px] flex-1 rounded-full" />
                </div>
                <div className="tk-skel mt-3 h-[11px] w-[210px]" />
                <div className="mt-4 flex gap-2">
                    {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="flex flex-1 flex-col items-center gap-2">
                            <div className="tk-skel h-[26px] w-[26px] rounded-full" />
                            <div className="tk-skel h-[10px] w-[54px]" />
                        </div>
                    ))}
                </div>
                <div className="tk-skel mt-4 h-[128px] rounded-[10px]" />
            </Card>
        );
    }

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
    // 正常结束后停在「构建汇总」。
    // 阶段切换的空窗（上一阶段已 done、下一阶段还没置 running）沿用上一个有结果的阶段
    const gapStage = runningNow && !runningStage
        ? (stages.find((st) => st.status === 'pending') || null) : null;
    const fallbackKey = runningNow
        ? (runningStage?.key ?? gapStage?.key ?? lastActionableStage?.key ?? 'summary')
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
    // 各阶段耗时求和：优先后端 duration_seconds，旧任务没下发时由 started/finished 现算，
    // 否则汇总会少算整段（旧任务甚至求和为 0，只能落到「本次构建已完成」）
    const totalDuration = stages.reduce((n, st) => n + (stageDuration(st) ?? 0), 0);
    const starts = stages.map((st) => tsOf(st.started_at)).filter((t): t is number => t != null);
    const ends = stages.map((st) => tsOf(st.finished_at)).filter((t): t is number => t != null);
    const overallElapsed = starts.length
        ? Math.max(0, ((runningNow ? nowMs : (ends.length ? Math.max(...ends) : nowMs))
            - Math.min(...starts)) / 1000)
        : null;

    // 终止态的真实进度由后端保留（cancelled/failed 都是打断时的 percent，不是 100）。
    // 若某一帧没带 percent，沿用上一次的读数——绝不能再回落成 100%，
    // 否则「停止中 → 已中止」会先跳到 100% 再跳回来（2026-11-19 反馈）
    const overallPercent = s?.status === 'completed' ? 100
        : typeof mainPercent === 'number' ? mainPercent
            : finished ? lastPercentRef.current : 0;
    const overallState = s?.status === 'completed' && !runningNow ? 'done'
        : s?.status === 'failed' ? 'failed'
            : stopping ? 'stopping'
                : s?.status === 'cancelled' ? 'cancelled' : 'running';
    const overallMeta = stopping
        ? (s?.message || '正在停止（等待任务退出）…')
        : runningNow
        ? `已完成 ${doneCount}/${stages.length} 个阶段`
        : s?.status === 'completed'
            ? (totalDuration > 0 ? `本次构建用时 ${fmtClock(totalDuration)}` : '本次构建已完成')
            : s?.status === 'failed'
                ? `构建未完成${failedStage ? ` · 停在「${stageLabel(failedStage)}」` : ''}`
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

    // 段头只补充运行中的上下文：完成态的总用时已经在总进度行与汇总区表达，不再重复第三遍。
    // 「已用」拿不到起点时（旧任务没有 started_at）整段略去，不显示「已用 —」；
    // 计时同样走 01:23 的计时器写法（2026-11-17），与阶段头部那枚计时药丸同一套
    const headSub = stopping
        ? [
            '正在停止，等待后台任务退出',
            overallElapsed != null ? `已用 ${fmtClock(overallElapsed)}` : '',
        ].filter(Boolean).join(' · ')
        : runningNow
        ? [
            runningStage ? `当前阶段：${stageLabel(runningStage)}` : '构建进行中',
            overallElapsed != null ? `已用 ${fmtClock(overallElapsed)}` : '',
        ].filter(Boolean).join(' · ')
        : '';

    const chips = activeKey === s?.stage ? statChips(s?.sub?.stats) : [];

    const detailMessage = (st: PipelineStageState, shown: string): string => {
        if (shown === 'stopping') return s?.message || '正在停止（等待任务退出）…';
        if (shown === 'running') return s?.sub?.message || s?.message || st.detail || '正在执行…';
        if (shown === 'failed') return st.detail || s?.message || '阶段失败';
        if (shown === 'cancelled') return st.detail || '已中止';
        if (shown === 'skipped') return '本档位跳过该阶段';
        if (shown === 'pending') return '等待前置阶段完成';
        return st.detail || '已完成';
    };

    const summaryTagState = s?.status === 'completed' ? 'done'
        : s?.status === 'failed' ? 'failed'
            : stopping ? 'stopping'
                : s?.status === 'cancelled' ? 'cancelled' : 'running';

    return (
        <Card
            size="small"
            className="mb-3"
            title={(
                <Space>
                    {runningNow && <SyncOutlined spin style={{ color: stopping ? '#B8790F' : '#5B8DEF' }} />}
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
                    {runningNow && (stopping ? (
                        /* 已发取消、后端还在等 worker 退出（或超时兜底）：
                           期间不能重复点停止，也不能重新构建（后端会拒绝） */
                        <Button size="small" className="tk-warn-soft-btn" loading disabled
                                icon={<PauseCircleOutlined />}>停止中</Button>
                    ) : (
                        <Popconfirm title="停止当前构建？"
                                    description={(
                                        <div className="max-w-[260px] text-[13px]">
                                            立即终止本次构建（含挂起中的 AI 调用）；
                                            已完成的阶段结果保留。
                                        </div>
                                    )}
                                    okText="停止" cancelText="继续"
                                    onConfirm={cancel}>
                            <Button size="small" className="tk-warn-soft-btn" icon={<PauseCircleOutlined />}>停止</Button>
                        </Popconfirm>
                    ))}
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
                    width: 100%; padding: 7px 2px 3px; border-radius: 8px;
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
                    content: ''; position: absolute; top: 20px;
                    left: calc(50% + 17px); right: calc(-50% + 17px);
                    height: 2px; background: #E4ECF8; z-index: 2;
                }
                .pl-tl-item:last-child::after { display: none; }
                .pl-tl-item.done::after { background: rgba(27,135,173,0.34); }
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
                    50% { box-shadow: 0 0 0 6px rgba(91,141,239,0.06); }
                }
                .pl-live-dot {
                    width: 7px; height: 7px; border-radius: 50%; background: #5B8DEF;
                    flex: none; animation: pl-blink 1.4s infinite;
                }
                @keyframes pl-blink { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
                /* 段落标题前的品牌彩条（2026-11-17）：与图谱页 / 资产详情的 .ap-mark 同一枚，
                   用来给「构建汇总」「当前阶段」这类小标题一个明确起点。
                   间距交给所在 flex 行的 gap：早先这里再写 margin-right 8px，
                   叠上 gap-2 变成 16px，彩条和标题之间空出一大截（2026-11-18 反馈） */
                .pl-mark {
                    display: inline-block; flex: none; width: 3px; height: 13px;
                    border-radius: 2px;
                    background: linear-gradient(180deg, #5B8DEF, #8B5CF6);
                }
                /* 后端还没给阶段内百分比时的「不确定态」：一条往返的细条，不显示具体数字 */
                .pl-ind {
                    position: absolute; top: 0; bottom: 0; width: 35%;
                    border-radius: 9999px; display: block;
                    background: linear-gradient(90deg,#84ACF7,#5B8DEF);
                    animation: pl-ind 1.4s ease-in-out infinite;
                }
                @keyframes pl-ind { 0% { left: -35%; } 100% { left: 100%; } }
            `}</style>

            {/* 总进度（阶段边界才更新的后端主键 + 子进度折算，平滑但只增不减）。
                2026-11-17 版式二：百分比 + 进度条一行，状态说明（本次构建用时 / 已完成 N/M 个阶段）
                单独成行放在条子下面——上一版把说明塞在条子右端，结果它和下面那条阶段时间线
                几乎贴在一起，两条"条状物"上下挤成一坨。百分比给固定宽度，
                数字位数变化时条子起点不跟着跳 */}
            {stages.length > 0 && (
                <>
                    <div className="mt-1 flex items-center gap-4">
                        {/* 宽度只保证装得下「100%」（2026-11-17）：26px 等宽下 100% 约 56px，
                            原来留 86px 左边空出一大块；64px 够用，条子起点仍固定 */}
                        <div className="tk-mono w-[64px] shrink-0 text-[26px] font-semibold leading-none"
                             style={{ color: overallState === 'done' ? '#1B87AD'
                                 : overallState === 'failed' ? '#B42318'
                                     : (overallState === 'cancelled' || overallState === 'stopping') ? '#B8790F' : '#5B8DEF' }}>
                            {overallPercent}<span className="ml-[1px] text-[15px] font-medium">%</span>
                        </div>
                        <div className="h-[7px] min-w-0 flex-1 overflow-hidden rounded-full bg-[#EDF1F8]"
                             style={{ boxShadow: 'inset 0 1px 2px rgba(15,23,42,0.06)' }}>
                            <div className="h-full rounded-full transition-[width] duration-500"
                                 style={{ width: `${overallPercent}%`, background: BAR_BG[overallState] || BAR_BG.running }} />
                        </div>
                    </div>
                    <div className="mt-2 text-[11.5px] text-[#8A94AB]">
                        {overallMeta}
                    </div>
                </>
            )}

            {/* 阶段时间线：默认跟随运行中的阶段；点已完成阶段只是临时看一眼，
                运行阶段推进后自动回到实时（纯跟随，无固定）。
                2026-11-17：与上方进度条拉开距离（mt-4），不然两条横条上下贴在一起 */}
            {stages.length > 0 && (
                <div className="pl-tl mt-4">
                    {stages.map((st) => {
                        const pendingGap = runningNow && !runningStage
                            ? stages.find((x) => x.status === 'pending') : null;
                        const shown = (st.status === 'running'
                            || (pendingGap && st.key === pendingGap.key))
                            ? (stopping ? 'stopping' : (!runningNow ? 'cancelled' : 'running'))
                            : st.status;
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
                                    className={`pl-tl-item ${shown} ${activeKey === st.key ? 'selected' : ''}`}>
                                <span className="pl-tl-inner">
                                    <span className="pl-tl-dot" style={{ background: tone.bg, color: tone.color }}>
                                        {shown === 'running'
                                            ? <SyncOutlined spin />
                                            : (STATUS_ICON[shown] || STATUS_ICON.pending)}
                                    </span>
                                    <span className="pl-tl-label">{stageLabel(st)}</span>
                                    <span className="pl-tl-state">{STAGE_STATUS_TEXT[shown] || '待开始'}</span>
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}

            {/* 单一详情区：当前阶段明细 / 构建完成后的阶段耗时汇总。
                2026-11-17：底子从一块浅蓝白改成「冷蓝渐变 + 柔光 + 节点线稿」，
                和卡面本身的白纸区分开——原来是白底压在冷蓝卡上，整段显得没做过设计 */}
            {stages.length > 0 && (
                <div
                    className="relative overflow-hidden rounded-[10px] border border-[rgba(91,141,239,0.18)] px-3.5 py-3"
                    style={{
                        background:
                            'radial-gradient(520px 200px at 100% 0%, rgba(91,141,239,0.16), transparent 72%),'
                            + 'radial-gradient(420px 220px at 0% 100%, rgba(139,92,246,0.09), transparent 74%),'
                            + 'linear-gradient(156deg, #FBFDFF 0%, #F2F7FF 58%, #EDF3FE 100%)',
                    }}
                >
                    <DecoConstellation id="pl-detail"
                                       className="pointer-events-none absolute -bottom-4 -right-6 h-[150px] w-[400px] opacity-70"
                                       style={{ transform: 'scaleY(-1)' }} />
                    <div className="relative">
                    {activeKey === 'summary' || !activeStage ? (
                        <>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="pl-mark" aria-hidden />
                                <span className="text-[14px] font-semibold text-[#0F172A]">构建汇总</span>
                                <Tag style={{ ...STATUS_TAG_STYLE[summaryTagState], marginInlineEnd: 0 }}
                                     className="!text-[11.5px]">
                                    {STAGE_STATUS_TEXT[summaryTagState] || '进行中'}
                                </Tag>
                                {/* 等宽小标签（2026-11-17）：与图谱页的 SNAPSHOT / GRAPH 同一枚装饰件，
                                    补一点科技感，不承载信息 */}
                                <span className="tk-mono ml-auto text-[9.5px] uppercase tracking-[0.18em] text-[#A9B5CC]">
                                    build summary
                                </span>
                            </div>
                            {/* 八阶段耗时（2026-11-17）：原来是一格一格的小白卡，叠在浅蓝明细区里
                                "碎"成八块；改成发丝线分列的表格——标签前一枚状态圆点、右侧等宽数字，
                                与资产详情左栏的指标行同一套排版 */}
                            <div className="mt-2.5 overflow-hidden rounded-[10px] border border-[rgba(91,141,239,0.12)] bg-[rgba(91,141,239,0.10)]">
                                <div className="grid grid-cols-2 gap-px md:grid-cols-4">
                                    {stages.map((st) => {
                                        const tone = STAGE_TONE[st.status] || STAGE_TONE.pending;
                                        // 未开始的阶段不可点（2026-11-17 用户反馈）：上方时间线早就
                                        // 禁掉了 pending，下面这张汇总表却还能点开一个空的「等待前置阶段完成」。
                                        // 两处口径统一：只放行已经走过（完成/跳过）或出过状况的阶段
                                        const peekable = st.status !== 'pending';
                                        return (
                                            <button type="button" key={st.key}
                                                    disabled={!peekable}
                                                    onClick={() => {
                                                        setPeekKey((prev) => (prev === st.key ? null : st.key));
                                                        peekLiveKeyRef.current = status?.stage || null;
                                                    }}
                                                    className={`flex items-center gap-2 bg-[rgba(255,255,255,0.55)] px-3 py-[9px] text-left transition-colors ${
                                                        peekable
                                                            ? 'cursor-pointer hover:bg-[rgba(91,141,239,0.10)]'
                                                            : 'cursor-default'}`}>
                                                <span className="h-[6px] w-[6px] flex-none rounded-full"
                                                      style={{ background: tone.c, boxShadow: `0 0 0 3px ${tone.t}` }} />
                                                <span className="min-w-0 truncate text-[11.5px] text-[#8A94AB]">
                                                    {stageLabel(st)}
                                                </span>
                                                <span className="tk-mono ml-auto whitespace-nowrap text-[12.5px] font-semibold"
                                                      style={{ color: st.status === 'running' ? '#2A57C6' : tone.c }}>
                                                    {st.status === 'skipped' ? '已跳过'
                                                        : st.status === 'pending' ? '—'
                                                            // 与详情区那枚计时药丸共用 stageDuration：
                                                            // 旧任务没有 duration_seconds 时也能由起止时间补算，
                                                            // 运行中的阶段同样按秒走动
                                                            : fmtStageTime(stageDuration(st))}
                                                </span>
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>
                            <div className="mt-2.5 text-[11.5px] text-[#A3ADC2]">
                                点击已开始的阶段，可查看该阶段的明细
                            </div>
                        </>
                    ) : (
                        (() => {
                            const shown = activeStage.status === 'running'
                                ? (stopping ? 'stopping' : (!runningNow ? 'cancelled' : 'running'))
                                : activeStage.status;
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
                            // 后端标了 skipped 的步骤（如解析阶段不需要补写向量）不会再执行，
                            // 灰段只会让这一行"缺一块"，直接不展示（2026-11-17 反馈）。
                            // pending 仍保留：那是后面真会执行的步骤，灰着表示还没轮到
                            const visibleSteps = detailSteps.filter((s) => s.state !== 'skipped');
                            // 方案八的一句话摘要用：当前步骤、步序、已完成步数（全部后端数据）
                            const runStepIdx = visibleSteps.findIndex((s) => s.state === 'running');
                            const runStep = runStepIdx >= 0 ? visibleSteps[runStepIdx] : null;
                            const doneStepCount = visibleSteps.filter((s) => s.state === 'done').length;
                            const ringColor = shown === 'failed' ? '#B42318'
                                : shown === 'done' ? '#1B87AD'
                                    : (shown === 'cancelled' || shown === 'stopping') ? '#B8790F'
                                        : shown === 'running' ? '#5B8DEF' : '#C2C7CF';
                            const ringLabel = STAGE_STATUS_TEXT[shown] || '—';
                            return (
                                <>
                                    {/* 临时查看与实时进度不一致时给出说明：这只是看一眼，
                                        运行阶段一推进就会自动回到实时（2026-11-16 纯跟随） */}
                                    {peekKey && runningNow && runningStage
                                        && activeStage.key !== runningStage.key && (
                                        <div className="mb-2 flex items-center gap-2 rounded-md border border-[rgba(91,141,239,0.35)] bg-[rgba(91,141,239,0.08)] px-2.5 py-[5px] text-[11.5px] text-[#2A57C6]">
                                            <span className="min-w-0 flex-1 truncate">
                                                临时查看「{stageLabel(activeStage)}」· 当前进行「{stageLabel(runningStage)}」，阶段推进后自动回到实时
                                            </span>
                                            <Button type="link" size="small"
                                                    className="!h-auto !p-0 !text-[11.5px]"
                                                    onClick={() => setPeekKey(null)}>
                                                立即回到实时
                                            </Button>
                                        </div>
                                    )}
                                    <div className="flex flex-wrap items-center gap-2">
                                        <span className="pl-mark" aria-hidden />
                                        <span className="text-[14px] font-semibold text-[#0F172A]">
                                            {stageLabel(activeStage)}
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
                                                        {runningNow ? '回到实时进度' : '回到构建汇总'}
                                                    </Button>
                                                </span>
                                            )}
                                            {/* 阶段用时（2026-11-17 改计时器药丸）：原来是一行
                                                「用时 3 分 12 秒」裸文字，运行中每秒跳动、字宽也在变；
                                                改成「时钟图标 + 等宽计时」，和右侧的临时查看入口同一行不打架。
                                                不足 1 秒的阶段（冲突检测 / 结构自检）统一显示「<1s」（2026-11-19） */}
                                            {shown !== 'pending' && (
                                                <span
                                                    className="inline-flex items-center gap-1 rounded-full border px-2 py-[2px]"
                                                    style={{
                                                        // 2026-11-19：原来是纯白 0.66 的药丸，压在冷蓝明细区上像一枚按钮；
                                                        // 改成冷白纸面（与卡片同一档 #FBFDFF），运行中换成淡蓝底 + 极浅光晕，
                                                        // 「在走」和「已停」在材质上就分开了
                                                        background: shown === 'running'
                                                            ? 'rgba(91,141,239,0.10)' : 'rgba(251,253,255,0.72)',
                                                        borderColor: shown === 'running'
                                                            ? 'rgba(91,141,239,0.30)' : 'rgba(91,141,239,0.20)',
                                                        boxShadow: shown === 'running'
                                                            ? '0 0 0 3px rgba(91,141,239,0.06)'
                                                            : 'inset 0 1px 0 rgba(255,255,255,0.80)',
                                                    }}
                                                >
                                                    <ClockCircleOutlined
                                                        style={{
                                                            fontSize: 11,
                                                            color: shown === 'running' ? '#5B8DEF' : '#9AA8C7',
                                                        }} />
                                                    <span
                                                        className={`tk-mono text-[12px] font-medium ${
                                                            shown === 'running' ? 'text-[#2A57C6]' : 'text-[#33415C]'}`}
                                                    >
                                                        {fmtStageTime(live)}
                                                    </span>
                                                </span>
                                            )}
                                        </span>
                                    </div>
                                    {/* 方案八（feat_simplify_tsz_08）：环形总进度 + 一句当前步骤，不画横条。
                                        2026-11-17：当前步骤 / 实时计数 / 状态说明收成环形右侧的一列，
                                        把整块宽度用起来——原来三行各自占满一整行，右侧空出一大半 */}
                                    <div className="mt-2.5 flex items-start gap-3.5">
                                        <PctRing percent={pct} color={ringColor} label={ringLabel} />
                                        <div className="min-w-0 flex-1">
                                            <div className="text-[12px] leading-5 text-[#8A94AB]">
                                                {shown === 'running' ? (
                                                    visibleSteps.length > 0 ? (
                                                        runStep ? (
                                                            <>
                                                                当前：
                                                                {/* 百分比只在左侧圆环里留一处（2026-11-17）：
                                                                    这里再写一遍 38% 和环里是同一个数，读起来重复 */}
                                                                <span className="font-semibold text-[#2A57C6]">
                                                                    {runStep.label}
                                                                </span>
                                                                <span className="text-[#A3ADC2]">
                                                                    （第 {runStepIdx + 1}/{visibleSteps.length} 步）
                                                                </span>
                                                                {doneStepCount > 0 && (
                                                                    <span className="text-[#A3ADC2]"> · 已完成 {doneStepCount} 步</span>
                                                                )}
                                                            </>
                                                        ) : (
                                                            <span className="text-[#A3ADC2]">
                                                                本阶段步骤已完成 {doneStepCount}/{visibleSteps.length}
                                                            </span>
                                                        )
                                                    ) : (
                                                        <span className="text-[#A3ADC2]">等待本阶段子步骤进度…</span>
                                                    )
                                                ) : (
                                                    <span className="text-[#A3ADC2]">
                                                        {shown === 'done' ? '本阶段已完成'
                                                            : shown === 'skipped' ? '本档位跳过该阶段'
                                                                : shown === 'failed' ? '阶段失败'
                                                                    : shown === 'cancelled' ? '已中止' : '等待前置阶段完成'}
                                                    </span>
                                                )}
                                            </div>

                                            {/* 实时计数：切片 / 批次 / 缓存命中 / 指代消解 / 缝合 */}
                                            {chips.length > 0 && (
                                                <div className="mt-1.5 flex flex-wrap gap-1.5">
                                                    {chips.map(([k, v]) => (
                                                        <span key={k}
                                                              className="rounded-md border border-[#E4ECF8] bg-[rgba(251,253,255,0.72)] px-2 py-[1px] text-[11.5px] text-[#8A94AB]">
                                                            {k}{' '}
                                                            <b className="tk-mono font-semibold text-[#2A57C6]">{v}</b>
                                                        </span>
                                                    ))}
                                                </div>
                                            )}

                                            <div className="mt-1.5 flex items-start gap-1.5 text-[11.5px]"
                                                 style={{ color: shown === 'running' ? '#2A57C6'
                                                     : shown === 'failed' ? '#B42318'
                                                         : shown === 'cancelled' ? '#B8790F' : '#8A94AB' }}>
                                                {shown === 'running' && <span className="pl-live-dot mt-[5px]" />}
                                                <span className="min-w-0 leading-5">{detailMessage(activeStage, shown)}</span>
                                            </div>

                                        </div>
                                    </div>
                                </>
                            );
                        })()
                    )}
                    </div>
                </div>
            )}

            {/* 从未构建 / 快照已过期：给一块带图标和下一步的空状态（2026-11-17），
                原来是一行居中灰字压在虚线框里，像一条报错 */}
            {stages.length === 0 && s?.status !== 'scheduled' && (() => {
                const empty = finished
                    ? {
                        icon: <ClockCircleOutlined />,
                        title: '暂无构建明细',
                        hint: '进度快照超过 24 小时已过期。重新构建后可查看各阶段耗时与明细。',
                    }
                    : restartMode
                        ? {
                            icon: <RetweetOutlined />,
                            title: '没有进行中的构建',
                            hint: '点右上角「重新构建」，按现有文档重新解析、重新抽取。',
                        }
                        : {
                            icon: <NodeIndexOutlined />,
                            title: '尚未构建本体',
                            hint: '上传文档后会自动解析并构建本体（解析 → AI 抽取 → 自动审查），完成后这里显示各阶段耗时。',
                        };
                return (
                    <div
                        className="relative mt-1 overflow-hidden rounded-[12px] border border-dashed border-[rgba(91,141,239,0.24)]"
                        style={{
                            background:
                                'radial-gradient(380px 140px at 100% 0%, rgba(91,141,239,0.12), transparent 72%),'
                                + 'linear-gradient(150deg, rgba(255,255,255,0.78) 0%, rgba(237,244,255,0.72) 100%)',
                        }}
                    >
                        <DecoConstellation id="pl-empty"
                                           className="pointer-events-none absolute -bottom-6 -right-4 h-[120px] w-[340px] opacity-60"
                                           style={{ transform: 'scaleY(-1)' }} />
                        <div className="relative flex items-start gap-3 px-4 py-4">
                            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] text-[16px] text-[#2F62D8]"
                                  style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}>
                                {empty.icon}
                            </span>
                            <div className="min-w-0">
                                <div className="text-[13px] font-medium text-[#1F2A44]">{empty.title}</div>
                                <div className="mt-1 text-[11.5px] leading-5 text-[#8A94AB]">{empty.hint}</div>
                            </div>
                        </div>
                    </div>
                );
            })()}

            {/* 状态提示统一走一行式提示条（Alert 带 description 会渲染成一大块）；
                失败提示本身已是完整一句话，tip={false} 避免悬停时把同一句再弹一遍（2026-11-23） */}
            {finished && s?.status === 'failed' && (
                <InlineNotice tone="error" className="mt-2" tip={false}>
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
