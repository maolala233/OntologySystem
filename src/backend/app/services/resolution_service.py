# app/services/resolution_service.py - 消解/冲突业务编排（docs/design/04 §5/§6，M3-5）
# adapters 层保持纯函数；本服务层负责：行表装载、embedding 调用、review_items 物化去重。
# 被Celery 任务（extract 队列）与 API（merge/split/conflicts detect）共用。

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.adapters.conflicts import ConflictType, detect_conflicts
from app.adapters.resolution import (
    AUTO_MERGE_THRESHOLD,
    MANUAL_REVIEW_THRESHOLD,
    DuplicateCluster,
    detect_duplicates,
    merge_entities,
)
from app.core.logging import logger
from app.infrastructure.database import Entity, Project, Relation, ReviewItem

# 单次消解/冲突检测物化的审核项上限（防审核队列被一次大批次刷爆）
MAX_REVIEW_ITEMS_PER_TYPE = 50

_CONFLICT_ITEM_TYPE = {
    ConflictType.VALUE: "conflict_value",
    ConflictType.TYPE: "conflict_type",
    ConflictType.RELATIONSHIP: "conflict_relationship",
    ConflictType.TEMPORAL: "conflict_value",  # 04 §6：temporal 归入 conflict_value(kind=temporal)
    ConflictType.AXIOM: "conflict_axiom",     # 公理 2 期：TBox 公理违例转审核
    ConflictType.LOGICAL: "conflict_axiom",   # O2-B6：SHACL 违例与公理违例同一审核通道（kind=logical）
}


def load_active_entities(db: Session, project_id: int, scope: str = "all") -> list[Entity]:
    """参与消解的实体：非类节点、未合并；scope='class:X' 过滤单类。"""
    q = db.query(Entity).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(False),
        Entity.status != "merged",
    )
    if scope and scope.startswith("class:"):
        q = q.filter(Entity.class_label == scope.split(":", 1)[1])
    return q.all()


def _embeddings_fn(project_id: Optional[int] = None):
    """bge-m3 同步嵌入函数；provider 未配置/调用失败返回 None（消解退回拼音层）。"""
    try:
        from openai import OpenAI

        from app.adapters.provider import resolve_embedding
        from app.infrastructure.database import SessionLocal

        s = SessionLocal()
        try:
            resolved = resolve_embedding(project_id, db=s)
        finally:
            s.close()
        client = OpenAI(base_url=resolved.base_url, api_key=resolved.api_key or "EMPTY",
                        timeout=60)

        def _embed(texts: list[str]) -> list[list[float]]:
            # 耗时剖析：嵌入模型调用也计入 resolution 阶段（token 用量由端点返回）
            from app.services.pipeline_profiler import (
                begin_llm_call,
                finish_llm_call,
                note_llm_attempt,
                note_llm_response,
            )

            _scope = begin_llm_call(
                model=resolved.model_name, stream=False,
                system_prompt="", user_prompt="\n".join(texts),
                caller="resolution_service.embed")
            try:
                note_llm_attempt()
                resp = client.embeddings.create(input=texts, model=resolved.model_name)
                note_llm_response(resp)
                return [d.embedding for d in resp.data]
            finally:
                finish_llm_call(_scope)

        return _embed
    except Exception as e:  # noqa: BLE001 —— 向量层不可用不阻断消解
        logger.warning(f"[resolution] 向量层不可用，退回拼音层: {e}")
        return None


def _prop_key_merge(db: Session, project_id: int, ents: list) -> tuple[int, int]:
    """自然键合并层（外部 get_* 登记表思想的晚绑定版，2026-11-09 测试6）。

    同类实体共享同一唯一编码属性值（产品编码/接口编码/编号…）→ 确定性合并。
    跨文档场景：文档A按名称、文档B按编码提及同一对象（实测 P001 与 稳盈固收
    理财 props 全同却成两例）——相似度层（名称文本）对"名称↔编码"天然无能为力，
    编码属性是两文档共同背书的强证据。返回 (合并组数, 迁移关系数)。
    """
    import re as _re

    key_re = _re.compile(r"(编码|代码|编号)$")
    groups: dict[tuple[str, str], list] = {}
    for e in ents:
        props = e.props if isinstance(e.props, dict) else {}
        for k, v in props.items():
            if k and key_re.search(str(k)) and isinstance(v, (str, int, float)) \
                    and str(v).strip() and len(str(v).strip()) >= 2:
                groups.setdefault((e.class_label or "", f"{k}={str(v).strip()}"), []).append(e)
                break
    merged = migrated = 0
    specs: list[tuple[int, list[int]]] = []
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        # 规范实体：属性最丰富者优先（结构化文档产出的实体通常属性最全）
        members.sort(key=lambda e: (-len(e.props or {}), e.id))
        canon = members[0]
        rest = [m for m in members if m.id != canon.id]
        if not rest:
            continue
        res = merge_entities(canon.id, [m.id for m in rest], db=db)
        specs.append((canon.label, canon.class_label,
                      [(m.label, m.class_label) for m in rest]))
        merged += 1
        migrated += res.relations_migrated
    return merged, migrated, specs


def _class_family_subclass_edges(db: Session, project_id: int) -> int:
    """类归一（346 修复，2026-11-18）：类名碎片化的确定性收编——类 B 的类名以
    类 A 的类名结尾（前端接口/BP接口/交互接口 之于 接口）且 A 更短 → B ⊑ A
    子类边落行表（SUBCLASS_OF，is_class_edge）。命名约定下"XX接口"必是接口的
    下位类；接口输入字段/接口输出项不以"接口"结尾，天然排除。只加边不删类
    不改名（零破坏、可逆；计数口径的运行时补偿在 QA 类族合并，此处补图结构）。
    返回新增边数。"""
    classes = db.query(Entity).filter(
        Entity.project_id == project_id,
        Entity.is_class_node.is_(True),
        Entity.status != "rejected",
    ).all()
    # 同名类节点取 id 最小者作代表（类节点自身也可能重复）
    repr_by_label: dict[str, Entity] = {}
    for c in classes:
        cur = repr_by_label.get(c.label)
        if cur is None or c.id < cur.id:
            repr_by_label[c.label] = c
    existing = {
        (r.subject_id, r.object_id)
        for r in db.query(Relation).filter(
            Relation.project_id == project_id,
            Relation.predicate == "SUBCLASS_OF",
        ).all()
    }
    added = 0
    pairs: list[tuple[str, str]] = []
    for label_a, a in repr_by_label.items():
        if len(label_a) < 2:
            continue
        for label_b, b in repr_by_label.items():
            if label_a == label_b or len(label_b) <= len(label_a):
                continue
            if not label_b.endswith(label_a) or (b.id, a.id) in existing:
                continue
            db.add(Relation(project_id=project_id, subject_id=b.id,
                            predicate="SUBCLASS_OF", object_id=a.id,
                            is_class_edge=True, status="auto", confidence=0.9))
            existing.add((b.id, a.id))
            pairs.append((label_b, label_a))
            added += 1
    return added, pairs


def _sync_resolution_to_blob(db: Session, project_id: int,
                             subclass_pairs: list[tuple[str, str]],
                             merge_specs: list[tuple[str, str, list[tuple[str, str]]]] | None = None) -> int:
    """merge_specs：(canonical_label, canonical_class, [(dup_label, dup_class)])。
    用 (label, class) 稳定键而非行 id——行表每次 graph_data 写入都重建、id 漂移，
    id 引用会落到回收后的任意实体（346 实测误删 126 个接口实例节点）。"""
    """M4 blob 收敛（346 修复，2026-11-18）：行表是 graph_data blob 的投影
    （before_flush/after_flush 监听器：blob 一写就全量重建行表）——消解只改行表
    不回写 blob，任何后续 graph_data 写入（公理申报写 schema 等）都会把合并
    冲掉（346 实测：108 处合并被 AI 复核的公理申报吞掉）。

    终局一次性回写：
    1. merged 行沿 canonical_id 链归约到活跃代表 → blob 删除重复节点、
       边重指 canonical（同 (src,rel,dst) 去重）、属性/别名并集；
    2. 类族子类边（subclass_pairs: (子类label, 父类label)）追加 SUBCLASS_OF 边。
    返回处理的合并节点数；写后调用方需 flag_modified(graph_data)。
    """
    from app.services.graph_rows import entity_uri

    proj = db.query(Project).filter(Project.id == project_id).first()
    gd = proj.graph_data if proj is not None and isinstance(proj.graph_data, dict) else None
    if gd is None:
        return 0
    nodes = gd.get("nodes") or []
    edges = gd.get("edges") or []

    # 显式合并清单（346 修复）：本轮合并的 (canonical_id, [dup_ids]) 由调用方
    # 逐环节收集传入——旧实现扫描 status='merged' 全部行，行表 id 在多次图写入
    # 重建间漂移，链路落到回收后的任意实体 → blob 手术误删活跃节点（126 个）。
    merge_specs = merge_specs or []
    if not merge_specs and not subclass_pairs:
        return 0
    repr_uri: dict[str, str] = {}  # (dup_label, dup_class) → (canon_label, canon_class)
    for canon_lbl, canon_cls, dups in merge_specs:
        for d_lbl, d_cls in dups or []:
            repr_uri[(d_lbl, d_cls)] = (canon_lbl, canon_cls)

    node_by_bid = {str(n.get("id")): n for n in nodes if n.get("id")}
    inst = [n for n in nodes if (n.get("data") or {}).get("type") == "owl:NamedIndividual"]

    def _props_merge(dst: dict, src: dict) -> None:
        d = dst.setdefault("data", {})
        props = d.get("properties") if isinstance(d.get("properties"), dict) else {}
        for k, v in (src.get("properties") or {}).items() if isinstance(src.get("properties"), dict) else []:
            if k not in props or props[k] in (None, ""):
                props[k] = v
        d["properties"] = props
        al = d.get("aliases") or []
        for a in (src.get("aliases") or []):
            if a and a not in al:
                al.append(a)
        if al:
            d["aliases"] = al[:64]

    # blob 节点 (label, class_label) → node id（稳定键）
    lc_bid: dict[tuple[str, str], str] = {}
    for n in inst:
        d = n.get("data") or {}
        lc_bid[(str(d.get("label") or ""), str(d.get("class_label") or ""))] = str(n.get("id"))
    removed: set[str] = set()
    for (m_lbl, m_cls), (c_lbl, c_cls) in repr_uri.items():
        m_bid = lc_bid.get((m_lbl, m_cls))
        c_bid = lc_bid.get((c_lbl, c_cls))
        if not m_bid or not c_bid or m_bid == c_bid or m_bid not in node_by_bid:
            print(f"[blob手术-debug] 跳过: {m_lbl[:20]}({m_cls[:12]}) → {c_lbl[:20]} "
                  f"(m_bid={'无' if not m_bid else '有'}, c_bid={'无' if not c_bid else '有'})")
            continue
        _props_merge(node_by_bid[c_bid], node_by_bid[m_bid].get("data") or {})
        removed.add(m_bid)
        print(f"[blob手术-debug] 合并: {m_lbl[:20]}({m_cls[:12]}) → {c_lbl[:20]}({c_cls[:12]})")
    def _redirect(bid: str) -> str:
        """merged 节点的 bid → 活跃代表 bid；非 merged 原样返回（稳定键）。"""
        data = (node_by_bid.get(bid) or {}).get("data") or {}
        lbl_cls = (str(data.get("label") or ""), str(data.get("class_label") or ""))
        if bid in removed and lbl_cls in repr_uri:
            c_bid = lc_bid.get(repr_uri[lbl_cls])
            if c_bid:
                return c_bid
        return bid

    if removed:
        gd["nodes"] = [n for n in nodes if str(n.get("id")) not in removed]
        # 边重指 + 去重
        seen_e: set[tuple[str, str, str]] = set()
        new_edges: list = []
        for e in edges:
            s, t = str(e.get("source")), str(e.get("target"))
            s2, t2 = _redirect(s), _redirect(t)
            rel = (e.get("data") or {}).get("relation") if isinstance(e.get("data"), dict) else None
            rel = rel or e.get("label") or "related"
            key = (s2, str(rel), t2)
            if key in seen_e:
                continue
            seen_e.add(key)
            if s2 != s:
                e["source"] = s2
            if t2 != t:
                e["target"] = t2
            new_edges.append(e)
        gd["edges"] = new_edges

    # 类族子类边追加（blob 层）
    added_cls = 0
    if subclass_pairs:
        existing_cls = {(str(e.get("source")), str(e.get("target")))
                        for e in (gd.get("edges") or [])
                        if isinstance(e.get("data"), dict)
                        and (e["data"].get("relation") or e.get("label")) == "SUBCLASS_OF"}
        for child_lbl, parent_lbl in subclass_pairs:
            rows_ = db.query(Entity).filter(
                Entity.project_id == project_id,
                Entity.is_class_node.is_(True),
                Entity.label.in_([child_lbl, parent_lbl])).all()
            by_lbl: dict[str, Entity] = {}
            for c in rows_:
                if c.label not in by_lbl or c.id < by_lbl[c.label].id:
                    by_lbl[c.label] = c
            # (label, class) 稳定键查 blob 节点 id（bid_by_uri 已随 uri 体系移除）
            cb = lc_bid.get((by_lbl[child_lbl].label, by_lbl[child_lbl].class_label))
            pb = lc_bid.get((by_lbl[parent_lbl].label, by_lbl[parent_lbl].class_label))
            if not cb or not pb or cb == pb or (cb, pb) in existing_cls:
                continue
            gd.setdefault("edges", []).append({
                "source": cb, "target": pb, "label": "SUBCLASS_OF",
                "data": {"relation": "SUBCLASS_OF", "confidence": 0.9,
                         "source": "类族归一（消解阶段确定性收编）"}})
            existing_cls.add((cb, pb))
            added_cls += 1
    from sqlalchemy.orm.attributes import flag_modified as _fm

    _fm(proj, "graph_data")
    return len(removed) + added_cls



def _label_prop_bridge_merge(db: Session, project_id: int, ents: list) -> tuple[int, int]:
    """【已退役 2026-11-19】跨形态桥接合并：其目标问题（展开=服务标识/LLM=服务名称
    的双形态实例）已由"映射命中切片跳过 LLM 实例抽取"在源头根除——表格切片的
    实体唯一来源是确定性展开，双形态不再产生。且本层在混合文档上有误配风险
    （346 实测：接口的描述属性值撞输入项字段名，C250405Y 被误并进信用保障措施）。
    保留空实现以维持调用点兼容；若未来叙事/表格混合需要精确挂接，须走
    LLM 辅助配对审计而非属性值精确匹配。"""
    return 0, 0


def run_resolution(project_id: int, scope: str = "all",
                   thresholds: Optional[dict] = None,
                   blocking: str = "pinyin",
                   db: Optional[Session] = None) -> dict:
    """三层消解执行：自动合并 + 待审聚类（review_items entity_merge）。

    可同步调用（API merge 的小规模场景）或由 Celery 任务调用。
    """
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        auto_th = float((thresholds or {}).get("auto", AUTO_MERGE_THRESHOLD))
        review_th = float((thresholds or {}).get("manual", MANUAL_REVIEW_THRESHOLD))
        ents = load_active_entities(db, project_id, scope)
        prop_key_merged, prop_key_migrated = (0, 0)
        bridge_merged, bridge_migrated = (0, 0)
        merge_specs: list[tuple[int, list[int]]] = []  # (canonical_id, [dup_ids])——blob 收敛显式清单
        if len(ents) >= 2:
            prop_key_merged, prop_key_migrated, pk_specs = _prop_key_merge(db, project_id, ents)
            merge_specs.extend(pk_specs)
            if prop_key_merged:
                db.commit()
                ents = load_active_entities(db, project_id, scope)
            # 跨形态桥接层已退役（双形态源头已根除：确定性展开唯一来源 + 谓词分离
            # 护栏后无合法桥接场景；混合文档误配 C250405Y→信用保障措施 事故层），
            # 不再调用 _label_prop_bridge_merge
        if len(ents) < 2:
            return {"clusters_total": 0, "auto_merged": 0, "review_created": 0,
                    "prop_key_merged": prop_key_merged,
                    "relations_migrated": prop_key_migrated,
                    "entities_scanned": len(ents)}
        clusters = detect_duplicates(
            ents, blocking=blocking, embeddings_fn=_embeddings_fn(project_id),
            auto_threshold=auto_th, review_threshold=review_th)

        relations_migrated = 0
        auto_merged = 0
        review_created = 0
        for c in clusters:
            if c.needs_review or c.canonical_id is None or len(c.member_ids) < 2:
                continue
            rest = [i for i in c.member_ids if i != c.canonical_id]
            res = merge_entities(c.canonical_id, rest, db=db)
            dups_lc = [(lbl, c.class_label) for lbl in c.member_labels
                       if lbl != c.canonical_label]
            merge_specs.append((c.canonical_label, c.class_label, dups_lc))
            relations_migrated += res.relations_migrated
            auto_merged += 1
        for c in clusters:
            if not c.needs_review:
                continue
            if _pending_merge_item_exists(db, project_id, c):
                continue
            # 成员类映射（AI 复核素材）：跨类同名项 LLM 判定需要看各类语义
            member_classes = {}
            try:
                _mrows = (db.query(Entity.label, Entity.class_label)
                          .filter(Entity.id.in_(c.member_ids)).all())
                member_classes = {lbl: cl for lbl, cl in _mrows}
            except Exception:  # noqa: BLE001 —— 素材缺失不阻断项创建
                member_classes = {}
            db.add(ReviewItem(
                project_id=project_id,
                item_type="entity_merge",
                payload={"cluster": c.model_dump(),
                         "layer": c.layer, "member_classes": member_classes},
                reason=f"相似度 {c.similarity} 落在人工复核区间（{c.layer} 层）",
                priority="medium",
                status="pending",
                suggested_action={"action": "merge", "canonical_id": c.canonical_id,
                                  "duplicate_ids": [i for i in c.member_ids
                                                    if i != c.canonical_id]},
            ))
            review_created += 1
        subclass_added, subclass_pairs = _class_family_subclass_edges(db, project_id)
        db.commit()
        # M4 blob 收敛（346 修复）：合并/类边回写 graph_data——行表是 blob 的投影，
        # 不回写则任何后续 blob 写入（公理申报写 schema 等）触发行表重建时会把
        # 合并冲掉。放主事务提交后的独立短事务：监听器在另会话重建行表，与主事务
        # 未释放的行锁串行冲突会锁等待超时（1205，346 实测）。
        blob_n = 0
        try:
            blob_n = _sync_resolution_to_blob(db, project_id, subclass_pairs,
                                              merge_specs=merge_specs)
            try:
                band_n = _link_band_table_parents(db, project_id)
                if band_n:
                    logger.info(f"[resolution] 带表父子挂接：新增 {band_n} 条包含边")
            except Exception as _e:
                logger.warning(f"[resolution] 带表父子挂接失败（跳过）: {_e}")
            db.commit()  # 触发 after_flush 监听器：行表按新 blob 重建 + outbox 事件
        except Exception as e:  # noqa: BLE001 —— 收敛失败行表仍是合并态，可对账重放
            db.rollback()
            logger.warning(f"[resolution] blob 收敛失败（行表保持已合并态）: {e}")
        if blob_n:
            logger.info(f"[resolution] blob 收敛完成：{blob_n} 个节点合并/类边回写 graph_data")
        return {"clusters_total": len(clusters), "auto_merged": auto_merged,
                "review_created": review_created,
                "prop_key_merged": prop_key_merged,
                "bridge_merged": bridge_merged,
                "subclass_edges_added": subclass_added,
                "blob_nodes_merged": blob_n,
                "relations_migrated": relations_migrated + prop_key_migrated + bridge_migrated,
                "entities_scanned": len(ents)}
    except Exception:
        if own:
            db.rollback()
        raise
    finally:
        if own:
            db.close()


def _pending_merge_item_exists(db: Session, project_id: int, cluster: DuplicateCluster) -> bool:
    """同聚类（成员标签集合一致）的待审项已存在 → 不重复生成。

    按成员**归一化标签**比对而不是 member_ids：graph_data 每次写入都会触发
    行表监听器删重建（id 全换），按 id 去重必然失效 → 每轮重抽/重扫都堆一条
    重复聚类项（项目 245 实测 #592/#644 同簇两条）。
    """
    from app.adapters.resolution import normalize_label

    rows = db.query(ReviewItem).filter(
        ReviewItem.project_id == project_id,
        ReviewItem.item_type == "entity_merge",
        ReviewItem.status == "pending",
    ).all()
    want = {normalize_label(str(lb)) for lb in (cluster.member_labels or [])}
    for r in rows:
        got = {normalize_label(str(lb)) for lb in
               ((r.payload or {}).get("cluster", {}).get("member_labels") or [])}
        if got and got == want:
            return True
    return False


def run_conflict_detection(project_id: int, db: Optional[Session] = None,
                           types: Optional[list[str]] = None) -> dict:
    """冲突检测 + review_items 物化（04 §6：消解之后运行，减少假冲突）。"""
    own = db is None
    if own:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
    try:
        ents = db.query(Entity).filter(
            Entity.project_id == project_id,
            Entity.is_class_node.is_(False),
            Entity.status != "merged",
        ).all()
        rels = db.query(Relation).filter(Relation.project_id == project_id).all()
        # 行表 relation 无 label 列：用全量实体索引（含类节点/已合并）富化主客体标签，
        # 否则按客体聚合的检测（RELATIONSHIP/AXIOM 函数性、基数）全部空转
        entity_index = {e.id: {"uri": str(e.uri or ""), "label": e.label or "",
                               "class_label": e.class_label or ""}
                        for e in db.query(Entity).filter(Entity.project_id == project_id).all()}
        rel_dicts = [{"subject_id": r.subject_id, "predicate": r.predicate,
                      "object_id": r.object_id,
                      "subject_label": (entity_index.get(r.subject_id) or {}).get("label", ""),
                      "object_label": (entity_index.get(r.object_id) or {}).get("label", "")}
                     for r in rels]
        object_properties = {}
        proj = db.query(Project).filter(Project.id == project_id).first()
        schema = (proj.graph_data or {}).get("schema") if proj else None
        if isinstance(schema, dict):
            object_properties = {op.get("label"): op for op in
                                 (schema.get("object_properties") or [])
                                 if isinstance(op, dict) and op.get("label")}
        else:
            schema = None
        want_types = tuple(
            t for t in (ConflictType(x) for x in
                        (types or ["value", "type", "relationship", "axiom", "logical"])))
        records = detect_conflicts(ents, rel_dicts, types=want_types,
                                   object_properties=object_properties, schema=schema,
                                   entity_index=entity_index)

        # 物化去重：同 entity_uri+property+values 的待审冲突项不重复生成
        existing = db.query(ReviewItem).filter(
            ReviewItem.project_id == project_id,
            ReviewItem.item_type.in_(["conflict_value", "conflict_type",
                                      "conflict_relationship", "conflict_axiom"]),
            ReviewItem.status == "pending",
        ).all()
        seen_keys = {
            ((r.payload or {}).get("entity_uri"), (r.payload or {}).get("property_name"),
             tuple(sorted((r.payload or {}).get("conflicting_values") or [])))
            for r in existing}
        created = 0
        skipped = 0
        per_type_count: dict[str, int] = {}
        for rec in records:
            item_type = _CONFLICT_ITEM_TYPE[rec.conflict_type]
            key = (rec.entity_uri, rec.property_name, tuple(sorted(rec.conflicting_values)))
            if key in seen_keys:
                skipped += 1
                continue
            if per_type_count.get(item_type, 0) >= MAX_REVIEW_ITEMS_PER_TYPE:
                skipped += 1
                continue
            per_type_count[item_type] = per_type_count.get(item_type, 0) + 1
            payload = rec.model_dump()
            if rec.conflict_type == ConflictType.TEMPORAL:
                payload["kind"] = "temporal"
            db.add(ReviewItem(
                project_id=project_id,
                item_type=item_type,
                payload=payload,
                reason=f"{rec.conflict_type.value} 冲突：{rec.property_name or ''}",
                priority="critical" if rec.severity == "critical" else
                         ("high" if rec.severity == "high" else "medium"),
                status="pending",
                suggested_action={"action": rec.recommended_action.value},
            ))
            seen_keys.add(key)
            created += 1
        db.commit()
        return {"detected": len(records), "review_created": created, "deduped": skipped,
                "entities_scanned": len(ents), "relations_scanned": len(rels)}
    except Exception:
        if own:
            db.rollback()
        raise
    finally:
        if own:
            db.close()
