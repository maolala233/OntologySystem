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

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
import {
    ApartmentOutlined,
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
import Navbar from '../components/Layout/Navbar';
import PageHeader from '../components/Layout/PageHeader';

const ProjectOverviewPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const pid = Number(projectId);
    const {
        projectId: storeProjectId, projectName, projectStatus, domainName,
        projectDescription, projectCreatedAt, projectUpdatedAt, loadProject,
    } = useBuilderStore();
    // 元数据属于"当前 pid"才算就绪；切换项目后 fetch 回来之前，头部先显示占位而不是上一个项目的名字
    const projectReady = storeProjectId === pid;
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

    // 路由参数直接切到别的项目（组件不重挂载）时，清掉本页保存的上一个项目的快照/结果，
    // 否则结果卡、状态徽标会先显示旧项目的数字，等新请求回来才被覆盖
    useEffect(() => {
        setSummary(null);
        setPublishedVersion(null);
        setPipeStatus(null);
        setPipeStage(null);
        lastPipeStatusRef.current = null;
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
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                /* 三段共用一个白面（2026-11-13）：子组件各自渲染的 antd Card 在这里被压平，
                   变成"同一张卡里的三个区块"，区块之间一条发丝线——不再像三块独立板子。
                   只作用于本页，专家模式 Tab 里的 Card 外观不受影响 */
                .overview-stack > .ant-card {
                    margin-bottom: 0 !important;
                    border: 0;
                    border-radius: 0;
                    background: transparent;
                    box-shadow: none;
                }
                .overview-stack > .ant-card + .ant-card {
                    border-top: 1px solid #F0F4FB;
                }
                .overview-stack > .ant-card > .ant-card-head {
                    min-height: 44px;
                    padding-inline: 20px;
                    border-bottom: 0;
                    background: transparent;
                }
                .overview-stack > .ant-card > .ant-card-head .ant-card-head-title {
                    font-size: 14px;
                    font-weight: 600;
                    color: #1F2A44;
                }
                .overview-stack > .ant-card > .ant-card-body {
                    padding: 16px 20px;
                }
                /* 页头固定高度（2026-11-13）：超长项目名 / 描述不允许撑开页头。
                   PageHeader 外层是 flex-wrap，长名字会先把左侧组撑满整行再换行，
                   内层的 truncate 就没机会生效——这里强制单行收缩，再对 h1/p 直接兜底 */
                .overview-head { flex-wrap: nowrap !important; }
                .overview-head > div:first-child { flex: 1 1 auto !important; min-width: 0 !important; }
                .overview-head > div:first-child > div { min-width: 0 !important; }
                .overview-head h1 {
                    overflow: hidden;
                    white-space: nowrap;
                    text-overflow: ellipsis;
                }
                .overview-head h1 > span > span:first-child {
                    /* 项目名单独限宽：比描述更窄一档，长名字不会把状态/领域标签推到很远处 */
                    max-width: 320px;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }
                .overview-head p {
                    /* 描述限宽（2026-11-13）：铺满整行会让页头成为一整段长文字，
                       限到 520px 左右读起来是一句说明而不是一条横幅；超出单行省略 */
                    max-width: 520px;
                    display: -webkit-box;
                    -webkit-line-clamp: 1;
                    -webkit-box-orient: vertical;
                    overflow: hidden;
                }
            `}</style>
            {/* 顶栏走全站 Navbar（面包屑承载层级与返回） */}
            <Navbar breadcrumbs={[
                { title: '首页', path: '/' },
                { title: '我的项目', path: '/my-projects' },
                { title: projectReady && projectName ? projectName : '项目详情' },
            ]} />
            <div className="w-full p-4 lg:px-8 2xl:max-w-[1600px] 2xl:mx-auto flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {/* 页头走全站 PageHeader（与报告生成 / 系统配置各页同一规格）：
                    项目身份与主操作直接挂在页面上，不再单独占一张白卡 */}
                <PageHeader
                    className="overview-head"
                    icon={<ApartmentOutlined />}
                    title={(
                        /* 项目名可能很长：名字单行截断（悬停看全称），状态/领域标签 shrink-0 不被挤掉，
                           整行不再换行——否则长名字会把页头撑成三四行，把下面的内容整个推下去 */
                        <span className="flex min-w-0 items-center gap-2">
                            <span className="min-w-0 truncate" title={projectReady ? projectName : undefined}>
                                {projectReady ? projectName : '加载中…'}
                            </span>
                            {projectReady && (
                                <Tag color={statusMeta.color} className="!m-0 shrink-0">{statusMeta.text}</Tag>
                            )}
                            {projectReady && domainName && (
                                <Tag
                                    className="!m-0 shrink-0 max-w-[150px] overflow-hidden text-ellipsis whitespace-nowrap"
                                    title={domainName}
                                    style={{ background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' }}
                                >
                                    {domainName}
                                </Tag>
                            )}
                        </span>
                    )}
                    subtitle={
                        projectReady && projectDescription
                            ? (
                                <span className="line-clamp-1" title={projectDescription}>
                                    {projectDescription}
                                </span>
                            )
                            : undefined
                    }
                    extra={(
                        <>
                            <span className="whitespace-nowrap text-[12px] text-[#A3ADC2] mr-1">
                                {projectReady
                                    ? `创建 ${fmtDate(projectCreatedAt)} · 更新 ${fmtDate(projectUpdatedAt)}`
                                    : ' '}
                            </span>
                            {isExpert && (
                                <Button icon={<NodeIndexOutlined />}
                                        onClick={() => navigate(`/projects/${pid}/documents`)}>
                                    专家模式
                                </Button>
                            )}
                            <Button type="primary" ghost icon={<RocketOutlined />}
                                    onClick={() => navigate(`/qa?projectId=${pid}`)}>
                                去问答
                            </Button>
                        </>
                    )}
                />

                {/* 主体：构建 / 文档 / 结果原本是三张独立白卡，堆在一起像三块板子；
                    现在收进同一张白面，用发丝线分段。子组件自身的 Card 由 .overview-stack
                    压平（只影响本页，专家模式 Tab 里的用法不变） */}
                <div className="overview-stack mb-4 overflow-hidden rounded-xl border border-[#E7EEF9] bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04),0_12px_26px_-22px_rgba(15,23,42,0.28)]">
                    <PipelinePanel projectId={pid} signal={pipelineSignal}
                                   onPipeStatus={handlePipeStatus}
                                   published={summary?.is_published}
                                   built={(summary?.schema.classes_total ?? 0) > 0}
                                   onNavigate={(tab) => navigate(tab === 'reviews'
                                       ? (isExpert ? `/projects/${pid}/reviews` : `/projects/${pid}/result`)
                                       : `/projects/${pid}/${tab}`)} />

                    <ProjectDocsCard projectId={pid} onChanged={load} pipelineStage={pipeStage} />

                    {!building && summary && summary.schema.classes_total > 0 && (
                        <BuildResultsPanel projectId={pid} summary={summary} checks={summary.checks}
                                           publishedVersionNo={publishedVersion?.version_no ?? null}
                                           onPublishedChange={() => { load(); loadProject(pid); }} />
                    )}
                </div>
            </div>

        </div>
    );
};

export default ProjectOverviewPage;
