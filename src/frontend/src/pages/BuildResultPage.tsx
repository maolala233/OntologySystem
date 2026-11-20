/**
 * 本体图谱查看页（2026-11-05 改版）：`/projects/:id/result` —— 只读图谱。
 * 构建结果明细（类/关系/实例/公理）已在项目详情页直接展示，本页专注图谱呈现：
 * D3 力导图（类常显、右键类节点展开实例、点击节点/边看详情）。
 *
 * 2026-11-17 视觉对齐资产中心「已发布本体详情」：
 *   浅色外壳 + 内缩深色台面（.asset-workspace）→ 左栏深色信息面板（.asset-panel：
 *   身份 / 结构概览 / 质量自检 / 图例 / 操作）→ 右侧占位工具条 + 类实例列表 + 画布。
 *   原来「白卡头部 + bg-gray-900 圆角块 + 浮在画布中央的工具条」那套是旧版拼装：
 *   头部白卡和深色画布硬切、工具条压在列表头上、列表树箭头和圆点不共线。
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { message, notifyError } from '../shared/feedback';
import { Button, Input, Modal, Tag, Tooltip, Tree } from 'antd';
import type { TreeProps } from 'antd/es/tree';
import {
    ArrowLeftOutlined, DatabaseOutlined,
    ExpandOutlined, SearchOutlined, ShrinkOutlined, UnorderedListOutlined,
} from '@ant-design/icons';

import { pipelineApi } from '../api/pipeline';
import { projectsApi } from '../api/projects';
import { PROJECT_STATUS } from '../shared/projectStatus';
import { useBuilderStore } from '../features/builder/store';
import D3ForceGraph, { spacingLabel } from '../components/OntologyGraph/D3ForceGraph';
import DetailDrawer from '../features/graph-explorer/DetailDrawer';
import Navbar from '../components/Layout/Navbar';
import WorkspaceSkeleton from '../components/Layout/WorkspaceSkeleton';
import Tip from '../shared/Tip';
import EmptyState from '../components/EmptyState';

const CLASS_TYPES = new Set(['owl:Class', 'Class']);

/** 深色面板内部的分隔线：和画布的 #1A2233 同一档（与资产详情页同一个常量语义） */
const AP_DIV = 'rgba(255,255,255,0.07)';

/** 结构概览四个指标各自的色：直接取画布节点/关系的配色（类紫、实例蓝、关系青、属性琥珀），
 *  数字用同色相的浅一档——深底上淡彩数字立得住 */
const AP_STATS = [
    { key: 'classes', label: '类', color: '#B585F2', ink: '#C9AEF8' },
    { key: 'instances', label: '实例', color: '#5B8DEF', ink: '#9BC0F7' },
    { key: 'relations', label: '关系', color: '#5AC8FA', ink: '#8BDCF2' },
    { key: 'properties', label: '属性', color: '#F2B950', ink: '#F5D08C' },
] as const;

const fmtDate = (v?: string | null) =>
    v ? new Date(v).toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }) : '';

const BuildResultPage: React.FC = () => {
    const { projectId } = useParams<{ projectId: string }>();
    const navigate = useNavigate();
    const pid = Number(projectId);
    const {
        projectId: storeProjectId, projectName, projectStatus, domainName,
        projectDescription, projectUpdatedAt, loadProject,
    } = useBuilderStore();
    // 元数据属于"当前 pid"才算就绪：切换项目后 fetch 回来之前先显示占位而不是上一个项目的名字
    const projectReady = storeProjectId === pid;

    const [summary, setSummary] = useState<import('../api/pipeline').BuildSummary | null>(null);
    const [loading, setLoading] = useState(true);

    // 只读图谱（数据源 = 项目 graph_data）
    const [graphNodes, setGraphNodes] = useState<any[]>([]);
    const [graphEdges, setGraphEdges] = useState<any[]>([]);
    const [expandedNodeIds, setExpandedNodeIds] = useState<Set<string>>(new Set());
    const [selectedNode, setSelectedNode] = useState<{ type: 'node'; id: string } | null>(null);
    const [selectedEdge, setSelectedEdge] = useState<any>(null);
    // 类/实例列表树（与资产中心同款）+ 展开收起所有实例
    const [isLeftPanelExpanded, setIsLeftPanelExpanded] = useState(false);
    const [treeSearchValue, setTreeSearchValue] = useState('');
    const [manualExpandedKeys, setManualExpandedKeys] = useState<Set<string>>(new Set());
    // 节点间距（0–100）：控件摆在工具条里，值由页面持有再传给画布（受控模式）
    const [nodeSpacing, setNodeSpacing] = useState(50);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            // 一次 getProject 同时喂「只读图谱」和顶部元数据：store 复用同一份，
            // 不再各自 GET 一次（原来同一 URL 会打两次，2026-11-19）
            const [project, summaryData] = await Promise.all([
                projectsApi.getProject(pid).catch(() => null),
                pipelineApi.buildSummary(pid).catch((e: any) => {
                    notifyError(e, '加载构建结果失败');
                    return null;
                }),
            ]);
            if (project) {
                setGraphNodes(project.graph_data?.nodes || []);
                setGraphEdges(project.graph_data?.edges || []);
                void loadProject(pid, project);
            } else {
                void loadProject(pid);   // 兜底：失败也让 store 记录 loadError（左栏提示）
            }
            if (summaryData) setSummary(summaryData);
        } finally {
            setLoading(false);
        }
    }, [pid, loadProject]);

    useEffect(() => { load(); }, [load]);
    // 左栏身份段（知识域 / 更新时间）走共享 store：由上面的 load() 用同一份 getProject 填充

    // 切到别的项目时先清掉上一个项目的摘要与图谱，避免路由参数直切时短暂显示旧数据
    useEffect(() => {
        setSummary(null);
        setGraphNodes([]);
        setGraphEdges([]);
        setSelectedNode(null);
        setSelectedEdge(null);
    }, [pid]);

    // 与资产中心/实例探索同规则：类节点常显；实例仅在其所属类被展开后显示
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
                visible.add(String(n.id));
                if (expandedNodeIds.has(String(n.id))) {
                    (classToInstances.get(String(n.id)) || []).forEach((iid) => visible.add(iid));
                }
            } else if (t === 'owl:NamedIndividual') {
                const owner = instanceToClass.get(String(n.id));
                if (owner && expandedNodeIds.has(owner)) visible.add(String(n.id));
            }
        });
        return {
            displayNodes: graphNodes.filter((n) => visible.has(String(n.id))),
            displayEdges: graphEdges.filter((e) => visible.has(String(e.source))
                && visible.has(String(e.target))),
        };
    }, [graphNodes, graphEdges, expandedNodeIds]);

    // ── 左侧列表树（与资产中心同款：类 → 实例）──
    const buildTreeData = useCallback(() => {
        const classNodes = graphNodes.filter((n) => CLASS_TYPES.has((n.data || {}).type));
        const classToInstances: Record<string, string[]> = {};
        const linkedInstances = new Set<string>();
        graphEdges.forEach((e) => {
            const label = e.label || e.data?.label || e.data?.relation || '';
            if ((label === 'rdf:type' || e.data?.relation === 'instance_of')
                && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type)) {
                const classId = String(e.target);
                (classToInstances[classId] = classToInstances[classId] || []).push(String(e.source));
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
                if (cid) (classToInstances[cid] = classToInstances[cid] || []).push(String(n.id));
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
                    icon: <span className="inline-block w-3 h-3 rounded-full" style={{ background: '#5B8DEF' }} />,
                    isLeaf: true,
                    searchableTitle: inst?.data?.label || '未命名实例',
                };
            });
            return {
                title,
                key: classNode.id,
                icon: <span className="inline-block w-3 h-3 rounded-full" style={{ background: '#B585F2' }} />,
                children,
                searchableTitle: title,
            };
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

    const expandAllInstances = () => {
        const classIdsWithInstances = new Set(
            graphEdges
                .filter((e) => (e.label === 'rdf:type' || e.data?.relation === 'instance_of')
                    && CLASS_TYPES.has(graphNodes.find((n) => n.id === e.target)?.data?.type))
                .map((e) => String(e.target))
        );
        setExpandedNodeIds(new Set(classIdsWithInstances));
        if (classIdsWithInstances.size === 0) message.info('当前没有实例节点');
    };

    const collapseAllInstances = () => {
        setExpandedNodeIds(new Set());
    };

    // 面包屑：项目名来自 store，未就绪时用「项目详情」占位，避免标题闪变
    const breadcrumbs = [
        { title: '首页', path: '/' },
        { title: '我的项目', path: '/my-projects' },
        {
            title: projectReady && projectName ? projectName : '项目详情',
            path: `/projects/${pid}`,
        },
        { title: '本体图谱' },
    ];

    if (loading || !summary) {
        // 加载态直接用最终构图（与资产详情同一套骨架），避免深色台面在数据到达时突然出现
        return <WorkspaceSkeleton breadcrumbs={breadcrumbs} />;
    }

    const sc = (summary.pipeline?.selfcheck || null) as null | {
        rounds?: number; fixed?: number; remaining?: number;
    };
    const checks = summary.checks;
    const checkPassed = !checks || checks.error_count === 0;
    const statusMeta = PROJECT_STATUS[projectStatus] || PROJECT_STATUS.ready;

    // 结构概览：优先用构建摘要（口径与详情页一致），图谱没加载出来时不会掉成 0
    const statValue = (key: typeof AP_STATS[number]['key']) => {
        switch (key) {
            case 'classes': return summary.schema?.classes_total ?? 0;
            case 'instances': return summary.instances?.total ?? 0;
            case 'relations': return summary.relation_stats?.total_edges ?? summary.relations_total ?? graphEdges.length;
            case 'properties':
                return (summary.schema?.object_properties_total ?? 0)
                    + (summary.schema?.datatype_properties_total ?? 0);
            default: return 0;
        }
    };
    const classCount = summary.schema?.classes_total ?? 0;
    const instanceCount = summary.instances?.total ?? 0;

    return (
        <div className="h-full flex flex-col app-canvas">
            <Navbar breadcrumbs={breadcrumbs} />
            <div className="asset-workspace flex-1 flex relative overflow-hidden m-4 sm:m-5 min-h-0">
                <style>{`
                    /* ── 外壳：与资产详情页完全同一套 ──
                       内容区不整屏铺深色（会和浅色侧栏/顶栏硬切一刀），而是四边内缩、
                       做成一块圆角深色"台面"压在浅色底图上 */
                    .asset-workspace {
                        border-radius: 16px;
                        background: #0F1420;
                        border: 1px solid rgba(15, 23, 42, 0.22);
                        box-shadow:
                            0 28px 60px -34px rgba(15, 23, 42, 0.62),
                            0 8px 18px -12px rgba(15, 23, 42, 0.28),
                            inset 0 1px 0 rgba(255, 255, 255, 0.06);
                    }
                    /* 左栏信息面板：画布里浮起的一层——比画布抬一档的冷海军蓝 +
                       左上蓝 / 右下紫两团柔光。不用点阵一类的颗粒肌理（深底上一密就显脏） */
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
                    /* 段落标题前的短彩条（面板装饰语言：彩条 + 发光圆点 + 彩色等宽数字） */
                    .ap-mark {
                        width: 3px;
                        height: 13px;
                        border-radius: 999px;
                        background: linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%);
                        flex-shrink: 0;
                    }
                    /* 工具条按钮统一成「深色玻璃」：
                       透明底 + 冷灰描边 + 浅灰字，hover 才亮到品牌蓝 */
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
                    /* 「列表」开着时保持选中态——纯开关看不出开没开 */
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
                    /* 搜索框：默认白底输入框压在深色列表头上太跳，换成半透明冷色 */
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
                    /* 工具条里的节点间距滑杆：4px 轨道 + 12px 亮面滑钮。
                       1) 选择器写成 input[type="range"].ap-range——index.css 里全局
                          input[type="range"]{width:100%;height:8px} 特异性更高，只用 .ap-range 会被压掉；
                       2) 元素本身 16px 高、轨道 4px：元素盒子太矮时滑钮会被压到偏低的位置；
                       3) 轨道"已填充"的渐变由行内 CSS 变量 --ap-range-fill 传进来 */
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
                    /* ── 列表树（逐项对齐 antd v5 的默认值）──
                       1) treenode 是 align-items: flex-start —— 箭头贴在行顶；
                       2) 图标容器固定 24×24、vertical-align: top —— 12px 圆点被顶高、还和文字拉开距离；
                       3) hover / 选中态的文字色取 colorText（近黑）—— 深底上等于把字擦掉 */
                    .asset-inst-tree, .asset-inst-tree .ant-tree-list, .asset-inst-tree .ant-tree-list-holder,
                    .asset-inst-tree .ant-tree-list-holder-inner { background: transparent !important; }
                    .asset-inst-tree .ant-tree-treenode { align-items: center; padding: 2px 0; color: #E6EAF2; }
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
                    .asset-inst-tree .ant-tree-iconEle > span { margin-right: 0 !important; }
                    .asset-inst-tree .ant-tree-title {
                        flex: 1 1 auto;
                        min-width: 0;
                        overflow: hidden;
                        text-overflow: ellipsis;
                        white-space: nowrap;
                    }
                `}</style>

                {/* ── 左栏：项目身份 + 结构概览 + 质量自检 + 图例 + 操作 ──
                    原来这些（除了图例）挤在页面顶部一张白卡里，和深色画布硬切成两块；
                    并进深色面板后，整页只剩"一块台面"这一个材质 */}
                <div
                    className="w-[360px] 2xl:w-[400px] flex-shrink-0 overflow-y-auto p-4"
                    style={{ borderRight: '1px solid #1A2233' }}
                >
                    <div className="asset-panel">
                        {/* 身份段：名称 + 状态 + 一行元信息（知识域 · 更新时间） */}
                        <div className="border-b px-5 pb-3.5 pt-4" style={{ borderColor: AP_DIV }}>
                            <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0">
                                    <Tip text={projectReady ? projectName : undefined}>
                                        <h1 className="line-clamp-2 text-[19px] font-semibold leading-7 text-[#F2F6FF]">
                                            {projectReady && projectName ? projectName : '加载中…'}
                                        </h1>
                                    </Tip>
                                    <div className="mt-1 text-[11.5px] text-[#8B94AB]">本体图谱 · 只读视图</div>
                                </div>
                                <Tag
                                    className="!m-0 shrink-0"
                                    style={{
                                        background: 'rgba(91,141,239,0.18)',
                                        color: '#9EC1FF',
                                        borderColor: 'rgba(91,141,239,0.36)',
                                    }}
                                >
                                    {statusMeta.text}
                                </Tag>
                            </div>
                            <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-[#8B94AB]">
                                {projectReady && domainName && (
                                    <span className="flex items-center gap-1">
                                        <DatabaseOutlined style={{ color: '#6E7B99' }} />{domainName}
                                    </span>
                                )}
                                {projectReady && projectUpdatedAt && (
                                    <span>{`更新 ${fmtDate(projectUpdatedAt)}`}</span>
                                )}
                            </div>
                        </div>

                        {/* 结构概览：类 / 实例 / 关系 / 属性。四项之间走竖向发丝线分列，
                            每项一枚同色小光点 + 浅一档的彩色等宽数字 */}
                        <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                            <div className="mb-2.5 flex items-center justify-between">
                                <span className="flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                    <span className="ap-mark" aria-hidden />
                                    结构概览
                                </span>
                                <span className="tk-mono text-[10px] uppercase tracking-[0.16em] text-[#6E7B99]">graph</span>
                            </div>
                            <div className="grid grid-cols-4">
                                {AP_STATS.map((k, i) => (
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
                                            {statValue(k.key)}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>

                        {/* 项目描述：有才渲染，没有就整段不出现（不摆"暂无描述"占位） */}
                        {projectReady && projectDescription && (
                            <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                                <div className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                    <span className="ap-mark" aria-hidden />
                                    项目描述
                                </div>
                                <Tip text={projectDescription}>
                                    <p className="m-0 line-clamp-6 text-[13px] leading-6 text-[#A9B7D9]">
                                        {projectDescription}
                                    </p>
                                </Tip>
                            </div>
                        )}

                        {/* 质量自检（原头部卡里的两个标签）：状态点 + 说明，
                            比 antd 预设色标签（浅绿/浅红底）压在深色面板上更贴这套材质 */}
                        {(checks || sc) && (
                            <div className="border-b px-5 py-3.5" style={{ borderColor: AP_DIV }}>
                                <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-[#E6EAF2]">
                                    <span className="ap-mark" aria-hidden />
                                    质量自检
                                </div>
                                {checks && (
                                    <div className="flex items-center gap-2 text-[12.5px]">
                                        <span
                                            className="h-2 w-2 shrink-0 rounded-full"
                                            style={{
                                                background: checkPassed ? '#34C3A4' : '#F2685C',
                                                boxShadow: `0 0 8px ${checkPassed ? 'rgba(52,195,164,0.75)' : 'rgba(242,104,92,0.75)'}`,
                                            }}
                                        />
                                        <span style={{ color: checkPassed ? '#8BE8CF' : '#FFB4AC' }}>
                                            {checkPassed ? '机检通过' : `机检 ${checks.error_count} 处问题`}
                                        </span>
                                    </div>
                                )}
                                {sc && (
                                    <div className="mt-1.5 text-[12px] leading-5 text-[#A9B7D9]">
                                        自检修复 {sc.rounds ?? 0} 轮 · 修复 {sc.fixed ?? 0} 处
                                        {(sc.remaining ?? 0) > 0 && (
                                            <span style={{ color: '#F5D08C' }}> · 剩余 {sc.remaining} 硬伤</span>
                                        )}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* 图例：配色与画布一致（深色主题档），圆点上带一层同色荧光。
                            面板最后一段，所以不画下边线（否则面板底部会多一条悬空的发丝线） */}
                        <div className="px-5 py-3.5">
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
                    </div>
                </div>

                {/* ── 右侧：工具条 + 类实例列表 + 画布 ── */}
                <div className="flex-1 flex flex-col min-w-0 relative h-full" style={{ background: '#0F1420' }}>
                    {/* 顶部工具条（占位，不再是浮在画布上的两个簇）：左返回、右操作 */}
                    <div
                        className="flex h-12 items-center gap-2 px-3 flex-shrink-0"
                        style={{ borderBottom: '1px solid #1A2233' }}
                    >
                        <Button
                            icon={<ArrowLeftOutlined />}
                            onClick={() => navigate(`/projects/${pid}`)}
                            className="ap-ghost"
                        >
                            返回项目详情
                        </Button>
                        <div className="ml-auto flex items-center gap-3">
                            {/* 节点间距常显滑杆；窄屏整组隐藏，图仍可缩放/拖拽 */}
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
                                    onClick={Array.from(expandedNodeIds).length > 0 ? collapseAllInstances : expandAllInstances}
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
                        {/* 左侧列表：内层固定宽度、外层 overflow-hidden——收放时是"滑出/滑入"
                            而不是"挤扁"，否则树里的长标题会在动画中途折成两行 */}
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
                                        allowClear
                                        prefix={<SearchOutlined style={{ color: '#6E7B99' }} />}
                                    />
                                </div>
                                <div className="flex-1 overflow-auto p-2">
                                    {treeSearchValue.trim() && listTreeData.length === 0 ? (
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

                        {/* 画布 */}
                        <div className="flex-1 flex flex-col min-w-0 relative h-full">
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
                                        onNodeClick={(node: any) => {
                                            setSelectedEdge(null);
                                            setSelectedNode(node ? { type: 'node', id: node.id } : null);
                                        }}
                                        onEdgeClick={(edge: any) => {
                                            setSelectedNode(null);
                                            setSelectedEdge(edge);
                                        }}
                                        onNodeRightClick={(node: any) => {
                                            if (!node) return;
                                            const nid = String(node.id);
                                            setExpandedNodeIds((prev) => {
                                                const next = new Set(prev);
                                                if (next.has(nid)) {
                                                    next.delete(nid);
                                                } else {
                                                    next.add(nid);
                                                }
                                                return next;
                                            });
                                        }}
                                    />
                                </div>
                                {/* 操作提示：深色玻璃胶囊，压在画布左下角。
                                    放左边是因为右下角已被画布自带的「当前视图 N 个节点」占着，
                                    两个都居中/居右在窄画布上会叠到一起 */}
                                <div
                                    className="pointer-events-none absolute bottom-3 left-3 whitespace-nowrap rounded-full px-3 py-1 text-[11.5px]"
                                    style={{
                                        background: 'rgba(19, 26, 42, 0.85)',
                                        border: '1px solid #1A2233',
                                        color: '#8B94AB',
                                        backdropFilter: 'blur(6px)',
                                    }}
                                >
                                    只读 · 右键类节点展开/收起实例 · 点击节点查看详情
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* 节点详情：与实例探索一致的 DetailDrawer（只读展示） */}
            <DetailDrawer
                projectId={pid}
                selection={selectedNode}
                onClose={() => setSelectedNode(null)}
                onNavigateNode={(nid: string) => setSelectedNode({ type: 'node', id: nid })}
                onExpandNode={(nid: string) => {
                    setExpandedNodeIds((prev) => {
                        const next = new Set(prev);
                        next.add(String(nid));
                        return next;
                    });
                }}
            />

            {/* 边详情：轻量弹窗。与详情抽屉同一套语言——冷蓝底 + 彩条标题 + 细线行 + 等宽 id，
                不再是白底黑字的三行纯文本（padding 归零：.ant-modal-content 自带一圈内边距） */}
            {selectedEdge && (
                <Modal
                    open
                    onCancel={() => setSelectedEdge(null)}
                    footer={null}
                    width={440}
                    rootClassName="tk-surface-immersive tk-surface-deco"
                    title={
                        <span className="flex items-center gap-2 text-[15px] font-semibold text-[#0F172A]">
                            <span className="ap-mark" aria-hidden />
                            关系详情
                        </span>
                    }
                    styles={{
                        // 冷蓝底由全局 --surface-immersive 统一提供（与详情抽屉同色值）
                        content: { padding: 0 },
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
                        {
                            label: '起点',
                            value: graphNodes.find((n) => n.id === selectedEdge.source)?.data?.label
                                || String(selectedEdge.source),
                        },
                        {
                            label: '终点',
                            value: graphNodes.find((n) => n.id === selectedEdge.target)?.data?.label
                                || String(selectedEdge.target),
                        },
                    ].map((r) => (
                        <div
                            key={r.label}
                            className="flex items-baseline gap-3 py-1.5 text-[13px]"
                            style={{ borderTop: '1px solid rgba(47, 98, 216, 0.10)' }}
                        >
                            <span className="w-[52px] shrink-0 text-[#66738F]">{r.label}</span>
                            <span className="min-w-0 break-all text-[#1F2A44]" style={{ fontSize: 12 }}>
                                {r.value}
                            </span>
                        </div>
                    ))}
                    {selectedEdge.data?.source_quote && (
                        <div
                            className="mt-2 rounded-lg px-3 py-2 text-[12px] leading-[1.7] text-[#41506F]"
                            style={{ background: 'rgba(255,255,255,0.55)', borderLeft: '3px solid #F2B950' }}
                        >
                            <div className="mb-0.5 text-[11px] text-[#8B94AB]">证据</div>
                            {selectedEdge.data.source_quote}
                        </div>
                    )}
                </Modal>
            )}
        </div>
    );
};

export default BuildResultPage;
