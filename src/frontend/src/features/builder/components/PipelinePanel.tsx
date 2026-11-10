/**
 * 一键构建管线进度面板（新手简化 2026-11）：四步快速开始清单 + 七阶段实时进度。
 * - 进度只展示实时计数（解析 x/y、切片 x/y、过闸 n 条），不展示预估时间；
 * - 任务 id 存 Redis 锁 + localStorage，切页/刷新不丢（BuilderPage 常驻挂载，不随 Tab 卸载）；
 * - 完成后显示「还剩 N 个高优先级结果等你确认」并直跳审核 Tab。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message, confirmModal } from '../../../shared/feedback';
import { Alert, Button, Card, Popconfirm, Progress, Space, Steps, Tag } from 'antd';
import {
    CheckCircleOutlined, CloseCircleOutlined, MinusCircleOutlined,
    SyncOutlined, RetweetOutlined, PauseCircleOutlined,
    ThunderboltOutlined,
} from '@ant-design/icons';

import { PipelineStageState, PipelineStatus, pipelineApi } from '../../../api/pipeline';

const QUICK_STEPS = ['上传文档', '一键构建', '查看结果与发布'];

/** 最近一次管线的 task_id（localStorage，锁过期后仍可查 24h 内历史状态） */
const lastTaskKey = (pid: number) => `pipeline:last:${pid}`;

interface Props {
    projectId: number;
    /** 项目已发布（步骤条走到「发布」并展示版本+导出） */
    published?: boolean;
    /** 已有构建结果（步骤条走到「查看结果」） */
    built?: boolean;
    /** 外部触发重查（上传完成等） */
    signal?: number;
    /** 跳转指定 Tab（如 reviews） */
    onNavigate?: (tab: string) => void;
    /** 状态上抛（SSE/refresh 每次更新都回调）：父页面据此做跃迁刷新，免自建轮询 */
    onPipeStatus?: (status: PipelineStatus | null) => void;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
    done: <CheckCircleOutlined style={{ color: '#10b981' }} />,
    failed: <CloseCircleOutlined style={{ color: '#ef4444' }} />,
    running: <SyncOutlined spin style={{ color: '#3b82f6' }} />,
    skipped: <MinusCircleOutlined style={{ color: '#9ca3af' }} />,
    cancelled: <PauseCircleOutlined style={{ color: '#f59e0b' }} />,
    pending: <span style={{ color: '#d1d5db' }}>○</span>,
};

const PipelinePanel: React.FC<Props> = ({ projectId, signal = 0, onNavigate, published, built, onPipeStatus }) => {
    const [status, setStatus] = useState<PipelineStatus | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [starting, setStarting] = useState(false);
    // 完结后可收起进度展示区；只折叠不卸载——收起后卡片头保留并出现「展开」，
    // 重新发起构建时自动展开（旧版收起=整卡移除，页面再无恢复入口——2026-11-06 实测修复）
    // SSE 订阅（2026-11-07）：后端运行锁为事实源推送进度，3s 轮询降级为断流兜底
    const esRef = useRef<EventSource | null>(null);
    const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [sseUp, setSseUp] = useState(false);

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
            setStatus(s);
            if (s.task_id && s.status !== 'idle') {
                localStorage.setItem(lastTaskKey(projectId), s.task_id);
            }
        } catch {
            /* 状态查询失败不打扰用户（模块未开通等） */
        } finally {
            setLoaded(true);
        }
    }, [projectId]);

    useEffect(() => { refresh(); }, [refresh, signal]);

    // 状态上抛：父页面（ProjectOverviewPage）据此做跃迁刷新，免自建 5s 轮询
    useEffect(() => { onPipeStatus?.(status); }, [status, onPipeStatus]);

    // SSE 订阅：服务端每秒看运行锁 diff 推帧、idle→running 跃迁也推——
    // 坐在页面上等构建全程零轮询；断流/超时才轮询兜底 + 8s 换票重订
    const subscribe = useCallback(async () => {
        esRef.current?.close();
        esRef.current = null;
        try {
            const lastTask = localStorage.getItem(lastTaskKey(projectId)) || undefined;
            const url = await pipelineApi.eventsUrl(projectId, lastTask);
            const es = new EventSource(url);
            es.onopen = () => setSseUp(true);
            es.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const noReviews = {
                        pending_by_priority: {}, pending_total: 0, need_human: 0, auto_handled: 0,
                    };
                    // SSE 帧是 Redis 原始进度键：stages 嵌在 stats.stages 里
                    // （摊平到顶层是 REST status 端点的事）——不取的话格子永远
                    // 停在挂载时的快照，消息行在动、格子不动（2026-11-08 实测）
                    const incomingStages = (data.stats && data.stats.stages) || data.stages;
                    // idle 帧不带 stages（保留旧 stages 会把 runningNow 卡在转圈）
                    setStatus((prev) => (data.status === 'idle'
                        ? { ...data, stages: [], reviews: prev?.reviews ?? noReviews }
                        : {
                            ...prev, ...data,
                            stages: incomingStages || prev?.stages || [],
                            reviews: data.reviews ?? prev?.reviews ?? noReviews,
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
    }, [projectId, refresh]);

    useEffect(() => {
        subscribe();
        return () => {
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
    const stages: PipelineStageState[] = s?.stages || [];
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

    // 重新发起构建（idle→running/queued）时自动展开折叠的进度区
    useEffect(() => {
    }, [s?.status, s]);

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

    const quickCurrent = published ? 3
        : s?.status === 'completed' ? 2
            : runningNow ? 1
                : s?.status === 'failed' || s?.status === 'cancelled' ? 1
                    : built ? 2 : 0;

    return (
        <Card
            size="small"
            className="mb-3"
            title={(
                <Space>
                    {runningNow && <SyncOutlined spin style={{ color: '#3b82f6' }} />}
                    <span>快速开始</span>
                    <span className="text-xs text-gray-400 font-normal">
                        上传 → 一键构建 → 查看结果与发布
                    </span>
                </Space>
            )}
            extra={(
                <Space split={<span className="text-gray-300">|</span>}>
                    {runningNow && (
                        <Popconfirm title="停止当前构建？"
                                    description={(
                                        <div className="max-w-[260px] text-xs">
                                            立即终止本次构建（含挂起中的 AI 调用）；
                                            已完成的阶段结果保留。
                                        </div>
                                    )}
                                    okText="停止" cancelText="继续"
                                    onConfirm={cancel}>
                            <Button size="small" icon={<PauseCircleOutlined />}>停止</Button>
                        </Popconfirm>
                    )}
                    {s?.status === 'scheduled' && (
                        <Button size="small" icon={<PauseCircleOutlined />} onClick={cancel}>
                            取消定时
                        </Button>
                    )}
                    {!runningNow && (
                        <Button size="small" type="primary" loading={starting}
                                icon={<RetweetOutlined />}
                                onClick={() => confirmModal({
                                    title: '重新构建？',
                                    content: '全部文档将重新解析，骨架与实例全部重新抽取。',
                                    okText: '重新构建', cancelText: '取消',
                                    onOk: () => start(true),
                                })}>
                            重新构建
                        </Button>
                    )}
                </Space>
            )}
        >
            {/* 三步快速开始清单（当前所处状态高亮，蓝色）+ 七阶段进度 + 完结提示 */}
            <Steps
                size="small"
                current={quickCurrent}
                items={QUICK_STEPS.map((t) => ({ title: t }))}
                className="mb-3"
            />

            {/* 总进度条（2026-11-08：只有"汉字+图标"感知不到进度，补百分比主条） */}
            {runningNow && typeof s?.percent === 'number' && (
                <Progress percent={s.percent} size="small" strokeColor="#5B8DEF"
                          className="mb-2" />
            )}
            {stages.length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5 mb-1">
                    {stages.map((st) => {
                        const shown = st.status === 'running' && !runningNow ? 'cancelled' : st.status;
                        return (
                            <div key={st.key}
                                 className={`flex items-center gap-1.5 border rounded px-2 py-1 text-xs ${
                                     shown === 'running' ? 'border-blue-400 bg-blue-50 shadow-sm'
                                         : shown === 'failed' ? 'border-red-200 bg-red-50'
                                             : shown === 'cancelled' ? 'border-amber-200 bg-amber-50'
                                                 : 'border-gray-100 bg-white'}`}>
                                {STATUS_ICON[shown] || STATUS_ICON.pending}
                                <span className={`whitespace-nowrap ${shown === 'running'
                                    ? 'font-semibold text-blue-700' : 'font-medium'}`}>{st.label}</span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* 当前阶段切片级进度（SSE sub 帧）：切片 x/y + 子百分比——
                多切片慢抽时不再像卡死（2026-11-08）；子条用青色与主条品牌蓝区分层级 */}
            {runningNow && s?.sub?.message && (
                <div className="mt-1 border border-blue-100 bg-blue-50/40 rounded px-2 py-1.5">
                    <div className="text-xs text-blue-700 mb-1">{s.sub.message}</div>
                    {typeof s.sub.percent === 'number' && (
                        <Progress percent={s.sub.percent} size="small" strokeColor="#36CFC9"
                                  showInfo={false} />
                    )}
                    {(() => {
                        const stt = (s.sub?.stats || {}) as Record<string, any>;
                        // 预计剩余已按用户要求移除（估算不准反成噪音），保留切片计数
                        if (typeof stt.chunks_done === 'number' && typeof stt.chunks_total === 'number') {
                            return <div className="text-xs text-gray-400 mt-0.5">切片 {stt.chunks_done}/{stt.chunks_total}</div>;
                        }
                        return null;
                    })()}
                </div>
            )}

            {finished && s?.status === 'failed' && (
                <Alert type="error" showIcon className="mt-2" message="构建未完成"
                       description={s.message} />
            )}
            {s?.status === 'cancelled' && (
                <Alert type="warning" showIcon className="mt-2" message="构建已停止"
                       description="点击「重新构建」将全部重新解析与抽取。" />
            )}
            {!runningNow && !finished && s?.reviews.pending_total ? (
                <Alert type="info" showIcon className="mt-2"
                       message={`有 ${s.reviews.pending_total} 个待审结果将由大模型裁决`}
                       description="裁决完成后自动留痕，无需人工确认；可在「AI 裁决明细」中查看与撤销。"
                       action={<Button size="small" onClick={() => onNavigate?.('reviews')}>查看明细</Button>} />
            ) : null}
        </Card>
    );
};

export default PipelinePanel;
