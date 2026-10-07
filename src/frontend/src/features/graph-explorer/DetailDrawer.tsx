/**
 * ★节点/边详情抽屉（M4 用户增强）：点击节点或关系连接点查看全量信息。
 * 节点：基本信息/属性/溯源证据（原文引用+文档定位）/关系清单/合并留痕/相关审核/时间线。
 * 边：谓词、两端实体、置信度、溯源证据。
 */
import React, { useEffect, useRef, useState } from 'react';
import { Button, Descriptions, Drawer, Empty, Modal, Space, Spin, Table, Tag, Tooltip, Typography, message } from 'antd';
import { FileTextOutlined, LinkOutlined, ReloadOutlined, SendOutlined, ExpandOutlined } from '@ant-design/icons';

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
            .catch(() => message.error('切片上下文加载失败'))
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
    if (!rows.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无溯源记录" />;
    return (
        <div className="space-y-2">
            {rows.map((p) => (
                <div key={p.id} className="border-l-4 border-amber-400 bg-amber-50/40 rounded-r px-3 py-2">
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
                                       .catch(() => message.error('原文档下载失败'));
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
        <div className="mb-4">
            <div className="flex items-center justify-between mb-2">
                <Text strong className="text-sm text-gray-700">{title}</Text>
                {extra}
            </div>
            {children}
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

const DetailDrawer: React.FC<Props> = ({ projectId, selection, onClose, onNavigateNode, onExpandNode, extraNodeActions, reloadKey }) => {
    const [nodeTitle, setNodeTitle] = useState<string>('节点详情');
    const [edgeDetail, setEdgeDetail] = useState<EdgeDetail | null>(null);

    useEffect(() => {
        setEdgeDetail(null);
        if (!selection || selection.type !== 'edge') return;
        const load = async () => {
            try {
                setEdgeDetail(await graphApi.edgeDetail(projectId, selection.id));
            } catch (e: any) {
                message.error(e.response?.data?.error?.message || '加载详情失败');
            }
        };
        load();
    }, [projectId, selection]);

    const title = selection?.type === 'edge' ? '关系详情' : nodeTitle;

    return (
        <Drawer
            open={!!selection}
            onClose={onClose}
            width={560}
            title={<Space><span>{title}</span></Space>}
            extra={selection?.type === 'node' && (
                <Space wrap size={4}>
                    {extraNodeActions?.(selection.id)}
                    {onExpandNode && <Button size="small" icon={<SendOutlined />}
                                             onClick={() => onExpandNode(selection.id)}>在画布中聚焦</Button>}
                    {onExpandNode && <Button size="small" type="primary" ghost icon={<ExpandOutlined />}
                                             onClick={() => onExpandNode(selection.id, { expand: true })}>邻域展开</Button>}
                </Space>
            )}
        >
            {!selection && <Empty description="选择节点或关系查看详情" />}
            {selection?.type === 'node' && (
                <NodeDetailContent
                    projectId={projectId}
                    nodeId={selection.id}
                    reloadKey={reloadKey}
                    onNavigateNode={onNavigateNode}
                    onExpandNode={onExpandNode}
                    onLoaded={(d) => setNodeTitle(d.node?.label || '节点详情')}
                />
            )}
            {selection?.type === 'edge' && edgeDetail && (
                <div>
                    <Section title="关系">
                        <Descriptions size="small" column={1} bordered>
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
}> = ({ projectId, nodeId, onNavigateNode, onExpandNode, onLoaded, reloadKey }) => {
    const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [refreshKey, setRefreshKey] = useState(0);

    useEffect(() => {
        let alive = true;
        setLoading(true);
        setNodeDetail(null);
        const load = async () => {
            try {
                const d = await graphApi.nodeDetail(projectId, nodeId);
                if (!alive) return;
                setNodeDetail(d);
                onLoaded?.(d);
            } catch (e: any) {
                if (alive) message.error(e.response?.data?.error?.message || '加载详情失败');
            } finally {
                if (alive) setLoading(false);
            }
        };
        load();
        return () => { alive = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [projectId, nodeId, refreshKey, reloadKey]);

    if (loading) return <Spin className="block mx-auto my-10" />;
    if (!nodeDetail) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="详情加载失败或节点不存在" />;
    return (
                <div>
    <div className="flex justify-end mb-1">
        <Button size="small" type="text" icon={<ReloadOutlined />}
                onClick={() => setRefreshKey((k) => k + 1)} />
    </div>
                    <Space wrap className="mb-3">
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
                        <Descriptions size="small" column={1} bordered>
                            <Descriptions.Item label="URI"><Text code copyable style={{ fontSize: 11 }}>{nodeDetail.entity.uri}</Text></Descriptions.Item>
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
                            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="无属性" />
                            : (
                                <Table
                                    size="small" pagination={false} rowKey={(r) => String(r.name)}
                                    dataSource={Object.entries(nodeDetail.properties).map(([name, value]) => ({ name, value }))}
                                    columns={[
                                        { title: '属性', dataIndex: 'name', width: '38%', render: (v) => <Text strong>{v}</Text> },
                                        { title: '值', dataIndex: 'value', render: (v) => String(v) },
                                    ]}
                                />
                            )}
                        {Object.keys(nodeDetail.raw_props).length > 0 && (
                            <details className="mt-2">
                                <summary className="text-xs text-gray-400 cursor-pointer">内部字段（{Object.keys(nodeDetail.raw_props).length}）</summary>
                                <pre className="text-[10px] bg-gray-50 p-2 rounded overflow-auto max-h-32">
                                    {JSON.stringify(nodeDetail.raw_props, null, 2)}
                                </pre>
                            </details>
                        )}
                    </Section>

                    <Section title={<span><Term t="溯源证据" />（{nodeDetail.provenance.length}）</span>}>
                        <EvidenceCards rows={nodeDetail.provenance} projectId={projectId} />
                    </Section>

                    <Section title={`关系（${nodeDetail.relations.length}）`}>
                        <Table
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
                            <Descriptions size="small" column={2} bordered>
                                <Descriptions.Item label="生效起">{nodeDetail.timeline.valid_from || '—'}</Descriptions.Item>
                                <Descriptions.Item label="生效止">{nodeDetail.timeline.valid_until || '—'}</Descriptions.Item>
                            </Descriptions>
                        </Section>
                    )}
                </div>
    );
};


export default DetailDrawer;
