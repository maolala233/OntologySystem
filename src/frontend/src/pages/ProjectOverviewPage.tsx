/**
 * 新版项目详情页（新手简化 2026-11 交互改版）：`/projects/:id` 默认落地页。
 * 后台构建态（进度默认收起一行）→ 完成态（结果卡：查看结果/发布/去问答）→ 资产概览。
 * 专家模式（多 Tab 工作台）需 builder_pro 模块授权，未授权不显示入口。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { message } from '../shared/feedback';
import { Button, Col, Row, Space, Spin, Statistic, Tag, Typography } from 'antd';

const { Text } = Typography;
import {
    ArrowLeftOutlined, AuditOutlined, DatabaseOutlined,
    NodeIndexOutlined, RocketOutlined,
} from '@ant-design/icons';

import { BuildSummary, pipelineApi } from '../api/pipeline';
import type { PipelineStatus } from '../api/pipeline';
import { EXPORT_FORMAT_EXT, ExportFormatId } from '../api/projects';
import { projectsApi } from '../api/projects';
import { versionsApi, VersionRow } from '../api/governance';
import { useAuthStore } from '../shared/auth/authStore';
import { PROJECT_STATUS } from '../shared/projectStatus';
import PipelinePanel from '../features/builder/components/PipelinePanel';
import ProjectDocsCard from '../features/builder/components/ProjectDocsCard';
import QualityCheckTag from '../features/builder/components/QualityCheckTag';
import BuildResultsPanel from '../features/builder/components/BuildResultsPanel';
import { useBuilderStore } from '../features/builder/store';

const ProjectOverviewPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const pid = Number(projectId);
    const { projectName, projectStatus, domainName, loadProject } = useBuilderStore();
    const { hasModule } = useAuthStore();
    const isExpert = hasModule('builder_pro');

    const [summary, setSummary] = useState<BuildSummary | null>(null);
    const [pipelineSignal, setPipelineSignal] = useState(0);
    const [publishedVersion, setPublishedVersion] = useState<VersionRow | null>(null);
    // 实时管线状态（5s 轮询权威值，比摘要里的 last_pipeline 新）+ 文档数
    const [pipeStatus, setPipeStatus] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!Number.isFinite(pid) || pid <= 0) return;
        try {
            const s = await pipelineApi.buildSummary(pid);
            setSummary(s);
        } catch { /* 模块未开通等：静默 */ }
        try {
            const v = await versionsApi.list(pid);
            // 已发布版本=publication 快照（is_current 是"最新快照"，schema/full 入库也会置 current，
            // 拿它当发布状态会让所有已构建项目都显示假「已发布 vN」——2026-11-06 实测修复）
            const pubs = (v.items || []).filter((x) => x.kind === 'publication');
            setPublishedVersion(pubs.sort((a, b) => b.version_no - a.version_no)[0] || null);
        } catch { /* 版本信息失败不阻断 */ }
    }, [pid]);

    useEffect(() => { loadProject(pid); }, [pid, loadProject]);
    useEffect(() => { load(); }, [load, pipelineSignal]);

    // 生命周期状态只分 draft/building/ready/published，没有「中止/失败」——
    // 构建被中止或失败后只要已产出骨架，后端仍标 ready（已构建），语义上会误导。
    // 这里以最近一次管线终态覆盖显示：已发布优先，其次中止/失败。
    const statusMeta = projectStatus === 'published'
        ? PROJECT_STATUS.published
        : pipeStatus === 'cancelled'
            ? { color: 'warning', text: '已中止', desc: '最近一次构建被中止，已完成阶段的成果保留' }
            : pipeStatus === 'failed'
                ? { color: 'error', text: '构建失败', desc: '最近一次构建失败' }
                : PROJECT_STATUS[projectStatus] || PROJECT_STATUS.draft;
    // 构建中判定以轮询到的实时状态为准（摘要里的 last_pipeline 可能滞后一拍）
    const building = ['running', 'queued', 'scheduled'].includes(pipeStatus || '')
        || (!pipeStatus && (summary?.pipeline?.status === 'running'
            || summary?.pipeline?.status === 'queued'
            || summary?.pipeline?.status === 'scheduled'));

    // 管线状态跃迁自动刷新（2026-11-07 SSE 版）：PipelinePanel 订阅后端事件流
    // （运行锁为事实源），状态经 onPipeStatus 上抛——页面不再自建 5s 轮询
    // （常驻轮询每帧两趟审核统计 SQL，是 status 请求日志不断的主力）。
    // 跃迁语义与旧轮询版一致：idle→running（结果区让位进度）、
    // *→completed/failed/cancelled（结果自动出现）。
    const lastPipeStatusRef = useRef<string | null>(null);
    // 当前运行阶段（下传文档卡：构建中显示「骨架抽取中 / 实例抽取中…」）
    const [pipeStage, setPipeStage] = useState<{ key: string; label: string; status: string; message?: string } | null>(null);
    const handlePipeStatus = useCallback((s: PipelineStatus | null) => {
        if (!s || !Number.isFinite(pid) || pid <= 0) return;
        const stages = s.stages || [];
        // 子任务已 100% 但主阶段尚未置 done（收尾间隙，如骨架索引同步）：视为已完成，
        // 直接看下一个待执行阶段，避免文档卡卡在上一阶段
        // 只在子进度属于当前阶段时才采信（避免沿用上一阶段残留的 100%）
        const subDone = typeof s.sub?.percent === 'number' && s.sub.percent >= 100
            && (!!s.sub.stage && s.sub.stage === s.stage);
        // 只有管线仍在运行/排队时才认阶段：中止/失败/完成后，进度快照里可能还残留
        // running 阶段，不能据此把文档状态显示成「X中」（2026-11-11 中止场景实测）
        const pipelineActive = s.status === 'running' || s.status === 'queued';
        const runningStage = (pipelineActive && !subDone)
            ? stages.find((st) => st.status === 'running')
            : undefined;
        const pendingStage = s.status === 'running' ? stages.find((st) => st.status === 'pending') : undefined;
        const activeStage = runningStage || pendingStage;
        if (activeStage) {
            setPipeStage({ key: activeStage.key, label: activeStage.label, status: activeStage.status, message: s.message });
        } else if (s.status === 'running') {
            // 兜底：管线在跑但拿不到具体阶段（如末阶段被跳过）→ 统一显示「构建中」，
            // 不能让文档状态退回「已解析」
            setPipeStage({ key: 'running', label: '构建', status: 'running', message: s.message });
        } else {
            setPipeStage(null);
        }
        const prev = lastPipeStatusRef.current;
        lastPipeStatusRef.current = s.status;
        setPipeStatus(s.status); // 实时状态供 building/引导区判定
        if (prev === null || s.status === prev) return; // 首查只建立基线；同状态不重拉
        if (s.status === 'completed') {
            load();
            loadProject(pid); // 项目状态（草稿/构建中/已构建/已发布徽标）随管线终态同步
            message.success('构建完成，结果已更新');
        } else if (s.status === 'failed' || s.status === 'cancelled') {
            load();
            loadProject(pid);
            if (s.status === 'failed') message.error('构建失败：' + (s.message || '未知原因'));
        } else {
            load(); // idle/running/queued/scheduled 之间跃迁也同步摘要（结果区随构建态收放）
            loadProject(pid);
        }
    }, [pid, load, loadProject]);

    return (
        <div className="h-full flex flex-col bg-gray-50">
            {/* 头部 */}
            <div className="shrink-0 flex items-center gap-3 px-4 py-2 bg-white border-b border-gray-200">
                <Button icon={<ArrowLeftOutlined />} size="small"
                        onClick={() => navigate('/my-projects')}>返回</Button>
                <span className="font-semibold text-gray-800">{projectName || `项目 #${pid}`}</span>
                <Tag color={statusMeta.color}>{statusMeta.text}</Tag>
                {domainName && <Tag color="blue">{domainName}</Tag>}
                <div className="ml-auto flex items-center gap-2">
                    {isExpert && (
                        <Button size="small" icon={<NodeIndexOutlined />}
                                onClick={() => navigate(`/projects/${pid}/documents`)}>
                            专家模式
                        </Button>
                    )}
                    <Button size="small" type="primary" ghost icon={<RocketOutlined />}
                            onClick={() => navigate(`/qa?projectId=${pid}`)}>
                        去问答
                    </Button>
                </div>
            </div>

            <div className="w-full p-4 lg:px-8 2xl:max-w-[1600px] 2xl:mx-auto flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {/* 构建状态卡（PipelinePanel：四步清单 + 阶段计数；构建中可收起） */}
                <PipelinePanel projectId={pid} signal={pipelineSignal}
                               onPipeStatus={handlePipeStatus}
                               published={summary?.is_published}
                               built={(summary?.schema.classes_total ?? 0) > 0}
                               onNavigate={(tab) => navigate(tab === 'reviews'
                                   ? (isExpert ? `/projects/${pid}/reviews` : `/projects/${pid}/result`)
                                   : `/projects/${pid}/${tab}`)} />

                {/* （资产概览统计行已删除：结果卡头部与文档卡标题已含同一组数字，避免重复） */}

                {/* 文档管理卡：清单 + 上传 + 下载 + 删除（2026-11 构建界面改版）
                    mt-4：卡片自身的 mb-* 被 index.css 的 .ant-card{margin-bottom:0} 覆盖，
                    间距改用外层 div 控制（2026-11-11） */}
                <div className="mt-4 mb-4">
                    <ProjectDocsCard projectId={pid} onChanged={load} pipelineStage={pipeStage} />
                </div>

                {!building && summary && summary.schema.classes_total > 0 && (
                    <div className="mb-4">
                        <BuildResultsPanel projectId={pid} summary={summary} checks={summary.checks}
                                           publishedVersionNo={publishedVersion?.version_no ?? null}
                                           onPublishedChange={() => { load(); loadProject(pid); }} />
                    </div>
                )}

                {/* 空项目引导卡已按用户要求移除（2026-11-06）：构建状态与操作
                    全部由上方 PipelinePanel 承担，不再展示任何引导文案 */}
            </div>

        </div>
    );
};

export default ProjectOverviewPage;
