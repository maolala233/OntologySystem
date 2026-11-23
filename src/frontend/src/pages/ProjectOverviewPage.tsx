/**
 * 新版项目详情页（新手简化 2026-11 交互改版）：`/projects/:id` 默认落地页。
 * 后台构建态（进度默认收起一行）→ 完成态（结果卡：查看结果/发布/去问答）→ 资产概览。
 * 专家模式（多 Tab 工作台）需 builder_pro 模块授权，未授权不显示入口。
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { message } from '../shared/feedback';
import { Button, Tag } from 'antd';

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
import { NodeIndexOutlined, RocketOutlined } from '@ant-design/icons';

import { BuildSummary, pipelineApi } from '../api/pipeline';
import type { PipelineStatus } from '../api/pipeline';
import { versionsApi } from '../api/governance';
import { useAuthStore } from '../shared/auth/authStore';
import { PROJECT_STATUS, statusChip } from '../shared/projectStatus';
import PipelinePanel from '../features/builder/components/PipelinePanel';
import ProjectDocsCard from '../features/builder/components/ProjectDocsCard';
import BuildResultsPanel from '../features/builder/components/BuildResultsPanel';
import { useBuilderStore } from '../features/builder/store';
import type { ProjectData } from '../types/ontology';
import Navbar from '../components/Layout/Navbar';
import Tip from '../shared/Tip';

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
    const [publishedVersionNo, setPublishedVersionNo] = useState<number | null>(null);
    // 实时管线状态（5s 轮询权威值，比摘要里的 last_pipeline 新）+ 文档数
    const [pipeStatus, setPipeStatus] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!Number.isFinite(pid) || pid <= 0) return;
        let s: BuildSummary | null = null;
        try {
            s = await pipelineApi.buildSummary(pid, { lean: true });
            setSummary(s);
        } catch { /* 模块未开通等：静默 */ }
        // 已发布版本号只服务结果卡上那枚「已发布 vN」标签（2026-11-19 接口收敛）：
        // ① summary 已经带 published_version_no，直接用，不再拉 /versions；
        // ② 未发布项目根本不需要版本列表，也不再白拉；
        // ③ 只有「已发布但 summary 没带版本号」（API 进程还是旧代码）才回退一次。
        //    判定仍是 publication 快照按 version_no 取最大——is_current 是"最新快照"，
        //    schema/full 入库也会置 current，拿它当发布状态会显示假「已发布 vN」（2026-11-06）
        const vno = s?.published_version_no;
        if (typeof vno === 'number' && vno > 0) {
            setPublishedVersionNo(vno);
        } else if (s?.is_published) {
            try {
                const v = await versionsApi.list(pid);
                const pubs = (v.items || []).filter((x) => x.kind === 'publication');
                setPublishedVersionNo(pubs.sort((a, b) => b.version_no - a.version_no)[0]?.version_no ?? null);
            } catch { /* 版本信息失败不阻断 */ }
        } else if (s) {
            setPublishedVersionNo(null);
        }
    }, [pid]);

    // 路由参数直接切到别的项目（组件不重挂载）时，清掉本页保存的上一个项目的快照/结果，
    // 否则结果卡、状态徽标会先显示旧项目的数字，等新请求回来才被覆盖
    useEffect(() => {
        setSummary(null);
        setPublishedVersionNo(null);
        setPipeStatus(null);
        setPipeStage(null);
        lastPipeStatusRef.current = null;
    }, [pid]);

    // 新建项目跳转过来时带一份创建接口的返回值（2026-11-19）：10 秒内直接采用，
    // 省掉刚进页面又拉一次 /projects/:id。超时一律回源——浏览器刷新会重放历史 state，
    // 不能把创建时的旧状态当成当前状态
    const location = useLocation();
    useEffect(() => {
        const st = location.state as { project?: ProjectData; at?: number } | null;
        const preloaded = st?.project?.id === pid && typeof st.at === 'number'
            && Date.now() - st.at < 10000 ? st.project : undefined;
        loadProject(pid, preloaded);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pid, loadProject]);
    useEffect(() => { load(); }, [load, pipelineSignal]);

    // 上传后自动起构建（2026-11-23）：文档卡上传成功即调用 pipelineApi.run，
    // 这里把信号 +1 让进度面板立刻重查并订上 SSE——不需要用户再点「一键构建」，
    // 也不需要人工确认构建有没有真的跑起来。
    const bumpPipelineSignal = useCallback(() => setPipelineSignal((v) => v + 1), []);

    // 生命周期状态只分 draft/building/ready/published，没有「中止/失败」——
    // 构建被中止或失败后只要已产出骨架，后端仍标 ready（已构建），语义上会误导。
    // 这里以最近一次管线终态覆盖显示：已发布优先，其次中止/失败。
    // 构建中 / 待构建直接由实时管线状态给出（2026-11-19）：这两个状态本来就已经
    // 通过 SSE 上抛了，再为了一枚徽标去重拉 /projects/:id 是白跑一趟。
    // 优先级与后端 get_project 一致：构建中 > 待构建 > 已发布 > …
    const statusKey = (pipeStatus === 'running' || pipeStatus === 'queued')
        ? 'building'
        : pipeStatus === 'scheduled'
            ? 'scheduled'
            : projectStatus === 'published'
                ? 'published'
                : pipeStatus === 'cancelling'
                    ? 'stopping'
                    : pipeStatus === 'cancelled'
                        ? 'cancelled'
                        : pipeStatus === 'failed'
                            ? 'failed'
                            : (PROJECT_STATUS[projectStatus] ? projectStatus : 'draft');
    const statusMeta = statusKey === 'stopping'
        ? { color: 'warning', text: '停止中', desc: '已发出停止请求，等后台任务退出后转为已中止' }
        : statusKey === 'failed'
            ? { color: 'error', text: '构建失败', desc: '最近一次构建失败' }
            : statusKey === 'cancelled'
                ? { color: 'warning', text: '已中止', desc: '最近一次构建被中止，已完成阶段的成果保留' }
                : PROJECT_STATUS[statusKey] || PROJECT_STATUS.draft;
    // 构建中判定以轮询到的实时状态为准（摘要里的 last_pipeline 可能滞后一拍）
    // 「停止中」也按构建中处理：后端保留运行锁，页面继续显示进度区、结果区让位
    const building = ['running', 'queued', 'scheduled', 'cancelling'].includes(pipeStatus || '')
        || (!pipeStatus && (summary?.pipeline?.status === 'running'
            || summary?.pipeline?.status === 'queued'
            || summary?.pipeline?.status === 'scheduled'
            || summary?.pipeline?.status === 'cancelling'));
    // 文档集合是否锁住：后端只在真正跑构建时拒绝改文档（c9b9388，409 PIPELINE_RUNNING），
    // 「待构建（scheduled）」不占运行锁，仍可增删（2026-11-16）；
    // 「停止中（cancelling）」backend 仍持锁（老任务可能还在写产物），继续锁住（2026-11-19）
    const docsLocked = ['running', 'queued', 'cancelling'].includes(pipeStatus || '')
        || (!pipeStatus && ['running', 'queued', 'cancelling'].includes(summary?.pipeline?.status || ''));

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
            if (s.status === 'failed') {
                // 阶段失败的后端文案已自带「骨架抽取失败：…」前缀（2026-11-18），
                // 再叠一层「构建失败：」就是「构建失败：骨架抽取失败：…」；只给
                // 没有阶段信息的原因补前缀
                const msg = s.message || '未知原因';
                message.error(/失败[:：]/.test(msg) ? msg : `构建失败：${msg}`);
            }
        }
        // idle/running/queued/scheduled 之间跃迁一律不重拉（2026-11-19）：
        // 结果区是否显示由 building 决定，徽标由上面的 pipeStatus 推导，
        // 项目元数据（名称/领域/时间）在这段时间不会变；终态才需要同步一次
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
                    border-top: 1px solid rgba(91, 141, 239, 0.10);
                }
                /* 卡面材质（2026-11-17）：不再是一张纯白纸——冷白底 + 左上蓝光 + 右下紫光，
                   与工作台说明板同源（只保留"渐变 + 大号柔光"，不加深色颗粒肌理）。
                   里面每一段保持半透明白，形成"冷蓝纸 → 白卡片"的层次；
                   原来纯白卡在浅蓝画布上像一张贴纸，和 Hero 的冷调也对不上 */
                .overview-stack.surface-card {
                    background:
                        radial-gradient(760px 460px at -6% -10%, rgba(91, 141, 239, 0.18), transparent 68%),
                        radial-gradient(680px 520px at 106% 112%, rgba(139, 92, 246, 0.12), transparent 70%),
                        linear-gradient(160deg, #F6FAFF 0%, #EDF4FE 55%, #F3F8FF 100%);
                    border-color: #DFE8F6;
                    box-shadow:
                        inset 0 1px 0 rgba(255, 255, 255, 0.92),
                        inset 0 -1px 0 rgba(210, 222, 240, 0.55),
                        0 14px 34px -22px rgba(15, 23, 42, 0.28),
                        0 2px 5px rgba(15, 23, 42, 0.04);
                }
                /* 段头留白（2026-11-16）：
                   ① 段头 6px 上留白 + min-height 44 → 段标题距上方分隔线约 14px。
                      原来只有 8px，标题贴着分隔线显得挤；
                   ② 段体上留白 10px（原 16），把"段标题→内容"从 26px 收到 17px ——
                      文档段下面紧跟表头色带，原来那一大块空白尤其显空。
                   注意：6px 只加在顶部（padding: 6px 20px 0），底部不加，
                   否则段标题到内容又会变松。 */
                .overview-stack > .ant-card > .ant-card-head {
                    min-height: 44px;
                    padding: 6px 20px 0;
                    border-bottom: 0;
                    background: transparent;
                }
                .overview-stack > .ant-card > .ant-card-head .ant-card-head-title {
                    display: flex;
                    align-items: center;
                    min-width: 0;
                    font-size: 14px;
                    font-weight: 600;
                    color: #1F2A44;
                }
                /* 段标题前的品牌彩条（2026-11-17）：与资产详情 / 图谱页同一枚装饰件，
                   让三段标题在整张白面里有明确的起点；用伪元素挂上去，不改子组件结构 */
                .overview-stack > .ant-card > .ant-card-head .ant-card-head-title::before {
                    content: '';
                    flex: none;
                    width: 3px;
                    height: 13px;
                    margin-right: 8px;
                    border-radius: 2px;
                    background: linear-gradient(180deg, #5B8DEF, #8B5CF6);
                }
                .overview-stack > .ant-card > .ant-card-head .ant-card-head-title > span {
                    min-width: 0;
                }
                .overview-stack > .ant-card > .ant-card-body {
                    padding: 10px 20px 16px;
                }
                /* 段头按钮统一规格（2026-11-17）：重新构建 / 继续上传 / 查询图谱 / 发布，
                   统一 30px 高、12.5px 字——和身份段的两个按钮同高，主次只靠 primary / default 区分 */
                .overview-stack > .ant-card > .ant-card-head .ant-btn {
                    height: 30px;
                    padding-inline: 13px;
                    font-size: 12.5px;
                }
                /* 身份段右上角按钮（专家模式 / 去问答）：与段头按钮同规格 */
                .ov-head-btn.ant-btn {
                    height: 30px;
                    padding-inline: 14px;
                    font-size: 12.5px;
                }
                /* 按钮材质（2026-11-17）：默认 antd 的纯色蓝 / 灰边白底压在冷蓝纸面上像外挂控件。
                   ① 主按钮（重新构建 / 发布到资产中心）：蓝到深蓝的轻渐变 + 内高光 + 近距投影，
                      比纯色 #5B8DEF 更"实"，也压得住卡面；
                   ② 幽灵主按钮（去问答 / 查看图谱）：白底蓝边太飘，改成淡蓝实底的软按钮；
                   ③ 次要按钮（专家模式 / 继续上传 / 取消发布）：半透明白 + 品牌发丝边，
                      文字保持深墨蓝——一屏里只有两个蓝按钮，层级才清楚 */
                .overview-stack .ant-btn-primary:not(.ant-btn-background-ghost):not(:disabled) {
                    background: linear-gradient(180deg, #5B8DEF 0%, #3F6FD8 100%);
                    border-color: transparent;
                    box-shadow: 0 8px 18px -12px rgba(47, 98, 216, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.22);
                }
                .overview-stack .ant-btn-primary:not(.ant-btn-background-ghost):not(:disabled):hover {
                    background: linear-gradient(180deg, #5486EC 0%, #3865CE 100%);
                }
                .overview-stack .ant-btn-primary:not(.ant-btn-background-ghost):not(:disabled):active {
                    background: linear-gradient(180deg, #4E7FE4 0%, #335DC0 100%);
                }
                .overview-stack .ant-btn-primary.ant-btn-background-ghost:not(:disabled) {
                    color: #2A57C6;
                    background: rgba(91, 141, 239, 0.10);
                    border-color: rgba(91, 141, 239, 0.26);
                    box-shadow: none;
                }
                .overview-stack .ant-btn-primary.ant-btn-background-ghost:not(:disabled):hover {
                    color: #2A57C6 !important;
                    background: rgba(91, 141, 239, 0.16);
                    border-color: rgba(91, 141, 239, 0.42);
                }
                .overview-stack .ant-btn-default:not(:disabled) {
                    color: #33415C;
                    background: rgba(255, 255, 255, 0.68);
                    border-color: rgba(91, 141, 239, 0.22);
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
                }
                .overview-stack .ant-btn-default:not(:disabled):hover {
                    color: #1F2A44 !important;
                    background: #FFFFFF;
                    border-color: rgba(91, 141, 239, 0.40);
                }
                /* 身份段底纹去掉（2026-11-17）：卡面本身已经是冷蓝纸，页头再叠一层品牌淡底
                   只是把左上那圈柔光又压平一次；现在让纸面的光自然透到标题后面 */
                /* 表格融进卡面（2026-11-17）：表头色带换成品牌淡蓝、行背景透明——
                   原来行是实白，压在一张冷蓝纸上会横切出一条白色带子 */
                /* 表头与行 hover 要拉开层级（2026-11-19 用户反馈：原来表头 6%、
                   hover 5.5%，叠在这张冷蓝纸上几乎同色，鼠标划过等于没有反馈）。
                   口径和「我的项目」一致：表头是更深的色带，hover 是更浅的一层 */
                .overview-stack .ant-table-thead > tr > th {
                    background: rgba(91, 141, 239, 0.10) !important;
                }
                .overview-stack .ant-table-tbody > tr > td {
                    background: transparent;
                }
                .overview-stack .ant-table-tbody > tr.ant-table-row:hover > td,
                .overview-stack .ant-table-tbody > tr > td.ant-table-cell-row-hover {
                    background: rgba(91, 141, 239, 0.05) !important;
                }
                /* 表格内的链接按钮（下载 / 删除）：字号跟表格收到 12.5px；
                   删除用冷调红——antd 默认 #ff4d4f 在这套冷色里过艳 */
                .overview-stack .ant-table .ant-btn-link {
                    height: 24px;
                    padding-inline: 0;
                    font-size: 12.5px;
                }
                .overview-stack .ant-table .ant-btn-link.ant-btn-dangerous { color: #B42318; }
                .overview-stack .ant-table .ant-btn-link.ant-btn-dangerous:hover { color: #8E1B12; }
                /* 卡内字号三档（2026-11-16）：段标题 14/600（上面已设）· 正文 13 · 辅助 11.5。
                   antd 表格默认 14px 正文、表头也跟着 14，比卡内其他正文大一档；
                   标签统一收到 11.5，避免同一屏里出现 11 / 12 / 13 / 14 四种字号 */
                .overview-stack .ant-table { font-size: 13px; }
                /* 单元格要单独写：antd 把字号挂在 .ant-table-cell 上，
                   只改 .ant-table 不会传到 td（实测仍是 14px） */
                .overview-stack .ant-table-cell { font-size: 13px; }
                .overview-stack .ant-table-thead > tr > th { font-size: 12.5px; }
                .overview-stack .ant-tag { font-size: 11.5px; }
                /* 项目名与描述的截断改用 Tailwind 的 truncate 直接写在元素上
                   （2026-11-16 随页头并入卡内），这里不再需要 .overview-head 的兜底 */
            `}</style>
            {/* 顶栏走全站 Navbar（面包屑承载层级与返回） */}
            <Navbar breadcrumbs={[
                { title: '首页', path: '/' },
                { title: '我的项目', path: '/my-projects' },
                { title: projectReady && projectName ? projectName : '项目详情' },
            ]} />
            {/* 页面留白与工作台 / 我的项目统一（2026-11-16）：p-4 sm:p-6，不再用 lg:px-8 +
                2xl:max-w-[1600px] 居中——那套让详情页在宽屏上比同级页面更"缩"，边界也对不齐 */}
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                {/* 主体：构建 / 文档 / 结果原本是三张独立白卡，堆在一起像三块板子；
                    现在收进同一张白面，用发丝线分段。子组件自身的 Card 由 .overview-stack
                    压平（只影响本页，专家模式 Tab 里的用法不变） */}
                {/* 卡面走全站统一的 .surface-card 材质（2026-11-16）：冷白渐变 + 顶部内高光 +
                   底部内阴影 + 圆角 14，与系统配置五页 / 知识域管理 / MCP 一致。
                   原来手写的纯白 + rounded-xl + 自绘阴影在浅蓝画布上像一张贴纸 */}
                <div className="overview-stack surface-card mb-4">
                    {/* 卡内第 0 段：项目身份（2026-11-16）。原来是挂在页面上的 PageHeader，
                        浮在画布上另起一段，和下面白卡割裂；名称/状态/领域/时间/主操作本质都是
                        "这个项目的属性"，与构建、文档、结果同属一张白面，因此并进卡里当第一段。
                        图标块去掉：层级已由面包屑「我的项目 / 项目名」表达，卡内再来一个图标是重复。
                        内边距 16 / 14 / 20（上/下/左右）：顶部试过 24，整块反而显空，已回退 */}
                    <div className="border-b border-[rgba(91,141,239,0.10)] px-5 pb-3.5 pt-4">
                        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                            <div className="min-w-0 flex-1">
                                <div className="flex min-w-0 items-center gap-2">
                                    {/* 项目名不再挂悬停提示（2026-11-19 用户反馈）：它就在页面标题处，
                                        提示内容与可见文字完全重复；超长由 truncate 收成省略号 */}
                                    <h1 className="m-0 max-w-[280px] min-w-0 truncate text-[18px] font-semibold leading-7 text-[#0F172A]">
                                        {projectReady
                                            ? projectName
                                            // 骨架条占位（2026-11-19）：原来是一行"加载中…"，
                                            // 和下面的转圈/空白叠在一起像多个 loading
                                            : <span className="tk-skel inline-block h-[18px] w-[150px] align-middle" />}
                                    </h1>
                                    {projectReady && (
                                        <Tag
                                            className="!m-0 shrink-0"
                                            style={{ ...statusChip(statusKey), marginInlineEnd: 0 }}
                                        >
                                            {statusMeta.text}
                                        </Tag>
                                    )}
                                    {projectReady && domainName && (
                                        <Tip text={domainName}>
                                            <Tag
                                                className="!m-0 shrink-0 max-w-[150px] overflow-hidden text-ellipsis whitespace-nowrap"
                                                style={{ background: 'rgba(91,141,239,0.10)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.24)', marginInlineEnd: 0 }}
                                            >
                                                {domainName}
                                            </Tag>
                                        </Tip>
                                    )}
                                </div>
                                {projectReady && projectDescription && (
                                    <Tip text={projectDescription}>
                                        <p className="m-0 mt-1 truncate text-[12.5px] leading-5 text-[#66738F]">
                                            {projectDescription}
                                        </p>
                                    </Tip>
                                )}
                            </div>
                            <div className="flex shrink-0 items-center gap-2.5">
                                <span className="hidden whitespace-nowrap text-[11.5px] text-[#A3ADC2] md:inline">
                                    {projectReady
                                        ? `创建 ${fmtDate(projectCreatedAt)} · 更新 ${fmtDate(projectUpdatedAt)}`
                                        : ' '}
                                </span>
                                <span className="hidden h-4 w-px shrink-0 bg-[#E7EEF9] md:inline-block" />
                                {isExpert && (
                                    <Button className="ov-head-btn" icon={<NodeIndexOutlined />}
                                            onClick={() => navigate(`/projects/${pid}/documents`)}>
                                        专家模式
                                    </Button>
                                )}
                                <Button className="ov-head-btn" type="primary" ghost icon={<RocketOutlined />}
                                        onClick={() => navigate(`/qa?projectId=${pid}`)}>
                                    去问答
                                </Button>
                            </div>
                        </div>
                    </div>
                    <PipelinePanel projectId={pid} signal={pipelineSignal}
                                   onPipeStatus={handlePipeStatus}
                                   published={summary?.is_published}
                                   built={(summary?.schema.classes_total ?? 0) > 0}
                                   onNavigate={(tab) => navigate(tab === 'reviews'
                                       ? (isExpert ? `/projects/${pid}/reviews` : `/projects/${pid}/result`)
                                       : `/projects/${pid}/${tab}`)} />

                    {/* 不再挂 onChanged：「解析告一段落」不改变构建产物，摘要由终态跃迁统一刷新；
                        挂了只会在构建结束那一秒和终态刷新撞车，白拉一次 summary（2026-11-19） */}
                    <ProjectDocsCard projectId={pid} pipelineStage={pipeStage}
                                     locked={docsLocked}
                                     onChanged={bumpPipelineSignal} />

                    {!building && summary && summary.schema.classes_total > 0 && (
                        <BuildResultsPanel projectId={pid} summary={summary} checks={summary.checks}
                                           publishedVersionNo={publishedVersionNo}
                                           // 项目元数据还没回来时传 undefined：退回探测逻辑，避免误弹选择器
                                           hasDomain={projectReady ? !!domainName : undefined}
                                           onPublishedChange={() => { load(); loadProject(pid); }} />
                    )}
                </div>
            </div>

        </div>
    );
};

export default ProjectOverviewPage;
