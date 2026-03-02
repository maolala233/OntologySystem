import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Drawer,
    Form,
    Input,
    Button,
    message,
    Space,
    Upload,
    Modal,
    Spin,
    Tooltip,
    Select,
    Tag,
    Divider,
    Switch,
    Tree,
    Collapse,
} from 'antd';
import {
    SaveOutlined,
    CloudUploadOutlined,
    PlusOutlined,
    DeleteOutlined,
    CloudServerOutlined,
    ArrowLeftOutlined,
    FileTextOutlined,
    InfoCircleOutlined,
    DownloadOutlined,
    EyeOutlined,
    MinusCircleOutlined,
    EditOutlined,
    TagsOutlined,
    LinkOutlined,
    DatabaseOutlined,
    ApiOutlined,
    ClusterOutlined,
    UnorderedListOutlined,
    AppstoreOutlined,
    RightOutlined,
    LeftOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import { OntologyNode, OntologyEdge } from '../types/ontology';
import { projectsApi } from '../api/projects';
import { getLayoutedElements } from '../utils/layoutUtils';
import { systemApi } from '../api/system';
import apiClient from '../api/client';
import D3ForceGraph from '../components/OntologyGraph/D3ForceGraph';

const { TreeNode } = Tree;
const { Panel } = Collapse;

const OntologyBuilderPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();

    // 强制初始化状态
    useEffect(() => {
        if (projectId && projectId.trim() !== '') {
            setIsCreatingProject(false);
        }
    }, [projectId]);

    const [nodes, setNodes] = useState<any[]>([]);
    const [edges, setEdges] = useState<any[]>([]);
    const [selectedElement, setSelectedElement] = useState<OntologyNode | OntologyEdge | null>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    const [isRuleModalOpen, setIsRuleModalOpen] = useState(false);
    const [pendingFile, setPendingFile] = useState<File | null>(null);
    const [loading, setLoading] = useState(false);
    const [projectName, setProjectName] = useState('');
    const [isPublished, setIsPublished] = useState(false);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [lastSavedNodes, setLastSavedNodes] = useState<any[]>([]);
    const [lastSavedEdges, setLastSavedEdges] = useState<any[]>([]);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [isAddRelationModalOpen, setIsAddRelationModalOpen] = useState(false);
    const [isCreatingProject, setIsCreatingProject] = useState(false);
    const [createProjectForm] = Form.useForm();
    const [relationForm] = Form.useForm();
    const [form] = Form.useForm();
    const [ruleForm] = Form.useForm();
    const [customRelationType, setCustomRelationType] = useState('');
    const [isConfigModalOpen, setIsConfigModalOpen] = useState(false);
    const [configForm] = Form.useForm();
    const [isAdmin, setIsAdmin] = useState(false);
    const [isAddInstanceModalOpen, setIsAddInstanceModalOpen] = useState(false);
    const [addInstanceForm] = Form.useForm();
    const [isNewNode, setIsNewNode] = useState(false);
    const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);

    // 左侧列表展开状态
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);

    // 测试连通性状态
    const [testingLLM, setTestingLLM] = useState(false);
    const [testingNeo4J, setTestingNeo4J] = useState(false);
    const [testingEmbedding, setTestingEmbedding] = useState(false);
    const [testingMilvus, setTestingMilvus] = useState(false);

    useEffect(() => {
        // 优先显示编辑界面，只有当明确没有 projectId 时才显示创建表单
        const hasValidProjectId = projectId && typeof projectId === 'string' && projectId.trim() !== '';

        if (hasValidProjectId) {
            setIsCreatingProject(false);
            loadProject();
        } else {
            setIsCreatingProject(true);
        }
    }, [projectId]);

    // 监听节点和边的变化
    useEffect(() => {
        if (projectId) {
            const hasChanged = JSON.stringify(nodes) !== JSON.stringify(lastSavedNodes) ||
                JSON.stringify(edges) !== JSON.stringify(lastSavedEdges);
            setHasUnsavedChanges(hasChanged);
        }
    }, [nodes, edges, lastSavedNodes, lastSavedEdges, projectId]);

    // 页面卸载前的确认
    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (hasUnsavedChanges) {
                e.preventDefault();
                e.returnValue = '您有未保存的更改，确定要离开吗？';
            }
        };

        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => {
            window.removeEventListener('beforeunload', handleBeforeUnload);
        };
    }, [hasUnsavedChanges]);

    const loadProject = async () => {
        setLoading(true);
        try {
            const project = await projectsApi.getProject(Number(projectId));
            setProjectName(project.name);
            setIsPublished(project.is_published);

            // 保持空白画布，不初始化默认根类节点
            if (!project.graph_data || !project.graph_data.nodes || project.graph_data.nodes.length === 0) {
                setNodes([]);
                setLastSavedNodes([]);
            } else {
                if (project.graph_data?.nodes) {
                    setNodes(project.graph_data.nodes);
                    setLastSavedNodes(project.graph_data.nodes);
                }
                if (project.graph_data?.edges) {
                    setEdges(project.graph_data.edges);
                    setLastSavedEdges(project.graph_data.edges);
                }
            }

            // 获取用户信息判断是否为 admin
            const userStr = localStorage.getItem('user');
            if (userStr) {
                const user = JSON.parse(userStr);
                setIsAdmin(user.username === 'admin');
            }
        } catch (error: any) {
            message.error('加载项目失败');
            navigate('/my-projects');
        } finally {
            setLoading(false);
        }
    };

    // 点击节点
    const onNodeClick = (node: any) => {
        setSelectedElement(node);
        setIsDrawerOpen(true);

        const propsObj = node.data?.properties || {};
        const propsArray = Object.entries(propsObj).map(([key, value]) => ({
            name: key,
            value: String(value)
        }));

        form.setFieldsValue({
            label: node.data?.label || '',
            type: node.data?.type || 'owl:Class',
            properties: propsArray
        });
    };

    // 点击边
    const onEdgeClick = (edge: any) => {
        setSelectedElement(edge);
        setIsDrawerOpen(true);

        form.setFieldsValue({
            label: edge.data?.label || edge.data?.relation || '',
            relation: edge.data?.relation || edge.data?.label || '',
        });
    };

    // 保存属性修改
    const handleSaveProperties = (values: any) => {
        if (!selectedElement) return;

        const { label, type, properties, relation } = values;

        // 判断是节点还是边
        const isNode = 'position' in selectedElement;

        if (isNode) {
            // 节点属性编辑
            // 将 properties 数组转换回对象
            const propsObj: Record<string, any> = {};
            if (Array.isArray(properties)) {
                properties.forEach((p: any) => {
                    if (p && p.name) {
                        propsObj[p.name] = p.value;
                    }
                });
            }

            setNodes((nds) =>
                nds.map((node) => {
                    if (node.id === selectedElement.id) {
                        return {
                            ...node,
                            data: {
                                ...node.data,
                                label: label,
                                type: type,
                                properties: propsObj,
                            },
                        };
                    }
                    return node;
                })
            );
            
            // 如果是新节点保存后，清除高亮状态
            if (isNewNode) {
                setIsNewNode(false);
                setHighlightNodeId(null);
            }
        } else {
            // 边属性编辑
            setEdges((eds) =>
                eds.map((edge) => {
                    if (edge.id === selectedElement.id) {
                        return {
                            ...edge,
                            data: {
                                ...edge.data,
                                label: label || relation,
                                relation: relation || label,
                            },
                        };
                    }
                    return edge;
                })
            );
        }

        setIsDrawerOpen(false);
        message.success('属性已更新');
    };

    // 新增类
    const addNewClass = () => {
        const newNode: OntologyNode = {
            id: `node_${Date.now()}`,
            type: 'custom',
            position: { x: window.innerWidth / 2 - 200, y: window.innerHeight / 2 - 200 },
            data: {
                label: '新类',
                type: 'owl:Class',
                properties: {}
            },
        };
        setNodes((nds) => nds.concat(newNode));
        setIsNewNode(true);
        setHighlightNodeId(newNode.id);
        
        // 自动选中并打开编辑抽屉
        setSelectedElement(newNode);
        setIsDrawerOpen(true);
        form.setFieldsValue({
            label: '新类',
            type: 'owl:Class',
            properties: []
        });
        message.success('已添加新类，请编辑节点名称');
    };

    // 新增实例 - 打开选择类对话框
    const addNewInstance = () => {
        const classNodes = nodes.filter(node => node.data?.type === 'owl:Class');
        if (classNodes.length === 0) {
            message.warning('请先创建至少一个类，然后才能添加实例');
            return;
        }
        addInstanceForm.resetFields();
        setIsAddInstanceModalOpen(true);
    };

    // 确认添加实例
    const handleConfirmAddInstance = async () => {
        try {
            const values = await addInstanceForm.validateFields();
            const { instanceLabel, parentClassId } = values;

            const newNode: OntologyNode = {
                id: `node_${Date.now()}`,
                type: 'custom',
                position: { x: Math.random() * 400 + 100, y: Math.random() * 400 + 100 },
                data: {
                    label: instanceLabel,
                    type: 'owl:NamedIndividual',
                    properties: {}
                },
            };

            // 创建 rdf:type 关系到选中的类（本体论关系）
            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${newNode.id}_${parentClassId}`,
                source: newNode.id,
                target: parentClassId,
                data: { label: 'rdf:type', relation: 'instance_of' },
            } as OntologyEdge;

            // 添加新节点和边
            setNodes((nds) => nds.concat(newNode));
            setEdges((eds) => [...eds, newEdge]);

            // 自动展开该类
            setExpandedNodeIds(prev => {
                const newSet = new Set(prev);
                newSet.add(parentClassId);
                return newSet;
            });

            message.success('已添加新实例');
            setIsAddInstanceModalOpen(false);
            addInstanceForm.resetFields();
        } catch (error) {
            console.error('添加实例失败:', error);
        }
    };

    // 删除选中元素
    const deleteSelectedElement = () => {
        if (!selectedElement) {
            message.warning('请先选择要删除的元素');
            return;
        }

        Modal.confirm({
            title: '确认删除',
            content: '确定要删除选中的元素吗？',
            okText: '确定',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: () => {
                const isNode = 'position' in selectedElement;
                if (isNode) {
                    setNodes((nds) => nds.filter((node) => node.id !== selectedElement.id));
                } else {
                    setEdges((eds) => eds.filter((edge) => edge.id !== selectedElement.id));
                }
                setIsDrawerOpen(false);
                setSelectedElement(null);
                message.success('删除成功');
            },
        });
    };

    // 保存草稿
    const handleSaveDraft = async () => {
        if (!projectId) return;

        setLoading(true);
        try {
            await projectsApi.updateProject(Number(projectId), {
                graph_data: { nodes, edges },
            });

            await projectsApi.updateOntology(Number(projectId), { nodes, edges });

            setLastSavedNodes([...nodes]);
            setLastSavedEdges([...edges]);
            setHasUnsavedChanges(false);

            message.success('草稿已保存，已自动同步到图数据库 (Neo4j)');
        } catch (error) {
            message.error('保存失败，请重试');
        } finally {
            setLoading(false);
        }
    };

    // 发布/取消发布项目
    const handleTogglePublish = async () => {
        if (!projectId) return;

        const actionText = isPublished ? '取消发布' : '发布资产';
        const contentText = isPublished
            ? '取消发布后，该本体将从资产中心下架。确定吗？'
            : '发布后，您的本体将在资产中心公开展示。确定要发布吗？';

        Modal.confirm({
            title: `确认${actionText}`,
            content: contentText,
            okText: `确定${actionText}`,
            cancelText: '取消',
            onOk: async () => {
                setLoading(true);
                try {
                    if (isPublished) {
                        await projectsApi.unpublishProject(Number(projectId));
                        message.success('已取消发布');
                    } else {
                        if (hasUnsavedChanges) {
                            await projectsApi.updateProject(Number(projectId), {
                                graph_data: { nodes, edges },
                            });
                            await projectsApi.updateOntology(Number(projectId), { nodes, edges });
                        }
                        await projectsApi.publishProject(Number(projectId));
                        message.success('发布成功！已在资产中心公开展示');
                    }

                    const updatedProject = await projectsApi.getProject(Number(projectId));
                    setIsPublished(updatedProject.is_published);

                    if (!isPublished && updatedProject.is_published) {
                        setHasUnsavedChanges(false);
                        setLastSavedNodes([...nodes]);
                        setLastSavedEdges([...edges]);
                    }
                } catch (error: any) {
                    message.error(error.response?.data?.detail || `${actionText}失败`);
                } finally {
                    setLoading(false);
                }
            },
        });
    };

    // 准备上传
    const beforeUpload = (file: File) => {
        setPendingFile(file);
        setIsRuleModalOpen(true);
        return false;
    };

    // 执行带规则的上传
    const handleStartExtraction = async () => {
        if (!projectId || !pendingFile) return;

        const values = await ruleForm.validateFields();
        setIsRuleModalOpen(false);
        setLoading(true);

        try {
            const response = await projectsApi.uploadDocument(Number(projectId), pendingFile, values.rules);

            if (response.nodes && response.nodes.length > 0) {
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.nodes,
                    response.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                message.success(response.message || '本体处理成功！');
            } else {
                message.warning('提取完成，但未发现有效的本体节点，请检查模型配置或文档内容');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
            setPendingFile(null);
        }
    };

    // 处理 TTL 文件上传
    const handleUploadTTL = async (file: File) => {
        if (!projectId) return;

        setLoading(true);
        try {
            const response = await projectsApi.uploadTTLFile(Number(projectId), file);

            if (response.nodes) {
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.nodes,
                    response.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
            }

            message.success(response.message || 'TTL 文件解析成功！');
        } catch (error: any) {
            message.error(error.response?.data?.detail || 'TTL 文件解析失败');
        } finally {
            setLoading(false);
        }
        return false;
    };

    // 下载 TTL 文件
    const handleDownloadTTL = async () => {
        if (!projectId) return;

        try {
            await projectsApi.downloadTTL(Number(projectId));
            message.success('TTL 文件已开始下载');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '下载 TTL 文件失败');
        }
    };

    // 一键展开/收起所有实例
    const expandAllInstances = () => {
        const classIdsWithInstances = new Set<string>();
        
        // 遍历边找到所有 instance_of 关系
        edges.forEach(edge => {
            const label = edge.data?.label || edge.label || '';
            const relation = edge.data?.relation || '';
            // 支持多种标签格式：rdf:type, type, instance_of
            const isInstanceRelation = label === 'rdf:type' || label === 'type' || relation === 'instance_of';
            if (isInstanceRelation) {
                const classId = String(edge.target);
                classIdsWithInstances.add(classId);
            }
        });

        // 检查是否已经有展开的类
        const hasExpandedInstances = Array.from(expandedNodeIds).some(id => classIdsWithInstances.has(id));

        if (hasExpandedInstances) {
            // 收起所有实例 - 清空展开状态
            setExpandedNodeIds(new Set());
            message.success('已收起所有实例');
        } else {
            // 展开所有有实例的类
            setExpandedNodeIds(classIdsWithInstances);
            if (classIdsWithInstances.size > 0) {
                message.success(`已展开 ${classIdsWithInstances.size} 个类的实例`);
            } else {
                message.info('没有找到实例关联的类');
            }
        }
    };

    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目', path: '/my-projects' },
        { title: projectName || '本体构建' },
    ];

    const nodeTypes = [
        { label: '类 (Class)', value: 'owl:Class' },
        { label: '实例 (Individual)', value: 'owl:NamedIndividual' },
        { label: '属性 (Property)', value: 'owl:ObjectProperty' },
    ];

    const relationTypes = [
        { label: '关联 (related_to)', value: 'related_to' },
        { label: '子类 (subclass_of)', value: 'subclass_of' },
        { label: '属于 (instance_of)', value: 'instance_of' },
        { label: '包含 (contains)', value: 'contains' },
        { label: '依赖 (depends_on)', value: 'depends_on' },
    ];

    // 计算当前显示的元素 - 根据展开状态控制实例节点的显示
    const getDisplayElements = useCallback(() => {
        const visibleNodeIds = new Set<string>();
        
        // 构建类到实例的映射
        const classToInstances: Map<string, string[]> = new Map();
        // 构建实例到类的映射
        const instanceToClass: Map<string, string> = new Map();
        
        edges.forEach(edge => {
            const label = edge.data?.label || edge.label || '';
            const relation = edge.data?.relation || '';
            // 支持多种标签格式：rdf:type, type, instance_of
            const isInstanceRelation = label === 'rdf:type' || label === 'type' || relation === 'instance_of';
            if (isInstanceRelation) {
                const instanceId = String(edge.source);
                const classId = String(edge.target);
                if (!classToInstances.has(classId)) {
                    classToInstances.set(classId, []);
                }
                classToInstances.get(classId)!.push(instanceId);
                instanceToClass.set(instanceId, classId);
            }
        });

        nodes.forEach(node => {
            if (node.data?.type === 'owl:Class') {
                // 类节点始终显示
                visibleNodeIds.add(node.id);
                // 如果该类被展开，显示其所有实例
                if (expandedNodeIds.has(node.id)) {
                    const instances = classToInstances.get(node.id) || [];
                    instances.forEach(instanceId => visibleNodeIds.add(instanceId));
                }
            } else if (node.data?.type === 'owl:NamedIndividual') {
                // 实例节点只有在所属类被展开时才显示
                const parentClassId = instanceToClass.get(node.id);
                if (parentClassId && expandedNodeIds.has(parentClassId)) {
                    visibleNodeIds.add(node.id);
                }
            } else {
                // 其他类型节点始终显示
                visibleNodeIds.add(node.id);
            }
        });

        const displayNodes = nodes.filter(n => visibleNodeIds.has(n.id));

        // 边只有在两端节点都可见时才显示
        const displayEdges = edges.filter(e => {
            const sourceId = String(e.source);
            const targetId = String(e.target);
            const sourceVisible = visibleNodeIds.has(sourceId);
            const targetVisible = visibleNodeIds.has(targetId);
            return sourceVisible && targetVisible;
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds]);

    const { displayNodes, displayEdges } = getDisplayElements();

    // 创建新关系
    const addNewRelation = () => {
        relationForm.resetFields();
        const nodeOptions = nodes.map(node => ({
            label: `${node.data.label} (${node.data.type})`,
            value: node.id
        }));
        setIsAddRelationModalOpen(true);
    };

    // 确认创建新关系
    const handleConfirmNewRelation = async () => {
        try {
            const values = await relationForm.validateFields();
            const { sourceNodeId, targetNodeId, relationType } = values;

            let finalRelationType = relationType;
            if (relationType && !relationTypes.some(opt => opt.value === relationType)) {
                finalRelationType = relationType;
            }

            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${sourceNodeId}_${targetNodeId}`,
                source: sourceNodeId,
                target: targetNodeId,
                data: { label: finalRelationType, relation: finalRelationType },
            } as OntologyEdge;

            setEdges((eds) => {
                const newEdges = [...eds, newEdge];
                return newEdges;
            });
            message.success('关系已创建');

            setIsAddRelationModalOpen(false);
        } catch (error) {
            console.error('创建关系失败:', error);
        }
    };

    // 系统配置相关
    const openConfigModal = async () => {
        setLoading(true);
        try {
            const config = await systemApi.getConfig('llm_config');
            configForm.setFieldsValue(config.value);
            setIsConfigModalOpen(true);
        } catch (error) {
            message.error('获取配置失败');
        } finally {
            setLoading(false);
        }
    };

    const handleSaveConfig = async () => {
        try {
            const values = await configForm.validateFields();
            const configValues = {
                ...values,
                streaming_enabled: values.streaming_enabled === true,
                milvus_enabled: values.milvus_enabled === true,
            };
            await systemApi.updateConfig('llm_config', configValues);
            message.success('系统配置已保存');
            setIsConfigModalOpen(false);
        } catch (error) {
            console.error('保存配置失败:', error);
            message.error('保存配置失败，请检查输入');
        }
    };

    // 测试大模型连通性
    const testLLMConnectivity = async () => {
        setTestingLLM(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/llm', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`大模型连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingLLM(false);
        }
    };

    // 测试 Neo4j 连通性
    const testNeo4JConnectivity = async () => {
        setTestingNeo4J(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/neo4j', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Neo4j 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingNeo4J(false);
        }
    };

    // 测试 Embedding 连通性
    const testEmbeddingConnectivity = async () => {
        setTestingEmbedding(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/embedding', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Embedding 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingEmbedding(false);
        }
    };

    // 测试 Milvus 连通性
    const testMilvusConnectivity = async () => {
        setTestingMilvus(true);
        try {
            const values = await configForm.validateFields();
            const response = await apiClient.post('/api/system/test-connectivity/milvus', values);
            if (response.data.status === 'success') {
                message.success(response.data.message);
            } else {
                message.error(response.data.message);
            }
        } catch (error: any) {
            message.error(`Milvus 连通性测试失败：${error.response?.data?.message || error.message}`);
        } finally {
            setTestingMilvus(false);
        }
    };

    // 构建树形数据
    const buildTreeData = useCallback(() => {
        const classNodes = nodes.filter(n => n.data?.type === 'owl:Class');
        const instanceNodes = nodes.filter(n => n.data?.type === 'owl:NamedIndividual');

        // 构建类到实例的映射
        const classToInstances: Record<string, any[]> = {};
        instanceNodes.forEach(instance => {
            const parentClassEdge = edges.find(e =>
                e.source === instance.id &&
                (e.label === 'rdf:type' || e.data?.label === 'type' || e.data?.relation === 'instance_of')
            );
            if (parentClassEdge && parentClassEdge.target) {
                if (!classToInstances[parentClassEdge.target]) {
                    classToInstances[parentClassEdge.target] = [];
                }
                classToInstances[parentClassEdge.target].push(instance);
            }
        });

        return classNodes.map(classNode => ({
            title: classNode.data?.label || '未命名类',
            key: classNode.id,
            icon: <span className="inline-block w-3 h-3 rounded-full bg-[#4cc9f0] mr-2" />,
            children: classToInstances[classNode.id]?.map(instance => ({
                title: instance.data?.label || '未命名实例',
                key: instance.id,
                icon: <span className="inline-block w-3 h-3 rounded-full bg-[#f79767] mr-2" />,
                isLeaf: true,
            })) || [],
        }));
    }, [nodes, edges]);

    // 点击树节点
    const onTreeSelect = (selectedKeys: React.Key[]) => {
        if (selectedKeys.length === 0) return;
        const key = selectedKeys[0] as string;
        const node = nodes.find(n => n.id === key);
        if (node) {
            onNodeClick(node);
        }
    };

    return (
        <div className="h-screen flex flex-col bg-gray-50">
            {(!projectId || projectId.trim() === '' || isCreatingProject) ? (
                // 创建项目表单
                <div className="flex-1 flex items-center justify-center">
                    <div className="w-full max-w-md p-6 bg-white rounded-lg shadow-xl">
                        <h2 className="text-2xl font-bold text-gray-800 mb-6 text-center">创建新本体项目</h2>
                        <Form
                            form={createProjectForm}
                            layout="vertical"
                            onFinish={async (values) => {
                                setLoading(true);
                                try {
                                    const newProject = await projectsApi.createProject({
                                        name: values.name,
                                        description: values.description
                                    });

                                    navigate(`/ontology-builder/${newProject.id}`);
                                    message.success('项目创建成功，已进入编辑界面');
                                } catch (error: any) {
                                    message.error('创建项目失败，请重试');
                                } finally {
                                    setLoading(false);
                                }
                            }}
                        >
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
                                    <Button onClick={() => navigate('/my-projects')}>取消</Button>
                                    <Button type="primary" htmlType="submit" className="bg-blue-600">
                                        创建项目
                                    </Button>
                                </Space>
                            </Form.Item>
                        </Form>
                    </div>
                </div>
            ) : (
                // 可视化编辑界面 - 力导向图
                <div className="flex-1 flex relative">
                    {/* 左侧展开面板 */}
                    <div
                        className={`absolute left-0 top-0 bottom-0 bg-white shadow-lg z-20 transition-all duration-300 ${
                            isLeftPanelExpanded ? 'w-[420px]' : 'w-0'
                        } overflow-hidden`}
                    >
                        {isLeftPanelExpanded && (
                            <div className="p-4 h-full flex flex-col">
                                <div className="flex items-center justify-between mb-4">
                                    <h3 className="font-semibold text-gray-700 flex items-center">
                                        <UnorderedListOutlined className="mr-2" />
                                        类与实例列表
                                    </h3>
                                </div>
                                <div className="flex-1 overflow-auto">
                                    <Tree
                                        showIcon
                                        defaultExpandAll
                                        selectedKeys={selectedElement ? [selectedElement.id] : []}
                                        onSelect={onTreeSelect}
                                        treeData={buildTreeData()}
                                    />
                                </div>
                                <div className="mt-4 pt-4 border-t border-gray-200">
                                    <div className="flex items-center text-sm text-gray-500">
                                        <InfoCircleOutlined className="mr-2" />
                                        <span>共 {nodes.filter(n => n.data?.type === 'owl:Class').length} 个类，{nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length} 个实例</span>
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* 左侧展开/收起按钮 */}
                    <button
                        className={`absolute left-0 top-1/2 -translate-y-1/2 z-30 bg-white shadow-md rounded-r-lg p-2 hover:bg-gray-50 transition-all duration-300 ${
                            isLeftPanelExpanded ? 'left-[420px]' : 'left-0'
                        }`}
                        onClick={() => setIsLeftPanelExpanded(!isLeftPanelExpanded)}
                        title={isLeftPanelExpanded ? '收起列表' : '展开列表'}
                    >
                        {isLeftPanelExpanded ? <LeftOutlined /> : <RightOutlined />}
                    </button>

                    <Navbar breadcrumbs={breadcrumbs} />
                    <div className="flex-1 relative">
                        {loading && (
                            <div className="absolute inset-0 bg-white bg-opacity-75 flex items-center justify-center z-50">
                                <Spin size="large" tip="正在加载..." />
                            </div>
                        )}

                        {/* 顶部工具栏 */}
                        <div className="absolute top-4 right-4 z-10">
                            <Space className="bg-white p-2 rounded-lg shadow-md border border-gray-100 flex-wrap max-w-[calc(100vw-400px)]">
                                <Upload
                                    accept=".txt,.pdf,.doc,.docx"
                                    showUploadList={false}
                                    beforeUpload={beforeUpload}
                                >
                                    <Tooltip title="定义规则并上传文档提取">
                                        <Button type="primary" icon={<CloudUploadOutlined />} className="bg-indigo-600">
                                            自动提取构建
                                        </Button>
                                    </Tooltip>
                                </Upload>

                                <Upload
                                    accept=".ttl"
                                    showUploadList={false}
                                    beforeUpload={handleUploadTTL}
                                >
                                    <Tooltip title="上传 TTL 文件直接解析">
                                        <Button icon={<FileTextOutlined />} className="bg-purple-600 text-white">
                                            上传 TTL 文件
                                        </Button>
                                    </Tooltip>
                                </Upload>

                                <Button
                                    icon={<PlusOutlined />}
                                    onClick={addNewClass}
                                    className="border-blue-500 text-blue-600 hover:text-blue-700 hover:border-blue-700"
                                >
                                    新增类
                                </Button>

                                <Button
                                    icon={<PlusOutlined />}
                                    onClick={addNewInstance}
                                    className="border-orange-500 text-orange-600 hover:text-orange-700 hover:border-orange-700"
                                >
                                    新增实例
                                </Button>

                                <Button
                                    icon={<EyeOutlined />}
                                    onClick={expandAllInstances}
                                    disabled={nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length === 0}
                                >
                                    展开/收起所有实例
                                </Button>

                                <Button
                                    icon={<PlusOutlined />}
                                    onClick={addNewRelation}
                                >
                                    创建新关系
                                </Button>

                                <Button
                                    icon={<DeleteOutlined />}
                                    danger
                                    onClick={deleteSelectedElement}
                                    disabled={!selectedElement}
                                >
                                    删除
                                </Button>

                                <Divider type="vertical" />

                                <Button
                                    icon={<SaveOutlined />}
                                    onClick={handleSaveDraft}
                                    loading={loading}
                                    type={hasUnsavedChanges ? "primary" : "default"}
                                    ghost={hasUnsavedChanges}
                                >
                                    保存草稿
                                </Button>

                                <Button
                                    type={isPublished ? "default" : "primary"}
                                    icon={isPublished ? <EyeOutlined /> : <CloudServerOutlined />}
                                    onClick={handleTogglePublish}
                                    loading={loading}
                                    className={isPublished ? "" : "bg-green-600 hover:bg-green-700"}
                                >
                                    {isPublished ? '已发布资产 (点击关闭)' : '发布资产中心'}
                                </Button>

                                <Button
                                    icon={<DownloadOutlined />}
                                    onClick={handleDownloadTTL}
                                >
                                    下载 TTL
                                </Button>
                            </Space>
                        </div>

                        {/* 返回按钮 */}
                        <div 
                            className={`absolute top-4 z-10 transition-all duration-300 ${
                                isLeftPanelExpanded ? 'left-[240px]' : 'left-4'
                            }`}
                        >
                            <Button
                                icon={<ArrowLeftOutlined />}
                                onClick={() => navigate('/my-projects')}
                                className="shadow-sm bg-white"
                            >
                                返回项目列表
                            </Button>
                        </div>

                        {/* 底部统计 */}
                        <div 
                            className={`absolute bottom-4 z-10 transition-all duration-300 ${
                                isLeftPanelExpanded ? 'left-[240px]' : 'left-4'
                            }`}
                        >
                            <div className="bg-white px-4 py-2 rounded-lg shadow-md border border-gray-100 flex flex-col space-y-1">
                                <div className="flex items-center space-x-4">
                                    <span className="text-gray-500 font-medium"><InfoCircleOutlined className="mr-1" /> 视图统计:</span>
                                    <span><Tag color="blue">{nodes.length}</Tag> 实体</span>
                                    <span><Tag color="green">{edges.length}</Tag> 关系</span>
                                </div>
                                <div className="text-[10px] text-gray-400">
                                    提示：可以
                                </div>
                            </div>
                        </div>

                        {/* 力导向图组件 */}
                        <div className="absolute inset-0">
                            <D3ForceGraph
                                nodes={displayNodes}
                                edges={displayEdges}
                                onNodeClick={onNodeClick}
                                onEdgeClick={onEdgeClick}
                                onNodesChange={(updatedNodes) => {
                                    if (Array.isArray(updatedNodes)) {
                                        setNodes((prevNodes) =>
                                            prevNodes.map(node => {
                                                const updated = updatedNodes.find(n => n.id === node.id);
                                                return updated ? { ...updated } : node;
                                            })
                                        );
                                    }
                                }}
                                onNodeRightClick={(node) => {
                                    // 右键点击类节点时，展开该类的实例
                                    const classId = node.id;
                                    setExpandedNodeIds(prev => {
                                        const newSet = new Set(prev);
                                        if (newSet.has(classId)) {
                                            // 如果已展开，则收起（切换）
                                            newSet.delete(classId);
                                            message.info(`已收起 "${node.data?.label}" 的实例`);
                                        } else {
                                            // 如果未展开，则展开
                                            newSet.add(classId);
                                            message.success(`已展开 "${node.data?.label}" 的实例`);
                                        }
                                        return newSet;
                                    });
                                }}
                                width={window.innerWidth - (isLeftPanelExpanded ? 420 : 60)}
                                height={window.innerHeight - 60}
                                highlightNodeId={highlightNodeId}
                            />
                        </div>

                        {/* 属性编辑抽屉 */}
                        <Drawer
                            title={selectedElement ? (
                                'position' in selectedElement
                                    ? `编辑节点属性 - ${selectedElement.data?.label || '未命名'}`
                                    : `编辑关系属性 - ${selectedElement.data?.label || '未命名'}`
                            ) : "属性编辑"}
                            placement="right"
                            onClose={() => {
                                setIsDrawerOpen(false);
                                setSelectedElement(null);
                                form.resetFields();
                            }}
                            open={isDrawerOpen}
                            width={450}
                            destroyOnClose={true}
                        >
                            {selectedElement && (
                                <Form
                                    form={form}
                                    layout="vertical"
                                    onFinish={handleSaveProperties}
                                    initialValues={{
                                        label: selectedElement.data?.label || '',
                                        type: selectedElement.data?.type || 'owl:Class',
                                        relation: selectedElement.data?.relation || selectedElement.data?.label || ''
                                    }}
                                >
                                    {'position' in selectedElement ? (
                                        // 节点属性编辑
                                        <>
                                            <SectionTitle icon={<EditOutlined />} title="基本属性" />

                                            <Form.Item
                                                name="label"
                                                label="节点名称"
                                                rules={[{ required: true, message: '请输入节点名称' }]}
                                            >
                                                <Input placeholder="请输入节点名称" />
                                            </Form.Item>

                                            <Form.Item
                                                name="type"
                                                label="节点类型"
                                            >
                                                <Input disabled value={form.getFieldValue('type') === 'owl:Class' ? '类 (Class)' : form.getFieldValue('type') === 'owl:NamedIndividual' ? '实例 (Individual)' : form.getFieldValue('type')} />
                                            </Form.Item>

                                            <SectionTitle icon={<TagsOutlined />} title="自定义属性" />
                                            <Form.List name="properties">
                                                {(fields, { add, remove }) => (
                                                    <>
                                                        {fields.map(({ key, name, ...restField }) => (
                                                            <div
                                                                key={key}
                                                                style={{
                                                                    display: 'flex',
                                                                    gap: '10px',
                                                                    marginBottom: 8,
                                                                    alignItems: 'flex-start'
                                                                }}
                                                            >
                                                                <Form.Item
                                                                    {...restField}
                                                                    name={[name, 'name']}
                                                                    rules={[{ required: true, message: '属性名不能为空' }]}
                                                                    style={{ width: '120px', marginBottom: 0, flexShrink: 0 }}
                                                                >
                                                                    <Input placeholder="属性名" />
                                                                </Form.Item>

                                                                <Form.Item
                                                                    {...restField}
                                                                    name={[name, 'value']}
                                                                    rules={[{ required: true, message: '属性值不能为空' }]}
                                                                    style={{ flex: 1, marginBottom: 0 }}
                                                                >
                                                                    <Input.TextArea
                                                                        placeholder="属性值"
                                                                        autoSize={{ minRows: 1, maxRows: 6 }}
                                                                    />
                                                                </Form.Item>

                                                                <MinusCircleOutlined
                                                                    onClick={() => remove(name)}
                                                                    style={{ marginTop: 8, color: '#999', cursor: 'pointer' }}
                                                                />
                                                            </div>
                                                        ))}
                                                        <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>
                                                            添加自定义属性
                                                        </Button>
                                                    </>
                                                )}
                                            </Form.List>
                                        </>
                                    ) : (
                                        // 关系属性编辑
                                        <>
                                            <SectionTitle icon={<LinkOutlined />} title="关系属性" />

                                            <Form.Item
                                                name="label"
                                                label="关系标签"
                                                rules={[{ required: true, message: '请输入关系标签' }]}
                                            >
                                                <Input placeholder="例如：关联、属于、包含" />
                                            </Form.Item>

                                            <Form.Item
                                                name="relation"
                                                label="关系类型"
                                                rules={[{ required: true, message: '请选择关系类型' }]}
                                            >
                                                <Select
                                                    showSearch
                                                    placeholder="选择或输入自定义关系类型"
                                                    optionFilterProp="label"
                                                    options={relationTypes}
                                                    dropdownRender={(menu) => (
                                                        <>
                                                            {menu}
                                                            <Divider style={{ margin: '8px 0' }} />
                                                            <div style={{ padding: '4px 8px', cursor: 'pointer' }}>
                                                                <Input
                                                                    placeholder="输入自定义关系类型"
                                                                    value={customRelationType}
                                                                    onChange={(e) => setCustomRelationType(e.target.value)}
                                                                    onPressEnter={() => {
                                                                        if (customRelationType.trim()) {
                                                                            form.setFieldsValue({ relation: customRelationType.trim() });
                                                                        }
                                                                    }}
                                                                />
                                                            </div>
                                                        </>
                                                    )}
                                                />
                                            </Form.Item>
                                        </>
                                    )}

                                    <div className="flex justify-end space-x-2 mt-6 pt-4 border-t border-gray-200">
                                        <Button
                                            onClick={() => {
                                                setIsDrawerOpen(false);
                                                setSelectedElement(null);
                                                form.resetFields();
                                            }}
                                        >
                                            取消
                                        </Button>
                                        <Button
                                            icon={<DeleteOutlined />}
                                            danger
                                            onClick={deleteSelectedElement}
                                        >
                                            删除
                                        </Button>
                                        <Button type="primary" htmlType="submit" className="bg-blue-600">
                                            保存修改
                                        </Button>
                                    </div>
                                </Form>
                            )}
                        </Drawer>

                        {/* 抽取规则定义 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center space-x-2">
                                    <CloudUploadOutlined style={{ color: '#indigo' }} />
                                    <span>定义抽取规则 (可选)</span>
                                </div>
                            }
                            open={isRuleModalOpen}
                            onOk={handleStartExtraction}
                            onCancel={() => setIsRuleModalOpen(false)}
                            okText="开始提取"
                            cancelText="取消"
                            width={800}
                        >
                            <div className="mb-4 text-gray-500 text-sm">
                                请在下方配置主体、属性和关系，帮助 AI 更准确地从文档中提取您关注的内容。留空则按通用模式提取。
                            </div>
                            <Form form={ruleForm} layout="vertical">
                                <SectionTitle icon={<DatabaseOutlined />} title="主体 (Class)" />
                                <Form.List name="classes">
                                    {(fields, { add, remove }) => (
                                        <>
                                            <div className="overflow-x-auto mb-4">
                                                <table className="min-w-full divide-y divide-gray-200 border border-gray-200 rounded-lg">
                                                    <thead className="bg-gray-50">
                                                        <tr>
                                                            <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/3">
                                                                主体 (Class)
                                                            </th>
                                                            <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/3">
                                                                属性 (DataProp)
                                                            </th>
                                                            <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider w-1/3">
                                                                关系 (ObjectProp)
                                                            </th>
                                                            <th scope="col" className="px-4 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider w-12">
                                                                操作
                                                            </th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="bg-white divide-y divide-gray-200">
                                                        {fields.map(({ key, name, ...restField }) => (
                                                            <tr key={key} className="hover:bg-gray-50">
                                                                <td className="px-4 py-3 whitespace-nowrap">
                                                                    <Form.Item
                                                                        {...restField}
                                                                        name={[name, 'class']}
                                                                        rules={[{ required: true, message: '主体名称不能为空' }]}
                                                                        noStyle
                                                                    >
                                                                        <Input placeholder="例如：技术与知识领域" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-4 py-3 whitespace-nowrap">
                                                                    <Form.Item
                                                                        {...restField}
                                                                        name={[name, 'properties']}
                                                                        noStyle
                                                                    >
                                                                        <Input placeholder="例如：描述，成熟度" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-4 py-3 whitespace-nowrap">
                                                                    <Form.Item
                                                                        {...restField}
                                                                        name={[name, 'relations']}
                                                                        noStyle
                                                                    >
                                                                        <Input placeholder="例如：支撑 (创新载体), 应用于 (业务价值)" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-4 py-3 whitespace-nowrap text-right text-sm font-medium">
                                                                    <MinusCircleOutlined
                                                                        onClick={() => remove(name)}
                                                                        className="text-red-500 hover:text-red-700 cursor-pointer"
                                                                    />
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />}>添加主体配置</Button>
                                        </>
                                    )}
                                </Form.List>

                                <SectionTitle icon={<InfoCircleOutlined />} title="场景描述 (可选)" />
                                <Form.Item
                                    name="scenario"
                                    label="场景描述"
                                    tooltip="例如：分析这份半导体行业研报..."
                                >
                                    <Input.TextArea
                                        rows={3}
                                        placeholder="请输入场景描述，帮助 AI 理解上下文..."
                                    />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 创建新关系 Modal */}
                        <Modal
                            title="创建新关系"
                            open={isAddRelationModalOpen}
                            onOk={handleConfirmNewRelation}
                            onCancel={() => setIsAddRelationModalOpen(false)}
                            okText="创建"
                            cancelText="取消"
                        >
                            <Form form={relationForm} layout="vertical">
                                <Form.Item
                                    name="sourceNodeId"
                                    label="起始节点"
                                    rules={[{ required: true, message: '请选择起始节点' }]}
                                >
                                    <Select
                                        showSearch
                                        placeholder="选择起始节点"
                                        optionFilterProp="label"
                                        options={nodes.map(node => ({
                                            label: `${node.data.label} (${node.data.type})`,
                                            value: node.id
                                        }))}
                                    />
                                </Form.Item>
                                <Form.Item
                                    name="targetNodeId"
                                    label="目标节点"
                                    rules={[{ required: true, message: '请选择目标节点' }]}
                                >
                                    <Select
                                        showSearch
                                        placeholder="选择目标节点"
                                        optionFilterProp="label"
                                        options={nodes.map(node => ({
                                            label: `${node.data.label} (${node.data.type})`,
                                            value: node.id
                                        }))}
                                    />
                                </Form.Item>
                                <Form.Item
                                    name="relationType"
                                    label="关系类型"
                                    rules={[{ required: true, message: '请选择或输入关系类型' }]}
                                >
                                    <Select
                                        showSearch
                                        placeholder="选择或输入自定义关系类型"
                                        optionFilterProp="label"
                                        options={relationTypes}
                                        dropdownRender={(menu) => (
                                            <>
                                                {menu}
                                                <Divider style={{ margin: '8px 0' }} />
                                                <div style={{ padding: '4px 8px', cursor: 'pointer' }}>
                                                    <Input
                                                        placeholder="输入自定义关系类型"
                                                        value={customRelationType}
                                                        onChange={(e) => setCustomRelationType(e.target.value)}
                                                        onPressEnter={() => {
                                                            if (customRelationType.trim()) {
                                                                relationForm.setFieldsValue({ relationType: customRelationType.trim() });
                                                            }
                                                        }}
                                                    />
                                                </div>
                                            </>
                                        )}
                                    />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 新增实例 Modal - 选择父类 */}
                        <Modal
                            title={
                                <div className="flex items-center space-x-2">
                                    <PlusOutlined style={{ color: '#fa8c16' }} />
                                    <span>新增实例</span>
                                </div>
                            }
                            open={isAddInstanceModalOpen}
                            onOk={handleConfirmAddInstance}
                            onCancel={() => {
                                setIsAddInstanceModalOpen(false);
                                addInstanceForm.resetFields();
                            }}
                            okText="创建"
                            cancelText="取消"
                            width={500}
                        >
                            <div className="mb-4 text-gray-500 text-sm">
                                请选择要归属的类，并输入实例名称。实例将通过 <Tag color="orange">rdf:type</Tag> 关系（本体论中的"属于"关系）关联到选中的类。
                            </div>
                            <Form
                                form={addInstanceForm}
                                layout="vertical"
                                initialValues={{
                                    instanceLabel: '',
                                    parentClassId: ''
                                }}
                            >
                                <Form.Item
                                    name="parentClassId"
                                    label="选择父类"
                                    rules={[{ required: true, message: '请选择一个类作为父类' }]}
                                >
                                    <Select
                                        showSearch
                                        placeholder="选择一个类"
                                        optionFilterProp="label"
                                        options={nodes.filter(n => n.data?.type === 'owl:Class').map(node => ({
                                            label: node.data?.label || '未命名类',
                                            value: node.id
                                        }))}
                                    />
                                </Form.Item>

                                <Form.Item
                                    name="instanceLabel"
                                    label="实例名称"
                                    rules={[{ required: true, message: '请输入实例名称' }]}
                                >
                                    <Input placeholder="请输入实例名称" />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 系统配置 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center space-x-2">
                                    <CloudServerOutlined style={{ color: '#1890ff' }} />
                                    <span>模型服务配置 (仅管理员)</span>
                                </div>
                            }
                            open={isConfigModalOpen}
                            onOk={handleSaveConfig}
                            onCancel={() => setIsConfigModalOpen(false)}
                            width={600}
                            okText="保存配置"
                            cancelText="取消"
                            maskClosable={false}
                        >
                            <div className="bg-yellow-50 p-3 mb-4 rounded border border-yellow-100 flex items-start space-x-2">
                                <InfoCircleOutlined className="mt-1 text-yellow-600" />
                                <div className="text-yellow-800 text-sm">
                                    此处的配置将覆盖环境变量中的默认设置。修改后将立即在自动提取和构建任务中生效。
                                </div>
                            </div>

                            <Form
                                form={configForm}
                                layout="vertical"
                                initialValues={{
                                    api_key: '',
                                    base_url: '',
                                    model: '',
                                    chunk_size: 15000,
                                    chunk_overlap: 500,
                                    request_interval: 2,
                                    streaming_enabled: false,
                                    milvus_enabled: false,
                                    neo4j_uri: 'bolt://localhost:7687',
                                    neo4j_username: 'neo4j',
                                    neo4j_password: 'password',
                                    embedding_base_url: 'http://localhost:11434/v1',
                                    embedding_model: 'nomic-embed-text:latest',
                                    milvus_host: '127.0.0.1',
                                    milvus_port: '19530'
                                }}
                            >
                                <div className="grid grid-cols-2 gap-4">
                                    <Form.Item
                                        name="base_url"
                                        label="API Endpoint (Base URL)"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入 API 端点' }]}
                                    >
                                        <Input placeholder="例如：https://api.openai.com/v1" />
                                    </Form.Item>

                                    <Form.Item
                                        name="api_key"
                                        label="API Key"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入 API Key' }]}
                                    >
                                        <Input.Password placeholder="sk-..." />
                                    </Form.Item>

                                    <Form.Item
                                        name="model"
                                        label="模型名称"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入模型名称' }]}
                                    >
                                        <Input placeholder="例如：gpt-3.5-turbo 或 gpt-4" />
                                    </Form.Item>

                                    <Form.Item
                                        name="chunk_size"
                                        label="提取分块大小 (Chunk Size)"
                                    >
                                        <Input type="number" suffix="字符" />
                                    </Form.Item>

                                    <Form.Item
                                        name="chunk_overlap"
                                        label="分块重叠 (Overlap)"
                                    >
                                        <Input type="number" suffix="字符" />
                                    </Form.Item>

                                    <Form.Item
                                        name="request_interval"
                                        label="请求间隔 (Interval)"
                                    >
                                        <Input type="number" suffix="秒" />
                                    </Form.Item>

                                    <Form.Item
                                        name="streaming_enabled"
                                        valuePropName="checked"
                                        className="col-span-2"
                                    >
                                        <Switch checkedChildren="流式启用" unCheckedChildren="流式禁用" />
                                    </Form.Item>

                                    <Form.Item
                                        name="neo4j_uri"
                                        label="Neo4j URI"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入 Neo4j URI' }]}
                                    >
                                        <Input placeholder="例如：bolt://localhost:7687" />
                                    </Form.Item>

                                    <Form.Item
                                        name="neo4j_username"
                                        label="Neo4j 用户名"
                                        className="col-span-1"
                                        rules={[{ required: true, message: '请输入 Neo4j 用户名' }]}
                                    >
                                        <Input placeholder="neo4j" />
                                    </Form.Item>

                                    <Form.Item
                                        name="neo4j_password"
                                        label="Neo4j 密码"
                                        className="col-span-1"
                                        rules={[{ required: true, message: '请输入 Neo4j 密码' }]}
                                    >
                                        <Input.Password placeholder="password" />
                                    </Form.Item>

                                    <Form.Item
                                        name="milvus_enabled"
                                        valuePropName="checked"
                                        className="col-span-2"
                                    >
                                        <Switch checkedChildren="Milvus 启用" unCheckedChildren="Milvus 禁用" />
                                    </Form.Item>

                                    <Form.Item
                                        name="embedding_base_url"
                                        label="Embedding API 地址"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入 Embedding API 地址' }]}
                                    >
                                        <Input placeholder="例如：http://localhost:11434/v1" />
                                    </Form.Item>

                                    <Form.Item
                                        name="embedding_model"
                                        label="Embedding 模型"
                                        className="col-span-2"
                                        rules={[{ required: true, message: '请输入 Embedding 模型名称' }]}
                                    >
                                        <Input placeholder="例如：nomic-embed-text:latest" />
                                    </Form.Item>

                                    <Form.Item
                                        name="milvus_host"
                                        label="Milvus 主机"
                                        className="col-span-1"
                                        rules={[{ required: true, message: '请输入 Milvus 主机地址' }]}
                                    >
                                        <Input placeholder="127.0.0.1" />
                                    </Form.Item>

                                    <Form.Item
                                        name="milvus_port"
                                        label="Milvus 端口"
                                        className="col-span-1"
                                        rules={[{ required: true, message: '请输入 Milvus 端口' }]}
                                    >
                                        <Input placeholder="19530" />
                                    </Form.Item>
                                </div>

                                <div className="mt-6 pt-4 border-t border-gray-200">
                                    <h3 className="font-medium text-gray-700 mb-3">连通性测试</h3>
                                    <div className="grid grid-cols-2 gap-3">
                                        <Button
                                            type="default"
                                            onClick={testLLMConnectivity}
                                            loading={testingLLM}
                                            icon={<CloudServerOutlined />}
                                        >
                                            测试大模型连通性
                                        </Button>
                                        <Button
                                            type="default"
                                            onClick={testNeo4JConnectivity}
                                            loading={testingNeo4J}
                                            icon={<DatabaseOutlined />}
                                        >
                                            测试 Neo4j 连通性
                                        </Button>
                                        <Button
                                            type="default"
                                            onClick={testEmbeddingConnectivity}
                                            loading={testingEmbedding}
                                            icon={<ApiOutlined />}
                                        >
                                            测试 Embedding 连通性
                                        </Button>
                                        <Button
                                            type="default"
                                            onClick={testMilvusConnectivity}
                                            loading={testingMilvus}
                                            icon={<ClusterOutlined />}
                                        >
                                            测试 Milvus 连通性
                                        </Button>
                                    </div>
                                </div>
                            </Form>
                        </Modal>
                    </div>
                </div>
            )}
        </div>
    );
};

const SectionTitle = ({ icon, title }: { icon: any, title: string }) => (
    <div className="flex items-center space-x-2 mb-4 font-medium text-gray-700">
        {icon}
        <span>{title}</span>
    </div>
);

export default OntologyBuilderPage;