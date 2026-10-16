# app/services/copilot_executor.py - 提案执行器 + 机检自愈（O5-B25，docs/design/14 §5 S2）
# 职责：①状态机 pending→applied/rejected/expired（并发画布冲突/超期在执行时判 expired）；
# ②按 kind 在内存中改画布 gd（复用 B19.1 normalize_label 归一匹配），通过机检才提交——
#   schema_checks（TBox：类层次环/悬空父类）+ SHACL 摘要（ABox：gd 内存投影）+ owlrl
#   一致性（仅 declare_axiom，基线对比）；新增违例即回滚（不提交），提案转 rejected 并留原因；
# ③提交走 manual 打版（generate_ttl + create_version + audit_logs 关联 session_id）。
# 不引 agent 框架：执行器是确定性函数，LLM 只负责上游提案生成（B24）。
from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.adapters.resolution import normalize_label
from app.infrastructure.database import CopilotProposal, OntologyVersion, Project, User
from app.services.copilot_proposals import HIGH_RISK_KINDS

logger = logging.getLogger(__name__)

PROPOSAL_TTL_HOURS = 24  # 会话内未处理即过期（§3.1：防基于过期画布执行）


# ---------------------------------------------------------------- 画布内存投影

def _nodes(gd: dict) -> list:
    return gd.setdefault("nodes", [])


def _edges(gd: dict) -> list:
    return gd.setdefault("edges", [])


def _node_data(n: dict) -> dict:
    d = n.get("data")
    return d if isinstance(d, dict) else {}


def _is_class(n: dict) -> bool:
    return str(_node_data(n).get("type") or "") in ("owl:Class", "Class")


def _edge_relation(e: dict) -> str:
    d = e.get("data") if isinstance(e.get("data"), dict) else {}
    return str(d.get("relation") or e.get("label") or "")


def _abox_projection(gd: dict) -> tuple[list, list]:
    """gd → SHACL 用 ents/rels 字典投影（字符串 id 自洽即可，conflicts._as_* 保形）。"""
    ents, rels = [], []
    for n in gd.get("nodes") or []:
        d = _node_data(n)
        ents.append({"id": n.get("id"), "uri": "", "label": str(d.get("label") or ""),
                     "class_label": str(d.get("class_label") or d.get("label") or ""),
                     "props": d.get("properties") or {}})
    for e in gd.get("edges") or []:
        rels.append({"subject_id": e.get("source"), "predicate": _edge_relation(e),
                     "object_id": e.get("target")})
    return ents, rels


# ---------------------------------------------------------------- 机检（基线对比）

def _run_checks(gd: dict) -> dict:
    """对 gd 跑 TBox 机检 + SHACL 摘要；返回 {schema_errors, shacl_violations}。"""
    from app.adapters.schema_checks import run_schema_checks

    out = {"schema_errors": 0, "shacl_violations": 0}
    try:
        out["schema_errors"] = int(
            run_schema_checks(gd.get("schema")).summary().get("error_count") or 0)
    except Exception as exc:  # noqa: BLE001 —— 机检失败按 0 计（不误伤）
        logger.warning(f"[copilot-executor] schema_checks 失败：{exc}")
    try:
        from app.adapters.shacl_checks import run_shacl_summary
        ents, rels = _abox_projection(gd)
        out["shacl_violations"] = int(
            (run_shacl_summary(ents, rels, gd.get("schema")) or {}).get("violations") or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[copilot-executor] SHACL 复算失败：{exc}")
    return out


def _owlrl_signature(gd: dict) -> frozenset | None:
    """owlrl 一致性信号集（owl:Nothing 实例 + error_messages）；owlrl 不可用返回 None 跳过。"""
    try:
        import owlrl
    except Exception:  # noqa: BLE001
        return None
    try:
        from app.adapters.axiom_gates import _fact_graph, _owl_nothing_ids
        ents, rels = _abox_projection(gd)
        g = _fact_graph(gd.get("schema") or {}, ents, rels, None)
        closure = owlrl.OWLRL_Semantics(g, axioms=True, daxioms=True, rdfs=True)
        closure.closure()
        sig = set(_owl_nothing_ids(g))
        if closure.error_messages:
            sig.add("*")
        return frozenset(sig)
    except Exception as exc:  # noqa: BLE001 —— 病态图按矛盾处理（宁可错拒）
        logger.warning(f"[copilot-executor] owlrl 展开失败：{exc}")
        return frozenset({"*"})


# ---------------------------------------------------------------- 内存画布变更（按 kind）

def _remove_node_and_touching_edges(nodes: list, edges: list, node_id) -> None:
    nodes[:] = [n for n in nodes if str(n.get("id")) != str(node_id)]
    edges[:] = [e for e in edges
                if str(e.get("source")) != str(node_id)
                and str(e.get("target")) != str(node_id)]


def _mutate_delete_class(gd: dict, targets: dict) -> None:
    target = normalize_label(str(targets.get("class") or ""))
    schema = gd.setdefault("schema", {})
    classes = schema.get("classes") or []
    cls_labels = {c.get("label") for c in classes
                  if isinstance(c, dict) and normalize_label(str(c.get("label") or "")) == target}
    # schema：类条目 + 以它为 domain/range 的对象属性 + 其它类对它的 sub_class_of 引用
    schema["classes"] = [c for c in classes
                         if not (isinstance(c, dict)
                                 and normalize_label(str(c.get("label") or "")) == target)]
    props = schema.get("object_properties") or []
    schema["object_properties"] = [
        p for p in props
        if not (isinstance(p, dict)
                and (normalize_label(str(p.get("domain") or "")) == target
                     or normalize_label(str(p.get("range") or "")) == target))]
    for c in schema["classes"]:
        if isinstance(c, dict) and normalize_label(str(c.get("sub_class_of") or "")) == target:
            c["sub_class_of"] = None
    # 画布：类节点及其实例节点 + 触碰边
    doomed = set()
    for n in list(_nodes(gd)):
        d = _node_data(n)
        lbl = normalize_label(str(d.get("label") or ""))
        if (_is_class(n) and lbl == target) or (
                not _is_class(n) and normalize_label(str(d.get("class_label") or "")) == target):
            doomed.add(str(n.get("id")))
    for nid in doomed:
        _remove_node_and_touching_edges(_nodes(gd), _edges(gd), nid)


def _mutate_merge_classes(gd: dict, targets: dict) -> None:
    frm = {normalize_label(str(x or "")) for x in (targets.get("from") or [])} - {""}
    into = normalize_label(str(targets.get("into") or ""))
    schema = gd.setdefault("schema", {})
    classes = schema.get("classes") or []
    into_label = next((str(c.get("label")) for c in classes if isinstance(c, dict)
                       and normalize_label(str(c.get("label") or "")) == into), None)
    if into_label is None:
        raise ValueError(f"合并目标类不存在：{targets.get('into')}")
    # schema：from 类的实例归入 into、属性端点改指 into、sub_class_of 引用改指 into，from 条目删除
    schema["classes"] = [c for c in classes
                         if not (isinstance(c, dict)
                                 and normalize_label(str(c.get("label") or "")) in frm)]
    for p in schema.get("object_properties") or []:
        if not isinstance(p, dict):
            continue
        if normalize_label(str(p.get("domain") or "")) in frm:
            p["domain"] = into_label
        if normalize_label(str(p.get("range") or "")) in frm:
            p["range"] = into_label
    for c in schema["classes"]:
        if isinstance(c, dict) and normalize_label(str(c.get("sub_class_of") or "")) in frm:
            c["sub_class_of"] = into_label
    # 画布：实例 class_label 改指 into，instance_of 边改指 into 类节点，from 类节点删除
    into_node = next((n for n in _nodes(gd) if _is_class(n)
                      and normalize_label(str(_node_data(n).get("label") or "")) == into), None)
    frm_nodes = {str(n.get("id")) for n in _nodes(gd) if _is_class(n)
                 and normalize_label(str(_node_data(n).get("label") or "")) in frm}
    for n in _nodes(gd):
        d = _node_data(n)
        if not _is_class(n) and normalize_label(str(d.get("class_label") or "")) in frm:
            d["class_label"] = into_label
            n["data"] = d
    for e in _edges(gd):
        if str(e.get("target")) in frm_nodes and into_node is not None:
            e["target"] = into_node.get("id")
    for nid in frm_nodes:
        _remove_node_and_touching_edges(_nodes(gd), _edges(gd), nid)


def _mutate_add_class(gd: dict, targets: dict) -> None:
    import secrets

    label = str(targets.get("label") or "").strip()
    parent = str(targets.get("sub_class_of") or "").strip() or None
    schema = gd.setdefault("schema", {})
    schema.setdefault("classes", []).append(
        {"id": f"manual_{secrets.token_hex(4)}", "label": label,
         "sub_class_of": parent, "parent": parent})  # parent：diff 版本对比读这个键
    _nodes(gd).append({"id": f"manual_{secrets.token_hex(6)}",
                       "data": {"label": label, "type": "owl:Class", "properties": {}}})


def _mutate_add_relation(gd: dict, targets: dict) -> None:
    import secrets

    schema = gd.setdefault("schema", {})
    schema.setdefault("object_properties", []).append({
        "id": f"manual_{secrets.token_hex(4)}",
        "label": str(targets.get("predicate") or "").strip(),
        "domain": str(targets.get("domain") or "").strip(),
        "range": str(targets.get("range") or "").strip(),
        "functional": bool(targets.get("functional")),
    })


def _mutate_edit_relation(gd: dict, targets: dict) -> None:
    """B24 契约：{subject, predicate, object, new_predicate} —— 关系改名（TBox 权威键 +
    画布同名边标签同步）。new_predicate 缺省视为无可执行变更。"""
    pred = normalize_label(str(targets.get("predicate") or ""))
    new_pred = str(targets.get("new_predicate") or "").strip()
    if not new_pred:
        raise ValueError("edit_relation 缺少 new_predicate（关系改名目标）")
    schema = gd.setdefault("schema", {})
    hit = 0
    for p in schema.get("object_properties") or []:
        if isinstance(p, dict) and normalize_label(str(p.get("label") or "")) == pred:
            p["label"] = new_pred
            hit += 1
    if not hit:
        raise ValueError(f"对象属性不存在：{targets.get('predicate')}")
    for e in _edges(gd):
        if _edge_relation(e) and normalize_label(_edge_relation(e)) == pred:
            d = e.get("data") if isinstance(e.get("data"), dict) else {}
            d["relation"] = new_pred
            e["data"] = d
            e["label"] = new_pred


def _mutate_delete_instance(gd: dict, targets: dict) -> None:
    label = normalize_label(str(targets.get("instance") or ""))
    for n in list(_nodes(gd)):
        if not _is_class(n) and normalize_label(str(_node_data(n).get("label") or "")) == label:
            _remove_node_and_touching_edges(_nodes(gd), _edges(gd), n.get("id"))


def _mutate_edit_instance(gd: dict, targets: dict) -> None:
    label = normalize_label(str(targets.get("instance") or ""))
    for n in _nodes(gd):
        if _is_class(n) or normalize_label(str(_node_data(n).get("label") or "")) != label:
            continue
        d = _node_data(n)
        if targets.get("new_label"):
            d["label"] = str(targets["new_label"]).strip()
        if isinstance(targets.get("properties"), dict):
            props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
            props.update(targets["properties"])
            d["properties"] = props
        n["data"] = d
        return
    raise ValueError(f"实例不存在：{targets.get('instance')}")


def _mutate_edit_class(gd: dict, targets: dict) -> None:
    """S6：类属性编辑——新增/修改数据属性（值可为空串=先建槽位）、移除属性、改名。
    与画布「属性编辑」抽屉同口径（node.data.properties，name→value）。
    S8：属性必须带数据类型——同步 node.data.property_definitions（name/data_type/description）
    与 schema.classes[].properties（stats 与版本 diff 的数据源），改属性后时间轴才看得到。"""
    label = normalize_label(str(targets.get("class") or ""))
    new_label = str(targets.get("new_label") or "").strip()
    set_props = targets.get("set_properties") if isinstance(targets.get("set_properties"), dict) else {}
    rm_props = targets.get("remove_properties") if isinstance(targets.get("remove_properties"), list) else []
    prop_types = targets.get("property_types") if isinstance(targets.get("property_types"), dict) else {}

    def _cls_entry(schema: dict, lbl: str) -> dict | None:
        return next((c for c in schema.get("classes") or []
                     if isinstance(c, dict)
                     and normalize_label(str(c.get("label") or "")) == lbl), None)

    for n in _nodes(gd):
        if not _is_class(n) or normalize_label(str(_node_data(n).get("label") or "")) != label:
            continue
        d = _node_data(n)
        if new_label:
            d["label"] = new_label
        if set_props:
            props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
            props.update(set_props)
            d["properties"] = props
            # property_definitions 槽位定义（含数据类型）：已有定义保类型与描述，新属性取 property_types/缺省 string
            defs = d.get("property_definitions") if isinstance(d.get("property_definitions"), list) else []
            for pname in set_props:
                ptype = str(prop_types.get(pname) or "").strip()
                hit = next((x for x in defs
                            if isinstance(x, dict)
                            and normalize_label(str(x.get("name") or "")) == normalize_label(pname)), None)
                if hit is not None:
                    if ptype:
                        hit["data_type"] = ptype
                    if pname != hit.get("name"):
                        hit["name"] = pname
                else:
                    defs.append({"name": pname,
                                 "data_type": ptype or "string", "description": ""})
            d["property_definitions"] = defs
        if rm_props:
            props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
            rm_norm = {normalize_label(str(p)) for p in rm_props}
            for key in [k for k in props if normalize_label(str(k)) in rm_norm]:
                props.pop(key, None)
            d["properties"] = props
            defs = d.get("property_definitions")
            if isinstance(defs, list):
                d["property_definitions"] = [x for x in defs
                                             if not (isinstance(x, dict)
                                                     and normalize_label(str(x.get("name") or "")) in rm_norm)]
        n["data"] = d

        # schema 层同步（stats.properties 与版本 diff 的数据源）
        schema = gd.setdefault("schema", {})
        entry = _cls_entry(schema, label)
        if entry is None:
            entry = {"label": str(_node_data(n).get("label") or "")}
            schema.setdefault("classes", []).append(entry)
        if new_label:
            entry["label"] = new_label
        if set_props or rm_props:
            sprops = [p for p in (entry.get("properties") or []) if isinstance(p, dict)]
            for p in rm_props:
                sprops = [p for p in sprops
                          if normalize_label(str(p.get("name") or "")) not in
                          {normalize_label(str(x)) for x in rm_props}]
            for pname in set_props:
                ptype = str(prop_types.get(pname) or "").strip() or "string"
                hit = next((p for p in sprops
                            if normalize_label(str(p.get("name") or "")) == normalize_label(pname)), None)
                if hit is not None:
                    if ptype:
                        hit["data_type"] = ptype
                    hit["name"] = pname
                else:
                    sprops.append({"name": pname, "data_type": ptype})
            entry["properties"] = sprops
        return
    raise ValueError(f"类不存在：{targets.get('class')}")


def _mutate_add_instance(gd: dict, targets: dict) -> None:
    """S7：新增实例——owl:NamedIndividual 节点 + instance_of 边指向所属类节点，
    与画布「添加实例」/实例抽取写出口径一致（data.class_label 归属类）。"""
    import secrets

    label = str(targets.get("label") or "").strip()
    cls_lbl = str(targets.get("class") or "").strip()
    cls_node = next((n for n in _nodes(gd) if _is_class(n)
                     and normalize_label(str(_node_data(n).get("label") or ""))
                     == normalize_label(cls_lbl)), None)
    if cls_node is None:
        raise ValueError(f"所属类不存在：{cls_lbl}")
    nid = f"manual_{secrets.token_hex(6)}"
    props = dict(targets.get("properties") or {})
    _nodes(gd).append({"id": nid,
                       "data": {"label": label, "type": "owl:NamedIndividual",
                                "class_label": str(_node_data(cls_node).get("label") or ""),
                                "properties": props}})
    _edges(gd).append({"id": f"manual_{secrets.token_hex(6)}", "source": nid,
                       "target": cls_node.get("id"), "label": "instance_of"})


_MUTATORS = {
    "delete_class": _mutate_delete_class,
    "merge_classes": _mutate_merge_classes,
    "add_class": _mutate_add_class,
    "add_relation": _mutate_add_relation,
    "edit_relation": _mutate_edit_relation,
    "delete_instance": _mutate_delete_instance,
    "edit_instance": _mutate_edit_instance,
    "edit_class": _mutate_edit_class,
    "add_instance": _mutate_add_instance,
}


# ---------------------------------------------------------------- 版本号

def _latest_version_no(db: Session, project_id: int) -> int:
    row = (db.query(OntologyVersion.version_no)
           .filter(OntologyVersion.project_id == project_id)
           .order_by(OntologyVersion.version_no.desc()).first())
    return int(row[0]) if row else 0


# ---------------------------------------------------------------- 执行主流程

def apply_proposal(db: Session, project: Project, user: User,
                   proposal: CopilotProposal, confirm: bool = False) -> dict:
    """执行提案：闸门 → 内存变更 → 机检对比 → 提交/回滚。返回统一结果 dict。

    {"applied": True, "version_no": N, "message": ...}
    {"applied": False, "need_confirm": True}                    高风险未二次确认
    {"applied": False, "conflict": True, "message": ...}        画布并发冲突（提案转 expired）
    {"applied": False, "healed": True, "reason": ...}           机检自愈回滚（提案转 rejected）
    {"applied": False, "error": ...}                            状态/目标不合法
    """
    if proposal.status != "pending":
        return {"applied": False, "error": f"提案已处理（{proposal.status}），不能重复执行"}
    kind = proposal.kind
    if kind not in _MUTATORS and kind != "declare_axiom":
        return {"applied": False, "error": f"不支持的提案类型：{kind}"}
    if kind in HIGH_RISK_KINDS and not confirm:
        return {"applied": False, "need_confirm": True, "risk_tier": proposal.risk_tier,
                "message": "高风险操作（删除/合并会级联影响实例与关系），请二次确认后执行"}

    # 并发冲突闸：提案生成时的画布版本 ≠ 当前版本 → 画布已被编辑，拒绝并提示刷新
    latest = _latest_version_no(db, project.id)
    if proposal.base_version is not None and int(proposal.base_version) != latest:
        proposal.status = "expired"
        proposal.result = {"reason": f"画布已更新（当前 v{latest}，提案基于 v{proposal.base_version}）",
                           "conflict": True}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(proposal, "result")
        db.commit()
        return {"applied": False, "conflict": True,
                "message": f"画布已更新（当前 v{latest}，提案基于 v{proposal.base_version}），"
                           "请刷新画布后重新生成提案"}
    # 超期闸：会话内长时间未处理即 expired（created_at 为 DB 本地 naive 时间，用本地 now 对比）
    created = proposal.created_at
    if created is not None:
        if datetime.now() - created > timedelta(hours=PROPOSAL_TTL_HOURS):
            proposal.status = "expired"
            proposal.result = {"reason": f"提案超过 {PROPOSAL_TTL_HOURS} 小时未处理，已过期"}
            db.commit()
            return {"applied": False, "error": "提案已过期，请重新生成"}

    gd = project.graph_data if isinstance(project.graph_data, dict) else {}
    snapshot = copy.deepcopy(gd)
    targets = proposal.targets or {}

    base_sig = None
    if kind == "declare_axiom":
        base_sig = _owlrl_signature(gd)
    base_checks = _run_checks(gd)

    heal_reason = None
    try:
        if kind == "declare_axiom":
            from app.services.axiom_store import declare_axiom
            # B24 校验后的 targets：{"axiom_kind", "targets": [label...], "argument"?}
            # → axiom_store 入参键（subclass_of/disjoint_with/equivalent_classes 用 classes，
            #   functional/cardinality 用 predicate，inverse_of 用 predicates）
            ak = str(targets.get("axiom_kind") or "")
            tg = [str(x or "") for x in (targets.get("targets") or [])]
            kind_alias = "subclass_of" if ak == "sub_class_of" else ak
            payload = {"kind": kind_alias}
            if kind_alias in ("disjoint_with", "equivalent_classes", "subclass_of"):
                payload["classes"] = tg[:2]
            elif kind_alias == "inverse_of":
                payload["predicates"] = tg[:2]
            elif kind_alias in ("functional", "cardinality"):
                payload["predicate"] = tg[0] if tg else ""
                if kind_alias == "cardinality":
                    arg = int(targets.get("argument") or 1)
                    payload["min_card"] = arg
                    payload["max_card"] = arg
            result = declare_axiom(db, project, payload)
            if "skipped" in result:
                raise ValueError(str(result["skipped"]))
        else:
            _MUTATORS[kind](gd, targets)
    except Exception as exc:  # noqa: BLE001 —— 变更未提交，直接放弃
        project.graph_data = snapshot
        proposal.status = "rejected"
        proposal.result = {"reason": f"提案无法执行：{exc}"}
        db.commit()
        return {"applied": False, "error": f"提案无法执行：{exc}"}

    # 机检自愈：复算并与基线对比，新增违例即回滚（不提交，画布无残留）
    after = _run_checks(project.graph_data)
    reasons = []
    if after["schema_errors"] > base_checks["schema_errors"]:
        reasons.append(f"TBox 机检新增 {after['schema_errors'] - base_checks['schema_errors']} 个错误")
    if after["shacl_violations"] > base_checks["shacl_violations"]:
        reasons.append(f"SHACL 新增 {after['shacl_violations'] - base_checks['shacl_violations']} 个违例")
    if kind == "declare_axiom" and base_sig is not None:
        post_sig = _owlrl_signature(project.graph_data)
        if post_sig is not None and post_sig - base_sig:
            reasons.append("owlrl 一致性检查发现新逻辑矛盾（owl:Nothing）")
    if reasons:
        project.graph_data = snapshot
        proposal.status = "rejected"
        proposal.result = {"healed": True, "reason": "；".join(reasons)}
        db.commit()
        return {"applied": False, "healed": True, "reason": "；".join(reasons),
                "message": f"提案已自动回滚：{'；'.join(reasons)}"}

    # 提交：manual 打版（TTL 同步 + 版本快照 + 审计），提案状态同事务落定
    return _commit_apply(db, project, user, proposal, kind, latest)


def _commit_apply(db: Session, project: Project, user: User,
                  proposal: CopilotProposal, kind: str, base_latest: int) -> dict:
    from app.api.ontology import generate_ttl_from_graph_data
    from sqlalchemy.orm.attributes import flag_modified

    gd = project.graph_data or {}
    project.graph_data = gd
    flag_modified(project, "graph_data")
    project.ttl_content = generate_ttl_from_graph_data(
        gd.get("nodes") or [], gd.get("edges") or [])

    version_no = None
    try:
        from app.adapters.versioning import create_version
        ver = create_version(db, project, "manual", user.id,
                             label=f"Copilot提案：{kind}",
                             description=str(proposal.llm_reason or "")[:500])
        version_no = ver.version_no
    except Exception as exc:  # noqa: BLE001 —— 打版失败不阻断编辑（同 graph_edit 口径）
        logger.warning(f"[copilot-executor] 项目 {project.id} 提案打版失败: {exc}")

    from app.services.audit_service import log_action
    # B29：审计带 diff 摘要（谁/何时/哪个会话/依据什么/改了什么，一个入口可答）
    _diff = proposal.diff_preview
    if isinstance(_diff, dict):
        _diff_brief = "; ".join(
            f"{k}:{len(v) if isinstance(v, list) else v}" for k, v in _diff.items())[:200]
    elif isinstance(_diff, list):
        _diff_brief = f"{len(_diff)} 处改动"
    else:
        _diff_brief = ""
    log_action(db, user.id, "copilot.proposal.apply", "project", project.id,
               {"proposal_id": proposal.proposal_id, "session_id": proposal.session_id,
                "kind": kind, "risk_tier": proposal.risk_tier, "version_no": version_no,
                "diff_summary": _diff_brief,
                "reason": str(proposal.llm_reason or "")[:200]})

    proposal.status = "applied"
    proposal.decided_at = datetime.now()
    proposal.result = {"version_no": version_no, "base_version": base_latest}
    flag_modified(proposal, "result")
    db.commit()
    msg = (f"已生成 manual 版本 v{version_no}，可在版本时间轴回滚"
           if version_no else "提案已应用（版本快照不可用，画布已更新）")
    return {"applied": True, "version_no": version_no, "message": msg}


def reject_proposal(db: Session, proposal: CopilotProposal) -> dict:
    if proposal.status != "pending":
        return {"rejected": False, "error": f"提案已处理（{proposal.status}）"}
    proposal.status = "rejected"
    proposal.result = {"reason": "用户忽略"}
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(proposal, "result")
    db.commit()
    return {"rejected": True}
