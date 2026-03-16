import React, { useEffect, useState } from 'react';
import { 
    Card, Button, Empty, Spin, Modal, Form, Input, message, 
    Tag, Space, Table, Select, Typography, Drawer
} from 'antd';
import {
    PlusOutlined,
    EditOutlined,
    DeleteOutlined,
    SwapOutlined,
    ExclamationCircleOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { getDomains, createDomain, updateDomain, deleteDomain, getDomainProjects, migrateProjectsBatch, KnowledgeDomain, ProjectInDomain } from '../api/domains';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';

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
            const allProjects = await projectsApi.getPublicProjects();
            
            const domainStats: DomainWithCount[] = allDomains.map(domain => ({
                ...domain,
                projectCount: allProjects.filter(p => p.domain_id === domain.id).length,
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
            
            const actionType = deletingDomain === targetDomain ? '迁移' : '迁移';
            message.success(`已${actionType} ${migrationItems.length} 个项目`);
            
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
            if (error.response?.data?.detail) {
                message.error(typeof error.response.data.detail === 'string' 
                    ? error.response.data.detail 
                    : error.response.data.detail.message);
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
        { title: '知识域管理' },
    ];

    const columns = [
        {
            title: 'ID',
            dataIndex: 'id',
            key: 'id',
            width: 80,
        },
        {
            title: '知识域名称',
            dataIndex: 'name',
            key: 'name',
            width: 200,
            render: (name: string) => <Text strong>{name}</Text>,
        },
        {
            title: '描述',
            dataIndex: 'description',
            key: 'description',
            ellipsis: true,
        },
        {
            title: '项目数量',
            key: 'projectCount',
            width: 120,
            render: (_: any, record: DomainWithCount) => (
                <Tag color="blue">{record.projectCount} 个</Tag>
            ),
        },
        {
            title: '操作',
            key: 'action',
            width: 280,
            render: (_: any, record: DomainWithCount) => (
                <Space>
                    <Button
                        type="link"
                        icon={<EditOutlined />}
                        onClick={() => handleEdit(record)}
                    >
                        编辑
                    </Button>
                    <Button
                        type="link"
                        icon={<SwapOutlined />}
                        onClick={() => handleMigrateClick(record)}
                    >
                        迁移
                    </Button>
                    <Button
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
        <div className="min-h-screen bg-gray-50">
            <Navbar breadcrumbs={breadcrumbs} />

            <div className="p-4 sm:p-6">
                {/* 顶部操作栏 */}
                <div className="mb-6 flex justify-between items-center">
                    <div>
                        <h1 className="text-2xl font-bold text-gray-800">知识域管理</h1>
                        <p className="text-gray-500 mt-1">管理系统中的知识域分类，支持增删改查和项目迁移</p>
                    </div>
                    <Button
                        type="primary"
                        icon={<PlusOutlined />}
                        onClick={openCreateModal}
                        size="large"
                    >
                        新建知识域
                    </Button>
                </div>

                {/* 知识域列表表格 */}
                <Card className="shadow-sm">
                    {loading ? (
                        <div className="flex justify-center items-center h-64">
                            <Spin size="large" />
                        </div>
                    ) : domains.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-64">
                            <Empty description="暂无知识域" />
                            <Button
                                type="primary"
                                icon={<PlusOutlined />}
                                onClick={openCreateModal}
                            >
                                创建知识域
                            </Button>
                        </div>
                    ) : (
                        <Table
                            columns={columns}
                            dataSource={domains}
                            rowKey="id"
                            pagination={{ pageSize: 10 }}
                        />
                    )}
                </Card>
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
                        <SwapOutlined className="text-blue-500" />
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
                        <div className="text-sm text-gray-500">
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
                                {deletingDomain ? '确认迁移' : '确认迁移'}
                            </Button>
                        </Space>
                    </div>
                }
            >
                <div className="space-y-4">
                    <p className="text-gray-600">
                        {deletingDomain ? (
                            <>
                                知识域「<Text strong>{migratingDomain?.name}</Text>」中有 <Text strong>{migratingDomain?.projectCount}</Text> 个已发布的本体项目。
                                删除前需要将这些项目迁移到其他知识域。
                            </>
                        ) : (
                            <>
                                将知识域「<Text strong>{migratingDomain?.name}</Text>」中的 <Text strong>{migratingDomain?.projectCount}</Text> 个项目迁移到其他知识域。
                            </>
                        )}
                    </p>
                    <div className="bg-blue-50 p-3 rounded border border-blue-100">
                        <p className="text-sm text-blue-800">
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
                                                <Tag color="green">已发布</Tag>
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
                                        <Tag color="blue">{count}</Tag>
                                    ),
                                },
                                {
                                    title: '实例数量',
                                    dataIndex: 'instance_count',
                                    key: 'instance_count',
                                    width: 100,
                                    align: 'center',
                                    render: (count: number) => (
                                        <Tag color="green">{count}</Tag>
                                    ),
                                },
                                {
                                    title: '关系数量',
                                    dataIndex: 'edge_count',
                                    key: 'edge_count',
                                    width: 100,
                                    align: 'center',
                                    render: (count: number) => (
                                        <Tag color="purple">{count}</Tag>
                                    ),
                                },
                                {
                                    title: '当前知识域',
                                    dataIndex: 'current_domain_name',
                                    key: 'current_domain_name',
                                    width: 150,
                                    render: (name: string) => (
                                        <Tag color="orange">{name}</Tag>
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
                            size="middle"
                        />
                    </div>
                </div>
            </Drawer>

            {/* 直接删除确认弹窗（无项目时） */}
            <Modal
                title={
                    <div className="flex items-center gap-2 text-red-500">
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
                <p className="text-gray-500 mt-2">此操作不可恢复！</p>
            </Modal>
        </div>
    );
};

export default DomainManagementPage;
