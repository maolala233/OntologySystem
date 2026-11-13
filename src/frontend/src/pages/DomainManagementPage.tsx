import React, { useEffect, useState } from 'react';
import { PROJECT_STATUS, PUBLISHED_TAG } from '../shared/projectStatus';
import { Button, Spin, Modal, Form, Input, Tag, Space, Table, Select, Typography, Drawer } from 'antd';
import { message } from '../shared/feedback';
import {
    PlusOutlined,
    EditOutlined,
    DeleteOutlined,
    SwapOutlined,
    ExclamationCircleOutlined,
    ClusterOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import PageHeader from '../components/Layout/PageHeader';
import { getDomains, createDomain, updateDomain, deleteDomain, getDomainProjects, migrateProjectsBatch, KnowledgeDomain, ProjectInDomain } from '../api/domains';

const { TextArea } = Input;
const { Text } = Typography;

interface DomainWithCount extends KnowledgeDomain {
    projectCount: number;
}

const DomainManagementPage: React.FC = () => {
    const [domains, setDomains] = useState<DomainWithCount[]>([]);
    const [loading, setLoading] = useState(true);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
    const [isMigrateDrawerOpen, setIsMigrateDrawerOpen] = useState(false);
    const [editingDomain, setEditingDomain] = useState<KnowledgeDomain | null>(null);
    const [deletingDomain, setDeletingDomain] = useState<DomainWithCount | null>(null);
    const [migratingDomain, setMigratingDomain] = useState<DomainWithCount | null>(null);
    const [domainProjects, setDomainProjects] = useState<ProjectInDomain[]>([]);
    const [projectTargetDomains, setProjectTargetDomains] = useState<Record<number, number>>({}); // projectId -> targetDomainId
    const [form] = Form.useForm();

    useEffect(() => {
        loadDomains();
    }, []);

    const loadDomains = async () => {
        setLoading(true);
        try {
            const allDomains = await getDomains();

            const domainStats: DomainWithCount[] = allDomains.map(domain => ({
                ...domain,
                // 后端返回的项目总数（含未发布），与删除校验口径一致
                projectCount: domain.project_count ?? 0,
            }));

            setDomains(domainStats);
        } catch (error: any) {
            message.error('加载知识域失败');
        } finally {
            setLoading(false);
        }
    };

    const handleCreateOrUpdateDomain = async (values: any) => {
        try {
            if (editingDomain) {
                await updateDomain(editingDomain.id, values);
                message.success('知识域更新成功！');
            } else {
                await createDomain(values);
                message.success('知识域创建成功！');
            }
            setIsModalOpen(false);
            form.resetFields();
            setEditingDomain(null);
            loadDomains();
        } catch (error: any) {
            message.error('操作失败，请稍后重试');
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
        setMigratingDomain(domain);
        setProjectTargetDomains({});
        
        if (domain.projectCount > 0) {
            // 加载该知识域下的项目
            try {
                const projects = await getDomainProjects(domain.id);
                setDomainProjects(projects);
                setIsMigrateDrawerOpen(true);
            } catch (error: any) {
                message.error('加载项目列表失败');
            }
        } else {
            message.info('该知识域下没有项目，无需迁移');
        }
    };

    /**
     * 点击删除按钮
     * 如果有项目，自动进入迁移流程；否则直接确认删除
     */
    const handleDeleteClick = async (domain: DomainWithCount) => {
        setDeletingDomain(domain);
        setProjectTargetDomains({});
        
        if (domain.projectCount > 0) {
            // 有项目，进入迁移流程
            setMigratingDomain(domain);
            try {
                const projects = await getDomainProjects(domain.id);
                setDomainProjects(projects);
                setIsMigrateDrawerOpen(true);
            } catch (error: any) {
                message.error('加载项目列表失败');
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
        if (!targetDomain) return;

        try {
            // 检查是否有项目指定了目标知识域
            const hasTargetDomains = Object.keys(projectTargetDomains).length > 0;
            
            if (!hasTargetDomains) {
                message.warning('请为至少一个项目指定目标知识域');
                return;
            }
            
            // 使用批量迁移 API
            const migrationItems = Object.entries(projectTargetDomains).map(([projectId, targetDomainId]) => ({
                project_id: Number(projectId),
                target_domain_id: targetDomainId,
            }));
            
            await migrateProjectsBatch(targetDomain.id, {
                items: migrationItems,
            });
            
            const isDeleteFlow = !!(deletingDomain && deletingDomain.id === targetDomain.id);
            
            if (isDeleteFlow) {
                // 删除流程：项目迁移完成后域内已清空，随即删除知识域
                await deleteDomain(targetDomain.id);
                message.success(`已迁移 ${migrationItems.length} 个项目并删除知识域「${targetDomain.name}」`);
            } else {
                message.success(`已迁移 ${migrationItems.length} 个项目`);
            }
            
            setIsMigrateDrawerOpen(false);
            setIsDeleteModalOpen(false);
            setMigratingDomain(null);
            setDeletingDomain(null);
            loadDomains();
        } catch (error: any) {
            if (error.response?.data?.detail) {
                message.error(typeof error.response.data.detail === 'string' 
                    ? error.response.data.detail 
                    : error.response.data.detail.message);
            } else {
                message.error('操作失败，请稍后重试');
            }
        }
    };

    /**
     * 确认删除（无项目时）
     */
    const handleConfirmDelete = async () => {
        if (!deletingDomain) return;

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
                try {
                    const projects = await getDomainProjects(deletingDomain.id);
                    setDomainProjects(projects);
                    setIsMigrateDrawerOpen(true);
                } catch (innerError: any) {
                    message.error('加载项目列表失败');
                }
                return;
            }
            if (detail) {
                message.error(typeof detail === 'string' 
                    ? detail 
                    : detail.message);
            } else {
                message.error('删除失败，请稍后重试');
            }
        }
    };

    const openCreateModal = () => {
        setEditingDomain(null);
        form.resetFields();
        setIsModalOpen(true);
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
            render: (id: number) => <span className="font-mono text-xs text-[#8A94AB]">#{id}</span>,
        },
        {
            title: '知识域名称',
            dataIndex: 'name',
            key: 'name',
            width: 240,
            render: (name: string) => <Text strong>{name}</Text>,
        },
        {
            title: '描述',
            dataIndex: 'description',
            key: 'description',
            ellipsis: true,
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
                <Tag className="!m-0" style={{ background: 'rgba(91,141,239,0.12)', color: '#2A57C6', borderColor: 'rgba(91,141,239,0.28)' }}>{record.projectCount} 个</Tag>
            ),
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
                        onClick={() => handleMigrateClick(record)}
                    >
                        迁移
                    </Button>
                    <Button
                        size="small"
                        type="link"
                        danger
                        icon={<DeleteOutlined />}
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

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6 flex-1 min-h-0 overflow-y-auto scrollbar-stable">
                <PageHeader
                    icon={<ClusterOutlined />}
                    title="知识域管理"
                    subtitle="管理系统中的知识域分类：新建、编辑、删除，以及知识域之间的项目迁移"
                    extra={
                        <Button type="primary" icon={<PlusOutlined />} onClick={openCreateModal}>
                            新建知识域
                        </Button>
                    }
                />

                {/* 知识域列表：与「我的项目」列表同一套浮起面板 / 行高 / 分页留白 */}
                <div className="surface-card">
                    {loading ? (
                        <div className="flex justify-center items-center h-64">
                            <Spin size="large" />
                        </div>
                    ) : domains.length === 0 ? (
                        <div className="flex flex-col items-center justify-center px-4 py-16">
                            <div
                                className="w-14 h-14 rounded-2xl flex items-center justify-center text-[22px] text-[#2F62D8]"
                                style={{ background: 'linear-gradient(135deg, rgba(91,141,239,0.16), rgba(139,92,246,0.10))' }}
                            >
                                <ClusterOutlined />
                            </div>
                            <div className="mt-3 text-[14px] font-semibold text-[#0F172A]">暂无知识域</div>
                            <div className="mt-1 text-[12.5px] text-[#66738F]">创建后可用于归类本体项目</div>
                            <Button
                                type="primary"
                                icon={<PlusOutlined />}
                                className="mt-4"
                                onClick={openCreateModal}
                            >
                                创建知识域
                            </Button>
                        </div>
                    ) : (
                        <Table
                            size="middle"
                            columns={columns}
                            dataSource={domains}
                            rowKey="id"
                            scroll={{ x: 860 }}
                            pagination={{
                                pageSize: 10,
                                showSizeChanger: false,
                                showTotal: (t) => `共 ${t} 个知识域`,
                            }}
                        />
                    )}
                </div>
            </div>

            {/* 创建/编辑知识域弹窗 */}
            <Modal
                title={editingDomain ? '编辑知识域' : '创建知识域'}
                open={isModalOpen}
                onCancel={() => {
                    setIsModalOpen(false);
                    form.resetFields();
                    setEditingDomain(null);
                }}
                footer={null}
                width={500}
            >
                <Form form={form} layout="vertical" onFinish={handleCreateOrUpdateDomain}>
                    <Form.Item
                        name="name"
                        label="知识域名称"
                        rules={[{ required: true, message: '请输入知识域名称' }]}
                    >
                        <Input placeholder="例如：IT 架构、财务规范" />
                    </Form.Item>

                    <Form.Item name="description" label="描述">
                        <TextArea
                            rows={4}
                            placeholder="描述这个知识域的用途和范围..."
                        />
                    </Form.Item>

                    <Form.Item>
                        <Space className="w-full justify-end">
                            <Button onClick={() => {
                                setIsModalOpen(false);
                                form.resetFields();
                                setEditingDomain(null);
                            }}>
                                取消
                            </Button>
                            <Button type="primary" htmlType="submit">
                                {editingDomain ? '更新' : '创建'}
                            </Button>
                        </Space>
                    </Form.Item>
                </Form>
            </Modal>

            {/* 迁移项目抽屉 */}
            <Drawer
                title={
                    <div className="flex items-center gap-2">
                        <SwapOutlined className="text-[#5B8DEF]" />
                        <span>{deletingDomain ? '迁移项目后删除知识域' : '迁移项目'}</span>
                    </div>
                }
                placement="right"
                open={isMigrateDrawerOpen}
                onClose={() => {
                    setIsMigrateDrawerOpen(false);
                    setDomainProjects([]);
                    setProjectTargetDomains({});
                    setMigratingDomain(null);
                }}
                width={1200}
                footer={
                    <div className="flex justify-between items-center w-full">
                        <div className="text-sm text-[#66738F]">
                            共 {domainProjects.length} 个项目，
                            已指定 {Object.keys(projectTargetDomains).length} 个项目的目标知识域
                        </div>
                        <Space>
                            <Button onClick={() => {
                                setIsMigrateDrawerOpen(false);
                                setDomainProjects([]);
                                setProjectTargetDomains({});
                                setMigratingDomain(null);
                            }}>
                                取消
                            </Button>
                            <Button 
                                type="primary" 
                                onClick={handleConfirmMigrate}
                                danger={!!deletingDomain}
                            >
                                {deletingDomain ? '迁移并删除' : '确认迁移'}
                            </Button>
                        </Space>
                    </div>
                }
            >
                <div className="space-y-4">
                    <p className="text-[#52607A]">
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
                    </p>
                    <div className="rounded-lg border border-[#DCE7F7] p-3" style={{ background: 'rgba(91,141,239,0.06)' }}>
                        <p className="text-sm text-[#2A57C6]">
                            <strong>操作说明：</strong>为每个项目选择要迁移到的目标知识域（从左到右：本体名、类数量、实例数量、关系数量、当前知识域、目标知识域）。
                            不指定目标知识域的项目将不会被迁移。
                        </p>
                    </div>

                    {/* 项目列表表格 */}
                    <div className="border rounded-lg overflow-hidden">
                        <Table
                            columns={[
                                {
                                    title: '本体名',
                                    dataIndex: 'name',
                                    key: 'name',
                                    width: 200,
                                    render: (name: string, record: ProjectInDomain) => (
                                        <div className="flex items-center gap-2">
                                            <Text strong>{name}</Text>
                                            {record.is_published && (
                                                <Tag color={PUBLISHED_TAG.color}>{PUBLISHED_TAG.text}</Tag>
                                            )}
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
                                        <Tag className="!m-0">{name}</Tag>
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
                                                setProjectTargetDomains(prev => ({
                                                    ...prev,
                                                    [record.id]: value,
                                                }));
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
                            scroll={{ y: 400 }}
                            size="small"
                        />
                    </div>
                </div>
            </Drawer>

            {/* 直接删除确认弹窗（无项目时） */}
            <Modal
                title={
                    <div className="flex items-center gap-2 text-[#B42318]">
                        <ExclamationCircleOutlined className="text-xl" />
                        <span>确认删除</span>
                    </div>
                }
                open={isDeleteModalOpen}
                onCancel={() => setIsDeleteModalOpen(false)}
                onOk={handleConfirmDelete}
                okText="确定删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
            >
                <p>
                    确定要删除知识域「<Text strong>{deletingDomain?.name}</Text>」吗？
                </p>
                <p className="text-[#66738F] mt-2">此操作不可恢复！</p>
            </Modal>
        </div>
    );
};

export default DomainManagementPage;
