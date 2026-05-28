import React, { useEffect, useState, useRef, useMemo } from 'react';
import { Card, Button, Empty, Spin, Modal, Form, Input, message, Tag, Space, Checkbox, Select, InputNumber } from 'antd';
import {
    PlusOutlined,
    EditOutlined,
    DeleteOutlined,
    EyeOutlined,
    CloudUploadOutlined,
    SearchOutlined,
    SortAscendingOutlined,
    DeleteFilled,
} from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import Navbar from '../components/Layout/Navbar';
import { projectsApi } from '../api/projects';
import { ProjectData } from '../types/ontology';
import KnowledgeDomainSelector from '../components/KnowledgeDomainSelector';

type SortField = 'created_at' | 'name' | 'node_count';
type SortOrder = 'asc' | 'desc';

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

    const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
    const [searchText, setSearchText] = useState('');
    const [sortField, setSortField] = useState<SortField>('created_at');
    const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

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

    const handleBatchDelete = () => {
        if (selectedIds.size === 0) {
            message.warning('请先选择要删除的项目');
            return;
        }
        Modal.confirm({
            title: '批量删除确认',
            content: `确定要删除选中的 ${selectedIds.size} 个项目吗？删除后无法恢复。`,
            okText: '确定删除',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: async () => {
                let successCount = 0;
                let failCount = 0;
                for (const projectId of selectedIds) {
                    try {
                        await projectsApi.deleteProject(projectId);
                        successCount++;
                    } catch {
                        failCount++;
                    }
                }
                if (failCount === 0) {
                    message.success(`成功删除 ${successCount} 个项目`);
                } else {
                    message.warning(`成功删除 ${successCount} 个项目，${failCount} 个删除失败`);
                }
                setSelectedIds(new Set());
                loadProjects();
            },
        });
    };

    const toggleSelect = (projectId: number) => {
        setSelectedIds(prev => {
            const next = new Set(prev);
            if (next.has(projectId)) {
                next.delete(projectId);
            } else {
                next.add(projectId);
            }
            return next;
        });
    };

    const toggleSelectAll = () => {
        if (selectedIds.size === filteredAndSortedProjects.length && filteredAndSortedProjects.length > 0) {
            setSelectedIds(new Set());
        } else {
            setSelectedIds(new Set(filteredAndSortedProjects.map(p => p.id)));
        }
    };

    const handleSortChange = (value: SortField) => {
        if (value === sortField) {
            setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(value);
            setSortOrder('desc');
        }
    };

    const filteredAndSortedProjects = useMemo(() => {
        let result = [...projects];

        if (searchText.trim()) {
            const keyword = searchText.trim().toLowerCase();
            result = result.filter(p => p.name.toLowerCase().includes(keyword));
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
    }, [projects, searchText, sortField, sortOrder]);

    const sortOptions = [
        { label: '创建时间', value: 'created_at' },
        { label: '项目名称', value: 'name' },
        { label: '节点数量', value: 'node_count' },
    ];

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
                {projects.length > 0 && (
                    <div className="mb-4 flex flex-wrap items-center gap-3">
                        <Input
                            prefix={<SearchOutlined className="text-gray-400" />}
                            placeholder="搜索项目名称..."
                            value={searchText}
                            onChange={e => setSearchText(e.target.value)}
                            allowClear
                            style={{ width: 260 }}
                        />
                        <div className="flex items-center gap-2">
                            <span className="text-gray-500 text-sm">排序：</span>
                            <Select
                                value={sortField}
                                onChange={handleSortChange}
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
                    </div>
                )}

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
                ) : filteredAndSortedProjects.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-96 px-4">
                        <Empty
                            description="没有找到匹配的项目"
                            image={Empty.PRESENTED_IMAGE_SIMPLE}
                        >
                            <Button onClick={() => setSearchText('')}>清除搜索</Button>
                        </Empty>
                    </div>
                ) : (
                    <>
                        {filteredAndSortedProjects.length > 0 && (
                            <div className="mb-3 flex items-center gap-3">
                                <Checkbox
                                    checked={selectedIds.size === filteredAndSortedProjects.length && filteredAndSortedProjects.length > 0}
                                    indeterminate={selectedIds.size > 0 && selectedIds.size < filteredAndSortedProjects.length}
                                    onChange={toggleSelectAll}
                                >
                                    <span className="text-sm text-gray-500">全选</span>
                                </Checkbox>
                                <span className="text-xs text-gray-400">共 {filteredAndSortedProjects.length} 个项目</span>
                                {selectedIds.size > 0 && (
                                    <>
                                        <div className="flex-1" />
                                        <span className="text-sm text-blue-600 font-medium">已选 {selectedIds.size} 项</span>
                                        <Button
                                            danger
                                            size="small"
                                            icon={<DeleteFilled />}
                                            onClick={handleBatchDelete}
                                        >
                                            批量删除
                                        </Button>
                                        <Button
                                            size="small"
                                            onClick={() => setSelectedIds(new Set())}
                                        >
                                            取消选择
                                        </Button>
                                    </>
                                )}
                            </div>
                        )}
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 sm:gap-6">
                            {filteredAndSortedProjects.map((project) => {
                                const isSelected = selectedIds.has(project.id);
                                return (
                                    <div key={project.id} className="relative">
                                        <div
                                            className={`absolute top-3 left-3 z-10 w-6 h-6 flex items-center justify-center rounded-full transition-all duration-200 ${isSelected ? 'bg-blue-500' : 'bg-white/70 hover:bg-white shadow-sm'}`}
                                            onClick={(e) => { e.stopPropagation(); toggleSelect(project.id); }}
                                        >
                                            <Checkbox checked={isSelected} className="[&_.ant-checkbox-inner]:border-gray-400" />
                                        </div>
                                        <Card
                                            hoverable
                                            className={`rounded-xl shadow-md hover:shadow-xl transition-all duration-300 cursor-pointer ${isSelected ? 'ring-2 ring-blue-500 ring-offset-1' : ''}`}
                                            cover={
                                                <div
                                                    className="h-32 sm:h-40 bg-gradient-to-br from-blue-400 via-purple-400 to-pink-400 flex items-center justify-center"
                                                    onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                                >
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
                                                    onClick={(e) => { e.stopPropagation(); handleDeleteProject(project.id); }}
                                                    className="text-xs sm:text-sm"
                                                >
                                                    <span className="hidden sm:inline">删除</span>
                                                </Button>,
                                            ]}
                                        >
                                            <Card.Meta
                                                title={
                                                    <div className="flex items-center justify-between">
                                                        <span
                                                            className="truncate cursor-pointer hover:text-blue-500 transition-colors"
                                                            onClick={() => navigate(`/ontology-builder/${project.id}`)}
                                                        >
                                                            {project.name}
                                                        </span>
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
                                                        {project.created_at && (
                                                            <div className="text-xs text-gray-400 mt-1">
                                                                {new Date(project.created_at).toLocaleDateString('zh-CN')}
                                                            </div>
                                                        )}
                                                    </div>
                                                }
                                            />
                                        </Card>
                                    </div>
                                );
                            })}
                        </div>
                    </>
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
