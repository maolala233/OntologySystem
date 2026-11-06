/**
 * 新版项目详情页（新手简化 2026-11 交互改版）：`/projects/:id` 默认落地页。
 * 后台构建态（进度默认收起一行）→ 完成态（结果卡：查看结果/发布/去问答）→ 资产概览。
 * 专家模式（多 Tab 工作台）需 builder_pro 模块授权，未授权不显示入口。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Button, Col, Row, Space, Spin, Statistic, Tag, Typography, message } from 'antd';

const { Text } = Typography;
import {
    ArrowLeftOutlined, AuditOutlined, DatabaseOutlined,
    NodeIndexOutlined, RocketOutlined,
} from '@ant-design/icons';

import { BuildSummary, pipelineApi } from '../api/pipeline';
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
            setPublishedVersion((v.items || []).find((x) => x.is_current) || null);
        } catch { /* 版本信息失败不阻断 */ }
    }, [pid]);

    useEffect(() => { loadProject(pid); }, [pid, loadProject]);
    useEffect(() => { load(); }, [load, pipelineSignal]);

    const statusMeta = PROJECT_STATUS[projectStatus] || PROJECT_STATUS.draft;
    // 构建中判定以轮询到的实时状态为准（摘要里的 last_pipeline 可能滞后一拍）
    const building = ['running', 'queued', 'scheduled'].includes(pipeStatus || '')
        || (!pipeStatus && (summary?.pipeline?.status === 'running'
            || summary?.pipeline?.status === 'queued'
            || summary?.pipeline?.status === 'scheduled'));

    // 管线状态跃迁自动刷新（2026-11-05 根治版）：
    // 旧实现用"摘要说构建中"做轮询门槛有两个漏洞——①新建项目落地时进度键尚未写入、
    // 轮询永不启动；②页面上点「一键构建」只更新面板自身的状态、父页面摘要不重拉，
    // 构建完成后"原来是啥就是啥"。现改为无条件 5s 轮询 + 任何状态跃迁都重拉摘要：
    // idle→running（结果区让位进度）、*→completed/failed/cancelled（结果自动出现）。
    useEffect(() => {
        if (!Number.isFinite(pid) || pid <= 0) return;
        let stopped = false;
        let last: string | null = null; // null=页面刚打开（首查不触发刷新）
        const timer = setInterval(async () => {
            try {
                const s = await pipelineApi.status(pid);
                if (stopped || !s || s.status === last) return;
                const prev = last;
                last = s.status;
                setPipeStatus(s.status); // 实时状态供 building/引导区判定
                if (prev === null) return; // 首查只建立基线
                if (s.status === 'completed') {
                    load();
                    message.success('构建完成，结果已更新');
                } else if (s.status === 'failed' || s.status === 'cancelled') {
                    load();
                    if (s.status === 'failed') message.error('构建失败：' + (s.message || '未知原因'));
                } else {
                    load(); // idle/running/queued/scheduled 之间跃迁也同步摘要（结果区随构建态收放）
                }
            } catch { /* 轮询失败下一轮再试 */ }
        }, 5000);
        return () => { stopped = true; clearInterval(timer); };
    }, [pid, load]);

    return (
        <div className="min-h-screen bg-gray-50">
            {/* 头部 */}
            <div className="flex items-center gap-3 px-4 py-2 bg-white border-b border-gray-200">
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

            <div className="w-full p-4 lg:px-8 2xl:max-w-[1600px] 2xl:mx-auto">
                {/* 构建状态卡（PipelinePanel：四步清单 + 阶段计数；构建中可收起） */}
                <PipelinePanel projectId={pid} signal={pipelineSignal}
                               published={summary?.is_published}
                               built={(summary?.schema.classes_total ?? 0) > 0}
                               onNavigate={(tab) => navigate(tab === 'reviews'
                                   ? (isExpert ? `/projects/${pid}/reviews` : `/projects/${pid}/result`)
                                   : `/projects/${pid}/${tab}`)} />

                {/* （资产概览统计行已删除：结果卡头部与文档卡标题已含同一组数字，避免重复） */}

                {/* 文档管理卡：清单 + 上传 + 下载 + 删除（2026-11 构建界面改版） */}
                <div className="mb-4">
                    <ProjectDocsCard projectId={pid} onChanged={load} />
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
