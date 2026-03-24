import React, { useEffect, useState, useRef } from 'react';
import { Card, Button, Empty, Spin, Modal, Form, Input, message, Tag, Space } from 'antd';
import {
    PlusOutlined,
    EditOutlined,
    DeleteOutlined,
    EyeOutlined,
    CloudUploadOutlined,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import KnowledgeDomainSelector from '../components/KnowledgeDomainSelector';

const MyProjectsPage: React.FC = () => {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [loading, setLoading] = useState(true);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isEditModalOpen, setIsEditModalOpen] = useState(false);
    const [editingProject, setEditingProject] = useState<ProjectData | null>(null);
    const [form] = Form.useForm();
    const [editForm] = Form.useForm();
    const [selectedDomainId, setSelectedDomainId] = useState<number | undefined>(undefined);
    const [selectedDomainName, setSelectedDomainName] = useState<string | undefined>(undefined);
    const scrollPositionRef = useRef<number>(0);

    useEffect(() => {
        loadProjects();
    }, []);

    const loadProjects = () => {
        setLoading(true);
        projectsApi.getMyProjects()
            .then((data) => {
                setProjects(data);
            })
            .catch((error: any) => {
                message.error('加载项目失败');
            })
            .finally(() => {
                setLoading(false);
            });
    };

    const handleCreateProject = async (values: any) => {
        if (!selectedDomainId) {
            message.warning('请选择知识域');
            return;
        }
        try {
            const newProject = await projectsApi.createProject({
                name: values.name,
                description: values.description,
                domain_id: selectedDomainId,
                domain_name: selectedDomainName,
            });
            message.success('项目创建成功！');
            setIsModalOpen(false);
            form.resetFields();
            setSelectedDomainId(undefined);
            setSelectedDomainName(undefined);

            // 立即跳转到该项目的编辑页面
            navigate(`/ontology-builder/${newProject.id}`);
        } catch (error: any) {
            message.error('创建失败，请稍后重试');
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
        // 保存当前滚动位置到 ref
        scrollPositionRef.current = window.scrollY;
        try {
            await projectsApi.updateProject(editingProject.id, {
                name: values.name,
                description: values.description,
            });
            message.success('项目信息更新成功！');
            setIsEditModalOpen(false);
            setEditingProject(null);
            // 直接更新本地状态而不是重新加载整个列表
            setProjects((prevProjects) =>
                prevProjects.map((project) =>
                    project.id === editingProject.id
                        ? { ...project, name: values.name, description: values.description }
                        : project
                )
            );
            // 恢复滚动位置
            setTimeout(() => {
                window.scrollTo(0, scrollPositionRef.current);
            }, 50);
        } catch (error: any) {
            message.error('更新失败，请稍后重试');
        }
    };

    const handleDeleteProject = (projectId: number) => {
        Modal.confirm({
            title: '确认删除',
            content: '删除后无法恢复，确定要删除这个项目吗？',
            okText: '确定',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: async () => {
                try {
                    await projectsApi.deleteProject(projectId);
                    message.success('删除成功');
                    loadProjects();
                } catch (error) {
                    message.error('删除失败');
                }
            },
        });
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目' },
    ];

    return (
        <div className="min-h-screen bg-gray-50">
            <Navbar
                breadcrumbs={breadcrumbs}
                showCreateButton
                onCreateProject={() => setIsModalOpen(true)}
            />

            <div className="p-4 sm:p-6">
                {loading ? (
                    <div className="flex justify-center items-center h-96">
                        <Spin size="large" />
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
                                className="bg-blue-600"
                            >
                                创建项目
                            </Button>
                        </Empty>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                        {projects.map((project) => (
                            <Card
                                key={project.id}
                                hoverable
                                className="rounded-xl shadow-md hover:shadow-xl transition-shadow duration-300"
                                cover={
                                    <div className="h-32 sm:h-40 bg-gradient-to-br from-blue-400 via-purple-400 to-pink-400 flex items-center justify-center">
                                        <div className="text-white text-4xl sm:text-6xl font-bold opacity-20">
                                            {project.name.charAt(0).toUpperCase()}
                                        </div>
                                    </div>
                                }
                                actions={[
                                    <Button
                                        type="text"
                                        icon={<EyeOutlined />}
                                        onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                        className="text-xs sm:text-sm"
                                    >
                                        <span className="hidden sm:inline">查看</span>
                                    </Button>,
                                    <Button
                                        type="text"
                                        icon={<EditOutlined />}
                                        onClick={() => handleEditProject(project)}
                                        className="text-xs sm:text-sm"
                                    >
                                        <span className="hidden sm:inline">信息</span>
                                    </Button>,
                                    <Button
                                        type="text"
                                        icon={<EditOutlined />}
                                        onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                        className="text-xs sm:text-sm"
                                    >
                                        <span className="hidden sm:inline">编辑</span>
                                    </Button>,
                                    <Button
                                        type="text"
                                        danger
                                        icon={<DeleteOutlined />}
                                        onClick={() => handleDeleteProject(project.id)}
                                        className="text-xs sm:text-sm"
                                    >
                                        <span className="hidden sm:inline">删除</span>
                                    </Button>,
                                ]}
                            >
                                <Card.Meta
                                    title={
                                        <div className="flex items-center justify-between">
                                            <span className="truncate">{project.name}</span>
                                            {project.is_published && (
                                                <Tag color="green" className="ml-2 flex-shrink-0">
                                                    已发布
                                                </Tag>
                                            )}
                                        </div>
                                    }
                                    description={
                                        <div className="text-gray-500 text-sm">
                                            <div className="truncate mb-2">
                                                {project.description || '暂无描述'}
                                            </div>
                                            <div className="flex items-center justify-between text-xs">
                                                <span>
                                                    节点：{project.graph_data?.nodes?.length || 0}
                                                </span>
                                                <span>
                                                    关系：{project.graph_data?.edges?.length || 0}
                                                </span>
                                            </div>
                                        </div>
                                    }
                                />
                            </Card>
                        ))}
                    </div>
                )}
            </div>

            {/* 创建项目弹窗 */}
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

                    <Form.Item>
                        <Space className="w-full justify-end">
                            <Button onClick={() => setIsModalOpen(false)}>取消</Button>
                            <Button type="primary" htmlType="submit" className="bg-blue-600">
                                创建
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
            >
                <Form form={editForm} layout="vertical" onFinish={handleUpdateProject}>
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

                    <Form.Item>
                        <Space className="w-full justify-end">
                            <Button onClick={() => {
                                setIsEditModalOpen(false);
                                setEditingProject(null);
                                editForm.resetFields();
                            }}>取消</Button>
                            <Button type="primary" htmlType="submit" className="bg-blue-600">
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
