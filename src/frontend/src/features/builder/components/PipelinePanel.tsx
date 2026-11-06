/**
 * 一键构建管线进度面板（新手简化 2026-11）：四步快速开始清单 + 七阶段实时进度。
 * - 进度只展示实时计数（解析 x/y、切片 x/y、过闸 n 条），不展示预估时间；
 * - 任务 id 存 Redis 锁 + localStorage，切页/刷新不丢（BuilderPage 常驻挂载，不随 Tab 卸载）；
 * - 完成后显示「还剩 N 个高优先级结果等你确认」并直跳审核 Tab。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Alert, Button, Card, Dropdown, Modal, Popconfirm, Space, Spin, Steps, Tag, message } from 'antd';
import {
    CheckCircleOutlined, CloseCircleOutlined, DownOutlined, MinusCircleOutlined,
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
}

const STATUS_ICON: Record<string, React.ReactNode> = {
    done: <CheckCircleOutlined style={{ color: '#10b981' }} />,
    failed: <CloseCircleOutlined style={{ color: '#ef4444' }} />,
    running: <SyncOutlined spin style={{ color: '#3b82f6' }} />,
    skipped: <MinusCircleOutlined style={{ color: '#9ca3af' }} />,
    cancelled: <PauseCircleOutlined style={{ color: '#f59e0b' }} />,
    pending: <span style={{ color: '#d1d5db' }}>○</span>,
};

const PipelinePanel: React.FC<Props> = ({ projectId, signal = 0, onNavigate, published, built }) => {
    const [status, setStatus] = useState<PipelineStatus | null>(null);
    const [loaded, setLoaded] = useState(false);
    const [starting, setStarting] = useState(false);
    // 完结后可收起进度展示区；只折叠不卸载——收起后卡片头保留并出现「展开」，
    // 重新发起构建时自动展开（旧版收起=整卡移除，页面再无恢复入口——2026-11-06 实测修复）
    const [collapsed, setCollapsed] = useState(false);

    const refresh = useCallback(async () => {
        try {
            const lastTask = localStorage.getItem(lastTaskKey(projectId)) || undefined;
            const s = await pipelineApi.status(projectId, lastTask);
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

    const s = status;
    const stages: PipelineStageState[] = s?.stages || [];
    // 运行中 3s 轮询；其余状态不轮询（事件驱动：signal / 手动刷新）
    const runningNow = !!s && (s.status === 'running' || s.status === 'queued'
        || (!!stages.length && s.stages.some((x) => x.status === 'running')
            && s.status !== 'completed' && s.status !== 'failed' && s.status !== 'cancelled'));
    useEffect(() => {
        if (!runningNow) return;
        const t = setInterval(refresh, 3000);
        return () => clearInterval(t);
    }, [refresh, runningNow, s?.task_id]);

    // 重新发起构建（idle→running/queued）时自动展开折叠的进度区
    useEffect(() => {
        if (s && (s.status === 'running' || s.status === 'queued')) setCollapsed(false);
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
                message.success('重头开始：全部文档重新解析并重新抽取（人工编辑与已确认结果会保留）');
            } else {
                message.success('一键构建已启动：解析 → AI 抽取 → AI 自动处理，完成后确认高优先级结果即可');
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
            await pipelineApi.cancel(projectId);
            message.success('正在停止：当前这次 AI 调用返回后立即中断');
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
            styles={collapsed ? { body: { display: 'none' } } : undefined}
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
                <Space>
                    {runningNow && (
                        <Popconfirm title="停止当前构建？"
                                    description={(
                                        <div className="max-w-[260px] text-xs">
                                            立即终止本次构建（含挂起中的 AI 调用）；
                                            已完成的阶段结果保留。停止后可「重头开始」或「继续构建」。
                                        </div>
                                    )}
                                    okText="停止" cancelText="继续"
                                    onConfirm={cancel}>
                            <Button size="small" icon={<PauseCircleOutlined />}>停止</Button>
                        </Popconfirm>
                    )}
                    {!runningNow && (
                        <Dropdown.Button
                            size="small"
                            type="primary"
                            icon={<DownOutlined />}
                            loading={starting}
                            onClick={() => start()}
                            menu={{
                                items: [{ key: 'restart', label: '重头开始（全部重新解析与抽取）', icon: <RetweetOutlined /> }],
                                onClick: ({ key }) => {
                                    if (key === 'restart') {
                                        Modal.confirm({
                                            title: '重头开始？',
                                            content: '全部文档将重新解析，骨架与实例全部重新抽取（人工编辑的部分按保留规则合并）。一般在解析结果有问题或想完全重来时使用。',
                                            okText: '重头开始', cancelText: '取消',
                                            onOk: () => start(true),
                                        });
                                    }
                                },
                            }}
                        >
                            {s?.status === 'cancelled' || s?.status === 'failed' ? '继续构建' : '一键构建'}
                        </Dropdown.Button>
                    )}
                    {finished && (
                        <Button size="small" type="text"
                                onClick={() => setCollapsed(!collapsed)}>
                            {collapsed ? '展开' : '收起'}
                        </Button>
                    )}
                </Space>
            )}
        >
            {/* 三步快速开始清单（当前所处状态高亮，蓝色）+ 七阶段进度 + 提示：收起时整块隐藏 */}
            {!collapsed && (<>
            <Steps
                size="small"
                current={quickCurrent}
                items={QUICK_STEPS.map((t) => ({ title: t }))}
                className="mb-3"
            />

            {/* 七阶段实时进度（仅计数，无预估时间）；历史快照里 running 的格子
                在管线已停止/完成后按中断显示，不再转圈 */}
            {stages.length > 0 && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-1.5 mb-1">
                    {stages.map((st) => {
                        const shown = st.status === 'running' && !runningNow ? 'cancelled' : st.status;
                        return (
                            <div key={st.key}
                                 className={`flex items-center gap-1.5 border rounded px-2 py-1 text-xs ${
                                     shown === 'running' ? 'border-blue-300 bg-blue-50'
                                         : shown === 'failed' ? 'border-red-200 bg-red-50'
                                             : shown === 'cancelled' ? 'border-amber-200 bg-amber-50'
                                                 : 'border-gray-100 bg-white'}`}>
                                {STATUS_ICON[shown] || STATUS_ICON.pending}
                                <span className="font-medium whitespace-nowrap">{st.label}</span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* 实时消息 / 完结提示 */}
            {runningNow && s?.message && (
                <div className="text-xs text-gray-500 mt-1">
                    <Spin size="small" className="mr-1" /> {s.message}
                </div>
            )}
            {finished && s?.status === 'failed' && (
                <Alert type="error" showIcon className="mt-2" message="构建未完成"
                       description={s.message} />
            )}
            {s?.status === 'cancelled' && (
                <Alert type="warning" showIcon className="mt-2" message="构建已停止"
                       description="点击「继续构建」将从被中断的阶段重新执行；要全部重来请用右侧下拉的「重头开始」。" />
            )}
            {!runningNow && !finished && s?.reviews.pending_total ? (
                <Alert type="info" showIcon className="mt-2"
                       message={`有 ${s.reviews.pending_total} 个待审结果将由大模型裁决`}
                       description="裁决完成后自动留痕，无需人工确认；可在「AI 裁决明细」中查看与撤销。"
                       action={<Button size="small" onClick={() => onNavigate?.('reviews')}>查看明细</Button>} />
            ) : null}
            </>)}
        </Card>
    );
};

export default PipelinePanel;
