import React, { useEffect, useMemo, useState } from 'react';
import { Button, Modal, Form, Input, Space, Table, Select, Typography, Drawer } from 'antd';
import { message, notifyError } from '../shared/feedback';
import {
    ApartmentOutlined,
    ClockCircleOutlined,
    ClusterOutlined,
    DeleteOutlined,
    EditOutlined,
    ExclamationCircleOutlined,
    PlusOutlined,
    ReloadOutlined,
    SwapOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import TechHero from '../components/TechHero';
import { Dot, Note, PageLoading, StatBand, TkTag } from '../shared/consoleUi';
import { getDomains, invalidateDomains, createDomain, updateDomain, deleteDomain, getDomainProjects, migrateProjectsBatch, KnowledgeDomain, ProjectInDomain } from '../api/domains';

const { TextArea } = Input;
const { Text } = Typography;

interface DomainWithCount extends KnowledgeDomain {
    projectCount: number;
}

/** 表格里的日期只到天：知识域是慢变参考数据，精确到秒没有信息量 */
const fmtDay = (v?: string | null) => (v ? v.slice(0, 10) : '—');

const DomainManagementPage: React.FC = () => {
    const [domains, setDomains] = useState<DomainWithCount[]>([]);
    const [loading, setLoading] = useState(true);
    // 刷新按钮的 loading 只在手动点击时出现：首屏加载由表格自己显示，按钮不动
    const [refreshing, setRefreshing] = useState(false);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
    const [isMigrateDrawerOpen, setIsMigrateDrawerOpen] = useState(false);
    const [editingDomain, setEditingDomain] = useState<KnowledgeDomain | null>(null);
    const [deletingDomain, setDeletingDomain] = useState<DomainWithCount | null>(null);
    const [migratingDomain, setMigratingDomain] = useState<DomainWithCount | null>(null);
    const [domainProjects, setDomainProjects] = useState<ProjectInDomain[]>([]);
    const [projectTargetDomains, setProjectTargetDomains] = useState<Record<number, number>>({}); // projectId -> targetDomainId
    const [form] = Form.useForm();
    // 首屏加载失败 ≠ 没有数据：单独成态，避免把网络/权限错误渲染成「暂无知识域」
    const [loadFailed, setLoadFailed] = useState(false);
    // 提交中状态：防连点重复创建/迁移/删除
    const [formSubmitting, setFormSubmitting] = useState(false);
    const [migrateSubmitting, setMigrateSubmitting] = useState(false);
    const [deleteSubmitting, setDeleteSubmitting] = useState(false);
    const [rowLoading, setRowLoading] = useState<{ id: number; action: 'migrate' | 'delete' } | null>(null);

    useEffect(() => {
        loadDomains();
    }, []);

    const loadDomains = async (force = false) => {
        // force：手动刷新时绕过 60s 缓存，真正回源
        if (force) invalidateDomains();
        setLoading(true);
        try {
            const allDomains = await getDomains();

            const domainStats: DomainWithCount[] = allDomains.map(domain => ({
                ...domain,
                // 后端返回的项目总数（含未发布），与删除校验口径一致
                projectCount: domain.project_count ?? 0,
            }));

            setDomains(domainStats);
            setLoadFailed(false);
        } catch (error: any) {
            setLoadFailed(true);
            notifyError(error, '加载知识域失败');
        } finally {
            setLoading(false);
        }
    };

    const refresh = async () => {
        setRefreshing(true);
        try {
            await loadDomains(true);
        } finally {
            setRefreshing(false);
        }
    };

    /** 迁移/删除流程收尾：关抽屉、清临时状态、回源列表 */
    const finishMigrate = () => {
        setIsMigrateDrawerOpen(false);
        setIsDeleteModalOpen(false);
        setMigratingDomain(null);
        setDeletingDomain(null);
        setDomainProjects([]);
        setProjectTargetDomains({});
        loadDomains();
    };

    /** 迁移可能已部分生效：重新拉项目列表并清空旧分配，避免拿过期数据重试 */
    const reloadMigrateProjects = async (domainId: number) => {
        setProjectTargetDomains({});
        try {
            setDomainProjects(await getDomainProjects(domainId));
        } catch {
            /* 拉取失败保留旧列表，用户可自行关闭抽屉 */
        }
        loadDomains();
    };

    const stats = useMemo(() => {
        const covered = domains.reduce((sum, d) => sum + d.projectCount, 0);
        const empty = domains.filter(d => d.projectCount === 0).length;
        const latest = domains.reduce((max, d) => (d.updated_at && d.updated_at > max ? d.updated_at : max), '');
        return { covered, empty, latest };
    }, [domains]);

    const handleCreateOrUpdateDomain = async (values: any) => {
        if (formSubmitting) return;
        setFormSubmitting(true);
        try {
            const payload = {
                name: String(values.name ?? '').trim(),
                description: typeof values.description === 'string' ? values.description.trim() : values.description,
            };
            if (editingDomain) {
                await updateDomain(editingDomain.id, payload);
                message.success('知识域已更新');
            } else {
                await createDomain(payload);
                message.success('知识域已创建');
            }
            setIsModalOpen(false);
            form.resetFields();
            setEditingDomain(null);
            loadDomains();
        } catch (error: any) {
            // 后端对重名返回 409：单独提示，别再落到「操作失败」的兜底文案
            if (error?.response?.status === 409) {
                message.warning('该知识域名称已存在，请换一个名称');
            } else {
                notifyError(error, '操作失败，请稍后重试');
            }
        } finally {
            setFormSubmitting(false);
        }
    };

    const handleEdit = (domain: KnowledgeDomain) => {
        setEditingDomain(domain);
        form.setFieldsValue({
            name: domain.name,
            description: domain.description,
        });
        setIsModalOpen(true);
    };

    /**
     * 打开迁移抽屉
     */
    const handleMigrateClick = async (domain: DomainWithCount) => {
        if (rowLoading) return;
        setMigratingDomain(domain);
        // 普通迁移不是删除流程：清掉上一次删除流程可能遗留的标记，
        // 否则抽屉会继续显示「迁移并删除」和 danger 样式（实际不会删除，但界面是错的）
        setDeletingDomain(null);
        setProjectTargetDomains({});

        if (domain.projectCount <= 0) {
            setMigratingDomain(null);
            message.info('该知识域下没有项目，无需迁移');
            return;
        }
        // 后端读的是整份 graph_data，项目多时要等几秒：行按钮给 loading，避免以为没点上
        setRowLoading({ id: domain.id, action: 'migrate' });
        try {
            setDomainProjects(await getDomainProjects(domain.id));
            setIsMigrateDrawerOpen(true);
        } catch (error: any) {
            notifyError(error, '加载项目列表失败');
        } finally {
            setRowLoading(null);
        }
    };

    /**
     * 点击删除按钮
     * 如果有项目，自动进入迁移流程；否则直接确认删除
     */
    const handleDeleteClick = async (domain: DomainWithCount) => {
        if (rowLoading) return;
        setDeletingDomain(domain);
        setProjectTargetDomains({});

        if (domain.projectCount > 0) {
            // 有项目，进入迁移流程
            setMigratingDomain(domain);
            setRowLoading({ id: domain.id, action: 'delete' });
            try {
                setDomainProjects(await getDomainProjects(domain.id));
                setIsMigrateDrawerOpen(true);
            } catch (error: any) {
                notifyError(error, '加载项目列表失败');
            } finally {
                setRowLoading(null);
            }
        } else {
            // 没有项目，直接确认删除
            setIsDeleteModalOpen(true);
        }
    };

    /**
     * 确认迁移（可能同时删除知识域）
     */
    const handleConfirmMigrate = async () => {
        const targetDomain = migratingDomain;
        if (!targetDomain || migrateSubmitting) return;
        const isDeleteFlow = !!(deletingDomain && deletingDomain.id === targetDomain.id);

        // 删除流程但域内已无项目（projectCount 过期）：直接删除，不必再要求指定目标
        if (isDeleteFlow && domainProjects.length === 0) {
            setMigrateSubmitting(true);
            try {
                await deleteDomain(targetDomain.id);
                message.success(`知识域「${targetDomain.name}」已删除`);
                finishMigrate();
            } catch (error: any) {
                notifyError(error, '删除失败，请稍后重试');
                await reloadMigrateProjects(targetDomain.id);
            } finally {
                setMigrateSubmitting(false);
            }
            return;
        }

        if (!domainProjects.some(p => projectTargetDomains[p.id] != null)) {
            message.warning('请为至少一个项目指定目标知识域');
            return;
        }
        // 删除流程必须先清空域内全部项目，否则后端会拒绝删除：提前拦住，别等迁移完才失败
        if (isDeleteFlow && !domainProjects.every(p => projectTargetDomains[p.id] != null)) {
            message.warning('删除前需要为全部项目指定目标知识域');
            return;
        }

        // 只提交仍在当前列表里的项目（失败重试时可能残留已迁走的分配）
        const migrationItems = Object.entries(projectTargetDomains)
            .filter(([projectId]) => domainProjects.some(p => p.id === Number(projectId)))
            .map(([projectId, targetDomainId]) => ({
                project_id: Number(projectId),
                target_domain_id: targetDomainId,
            }));

        setMigrateSubmitting(true);
        try {
            // 使用批量迁移 API
            const res = await migrateProjectsBatch(targetDomain.id, { items: migrationItems });

            if (isDeleteFlow) {
                // 删除流程：项目迁移完成后域内已清空，随即删除知识域
                await deleteDomain(targetDomain.id);
                message.success(`已迁移 ${res.migrated_count} 个项目并删除知识域「${targetDomain.name}」`);
            } else {
                message.success(`已迁移 ${res.migrated_count} 个项目`);
            }
            finishMigrate();
        } catch (error: any) {
            notifyError(error, '操作失败，请稍后重试');
            // 迁移可能已部分生效、删除却失败：重新拉项目列表并清空旧分配，避免用过期数据重试
            await reloadMigrateProjects(targetDomain.id);
        } finally {
            setMigrateSubmitting(false);
        }
    };

    /**
     * 确认删除（无项目时）
     */
    const handleConfirmDelete = async () => {
        if (!deletingDomain || deleteSubmitting) return;
        setDeleteSubmitting(true);
        try {
            await deleteDomain(deletingDomain.id);
            message.success('知识域已删除');

            setIsDeleteModalOpen(false);
            setDeletingDomain(null);
            loadDomains();
        } catch (error: any) {
            const detail = error.response?.data?.detail;
            // 兜底：如果后端发现域内仍有项目（计数过期/并发新增），自动转入迁移流程而不是直接报错
            if (detail && typeof detail === 'object' && detail.project_count > 0) {
                setDeletingDomain(deletingDomain);
                setMigratingDomain(deletingDomain);
                setIsDeleteModalOpen(false);
                setRowLoading({ id: deletingDomain.id, action: 'delete' });
                try {
                    const projects = await getDomainProjects(deletingDomain.id);
                    setDomainProjects(projects);
                    setIsMigrateDrawerOpen(true);
                } catch (innerError: any) {
                    notifyError(innerError, '加载项目列表失败');
                } finally {
                    setRowLoading(null);
                }
                return;
            }
            notifyError(error, '删除失败，请稍后重试');
        } finally {
            setDeleteSubmitting(false);
        }
    };

    const openCreateModal = () => {
        setEditingDomain(null);
        form.resetFields();
        setIsModalOpen(true);
    };

    const closeCreateModal = () => {
        setIsModalOpen(false);
        form.resetFields();
        setEditingDomain(null);
    };

    const closeMigrateDrawer = () => {
        if (migrateSubmitting) return;  // 提交中不允许关闭：请求还在飞，清状态会让结果无处落地
        setIsMigrateDrawerOpen(false);
        setDomainProjects([]);
        setProjectTargetDomains({});
        setMigratingDomain(null);
        // 必须一起清：否则删除流程的 deletingDomain 会泄漏到下一次「普通迁移」，
        // 抽屉标题/按钮会错误地显示成「迁移并删除」
        setDeletingDomain(null);
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '系统配置' },
        { title: '知识域管理' },
    ];

    const columns = [
        {
            title: 'ID',
            dataIndex: 'id',
            key: 'id',
            width: 80,
            render: (id: number) => <span className="tk-mono text-xs text-[#8A94AB]">#{id}</span>,
        },
        {
            title: '知识域名称',
            dataIndex: 'name',
            key: 'name',
            width: 240,
            render: (name: string) => (
                <div className="flex items-center gap-2 min-w-0">
                    <span
                        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-[13px]"
                        style={{ background: 'rgba(91,141,239,0.10)', color: '#2A57C6' }}
                    >
                        <ClusterOutlined />
                    </span>
                    <Text strong ellipsis={{ tooltip: name }} className="!m-0">{name}</Text>
                </div>
            ),
        },
        {
            title: '描述',
            dataIndex: 'description',
            key: 'description',
            // 超出宽度仍截断，但不挂浏览器原生 title 提示：描述已经在单元格里，hover 再弹同文提示是重复的
            ellipsis: { showTitle: false },
            // rc-table 对表头会无视 showTitle 强制给 th 加 title（内容就是列名「描述」，毫无信息量），
            // 用空 title 顶掉；单元格的 title 仍由上面的 showTitle: false 关掉
            onHeaderCell: () => ({ title: '' }),
            render: (description?: string) => (
                <span className="text-[13px] text-[#66738F]">{description || '—'}</span>
            ),
        },
        {
            title: '项目数量',
            key: 'projectCount',
            width: 120,
            align: 'center' as const,
            render: (_: any, record: DomainWithCount) => (
                <TkTag tone={record.projectCount > 0 ? 'brand' : 'quiet'} mono>
                    {record.projectCount} 个
                </TkTag>
            ),
        },
        {
            title: '更新时间',
            dataIndex: 'updated_at',
            key: 'updated_at',
            width: 130,
            render: (v?: string) => <span className="tk-mono text-[12px] text-[#8A94AB]">{fmtDay(v)}</span>,
        },
        {
            title: '操作',
            key: 'action',
            width: 230,
            render: (_: any, record: DomainWithCount) => (
                <Space size={0}>
                    <Button
                        size="small"
                        type="link"
                        icon={<EditOutlined />}
                        onClick={() => handleEdit(record)}
                    >
                        编辑
                    </Button>
                    <Button
                        size="small"
                        type="link"
                        icon={<SwapOutlined />}
                        loading={rowLoading?.id === record.id && rowLoading.action === 'migrate'}
                        disabled={!!rowLoading && !(rowLoading.id === record.id && rowLoading.action === 'migrate')}
                        onClick={() => handleMigrateClick(record)}
                    >
                        迁移
                    </Button>
                    <Button
                        size="small"
                        type="link"
                        danger
                        icon={<DeleteOutlined />}
                        loading={rowLoading?.id === record.id && rowLoading.action === 'delete'}
                        disabled={!!rowLoading && !(rowLoading.id === record.id && rowLoading.action === 'delete')}
                        onClick={() => handleDeleteClick(record)}
                    >
                        删除
                    </Button>
                </Space>
            ),
        },
    ];

    // 获取除当前知识域外的其他知识域选项
    const getTargetDomainOptions = () => {
        const excludeId = migratingDomain?.id;
        return domains
            .filter(d => d.id !== excludeId)
            .map(d => ({
                label: d.name,
                value: d.id,
            }));
    };

    const assignedCount = Object.keys(projectTargetDomains).length;
    // 首屏：指标带等静态结构先出（数值显示 —），表格等数据齐了再出
    const initialLoading = loading && domains.length === 0;

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <TechHero
                    compact
                    tone="light"
                    className="!mb-4"
                    title="知识域管理"
                    titleSuffix="DOMAIN REGISTRY"
                    subtitle="管理系统中的知识域分类：新建、编辑、删除，以及知识域之间的项目迁移"
                    showConstellation={domains.length > 0}
                    actions={
                        <>
                            <Button className="tk-soft-btn" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>
                                刷新
                            </Button>
                            <Button type="primary" className="tk-primary-btn" icon={<PlusOutlined />} onClick={openCreateModal}>
                                新建知识域
                            </Button>
                        </>
                    }
                />

                <StatBand
                    className="mb-3"
                    items={[
                        { label: '知识域', value: initialLoading ? '—' : domains.length, tone: 'brand', icon: <ClusterOutlined /> },
                        { label: '已归类项目', value: initialLoading ? '—' : stats.covered, tone: 'violet', icon: <ApartmentOutlined /> },
                        { label: '空知识域', value: initialLoading ? '—' : stats.empty, tone: 'quiet', icon: <SwapOutlined /> },
                        /* hint 这一行始终保留（加载期用 — 占位）：否则数据回来时指标带会长高 */
                        { label: '最近更新', value: initialLoading ? '—' : stats.latest ? stats.latest.slice(5, 10) : '—', tone: 'cyan', icon: <ClockCircleOutlined />, hint: initialLoading ? '—' : stats.latest ? `最近更新于 ${fmtDay(stats.latest)}` : '暂无更新时间' },
                    ]}
                />

                {/* 知识域列表：与「我的项目」列表同一套冷蓝表体 / 行 hover / 分页留白；
                    首屏正文由 PageLoading 替代，刷新时旧数据保留在 Table loading 遮罩下 */}
                {initialLoading ? (
                    <PageLoading hint="正在加载知识域…" />
                ) : loadFailed && domains.length === 0 ? (
                    /* 加载失败与「真的没有知识域」必须区分：否则权限/网络错误看起来像空数据 */
                    <div className="surface-card">
                        <div className="flex flex-col items-center justify-center px-4 py-14 text-center">
                            <span
                                className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px]"
                                style={{ background: 'rgba(180,35,24,0.10)', color: '#B42318' }}
                            >
                                <ExclamationCircleOutlined />
                            </span>
                            <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">知识域加载失败</div>
                            <div className="mt-1 text-[12.5px] text-[#66738F]">请检查网络或后端服务后重试</div>
                            <Button className="tk-soft-btn mt-4" icon={<ReloadOutlined />} onClick={refresh} loading={refreshing}>
                                重新加载
                            </Button>
                        </div>
                    </div>
                ) : (
                <div className="surface-card tk-table">
                    {/* 首屏由 PageLoading 整块替代；刷新时旧数据保留在 Table loading 遮罩下 */}
                    <Table
                        size="middle"
                        loading={loading}
                        columns={columns}
                        dataSource={domains}
                        rowKey="id"
                        scroll={{ x: 960 }}
                        locale={{
                            emptyText: (
                                <div className="flex flex-col items-center justify-center px-4 py-14">
                                    <div
                                        className="flex h-14 w-14 items-center justify-center rounded-2xl text-[22px] text-[#2F62D8]"
                                        style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                                    >
                                        <ClusterOutlined />
                                    </div>
                                    <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">暂无知识域</div>
                                    <div className="mt-1 text-[12.5px] text-[#66738F]">创建后可用于归类本体项目</div>
                                    <Button className="tk-soft-btn mt-4" icon={<PlusOutlined />} onClick={openCreateModal}>
                                        创建知识域
                                    </Button>
                                </div>
                            ),
                        }}
                        pagination={{
                            pageSize: 10,
                            showSizeChanger: false,
                            showTotal: (t) => `共 ${t} 个知识域`,
                        }}
                    />
                </div>
                )}
            </div>

            {/* 创建/编辑知识域弹窗 */}
            <Modal
                title={editingDomain ? '编辑知识域' : '创建知识域'}
                open={isModalOpen}
                onCancel={closeCreateModal}
                footer={null}
                width={500}
                className="tk-form-compact"
                destroyOnHidden
            >
                <Form form={form} layout="vertical" onFinish={handleCreateOrUpdateDomain} className="pt-1">
                    <Form.Item
                        name="name"
                        label="知识域名称"
                        rules={[
                            { required: true, whitespace: true, message: '请输入知识域名称' },
                            { max: 100, message: '名称不能超过 100 个字符' },
                        ]}
                    >
                        <Input maxLength={100} placeholder="例如：IT 架构、财务规范" />
                    </Form.Item>

                    <Form.Item name="description" label="描述">
                        <TextArea
                            rows={4}
                            maxLength={500}
                            placeholder="描述这个知识域的用途和范围..."
                        />
                    </Form.Item>

                    <Form.Item className="!mb-0">
                        <Space className="w-full justify-end">
                            <Button onClick={closeCreateModal} disabled={formSubmitting}>取消</Button>
                            <Button type="primary" htmlType="submit" loading={formSubmitting}>
                                {editingDomain ? '更新' : '创建'}
                            </Button>
                        </Space>
                    </Form.Item>
                </Form>
            </Modal>

            {/* 迁移项目抽屉 */}
            <Drawer
                title={
                    <span className="flex items-center gap-2">
                        <SwapOutlined className="text-[#5B8DEF]" />
                        <span>{deletingDomain ? '迁移项目后删除知识域' : '迁移项目'}</span>
                    </span>
                }
                placement="right"
                open={isMigrateDrawerOpen}
                onClose={closeMigrateDrawer}
                width={1200}
                styles={{
                    header: { padding: '10px 24px' },
                    body: { padding: '12px 24px 10px' },
                }}
                footer={
                    <div className="flex w-full flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2 text-[12.5px] text-[#66738F]">
                            <Dot tone={assignedCount > 0 ? 'brand' : 'quiet'} />
                            <span>
                                共 <span className="tk-mono font-semibold text-[#1F2A44]">{domainProjects.length}</span> 个项目，
                                已指定 <span className="tk-mono font-semibold text-[#2A57C6]">{assignedCount}</span> 个目标知识域
                            </span>
                        </div>
                        <Space>
                            <Button onClick={closeMigrateDrawer} disabled={migrateSubmitting}>取消</Button>
                            <Button
                                type="primary"
                                onClick={handleConfirmMigrate}
                                danger={!!deletingDomain}
                                loading={migrateSubmitting}
                            >
                                {deletingDomain ? '迁移并删除' : '确认迁移'}
                            </Button>
                        </Space>
                    </div>
                }
            >
                <div className="space-y-4">
                    <Note>
                        <div className="text-[13px] text-[#3D4B66]">
                            {deletingDomain ? (
                                <>
                                    知识域「<Text strong>{migratingDomain?.name}</Text>」中有 <Text strong>{migratingDomain?.projectCount}</Text> 个本体项目。
                                    删除前需要将这些项目迁移到其他知识域，迁移完成后将删除该知识域。
                                </>
                            ) : (
                                <>
                                    将知识域「<Text strong>{migratingDomain?.name}</Text>」中的 <Text strong>{migratingDomain?.projectCount}</Text> 个项目迁移到其他知识域。
                                </>
                            )}
                        </div>
                    </Note>
                    <Note tone="quiet">
                        <span className="tk-note-title">操作说明：</span>
                        为每个项目选择要迁移到的目标知识域（从左到右：本体名、类数量、实例数量、关系数量、当前知识域、目标知识域）。
                        不指定目标知识域的项目将不会被迁移。
                    </Note>

                    {/* 项目列表表格 */}
                    <div className="tk-controls">
                        <div className="surface-card tk-table !rounded-xl">
                            <Table
                                columns={[
                                    {
                                        title: '本体名',
                                        dataIndex: 'name',
                                        key: 'name',
                                        width: 200,
                                        render: (name: string, record: ProjectInDomain) => (
                                            <div className="flex items-center gap-2">
                                                <Text strong ellipsis={{ tooltip: name }} className="!m-0">{name}</Text>
                                                {record.is_published && <TkTag tone="violet">已发布</TkTag>}
                                            </div>
                                        ),
                                    },
                                    {
                                        title: '本体类数量',
                                        dataIndex: 'class_count',
                                        key: 'class_count',
                                        width: 100,
                                        align: 'center',
                                        render: (count: number) => (
                                            <span className="tk-mono text-[13px] text-[#3D4B66]">{count}</span>
                                        ),
                                    },
                                    {
                                        title: '实例数量',
                                        dataIndex: 'instance_count',
                                        key: 'instance_count',
                                        width: 100,
                                        align: 'center',
                                        render: (count: number) => (
                                            <span className="tk-mono text-[13px] text-[#3D4B66]">{count}</span>
                                        ),
                                    },
                                    {
                                        title: '关系数量',
                                        dataIndex: 'edge_count',
                                        key: 'edge_count',
                                        width: 100,
                                        align: 'center',
                                        render: (count: number) => (
                                            <span className="tk-mono text-[13px] text-[#3D4B66]">{count}</span>
                                        ),
                                    },
                                    {
                                        title: '当前知识域',
                                        dataIndex: 'current_domain_name',
                                        key: 'current_domain_name',
                                        width: 150,
                                        render: (name: string) => (
                                            <TkTag tone="quiet">{name}</TkTag>
                                        ),
                                    },
                                    {
                                        title: '目标知识域',
                                        key: 'target_domain',
                                        width: 200,
                                        render: (_: any, record: ProjectInDomain) => (
                                            <Select
                                                value={projectTargetDomains[record.id] || undefined}
                                                onChange={(value) => {
                                                    // allowClear 会传 undefined：必须删掉 key，
                                                    // 否则「已指定 N 个」虚增，提交时该字段还会被 JSON 丢弃导致 422
                                                    setProjectTargetDomains(prev => {
                                                        const next = { ...prev };
                                                        if (value == null) delete next[record.id];
                                                        else next[record.id] = value;
                                                        return next;
                                                    });
                                                }}
                                                placeholder="请选择目标知识域"
                                                className="w-full"
                                                size="small"
                                                allowClear
                                                options={getTargetDomainOptions()}
                                            />
                                        ),
                                    },
                                ]}
                                dataSource={domainProjects}
                                rowKey="id"
                                pagination={false}
                                /* 只有项目较多时才封顶高度：antd 在固定 scroll.y 下会常驻一条
                                   纵向滚动条（Windows 上只看到上下箭头），两三条数据时非常多余 */
                                scroll={domainProjects.length > 8 ? { y: 400 } : undefined}
                                size="small"
                            />
                        </div>
                    </div>
                </div>
            </Drawer>

            {/* 直接删除确认弹窗（无项目时） */}
            <Modal
                title="删除知识域"
                open={isDeleteModalOpen}
                onCancel={() => { if (!deleteSubmitting) setIsDeleteModalOpen(false); }}
                onOk={handleConfirmDelete}
                okText="确定删除"
                cancelText="取消"
                confirmLoading={deleteSubmitting}
                cancelButtonProps={{ disabled: deleteSubmitting }}
                okButtonProps={{ danger: true }}
            >
                <div className="space-y-3">
                    <div className="text-[13px] text-[#33415C]">
                        确定要删除知识域「<Text strong>{deletingDomain?.name}</Text>」吗？
                    </div>
                    <Note tone="danger" icon={<ExclamationCircleOutlined />}>
                        此操作不可恢复。
                    </Note>
                </div>
            </Modal>
        </div>
    );
};

export default DomainManagementPage;
