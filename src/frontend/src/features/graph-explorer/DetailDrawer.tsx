/**
 * ★节点/边详情抽屉（M4 用户增强）：点击节点或关系连接点查看全量信息。
 * 节点：基本信息/属性/溯源证据（原文引用+文档定位）/关系清单/合并留痕/相关审核/时间线。
 * 边：谓词、两端实体、置信度、溯源证据。
 */
import React, { useEffect, useRef, useState } from 'react';
import { notifyError } from '../../shared/feedback';
import { Button, Descriptions, Drawer, Empty, Modal, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { CheckCircleFilled, FileTextOutlined, LinkOutlined, ReloadOutlined, SendOutlined, ExpandOutlined } from '@ant-design/icons';

import { EdgeDetail, NodeDetail, graphApi } from '../../api/graphview';
import { documentsApi } from '../../api/documents';

const { Text, Paragraph } = Typography;

const KIND_LABEL: Record<string, string> = {
    class: '类', instance: '实例',
};

const STATUS_COLOR: Record<string, string> = {
    auto: 'default', merged: 'cyan', pending_review: 'gold',
    approved: 'success', rejected: 'error', claimed: 'processing',
};

/** 切片原文上下文弹层：切片全文 + 证据原句高亮（溯源 char_start/end 为切片内偏移） */
const ChunkContextModal: React.FC<{
    projectId: number;
    prov: { document_id: number | null; document_name: string | null;
            chunk_index: number | null; evidence: string | null;
            char_start?: number | null; char_end?: number | null } | null;
    onClose: () => void;
}> = ({ projectId, prov, onClose }) => {
    const [loading, setLoading] = useState(false);
    const [chunk, setChunk] = useState<{ text: string; document_name: string; chunk_index: number; char_start: number | null } | null>(null);
    const highlightRef = useRef<HTMLSpanElement>(null);

    useEffect(() => {
        if (!prov?.document_id || prov.chunk_index == null) return;
        setLoading(true);
        setChunk(null);
        documentsApi.chunkContext(projectId, prov.document_id, prov.chunk_index)
            .then((res) => setChunk(res.chunk))
            .catch((e) => notifyError(e, '切片上下文加载失败'))
            .finally(() => setLoading(false));
    }, [prov?.document_id, prov?.chunk_index, projectId]);

    useEffect(() => {
        // 弹层打开后滚到高亮句
        if (chunk && highlightRef.current) {
            setTimeout(() => highlightRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }), 100);
        }
    }, [chunk]);

    let before = '';
    let hit = '';
    let after = '';
    if (chunk && prov?.char_start != null && prov?.char_end != null
        && prov.char_end > prov.char_start && prov.char_end <= chunk.text.length) {
        before = chunk.text.slice(0, prov.char_start);
        hit = chunk.text.slice(prov.char_start, prov.char_end);
        after = chunk.text.slice(prov.char_end);
    } else if (chunk) {
        after = chunk.text;
    }

    return (
        <Modal
            open={!!prov}
            onCancel={onClose}
            footer={null}
            width={720}
            rootClassName="tk-surface-deco"
            title={
                <span>
                    <FileTextOutlined className="mr-2" />
                    原文上下文{chunk ? `：${chunk.document_name} · 切片 #${chunk.chunk_index}` : ''}
                </span>
            }
        >
            {loading ? (
                <div className="py-10 text-center"><Spin tip="加载切片原文…" /></div>
            ) : chunk ? (
                <>
                    {chunk.char_start != null && (
                        <div className="mb-2 text-xs text-gray-500">
                            切片在原文档中的偏移：字符 [{chunk.char_start}, {chunk.char_start + chunk.text.length}]
                        </div>
                    )}
                    <div className="border rounded p-3 max-h-[480px] overflow-auto bg-white" style={{ fontSize: 13, lineHeight: 1.9 }}>
                        <span>{before}</span>
                        {hit && (
                            <span ref={highlightRef} className="bg-amber-300 rounded px-0.5 font-medium">{hit}</span>
                        )}
                        <span>{after}</span>
                    </div>
                </>
            ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无法加载切片" />
            )}
        </Modal>
    );
};

type ProvRow = { id: number; document_id: number | null; document_name: string | null;
                chunk_index: number | null; evidence: string | null;
                method?: string | null; checksum?: string | null;
                char_start?: number | null; char_end?: number | null };

function EvidenceCards({ rows, projectId }: { projectId: number; rows: ProvRow[] }) {
    const [ctxProv, setCtxProv] = useState<ProvRow | null>(null);
    if (!rows.length) return <EmptyLine text="暂无溯源记录" />;
    return (
        <div className="space-y-2">
            {rows.map((p) => (
                <div key={p.id} className="dd-evidence">
                    <div className="flex items-center gap-2 flex-wrap mb-1 text-xs text-gray-500">
                        <Text strong className="text-gray-700">{p.document_name || '未知文档'}</Text>
                        {p.chunk_index != null &&
                            <Tooltip title={TERM_HELP['chunk']}><Tag>chunk #{p.chunk_index}</Tag></Tooltip>}
                        {p.method && (
                            <Tooltip title={p.method === 'llm_ner'
                                ? '该证据由大模型命名实体识别（LLM NER）从原文抽取'
                                : `证据抽取方式：${p.method}`}>
                                <Tag color="blue">{p.method}</Tag>
                            </Tooltip>
                        )}
                        {p.char_start != null && p.char_end != null &&
                            <Tag>字符 [{p.char_start}, {p.char_end}]</Tag>}
                        {p.checksum && <Tooltip title={`checksum ${p.checksum}`}><Text code className="text-[10px]">{p.checksum.slice(0, 10)}…</Text></Tooltip>}
                    </div>
                    <Paragraph className="!mb-0 text-gray-800" style={{ fontSize: 13 }}>
                        “{(p.evidence || '').trim()}”
                    </Paragraph>
                    <div className="flex items-center gap-3 mt-1">
                        {p.document_id && p.chunk_index != null && (
                            <a className="text-xs cursor-pointer"
                               onClick={() => setCtxProv(p)}>
                                <FileTextOutlined /> 查看原文上下文
                            </a>
                        )}
                        {p.document_id && (
                            <a className="text-xs cursor-pointer"
                               onClick={() => {
                                   documentsApi.download(projectId, p.document_id!, p.document_name || undefined)
                                       .catch((e) => notifyError(e, '原文档下载失败'));
                               }}>
                                <LinkOutlined /> 查看原文档
                            </a>
                        )}
                    </div>
                </div>
            ))}
            <ChunkContextModal projectId={projectId} prov={ctxProv} onClose={() => setCtxProv(null)} />
        </div>
    );
}

function Section({ title, extra, children }: { title: React.ReactNode; extra?: React.ReactNode; children: React.ReactNode }) {
    return (
        <section className="mb-5">
            <div className="dd-section-head">
                <span className="dd-mark" aria-hidden />
                <Text strong className="text-[13px] text-[#1F2A44]">{title}</Text>
                {extra && <span className="ml-auto">{extra}</span>}
            </div>
            {children}
        </section>
    );
}

/**
 * 详情分区的一行式空状态（2026-11-25）。
 * antd Empty 的插图 + 上下留白要占近 100px，「暂无属性 / 暂无溯源 / 暂无关系」
 * 这种一句话就能说清的事，换成一条虚线短条：仍然明确「这一项没有值」，
 * 但不会把下面的分区一路推到抽屉外。
 */
function EmptyLine({ text }: { text: string }) {
    return (
        <div className="flex items-center gap-1.5 rounded-md border border-dashed border-[#D8E2F3] px-2.5 py-1.5 text-[11.5px] text-[#8A94AB]">
            <span className="h-1 w-1 shrink-0 rounded-full bg-[#B7C1D4]" aria-hidden />
            {text}
        </div>
    );
}

/** 学术术语悬停解释：虚线下划线提示可悬停 */
const TERM_HELP: Record<string, string> = {
    度数: '该节点与其他节点直接相连的关系边总数（入边 + 出边）。度数越高，说明该实体在图谱中关联越密集、越核心。',
    置信: '抽取引擎对这条数据的把握程度（0~1）。越高越可信；置信度较低的数据通常会转入人工审核。',
    置信度: '抽取引擎对这条数据的把握程度（0~1）。越高越可信；置信度较低的数据通常会转入人工审核。',
    URI: '该实体在本项目图谱中的唯一标识地址，类似「身份证号」，跨系统引用时不会混淆。',
    规范名: '实体消解合并后，作为该实体标准称呼的名称。',
    归一化: '实体消解时把不同写法（全半角、大小写、别名等）统一后的标准形式，用于机器判断是否为同一实体。',
    溯源证据: '记录这条数据来自哪份文档的哪个切片、原文是什么，便于人工核对与审计。',
    谓词: '两个实体之间关系的类型，如「属于」「发行机构」。',
    双向边: '两个节点之间同时存在方向相反的两条关系时，画布上以弧形错开绘制，避免两条线重叠。',
    chunk: '文档解析时切分出的片段编号，是检索与溯源的基本单位。',
    被合并: '实体消解判定它与另一实体为同一事物，数据已并入规范实体（点击可跳转查看）。',
    合并源: '该实体由多个来源节点合并而来，这是被并入的来源节点。',
};

export function Term({ t, children }: { t: string; children?: React.ReactNode }) {
    const help = TERM_HELP[t];
    if (!help) return <>{children ?? t}</>;
    return (
        <Tooltip title={help}>
            <span className="cursor-help border-b border-dotted border-gray-400">{children ?? t}</span>
        </Tooltip>
    );
}

interface Props {
    projectId: number;
    selection: { type: 'node' | 'edge'; id: string } | null;
    onClose: () => void;
    onNavigateNode?: (nodeId: string) => void;
    /** expand=true 时额外展开该节点的邻域（类节点=展开实例） */
    onExpandNode?: (nodeId: string, opts?: { expand?: boolean }) => void;
    /** R13：节点详情右上角追加操作（实例探索的编辑/加关系/删除等，由宿主页面注入） */
    extraNodeActions?: (nodeId: string) => React.ReactNode;
    /** 变化后触发详情刷新（手动编辑保存成功等） */
    reloadKey?: number;
}

/**
 * 详情抽屉的局部样式（2026-11-17）：把 antd 默认的「灰色带框表格 + 加粗黑标题」
 * 换成全站同一套语言——细线分段、彩条小标题、冷白底，和说明板 / 信息面板一致。
 * 抽屉本质是阅读面板，跟着浅色外壳走，不跟着深色画布走。
 */
const DETAIL_CSS = `
    .dd-mark {
        width: 3px; height: 13px; border-radius: 999px; flex-shrink: 0;
        background: linear-gradient(180deg, #5B8DEF 0%, #8B5CF6 100%);
    }
    .dd-section-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    /* 描述列表：去掉 antd 的表格边框，改成"标签列 + 细线行"，并且一律透底 */
    .dd-desc .ant-descriptions-view { border: 0 !important; background: transparent !important; }
    .dd-desc .ant-descriptions-view > table { border: 0 !important; background: transparent !important; }
    .dd-desc .ant-descriptions-row > th,
    .dd-desc .ant-descriptions-row > td {
        border-bottom: 1px solid rgba(47, 98, 216, 0.10) !important;
        background: transparent !important;
        padding: 7px 0 !important;
    }
    .dd-desc .ant-descriptions-item-label { width: 88px; color: #66738F !important; font-weight: 400 !important; }
    .dd-desc .ant-descriptions-item-content { color: #1F2A44; }
    /* 溯源证据：不再是一张张白卡片，改成带琥珀竖线的引用块——证据本来就是"原文引用"，
       引用块的形态最贴切，也不会在冷蓝底上戳出一排白方块 */
    .dd-evidence {
        background: transparent;
        border: 0;
        border-left: 3px solid #F2B950;
        border-radius: 0;
        padding: 2px 0 2px 12px;
    }
    /* 抽屉头部两个操作按钮（在画布中聚焦 / 邻域展开）：
       默认的 antd 白底按钮在冷蓝底上同样显白，统一成"半透明白描边 + 主按钮走品牌渐变" */
    .dd-action.ant-btn {
        background: rgba(255, 255, 255, 0.62);
        border-color: rgba(91, 141, 239, 0.30);
        color: #2A57C6;
        border-radius: 8px;
    }
    .dd-action.ant-btn:hover,
    .dd-action.ant-btn:focus-visible {
        background: #FFFFFF;
        border-color: rgba(91, 141, 239, 0.55) !important;
        color: #1F4FB3 !important;
    }
    .dd-action-primary.ant-btn {
        background: linear-gradient(95deg, #5B8DEF 0%, #8B5CF6 100%);
        border: 0;
        color: #FFFFFF;
        box-shadow: 0 6px 14px -10px rgba(91, 141, 239, 0.85);
    }
    .dd-action-primary.ant-btn:hover,
    .dd-action-primary.ant-btn:focus-visible {
        background: linear-gradient(95deg, #5B8DEF 0%, #8B5CF6 100%) !important;
        color: #FFFFFF !important;
        filter: brightness(1.06);
    }
    /* 表格：透底 + 表头淡蓝、行间细线，不再是一块白格子 */
    .dd-table .ant-table { background: transparent; }
    .dd-table .ant-table-thead > tr > th {
        background: rgba(91, 141, 239, 0.08) !important;
        color: #66738F !important;
        font-weight: 600;
        border-bottom: 1px solid rgba(47, 98, 216, 0.12) !important;
    }
    .dd-table .ant-table-tbody > tr > td {
        background: transparent !important;
        border-bottom: 1px solid rgba(47, 98, 216, 0.08) !important;
    }
    .dd-table .ant-table-tbody > tr:last-child > td { border-bottom: 0 !important; }
    .dd-table .ant-pagination { background: transparent; }
    /* 最后一个分组及其内部最后一块内容都去掉底边距：
       否则分组 20px + 表格分页 16px + body 内边距会层层叠加，抽屉底部空一大截 */
    .dd-drawer .ant-drawer-body section:last-child { margin-bottom: 0; }
    .dd-drawer .ant-drawer-body section:last-child > *:last-child { margin-bottom: 0; }
    .dd-drawer .ant-drawer-body section:last-child .ant-pagination { margin-bottom: 0 !important; }
    .dd-icon-btn.ant-btn { color: #8A94AB !important; }
    .dd-icon-btn.ant-btn:hover { color: #2A57C6 !important; background: rgba(91, 141, 239, 0.10) !important; }
    /* 关闭按钮挪到右侧：antd Drawer 默认放左边，和全站 Modal 的关闭位置不一致 */
    .dd-drawer .ant-drawer-header-title { flex-direction: row-reverse; }
    .dd-drawer .ant-drawer-close { margin-inline: 0 0; }
    .dd-raw summary { color: #8A94AB; cursor: pointer; font-size: 12px; }
    .dd-raw pre {
        margin: 6px 0 0; padding: 8px; background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(47, 98, 216, 0.12); border-radius: 6px;
        font-size: 10.5px; line-height: 1.6; max-height: 128px; overflow: auto;
    }
`;

const DetailDrawer: React.FC<Props> = ({ projectId, selection, onClose, onNavigateNode, onExpandNode, extraNodeActions, reloadKey }) => {
    const [edgeDetail, setEdgeDetail] = useState<EdgeDetail | null>(null);

    useEffect(() => {
        setEdgeDetail(null);
        if (!selection || selection.type !== 'edge') return;
        const load = async () => {
            try {
                setEdgeDetail(await graphApi.edgeDetail(projectId, selection.id));
            } catch (e: any) {
                notifyError(e, '加载详情失败');
            }
        };
        load();
    }, [projectId, selection]);

    return (
        <>
        <style>{DETAIL_CSS}</style>
        <Drawer
            open={!!selection}
            onClose={onClose}
            width={560}
            rootClassName="dd-drawer tk-surface-immersive"
            title={
                <span className="flex items-center gap-2 text-[15px] font-semibold text-[#0F172A]">
                    <span className="dd-mark" aria-hidden />
                    {selection?.type === 'edge' ? '关系详情' : '节点详情'}
                </span>
            }
            styles={{
                // 抽屉整体走冷蓝底（深色画布档，由全局 --surface-immersive 提供，
                // 与关系详情弹窗同一组色值）：header / body 透明，内部表格和描述行一律透底
                header: { padding: '12px 20px', borderBottom: '1px solid #E1EAF8', marginBottom: 0, background: 'transparent' },
                body: { padding: '12px 20px', background: 'transparent' },
            }}
        >
            {!selection && <Empty description="选择节点或关系查看详情" />}
            {selection?.type === 'node' && (
                <NodeDetailContent
                    projectId={projectId}
                    nodeId={selection.id}
                    reloadKey={reloadKey}
                    onNavigateNode={onNavigateNode}
                    onExpandNode={onExpandNode}
                    headerActions={selection ? (
                        <Space wrap size={4}>
                            {extraNodeActions?.(selection.id)}
                            {onExpandNode && <Button size="small" className="dd-action" icon={<SendOutlined />}
                                                     onClick={() => onExpandNode(selection.id)}>在画布中聚焦</Button>}
                            {onExpandNode && <Button size="small" className="dd-action dd-action-primary" icon={<ExpandOutlined />}
                                                     onClick={() => onExpandNode(selection.id, { expand: true })}>邻域展开</Button>}
                        </Space>
                    ) : undefined}
                />
            )}
            {selection?.type === 'edge' && edgeDetail && (
                <div>
                    <Section title="关系">
                        <Descriptions size="small" column={1} className="dd-desc">
                            <Descriptions.Item label={<Term t="谓词" />}><Tag color="purple">{edgeDetail.edge.label}</Tag></Descriptions.Item>
                            <Descriptions.Item label="主体">
                                {edgeDetail.subject ? (
                                    <a onClick={() => edgeDetail.subject && onNavigateNode?.(edgeDetail.subject.id)}>
                                        {edgeDetail.subject.label} <Tag>{edgeDetail.subject.class_label}</Tag>
                                    </a>
                                ) : '（缺失）'}
                            </Descriptions.Item>
                            <Descriptions.Item label="客体">
                                {edgeDetail.object ? (
                                    <a onClick={() => edgeDetail.object && onNavigateNode?.(edgeDetail.object.id)}>
                                        {edgeDetail.object.label} <Tag>{edgeDetail.object.class_label}</Tag>
                                    </a>
                                ) : '（缺失）'}
                            </Descriptions.Item>
                            <Descriptions.Item label={<Term t="置信度" />}>
                                {edgeDetail.confidence != null ? <Tag color="blue">{edgeDetail.confidence.toFixed(2)}</Tag> : '—'}
                            </Descriptions.Item>
                            <Descriptions.Item label="状态"><Tag color={STATUS_COLOR[edgeDetail.status]}>{edgeDetail.status}</Tag></Descriptions.Item>
                            <Descriptions.Item label={<Term t="双向边" />}>{edgeDetail.edge.bidirectional ? `是（弧形偏移 #${edgeDetail.edge.pairIndex}）` : '否'}</Descriptions.Item>
                            {Object.keys(edgeDetail.props).length > 0 && (
                                <Descriptions.Item label="属性">
                                    {Object.entries(edgeDetail.props).map(([k, v]) =>
                                        <div key={k}><Text code>{k}</Text> = {String(v)}</div>)}
                                </Descriptions.Item>
                            )}
                        </Descriptions>
                    </Section>
                    <Section title={<span><Term t="溯源证据" />（{edgeDetail.provenance.length}）</span>}>
                        <EvidenceCards rows={edgeDetail.provenance} projectId={projectId} />
                    </Section>
                </div>
            )}
        </Drawer>
        </>
    );
};

/**
 * 节点详情内容（可复用）：抽壳自 DetailDrawer，「骨架编辑」详情页签与本抽屉共用。
 */
export const NodeDetailContent: React.FC<{
    projectId: number;
    nodeId: string;
    onNavigateNode?: (nodeId: string) => void;
    onExpandNode?: (nodeId: string, opts?: { expand?: boolean }) => void;
    onLoaded?: (d: NodeDetail) => void;
    /** 变化后触发重新加载（宿主页编辑保存成功等） */
    reloadKey?: number;
    /** 节点可能尚未落库（如手动新增未保存）时的友好提示：加载失败时显示它而非报错 */
    unsavedHint?: string;
    /** 抽屉宿主注入的操作按钮（编辑/加关系/删除/聚焦等），渲染在标题下方 */
    headerActions?: React.ReactNode;
}> = ({ projectId, nodeId, onNavigateNode, onExpandNode, onLoaded, reloadKey, unsavedHint, headerActions }) => {
    const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [refreshKey, setRefreshKey] = useState(0);
    // 刷新成功的短暂反馈（时间戳驱动重挂定时器；数据没变化时用户否则不知道点没生效）
    const [refreshOkAt, setRefreshOkAt] = useState<number | null>(null);

    // 当前抽屉里已渲染的是哪个节点（projectId:nodeId）：刷新同一节点时不清空内容
    const loadedKeyRef = useRef<string | null>(null);

    useEffect(() => {
        let alive = true;
        const key = `${projectId}:${nodeId}`;
        // 这次是「同一节点重取」还是首次/换节点：只有前者才需要「已刷新」提示
        const isRefresh = loadedKeyRef.current === key;
        // 刷新 / 宿主保存后重载：内容还是这个节点的，保留旧内容只出 loading；
        // 切换节点才清空——否则会短暂把上一个节点的属性挂到新的标题下
        // （上一个节点留下的「已刷新」也要一起清掉，不能挂到新节点头上）
        if (!isRefresh) {
            setNodeDetail(null);
            setRefreshOkAt(null);
        }
        setLoading(true);
        const load = async () => {
            try {
                const d = await graphApi.nodeDetail(projectId, nodeId);
                if (!alive) return;
                loadedKeyRef.current = key;
                setNodeDetail(d);
                if (isRefresh) setRefreshOkAt(Date.now());
                onLoaded?.(d);
            } catch (e: any) {
                // 未保存节点（宿主已给提示）属预期 404：不再弹错误 toast。
                // 刷新失败时保留上一次的内容，不把抽屉清成空态
                if (alive && !unsavedHint) notifyError(e, '加载详情失败');
            } finally {
                if (alive) setLoading(false);
            }
        };
        load();
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, nodeId, refreshKey, reloadKey]);

    // 「已刷新」1.8s 后收起，避免长期占位
    useEffect(() => {
        if (refreshOkAt == null) return;
        const t = setTimeout(() => setRefreshOkAt(null), 1800);
        return () => clearTimeout(t);
    }, [refreshOkAt]);

    // 首次加载（或切到新节点）才在空白区转圈；刷新时内容还在，loading 落在刷新按钮上
    if (loading && !nodeDetail) return <Spin className="block mx-auto my-10" />;
    if (!nodeDetail) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={unsavedHint || "详情加载失败或节点不存在"} />;
    return (
                <div>
                    {/* 标题行：名字 + 刷新（刷新原来单独占一行、孤零零挂在右上，合并进标题行） */}
                    <div className="mb-3">
                        <div className="flex items-start justify-between gap-3">
                            <Paragraph strong className="!mb-0" style={{ fontSize: 17, lineHeight: 1.45, wordBreak: 'break-word', color: '#0F172A' }}>
                                {nodeDetail.node.label || '未命名节点'}
                            </Paragraph>
                            <div className="flex shrink-0 items-center gap-1.5">
                                {refreshOkAt != null && (
                                    <span className="inline-flex items-center gap-1 text-[11.5px] leading-none text-[#1B87AD]">
                                        <CheckCircleFilled className="text-[11px]" />
                                        已刷新
                                    </span>
                                )}
                                {/* 刷新是通用图标，不再挂「刷新详情」悬停提示（2026-11-25）：
                                    aria-label 留给读屏，鼠标悬停不弹气泡 */}
                                <Button size="small" type="text" icon={<ReloadOutlined />} className="dd-icon-btn shrink-0"
                                        aria-label="刷新详情"
                                        loading={loading}
                                        onClick={() => setRefreshKey((k) => k + 1)} />
                            </div>
                        </div>
                        {headerActions && <div className="mt-2">{headerActions}</div>}
                    </div>
                    <Space wrap className="mb-4">
                        <Tag color={nodeDetail.node.kind.startsWith('class') ? 'purple' : 'blue'}>
                            {KIND_LABEL[nodeDetail.node.kind] || nodeDetail.node.kind}
                        </Tag>
                        <Tag color="geekblue">{nodeDetail.entity.class_label}</Tag>
                        <Tag color={STATUS_COLOR[nodeDetail.entity.status]}>{nodeDetail.entity.status}</Tag>
                        {nodeDetail.entity.confidence != null &&
                            <Tooltip title={TERM_HELP['置信']}><Tag color="blue">置信 {nodeDetail.entity.confidence.toFixed(2)}</Tag></Tooltip>}
                        <Tooltip title={TERM_HELP['度数']}><Tag>度数 {nodeDetail.degree}</Tag></Tooltip>
                    </Space>

                    <Section title="基本信息">
                        <Descriptions size="small" column={1} className="dd-desc">
                            {/* 复制图标本身已经说明动作，去掉 antd 默认的「复制/已复制」悬停气泡（2026-11-25）*/}
                            <Descriptions.Item label="URI"><Text code copyable={{ tooltips: false }} style={{ fontSize: 11 }}>{nodeDetail.entity.uri}</Text></Descriptions.Item>
                            <Descriptions.Item label={<Term t="规范名" />}>{nodeDetail.entity.label}</Descriptions.Item>
                            <Descriptions.Item label={<Term t="归一化" />}>{nodeDetail.entity.label_normalized}</Descriptions.Item>
                            {nodeDetail.entity.instance_count != null &&
                                <Descriptions.Item label="实例数">{nodeDetail.entity.instance_count}</Descriptions.Item>}
                            {nodeDetail.entity.created_at &&
                                <Descriptions.Item label="创建时间">{nodeDetail.entity.created_at.slice(0, 19).replace('T', ' ')}</Descriptions.Item>}
                            {nodeDetail.entity.updated_at &&
                                <Descriptions.Item label="更新时间">{nodeDetail.entity.updated_at.slice(0, 19).replace('T', ' ')}</Descriptions.Item>}
                        </Descriptions>
                    </Section>

                    <Section title={`属性（${Object.keys(nodeDetail.properties).length}）`}>
                        {Object.keys(nodeDetail.properties).length === 0
                            ? <EmptyLine text="暂无属性" />
                            : (
                                <Table
                                    className="dd-table"
                                    size="small" pagination={false} rowKey={(r) => String(r.name)}
                                    dataSource={Object.entries(nodeDetail.properties).map(([name, value]) => ({ name, value }))}
                                    columns={[
                                        { title: '属性', dataIndex: 'name', width: '38%', render: (v) => <Text strong>{v}</Text> },
                                        { title: '值', dataIndex: 'value', render: (v) => String(v) },
                                    ]}
                                />
                            )}
                        {Object.keys(nodeDetail.raw_props).length > 0 && (
                            <details className="dd-raw mt-2">
                                <summary>内部字段（{Object.keys(nodeDetail.raw_props).length}）</summary>
                                <pre>
                                    {JSON.stringify(nodeDetail.raw_props, null, 2)}
                                </pre>
                            </details>
                        )}
                    </Section>

                    <Section title={<span><Term t="溯源证据" />（{nodeDetail.provenance.length}）</span>}>
                        <EvidenceCards rows={nodeDetail.provenance} projectId={projectId} />
                    </Section>

                    <Section title={`关系（${nodeDetail.relations.length}）`}>
                        {nodeDetail.relations.length === 0
                            ? <EmptyLine text="暂无关系" />
                            : (
                                <Table
                                    className="dd-table"
                                    size="small" pagination={{ pageSize: 8 }} rowKey="id"
                                    dataSource={nodeDetail.relations}
                                    columns={[
                                        { title: <Term t="谓词" />, dataIndex: 'predicate', width: '30%',
                                          render: (v, r) => <Tag color={r.direction === 'out' ? 'blue' : 'green'}>{v}{r.direction === 'out' ? ' →' : ' ←'}</Tag> },
                                        { title: '对方', dataIndex: 'other_label',
                                          render: (v, r) => r.other_id ? (
                                              <a onClick={() => r.other_id && onNavigateNode?.(r.other_id)}>{v} <Text type="secondary" className="text-xs">{r.other_class}</Text></a>
                                          ) : v },
                                        { title: <Term t="置信" />, dataIndex: 'confidence', width: 70,
                                          render: (v) => v != null ? v.toFixed(2) : '—' },
                                    ]}
                                />
                            )}
                    </Section>

                    {(nodeDetail.merge.canonical_id || nodeDetail.merge.merged_children.length > 0) && (
                        <Section title="合并信息">
                            {nodeDetail.merge.canonical_id && (
                                <div className="mb-1">
                                    <Tooltip title={TERM_HELP['被合并']}><Tag color="cyan">被合并</Tag></Tooltip>
                                    <a onClick={() => onNavigateNode?.(nodeDetail.merge.canonical_id!)}>
                                        规范实体 #{nodeDetail.merge.canonical_id}
                                    </a>
                                </div>
                            )}
                            {nodeDetail.merge.merged_children.map((c) => (
                                <div key={c.id} className="mb-1">
                                    <Tooltip title={TERM_HELP['合并源']}><Tag color="cyan">合并源</Tag></Tooltip>
                                    <a onClick={() => onNavigateNode?.(c.id)}>{c.label} (#{c.id})</a>
                                </div>
                            ))}
                            {nodeDetail.merge.merge_reviews.map((m) => (
                                <div key={m.review_id} className="text-xs text-gray-500">
                                    审核 #{m.review_id} · {m.status} · {m.decided_at?.slice(0, 19) || ''}
                                </div>
                            ))}
                        </Section>
                    )}

                    {nodeDetail.reviews.length > 0 && (
                        <Section title={`待处理审核项（${nodeDetail.reviews.length}）`}>
                            <div className="space-y-1">
                                {nodeDetail.reviews.map((r) => (
                                    <div key={r.review_id} className="flex items-center gap-2 text-sm">
                                        <Tag color={r.priority === 'critical' ? 'red' : r.priority === 'high' ? 'orange' : 'blue'}>{r.priority}</Tag>
                                        <Tag>{r.item_type}</Tag>
                                        <Text type="secondary" className="text-xs">{r.reason || ''}</Text>
                                    </div>
                                ))}
                            </div>
                        </Section>
                    )}

                    {(nodeDetail.timeline.valid_from || nodeDetail.timeline.valid_until) && (
                        <Section title="时间线">
                            <Descriptions size="small" column={2} className="dd-desc">
                                <Descriptions.Item label="生效起">{nodeDetail.timeline.valid_from || '—'}</Descriptions.Item>
                                <Descriptions.Item label="生效止">{nodeDetail.timeline.valid_until || '—'}</Descriptions.Item>
                            </Descriptions>
                        </Section>
                    )}
                </div>
    );
};


export default DetailDrawer;
