/**
 * 本体健康记分卡 /projects/:projectId/scorecard（O2-F7，docs/design/09 §2/§3）。
 * 第一层机检指标可视化：一致性违例 / 环 / 悬空引用 / 命名一致率 / 证据闭合率 /
 * 实例覆盖率 / 孤立类 / 审核通过率趋势。指标全部实时聚合，每个指标带口径悬停提示。
 * 版式与全站一致（时间轴/审核工作台同款）：结论先行 Alert + 分区卡片 + 状态 Tag，
 * 品牌色 #5B8DEF（信息/图表），语义状态色只用于达标/关注/阻断判定。
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
    Alert, Button, Card, Empty, Popover, Spin, Tag, Tooltip, Typography, message,
} from 'antd';
import {
    ArrowLeftOutlined, CheckCircleOutlined, CloseCircleOutlined, QuestionCircleOutlined, ReloadOutlined,
} from '@ant-design/icons';

import { Scorecard, scorecardApi } from '../api/governance';

const { Title, Text } = Typography;

/** 百分比格式化；无效值返回「—」 */
const pct = (v: number | null | undefined): string =>
    (typeof v === 'number' && Number.isFinite(v)) ? `${Math.round(v * 100)}%` : '—';

/** 口径提示内容（悬停解释，验收要求：指标有口径提示） */
const METRIC_HELP: Record<string, React.ReactNode> = {
    consistency: (
        <>
            当前待处理的冲突类审核项数量（属性值冲突、类型冲突、关系冲突、公理违例，
            含 SHACL 逻辑检查产出的违例）。冲突未处理完会阻断发布。
        </>
    ),
    cycles: (
        <>类继承层次中出现的环（A 是 B 的子类、B 又是 A 的子类）。环会让推理陷入循环，属硬伤，阻断发布。</>
    ),
    dangling: (
        <>
            引用了不存在对象的次数：父类指向不存在的类、属性的 domain/range 指向不存在的类。
            悬空引用会让导出的本体在其它系统里无法解析。
        </>
    ),
    naming: (
        <>
            类与关系的命名规范程度：标签非空、无首尾空白、归一（忽略大小写）后不重复。
            命名不一致会造成引用歧义与合并困难。
        </>
    ),
    evidence: (
        <>
            框架对象（类/关系）带有效证据锚点的比例。锚点指向已不存在的文档切片（文档被删）为「悬空」，
            完全无锚点为「缺失」——这两类不计入闭合率；悬空会阻断发布。
        </>
    ),
    coverage: (
        <>有至少一个实例的类占全部类的比例。覆盖率为 0 的类只出现在框架里，抽取环节还没有识别出它的实例。</>
    ),
    orphan: (
        <>
            没有任何实例、也没有被任何关系或继承层级引用的类。孤立类通常是被遗忘的草稿，
            建议补充定义或从框架中移除。
        </>
    ),
    review: (
        <>近 6 个月每月「已裁决审核项中批准的比例」。通过率持续偏低说明抽取质量或数据源需要关注。</>
    ),
};

/** 指标口径提示：标签旁的问号图标（F10 人话文案风格） */
function MetricHelp({ id }: { id: string }) {
    return (
        <Popover content={<div style={{ maxWidth: 320 }}>{METRIC_HELP[id]}</div>}
                 title="指标口径" trigger="hover">
            <QuestionCircleOutlined className="!text-gray-400 hover:!text-[#5B8DEF] transition-colors"
                                    style={{ fontSize: 13, cursor: 'help' }} />
        </Popover>
    );
}

type Status = 'pass' | 'warn' | 'risk' | 'info';

/** 状态 → 颜色与文字（颜色只是冗余信号，文字保证可读） */
const STATUS_META: Record<Status, { color: string; label: string }> = {
    pass: { color: '#52c41a', label: '达标' },
    warn: { color: '#faad14', label: '关注' },
    risk: { color: '#ff4d4f', label: '阻断' },
    info: { color: '#5B8DEF', label: '待处理' },
};

/** 数量型指标：0=达标、少量=关注、多=阻断 */
function countStatus(v: number): Status {
    if (v <= 0) return 'pass';
    if (v <= 5) return 'warn';
    return 'risk';
}

/** 率型指标：≥0.9 达标、≥0.7 关注、其余阻断 */
function rateStatus(v: number): Status {
    if (typeof v !== 'number' || !Number.isFinite(v)) return 'warn';
    if (v >= 0.9) return 'pass';
    if (v >= 0.7) return 'warn';
    return 'risk';
}

interface MetricDef {
    id: string;
    label: string;
    value: string;
    status: Status;
    footnote?: string;
}

/** 单个指标块：标签 + 口径问号 + 数值（等宽数字）+ 状态 Tag + 脚注 */
function MetricTile({ m }: { m: MetricDef }) {
    const meta = STATUS_META[m.status];
    const neutral = m.status === 'info';
    return (
        <div className="min-w-0">
            <div className="flex items-center gap-1.5 mb-1.5">
                <span className="text-[13px] text-gray-500 truncate">{m.label}</span>
                <MetricHelp id={m.id} />
            </div>
            <div className="flex items-baseline gap-2 flex-wrap">
                <span style={{
                    fontSize: 28, fontWeight: 600, lineHeight: 1.1,
                    fontVariantNumeric: 'tabular-nums',
                    color: neutral ? '#5B8DEF' : meta.color,
                }}>{m.value}</span>
                <Tag style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '18px' }}
                     color={m.status === 'pass' ? 'success' : m.status === 'warn' ? 'warning'
                         : m.status === 'risk' ? 'error' : 'processing'}>
                    {meta.label}
                </Tag>
            </div>
            {m.footnote && <div className="text-xs text-gray-400 mt-1.5">{m.footnote}</div>}
        </div>
    );
}

const ScorecardPage: React.FC = () => {
    const { projectId } = useParams();
    const navigate = useNavigate();
    const [data, setData] = useState<Scorecard | null>(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async () => {
        if (!projectId) return;
        setLoading(true);
        try {
            setData(await scorecardApi.get(Number(projectId)));
        } catch (e: any) {
            message.error(e?.response?.data?.error?.message || '记分卡加载失败');
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    useEffect(() => { void load(); }, [load]);

    if (loading && !data) {
        return <div style={{ padding: 64, textAlign: 'center' }}><Spin size="large" /></div>;
    }
    if (!data) {
        return <Empty description="暂无记分卡数据" style={{ padding: 64 }} />;
    }

    const sc = data.schema_checks || { passed: true, error_count: 0, warning_count: 0 };
    const hasDetail = data.dangling_domain_range.length > 0 || data.evidence_dangling.length > 0
        || data.orphan_classes.length > 0 || data.naming_bad.length > 0;
    const trendEmpty = data.review_trend.every(t => t.approved + t.rejected === 0);
    const maxTrend = Math.max(1, ...data.review_trend.map(t => t.approved + t.rejected));

    const blockerMetrics: MetricDef[] = [
        { id: 'consistency', label: '一致性违例（待处理）', value: String(data.consistency_violations),
          status: countStatus(data.consistency_violations) },
        { id: 'cycles', label: '类层次环', value: String(data.cycles), status: countStatus(data.cycles) },
        { id: 'dangling', label: '悬空引用', value: String(data.dangling_refs),
          status: countStatus(data.dangling_refs) },
        { id: 'evidence', label: '证据闭合率', value: pct(data.evidence_closure_rate),
          status: rateStatus(data.evidence_closure_rate) },
    ];
    const qualityMetrics: MetricDef[] = [
        { id: 'naming', label: '命名一致率', value: pct(data.naming_consistency),
          status: rateStatus(data.naming_consistency) },
        { id: 'coverage', label: '实例覆盖率', value: pct(data.instance_coverage),
          status: rateStatus(data.instance_coverage),
          footnote: `${data.class_count} 类 / ${data.instance_count} 实例` },
        { id: 'orphan', label: '孤立类', value: String(data.orphan_count),
          status: countStatus(data.orphan_count) },
        { id: 'review', label: '待审项', value: String(data.pending_reviews), status: 'info' },
    ];

    return (
        <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(-1)}>返回</Button>
                <Title level={4} style={{ margin: 0 }}>本体健康记分卡</Title>
                <Tooltip title="重新统计全部指标">
                    <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading}
                            style={{ marginLeft: 'auto' }}>刷新</Button>
                </Tooltip>
            </div>
            <Text type="secondary" className="block mb-4">
                指标实时统计自系统机检（类层次检查 / SHACL 逻辑检查 / 证据闭合）与审核工作流；
                悬停每个指标旁的问号图标可查看计算口径。
            </Text>

            {/* 结论先行：类层次机检结果 */}
            <Alert style={{ marginBottom: 16 }}
                   type={sc.passed ? 'success' : 'error'}
                   showIcon
                   icon={sc.passed ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                   message={sc.passed
                       ? <>类层次机检通过，无阻断性硬伤{sc.warning_count > 0 ? `（另有 ${sc.warning_count} 条改进建议）` : ''}</>
                       : <>类层次机检存在 {sc.error_count} 处硬伤（环 / 悬空父类 / 自父类），修复前无法发布</>} />

            <Card size="small" title="发布阻断检查"
                  extra={<span className="text-xs text-gray-400">以下任一项未达标都会阻止发布</span>}>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-8 py-1">
                    {blockerMetrics.map(m => <MetricTile key={m.id} m={m} />)}
                </div>
            </Card>

            <Card size="small" title="质量指标"
                  extra={<span className="text-xs text-gray-400">反映本体质量与工作量，不直接阻断发布</span>}
                  style={{ marginTop: 16 }}>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-8 py-1">
                    {qualityMetrics.map(m => <MetricTile key={m.id} m={m} />)}
                </div>
            </Card>

            {/* 明细提示：悬空引用 / 证据悬空 / 孤立类 / 命名问题 */}
            {hasDetail && (
                <Card size="small" title="问题明细" style={{ marginTop: 16 }}>
                    <div className="space-y-3" style={{ maxHeight: 200, overflowY: 'auto' }}>
                        {data.evidence_dangling.length > 0 && (
                            <div>
                                <Text type="secondary" className="text-xs block mb-1.5">
                                    证据锚点悬空（锚点指向已删除的文档，会阻断发布）：
                                </Text>
                                {data.evidence_dangling.map(d => <Tag key={d} color="red">{d}</Tag>)}
                            </div>
                        )}
                        {data.dangling_domain_range.length > 0 && (
                            <div>
                                <Text type="secondary" className="text-xs block mb-1.5">
                                    悬空 domain/range（指向不存在的类）：
                                </Text>
                                {data.dangling_domain_range.map(d => <Tag key={d} color="orange">{d}</Tag>)}
                            </div>
                        )}
                        {data.naming_bad.length > 0 && (
                            <div>
                                <Text type="secondary" className="text-xs block mb-1.5">命名不规范：</Text>
                                {data.naming_bad.map(d => <Tag key={d} color="orange">{d}</Tag>)}
                            </div>
                        )}
                        {data.orphan_classes.length > 0 && (
                            <div>
                                <Text type="secondary" className="text-xs block mb-1.5">孤立类：</Text>
                                {data.orphan_classes.map(d => <Tag key={d}>{d}</Tag>)}
                            </div>
                        )}
                    </div>
                </Card>
            )}

            <Card size="small" style={{ marginTop: 16 }}
                  title={<span className="flex items-center gap-1.5">
                      审核通过率趋势（近 6 个月）<MetricHelp id="review" />
                  </span>}>
                {trendEmpty ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="近 6 个月还没有已裁决的审核项" />
                ) : (
                    <>
                        <div className="grid grid-cols-6 gap-3 items-end" style={{ height: 150 }}>
                            {data.review_trend.map(t => {
                                const total = t.approved + t.rejected;
                                const h = total === 0 ? 0 : Math.max(8, (total / maxTrend) * 100);
                                // 品牌蓝按通过率调透明度；无数据月份显示基线短横
                                const barColor = t.pass_rate === null ? '#D9DEE8'
                                    : `rgba(91, 141, 239, ${0.35 + 0.65 * (t.pass_rate ?? 0)})`;
                                return (
                                    <div key={t.month} className="flex flex-col items-center justify-end h-full">
                                        <Tooltip title={`${t.month}：批准 ${t.approved} / 驳回 ${t.rejected}${t.pass_rate !== null ? `，通过率 ${pct(t.pass_rate)}` : ''}`}>
                                            <div style={{
                                                width: 32, height: h,
                                                background: barColor,
                                                borderRadius: '4px 4px 0 0',
                                                transition: 'height .3s ease-out',
                                            }} />
                                        </Tooltip>
                                    </div>
                                );
                            })}
                        </div>
                        <div className="grid grid-cols-6 gap-3 border-t border-gray-100 pt-2 mt-0">
                            {data.review_trend.map(t => (
                                <div key={t.month} className="text-center">
                                    <div className="text-xs text-gray-400">{t.month.slice(5)}</div>
                                    <div className="text-xs mt-0.5"
                                         style={{ fontVariantNumeric: 'tabular-nums', color: '#5B8DEF' }}>
                                        {t.pass_rate === null ? '—' : pct(t.pass_rate)}
                                    </div>
                                </div>
                            ))}
                        </div>
                    </>
                )}
            </Card>
        </div>
    );
};

export default ScorecardPage;
