import React, { useEffect, useState } from 'react';
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

const MyProjectsPage: React.FC = () => {
    const navigate = useNavigate();
    const [projects, setProjects] = useState<ProjectData[]>([]);
    const [loading, setLoading] = useState(true);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [form] = Form.useForm();

    useEffect(() => {
        loadProjects();
    }, []);

    const loadProjects = async () => {
        setLoading(true);
        try {
            const data = await projectsApi.getMyProjects();
            setProjects(data);
        } catch (error: any) {
            message.error('加载项目失败');
        } finally {
            setLoading(false);
        }
    };

    const handleCreateProject = async (values: any) => {
        try {
            const newProject = await projectsApi.createProject(values);
            message.success('项目创建成功！');
            setIsModalOpen(false);
            form.resetFields();

            // 立即跳转到该项目的编辑页面
            navigate(`/ontology-builder/${newProject.id}`);
        } catch (error: any) {
            message.error('创建失败，请稍后重试');
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

            <div className="p-6">
                {loading ? (
                    <div className="flex justify-center items-center h-96">
                        <Spin size="large" />
                    </div>
                ) : projects.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96">
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
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                        {projects.map((project) => (
                            <Card
                                key={project.id}
                                hoverable
                                className="rounded-xl shadow-md hover:shadow-xl transition-shadow duration-300"
                                cover={
                                    <div className="h-40 bg-gradient-to-br from-blue-400 via-purple-400 to-pink-400 flex items-center justify-center">
                                        <div className="text-white text-6xl font-bold opacity-20">
                                            {project.name.charAt(0).toUpperCase()}
                                        </div>
                                    </div>
                                }
                                actions={[
                                    <Button
                                        type="text"
                                        icon={<EyeOutlined />}
                                        onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                    >
                                        查看
                                    </Button>,
                                    <Button
                                        type="text"
                                        icon={<EditOutlined />}
                                        onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                    >
                                        编辑
                                    </Button>,
                                    <Button
                                        type="text"
                                        danger
                                        icon={<DeleteOutlined />}
                                        onClick={() => handleDeleteProject(project.id)}
                                    >
                                        删除
                                    </Button>,
                                ]}
                            >
                                <Card.Meta
                                    title={
                                        <div className="flex items-center justify-between">
                                            <span className="truncate">{project.name}</span>
                                            {project.is_published && (
                                                <Tag color="green" className="ml-2">
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
                                                    节点: {project.graph_data?.nodes?.length || 0}
                                                </span>
                                                <span>
                                                    关系: {project.graph_data?.edges?.length || 0}
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
                        <Input placeholder="例如: 工业本体" />
                    </Form.Item>

                    <Form.Item name="description" label="项目描述">
                        <Input.TextArea
                            rows={4}
                            placeholder="简要描述这个项目的用途..."
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
        </div>
    );
};

export default MyProjectsPage;
