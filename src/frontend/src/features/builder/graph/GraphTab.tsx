/**
 * 构建器 · 实例探索 Tab：与「骨架编辑」统一布局（顶部引导条 + 可折叠类/实例列表 + 深色力导向画布）。
 * 功能：根据当前框架抽取实例（可选框架版本）/ 消解分析 / 冲突检测 / 导出 TTL·JSON /
 * RAG 同步（RAGFlow）/ 发布到资产中心。悬停邻域高亮、右键展开实例、点击节点查看详情。
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    AutoComplete, Button, Dropdown, Form, Input, Modal, Popconfirm, Radio, Select, Space, Tag, Tooltip, Tree, message,
} from 'antd';
import type { MenuProps, TreeProps } from 'antd';
import {
    ApartmentOutlined, CheckCircleOutlined, CloudServerOutlined, ClusterOutlined, DeleteOutlined,
    EditOutlined, ExperimentOutlined, ExportOutlined, EyeOutlined, ExpandOutlined, LeftOutlined, MoreOutlined,
    PlusOutlined, RadarChartOutlined, RightOutlined, SaveOutlined, SearchOutlined,
    ShrinkOutlined, ThunderboltOutlined, UnorderedListOutlined,
} from '@ant-design/icons';

import { projectsApi } from '../../../api/projects';
import { setUnsavedGuard } from '../../../utils/unsavedGuard';
import { extractionApi } from '../../../api/extraction';
import { versionsApi } from '../../../api/governance';
import { graphApi, graphEditApi, GraphEditResult } from '../../../api/graphview';
import D3ForceGraph from '../../../components/OntologyGraph/D3ForceGraph';
import DetailDrawer from '../../graph-explorer/DetailDrawer';
import RagSyncModal from '../components/RagSyncModal';
import TaskProgressModal from '../components/TaskProgressModal';
import ExportDialog from '../components/ExportDialog';
import ReasoningModal from '../components/ReasoningModal';

const CLASS_TYPES = new Set(['owl:Class', 'Class']);

interface Props {
    projectId: number;
    onChanged?: () => void;
}

const GraphTab: React.FC<Props> = ({ projectId, onChanged }) => {
    const navigate = useNavigate();
    const [refreshKey, setRefreshKey] = useState(0);
    const [taskId, setTaskId] = useState<string | null>(null);
    const [progressOpen, setProgressOpen] = useState(false);
    const [progressTitle, setProgressTitle] = useState('任务进度');
    // 实例抽取框架选择（框架复用）：空 = 当前画布框架；数字 = 历史框架版本快照
    const [instOpen, setInstOpen] = useState(false);
    const [instVersion, setInstVersion] = useState<number | undefined>(undefined);
    // 推理分析（推理期 R1：蕴含推理 + 规则引擎）
    const [reasoningOpen, setReasoningOpen] = useState(false);
    const [versionOptions, setVersionOptions] = useState<{ value: number; label: string }[]>([]);
    const [instStarting, setInstStarting] = useState(false);
    // 与骨架编辑统一的画布数据与实例展开状态
    const [graphNodes, setGraphNodes] = useState<any[]>([]);
    const [graphEdges, setGraphEdges] = useState<any[]>([]);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    // 节点详情走原探索器的 DetailDrawer 标准（相邻关系/溯源/合并/审核）；边用轻量抽屉
    const [selectedNode, setSelectedNode] = useState<{ type: 'node'; id: string } | null>(null);
    const [selectedEdge, setSelectedEdge] = useState<any>(null);
    const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
    // 固定邻域高亮（"在画布中聚焦"）：持续版悬停效果；点击画布其他节点/空白即恢复
    const [neighborhoodPinId, setNeighborhoodPinId] = useState<string | null>(null);
    // 画布聚焦请求（seq 递增触发 D3 平移缩放定位）
    const [focusRequest, setFocusRequest] = useState<{ nodeId: string; seq: number } | null>(null);
    const focusSeqRef = useRef(0);
    // 左侧类/实例列表面板
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    const [treeSearchValue, setTreeSearchValue] = useState('');
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    // 发布 / RAG 同步
    const [isPublished, setIsPublished] = useState(false);
    const [isRagModalOpen, setIsRagModalOpen] = useState(false);
    // 保存（与骨架编辑同款）：拖拽布局等画布改动 → 更新当前抽取结果
    const [isSaving, setIsSaving] = useState(false);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);

    // ── R13：实例手动编辑（每次编辑后端落一版 kind=manual，在「时间轴」可追溯）──
    const [editTick, setEditTick] = useState(0);          // 编辑成功后刷新详情抽屉
    const [savingEdit, setSavingEdit] = useState(false);  // 编辑弹窗提交中
    const [instModal, setInstModal] = useState<{ mode: 'add' | 'edit'; nodeId?: string } | null>(null);
    const [relModal, setRelModal] = useState<{ anchorId: string } | null>(null);
    const [edgePred, setEdgePred] = useState('');         // 边弹窗可编辑谓词
    const [instForm] = Form.useForm();
    const [relForm] = Form.useForm();

    // 未保存守卫：Tab 切换 / 侧边导航离开前弹窗确认；浏览器关闭/刷新走 beforeunload
    useEffect(() => {
        setUnsavedGuard('graph', hasUnsavedChanges);
        return () => setUnsavedGuard('graph', false);
    }, [hasUnsavedChanges]);
    useEffect(() => {
        const handleBeforeUnload = (e: BeforeUnloadEvent) => {
            if (hasUnsavedChanges) {
                e.preventDefault();
                e.returnValue = '您有未保存的更改，确定要离开吗？';
            }
        };
        window.addEventListener('beforeunload', handleBeforeUnload);
        return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }, [hasUnsavedChanges]);

    const load = useCallback(async () => {
        try {
            const p = await projectsApi.getProject(projectId);
            const gd = (p.graph_data || {}) as { nodes?: any[]; edges?: any[] };
            setGraphNodes(gd.nodes || []);
            setGraphEdges(gd.edges || []);
            setIsPublished(!!p.is_published);
        } catch {
            /* 项目加载失败不阻断 */
        }
    }, [projectId]);

    useEffect(() => { load(); }, [load, refreshKey]);

    const refresh = useCallback(() => {
        setRefreshKey((k) => k + 1);
        load();
        onChanged?.();
    }, [load, onChanged]);

    const classCount = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type)).length;
    const instanceCount = graphNodes.filter((n) => (n.data || {}).type === 'owl:NamedIndividual').length;

    // 保存当前画布（含拖拽布局与实例展开结果）——与骨架编辑的保存同链路
    const handleSaveGraph = async () => {
        if (!projectId) return;
        setIsSaving(true);
        try {
            await projectsApi.updateProject(projectId, { graph_data: { nodes: graphNodes, edges: graphEdges } });
            await projectsApi.updateOntology(projectId, { nodes: graphNodes, edges: graphEdges });
            setHasUnsavedChanges(false);
            message.success('抽取结果已保存，已同步到图数据库 (Neo4j)');
            onChanged?.();
        } catch {
            message.error('保存失败，请重试');
        } finally {
            setIsSaving(false);
        }
    };

    // ───────────────────────── R13：实例手动编辑 ─────────────────────────
    const isInstanceNode = (n: any) => (n?.data || {}).type === 'owl:NamedIndividual';
    const classNodeOptions = useMemo(() => graphNodes
        .filter((n) => CLASS_TYPES.has((n.data || {}).type))
        .map((n) => ({ value: String(n.id), label: n.data?.label || String(n.id) }))
        .sort((a, b) => a.label.localeCompare(b.label, 'zh')), [graphNodes]);
    const instanceNodeOptions = useMemo(() => graphNodes
        .filter((n) => (n.data || {}).type === 'owl:NamedIndividual')
        .map((n) => ({
            value: String(n.id),
            label: `${n.data?.label || String(n.id)}（${n.data?.class_label || '未分类'}）`,
        }))
        .sort((a, b) => a.label.localeCompare(b.label, 'zh')), [graphNodes]);
    // 谓词候选：画布已有关系谓词（rdf:type 除外），支持自由输入
    const predicateOptions = useMemo(() => {
        const s = new Set<string>();
        graphEdges.forEach((e) => {
            const r = (e.data || {}).relation || e.label;
            if (r && r !== 'rdf:type' && r !== 'instance_of') s.add(String(r));
        });
        return [...s].sort().map((v) => ({ value: v }));
    }, [graphEdges]);

    // 实例当前所属类节点 id：rdf:type 边 → class_label 兜底
    const resolveClassId = useCallback((nodeId: string): string | undefined => {
        const typeEdge = graphEdges.find((e) => String(e.source) === nodeId
            && (e.label === 'rdf:type' || (e.data || {}).relation === 'instance_of'));
        if (typeEdge && CLASS_TYPES.has(graphNodes.find((n) => n.id === typeEdge.target)?.data?.type)) {
            return String(typeEdge.target);
        }
        const node = graphNodes.find((n) => String(n.id) === nodeId);
        const cl = (node?.data || {}).class_label;
        const byLabel = cl && graphNodes.find((n) => CLASS_TYPES.has((n.data || {}).type)
            && n.data?.label === cl);
        return byLabel ? String(byLabel.id) : undefined;
    }, [graphEdges, graphNodes]);

    const afterEdit = useCallback((r: GraphEditResult, tips?: string) => {
        message.success(r.version_recorded
            ? `${tips || '已保存'}，已记录到版本 v${r.version_no}（时间轴可查）`
            : `${tips || '已保存'}（版本记录失败，稍后可通过「保存」补偿）`);
        setEditTick((t) => t + 1);
        refresh();
    }, [refresh]);

    const editErrMsg = (e: any, fallback: string) => {
        message.error(e?.response?.data?.error?.message || e?.response?.data?.detail || fallback);
    };

    const openInstAdd = () => {
        instForm.resetFields();
        instForm.setFieldsValue({ label: '', class_node_id: undefined, properties: [] });
        setInstModal({ mode: 'add' });
    };

    const openInstEdit = (node: any) => {
        instForm.resetFields();
        instForm.setFieldsValue({
            label: node.data?.label || '',
            class_node_id: resolveClassId(String(node.id)),
            properties: Object.entries(node.data?.properties || {}).map(([key, value]) => ({ key, value: String(value ?? '') })),
        });
        setInstModal({ mode: 'edit', nodeId: String(node.id) });
    };

    const openRelAdd = (node: any) => {
        relForm.resetFields();
        relForm.setFieldsValue({ direction: 'out', target: undefined, predicate: '' });
        setRelModal({ anchorId: String(node.id) });
    };

    const handleInstSave = async () => {
        try {
            const values = await instForm.validateFields();
            const props: Record<string, string> = {};
            (values.properties || []).forEach((row: any) => {
                if (row?.key && String(row.key).trim()) props[String(row.key).trim()] = String(row.value ?? '');
            });
            setSavingEdit(true);
            if (instModal?.mode === 'add') {
                const r = await graphEditApi.addInstance(projectId, {
                    class_node_id: values.class_node_id,
                    label: String(values.label).trim(),
                    properties: props,
                });
                // 新实例挂到所选类并聚焦
                setExpandedNodeIds((prev) => new Set(prev).add(values.class_node_id));
                if (r.node_id) {
                    setNeighborhoodPinId(r.node_id);
                    setHighlightNodeId(r.node_id);
                    focusSeqRef.current += 1;
                    setFocusRequest({ nodeId: r.node_id, seq: focusSeqRef.current });
                }
                afterEdit(r, `已新增实例「${values.label}」`);
            } else if (instModal?.nodeId) {
                const r = await graphEditApi.updateInstance(projectId, instModal.nodeId, {
                    label: String(values.label).trim(),
                    class_node_id: values.class_node_id,
                    properties: props,
                });
                afterEdit(r, `已修改实例「${values.label}」`);
            }
            setInstModal(null);
            if (selectedNode) setSelectedNode({ ...selectedNode });
        } catch (e: any) {
            if (e?.errorFields) return; // 表单校验错误
            editErrMsg(e, '保存失败');
        } finally {
            setSavingEdit(false);
        }
    };

    const handleDeleteInstance = async (node: any) => {
        try {
            const r = await graphEditApi.deleteInstance(projectId, String(node.id));
            setSelectedNode(null);
            afterEdit(r, `已删除实例「${node.data?.label || node.id}」`);
        } catch (e: any) {
            editErrMsg(e, '删除失败');
        }
    };

    const handleRelSave = async () => {
        if (!relModal) return;
        try {
            const values = await relForm.validateFields();
            const anchor = relModal.anchorId;
            const source = values.direction === 'out' ? anchor : values.target;
            const target = values.direction === 'out' ? values.target : anchor;
            setSavingEdit(true);
            const r = await graphEditApi.addRelation(projectId, {
                source_node_id: source,
                target_node_id: target,
                predicate: String(values.predicate).trim(),
            });
            setRelModal(null);
            afterEdit(r, '已新增关系');
            if (selectedNode) setSelectedNode({ ...selectedNode });
        } catch (e: any) {
            if (e?.errorFields) return;
            editErrMsg(e, '保存失败');
        } finally {
            setSavingEdit(false);
        }
    };

    const handleEdgeSave = async () => {
        if (!selectedEdge?.id) return;
        try {
            setSavingEdit(true);
            const r = await graphEditApi.updateRelation(projectId, String(selectedEdge.id), {
                predicate: edgePred.trim(),
            });
            afterEdit(r, '已修改关系谓词');
            setSelectedEdge(null);
        } catch (e: any) {
            editErrMsg(e, '保存失败');
        } finally {
            setSavingEdit(false);
        }
    };

    const handleEdgeDelete = async () => {
        if (!selectedEdge?.id) return;
        try {
            setSavingEdit(true);
            const r = await graphEditApi.deleteRelation(projectId, String(selectedEdge.id));
            setSelectedEdge(null);
            afterEdit(r, '已删除关系');
        } catch (e: any) {
            editErrMsg(e, '删除失败');
        } finally {
            setSavingEdit(false);
        }
    };

    // 打开边弹窗时同步可编辑谓词初值
    useEffect(() => {
        if (selectedEdge) setEdgePred(String(selectedEdge.data?.relation || selectedEdge.data?.label || ''));
    }, [selectedEdge]);

    // 与骨架编辑同规则：类节点常显；实例仅在其所属类被右键展开后显示
    const { displayNodes, displayEdges } = useMemo(() => {        const classToInstances = new Map<string, string[]>();
        const instanceToClass = new Map<string, string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            const isTypeEdge = label === 'rdf:type' || e.data?.relation === 'instance_of';
            if (isTypeEdge && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                if (!classToInstances.has(classId)) classToInstances.set(classId, []);
                classToInstances.get(classId)!.push(String(e.source));
                instanceToClass.set(String(e.source), classId);
            }
        });
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退挂到类
        const classIdByLabel = new Map<string, string>();
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (CLASS_TYPES.has(d.type) && d.label) classIdByLabel.set(String(d.label), String(n.id));
        });
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (d.type === 'owl:NamedIndividual' && !instanceToClass.has(String(n.id))) {
                const cid = classIdByLabel.get(String(d.class_label || ''));
                if (cid) {
                    instanceToClass.set(String(n.id), cid);
                    if (!classToInstances.has(cid)) classToInstances.set(cid, []);
                    classToInstances.get(cid)!.push(String(n.id));
                }
            }
        });
        const visible = new Set<string>();
        graphNodes.forEach((n) => {
            const t = (n.data || {}).type;
            if (CLASS_TYPES.has(t)) {
                visible.add(n.id);
                if (expandedNodeIds.has(n.id)) {
                    (classToInstances.get(n.id) || []).forEach((iid) => visible.add(iid));
                }
            } else if (t === 'owl:NamedIndividual') {
                const pid = instanceToClass.get(n.id);
                if (pid && expandedNodeIds.has(pid)) visible.add(n.id);
            } else {
                visible.add(n.id);
            }
        });
        return {
            displayNodes: graphNodes.filter((n) => visible.has(n.id)),
            displayEdges: graphEdges.filter((e) => visible.has(String(e.source)) && visible.has(String(e.target))),
        };
    }, [graphNodes, graphEdges, expandedNodeIds]);

    // ── 左侧列表树（与骨架编辑同款：类 → 实例）──
    const buildTreeData = useCallback(() => {
        const classNodes = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type));
        const classToInstances: Record<string, any[]> = {};
        const linkedInstances = new Set<string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            if ((label === 'rdf:type' || e.data?.relation === 'instance_of') && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                (classToInstances[classId] = classToInstances[classId] || []).push(e.source);
                linkedInstances.add(String(e.source));
            }
        });
        // 兼容旧数据：无 rdf:type 边的孤立实例按 class_label 回退挂到类
        const classIdByLabel: Record<string, string> = {};
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (CLASS_TYPES.has(d.type) && d.label) classIdByLabel[String(d.label)] = String(n.id);
        });
        graphNodes.forEach((n) => {
            const d = n.data || {};
            if (d.type === 'owl:NamedIndividual' && !linkedInstances.has(String(n.id))) {
                const cid = classIdByLabel[String(d.class_label || '')];
                if (cid) (classToInstances[cid] = classToInstances[cid] || []).push(n.id);
            }
        });
        const q = treeSearchValue.trim().toLowerCase();
        const nodes = classNodes.map((classNode) => {
            const title = classNode.data?.label || '未命名类';
            const children = (classToInstances[classNode.id] || []).map((iid) => {
                const inst = graphNodes.find((n) => n.id === iid);
                return {
                    title: inst?.data?.label || '未命名实例',
                    key: iid,
                    icon: <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ background: '#5B8DEF' }} />,
                    isLeaf: true,
                    searchableTitle: inst?.data?.label || '未命名实例',
                };
            });
            return { title, key: classNode.id, icon: <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ background: '#B585F2' }} />, children, searchableTitle: title };
        });
        if (!q) return nodes;
        return nodes
            .map((n: any) => {
                const children = (n.children || []).filter((c: any) => c.searchableTitle?.toLowerCase().includes(q));
                const self = n.searchableTitle?.toLowerCase().includes(q);
                if (!self && children.length === 0) return null;
                return { ...n, children: self ? n.children : children };
            })
            .filter(Boolean);
    }, [graphNodes, graphEdges, treeSearchValue]);

    const onTreeSelect: TreeProps['onSelect'] = (selectedKeys) => {
        if (selectedKeys.length === 0) return;
        setSelectedEdge(null);
        setSelectedNode({ type: 'node', id: selectedKeys[0] as string });
    };

    const toggleExpand = (node: any) => {
        const classId = node.id;
        setExpandedNodeIds((prev) => {
            const next = new Set(prev);
            if (next.has(classId)) {
                next.delete(classId);
                message.info(`已收起 "${node.data?.label}" 的实例`);
            } else {
                next.add(classId);
                message.success(`已展开 "${node.data?.label}" 的实例`);
            }
            return next;
        });
    };

    const expandAll = () => {
        const classIdsWithInstances = new Set(
            graphEdges
                .filter((e) => (e.label === 'rdf:type' || e.data?.relation === 'instance_of')
                    && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type))
                .map((e) => String(e.target))
        );
        setExpandedNodeIds(new Set(classIdsWithInstances));
        message.success(classIdsWithInstances.size > 0 ? `已展开 ${classIdsWithInstances.size} 个类的实例` : '当前没有实例节点，请先抽取实例');
    };

    const collapseAll = () => {
        setExpandedNodeIds(new Set());
        message.info('已收起所有实例');
    };

    const startInstances = async () => {
        setInstVersion(undefined);
        setInstOpen(true);
        try {
            const r = await versionsApi.list(projectId);
            setVersionOptions((r.items || [])
                .filter((v) => v.kind === 'schema' || v.kind === 'full' || v.kind === 'rollback')
                .map((v) => ({
                    value: v.version_no,
                    label: `v${v.version_no} · ${v.kind === 'schema' ? '框架' : v.kind === 'full' ? '全量' : '回滚'}${v.label ? ` · ${v.label}` : ''}${v.created_at ? ` · ${new Date(v.created_at).toLocaleString()}` : ''}`,
                })));
        } catch {
            /* 版本列表失败不阻断：仍可基于当前画布框架抽取 */
        }
    };

    const confirmStartInstances = async () => {
        setInstStarting(true);
        try {
            const r = await extractionApi.runInstances(projectId, {
                parallelism: 4,
                schema_version_no: instVersion,
            });
            setProgressTitle(instVersion ? `实例抽取（基于框架 v${instVersion}）` : '实例抽取（严格闸门）');
            setTaskId(r.task_id);
            setProgressOpen(true);
            setInstOpen(false);
        } catch (e: any) {
            message.error(e.response?.data?.error?.message || '发起实例抽取失败');
        } finally {
            setInstStarting(false);
        }
    };

    const startResolution = async () => {
        try {
            const r = await fetch(`${window.location.origin}/api/projects/${projectId}/resolution/run`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('access_token') || ''}`,
                },
                body: JSON.stringify({ blocking: 'pinyin' }),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            setProgressTitle('实体消解（拼音 blocking）');
            setTaskId(data.task_id);
            setProgressOpen(true);
        } catch (e: any) {
            message.error(e.message || '消解分析失败');
        }
    };

    const detectConflicts = async () => {
        try {
            // 后端路由为 POST /api/projects/{id}/conflicts/detect（无 /resolution 前缀）
            const r = await fetch(`${window.location.origin}/api/projects/${projectId}/conflicts/detect`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Authorization: `Bearer ${localStorage.getItem('access_token') || ''}`,
                },
                body: JSON.stringify({}),
            });
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            const data = await r.json();
            // 异步任务：复用一个进度窗口（TaskProgressModal onCompleted 已绑定 refresh）
            setProgressTitle('冲突检测');
            setTaskId(data.task_id);
            setProgressOpen(true);
        } catch (e: any) {
            message.error(e.message || '冲突检测失败');
        }
    };

    // ── 导出 / 发布（自骨架编辑迁入）──
    // 统一导出弹窗（RDF 6 种序列化 + 平台 JSON）
    const [exportOpen, setExportOpen] = useState(false);

    const handleTogglePublish = async () => {
        try {
            const p = await projectsApi.getProject(projectId);
            if (!p.is_published && !p.domain_id && !p.domain) {
                Modal.warning({
                    title: '发布失败',
                    content: (
                        <div>
                            <p>请先在「骨架编辑」页配置知识域，然后再发布到资产中心。</p>
                            <p className="mt-2 text-gray-600">知识域用于对本体项目进行分类管理，是发布的必要条件。</p>
                        </div>
                    ),
                    okText: '知道了',
                });
                return;
            }
            const actionText = p.is_published ? '取消发布' : '发布资产';
            const contentText = p.is_published
                ? '取消发布后，该本体将从资产中心下架。确定吗？'
                : '发布后，您的本体将在资产中心公开展示。确定要发布吗？';
            Modal.confirm({
                title: `确认${actionText}`,
                content: contentText,
                okText: `确定${actionText}`,
                cancelText: '取消',
                onOk: async () => {
                    try {
                        if (p.is_published) {
                            await projectsApi.unpublishProject(Number(projectId));
                            message.success('已取消发布');
                        } else {
                            await projectsApi.publishProject(Number(projectId));
                            message.success('发布成功！已在资产中心公开展示');
                        }
                        const updated = await projectsApi.getProject(projectId);
                        setIsPublished(!!updated.is_published);
                        onChanged?.();
                    } catch (error: any) {
                        message.error(error.response?.data?.detail || `${actionText}失败`);
                    }
                },
            });
        } catch (e: any) {
            message.error(e.message || '发布操作失败');
        }
    };

    const moreMenuItems = [
        { key: 'export', icon: <ExportOutlined />, label: '导出图谱…' },
        { key: 'rag-sync', icon: <ThunderboltOutlined />, label: 'RAG 同步（RAGFlow）' },
        { type: 'divider' as const },
        { key: 'publish', icon: isPublished ? <EyeOutlined /> : <CloudServerOutlined />, label: isPublished ? '已发布 · 点击下线' : '发布到资产中心' },
    ];

    const handleMoreMenuClick: MenuProps['onClick'] = ({ key }) => {
        switch (key) {
            case 'export':
                setExportOpen(true);
                break;
            case 'rag-sync':
                setIsRagModalOpen(true);
                break;
            case 'publish':
                handleTogglePublish();
                break;
        }
    };

    // DetailDrawer 导航/聚焦用的是行表 id（数字或 URI），画布节点 id 存于实体 uri 尾段 → 解析回画布并聚焦
    const focusCanvasNode = useCallback(async (rowId: string, opts?: { expand?: boolean }) => {
        try {
            const d = await graphApi.nodeDetail(projectId, rowId);
            // 实体 uri 形如 urn:onto:{pid}:entity/{画布节点 id}
            const uri = d.entity?.uri || '';
            if (!uri.includes(':entity/')) return;
            const canvasId = decodeURIComponent(uri.split(':entity/')[1]);
            const node = graphNodes.find((n) => n.id === canvasId);
            if (node?.data?.type === 'owl:NamedIndividual') {
                const typeEdge = graphEdges.find((e) => String(e.source) === canvasId
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
    }, [projectId, graphNodes, graphEdges, expandedNodeIds]);

    const renderSelectionDrawers = () => {
        return (
            <>
                <DetailDrawer
                    projectId={projectId}
                    selection={selectedNode}
                    onClose={() => setSelectedNode(null)}
                    onNavigateNode={(nid) => {
                        setSelectedNode({ type: 'node', id: nid });
                        focusCanvasNode(nid);
                    }}
                    onExpandNode={(nid) => focusCanvasNode(nid)}
                    reloadKey={editTick}
                    extraNodeActions={(nid) => {
                        const node = graphNodes.find((n) => String(n.id) === nid);
                        if (!isInstanceNode(node)) return null; // 类节点的编辑走「骨架编辑」
                        return (
                            <>
                                <Button size="small" icon={<EditOutlined />}
                                        onClick={() => openInstEdit(node)}>编辑</Button>
                                <Button size="small" icon={<ApartmentOutlined />}
                                        onClick={() => openRelAdd(node)}>加关系</Button>
                                <Popconfirm
                                    title={`删除实例「${node.data?.label || nid}」？`}
                                    description="将连带删除它的全部关系边；操作会记录到版本时间轴。"
                                    okText="删除" okButtonProps={{ danger: true }} cancelText="取消"
                                    onConfirm={() => handleDeleteInstance(node)}>
                                    <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
                                </Popconfirm>
                            </>
                        );
                    }}
                />
                {selectedEdge && (() => {
                    const rel = selectedEdge.data?.relation || selectedEdge.data?.label || '';
                    const isTypeEdge = rel === 'rdf:type' || rel === 'instance_of';
                    const hasId = !!selectedEdge.id;
                    return (
                        <Modal
                            title={`关系 - ${selectedEdge.data?.label || rel || '未命名'}`}
                            open
                            onCancel={() => setSelectedEdge(null)}
                            footer={null}
                            width={460}
                        >
                            <div className="space-y-2 text-sm">
                                <div>源头：{String(selectedEdge.source)}</div>
                                <div>目标：{String(selectedEdge.target)}</div>
                                {selectedEdge.data?.confidence != null && (
                                    <div>置信度：{Number(selectedEdge.data.confidence).toFixed(2)}</div>
                                )}
                                {isTypeEdge ? (
                                    <div className="text-xs text-gray-400">
                                        rdf:type 边（实例归属类）不支持在此编辑；如需调整类别请在实例详情里「编辑」。
                                    </div>
                                ) : hasId ? (
                                    <>
                                        <div className="flex items-center gap-2 pt-1">
                                            <span className="shrink-0">谓词：</span>
                                            <AutoComplete
                                                value={edgePred}
                                                onChange={(v) => setEdgePred(v)}
                                                options={predicateOptions}
                                                style={{ width: 220 }}
                                                placeholder="关系类型，如：属于"
                                            />
                                        </div>
                                        <div className="flex items-center gap-2 pt-2">
                                            <Button type="primary" size="small" loading={savingEdit}
                                                    disabled={!edgePred.trim() || edgePred.trim() === rel}
                                                    onClick={handleEdgeSave}>保存修改</Button>
                                            <Popconfirm title="删除这条关系？" okText="删除"
                                                        okButtonProps={{ danger: true }} cancelText="取消"
                                                        onConfirm={handleEdgeDelete}>
                                                <Button danger size="small" icon={<DeleteOutlined />}
                                                        loading={savingEdit}>删除关系</Button>
                                            </Popconfirm>
                                        </div>
                                        <div className="text-xs text-gray-400">
                                            修改会记录到「时间轴」版本记录。
                                        </div>
                                    </>
                                ) : (
                                    <div className="text-xs text-gray-400">
                                        该关系缺少稳定 ID（旧数据），请先点击顶部「保存」后再编辑。
                                    </div>
                                )}
                            </div>
                        </Modal>
                    );
                })()}
            </>
        );
    };

    return (
        <div className="h-full flex flex-col" style={{ background: '#0F1420' }}>
            <div className="flex-1 flex flex-col overflow-hidden">
                {/* ── 顶部：引导条 + 主操作（与骨架编辑统一布局）── */}
                <div
                    className="flex items-center justify-between flex-shrink-0 px-4 py-2 border-b gap-3 flex-wrap"
                    style={{ background: '#131A2A', borderColor: '#1A2233' }}
                >
                    <div className="flex items-center gap-2 min-w-0">
                        {/* 第 1 步：抽取框架（跳回骨架编辑） */}
                        <div
                            className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg border transition-all cursor-pointer"
                            style={{ borderColor: 'rgba(181,133,242,0.45)', background: 'rgba(181,133,242,0.10)' }}
                            onClick={() => navigate(`/projects/${projectId}/schema`)}
                        >
                            <span className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold flex-shrink-0" style={{ background: '#B585F2', color: '#fff' }}>
                                <CheckCircleOutlined />
                            </span>
                            <div className="leading-tight">
                                <div className="text-sm font-medium" style={{ color: '#E6EAF2' }}>抽取框架</div>
                                <div className="text-xs" style={{ color: '#8B94AB' }}>定义类与关系 · {classCount} 个类</div>
                            </div>
                        </div>
                        <RightOutlined style={{ color: '#56679B', fontSize: 12 }} />
                        {/* 第 2 步：抽取实例（当前步骤） */}
                        <div
                            className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg border transition-all"
                            style={{ borderColor: 'rgba(91,141,239,0.45)', background: 'rgba(91,141,239,0.10)', boxShadow: '0 0 0 1px rgba(91,141,239,0.35)' }}
                        >
                            <span className="flex items-center justify-center w-6 h-6 rounded-full text-xs font-semibold flex-shrink-0" style={{ background: '#5B8DEF', color: '#fff' }}>2</span>
                            <div className="leading-tight">
                                <div className="text-sm font-medium" style={{ color: '#E6EAF2' }}>抽取实例 <RightOutlined style={{ fontSize: 10, color: '#8B94AB' }} /></div>
                                <div className="text-xs" style={{ color: '#8B94AB' }}>在「实例探索」进行 · {instanceCount} 个实例</div>
                            </div>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <Tooltip title="基于当前框架从文档中抽取实例（可选择框架版本）">
                            <Button type="primary" icon={<ThunderboltOutlined />} onClick={startInstances}
                                className="bg-orange-500 hover:bg-orange-600 border-none">
                                开始实例抽取
                            </Button>
                        </Tooltip>
                        <Button icon={<ClusterOutlined />} onClick={startResolution}>消解分析</Button>
                        <Button icon={<RadarChartOutlined />} onClick={detectConflicts}>冲突检测</Button>
                        <Tooltip title="语义蕴含推理（OWL-RL/RDFS）+ 自定义规则推理；结果与原始事实分开存放">
                            <Button icon={<ExperimentOutlined />} onClick={() => setReasoningOpen(true)}>推理分析</Button>
                        </Tooltip>
                        <Tooltip title={hasUnsavedChanges ? '保存修改（含拖拽布局）' : '保存当前抽取结果'}>
                            <Button
                                icon={<SaveOutlined />}
                                onClick={handleSaveGraph}
                                loading={isSaving}
                                type={hasUnsavedChanges ? 'primary' : 'default'}
                                ghost={hasUnsavedChanges}
                                className={hasUnsavedChanges ? 'bg-blue-600' : ''}
                                style={!hasUnsavedChanges ? { background: 'transparent', borderColor: '#2A3550', color: '#E6EAF2' } : undefined}
                            >
                                保存
                            </Button>
                        </Tooltip>
                        <Dropdown
                            trigger={['click']}
                            menu={{ items: moreMenuItems, onClick: handleMoreMenuClick }}
                        >
                            <Button icon={<MoreOutlined />} style={{ background: 'transparent', borderColor: '#2A3550', color: '#E6EAF2' }} />
                        </Dropdown>
                    </div>
                </div>

                {/* ── 主体：左列表 + 画布 ── */}
                <div className="flex-1 flex overflow-hidden">
                    {/* 左侧展开面板（与骨架编辑同款） */}
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
                        {isLeftPanelExpanded && (
                            <button
                                className="absolute top-1/2 -translate-y-1/2 z-[100] shadow-md rounded-r-lg p-2 transition-all duration-300"
                                style={{ right: '-32px', background: '#1A2233', color: '#E6EAF2', border: '1px solid #2A3550', borderLeft: 'none' }}
                                onClick={() => setIsLeftPanelExpanded(false)}
                                title="收起列表"
                            >
                                <LeftOutlined />
                            </button>
                        )}

                        <div className="h-full flex flex-col overflow-hidden" style={{ minWidth: isLeftPanelExpanded ? '380px' : '0' }}>
                            <div className="p-3 border-b flex-shrink-0" style={{ borderColor: '#1A2233' }}>
                                <div className="flex items-center justify-between mb-2">
                                    <h3 className="font-semibold flex items-center text-sm" style={{ color: '#E6EAF2' }}>
                                        <UnorderedListOutlined className="mr-2" style={{ color: '#8B94AB' }} />
                                        类与实例列表
                                    </h3>
                                    <div className="flex items-center gap-1">
                                        <Tooltip title="全部展开">
                                            <Button type="text" size="small" icon={<ExpandOutlined />}
                                                onClick={() => setManualExpandedKeys(new Set(buildTreeData().map((n: any) => n.key)))}
                                                style={{ color: '#C6CEDF' }} />
                                        </Tooltip>
                                        <Tooltip title="全部收起">
                                            <Button type="text" size="small" icon={<ShrinkOutlined />}
                                                onClick={() => setManualExpandedKeys(new Set())}
                                                style={{ color: '#C6CEDF' }} />
                                        </Tooltip>
                                    </div>
                                </div>
                                <Input
                                    placeholder="搜索类或实例..."
                                    size="small"
                                    value={treeSearchValue}
                                    onChange={(e) => setTreeSearchValue(e.target.value)}
                                    allowClear
                                    prefix={<SearchOutlined className="text-gray-400" />}
                                />
                            </div>
                            <div className="flex-1 overflow-auto p-2">
                                <style>{`
                                    .inst-tree, .inst-tree .ant-tree-list, .inst-tree .ant-tree-list-holder,
                                    .inst-tree .ant-tree-list-holder-inner { background: transparent !important; }
                                    .inst-tree .ant-tree-treenode { padding: 2px 0; color: #E6EAF2; }
                                    .inst-tree .ant-tree-node-content-wrapper { padding: 2px 8px; min-height: 24px; line-height: 20px; color: #E6EAF2; }
                                    .inst-tree .ant-tree-node-content-wrapper:hover { background: rgba(91, 141, 239, 0.15) !important; border-radius: 4px; }
                                    .inst-tree .ant-tree-node-content-wrapper-selected { background: rgba(91, 141, 239, 0.28) !important; }
                                    .inst-tree .ant-tree-switcher { width: 20px; height: 24px; line-height: 24px; }
                                `}</style>
                                <Tree
                                    showIcon
                                    expandedKeys={Array.from(manualExpandedKeys)}
                                    onExpand={(keys) => setManualExpandedKeys(new Set(keys as string[]))}
                                    selectedKeys={selectedNode ? [selectedNode.id] : []}
                                    onSelect={onTreeSelect}
                                    treeData={buildTreeData() as any}
                                    blockNode
                                    className="inst-tree"
                                />
                            </div>
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

                    {/* 主内容区 */}
                    <div className="flex-1 flex flex-col min-w-0 relative h-full">
                        {/* 画布悬浮工具条（与骨架编辑同款） */}
                        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20">
                            <div
                                className="flex items-center gap-1 px-2 py-1.5 rounded-lg border"
                                style={{ background: 'rgba(19, 26, 42, 0.92)', borderColor: '#1A2233', backdropFilter: 'blur(6px)' }}
                            >
                                <Tooltip title={Array.from(expandedNodeIds).length > 0 ? '收起所有实例' : '展开所有实例'}>
                                    <Button
                                        size="small" type="primary" ghost
                                        icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />}
                                        onClick={Array.from(expandedNodeIds).length > 0 ? collapseAll : expandAll}
                                    >
                                        展开/收起实例
                                    </Button>
                                </Tooltip>
                                <Tooltip title="类与实例列表">
                                    <Button size="small" type="primary" ghost icon={<UnorderedListOutlined />} onClick={() => setIsLeftPanelExpanded((v) => !v)}>
                                        列表
                                    </Button>
                                </Tooltip>
                                <Tooltip title="手动新增一个实例节点（挂到所选类下，操作记录到版本时间轴）">
                                    <Button size="small" type="primary" ghost icon={<PlusOutlined />} onClick={openInstAdd}>
                                        新增实例
                                    </Button>
                                </Tooltip>
                            </div>
                        </div>

                        {/* 画布 */}
                        <div className="flex-1 relative min-h-0">
                            <div className="absolute inset-0">
                                <D3ForceGraph
                                    theme="dark"
                                    nodes={displayNodes}
                                    edges={displayEdges}
                                    onNodeClick={(node) => {
                                        setSelectedEdge(null);
                                        // 点击画布任意节点 = 清除固定邻域高亮（恢复常态），并打开该节点详情
                                        setNeighborhoodPinId(null);
                                        setHighlightNodeId(null);
                                        setSelectedNode(node ? { type: 'node', id: node.id } : null);
                                    }}
                                    onEdgeClick={(edge) => {
                                        setSelectedNode(null);
                                        setSelectedEdge(edge);
                                    }}
                                    onNodesChange={(updatedNodes) => {
                                        if (Array.isArray(updatedNodes)) {
                                            setGraphNodes((prev) => prev.map((n) => {
                                                const updated = updatedNodes.find((u) => u.id === n.id);
                                                return updated ? { ...n, position: updated.position } : n;
                                            }));
                                            if (updatedNodes.length > 0) setHasUnsavedChanges(true);
                                        }
                                    }}
                                    onNodeRightClick={toggleExpand}
                                    highlightNodeId={highlightNodeId}
                                    neighborhoodPinId={neighborhoodPinId}
                                    focusRequest={focusRequest}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {renderSelectionDrawers()}

            <Modal
                title="开始实例抽取"
                open={instOpen}
                onOk={confirmStartInstances}
                okText="开始抽取"
                confirmLoading={instStarting}
                onCancel={() => setInstOpen(false)}
            >
                <div className="mb-2 text-sm text-gray-600">选择本次实例抽取所基于的框架（TBox）：</div>
                <Select
                    style={{ width: '100%' }}
                    value={instVersion ?? 'current'}
                    onChange={(v) => setInstVersion(v === 'current' || typeof v === 'number' ? (v === 'current' ? undefined : v) : undefined)}
                    options={[
                        { value: 'current', label: '当前画布框架（最新调整）' },
                        ...versionOptions,
                    ]}
                />
                <div className="mt-2 text-xs text-gray-400">
                    选历史版本时从该版本快照读取框架，抽取结果仍写回当前画布。
                </div>
            </Modal>

            {/* ── R13：新增/编辑实例弹窗 ── */}
            <Modal
                title={instModal?.mode === 'add' ? '新增实例' : '编辑实例'}
                open={!!instModal}
                onOk={handleInstSave}
                okText="保存"
                confirmLoading={savingEdit}
                onCancel={() => setInstModal(null)}
                okButtonProps={instModal?.mode === 'add' ? {} : { danger: false }}
                destroyOnClose
            >
                <Form form={instForm} layout="vertical" className="mt-2">
                    <Form.Item name="label" label="实例名称" rules={[{ required: true, message: '请输入实例名称' }]}>
                        <Input maxLength={200} placeholder="如：XX 稳健型理财产品" />
                    </Form.Item>
                    <Form.Item name="class_node_id" label="所属类" rules={[{ required: true, message: '请选择所属类' }]}>
                        <Select
                            showSearch
                            optionFilterProp="label"
                            placeholder="选择该实例归属的类"
                            options={classNodeOptions}
                        />
                    </Form.Item>
                    <Form.Item label="属性（可选）" className="mb-0">
                        <Form.List name="properties">
                            {(fields, { add, remove }) => (
                                <>
                                    {fields.map((field) => (
                                        <Space key={field.key} className="flex mb-2">
                                            <Form.Item name={[field.name, 'key']} noStyle>
                                                <Input maxLength={100} placeholder="属性名" style={{ width: 150 }} />
                                            </Form.Item>
                                            <Form.Item name={[field.name, 'value']} noStyle>
                                                <Input maxLength={500} placeholder="值" style={{ width: 240 }} />
                                            </Form.Item>
                                            <Button type="text" danger icon={<DeleteOutlined />}
                                                    onClick={() => remove(field.name)} />
                                        </Space>
                                    ))}
                                    <Button type="dashed" block icon={<PlusOutlined />}
                                            onClick={() => add({ key: '', value: '' })}>
                                        添加属性
                                    </Button>
                                </>
                            )}
                        </Form.List>
                    </Form.Item>
                </Form>
                <div className="text-xs text-gray-400 mt-2">
                    保存后会同步行表与图数据库，并自动记录一个「手动编辑」版本。
                </div>
            </Modal>

            {/* ── R13：新增关系弹窗 ── */}
            <Modal
                title="新增关系"
                open={!!relModal}
                onOk={handleRelSave}
                okText="保存"
                confirmLoading={savingEdit}
                onCancel={() => setRelModal(null)}
                destroyOnClose
            >
                {relModal && (() => {
                    const anchor = graphNodes.find((n) => String(n.id) === relModal.anchorId);
                    return (
                        <Form form={relForm} layout="vertical" className="mt-2">
                            <div className="mb-3 text-sm">
                                当前实例：<Tag color="blue">{anchor?.data?.label || relModal.anchorId}</Tag>
                            </div>
                            <Form.Item name="direction" label="方向" rules={[{ required: true }]}>
                                <Radio.Group>
                                    <Radio value="out">当前实例 → 对方</Radio>
                                    <Radio value="in">对方 → 当前实例</Radio>
                                </Radio.Group>
                            </Form.Item>
                            <Form.Item name="target" label="对方实例" rules={[{ required: true, message: '请选择对方实例' }]}>
                                <Select
                                    showSearch
                                    optionFilterProp="label"
                                    placeholder="选择另一个实例"
                                    options={instanceNodeOptions.filter((o) => o.value !== relModal.anchorId)}
                                />
                            </Form.Item>
                            <Form.Item name="predicate" label="关系类型（谓词）" rules={[{ required: true, message: '请输入关系类型' }]}>
                                <AutoComplete
                                    options={predicateOptions}
                                    placeholder="如：属于 / 发行机构（可自由输入）"
                                    filterOption={(input, option) =>
                                        (option?.value ?? '').toLowerCase().includes(input.toLowerCase())}
                                />
                            </Form.Item>
                            <div className="text-xs text-gray-400">
                                保存后会同步行表与图数据库，并自动记录一个「手动编辑」版本。
                            </div>
                        </Form>
                    );
                })()}
            </Modal>

            <RagSyncModal
                projectId={Number(projectId)}
                open={isRagModalOpen}
                onClose={() => setIsRagModalOpen(false)}
            />

            <ExportDialog
                open={exportOpen}
                onClose={() => setExportOpen(false)}
                projectId={Number(projectId)}
                title="导出图谱（实例探索）"
            />

            <TaskProgressModal
                projectId={projectId}
                taskId={taskId}
                title={progressTitle}
                open={progressOpen}
                onClose={() => setProgressOpen(false)}
                onCompleted={() => refresh()}
            />

            <ReasoningModal
                projectId={projectId}
                open={reasoningOpen}
                onClose={() => setReasoningOpen(false)}
            />
        </div>
    );
};

export default GraphTab;
