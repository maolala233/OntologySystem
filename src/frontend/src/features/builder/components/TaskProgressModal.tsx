/**
 * 抽取任务进度 Modal（03 §8）：轮询 GET /extraction/tasks/{id}（2s）+ 取消。
 * Schema/Graph 两 Tab 共用；SSE 版（ticket）保留在 SchemaTab 兼容旧交互。
 * 完成后展示待人工确认数量（review_items pending），引导前往项目内审核（04 §6 审核队列）。
 */
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Modal, Progress, Button, Space, Typography } from 'antd';
import { AuditOutlined, StopOutlined } from '@ant-design/icons';
import InlineNotice from '../../../components/InlineNotice';

import { extractionApi } from '../../../api/extraction';
import { reviewsApi } from '../../../api/governance';

const { Text } = Typography;

interface Props {
    projectId: number;
    taskId: string | null;
    open: boolean;
    title?: string;
    onClose: () => void;
    onCompleted?: (stats: Record<string, any>) => void;
    onFailed?: (error?: string) => void;
}

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

const TaskProgressModal: React.FC<Props> = ({
    projectId, taskId, open, title = '任务进度', onClose, onCompleted, onFailed,
}) => {
    const navigate = useNavigate();
    const [progress, setProgress] = useState(0);
    const [message, setMessage] = useState('排队中...');
    const [detail, setDetail] = useState('');
    const [status, setStatus] = useState('queued');
    const [stats, setStats] = useState<Record<string, any>>({});
    const [pendingReviews, setPendingReviews] = useState<number | null>(null);
    const [cancelReq, setCancelReq] = useState(false);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

    useEffect(() => {
        if (!open || !taskId) return;
        let stopped = false;
        setProgress(0);
        setStatus('queued');
        setMessage('排队中...');
        setDetail('');
        setPendingReviews(null);
        setCancelReq(false);

        const poll = async () => {
            try {
                const p = await extractionApi.getTask(projectId, taskId);
                if (stopped) return;
                const pct = typeof p.percent === 'number' ? p.percent
                    : typeof p.progress === 'number' ? p.progress * 100 : 0;
                setProgress(Math.max(0, Math.min(100, pct)));
                setStats(p.stats || {});
                setMessage(p.message || '');
                setDetail(p.detail || '');
                setStatus(p.status);
                if (TERMINAL.has(p.status)) {
                    stop();
                    if (p.status === 'completed') {
                        onCompleted?.(p.stats || {});
                        // 人工确认闸门：抽取完成后统计待审项，引导去项目内审核
                        try {
                            const r = await reviewsApi.listByProject(projectId, { status: 'pending' });
                            if (!stopped) setPendingReviews(r.total ?? r.items?.length ?? 0);
                        } catch {
                            /* 待审计数失败不影响任务完成态 */
                        }
                    } else if (p.status === 'failed') {
                        onFailed?.(p.error || p.message);
                    }
                }
            } catch {
                /* 网络抖动：下一轮重试 */
            }
        };
        const stop = () => {
            if (timerRef.current) {
                clearInterval(timerRef.current);
                timerRef.current = null;
            }
        };
        poll();
        timerRef.current = setInterval(poll, 2000);
        return () => {
            stopped = true;
            stop();
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, taskId, projectId]);

    const handleCancel = async () => {
        if (!taskId) return;
        try {
            await extractionApi.cancelTask(projectId, taskId);
            setCancelReq(true);
            setMessage('取消请求已发送，将在当前阶段结束后生效...');
        } catch (e: any) {
            console.warn('[task] 取消失败', e);
        }
    };

    return (
        <Modal
            open={open}
            title={title}
            onCancel={() => {
                // 终态可直接关；已请求取消的运行中任务也放行（结果将被丢弃，用户无需守着弹窗）
                if (TERMINAL.has(status) || cancelReq) onClose();
            }}
            footer={
                <Space>
                    {!TERMINAL.has(status) && (
                        <Button icon={<StopOutlined />} danger disabled={cancelReq} onClick={handleCancel}>
                            {cancelReq ? '取消中…' : '取消任务'}
                        </Button>
                    )}
                    {status === 'completed' && (pendingReviews ?? 0) > 0 && (
                        <Button
                            type="primary"
                            icon={<AuditOutlined />}
                            onClick={() => navigate(`/projects/${projectId}/reviews`)}
                        >
                            查看待审明细（{pendingReviews} 项）
                        </Button>
                    )}
                    <Button type="primary" disabled={!TERMINAL.has(status)} onClick={onClose}>关闭</Button>
                </Space>
            }
            maskClosable={false}
        >
            <Progress
                percent={progress}
                status={status === 'failed' ? 'exception' : status === 'completed' ? 'success' : 'active'}
            />
            {status !== 'completed' && Number(stats?.chunks_total) > 0 && (
                <div className="mt-1">
                    <Text type="secondary" style={{ fontSize: 12 }}>
                        分片 {stats.chunks_done ?? 0}/{stats.chunks_total}
                        {stats.batches_total ? ` · 批次 ${stats.batch_no ?? '-'}/${stats.batches_total}` : ''}
                        
                    </Text>
                </div>
            )}
            <div className="mt-2">
                <Text strong>{message || status}</Text>
                {detail && (
                    <div className="mt-1">
                        <Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{detail}</Text>
                    </div>
                )}
            </div>
            {/* 一行式提示条：Alert 的 message+description 组合在进度弹窗里太占地方 */}
            {status === 'completed' && (pendingReviews ?? 0) > 0 && (
                <InlineNotice tone="warning" className="mt-3" icon={<AuditOutlined />}>
                    {`抽取完成，有 ${pendingReviews} 项待人工确认，请在「审核」页逐项核验后通过或驳回`}
                </InlineNotice>
            )}
            {cancelReq && status !== 'cancelled' && !TERMINAL.has(status) && (
                <InlineNotice tone="info" className="mt-3">
                    已请求取消：当前阶段结束后生效，进行中的 AI 调用结果会被丢弃
                </InlineNotice>
            )}
            {status === 'cancelled' && (
                <InlineNotice tone="info" className="mt-3">任务已取消</InlineNotice>
            )}
        </Modal>
    );
};

export default TaskProgressModal;
