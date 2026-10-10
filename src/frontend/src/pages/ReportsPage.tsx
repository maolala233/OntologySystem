// src/pages/ReportsPage.tsx - 主题分析报告/PPT 生成（R8 工具层）
// 围绕分析主题从图谱检索证据（相关实体/关系）→ LLM 生成面向业务读者的报告（md/docx）/ PPT。
// 主题必填；主题建议由 LLM 生成；PPT 支持上传自定义模板（占位符协议自动填写）。
import { useCallback, useEffect, useState } from 'react';
import {
    Button, Checkbox, Input, InputNumber, Popconfirm, Select, Spin, Tag,
    Tooltip, Upload, message,
} from 'antd';
import {
    DownloadOutlined, FileTextOutlined, PlayCircleOutlined, QuestionCircleOutlined,
    UploadOutlined,
} from '@ant-design/icons';
import {
    deletePptTemplate, downloadPptTemplate, downloadSampleTemplate, generateReport,
    getPptTemplate, suggestTopics, uploadPptTemplate,
    type ReportResult,
} from '../api/reports';
import { projectsApi } from '../api/projects';
import type { ProjectData } from '../types/ontology';
import { useLocation } from 'react-router-dom';
import MarkdownAnswer from '../features/qa/MarkdownAnswer';

export default function ReportsPage() {
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
    const [useTemplate, setUseTemplate] = useState(true);

    const loadTemplateInfo = useCallback(async (pid: number) => {
        try {
            setTemplateInfo(await getPptTemplate(pid));
        } catch {
            setTemplateInfo({ exists: false });
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
            message.error(e?.response?.data?.error?.message || '模板上传失败');
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
        } catch {
            message.error('清除失败');
        }
    }, [projectId]);

    const handleDownload = useCallback(async (kind: 'current' | 'sample') => {
        if (projectId == null) return;
        try {
            const url = kind === 'current'
                ? await downloadPptTemplate(projectId)
                : await downloadSampleTemplate(projectId);
            window.open(url, '_blank');
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '获取下载链接失败');
        }
    }, [projectId]);

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
                const list = await projectsApi.getSelectableProjects();
                setProjects(list);
                const saved = Number(localStorage.getItem('qa_last_project_id'));
                const pid = list.find(p => p.id === saved)?.id ?? list[0]?.id ?? null;
                setProjectId(pid);
                if (pid != null) {
                    loadChips(pid);
                    loadTemplateInfo(pid);
                }
            } catch {
                message.error('加载项目列表失败');
            }
        })();
    }, [loadChips, loadTemplateInfo]);

    // 切项目后刷新主题建议与模板信息
    const onProjectChange = useCallback(async (pid: number) => {
        setProjectId(pid);
        setResult(null);
        localStorage.setItem('qa_last_project_id', String(pid));
        loadChips(pid);
        loadTemplateInfo(pid);
    }, [loadChips, loadTemplateInfo]);

    // 生成耗时 60s+：显示计时
    useEffect(() => {
        if (!busy) return;
        setElapsed(0);
        const t = setInterval(() => setElapsed(s => s + 1), 1000);
        return () => clearInterval(t);
    }, [busy]);

    const run = useCallback(async () => {
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
            message.error(e?.response?.data?.error?.message || e?.message || '生成失败');
        } finally {
            setBusy(false);
        }
    }, [projectId, kind, topic, maxSlides, includeInferred]);

    const download = (url: string) => window.open(url, '_blank');

    return (
        <div className="p-5 h-full overflow-auto bg-gray-50">
            <div className="max-w-4xl mx-auto">
                <div className="bg-white rounded-xl border border-gray-200 p-4 mb-4">
                    <div className="flex items-center gap-3 flex-wrap">
                        <FileTextOutlined className="text-blue-500 text-lg" />
                        <span className="font-semibold text-gray-800">{kind === 'ppt' ? 'PPT 演示生成' : '主题分析报告'}</span>
                        <div className="flex-1" />
                        <Select
                            value={projectId ?? undefined}
                            onChange={onProjectChange}
                            placeholder="选择项目"
                            className="min-w-48"
                            showSearch
                            optionFilterProp="label"
                            options={projects.map(p => ({ value: p.id, label: p.name }))}
                        />
                    </div>
                    <div className="mt-3">
                        <div className="text-xs text-gray-400 mb-1.5">
                            围绕主题从知识图谱中提取相关实体、属性与关系作为证据，生成面向业务读者的分析
                        </div>
                        <div className="flex items-center gap-3 flex-wrap">
                            <Input
                                value={topic}
                                onChange={e => setTopic(e.target.value)}
                                placeholder="分析主题（必填），如：理财产品的风险类型与应对措施"
                                className="max-w-lg"
                                disabled={busy}
                                onPressEnter={run}
                            />
                            {kind === 'ppt' && (
                                <span className="flex items-center gap-1 text-sm text-gray-500">
                                    页数
                                    <InputNumber min={3} max={15} value={maxSlides}
                                        onChange={v => setMaxSlides(v ?? 8)} disabled={busy} />
                                </span>
                            )}
                            <Button
                                type="primary" icon={<PlayCircleOutlined />} onClick={run}
                                loading={busy} disabled={projectId == null}
                            >
                                {busy ? `生成中…（${elapsed}s）` : '生成'}
                            </Button>
                            {busy && kind === 'report' && (
                                <span className="text-xs text-gray-400">全文生成约需 1~2 分钟，请稍候</span>
                            )}
                        </div>
                        {/* 推理附录（语义推理产物，生成后确定性拼接，不进 LLM 正文材料） */}
                        <div className="mt-2 flex items-center gap-1.5">
                            <Checkbox checked={includeInferred} disabled={busy}
                                onChange={e => setIncludeInferred(e.target.checked)}>
                                附录：推理衍生事实
                            </Checkbox>
                            <Tooltip title="在报告末尾（PPT 为附录页）追加「推理衍生事实」表：系统基于本体公理/规则自动推导的结论，已标注为语义推理产物、非原始事实记载；正文分析不使用该内容。">
                                <QuestionCircleOutlined className="text-gray-300 text-xs" />
                            </Tooltip>
                        </div>

                        {/* PPT 自定义模板上传（占位符协议自动填写） */}
                        {kind === 'ppt' && projectId != null && (
                            <div className="mt-3 rounded-lg border border-dashed border-gray-300 bg-gray-50/60 p-3">
                                <div className="flex items-center gap-2 flex-wrap">
                                    <span className="text-xs font-medium text-gray-600">自定义模板</span>
                                    <Tooltip title="封面页 {{title}}/{{subtitle}}/{{date}}/{{project}}；内容页 {{slide_title}} + {{bullets}}；结尾页（可选）{{closing}}。生成时自动按大纲填写并复制内容页。">
                                        <QuestionCircleOutlined className="text-gray-300 text-xs" />
                                    </Tooltip>
                                    <Upload
                                        accept=".pptx"
                                        showUploadList={false}
                                        beforeUpload={() => false}
                                        disabled={busy || templateUploading}
                                        onChange={({ file }) => {
                                            if (file.status === 'removed' || !file.originFileObj) return;
                                            handleTemplateUpload(file.originFileObj as File);
                                        }}
                                    >
                                        <Button size="small" icon={<UploadOutlined />}
                                            loading={templateUploading} disabled={busy}>
                                            上传模板
                                        </Button>
                                    </Upload>
                                    {templateInfo.exists ? (
                                        <>
                                            <Tag color="blue" className="!m-0">{templateInfo.filename}</Tag>
                                            <Checkbox checked={useTemplate} disabled={busy}
                                                onChange={e => setUseTemplate(e.target.checked)}>生成时使用模板</Checkbox>
                                            <Button size="small" type="link" className="!px-1"
                                                onClick={() => handleDownload('current')}>下载</Button>
                                            <Popconfirm title="确定清除已上传的模板？" onConfirm={handleTemplateDelete}>
                                                <Button size="small" type="link" danger className="!px-1">清除</Button>
                                            </Popconfirm>
                                        </>
                                    ) : (
                                        <span className="text-xs text-gray-400">未上传 · 将使用系统默认模板（深蓝商务风）</span>
                                    )}
                                    <div className="flex-1" />
                                    <Button size="small" type="link" className="!px-1 !text-[11px]"
                                        onClick={() => handleDownload('sample')}>
                                        下载示例模板（内含使用说明）
                                    </Button>
                                </div>
                            </div>
                        )}
                        {chipsLoading && (
                            <div className="flex items-center gap-1.5 mt-2 text-xs text-gray-400">
                                <Spin size="small" /> 正在根据图谱内容生成主题建议…
                            </div>
                        )}
                        {!chipsLoading && chips.length > 0 && (
                            <div className="flex items-center gap-1.5 flex-wrap mt-2">
                                <span className="text-xs text-gray-400">主题建议：</span>
                                {chips.map(c => (
                                    <Tag key={c} className="!m-0 cursor-pointer hover:border-blue-400 hover:text-blue-500"
                                        onClick={() => { if (!busy) setTopic(t => (t ? `${t.trim()}与${c}` : `${c}`)); }}>
                                        {c}
                                    </Tag>
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                {busy && (
                    <div className="bg-white rounded-xl border border-gray-200 p-10 text-center">
                        <Spin size="large" />
                        <div className="text-gray-500 mt-4">
                            正在围绕「{topic.trim()}」检索图谱证据并生成{kind === 'report' ? '分析报告' : '演示文稿大纲'}…
                        </div>
                    </div>
                )}

                {result && !busy && (
                    <div className="bg-white rounded-xl border border-gray-200 p-5">
                        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                            <div className="flex items-center gap-2">
                                <span className="font-semibold text-gray-800">{result.title}</span>
                                <Tag color="blue">{result.kind === 'report' ? '分析报告' : '演示文稿'}</Tag>
                                <span className="text-xs text-gray-400">{(result.latency_ms / 1000).toFixed(1)}s</span>
                            </div>
                            <div className="flex gap-2">
                                {Object.entries(result.downloads).map(([fmt, url]) => (
                                    <Button key={fmt} size="small" icon={<DownloadOutlined />}
                                        onClick={() => download(url)}>
                                        下载 .{fmt}
                                    </Button>
                                ))}
                            </div>
                        </div>

                        {result.kind === 'report' && result.content && (
                            <div className="border-t border-gray-100 pt-3">
                                <MarkdownAnswer content={result.content} sourceCount={0} onCite={() => {}} />
                            </div>
                        )}

                        {result.kind === 'ppt' && result.outline && (
                            <div className="border-t border-gray-100 pt-3 space-y-3">
                                <div className="text-center py-6 bg-gradient-to-br from-blue-600 to-purple-600 rounded-lg text-white">
                                    <div className="text-xl font-bold">{result.outline.title}</div>
                                    {result.outline.subtitle && (
                                        <div className="text-sm opacity-80 mt-1">{result.outline.subtitle}</div>
                                    )}
                                </div>
                                {result.outline.slides.map((s, i) => (
                                    <div key={i} className="border border-gray-200 rounded-lg p-3">
                                        <div className="text-sm font-semibold text-gray-800 mb-1.5">
                                            <Tag className="mr-1.5">{i + 1}</Tag>{s.title}
                                        </div>
                                        <ul className="list-disc pl-9 text-sm text-gray-600 space-y-0.5">
                                            {s.bullets.map((b, j) => (
                                                <li key={j}>{typeof b === 'string' ? b : JSON.stringify(b)}</li>
                                            ))}
                                        </ul>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                )}

                {!result && !busy && (
                    <div className="bg-white rounded-xl border border-dashed border-gray-300 p-12 text-center text-gray-400">
                        填写分析主题后点击「生成」——系统会从项目图谱中检索与主题相关的实体、属性与关系，
                        撰写面向业务读者的主题分析报告或演示大纲
                    </div>
                )}
            </div>
        </div>
    );
}
