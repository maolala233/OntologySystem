/**
 * 推理分析 Modal（推理期 R1/R3/R4）：蕴含推理（owlrl）+ 规则引擎的运行与结果查看。
 * 结果区分原则：面板内展示的全部是「推理得出的」三元组（原始事实不进 results），
 * 每条带来源标签（OWL-RL 蕴含 / RDFS 蕴含 / 规则·名称），并支持含推理分节的 TTL 导出。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
    Button, Empty, Input, InputNumber, Modal, Popconfirm, Radio, Select, Spin, Switch, Tag, message, Pagination, Dropdown } from 'antd';
import {
    DeleteOutlined, DownloadOutlined, ExperimentOutlined, PlusOutlined, ReloadOutlined, SettingOutlined,
} from '@ant-design/icons';

import { reasoningApi, type DeclaredAxiom, type OntologyRule, type ReasoningItem, type ReasoningSummary, type RuleSuggestion } from '../../../api/reasoning';

const SOURCE_TAG: Record<string, { color: string; label: string }> = {
    owlrl: { color: '#5B8DEF', label: 'OWL-RL 蕴含' },
    rdfs: { color: '#36CFC9', label: 'RDFS 蕴含' },
    rule: { color: '#B585F2', label: '规则推理' },
};

const AXIOM_KIND_LABEL: Record<string, string> = {
    functional: '函数性',
    inverse_of: '互逆',
    disjoint_with: '互斥',
    subclass_of: '上下位',
    equivalent_classes: '等价类',
    cardinality: '基数',
};

/** 公理条目 → 人话描述 */
const axiomText = (a: DeclaredAxiom): string => {
    switch (a.kind) {
        case 'functional':
            return `「${a.predicate}」每个主体至多一个客体`;
        case 'inverse_of':
            return `「${a.predicates?.[0]}」与「${a.predicates?.[1]}」互为逆关系`;
        case 'disjoint_with':
            return `「${a.classes?.[0]}」与「${a.classes?.[1]}」无共同实例`;
        case 'subclass_of':
            return `「${a.classes?.[0]}」⊆「${a.classes?.[1]}」`;
        case 'equivalent_classes':
            return `「${a.classes?.[0]}」≡「${a.classes?.[1]}」`;
        case 'cardinality': {
            const parts = [a.min != null ? `≥${a.min}` : null, a.max != null ? `≤${a.max}` : null].filter(Boolean);
            return `「${a.predicate}」基数 ${parts.join(' 且 ')}（约束类：${a.class || '未指定'}）`;
        }
        default:
            return a.key;
    }
};

const PAGE_SIZE = 50;
/** 「蕴含」Tab = OWL-RL + RDFS 蕴含合并（旧批次/RDFS profile 的整批 rdfs 标注也在其中） */
const ENTAILMENT_SOURCES = 'owlrl,rdfs';

/** 公理表单状态：kind 决定显示哪些字段 */
interface AxiomForm {
    kind: string;
    predicate: string;
    p: string;   // inverse_of 第二谓词
    a: string;   // classes[0]（subclass_of=子类）
    b: string;   // classes[1]（subclass_of=父类）
    min_card: number | null;
    max_card: number | null;
}

const EMPTY_AXIOM_FORM: AxiomForm = { kind: 'functional', predicate: '', p: '', a: '', b: '', min_card: null, max_card: null };

const ReasoningModal: React.FC<{
    projectId: number;
    open: boolean;
    onClose: () => void;
}> = ({ projectId, open, onClose }) => {
    const [profile, setProfile] = useState<'owlrl' | 'rdfs'>('owlrl');
    const [running, setRunning] = useState(false);
    const [summary, setSummary] = useState<ReasoningSummary | null>(null);
    const [items, setItems] = useState<ReasoningItem[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [sourceFilter, setSourceFilter] = useState<string | undefined>(undefined);
    const [page, setPage] = useState(1);
    // 规则与公理管理
    const [rules, setRules] = useState<OntologyRule[]>([]);
    const [rulesOpen, setRulesOpen] = useState(false);
    const [ruleForm, setRuleForm] = useState<{ name: string; s: string; p: string; o: string; q: string } | null>(null);
    const [editingRule, setEditingRule] = useState<OntologyRule | null>(null);
    const [savingRule, setSavingRule] = useState(false);
    // AI 规则挖掘（S9）：从实例事实的物质化蕴含挖候选规则，人工采纳后下次运行生效
    const [mining, setMining] = useState(false);
    const [suggestions, setSuggestions] = useState<RuleSuggestion[] | null>(null);
    // 公理（O4 后扩展：抽取批准的公理与手工公理统一列出、可编辑可新增）
    const [axioms, setAxioms] = useState<DeclaredAxiom[]>([]);
    const [classLabels, setClassLabels] = useState<string[]>([]);
    const [predicateLabels, setPredicateLabels] = useState<string[]>([]);
    const [axiomForm, setAxiomForm] = useState<AxiomForm | null>(null);
    const [editingAxiom, setEditingAxiom] = useState<DeclaredAxiom | null>(null);
    const [savingAxiom, setSavingAxiom] = useState(false);

    const loadResults = useCallback(async (src?: string, page = 1) => {
        setLoading(true);
        try {
            // 「蕴含」Tab 前端语义 = owlrl+rdfs 合并（后端 source 支持逗号多值）
            const srcParam = src === 'owlrl' ? ENTAILMENT_SOURCES : src;
            const r = await reasoningApi.getResults(projectId, {
                limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, source: srcParam,
            });
            setItems(r.items);
            setTotal(r.total);
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '加载推理结果失败');
        } finally {
            setLoading(false);
        }
    }, [projectId]);

    const loadRules = useCallback(async () => {
        try {
            const r = await reasoningApi.listRules(projectId);
            setRules(r.items);
        } catch { /* 规则加载失败不阻断 */ }
    }, [projectId]);

    const loadAxioms = useCallback(async () => {
        try {
            const r = await reasoningApi.listAxioms(projectId);
            setAxioms(r.items);
            setClassLabels(r.class_labels);
            setPredicateLabels(r.predicate_labels);
        } catch { /* 公理加载失败不阻断 */ }
    }, [projectId]);

    useEffect(() => {
        if (open) {
            setPage(1);
            loadResults(sourceFilter, 1);
            loadRules();
        }
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    const runReasoning = async () => {
        setRunning(true);
        try {
            const s = await reasoningApi.run(projectId, { profile });
            setSummary(s);
            message.success(`推理完成：事实 ${s.fact_triples} 条，推理得出 ${s.inferred_total} 条`);
            // 新批次后回到「全部」：蕴含/规则产出比例随批次变化，停留在空 tab 会造成"没落盘"错觉
            setSourceFilter(undefined);
            setPage(1);
            await loadResults(undefined, 1);
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '推理运行失败');
        } finally {
            setRunning(false);
        }
    };

    const EXPORT_FMT_EXT: Record<string, string> = {
        turtle: 'ttl', ntriples: 'nt', rdfxml: 'rdf', jsonld: 'jsonld', trig: 'trig', owl: 'owl',
    };
    const exportInferred = async (fmt: string) => {
        try {
            const resp = await fetch(`/api/projects/${projectId}/export?format=${fmt}&include_inferred=true`, {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ontology_project_${projectId}_inferred.${EXPORT_FMT_EXT[fmt] || fmt}`;
            a.click();
            URL.revokeObjectURL(url);
        } catch (e: any) {
            message.error('导出失败，请重试');
        }
    };

    const saveRule = async () => {
        if (!ruleForm) return;
        if (!ruleForm.name.trim() || !ruleForm.q.trim()) {
            message.warning('规则名称与结论谓词不能为空');
            return;
        }
        setSavingRule(true);
        try {
            const body = {
                name: ruleForm.name.trim(),
                if_subject_class: ruleForm.s.trim(),
                if_predicate: ruleForm.p.trim(),
                if_object_class: ruleForm.o.trim(),
                then_predicate: ruleForm.q.trim(),
                enabled: true,
            };
            if (editingRule) {
                await reasoningApi.updateRule(projectId, editingRule.id, { ...body, enabled: editingRule.enabled });
                message.success('规则已更新');
            } else {
                await reasoningApi.createRule(projectId, body);
                message.success('规则已创建');
            }
            setRuleForm(null);
            setEditingRule(null);
            await loadRules();
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '保存规则失败');
        } finally {
            setSavingRule(false);
        }
    };

    const toggleRule = async (rule: OntologyRule, enabled: boolean) => {
        try {
            await reasoningApi.updateRule(projectId, rule.id, {
                name: rule.name, if_subject_class: rule.if_subject_class,
                if_predicate: rule.if_predicate, if_object_class: rule.if_object_class,
                then_predicate: rule.then_predicate, enabled,
            });
            await loadRules();
        } catch {
            message.error('更新失败');
        }
    };

    const removeRule = async (rule: OntologyRule) => {
        try {
            await reasoningApi.deleteRule(projectId, rule.id);
            message.success('已删除');
            await loadRules();
        } catch {
            message.error('删除失败');
        }
    };

    // ── AI 规则挖掘（S9）：统计+LLM 双通道候选 + 一键采纳（走既有 createRule 人工门禁）──
    const mineRules = async () => {
        setMining(true);
        try {
            const r = await reasoningApi.getRuleSuggestions(projectId, 'all');
            setSuggestions(r.items);
            const skipped = (r as { llm?: { skipped?: string } }).llm?.skipped;
            if (!r.items.length) {
                message.info(skipped
                    ? '未挖掘到候选规则：LLM 通道跳过（抽取模型未配置或调用失败），统计通道需「带 p 必带 q」的稳定伴随模式'
                    : '未挖掘到候选规则：需要实例关系间存在稳定伴随模式或 LLM 可提议的隐性关系');
            } else if (skipped) {
                message.info('LLM 通道跳过（抽取模型未配置或调用失败），仅统计通道产出');
            }
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '规则挖掘失败');
        } finally {
            setMining(false);
        }
    };

    const adoptSuggestion = async (s: RuleSuggestion) => {
        try {
            await reasoningApi.createRule(projectId, {
                name: s.name,
                if_subject_class: s.if_subject_class,
                if_predicate: s.if_predicate,
                if_object_class: s.if_object_class,
                then_predicate: s.then_predicate,
                enabled: true,
            });
            message.success(`已采纳规则「${s.name}」，点击「运行推理」生效`);
            setSuggestions((prev) => (prev ? prev.filter((x) => x !== s) : null));
            await loadRules();
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '采纳失败');
        }
    };

    const axiomBody = (f: AxiomForm) => ({
        kind: f.kind,
        predicate: f.predicate.trim() || undefined,
        predicates: f.kind === 'inverse_of' ? [f.predicate.trim(), f.p.trim()] : undefined,
        classes: ['disjoint_with', 'subclass_of', 'equivalent_classes'].includes(f.kind)
            ? [f.a.trim(), f.b.trim()] : undefined,
        min_card: f.kind === 'cardinality' ? (f.min_card ?? undefined) : undefined,
        max_card: f.kind === 'cardinality' ? (f.max_card ?? undefined) : undefined,
    });

    const saveAxiom = async () => {
        if (!axiomForm) return;
        const f = axiomForm;
        if ((f.kind === 'functional' || f.kind === 'cardinality') && !f.predicate.trim()) {
            message.warning('请选择谓词');
            return;
        }
        if (f.kind === 'inverse_of' && (!f.predicate.trim() || !f.p.trim())) {
            message.warning('请选择两个互逆谓词');
            return;
        }
        if (['disjoint_with', 'subclass_of', 'equivalent_classes'].includes(f.kind)
                && (!f.a.trim() || !f.b.trim())) {
            message.warning('请选择两个类');
            return;
        }
        if (f.kind === 'inverse_of' && f.predicate.trim() === f.p.trim()) {
            message.warning('互逆的两个谓词不能相同');
            return;
        }
        if (f.kind !== 'cardinality' && f.a.trim() === f.b.trim()
                && ['disjoint_with', 'subclass_of', 'equivalent_classes'].includes(f.kind)) {
            message.warning('两个类不能相同');
            return;
        }
        if (f.kind === 'cardinality' && f.min_card == null && f.max_card == null) {
            message.warning('请至少填写最小或最大基数');
            return;
        }
        setSavingAxiom(true);
        try {
            // 编辑 = 撤旧 + 声新（key 即锚点）
            if (editingAxiom) {
                await reasoningApi.removeAxiom(projectId, editingAxiom.key);
            }
            await reasoningApi.declareAxiom(projectId, axiomBody(f));
            message.success(editingAxiom ? '公理已更新' : '公理已声明，可运行推理生效');
            setAxiomForm(null);
            setEditingAxiom(null);
            await loadAxioms();
        } catch (e: any) {
            if (editingAxiom) await loadAxioms(); // 撤旧成功但声明失败时刷新，避免丢状态
            message.error(e?.response?.data?.detail || '保存公理失败');
        } finally {
            setSavingAxiom(false);
        }
    };

    const removeAxiom = async (a: DeclaredAxiom) => {
        try {
            await reasoningApi.removeAxiom(projectId, a.key);
            message.success('公理已撤销');
            await loadAxioms();
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '撤销失败');
        }
    };

    const editAxiom = (a: DeclaredAxiom) => {
        setEditingAxiom(a);
        setAxiomForm({
            kind: a.kind,
            predicate: a.predicate || a.predicates?.[0] || '',
            p: a.predicates?.[1] || '',
            a: a.classes?.[0] || '',
            b: a.classes?.[1] || '',
            min_card: a.min ?? null,
            max_card: a.max ?? null,
        });
    };

    return (
        <Modal
            title={<span><ExperimentOutlined style={{ color: '#B585F2', marginRight: 8 }} />推理分析（蕴含推理 + 规则引擎）</span>}
            open={open}
            onCancel={onClose}
            width={860}
            footer={null}
        >
            {/* 运行区 */}
            <div className="flex items-center gap-3 mb-3">
                <Radio.Group value={profile} onChange={(e) => setProfile(e.target.value)} size="small">
                    <Radio.Button value="owlrl">OWL-RL（推荐）</Radio.Button>
                    <Radio.Button value="rdfs">RDFS</Radio.Button>
                </Radio.Group>
                <Button type="primary" icon={<ExperimentOutlined />} loading={running}
                        onClick={runReasoning}>
                    运行推理
                </Button>
                <Dropdown menu={{
                    items: [
                        { key: 'turtle', label: 'Turtle（.ttl · 事实/推理分节标注）' },
                        { key: 'ntriples', label: 'N-Triples（.nt）' },
                        { key: 'rdfxml', label: 'RDF/XML（.rdf）' },
                        { key: 'jsonld', label: 'JSON-LD（.jsonld）' },
                        { key: 'trig', label: 'TriG（.trig）' },
                        { key: 'owl', label: 'OWL（.owl）' },
                    ],
                    onClick: ({ key }) => exportInferred(key),
                }}>
                    <Button icon={<DownloadOutlined />}>导出含推理 ▾</Button>
                </Dropdown>
                <Button icon={<SettingOutlined />} onClick={() => {
                    setRulesOpen(true); loadRules(); loadAxioms();
                }}>
                    规则与公理{rules.length + axioms.length > 0 ? `（${rules.length + axioms.length}）` : ''}
                </Button>
            </div>
            <div className="text-xs text-gray-500 mb-3">
                语义推理自动从公理推导新事实（子类传播、传递链、对称/互逆等）；自定义规则按「主语类-谓词→宾语类 ⇒ 新谓词」推导。
                推理结果<span className="font-medium text-purple-400">与原始事实分开存放、处处标注来源</span>，不会混入抽取数据。
            </div>

            {/* 摘要 */}
            {summary && (
                <div className="flex flex-wrap items-center gap-2 mb-3 p-3 rounded" style={{ background: 'rgba(91,141,239,0.08)' }}>
                    <span className="text-sm">事实 <b>{summary.fact_triples}</b> 条</span>
                    <span className="text-gray-500">→</span>
                    <span className="text-sm">推理得出 <b style={{ color: '#B585F2' }}>{summary.inferred_total}</b> 条</span>
                    <Tag color="blue">OWL-RL/RDFS 蕴含 {summary.inferred_entailment}</Tag>
                    <Tag color="purple">规则推理 {summary.inferred_rule}</Tag>
                    {summary.truncated && <Tag color="orange">超出 {summary.persisted} 条已截断展示</Tag>}
                </div>
            )}

            {/* 结果列表 */}
            <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">推理结果</span>
                <span className="text-xs text-gray-500">（共 {total} 条{total > 0 && ` · 第 ${(page - 1) * PAGE_SIZE + 1}-${Math.min(page * PAGE_SIZE, total)} 条`}）</span>
                <div className="flex-1" />
                <Radio.Group size="small" value={sourceFilter} onChange={(e) => { setSourceFilter(e.target.value); setPage(1); loadResults(e.target.value, 1); }}>
                    <Radio.Button value={undefined}>全部</Radio.Button>
                    <Radio.Button value="owlrl">蕴含</Radio.Button>
                    <Radio.Button value="rule">规则</Radio.Button>
                </Radio.Group>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => loadResults(sourceFilter)} />
            </div>
            {loading ? (
                <div className="py-10 text-center"><Spin /></div>
            ) : items.length === 0 ? (
                <Empty description={
                    sourceFilter === 'rule' && rules.length === 0
                        ? '暂无自定义规则：在「规则与公理」新建，或用 AI 规则挖掘从数据里挖候选'
                        : sourceFilter === 'rule'
                            ? '已启用的规则本次未推导出新关系，可点「运行推理」重新计算'
                            : '暂无推理结果，点击「运行推理」生成'
                } className="py-6" />
            ) : (
                <div className="max-h-[360px] overflow-y-auto border rounded" style={{ borderColor: '#2A3550' }}>
                    {items.map((it) => {
                        const tag = SOURCE_TAG[it.source] || { color: 'default', label: it.source };
                        return (
                            <div key={it.id} className="flex items-center gap-2 px-3 py-2 border-b text-sm"
                                 style={{ borderColor: '#1E2A44' }}>
                                <Tag color={tag.color} className="flex-shrink-0" style={{ fontSize: 11 }}>
                                    {it.source === 'rule' && it.rule_name ? `规则·${it.rule_name}` : tag.label}
                                </Tag>
                                <span className="font-medium">{it.subject_label || it.subject_uri}</span>
                                <span className="text-gray-500">—[{it.predicate_label}]→</span>
                                <span className="font-medium">{it.object_label || it.object_uri}</span>
                            </div>
                        );
                    })}
                </div>
            )}
            {total > PAGE_SIZE && (
                <div className="flex justify-center mt-2">
                    <Pagination
                        size="small"
                        current={page}
                        pageSize={PAGE_SIZE}
                        total={total}
                        onChange={(p) => { setPage(p); loadResults(sourceFilter, p); }}
                        showSizeChanger={false}
                    />
                </div>
            )}

            {/* ── 规则与公理管理抽屉（O4 后扩展：抽取批准的公理统一在此编辑）── */}
            <Modal
                title="规则与公理管理"
                open={rulesOpen}
                onCancel={() => { setRulesOpen(false); setRuleForm(null); setEditingRule(null); setAxiomForm(null); setEditingAxiom(null); }}
                width={680}
                footer={null}
            >
                {/* 公理区 */}
                <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium" style={{ color: '#B585F2' }}>本体公理</span>
                    <span className="text-xs text-gray-500">（AI 公理扫描批准的与手工声明的都在这里；编辑后点「运行推理」重新生效）</span>
                    <div className="flex-1" />
                    {!axiomForm && (
                        <Button size="small" icon={<PlusOutlined />}
                                onClick={() => { setEditingAxiom(null); setAxiomForm({ ...EMPTY_AXIOM_FORM }); }}>
                            新建公理
                        </Button>
                    )}
                </div>
                {axiomForm && (
                    <div className="p-3 mb-3 border rounded space-y-2" style={{ borderColor: '#2A3550' }}>
                        <div className="flex items-center gap-2 text-sm">
                            <span>公理类型</span>
                            <Select size="small" style={{ width: 140 }} value={axiomForm.kind}
                                    onChange={(v) => setAxiomForm({ ...EMPTY_AXIOM_FORM, kind: v })}
                                    options={Object.entries(AXIOM_KIND_LABEL).map(([k, label]) => ({ value: k, label }))} />
                            {editingAxiom && <span className="text-xs text-gray-500">（编辑 {editingAxiom.key}）</span>}
                        </div>
                        {(axiomForm.kind === 'functional' || axiomForm.kind === 'cardinality') && (
                            <div className="flex items-center gap-2 text-sm">
                                <span>谓词</span>
                                <Select size="small" showSearch style={{ width: 220 }} placeholder="选择关系谓词"
                                        value={axiomForm.predicate || undefined}
                                        onChange={(v) => setAxiomForm({ ...axiomForm, predicate: v })}
                                        options={predicateLabels.map((p) => ({ value: p, label: p }))} />
                            </div>
                        )}
                        {axiomForm.kind === 'inverse_of' && (
                            <div className="flex items-center gap-2 text-sm">
                                <Select size="small" showSearch style={{ width: 180 }} placeholder="谓词 P"
                                        value={axiomForm.predicate || undefined}
                                        onChange={(v) => setAxiomForm({ ...axiomForm, predicate: v })}
                                        options={predicateLabels.map((p) => ({ value: p, label: p }))} />
                                <span>⇄</span>
                                <Select size="small" showSearch style={{ width: 180 }} placeholder="谓词 Q（逆）"
                                        value={axiomForm.p || undefined}
                                        onChange={(v) => setAxiomForm({ ...axiomForm, p: v })}
                                        options={predicateLabels.map((p) => ({ value: p, label: p }))} />
                            </div>
                        )}
                        {['disjoint_with', 'subclass_of', 'equivalent_classes'].includes(axiomForm.kind) && (
                            <div className="flex items-center gap-2 text-sm">
                                <span>{axiomForm.kind === 'subclass_of' ? '子类' : '类 A'}</span>
                                <Select size="small" showSearch style={{ width: 180 }}
                                        placeholder={axiomForm.kind === 'subclass_of' ? '选择子类' : '选择类'}
                                        value={axiomForm.a || undefined}
                                        onChange={(v) => setAxiomForm({ ...axiomForm, a: v })}
                                        options={classLabels.map((c) => ({ value: c, label: c }))} />
                                <span>{axiomForm.kind === 'subclass_of' ? '⊆' : axiomForm.kind === 'disjoint_with' ? '⨯' : '≡'}</span>
                                <span>{axiomForm.kind === 'subclass_of' ? '父类' : '类 B'}</span>
                                <Select size="small" showSearch style={{ width: 180 }}
                                        placeholder={axiomForm.kind === 'subclass_of' ? '选择父类' : '选择类'}
                                        value={axiomForm.b || undefined}
                                        onChange={(v) => setAxiomForm({ ...axiomForm, b: v })}
                                        options={classLabels.map((c) => ({ value: c, label: c }))} />
                            </div>
                        )}
                        {axiomForm.kind === 'cardinality' && (
                            <div className="flex items-center gap-2 text-sm">
                                <span>基数</span>
                                <InputNumber size="small" min={0} placeholder="最小" style={{ width: 90 }}
                                             value={axiomForm.min_card}
                                             onChange={(v) => setAxiomForm({ ...axiomForm, min_card: v })} />
                                <span>~</span>
                                <InputNumber size="small" min={0} placeholder="最大" style={{ width: 90 }}
                                             value={axiomForm.max_card}
                                             onChange={(v) => setAxiomForm({ ...axiomForm, max_card: v })} />
                            </div>
                        )}
                        <div className="flex gap-2">
                            <Button size="small" type="primary" loading={savingAxiom} onClick={saveAxiom} className="bg-blue-600">保存</Button>
                            <Button size="small" onClick={() => { setAxiomForm(null); setEditingAxiom(null); }}>取消</Button>
                        </div>
                    </div>
                )}
                {axioms.length === 0 ? (
                    <Empty description="暂无已声明公理（可在审核工作台批准 AI 公理建议，或点「新建公理」手工声明）" className="py-3" />
                ) : (
                    <div className="max-h-[220px] overflow-y-auto mb-3">
                        {axioms.map((a) => (
                            <div key={a.key} className="flex items-center gap-2 py-1.5 border-b text-sm" style={{ borderColor: '#1E2A44' }}>
                                <Tag color="purple" className="flex-shrink-0" style={{ fontSize: 11 }}>
                                    {AXIOM_KIND_LABEL[a.kind] || a.kind}
                                </Tag>
                                <span className="flex-1 text-xs">{axiomText(a)}</span>
                                <Button size="small" type="text" icon={<PlusOutlined rotate={90} />} onClick={() => editAxiom(a)} />
                                <Popconfirm title="撤销该公理？" onConfirm={() => removeAxiom(a)}>
                                    <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                                </Popconfirm>
                            </div>
                        ))}
                    </div>
                )}

                {/* 规则区 */}
                <div className="flex items-center gap-2 mb-2">
                    <span className="text-sm font-medium" style={{ color: '#5B8DEF' }}>自定义推理规则</span>
                    <div className="flex-1" />
                    <Button size="small" loading={mining} icon={<ExperimentOutlined />}
                            onClick={mineRules}>
                        AI 规则挖掘
                    </Button>
                    {!ruleForm && (
                        <Button size="small" icon={<PlusOutlined />}
                                onClick={() => { setEditingRule(null); setRuleForm({ name: '', s: '', p: '', o: '', q: '' }); }}>
                            新建规则
                        </Button>
                    )}
                </div>
                <div className="mb-3 text-xs text-gray-500">
                    规则：当主体属于「主语类」（含子类）、经「谓词」连接到「宾语类」（含子类）的实例时，
                    自动推导出两者之间的「结论谓词」关系。规则同时作用于原始事实与蕴含结果（链式推理：
                    蕴含先推出的关系可以继续触发规则）。推导结果只进推理结果，不改原始事实。
                </div>
                {suggestions !== null && (
                    suggestions.length === 0 ? (
                        <div className="mb-3 text-xs text-gray-500">
                            未挖掘到候选规则：需要实例关系间存在「带 p 必带 q」的稳定伴随模式（支撑 ≥2 例且置信度 100%）。
                        </div>
                    ) : (
                        <div className="mb-3 border rounded p-2 space-y-2" style={{ borderColor: '#2A3550' }}>
                            <div className="text-xs" style={{ color: '#B585F2' }}>
                                AI 挖掘到的候选规则（统计通道挖实例事实的稳定伴随模式，LLM 通道从本体素材抽隐性关系；采纳后点「运行推理」生效）
                            </div>
                            {suggestions.map((s, i) => {
                                const src = s.source === 'both' ? { color: 'geekblue', label: '双通道' }
                                    : s.source === 'llm' ? { color: 'purple', label: 'LLM 抽取' }
                                    : { color: 'cyan', label: '统计挖掘' };
                                return (
                                    <div key={`${s.if_predicate}-${s.then_predicate}-${i}`}
                                         className="flex items-start gap-2 text-xs">
                                        <Tag color={src.color} className="flex-shrink-0" style={{ fontSize: 11 }}>{src.label}</Tag>
                                        <div className="flex-1">
                                            <div>
                                                {s.if_subject_class} —[{s.if_predicate}]→ {s.if_object_class}
                                                <span className="mx-1">⇒</span>
                                                <span style={{ color: '#B585F2' }}>{s.then_predicate}</span>
                                                {s.support != null && (
                                                    <span className="text-gray-500">
                                                        （支撑 {s.support} 例 · 置信度 {Math.round((s.confidence || 0) * 100)}%）
                                                    </span>
                                                )}
                                            </div>
                                            {s.llm_reason && (
                                                <div className="text-gray-500 mt-0.5">{s.llm_reason}</div>
                                            )}
                                        </div>
                                        <Button size="small" type="primary" onClick={() => adoptSuggestion(s)}>采纳</Button>
                                    </div>
                                );
                            })}
                        </div>
                    )
                )}
                {ruleForm && (
                    <div className="p-3 mb-3 border rounded space-y-2" style={{ borderColor: '#2A3550' }}>
                        <Input size="small" placeholder="规则名称（如：投资即投资了）"
                               value={ruleForm.name} onChange={(e) => setRuleForm({ ...ruleForm, name: e.target.value })} />
                        <div className="flex items-center gap-2 text-sm">
                            <Input size="small" style={{ width: 120 }} placeholder="主语类"
                                   value={ruleForm.s} onChange={(e) => setRuleForm({ ...ruleForm, s: e.target.value })} />
                            <span>—[</span>
                            <Input size="small" style={{ width: 100 }} placeholder="谓词"
                                   value={ruleForm.p} onChange={(e) => setRuleForm({ ...ruleForm, p: e.target.value })} />
                            <span>]→</span>
                            <Input size="small" style={{ width: 120 }} placeholder="宾语类"
                                   value={ruleForm.o} onChange={(e) => setRuleForm({ ...ruleForm, o: e.target.value })} />
                        </div>
                        <div className="flex items-center gap-2 text-sm">
                            <span>⇒ 自动推断</span>
                            <Input size="small" style={{ width: 140 }} placeholder="结论谓词"
                                   value={ruleForm.q} onChange={(e) => setRuleForm({ ...ruleForm, q: e.target.value })} />
                            <span>关系</span>
                        </div>
                        <div className="flex gap-2">
                            <Button size="small" type="primary" loading={savingRule} onClick={saveRule} className="bg-blue-600">保存</Button>
                            <Button size="small" onClick={() => { setRuleForm(null); setEditingRule(null); }}>取消</Button>
                        </div>
                    </div>
                )}
                {rules.length === 0 ? (
                    <Empty description="暂无规则" className="py-4" />
                ) : (
                    rules.map((r) => (
                        <div key={r.id} className="flex items-center gap-2 py-2 border-b text-sm" style={{ borderColor: '#1E2A44' }}>
                            <Switch size="small" checked={r.enabled} onChange={(v) => toggleRule(r, v)} />
                            <div className="flex-1">
                                <div className="font-medium">{r.name}</div>
                                <div className="text-xs text-gray-500">
                                    {r.if_subject_class} —[{r.if_predicate}]→ {r.if_object_class}
                                    <span className="mx-1">⇒</span>
                                    <span style={{ color: '#B585F2' }}>{r.then_predicate}</span>
                                </div>
                            </div>
                            <Button size="small" type="text" icon={<PlusOutlined rotate={90} />} onClick={() => {
                                setEditingRule(r);
                                setRuleForm({ name: r.name, s: r.if_subject_class, p: r.if_predicate, o: r.if_object_class, q: r.then_predicate });
                            }} />
                            <Popconfirm title="确认删除该规则？" onConfirm={() => removeRule(r)}>
                                <Button size="small" type="text" danger icon={<DeleteOutlined />} />
                            </Popconfirm>
                        </div>
                    ))
                )}
            </Modal>
        </Modal>
    );
};

export default ReasoningModal;
