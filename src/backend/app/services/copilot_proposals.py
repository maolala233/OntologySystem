# app/services/copilot_proposals.py - 意图分类与编辑提案生成（O5-B24，docs/design/14 §5 S2 / §3.1）
# 职责：①意图五分类（规则关键词优先，快模型兜底，不把慢模型当前置）；
# ②编辑类单次 LLM 调用生成提案（不进 tool-loop）；
# ③存在性校验闸前置（复用 B19.1 normalize_label 归一匹配；幻觉 target 打回重生成 1 次，再败转澄清）；
# ④影响面/服务端确定性重建 diff（不信任 LLM 的数字，验收要求删类 impact 必含级联计数）。
# 本模块只生成提案（status=pending 语义由 B25 的 copilot_proposals 表接管持久化与执行）。
from __future__ import annotations

import json
import logging
import re

from sqlalchemy.orm import Session

from app.adapters.resolution import normalize_label
from app.infrastructure.database import Project, ReviewItem

logger = logging.getLogger(__name__)

# 意图五分类（14 §5 B24）
INTENTS = ("edit", "analysis", "clarify_continue", "verify", "chitchat")

# 提案十类（14 §3.1 + S6 补：类属性编辑 + S7 补：新增实例）；高风险：删类/并类/删实例
KINDS = ("merge_classes", "delete_class", "add_class", "add_relation",
         "edit_relation", "delete_instance", "edit_instance", "edit_class",
         "declare_axiom", "add_instance")
HIGH_RISK_KINDS = {"delete_class", "merge_classes", "delete_instance"}

AXIOM_KINDS = ("disjoint_with", "equivalent_classes", "sub_class_of",
               "functional", "inverse_of", "cardinality")

AXIOM_KIND_LABELS = {
    "disjoint_with": "互斥类", "equivalent_classes": "等价类",
    "sub_class_of": "子类约束", "functional": "函数性属性",
    "inverse_of": "逆关系", "cardinality": "基数约束",
}

INSTANCE_LABEL_BUDGET = 2200  # 实例清单在生成 prompt 里的字符预算（超预算截断 + 提示抽样）


# ---------------------------------------------------------------- 意图分类（双路）

_CHITCHAT_RE = re.compile(
    r"^(你好|您好|hi|hello|嗨|哈喽|谢谢|多谢|感谢|辛苦|再见|拜拜|晚安)[!！。~～，,\s]*$"
    r"|^(你是谁|你叫什么|你是什么|你能(做|干)什么|介绍一下你自己)[?？!！。~～\s]*$")
_EDIT_RE = re.compile(
    r"(删除|删掉|删了|删去|移除|去掉|合并|并成|归并|合为一个|合为一个|新建|新加|新增|"
    r"加一[个条条]|添加|加上|补一[个条]|补上|补齐|改名|重命名|改名为|更名为|拆分|"
    r"声明.{0,8}公理|加[个条].{0,6}公理|declare)")
_VERIFY_RE = re.compile(
    r"(验证|评测|CQ|competency|一致性检查|一致性校验|跑推理|推理分析|机检|SHACL|"
    r"体检|质量检查|抽检|复核)")
# 澄清后继续：短肯定语（S3-B26 选项化反问的口头回应；无历史时按字面识别）
_CLARIFY_RE = re.compile(
    r"^(是的?|对的?|嗯+|哦了|ok|okay|好[的吧的呀]?|可以|行|就按|按.{0,12}(方案|建议|来办)|"
    r"继续|确认|同意|没错|就是这样)[!！。~～，,\s]*$")
_ANALYSIS_RE = re.compile(
    r"(怎么样|咋样|如何|多少|几个|哪些|什么|为什么|为啥|解释|说明|介绍|看看|分析|情况|"
    r"对比|原因|理由|合理吗|正常吗|吗[?？]\s*$|[?？]\s*$)")
# 模糊编辑指令（S3-B26）：句首是"整理/优化/处理"这类没说清操作的动词 → 澄清而非猜测改动
_VAGUE_EDIT_RE = re.compile(
    r"^(?:请|帮我|麻烦你?|给我)?(?:整理|优化|完善|处理|清理|收拾|治理|规范)(?:一?下)?")
CLARIFY_MAX_ROUNDS = 2  # 最多澄清 2 轮，超过建议转人工编辑（14 §5 B26）


def classify_intent_by_rule(message: str, history: list | None = None) -> dict:
    """规则路：高置信关键词命中即返回（毫秒级，无模型也可用）。

    返回 {"intent", "via", "confidence"}；无命中时 intent="analysis"（低置信，
    调用方可决定是否走 LLM 兜底路）。
    """
    text = (message or "").strip()
    if not text:
        return {"intent": "chitchat", "via": "rule", "confidence": 0.6}
    if _CHITCHAT_RE.search(text):
        return {"intent": "chitchat", "via": "rule", "confidence": 0.9}
    # 编辑动词优先级最高（「删除的实例怎么恢复」这类反例交给 LLM 路纠偏：
    # 疑问词 + 编辑词同时出现且疑问词在后（句首）时降置信，让 LLM 复核）
    edit_hit = _EDIT_RE.search(text)
    question_head = bool(re.match(r"^(什么|为什么|为啥|哪些|多少|怎么|如何|难道)", text))
    if edit_hit and not (question_head and edit_hit.start() > 8):
        return {"intent": "edit", "via": "rule", "confidence": 0.9}
    if _VERIFY_RE.search(text):
        return {"intent": "verify", "via": "rule", "confidence": 0.85}
    # 模糊编辑动词（句首"整理/优化/处理…"）：是编辑意图但操作不明 → edit+vague 标记
    # （B26：调用方据此直接澄清，不赌提案生成；非提问语境才生效）
    if _VAGUE_EDIT_RE.match(text) and not _ANALYSIS_RE.search(text):
        return {"intent": "edit", "via": "rule", "confidence": 0.55, "vague": True}
    # 短肯定语（≤12 字）且非提问 → 澄清后继续
    if len(text) <= 12 and _CLARIFY_RE.match(text) and not _ANALYSIS_RE.search(text):
        return {"intent": "clarify_continue", "via": "rule", "confidence": 0.8}
    if _ANALYSIS_RE.search(text):
        return {"intent": "analysis", "via": "rule", "confidence": 0.85}
    return {"intent": "analysis", "via": "rule", "confidence": 0.4}


def classify_intent(message: str, history: list | None = None,
                    llm=None) -> dict:
    """意图分类主入口：规则优先，低置信且有 LLM 时走快模型兜底（单次调用）。

    LLM 失败/超时不阻断：降级为规则结果（analysis）。
    """
    ruled = classify_intent_by_rule(message, history=history)
    if ruled.get("vague"):
        # 模糊编辑指令不再花一次 LLM 复核分类——分类器没有画布知识，改不了"没说清操作"
        return ruled
    if ruled["confidence"] >= 0.75 or llm is None:
        return ruled
    try:
        # 兼容 ORM CopilotMessage 与 dict 两种 history 形态
        recent = "\n".join(
            f"{'用户' if getattr(m, 'role', None) == 'user' else '助手'}："
            f"{(getattr(m, 'content', None) or '')[:120]}"
            for m in (history or [])[-4:])
        sys_p = ("你是意图分类器。把用户消息分为五类之一并只输出 JSON："
                 '{"intent":"edit|analysis|clarify_continue|verify|chitchat"}。'
                 "edit=要求修改本体（增删改类/关系/实例/公理）；"
                 "用户用陈述句给出业务事实或业务规则也算 edit——"
                 "例如「客户可以购买基金」是关系诉求、「客户具备年龄属性」是属性诉求，"
                 "都应转化为提案；"
                 "analysis=分析问答；"
                 "clarify_continue=对助手澄清/建议的简短确认或选择；"
                 "verify=要求验证/评测（CQ、一致性、推理）；chitchat=寒暄或问助手本身。")
        user_p = (f"【最近对话】\n{recent}\n【用户消息】\n{message}")
        resp = llm.call_llm(system_prompt=sys_p, user_prompt=user_p,
                            stream=False, json_schema={
                                "name": "intent",
                                "schema": {"type": "object", "properties": {
                                    "intent": {"type": "string",
                                               "enum": list(INTENTS)}}}})
        # 双返回形态：{"content": str|dict} 或已解析 dict（json_object 降级路径）
        if isinstance(resp, dict) and resp.get("content") is None:
            data = resp
        else:
            text = resp.get("content") if isinstance(resp, dict) else resp
            data = text if isinstance(text, dict) else json.loads(str(text))
        intent = str(data.get("intent") or "").strip()
        if intent in INTENTS:
            return {"intent": intent, "via": "llm", "confidence": 0.85}
    except Exception as exc:  # noqa: BLE001 —— 分类失败不阻断主链路
        logger.warning(f"[copilot-intent] LLM 分类失败，降级规则结果：{exc}")
    return ruled


# ---------------------------------------------------------------- 画布上下文

def _schema_of(gd_or_project) -> dict:
    """取项目/图数据的 schema 节（兼容 Project ORM 与纯 dict 两种入参）。"""
    if isinstance(gd_or_project, dict):
        gd = gd_or_project
    else:
        gd = gd_or_project.graph_data if isinstance(gd_or_project.graph_data, dict) else {}
    schema = gd.get("schema")
    return schema if isinstance(schema, dict) else {}


def _nodes(gd: dict) -> list:
    return [n for n in (gd.get("nodes") or []) if isinstance(n, dict)]


def _edges(gd: dict) -> list:
    return [e for e in (gd.get("edges") or []) if isinstance(e, dict)]


def _node_data(n: dict) -> dict:
    d = n.get("data")
    return d if isinstance(d, dict) else {}


def _is_class(n: dict) -> bool:
    return str(_node_data(n).get("type") or "") in ("owl:Class", "Class")


def instance_nodes(gd: dict) -> list[dict]:
    """实例节点（含 data.label 与 class_label）。"""
    return [n for n in _nodes(gd) if not _is_class(n)]


def class_labels(gd: dict) -> list[str]:
    out = []
    for n in _nodes(gd):
        if _is_class(n):
            lbl = str(_node_data(n).get("label") or "").strip()
            if lbl:
                out.append(lbl)
    return out


def predicate_labels(gd: dict) -> list[str]:
    schema = _schema_of(gd)
    out = []
    for op in (schema.get("object_properties") or []):
        if isinstance(op, dict) and op.get("label"):
            out.append(str(op["label"]))
    # 兜底：图上实际出现的关系谓词
    for e in _edges(gd):
        lbl = str(e.get("label") or "").strip()
        if lbl and lbl not in out:
            out.append(lbl)
    return out


def build_canvas_snapshot(db: Session, project: Project,
                          selected: dict | None = None) -> str:
    """提案生成的画布上下文：类/谓词全清单 + 实例清单（预算内）+ 选中态。"""
    gd = project.graph_data if isinstance(project.graph_data, dict) else {}
    insts = [str(_node_data(n).get("label") or "").strip()
             for n in instance_nodes(gd)]
    insts = [i for i in insts if i]
    inst_text = "、".join(insts)
    if len(inst_text) > INSTANCE_LABEL_BUDGET:
        inst_text = inst_text[:INSTANCE_LABEL_BUDGET] + f"…（共 {len(insts)} 个实例，仅展示前段）"
    schema = gd.get("schema") or {}
    prop_lines = []
    for op in (schema.get("object_properties") or []):
        if isinstance(op, dict) and op.get("label"):
            prop_lines.append(
                f"{op['label']}（domain={op.get('domain') or '?'}, range={op.get('range') or '?'}）")
    parts = [
        "【画布类清单】" + "、".join(class_labels(gd)),
        "【对象属性清单】" + ("；".join(prop_lines) if prop_lines else "（无）"),
        "【实例清单】" + (inst_text or "（无实例）"),
    ]
    if selected:
        parts.append("【用户画布选中态】" + json.dumps(selected, ensure_ascii=False)[:1200])
    return "\n".join(parts)


# ---------------------------------------------------------------- 存在性闸（B19.1 口径）

def _norm_tables(gd: dict) -> tuple[dict, dict, dict]:
    """归一索引：类 / 谓词 / 实例 → 画布原始 label（normalize_label 为键）。"""
    classes_by_norm: dict[str, str] = {}
    for lbl in class_labels(gd):
        classes_by_norm.setdefault(normalize_label(lbl), lbl)
    props_by_norm: dict[str, str] = {}
    for lbl in predicate_labels(gd):
        props_by_norm.setdefault(normalize_label(lbl), lbl)
    insts_by_norm: dict[str, str] = {}
    for n in instance_nodes(gd):
        lbl = str(_node_data(n).get("label") or "").strip()
        if lbl:
            insts_by_norm.setdefault(normalize_label(lbl), lbl)
    return classes_by_norm, props_by_norm, insts_by_norm


def _resolve(name, table: dict, what: str) -> tuple[str | None, str | None]:
    canon = table.get(normalize_label(str(name or "")))
    if canon is None:
        return None, f"引用了不存在的{what}: {name}（疑似幻觉）"
    return canon, None


# ---------------------------------------------------------------- 影响面（B13 口径）

def compute_impact(gd: dict, kind: str, targets: dict) -> dict:
    """影响面计数：{"instances", "relations", "review_items"}（3.1 契约；删类必含级联）。

    relations=受波及的图边数（实例边/类边）；review_items=pending 审核项中
    payload.label 命中目标的条数（DB 查询由调用方注入 review_hits，纯函数默认 0）。
    """
    nodes = _nodes(gd)
    edges = _edges(gd)
    node_label = {n.get("id"): str(_node_data(n).get("label") or "") for n in nodes}
    inst_by_label: dict[str, list] = {}
    for n in nodes:
        if not _is_class(n):
            lbl = normalize_label(str(_node_data(n).get("label") or ""))
            inst_by_label.setdefault(lbl, []).append(n.get("id"))

    def _ids_of(labels: list[str]) -> set:
        out = set()
        for lbl in labels:
            out.update(inst_by_label.get(normalize_label(lbl), []))
        return out

    def _instances_of_classes(class_labels: list[str]) -> set:
        """类下的实例：优先按 data.class_label 归一匹配，兜底 instance_of 边指向的类节点。"""
        wanted = {normalize_label(str(x or "")) for x in class_labels} - {""}
        cls_node_ids = {n.get("id") for n in nodes if _is_class(n)
                        and normalize_label(str(_node_data(n).get("label") or "")) in wanted}
        inst = {n.get("id") for n in nodes if not _is_class(n)
                and normalize_label(str(_node_data(n).get("class_label") or "")) in wanted}
        if not inst and cls_node_ids:
            inst = {e.get("source") for e in edges if e.get("target") in cls_node_ids}
        return inst

    if kind == "delete_class":
        target = str(targets.get("class") or "")
        cls_node_ids = {n.get("id") for n in nodes if _is_class(n)
                        and normalize_label(str(_node_data(n).get("label") or ""))
                        == normalize_label(target)}
        inst_ids = _instances_of_classes([target])
        hit = cls_node_ids | inst_ids
        rels = sum(1 for e in edges
                   if e.get("source") in hit or e.get("target") in hit)
        return {"instances": len(inst_ids), "relations": rels}
    if kind == "merge_classes":
        frm = targets.get("from") or []
        into = targets.get("into")
        inst_ids = _instances_of_classes([str(x) for x in frm] + [str(into or "")])
        rels = sum(1 for e in edges
                   if e.get("source") in inst_ids or e.get("target") in inst_ids)
        return {"instances": len(inst_ids), "relations": rels}
    if kind == "delete_instance":
        inst_ids = _ids_of([str(targets.get("instance") or "")])
        rels = sum(1 for e in edges
                   if e.get("source") in inst_ids or e.get("target") in inst_ids)
        return {"instances": len(inst_ids), "relations": rels}
    if kind == "add_relation":
        d = str(targets.get("domain") or "")
        r = str(targets.get("range") or "")
        inst_ids = _instances_of_classes([d, r])
        rels = sum(1 for e in edges
                   if e.get("source") in inst_ids and e.get("target") in inst_ids)
        return {"instances": 0, "relations": rels}
    if kind in ("edit_relation",):
        ids = _ids_of([str(targets.get("subject") or ""), str(targets.get("object") or "")])
        pred = normalize_label(str(targets.get("predicate") or ""))
        rels = sum(1 for e in edges
                   if e.get("source") in ids and e.get("target") in ids
                   and normalize_label(str(e.get("label") or "")) == pred)
        return {"instances": 0, "relations": rels}
    if kind in ("edit_instance", "edit_class", "add_instance"):
        return {"instances": 0, "relations": 0}
    return {"instances": 0, "relations": 0}


def build_diff_preview(kind: str, targets: dict, impact: dict) -> list:
    """确定性 diff 条目（前端红绿渲染口径），不采信 LLM 生成的 diff。"""
    if kind == "delete_class":
        return [{"op": "remove", "type": "class", "label": targets.get("class"),
                 "cascade": {"instances": impact["instances"],
                             "relations": impact["relations"]}}]
    if kind == "merge_classes":
        items = [{"op": "remove", "type": "class", "label": f}
                 for f in (targets.get("from") or [])]
        items.append({"op": "merge_into", "type": "class", "label": targets.get("into"),
                      "cascade": {"instances": impact["instances"],
                                  "relations": impact["relations"]}})
        return items
    if kind == "add_class":
        return [{"op": "add", "type": "class", "label": targets.get("label"),
                 "parent": targets.get("sub_class_of")}]
    if kind == "add_relation":
        return [{"op": "add", "type": "relation", "label": targets.get("predicate"),
                 "detail": f"{targets.get('domain')} → {targets.get('range')}"}]
    if kind == "edit_relation":
        changes = {}
        if targets.get("new_predicate"):
            changes["predicate"] = targets["new_predicate"]
        return [{"op": "update", "type": "relation",
                 "label": f"{targets.get('subject')} -{targets.get('predicate')}-> "
                          f"{targets.get('object')}",
                 "detail": (f"改名为「{targets['new_predicate']}」"
                            if targets.get("new_predicate") else "请补充新关系名"),
                 "changes": changes or {"note": "调整关系定义"}}]
    if kind == "delete_instance":
        return [{"op": "remove", "type": "instance", "label": targets.get("instance"),
                 "cascade": {"relations": impact["relations"]}}]
    if kind == "edit_instance":
        changes = {}
        if targets.get("new_label"):
            changes["label"] = targets["new_label"]
        if targets.get("properties"):
            changes["properties"] = targets["properties"]
        return [{"op": "update", "type": "instance", "label": targets.get("instance"),
                 "changes": changes or {"note": "调整实例属性"}}]
    if kind == "edit_class":
        suggested = set(targets.get("suggested_types") or [])
        ptypes = targets.get("property_types") or {}
        changes = {}
        if targets.get("new_label"):
            changes["label"] = targets["new_label"]
        if targets.get("set_properties"):
            changes["新增/修改属性"] = targets["set_properties"]
        if targets.get("remove_properties"):
            changes["移除属性"] = targets["remove_properties"]
        details = []
        if targets.get("set_properties"):
            parts = []
            for k in targets["set_properties"]:
                pt = str(ptypes.get(k) or "").strip()
                mark = "（建议）" if k in suggested else ""
                parts.append(f"{k}({pt}){mark}" if pt else k)
            details.append("新增/修改属性：" + "、".join(parts))
        if targets.get("remove_properties"):
            details.append("移除属性：" + "、".join(targets["remove_properties"]))
        if targets.get("new_label"):
            details.append(f"改名为「{targets['new_label']}」")
        return [{"op": "update", "type": "class", "label": targets.get("class"),
                 "detail": "；".join(details) or "调整类定义",
                 "changes": changes or {"note": "调整类定义"}}]
    if kind == "add_instance":
        return [{"op": "add", "type": "instance", "label": targets.get("label"),
                 "detail": f"所属类：{targets.get('class')}",
                 "changes": targets.get("properties") or {}}]
    if kind == "declare_axiom":
        tg = [str(x) for x in (targets.get("targets") or [])]
        return [{"op": "add", "type": "axiom",
                 "label": AXIOM_KIND_LABELS.get(targets.get("axiom_kind"),
                                                targets.get("axiom_kind") or "axiom"),
                 "detail": " × ".join(tg) if tg else None}]
    return []


# ---------------------------------------------------------------- 提案校验（存在性闸）

def validate_proposal(raw: dict, gd: dict) -> tuple[dict | None, list[str]]:
    """按 kind 校验 target 存在性并归一为画布原始 label；不合法返回错误列表。

    add_class 反向闸：新 label 不得与既有类重名（重复改建议 edit）。
    """
    errors: list[str] = []
    kind = str(raw.get("kind") or "").strip()
    if kind not in KINDS:
        return None, [f"未知 kind: {kind}"]
    reason = str(raw.get("llm_reason") or "").strip()
    if not reason:
        errors.append("缺少 llm_reason（人话理由）")
    classes_by_norm, props_by_norm, insts_by_norm = _norm_tables(gd)
    t = raw.get("targets") if isinstance(raw.get("targets"), dict) else {}
    out: dict = {}

    if kind == "delete_class":
        canon, err = _resolve(t.get("class"), classes_by_norm, "类")
        if err:
            errors.append(err)
        out = {"class": canon}
    elif kind == "merge_classes":
        frm = t.get("from") or []
        if not isinstance(frm, list) or len(frm) < 2:
            errors.append("merge_classes 需要 from 为至少两个类 label")
            frm = []
        froms = []
        for f in frm[:4]:
            canon, err = _resolve(f, classes_by_norm, "类")
            if err:
                errors.append(err)
            else:
                froms.append(canon)
        into, err = _resolve(t.get("into"), classes_by_norm, "保留类")
        if err:
            errors.append(err)
        out = {"from": froms, "into": into}
    elif kind == "add_class":
        lbl = str(t.get("label") or "").strip()
        if not lbl:
            errors.append("add_class 缺少新类 label")
        elif normalize_label(lbl) in classes_by_norm:
            errors.append(f"类「{lbl}」已存在，请改用 edit/merge 表达")
        parent = t.get("sub_class_of")
        canon_parent = None
        if parent:
            canon_parent, err = _resolve(parent, classes_by_norm, "父类")
            if err:
                errors.append(err)
        out = {"label": lbl, "sub_class_of": canon_parent}
    elif kind == "add_relation":
        pred = str(t.get("predicate") or "").strip()
        if not pred:
            errors.append("add_relation 缺少 predicate")
        dom, err = _resolve(t.get("domain"), {**classes_by_norm, **insts_by_norm},
                            "domain（类或实例）")
        if err:
            errors.append(err)
        rng, err = _resolve(t.get("range"), {**classes_by_norm, **insts_by_norm},
                            "range（类或实例）")
        if err:
            errors.append(err)
        # 冲突检查（S8）：与既有对象属性比对——完全重复拦下，同谓词/同向近似重复给警告
        warnings: list[str] = []
        if not errors and pred:
            pn = normalize_label(pred)
            dn = normalize_label(dom or "")
            rn = normalize_label(rng or "")
            existing = [o for o in (gd.get("schema") or {}).get("object_properties") or []
                        if isinstance(o, dict) and o.get("label")]
            if any(normalize_label(str(o.get("label"))) == pn
                   and normalize_label(str(o.get("domain") or "")) == dn
                   and normalize_label(str(o.get("range") or "")) == rn
                   for o in existing):
                errors.append(f"关系「{pred}」（{dom}→{rng}）已存在，若要修改请用 edit_relation")
            else:
                same_pred = [o for o in existing if normalize_label(str(o.get("label"))) == pn]
                if same_pred:
                    defs = "、".join(f"{o.get('domain')}→{o.get('range')}"
                                     for o in same_pred[:3])
                    warnings.append(
                        f"谓词「{pred}」已有定义（{defs}），本次将新增 {dom}→{rng}；"
                        "若意图是修改已有关系请改用 edit_relation")
                same_dir = [o for o in existing
                            if normalize_label(str(o.get("domain") or "")) == dn
                            and normalize_label(str(o.get("range") or "")) == rn]
                if same_dir:
                    names = "、".join(str(o.get("label")) for o in same_dir[:3])
                    warnings.append(
                        f"已存在同方向关系「{names}」（{dom}→{rng}），新关系可能语义重复；"
                        "若只是改名请用 edit_relation")
        out = {"predicate": pred, "domain": dom, "range": rng}
        if warnings:
            out["warnings"] = warnings
    elif kind == "edit_relation":
        subj, err = _resolve(t.get("subject"), {**classes_by_norm, **insts_by_norm},
                             "subject")
        if err:
            errors.append(err)
        obj, err = _resolve(t.get("object"), {**classes_by_norm, **insts_by_norm},
                            "object")
        if err:
            errors.append(err)
        pred, err = _resolve(t.get("predicate"), props_by_norm, "关系名")
        if err:
            errors.append(err)
        new_pred = None
        if t.get("new_predicate"):
            np = str(t["new_predicate"]).strip()
            if not np:
                errors.append("new_predicate 不能为空串")
            else:
                new_pred = np
        out = {"subject": subj, "predicate": pred, "object": obj,
               "new_predicate": new_pred}
    elif kind == "delete_instance":
        canon, err = _resolve(t.get("instance"), insts_by_norm, "实例")
        if err:
            errors.append(err)
        out = {"instance": canon}
    elif kind == "edit_instance":
        canon, err = _resolve(t.get("instance"), insts_by_norm, "实例")
        if err:
            errors.append(err)
        props = t.get("properties")
        if props is not None and not isinstance(props, dict):
            errors.append("properties 必须为对象")
            props = None
        out = {"instance": canon, "properties": props,
               "new_label": (str(t["new_label"]).strip() or None)
               if t.get("new_label") else None}
    elif kind == "edit_class":
        canon, err = _resolve(t.get("class"), classes_by_norm, "类")
        if err:
            errors.append(err)
        set_props = t.get("set_properties")
        if set_props is not None:
            if not isinstance(set_props, dict) or not set_props:
                errors.append("set_properties 必须为非空对象或 null")
                set_props = None
            else:
                set_props = {str(k).strip(): ("" if v is None else str(v))
                             for k, v in set_props.items()
                             if str(k).strip()}
                if not set_props:
                    errors.append("set_properties 缺少有效属性名")
                    set_props = None
        rm_props = t.get("remove_properties")
        if rm_props is not None:
            if not isinstance(rm_props, list) or not rm_props:
                errors.append("remove_properties 必须为非空列表或 null")
                rm_props = None
            else:
                rm_props = [str(p).strip() for p in rm_props if str(p).strip()]
                if not rm_props:
                    errors.append("remove_properties 缺少有效属性名")
                    rm_props = None
        new_label = (str(t["new_label"]).strip() or None) if t.get("new_label") else None
        # S8：属性必须带数据类型——LLM 没给的键缺省建议 string，并在 suggested_types 里留痕
        prop_types_raw = t.get("property_types")
        prop_types: dict[str, str] = {}
        if prop_types_raw is not None:
            if not isinstance(prop_types_raw, dict):
                errors.append("property_types 必须为对象或 null")
            else:
                prop_types = {str(k).strip(): str(v).strip()
                              for k, v in prop_types_raw.items()
                              if str(k).strip() and str(v).strip()}
        suggested_types: list[str] = []
        if set_props:
            for pname in set_props:
                if not prop_types.get(pname):
                    prop_types[pname] = "string"
                    suggested_types.append(pname)
            prop_types = {k: v for k, v in prop_types.items() if k in set_props}
        if set_props is None and rm_props is None and not new_label:
            errors.append("edit_class 至少要有 set_properties/remove_properties/new_label 之一")
        out = {"class": canon, "set_properties": set_props,
               "remove_properties": rm_props, "new_label": new_label}
        if prop_types:
            out["property_types"] = prop_types
        if suggested_types:
            out["suggested_types"] = suggested_types
    elif kind == "add_instance":
        lbl = str(t.get("label") or "").strip()
        if not lbl:
            errors.append("add_instance 缺少新实例 label")
        elif normalize_label(lbl) in {**classes_by_norm, **insts_by_norm}:
            errors.append(f"「{lbl}」已存在（类/实例不允许重名），请改用 edit 表达")
        cls, err = _resolve(t.get("class"), classes_by_norm, "所属类")
        if err:
            errors.append(err)
        props = t.get("properties")
        if props is not None:
            if not isinstance(props, dict):
                errors.append("properties 必须为对象或 null")
                props = None
            else:
                props = {str(k).strip(): ("" if v is None else str(v))
                         for k, v in props.items() if str(k).strip()}
                if not props:
                    props = None
        out = {"label": lbl, "class": cls, "properties": props}
    elif kind == "declare_axiom":
        ak = str(t.get("axiom_kind") or "").strip()
        if ak not in AXIOM_KINDS:
            errors.append(f"未知 axiom_kind: {ak}（可选 {'/'.join(AXIOM_KINDS)}）")
        tgts = t.get("targets") or []
        if not isinstance(tgts, list) or not tgts:
            errors.append("declare_axiom 缺少 targets")
            tgts = []
        resolved = []
        unified = {**classes_by_norm, **props_by_norm}
        for x in tgts[:6]:
            canon, err = _resolve(x, unified, "公理对象（类/关系）")
            if err:
                errors.append(err)
            else:
                resolved.append(canon)
        out = {"axiom_kind": ak, "targets": resolved}
        if t.get("argument") is not None:
            try:
                out["argument"] = int(t["argument"])
            except (TypeError, ValueError):
                errors.append("argument 必须为整数")

    if errors:
        return None, errors
    # S8：冲突警告从 targets 提到提案顶层（generate_proposal 按顶层读取传给 finalize）
    warnings = out.pop("warnings", None)
    proposal = {
        "kind": kind,
        "targets": out,
        "llm_reason": reason,
        "evidence": [str(x) for x in (raw.get("evidence") or []) if str(x).strip()][:6],
        "risk_tier": "high" if kind in HIGH_RISK_KINDS else "low",
    }
    if warnings:
        proposal["warnings"] = warnings
    return proposal, []


# ---------------------------------------------------------------- 生成入口

_PROPOSAL_SYSTEM = (
    "你是企业本体平台的编辑提案生成器。根据用户消息与画布清单，输出且仅输出一个 JSON 提案："
    '{"kind":"...","targets":{...},"llm_reason":"一段话人话理由","evidence":["..."]}'
    "\nkind 可选：merge_classes/delete_class/add_class/add_relation/edit_relation/"
    "delete_instance/edit_instance/edit_class/declare_axiom/add_instance。"
    "\ntargets 按 kind 分别为："
    "\n- delete_class: {\"class\": 画布已有类label}"
    "\n- merge_classes: {\"from\":[两个已有类label],\"into\":保留的类label}"
    "\n- add_class: {\"label\":新类名,\"sub_class_of\":已有父类label或null}"
    "\n- add_relation: {\"predicate\":关系名,\"domain\":已有label,\"range\":已有label}"
    "\n- edit_relation: {\"subject\":label,\"predicate\":现有关系名,\"object\":label,"
    "\"new_predicate\":新关系名或null}"
    "\n- delete_instance: {\"instance\":已有实例label}"
    "\n- edit_instance: {\"instance\":label,\"properties\":{属性:值}或null,\"new_label\":新名或null}"
    "\n- edit_class: {\"class\":已有类label,\"set_properties\":{属性名:值}或null,"
    "\"property_types\":{属性名:数据类型}或省略,"
    "\"remove_properties\":[属性名...]或null,\"new_label\":新名或省略}"
    "\n- declare_axiom: {\"axiom_kind\":\"disjoint_with|equivalent_classes|sub_class_of|"
    "functional|inverse_of|cardinality\",\"targets\":[已有label...],\"argument\":整数或省略}"
    "\n- add_instance: {\"label\":新实例名,\"class\":所属类label,\"properties\":{属性:值}或null}"
    "\n硬性要求：引用画布已有元素必须逐字使用清单中的原始 label，禁止编造；"
    "用户没说清 target 时也不要编造。impact/diff_preview/status 字段不需要你输出（服务端计算）。"
    "\n注意：给已有类新增/修改**数据属性**（如给「银行」加「联系方式」属性）必须用 edit_class，"
    "属性值用户没给就填空串表示先建属性槽位；绝不要为属性概念单独建类。"
    "\n注意：edit_class 的新增属性要尽量给出 property_types 数据类型"
    "（string/number/boolean/date/enumeration 中选）；用户没说类型时结合属性语义给出建议类型，"
    "并在 llm_reason 里注明该类型是系统建议（服务端也会标注「建议」）。"
    "\n注意：add_relation 生成前留意画布已有关系：完全同谓词同方向的关系会被直接拒绝，"
    "若意图是改已有关系请用 edit_relation；语义相近时仍可提案，服务端会附冲突提示。"
    "\n注意：往类下添加**实例**（如「工商银行」是「银行」的一个实例）必须用 add_instance，"
    "绝不要把实例建成类（add_class）。"
    "\n注意：用户用陈述句给出业务事实/规则时（如「客户可以购买基金」「客户具备年龄属性」），"
    "直接转化为对应提案而不要只做文字回答：涉及类与类之间的关联 → add_relation；"
    "给类新增数据属性 → edit_class（set_properties）。提案的 llm_reason 用一句话说明"
    "「从这句话识别出了什么诉求」；用户没提的属性值填空串建槽位。"
)


def _extract_json(resp) -> dict | None:
    """兼容 llm_client 双返回形态 + markdown 围栏包裹的 JSON。"""
    if isinstance(resp, dict) and resp.get("content") is None:
        return resp if isinstance(resp, dict) else None
    text = resp.get("content") if isinstance(resp, dict) else resp
    if isinstance(text, dict):
        return text
    if text is None:
        return None
    raw = str(text).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _review_hits(db: Session, project_id: int, labels: list[str]) -> int:
    if not labels:
        return 0
    targets = {normalize_label(x) for x in labels if x}
    rows = (db.query(ReviewItem.payload)
            .filter(ReviewItem.project_id == project_id,
                    ReviewItem.status.in_(("pending", "claimed"))).all())
    hits = 0
    for (payload,) in rows:
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, str) and normalize_label(v) in targets:
                    hits += 1
                    break
    return hits


def finalize_proposal(db: Session, project: Project, kind: str, targets: dict,
                      llm_reason: str, evidence: list, risk_tier: str,
                      warnings: list | None = None) -> dict:
    """服务端定稿：impact 级联计数 + 确定性 diff_preview + 提案号。

    warnings（S8 冲突/建议提示）随流带给前端即时渲染，并追加进 llm_reason 落库
    （copilot_proposals 无 warnings 列，刷新后从理由文案里仍能看到）。
    """
    gd = project.graph_data if isinstance(project.graph_data, dict) else {}
    impact = compute_impact(gd, kind, targets)
    labels = [str(v) for v in
              (targets.get("from") or [targets.get("class") or targets.get("instance")
                                       or targets.get("into") or ""]) if v]
    impact["review_items"] = _review_hits(db, project.id, labels)
    import uuid as _uuid
    warnings = [str(w) for w in (warnings or []) if str(w).strip()]
    reason = str(llm_reason or "").strip()
    if warnings:
        reason = (reason + " " if reason else "") + \
            " ".join(f"【注意】{w}" for w in warnings)
    result = {
        "proposal_id": f"cp_{_uuid.uuid4().hex[:10]}",
        "kind": kind,
        "targets": targets,
        "diff_preview": build_diff_preview(kind, targets, impact),
        "impact": impact,
        "llm_reason": reason,
        "evidence": evidence,
        "risk_tier": risk_tier,
        "status": "pending",
    }
    if warnings:
        result["warnings"] = warnings
    return result


def generate_proposal(db: Session, project: Project, message: str,
                      selected: dict | None, llm) -> dict:
    """编辑意图 → 提案（单次 LLM 调用；幻觉 target 打回重生成 1 次，再败转澄清）。"""
    snapshot = build_canvas_snapshot(db, project, selected)
    feedback = ""
    last_errors: list[str] = []
    for attempt in (1, 2):
        user_p = (f"{snapshot}\n\n【用户消息】\n{message}"
                  + (f"\n\n【上一次输出被拒绝】{feedback}" if feedback else ""))
        try:
            resp = llm.call_llm(system_prompt=_PROPOSAL_SYSTEM, user_prompt=user_p,
                                stream=False, json_schema={
                                    "name": "proposal",
                                    "schema": {"type": "object", "properties": {
                                        "kind": {"type": "string"},
                                        "targets": {"type": "object"},
                                        "llm_reason": {"type": "string"},
                                        "evidence": {"type": "array",
                                                     "items": {"type": "string"}}}}})
        except Exception as exc:  # noqa: BLE001 —— 模型失败直接转错误提示
            logger.warning(f"[copilot-proposal] 生成失败：{exc}")
            return {"ok": False, "kind": "model_error",
                    "clarify": f"模型暂时不可用，无法生成提案（{exc}）。请稍后重试。"}
        raw = _extract_json(resp)
        if raw is None:
            last_errors = ["模型输出不是合法 JSON"]
            feedback = "输出必须是一个 JSON 对象，不要有其他文字。"
            continue
        proposal, errors = validate_proposal(raw, project.graph_data
                                             if isinstance(project.graph_data, dict) else {})
        if proposal:
            final = finalize_proposal(db, project, proposal["kind"], proposal["targets"],
                                      proposal["llm_reason"], proposal["evidence"],
                                      proposal["risk_tier"],
                                      warnings=proposal.get("warnings"))
            return {"ok": True, "proposal": final}
        last_errors = errors
        feedback = ("；".join(errors)
                    + "。target 必须逐字使用画布清单中的原始 label；确实不存在的元素"
                      "不得引用（add_class 的新类名除外）。请重新输出完整提案 JSON。")
    # 重生成仍失败 → 转澄清（S3-B26 会升级为选项化反问）
    return {"ok": False, "kind": "hallucination",
            "missing": last_errors,
            "clarify": ("没有在画布上找到你提到的元素：" + "；".join(last_errors)
                        + "。请确认名称（可从画布复制），或告诉我具体想改哪个类/关系/实例。")}


# ---------------------------------------------------------------- 澄清循环（S3-B26）

_CLARIFY_SYSTEM = (
    "你是本体构建助手的澄清器。用户的编辑指令含糊（没说清想做什么操作），"
    "或引用了画布上不存在的元素。生成一个简短的澄清问题（一句话，人话、面向非专业用户）"
    "和最多 2 个最可能、具体可点的后续操作选项。"
    '只输出 JSON：{"question":"...","options":["...","..."]}。'
    "选项要具体（例如「新建一个「账户」类」），系统会自动补一个「其他」。"
)


def count_clarify_rounds(history) -> int:
    """本会话已发生的澄清轮数（assistant 消息 kind=clarify 的条数）。"""
    return sum(1 for m in (history or [])
               if getattr(m, "role", "") == "assistant"
               and getattr(m, "kind", "") == "clarify")


def clarify_context(message: str, history) -> str | None:
    """用户回复澄清时，回溯澄清链把补充并回原指令（B26：携带澄清上下文继续原流程）。

    澄清链形如 [用户原指令, 助手clarify, (用户补充, 助手clarify)*]；返回
    "原指令（用户补充：s1）（用户补充：s2）…"；history 末尾不是澄清链时返回 None。
    """
    ms = list(history or [])
    parts: list[str] = []
    i = len(ms) - 1
    while i >= 0:
        m = ms[i]
        if not (getattr(m, "role", "") == "assistant"
                and getattr(m, "kind", "") == "clarify"):
            break
        i -= 1
        while i >= 0 and getattr(ms[i], "role", "") != "user":
            i -= 1
        if i < 0:
            break
        parts.append((getattr(ms[i], "content", "") or "").strip())
        i -= 1
    if not parts:
        return None
    orig = parts[-1]
    sups = list(reversed(parts[:-1])) + [message]
    ctx = orig
    for s in sups:
        if s:
            ctx += f"（用户补充：{s}）"
    return ctx


def build_clarify(db: Session, project: Project, message: str, history,
                  llm, trigger: str = "vague",
                  errors: list[str] | None = None) -> dict:
    """产澄清问题 + ≤3 个选项（含"其他"）；LLM 失败降级为确定性话术。

    轮次上限：本会话已澄清 2 轮仍含糊 → 不再产新问题，返回 exhausted=True
    并建议转人工编辑（拆具体指令或画布选中后处理）。
    """
    rounds = count_clarify_rounds(history)
    if rounds >= CLARIFY_MAX_ROUNDS:
        return {
            "exhausted": True, "round": rounds, "options": [],
            "question": ("咱们已经澄清了两轮还没落到具体操作，我先把话收住，避免改错："
                         "建议把指令拆得更具体（写明类/关系/实例的名字），"
                         "或在画布上选中元素后再发给我处理。"),
        }
    question, options = None, []
    try:
        snapshot = build_canvas_snapshot(db, project, None)
        err_text = (f"\n【上一次尝试被拒绝的原因】{'；'.join(errors)}" if errors else "")
        resp = llm.call_llm(system_prompt=_CLARIFY_SYSTEM,
                            user_prompt=f"{snapshot}\n\n【用户消息】\n{message}{err_text}",
                            stream=False, json_schema={
                                "name": "clarify",
                                "schema": {"type": "object", "properties": {
                                    "question": {"type": "string"},
                                    "options": {"type": "array",
                                                "items": {"type": "string"}}}}})
        data = _extract_json(resp)
        if data:
            q = str(data.get("question") or "").strip()
            if q:
                question = q
            opts = data.get("options")
            if isinstance(opts, list):
                options = [str(o).strip() for o in opts
                           if str(o).strip() and str(o).strip() != "其他"]
    except Exception as exc:  # noqa: BLE001 —— 澄清问题允许降级，不阻断
        logger.warning(f"[copilot-clarify] LLM 生成澄清失败，降级话术：{exc}")
    if not question:
        if trigger == "hallucination":
            question = ("没有在画布上找到你提到的元素（" + "；".join(errors or [])
                        + "）。你想怎么处理？")
        else:
            question = "这个指令可以有几种理解——你想让我做哪一种？"
    options = list(dict.fromkeys(options))[:2] + ["其他"]
    return {"question": question, "options": options,
            "round": rounds + 1, "trigger": trigger}


__all__ = [
    "INTENTS", "KINDS", "HIGH_RISK_KINDS", "AXIOM_KINDS",
    "CLARIFY_MAX_ROUNDS",
    "classify_intent", "classify_intent_by_rule",
    "build_canvas_snapshot", "generate_proposal", "validate_proposal",
    "compute_impact", "build_diff_preview", "finalize_proposal",
    "count_clarify_rounds", "clarify_context", "build_clarify",
]
