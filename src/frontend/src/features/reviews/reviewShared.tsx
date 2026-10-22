/**
 * 审核展示共享件（从 ReviewsPage 抽出，S12）：人话摘要 + 类型/优先级映射 + 详情人话区。
 * 审核工作台（跨项目）与构建器「审核」Tab（项目内）共用，保证两处详情能力一致。
 */
import React, { useState } from 'react';
import { Radio, Tag, Typography } from 'antd';

import { ReviewItem } from '../../api/governance';

const { Text, Paragraph } = Typography;

export const PRIORITY_TAG: Record<string, { color: string; label: string }> = {
    critical: { color: 'red', label: 'critical' },
    high: { color: 'orange', label: 'high' },
    medium: { color: 'blue', label: 'medium' },
    low: { color: 'default', label: 'low' },
};

export const TYPE_LABEL: Record<string, string> = {
    entity_merge: '实体合并',
    new_class: '新类型',
    low_confidence_entity: '低置信实体',
    low_confidence_relation: '低置信关系',
    conflict_value: '属性值冲突',
    conflict_type: '类型冲突',
    conflict_relationship: '关系冲突',
    missing_evidence: '缺证据',
    conflict_axiom: '公理违例',
    axiom_suggestion: '公理建议',
};

/** 公理建议（O4-F11）：kind 中文名 / 来源徽标（统计推断、AI 生成、双通道佐证） */
export const AXIOM_KIND_LABEL: Record<string, string> = {
    functional: '函数性',
    inverse_of: '互逆关系',
    disjoint_with: '互斥类',
    domain_fix: '定义域修正',
    range_fix: '值域修正',
    subclass_of: '上下位（子类）',
    equivalent_classes: '等价类',
    cardinality: '基数约束',
};

export const AXIOM_SOURCE_TAG: Record<string, { text: string; color: string }> = {
    statistical: { text: '统计推断', color: 'cyan' },
    llm: { text: 'AI 生成', color: 'geekblue' },
    both: { text: '统计+AI 双佐证', color: 'purple' },
};

/** 0.793 → "79%"；无效值返回 null */
export const pct = (v: any): string | null => {
    const n = Number(v);
    if (!Number.isFinite(n) || n <= 0) return null;
    return `${Math.round(n * 100)}%`;
};

/**
 * 通俗摘要（摘要列用）：面向不熟悉图谱术语的用户，一句话说清"发现了什么问题、需要你做什么"。
 */
export function humanSummary(item: ReviewItem): string {
    const p = item.payload || {};
    switch (item.item_type) {
        case 'entity_merge': {
            const c = p.cluster || {};
            const labels: string[] = c.member_labels || [];
            const sim = pct(c.similarity);
            if (labels.length > 1) {
                return `以下称呼疑似指同一个东西，建议合并为一个：「${labels.join('”、“')}」${sim ? `（相似度 ${sim}）` : ''}`;
            }
            if (p.merged_ids || p.canonical_label) {
                return `已按规则自动合并到「${p.canonical_label || p.label || ''}」，此记录留痕，确认无误即可`;
            }
            return '发现疑似重复的实体，建议合并';
        }
        case 'low_confidence_entity':
            return `从文档中识别出实体「${p.label || ''}」，但把握不大${pct(p.confidence) ? `（可信度 ${pct(p.confidence)}）` : ''}，请判断该实体是否真实存在`;
        case 'low_confidence_relation':
            return `识别出关系「${p.subject_label || '?'} → ${p.predicate || '?'} → ${p.object_label || '?'}」，但把握不大${pct(p.confidence) ? `（可信度 ${pct(p.confidence)}）` : ''}，请判断该关系是否成立`;
        case 'missing_evidence':
            return `实体「${p.label || ''}」没能找到原文依据，只保留了所在段落的摘要，请人工核实`;
        case 'conflict_type': {
            const sources = Array.isArray(p.sources) ? p.sources : [];
            const classes = [...new Set(sources.map((s: any) => s.class).filter(Boolean))];
            const label = sources[0]?.label || '';
            return classes.length > 0
                ? `「${label}」在不同文档中被归入了不同类别（${classes.join('、')}），请判断哪个正确`
                : '同一实体在不同文档中被归入不同类别，请判断';
        }
        case 'conflict_value': {
            const vals = Array.isArray(p.values) ? p.values : [];
            return `「${p.label || p.entity_label || ''}」的属性「${p.property || '?'}」出现了多个不同取值（${vals.map((v: any) => String(v.value ?? v)).join('、') || '见详情'}），请判断以哪个为准`;
        }
        case 'conflict_relationship':
            return `「${p.subject_label || '?'}」与「${p.object_label || '?'}」之间的关系在不同文档中描述不一致，请判断`;
        case 'conflict_axiom': {
            const prop = p.property_name || '';
            const vals = Array.isArray(p.conflicting_values) ? p.conflicting_values : [];
            if (prop === 'owl:disjointWith') {
                const cls = p.sources?.[0]?.class || '';
                return `实体「${p.sources?.[0]?.label || ''}」被归入的类别「${cls}」同时落在互斥类别（${vals.join('、')}）的层级内，请判断真实归属`;
            }
            return `关系「${prop}」的取值与本体声明的公理（函数性/基数）不符（${vals.join('、')}），请核实`;
        }
        case 'new_class':
            return `从文档中发现了新类别「${p.label || ''}」，它不在当前本体骨架中，请决定是否加入`;
        case 'axiom_suggestion': {
            const kindLbl = AXIOM_KIND_LABEL[p.kind] || p.kind || '公理';
            const targets: string[] = p.kind === 'domain_fix' || p.kind === 'range_fix'
                ? [p.predicate, p.value].filter(Boolean)
                : (p.classes || p.predicates || [p.predicate].filter(Boolean) || []);
            const src = AXIOM_SOURCE_TAG[p.source]?.text || '';
            return `系统建议为本体添加「${kindLbl}」公理（${targets.join('、') || '见详情'}）${src ? `，依据：${src}` : ''}，请判断是否符合业务实际`;
        }
        default:
            return item.reason || JSON.stringify(p).slice(0, 60);
    }
}

/**
 * 详情抽屉的"人话"展示区：按类型渲染结构化说明 + 关键事实；原始 JSON 折叠给高级用户。
 * conflict_type 会内嵌类别单选（chosenClass/onChooseClass 由外层持有，随裁决提交）。
 */
export const FriendlyPayload: React.FC<{
    item: ReviewItem;
    chosenClass?: string | null;
    onChooseClass?: (cls: string) => void;
}> = ({ item, chosenClass, onChooseClass }) => {
    const p = item.payload || {};
    const [showRaw, setShowRaw] = useState(false);

    const Quote = ({ text, title }: { text?: string | null; title: string }) => (
        !text ? null : (
            <div>
                <Text type="secondary" className="text-xs">{title}</Text>
                <Paragraph className="!mb-0 mt-1 p-2 bg-amber-50 border border-amber-200/70 rounded text-gray-800"
                           style={{ fontSize: 13 }}>{String(text).replace(/^\[chunk \d+ 摘要\]\s*/, '')}</Paragraph>
            </div>
        )
    );

    switch (item.item_type) {
        case 'entity_merge': {
            const c = p.cluster || {};
            const labels: string[] = c.member_labels || [];
            const sim = pct(c.similarity);
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded">
                        <Text strong>系统认为这些称呼指的是同一个东西：</Text>
                        <div className="mt-2 flex flex-wrap gap-2">
                            {labels.map((l, i) => (
                                <Tag key={i} color={l === c.canonical_label ? 'purple' : 'default'}>{l}</Tag>
                            ))}
                        </div>
                        {sim && <div className="mt-2 text-sm">相似度：<Text strong>{sim}</Text>（越高越可能是同一个）</div>}
                        {c.canonical_label && <div className="mt-1 text-sm">建议保留：<Text strong>「{c.canonical_label}」</Text>作为统一名称</div>}
                    </div>
                    <div className="text-sm text-gray-600">
                        点击下方「通过」将把它们合并为一个实体；「驳回」则保留各自独立。合并后可随时撤销。
                    </div>
                    <Quote text={item.reason} title="系统说明" />
                </div>
            );
        }
        case 'low_confidence_entity':
        case 'low_confidence_relation':
        case 'missing_evidence': {
            const ev = p.evidence || p.downgraded_evidence;
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        {item.item_type === 'low_confidence_entity' && (
                            <>识别出实体 <Text strong>「{p.label}」</Text>（类别：{p.class_label || '未知'}），
                                可信度仅 <Text strong type="danger">{pct(p.confidence) || '未知'}</Text>（一般 ≥70% 才会自动收录）。</>
                        )}
                        {item.item_type === 'low_confidence_relation' && (
                            <>识别出关系：<Text strong>「{p.subject_label}」 → 「{p.predicate}」 → 「{p.object_label}」</Text>，
                                可信度仅 <Text strong type="danger">{pct(p.confidence) || '未知'}</Text>。</>
                        )}
                        {item.item_type === 'missing_evidence' && (
                            <>实体 <Text strong>「{p.label}」</Text>（类别：{p.class_label || '未知'}）在原文中找不到对应原句，
                                下方仅是它所在段落的摘要，可能不准确。</>
                        )}
                    </div>
                    <Quote text={ev} title="原文依据（来自文档切片）" />
                </div>
            );
        }
        case 'conflict_type': {
            const sources = Array.isArray(p.sources) ? p.sources : [];
            const steps = p.guide?.steps as string[] | undefined;
            const classOptions = [...new Set(sources.map((s: any) => String(s.class || '')).filter(Boolean))];
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        同一实体被归入了不同类别。请点选<b>正确的那一个</b>，点「通过」后实体将归入该类别：
                    </div>
                    <Radio.Group
                        value={chosenClass ?? null}
                        onChange={(e) => onChooseClass?.(e.target.value)}
                        className="flex flex-col gap-2"
                    >
                        {classOptions.map((cls) => (
                            <Radio key={cls} value={cls}>
                                <Tag color={cls === chosenClass ? 'purple' : 'default'}>{cls}</Tag>
                                {chosenClass === cls && <Text type="secondary" className="text-xs">（已选：通过后实体将归入此类）</Text>}
                            </Radio>
                        ))}
                    </Radio.Group>
                    {steps && steps.length > 0 && (
                        <div>
                            <Text type="secondary" className="text-xs">排查建议</Text>
                            <ol className="list-decimal pl-5 text-sm text-gray-700 space-y-1">
                                {steps.map((s, i) => <li key={i}>{s}</li>)}
                            </ol>
                        </div>
                    )}
                    <div className="text-sm text-gray-600">
                        选择后点「通过」即生效；若两个类别都不对，可「驳回」并在骨架编辑中手工调整。
                    </div>
                </div>
            );
        }
        case 'conflict_value': {
            const vals = Array.isArray(p.values) ? p.values : [];
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        「{p.label || p.entity_label}」的属性「{p.property}」在不同文档中取值不一致：
                    </div>
                    <div className="space-y-2">
                        {vals.map((v: any, i: number) => (
                            <div key={i} className="text-sm">
                                <Tag color="orange">{String(v.value ?? v)}</Tag>
                                {v.source && <Text type="secondary" className="text-xs">来源：{String(v.source)}</Text>}
                            </div>
                        ))}
                        {vals.length === 0 && <div className="text-sm text-gray-500">详见下方原始数据</div>}
                    </div>
                </div>
            );
        }
        case 'conflict_axiom': {
            const prop = p.property_name || '';
            const vals: any[] = Array.isArray(p.conflicting_values) ? p.conflicting_values : [];
            const steps = p.guide?.steps as string[] | undefined;
            const isDisjoint = prop === 'owl:disjointWith';
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-purple-50 border border-purple-100 rounded text-sm">
                        {isDisjoint ? (
                            <>
                                本体骨架中声明了互斥类别 <Text strong>「{vals.join('」「')}」</Text>，
                                但实体 <Text strong>「{p.sources?.[0]?.label || ''}」</Text>
                                （当前类别：{p.sources?.[0]?.class || '未知'}）同时落在了它们的层级内。
                            </>
                        ) : (
                            <>
                                关系 <Text strong>「{prop}」</Text> 在本体中声明了公理约束（函数性/基数），
                                但实例数据与之不符（{vals.length} 个客体：{vals.join('、')}）。
                            </>
                        )}
                    </div>
                    {vals.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                            {vals.map((v, i) => <Tag key={i} color="purple">{String(v)}</Tag>)}
                        </div>
                    )}
                    {steps && steps.length > 0 && (
                        <div>
                            <Text type="secondary" className="text-xs">排查建议</Text>
                            <ol className="list-decimal pl-5 text-sm text-gray-700 space-y-1">
                                {steps.map((s, i) => <li key={i}>{s}</li>)}
                            </ol>
                        </div>
                    )}
                    <div className="text-sm text-gray-600">
                        核实后在骨架编辑中修正实体类别或关系；若公理本身声明有误，可在节点/边详情面板调整后「通过」关闭本项。
                    </div>
                </div>
            );
        }
        case 'new_class':
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded text-sm">
                        文档中出现了新类别 <Text strong>「{p.label}」</Text>，当前本体骨架中没有它。
                        通过后会加入骨架；驳回则忽略。
                    </div>
                    {p.definition && <Quote text={p.definition} title="定义" />}
                </div>
            );
        case 'axiom_suggestion': {
            const kindLbl = AXIOM_KIND_LABEL[p.kind] || p.kind || '';
            const src = AXIOM_SOURCE_TAG[p.source] || { text: String(p.source || ''), color: 'default' };
            const targets: string[] = p.kind === 'domain_fix' || p.kind === 'range_fix'
                ? [p.predicate, p.value].filter(Boolean)
                : (p.classes || p.predicates || [p.predicate].filter(Boolean) || []);
            const ev = p.evidence || {};
            // 统计证据标量字段（examples / llm_* 另行渲染）
            const scalars = Object.entries(ev).filter(([k, v]) =>
                !['llm_reason', 'llm_examples', 'examples'].includes(k)
                && (typeof v !== 'object' || v === null));
            const examples: any[] = [...(ev.examples || []), ...(ev.llm_examples || [])];
            return (
                <div className="space-y-3">
                    <div className="p-3 bg-blue-50 border border-blue-100 rounded">
                        {/* 三元素①②③：kind 徽标 + 高风险黄标 + 来源徽标 */}
                        <div className="flex items-center gap-2 flex-wrap">
                            {kindLbl && <Tag color="blue">{kindLbl}</Tag>}
                            {p.risk_tier === 'high' && (
                                <Tag color="gold">高风险：批准前已通过一致性机检</Tag>
                            )}
                            {src.text && <Tag color={src.color}>{src.text}</Tag>}
                        </div>
                        {targets.length > 0 && (
                            <div className="mt-2 text-sm">
                                涉及目标：
                                {targets.map((t, i) => <Tag key={i} color="purple">{String(t)}</Tag>)}
                            </div>
                        )}
                        {ev.llm_reason && (
                            <div className="mt-2 text-sm">
                                <Text type="secondary">AI 依据：</Text>{ev.llm_reason}
                            </div>
                        )}
                        {scalars.length > 0 && (
                            <div className="mt-1 text-sm text-gray-600">
                                统计证据：{scalars.map(([k, v]) => `${k}=${String(v)}`).join('；')}
                            </div>
                        )}
                        {examples.length > 0 && (
                            <div className="mt-1 text-xs text-gray-500">
                                样本：{examples.map((x, i) => {
                                    if (typeof x === 'string') return x;
                                    if (x && typeof x === 'object') {
                                        // 常见形如 {entity, note} → "实体 — 说明"，其余键值对平铺
                                        if (x.entity || x.note) {
                                            return [x.entity, x.note].filter(Boolean).join(' — ');
                                        }
                                        return Object.entries(x).map(([k, v]) => `${k}=${String(v)}`).join('，');
                                    }
                                    return '';
                                }).filter(Boolean).slice(0, 5).join('；')}
                            </div>
                        )}
                    </div>
                    <div className="text-sm text-gray-600">
                        点击「通过」将把该公理写入本体骨架（函数性/互逆/互斥/等价类等会在后续冲突检测与发布门禁中自动校验）；
                        驳回则仅关闭本条建议。高风险公理（上下位/等价类/基数）已经过影子校验与一致性检查。
                    </div>
                    <Quote text={item.reason} title="系统说明" />
                </div>
            );
        }
        default:
            return null;
    }
};

