import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Drawer,
    Form,
    Input,
    Button,
    message,
    Space,
    Modal,
    Spin,
    Tabs,
    Tooltip,
    Select,
    AutoComplete,
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
    CloudDownloadOutlined,
    PlusOutlined,
    DeleteOutlined,
    CloudServerOutlined,
    FileTextOutlined,
    InfoCircleOutlined,
    EyeOutlined,
    MinusCircleOutlined,
    EditOutlined,
    TagsOutlined,
    LinkOutlined,
    DatabaseOutlined,
    UnorderedListOutlined,
    RightOutlined,
    LeftOutlined,
    SearchOutlined,
    ExpandOutlined,
    ShrinkOutlined,
    MoreOutlined,
    StopOutlined,
    CheckCircleOutlined,
    CloseCircleOutlined,
    LoadingOutlined,
    FileDoneOutlined,
    ClearOutlined,
    MessageOutlined,
    BookOutlined,
    SendOutlined,
    ThunderboltOutlined,
    AuditOutlined,
    ExportOutlined,
} from '@ant-design/icons';
import type { MenuProps } from 'antd';
import { OntologyNode, OntologyEdge, DataPropertyDef } from '../../../types/ontology';
import { projectsApi } from '../../../api/projects';
import { getLayoutedElements } from '../../../utils/layoutUtils';
import { modelConfigsApi } from '../../../api/model-configs';
import ModelPicker from '../../../shared/auth/ModelPicker';
import { getDomains, KnowledgeDomain } from '../../../api/domains';
import apiClient from '../../../api/client';
import { extractionApi } from '../../../api/extraction';
import { reviewsApi, versionsApi } from '../../../api/governance';
import D3ForceGraph from '../../../components/OntologyGraph/D3ForceGraph';
import KnowledgeDomainSelector from '../../../components/KnowledgeDomainSelector';
import ExportDialog from '../components/ExportDialog';
import { NodeDetailContent } from '../../graph-explorer/DetailDrawer';
import { graphApi } from '../../../api/graphview';
import { setUnsavedGuard } from '../../../utils/unsavedGuard';

const { TreeNode } = Tree;
const { TextArea } = Input;
const { Search } = AntInput;

const PROP_NAME_MAP: Record<string, string> = {};
const PROP_HIDDEN_SET = new Set(['_source_chunk_index', 'source_chunk_index']);
const PROP_NAME_REVERSE_MAP: Record<string, string> = Object.fromEntries(
    Object.entries(PROP_NAME_MAP).map(([k, v]) => [v, k])
);

// 类节点类型判据：与后端 graph_rows.CLASS_TYPES 对齐（抽取链路写 'Class'，手工节点用 'owl:Class'）
const CLASS_TYPE_VALUES = new Set(['owl:Class', 'Class']);
const isClassType = (t?: string): boolean => !!t && CLASS_TYPE_VALUES.has(t);

// 抽取引导模板（提示词注入）：一键填入规则表单；通用模板 = 全部留空按默认模式抽取
const GUIDANCE_TEMPLATES: Array<{
    key: string; label: string; desc: string;
    scenario: string;
    classes: Array<{ cls: string; properties: string; relations: string }>;
}> = [
    {
        key: 'general',
        label: '通用模式',
        desc: '不注入引导，由 AI 按通用模式识别文档中的核心概念、属性与关系',
        scenario: '',
        classes: [],
    },
    {
        key: 'finance',
        label: '行业研报',
        desc: '适合研报/分析报告：技术领域、机构、产品与市场',
        scenario: '本批文档为行业研究报告，重点识别报告中反复出现的技术领域、机构与产品，以及它们之间的支撑、竞争、应用关系。',
        classes: [
            { cls: '技术与知识领域', properties: '描述,成熟度,发展阶段', relations: '支撑,应用于,演进为' },
            { cls: '机构与公司', properties: '所属国家,主营业务', relations: '研发,发布,投资' },
            { cls: '产品与方案', properties: '版本,市场定位', relations: '属于,竞品,基于' },
            { cls: '市场与规模', properties: '规模,增速,时间段', relations: '涉及,驱动' },
        ],
    },
    {
        key: 'contract',
        label: '合同协议',
        desc: '适合合同/协议文本：合同主体、条款、标的与违约责任',
        scenario: '本批文档为合同协议类文本，重点识别合同各方、权利义务条款、标的物与金额、履约与违约责任。',
        classes: [
            { cls: '合同主体', properties: '角色（甲方/乙方）,联系方式', relations: '签署,委托,供货' },
            { cls: '合同条款', properties: '条款编号,金额,履行期限', relations: '约定,约束,隶属于' },
            { cls: '标的物', properties: '名称,规格,数量', relations: '交付,验收' },
            { cls: '违约与争议', properties: '情形,责任,赔偿', relations: '触发,适用,解决方式' },
        ],
    },
    {
        key: 'tech',
        label: '技术文档',
        desc: '适合需求/设计/运维文档：系统模块、接口、流程与故障',
        scenario: '本批文档为技术类文档，重点识别系统模块、接口、业务流程、故障与处理方案，以及模块间的依赖与调用关系。',
        classes: [
            { cls: '系统与模块', properties: '功能,版本,负责人', relations: '依赖,调用,集成' },
            { cls: '业务流程', properties: '触发条件,执行频率', relations: '包含,前置,产出' },
            { cls: '接口', properties: '协议,方法,路径', relations: '提供,消费' },
            { cls: '故障与处理', properties: '现象,级别', relations: '导致,处理方案,影响' },
        ],
    },
];

const SchemaTab: React.FC<{ projectId: string }> = ({ projectId }) => {
    const navigate = useNavigate();

    const [nodes, setNodes] = useState<any[]>([]);
    const [edges, setEdges] = useState<any[]>([]);
    const [selectedElement, setSelectedElement] = useState<OntologyNode | OntologyEdge | null>(null);
    const [isDrawerOpen, setIsDrawerOpen] = useState(false);
    // 详情/编辑页签（详情 = 实例探索同款 NodeDetailContent；编辑 = 原属性表单）
    const [drawerTab, setDrawerTab] = useState<'detail' | 'edit'>('detail');
    const [detailNodeId, setDetailNodeId] = useState<string | null>(null);
    // 画布聚焦请求（seq 递增触发 D3 平移缩放定位）
    const [focusRequest, setFocusRequest] = useState<{ nodeId: string; seq: number } | null>(null);
    const focusSeqRef = useRef(0);
    const [isRuleModalOpen, setIsRuleModalOpen] = useState(false);
    const [activeTemplate, setActiveTemplate] = useState<string>('general');
    const [loading, setLoading] = useState(false);
    const [projectName, setProjectName] = useState('');
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [lastSavedNodes, setLastSavedNodes] = useState<any[]>([]);
    const [lastSavedEdges, setLastSavedEdges] = useState<any[]>([]);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [isAddRelationModalOpen, setIsAddRelationModalOpen] = useState(false);
    const [selectedDomainId, setSelectedDomainId] = useState<number | undefined>(undefined);
    const [selectedDomainName, setSelectedDomainName] = useState<string | undefined>(undefined);
    const [currentProjectDomain, setCurrentProjectDomain] = useState<any | null>(null);
    const [relationForm] = Form.useForm();
    const [form] = Form.useForm();
    const [ruleForm] = Form.useForm();
    // 应用引导模板：一键填入场景与主体规则行（选通用模式则清空，按默认模式抽取）
    const applyGuidanceTemplate = (key: string) => {
        const tpl = GUIDANCE_TEMPLATES.find(t => t.key === key);
        if (!tpl) return;
        setActiveTemplate(key);
        ruleForm.setFieldsValue({
            scenario: tpl.scenario,
            classes: tpl.classes.map(c => ({ class: c.cls, properties: c.properties, relations: c.relations })),
        });
    };
    const [customRelationType, setCustomRelationType] = useState('');
    const [isAdmin, setIsAdmin] = useState(false);
    const [isNewNode, setIsNewNode] = useState(false);
    const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
    // 固定邻域高亮（"在画布中聚焦"）：持续版悬停效果；点击画布其他节点/空白即恢复
    const [neighborhoodPinId, setNeighborhoodPinId] = useState<string | null>(null);
    const [isDomainModalOpen, setIsDomainModalOpen] = useState(false);
    
    // GraphRAG 问答相关状态
    const [isQAModalOpen, setIsQAModalOpen] = useState(false);
    // 从其他项目导入框架（跨项目框架复用）
    const [isImportModalOpen, setIsImportModalOpen] = useState(false);
    const [importProjects, setImportProjects] = useState<{ id: number; name: string }[]>([]);
    const [importSourceId, setImportSourceId] = useState<number | undefined>(undefined);
    const [importVersions, setImportVersions] = useState<{ value: number; label: string }[]>([]);
    const [importVersionNo, setImportVersionNo] = useState<number | 'current'>('current');
    const [importing, setImporting] = useState(false);
    const [qaQuestion, setQaQuestion] = useState('');
    const [qaAnswer, setQaAnswer] = useState('');
    const [qaReferences, setQaReferences] = useState<any[]>([]);
    const [isQALoading, setIsQALoading] = useState(false);
    const [selectedQADomains, setSelectedQADomains] = useState<number[]>([]);
    const [availableDomains, setAvailableDomains] = useState<KnowledgeDomain[]>([]);
    const [isDomainsLoading, setIsDomainsLoading] = useState(false);
    
    // 文档管理相关状态
    const [uploadedDocuments, setUploadedDocuments] = useState<any[]>([]);
    const [isDocumentModalOpen, setIsDocumentModalOpen] = useState(false);
    const [isDocLoading, setIsDocLoading] = useState(false);
    // 文档列表是否已首次加载完成（空画布引导卡按"是否已有文档"分流文案，避免误提示去上传）
    const [docsLoaded, setDocsLoaded] = useState(false);
    
    // 任务进度相关状态
    const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
    const [taskProgress, setTaskProgress] = useState<number>(0);
    const [taskMessage, setTaskMessage] = useState<string>('');
    const [taskDetail, setTaskDetail] = useState<string>('');
    const [taskStatus, setTaskStatus] = useState<'pending' | 'running' | 'completed' | 'failed' | 'cancelled'>('pending');
    const [ssePendingReviews, setSsePendingReviews] = useState<number | null>(null);
    const [isProgressModalOpen, setIsProgressModalOpen] = useState(false);
    const [eventSource, setEventSource] = useState<EventSource | null>(null);
    
    // SSE 连接引用（避免 state 更新导致的闭包问题）
    const eventSourceRef = useRef<EventSource | null>(null);
    // SSE 断线后的轮询兜底定时器（GET tasks/{id}，终态自停）
    const progressPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

    // 左侧面板展开状态
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    
    // 树形列表搜索
    const [treeSearchValue, setTreeSearchValue] = useState('');
    
    // 树形列表展开状态（仅控制列表内部显示，不影响画布）
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    
    const [documentFilter, setDocumentFilter] = useState<string | null>(null);
    const availableDocuments = useMemo(() => {
        const docs = new Set<string>();
        nodes.forEach(node => {
            const doc = node.data?.source_document || node.data?._source_file;
            if (doc && doc !== 'unknown') docs.add(doc);
        });
        return Array.from(docs);
    }, [nodes]);
    
    // 画布缩放控制
    const [canvasZoom, setCanvasZoom] = useState(1);
    
    // 继承属性状态（用于显示）
    const [inheritedProperties, setInheritedProperties] = useState<{ name: string; value: string; from: string }[]>([]);

    // 测试连通性状态
    const [vlConfigured, setVlConfigured] = useState(false);

    // RAGFlow 注入相关状态

    // 提取配置参数（从系统配置中读取）
    const [extractConfig, setExtractConfig] = useState({
        chunk_size: 15000,
        chunk_overlap: 10,
        request_interval: 2,
        llm_timeout: 300,
        disable_think: true,
        vl_enabled: false,
    });

    useEffect(() => {
        if (projectId && projectId.trim() !== '') {
            loadProject();
        }
    }, [projectId]);

    // 加载提取配置参数（从系统配置中读取）
    useEffect(() => {
        const loadExtractConfig = async () => {
            try {
                // M2：抽取运行参数读 model_configs（extract 默认行 params）
                const rows = await modelConfigsApi.list({ purpose: 'extract' });
                const params = rows.find((r) => r.is_default)?.params || {};
                setExtractConfig({
                    chunk_size: params.chunk_size || 15000,
                    chunk_overlap: params.chunk_overlap || 10,
                    request_interval: params.request_interval || 2,
                    llm_timeout: params.llm_timeout || 300,
                    disable_think: params.disable_think !== undefined ? params.disable_think : true,
                    vl_enabled: params.vl_enabled || false,
                });
            } catch (error) {
                // 如果获取配置失败，使用默认值
                console.log('获取提取配置失败，使用默认值');
            }
        };
        loadExtractConfig();
        loadVlStatus();
    }, []);

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

    // 登记未保存状态：Tab 切换 / 侧边导航离开前由守卫统一弹窗确认
    useEffect(() => {
        setUnsavedGuard('schema', hasUnsavedChanges);
        return () => setUnsavedGuard('schema', false);
    }, [hasUnsavedChanges]);

    // ── 从其他项目导入框架（跨项目框架复用）──
    const openImportFromProject = async () => {
        setImportSourceId(undefined);
        setImportVersions([]);
        setImportVersionNo('current');
        setIsImportModalOpen(true);
        try {
            const list = await projectsApi.getMyProjects();
            setImportProjects(list
                .filter((p) => p.id !== Number(projectId))
                .map((p) => ({ id: p.id, name: p.name })));
        } catch {
            message.error('获取项目列表失败');
        }
    };

    const handleImportSourceChange = async (pid: number) => {
        setImportSourceId(pid);
        setImportVersionNo('current');
        setImportVersions([]);
        try {
            const r = await versionsApi.list(pid);
            setImportVersions((r.items || [])
                .filter((v) => v.kind === 'schema' || v.kind === 'full' || v.kind === 'rollback')
                .map((v) => ({
                    value: v.version_no,
                    label: `v${v.version_no} · ${v.kind === 'schema' ? '框架' : v.kind === 'full' ? '全量' : '回滚'}${v.label ? ` · ${v.label}` : ''}`,
                })));
        } catch { /* 版本列表失败不阻断 */ }
    };

    const confirmImportFramework = async () => {
        if (!importSourceId) {
            message.warning('请选择源项目');
            return;
        }
        setImporting(true);
        try {
            const res = await apiClient.post(`/api/projects/${projectId}/schema/import-from`, {
                source_project_id: importSourceId,
                version_no: importVersionNo === 'current' ? undefined : importVersionNo,
                // 只计算合并结果，不落库：导入仅更新画布，点「保存」才持久化并同步 Neo4j
                persist: false,
            });
            const data = res.data || {};
            if (data.graph) {
                const { nodes: layoutedNodes, edges: layoutedEdges } = getLayoutedElements(
                    data.graph.nodes || [],
                    data.graph.edges || []
                );
                setNodes(layoutedNodes);
                setEdges(layoutedEdges);
                message.success(`框架已载入画布（未保存）：类 ${data.imported_classes} 个、关系 ${data.imported_relations} 条，请点击「保存」落库并同步图数据库`);
            } else {
                // 兼容旧后端（直接落库）
                message.success(`框架导入成功：类 ${data.imported_classes} 个、关系 ${data.imported_relations} 条${data.version_no ? `，已存为版本 v${data.version_no}` : ''}`);
                await loadProject();
            }
            setIsImportModalOpen(false);
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || e?.response?.data?.detail || '框架导入失败');
        } finally {
            setImporting(false);
        }
    };

    const loadProject = async () => {
        setLoading(true);
        try {
            const project = await projectsApi.getProject(Number(projectId));
            setProjectName(project.name);
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
                // M1：角色判定改读 role 字段（登录时由 /auth/me 灌入）
                setIsAdmin(user.role === 'admin');
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
        setDrawerTab('detail');   // 点击类/实例默认展示详情（与实例探索一致），编辑在第二个页签
        setDetailNodeId(null);
        // 点击画布节点 = 清除固定邻域高亮（恢复常态）
        setNeighborhoodPinId(null);

        // 处理属性：区分直接属性和继承属性
        // properties_with_source 是后端返回的带来源标记的属性列表
        const propertiesWithSource = node.data?.properties_with_source || [];
        
        // 如果有 properties_with_source，使用它；否则回退到旧的 properties 字段
        let directPropsArray: { name: string; value: string }[] = [];
        let inheritedPropsArray: { name: string; value: string; from: string }[] = [];
        
        // ★ 辅助函数：从父类节点中查找属性值
        const findInheritedPropertyValue = (propName: string, parentClassId: string): string => {
            const parentNode = nodes.find(n => n.id === parentClassId);
            if (parentNode) {
                const parentProps = parentNode.data?.properties || {};
                if (parentProps[propName] !== undefined) {
                    return String(parentProps[propName]);
                }
                // 如果父类也没有该属性值，递归查找父类的父类
                const parentEdges = edges.filter(e => 
                    e.source === parentClassId && 
                    (e.data?.relation === 'subclass_of' || e.data?.label === 'subClassOf')
                );
                for (const edge of parentEdges) {
                    const grandParentId = edge.target;
                    const value = findInheritedPropertyValue(propName, grandParentId);
                    if (value !== '') {
                        return value;
                    }
                }
            }
            return '';
        };
        
        // ★ 辅助函数：查找父类 ID（通过 subclassOf 边）
        const findParentClassIds = (nodeId: string): string[] => {
            const parentIds: string[] = [];
            edges.forEach(edge => {
                if (edge.source === nodeId) {
                    const relation = edge.data?.relation || '';
                    const label = edge.data?.label || '';
                    if (relation === 'subclass_of' || label === 'subClassOf' || label === 'subclass_of') {
                        parentIds.push(String(edge.target));
                    }
                }
            });
            return parentIds;
        };
        
        if (propertiesWithSource.length > 0) {
            propertiesWithSource.forEach((p: any) => {
                if (PROP_HIDDEN_SET.has(p.name)) return;
                const displayName = PROP_NAME_MAP[p.name] || p.name;
                if (p.source === 'direct') {
                    const currentProps = node.data?.properties || {};
                    directPropsArray.push({
                        name: displayName,
                        value: String(currentProps[p.name] || '')
                    });
                } else if (p.source === 'inherited') {
                    const parentClassIds = findParentClassIds(node.id);
                    let inheritedValue = '';
                    let sourceClass = p.from || '父类';
                    
                    for (const parentId of parentClassIds) {
                        const value = findInheritedPropertyValue(p.name, parentId);
                        if (value !== '') {
                            inheritedValue = value;
                            const parentNode = nodes.find(n => n.id === parentId);
                            if (parentNode) {
                                sourceClass = parentNode.data?.label || '父类';
                            }
                            break;
                        }
                    }
                    
                    inheritedPropsArray.push({
                        name: displayName,
                        value: inheritedValue,
                        from: sourceClass
                    });
                }
            });
        } else {
            const propsObj = node.data?.properties || {};
            directPropsArray = Object.entries(propsObj)
                .filter(([key]) => !PROP_HIDDEN_SET.has(key))
                .map(([key, value]) => ({
                    name: PROP_NAME_MAP[key] || key,
                    value: String(value)
                }));
            
            if (node.data?.source_document) {
                directPropsArray.unshift({ name: '来源文档', value: node.data.source_document });
            }
            
            const parentClassIds = findParentClassIds(node.id);
            for (const parentId of parentClassIds) {
                const parentNode = nodes.find(n => n.id === parentId);
                if (parentNode) {
                    const parentProps = parentNode.data?.properties || {};
                    const parentLabel = parentNode.data?.label || '父类';
                    Object.entries(parentProps).forEach(([key, value]) => {
                        if (PROP_HIDDEN_SET.has(key)) return;
                        if (!propsObj.hasOwnProperty(key)) {
                            inheritedPropsArray.push({
                                name: PROP_NAME_MAP[key] || key,
                                value: String(value),
                                from: parentLabel
                            });
                        }
                    });
                }
            }
        }

        // 保存继承属性到状态，用于显示
        setInheritedProperties(inheritedPropsArray);

        form.setFieldsValue({
            label: node.data?.label || '',
            type: node.data?.type || 'owl:Class',
            // 抽取链路把定义写在 data.definition，展示/编辑统一到 description（description 优先，定义兜底）
            description: node.data?.description || node.data?.definition || '',
            properties: directPropsArray
        });
    };

    const onEdgeClick = (edge: any) => {
        setSelectedElement(edge);
        setIsDrawerOpen(true);
        setDrawerTab('edit');     // 关系无详情页签（行表边 id 与画布边 id 不对应），直接编辑

        form.setFieldsValue({
            label: edge.data?.label || edge.data?.relation || '',
            relation: edge.data?.relation || edge.data?.label || '',
        });
    };

    // 详情页里的导航/聚焦（行表 id）→ 解析实体 uri 尾段得到画布节点 id，高亮并确保可见
    const focusDetailNode = useCallback(async (rowId: string, opts?: { expand?: boolean }) => {
        try {
            const d = await graphApi.nodeDetail(Number(projectId), rowId);
            // 实体 uri 形如 urn:onto:{pid}:entity/{画布节点 id}
            const uri = d.entity?.uri || '';
            if (!uri.includes(':entity/')) return;
            const canvasId = decodeURIComponent(uri.split(':entity/')[1]);
            setDetailNodeId(rowId);
            const node = nodes.find((n) => n.id === canvasId);
            if (node) {
                setSelectedElement(node);
                setDrawerTab('detail');
            }
            if (node?.data?.type === 'owl:NamedIndividual') {
                const typeEdge = edges.find((e) => String(e.source) === canvasId
                    && (e.label === 'rdf:type' || e.data?.relation === 'instance_of'));
                if (typeEdge && !expandedNodeIds.has(String(typeEdge.target))) {
                    setExpandedNodeIds((prev) => new Set(prev).add(String(typeEdge.target)));
                }
            }
            // 邻域展开：类节点展开其全部实例
            if (opts?.expand && node && node.data?.type !== 'owl:NamedIndividual') {
                setExpandedNodeIds((prev) => new Set(prev).add(canvasId));
            }
            setHighlightNodeId(canvasId);
            setNeighborhoodPinId(canvasId);
            focusSeqRef.current += 1;
            setFocusRequest({ nodeId: canvasId, seq: focusSeqRef.current });
        } catch { /* 聚焦失败不阻断详情浏览 */ }
    }, [projectId, nodes, edges, expandedNodeIds]);

    const handleSaveProperties = (values: any) => {
        if (!selectedElement) return;

        const { label, type, properties, relation, description } = values;
        const isNode = 'position' in selectedElement;

        if (isNode) {
            const propsObj: Record<string, any> = {};
            if (Array.isArray(properties)) {
                properties.forEach((p: any) => {
                    if (p && p.name) {
                        const originalName = PROP_NAME_REVERSE_MAP[p.name] || p.name;
                        propsObj[originalName] = p.value ?? '';
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
                                description: description || '',
                                // 双写 definition：与抽取产物字段保持一致，避免编辑后丢定义
                                definition: description || '',
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

    // 实例的新增/抽取已统一到「实例探索」Tab（GraphTab），本页只维护框架（类/关系）

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

    const handleSchemaButtonClick = () => {
        // 文档上传统一在「文档解析」Tab；本页只基于已上传文档抽取框架
        (async () => {
            const docs = await loadDocuments();
            if (!docs || docs.length === 0) {
                // 未上传文档：直接引导到「文档解析」Tab
                message.info('请先在「文档解析」Tab 上传并解析文档');
                navigate(`/projects/${projectId}/documents`);
                return;
            }
            // 如果画布已有骨架（类节点），提示重新提取将覆盖（M3-7：TBox 以项目 graph_data 为准）
            if (nodes.some((n) => isClassType(n.data?.type))) {
                Modal.confirm({
                    title: '重新提取骨架',
                    content: '当前项目已有提取的骨架，重新提取将覆盖现有骨架。确定继续吗？',
                    okText: '确定',
                    cancelText: '取消',
                    onOk: () => {
                        // 清除之前的 schema
                        localStorage.removeItem(`project_${projectId}_schema_graph`);
                        localStorage.removeItem(`project_${projectId}_text_content`);
                        // 打开文档选择弹窗（选择要基于哪些文档抽取）
                        handleOpenDocumentModal();
                    }
                });
            } else {
                // 第一次提取：打开文档选择弹窗
                handleOpenDocumentModal();
            }
        })();
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
                
                message.success(response.data.message || '骨架解析成功！');
                
                // 提示用户需要上传文档进行实例提取，提供上传按钮
                Modal.confirm({
                    title: '骨架解析成功',
                    content: (
                        <div>
                            <p>类结构已成功解析，共 {response.data.schema_graph?.classes?.length || 0} 个类。</p>
                            <p className="mt-3 text-gray-600 font-medium">
                                是否现在上传文档进行实例提取？
                            </p>
                            <p className="mt-2 text-sm text-gray-500">
                                支持格式：TXT、PDF、DOC、DOCX、MD（可多选）
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
            message.error(`骨架解析失败：${errorDetail}`);
        } finally {
            setLoading(false);
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

    // 统一导出弹窗（RDF 6 种序列化 + 平台 JSON）
    const [exportOpen, setExportOpen] = useState(false);

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
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退
        const classIdByLabel = new Map<string, string>();
        nodes.forEach(n => {
            if (isClassType(n.data?.type) && n.data?.label) classIdByLabel.set(String(n.data.label), String(n.id));
        });
        nodes.forEach(n => {
            if (n.data?.type === 'owl:NamedIndividual' && n.data?.class_label) {
                const cid = classIdByLabel.get(String(n.data.class_label));
                if (cid) classIdsWithInstances.add(cid);
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
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退挂到类
        const fallbackClassIdByLabel = new Map<string, string>();
        nodes.forEach(n => {
            if (isClassType(n.data?.type) && n.data?.label) fallbackClassIdByLabel.set(String(n.data.label), String(n.id));
        });
        nodes.forEach(n => {
            if (n.data?.type === 'owl:NamedIndividual' && !instanceToClass.has(String(n.id)) && n.data?.class_label) {
                const cid = fallbackClassIdByLabel.get(String(n.data.class_label));
                if (cid) {
                    classToInstances.set(cid, [...(classToInstances.get(cid) || []), String(n.id)]);
                    instanceToClass.set(String(n.id), cid);
                }
            }
        });

        const isClassLike = (type: string | undefined) => isClassType(type);

        nodes.forEach(node => {
            if (isClassLike(node.data?.type)) {
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

        const displayNodes = nodes.filter(n => {
            if (!visibleNodeIds.has(n.id)) return false;
            if (!documentFilter) return true;
            if (isClassType(n.data?.type)) return true;
            return n.data?.source_document === documentFilter || n.data?._source_file === documentFilter;
        });

        const displayEdges = edges.filter(e => {
            const sourceId = String(e.source);
            const targetId = String(e.target);
            return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
        });

        return { displayNodes, displayEdges };
    }, [nodes, edges, expandedNodeIds, documentFilter]);

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

    const loadVlStatus = useCallback(async () => {
        try {
            // M2：VL 是否已配置 → model_configs 表是否存在启用的 vl 默认行
            const rows = await modelConfigsApi.list({ purpose: 'vl' });
            setVlConfigured(rows.some((r) => r.enabled));
        } catch {
            setVlConfigured(false);
        }
    }, []);

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
            return mappedDocs;
        } catch (error: any) {
            message.error(error.response?.data?.detail || '加载文档列表失败');
            setUploadedDocuments([]);
            return [];
        } finally {
            setIsDocLoading(false);
            setDocsLoaded(true);
        }
    }, [projectId]);

    // 挂载即预载文档列表：空画布引导卡需要按文档状态分流
    useEffect(() => {
        if (projectId) {
            loadDocuments();
        }
    }, [projectId, loadDocuments]);

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

    // 进度载荷字段归一：后端 SSE/轮询返回 {percent(0-100), message, stats, status}，
    // 兼容旧 {progress(0-1)} 形态（缺陷修复：此前读 data.progress 导致进度条恒 0%）
    const applyProgressData = useCallback((data: any): string => {
        const pct = typeof data?.percent === 'number' ? data.percent
            : typeof data?.progress === 'number' ? data.progress * 100 : 0;
        setTaskProgress(Math.max(0, Math.min(100, pct)) / 100);
        setTaskMessage(data?.message || '');
        setTaskDetail(data?.detail || '');
        setTaskStatus(data?.status || 'running');

        if (data?.status === 'completed') {
            // 保持弹窗打开：展示完成摘要 + 待人工确认数（审核工作台闸门）
            const stats = data?.stats || {};
            const parts: string[] = [];
            if (stats.classes !== undefined) parts.push(`类 ${stats.classes}`);
            if (stats.instances !== undefined) parts.push(`实例 ${stats.instances}`);
            if (stats.relations !== undefined) parts.push(`关系 ${stats.relations}`);
            if (stats.discarded_count) parts.push(`闸门丢弃 ${stats.discarded_count}`);
            message.success(`任务完成${parts.length ? '：' + parts.join(' / ') : ''}（已自动落版本）`);
            // 产物已写库（画布+行表+版本）——重新拉取项目刷新画布
            loadProject();
            // 人工确认：统计待审项并展示在进度弹窗内
            reviewsApi.listByProject(Number(projectId), { status: 'pending' })
                .then((r: any) => setSsePendingReviews(r.total ?? r.items?.length ?? 0))
                .catch(() => setSsePendingReviews(0));
        } else if (data?.status === 'failed') {
            setIsProgressModalOpen(false);
            message.error(data?.error || data?.message || '任务失败');
        } else if (data?.status === 'cancelled') {
            setIsProgressModalOpen(false);
            message.info('任务已取消');
        }
        return data?.status || 'running';
    }, [projectId, loadProject]);

    const stopProgressPolling = useCallback(() => {
        if (progressPollRef.current) {
            clearInterval(progressPollRef.current);
            progressPollRef.current = null;
        }
    }, []);

    // SSE 断线兜底：改轮询 GET tasks/{id}，拿到终态自停（避免进度条卡死）
    // 404 宽限 30s：任务刚入队、worker 尚未写入进度时 getTask 会临时 404，不应立刻停轮询
    const startProgressPolling = useCallback((taskId: string) => {
        if (progressPollRef.current || !projectId) return;
        let notFoundCount = 0;
        progressPollRef.current = setInterval(async () => {
            try {
                const data = await extractionApi.getTask(Number(projectId), taskId);
                notFoundCount = 0;
                const status = applyProgressData(data);
                if (status === 'completed' || status === 'failed' || status === 'cancelled') {
                    stopProgressPolling();
                }
            } catch {
                notFoundCount += 1;
                if (notFoundCount > 15) {
                    stopProgressPolling();  // 任务记录确实不存在或已过期
                }
            }
        }, 2000);
    }, [projectId, applyProgressData, stopProgressPolling]);

    // 连接 SSE 进度流（M3-7：extraction SSE + 一次性 ticket 鉴权；事件为默认 message 流）
    const connectToProgressStream = useCallback((taskId: string) => {
        if (!projectId) return;

        // 关闭之前的连接与兜底轮询
        const existingEs = eventSourceRef.current;
        if (existingEs) {
            existingEs.close();
            eventSourceRef.current = null;
        }
        stopProgressPolling();

        extractionApi.eventsUrl(Number(projectId), taskId).then((url) => {
            const es = new EventSource(url);
            console.log('[SSE] 连接建立:', taskId);

            es.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    const status = applyProgressData(data);
                    if (status === 'completed' || status === 'failed' || status === 'cancelled') {
                        stopProgressPolling();
                        es.close();
                        eventSourceRef.current = null;
                    }
                } catch (e) {
                    console.error('[SSE] 消息解析失败:', e);
                }
            };

            es.onerror = () => {
                console.log('[SSE] 连接断开，切换为轮询兜底');
                es.close();
                if (eventSourceRef.current === es) {
                    eventSourceRef.current = null;
                }
                startProgressPolling(taskId);
            };

            eventSourceRef.current = es;
        }).catch((e) => {
            console.error('[SSE] 建立连接失败:', e);
            message.error('进度订阅失败，任务仍在后台执行');
            startProgressPolling(taskId);
        });
    }, [projectId, applyProgressData, startProgressPolling, stopProgressPolling]);

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
            // M3-7：extraction API（extract 队列并行抽取 + 严格闸门 + 自动落版本 kind=schema）
            // 组装抽取引导（提示词注入）：场景 + 主体规则行，随 prompt 进缓存键
            const lines: string[] = [];
            if (values.scenario?.trim()) {
                lines.push(`场景：${values.scenario.trim()}`);
            }
            for (const row of (values.classes || [])) {
                if (!row?.class?.trim()) continue;
                let line = `- 主体「${row.class.trim()}」`;
                const bits: string[] = [];
                if (row.properties?.trim()) bits.push(`关注属性：${row.properties.trim()}`);
                if (row.relations?.trim()) bits.push(`常见关系：${row.relations.trim()}`);
                if (bits.length) line += `（${bits.join('；')}）`;
                lines.push(line);
            }
            const guidance = lines.join('\n').slice(0, 2000) || undefined;

            const docIds = uploadedDocuments.map(doc => doc.id);
            const response = await extractionApi.runSchema(Number(projectId), {
                document_ids: docIds,
                guidance,
            });

            setCurrentTaskId(response.task_id);
            setIsProgressModalOpen(true);
            setTaskStatus('running');
            setTaskProgress(0);
            setTaskMessage('开始骨架提取...');
            setSsePendingReviews(null);
            connectToProgressStream(response.task_id);
            message.info('任务已启动，请在进度窗口查看进度');
        } catch (error: any) {
            const errorDetail = error.response?.data?.error?.message || error.response?.data?.detail || error.message || '未知错误';
            message.error(`Schema 提取失败：${errorDetail}`);
        } finally {
            setLoading(false);
        }
    }, [projectId, uploadedDocuments, connectToProgressStream]);

    // 构建树形数据（带搜索过滤）
    const buildTreeData = useCallback(() => {
        const classNodes = nodes.filter(n => isClassType(n.data?.type));
        const instanceNodes = nodes.filter(n => n.data?.type === 'owl:NamedIndividual');

        const classToInstances: Record<string, any[]> = {};
        const linkedInstances = new Set<string>();
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
                linkedInstances.add(instance.id);
            }
        });
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退挂到类
        if (linkedInstances.size < instanceNodes.length) {
            const classIdByLabel: Record<string, string> = {};
            classNodes.forEach(n => {
                if (n.data?.label) classIdByLabel[String(n.data.label)] = String(n.id);
            });
            instanceNodes.forEach(instance => {
                if (!linkedInstances.has(instance.id) && instance.data?.class_label) {
                    const cid = classIdByLabel[String(instance.data.class_label)];
                    if (cid) {
                        (classToInstances[cid] = classToInstances[cid] || []).push(instance);
                    }
                }
            });
        }

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
                    icon: <span className="inline-block w-3 h-3 rounded-full mr-2 bg-[#f79767]" />,
                    isLeaf: true,
                    searchableTitle: instanceTitle,
                };
            }) || [];

            return {
                title: classTitle,
                key: classNode.id,
                icon: <span className="inline-block w-3 h-3 rounded-full mr-2 bg-[#4cc9f0]" />,
                children,
                searchableTitle: classTitle,
            };
        }).filter(node => {
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

    const classCount = nodes.filter(n => isClassType(n.data?.type)).length;
    const instanceCount = nodes.filter(n => n.data?.type === 'owl:NamedIndividual').length;


    // ==================== GraphRAG 问答相关函数 ====================

    // 加载知识域列表（用于问答多选）
    const loadAvailableDomains = useCallback(async () => {
        setIsDomainsLoading(true);
        try {
            const domains = await getDomains();
            setAvailableDomains(domains);
        } catch (error: any) {
            message.error('加载知识域列表失败');
        } finally {
            setIsDomainsLoading(false);
        }
    }, []);

    // 打开问答 Modal
    const handleOpenQAModal = useCallback(() => {
        setIsQAModalOpen(true);
        loadAvailableDomains();
    }, [loadAvailableDomains]);

    // 发送问题
    const handleSendQuestion = useCallback(async () => {
        if (!projectId || !qaQuestion.trim()) {
            message.warning('请输入问题');
            return;
        }

        setIsQALoading(true);
        try {
            // 构建选中的知识域字符串（逗号分隔）
            const selectedDomainsStr = selectedQADomains.length > 0
                ? selectedQADomains.map(id => {
                    const domain = availableDomains.find(d => d.id === id);
                    return domain?.name || '';
                }).filter(Boolean).join(',')
                : undefined;

            const response = await projectsApi.qaQuery(Number(projectId), qaQuestion, {
                selected_domains: selectedDomainsStr,
                top_k: 5,
            });

            setQaAnswer(response.answer || '未生成回答');
            setQaReferences(response.references || []);
        } catch (error: any) {
            const errorDetail = error.response?.data?.detail || error.message || '未知错误';
            message.error(`问答失败：${errorDetail}`);
            setQaAnswer('问答失败，请稍后重试');
        } finally {
            setIsQALoading(false);
        }
    }, [projectId, qaQuestion, selectedQADomains, availableDomains]);

    // 知识域多选切换
    const handleQADomainToggle = useCallback((domainId: number) => {
        setSelectedQADomains(prev => {
            if (prev.includes(domainId)) {
                return prev.filter(id => id !== domainId);
            } else {
                return [...prev, domainId];
            }
        });
    }, []);

    // 清空问答状态
    const handleClearQA = useCallback(() => {
        setQaQuestion('');
        setQaAnswer('');
        setQaReferences([]);
        setSelectedQADomains([]);
    }, []);

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
                    
                    // M3-7：extraction 任务取消（写取消标记 + 软 revoke）
                    await extractionApi.cancelTask(Number(projectId), currentTaskId);
                    message.info('任务取消请求已发送');
                    // 关闭 SSE 连接与兜底轮询
                    if (eventSourceRef.current) {
                        eventSourceRef.current.close();
                        eventSourceRef.current = null;
                    }
                    stopProgressPolling();
                    // 更新 UI 状态
                    setTaskStatus('cancelled');
                    setTaskMessage('任务已取消');
                } catch (error: any) {
                    console.error('[取消任务] 错误:', error);
                    message.error('取消任务失败：' + (error.response?.data?.error?.message || error.message || '未知错误'));
                }
            },
        });
    }, [projectId, currentTaskId, stopProgressPolling]);

    // 清理 SSE 连接与兜底轮询
    useEffect(() => {
        return () => {
            if (eventSource) {
                eventSource.close();
            }
            if (progressPollRef.current) {
                clearInterval(progressPollRef.current);
                progressPollRef.current = null;
            }
        };
    }, [eventSource]);

    // 两步抽取（04 §3.2）：第 1 步=抽取框架（类/关系），第 2 步=抽取实例；第 2 步以存在类节点为门槛
    const hasSchema = nodes.some((n) => isClassType(n.data?.type));

    const moreMenuItems: MenuProps['items'] = [
        { key: 'reextract-schema', icon: <CloudUploadOutlined />, label: '重新抽取框架' },
        { key: 'save-version', icon: <CloudUploadOutlined />, label: '存为版本快照（框架复用）' },
        { key: 'import-ttl', icon: <FileTextOutlined />, label: '导入 TTL / JSON' },
        { key: 'import-project', icon: <CloudDownloadOutlined />, label: '从其他项目导入框架' },
        { type: 'divider' },
        { key: 'export', icon: <ExportOutlined />, label: '导出图谱…' },
        { type: 'divider' },
        { key: 'qa', icon: <MessageOutlined />, label: 'GraphRAG 问答测试' },
        { key: 'domain', icon: <DatabaseOutlined />, label: `知识域：${currentProjectDomain?.name || '未设置'}` },
        { type: 'divider' },
        { key: 'clear', icon: <ClearOutlined />, label: '清空画布', danger: true },
    ];

    const handleMoreMenuClick: MenuProps['onClick'] = ({ key }) => {
        switch (key) {
            case 'reextract-schema':
                handleSchemaButtonClick();
                break;
            case 'save-version': {
                // 框架复用：先落盘当前画布，再打 schema 版本快照（实例抽取可选用该版本）
                Modal.confirm({
                    title: '存为版本快照',
                    content: '将保存当前画布框架并创建一个版本快照，后续实例抽取时可选择基于该版本进行。',
                    okText: '保存快照',
                    cancelText: '取消',
                    onOk: async () => {
                        try {
                            await handleSaveDraft();
                            const now = new Date();
                            const stamp = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
                            const ver = await versionsApi.create(Number(projectId), {
                                kind: 'schema',
                                label: `框架快照 ${stamp}`,
                                description: '画布手动保存的框架版本（框架复用）',
                            });
                            message.success(`已存为版本快照 v${ver.version_no}，实例抽取时可选用`);
                        } catch (e: any) {
                            message.error(e?.response?.data?.error?.message || '版本快照保存失败');
                        }
                    },
                });
                break;
            }
            case 'import-ttl': {
                const el = document.getElementById('ttl-import-input');
                if (el) el.click();
                break;
            }
            case 'import-project': {
                openImportFromProject();
                break;
            }
            case 'export':
                setExportOpen(true);
                break;
            case 'qa':
                handleOpenQAModal();
                break;
            case 'domain':
                handleOpenDomainModal();
                break;
            case 'clear':
                Modal.confirm({
                    title: '确认清空',
                    content: '确定要清空所有节点和关系吗？清空后需点击"保存"按钮才能同步到数据库。',
                    okText: '确定清空',
                    cancelText: '取消',
                    okButtonProps: { danger: true },
                    onOk: () => {
                        // 只清空前端状态，不自动同步到数据库
                        setNodes([]);
                        setEdges([]);
                        setExpandedNodeIds(new Set());
                        setSelectedElement(null);
                        setIsDrawerOpen(false);
                        setIsNewNode(false);
                        setHighlightNodeId(null);
                        form.resetFields();
                        setManualExpandedKeys(new Set());
                        setTreeSearchValue('');
                        setUploadedDocuments([]);
                        setQaQuestion('');
                        setQaAnswer('');
                        setQaReferences([]);
                        setSelectedQADomains([]);
                        setTaskProgress(0);
                        setTaskMessage('');
                        setTaskDetail('');
                        setTaskStatus('pending');
                        setCurrentTaskId(null);
                        if (eventSourceRef.current) {
                            eventSourceRef.current.close();
                            eventSourceRef.current = null;
                        }
                        stopProgressPolling();
                        // 清除 localStorage 中的 schema 数据
                        localStorage.removeItem(`project_${projectId}_schema_graph`);
                        localStorage.removeItem(`project_${projectId}_text_content`);
                        message.success('已清空画布，请点击"保存"按钮同步到数据库');
                    },
                });
                break;
        }
    };

    return (
        <div className="h-full flex flex-col" style={{ background: '#0F1420' }}>
            {!projectId ? (
                <div className="flex-1 flex items-center justify-center" style={{ color: '#8B94AB' }}>
                    缺少项目 ID，请从「我的项目」进入构建器。
                </div>
            ) : (
                <div className="flex-1 flex flex-col overflow-hidden">
                    {/* ── 顶部：两步抽取引导条（04 §3.2 两阶段构建） ── */}
                    <div
                        className="flex items-center justify-between flex-shrink-0 px-4 py-2 border-b gap-3 flex-wrap"
                        style={{ background: '#131A2A', borderColor: '#1A2233' }}
                    >
                        <div className="flex items-center gap-2 min-w-0">
                            {/* 第 1 步：抽取框架 */}
                            <div
                                className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg border transition-all"
                                style={hasSchema
                                    ? { borderColor: 'rgba(181,133,242,0.45)', background: 'rgba(181,133,242,0.10)' }
                                    : { borderColor: '#5B8DEF', background: 'rgba(91,141,239,0.12)', boxShadow: '0 0 0 1px rgba(91,141,239,0.35)' }}
                            >
                                <span
                                    className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold flex-shrink-0"
                                    style={hasSchema
                                        ? { background: '#B585F2', color: '#fff' }
                                        : { background: '#5B8DEF', color: '#fff' }}
                                >
                                    {hasSchema ? <CheckCircleOutlined /> : '1'}
                                </span>
                                <div className="leading-tight">
                                    <div className="text-sm font-medium" style={{ color: '#E6EAF2' }}>抽取框架</div>
                                    <div className="text-xs" style={{ color: '#8B94AB' }}>定义类与关系 · {classCount} 个类</div>
                                </div>
                            </div>
                            <RightOutlined style={{ color: '#56679B', fontSize: 12 }} />
                            {/* 第 2 步：实例抽取（统一在「实例探索」Tab 完成，此处为引导入口） */}
                            <div
                                className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg border transition-all"
                                onClick={hasSchema ? () => navigate(`/projects/${projectId}/graph`) : undefined}
                                style={{
                                    cursor: hasSchema ? 'pointer' : 'default',
                                    ...(!hasSchema
                                        ? { borderColor: '#1A2233', background: 'transparent', opacity: 0.55 }
                                        : { borderColor: 'rgba(91,141,239,0.45)', background: 'rgba(91,141,239,0.10)', boxShadow: '0 0 0 1px rgba(91,141,239,0.35)' }),
                                }}
                            >
                                <span
                                    className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold flex-shrink-0"
                                    style={!hasSchema
                                        ? { background: '#2A3550', color: '#8B94AB' }
                                        : { background: '#5B8DEF', color: '#fff' }}
                                >
                                    2
                                </span>
                                <div className="leading-tight">
                                    <div className="text-sm font-medium" style={{ color: '#E6EAF2' }}>抽取实例 <RightOutlined style={{ fontSize: 10, color: '#8B94AB' }} /></div>
                                    <div className="text-xs" style={{ color: '#8B94AB' }}>
                                        {!hasSchema ? '需先完成第 1 步' : `在「实例探索」进行 · ${instanceCount} 个实例`}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="flex items-center gap-2">
                            {/* 当前步骤主 CTA：本页只做框架抽取；实例抽取统一在「实例探索」Tab */}
                            {!hasSchema ? (
                                <Tooltip title="基于「文档解析」Tab 已上传的文档，AI 自动提取类、属性与关系">
                                    <Button
                                        type="primary"
                                        icon={<ThunderboltOutlined />}
                                        onClick={handleSchemaButtonClick}
                                        className="bg-indigo-600 hover:bg-indigo-700 border-none"
                                    >
                                        AI 抽取框架
                                    </Button>
                                </Tooltip>
                            ) : (
                                <Tooltip title="第 2 步实例抽取已统一到「实例探索」Tab（基于本页框架抽取）">
                                    <Button
                                        type="primary"
                                        icon={<ThunderboltOutlined />}
                                        onClick={() => navigate(`/projects/${projectId}/graph`)}
                                        className="bg-orange-500 hover:bg-orange-600 border-none"
                                    >
                                        前往实例探索
                                    </Button>
                                </Tooltip>
                            )}
                            <Tooltip title={hasUnsavedChanges ? '保存修改' : '已保存'}>
                                <Button
                                    icon={<SaveOutlined />}
                                    onClick={handleSaveDraft}
                                    loading={loading}
                                    type={hasUnsavedChanges ? 'primary' : 'default'}
                                    ghost={hasUnsavedChanges}
                                    className={hasUnsavedChanges ? 'bg-blue-600' : ''}
                                    style={!hasUnsavedChanges ? { background: 'transparent', borderColor: '#2A3550', color: '#E6EAF2' } : undefined}
                                >
                                    保存
                                </Button>
                            </Tooltip>
                            <Dropdown menu={{ items: moreMenuItems, onClick: handleMoreMenuClick }} trigger={['click']}>
                                <Button icon={<MoreOutlined />} style={{ background: 'transparent', borderColor: '#2A3550', color: '#E6EAF2' }} />
                            </Dropdown>
                        </div>
                    </div>

                    {/* 隐藏文件输入：TTL / JSON 本体导入（更多菜单） */}
                    <input
                        id="ttl-import-input"
                        type="file"
                        accept=".ttl,.nt,.ntriples,.n3,.rdf,.owl,.xml,.jsonld,.trig,.json"
                        multiple
                        style={{ display: 'none' }}
                        onChange={async (e) => {
                            const files = Array.from(e.target.files || []);
                            if (files.length > 0) {
                                await handleUploadTTLSchema(files);
                            }
                            e.target.value = '';
                        }}
                    />

                    <ExportDialog
                        open={exportOpen}
                        onClose={() => setExportOpen(false)}
                        projectId={Number(projectId)}
                        title="导出图谱（骨架编辑）"
                    />

                {/* 使用 Flex 布局实现平推式响应 */}
                <div className="flex-1 flex overflow-hidden">
                    {/* 左侧展开面板 - 使用 Flex 布局，展开时推挤主内容区 */}
                    <div
                        className={`transition-all duration-300 ease-in-out flex-shrink-0 relative ${
                            isLeftPanelExpanded ? 'w-[380px] shadow-lg' : 'w-0'
                        }`}
                        style={{
                            background: '#131A2A',
                            boxShadow: isLeftPanelExpanded ? '4px 0 12px rgba(0, 0, 0, 0.35)' : 'none',
                            borderRight: isLeftPanelExpanded ? '1px solid #1A2233' : 'none'
                        }}
                    >
                        {/* 收起按钮 - 仅在面板展开时显示在面板右侧边缘 */}
                        {isLeftPanelExpanded && (
                            <button
                                className="absolute top-1/2 -translate-y-1/2 z-[100] shadow-md rounded-r-lg p-2 transition-all duration-300"
                                style={{
                                    right: '-32px',
                                    background: '#1A2233',
                                    color: '#E6EAF2',
                                    border: '1px solid #2A3550',
                                    borderLeft: 'none',
                                }}
                                onClick={() => setIsLeftPanelExpanded(false)}
                                title="收起列表"
                            >
                                <LeftOutlined />
                            </button>
                        )}
                        
                        <div className="h-full flex flex-col overflow-hidden" style={{ minWidth: isLeftPanelExpanded ? '380px' : '0' }}>
                            
                            {/* 面板头部 - 搜索和操作 */}
                            <div className="p-3 border-b flex-shrink-0" style={{ borderColor: '#1A2233' }}>
                                <div className="flex items-center justify-between mb-2">
                                    <h3 className="font-semibold flex items-center text-sm" style={{ color: '#E6EAF2' }}>
                                        <UnorderedListOutlined className="mr-2" style={{ color: '#8B94AB' }} />
                                        类与实例列表
                                    </h3>
                                    <div className="flex items-center gap-1">
                                        <Tooltip title="全部展开">
                                            <Button type="text" size="small" icon={<ExpandOutlined />} onClick={expandAllTreeNodes} style={{ color: '#C6CEDF' }} />
                                        </Tooltip>
                                        <Tooltip title="全部收起">
                                            <Button type="text" size="small" icon={<ShrinkOutlined />} onClick={collapseAllTreeNodes} style={{ color: '#C6CEDF' }} />
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
                                {availableDocuments.length > 0 && (
                                    <Select
                                        style={{ width: '100%', marginTop: 8 }}
                                        placeholder="按文档筛选"
                                        allowClear
                                        size="small"
                                        value={documentFilter || undefined}
                                        onChange={(value) => setDocumentFilter(value || null)}
                                    >
                                        {availableDocuments.map(doc => (
                                            <Select.Option key={doc} value={doc}>{doc}</Select.Option>
                                        ))}
                                    </Select>
                                )}
                            </div>
                            
                            {/* 树形列表 */}
                            <div className="flex-1 overflow-auto p-2">
                                <style>{`
                                    .custom-tree,
                                    .custom-tree .ant-tree-list,
                                    .custom-tree .ant-tree-list-holder,
                                    .custom-tree .ant-tree-list-holder-inner {
                                        background: transparent !important;
                                    }
                                    .custom-tree .ant-tree-treenode {
                                        padding: 2px 0;
                                        color: #E6EAF2;
                                    }
                                    .custom-tree .ant-tree-node-content-wrapper {
                                        padding: 2px 8px;
                                        min-height: 24px;
                                        line-height: 20px;
                                        color: #E6EAF2;
                                    }
                                    .custom-tree .ant-tree-node-content-wrapper:hover {
                                        background: rgba(91, 141, 239, 0.15);
                                    }
                                    .custom-tree .ant-tree-node-content-wrapper.ant-tree-node-selected {
                                        background: rgba(91, 141, 239, 0.28) !important;
                                        color: #E6EAF2;
                                    }
                                    .custom-tree .ant-tree-indent-unit {
                                        width: 16px;
                                    }
                                    .custom-tree .ant-tree-switcher,
                                    .custom-tree .ant-tree-iconEle {
                                        color: #C6CEDF;
                                    }
                                    .custom-tree .ant-tree-switcher .ant-tree-switcher-icon,
                                    .custom-tree .ant-tree-iconEle svg {
                                        fill: #C6CEDF;
                                    }
                                    .custom-tree .ant-tree-switcher:hover .ant-tree-switcher-icon {
                                        fill: #E6EAF2;
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
                            <div className="p-3 border-t flex-shrink-0" style={{ borderColor: '#1A2233', background: 'rgba(15, 20, 32, 0.6)' }}>
                                <div className="flex items-center justify-between text-xs" style={{ color: '#8B94AB' }}>
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#B585F2' }} />
                                        类：{classCount}
                                    </span>
                                    <span className="flex items-center">
                                        <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#5B8DEF' }} />
                                        实例：{instanceCount}
                                    </span>
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* 主内容区 - 使用 Flex 布局自动适应剩余空间 */}
                    <div className="flex-1 flex flex-col min-w-0 relative h-full">

                        {loading && (
                            <div className="absolute inset-0 flex items-center justify-center z-50" style={{ background: 'rgba(15, 20, 32, 0.75)' }}>
                                <Spin size="large" tip="正在加载..." />
                            </div>
                        )}

                        {/* 画布悬浮工具条：高频操作收敛（添加/展开/列表），低频操作进右上「更多」菜单 */}
                        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
                            <div
                                className="flex items-center gap-1 px-2 py-1.5 rounded-lg border"
                                style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#1A2233', backdropFilter: 'blur(6px)' }}
                            >
                                <Dropdown
                                    trigger={['click']}
                                    menu={{
                                        items: [
                                            { key: 'class', icon: <PlusOutlined />, label: '新增对象类' },
                                            { key: 'relation', icon: <LinkOutlined />, label: '创建关系' },
                                        ],
                                        onClick: ({ key }) => {
                                            if (key === 'class') addNewClass();
                                            else if (key === 'relation') addNewRelation();
                                        },
                                    }}
                                >
                                    <Button size="small" type="primary" ghost icon={<PlusOutlined />}>添加</Button>
                                </Dropdown>
                                <Tooltip title={Array.from(expandedNodeIds).length > 0 ? '收起所有实例' : '展开所有实例'}>
                                    <Button
                                        size="small"
                                        type="primary"
                                        ghost
                                        icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />}
                                        onClick={expandAllInstances}
                                    >
                                        展开/收起实例
                                    </Button>
                                </Tooltip>
                                <Tooltip title="类与实例列表">
                                    <Button size="small" type="primary" ghost icon={<UnorderedListOutlined />} onClick={() => setIsLeftPanelExpanded((v) => !v)}>
                                        列表
                                    </Button>
                                </Tooltip>
                            </div>
                        </div>

                        {/* 空画布：第 1 步引导卡 */}
                        {!loading && nodes.length === 0 && (
                            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
                                <div
                                    className="pointer-events-auto text-center px-10 py-8 rounded-xl border"
                                    style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#1A2233', backdropFilter: 'blur(6px)' }}
                                >
                                    <div
                                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs mb-4"
                                        style={{ background: 'rgba(91, 141, 239, 0.15)', color: '#5B8DEF', border: '1px solid rgba(91, 141, 239, 0.4)' }}
                                    >
                                        <CheckCircleOutlined /> 两步构建 · 第 1 步
                                    </div>
                                    <h3 className="text-lg font-semibold mb-2" style={{ color: '#E6EAF2' }}>从文档中抽取本体框架</h3>
                                    <p className="text-sm mb-5" style={{ color: '#8B94AB' }}>
                                        {!docsLoaded
                                            ? <>基于文档 AI 自动提取类、属性与关系；<br />框架确认后，前往「实例探索」Tab 进行第 2 步实例抽取。</>
                                            : uploadedDocuments.length > 0
                                                ? <>已检测到 {uploadedDocuments.length} 个已上传文档，可直接 AI 抽取类、属性与关系；<br />框架确认后，前往「实例探索」Tab 进行第 2 步实例抽取。</>
                                                : <>先在「文档解析」Tab 上传并解析文档；回到本页基于文档 AI 抽取类、属性与关系，<br />框架确认后，前往「实例探索」Tab 进行第 2 步实例抽取。</>}
                                    </p>
                                    <Button type="primary" size="large" icon={<ThunderboltOutlined />} onClick={handleSchemaButtonClick} className="bg-indigo-600 hover:bg-indigo-700 border-none">
                                        {docsLoaded && uploadedDocuments.length > 0 ? 'AI 抽取框架' : docsLoaded ? '去上传文档并抽取' : 'AI 抽取框架'}
                                    </Button>
                                </div>
                            </div>
                        )}

                        {/* 力导向图组件 - 填满整个可用空间（深色主题与实例探索器统一） */}
                        <div className="absolute inset-0 w-full h-full">
                            <D3ForceGraph
                                theme="dark"
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
                                neighborhoodPinId={neighborhoodPinId}
                                focusRequest={focusRequest}
                            />
                        </div>

                        {/* 属性抽屉：节点 = 详情（实例探索同款）/编辑 双页签；关系 = 编辑 */}
                        <Drawer
                            title={selectedElement ? (
                                'position' in selectedElement
                                    ? `节点 - ${selectedElement.data?.label || '未命名'}`
                                    : `编辑关系 - ${selectedElement.data?.label || '未命名'}`
                            ) : "属性编辑"}
                            placement="right"
                            onClose={() => {
                                setIsDrawerOpen(false);
                                setSelectedElement(null);
                                form.resetFields();
                            }}
                            open={isDrawerOpen}
                            width={selectedElement && 'position' in selectedElement && drawerTab === 'detail' ? 560 : 420}
                            destroyOnClose={true}
                            className="property-drawer"
                            extra={selectedElement && 'position' in selectedElement && drawerTab === 'detail' ? (
                                <Space size={4}>
                                    <Button size="small" icon={<SendOutlined />}
                                            onClick={() => focusDetailNode(detailNodeId || String(selectedElement.id))}>在画布中聚焦</Button>
                                    <Button size="small" type="primary" ghost icon={<ExpandOutlined />}
                                            onClick={() => focusDetailNode(detailNodeId || String(selectedElement.id), { expand: true })}>邻域展开</Button>
                                </Space>
                            ) : undefined}
                        >
                            {selectedElement && 'position' in selectedElement ? (
                                <Tabs
                                    activeKey={drawerTab}
                                    onChange={(k) => setDrawerTab(k as 'detail' | 'edit')}
                                    size="small"
                                    items={[
                                        {
                                            key: 'detail', label: '详情',
                                            children: (
                                                <NodeDetailContent
                                                    projectId={Number(projectId)}
                                                    nodeId={detailNodeId || String(selectedElement.id)}
                                                    onNavigateNode={focusDetailNode}
                                                    onExpandNode={focusDetailNode}
                                                />
                                            ),
                                        },
                                        {
                                            key: 'edit', label: '编辑',
                                            children: (
                                <Form
                                    form={form}
                                    layout="vertical"
                                    onFinish={handleSaveProperties}
                                >
                                        <>
                                            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-100">
                                                <EditOutlined className="text-blue-500" />
                                                <span className="font-medium text-gray-700">节点属性</span>
                                            </div>
                                            
                                            <Form.Item name="label" label="节点名称" rules={[{ required: true, message: '请输入节点名称' }]}>
                                                <Input placeholder="请输入节点名称" />
                                            </Form.Item>

                                            <Form.Item name="type" label="节点类型">
                                                <Input disabled value={
                                                    (() => {
                                                        if (isClassType(form.getFieldValue('type'))) {
                                                            return '对象类 (Object Type)';
                                                        } else {
                                                            return `对象实例 (Individual)${selectedElement?.data?.class_label ? ' - ' + selectedElement.data.class_label : ''}`;
                                                        }
                                                    })()
                                                } />
                                            </Form.Item>

                                            <Form.Item name="description" label="描述">
                                                <Input.TextArea
                                                    placeholder="请输入节点描述"
                                                    autoSize={{ minRows: 2, maxRows: 6 }}
                                                    className="text-gray-600"
                                                />
                                            </Form.Item>

                                            {selectedElement?.data?.source_document && (
                                                <div className="mb-4 p-3 bg-blue-50 rounded-lg border border-blue-200">
                                                    <div className="flex items-center gap-2 mb-2">
                                                        <FileTextOutlined className="text-blue-500" />
                                                        <span className="font-medium text-blue-700">溯源文档</span>
                                                    </div>
                                                    <Tag color="blue" className="text-sm">{selectedElement.data.source_document}</Tag>
                                                </div>
                                            )}

                                            <div className="flex items-center gap-2 mb-3 pb-3 border-b border-gray-100">
                                                <TagsOutlined className="text-purple-500" />
                                                <span className="font-medium text-gray-700">自定义属性</span>
                                            </div>
                                            
                                            {/* 继承属性显示区域（只读） */}
                                            {inheritedProperties.length > 0 && (
                                                <div className="mb-4">
                                                    <div className="flex items-center gap-2 mb-2 pb-2 border-b border-gray-100">
                                                        <TagsOutlined className="text-gray-400" />
                                                        <span className="font-medium text-gray-500">继承属性（只读）</span>
                                                        <Tooltip title="这些属性从父类继承，不可直接编辑。如需修改，请编辑父类节点。">
                                                            <InfoCircleOutlined className="text-gray-400 text-sm" />
                                                        </Tooltip>
                                                    </div>
                                                    {inheritedProperties.map((prop, index) => (
                                                        <div key={`inherited-${index}`} className="flex gap-2 mb-2 items-start bg-gray-50 p-2 rounded border border-gray-200">
                                                            <div className="flex-shrink-0 w-[100px]">
                                                                <Input 
                                                                    value={prop.name} 
                                                                    size="small" 
                                                                    disabled 
                                                                    className="text-gray-500 bg-gray-100"
                                                                />
                                                            </div>
                                                            <div className="flex-1">
                                                                <Input.TextArea 
                                                                    value={prop.value} 
                                                                    size="small" 
                                                                    disabled 
                                                                    autoSize={{ minRows: 1, maxRows: 4 }}
                                                                    className="text-gray-500 bg-gray-100"
                                                                />
                                                            </div>
                                                            <Tag color="default" className="text-xs flex-shrink-0 mt-1">
                                                                来自: {prop.from}
                                                            </Tag>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                            
                                            {/* 直接属性编辑区域 */}
                                            <div className="flex items-center gap-2 mb-2">
                                                <TagsOutlined className="text-purple-500" />
                                                <span className="font-medium text-gray-700">直接属性</span>
                                                <Tooltip title="这些是当前节点直接定义的属性，可以编辑和删除。">
                                                    <InfoCircleOutlined className="text-gray-400 text-sm" />
                                                </Tooltip>
                                            </div>
                                            
                                            <Form.List name="properties">
                                                {(fields, { add, remove }) => {
                                                    const propDefs: DataPropertyDef[] = (selectedElement as any)?.data?.property_definitions || [];
                                                    const getPropDef = (propName: string) => propDefs.find(d => d.name === propName);
                                                    return (
                                                    <>
                                                        {fields.map(({ key, name, ...restField }) => {
                                                            const propName = form.getFieldValue(['properties', name, 'name']);
                                                            const propDef = getPropDef(propName);
                                                            const isMappedProp = PROP_NAME_REVERSE_MAP.hasOwnProperty(propName);
                                                            return (
                                                            <div key={key} className="flex gap-2 mb-2 items-start">
                                                                <div className="flex-shrink-0 flex items-center gap-1" style={{ width: '120px' }}>
                                                                    <Form.Item
                                                                        {...restField}
                                                                        name={[name, 'name']}
                                                                        rules={[{ required: true, message: '属性名不能为空' }]}
                                                                        className="mb-0"
                                                                        style={{ width: '100px' }}
                                                                    >
                                                                        <Input placeholder="属性名" size="small" disabled={isMappedProp} className={isMappedProp ? 'text-gray-500 bg-gray-50' : ''} />
                                                                    </Form.Item>
                                                                    {propDef && (
                                                                        <Tag color="blue" className="text-xs flex-shrink-0 mt-1">{propDef.data_type}</Tag>
                                                                    )}
                                                                </div>
                                                                <Form.Item
                                                                    {...restField}
                                                                    name={[name, 'value']}
                                                                    className="flex-1 mb-0"
                                                                >
                                                                    <Input.TextArea placeholder="属性值" autoSize={{ minRows: 1, maxRows: 4 }} size="small" />
                                                                </Form.Item>
                                                                <MinusCircleOutlined
                                                                    onClick={() => remove(name)}
                                                                    className="text-gray-400 hover:text-red-500 cursor-pointer mt-2 flex-shrink-0"
                                                                />
                                                            </div>
                                                        );
                                                        })}
                                                        <Button type="dashed" onClick={() => add()} block icon={<PlusOutlined />} size="small" className="mt-2">
                                                            添加属性
                                                        </Button>
                                                    </>
                                                    );
                                                }}
                                            </Form.List>
                                        </>

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
                                            ),
                                        },
                                    ]}
                                />
                            ) : selectedElement ? (
                                <Form
                                    form={form}
                                    layout="vertical"
                                    onFinish={handleSaveProperties}
                                >
                                        <>
                                            <div className="flex items-center gap-2 mb-4 pb-3 border-b border-gray-100">
                                                <LinkOutlined className="text-green-500" />
                                                <span className="font-medium text-gray-700">关系属性</span>
                                            </div>

                                            <Form.Item name="label" label="关系标签" rules={[{ required: true, message: '请输入关系标签' }]}>
                                                <Input placeholder="例如：关联、属于、包含" />
                                            </Form.Item>

                                            {(selectedElement as any)?.data?.cardinality && (
                                                <div className="mb-4">
                                                    <Tag color="purple">{(selectedElement as any).data.cardinality}</Tag>
                                                </div>
                                            )}

                                            {(selectedElement as any)?.data?.description && (
                                                <div className="mb-4 text-sm text-gray-500">{(selectedElement as any).data.description}</div>
                                            )}

                                            <Form.Item name="relation" label="关系类型" rules={[{ required: true, message: '请输入或选择关系类型' }]}>
                                                <AutoComplete
                                                    options={relationTypes}
                                                    placeholder="选择预设关系或输入自定义关系"
                                                    filterOption={(inputValue, option) =>
                                                        option!.label.toLowerCase().includes(inputValue.toLowerCase()) ||
                                                        option!.value.toLowerCase().includes(inputValue.toLowerCase())
                                                    }
                                                />
                                            </Form.Item>
                                        </>

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
                            ) : null}
                        </Drawer>

                        {/* 抽取引导 Modal（提示词注入：模板 + 自定义规则，组装后随抽取请求下发） */}
                        <Modal
                            title={<div className="flex items-center gap-2"><ThunderboltOutlined className="text-indigo-600" /><span>抽取引导（提示词注入）</span></div>}
                            open={isRuleModalOpen}
                            onOk={() => {
                                // M3-7：骨架提取统一走文档 ID 路径（extraction API）
                                if (uploadedDocuments.length === 0) {
                                    message.info('请先上传文档');
                                    handleOpenDocumentModal();
                                    return;
                                }
                                handleStartSchemaExtractionWithDocIds();
                            }}
                            onCancel={() => setIsRuleModalOpen(false)}
                            okText="开始提取"
                            cancelText="取消"
                            width={800}
                        >
                            <div className="mb-4 text-gray-500 text-sm">
                                选择一个引导模板快速填入，也可以在下方继续修改；<b>全部留空 = 通用模式</b>。
                                这里的内容会作为引导提示词注入到每一次切片抽取中，帮助 AI 聚焦你关心的主体和关系。
                            </div>
                            <div className="mb-4">
                                <div className="text-xs text-gray-500 mb-2">引导模板</div>
                                <div className="flex flex-wrap gap-2">
                                    {GUIDANCE_TEMPLATES.map(tpl => (
                                        <Tooltip key={tpl.key} title={tpl.desc}>
                                            <Button
                                                size="small"
                                                type={activeTemplate === tpl.key ? 'primary' : 'default'}
                                                onClick={() => applyGuidanceTemplate(tpl.key)}
                                                className={activeTemplate === tpl.key ? 'bg-indigo-600' : ''}
                                            >
                                                {tpl.label}
                                            </Button>
                                        </Tooltip>
                                    ))}
                                </div>
                                <div className="text-xs text-gray-400 mt-1.5">
                                    {GUIDANCE_TEMPLATES.find(t => t.key === activeTemplate)?.desc}
                                </div>
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

                                <div className="flex items-center gap-2 mb-3 pb-2 border-b border-gray-100 mt-4">
                                    <EyeOutlined className="text-purple-500" />
                                    <span className="font-medium text-gray-700">视觉解析</span>
                                </div>
                                <div className="flex items-center justify-between p-3 bg-gray-50 rounded-lg">
                                    <div>
                                        <div className="text-sm font-medium text-gray-700">VL 视觉模型解析</div>
                                        <div className="text-xs text-gray-500 mt-0.5">
                                            {!vlConfigured
                                                ? '未配置 VL 模型 — 请在系统设置中配置后启用'
                                                : extractConfig.vl_enabled
                                                    ? '已开启 — 将使用视觉模型识别文档中的图片、流程图、截图等内容'
                                                    : '已关闭 — 仅提取文本内容，图片中的信息将被忽略'}
                                        </div>
                                    </div>
                                    <Switch
                                        checked={extractConfig.vl_enabled}
                                        onChange={(checked) => setExtractConfig(prev => ({ ...prev, vl_enabled: checked }))}
                                        checkedChildren="关闭"
                                        unCheckedChildren="开启"
                                        disabled={!vlConfigured}
                                    />
                                </div>
                                {extractConfig.vl_enabled && vlConfigured && (
                                    <div className="mt-2 p-2 bg-purple-50 border border-purple-200 rounded text-xs text-purple-700">
                                        <EyeOutlined className="mr-1" />
                                        VL 模式已启用：系统将把文档页面渲染为图片后使用视觉模型识别，可提取流程图、截图、表格等图片内容。解析速度会稍慢，但信息更完整。
                                    </div>
                                )}
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
                                <Form.Item name="relationType" label="关系类型" rules={[{ required: true, message: '请输入或选择关系类型' }]}>
                                    <AutoComplete
                                        options={relationTypes}
                                        placeholder="选择预设关系或输入自定义关系"
                                        filterOption={(inputValue, option) =>
                                            option!.label.toLowerCase().includes(inputValue.toLowerCase()) ||
                                            option!.value.toLowerCase().includes(inputValue.toLowerCase())
                                        }
                                    />
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

                                {taskStatus === 'completed' && taskDetail && (
                                    <>
                                        <div className="p-3 bg-green-50 rounded-lg border border-green-100 text-sm text-gray-700">
                                            产物已保存：画布 + 行表 + 版本快照（可在「时间轴」Tab 查看）
                                        </div>
                                        {(ssePendingReviews ?? 0) > 0 && (
                                            <div className="p-3 bg-orange-50 rounded-lg border border-orange-200 text-sm text-gray-700 flex items-center justify-between gap-3">
                                                <span>
                                                    有 <b>{ssePendingReviews}</b> 项待人工确认（低置信实体/关系、新类、冲突等），通过后才会落地生效。
                                                </span>
                                                <Button type="primary" size="small" icon={<AuditOutlined />} onClick={() => navigate('/reviews')}>
                                                    去审核工作台
                                                </Button>
                                            </div>
                                        )}
                                    </>
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
                                        <p className="text-sm mt-1 mb-4">文档上传统一在「文档解析」Tab 完成</p>
                                        <Button
                                            type="primary"
                                            icon={<CloudUploadOutlined />}
                                            onClick={() => {
                                                setIsDocumentModalOpen(false);
                                                navigate(`/projects/${projectId}/documents`);
                                            }}
                                        >
                                            前往文档解析
                                        </Button>
                                    </div>
                                ) : (
                                    <>
                                        {/* 顶部操作栏（文档上传统一在「文档解析」Tab，此处仅展示数量） */}
                                        <div className="flex justify-between items-center mb-3 pb-3 border-b border-gray-100">
                                            <div className="text-sm text-gray-500">
                                                已上传 {uploadedDocuments.length} 个文档
                                            </div>
                                            <Button
                                                size="small"
                                                icon={<CloudUploadOutlined />}
                                                onClick={() => {
                                                    setIsDocumentModalOpen(false);
                                                    navigate(`/projects/${projectId}/documents`);
                                                }}
                                            >
                                                去上传文档
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
                                <div className="pt-4 border-t border-gray-200 mt-4">
                                    <div className="flex items-center justify-between p-2.5 bg-gray-50 rounded-lg mb-3">
                                        <span className="text-xs text-gray-500">当前抽取模型</span>
                                        <ModelPicker projectId={Number(projectId)} />
                                    </div>
                                    <div className="flex items-center justify-between p-2.5 bg-gray-50 rounded-lg mb-3">
                                        <div className="flex items-center gap-2">
                                            <EyeOutlined className={vlConfigured ? "text-purple-500" : "text-gray-400"} />
                                            <span className="text-sm font-medium text-gray-700">VL 视觉解析</span>
                                            {!vlConfigured && <Tag color="orange" className="text-xs">未配置</Tag>}
                                        </div>
                                        <Switch
                                            checked={extractConfig.vl_enabled}
                                            onChange={(checked) => setExtractConfig(prev => ({ ...prev, vl_enabled: checked }))}
                                            checkedChildren="关闭"
                                            unCheckedChildren="开启"
                                            disabled={!vlConfigured}
                                            size="small"
                                        />
                                    </div>
                                    <div className="flex justify-between items-center">
                                        <div className="text-sm text-gray-500">
                                            共 {uploadedDocuments.length} 个文档
                                        </div>
                                        <Space>
                                            <Button onClick={() => setIsDocumentModalOpen(false)}>关闭</Button>
                                            {/* 本页只负责框架抽取；实例抽取统一在「实例探索」Tab */}
                                            <Button
                                                type="primary"
                                                icon={<ThunderboltOutlined />}
                                                onClick={handleStartSchemaExtractionFromModal}
                                                className="bg-indigo-600 hover:bg-indigo-700"
                                            >
                                                开始骨架提取
                                            </Button>
                                        </Space>
                                    </div>
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

                        {/* 从其他项目导入框架 Modal（跨项目框架复用） */}
                        <Modal
                            title="从其他项目导入框架"
                            open={isImportModalOpen}
                            onOk={confirmImportFramework}
                            okText="导入框架"
                            confirmLoading={importing}
                            onCancel={() => setIsImportModalOpen(false)}
                            width={520}
                        >
                            <div className="mb-3 text-sm text-gray-600">
                                选择源项目及其框架版本，将其类、关系复制为本项目的框架（本项目已有实例会保留）。
                            </div>
                            <div className="space-y-3">
                                <div>
                                    <div className="text-xs text-gray-500 mb-1">源项目</div>
                                    <Select
                                        style={{ width: '100%' }}
                                        placeholder="选择项目"
                                        showSearch
                                        optionFilterProp="label"
                                        value={importSourceId}
                                        onChange={(v) => handleImportSourceChange(v)}
                                        options={importProjects.map((p) => ({ value: p.id, label: p.name }))}
                                    />
                                </div>
                                <div>
                                    <div className="text-xs text-gray-500 mb-1">框架版本</div>
                                    <Select
                                        style={{ width: '100%' }}
                                        value={importSourceId ? (importVersionNo ?? 'current') : undefined}
                                        disabled={!importSourceId}
                                        onChange={(v) => setImportVersionNo(v === 'current' ? 'current' : v)}
                                        options={[
                                            { value: 'current', label: '当前画布框架（最新）' },
                                            ...importVersions,
                                        ]}
                                    />
                                </div>
                                <div className="text-xs text-gray-400">
                                    导入会替换本项目的框架层（类与类间关系），保留已有实例；导入仅更新画布并标记为未保存，需点击「保存」才会落库并同步图数据库。
                                </div>
                            </div>
                        </Modal>

                        {/* GraphRAG 问答 Modal */}
                        <Modal
                            title={
                                <div className="flex items-center gap-2">
                                    <MessageOutlined className="text-green-500" />
                                    <span>GraphRAG 问答测试</span>
                                </div>
                            }
                            open={isQAModalOpen}
                            onCancel={() => {
                                setIsQAModalOpen(false);
                                handleClearQA();
                            }}
                            footer={null}
                            width={700}
                        >
                            <div className="py-2">
                                {/* 知识域多选区域 */}
                                <div className="mb-4">
                                    <div className="flex items-center gap-2 mb-2">
                                        <BookOutlined className="text-indigo-500" />
                                        <span className="font-medium text-gray-700">选择知识域（多选）</span>
                                    </div>
                                    <div className="text-xs text-gray-500 mb-2">
                                        选择要检索的知识域范围，不选择则默认在所有知识域中检索
                                    </div>
                                    {isDomainsLoading ? (
                                        <div className="flex justify-center py-4">
                                            <Spin size="small" />
                                        </div>
                                    ) : availableDomains.length === 0 ? (
                                        <div className="text-center py-4 text-gray-400 text-sm">
                                            暂无可用知识域
                                        </div>
                                    ) : (
                                        <div className="flex flex-wrap gap-2 max-h-32 overflow-auto p-2 border border-gray-200 rounded-lg bg-gray-50">
                                            {availableDomains.map((domain) => (
                                                <Tag
                                                    key={domain.id}
                                                    color={selectedQADomains.includes(domain.id) ? 'blue' : 'default'}
                                                    className={`cursor-pointer transition-all ${
                                                        selectedQADomains.includes(domain.id)
                                                            ? 'border-blue-500 text-blue-600'
                                                            : 'border-gray-300 hover:border-gray-400'
                                                    }`}
                                                    onClick={() => handleQADomainToggle(domain.id)}
                                                >
                                                    {domain.name}
                                                    {selectedQADomains.includes(domain.id) && (
                                                        <CheckCircleOutlined className="ml-1 text-blue-500" />
                                                    )}
                                                </Tag>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                {/* 问题输入区域 */}
                                <div className="mb-4">
                                    <div className="flex items-center gap-2 mb-2">
                                        <SendOutlined className="text-blue-500" />
                                        <span className="font-medium text-gray-700">问题</span>
                                    </div>
                                    <TextArea
                                        value={qaQuestion}
                                        onChange={(e) => setQaQuestion(e.target.value)}
                                        placeholder="请输入您的问题，例如：什么是本体论？"
                                        rows={3}
                                        disabled={isQALoading}
                                        onPressEnter={(e) => {
                                            if (!e.shiftKey) {
                                                e.preventDefault();
                                                handleSendQuestion();
                                            }
                                        }}
                                    />
                                    <div className="flex justify-end mt-2">
                                        <Button
                                            type="primary"
                                            icon={isQALoading ? <LoadingOutlined spin /> : <SendOutlined />}
                                            onClick={handleSendQuestion}
                                            loading={isQALoading}
                                            disabled={!qaQuestion.trim()}
                                            className="bg-green-600 hover:bg-green-700"
                                        >
                                            {isQALoading ? '生成中...' : '发送问题'}
                                        </Button>
                                    </div>
                                </div>

                                {/* 答案显示区域 */}
                                {qaAnswer && (
                                    <div className="mb-4">
                                        <div className="flex items-center gap-2 mb-2">
                                            <CheckCircleOutlined className="text-green-500" />
                                            <span className="font-medium text-gray-700">答案</span>
                                        </div>
                                        <div className="p-3 bg-green-50 border border-green-200 rounded-lg">
                                            <div className="text-sm text-gray-800 whitespace-pre-wrap">{qaAnswer}</div>
                                        </div>
                                    </div>
                                )}

                                {/* 溯源引用区域 */}
                                {qaReferences && qaReferences.length > 0 && (
                                    <div>
                                        <div className="flex items-center gap-2 mb-2">
                                            <BookOutlined className="text-purple-500" />
                                            <span className="font-medium text-gray-700">溯源引用 ({qaReferences.length})</span>
                                        </div>
                                        <div className="max-h-48 overflow-auto space-y-2">
                                            {qaReferences.map((ref, index) => (
                                                <div
                                                    key={ref.id}
                                                    className="p-2 bg-gray-50 border border-gray-200 rounded-lg text-sm"
                                                >
                                                    <div className="flex items-center gap-2 mb-1">
                                                        <Tag color="purple" className="font-medium">[{index + 1}]</Tag>
                                                        <span className="text-gray-600 font-medium">{ref.file}</span>
                                                    </div>
                                                    <div className="text-gray-500 pl-8 line-clamp-2">
                                                        {ref.quote}
                                                    </div>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* 空状态提示 */}
                                {!qaAnswer && !isQALoading && (
                                    <div className="text-center py-8 text-gray-400">
                                        <MessageOutlined className="text-4xl mb-2" />
                                        <p>请输入问题开始问答</p>
                                        <p className="text-sm mt-1">支持基于知识图谱的 RAG 检索和溯源</p>
                                    </div>
                                )}
                            </div>

                            {/* 底部操作区 */}
                            <div className="flex justify-between items-center pt-4 border-t border-gray-200">
                                <Button
                                    onClick={handleClearQA}
                                    icon={<ClearOutlined />}
                                    disabled={isQALoading || (!qaAnswer && qaReferences.length === 0)}
                                >
                                    清空
                                </Button>
                                <Button onClick={() => {
                                    setIsQAModalOpen(false);
                                    handleClearQA();
                                }}>
                                    关闭
                                </Button>
                            </div>
                        </Modal>

                        {/* RAGFlow 注入配置 Modal */}
                    </div>
                </div>
                </div>
            )}
        </div>
    );
};

export default SchemaTab;