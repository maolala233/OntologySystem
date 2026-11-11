import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message } from '../shared/feedback';
import { Badge, Button, Checkbox, Empty, Form, Input, Modal, Pagination, Radio, Popconfirm, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Upload, DatePicker } from 'antd';
import {
     PlusOutlined,
    EditOutlined,
    DeleteOutlined,
    EyeOutlined,
    ThunderboltOutlined,
    InboxOutlined,
    AppstoreOutlined,
    BarsOutlined,
    ReloadOutlined,
    SearchOutlined,
    SortAscendingOutlined,
 InfoCircleOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile } from 'antd/es/upload/interface';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { Segmented } from 'antd';
import { useAuthStore } from '../shared/auth/authStore';
import { PROJECT_STATUS, PUBLISHED_TAG, STALE_PUBLISHED_TAG } from '../shared/projectStatus';
import { UPLOAD_ACCEPT, UPLOAD_EXTS, MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from '../features/builder/documents/uploadFormats';
import Navbar from '../components/Layout/Navbar';
import TechHero, { TECH_COLORS, GraphMotif } from '../components/TechHero';
import { documentsApi } from '../api/documents';
import { pipelineApi } from '../api/pipeline';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import confirmImpact from '../utils/impactConfirm';

const TC = TECH_COLORS;

type SortField = 'created_at' | 'updated_at' | 'name';
type SortOrder = 'asc' | 'desc';
type ViewMode = 'list' | 'cards';
type StatusKey = 'draft' | 'scheduled' | 'building' | 'ready' | 'published';

/** 列表每页条数（列表/卡片共用，切视图不重发请求） */
const PAGE_SIZE = 10;
const EMPTY_COUNTS: Record<StatusKey, number> & { total: number } = {
    total: 0, draft: 0, scheduled: 0, building: 0, ready: 0, published: 0,
};

/** 状态归一：后端 /my 直接下发 status；旧数据回退按 is_published 推导 */
const statusOf = (p: ProjectData): string =>
    p.status || (p.is_published ? 'published' : 'draft');

// 状态说明（与 projectStatus.PROJECT_STATUS.desc 同源文案）
// 状态说明（与 projectStatus.PROJECT_STATUS.desc 同源文案）
const STATUS_OPTIONS = [
    { value: 'draft', label: '草稿（已创建未构建）' },
    { value: 'scheduled', label: '待构建（定时构建已登记）' },
    { value: 'building', label: '构建中（大模型后台构建）' },
    { value: 'ready', label: '已构建（完成未发布）' },
    { value: 'published', label: '已发布（已入资产中心）' },
];

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

const MyProjectsPage: React.FC = () => {
    const navigate = useNavigate();
    // 服务端分页：projects 只保存当前页，搜索/筛选/排序/翻页全部交给后端
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [total, setTotal] = useState(0);
    // 各状态数量（统计卡）：接口只回过滤后的 total，所以按状态各发一次 page_size=1 的 count
    const [counts, setCounts] = useState<Record<StatusKey, number> & { total: number }>(EMPTY_COUNTS);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isEditModalOpen, setIsEditModalOpen] = useState(false);
    const [editingProject, setEditingProject] = useState<ProjectData | null>(null);
    const [form] = Form.useForm();
    const [editForm] = Form.useForm();
    const scrollPositionRef = useRef<number>(0);

    // 2026-11 列表视图改版：默认列表展示，可切卡片；按状态筛选
    const [viewMode, setViewMode] = useState<ViewMode>('list');

    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [page, setPage] = useState(1);
    // 输入框即时值 + 防抖 300ms 后生效的查询词
    const [searchText, setSearchText] = useState('');
    const [keyword, setKeyword] = useState('');
    const [statusFilter, setStatusFilter] = useState<string[]>([]);
    const [sortField, setSortField] = useState<SortField>('created_at');
    const [sortOrder, setSortOrder] = useState<SortOrder>('desc');
    // 删除等场景：查询条件没变，用它触发列表重载
    const [reloadTick, setReloadTick] = useState(0);
    // 请求代际：连点翻页/边打字边搜索时，只有最新一次响应可写状态（防旧响应覆盖）
    const fetchGenRef = useRef(0);

    // R11：超级管理员（username=admin）可查看/编辑全部用户的项目；普通管理员与普通用户只看自己相关的
    const me = useAuthStore((s) => s.user);
    const isSuperAdmin = me?.role === 'admin' && me?.username === 'admin';
    const [scope, setScope] = useState<'mine' | 'all'>('mine');
    const apiScope = isSuperAdmin ? scope : undefined;

    // 搜索防抖：停止输入 300ms 后才发请求，并回到第一页
    useEffect(() => {
        const t = setTimeout(() => {
            setKeyword(searchText.trim());
            setPage(1);
        }, 300);
        return () => clearTimeout(t);
    }, [searchText]);

    // 列表加载：任一查询条件变化都从后端重新取当前页
    useEffect(() => {
        const gen = ++fetchGenRef.current;
        setLoading(true);
        projectsApi.getMyProjectsPaged({
            scope: apiScope,
            page,
            page_size: PAGE_SIZE,
            keyword: keyword || undefined,
            status: statusFilter.length ? statusFilter : undefined,
            sort_by: sortField,
            order: sortOrder,
        })
            .then((r) => {
                if (gen !== fetchGenRef.current) return;   // 过期响应（如连点翻页）丢弃
                setProjects(r.items);
                setTotal(r.total);
                setLoadError(null);
                // 删掉末页最后一条后页码越界：回退到最后一页
                if (r.items.length === 0 && r.total > 0 && page > 1) {
                    setPage(Math.max(1, Math.ceil(r.total / PAGE_SIZE)));
                }
            })
            .catch((error: any) => {
                if (gen !== fetchGenRef.current) return;
                const detail = error?.response?.data?.message || error?.response?.data?.detail || error?.message;
                setLoadError(String(detail || '未知错误'));
            })
            .finally(() => {
                if (gen === fetchGenRef.current) setLoading(false);
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [apiScope, page, keyword, statusFilter, sortField, sortOrder, reloadTick]);

    // 状态统计：总数 + 每状态各一次 count（只取 total，不取数据行）
    const loadCounts = useCallback(async () => {
        const base = { scope: apiScope, page: 1, page_size: 1 };
        try {
            const [all, draft, scheduled, building, ready, published] = await Promise.all([
                projectsApi.getMyProjectsPaged(base),
                projectsApi.getMyProjectsPaged({ ...base, status: ['draft'] }),
                projectsApi.getMyProjectsPaged({ ...base, status: ['scheduled'] }),
                projectsApi.getMyProjectsPaged({ ...base, status: ['building'] }),
                projectsApi.getMyProjectsPaged({ ...base, status: ['ready'] }),
                projectsApi.getMyProjectsPaged({ ...base, status: ['published'] }),
            ]);
            setCounts({
                total: all.total, draft: draft.total, scheduled: scheduled.total,
                building: building.total, ready: ready.total, published: published.total,
            });
        } catch {
            // 统计失败不阻塞列表，保留上一次数值
        }
    }, [apiScope]);

    useEffect(() => { void loadCounts(); }, [loadCounts]);

    // 删除/新建后：列表和统计一起刷新
    const refreshAll = () => {
        setReloadTick((t) => t + 1);
        void loadCounts();
    };

    // 新建项目：名称/域 + 可选上传文档 + 构建时机（一级菜单，默认标准构建）——创建即后台构建
    const [pendingFiles, setPendingFiles] = useState<File[]>([]);
    // Upload 受控文件列表：与 pendingFiles 同步，关闭弹窗时可一并清空（否则组件内部状态残留）
    const [uploadList, setUploadList] = useState<UploadFile[]>([]);
    const [creating, setCreating] = useState(false);

    // 关闭创建弹窗：清空全部输入（名称/描述/构建方式/已选文件），下次打开从空白开始
    const closeCreateModal = () => {
        setIsModalOpen(false);
        form.resetFields();
        setUploadList([]);
        setPendingFiles([]);
    };

    const handleCreateProject = async (values: any) => {
        setCreating(true);
        try {
            // 知识域 2026-11-08 起不在创建时选择：发布时才选（publishWithDomain
            // 弹选择器带域发布）；新建项目 domain 为空即可
            const newProject = await projectsApi.createProject({
                name: values.name,
                description: values.description,
            });
            const pid = newProject.id;
            let docNote = '';
            let buildNote = '';
            if (pendingFiles.length > 0) {
                // auto_parse=false：解析交给一键管线串行处理（避免解析与管线赛跑）
                await documentsApi.upload(pid, pendingFiles, false);
                docNote = `，已上传 ${pendingFiles.length} 个文档`;
            }
            const options: Record<string, any> = {
                depth: 'standard', // 标准构建（构建深度设置已收起，统一走标准）
            };
            if (values.build_mode === 'scheduled' && values.schedule_at) {
                options.schedule_at = values.schedule_at.toISOString();
            }
            if (pendingFiles.length > 0) {
                // 0 文档不发起构建：空管线会白跑一次 AI 调用（公理扫描等），
                // 还让新项目直接显示「构建中」+全绿勾，与"还没构建"的事实相悖
                const r = await pipelineApi.run(pid, options);
                message.success(r.mode === 'scheduled'
                    ? `项目已创建${docNote}；构建已定时，将在指定时间自动开始`
                    : `项目已创建${docNote}；后台构建已启动，完成后在项目页确认结果即可`);
            } else {
                buildNote = values.build_mode === 'scheduled'
                    ? '；定时构建已跳过（无文档），上传文档后请手动发起'
                    : '；上传文档后点「一键构建」即可开始';
                message.success(`项目已创建${buildNote}`);
            }
            closeCreateModal();

            navigate(`/projects/${pid}`);
        } catch (error: any) {
            const msg = error.response?.data?.error?.message || '创建失败，请稍后重试';
            message.error(msg);
        } finally {
            setCreating(false);
        }
    };

    const handleEditProject = (project: ProjectData) => {
        setEditingProject(project);
        editForm.setFieldsValue({
            name: project.name,
            description: project.description,
        });
        setIsEditModalOpen(true);
    };

    const handleUpdateProject = async (values: any) => {
        if (!editingProject) return;
        scrollPositionRef.current = window.scrollY;
        try {
            await projectsApi.updateProject(editingProject.id, {
                name: values.name,
                description: values.description,
            });
            message.success('项目信息更新成功！');
            setIsEditModalOpen(false);
            setEditingProject(null);
            setProjects((prevProjects) =>
                prevProjects.map((project) =>
                    project.id === editingProject.id
                        ? { ...project, name: values.name, description: values.description }
                        : project
                )
            );
            setTimeout(() => {
                window.scrollTo(0, scrollPositionRef.current);
            }, 50);
        } catch (error: any) {
            message.error('更新失败，请稍后重试');
        }
    };

    const handleDeleteProject = (projectId: number) => {
        confirmImpact({
            title: '确认删除项目',
            impact: [
                '项目内已上传的全部文档将一并删除，且无法恢复；',
                'AI 抽取出的本体框架与实例图谱数据将一并删除；',
                '待处理与历史审核项、评测记录、版本快照将一并删除；',
                '若已发布到资产中心，公共区的展示也会随之消失。',
            ],
            okText: '确认删除',
            onOk: async () => {
                try {
                    await projectsApi.deleteProject(projectId);
                    message.success('删除成功');
                    setSelectedIds(prev => {
                        const next = new Set(prev);
                        next.delete(projectId);
                        return next;
                    });
                    refreshAll();
                } catch (error) {
                    message.error('删除失败');
                }
            },
        });
    };

    // 批量删除：逐条调用删除接口，统计成功/失败并给出结果提示（不可恢复，先确认影响范围）
    const handleBatchDelete = () => {
        const ids = [...selectedIds];
        if (ids.length === 0) return;
        confirmImpact({
            title: `确认删除选中的 ${ids.length} 个项目`,
            impact: [
                '每个项目内的全部文档、AI 抽取的本体框架与实例图谱将一并删除，且无法恢复；',
                '待处理与历史审核项、评测记录、版本快照将一并删除；',
                '已发布到资产中心的项目，公共区的展示也会随之消失。',
            ],
            okText: '确认删除',
            onOk: async () => {
                try {
                    // 走后端批量接口：单事务逐项目提交，逐项返回结果（不再前端循环 N 次）
                    const r = await projectsApi.batchDeleteProjects(ids);
                    const failedNames = (r.failed || []).map(
                        (f) => projects.find(p => p.id === f.project_id)?.name || `#${f.project_id}`,
                    );
                    // 失败的保留选中，方便直接重试
                    setSelectedIds(new Set((r.failed || []).map(f => f.project_id)));
                    refreshAll();
                    if (failedNames.length === 0) message.success(`已删除 ${r.deleted.length} 个项目`);
                    else if (r.deleted.length === 0) message.error(`删除失败：${failedNames.join('、')}`);
                    else message.warning(`成功删除 ${r.deleted.length} 个，失败 ${failedNames.length} 个：${failedNames.join('、')}`);
                } catch (e: any) {
                    message.error(e?.response?.data?.error?.message || e?.response?.data?.detail || '批量删除失败');
                }
            },
        });
    };

    const sortOptions = [
        { label: '创建时间', value: 'created_at' },
        { label: '更新时间', value: 'updated_at' },
        { label: '项目名称', value: 'name' },
    ];

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目' },
    ];

    const actionsRenderer = (project: ProjectData) => (
        // 阻断冒泡：操作列点击不得触发 onRow 的整行跳转（否则编辑弹窗被路由切换冲掉）
        <div onClick={(e) => e.stopPropagation()}>
        <Space size={0}>
            <Button type="link" size="small" icon={<EyeOutlined />}
                    onClick={() => navigate(`/projects/${project.id}`)}>
                详情
            </Button>
            <Tooltip title="编辑项目名称与介绍">
                <Button type="link" size="small" icon={<EditOutlined />}
                        onClick={() => handleEditProject(project)}>
                    编辑
                </Button>
            </Tooltip>
            <Popconfirm
                title="确认删除项目？"
                description="文档、图谱数据、审核项与版本快照将一并删除且无法恢复"
                okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
                onConfirm={() => handleDeleteProject(project.id)}>
                <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                    删除
                </Button>
            </Popconfirm>
        </Space>
        </div>
    );

    const statusTag = (project: ProjectData, stale?: boolean) => {
        const meta = PROJECT_STATUS[statusOf(project)] || PROJECT_STATUS.draft;
        // 「有新版本」用角标圆点表达（双 Tag 并排会换行挤占 类/实例 列）
        const tag = <Tag color={meta.color} style={{ marginInlineEnd: 0 }}>{meta.text}</Tag>;
        return (
            <span className="inline-flex items-center">
                <Tooltip title={meta.desc} className="cursor-help">
                    {stale ? (
                        <Badge dot color={STALE_PUBLISHED_TAG.color}
                               title="重新构建过：资产中心还是旧版本，可重新发布">
                            {tag}
                        </Badge>
                    ) : tag}
                </Tooltip>
            </span>
        );
    };

    const listColumns: ColumnsType<ProjectData> = [
        {
            title: '项目名称', dataIndex: 'name', key: 'name', width: 220,
            render: (_, project) => (
                <div className="flex items-center gap-2">
                    <span className="font-medium text-gray-800">{project.name}</span>
                    {isSuperAdmin && scope === 'all' && (
                        <Tag className="ml-1" color="blue">{project.owner?.username || `用户#${project.owner_id ?? '?'}`}</Tag>
                    )}
                </div>
            ),
        },
        {
            title: '项目简介', dataIndex: 'description', key: 'description', ellipsis: true,
            render: (v: string) => <span className="text-gray-500">{v || '暂无描述'}</span>,
        },
        {
            title: '状态', key: 'status', width: 100, filters: STATUS_OPTIONS.map(s => ({ text: s.label, value: s.value })),
            onFilter: (v, p) => statusOf(p) === v,
            render: (_, project) => statusTag(project, project.stale_published),
        },
        {
            title: '类 / 实例', key: 'stats', width: 130,
            render: (_, project) => (
                <span className="tk-mono text-xs text-gray-500 whitespace-nowrap">
                    <span style={{ color: TC.blue }}>{project.classes_total ?? project.graph_data?.nodes?.length ?? 0}</span> 类
                    {' · '}
                    <span style={{ color: TC.purple }}>{project.instances_total ?? 0}</span> 实例
                </span>
            ),
        },
        { title: '创建日期', dataIndex: 'created_at', key: 'created_at', width: 150,
          render: (v: string) => <span className="text-gray-500 text-xs">{fmtDate(v)}</span> },
        { title: '修改日期', dataIndex: 'updated_at', key: 'updated_at', width: 150,
          render: (v: string) => <span className="text-gray-500 text-xs">{fmtDate(v)}</span> },
        { title: '操作', key: 'actions', width: 240, render: (_, project) => actionsRenderer(project) },
    ];

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                .mp-cover { position: relative; overflow: hidden; }
                .mp-cover-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.12) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.12) 1px, transparent 1px);
                    background-size: 24px 24px;
                }
                .mp-card { border-radius: 14px; border: 1px solid #EEF1F8; transition: all .25s ease; box-shadow: 0 1px 2px rgba(15,23,42,.05), 0 12px 30px -16px rgba(15,23,42,.20); }
                .mp-card:hover { box-shadow: 0 16px 34px -12px rgba(15,20,32,.22); transform: translateY(-3px); }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    chips={['PROJECT WORKSPACE', `共 ${counts.total} 个项目`]}
                    title={isSuperAdmin && scope === 'all' ? '全部项目' : '我的项目'}
                    subtitle={isSuperAdmin && scope === 'all'
                        ? '平台全部用户的项目，可查看与编辑'
                        : '创建与管理你的本体建模项目：AI 框架提取 · 骨架画布 · 实例探索 · 版本治理'}
                    showConstellation={counts.total > 0}
                    actions={isSuperAdmin ? (
                        <Segmented
                            value={scope}
                            onChange={(v) => {
                                // 切换范围：回到第一页，清掉跨范围失效的勾选
                                setScope(v as 'mine' | 'all');
                                setPage(1);
                                setSelectedIds(new Set());
                            }}
                            options={[
                                { value: 'mine', label: '我的项目' },
                                { value: 'all', label: '全部项目' },
                            ]}
                        />
                    ) : undefined}
                />

                {/* 状态统计（2026-11 列表视图改版；说明内嵌卡片 2026-11-09） */}
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
                    <div className="bg-white border border-gray-100 rounded-xl shadow-sm px-4 py-3">
                        <Statistic title="项目总数" value={counts.total} />
                    </div>
                    {(Object.keys(PROJECT_STATUS) as Array<keyof typeof PROJECT_STATUS>).map((k) => {
                        const meta = PROJECT_STATUS[k];
                        const countKey = k as 'draft' | 'published' | 'ready' | 'building' | 'scheduled';
                        const colorOf: Record<string, string> = {
                            draft: '#8c8c8c', published: '#722ed1', ready: '#52c41a',
                            building: '#5B8DEF', scheduled: '#faad14',
                        };
                        return (
                            <div key={k} className="bg-white border border-gray-100 rounded-xl px-4 py-3">
                                <Tooltip title={meta.desc} className="cursor-help">
                                    <div>
                                        <Statistic
                                            title={<span className="cursor-help">{meta.text}
                                                <InfoCircleOutlined className="ml-1 text-gray-300 text-xs" /></span>}
                                            value={counts[countKey as StatusKey]}
                                            valueStyle={{ color: colorOf[k] }} />
                                    </div>
                                </Tooltip>
                            </div>
                        );
                    })}
                </div>

                {/* 操作栏：搜索/筛选靠左，视图切换与「新建项目」靠右（新建按钮 2026-11-11 从顶栏迁入） */}
                {(counts.total > 0 || !!loadError) && (
                <div className="mb-4 flex flex-wrap items-center gap-2">
                    {counts.total > 0 && (<>
                        <Input
                            prefix={<SearchOutlined className="text-gray-500" />}
                            placeholder="搜索项目名称/描述..."
                            value={searchText}
                            onChange={e => setSearchText(e.target.value)}
                            allowClear
                            style={{ width: 240 }}
                        />
                        <Select
                            mode="multiple" allowClear maxTagCount="responsive"
                            placeholder="全部状态" style={{ minWidth: 180 }}
                            value={statusFilter}
                            onChange={(v) => { setStatusFilter(v); setPage(1); }}
                            options={STATUS_OPTIONS}
                        />
                        <div className="flex items-center gap-1">
                            <span className="text-gray-500 text-sm pl-1">排序</span>
                            <Select
                                value={sortField}
                                onChange={(v) => {
                                    if (v === sortField) {
                                        setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
                                    } else {
                                        setSortField(v);
                                        setSortOrder('desc');
                                    }
                                    setPage(1);
                                }}
                                options={sortOptions}
                                style={{ width: 130 }}
                                size="small"
                            />
                            <Button
                                size="small"
                                icon={<SortAscendingOutlined style={{ transform: sortOrder === 'desc' ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />}
                                onClick={() => { setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc'); setPage(1); }}
                                title={sortOrder === 'asc' ? '升序' : '降序'}
                            />
                        </div>
                        {selectedIds.size > 0 && (
                            <div className="flex items-center gap-1 pl-2 border-l border-gray-200">
                                <span className="text-sm text-gray-500">
                                    已选 <span className="text-blue-600 font-medium">{selectedIds.size}</span> 项
                                </span>
                                <Button type="link" size="small" danger icon={<DeleteOutlined />}
                                        className="!px-1" onClick={handleBatchDelete}>
                                    批量删除
                                </Button>
                                <Button type="link" size="small" className="!px-1"
                                        onClick={() => setSelectedIds(new Set())}>
                                    取消选择
                                </Button>
                            </div>
                        )}
                        <div className="ml-auto">
                            <Segmented
                                value={viewMode}
                                onChange={(v) => setViewMode(v as ViewMode)}
                                options={[
                                    { value: 'list', icon: <BarsOutlined />, label: '列表' },
                                    { value: 'cards', icon: <AppstoreOutlined />, label: '卡片' },
                                ]}
                            />
                        </div>
                    </>)}
                    <Button
                        type={counts.total > 0 ? 'primary' : 'default'}
                        icon={<PlusOutlined />}
                        onClick={() => setIsModalOpen(true)}
                        className={counts.total > 0 ? '' : 'ml-auto'}
                    >
                        新建项目
                    </Button>
                </div>
                )}

                {loading && projects.length === 0 ? (
                    <div className="flex justify-center items-center h-96">
                        <Spin size="large" />
                    </div>
                ) : loadError ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description={`项目加载失败：${loadError}`}
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button type="primary" icon={<ReloadOutlined />} onClick={refreshAll}>
                                重试
                            </Button>
                        </Empty>
                    </div>
                ) : counts.total === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description="还没有项目，创建第一个项目开始吧！"
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button
                                type="primary"
                                icon={<PlusOutlined />}
                                onClick={() => setIsModalOpen(true)}
                            >
                                创建项目
                            </Button>
                        </Empty>
                    </div>
                ) : total === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description="没有找到匹配的项目"
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button onClick={() => {
                                setSearchText(''); setStatusFilter([]); setPage(1);
                            }}>
                                清除筛选
                            </Button>
                        </Empty>
                    </div>
                ) : viewMode === 'list' ? (
                    <div className="bg-white border border-gray-100 rounded-xl shadow-sm overflow-hidden">
                        <Table
                            rowKey="id" size="middle"
                            loading={loading}
                            columns={listColumns}
                            dataSource={projects}
                            pagination={{
                                current: page,
                                pageSize: PAGE_SIZE,
                                total,
                                showSizeChanger: false,
                                showTotal: (t) => `共 ${t} 个项目`,
                                onChange: (p) => setPage(p),
                            }}
                            rowSelection={{
                                selectedRowKeys: [...selectedIds],
                                onChange: (keys) => setSelectedIds(new Set(keys as number[])),
                            }}
                            onRow={(record) => ({
                                onClick: () => navigate(`/projects/${record.id}`),
                                style: { cursor: 'pointer' },
                            })}
                        />
                    </div>
                ) : (
                    <>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                            {projects.map((project) => {
                                const isSelected = selectedIds.has(project.id);
                                return (
                                    <div key={project.id} className="relative">
                                        <button
                                            type="button"
                                            aria-pressed={isSelected}
                                            aria-label={`选择项目 ${project.name}`}
                                            className={`absolute top-3 left-3 z-10 w-6 h-6 flex items-center justify-center rounded-full transition-all duration-200 cursor-pointer ${isSelected ? 'bg-blue-500' : 'bg-white/70 hover:bg-white shadow-sm'}`}
                                            onClick={(e) => { e.stopPropagation(); setSelectedIds(prev => {
                                                const next = new Set(prev);
                                                if (next.has(project.id)) next.delete(project.id); else next.add(project.id);
                                                return next;
                                            }); }}
                                        >
                                            <Checkbox checked={isSelected} className="[&_.ant-checkbox-inner]:border-gray-400 pointer-events-none" tabIndex={-1} aria-hidden />
                                        </button>
                                        <div
                                            className="mp-cover h-32 sm:h-40 rounded-t-xl"
                                            style={{ background: TC.bgDark }}
                                            onClick={() => navigate(`/projects/${project.id}`)}
                                        >
                                            <div className="absolute inset-0 mp-cover-grid rounded-t-xl" />
                                            <div
                                                className="absolute -top-8 -right-8 w-36 h-36 rounded-full"
                                                style={{ background: TC.purple, filter: 'blur(60px)', opacity: 0.3 }}
                                            />
                                            <div
                                                className="absolute -bottom-10 -left-6 w-36 h-36 rounded-full"
                                                style={{ background: TC.blue, filter: 'blur(60px)', opacity: 0.3 }}
                                            />
                                            <GraphMotif seed={project.id} className="absolute inset-0 w-full h-full opacity-80" />
                                            <div
                                                className="absolute bottom-3 right-3 tk-mono text-[10px] tracking-widest"
                                                style={{ color: TC.textSub }}
                                            >
                                                {statusOf(project).toUpperCase()}
                                            </div>
                                        </div>
                                        <div className="mp-card bg-white rounded-b-xl border-t-0 p-4" style={{ borderRadius: '0 0 14px 14px' }}>
                                            <div className="flex items-center justify-between mb-1">
                                                <span
                                                    className="truncate font-medium text-gray-800 cursor-pointer hover:text-blue-500 transition-colors"
                                                    onClick={() => navigate(`/projects/${project.id}`)}
                                                >
                                                    {project.name}
                                                </span>
                                                {statusTag(project, project.stale_published)}
                                            </div>
                                            <div className="text-gray-500 text-sm truncate mb-2">
                                                {project.description || '暂无描述'}
                                            </div>
                                            <div className="flex items-center justify-between text-xs text-gray-400 mb-2">
                                                <span>创建 {fmtDate(project.created_at)}</span>
                                                <span>修改 {fmtDate(project.updated_at)}</span>
                                            </div>
                                            {actionsRenderer(project)}
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                        {/* 卡片视图没有表格自带分页器，单独补一个（与列表共用同一页数据） */}
                        {total > PAGE_SIZE && (
                            <div className="flex justify-end mt-4">
                                <Pagination
                                    current={page}
                                    pageSize={PAGE_SIZE}
                                    total={total}
                                    showSizeChanger={false}
                                    showTotal={(t) => `共 ${t} 个项目`}
                                    onChange={(p) => setPage(p)}
                                />
                            </div>
                        )}
                    </>
                )}
            </div>

            {/* 创建项目弹窗（2026-11 改版：文档可选；构建时机一级；标准构建；定时须晚于当前时间） */}
            <Modal
                title="创建新项目"
                open={isModalOpen}
                onCancel={closeCreateModal}
                footer={null}
                width={500}
            >
                <Form form={form} layout="vertical" onFinish={handleCreateProject}>
                    <Form.Item
                        name="name"
                        label="项目名称"
                        rules={[{ required: true, message: '请输入项目名称' }]}
                    >
                        <Input placeholder="例如：工业本体" />
                    </Form.Item>

                    <Form.Item name="description" label="项目描述">
                        <Input.TextArea
                            rows={4}
                            placeholder="简要描述这个项目的用途..."
                        />
                    </Form.Item>


                    <Form.Item label="上传文档（可选，可稍后补充）">
                        <Upload.Dragger
                            multiple
                            showUploadList={{ showRemoveIcon: true }}
                            beforeUpload={(f) => {
                                const ext = String(f.name).split('.').pop()?.toLowerCase() || '';
                                if (!UPLOAD_EXTS.includes(ext)) {
                                    message.warning(`不支持 .${ext} 格式：仅支持 ${UPLOAD_EXTS.join(' / ')}`);
                                    return Upload.LIST_IGNORE;
                                }
                                if (f.size > MAX_UPLOAD_BYTES) {
                                    message.warning(`「${f.name}」超过 ${MAX_UPLOAD_LABEL} 上限`);
                                    return Upload.LIST_IGNORE;
                                }
                                return false;  // 阻止自动上传（随表单一起提交）
                            }}
                            fileList={uploadList}
                            onChange={({ fileList }) => {
                                setUploadList(fileList);
                                setPendingFiles(fileList.map((f) => f.originFileObj).filter(Boolean) as File[]);
                            }}
                            accept={UPLOAD_ACCEPT}
                        >
                            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                            <p className="ant-upload-hint">
                                支持 {UPLOAD_EXTS.join(' / ')}，单文件 ≤{MAX_UPLOAD_LABEL}，可多选
                            </p>
                        </Upload.Dragger>
                    </Form.Item>

                    <Form.Item name="build_mode" label="构建时间" initialValue="now">
                        <Radio.Group>
                            <Radio value="now">立即开始构建</Radio>
                            <Radio value="scheduled">定时开始构建</Radio>
                        </Radio.Group>
                    </Form.Item>
                    <Form.Item noStyle shouldUpdate={(a, b) => a.build_mode !== b.build_mode}>
                        {({ getFieldValue }) => getFieldValue('build_mode') === 'scheduled' ? (
                            <Form.Item name="schedule_at" label="定时开始时间"
                                       rules={[
                                           { required: true, message: '请选择定时开始时间' },
                                           {
                                               validator: (_, v) => {
                                                   if (!v || dayjs(v).isAfter(dayjs())) return Promise.resolve();
                                                   // eslint-disable-next-line prefer-promise-reject-errors
                                                   return Promise.reject('定时时间早于当前时间，请重新选择');
                                               },
                                           },
                                       ]}>
                                <DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: '100%' }}
                                            disabledDate={(d) => d.isBefore(dayjs().startOf('day'))} />
                            </Form.Item>
                        ) : null}
                    </Form.Item>

                    <Form.Item>
                        <Space className="w-full justify-end">
                            <Button onClick={closeCreateModal}>取消</Button>
                            <Button type="primary" htmlType="submit" loading={creating}
                                    icon={<ThunderboltOutlined />}>
                                {pendingFiles.length > 0 ? '创建项目并开始构建' : '创建项目'}
                            </Button>
                        </Space>
                    </Form.Item>
                </Form>
            </Modal>

            {/* 编辑项目信息弹窗 */}
            <Modal
                title="编辑项目信息（名称与介绍）"
                open={isEditModalOpen}
                onCancel={() => {
                    setIsEditModalOpen(false);
                    setEditingProject(null);
                    editForm.resetFields();
                }}
                footer={null}
                width={500}
            >
                <Form form={editForm} layout="vertical" onFinish={handleUpdateProject}>
                    <Form.Item
                        name="name"
                        label="项目名称"
                        rules={[{ required: true, message: '请输入项目名称' }]}
                    >
                        <Input placeholder="例如：工业本体" />
                    </Form.Item>

                    <Form.Item name="description" label="项目介绍">
                        <Input.TextArea
                            rows={4}
                            placeholder="简要描述这个项目的用途..."
                        />
                    </Form.Item>

                    <Form.Item>
                        <Space className="w-full justify-end">
                            <Button onClick={() => {
                                setIsEditModalOpen(false);
                                setEditingProject(null);
                                editForm.resetFields();
                            }}>取消</Button>
                            <Button type="primary" htmlType="submit">
                                保存
                            </Button>
                        </Space>
                    </Form.Item>
                </Form>
            </Modal>
        </div>
    );
};

export default MyProjectsPage;
