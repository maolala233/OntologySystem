import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
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
    Input as AntInput,
    Dropdown,
    Menu,
} from 'antd';
import type { TreeProps } from 'antd';
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
    SearchOutlined,
    ExpandOutlined,
    ShrinkOutlined,
    ZoomInOutlined,
    ZoomOutOutlined,
    FullscreenOutlined,
    MoreOutlined,
    StopOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined,
    LoadingOutlined,
    FileDoneOutlined,
    ClearOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import Navbar from '../components/Layout/Navbar';
import { OntologyNode, OntologyEdge } from '../types/ontology';
import { projectsApi } from '../api/projects';
import { getLayoutedElements } from '../utils/layoutUtils';
import { systemApi } from '../api/system';
import apiClient from '../api/client';
import D3ForceGraph from '../components/OntologyGraph/D3ForceGraph';
import KnowledgeDomainSelector from '../components/KnowledgeDomainSelector';

const { TreeNode } = Tree;
const { Search } = AntInput;

// 扩展 Window 接口以支持自定义属性
declare global {
    interface Window {
        shouldExtractInstancesAfterFileSelect?: boolean;
    }
}

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
    const [pendingFiles, setPendingFiles] = useState<File[]>([]);
    const [isSchemaTypeModalOpen, setIsSchemaTypeModalOpen] = useState(false);
    const [schemaExtractionType, setSchemaExtractionType] = useState<'llm' | 'ttl'>('llm');
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
    const [selectedDomainId, setSelectedDomainId] = useState<number | undefined>(undefined);
    const [selectedDomainName, setSelectedDomainName] = useState<string | undefined>(undefined);
    const [currentProjectDomain, setCurrentProjectDomain] = useState<any | null>(null);
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
    const [isDomainModalOpen, setIsDomainModalOpen] = useState(false);
    
    // 文档管理相关状态
    const [uploadedDocuments, setUploadedDocuments] = useState<any[]>([]);
    const [isDocumentModalOpen, setIsDocumentModalOpen] = useState(false);
    const [isDocLoading, setIsDocLoading] = useState(false);
    
    // 任务进度相关状态
    const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
    const [taskProgress, setTaskProgress] = useState<number>(0);
    const [taskMessage, setTaskMessage] = useState<string>('');
    const [taskDetail, setTaskDetail] = useState<string>('');
    const [taskStatus, setTaskStatus] = useState<'pending' | 'running' | 'completed' | 'failed' | 'cancelled'>('pending');
    const [isProgressModalOpen, setIsProgressModalOpen] = useState(false);
    const [eventSource, setEventSource] = useState<EventSource | null>(null);
    
    // SSE 连接引用（避免 state 更新导致的闭包问题）
    const eventSourceRef = useRef<EventSource | null>(null);

    // 左侧面板展开状态
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    
    // 树形列表搜索
    const [treeSearchValue, setTreeSearchValue] = useState('');
    
    // 树形列表展开状态（仅控制列表内部显示，不影响画布）
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    
    // 画布缩放控制
    const [canvasZoom, setCanvasZoom] = useState(1);

    // 测试连通性状态
    const [testingLLM, setTestingLLM] = useState(false);
    const [testingNeo4J, setTestingNeo4J] = useState(false);
    const [testingEmbedding, setTestingEmbedding] = useState(false);
    const [testingMilvus, setTestingMilvus] = useState(false);

    useEffect(() => {
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
            setCurrentProjectDomain(project.domain || null);
            setSelectedDomainId(project.domain_id || undefined);
            setSelectedDomainName(project.domain?.name || undefined);

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

    const onEdgeClick = (edge: any) => {
        setSelectedElement(edge);
        setIsDrawerOpen(true);

        form.setFieldsValue({
            label: edge.data?.label || edge.data?.relation || '',
            relation: edge.data?.relation || edge.data?.label || '',
        });
    };

    const handleSaveProperties = (values: any) => {
        if (!selectedElement) return;

        const { label, type, properties, relation } = values;
        const isNode = 'position' in selectedElement;

        if (isNode) {
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
            
            if (isNewNode) {
                setIsNewNode(false);
                setHighlightNodeId(null);
            }
        } else {
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
        
        setSelectedElement(newNode);
        setIsDrawerOpen(true);
        form.setFieldsValue({
            label: '新类',
            type: 'owl:Class',
            properties: []
        });
        message.success('已添加新类，请编辑节点名称');
    };

    const addNewInstance = () => {
        const classNodes = nodes.filter(node => node.data?.type === 'owl:Class');
        if (classNodes.length === 0) {
            message.warning('请先创建至少一个类，然后才能添加实例');
            return;
        }
        addInstanceForm.resetFields();
        setIsAddInstanceModalOpen(true);
    };

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

            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${newNode.id}_${parentClassId}`,
                source: newNode.id,
                target: parentClassId,
                data: { label: 'rdf:type', relation: 'instance_of' },
            } as OntologyEdge;

            setNodes((nds) => nds.concat(newNode));
            setEdges((eds) => [...eds, newEdge]);

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

    // 打开知识域配置 Modal
    const handleOpenDomainModal = () => {
        setIsDomainModalOpen(true);
    };

    // 保存知识域配置
    const handleSaveDomain = async (domainId: number | undefined, domainName: string | undefined) => {
        if (!projectId) return;
        
        setLoading(true);
        try {
            await projectsApi.updateProject(Number(projectId), {
                domain_id: domainId,
                domain_name: domainName,
            });
            setCurrentProjectDomain(domainId ? { id: domainId, name: domainName } : null);
            setSelectedDomainId(domainId);
            setSelectedDomainName(domainName);
            message.success('知识域已更新');
            setIsDomainModalOpen(false);
        } catch (error: any) {
            message.error(error.response?.data?.detail || '更新知识域失败');
        } finally {
            setLoading(false);
        }
    };

    const handleTogglePublish = async () => {
        if (!projectId) return;

        const actionText = isPublished ? '取消发布' : '发布资产';
        
        // 发布前检查是否已配置知识域
        if (!isPublished && !currentProjectDomain) {
            Modal.warning({
                title: '发布失败',
                content: (
                    <div>
                        <p>请先配置知识域，然后再发布到资产中心。</p>
                        <p className="mt-2 text-gray-600">
                            知识域用于对本体项目进行分类管理，是发布的必要条件。
                        </p>
                    </div>
                ),
                okText: '去配置',
                onOk: () => {
                    setIsDomainModalOpen(true);
                },
            });
            return;
        }

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
                            // 【关键修复】保存草稿时同时保存 domain_id，确保发布时知识域信息正确
                            await projectsApi.updateProject(Number(projectId), {
                                graph_data: { nodes, edges },
                                domain_id: currentProjectDomain?.id || selectedDomainId,
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
                    const errorMsg = error.response?.data?.detail || `${actionText}失败`;
                    message.error(errorMsg);
                } finally {
                    setLoading(false);
                }
            },
        });
    };

    const handleSchemaButtonClick = () => {
        // 如果已有 schema，提示用户是否重新提取
        if (localStorage.getItem(`project_${projectId}_schema_graph`)) {
            Modal.confirm({
                title: '重新提取骨架',
                content: '当前项目已有提取的骨架，重新提取将覆盖现有骨架。确定继续吗？',
                okText: '确定',
                cancelText: '取消',
                onOk: () => {
                    // 清除之前的 schema
                    localStorage.removeItem(`project_${projectId}_schema_graph`);
                    localStorage.removeItem(`project_${projectId}_text_content`);
                    // 打开方式选择弹窗
                    setIsSchemaTypeModalOpen(true);
                }
            });
        } else {
            // 第一次提取，打开方式选择弹窗
            setIsSchemaTypeModalOpen(true);
        }
    };

    const handleSchemaTypeConfirm = () => {
        setIsSchemaTypeModalOpen(false);
        if (schemaExtractionType === 'ttl') {
            // TTL 方式：触发文件选择
            const fileInput = document.getElementById('ttl-schema-input');
            if (fileInput) {
                fileInput.click();
            }
        } else {
            // LLM 方式：打开文档管理 Modal，让用户先上传文档
            // 用户可以在文档管理 Modal 中浏览文档，然后点击"开始骨架提取"进入规则配置
            handleOpenDocumentModal();
        }
    };

    const handleUploadTTLSchema = async (files: File[]) => {
        if (!projectId || files.length === 0) return;
        
        setLoading(true);
        try {
            const formData = new FormData();
            files.forEach(file => {
                formData.append('files', file);
            });

            const response = await apiClient.post(
                `/api/projects/${projectId}/parse-ttl-schema`,
                formData,
                {
                    headers: { 'Content-Type': 'multipart/form-data' },
                }
            );

            if (response.data && response.data.schema_graph) {
                // 保存 schema 到 localStorage
                localStorage.setItem(`project_${projectId}_schema_graph`, JSON.stringify(response.data.schema_graph));
                
                // 更新画布
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.data.graph_data.nodes || [],
                    response.data.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                message.success(response.data.message || 'TTL 骨架解析成功！');
                
                // 提示用户需要上传文档进行实例提取，提供上传按钮
                Modal.confirm({
                    title: 'TTL 骨架解析成功',
                    content: (
                        <div>
                            <p>类结构已成功解析，共 {response.data.schema_graph?.classes?.length || 0} 个类。</p>
                            <p className="mt-3 text-gray-600 font-medium">
                                是否现在上传文档进行实例提取？
                            </p>
                            <p className="mt-2 text-sm text-gray-500">
                                支持格式：TXT、PDF、DOC、DOCX（可多选）
                            </p>
                        </div>
                    ),
                    okText: '上传文档',
                    cancelText: '稍后上传',
                    onOk: () => {
                        // 打开文档管理 Modal，让用户上传文档
                        handleOpenDocumentModal();
                    },
                    onCancel: () => {
                        // 用户选择稍后上传，不做任何操作
                    },
                });
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`TTL 骨架解析失败：${errorDetail}`);
        } finally {
            setLoading(false);
            setPendingFiles([]);
        }
    };

    const handleStartSchemaExtraction = async () => {
        if (!projectId || pendingFiles.length === 0) return;

        const values = await ruleForm.validateFields();
        setIsRuleModalOpen(false);
        setLoading(true);

        try {
            // 使用异步模式，支持取消，传递多个文件
            // save_documents: true 确保上传的文件保存到数据库
            const response = await projectsApi.extractSchema(Number(projectId), pendingFiles, {
                user_intent: values.scenario,
                chunk_size: 15000,
                chunk_overlap: 500,
                request_interval: 2,
                async_mode: true,
                save_documents: true,
            });

            // 如果返回 task_id，说明是异步任务
            if (response.task_id) {
                setCurrentTaskId(response.task_id);
                setIsProgressModalOpen(true);
                setTaskStatus('running');
                setTaskProgress(0);
                setTaskMessage('开始骨架提取...');
                connectToProgressStream(response.task_id);
                message.info('任务已启动，请在进度窗口查看进度');
            } else if (response.schema_graph && response.graph_data) {
                // 同步模式返回结果
                const textContent = response.text_content || '';
                localStorage.setItem(`project_${projectId}_text_content`, textContent);
                localStorage.setItem(`project_${projectId}_schema_graph`, JSON.stringify(response.schema_graph));

                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.graph_data.nodes || [],
                    response.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                message.success(response.message || `骨架提取完成！`);
            } else if (response.nodes && response.nodes.length > 0) {
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.nodes,
                    response.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                message.success(response.message || '本体处理成功！');
            } else {
                message.warning('提取完成，但未发现有效的本体节点');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`Schema 提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
            setPendingFiles([]);
        }
    };

    const handleStartInstanceExtraction = async () => {
        if (!projectId) return;

        try {
            const textContent = localStorage.getItem(`project_${projectId}_text_content`) || '';
            const schemaGraphStr = localStorage.getItem(`project_${projectId}_schema_graph`);
            
            if (!schemaGraphStr) {
                message.warning('请先上传文件提取 Schema，然后再进行实例提取');
                return;
            }
            
            // 检查是否有文本内容（TTL 方式构建骨架时没有文本内容）
            if (!textContent || textContent.trim() === '') {
                Modal.info({
                    title: '需要上传文档',
                    content: (
                        <div>
                            <p>当前骨架是通过 TTL 文件导入的，没有关联的原始文档。</p>
                            <p className="mt-2 text-gray-600">
                                实例提取需要基于原始文档内容进行抽取。请上传相关文档（TXT/PDF/DOC/DOCX），
                                系统将基于已定义的类结构从文档中提取实例。
                            </p>
                        </div>
                    ),
                    okText: '上传文档',
                    onOk: () => {
                        // 触发文档文件选择，并设置标志
                        const fileInput = document.getElementById('llm-schema-input');
                        if (fileInput) {
                            // 设置标志，表示文件选择后需要直接进行实例提取
                            window.shouldExtractInstancesAfterFileSelect = true;
                            fileInput.click();
                        }
                    },
                });
                return;
            }

            const schemaGraph = JSON.parse(schemaGraphStr);

            // 使用异步模式，支持取消
            const response = await projectsApi.extractInstances(Number(projectId), {
                text_content: textContent,
                schema_graph: schemaGraph,
                chunk_size: 15000,
                chunk_overlap: 500,
                request_interval: 2,
                async_mode: true,
            });

            // 如果返回 task_id，说明是异步任务
            if (response.task_id) {
                setCurrentTaskId(response.task_id);
                setIsProgressModalOpen(true);
                setTaskStatus('running');
                setTaskProgress(0);
                setTaskMessage('开始实例提取...');
                connectToProgressStream(response.task_id);
                message.info('任务已启动，请在进度窗口查看进度');
            } else if (response.graph_data) {
                // 同步模式返回结果（保持向后兼容）
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.graph_data.nodes || [],
                    response.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                let successMsg = response.message || `实例提取完成：${response.instances?.length || 0} 个实例`;
                if (response.discarded_edges_count > 0) {
                    successMsg += ` (⚠️ ${response.discarded_edges_count} 条不合规连线已自动丢弃)`;
                }
                message.success(successMsg);
            } else {
                message.warning('实例提取完成，但未发现有效实例');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`实例提取失败：${errorDetail}`);
        }
    };

    const handleStartExtraction = async () => {
        if (!projectId || pendingFiles.length === 0) return;

        const schemaGraphStr = localStorage.getItem(`project_${projectId}_schema_graph`);
        const textContent = localStorage.getItem(`project_${projectId}_text_content`) || '';
        
        // 如果已有 schema 且有文本内容，直接进行实例提取
        if (schemaGraphStr && textContent) {
            setIsRuleModalOpen(false);
            await handleStartInstanceExtraction();
        } 
        // 如果已有 schema 但没有文本内容（TTL 方式构建的骨架），使用新上传的文件进行实例提取
        else if (schemaGraphStr && !textContent) {
            setIsRuleModalOpen(false);
            await handleStartInstanceExtractionWithFiles();
        }
        // 如果是第一次提取（没有 schema），进行骨架提取
        else {
            await handleStartSchemaExtraction();
        }
    };

    const handleStartInstanceExtractionWithFiles = async (files?: File[]) => {
        /**
         * 当用户通过 TTL 方式构建骨架后，上传文档进行实例提取时使用
         * 调用后端 API 解析文件（支持 PDF/DOC/DOCX/TXT）
         * 
         * 关键点：
         * 1. 只解析文件获取文本内容，不重新提取 schema
         * 2. 使用 TTL 导入的 schema 进行实例提取
         * 3. 实例会添加到已有的类结构框架上
         * 4. 在调用实例提取前，先保存当前画布状态到数据库
         * 
         * 参数:
         * - files: 可选的文件数组，如果提供则直接使用，否则使用 pendingFiles 状态
         */
        const filesToUse = files || pendingFiles;
        if (!projectId || filesToUse.length === 0) return;

        setLoading(true);
        try {
            // 获取已保存的 schema（使用 TTL 导入的 schema）
            const schemaGraphStr = localStorage.getItem(`project_${projectId}_schema_graph`);
            if (!schemaGraphStr) {
                message.error('Schema 不存在，请先提取骨架');
                return;
            }
            
            const schemaGraph = JSON.parse(schemaGraphStr);

            // 【关键修复】在调用实例提取前，先保存当前画布状态到数据库
            // 这样后端才能从 db_project.graph_data 中获取到正确的类结构
            await projectsApi.updateProject(Number(projectId), {
                graph_data: { nodes, edges },
            });
            await projectsApi.updateOntology(Number(projectId), { nodes, edges });
            message.info('已保存当前画布状态，开始解析文件...');

            // 使用新的 parseFiles API 解析文件获取文本内容
            const parseResponse = await projectsApi.parseFiles(Number(projectId), filesToUse);
            
            // 获取解析后的文本内容（parseResponse 已经是 response.data）
            const textContent = parseResponse?.text_content || '';
            if (!textContent) {
                message.error('文件解析失败，未获取到文本内容');
                return;
            }
            
            // 保存文本内容到 localStorage
            localStorage.setItem(`project_${projectId}_text_content`, textContent);

            // 使用异步模式进行实例提取
            // 使用 TTL 导入的 schema，实例会添加到已有类结构上
            const instanceResponse = await projectsApi.extractInstances(Number(projectId), {
                text_content: textContent,
                schema_graph: schemaGraph,  // 使用 TTL 的 schema，不是新提取的
                chunk_size: 15000,
                chunk_overlap: 500,
                request_interval: 2,
                async_mode: true,
            });

            // 如果返回 task_id，说明是异步任务
            if (instanceResponse.task_id) {
                setCurrentTaskId(instanceResponse.task_id);
                setIsProgressModalOpen(true);
                setTaskStatus('running');
                setTaskProgress(0);
                setTaskMessage('开始实例提取...');
                connectToProgressStream(instanceResponse.task_id);
                message.info('任务已启动，请在进度窗口查看进度');
            } else if (instanceResponse.graph_data) {
                // 同步模式返回结果
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    instanceResponse.graph_data.nodes || [],
                    instanceResponse.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                let successMsg = instanceResponse.message || `实例提取完成：${instanceResponse.instances?.length || 0} 个实例`;
                if (instanceResponse.discarded_edges_count > 0) {
                    successMsg += ` (⚠️ ${instanceResponse.discarded_edges_count} 条不合规连线已自动丢弃)`;
                }
                message.success(successMsg);
            } else {
                message.warning('实例提取完成，但未发现有效实例');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`实例提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
            // 只在没有传入 files 参数时才清空 pendingFiles（避免重复清空）
            if (!files) {
                setPendingFiles([]);
            }
        }
    };

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

    const handleDownloadTTL = async () => {
        if (!projectId) return;

        try {
            await projectsApi.downloadTTL(Number(projectId));
            message.success('TTL 文件已开始下载');
        } catch (error: any) {
            message.error(error.response?.data?.detail || '下载 TTL 文件失败');
        }
    };

    const expandAllInstances = () => {
        const classIdsWithInstances = new Set<string>();
        
        // 遍历所有边，找到 rdf:type 关系（实例 -> 类）
        edges.forEach(edge => {
            const label = edge.data?.label || edge.label || '';
            const relation = edge.data?.relation || '';
            const isInstanceRelation = label === 'rdf:type' || label === 'type' || relation === 'instance_of';
            if (isInstanceRelation) {
                const classId = String(edge.target);
                classIdsWithInstances.add(classId);
            }
        });

        const hasExpandedInstances = Array.from(expandedNodeIds).some(id => classIdsWithInstances.has(id));

        if (hasExpandedInstances) {
            setExpandedNodeIds(new Set());
            message.success('已收起所有实例');
        } else {
            setExpandedNodeIds(classIdsWithInstances);
            if (classIdsWithInstances.size > 0) {
                message.success(`已展开 ${classIdsWithInstances.size} 个类的实例`);
            } else {
                // 检查是否有实例节点存在
                const instanceNodes = nodes.filter(n => n.data?.type === 'owl:NamedIndividual');
                if (instanceNodes.length > 0) {
                    // 有实例但没有找到关联的类，可能是 rdf:type 关系缺失
                    message.warning('发现实例节点，但未找到实例与类的关联关系。请确保实例已通过 rdf:type 关系关联到类。');
                } else {
                    message.info('当前没有实例节点。请先提取实例或手动添加实例。');
                }
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

    const getDisplayElements = useCallback(() => {
        const visibleNodeIds = new Set<string>();
        const classToInstances: Map<string, string[]> = new Map();
        const instanceToClass: Map<string, string> = new Map();
        
        edges.forEach(edge => {
            const label = edge.data?.label || edge.label || '';
            const relation = edge.data?.relation || '';
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
                visibleNodeIds.add(node.id);
                if (expandedNodeIds.has(node.id)) {
                    const instances = classToInstances.get(node.id) || [];
                    instances.forEach(instanceId => visibleNodeIds.add(instanceId));
                }
            } else if (node.data?.type === 'owl:NamedIndividual') {
                const parentClassId = instanceToClass.get(node.id);
                if (parentClassId && expandedNodeIds.has(parentClassId)) {
                    visibleNodeIds.add(node.id);
                }
            } else {
                visibleNodeIds.add(node.id);
            }
        });

        const displayNodes = nodes.filter(n => visibleNodeIds.has(n.id));

        const displayEdges = edges.filter(e => {
            const sourceId = String(e.source);
            const targetId = String(e.target);
            return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds]);

    const { displayNodes, displayEdges } = getDisplayElements();

    const addNewRelation = () => {
        relationForm.resetFields();
        setIsAddRelationModalOpen(true);
    };

    const handleConfirmNewRelation = async () => {
        try {
            const values = await relationForm.validateFields();
            const { sourceNodeId, targetNodeId, relationType } = values;

            const newEdge: OntologyEdge = {
                id: `edge_${Date.now()}_${sourceNodeId}_${targetNodeId}`,
                source: sourceNodeId,
                target: targetNodeId,
                data: { label: relationType, relation: relationType },
            } as OntologyEdge;

            setEdges((eds) => [...eds, newEdge]);
            message.success('关系已创建');
            setIsAddRelationModalOpen(false);
        } catch (error) {
            console.error('创建关系失败:', error);
        }
    };

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

    // ==================== 文档管理相关函数 ====================

    // 加载已上传文档列表
    const loadDocuments = useCallback(async () => {
        if (!projectId) return;
        setIsDocLoading(true);
        try {
            const response = await projectsApi.getDocuments(Number(projectId));
            // 确保 uploadedDocuments 是数组，并映射后端字段到前端字段
            const docsArray = Array.isArray(response) ? response : (response.documents || response.data || []);
            const mappedDocs = docsArray.map((doc: any) => ({
                id: doc.id,
                file_name: doc.filename || doc.file_name,  // 后端返回 filename
                file_size: doc.file_size,
                uploaded_at: doc.created_at || doc.uploaded_at,  // 后端返回 created_at
            }));
            setUploadedDocuments(mappedDocs);
        } catch (error: any) {
            message.error(error.response?.data?.detail || '加载文档列表失败');
            setUploadedDocuments([]);
        } finally {
            setIsDocLoading(false);
        }
    }, [projectId]);

    // 删除单个文档
    const handleDeleteDocument = useCallback(async (docId: number, docName: string) => {
        if (!projectId) return;
        
        Modal.confirm({
            title: '确认删除',
            content: `确定要删除文档"${docName}"吗？`,
            okText: '确定',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: async () => {
                try {
                    await projectsApi.deleteDocument(Number(projectId), docId);
                    message.success('文档已删除');
                    // 重新加载文档列表
                    loadDocuments();
                } catch (error: any) {
                    message.error(error.response?.data?.detail || '删除文档失败');
                }
            },
        });
    }, [projectId, loadDocuments]);

    // 清空所有文档
    const handleClearAllDocuments = useCallback(async () => {
        if (!projectId) return;
        
        Modal.confirm({
            title: '确认清空',
            content: '确定要清空该项目下所有文档吗？此操作不可恢复！',
            okText: '确定',
            cancelText: '取消',
            okButtonProps: { danger: true },
            onOk: async () => {
                try {
                    await projectsApi.clearAllDocuments(Number(projectId));
                    message.success('所有文档已清空');
                    setUploadedDocuments([]);
                    setIsDocumentModalOpen(false);
                } catch (error: any) {
                    message.error(error.response?.data?.detail || '清空文档失败');
                }
            },
        });
    }, [projectId]);

    // 打开文档管理 Modal
    const handleOpenDocumentModal = useCallback(() => {
        setIsDocumentModalOpen(true);
        loadDocuments();
    }, [loadDocuments]);

    // 上传文档到数据库（只保存，不自动开始提取）
    const handleUploadDocuments = useCallback(async (files: File[]) => {
        if (!projectId || files.length === 0) return;
        
        setLoading(true);
        try {
            // 使用 parseFiles API 解析文件并保存到数据库
            const response = await projectsApi.parseFiles(Number(projectId), files);
            
            message.success(`已上传 ${files.length} 个文档，请在文档管理界面查看`);
            
            // 刷新文档列表
            loadDocuments();
        } catch (error: any) {
            message.error(error.response?.data?.detail || '上传文档失败');
        } finally {
            setLoading(false);
        }
    }, [projectId, loadDocuments]);

    // 连接 SSE 进度流（提前定义，避免循环引用）
    const connectToProgressStream = useCallback((taskId: string) => {
        if (!projectId) return;
        
        // 关闭之前的连接
        const existingEs = eventSourceRef.current;
        if (existingEs) {
            existingEs.close();
            eventSourceRef.current = null;
        }

        const baseUrl = window.location.origin;
        // 注意：登录时存储的是 'access_token'，不是 'token'
        const token = localStorage.getItem('access_token') || '';
        
        // EventSource 不支持 headers，使用 URL 参数传递 token
        const es = new EventSource(`${baseUrl}/api/projects/${projectId}/task/${taskId}/progress-stream?token=${token}`);
        
        console.log('[SSE] 连接建立:', taskId);
        
        // 处理 progress 事件（常规进度更新）
        es.addEventListener('progress', (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                console.log('[SSE] 进度更新:', data);
                setTaskProgress(data.progress || 0);
                setTaskMessage(data.message || '');
                setTaskDetail(data.detail || '');
                setTaskStatus(data.status || 'running');
            } catch (e) {
                console.error('[SSE] progress 消息解析失败:', e, event.data);
            }
        });
        
        // 处理 completed 事件
        es.addEventListener('completed', (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                console.log('[SSE] 任务完成:', data);
                
                // 从任务结果中获取 graph_data 并更新画布
                if (data.result && data.result.graph_data) {
                    const graphData = data.result.graph_data;
                    const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                        graphData.nodes || [],
                        graphData.edges || []
                    );
                    setNodes(layoutedNodes);
                    setEdges(layoutedEdges);
                    
                    // 同时保存 schema_graph 和 text_content 到 localStorage 供实例提取使用
                    if (data.result.schema_graph) {
                        localStorage.setItem(`project_${projectId}_schema_graph`, JSON.stringify(data.result.schema_graph));
                    }
                    if (data.result.text_content) {
                        localStorage.setItem(`project_${projectId}_text_content`, data.result.text_content);
                    }
                    
                    // 如果是实例提取完成（结果中包含 instances），自动展开所有实例
                    if (data.result.instances && data.result.instances.length > 0) {
                        // 找出所有有实例的类 ID
                        const classIdsWithInstances = new Set<string>();
                        const updatedEdges = layoutedEdges || [];
                        updatedEdges.forEach(edge => {
                            const label = edge.data?.label || edge.label || '';
                            const isInstanceRelation = label === 'rdf:type' || label === 'type';
                            if (isInstanceRelation) {
                                const classId = String(edge.target);
                                classIdsWithInstances.add(classId);
                            }
                        });
                        
                        if (classIdsWithInstances.size > 0) {
                            setExpandedNodeIds(classIdsWithInstances);
                            console.log('[SSE] 实例提取完成，自动展开类:', Array.from(classIdsWithInstances));
                        }
                    }
                }
                
                // 构建成功消息（支持实例提取的 discarded_edges_count）
                let successMsg = data.message || '任务完成！';
                if (data.result && data.result.discarded_edges_count > 0) {
                    successMsg += ` (⚠️ ${data.result.discarded_edges_count} 条不合规连线已自动丢弃)`;
                }
                
                setIsProgressModalOpen(false);
                message.success(successMsg);
                es.close();
                eventSourceRef.current = null;
            } catch (e) {
                console.error('[SSE] completed 消息解析失败:', e, event.data);
            }
        });
        
        // 处理 failed 事件
        es.addEventListener('failed', (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                console.log('[SSE] 任务失败:', data);
                setIsProgressModalOpen(false);
                message.error(data.error || data.message || '任务失败');
                es.close();
                eventSourceRef.current = null;
            } catch (e) {
                console.error('[SSE] failed 消息解析失败:', e, event.data);
            }
        });
        
        // 处理 cancelled 事件
        es.addEventListener('cancelled', (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                console.log('[SSE] 任务取消:', data);
                setIsProgressModalOpen(false);
                message.info('任务已取消');
                es.close();
                eventSourceRef.current = null;
            } catch (e) {
                console.error('[SSE] cancelled 消息解析失败:', e, event.data);
            }
        });
        
        // 处理 error 事件
        es.addEventListener('error', (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                console.error('[SSE] 错误:', data);
                setIsProgressModalOpen(false);
                message.error(data.error || 'SSE 连接错误');
                es.close();
                eventSourceRef.current = null;
            } catch (e) {
                console.error('[SSE] error 消息解析失败:', e, event.data);
            }
        });
        
        es.onopen = () => {
            console.log('[SSE] 连接已打开');
        };
        
        es.onerror = (error) => {
            console.error('[SSE] 连接错误:', error);
            // 只在非正常关闭时显示错误
            if (es.readyState === EventSource.CLOSED) {
                console.log('[SSE] 连接已正常关闭');
            } else if (es.readyState === EventSource.CONNECTING) {
                console.log('[SSE] 正在重连...');
            } else {
                // 只在真正错误时显示提示
                console.log('[SSE] 连接断开，可能是任务已完成');
            }
            es.close();
            if (eventSourceRef.current === es) {
                eventSourceRef.current = null;
            }
        };
        
        eventSourceRef.current = es;
    }, [projectId]);

    // 从 Modal 开始骨架提取
    const handleStartSchemaExtractionFromModal = useCallback(async () => {
        if (!projectId || uploadedDocuments.length === 0) {
            message.warning('请先上传文档再进行骨架提取');
            return;
        }
        // 关闭文档管理 Modal
        setIsDocumentModalOpen(false);
        // 打开规则配置弹窗
        setIsRuleModalOpen(true);
    }, [projectId, uploadedDocuments]);

    // 从规则配置 Modal 开始骨架提取（使用文档 ID 调用 API）
    const handleStartSchemaExtractionWithDocIds = useCallback(async () => {
        if (!projectId || uploadedDocuments.length === 0) {
            message.warning('请先上传文档再进行骨架提取');
            return;
        }

        const values = await ruleForm.validateFields();
        setIsRuleModalOpen(false);
        setLoading(true);

        try {
            // 使用文档 ID 列表调用新的 API
            const docIds = uploadedDocuments.map(doc => doc.id);
            
            // 使用异步模式，支持取消
            const response = await projectsApi.extractSchemaFromDocuments(Number(projectId), docIds, {
                user_intent: values.scenario,
                chunk_size: 15000,
                chunk_overlap: 500,
                request_interval: 2,
                async_mode: true,
            });

            // 如果返回 task_id，说明是异步任务
            if (response.task_id) {
                setCurrentTaskId(response.task_id);
                setIsProgressModalOpen(true);
                setTaskStatus('running');
                setTaskProgress(0);
                setTaskMessage('开始骨架提取...');
                connectToProgressStream(response.task_id);
                message.info('任务已启动，请在进度窗口查看进度');
            } else if (response.schema_graph && response.graph_data) {
                // 同步模式返回结果
                const textContent = response.text_content || '';
                localStorage.setItem(`project_${projectId}_text_content`, textContent);
                localStorage.setItem(`project_${projectId}_schema_graph`, JSON.stringify(response.schema_graph));

                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.graph_data.nodes || [],
                    response.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                message.success(response.message || `骨架提取完成！`);
            } else {
                message.warning('提取完成，但未发现有效的本体节点');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`Schema 提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
        }
    }, [projectId, uploadedDocuments, connectToProgressStream]);

    // 从 Modal 开始实例提取（基于已上传文档 ID）
    const handleStartInstanceExtractionFromModal = useCallback(async () => {
        if (!projectId || uploadedDocuments.length === 0) {
            message.warning('请先上传文档再进行实例提取');
            return;
        }

        // 获取已保存的 schema
        const schemaGraphStr = localStorage.getItem(`project_${projectId}_schema_graph`);
        if (!schemaGraphStr) {
            message.warning('请先提取骨架再进行实例提取');
            return;
        }

        // 关闭文档管理 Modal
        setIsDocumentModalOpen(false);
        setLoading(true);

        try {
            // 【关键修复】在调用实例提取前，先保存当前画布状态到数据库
            await projectsApi.updateProject(Number(projectId), {
                graph_data: { nodes, edges },
            });
            await projectsApi.updateOntology(Number(projectId), { nodes, edges });
            message.info('已保存当前画布状态，开始实例提取...');

            // 获取文档 ID 列表
            const docIds = uploadedDocuments.map(doc => doc.id);

            // 使用新的 API 基于已上传文档 ID 进行实例提取
            const response = await projectsApi.extractInstancesFromDocuments(Number(projectId), docIds, {
                chunk_size: 15000,
                chunk_overlap: 500,
                request_interval: 2,
                async_mode: true,
            });

            // 如果返回 task_id，说明是异步任务
            if (response.task_id) {
                setCurrentTaskId(response.task_id);
                setIsProgressModalOpen(true);
                setTaskStatus('running');
                setTaskProgress(0);
                setTaskMessage('开始实例提取...');
                connectToProgressStream(response.task_id);
                message.info('任务已启动，请在进度窗口查看进度');
            } else if (response.graph_data) {
                // 同步模式返回结果
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    response.graph_data.nodes || [],
                    response.graph_data.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                
                let successMsg = response.message || `实例提取完成：${response.instances?.length || 0} 个实例`;
                if (response.discarded_edges_count > 0) {
                    successMsg += ` (⚠️ ${response.discarded_edges_count} 条不合规连线已自动丢弃)`;
                }
                message.success(successMsg);
            } else {
                message.warning('实例提取完成，但未发现有效实例');
            }
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`实例提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
        }
    }, [projectId, uploadedDocuments, nodes, edges, connectToProgressStream]);

    // 构建树形数据（带搜索过滤）
    const buildTreeData = useCallback(() => {
        const classNodes = nodes.filter(n => n.data?.type === 'owl:Class');
        const instanceNodes = nodes.filter(n => n.data?.type === 'owl:NamedIndividual');

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

        // 搜索过滤
        const filterNode = (title: string) => {
            if (!treeSearchValue) return true;
            return title.toLowerCase().includes(treeSearchValue.toLowerCase());
        };

        return classNodes.map(classNode => {
            const classTitle = classNode.data?.label || '未命名类';
            const children = classToInstances[classNode.id]?.map(instance => {
                const instanceTitle = instance.data?.label || '未命名实例';
                return {
                    title: instanceTitle,
                    key: instance.id,
                    icon: <span className="inline-block w-3 h-3 rounded-full bg-[#f79767] mr-2" />,
                    isLeaf: true,
                    searchableTitle: instanceTitle,
                };
            }) || [];

            return {
                title: classTitle,
                key: classNode.id,
                icon: <span className="inline-block w-3 h-3 rounded-full bg-[#4cc9f0] mr-2" />,
                children,
                searchableTitle: classTitle,
            };
        }).filter(node => {
            // 如果节点本身或子节点匹配搜索，则显示
            if (!treeSearchValue) return true;
            const selfMatch = node.searchableTitle?.toLowerCase().includes(treeSearchValue.toLowerCase());
            const childrenMatch = node.children?.some((child: any) => 
                child.searchableTitle?.toLowerCase().includes(treeSearchValue.toLowerCase())
            );
            return selfMatch || childrenMatch;
        });
    }, [nodes, edges, treeSearchValue]);

    // 树节点点击
    const onTreeSelect: TreeProps['onSelect'] = (selectedKeys) => {
        if (selectedKeys.length === 0) return;
        const key = selectedKeys[0] as string;
        const node = nodes.find(n => n.id === key);
        if (node) {
            onNodeClick(node);
        }
    };

    // 全部展开/折叠（仅控制列表内部显示，不影响画布）
    const expandAllTreeNodes = () => {
        // 展开所有节点（包括类和其实例）
        const allKeys = new Set(buildTreeData().map((node: any) => node.key));
        setManualExpandedKeys(allKeys);
        message.success('已展开列表');
    };

    const collapseAllTreeNodes = () => {
        setManualExpandedKeys(new Set());
        message.success('已收起列表');
    };

    // 处理单个节点的展开/收起
    const onTreeExpand = (keys: React.Key[]) => {
        setManualExpandedKeys(new Set(keys as string[]));
    };

    // 更多操作菜单（已移除树形列表操作，因为左侧面板已有独立按钮）
    const moreMenuItems: MenuProps['items'] = [];

    const classCount = nodes.filter(n => n.data?.type === 'owl:Class').length;
    const instanceCount = nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length;


    // 取消任务
    const handleCancelTask = useCallback(async () => {
        if (!projectId || !currentTaskId) return;
        
        Modal.confirm({
            title: '确认取消',
            content: '确定要取消当前任务吗？',
            okText: '确定',
            cancelText: '取消',
            onOk: async () => {
                try {
                    // 注意：登录时存储的是 'access_token'，不是 'token'
                    const token = localStorage.getItem('access_token');
                    console.log('[取消任务] 获取 token:', token ? '已获取' : 'null');
                    
                    if (!token) {
                        message.error('未找到认证 token，请重新登录');
                        return;
                    }
                    
                    const baseUrl = window.location.origin;
                    
                    // 使用 URL 参数传递 token（与 SSE 一致）
                    const response = await fetch(`${baseUrl}/api/projects/${projectId}/task/${currentTaskId}/cancel?token=${token}`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                    });
                    
                    // 处理空响应情况
                    const responseText = await response.text();
                    console.log('[取消任务] 响应状态:', response.status, '响应内容:', responseText);
                    
                    if (response.ok || response.status === 200) {
                        message.info('任务取消请求已发送');
                        // 关闭 SSE 连接
                        if (eventSourceRef.current) {
                            eventSourceRef.current.close();
                            eventSourceRef.current = null;
                        }
                        // 更新 UI 状态
                        setTaskStatus('cancelled');
                        setTaskMessage('任务已取消');
                    } else {
                        let errorMsg = '取消失败';
                        try {
                            if (responseText) {
                                const result = JSON.parse(responseText);
                                errorMsg = result.message || errorMsg;
                            }
                        } catch (e) {
                            console.log('无法解析响应为 JSON');
                        }
                        message.error(errorMsg);
                    }
                } catch (error: any) {
                    console.error('[取消任务] 错误:', error);
                    message.error('取消任务失败：' + (error.message || '未知错误'));
                }
            },
        });
    }, [projectId, currentTaskId]);

    // 清理 SSE 连接
    useEffect(() => {
        return () => {
            if (eventSource) {
                eventSource.close();
            }
        };
    }, [eventSource]);

    return (
        <div className="h-screen flex flex-col bg-gray-50">
            {(!projectId || projectId.trim() === '' || isCreatingProject) ? (
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
                                        description: values.description,
                                        domain_id: selectedDomainId,
                                        domain_name: selectedDomainName,
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
                            <Form.Item name="name" label="项目名称" rules={[{ required: true, message: '请输入项目名称' }]}>
                                <Input placeholder="例如：工业本体" />
                            </Form.Item>
                            <Form.Item name="description" label="项目描述">
                                <Input.TextArea rows={4} placeholder="简要描述这个项目的用途..." />
                            </Form.Item>
                            <Form.Item 
                                name="domainId" 
                                label="知识域" 
                                rules={[{ required: true, message: '请选择或创建一个知识域' }]}
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
                                    <Button onClick={() => navigate('/my-projects')}>取消</Button>
                                    <Button type="primary" htmlType="submit" className="bg-blue-600">创建项目</Button>
                                </Space>
                            </Form.Item>
                        </Form>
                    </div>
                </div>
            ) : (
                // 使用 Flex 布局实现平推式响应
                <div className="flex-1 flex overflow-hidden">
                    {/* 左侧展开面板 - 使用 Flex 布局，展开时推挤主内容区 */}
                    <div
                        className={`bg-white transition-all duration-300 ease-in-out flex-shrink-0 relative ${
                            isLeftPanelExpanded ? 'w-[380px] shadow-lg' : 'w-0'
                        }`}
                        style={{
                            boxShadow: isLeftPanelExpanded ? '4px 0 12px rgba(0, 0, 0, 0.1)' : 'none',
                            borderRight: isLeftPanelExpanded ? '1px solid #e5e7eb' : 'none'
                        }}
                    >
                        {/* 收起按钮 - 仅在面板展开时显示在面板右侧边缘 */}
                        {isLeftPanelExpanded && (
                            <button
                                className="absolute top-1/2 -translate-y-1/2 z-[100] bg-white shadow-md rounded-r-lg p-2 hover:bg-gray-50 transition-all duration-300 border border-gray-200 border-l-0"
                                style={{
                                    right: '-32px',
                                }}
                                onClick={() => setIsLeftPanelExpanded(false)}
                                title="收起列表"
                            >
                                <LeftOutlined />
                            </button>
                        )}
                        
                        <div className="h-full flex flex-col overflow-hidden" style={{ minWidth: isLeftPanelExpanded ? '380px' : '0' }}>
                            
                            {/* 面板头部 - 搜索和操作 */}
                            <div className="p-3 border-b border-gray-100 flex-shrink-0">
                                <div className="flex items-center justify-between mb-2">
                                    <h3 className="font-semibold text-gray-700 flex items-center text-sm">
                                        <UnorderedListOutlined className="mr-2 text-gray-500" />
                                        类与实例列表
                                    </h3>
                                    <div className="flex items-center gap-1">
                                        <Tooltip title="全部展开">
                                            <Button type="text" size="small" icon={<ExpandOutlined />} onClick={expandAllTreeNodes} />
                                        </Tooltip>
                                        <Tooltip title="全部收起">
                                            <Button type="text" size="small" icon={<ShrinkOutlined />} onClick={collapseAllTreeNodes} />
                                        </Tooltip>
                                    </div>
                                </div>
                                {/* 搜索框 */}
                                <Search
                                    placeholder="搜索类或实例..."
                                    size="small"
                                    value={treeSearchValue}
                                    onChange={(e) => setTreeSearchValue(e.target.value)}
                                    allowClear
                                    prefix={<SearchOutlined className="text-gray-400" />}
                                />
                            </div>
                            
                            {/* 树形列表 */}
                            <div className="flex-1 overflow-auto p-2">
                                <style>{`
                                    .custom-tree .ant-tree-treenode {
                                        padding: 2px 0;
                                    }
                                    .custom-tree .ant-tree-node-content-wrapper {
                                        padding: 2px 8px;
                                        min-height: 24px;
                                        line-height: 20px;
                                    }
                                    .custom-tree .ant-tree-indent-unit {
                                        width: 16px;
                                    }
                                    .custom-tree .ant-tree-switcher {
                                        width: 20px;
                                        height: 24px;
                                        line-height: 24px;
                                    }
                                `}</style>
                                <Tree
                                    showIcon
                                    expandedKeys={Array.from(manualExpandedKeys)}
                                    onExpand={onTreeExpand}
                                    selectedKeys={selectedElement ? [selectedElement.id] : []}
                                    onSelect={onTreeSelect}
                                    treeData={buildTreeData()}
                                    blockNode
                                    className="custom-tree"
                                />
                            </div>
                            
                            {/* 底部统计 */}
                            <div className="p-3 border-t border-gray-100 flex-shrink-0 bg-gray-50">
                                <div className="flex items-center justify-between text-xs text-gray-500">
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full bg-[#4cc9f0] mr-1.5" />
                                        类：{classCount}
                                    </span>
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full bg-[#f79767] mr-1.5" />
                                        实例：{instanceCount}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 主内容区 - 使用 Flex 布局自动适应剩余空间 */}
                    <div className="flex-1 flex flex-col min-w-0 relative h-full">
                        <Navbar breadcrumbs={breadcrumbs} />

                        {/* 展开按钮 - 仅在面板收起时显示 */}
                        {!isLeftPanelExpanded && (
                            <button
                                className="absolute top-1/2 -translate-y-1/2 z-30 bg-white shadow-md rounded-r-lg p-2 hover:bg-gray-50 transition-all duration-300"
                                style={{ left: '0px' }}
                                onClick={() => setIsLeftPanelExpanded(true)}
                                title="展开列表"
                            >
                                <RightOutlined />
                            </button>
                        )}
                        
                        {loading && (
                            <div className="absolute inset-0 bg-white bg-opacity-75 flex items-center justify-center z-50">
                                <Spin size="large" tip="正在加载..." />
                            </div>
                        )}

                        {/* 顶部工具栏 - 优化分组和视觉 */}
                        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
                            <div className="flex items-center gap-1 bg-white/95 backdrop-blur-sm px-2 py-1.5 rounded-lg shadow-lg border border-gray-200">
                                {/* 数据导入/导出组 */}
                                <div className="flex items-center gap-1 pr-2 border-r border-gray-200">
                                    {/* 隐藏的 LLM 文件输入 */}
                                    <input
                                        id="llm-schema-input"
                                        type="file"
                                        accept=".txt,.pdf,.doc,.docx"
                                        multiple
                                        style={{ display: 'none' }}
                                        onChange={async (e) => {
                                            const files = Array.from(e.target.files || []);
                                            if (files.length > 0) {
                                                // 检查是否已有 schema（TTL 导入或之前已提取过）
                                                const schemaGraphStr = localStorage.getItem(`project_${projectId}_schema_graph`);
                                                
                                                // 检查是否是从"需要上传文档"Modal 或文档管理 Modal 触发的实例提取
                                                const shouldExtractAfterUpload = window.shouldExtractInstancesAfterFileSelect === true;
                                                
                                                // 如果已有 schema，直接上传文件到数据库
                                                if (schemaGraphStr) {
                                                    // 上传文件到数据库保存
                                                    await handleUploadDocuments(files);
                                                    
                                                    // 如果是从实例提取流程触发的，直接开始实例提取
                                                    if (shouldExtractAfterUpload) {
                                                        // 重置标志
                                                        window.shouldExtractInstancesAfterFileSelect = false;
                                                        // 等待文件上传完成后，使用新上传的文件进行实例提取
                                                        await handleStartInstanceExtractionWithFiles(files);
                                                    } else {
                                                        // 否则打开文档管理 Modal 让用户浏览
                                                        handleOpenDocumentModal();
                                                    }
                                                } else {
                                                    // 第一次提取骨架：先上传文件到数据库，然后打开文档管理 Modal
                                                    // 让用户浏览文档情况，再点击"开始骨架提取"进入规则配置
                                                    await handleUploadDocuments(files);
                                                    // 打开文档管理 Modal
                                                    handleOpenDocumentModal();
                                                }
                                            }
                                            e.target.value = '';
                                        }}
                                    />
                                    {/* 隐藏的 TTL 文件输入 */}
                                    <input
                                        id="ttl-schema-input"
                                        type="file"
                                        accept=".ttl"
                                        multiple
                                        style={{ display: 'none' }}
                                        onChange={(e) => {
                                            const files = Array.from(e.target.files || []);
                                            if (files.length > 0) {
                                                handleUploadTTLSchema(files);
                                            }
                                            e.target.value = '';
                                        }}
                                    />
                                    <Tooltip title={localStorage.getItem(`project_${projectId}_schema_graph`) ? "重新提取骨架" : "提取骨架（支持多选）"}>
                                        <Button 
                                            size="small" 
                                            type="primary" 
                                            icon={<CloudUploadOutlined />} 
                                            className="bg-indigo-600 hover:bg-indigo-700 border-none"
                                            onClick={handleSchemaButtonClick}
                                        >
                                            骨架
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title={localStorage.getItem(`project_${projectId}_schema_graph`) ? "提取实例" : "请先提取骨架"}>
                                        <Button 
                                            size="small"
                                            icon={<DatabaseOutlined />} 
                                            onClick={() => {
                                                if (localStorage.getItem(`project_${projectId}_schema_graph`)) {
                                                    // 有 schema 时，打开文档管理 Modal
                                                    handleOpenDocumentModal();
                                                } else {
                                                    message.warning('请先提取骨架，然后再进行实例提取');
                                                }
                                            }}
                                            className="bg-orange-500 text-white hover:bg-orange-600 border-none"
                                            disabled={!localStorage.getItem(`project_${projectId}_schema_graph`)}
                                        >
                                            实例
                                        </Button>
                                    </Tooltip>
                                    {/* 开始实例提取按钮 - 仅在文档管理 Modal 中显示 */}
                                    <Upload accept=".ttl" showUploadList={false} beforeUpload={handleUploadTTL}>
                                        <Tooltip title="上传 TTL 文件">
                                            <Button size="small" icon={<FileTextOutlined />} className="border-purple-500 text-purple-600 hover:bg-purple-50">
                                                TTL
                                            </Button>
                                        </Tooltip>
                                    </Upload>
                                </div>

                                {/* 节点操作组 */}
                                <div className="flex items-center gap-1 px-2 border-r border-gray-200">
                                    <Tooltip title="新增类">
                                        <Button size="small" icon={<PlusOutlined />} onClick={addNewClass} className="border-blue-500 text-blue-600 hover:bg-blue-50">
                                            类
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title="新增实例">
                                        <Button size="small" icon={<PlusOutlined />} onClick={addNewInstance} className="border-orange-500 text-orange-600 hover:bg-orange-50">
                                            实例
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title="创建关系">
                                        <Button size="small" icon={<LinkOutlined />} onClick={addNewRelation} className="border-gray-300 hover:bg-gray-50">
                                            关系
                                        </Button>
                                    </Tooltip>
                                </div>

                                {/* 全局保存组 */}
                                <div className="flex items-center gap-1 pl-2">
                                    <Tooltip title="展开/收起所有实例">
                                        <Button 
                                            size="small" 
                                            icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />} 
                                            onClick={expandAllInstances}
                                            className="border-cyan-500 text-cyan-600 hover:bg-cyan-50"
                                        >
                                            展开/收起所有实例
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title={hasUnsavedChanges ? "保存修改" : "已保存"}>
                                        <Button 
                                            size="small" 
                                            icon={<SaveOutlined />} 
                                            onClick={handleSaveDraft}
                                            loading={loading}
                                            type={hasUnsavedChanges ? "primary" : "default"}
                                            className={hasUnsavedChanges ? "bg-blue-600 hover:bg-blue-700 border-none" : ""}
                                        >
                                            保存
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title={isPublished ? "已发布" : "发布到资产中心"}>
                                        <Button 
                                            size="small"
                                            type={isPublished ? "default" : "primary"}
                                            icon={isPublished ? <EyeOutlined /> : <CloudServerOutlined />}
                                            onClick={handleTogglePublish}
                                            loading={loading}
                                            className={isPublished ? "" : "bg-green-600 hover:bg-green-700 border-none"}
                                        >
                                            {isPublished ? '已发布' : '发布'}
                                        </Button>
                                    </Tooltip>
                                    <Tooltip title="下载 TTL">
                                        <Button size="small" icon={<DownloadOutlined />} onClick={handleDownloadTTL} className="border-gray-300 hover:bg-gray-50" />
                                    </Tooltip>
                                    <Tooltip title="配置知识域">
                                        <Button 
                                            size="small" 
                                            icon={<DatabaseOutlined />} 
                                            onClick={handleOpenDomainModal}
                                            className={currentProjectDomain ? "border-indigo-500 text-indigo-600 hover:bg-indigo-50" : "border-gray-300 hover:bg-gray-50"}
                                        >
                                            {currentProjectDomain?.name || '知识域'}
                                        </Button>
                                    </Tooltip>
                                </div>
                            </div>
                        </div>

                        {/* 返回按钮 - 放在工具栏左侧 */}
                        <div className="absolute z-20" style={{ top: '76px', left: '16px' }}>
                            <Button
                                icon={<ArrowLeftOutlined />}
                                onClick={() => navigate('/my-projects')}
                                className="shadow-sm bg-white hover:bg-gray-50"
                            >
                                返回
                            </Button>
                        </div>


                        {/* 统计面板 - 右下角，浮于画布之上 */}
                        <div className="absolute bottom-4 right-4 z-[100]">
                            <div className="bg-white/90 backdrop-blur-sm px-3 py-2 rounded-lg shadow-lg border border-gray-200">
                                <div className="flex items-center gap-3 text-sm">
                                    <span className="text-gray-500"><InfoCircleOutlined className="mr-1" />视图:</span>
                                    <Tag color="blue" className="font-medium">{nodes.length}</Tag>
                                    <span className="text-gray-600">节点</span>
                                    <Tag color="green" className="font-medium">{edges.length}</Tag>
                                    <span className="text-gray-600">关系</span>
                                </div>
                            </div>
                        </div>

                        {/* 力导向图组件 - 填满整个可用空间 */}
                        <div className="absolute inset-0 w-full h-full" style={{ top: '64px' }}>
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
                                    const classId = node.id;
                                    setExpandedNodeIds(prev => {
                                        const newSet = new Set(prev);
                                        if (newSet.has(classId)) {
                                            newSet.delete(classId);
                                            message.info(`已收起 "${node.data?.label}" 的实例`);
                                        } else {
                                            newSet.add(classId);
                                            message.success(`已展开 "${node.data?.label}" 的实例`);
                                        }
                                        return newSet;
                                    });
                                }}
                                highlightNodeId={highlightNodeId}
                            />
                        </div>

                        {/* 属性编辑抽屉 */}
                        <Drawer
                            title={selectedElement ? (
                                'position' in selectedElement
                                    ? `编辑节点 - ${selectedElement.data?.label || '未命名'}`
                                    : `编辑关系 - ${selectedElement.data?.label || '未命名'}`
                            ) : "属性编辑"}
                            placement="right"
                            onClose={() => {
                                setIsDrawerOpen(false);
                                setSelectedElement(null);
                                form.resetFields();
                            }}
                            open={isDrawerOpen}
                            width={420}
                            destroyOnClose={true}
                            className="property-drawer"
                        >
                            {selectedElement && (
                                <Form
                                    form={form}
                                    layout="vertical"
                                    onFinish={handleSaveProperties}
                                >
                                    {'position' in selectedElement ? (
                                        <>
                                            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-100">
                                                <EditOutlined className="text-blue-500" />
                                                <span className="font-medium text-gray-700">节点属性</span>
                                            </div>
                                            
                                            <Form.Item name="label" label="节点名称" rules={[{ required: true, message: '请输入节点名称' }]}>
                                                <Input placeholder="请输入节点名称" />
                                            </Form.Item>

                                            <Form.Item name="type" label="节点类型">
                                                <Input disabled value={form.getFieldValue('type') === 'owl:Class' ? '类 (Class)' : '实例 (Individual)'} />
                                            </Form.Item>

                                            <div className="flex items-center gap-2 mb-3 pb-3 border-b border-gray-100">
                                                <TagsOutlined className="text-purple-500" />
                                                <span className="font-medium text-gray-700">自定义属性</span>
                                            </div>
                                            
                                            <Form.List name="properties">
                                                {(fields, { add, remove }) => (
                                                    <>
                                                        {fields.map(({ key, name, ...restField }) => (
                                                            <div key={key} className="flex gap-2 mb-2 items-start">
                                                                <Form.Item
                                                                    {...restField}
                                                                    name={[name, 'name']}
                                                                    rules={[{ required: true, message: '属性名不能为空' }]}
                                                                    className="flex-shrink-0 mb-0"
                                                                    style={{ width: '100px' }}
                                                                >
                                                                    <Input placeholder="属性名" size="small" />
                                                                </Form.Item>
                                                                <Form.Item
                                                                    {...restField}
                                                                    name={[name, 'value']}
                                                                    rules={[{ required: true, message: '属性值不能为空' }]}
                                                                    className="flex-1 mb-0"
                                                                >
                                                                    <Input.TextArea placeholder="属性值" autoSize={{ minRows: 1, maxRows: 4 }} size="small" />
                                                                </Form.Item>
                                                                <MinusCircleOutlined
                                                                    onClick={() => remove(name)}
                                                                    className="text-gray-400 hover:text-red-500 cursor-pointer mt-2 flex-shrink-0"
                                                                />
                                                            </div>
                                                        ))}
                                                        <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />} size="small" className="mt-2">
                                                            添加属性
                                                        </Button>
                                                    </>
                                                )}
                                            </Form.List>
                                        </>
                                    ) : (
                                        <>
                                            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-100">
                                                <LinkOutlined className="text-green-500" />
                                                <span className="font-medium text-gray-700">关系属性</span>
                                            </div>

                                            <Form.Item name="label" label="关系标签" rules={[{ required: true, message: '请输入关系标签' }]}>
                                                <Input placeholder="例如：关联、属于、包含" />
                                            </Form.Item>

                                            <Form.Item name="relation" label="关系类型" rules={[{ required: true, message: '请选择关系类型' }]}>
                                                <Select
                                                    showSearch
                                                    placeholder="选择或输入关系类型"
                                                    optionFilterProp="label"
                                                    options={relationTypes}
                                                />
                                            </Form.Item>
                                        </>
                                    )}

                                    <div className="flex justify-end gap-2 mt-6 pt-4 border-t border-gray-100">
                                        <Button onClick={() => {
                                            setIsDrawerOpen(false);
                                            setSelectedElement(null);
                                            form.resetFields();
                                        }}>
                                            取消
                                        </Button>
                                        <Button icon={<DeleteOutlined />} danger onClick={deleteSelectedElement}>
                                            删除
                                        </Button>
                                        <Button type="primary" htmlType="submit" className="bg-blue-600">
                                            保存
                                        </Button>
                                    </div>
                                </Form>
                            )}
                        </Drawer>

                        {/* 骨架提取方式选择 Modal */}
                        <Modal
                            title={<div className="flex items-center gap-2"><CloudUploadOutlined className="text-indigo-600" /><span>选择骨架构建方式</span></div>}
                            open={isSchemaTypeModalOpen}
                            onCancel={() => setIsSchemaTypeModalOpen(false)}
                            footer={null}
                            width={500}
                        >
                            <div className="py-4">
                                <p className="text-gray-600 mb-4">请选择构建骨架（类结构）的方式：</p>
                                <div className="space-y-3">
                                    <div 
                                        className={`p-4 border rounded-lg cursor-pointer transition-all ${schemaExtractionType === 'llm' ? 'border-indigo-500 bg-indigo-50' : 'border-gray-200 hover:border-gray-300'}`}
                                        onClick={() => setSchemaExtractionType('llm')}
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${schemaExtractionType === 'llm' ? 'border-indigo-500 bg-indigo-500' : 'border-gray-300'}`}>
                                                {schemaExtractionType === 'llm' && <div className="w-2 h-2 rounded-full bg-white" />}
                                            </div>
                                            <div>
                                                <div className="font-medium text-gray-800">大模型提取</div>
                                                <div className="text-sm text-gray-500">上传文档（TXT/PDF/DOC/DOCX），通过 AI 自动提取类结构</div>
                                            </div>
                                        </div>
                                    </div>
                                    <div 
                                        className={`p-4 border rounded-lg cursor-pointer transition-all ${schemaExtractionType === 'ttl' ? 'border-purple-500 bg-purple-50' : 'border-gray-200 hover:border-gray-300'}`}
                                        onClick={() => setSchemaExtractionType('ttl')}
                                    >
                                        <div className="flex items-center gap-3">
                                            <div className={`w-5 h-5 rounded-full border-2 flex items-center justify-center ${schemaExtractionType === 'ttl' ? 'border-purple-500 bg-purple-500' : 'border-gray-300'}`}>
                                                {schemaExtractionType === 'ttl' && <div className="w-2 h-2 rounded-full bg-white" />}
                                            </div>
                                            <div>
                                                <div className="font-medium text-gray-800">上传 TTL 文件</div>
                                                <div className="text-sm text-gray-500">上传已有的 TTL 本体文件，解析其中的类和关系</div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                                <div className="flex justify-end mt-6">
                                    <Space>
                                        <Button onClick={() => setIsSchemaTypeModalOpen(false)}>取消</Button>
                                        <Button type="primary" onClick={handleSchemaTypeConfirm} className="bg-indigo-600">
                                            确定
                                        </Button>
                                    </Space>
                                </div>
                            </div>
                        </Modal>

                        {/* 抽取规则 Modal */}
                        <Modal
                            title={<div className="flex items-center gap-2"><CloudUploadOutlined className="text-indigo-600" /><span>定义抽取规则</span></div>}
                            open={isRuleModalOpen}
                            onOk={() => {
                                // 判断是从文档管理 Modal 打开的（有 uploadedDocuments）还是从文件选择打开的（有 pendingFiles）
                                if (uploadedDocuments.length > 0 && pendingFiles.length === 0) {
                                    // 从文档管理 Modal 打开，使用文档 ID 调用 API
                                    handleStartSchemaExtractionWithDocIds();
                                } else {
                                    // 从文件选择打开，使用传统方式
                                    handleStartExtraction();
                                }
                            }}
                            onCancel={() => setIsRuleModalOpen(false)}
                            okText="开始提取"
                            cancelText="取消"
                            width={800}
                        >
                            <div className="mb-4 text-gray-500 text-sm">
                                配置主体、属性和关系，帮助 AI 更准确地提取内容。留空则按通用模式提取。
                            </div>
                            <Form form={ruleForm} layout="vertical">
                                <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-100">
                                    <DatabaseOutlined className="text-indigo-500" />
                                    <span className="font-medium text-gray-700">主体配置</span>
                                </div>
                                <Form.List name="classes">
                                    {(fields, { add, remove }) => (
                                        <>
                                            <div className="overflow-x-auto mb-3">
                                                <table className="w-full border border-gray-200 rounded-lg">
                                                    <thead className="bg-gray-50">
                                                        <tr>
                                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">主体 (Class)</th>
                                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">属性 (DataProp)</th>
                                                            <th className="px-3 py-2 text-left text-xs font-medium text-gray-500">关系 (ObjectProp)</th>
                                                            <th className="px-3 py-2 text-right text-xs font-medium text-gray-500 w-10">操作</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-gray-200">
                                                        {fields.map(({ key, name, ...restField }) => (
                                                            <tr key={key}>
                                                                <td className="px-3 py-2">
                                                                    <Form.Item {...restField} name={[name, 'class']} rules={[{ required: true, message: '主体不能为空' }]} className="mb-0">
                                                                        <Input placeholder="例如：技术与知识领域" size="small" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-3 py-2">
                                                                    <Form.Item {...restField} name={[name, 'properties']} className="mb-0">
                                                                        <Input placeholder="描述，成熟度" size="small" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-3 py-2">
                                                                    <Form.Item {...restField} name={[name, 'relations']} className="mb-0">
                                                                        <Input placeholder="支撑，应用于" size="small" />
                                                                    </Form.Item>
                                                                </td>
                                                                <td className="px-3 py-2 text-right">
                                                                    <MinusCircleOutlined onClick={() => remove(name)} className="text-red-500 hover:text-red-700 cursor-pointer" />
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                            <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />} size="small">添加配置行</Button>
                                        </>
                                    )}
                                </Form.List>

                                <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-100 mt-4">
                                    <InfoCircleOutlined className="text-blue-500" />
                                    <span className="font-medium text-gray-700">场景描述</span>
                                </div>
                                <Form.Item name="scenario" label="场景描述" tooltip="帮助 AI 理解上下文">
                                    <Input.TextArea rows={3} placeholder="例如：分析这份半导体行业研报..." />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 创建关系 Modal */}
                        <Modal title="创建新关系" open={isAddRelationModalOpen} onOk={handleConfirmNewRelation} onCancel={() => setIsAddRelationModalOpen(false)} okText="创建" cancelText="取消">
                            <Form form={relationForm} layout="vertical">
                                <Form.Item name="sourceNodeId" label="起始节点" rules={[{ required: true, message: '请选择起始节点' }]}>
                                    <Select options={nodes.map(node => ({ label: `${node.data.label} (${node.data.type})`, value: node.id }))} />
                                </Form.Item>
                                <Form.Item name="targetNodeId" label="目标节点" rules={[{ required: true, message: '请选择目标节点' }]}>
                                    <Select options={nodes.map(node => ({ label: `${node.data.label} (${node.data.type})`, value: node.id }))} />
                                </Form.Item>
                                <Form.Item name="relationType" label="关系类型" rules={[{ required: true, message: '请选择关系类型' }]}>
                                    <Select options={relationTypes} showSearch optionFilterProp="label" />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 新增实例 Modal */}
                        <Modal
                            title={<div className="flex items-center gap-2"><PlusOutlined className="text-orange-500" /><span>新增实例</span></div>}
                            open={isAddInstanceModalOpen}
                            onOk={handleConfirmAddInstance}
                            onCancel={() => { setIsAddInstanceModalOpen(false); addInstanceForm.resetFields(); }}
                            okText="创建"
                            cancelText="取消"
                        >
                            <div className="mb-3 text-gray-500 text-sm">
                                选择父类并输入实例名称，实例将通过 <Tag color="orange" className="mx-1">rdf:type</Tag> 关系关联到类。
                            </div>
                            <Form form={addInstanceForm} layout="vertical">
                                <Form.Item name="parentClassId" label="选择父类" rules={[{ required: true, message: '请选择一个类' }]}>
                                    <Select options={nodes.filter(n => n.data?.type === 'owl:Class').map(node => ({ label: node.data?.label || '未命名类', value: node.id }))} />
                                </Form.Item>
                                <Form.Item name="instanceLabel" label="实例名称" rules={[{ required: true, message: '请输入实例名称' }]}>
                                    <Input placeholder="请输入实例名称" />
                                </Form.Item>
                            </Form>
                        </Modal>

                        {/* 任务进度 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center gap-2">
                                    {taskStatus === 'running' && <LoadingOutlined className="text-blue-500 animate-spin" />}
                                    {taskStatus === 'completed' && <CheckCircleOutlined className="text-green-500" />}
                                    {taskStatus === 'failed' && <CloseCircleOutlined className="text-red-500" />}
                                    {taskStatus === 'cancelled' && <CloseCircleOutlined className="text-gray-500" />}
                                    <span>
                                        {taskStatus === 'running' && '任务进行中...'}
                                        {taskStatus === 'completed' && '任务完成'}
                                        {taskStatus === 'failed' && '任务失败'}
                                        {taskStatus === 'cancelled' && '任务已取消'}
                                        {taskStatus === 'pending' && '任务等待中'}
                                    </span>
                                </div>
                            }
                            open={isProgressModalOpen}
                            onCancel={() => {}}
                            footer={
                                <div className="flex justify-between">
                                    <Button 
                                        danger 
                                        icon={<StopOutlined />} 
                                        onClick={handleCancelTask}
                                        disabled={taskStatus === 'completed' || taskStatus === 'failed' || taskStatus === 'cancelled' || !currentTaskId}
                                    >
                                        取消任务
                                    </Button>
                                    <Button 
                                        type="primary" 
                                        onClick={() => setIsProgressModalOpen(false)}
                                        disabled={taskStatus === 'running' || taskStatus === 'pending'}
                                    >
                                        关闭
                                    </Button>
                                </div>
                            }
                            width={500}
                        >
                            <div className="space-y-4">
                                {/* 进度条 */}
                                <div>
                                    <div className="flex justify-between text-sm mb-1">
                                        <span className="text-gray-600">进度</span>
                                        <span className="font-medium">{Math.round(taskProgress * 100)}%</span>
                                    </div>
                                    <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
                                        <div 
                                            className={`h-full transition-all duration-300 ${
                                                taskStatus === 'completed' ? 'bg-green-500' :
                                                taskStatus === 'failed' ? 'bg-red-500' :
                                                taskStatus === 'cancelled' ? 'bg-gray-500' :
                                                'bg-blue-500'
                                            }`}
                                            style={{ width: `${taskProgress * 100}%` }}
                                        />
                                    </div>
                                </div>
                                
                                {/* 当前消息 */}
                                {taskMessage && (
                                    <div className="p-3 bg-blue-50 rounded-lg border border-blue-100">
                                        <div className="flex items-start gap-2">
                                            <LoadingOutlined className={`text-blue-500 mt-0.5 ${taskStatus !== 'running' ? 'hidden' : ''}`} />
                                            <div className="text-sm text-gray-700">{taskMessage}</div>
                                        </div>
                                    </div>
                                )}
                                
                                {/* 详细信息 */}
                                {taskDetail && (
                                    <div className="p-3 bg-gray-50 rounded-lg">
                                        <div className="text-xs text-gray-500 mb-1">详细信息</div>
                                        <div className="text-sm text-gray-700">{taskDetail}</div>
                                    </div>
                                )}
                                
                                {/* 任务 ID */}
                                {currentTaskId && (
                                    <div className="text-xs text-gray-400">
                                        任务 ID: {currentTaskId.slice(0, 8)}...{currentTaskId.slice(-4)}
                                    </div>
                                )}
                            </div>
                        </Modal>

                        {/* 文档管理 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center gap-2">
                                    <FileDoneOutlined className="text-green-500" />
                                    <span>已上传文档管理</span>
                                </div>
                            }
                            open={isDocumentModalOpen}
                            onCancel={() => setIsDocumentModalOpen(false)}
                            footer={null}
                            width={700}
                        >
                            <div className="py-2">
                                {isDocLoading ? (
                                    <div className="flex justify-center py-8">
                                        <Spin tip="加载中..." />
                                    </div>
                                ) : uploadedDocuments.length === 0 ? (
                                    <div className="text-center py-8 text-gray-400">
                                        <FileTextOutlined className="text-4xl mb-2" />
                                        <p>暂无已上传文档</p>
                                        <p className="text-sm mt-1 mb-4">请先上传文档进行骨架或实例提取</p>
                                        <Button 
                                            type="primary" 
                                            icon={<CloudUploadOutlined />}
                                            onClick={() => {
                                                setIsDocumentModalOpen(false);
                                                // 触发骨架按钮的文件选择
                                                const fileInput = document.getElementById('llm-schema-input');
                                                if (fileInput) {
                                                    fileInput.click();
                                                }
                                            }}
                                        >
                                            上传文档
                                        </Button>
                                    </div>
                                ) : (
                                    <>
                                        {/* 顶部操作栏 - 添加上传文档按钮 */}
                                        <div className="flex justify-between items-center mb-3 pb-3 border-b border-gray-100">
                                            <div className="text-sm text-gray-500">
                                                已上传 {uploadedDocuments.length} 个文档
                                            </div>
                                            <Button 
                                                type="primary" 
                                                icon={<CloudUploadOutlined />}
                                                onClick={() => {
                                                    // 触发骨架按钮的文件选择
                                                    const fileInput = document.getElementById('llm-schema-input');
                                                    if (fileInput) {
                                                        fileInput.click();
                                                    }
                                                }}
                                            >
                                                上传文档
                                            </Button>
                                        </div>
                                        
                                        <div className="max-h-80 overflow-auto">
                                            <table className="w-full text-sm">
                                                <thead className="bg-gray-50 sticky top-0">
                                                    <tr>
                                                        <th className="px-3 py-2 text-left font-medium text-gray-600">文件名</th>
                                                        <th className="px-3 py-2 text-left font-medium text-gray-600">大小</th>
                                                        <th className="px-3 py-2 text-left font-medium text-gray-600">上传时间</th>
                                                        <th className="px-3 py-2 text-center font-medium text-gray-600 w-24">操作</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-gray-100">
                                                    {uploadedDocuments.map((doc: any) => (
                                                        <tr key={doc.id} className="hover:bg-gray-50">
                                                            <td className="px-3 py-2">
                                                                <div className="flex items-center gap-2">
                                                                    <FileTextOutlined className="text-gray-400" />
                                                                    <span className="truncate max-w-xs">{doc.file_name}</span>
                                                                </div>
                                                            </td>
                                                            <td className="px-3 py-2 text-gray-500">
                                                                {(doc.file_size / 1024).toFixed(2)} KB
                                                            </td>
                                                            <td className="px-3 py-2 text-gray-500">
                                                                {doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleString('zh-CN') : '未知'}
                                                            </td>
                                                            <td className="px-3 py-2 text-center">
                                                                <Tooltip title="删除">
                                                                    <Button 
                                                                        type="text" 
                                                                        size="small" 
                                                                        danger
                                                                        icon={<DeleteOutlined />}
                                                                        onClick={() => handleDeleteDocument(doc.id, doc.file_name)}
                                                                    />
                                                                </Tooltip>
                                                            </td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>
                                    </>
                                )}
                            </div>
                            
                            {/* 底部操作区 */}
                            {uploadedDocuments.length > 0 && (
                                <div className="flex justify-between items-center pt-4 border-t border-gray-200 mt-4">
                                    <div className="text-sm text-gray-500">
                                        共 {uploadedDocuments.length} 个文档
                                    </div>
                                    <Space>
                                        <Button onClick={() => setIsDocumentModalOpen(false)}>关闭</Button>
                                        {/* 如果没有 schema，显示骨架提取按钮；如果有 schema，显示实例提取按钮 */}
                                        {!localStorage.getItem(`project_${projectId}_schema_graph`) ? (
                                            <Button 
                                                type="primary" 
                                                icon={<CloudUploadOutlined />} 
                                                onClick={handleStartSchemaExtractionFromModal}
                                                className="bg-indigo-600 hover:bg-indigo-700"
                                            >
                                                开始骨架提取
                                            </Button>
                                        ) : (
                                            <Button 
                                                type="primary" 
                                                icon={<DatabaseOutlined />} 
                                                onClick={handleStartInstanceExtractionFromModal}
                                                className="bg-orange-500 hover:bg-orange-600"
                                            >
                                                开始实例提取
                                            </Button>
                                        )}
                                    </Space>
                                </div>
                            )}
                        </Modal>

                        {/* 知识域配置 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center gap-2">
                                    <DatabaseOutlined className="text-indigo-600" />
                                    <span>配置知识域</span>
                                </div>
                            }
                            open={isDomainModalOpen}
                            onCancel={() => setIsDomainModalOpen(false)}
                            footer={null}
                            width={500}
                        >
                            <div className="py-4">
                                <p className="text-gray-600 mb-4">
                                    选择或创建本项目所属的知识领域。知识域用于对本体项目进行分类管理。
                                </p>
                                <Form layout="vertical">
                                    <Form.Item label="知识域" tooltip="选择已有知识域或创建新的知识域">
                                        <KnowledgeDomainSelector
                                            value={selectedDomainId}
                                            onChange={setSelectedDomainId}
                                            domainName={selectedDomainName}
                                            onDomainNameChange={setSelectedDomainName}
                                            placeholder="选择知识域"
                                        />
                                    </Form.Item>
                                </Form>
                                <div className="flex justify-end gap-2 mt-6 pt-4 border-t border-gray-100">
                                    <Button onClick={() => setIsDomainModalOpen(false)}>取消</Button>
                                    <Button 
                                        type="primary" 
                                        onClick={() => handleSaveDomain(selectedDomainId, selectedDomainName)}
                                        className="bg-indigo-600 hover:bg-indigo-700"
                                    >
                                        保存
                                    </Button>
                                </div>
                            </div>
                        </Modal>

                        {/* 系统配置 Modal */}
                        <Modal
                            title={<div className="flex items-center gap-2"><CloudServerOutlined className="text-blue-500" /><span>模型服务配置 (管理员)</span></div>}
                            open={isConfigModalOpen}
                            onOk={handleSaveConfig}
                            onCancel={() => setIsConfigModalOpen(false)}
                            width={600}
                            okText="保存"
                            cancelText="取消"
                            maskClosable={false}
                        >
                            <div className="bg-yellow-50 p-3 mb-4 rounded border border-yellow-100 flex gap-2">
                                <InfoCircleOutlined className="text-yellow-600 mt-0.5 flex-shrink-0" />
                                <div className="text-yellow-800 text-sm">配置将覆盖环境变量，修改后立即在提取任务中生效。</div>
                            </div>
                            <Form form={configForm} layout="vertical">
                                <div className="grid grid-cols-2 gap-3">
                                    <Form.Item name="base_url" label="API Endpoint" className="col-span-2" rules={[{ required: true, message: '请输入 API 端点' }]}>
                                        <Input placeholder="https://api.openai.com/v1" />
                                    </Form.Item>
                                    <Form.Item name="api_key" label="API Key" className="col-span-2" rules={[{ required: true, message: '请输入 API Key' }]}>
                                        <Input.Password placeholder="sk-..." />
                                    </Form.Item>
                                    <Form.Item name="model" label="模型名称" className="col-span-2" rules={[{ required: true, message: '请输入模型名称' }]}>
                                        <Input placeholder="gpt-3.5-turbo" />
                                    </Form.Item>
                                    <Form.Item name="chunk_size" label="分块大小"><Input type="number" suffix="字符" /></Form.Item>
                                    <Form.Item name="chunk_overlap" label="重叠"><Input type="number" suffix="字符" /></Form.Item>
                                    <Form.Item name="request_interval" label="请求间隔"><Input type="number" suffix="秒" /></Form.Item>
                                    <Form.Item name="streaming_enabled" valuePropName="checked" className="col-span-2">
                                        <Switch checkedChildren="流式启用" unCheckedChildren="流式禁用" />
                                    </Form.Item>
                                    <Form.Item name="neo4j_uri" label="Neo4j URI" className="col-span-2" rules={[{ required: true, message: '请输入 Neo4j URI' }]}>
                                        <Input placeholder="bolt://localhost:7687" />
                                    </Form.Item>
                                    <Form.Item name="neo4j_username" label="Neo4j 用户名"><Input placeholder="neo4j" /></Form.Item>
                                    <Form.Item name="neo4j_password" label="Neo4j 密码"><Input.Password placeholder="password" /></Form.Item>
                                    <Form.Item name="milvus_enabled" valuePropName="checked" className="col-span-2">
                                        <Switch checkedChildren="Milvus 启用" unCheckedChildren="Milvus 禁用" />
                                    </Form.Item>
                                    <Form.Item name="embedding_base_url" label="Embedding 地址" className="col-span-2"><Input placeholder="http://localhost:11434/v1" /></Form.Item>
                                    <Form.Item name="embedding_model" label="Embedding 模型" className="col-span-2"><Input placeholder="nomic-embed-text:latest" /></Form.Item>
                                    <Form.Item name="milvus_host" label="Milvus 主机"><Input placeholder="127.0.0.1" /></Form.Item>
                                    <Form.Item name="milvus_port" label="Milvus 端口"><Input placeholder="19530" /></Form.Item>
                                </div>
                                <div className="mt-4 pt-4 border-t border-gray-200">
                                    <h4 className="font-medium text-gray-700 mb-3 text-sm">连通性测试</h4>
                                    <div className="grid grid-cols-2 gap-2">
                                        <Button onClick={testLLMConnectivity} loading={testingLLM} size="small"><CloudServerOutlined className="mr-1" />大模型</Button>
                                        <Button onClick={testNeo4JConnectivity} loading={testingNeo4J} size="small"><DatabaseOutlined className="mr-1" />Neo4j</Button>
                                        <Button onClick={testEmbeddingConnectivity} loading={testingEmbedding} size="small"><ApiOutlined className="mr-1" />Embedding</Button>
                                        <Button onClick={testMilvusConnectivity} loading={testingMilvus} size="small"><ClusterOutlined className="mr-1" />Milvus</Button>
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

export default OntologyBuilderPage;