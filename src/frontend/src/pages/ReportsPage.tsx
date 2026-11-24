// src/pages/ReportsPage.tsx - 主题分析报告/PPT 生成（R8 工具层）
// 围绕分析主题从图谱检索证据（相关实体/关系）→ LLM 生成面向业务读者的报告（md/docx）/ PPT。
// 主题必填；主题建议由 LLM 生成；PPT 支持上传自定义模板（占位符协议自动填写）。
// 视觉（2026-11-23）：与「我的项目 / 资产中心」共用 TechHero 页头 + 冷蓝工作台；
// 左栏配置、右栏输出，长任务用不定进度条而不是编造的百分比。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Input, InputNumber, Popconfirm, Select, Tooltip, Upload } from 'antd';
import { message, notifyError } from '../shared/feedback';
import {
    BulbOutlined, DownloadOutlined, FileImageOutlined, FilePptOutlined, FileTextOutlined,
    PlusOutlined, PlayCircleOutlined, QuestionCircleOutlined, UploadOutlined,
} from '@ant-design/icons';
import {
    deletePptTemplate, downloadPptTemplate, downloadSampleTemplate, generateReport,
    getPptTemplate, suggestTopics, uploadPptTemplate,
    type ReportResult,
} from '../api/reports';
import { projectsApi } from '../api/projects';
import { downloadFromUrl, filenameFromUrl } from '../utils/urlDownload';
import { QA_LAST_PROJECT_ID_KEY } from '../shared/keys';
import type { ProjectData } from '../types/ontology';
import { useLocation, useNavigate } from 'react-router-dom';
import MarkdownAnswer from '../features/qa/MarkdownAnswer';
import Navbar from '../components/Layout/Navbar';
import TechHero from '../components/TechHero';
import { Dot, Note, SectionHead, TkTag } from '../shared/consoleUi';

export default function ReportsPage() {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [projectId, setProjectId] = useState<number | null>(null);
    // kind 由路由决定（R11：报告/PPT 分开授权与入口）
    const kind: 'report' | 'ppt' = useLocation().pathname === '/tools/ppt' ? 'ppt' : 'report';
    const [topic, setTopic] = useState('');
    const [chips, setChips] = useState<string[]>([]);
    const [chipsLoading, setChipsLoading] = useState(false);
    const [maxSlides, setMaxSlides] = useState(8);
    const [includeInferred, setIncludeInferred] = useState(false);
    const [busy, setBusy] = useState(false);
    const [result, setResult] = useState<ReportResult | null>(null);
    const [elapsed, setElapsed] = useState(0);
    const [templateInfo, setTemplateInfo] = useState<{ exists: boolean; filename?: string }>({ exists: false });
    const [templateUploading, setTemplateUploading] = useState(false);
    const [templateDownloading, setTemplateDownloading] = useState(false);
    // 模板信息是异步读取的：首帧先给「读取中」，避免先显示『未上传』再跳成『已上传』
    const [templateLoading, setTemplateLoading] = useState(false);
    // 项目列表未回来前不判定「还没有可用项目」，避免提示文案先错后对
    const [projectsLoading, setProjectsLoading] = useState(true);
    const [useTemplate, setUseTemplate] = useState(true);
    const resultRef = useRef<HTMLElement | null>(null);

    const isPpt = kind === 'ppt';
    // 模板块的稳定文案：项目未选中 / 模板信息读取中 / 已上传 / 未上传 四态，
    // 文案在首帧就占位，避免块从无到有、按钮被顶下去
    const templateReady = projectId != null && !templateLoading;
    const templateTitle = projectId == null
        ? (projectsLoading ? '正在加载项目…' : '选择项目后可上传模板')
        : templateLoading
            ? '正在读取模板…'
            : templateInfo.exists
                ? (templateInfo.filename || '已上传模板')
                : '未上传 · 使用系统默认模板';
    const templateTagText = projectId == null ? '待选项目' : templateLoading ? '读取中' : templateInfo.exists ? '已上传' : '未上传';
    const activeProject = useMemo(
        () => projects.find(p => p.id === projectId) ?? null,
        [projects, projectId],
    );

    const loadTemplateInfo = useCallback(async (pid: number) => {
        setTemplateLoading(true);
        try {
            setTemplateInfo(await getPptTemplate(pid));
        } catch {
            setTemplateInfo({ exists: false });
        } finally {
            setTemplateLoading(false);
        }
    }, []);

    const handleTemplateUpload = useCallback(async (file: File) => {
        if (projectId == null) return;
        setTemplateUploading(true);
        try {
            const r = await uploadPptTemplate(projectId, file);
            message.success(`模板「${r.filename}」已上传`);
            setTemplateInfo({ exists: true, filename: r.filename });
            setUseTemplate(true);
        } catch (e: any) {
            notifyError(e, '模板上传失败');
        } finally {
            setTemplateUploading(false);
        }
    }, [projectId]);

    const handleTemplateDelete = useCallback(async () => {
        if (projectId == null) return;
        try {
            await deletePptTemplate(projectId);
            setTemplateInfo({ exists: false });
            message.success('模板已清除');
        } catch (e) {
            notifyError(e, '清除失败');
        }
    }, [projectId]);

    // 模板下载：预签名直链在当前页拉流保存，不新开标签页（新标签页会先白屏等附件）
    const handleDownload = useCallback(async (kind: 'current' | 'sample') => {
        if (projectId == null || templateDownloading) return;
        setTemplateDownloading(true);
        try {
            if (kind === 'current') {
                const url = await downloadPptTemplate(projectId);
                await downloadFromUrl(url, templateInfo.filename || `PPT模板_project${projectId}.pptx`);
            } else {
                const url = await downloadSampleTemplate(projectId);
                await downloadFromUrl(url, 'PPT模板示例（内含使用说明）.pptx');
            }
        } catch (e: any) {
            notifyError(e, '下载模板失败');
        } finally {
            setTemplateDownloading(false);
        }
    }, [projectId, templateDownloading, templateInfo.filename]);

    // 主题建议由 LLM 基于图谱材料生成（后端缓存 10 分钟），加载不阻塞页面
    const loadChips = useCallback(async (pid: number) => {
        setChipsLoading(true);
        setChips([]);
        try {
            setChips(await suggestTopics(pid));
        } catch {
            setChips([]);
        } finally {
            setChipsLoading(false);
        }
    }, []);

    useEffect(() => {
        (async () => {
            try {
                // 只列「我参与的项目」：报告/PPT 生成走 _ensure_qa_access（要求项目成员），
                // 已发布的公共本体对非成员只读，选中后生成必然 403——与本体问答页保持一致
                const list = await projectsApi.getMyProjects();
                setProjects(list);
                const saved = Number(localStorage.getItem(QA_LAST_PROJECT_ID_KEY));
                const pid = list.find(p => p.id === saved)?.id ?? list[0]?.id ?? null;
                setProjectId(pid);
                if (pid != null) {
                    // 主题建议不自动生成（LLM 调用，网络抖动时挂几分钟）——点击「生成主题建议」按钮触发
                    loadTemplateInfo(pid);
                }
            } catch (e) {
                notifyError(e, '加载项目列表失败');
            } finally {
                setProjectsLoading(false);
            }
        })();
    }, [loadTemplateInfo]);

    // 切项目后刷新模板信息；主题建议保持点击生成（不随切项目自动打 LLM）
    const onProjectChange = useCallback(async (pid: number) => {
        setProjectId(pid);
        setResult(null);
        setChips([]);
        setTemplateInfo({ exists: false });
        localStorage.setItem(QA_LAST_PROJECT_ID_KEY, String(pid));
        loadTemplateInfo(pid);
    }, [loadTemplateInfo]);

    // 生成耗时 60s+：显示计时
    useEffect(() => {
        if (!busy) return;
        setElapsed(0);
        const t = setInterval(() => setElapsed(s => s + 1), 1000);
        return () => clearInterval(t);
    }, [busy]);

    // 结果落地后把它带进视口：左栏配置在窄屏会占掉首屏，结果可能正好在折叠线以下
    useEffect(() => {
        if (!result || busy) return;
        const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        resultRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
    }, [result, busy]);

    const run = useCallback(async () => {
        if (busy) return;  // Enter 连续触发时不重复提交
        if (projectId == null) {
            message.warning('请先选择项目');
            return;
        }
        if (!topic.trim()) {
            message.warning('请先填写分析主题（可点击下方建议快速填入）');
            return;
        }
        setBusy(true);
        setResult(null);
        try {
            const r = await generateReport(projectId, {
                kind,
                topic: topic.trim(),
                max_slides: maxSlides,
                use_template: kind === 'ppt' && useTemplate && templateInfo.exists,
                include_inferred: includeInferred,
            });
            setResult(r);
        } catch (e: any) {
            notifyError(e, '生成失败');
        } finally {
            setBusy(false);
        }
    }, [busy, projectId, kind, topic, maxSlides, includeInferred, useTemplate, templateInfo.exists]);

    // 成品下载：同样走当前页拉流，避免新标签页白屏
    const download = async (url: string) => {
        try {
            await downloadFromUrl(url, filenameFromUrl(url, '下载文件'));
        } catch (e: any) {
            notifyError(e, '下载失败');
        }
    };

    const appendSuggestion = (c: string) => {
        if (busy) return;
        setTopic(t => (t.trim() ? `${t.trim()}与${c}` : c));
    };

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={[{ title: '首页', path: '/' }, { title: '工具' }, { title: isPpt ? 'PPT生成' : '报告生成' }]} />
            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title={isPpt ? 'PPT 演示生成' : '主题分析报告'}
                    titleSuffix={isPpt ? 'SLIDE STUDIO' : 'REPORT STUDIO'}
                    subtitle={isPpt
                        ? '围绕主题从项目图谱检索实体、属性与关系作为证据，生成演示大纲并按模板排版导出 .pptx'
                        : '围绕主题从项目图谱检索相关实体、属性与关系作为证据，生成面向业务读者的分析报告'}
                    showConstellation={projects.length > 0}
                    actions={
                        <div className="flex flex-wrap items-center gap-2">
                            <div className="tk-controls flex items-center gap-2">
                                <span className="text-[12.5px] whitespace-nowrap text-[#66738F]">项目</span>
                                <Select
                                    value={projectId ?? undefined}
                                    onChange={onProjectChange}
                                    placeholder={projectsLoading ? '正在加载项目…' : '选择项目'}
                                    loading={projectsLoading}
                                    className="w-[240px]"
                                    showSearch
                                    optionFilterProp="label"
                                    options={projects.map(p => ({ value: p.id, label: p.name }))}
                                />
                            </div>
                            <span className="inline-flex min-w-0 items-center gap-1.5 text-[11.5px] text-[#66738F]">
                                <Dot tone="cyan" />
                                仅列出我参与的项目
                            </span>
                        </div>
                    }
                />

                <div className="grid gap-4 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
                    {/* ── 左栏：生成配置 ── */}
                    <div className="tk-controls min-w-0 space-y-4">
                        <section className="surface-card p-5">
                            <SectionHead
                                title={isPpt ? '演示主题' : '分析主题'}
                                hint="必填"
                                extra={<TkTag tone="quiet">{isPpt ? '得到演示大纲' : '得到分析正文'}</TkTag>}
                            />
                            <div className="mt-3">
                                <Input
                                    size="large"
                                    value={topic}
                                    onChange={e => setTopic(e.target.value)}
                                    placeholder={isPpt
                                        ? '如：理财产品的风险类型与应对措施'
                                        : '如：理财产品的风险类型与应对措施'}
                                    disabled={busy}
                                    allowClear
                                    onPressEnter={run}
                                    className="!text-[14px]"
                                />
                            </div>

                            {/* 主题建议（AI 生成，点击触发） */}
                            <div className="mt-2.5 flex flex-wrap items-center gap-x-2 gap-y-1.5">
                                <Button
                                    size="small"
                                    className="tk-soft-btn tk-soft-btn-dashed !px-2.5"
                                    icon={<BulbOutlined />}
                                    loading={chipsLoading}
                                    disabled={busy || projectId == null}
                                    onClick={() => projectId != null && loadChips(projectId)}
                                >
                                    生成主题建议
                                </Button>
                                <span className="text-[11px] text-[#8A94AB]">AI 生成，约需 1~2 分钟；也可直接手填</span>
                            </div>

                            {chipsLoading && (
                                <div className="mt-2 flex items-center gap-2 rounded-lg bg-[rgba(91,141,239,0.06)] px-2.5 py-2 text-[11.5px] text-[#66738F]">
                                    <Dot tone="brand" />
                                    正在根据图谱内容生成主题建议，超时会回退到知识锚点…
                                </div>
                            )}
                            {!chipsLoading && chips.length > 0 && (
                                <div className="mt-3">
                                    <div className="mb-1.5 text-[11px] text-[#8A94AB]">点击加入主题（可继续追加）</div>
                                    <div className="flex flex-wrap gap-1.5">
                                        {chips.map(c => (
                                            <button
                                                key={c}
                                                type="button"
                                                disabled={busy}
                                                onClick={() => appendSuggestion(c)}
                                                className="tk-tag tk-tag--brand transition-colors hover:border-[rgba(91,141,239,0.55)] hover:bg-[rgba(91,141,239,0.18)] disabled:cursor-not-allowed disabled:opacity-50"
                                            >
                                                <PlusOutlined className="text-[9px]" />
                                                {c}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </section>

                        <section className="surface-card p-5">
                            <SectionHead
                                title="生成选项"
                                extra={isPpt && (
                                    <span className="flex items-center gap-1.5 text-[12px] text-[#66738F]">
                                        页数
                                        <InputNumber
                                            min={3}
                                            max={15}
                                            value={maxSlides}
                                            onChange={v => setMaxSlides(v ?? 8)}
                                            disabled={busy}
                                            className="w-[72px]"
                                        />
                                    </span>
                                )}
                            />

                            {/* 用 div 而不是 label 包 Checkbox：antd Checkbox 自身就是 label，
                                嵌套 label 会让点复选框时触发两次切换；复选框单独 stopPropagation */}
                            <div
                                className="mt-3 flex cursor-pointer items-start gap-2.5 rounded-xl border border-[rgba(91,141,239,0.16)] bg-[rgba(91,141,239,0.05)] px-3 py-2.5 transition-colors hover:border-[rgba(91,141,239,0.32)] hover:bg-[rgba(91,141,239,0.08)]"
                                onClick={() => { if (!busy) setIncludeInferred(v => !v); }}
                            >
                                <span className="inline-flex pt-0.5" onClick={e => e.stopPropagation()}>
                                    <Checkbox
                                        checked={includeInferred}
                                        disabled={busy}
                                        onChange={e => setIncludeInferred(e.target.checked)}
                                    />
                                </span>
                                <div className="min-w-0">
                                    <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-[#1F2A44]">
                                        附录：推理衍生事实
                                        <Tooltip title={isPpt
                                            ? '在 PPT 末尾追加「推理衍生事实」附录页：系统基于本体公理/规则自动推导的结论，已标注为语义推理产物、非原始事实记载；正文分析不使用该内容。'
                                            : '在报告末尾追加「推理衍生事实」表：系统基于本体公理/规则自动推导的结论，已标注为语义推理产物、非原始事实记载；正文分析不使用该内容。'}>
                                            <QuestionCircleOutlined className="text-[#A9B4C9] text-xs" />
                                        </Tooltip>
                                    </div>
                                    <div className="mt-0.5 text-[11px] leading-[1.6] text-[#8A94AB]">
                                        基于公理/规则推导的结论，单独成表并标注为推理产物
                                    </div>
                                </div>
                            </div>

                            {/* PPT 自定义模板上传（占位符协议自动填写）。
                                始终渲染（不随 projectId 到位才出现）：否则进入页面时这块会迟到，
                                下面的「生成 PPT」按钮会被顶下去——2026-11-23 反馈 */}
                            {isPpt && (
                                <div className="mt-3 rounded-xl border border-[rgba(91,141,239,0.16)] bg-[rgba(91,141,239,0.04)] p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <div className="flex min-w-0 items-center gap-2">
                                            <span
                                                className="tk-stat-icon shrink-0"
                                                style={{ background: 'rgba(139,92,246,0.12)', color: '#6D3FD8' }}
                                            >
                                                <FilePptOutlined />
                                            </span>
                                            <div className="min-w-0">
                                                <div className="flex items-center gap-1.5 text-[12.5px] font-medium text-[#1F2A44]">
                                                    自定义模板
                                                    <Tooltip title="封面页 {{title}}/{{subtitle}}/{{date}}/{{project}}；内容页 {{slide_title}} + {{bullets}}；结尾页（可选）{{closing}}。生成时自动按大纲填写并复制内容页。">
                                                        <QuestionCircleOutlined className="text-[#A9B4C9] text-xs" />
                                                    </Tooltip>
                                                </div>
                                                <div className="max-w-[220px] truncate text-[11px] text-[#8A94AB]">
                                                    {templateTitle}
                                                </div>
                                            </div>
                                        </div>
                                        <TkTag tone={templateReady && templateInfo.exists ? 'violet' : 'quiet'}>
                                            {templateTagText}
                                        </TkTag>
                                    </div>

                                    <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2">
                                        <Upload
                                            accept=".pptx"
                                            showUploadList={false}
                                            beforeUpload={() => false}
                                            disabled={busy || templateUploading || projectId == null || templateLoading}
                                            onChange={({ file }) => {
                                                if (file.status === 'removed' || !file.originFileObj) return;
                                                handleTemplateUpload(file.originFileObj as File);
                                            }}
                                        >
                                            <Button size="small" icon={<UploadOutlined />} loading={templateUploading} disabled={busy || projectId == null || templateLoading}>
                                                上传模板
                                            </Button>
                                        </Upload>
                                        {templateReady && templateInfo.exists && (
                                            <>
                                                <Checkbox checked={useTemplate} disabled={busy} onChange={e => setUseTemplate(e.target.checked)}>
                                                    生成时使用
                                                </Checkbox>
                                                <Button
                                                    size="small"
                                                    type="link"
                                                    className="!px-1"
                                                    loading={templateDownloading}
                                                    onClick={() => handleDownload('current')}
                                                >
                                                    下载
                                                </Button>
                                                <Popconfirm title="确定清除已上传的模板？" onConfirm={handleTemplateDelete}>
                                                    <Button size="small" type="link" danger className="!px-1">清除</Button>
                                                </Popconfirm>
                                            </>
                                        )}
                                    </div>
                                    <div className="mt-1.5 flex min-h-[35px] flex-wrap items-center justify-between gap-2">
                                        <span className="text-[11px] text-[#8A94AB]">
                                            {projectId == null
                                                ? '选择项目后可上传自定义模板'
                                                : templateInfo.exists
                                                    ? '不勾选「生成时使用」时仍走系统默认模板'
                                                    : '未上传时使用系统默认模板（深蓝商务风）'}
                                        </span>
                                        <Button
                                            size="small"
                                            type="link"
                                            className="!px-0 !text-[11px]"
                                            loading={templateDownloading}
                                            disabled={projectId == null || templateDownloading}
                                            onClick={() => handleDownload('sample')}
                                        >
                                            下载示例模板（内含使用说明）
                                        </Button>
                                    </div>
                                </div>
                            )}

                            <div className="mt-4 flex items-center gap-2">
                                <Button
                                    type="primary"
                                    size="large"
                                    icon={<PlayCircleOutlined />}
                                    onClick={run}
                                    loading={busy}
                                    disabled={projectId == null || (isPpt && templateLoading)}
                                    className="tk-primary-btn flex-1 !text-[14px]"
                                >
                                    {busy
                                        ? <>生成中…（<span className="tk-mono">{elapsed}s</span>）</>
                                        : (isPpt ? '生成 PPT' : '生成报告')}
                                </Button>
                                {busy && (
                                    <span className="text-[11px] leading-4 text-[#66738F]">
                                        {isPpt ? <>大纲与排版<br />约 1~3 分钟</> : <>全文生成<br />约 1~2 分钟</>}
                                    </span>
                                )}
                            </div>
                        </section>
                    </div>

                    {/* ── 右栏：运行状态 / 结果 / 引导 ── */}
                    <div className="min-w-0">
                        {busy && (
                            <section className="surface-card p-5">
                                <SectionHead
                                    title={isPpt ? '正在生成演示文稿' : '正在生成分析报告'}
                                    extra={<TkTag tone="brand" mono>{elapsed}s</TkTag>}
                                />
                                <div className="tk-ind-bar mt-4" />
                                <div className="mt-3 text-[12.5px] leading-relaxed text-[#66738F]">
                                    正在围绕「<span className="font-medium text-[#1F2A44]">{topic.trim()}</span>」检索图谱证据并生成
                                    {isPpt ? '演示大纲与排版' : '分析正文'}…
                                </div>
                                <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-[11px] text-[#A3ADC2]">
                                    <span className="flex items-center gap-1.5"><Dot tone="brand" />检索实体与关系</span>
                                    <span className="flex items-center gap-1.5"><Dot tone="violet" />组织{isPpt ? '大纲' : '正文'}</span>
                                    <span className="flex items-center gap-1.5"><Dot tone="quiet" />{isPpt ? '排版导出' : '排版下载'}</span>
                                </div>
                            </section>
                        )}

                        {result && !busy && (
                            <section ref={resultRef} className="surface-card overflow-hidden scroll-mt-4">
                                <div className="flex flex-wrap items-center gap-2 border-b border-[rgba(91,141,239,0.12)] px-5 py-3.5">
                                    <span className="tk-mark" aria-hidden />
                                    <span className="min-w-0 truncate text-[14px] font-semibold text-[#0F172A]">{result.title}</span>
                                    <TkTag tone={result.kind === 'ppt' ? 'violet' : 'brand'}>
                                        {result.kind === 'report' ? '分析报告' : '演示文稿'}
                                    </TkTag>
                                    <span className="tk-mono text-[11.5px] text-[#8A94AB]">{(result.latency_ms / 1000).toFixed(1)}s</span>
                                    <div className="ml-auto flex flex-wrap gap-2">
                                        {Object.entries(result.downloads).map(([fmt, url]) => (
                                            <Button
                                                key={fmt}
                                                size="small"
                                                className="tk-soft-btn"
                                                icon={<DownloadOutlined />}
                                                onClick={() => download(url)}
                                            >
                                                下载 .{fmt}
                                            </Button>
                                        ))}
                                    </div>
                                </div>

                                {result.kind === 'report' && result.content && (
                                    <div className="p-5">
                                        <div className="rounded-xl border border-[rgba(91,141,239,0.14)] bg-[#FBFDFF] px-4 py-3 shadow-[inset_0_1px_0_rgba(255,255,255,0.9)]">
                                            <MarkdownAnswer content={result.content} sourceCount={0} onCite={() => {}} />
                                        </div>
                                    </div>
                                )}

                                {result.kind === 'ppt' && result.outline && (
                                    <div className="space-y-3 p-5">
                                        {/* 封面：与系统默认模板同一支深蓝→紫，只做成品示意 */}
                                        <div
                                            className="relative overflow-hidden rounded-xl px-6 py-7 text-center text-white"
                                            style={{
                                                background: 'linear-gradient(135deg, #1E3A68 0%, #3F6FD8 58%, #7A5CF0 100%)',
                                                boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.20), 0 16px 32px -24px rgba(30,58,104,0.7)',
                                            }}
                                        >
                                            <div className="pointer-events-none absolute -right-10 -top-12 h-40 w-40 rounded-full" style={{ background: 'radial-gradient(circle, rgba(255,255,255,0.22), transparent 70%)' }} />
                                            <div className="tk-mono relative text-[10px] tracking-[0.28em] text-white/60">PRESENTATION OUTLINE</div>
                                            <div className="relative mt-2 text-xl font-bold leading-tight">{result.outline.title}</div>
                                            {result.outline.subtitle && (
                                                <div className="relative mt-1 text-[13px] text-white/80">{result.outline.subtitle}</div>
                                            )}
                                            <div className="relative mt-3 flex flex-wrap items-center justify-center gap-x-2 gap-y-1 text-[11px] text-white/65">
                                                <span>{result.outline.slides.length} 页</span>
                                                <span aria-hidden>·</span>
                                                <span className="max-w-[260px] truncate">{activeProject?.name ?? '当前项目'}</span>
                                            </div>
                                        </div>

                                        <div className="grid gap-3 2xl:grid-cols-2">
                                        {result.outline.slides.map((s, i) => (
                                            <div key={i} className="rounded-xl border border-[#E7EEF9] bg-[#F7F9FD] p-3.5">
                                                <div className="mb-1.5 flex items-start gap-2.5">
                                                    <span
                                                        className="tk-mono mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-lg text-[11px] font-bold text-white"
                                                        style={{
                                                            background: 'linear-gradient(135deg, #5B8DEF, #8B5CF6)',
                                                            boxShadow: '0 6px 14px -10px rgba(91,141,239,0.85)',
                                                        }}
                                                    >
                                                        {i + 1}
                                                    </span>
                                                    <div className="min-w-0 text-[13px] font-semibold leading-6 text-[#1F2A44]">{s.title}</div>
                                                </div>
                                                <ul className="m-0 list-disc space-y-0.5 pl-5 text-[12.5px] leading-[1.65] text-[#52607A] marker:text-[#B7C1D4]">
                                                    {s.bullets.map((b, j) => (
                                                        <li key={j}>{typeof b === 'string' ? b : JSON.stringify(b)}</li>
                                                    ))}
                                                </ul>
                                            </div>
                                        ))}
                                        </div>
                                    </div>
                                )}
                            </section>
                        )}

                        {!result && !busy && (
                            <section className="surface-card p-5 sm:p-6">
                                {projects.length === 0 && !projectsLoading ? (
                                    <div className="flex flex-col items-center py-8 text-center">
                                        <span
                                            className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] text-[#2F62D8]"
                                            style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                        >
                                            {isPpt ? <FileImageOutlined /> : <FileTextOutlined />}
                                        </span>
                                        <div className="mt-4 text-[14px] font-semibold text-[#0F172A]">还没有可用项目</div>
                                        <div className="mt-1.5 max-w-[360px] text-[12.5px] leading-[1.7] text-[#66738F]">
                                            请先在「我的项目」创建项目并完成建模，再回来生成{isPpt ? ' PPT' : '报告'}。
                                        </div>
                                        <Button className="tk-soft-btn mt-4" onClick={() => navigate('/my-projects')}>去我的项目</Button>
                                    </div>
                                ) : (
                                    <>
                                        <div className="flex items-start gap-3">
                                            <span
                                                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-[19px] text-white"
                                                style={{
                                                    background: isPpt
                                                        ? 'linear-gradient(140deg, #6D3FD8 0%, #5A2FBA 100%)'
                                                        : 'linear-gradient(140deg, #2F62D8 0%, #244FB8 100%)',
                                                    boxShadow: isPpt
                                                        ? '0 10px 20px -12px #8B5CF6, inset 0 1px 0 rgba(255,255,255,0.28)'
                                                        : '0 10px 20px -12px #5B8DEF, inset 0 1px 0 rgba(255,255,255,0.28)',
                                                }}
                                            >
                                                {isPpt ? <FilePptOutlined /> : <FileTextOutlined />}
                                            </span>
                                            <div className="min-w-0">
                                                <div className="text-[15px] font-semibold text-[#0F172A]">
                                                    {isPpt ? '从图谱证据生成演示文稿' : '从图谱证据生成分析报告'}
                                                </div>
                                                <div className="mt-1 text-[12.5px] leading-[1.65] text-[#66738F]">
                                                    {isPpt
                                                        ? '系统检索项目图谱中的实体、属性与关系作为证据，生成演示大纲并按模板排版导出 .pptx。'
                                                        : '系统检索项目图谱中的实体、属性与关系作为证据，撰写可下载的主题分析报告（Markdown / Word）。'}
                                                </div>
                                            </div>
                                        </div>

                                        <div className="mt-5 grid gap-3 sm:grid-cols-3">
                                            {[
                                                { t: '选择项目', d: '右上角切换要检索的项目图谱' },
                                                { t: '填写主题', d: isPpt ? '可点主题建议快速填入' : '越具体，证据越聚焦' },
                                                { t: isPpt ? '生成并导出' : '生成并下载', d: isPpt ? '按模板自动排版 .pptx' : '支持 md / docx 下载' },
                                            ].map((s, i) => (
                                                <div key={s.t} className="rounded-xl border border-[#EDF1F8] bg-[rgba(91,141,239,0.035)] px-3 py-2.5">
                                                    <div className="flex items-center gap-2">
                                                        <span
                                                            className="tk-mono flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
                                                            style={{ background: 'linear-gradient(135deg, #5B8DEF 0%, #8B5CF6 100%)' }}
                                                        >
                                                            {i + 1}
                                                        </span>
                                                        <span className="text-[12.5px] font-semibold text-[#1F2A44]">{s.t}</span>
                                                    </div>
                                                    <div className="mt-1 text-[11px] leading-[1.6] text-[#8A94AB]">{s.d}</div>
                                                </div>
                                            ))}
                                        </div>

                                        <Note tone="quiet" className="mt-4">
                                            {isPpt
                                                ? '可先在「生成选项」上传自定义 .pptx 模板（含占位符）；未上传时使用系统默认模板（深蓝商务风）。'
                                                : '正文生成约需 1~2 分钟；生成完成后可下载 Markdown / Word 两种格式。'}
                                        </Note>
                                    </>
                                )}
                            </section>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
