import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { PROJECT_STATUS, PUBLISHED_TAG } from '../shared/projectStatus';
import { useParams, useNavigate, useLocation } from 'react-router-dom';
import { message, notifyError } from '../shared/feedback';
import { Button, Tag, Modal, Input, Tooltip, Tree } from 'antd';
import type { TreeProps } from 'antd';
import {
    ArrowLeftOutlined, UserOutlined, InfoCircleOutlined, DatabaseOutlined,
    UnorderedListOutlined, RightOutlined, ExportOutlined,
    ExpandOutlined, ShrinkOutlined, SearchOutlined,
} from '@ant-design/icons';
import Navbar from '../components/Layout/Navbar';
import WorkspaceSkeleton from '../components/Layout/WorkspaceSkeleton';
import EmptyState from '../components/EmptyState';
import { publishApi } from '../api/governance';
import { ProjectData } from '../types/ontology';
import { useImeSafeSearch } from '../shared/useImeSafeSearch';

/** 资产详情返回的是「发布快照」视角，比构建器的 ProjectData 多这几个字段（2026-11-16） */
interface AssetProjectData extends ProjectData {
    /** 发布快照统计（类 / 实例 / 关系 / 属性） */
    stats?: { classes?: number; instances?: number; relations?: number; properties?: number };
    published_at?: string | null;
    version_no?: number | null;
    project_name?: string;
}
import { graphApi } from '../api/graphview';
import D3ForceGraph, { spacingLabel } from '../components/OntologyGraph/D3ForceGraph';
import DetailDrawer from '../features/graph-explorer/DetailDrawer';
import ExportDialog from '../features/builder/components/ExportDialog';

const CLASS_TYPES = new Set(['owl:Class', 'Class']);

/** 深色面板内部的分隔线：和画布的 #1A2233 同一档，不用纯白低透明（在白底上能用的那套到深底会发灰） */
const AP_DIV = 'rgba(255,255,255,0.07)';

/** 结构概览四个指标各自的色：直接取画布节点/关系的配色（类紫、实例蓝、关系青、属性琥珀），
 *  数字用同色相的浅一档——深底上淡彩数字立得住，纯白数字四列摆一起反而没有分区感 */
const AP_STATS = [
    { key: 'classes', label: '类', color: '#B585F2', ink: '#C9AEF8' },
    { key: 'instances', label: '实例', color: '#5B8DEF', ink: '#9BC0F7' },
    { key: 'relations', label: '关系', color: '#5AC8FA', ink: '#8BDCF2' },
    { key: 'properties', label: '属性', color: '#F2B950', ink: '#F5D08C' },
] as const;

/** 资产中心 · 已发布本体详情：展示逻辑与构建器「实例探索」一致（深色画布 + 类/实例列表 + DetailDrawer），
 *  但全部只读——不提供画布编辑回写、不展示任何编辑入口。 */
const AssetDetailPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const location = useLocation();
    // 返回资产中心（2026-11-25）：图谱详情是第三层，进入时资产中心把来源知识域带过来，
    // 返回就回到第二层（该知识域的项目列表），而不是一级的知识域总览；
    // 直接打开链接（没有来源）时仍回总览
    const fromOrigin = location.state as {
        domainId?: number | null;
        domainName?: string;
        projectName?: string;
    } | null;
    const fromDomainId = fromOrigin?.domainId;
    const fromDomainName = fromOrigin?.domainName;
    // 首帧用的项目名（2026-11-25）：资产中心点进来时会带过来，
    // 面包屑和骨架屏先显示它，等详情加载完再用接口返回的名称替换，
    // 不会再出现「本体详情 → 实际名字」这一下跳变
    const fromProjectName = fromOrigin?.projectName;
    // 按钮文案跟着实际目的地走：回二级就写「返回「域名」」，回一级才写「返回资产中心」
    const backLabel = fromDomainId == null
        ? '返回资产中心'
        : (fromDomainName ? `返回「${fromDomainName}」` : '返回资产列表');
    const backToAssetCenter = useCallback(() => {
        navigate('/asset-center', { state: fromDomainId == null ? undefined : { domainId: fromDomainId } });
    }, [navigate, fromDomainId]);
    const [project, setProject] = useState<AssetProjectData | null>(null);
    const [loading, setLoading] = useState(true);
    const [graphNodes, setGraphNodes] = useState<any[]>([]);
    const [graphEdges, setGraphEdges] = useState<any[]>([]);
    // 类节点展开状态（右键类节点展开/收起其实例）
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    // 节点详情走与实例探索一致的 DetailDrawer；边用轻量弹窗
    const [selectedNode, setSelectedNode] = useState<{ type: 'node'; id: string } | null>(null);
    const [selectedEdge, setSelectedEdge] = useState<any>(null);
    const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null);
    const [neighborhoodPinId, setNeighborhoodPinId] = useState<string | null>(null);
    const [focusRequest, setFocusRequest] = useState<{ nodeId: string; seq: number } | null>(null);
    const focusSeqRef = useRef(0);
    // 左侧类/实例列表面板
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    // 列表树搜索（2026-11-25）：输入框即时回显，组字结束/停顿 200ms 后才重算整棵树
    const {
        value: treeSearchValue,
        setValue: setTreeSearchValue,
        query: treeSearchQuery,
        inputProps: treeSearchInputProps,
    } = useImeSafeSearch(200);
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    const [exportOpen, setExportOpen] = useState(false);
    // 节点间距（0–100）：控件放在顶部工具条里，值由页面持有再传给画布（画布受控模式）
    const [nodeSpacing, setNodeSpacing] = useState(50);

    useEffect(() => {
        if (projectId) {
            loadProject();
        }
    }, [projectId]);

    const loadProject = async () => {
        setLoading(true);
        try {
            // 已发布内容 = 发布版本快照（非活图谱）：重抽不影响展示，重新发布才更新
            const data = await publishApi.assetDetail(Number(projectId));
            setProject({ ...data, name: data.project_name || data.name });
            setGraphNodes(data.graph_data?.nodes || []);
            setGraphEdges(data.graph_data?.edges || []);
        } catch (error: any) {
            notifyError(error, '加载本体详情失败');
            backToAssetCenter();
        } finally {
            setLoading(false);
        }
    };

    // 发布快照自带的统计（类/实例/关系/属性）：比按节点 type 数更准，
    // 也不用把 owl:NamedIndividual 这类术语暴露给用户（2026-11-16）
    const assetStats = project?.stats || {};
    // 知识域名称：资产接口给的是字符串（proj.domains），构建器的 ProjectData 里是对象。
    // 旧代码一律取 .name，字符串上取不到 → 渲染出"只有图标没有文字"的空项（2026-11-16 修）
    const domainLabel = (() => {
        const d = project?.domain as unknown as string | { name?: string } | null | undefined;
        if (!d) return '';
        return typeof d === 'string' ? d : (d.name || '');
    })();

    const classCount = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type)).length;
    const instanceCount = graphNodes.filter((n) => (n.data || {}).type === 'owl:NamedIndividual').length;

    // 与实例探索同规则：类节点常显；实例仅在其所属类被右键展开后显示
    const { displayNodes, displayEdges } = useMemo(() => {
        const classToInstances = new Map<string, string[]>();
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

    // ── 左侧列表树（与实例探索同款：类 → 实例）──
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
        const q = treeSearchQuery.toLowerCase();
        const nodes = classNodes.map((classNode) => {
            const title = classNode.data?.label || '未命名类';
            const children = (classToInstances[classNode.id] || []).map((iid) => {
                const inst = graphNodes.find((n) => n.id === iid);
                return {
                    title: inst?.data?.label || '未命名实例',
                    key: iid,
                    icon: <span className="inline-block w-3 h-3 rounded-full" style={{ background: '#5B8DEF' }} />,
                    isLeaf: true,
                    searchableTitle: inst?.data?.label || '未命名实例',
                };
            });
            return { title, key: classNode.id, icon: <span className="inline-block w-3 h-3 rounded-full" style={{ background: '#B585F2' }} />, children, searchableTitle: title };
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
    }, [graphNodes, graphEdges, treeSearchQuery]);

    // 列表树右上角只放一个「全部展开 / 全部收起」（2026-11-20）：按当前是否已全展开切换。
    // 面板收起时不计算树数据，省掉一次整图遍历。
    const listTreeData = isLeftPanelExpanded ? buildTreeData() : [];
    // 只看有子节点的类：没有实例的类展开不了，不该让按钮永远停在「全部展开」
    const listExpandableNodes = listTreeData.filter((n: any) => (n.children?.length ?? 0) > 0);
    const listTreeAllExpanded = listExpandableNodes.length > 0
        && listExpandableNodes.every((n: any) => manualExpandedKeys.has(n.key));

    const onTreeSelect: TreeProps['onSelect'] = (selectedKeys) => {
        if (selectedKeys.length === 0) return;
        setSelectedEdge(null);
        setSelectedNode({ type: 'node', id: selectedKeys[0] as string });
    };

    // 展开/收起只改变视图，入口文案已随状态切换，不再弹全局提示（2026-11-20）
    const toggleExpand = (node: any) => {
        const classId = node.id;
        setExpandedNodeIds((prev) => {
            const next = new Set(prev);
            if (next.has(classId)) next.delete(classId);
            else next.add(classId);
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
        if (classIdsWithInstances.size === 0) message.info('当前没有实例节点');
    };

    const collapseAll = () => {
        setExpandedNodeIds(new Set());
    };

    // DetailDrawer 导航/聚焦用的是行表 id（数字或 URI），画布节点 id 存于实体 uri 尾段 → 解析回画布并聚焦
    const focusCanvasNode = useCallback(async (rowId: string, opts?: { expand?: boolean }) => {
        if (!projectId) return;
        try {
            const d = await graphApi.nodeDetail(Number(projectId), rowId);
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
            if (opts?.expand && node && node.data?.type !== 'owl:NamedIndividual') {
                setExpandedNodeIds((prev) => new Set(prev).add(canvasId));
            }
            setHighlightNodeId(canvasId);
            setNeighborhoodPinId(canvasId);
            focusSeqRef.current += 1;
            setFocusRequest({ nodeId: canvasId, seq: focusSeqRef.current });
        } catch { /* 聚焦失败不阻断详情浏览 */ }
    }, [projectId, graphNodes, graphEdges, expandedNodeIds]);

    // 面包屑与三级路径一致（2026-11-25）：资产中心 → 域名 → 本体。
    // 「资产中心」回一级总览，域名回二级（该域的本体列表），和左上返回按钮的落点一致
    const breadcrumbs = [
        { title: '首页', path: '/' },
        fromDomainId == null
            ? { title: '资产中心', path: '/asset-center' }
            : { title: '资产中心', onClick: () => navigate('/asset-center') },
        ...(fromDomainName ? [{ title: fromDomainName, onClick: backToAssetCenter }] : []),
        { title: project?.name || fromProjectName || '本体详情' },
    ];

    // 加载态直接用最终构图：同样是浅色外壳 + 内缩深色台面 + 左栏面板骨架，
    // 数据回来是在原位填充，避免一整块深色面积突然浮上来（2026-11-19）
    if (loading) {
        return <WorkspaceSkeleton breadcrumbs={breadcrumbs} panelClass="w-[400px]" />;
    }

    if (!project) {
        return (
            <div className="h-full flex flex-col app-canvas">
                <Navbar breadcrumbs={breadcrumbs} />
                <div className="flex-1 flex items-center justify-center text-sm text-[#66738F]">
                    本体不存在或已下线
                </div>
            </div>
        );
    }

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={breadcrumbs} />
            <div className="asset-workspace flex-1 flex relative overflow-hidden m-4 sm:m-5 min-h-0">
                <style>{`
                    /* 深色工作区：内容区不整屏铺深色（那会和浅色侧栏/顶栏硬切一刀，像主题没切干净），
                       而是四边内缩、做成一块圆角深色"台面"压在浅色底图上——
                       和构建器「实例探索」同一套路（那边靠 px-4 内缩），这里再补圆角和投影。
                       留白给足（16/20px）+ 深一档的外投影，它才读作"挂上去的一块屏"，
                       而不是"另一半页面变黑了"（2026-11-17） */
                    .asset-workspace {
                        border-radius: 16px;
                        background: #0F1420;
                        border: 1px solid rgba(15, 23, 42, 0.22);
                        box-shadow:
                            0 28px 60px -34px rgba(15, 23, 42, 0.62),
                            0 8px 18px -12px rgba(15, 23, 42, 0.28),
                            inset 0 1px 0 rgba(255, 255, 255, 0.06);
                    }
                    /* 左栏信息面板（2026-11-17 改深色）：原来是白面 .surface-card 浮在浅色画布上，
                       右边紧挨着 #0F1420 的图谱画布——两套材质硬拼在一起，白卡像一张纸贴在黑板上。
                       现在面板是画布里浮起的一层：比画布抬一档的冷海军蓝 +
                       左上蓝 / 右下紫两团柔光，和图谱画布的节点色（类紫 / 实例蓝 / 关系青 / 属性琥珀）同一套。
                       不做点阵一类的颗粒肌理（2026-11-17 用户否掉）：深色面板上它一密就显得脏 */
                    .asset-panel {
                        border-radius: 16px;
                        border: 1px solid rgba(255, 255, 255, 0.085);
                        background-image:
                            radial-gradient(460px 280px at -10% -8%, rgba(91, 141, 239, 0.28), transparent 70%),
                            radial-gradient(460px 320px at 112% 116%, rgba(139, 92, 246, 0.20), transparent 72%),
                            linear-gradient(165deg, #1E2740 0%, #182035 55%, #151D2F 100%);
                        box-shadow:
                            inset 0 1px 0 rgba(255, 255, 255, 0.10),
                            0 26px 50px -32px rgba(0, 0, 0, 0.85),
                            0 2px 6px rgba(0, 0, 0, 0.35);
                        overflow: hidden;
                    }
                    /* 段落标题前的短彩条：与工作台说明板同一枚，深底上给整列一条竖向彩色节奏 */
                    .ap-mark {
                        width: 3px;
                        height: 13px;
                        border-radius: 999px;
                        background: linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%);
                        flex-shrink: 0;
                    }
                    /* 导出走链接按钮：默认给品牌浅蓝，深底上才不会落成一条看不见的灰字 */
                    .ap-export.ant-btn { color: #8FB6F5 !important; }
                    .ap-export.ant-btn:hover,
                    .ap-export.ant-btn:focus-visible {
                        color: #A9C8FF !important;
                        background: rgba(91, 141, 239, 0.12) !important;
                    }
                    /* 顶部工具条的按钮（2026-11-17）：原来用的是 antd 默认按钮 + 深色底覆盖，
                       描边是默认灰、hover 直接落成 antd 的浅蓝底，在深色画布上像控件没做完。
                       统一成「深色玻璃」：透明底 + 冷灰描边 + 浅灰字，hover 才亮到品牌蓝 */
                    .ap-ghost.ant-btn,
                    .ap-tool.ant-btn {
                        display: inline-flex;
                        align-items: center;
                        gap: 6px;
                        border-radius: 8px;
                        background: rgba(19, 26, 42, 0.55) !important;
                        border-color: #24304A !important;
                        color: #C6CEDF !important;
                        font-size: 12.5px;
                    }
                    .ap-ghost.ant-btn { height: 32px; padding: 0 12px; }
                    .ap-tool.ant-btn { height: 30px; padding: 0 10px; }
                    .ap-ghost.ant-btn:hover,
                    .ap-tool.ant-btn:hover,
                    .ap-ghost.ant-btn:focus-visible,
                    .ap-tool.ant-btn:focus-visible {
                        background: rgba(91, 141, 239, 0.14) !important;
                        border-color: rgba(91, 141, 239, 0.55) !important;
                        color: #E6EAF2 !important;
                    }
                    /* 「列表」开着时保持选中态——一个纯开关，看不出开没开 */
                    .ap-tool-on.ant-btn,
                    .ap-tool-on.ant-btn:hover {
                        background: rgba(91, 141, 239, 0.20) !important;
                        border-color: rgba(91, 141, 239, 0.50) !important;
                        color: #C9D8FF !important;
                    }
                    /* 列表头部两个图标按钮 */
                    .ap-icon-btn.ant-btn { color: #8B94AB !important; }
                    .ap-icon-btn.ant-btn:hover,
                    .ap-icon-btn.ant-btn:focus-visible {
                        color: #C9D8FF !important;
                        background: rgba(91, 141, 239, 0.14) !important;
                    }
                    /* 搜索框：默认的白底输入框压在深色列表头上太跳，换成半透明冷色 */
                    .asset-inst-search.ant-input-affix-wrapper {
                        background: rgba(255, 255, 255, 0.05) !important;
                        border-color: rgba(255, 255, 255, 0.10) !important;
                        border-radius: 8px;
                    }
                    .asset-inst-search.ant-input-affix-wrapper:hover,
                    .asset-inst-search.ant-input-affix-wrapper-focused {
                        background: rgba(255, 255, 255, 0.07) !important;
                        border-color: rgba(91, 141, 239, 0.55) !important;
                        box-shadow: none !important;
                    }
                    .asset-inst-search .ant-input { background: transparent !important; color: #E6EAF2 !important; }
                    .asset-inst-search .ant-input::placeholder { color: #6E7B99 !important; }
                    .asset-inst-search .ant-input-clear-icon { color: #6E7B99; }
                    /* 工具条里的节点间距滑杆（2026-11-17）：4px 轨道 + 14px 亮面滑钮。
                       几个坑一次说清：
                       1) 选择器必须写成 input[type="range"].ap-range——index.css 里的全局
                          input[type="range"]{width:100%;height:8px} 特异性更高，只用 .ap-range 会被压掉；
                       2) 输入元素本身给 16px 高、轨道缩到 4px：元素盒子太矮时滑钮会被压到偏低的位置，
                          再用 margin-top:(thumb-track)/-2 把钮按回轨道中线；
                       3) 轨道"已填充"的渐变由行内 CSS 变量 --ap-range-fill 传进来，画在 track 上
                          （元素背景是透明的，否则 track 会盖住它） */
                    input[type="range"].ap-range {
                        width: 116px;
                        height: 16px;
                        background: transparent;
                        -webkit-appearance: none;
                        appearance: none;
                        outline: none;
                        cursor: pointer;
                    }
                    input[type="range"].ap-range::-webkit-slider-runnable-track {
                        height: 4px;
                        border-radius: 999px;
                        background: var(--ap-range-fill, #24304A);
                    }
                    input[type="range"].ap-range::-moz-range-track {
                        height: 4px;
                        border-radius: 999px;
                        background: var(--ap-range-fill, #24304A);
                    }
                    input[type="range"].ap-range::-webkit-slider-thumb {
                        -webkit-appearance: none;
                        appearance: none;
                        box-sizing: border-box;
                        width: 12px;
                        height: 12px;
                        /* (12 - 4) / -2 = -4：让滑钮正好压在轨道中线上 */
                        margin-top: -4px;
                        border-radius: 50%;
                        /* 品牌蓝→紫的小珠子：深色包边把它和同色相的轨道分开，
                           顶部一道内高光 + 外圈荧光，和面板里的发光圆点是同一套语言
                           （纯白钮太跳、16px 又偏大，2026-11-17 两次调整后定在 12px） */
                        background: linear-gradient(150deg, #7FB2FF 0%, #8B5CF6 100%);
                        border: 1.5px solid #0F1420;
                        box-shadow:
                            0 0 0 1px rgba(139, 92, 246, 0.55),
                            0 0 9px rgba(91, 141, 239, 0.70),
                            inset 0 1px 1px rgba(255, 255, 255, 0.55);
                        cursor: pointer;
                        transition: box-shadow 0.18s ease, transform 0.18s ease;
                    }
                    input[type="range"].ap-range::-webkit-slider-thumb:hover {
                        transform: scale(1.08);
                        box-shadow:
                            0 0 0 1px rgba(139, 92, 246, 0.85),
                            0 0 13px rgba(91, 141, 239, 0.90),
                            inset 0 1px 1px rgba(255, 255, 255, 0.55);
                    }
                    input[type="range"].ap-range::-moz-range-thumb {
                        box-sizing: border-box;
                        width: 12px;
                        height: 12px;
                        border-radius: 50%;
                        background: linear-gradient(150deg, #7FB2FF 0%, #8B5CF6 100%);
                        border: 1.5px solid #0F1420;
                        box-shadow:
                            0 0 0 1px rgba(139, 92, 246, 0.55),
                            0 0 9px rgba(91, 141, 239, 0.70),
                            inset 0 1px 1px rgba(255, 255, 255, 0.55);
                        cursor: pointer;
                    }
                `}</style>
                {/* 左侧信息面板（2026-11-16）：原来是 5 张独立 Card 竖排，在 420px 宽的列里
                    像一叠板子；合并成一张面板、内部用发丝线分段——与项目详情页同一套结构。
                    空描述整段不渲染；OWL 术语（owl:NamedIndividual 等）不再暴露，
                    统计改用发布快照自带的 stats */}
                <div
                    className="w-[400px] flex-shrink-0 overflow-y-auto p-4"
                    style={{ borderRight: '1px solid #1A2233' }}
                >
                    <div className="asset-panel">
                        {/* 身份段：名称 + 发布状态 + 一行元信息（作者 · 知识域 · 发布时间 · 版本） */}
                        <div className="border-b px-5 pb-3.5 pt-4" style={{ borderColor: AP_DIV }}>
                            <div className="flex items-start justify-between gap-4">
                                {/* 项目名单行截断（2026-11-25）：与「查看图谱」页同一处理——
                                    长名字不再折成两行去挤右侧的发布状态标签 */}
                                <h1
                                    className="min-w-0 flex-1 truncate text-[20px] font-semibold leading-7 text-[#F2F6FF]"
                                    title={project.name}
                                >
                                    {project.name}
                                </h1>
                                <Tag
                                    className="!m-0 shrink-0"
                                    style={{ background: 'rgba(91,141,239,0.18)', color: '#9EC1FF', borderColor: 'rgba(91,141,239,0.36)' }}
                                >
                                    {PUBLISHED_TAG.text}
                                </Tag>
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-[#8B94AB]">
                                {/* 资产详情接口目前不返回 owner，缺数据时整项不显示，
                                    而不是摆一个「未知」占位（2026-11-16） */}
                                {project.owner?.username && (
                                    <span className="flex items-center gap-1">
                                        <UserOutlined style={{ color: '#6E7B99' }} />{project.owner.username}
                                    </span>
                                )}
                                {domainLabel && (
                                    <span className="flex items-center gap-1">
                                        <DatabaseOutlined style={{ color: '#6E7B99' }} />{domainLabel}
                                    </span>
                                )}
                                {/* 只显示发布时间，不显示 version_no（2026-11-16）：
                                    version_no 是"该项目第几个版本快照"（构建和发布共用计数器），
                                    不是"第几次发布"——项目 10 显示 v5 其实只发布过 1 次，容易被误读 */}
                                {project.published_at && (
                                    <span>{`发布 ${new Date(project.published_at).toLocaleDateString('zh-CN')}`}</span>
                                )}
                            </div>
                        </div>

                        {/* 结构概览：类 / 实例 / 关系 / 属性，取自发布快照 stats（比按节点类型数更准）。
                            四项之间走竖向发丝线分列（不再是四块各自为政的小格），
                            每项一枚同色小光点 + 浅一档的彩色数字，四列才有分区感 */}
                        <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                            <div className="mb-2.5 flex items-center justify-between">
                                <span className="flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                    <span className="ap-mark" aria-hidden />
                                    结构概览
                                </span>
                                <span className="tk-mono text-[10px] uppercase tracking-[0.16em] text-[#6E7B99]">snapshot</span>
                            </div>
                            <div className="grid grid-cols-4">
                                {AP_STATS.map((k, i) => {
                                    const value =
                                        k.key === 'classes' ? (assetStats.classes ?? classCount)
                                            : k.key === 'instances' ? (assetStats.instances ?? instanceCount)
                                                : k.key === 'relations' ? (assetStats.relations ?? graphEdges.length)
                                                    : (assetStats.properties ?? null);
                                    return (
                                        <div
                                            key={k.key}
                                            className={i > 0 ? 'border-l pl-3' : ''}
                                            style={i > 0 ? { borderColor: AP_DIV } : undefined}
                                        >
                                            <div className="flex items-center gap-1.5 text-[11px] text-[#8B94AB]">
                                                <span
                                                    className="h-[5px] w-[5px] shrink-0 rounded-full"
                                                    style={{ background: k.color, boxShadow: `0 0 6px ${k.color}` }}
                                                />
                                                {k.label}
                                            </div>
                                            <div className="tk-mono mt-1 text-[20px] font-semibold leading-6" style={{ color: k.ink }}>
                                                {value ?? '—'}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>

                        {/* 项目描述：没有描述就整段不出现，不再用一张卡表达"暂无描述" */}
                        {project.description && (
                            <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                                <div className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                    <span className="ap-mark" aria-hidden />
                                    项目描述
                                </div>
                                <p className="m-0 line-clamp-6 text-[13px] leading-6 text-[#A9B7D9]">
                                    {project.description}
                                </p>
                            </div>
                        )}

                        {/* 图例（2026-11-16）：原本压在画布左下角，挪进左栏——
                            既填上左栏下半部的空白，也让"图例属于这个本体"的归属更清楚。
                            配色与画布一致（深色主题档），圆点上带一层同色荧光，深底上才点得出来 */}
                        <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                            <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                <span className="ap-mark" aria-hidden />
                                图例
                            </div>
                            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-[#C6CEDF]">
                                <span className="flex items-center gap-1.5">
                                    <span
                                        className="h-[11px] w-[11px] rounded-full"
                                        style={{ background: '#B585F2', border: '2px solid #8F5BD9', boxShadow: '0 0 8px rgba(181,133,242,0.55)' }}
                                    />
                                    类
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <span
                                        className="h-[11px] w-[11px] rounded-full"
                                        style={{ background: '#5B8DEF', border: '2px solid #3E6CC7', boxShadow: '0 0 8px rgba(91,141,239,0.55)' }}
                                    />
                                    实例
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <span className="inline-block w-4" style={{ borderTop: '1.5px dashed #5B8DEF' }} />
                                    类型
                                </span>
                                <span className="flex items-center gap-1.5">
                                    <span className="inline-block w-4" style={{ borderTop: '1.5px solid #6E80B0' }} />
                                    关系
                                </span>
                            </div>
                        </div>

                        {/* 导出：改成段尾的文字按钮，不再单独占一行 block 按钮 */}
                        <div className="px-5 py-2.5">
                            <Button
                                type="link"
                                size="small"
                                icon={<ExportOutlined />}
                                className="ap-export !h-auto !px-0 !text-[12.5px]"
                                onClick={() => setExportOpen(true)}
                            >
                                导出图谱…
                            </Button>
                        </div>
                    </div>
                </div>

                {/* 右侧图谱展示区（与实例探索一致的深色展示） */}
                <div className="flex-1 flex flex-col min-w-0 relative h-full" style={{ background: '#0F1420' }}>
                    {/* 顶部工具条（2026-11-17）：返回按钮和两个操作按钮原来是浮在画布上的两个簇，
                        列表一展开就压在列表头上——返回按钮(z-30)盖住树的前几行、居中的工具条盖住
                        「类与实例列表」标题和搜索框。改成一整条占位的工具条：左返回、右操作，
                        下面是「列表 + 画布」，谁也不再浮在谁上面 */}
                    <div
                        className="flex h-12 items-center gap-2 px-3 flex-shrink-0"
                        style={{ borderBottom: '1px solid #1A2233' }}
                    >
                        <Button
                            icon={<ArrowLeftOutlined />}
                            onClick={backToAssetCenter}
                            className="ap-ghost"
                        >
                            {/* 域名可能很长：限宽截断，工具栏右侧还有节点间距滑杆等控件 */}
                            <span className="inline-block max-w-[180px] truncate align-middle">{backLabel}</span>
                        </Button>
                        <div className="ml-auto flex items-center gap-3">
                            {/* 节点间距（2026-11-17）：直接摆进工具条、常显滑杆。
                                它本来就不是"设置项"，是边看图边拉的东西——收进药丸要点两下才看得见效果。
                                窄屏（<1280）工具条放不下整组，这一组整块隐藏，图仍可缩放/拖拽 */}
                            <label className="hidden xl:flex shrink-0 items-center gap-2">
                                <span className="whitespace-nowrap text-[11.5px] text-[#8B94AB]">节点间距</span>
                                <input
                                    type="range"
                                    min={0}
                                    max={100}
                                    value={nodeSpacing}
                                    onChange={(e) => setNodeSpacing(Number(e.target.value))}
                                    className="ap-range shrink-0"
                                    aria-label="节点间距"
                                    style={{
                                        // 已填充到当前值的轨道渐变：用 CSS 变量传给 ::-webkit-slider-runnable-track
                                        '--ap-range-fill': `linear-gradient(90deg, #5B8DEF 0%, #8B5CF6 ${nodeSpacing}%, #24304A ${nodeSpacing}%, #24304A 100%)`,
                                    } as React.CSSProperties}
                                />
                                <span className="tk-mono w-[30px] shrink-0 whitespace-nowrap text-[11.5px]" style={{ color: '#9BC0F7' }}>
                                    {spacingLabel(nodeSpacing)}
                                </span>
                            </label>
                            <span className="hidden xl:block h-5 w-px" style={{ background: '#1A2233' }} aria-hidden />
                            <div className="flex items-center gap-1.5">
                                {/* 文案自己说明动作（2026-11-19）：展开中显示「收起实例」、收起时显示「展开实例」，
                                    不再靠悬停提示复述按钮要做什么 */}
                                <Button
                                    icon={Array.from(expandedNodeIds).length > 0 ? <ShrinkOutlined /> : <ExpandOutlined />}
                                    onClick={Array.from(expandedNodeIds).length > 0 ? collapseAll : expandAll}
                                    className="ap-tool"
                                >
                                    {Array.from(expandedNodeIds).length > 0 ? '收起实例' : '展开实例'}
                                </Button>
                                <Button
                                    icon={<UnorderedListOutlined />}
                                    onClick={() => setIsLeftPanelExpanded((v) => !v)}
                                    className={`ap-tool ${isLeftPanelExpanded ? 'ap-tool-on' : ''}`}
                                >
                                    {isLeftPanelExpanded ? '收起列表' : '展开列表'}
                                </Button>
                            </div>
                        </div>
                    </div>

                    <div className="flex-1 flex overflow-hidden">
                        {/* 左侧展开面板（与实例探索同款） */}
                        {/* 左侧列表（2026-11-17）：宽度动画期间内容不再重排——内层固定宽度、
                            外层 overflow-hidden，收放时是"滑出/滑入"而不是"挤扁"，
                            否则树里的长标题会在动画中途折成两行。边缘那枚悬浮收起按钮已去掉，
                            改由顶部工具条的「列表」按钮开关（带选中态），少一个压在树上的控件。
                            宽度 380 → 240（2xl 280）：这一列在 1440 屏下只有 ~760px，
                            380 的列表加 400 的信息栏，画布只剩一半不到；240 时画布能拿到近 70%，
                            树里的长名字靠单行省略兜住 */}
                        <div
                            className={`transition-all duration-300 ease-in-out flex-shrink-0 relative overflow-hidden ${
                                isLeftPanelExpanded ? 'w-[240px] 2xl:w-[280px]' : 'w-0'
                            }`}
                            style={{
                                background: '#131A2A',
                                boxShadow: isLeftPanelExpanded ? '4px 0 12px rgba(0, 0, 0, 0.35)' : 'none',
                                borderRight: isLeftPanelExpanded ? '1px solid #1A2233' : 'none',
                            }}
                        >
                            <div className="h-full flex flex-col overflow-hidden min-w-[240px] 2xl:min-w-[280px]">
                                <div className="p-3 border-b flex-shrink-0" style={{ borderColor: '#1A2233' }}>
                                    <div className="flex items-center justify-between mb-2">
                                        <h3 className="flex items-center gap-2 text-[13px] font-semibold" style={{ color: '#E6EAF2' }}>
                                            <span className="ap-mark" aria-hidden />
                                            类与实例列表
                                        </h3>
                                        <div className="flex items-center gap-0.5">
                                            <Tooltip title={listTreeAllExpanded ? '全部收起' : '全部展开'}>
                                                <Button type="text" size="small" className="ap-icon-btn"
                                                    icon={listTreeAllExpanded ? <ShrinkOutlined /> : <ExpandOutlined />}
                                                    onClick={() => setManualExpandedKeys(listTreeAllExpanded
                                                        ? new Set()
                                                        : new Set(listTreeData.map((n: any) => n.key)))} />
                                            </Tooltip>
                                        </div>
                                    </div>
                                    <Input
                                        className="asset-inst-search"
                                        placeholder="搜索类或实例…"
                                        size="small"
                                        value={treeSearchValue}
                                        onChange={(e) => setTreeSearchValue(e.target.value)}
                                        {...treeSearchInputProps}
                                        allowClear
                                        prefix={<SearchOutlined style={{ color: '#6E7B99' }} />}
                                    />
                                </div>
                                <div className="flex-1 overflow-auto p-2">
                                    <style>{`
                                        /* 列表树（2026-11-17 逐项对齐）。antd v5 Tree 的默认值有三处和深色列表冲突，
                                           不显式压掉就会出现"箭头偏下、圆点和文字不共线、hover 字消失"：
                                           1) treenode 是 align-items: flex-start —— 箭头贴在行顶；
                                           2) 图标容器固定 24×24、vertical-align: top —— 12px 圆点被顶高、还和文字拉开距离；
                                           3) hover / 选中态的文字色取 colorText（近黑）—— 深底上等于把字擦掉。 */
                                        .asset-inst-tree, .asset-inst-tree .ant-tree-list, .asset-inst-tree .ant-tree-list-holder,
                                        .asset-inst-tree .ant-tree-list-holder-inner { background: transparent !important; }
                                        /* 行：整行垂直居中，而不是 antd 的 flex-start */
                                        .asset-inst-tree .ant-tree-treenode { align-items: center; padding: 2px 0; color: #E6EAF2; }
                                        /* 箭头：宽度收窄、撑满行高、自身居中（不再固定 24px 高） */
                                        .asset-inst-tree .ant-tree-switcher {
                                            width: 18px;
                                            height: auto;
                                            align-self: stretch;
                                            display: flex;
                                            align-items: center;
                                            justify-content: center;
                                            margin-inline-end: 0;
                                            color: #6E7B99;
                                        }
                                        /* 内容：flex 单行，圆点与标题都是 flex 项，共用一个 align-items: center */
                                        .asset-inst-tree .ant-tree-node-content-wrapper {
                                            display: flex;
                                            align-items: center;
                                            gap: 6px;
                                            min-height: 26px;
                                            padding: 2px 6px;
                                            line-height: 20px;
                                            color: #E6EAF2;
                                            min-width: 0;
                                        }
                                        .asset-inst-tree .ant-tree-node-content-wrapper:hover {
                                            background: rgba(91, 141, 239, 0.15) !important;
                                            color: #E6EAF2 !important;
                                            border-radius: 6px;
                                        }
                                        .asset-inst-tree .ant-tree-node-content-wrapper-selected,
                                        .asset-inst-tree .ant-tree-node-selected {
                                            background: rgba(91, 141, 239, 0.28) !important;
                                            color: #E6EAF2 !important;
                                        }
                                        /* 图标容器：antd 固定 24×24（选择器是 .ant-tree .ant-tree-node-content-wrapper .ant-tree-iconEle，
                                           三档特异性），这里要写得同样深再补 !important，否则压不掉——压不掉的表现就是
                                           圆点被 24px 的盒子推到离箭头很远、并靠上 */
                                        .asset-inst-tree .ant-tree-node-content-wrapper .ant-tree-iconEle {
                                            width: 12px !important;
                                            height: 12px !important;
                                            line-height: 12px !important;
                                            display: flex !important;
                                            align-items: center;
                                            justify-content: center;
                                            vertical-align: middle;
                                            flex: none;
                                        }
                                        /* 圆点自己的右边距交给上面的 gap，避免双份间距 */
                                        .asset-inst-tree .ant-tree-iconEle > span { margin-right: 0 !important; }
                                        /* 标题单行省略：必须走 flex 项（min-width:0），直接 display:block 会掉到图标下面 */
                                        .asset-inst-tree .ant-tree-title {
                                            flex: 1 1 auto;
                                            min-width: 0;
                                            overflow: hidden;
                                            text-overflow: ellipsis;
                                            white-space: nowrap;
                                        }
                                    `}</style>
                                    {treeSearchQuery && listTreeData.length === 0 ? (
                                        <EmptyState
                                            tone="dark"
                                            className="h-full min-h-[220px]"
                                            title="没有找到匹配的类或实例"
                                            hint="换个关键词试试，或清除搜索查看全部"
                                            actionText="清除搜索"
                                            onAction={() => setTreeSearchValue('')}
                                        />
                                    ) : (
                                        <Tree
                                            showIcon
                                            expandedKeys={Array.from(manualExpandedKeys)}
                                            onExpand={(keys) => setManualExpandedKeys(new Set(keys as string[]))}
                                            selectedKeys={selectedNode ? [selectedNode.id] : []}
                                            onSelect={onTreeSelect}
                                            treeData={listTreeData as any}
                                            blockNode
                                            className="asset-inst-tree"
                                        />
                                    )}
                                </div>
                                <div className="p-3 border-t flex-shrink-0" style={{ borderColor: '#1A2233', background: 'rgba(15, 20, 32, 0.6)' }}>
                                    <div className="flex items-center justify-between text-xs" style={{ color: '#8B94AB' }}>
                                        <span className="flex items-center">
                                            <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#B585F2', boxShadow: '0 0 6px rgba(181,133,242,0.7)' }} />
                                            类：<span className="tk-mono ml-1" style={{ color: '#C9AEF8' }}>{classCount}</span>
                                        </span>
                                        <span className="flex items-center">
                                            <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: '#5B8DEF', boxShadow: '0 0 6px rgba(91,141,239,0.7)' }} />
                                            实例：<span className="tk-mono ml-1" style={{ color: '#9BC0F7' }}>{instanceCount}</span>
                                        </span>
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* 主内容区 */}
                        <div className="flex-1 flex flex-col min-w-0 relative h-full">
                            {/* 画布 */}
                            <div className="flex-1 relative min-h-0">
                                <div className="absolute inset-0">
                                    <D3ForceGraph
                                        theme="dark"
                                        showLegend={false}
                                        spacingControl="none"
                                        spacing={nodeSpacing}
                                        onSpacingChange={setNodeSpacing}
                                        nodes={displayNodes}
                                        edges={displayEdges}
                                        onNodeClick={(node) => {
                                            setSelectedEdge(null);
                                            setNeighborhoodPinId(null);
                                            setHighlightNodeId(null);
                                            setSelectedNode(node ? { type: 'node', id: node.id } : null);
                                        }}
                                        onEdgeClick={(edge) => {
                                            setSelectedNode(null);
                                            setSelectedEdge(edge);
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
            </div>

            {/* 节点详情：与实例探索一致的 DetailDrawer（只读展示） */}
            <DetailDrawer
                projectId={Number(projectId)}
                selection={selectedNode}
                onClose={() => setSelectedNode(null)}
                onNavigateNode={(nid) => {
                    setSelectedNode({ type: 'node', id: nid });
                    focusCanvasNode(nid);
                }}
                onExpandNode={(nid) => focusCanvasNode(nid)}
            />

            {/* 边详情：轻量弹窗（只读）。2026-11-17 换成和详情抽屉同一套语言：
                冷蓝底 + 彩条标题 + 细线行 + 等宽 id，不再是白底黑字的三行纯文本 */}
            {selectedEdge && (
                <Modal
                    open
                    onCancel={() => setSelectedEdge(null)}
                    footer={null}
                    width={440}
                    rootClassName="tk-surface-immersive tk-surface-deco tk-title-marked"
                    title={
                        <span className="flex items-center gap-2 text-[15px] font-semibold text-[#0F172A]">
                            <span className="ap-mark" aria-hidden />
                            关系详情
                        </span>
                    }
                    styles={{
                        // 冷蓝底由全局 --surface-immersive 统一提供（与详情抽屉同色值），
                        // 这里只把 padding 归零：.ant-modal-content 自带一圈内边距，
                        // 不归零的话 header/body 的边距是叠加在它上面的，怎么调都多一截
                        content: { padding: 0 },
                        // 标题栏必须显式 transparent：antd 默认给 header 铺的是白色（colorBgElevated），
                        // 不覆盖就会在渐变上盖一条白带
                        header: { background: 'transparent', borderBottom: '1px solid #E1EAF8', marginBottom: 0, padding: '12px 18px' },
                        body: { padding: '14px 18px 16px' },
                    }}
                >
                    <div className="mb-2 flex items-center gap-2">
                        <Tag
                            className="!m-0"
                            style={{ background: 'rgba(139,92,246,0.12)', color: '#6D3FD8', borderColor: 'rgba(139,92,246,0.30)' }}
                        >
                            {selectedEdge.data?.label || selectedEdge.data?.relation || '未命名关系'}
                        </Tag>
                        {selectedEdge.data?.confidence != null && (
                            <span className="tk-mono text-[11.5px] text-[#66738F]">
                                置信 {Number(selectedEdge.data.confidence).toFixed(2)}
                            </span>
                        )}
                    </div>
                    {[
                        { label: '源头', value: String(selectedEdge.source) },
                        { label: '目标', value: String(selectedEdge.target) },
                    ].map((r) => (
                        <div
                            key={r.label}
                            className="flex items-baseline gap-3 py-1.5 text-[13px]"
                            style={{ borderTop: '1px solid rgba(47, 98, 216, 0.10)' }}
                        >
                            <span className="w-[52px] shrink-0 text-[#66738F]">{r.label}</span>
                            <span className="tk-mono min-w-0 break-all text-[#1F2A44]" style={{ fontSize: 12 }}>
                                {r.value}
                            </span>
                        </div>
                    ))}
                </Modal>
            )}

            <ExportDialog
                open={exportOpen}
                onClose={() => setExportOpen(false)}
                projectId={Number(projectId)}
                title="导出图谱（资产中心）"
            />
        </div>
    );
};

export default AssetDetailPage;
