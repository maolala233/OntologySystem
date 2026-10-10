/**
 * 推理分析 Modal（推理期 R1/R3/R4）：蕴含推理（owlrl）+ 规则引擎的运行与结果查看。
 * 结果区分原则：面板内展示的全部是「推理得出的」三元组（原始事实不进 results），
 * 每条带来源标签（OWL-RL 蕴含 / RDFS 蕴含 / 规则·名称），并支持含推理分节的 TTL 导出。
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
    Button, Empty, Modal, Radio, Spin, Switch, Tag, message, Input, Popconfirm,
} from 'antd';
import {
    DeleteOutlined, DownloadOutlined, ExperimentOutlined, PlusOutlined, ReloadOutlined, SettingOutlined,
} from '@ant-design/icons';

import { reasoningApi, type OntologyRule, type ReasoningItem, type ReasoningSummary } from '../../../api/reasoning';

const SOURCE_TAG: Record<string, { color: string; label: string }> = {
    owlrl: { color: '#5B8DEF', label: 'OWL-RL 蕴含' },
    rdfs: { color: '#36CFC9', label: 'RDFS 蕴含' },
    rule: { color: '#B585F2', label: '规则推理' },
};

const LIMIT = 300;

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
    // 规则管理
    const [rules, setRules] = useState<OntologyRule[]>([]);
    const [rulesOpen, setRulesOpen] = useState(false);
    const [ruleForm, setRuleForm] = useState<{ name: string; s: string; p: string; o: string; q: string } | null>(null);
    const [editingRule, setEditingRule] = useState<OntologyRule | null>(null);
    const [savingRule, setSavingRule] = useState(false);

    const loadResults = useCallback(async (src?: string) => {
        setLoading(true);
        try {
            const r = await reasoningApi.getResults(projectId, { limit: LIMIT, source: src });
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

    useEffect(() => {
        if (open) {
            loadResults(sourceFilter);
            loadRules();
        }
    }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

    const runReasoning = async () => {
        setRunning(true);
        try {
            const s = await reasoningApi.run(projectId, { profile });
            setSummary(s);
            message.success(`推理完成：事实 ${s.fact_triples} 条，推理得出 ${s.inferred_total} 条`);
            await loadResults(sourceFilter);
        } catch (e: any) {
            message.error(e?.response?.data?.detail || '推理运行失败');
        } finally {
            setRunning(false);
        }
    };

    const exportInferred = async () => {
        try {
            const resp = await fetch(reasoningApi.exportInferredUrl(projectId), {
                headers: { Authorization: `Bearer ${localStorage.getItem('access_token') || ''}` },
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ontology_project_${projectId}_inferred.ttl`;
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
                        onClick={runReasoning} className="bg-blue-600">
                    运行推理
                </Button>
                <Button icon={<DownloadOutlined />} onClick={exportInferred}>导出含推理 TTL</Button>
                <Button icon={<SettingOutlined />} onClick={() => { setRulesOpen(true); loadRules(); }}>
                    管理规则{rules.length > 0 ? `（${rules.length}）` : ''}
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
                    <span className="text-gray-400">→</span>
                    <span className="text-sm">推理得出 <b style={{ color: '#B585F2' }}>{summary.inferred_total}</b> 条</span>
                    <Tag color="blue">OWL-RL/RDFS 蕴含 {summary.inferred_entailment}</Tag>
                    <Tag color="purple">规则推理 {summary.inferred_rule}</Tag>
                    {summary.truncated && <Tag color="orange">超出 {summary.persisted} 条已截断展示</Tag>}
                </div>
            )}

            {/* 结果列表 */}
            <div className="flex items-center gap-2 mb-2">
                <span className="text-sm font-medium">推理结果</span>
                <span className="text-xs text-gray-400">（共 {total} 条，最多展示 {LIMIT} 条）</span>
                <div className="flex-1" />
                <Radio.Group size="small" value={sourceFilter} onChange={(e) => { setSourceFilter(e.target.value); loadResults(e.target.value); }}>
                    <Radio.Button value={undefined}>全部</Radio.Button>
                    <Radio.Button value="owlrl">蕴含</Radio.Button>
                    <Radio.Button value="rule">规则</Radio.Button>
                </Radio.Group>
                <Button size="small" icon={<ReloadOutlined />} onClick={() => loadResults(sourceFilter)} />
            </div>
            {loading ? (
                <div className="py-10 text-center"><Spin /></div>
            ) : items.length === 0 ? (
                <Empty description="暂无推理结果，点击「运行推理」生成" className="py-6" />
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
                                <span className="text-gray-400">—[{it.predicate_label}]→</span>
                                <span className="font-medium">{it.object_label || it.object_uri}</span>
                            </div>
                        );
                    })}
                </div>
            )}

            {/* ── 规则管理抽屉 ── */}
            <Modal
                title="推理规则管理"
                open={rulesOpen}
                onCancel={() => { setRulesOpen(false); setRuleForm(null); setEditingRule(null); }}
                width={640}
                footer={null}
            >
                <div className="mb-3 text-xs text-gray-500">
                    规则：当主体属于「主语类」（含子类）、经「谓词」连接到「宾语类」（含子类）的实例时，
                    自动推导出两者之间的「结论谓词」关系。推导结果只进推理结果，不改原始事实。
                </div>
                {!ruleForm ? (
                    <Button size="small" icon={<PlusOutlined />}
                            onClick={() => { setEditingRule(null); setRuleForm({ name: '', s: '', p: '', o: '', q: '' }); }}>
                        新建规则
                    </Button>
                ) : (
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
                                <div className="text-xs text-gray-400">
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
