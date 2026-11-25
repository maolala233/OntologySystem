import React, { useCallback, useEffect, useRef, useState } from 'react';
import { message, notifyError } from '../shared/feedback';
import { Badge, Button, Checkbox, Empty, Form, Input, Modal, Pagination, Radio, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Upload, DatePicker } from 'antd';
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
    ClockCircleOutlined,
    SyncOutlined,
    CheckCircleOutlined,
    RocketOutlined,
    FolderOpenOutlined,
    FileTextOutlined,
    ApartmentOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import type { UploadFile } from 'antd/es/upload/interface';
import { useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import { Segmented } from 'antd';
import { useAuthStore } from '../shared/auth/authStore';
import { PROJECT_STATUS, STALE_PUBLISHED_TAG, projectStatusOf as statusOf, statusChip } from '../shared/projectStatus';
import { fmtSchedule, fmtScheduleFull } from '../shared/schedule';
import { UPLOAD_ACCEPT, UPLOAD_EXTS, UPLOAD_TYPE_LABEL, MAX_UPLOAD_BYTES, MAX_UPLOAD_LABEL } from '../features/builder/documents/uploadFormats';
import Navbar from '../components/Layout/Navbar';
import TechHero, { TECH_COLORS } from '../components/TechHero';
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

// 状态筛选项：文案与列表状态 Tag 一致（状态说明由 Tag / 统计卡的 tooltip 承载）
const STATUS_OPTIONS = [
    { value: 'draft', label: '草稿' },
    { value: 'scheduled', label: '待构建' },
    { value: 'building', label: '构建中' },
    { value: 'ready', label: '已构建' },
    { value: 'published', label: '已发布' },
];

/** 状态统计卡主色（2026-11-17 改取共享徽标的文字色）：数字 / 角标图标 / 顶部高光条
 *  与列表状态胶囊同一份配色，不再各写一套 antd 预设色（绿 #52c41a / 紫 #722ed1） */
const STATUS_ACCENT: Record<string, string> = Object.fromEntries(
    Object.keys(PROJECT_STATUS).map((k) => [k, statusChip(k).color]),
);

const STATUS_ICON: Record<string, React.ReactNode> = {
    draft: <EditOutlined />,
    scheduled: <ClockCircleOutlined />,
    building: <SyncOutlined />,
    ready: <CheckCircleOutlined />,
    published: <RocketOutlined />,
};

/** '#5B8DEF' + 0.09 -> 'rgba(91,141,239,0.09)'（卡片淡色底） */
const withAlpha = (hex: string, a: number) => {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

/** 文件体量：B / KB / MB（上传列表与汇总共用，保留一位小数，避免长度抖动） */
const fmtBytes = (n?: number) => {
    if (!n || n <= 0) return '0 B';
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
};

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';

/** 紧凑卡片用的短日期：只保留 月/日 */
const fmtShortDate = (v?: string | null) =>
    v ? new Date(v).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) : '—';


/**
 * 单行截断文本 + 自定义悬浮提示：antd 列自带 ellipsis 的 title 是浏览器原生提示
 * （不可控样式、短文本也弹），项目名称/简介统一用它——只有真正溢出时才弹全文。
 */
const EllipsisTip: React.FC<{
    text?: string | null;
    fallback?: string;
    className?: string;
    placement?: 'topLeft' | 'top' | 'topRight';
    onClick?: React.MouseEventHandler<HTMLSpanElement>;
}> = ({ text, fallback = '—', className, placement = 'top', onClick }) => {
    const ref = useRef<HTMLSpanElement>(null);
    const [hovered, setHovered] = useState(false);
    const [overflow, setOverflow] = useState(false);
    if (!text) return <span className={className}>{fallback}</span>;
    return (
        <Tooltip title={text} placement={placement}
                 open={hovered && overflow}>
            <span
                ref={ref}
                className={`block truncate ${className || ''}`}
                onClick={onClick}
                onMouseEnter={() => {
                    const el = ref.current;
                    setOverflow(!!el && el.scrollWidth > el.clientWidth + 1);
                    setHovered(true);
                }}
                onMouseLeave={() => setHovered(false)}
            >
                {text}
            </span>
        </Tooltip>
    );
};

const MyProjectsPage: React.FC = () => {
    const navigate = useNavigate();
    // 服务端分页：projects 只保存当前页，搜索/筛选/排序/翻页全部交给后端
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [total, setTotal] = useState(0);
    // 各状态数量（统计卡）：接口只回过滤后的 total，所以按状态各发一次 page_size=1 的 count
    const [counts, setCounts] = useState<Record<StatusKey, number> & { total: number }>(EMPTY_COUNTS);
    // 统计是否已出过一次结果：加载中 counts 还是全 0，不能据此把操作栏藏起来（会闪一下才出现）
    const [countsReady, setCountsReady] = useState(false);
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

    // 状态统计：一次轻量 /my 取回全部项目（ProjectBrief，不含图谱），前端按 status 汇总。
    // 原来是对每个状态各发一次 page_size=1 的 count（连总数共 6 个请求），进页面光统计就 6 发。
    const loadCounts = useCallback(async () => {
        try {
            const list = await projectsApi.getMyProjects(apiScope);
            const next = { ...EMPTY_COUNTS, total: list.length };
            for (const p of list) {
                const key = statusOf(p) as StatusKey;
                if (key in next) next[key] += 1;
            }
            setCounts(next);
        } catch {
            // 统计失败不阻塞列表，保留上一次数值
        } finally {
            setCountsReady(true);
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
    const [updating, setUpdating] = useState(false);

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
                    ? `项目已创建${docNote}，构建已定时`
                    : `项目已创建${docNote}，后台构建已启动`);
            } else {
                buildNote = values.build_mode === 'scheduled'
                    ? '；无文档，定时构建已跳过'
                    : '；上传文档后点「一键构建」即可开始';
                message.success(`项目已创建${buildNote}`);
            }
            closeCreateModal();

            // 带上创建接口的返回值（2026-11-19）：详情页首屏直接用它，省掉一次
            // /projects/:id；附时间戳，详情页只在极短时间内采信，避免刷新重放旧快照
            navigate(`/projects/${pid}`, { state: { project: newProject, at: Date.now() } });
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
        setUpdating(true);
        scrollPositionRef.current = window.scrollY;
        try {
            await projectsApi.updateProject(editingProject.id, {
                name: values.name,
                description: values.description,
            });
            message.success('项目信息已更新');
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
            notifyError(error, '更新失败，请稍后重试');
        } finally {
            setUpdating(false);
        }
    };

    const handleDeleteProject = (projectId: number) => {
        confirmImpact({
            title: '确认删除项目',
            impact: [
                '项目内已上传的全部文档将一并删除，且无法恢复；',
                'AI 抽取出的本体框架与实例图谱数据将一并删除；',
                '若已发布到资产中心，公共区的展示也会随之消失。',
            ],
            okText: '确认删除',
            onOk: async () => {
                try {
                    await projectsApi.deleteProject(projectId);
                    message.success('项目已删除');
                    setSelectedIds(prev => {
                        const next = new Set(prev);
                        next.delete(projectId);
                        return next;
                    });
                    refreshAll();
                } catch (error) {
                    notifyError(error, '删除失败');
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
                    notifyError(e, '批量删除失败');
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
            {/* 删除只保留一次确认：handleDeleteProject 里的影响范围弹窗（confirmImpact）
                已经把不可恢复的内容逐条列出，外面再套 Popconfirm 就是点两次确认（2026-11-19） */}
            <Button type="link" size="small" danger icon={<DeleteOutlined />}
                    onClick={() => handleDeleteProject(project.id)}>
                删除
            </Button>
        </Space>
    );

    /** layout=stack：表格里时间另起一行；inline：卡片视图里跟在标签后面 */
    const statusTag = (project: ProjectData, stale?: boolean, layout: 'stack' | 'inline' = 'inline') => {
        const statusKey = statusOf(project);
        const meta = PROJECT_STATUS[statusKey] || PROJECT_STATUS.draft;
        // 状态胶囊走共享的冷调配色（2026-11-17）：与项目详情页身份段、文档状态、阶段状态同一套，
        // 不再是 antd 预设的淡绿 / 淡紫
        const chip = statusChip(statusKey);
        // 「有新版本」用角标圆点表达（双 Tag 并排会换行挤占 类/实例 列）；
        // 说明统一走外层 Tooltip——角标不再挂原生 title，避免两层提示叠着弹
        const tag = <Tag style={{ ...chip, marginInlineEnd: 0 }}>{meta.text}</Tag>;
        const tagNode = (
            <Tooltip
                className="cursor-help"
                title={stale
                    ? `${meta.desc}；有新版本：重新构建过，资产中心还是旧版本，可重新发布`
                    : meta.desc}>
                {stale ? (
                    <Badge dot color={STALE_PUBLISHED_TAG.color}>{tag}</Badge>
                ) : tag}
            </Tooltip>
        );
        const schedLabel = statusKey === 'scheduled' ? fmtSchedule(project.scheduled_at) : '';
        if (!schedLabel) {
            return <span className="inline-flex items-center">{tagNode}</span>;
        }
        // 时间并进同一个标签（2026-11-16）：原来是"标签 + 下一行裸文字"，
        // 一个胶囊配一行小字，左边缘也对不齐，看着散。合成一个 pill 之后
        // 是"待构建 · 今天 14:00"一整个信息单元，扫读时更整
        return (
            <span className="inline-flex items-center">
                <Tooltip title={`定时构建：${fmtScheduleFull(project.scheduled_at)}`}>
                    <Tag style={{ ...chip, marginInlineEnd: 0 }} className="cursor-help whitespace-nowrap">
                        {meta.text}
                        {/* 分隔符用中点 ·（2026-11-16）：冒号在全角下偏重，把标签切成两截；
                            连字符又会和时间里的 `11-18` 撞（「待构建-11-18 09:30」读起来像范围）。
                            ·还和同表「18 类 · 0 实例」的并列写法一致。字重、颜色与状态一致，
                            两者是同一句「状态 · 时间」，不再一深一浅 */}
                        <span>{` · ${schedLabel}`}</span>
                    </Tag>
                </Tooltip>
            </span>
        );
    };

    const listColumns: ColumnsType<ProjectData> = [
        {
            title: '项目名称', dataIndex: 'name', key: 'name', width: '14.5%',
            ellipsis: { showTitle: false },
            render: (_, project) => (
                <div className="flex items-center gap-2 min-w-0">
                    <EllipsisTip text={project.name} className="font-medium text-gray-800 min-w-0 flex-1 cursor-default" />
                    {isSuperAdmin && scope === 'all' && (
                        <Tag className="ml-1 shrink-0" color="blue">{project.owner?.username || `用户#${project.owner_id ?? '?'}`}</Tag>
                    )}
                </div>
            ),
        },
        {
            title: '项目简介', dataIndex: 'description', key: 'description', width: '19.5%',
            ellipsis: { showTitle: false },
            render: (v: string) => <EllipsisTip text={v} fallback="暂无描述" className="text-gray-500 cursor-default" />,
        },
        {
            // 这里不再挂列内筛选（2026-11-16）：工具栏的「全部状态」已经是服务端口径的
            // 多选，列内这个 filters 只在当前页做客户端过滤，两者口径不一致；去掉后
            // 表头也没有漏斗图标挤占，标题自然居中
            title: '状态', key: 'status', width: '12%', align: 'center',
            render: (_, project) => statusTag(project, project.stale_published, 'stack'),
        },
        {
            title: '类 / 实例', key: 'stats', width: '10.5%', align: 'center',
            render: (_, project) => (
                <span className="tk-mono text-xs text-gray-500 whitespace-nowrap">
                    <span style={{ color: TC.blue }}>{project.classes_total ?? project.graph_data?.nodes?.length ?? 0}</span> 类
                    {' · '}
                    <span style={{ color: TC.purple }}>{project.instances_total ?? 0}</span> 实例
                </span>
            ),
        },
        { title: '创建日期', dataIndex: 'created_at', key: 'created_at', width: '11.5%', align: 'center',
          render: (v: string) => <span className="text-gray-500 text-xs">{fmtDate(v)}</span> },
        { title: '修改日期', dataIndex: 'updated_at', key: 'updated_at', width: '11.5%', align: 'center',
          render: (v: string) => <span className="text-gray-500 text-xs">{fmtDate(v)}</span> },
        // 状态 / 类实例 / 日期 / 操作 居中（2026-11-16）：这些都是定宽的短内容列，
        // 左对齐会在列右侧留下一大块空白，和「项目名称 / 项目简介」两个左对齐文本列
        // 一起看节奏是散的；居中后 5 列形成一条对称轴
        { title: '操作', key: 'actions', width: '18%', align: 'center', render: (_, project) => actionsRenderer(project) },
    ];

    return (
        <div className="h-full flex flex-col app-canvas">
            <style>{`
                /* 项目卡（2026-11-13）：不再是纯白底——白到冷蓝的极轻渐变，
                   右下角一组品牌蓝同心圆环做装饰（不用图标，避免和左上角图标重复），
                   hover 时圆环更清晰 */
                /* 选择列贴边（2026-11-16）：复选框离卡片左缘只有 8px，被"顶"在边上；
                   补 8px 让它和表格内容有同样的呼吸（表格其它单元格左右各 12px） */
                /* 选择器要写足层级：antd 自己的规则是带元素选择器的复合选择器
                   （.ant-table-wrapper .ant-table-tbody > tr > td），只写
                   .mp-table .ant-table-selection-column 优先级不够，padding 仍为 8px */
                .mp-table .ant-table-wrapper .ant-table-thead > tr > th.ant-table-selection-column,
                .mp-table .ant-table-wrapper .ant-table-tbody > tr > td.ant-table-selection-column {
                    padding-left: 12px;
                }
                .mp-proj-card {
                    background-image: linear-gradient(158deg, #FFFFFF 0%, #F8FBFF 55%, #EEF5FE 100%);
                }
                .mp-proj-ring {
                    position: absolute; right: -26px; bottom: -34px;
                    width: 112px; height: 112px; opacity: .12;
                    pointer-events: none; transition: opacity .2s ease;
                    background: radial-gradient(circle at center,
                        transparent 27%, currentColor 28.2%, transparent 29.6%,
                        transparent 47%, currentColor 48.2%, transparent 49.6%,
                        transparent 67%, currentColor 68.2%, transparent 69.6%);
                }
                .group:hover .mp-proj-ring { opacity: .20; }
                /* 状态统计卡：右角状态色图标块 + 同色淡底，纯展示卡不做 hover 位移
                   （--stat-accent / --stat-tint 由卡片内联按状态下发） */
                .mp-stat {
                    position: relative;
                    overflow: hidden;
                    border-radius: 14px;
                    border: 1px solid #EEF1F8;
                    background:
                        radial-gradient(150px 100px at 100% 0%, var(--stat-tint, rgba(91,141,239,.10)), transparent 72%),
                        linear-gradient(180deg, #FFFFFF 55%, var(--stat-tint, #F7F9FF) 160%);
                    box-shadow:
                        0 1px 2px rgba(15,23,42,.05),
                        0 12px 30px -18px rgba(15,23,42,.18),
                        inset 0 1px 0 rgba(255,255,255,.6);
                }
                .mp-stat .ant-statistic-title { font-size: 12px; color: #6B7280; margin-bottom: 4px; }
                .mp-stat .ant-statistic-content {
                    font-size: 26px; font-weight: 600;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                    font-variant-numeric: tabular-nums;
                }
                .mp-stat-icon {
                    position: absolute; top: 10px; right: 12px;
                    width: 26px; height: 26px;
                    display: inline-flex; align-items: center; justify-content: center;
                    border-radius: 8px;
                    background: var(--stat-tint, rgba(91,141,239,.10));
                    color: var(--stat-accent, #5B8DEF);
                    font-size: 14px;
                }
                /* 创建/编辑弹窗：antd 默认 24px 表单行距 + 20/24 内边距，在 500px 弹窗里显得松散，
                   表单行距收到 10px、标签 13px；外围留白比默认略宽，避免内容贴边；上传拖拽区图标与留白也缩一档 */
                .mp-modal .ant-modal-content { padding: 20px 24px 20px; }
                .mp-modal .ant-modal-header { margin-bottom: 12px; }
                .mp-modal .ant-modal-title { font-size: 15px; }
                .mp-modal .ant-form-item { margin-bottom: 10px; }
                .mp-modal .ant-form-item:last-child { margin-bottom: 0; }
                .mp-modal .ant-form-item-label { padding-bottom: 4px; }
                .mp-modal .ant-form-item-label > label { height: auto; font-size: 13px; color: #52607A; }
                /* 弹窗 · 上传文档（2026-11-23 改版）
                   改版前：拖拽区只有大图标 + 一行灰字；选中文件后是 antd 默认灰字列表，
                   和 hint 同为 #8A94AB，看起来像"没选上 / 不可用"。
                   改版后：拖拽区补一行深色主文案、图标收成品牌色圆角块；
                   已选文件变成冷白小卡（品牌文档图标 / 深色文件名 / 等宽体量 / 独立移除），
                   下方再用「发光圆点 + 彩色等宽数字」汇总，沿用站内面板装饰语言。 */
                .mp-modal .ant-upload-drag .ant-upload-btn { padding: 12px 0; }
                .mp-modal .ant-upload-drag-icon { display: flex; justify-content: center; margin-bottom: 5px; }
                .mp-modal .ant-upload-drag .ant-upload-drag-icon .anticon {
                    width: 40px; height: 40px;
                    display: inline-flex; align-items: center; justify-content: center;
                    font-size: 20px; border-radius: 12px;
                    color: #2A57C6;
                    background: linear-gradient(135deg, rgba(91, 141, 239, 0.18), rgba(110, 107, 240, 0.10));
                    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.85), 0 6px 14px -12px rgba(42, 87, 198, 0.55);
                }
                .mp-modal .ant-upload-drag .ant-upload-text {
                    margin: 0 0 3px;
                    color: #1F2A44; font-size: 13px; font-weight: 600;
                }
                .mp-modal .ant-upload-drag .ant-upload-hint { font-size: 12px; line-height: 1.6; color: #6B7794; }
                /* 拖拽区 / 已选文件 / 汇总条属于同一个「上传文档」板块，间距收到 4px，
                   让它们读起来是一组，而不是三块互不相干的内容；
                   已选列表同时压平 antd 的定高 / 内边距 / 灰底 hover，一行一条冷白小卡 */
                .mp-modal .ant-upload-list { margin-top: 4px; display: flex; flex-direction: column; gap: 4px; }
                .mp-modal .ant-upload-list .ant-upload-list-item,
                .mp-modal .ant-upload-list .ant-upload-list-item-container { height: auto; margin: 0; padding: 0; }
                /* antd 的 hover 底色是铺在 item 本体上的（不是 ::before），一并清掉，
                   悬停反馈交给下方小卡自己的描边 */
                .mp-modal .ant-upload-list .ant-upload-list-item:hover { background: transparent; }
                .mp-modal .ant-upload-list .ant-upload-list-item::before { display: none; }
                .mp-upload-item {
                    display: flex; align-items: center; gap: 8px;
                    width: 100%;
                    padding: 6px 8px 6px 6px;
                    transition: border-color 0.15s ease;
                    border: 1px solid #E1E9F7;
                    border-radius: 10px;
                    background: linear-gradient(180deg, #FBFDFF 0%, #F4F8FF 100%);
                    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.9);
                    animation: mp-upload-in 0.18s ease-out;
                }
                .mp-upload-item:hover { border-color: rgba(91, 141, 239, 0.45); }
                @keyframes mp-upload-in {
                    from { opacity: 0; transform: translateY(-3px); }
                    to { opacity: 1; transform: none; }
                }
                .mp-upload-item-icon {
                    flex: none;
                    width: 26px; height: 26px;
                    display: inline-flex; align-items: center; justify-content: center;
                    border-radius: 8px;
                    color: #2A57C6; font-size: 14px;
                    background: rgba(91, 141, 239, 0.12);
                }
                .mp-upload-item-name {
                    flex: 1 1 auto; min-width: 0;
                    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
                    color: #1F2A44; font-size: 13px; font-weight: 500;
                }
                .mp-upload-item-size {
                    flex: none;
                    color: #52607A; font-size: 12px;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                    font-variant-numeric: tabular-nums;
                }
                .mp-upload-item-remove {
                    flex: none;
                    width: 22px; height: 22px; padding: 0;
                    display: inline-flex; align-items: center; justify-content: center;
                    border: 0; border-radius: 6px; cursor: pointer;
                    color: #8A94AB; background: transparent;
                    font-size: 12px; transition: color 0.15s ease, background 0.15s ease;
                }
                .mp-upload-item-remove:hover { color: #D92D20; background: rgba(217, 45, 32, 0.08); }
                /* 汇总条：发光圆点 + 彩色等宽数字，直接回答"到底选上了没有" */
                .mp-upload-summary {
                    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
                    margin-top: 4px;
                    font-size: 12px; color: #52607A;
                }
                .mp-upload-summary-dot {
                    width: 7px; height: 7px; border-radius: 50%; flex: none;
                    background: #2F80ED;
                    box-shadow: 0 0 0 3px rgba(91, 141, 239, 0.16), 0 0 8px rgba(91, 141, 239, 0.55);
                }
                .mp-upload-summary b {
                    color: #2A57C6; font-weight: 600;
                    font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
                    font-variant-numeric: tabular-nums;
                }
                .mp-upload-summary-note { color: #8A94AB; }
                /* 列表面板：和卡片同一套实体感材质（极轻渐变 + 内高光 + 近距投影） */
                /* 列表面板：和卡片同一套实体感材质（极轻渐变 + 内高光 + 近距投影） */
                .mp-table {
                    /* 平铺冷蓝：垂直渐变在长表格里会被拉得很淡，前几屏几乎还是白的；
                       平铺一层浅冷蓝，整块表体从第一行起就能看出和页面白卡的区分 */
                    background-color: #F5F9FF;
                    background-image: none;
                    box-shadow: inset 0 1px 0 rgba(255,255,255,.95), inset 0 -1px 0 rgba(219,228,242,.45), 0 10px 26px -20px rgba(15,23,42,.22), 0 2px 5px rgba(15,23,42,.04);
                }
                /* 表体透底（2026-11-18）：antd 默认给 .ant-table / 单元格铺 colorBgContainer 纯白，
                   会在冷白卡片里露出一块白色矩形；这里必须 !important——antd 的表格背景
                   选择器带元素层级，普通规则会被压住，看起来就像"没生效" */
                .mp-table .ant-table-wrapper,
                .mp-table .ant-table,
                .mp-table .ant-table-container,
                .mp-table .ant-table-content,
                .mp-table .ant-table-tbody > tr > td,
                .mp-table .ant-table-tbody > tr > td.ant-table-cell {
                    background: transparent !important;
                }
                /* 表头在冷蓝底上再压一档，形成明确的分层带；
                   注意别和行 hover 撞色：6% 品牌蓝叠在 #F5F9FF 上约等于 #ECF3FE，
                   表头要比它更深，hover 才有"比表头更浅"的层级感 */
                .mp-table .ant-table-thead > tr > th {
                    background: #E7EFFC !important;
                }
                .mp-table .ant-table-tbody > tr.ant-table-row:hover > td,
                .mp-table .ant-table-tbody > tr > td.ant-table-cell-row-hover {
                    background: rgba(91, 141, 239, 0.06) !important;
                }
                /* 分页器默认贴死在卡片右缘，看起来太挤；整体往左收 16px 留出呼吸感
                   （不做居中：站内其他表格都是靠右，保持一致） */
                .mp-table .ant-table-pagination.ant-pagination { padding-right: 16px; }
                /* 顶部卡片里的「我的项目 / 全部项目」切换（2026-11-13）：
                   默认的灰轨道 + 白滑块 + 大范围柔阴影，压在浅色渐变横幅上会显得脏，
                   选中态也糊；改成品牌淡蓝胶囊轨道 + 白色滑块 + 品牌蓝加粗文字，
                   滑块只保留一层 1px 的轻投影 */
                .scope-seg.ant-segmented {
                    background: rgba(91, 141, 239, 0.10);
                    border: 1px solid rgba(91, 141, 239, 0.16);
                    padding: 3px;
                }
                .scope-seg.ant-segmented .ant-segmented-item {
                    color: #52607A;
                    transition: color 0.2s ease;
                }
                .scope-seg.ant-segmented .ant-segmented-item-label {
                    padding: 0 14px;
                    font-size: 13px;
                }
                .scope-seg.ant-segmented .ant-segmented-item-selected {
                    color: #2A57C6;
                    font-weight: 600;
                    background: #FFFFFF;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.14);
                }
                .scope-seg.ant-segmented .ant-segmented-thumb {
                    background: #FFFFFF;
                    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.14);
                }
                /* 未选中项的悬停/按下底色：默认是灰的，在淡蓝轨道里像一块脏斑，
                   改成白色微亮；!important 用于压过 antd 的 :hover::after */
                .scope-seg.ant-segmented .ant-segmented-item:not(.ant-segmented-item-selected)::after {
                    background-color: rgba(255, 255, 255, 0.65) !important;
                }
                .scope-seg.ant-segmented .ant-segmented-item:not(.ant-segmented-item-selected):hover {
                    color: #1F2A44;
                }
                /* 查询条样式走全站 .tk-query-bar（冷蓝纸面 + 控件去边框 + 占位符加深） */
                /* 新建项目（2026-11-13 首版纯色 #3F6FD8；2026-11-18 起与项目概览页对齐）：
                   页面级主按钮统一成「上亮下深的品牌竖向渐变」，
                   平均色仍是原来的深一档，不抢上方 Hero，但和全站主按钮同一套语言 */
                .mp-new-btn.ant-btn-primary {
                    background: linear-gradient(180deg, #5B8DEF 0%, #3F6FD8 100%) !important;
                    border-color: transparent !important;
                    box-shadow:
                        0 8px 18px -12px rgba(47, 98, 216, 0.65),
                        inset 0 1px 0 rgba(255, 255, 255, 0.22) !important;
                }
                .mp-new-btn.ant-btn-primary:not(:disabled):hover {
                    background: linear-gradient(180deg, #5486EC 0%, #3865CE 100%) !important;
                }
                .mp-new-btn.ant-btn-primary:not(:disabled):active {
                    background: linear-gradient(180deg, #4E7FE4 0%, #335DC0 100%) !important;
                }
                /* 空状态「清除筛选」：默认按钮在浅色画布上太素，改成品牌蓝描边 + 白底，
                   hover 时底色和描边一起加深（与 Hero 的幽灵按钮同一套语言） */
                .mp-clear-btn.ant-btn-default {
                    color: #2A57C6;
                    border-color: rgba(91, 141, 239, 0.38);
                    background: rgba(255, 255, 255, 0.75);
                }
                .mp-clear-btn.ant-btn-default:hover {
                    color: #2A57C6 !important;
                    border-color: rgba(91, 141, 239, 0.60) !important;
                    background: rgba(91, 141, 239, 0.10) !important;
                }
            `}</style>
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    title={isSuperAdmin && scope === 'all' ? '全部项目' : '我的项目'}
                    titleSuffix="PROJECT WORKSPACE"
                    subtitle={isSuperAdmin && scope === 'all'
                        ? '平台全部用户的项目，可查看与编辑'
                        : '创建与管理你的本体建模项目：AI 框架提取 · 骨架画布 · 实例探索 · 版本治理'}
                    showConstellation={counts.total > 0}
                    actions={isSuperAdmin ? (
                        <Segmented
                            className="scope-seg"
                            shape="round"
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

                {/* 状态统计（2026-11 列表视图改版；说明内嵌卡片 2026-11-09）
                   样式 2026-11-12：统一 .mp-stat（右角状态色图标块 + 同色淡底） */}
                <div className="grid grid-cols-2 md:grid-cols-6 gap-3 mb-4">
                    <div className="mp-stat px-4 py-3"
                         style={{ '--stat-accent': '#5B8DEF', '--stat-tint': withAlpha('#5B8DEF', 0.09) } as React.CSSProperties}>
                        <Statistic title="项目总数" value={counts.total}
                                   valueStyle={{ color: '#5B8DEF' }} />
                        <span className="mp-stat-icon"><FolderOpenOutlined /></span>
                    </div>
                    {(Object.keys(PROJECT_STATUS) as Array<keyof typeof PROJECT_STATUS>).map((k) => {
                        const meta = PROJECT_STATUS[k];
                        const countKey = k as StatusKey;
                        const accent = STATUS_ACCENT[k] || '#5B8DEF';
                        return (
                            <div key={k} className="mp-stat px-4 py-3"
                                 style={{ '--stat-accent': accent, '--stat-tint': withAlpha(accent, 0.09) } as React.CSSProperties}>
                                <Tooltip title={meta.desc} className="cursor-help">
                                    <div>
                                        <Statistic
                                            title={<span className="cursor-help">{meta.text}
                                                <InfoCircleOutlined className="ml-1 text-gray-300 text-xs" /></span>}
                                            value={counts[countKey]}
                                            valueStyle={{ color: accent }} />
                                    </div>
                                </Tooltip>
                                <span className="mp-stat-icon">{STATUS_ICON[k]}</span>
                            </div>
                        );
                    })}
                </div>

                {/* 操作栏：搜索/筛选/排序靠左；视图切换与「新建项目」成组靠右（新建按钮 2026-11-11 从顶栏迁入）。
                    搜索/筛选/视图切换是纯前端控件，首屏直接渲染，不等统计接口——
                    否则页面进入时会先空一段、再"补出"这一行（统计数据未回来时 counts 全 0）。
                    2026-11-12：控件统一 32px 高；右侧两个控件固定成组，窄屏换行时不会散开 */}
                <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
                    {(!countsReady || counts.total > 0) && (
                        <div className="tk-query-bar flex flex-wrap items-center gap-1 p-1">
                            <Input
                                prefix={<SearchOutlined className="text-[#5B8DEF]" />}
                                placeholder="搜索项目名称/描述..."
                                value={searchText}
                                onChange={e => setSearchText(e.target.value)}
                                allowClear
                                style={{ width: 220 }}
                            />
                            <Select
                                mode="multiple" allowClear maxTagCount="responsive"
                                placeholder="全部状态" style={{ width: 170 }}
                                value={statusFilter}
                                onChange={(v) => { setStatusFilter(v); setPage(1); }}
                                options={STATUS_OPTIONS}
                            />
                            <div className="flex items-center gap-1 pl-1">
                                <span className="px-1 text-[12.5px] whitespace-nowrap text-[#66738F]">排序</span>
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
                                    style={{ width: 118 }}
                                />
                                <Tooltip title={sortOrder === 'asc' ? '升序' : '降序'}>
                                    <Button
                                        icon={<SortAscendingOutlined style={{ transform: sortOrder === 'desc' ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />}
                                        onClick={() => { setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc'); setPage(1); }}
                                    />
                                </Tooltip>
                            </div>
                        </div>
                    )}
                    {selectedIds.size > 0 && (
                        <div className="flex items-center gap-1 rounded-full border border-[rgba(91,141,239,0.22)] bg-[rgba(91,141,239,0.10)] pl-3 pr-1.5 py-0.5">
                            <span className="text-[12.5px] whitespace-nowrap text-[#2A57C6]">
                                已选 <span className="font-semibold">{selectedIds.size}</span> 项
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
                    <div className="ml-auto flex items-center gap-2">
                        {(!countsReady || counts.total > 0) && (
                            <Segmented
                                className="scope-seg"
                                value={viewMode}
                                onChange={(v) => setViewMode(v as ViewMode)}
                                options={[
                                    { value: 'list', icon: <BarsOutlined />, label: '列表' },
                                    { value: 'cards', icon: <AppstoreOutlined />, label: '卡片' },
                                ]}
                            />
                        )}
                        <Button
                            type={!countsReady || counts.total > 0 ? 'primary' : 'default'}
                            icon={<PlusOutlined />}
                            onClick={() => setIsModalOpen(true)}
                            className="mp-new-btn"
                        >
                            新建项目
                        </Button>
                    </div>
                </div>

                {/* countsReady 之前不判「还没有项目」：列表可能先于统计返回，否则会闪一下空状态 */}
                {(loading && projects.length === 0) || !countsReady ? (
                    <div className="flex justify-center pt-16 pb-24">
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
                    /* 空结果（2026-11-13）：antd 默认 Empty 只有一张灰图和一句灰字，在浅蓝画布上很单薄。
                       改成「图标块 + 主标题 + 一句原因提示 + 品牌蓝次要按钮」：
                       先告诉用户为什么是空的，再给出一个明确的下一步 */
                    <div className="flex flex-col items-center justify-center px-4 py-14">
                        <div className="flex max-w-[420px] flex-col items-center text-center">
                            <span
                                className="flex h-14 w-14 items-center justify-center rounded-2xl text-[24px] text-[#2F62D8]"
                                style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                            >
                                <SearchOutlined />
                            </span>
                            <h3 className="mt-4 text-[15px] font-semibold text-[#0F172A]">没有找到匹配的项目</h3>
                            <p className="mt-1.5 text-[12.5px] leading-[1.7] text-[#66738F]">
                                检查一下关键词，或清除当前的状态筛选后再试
                            </p>
                            <Button
                                className="mp-clear-btn mt-4"
                                onClick={() => {
                                    setSearchText(''); setStatusFilter([]); setPage(1);
                                }}
                            >
                                清除筛选
                            </Button>
                        </div>
                    </div>
                ) : viewMode === 'list' ? (
                    <div className="mp-table border border-[#E4ECF8] rounded-xl shadow-sm overflow-hidden">
                        <Table
                            rowKey="id" size="middle"
                            loading={loading}
                            /* 列宽按比例分配（宽度变化时整体等比缩放）；
                               x 是最小宽度兜底：窄屏下改为横向滚动，避免操作列/日期被压坏 */
                            scroll={{ x: 1120 }}
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
                        />
                    </div>
                ) : (
                    <>
                        {/* 卡片视图（2026-11-13 重设计）：原来的 160px 大封面只承载装饰，
                            一张卡 310px，一屏只能看到 4–6 个项目。改成紧凑信息卡——
                            图标 + 名称 + 状态 + 一行描述 + 节点/更新时间的元信息行，
                            大屏一行 6 个，一屏能看到 15 个以上；操作按钮 hover 才出现，
                            不挤占常驻信息 */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
                            {projects.map((project) => {
                                const isSelected = selectedIds.has(project.id);
                                const nodes = project.nodes_total ?? project.graph_data?.nodes?.length ?? 0;
                                return (
                                    <div key={project.id} className="group relative h-full">
                                        {/* 选择圆圈：右侧，未选中时 hover 才浮现 */}
                                        <button
                                            type="button"
                                            aria-pressed={isSelected}
                                            aria-label={`选择项目 ${project.name}`}
                                            className={`absolute top-2.5 right-2.5 z-10 w-5 h-5 flex items-center justify-center rounded-full transition-all duration-200 cursor-pointer ${
                                                isSelected
                                                    ? 'bg-blue-500 border border-blue-500 opacity-100'
                                                    : 'bg-white border border-[#DCE3F0] shadow-sm opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
                                            }`}
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                setSelectedIds(prev => {
                                                    const next = new Set(prev);
                                                    if (next.has(project.id)) next.delete(project.id); else next.add(project.id);
                                                    return next;
                                                });
                                            }}
                                        >
                                            <Checkbox
                                                checked={isSelected}
                                                className="[&_.ant-checkbox-inner]:border-gray-400 scale-75 pointer-events-none"
                                                tabIndex={-1}
                                                aria-hidden
                                            />
                                        </button>

                                        <div
                                            role="button"
                                            tabIndex={0}
                                            aria-label={`查看项目 ${project.name}`}
                                            onClick={() => navigate(`/projects/${project.id}`)}
                                            onKeyDown={(e) => {
                                                if (e.key === 'Enter' || e.key === ' ') {
                                                    e.preventDefault();
                                                    navigate(`/projects/${project.id}`);
                                                }
                                            }}
                                            className="mp-proj-card relative flex h-full flex-col overflow-hidden rounded-xl border border-[#E7EEF9] p-3.5 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-[border-color,box-shadow] duration-200 hover:border-[#D3E1F5] hover:shadow-[0_16px_30px_-22px_rgba(15,23,42,0.40)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                                        >
                                            <span className="mp-proj-ring" style={{ color: '#5B8DEF' }} aria-hidden />
                                            <div className="flex items-start gap-2.5">
                                                <span
                                                    className="w-9 h-9 shrink-0 rounded-lg flex items-center justify-center text-[16px] text-[#2F62D8]"
                                                    style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                                >
                                                    <ApartmentOutlined />
                                                </span>
                                                <div className="min-w-0 flex-1 pr-4">
                                                    <EllipsisTip
                                                        text={project.name}
                                                        className="text-[14px] font-semibold text-[#0F172A] transition-colors group-hover:text-[#2F62D8]"
                                                    />
                                                </div>
                                            </div>

                                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                {statusTag(project, project.stale_published)}
                                                {isSuperAdmin && scope === 'all' && (
                                                    <Tag className="!m-0 !text-[10px]" color="blue">
                                                        {project.owner?.username || `用户#${project.owner_id ?? '?'}`}
                                                    </Tag>
                                                )}
                                            </div>

                                            <EllipsisTip
                                                text={project.description}
                                                fallback="暂无描述"
                                                className="mt-1.5 text-[12px] text-[#66738F]"
                                            />

                                            <div className="mt-auto flex h-6 items-center justify-between gap-2 border-t border-[#F0F4FB] pt-2">
                                                <span className="truncate text-[11px] text-[#8A94AB]">
                                                    <span className="tk-mono">{nodes}</span> 节点 · {fmtShortDate(project.updated_at)} 更新
                                                </span>
                                                <div
                                                    className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100"
                                                    onClick={(e) => e.stopPropagation()}
                                                >
                                                    {/* 眼睛图标本身就是「查看详情」，不再挂悬停提示 */}
                                                    <Button type="text" size="small" icon={<EyeOutlined />}
                                                            aria-label="查看项目详情"
                                                            onClick={() => navigate(`/projects/${project.id}`)} />
                                                    <Tooltip title="编辑项目名称与介绍">
                                                        <Button type="text" size="small" icon={<EditOutlined />}
                                                                onClick={() => handleEditProject(project)} />
                                                    </Tooltip>
                                                    {/* 同表格视图：确认只留影响范围弹窗这一层 */}
                                                    <Button type="text" size="small" danger icon={<DeleteOutlined />}
                                                            aria-label="删除项目"
                                                            onClick={() => handleDeleteProject(project.id)} />
                                                </div>
                                            </div>
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
                className="mp-modal"
                maskClosable={false}
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
                            rows={3}
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
                                    message.warning(`不支持 .${ext} 格式：仅支持 ${UPLOAD_TYPE_LABEL}`);
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
                            /* 已选文档改用自绘行（2026-11-23）：antd 默认列表是灰字，
                               和拖拽区提示同色，选完文件后用户常常看不出"已经选上了" */
                            itemRender={(_node, file, _list, actions) => (
                                <div className="mp-upload-item">
                                    <span className="mp-upload-item-icon"><FileTextOutlined /></span>
                                    <span className="mp-upload-item-name" title={file.name}>{file.name}</span>
                                    <span className="mp-upload-item-size">{fmtBytes(file.size)}</span>
                                    <button
                                        type="button"
                                        className="mp-upload-item-remove"
                                        aria-label={`移除 ${file.name}`}
                                        onClick={() => actions.remove()}
                                    >
                                        <DeleteOutlined />
                                    </button>
                                </div>
                            )}
                        >
                            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                            <p className="ant-upload-text">点击或拖拽文档到此处</p>
                            <p className="ant-upload-hint">
                                支持 {UPLOAD_TYPE_LABEL}，单文件 ≤{MAX_UPLOAD_LABEL}，可多选
                            </p>
                        </Upload.Dragger>
                        {pendingFiles.length > 0 && (
                            <div className="mp-upload-summary">
                                <span className="mp-upload-summary-dot" aria-hidden />
                                <span>
                                    已选择 <b>{pendingFiles.length}</b> 个文档 · 共{' '}
                                    <b>{fmtBytes(pendingFiles.reduce((sum, f) => sum + f.size, 0))}</b>
                                </span>
                                <span className="mp-upload-summary-note">创建项目后自动上传并构建</span>
                            </div>
                        )}
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
                title="编辑项目信息"
                open={isEditModalOpen}
                onCancel={() => {
                    setIsEditModalOpen(false);
                    setEditingProject(null);
                    editForm.resetFields();
                }}
                footer={null}
                width={500}
                className="mp-modal"
                maskClosable={false}
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
                            rows={3}
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
                            <Button type="primary" htmlType="submit" loading={updating}>
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
