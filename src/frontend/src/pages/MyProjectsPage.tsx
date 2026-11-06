import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Empty, Form, Input, message, Modal, Radio, Popconfirm, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Upload, DatePicker } from 'antd';
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
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { Segmented } from 'antd';
import { useAuthStore } from '../shared/auth/authStore';
import { PROJECT_STATUS, PUBLISHED_TAG } from '../shared/projectStatus';
import Navbar from '../components/Layout/Navbar';
import TechHero, { TECH_COLORS, GraphMotif } from '../components/TechHero';
import { documentsApi } from '../api/documents';
import { pipelineApi } from '../api/pipeline';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import KnowledgeDomainSelector from '../components/KnowledgeDomainSelector';
import confirmImpact from '../utils/impactConfirm';

const TC = TECH_COLORS;

type SortField = 'created_at' | 'name' | 'node_count';
type SortOrder = 'asc' | 'desc';
type ViewMode = 'list' | 'cards';

/** 状态归一：后端 /my 直接下发 status；旧数据回退按 is_published 推导 */
const statusOf = (p: ProjectData): string =>
    p.status || (p.is_published ? 'published' : 'draft');

const STATUS_OPTIONS = [
    { value: 'draft', label: '草稿' },
    { value: 'building', label: '构建中' },
    { value: 'ready', label: '已构建' },
    { value: 'published', label: '已发布' },
];

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

const MyProjectsPage: React.FC = () => {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isEditModalOpen, setIsEditModalOpen] = useState(false);
    const [editingProject, setEditingProject] = useState<ProjectData | null>(null);
    const [form] = Form.useForm();
    const [editForm] = Form.useForm();
    const [selectedDomainId, setSelectedDomainId] = useState<number | undefined>(undefined);
    const [selectedDomainName, setSelectedDomainName] = useState<string | undefined>(undefined);
    const scrollPositionRef = useRef<number>(0);

    // 2026-11 列表视图改版：默认列表展示，可切卡片；按状态筛选
    const [viewMode, setViewMode] = useState<ViewMode>('list');
    const [statusFilter, setStatusFilter] = useState<string[]>([]);

    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [searchText, setSearchText] = useState('');
    const [sortField, setSortField] = useState<SortField>('created_at');
    const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

    // R11：超级管理员（username=admin）可查看/编辑全部用户的项目；普通管理员与普通用户只看自己相关的
    const me = useAuthStore((s) => s.user);
    const isSuperAdmin = me?.role === 'admin' && me?.username === 'admin';
    const [scope, setScope] = useState<'mine' | 'all'>('mine');

    useEffect(() => {
        loadProjects();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [scope]);

    const loadProjects = () => {
        setLoading(true);
        projectsApi.getMyProjects(isSuperAdmin ? scope : undefined)
            .then((data) => {
                setProjects(data);
                setLoadError(null);
            })
            .catch((error: any) => {
                const detail = error?.response?.data?.message || error?.response?.data?.detail || error?.message;
                setLoadError(String(detail || '未知错误'));
            })
            .finally(() => {
                setLoading(false);
            });
    };

    // 新建项目：名称/域 + 可选上传文档 + 构建时机（一级菜单，默认标准构建）——创建即后台构建
    const [pendingFiles, setPendingFiles] = useState<File[]>([]);
    const [creating, setCreating] = useState(false);

    const handleCreateProject = async (values: any) => {
        if (!selectedDomainId) {
            message.warning('请选择知识域');
            return;
        }
        setCreating(true);
        try {
            const newProject = await projectsApi.createProject({
                name: values.name,
                description: values.description,
                domain_id: selectedDomainId,
                domain_name: selectedDomainName,
            });
            const pid = newProject.id;
            let docNote = '';
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
            const r = await pipelineApi.run(pid, options);
            message.success(r.mode === 'scheduled'
                ? `项目已创建${docNote}；构建已定时，将在指定时间自动开始`
                : `项目已创建${docNote}；后台构建已启动，完成后在项目页确认结果即可`);
            setIsModalOpen(false);
            form.resetFields();
            setPendingFiles([]);
            setSelectedDomainId(undefined);
            setSelectedDomainName(undefined);

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
                    loadProjects();
                } catch (error) {
                    message.error('删除失败');
                }
            },
        });
    };

    const statusCounts = useMemo(() => {
        const c = { total: projects.length, published: 0, ready: 0, building: 0 };
        projects.forEach((p) => {
            const s = statusOf(p);
            if (s === 'published') c.published++;
            else if (s === 'ready') c.ready++;
            else if (s === 'building') c.building++;
        });
        return c;
    }, [projects]);

    const filteredAndSortedProjects = useMemo(() => {
        let result = [...projects];

        if (searchText.trim()) {
            const keyword = searchText.trim().toLowerCase();
            result = result.filter(p => p.name.toLowerCase().includes(keyword));
        }
        if (statusFilter.length > 0) {
            result = result.filter(p => statusFilter.includes(statusOf(p)));
        }

        result.sort((a, b) => {
            let cmp = 0;
            switch (sortField) {
                case 'name':
                    cmp = a.name.localeCompare(b.name, 'zh-CN');
                    break;
                case 'node_count':
                    cmp = (a.graph_data?.nodes?.length || 0) - (b.graph_data?.nodes?.length || 0);
                    break;
                case 'created_at':
                default:
                    cmp = new Date(a.created_at || 0).getTime() - new Date(b.created_at || 0).getTime();
                    break;
            }
            return sortOrder === 'asc' ? cmp : -cmp;
        });

        return result;
    }, [projects, searchText, statusFilter, sortField, sortOrder]);

    const sortOptions = [
        { label: '创建时间', value: 'created_at' },
        { label: '项目名称', value: 'name' },
        { label: '节点数量', value: 'node_count' },
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
                打开
            </Button>
            <Tooltip title="编辑项目名称与介绍">
                <Button type="link" size="small" icon={<EditOutlined />}
                        onClick={() => handleEditProject(project)}>
                    项目信息
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

    const statusTag = (project: ProjectData) => {
        const meta = PROJECT_STATUS[statusOf(project)] || PROJECT_STATUS.draft;
        return <Tag color={meta.color}>{meta.text}</Tag>;
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
            render: (_, project) => statusTag(project),
        },
        {
            title: '类 / 实例', key: 'stats', width: 120,
            render: (_, project) => (
                <span className="tk-mono text-xs text-gray-500">
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
        <div className="min-h-screen bg-gray-50">
            <style>{`
                .mp-cover { position: relative; overflow: hidden; }
                .mp-cover-grid {
                    background-image:
                        linear-gradient(rgba(91,141,239,0.12) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(91,141,239,0.12) 1px, transparent 1px);
                    background-size: 24px 24px;
                }
                .mp-card { border-radius: 14px; border: 1px solid #EEF1F8; transition: all .25s ease; }
                .mp-card:hover { box-shadow: 0 16px 34px -12px rgba(15,20,32,.22); transform: translateY(-3px); }
            `}</style>
            <Navbar
                breadcrumbs={breadcrumbs}
                showCreateButton
                onCreateProject={() => setIsModalOpen(true)}
            />

            <div className="p-4 sm:p-6">
                <TechHero
                    compact
                    chips={['PROJECT WORKSPACE', `共 ${projects.length} 个项目`]}
                    title={isSuperAdmin && scope === 'all' ? '全部项目' : '我的项目'}
                    subtitle={isSuperAdmin && scope === 'all'
                        ? '平台全部用户的项目，可查看与编辑'
                        : '创建与管理你的本体建模项目：AI 框架提取 · 骨架画布 · 实例探索 · 版本治理'}
                    showConstellation={projects.length > 0}
                    actions={isSuperAdmin ? (
                        <Segmented
                            value={scope}
                            onChange={(v) => setScope(v as 'mine' | 'all')}
                            options={[
                                { value: 'mine', label: '我的项目' },
                                { value: 'all', label: '全部项目' },
                            ]}
                        />
                    ) : undefined}
                />

                {/* 状态统计（2026-11 列表视图改版） */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                    <div className="bg-white border border-gray-100 rounded-xl px-4 py-3">
                        <Statistic title="项目总数" value={statusCounts.total} />
                    </div>
                    <div className="bg-white border border-gray-100 rounded-xl px-4 py-3">
                        <Statistic title="已发布" value={statusCounts.published}
                                   valueStyle={{ color: '#722ed1' }} />
                    </div>
                    <div className="bg-white border border-gray-100 rounded-xl px-4 py-3">
                        <Statistic title="已构建" value={statusCounts.ready}
                                   valueStyle={{ color: '#52c41a' }} />
                    </div>
                    <div className="bg-white border border-gray-100 rounded-xl px-4 py-3">
                        <Statistic title="处理中" value={statusCounts.building}
                                   valueStyle={{ color: '#5B8DEF' }} />
                    </div>
                </div>

                {projects.length > 0 && (
                    <div className="mb-4 flex flex-wrap items-center gap-3">
                        <Input
                            prefix={<SearchOutlined className="text-gray-500" />}
                            placeholder="搜索项目名称..."
                            value={searchText}
                            onChange={e => setSearchText(e.target.value)}
                            allowClear
                            style={{ width: 260 }}
                        />
                        <Select
                            mode="multiple" allowClear maxTagCount="responsive"
                            placeholder="全部状态" style={{ minWidth: 200 }}
                            value={statusFilter} onChange={setStatusFilter}
                            options={STATUS_OPTIONS}
                        />
                        <div className="flex items-center gap-2">
                            <span className="text-gray-500 text-sm">排序：</span>
                            <Select
                                value={sortField}
                                onChange={(v) => {
                                    if (v === sortField) {
                                        setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
                                    } else {
                                        setSortField(v);
                                        setSortOrder('desc');
                                    }
                                }}
                                options={sortOptions}
                                style={{ width: 130 }}
                                size="small"
                            />
                            <Button
                                size="small"
                                icon={<SortAscendingOutlined style={{ transform: sortOrder === 'desc' ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />}
                                onClick={() => setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')}
                                title={sortOrder === 'asc' ? '升序' : '降序'}
                            />
                        </div>
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
                    </div>
                )}

                {loading ? (
                    <div className="flex justify-center items-center h-96">
                        <Spin size="large" />
                    </div>
                ) : loadError ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description={`项目加载失败：${loadError}`}
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button type="primary" icon={<ReloadOutlined />} onClick={loadProjects}>
                                重试
                            </Button>
                        </Empty>
                    </div>
                ) : projects.length === 0 ? (
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
                ) : filteredAndSortedProjects.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description="没有找到匹配的项目"
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button onClick={() => { setSearchText(''); setStatusFilter([]); }}>清除筛选</Button>
                        </Empty>
                    </div>
                ) : viewMode === 'list' ? (
                    <div className="bg-white border border-gray-100 rounded-xl overflow-hidden">
                        <Table
                            rowKey="id" size="middle"
                            columns={listColumns}
                            dataSource={filteredAndSortedProjects}
                            pagination={{ pageSize: 10, showSizeChanger: false }}
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
                        {selectedIds.size > 0 && (
                            <div className="mb-3 flex items-center gap-3">
                                <span className="text-sm text-blue-600 font-medium">已选 {selectedIds.size} 项</span>
                                <Button
                                    danger
                                    size="small"
                                    icon={<DeleteOutlined />}
                                    onClick={async () => {
                                        for (const id of selectedIds) await projectsApi.deleteProject(id).catch(() => null);
                                        setSelectedIds(new Set());
                                        loadProjects();
                                    }}
                                >
                                    批量删除
                                </Button>
                                <Button size="small" onClick={() => setSelectedIds(new Set())}>取消选择</Button>
                            </div>
                        )}
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                            {filteredAndSortedProjects.map((project) => {
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
                                                {statusTag(project)}
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
                    </>
                )}
            </div>

            {/* 创建项目弹窗（2026-11 改版：文档可选；构建时机一级；标准构建；定时须晚于当前时间） */}
            <Modal
                title="创建新项目"
                open={isModalOpen}
                onCancel={() => {
                    setIsModalOpen(false);
                    form.resetFields();
                }}
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

                    <Form.Item
                        label="知识域"
                        rules={[{ required: true, message: '请选择知识域' }]}
                        tooltip="选择或创建本项目所属的知识领域"
                    >
                        <KnowledgeDomainSelector
                            value={selectedDomainId}
                            onChange={setSelectedDomainId}
                            domainName={selectedDomainName}
                            onDomainNameChange={setSelectedDomainName}
                            placeholder="选择知识域"
                        />
                    </Form.Item>

                    <Form.Item label="上传文档（可选，可稍后补充）">
                        <Upload.Dragger
                            multiple
                            showUploadList={{ showRemoveIcon: true }}
                            beforeUpload={() => false}
                            onChange={({ fileList }) => {
                                setPendingFiles(fileList.map((f) => f.originFileObj).filter(Boolean) as File[]);
                            }}
                            accept=".txt,.md,.pdf,.doc,.docx,.xls,.xlsx,.csv,.ppt,.pptx,.html,.json"
                        >
                            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                            <p className="ant-upload-hint">
                                上传后系统自动完成：解析 → AI 本体构建（含质量自检修复）→ 完成后你来确认结果
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
                            <Button onClick={() => setIsModalOpen(false)}>取消</Button>
                            <Button type="primary" htmlType="submit" loading={creating}
                                    icon={<ThunderboltOutlined />}>
                                {pendingFiles.length > 0 ? '创建并开始构建' : '创建'}
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
