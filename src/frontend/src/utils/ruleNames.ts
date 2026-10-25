// utils/ruleNames.ts - 机检规则 id → 人话（S25-3 走查 P2：告警不暴露内部 id）
const RULE_NAMES: Record<string, string> = {
    single_direct_child: '单子类冗余（建议并入父类或补充兄弟类）',
    sibling_generalization_level: '同层泛化层级不一致（兄弟类抽象层级差异大）',
    class_hierarchy_cycle: '分类层次成环',
    dangling_parent_ref: '父类引用悬空（指向不存在的类）',
    self_parent_ref: '类把自己声明为父类',
    domain_violation: 'domain 违例（主语类型不符）',
    range_violation: 'range 违例（宾语类型不符）',
    disjoint_violation: '互斥类矛盾（disjointWith）',
    functional_duplicate: '函数性谓词重复赋值',
    unknown_class: '类不在本体中',
    metric: '治理指标口径命中',
};

/** 把告警文本里的 [rule_id] 替换为人话；未知 id 原样保留。 */
export function humanizeRuleText(text: string | null | undefined): string {
    if (!text) return '';
    return String(text).replace(/\[([a-z_]+)\]/g, (m, id) =>
        RULE_NAMES[id] ? `【${RULE_NAMES[id]}】` : m);
}

/** 规则 id → 人话标签（schema_warnings.rule 字段用）；未知返回 null。 */
export function ruleLabel(rule: string | null | undefined): string | null {
    if (!rule) return null;
    return RULE_NAMES[String(rule)] ?? null;
}
