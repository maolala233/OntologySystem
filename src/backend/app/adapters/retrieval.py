# app/adapters/retrieval.py - GraphRAG 检索与问答（docs/design/07 §4）
# M5 实装：向量召回（Milvus，项目/知识域过滤表达式）+ 图扩展（行表 relations BFS）
# + proximity 融合 + 溯源引用（provenance_records evidence/char 定位）
# + LLM 接地答案（非流式 QAResult / 流式 token 生成器）。
# 本模块为 03 §15 新 QA 契约的后端（旧 services/rag_engine.py 已于 O1-B4 删除）。

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.infrastructure.database import Entity, ProvenanceRecord, Project, ReasoningResult, Relation, UploadedDocument
from app.infrastructure.llm_client import LLMClient
from app.infrastructure.vector_client import VectorStoreManager

logger = logging.getLogger(__name__)

# 单响应引用上限（07 §3.3：防超大响应；问答引用最多 10 条与旧链路一致）
MAX_SOURCES = 10
# 图事实在融合结果中的名额上限：结构化三元组仅作关系类问题的补充，
# 原文切片（向量/关键词）才是内容问答的主要证据来源
MAX_GRAPH_FACTS = 5  # 聚合引用（同主体谓词合并为一条清单）后，5 条足以覆盖枚举问题
# 语义推理引用名额上限：推理结果是推导产物（非已确认事实），只做补充证据，
# 不得挤占事实类引用；引用卡与 LLM 上下文中都带"语义推理"标注。
# 事实满额时为推理保留 2 个名额（事实压缩到 MAX_SOURCES-2）
MAX_INFERRED_SOURCES = 4
MAX_INFERRED_RESERVED = 2
# 已声明公理引用名额：公理是审核通过的结构约束（互斥/传递/函数性等），
# 问题命中相关类/关系名时才注入，属高信任约束证据（与事实同级）
MAX_AXIOM_SOURCES = 4


class RetrievedSource(BaseModel):
    """问答引用（[n] 标注的数据载体，char 定位供前端高亮）。"""

    doc_file: str
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    ref_type: str = "vector_chunk"  # vector_chunk | graph_edge | graph_node | keyword_chunk | inference | axiom
    score: float = 0.0
    source_document_id: int | None = None  # 前端跳文档预览用


class QAResult(BaseModel):
    answer: str
    sources: list[RetrievedSource] = Field(default_factory=list)
    reasoning_path: list[str] = Field(default_factory=list)
    # 检索过程卡片（前端逐步展示"怎么得到这个结果"）：每次查询动态不同
    retrieval_trace: list[dict] = Field(default_factory=list)
    confidence: float = 0.0
    model: str | None = None
    latency_ms: int = 0
    # O3-B15 QA 自修复：失败检测→换策略重试 ≤2 轮；repair_rounds>0 即发生过修复
    repair_rounds: int = 0
    failed: bool = False


class RetrievalQuery(BaseModel):
    project_id: int
    question: str
    top_k: int = 12
    use_graph: bool = True
    use_inferred: bool = True  # 是否注入语义推理引用（来源标注区分，不与事实混排）
    max_expansion_hops: int = 2
    knowledge_domain: str | None = None  # 向量过滤表达式（权限隔离强制 project_id）


def _milvus_expr(query: RetrievalQuery) -> str:
    """权限过滤表达式：**强制** project_id == 项目（07 §4 权限过滤），可选知识域。"""
    expr = f"project_id == {int(query.project_id)}"
    if query.knowledge_domain:
        # Milvus 表达式字符串只能用双引号；滤掉引号防注入
        domain = str(query.knowledge_domain).replace('"', "").replace("\\", "")
        if domain:
            expr += f' and domain == "{domain}"'
    return expr


def _match_entities(db: Session, query: RetrievalQuery) -> list[Entity]:
    """问题与候选实体加权匹配（EvoOntology browse 打分思想）：
    label/别名整词命中（5+，长名更特异加分）> 属性值命中（4，编码/编号级锚点）。
    此前无差别子串命中，"PCOPCM 的评级"这类按编码提问永远引不上图谱。"""
    q = query.question.strip()
    if not q:
        return []
    ents = (
        db.query(Entity)
        .filter(
            Entity.project_id == query.project_id,
            Entity.is_class_node.is_(False),
            Entity.status != "rejected",
        )
        .all()
    )
    ql = q.lower()
    scored: list[tuple[float, int, Entity]] = []
    for e in ents:
        best = 0.0
        for name in [e.label] + list(e.aliases or []):
            n = (name or "").strip().lower()
            if len(n) >= 2 and n in ql:
                best = max(best, 5.0 + min(len(n), 20) / 20.0)
        if best == 0.0:
            # 属性值锚点：值长 ≥4（排除"已接入"/评级"A"这类低区分度值）
            for v in (e.props or {}).values():
                s = str(v).strip().lower()
                if len(s) >= 4 and s in ql:
                    best = 4.0
                    break
        if best > 0:
            scored.append((best, len(e.label), e))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [e for _s, _l, e in scored[:8]]


def _match_class_labels(db: Session, query: RetrievalQuery) -> list[str]:
    """类锚定（词法）：问题文本命中 TBox 类节点的 label/别名（≥2 字符）。
    解决"接入的系统有哪些"这类枚举问题——图谱事实路按实体名锚定，永远接不到它。"""
    q = query.question.strip()
    if not q:
        return []
    classes = (
        db.query(Entity)
        .filter(
            Entity.project_id == query.project_id,
            Entity.is_class_node.is_(True),
            Entity.status != "rejected",
        )
        .all()
    )
    ql = q.lower()
    scored: list[tuple[float, str]] = []
    for c in classes:
        for name in [c.label] + list(c.aliases or []):
            n = (name or "").strip().lower()
            if len(n) >= 2 and n in ql:
                scored.append((min(len(n), 20) / 20.0, c.label))
                break
    scored.sort(key=lambda t: t[0], reverse=True)
    return [label for _s, label in scored[:2]]


def _cls_doc_dist(gd: dict | None) -> dict[str, dict[str, int]]:
    """graph_data 节点 → {类: {来源文件: 实例数, "__total": 总数}}。
    通用口径：只认实例 source_quote 的文件前缀（认出处，不认业务词）——
    MVP 直落项目行表无溯源，从这里取"每类来自哪些文档各多少"的构成素材，
    供 QA 回答"152 个来自基线、30 个来自梳理"式的人话拆解。"""
    out: dict[str, dict[str, int]] = {}
    for n in (gd or {}).get("nodes") or []:
        d = n.get("data") or {}
        if d.get("type") != "owl:NamedIndividual":
            continue
        cl = str(d.get("class_label") or "").strip()
        if not cl:
            continue
        bucket = out.setdefault(cl, {"__total": 0})
        bucket["__total"] += 1
        sq = str(d.get("source_quote") or "").strip()
        if not sq:
            continue
        fn = re.split(r"[\[：:]", sq, 1)[0].strip()
        if fn.startswith("文件"):
            fn = fn[2:].strip()
        if fn:
            bucket[fn] = bucket.get(fn, 0) + 1
    return out


def _retrieval_trace(sources: list[RetrievedSource], question: str) -> list[dict]:
    """检索过程卡片数据：按"查什么→拿到什么"分步组织本次引用（每次查询动态不同）。
    步骤按检索阶段划分（通用，认 ref_type 不认业务词）；refs=引用编号（与回答 [n] 一致），
    sample=该步代表内容（截 80 字），前端卡片逐步渲染并可定位引用。"""
    groups: dict[str, list[int]] = {}
    for i, s in enumerate(sources):
        groups.setdefault(s.ref_type, []).append(i + 1)
    step_def = [
        ("tool_query", "查工具 · query_facts",
         "确定性清单/计数查询，返回 total 与翻页游标"),
        ("graph_node", "查图谱 · 类与实例清单", "从本体图谱查到类锚定清单与全图分布概览"),
        ("axiom", "查规则/公理 · 结构约束", "从本体公理查到定义域/值域/互斥等约束"),
        ("graph_edge", "查图谱 · 关系事实", "从图谱关系边查到实例间的关联事实"),
        ("inference", "语义推理 · 公理推导", "从公理/规则推导出的结论"),
        ("vector_chunk", "查向量库 · 原文切片", "语义相似召回的原文切片"),
        ("keyword_chunk", "查关键词索引 · 原文切片", "关键词命中的原文切片"),
    ]
    trace: list[dict] = []
    for ref_type, stage, what in step_def:
        ids = groups.pop(ref_type, [])
        if not ids:
            continue
        # 工具步骤要把 query_facts(...) 的调用参数/总数露出来；普通步骤 80 字够摘要
        sample = str(sources[ids[0] - 1].quote or "")[:160 if ref_type == "tool_query" else 80]
        trace.append({"stage": stage, "what": what, "count": len(ids),
                      "refs": ids, "sample": sample})
    for rt, ids in groups.items():   # 未登记的通道兜底展示（不丢）
        if ids:
            trace.append({"stage": rt, "what": "其它检索通道", "count": len(ids),
                          "refs": ids, "sample": str(sources[ids[0] - 1].quote or "")[:80]})
    trace.append({"stage": "大模型综合", "what": "融合全部引用接地生成答案",
                  "count": len(sources), "refs": list(range(1, len(sources) + 1)),
                  "sample": "结论先行 + 构成拆解 + [n] 溯源标注"})
    return trace


def _prop_distribution_sources(db: Session, query: RetrievalQuery) -> list[RetrievedSource]:
    """属性分布聚合引用（349 修复）：计数类问题的决定性证据通道。

    长表（89 行）的切片引用只含前 ~20 行，模型按"可见行数"计数必错
    （349 实测：真值 38 已接入答成 20/15）。本通道对实例数 top 类做
    低基数属性分布聚合（"接入情况：已接入 38、未接入 51"），独立于类锚定——
    问题不含类名（"有多少个系统…"）也能命中。仅问题含计数词时触发。
    """
    if not re.search(r"多少|几个|数量|占比|比例|统计|哪些|哪几|列出|清单|名单",
                      query.question or ""):
        return []
    try:
        top = (db.query(Entity.class_label, func.count(Entity.id))
               .filter(Entity.project_id == query.project_id,
                       Entity.is_class_node.is_(False),
                       Entity.status == "auto",
                       Entity.class_label.isnot(None))
               .group_by(Entity.class_label)
               .order_by(func.count(Entity.id).desc())
               .limit(6).all())
        out: list[RetrievedSource] = []
        for label, n in top:
            if n < 2:
                continue
            insts = (db.query(Entity)
                     .filter(Entity.project_id == query.project_id,
                             Entity.is_class_node.is_(False),
                             Entity.status == "auto",
                             Entity.class_label == label)
                     .all())
            prop_keys: dict[str, dict[str, int]] = {}
            for e in insts:
                for k, v in (e.props or {}).items():
                    v = str(v or "").strip()
                    if v:
                        prop_keys.setdefault(str(k), {})
                        prop_keys[str(k)][v] = prop_keys[str(k)].get(v, 0) + 1
            dist_parts = []
            for k, cnt in sorted(prop_keys.items(),
                                 key=lambda kv: -sum(kv[1].values()))[:12]:
                total = sum(cnt.values())
                distinct = len(cnt)
                if 2 <= distinct <= 20 and total >= max(1, int(0.8 * len(insts))):
                    # 分布 + 成员清单（349）：列举题（"哪些系统未接入"）的全量证据
                    members: dict[str, list[str]] = {}
                    for e in insts:
                        vv2 = str((e.props or {}).get(k) or "").strip()
                        if vv2:
                            members.setdefault(vv2, []).append(e.label)
                    vals = []
                    for vv, cc in sorted(cnt.items(), key=lambda x: -x[1])[:8]:
                        names = "、".join(members.get(vv, [])[:60])
                        more = (f"（共{cc}个，完整清单见图谱）"
                                if cc > len(members.get(vv, [])) else "")
                        vals.append(f"{vv} {cc} 个（{names}{more}）")
                    dist_parts.append(f"「{k}」" + "；".join(vals))
            if dist_parts:
                out.append(RetrievedSource(
                    doc_file="本体图谱",
                    # cap=4000：成员清单与表格枚举同待遇（默认 1500 会截断长名单）
                    quote=_quote_clip(
                        f"「{label}」共 {len(insts)} 个实例的属性分布"
                        f"（按登记属性聚合）：{'；'.join(dist_parts[:3])}",
                        cap=4000),
                    ref_type="graph_node",
                    score=0.93,
                ))
        return out[:3]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[retrieval] 属性分布聚合失败：{e}")
        return []


def _class_inventory_sources(db: Session, query: RetrievalQuery,
                             class_labels: list[str]) -> list[RetrievedSource]:
    """类锚定枚举事实（graph_node）：列出该类全部实例（少量带属性摘要，量大只列名）。
    作为一条独立引用参与融合——LLM 拿到它才能回答"有哪些 X"。"""
    out: list[RetrievedSource] = []
    # 逐类来源构成（通用：认实例 source_quote 的文件前缀）；行表溯源为准，graph_data 聚合兜底
    try:
        _proj = db.query(Project).filter(Project.id == query.project_id).first()
        _doc_dist = _cls_doc_dist(_proj.graph_data) if _proj is not None else {}
    except Exception:  # noqa: BLE001
        _doc_dist = {}
    for label in class_labels[:2]:
        # ── 类族合并口径（计数题防碎片化）：类名碎片化（"接口"被同轮抽成
        # 前端接口/交互接口/BP接口/查询接口…）时单类计数严重偏少——锚定类的
        # 后缀族（类名以锚定类名结尾，如 BP接口/前端接口 之于 接口）按实例名
        # 去重合并计数，口径构成随引注入。干扰类天然排除：接口输入字段/接口
        # 输出项不以"接口"结尾，不进族。
        try:
            family_rows = (
                db.query(Entity.label)
                .filter(
                    Entity.project_id == query.project_id,
                    Entity.is_class_node.is_(True),
                    Entity.status != "rejected",
                    Entity.label != label,
                    Entity.label.endswith(label),
                )
                .all()
            )
            family_labels = [r[0] for r in family_rows]
        except Exception as e:  # noqa: BLE001
            family_labels = []
            logger.warning(f"[retrieval] 类 {label} 后缀族查询失败：{e}")
        if family_labels:
            try:
                from app.services.class_membership import membership_ids

                family_ids: set[int] = set()
                for _lab in [label] + family_labels:
                    family_ids |= membership_ids(db, query.project_id, _lab)
                fam_insts = (
                    db.query(Entity)
                    .filter(
                        Entity.project_id == query.project_id,
                        Entity.is_class_node.is_(False),
                        Entity.status != "rejected",
                        Entity.id.in_(family_ids or [-1]),
                    )
                    .all()
                )
                from app.adapters.extraction import normalize_label as _nl

                by_key: dict[str, Entity] = {}
                multi_cls = 0
                for e in fam_insts:
                    k = _nl(e.label)
                    if k in by_key:
                        multi_cls += 1
                        continue
                    by_key[k] = e
                distinct = list(by_key.values())
                # 锚定类自身的实例数（用于族增益判断）：族只比单类多零头则不注入
                anchor_only = sum(1 for e in distinct
                                  if _nl(e.class_label) == _nl(label))
                if len(distinct) > anchor_only:
                    fam_cnt = "、".join(
                        f"{lab} {len(membership_ids(db, query.project_id, lab))} 个"
                        for lab in ([label] + family_labels)
                        if membership_ids(db, query.project_id, lab))
                    names = "、".join(e.label for e in distinct[:40])
                    more = (f"（共{len(distinct)}个，完整清单见图谱）"
                            if len(distinct) > 40 else "")
                    multi_part = f"，其中 {multi_cls} 个实例同时挂在多类" if multi_cls else ""
                    out.append(RetrievedSource(
                        doc_file="本体图谱",
                        quote=_quote_clip(
                            f"「{label}」类族去重口径：{fam_cnt}，按实例名去重共 "
                            f"{len(distinct)} 个不同{label}{multi_part}：{names}{more}"),
                        ref_type="graph_node",
                        score=0.96,
                    ))
            except Exception as e:  # noqa: BLE001 —— 族合并失败回落单类清单
                logger.warning(f"[retrieval] 类 {label} 类族合并失败：{e}")
        try:
            from app.services.class_membership import membership_ids

            _ids = membership_ids(db, query.project_id, label)
            insts = (
                db.query(Entity)
                .filter(
                    Entity.project_id == query.project_id,
                    Entity.is_class_node.is_(False),
                    Entity.status != "rejected",
                    Entity.id.in_(_ids or [-1]),
                )
                .all()
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[retrieval] 类 {label} 实例枚举失败：{e}")
            continue
        if not insts:
            continue
        if len(insts) <= 40:
            items = []
            for e in insts:
                pv = "；".join(f"{k}={v}" for k, v in list((e.props or {}).items())[:3]
                              if v not in (None, ""))
                items.append(e.label + (f"（{pv}）" if pv else ""))
            body = "；".join(items)
        else:
            body = "、".join(e.label for e in insts)
        # 来源文档分布（provenance 有数据才带；MVP 直落项目无溯源则跳过）——
        # 让 LLM 能解释口径构成（"152 来自基线 + 30 来自梳理"）而不只是念总数
        doc_part = ""
        try:
            doc_rows = (
                db.query(UploadedDocument.filename, func.count(ProvenanceRecord.id))
                .join(ProvenanceRecord, ProvenanceRecord.source_document_id == UploadedDocument.id)
                .filter(
                    ProvenanceRecord.project_id == query.project_id,
                    ProvenanceRecord.target_type == "entity",
                    ProvenanceRecord.target_id.in_([e.id for e in insts]),
                )
                .group_by(UploadedDocument.filename)
                .order_by(func.count(ProvenanceRecord.id).desc())
                .limit(4)
                .all()
            )
            if doc_rows:
                doc_part = "（来源：" + "、".join(f"{fn}×{c}" for fn, c in doc_rows) + "）"
            else:
                # 行表溯源缺失（MVP 直落项目）：用 graph_data 逐实例文件前缀聚合兜底
                dd = {k: v for k, v in (_doc_dist.get(label) or {}).items() if k != "__total"}
                if dd:
                    doc_part = "（来源：" + "、".join(f"{fn} {c} 个" for fn, c in
                                                     sorted(dd.items(), key=lambda x: -x[1])[:4]) + "）"
        except Exception as e:  # noqa: BLE001 —— 来源分布失败不影响清单本体
            logger.warning(f"[retrieval] 类 {label} 来源分布查询失败：{e}")
        out.append(RetrievedSource(
            doc_file="本体图谱",
            quote=_quote_clip(f"「{label}」类共 {len(insts)} 个实例{doc_part}：{body}"),
            ref_type="graph_node",
            score=0.95,
        ))
        # 属性分布聚合引用（349 修复，计数题防"数切片可见行"）：问题含计数词时，
        # 对该类实例的 props 做低基数属性扫描——"多少个系统接入"这类属性分布
        # 计数题的决定性证据（长表 chunk 引用只见前 ~20 行，按可见行数必错）
        if re.search(r"多少|几个|数量|占比|比例|哪些|哪几|列出|清单|名单", query.question or ""):
            try:
                from collections import Counter as _Counter
                prop_keys: dict[str, dict[str, int]] = {}
                for e in insts:
                    for k, v in (e.props or {}).items():
                        v = str(v or "").strip()
                        if v:
                            prop_keys.setdefault(str(k), {})
                            prop_keys[str(k)][v] = prop_keys[str(k)].get(v, 0) + 1
                dist_parts = []
                for k, cnt in sorted(prop_keys.items(),
                                     key=lambda kv: -sum(kv[1].values()))[:12]:
                    total = sum(cnt.values())
                    distinct = len(cnt)
                    if 2 <= distinct <= 20 and total >= max(1, int(0.8 * len(insts))):
                        members: dict[str, list[str]] = {}
                        for e in insts:
                            vv2 = str((e.props or {}).get(k) or "").strip()
                            if vv2:
                                members.setdefault(vv2, []).append(e.label)
                        vals = []
                        for vv, cc in sorted(cnt.items(), key=lambda x: -x[1])[:8]:
                            names = "、".join(members.get(vv, [])[:40])
                            more = (f"（共{cc}个，完整清单见图谱）"
                                    if cc > len(members.get(vv, [])) else "")
                            vals.append(f"{vv} {cc} 个（{names}{more}）")
                        dist_parts.append(f"「{k}」" + "；".join(vals))
                    if dist_parts:
                        out.append(RetrievedSource(
                            doc_file="本体图谱",
                            # cap=4000：成员清单型引用与表格枚举同待遇——1500 默认
                            # 上限会把 51 个成员名截到前 7 个，模型又数可见项（349）
                            quote=_quote_clip(
                                f"「{label}」共 {len(insts)} 个实例的属性分布"
                                f"（按登记属性聚合）：{';'.join(dist_parts[:3])}",
                                cap=4000),
                            ref_type='graph_node',
                            score=0.93,
                        ))
            except Exception as e:  # noqa: BLE001 —— 分布聚合失败不影响清单本体
                logger.warning(f"[retrieval] 类 {label} 属性分布聚合失败：{e}")
    # 全图类分布概览：计数类问题的口径兜底——类名漂移（同文档不同轮抽成不同类名）
    # 或用户口径与类名不一致时，锚定类清单覆盖不到真类；把全图实例数 top 类一次性
    # 给到 LLM，让它自行挑选相关类并说明口径（分数低于锚定清单，名额让位）
    if out:
        try:
            dist_rows = (
                db.query(Entity.class_label, func.count(Entity.id))
                .filter(
                    Entity.project_id == query.project_id,
                    Entity.is_class_node.is_(False),
                    Entity.status != "rejected",
                    Entity.class_label.isnot(None),
                )
                .group_by(Entity.class_label)
                .order_by(func.count(Entity.id).desc())
                .limit(12)
                .all()
            )
            if dist_rows:
                # 每类带来源构成（graph_data 聚合），LLM 才能说"152 来自基线、30 来自梳理"
                def _lab_part(lab: str, cnt: int) -> str:
                    dd = {k: v for k, v in (_doc_dist.get(lab) or {}).items() if k != "__total"}
                    if not dd or len(dd) <= 1:
                        return f"{lab} {cnt} 个"
                    srcs = "、".join(f"{fn} {c} 个" for fn, c in sorted(dd.items(), key=lambda x: -x[1])[:3])
                    return f"{lab} {cnt} 个（{srcs}）"
                overview = "；".join(_lab_part(lab, cnt) for lab, cnt in dist_rows[:8])
                shown = dist_rows[:8]
                n_cls = (
                    db.query(func.count(func.distinct(Entity.class_label)))
                    .filter(
                        Entity.project_id == query.project_id,
                        Entity.is_class_node.is_(False),
                        Entity.status != "rejected",
                        Entity.class_label.isnot(None),
                    )
                    .scalar()
                )
                all_total = (
                    db.query(func.count(Entity.id))
                    .filter(
                        Entity.project_id == query.project_id,
                        Entity.is_class_node.is_(False),
                        Entity.status != "rejected",
                    )
                    .scalar()
                )
                tail = "" if all_total == sum(c for _l, c in shown) else \
                    f"（其余 {n_cls - len(shown)} 个类合计 {all_total - sum(c for _l, c in shown)} 个）"
                out.append(RetrievedSource(
                    doc_file="本体图谱",
                    quote=_quote_clip(f"全图实例总数 {all_total} 个、共 {n_cls} 个类。主要类分布：{overview}{tail}"),
                    ref_type="graph_node",
                    score=0.72,
                ))
        except Exception as e:  # noqa: BLE001 —— 概览失败不影响锚定清单
            logger.warning(f"[retrieval] 类分布概览生成失败：{e}")
    return out


def _tbox_class_anchor(query: RetrievalQuery, db: Session) -> list[str]:
    """类锚定（向量回退）：问题没逐字提到类名时，用 TBox 索引语义匹配 top 类。
    索引为空（老项目未同步）时先按当前画布骨架补建一次再查。"""
    try:
        from app.adapters.tbox_index import search_class_anchors, sync_tbox_index

        anchors = search_class_anchors(query.question, query.project_id)
        if not anchors:
            from app.infrastructure.database import Project

            proj = db.query(Project).filter(Project.id == query.project_id).first()
            if proj is not None and sync_tbox_index(query.project_id, proj.graph_data) > 0:
                anchors = search_class_anchors(query.question, query.project_id)
        return [label for label, _sim in anchors]
    except Exception as e:  # noqa: BLE001 —— 锚定失败不阻断检索
        logger.warning(f"[retrieval] TBox 类锚定向量回退失败：{e}")
        return []


def _schema_axioms(schema: dict | None) -> list[dict]:
    """画布 schema → 已声明公理的人话摘要列表（preds/clses=涉及的关系/类标签，
    quote=摘要）。公理涉及两类名字分权匹配：问题问到关系名比提到类名更定向。

    只收「有意义」的结构约束：互斥/函数性/反函数性/传递/对称/互逆/定义域/值域。
    层次（parent_classes）不收——类锚定与实例清单已覆盖 is-a 语义，重复注入
    只会挤占引用名额。
    """
    schema = schema or {}
    out: list[dict] = []
    classes = [c for c in (schema.get("classes") or []) if isinstance(c, dict)]
    for c in classes:
        label = str(c.get("label") or "")
        if not label:
            continue
        for peer in (c.get("disjoint_with") or []):
            peer = str(peer or "")
            if peer and peer != label:
                out.append({"preds": [], "clses": [label, peer], "pair": True,
                            "quote": f"类「{label}」与「{peer}」声明互斥（disjointWith）："
                                     f"二者不可能有共同实例，答案不得同时归入两类"})
    for op in (schema.get("object_properties") or []):
        if not (isinstance(op, dict) and op.get("label")):
            continue
        p = str(op["label"])
        dom, rng = str(op.get("domain") or ""), str(op.get("range") or "")
        if dom:
            out.append({"preds": [p], "clses": [dom],
                        "quote": f"关系「{p}」的定义域是「{dom}」：只有「{dom}」的实例能作主体"})
        if rng:
            out.append({"preds": [p], "clses": [rng],
                        "quote": f"关系「{p}」的值域是「{rng}」：客体只能是「{rng}」的实例"})
        if op.get("functional"):
            out.append({"preds": [p], "clses": [],
                        "quote": f"关系「{p}」是函数性的：每个主体至多有一个客体，"
                                 f"多值即矛盾"})
        if op.get("inverse_functional"):
            out.append({"preds": [p], "clses": [],
                        "quote": f"关系「{p}」是反函数性的：每个客体至多被一个主体指向"})
        if op.get("transitive"):
            out.append({"preds": [p], "clses": [],
                        "quote": f"关系「{p}」可传递：A→B 且 B→C ⇒ A→C，"
                                 f"间接可达即视为成立"})
        if op.get("symmetric"):
            out.append({"preds": [p], "clses": [],
                        "quote": f"关系「{p}」是对称的：A—{p}→B 则 B—{p}→A"})
        if op.get("inverse_of"):
            q = str(op["inverse_of"])
            out.append({"preds": [p, q], "clses": [], "pair": True,
                        "quote": f"关系「{p}」与「{q}」互逆：A—{p}→B ⇔ B—{q}→A"})
    return out


def _axiom_sources(query: RetrievalQuery, db: Session) -> list[RetrievedSource]:
    """路径 D：已声明公理 → QA 引用（ref_type='axiom'，高信任约束证据）。

    问题词项命中公理涉及的类/关系名才注入——公理是结构约束而非事实，
    命中才相关（如「ABCS和URSC能是同一个系统吗」命中互斥公理）。命中的
    公理进 LLM 上下文后，模型可以直接引用约束回答（而非只靠事实统计）。
    排序按相关度：关系名命中权重 2（问谓词的场合关系公理最定向），
    类名命中权重 1；同分保持声明顺序。
    """
    from app.infrastructure.database import Project

    proj = db.query(Project).filter(Project.id == query.project_id).first()
    schema = proj.graph_data.get("schema") \
        if proj is not None and isinstance(proj.graph_data, dict) else None
    axioms = _schema_axioms(schema)
    if not axioms:
        return []
    question = query.question or ""
    terms = set(_question_terms(question))
    hits: list[tuple[int, int, RetrievedSource]] = []  # (-相关度, 序, 引用)
    quote_seen: set[str] = set()  # 同文公理去重（schema 里 domain/range 双写会产出同 quote 两条）
    for order, ax in enumerate(axioms):
        rel_hit = sum(1 for lbl in ax["preds"] if lbl in question or lbl in terms)
        cls_hit = sum(1 for lbl in ax["clses"] if lbl in question or lbl in terms)
        relevance = rel_hit * 2 + cls_hit
        # 类对公理（互斥/等价）两个类名同时命中 = 问题正面问这两类的关系，+2 加成
        if ax.get("pair") and cls_hit >= 2:
            relevance += 2
        if not relevance or ax["quote"] in quote_seen:
            continue
        quote_seen.add(ax["quote"])
        hits.append((-relevance, order, RetrievedSource(
            doc_file="本体公理（已声明约束）",
            quote=ax["quote"],
            ref_type="axiom",
            score=0.9,
        )))
    hits.sort(key=lambda t: (t[0], t[1]))
    return [s for _, _, s in hits[:MAX_AXIOM_SOURCES]]


def _graph_expand(db: Session, query: RetrievalQuery, seeds: list[Entity]) -> list[dict]:
    """图扩展：种子实体沿 relations 行表 BFS 1~2 跳，返回事实三元组。

    行表是事实源（02 §4），Neo4j 为投影——问答扩展走行表与 graph_view 同源。
    """
    if not seeds or query.max_expansion_hops < 1:
        return []
    hops = min(int(query.max_expansion_hops), 2)
    facts: list[dict] = []
    # 实例属性也是事实（行表 Entity.props）：不进上下文的话「X的客服电话是多少」
    # 这类属性问答永远拿不到值（走查实测——只有 instance_of 关系、无属性值）
    for e in seeds:
        for pk, pv in (e.props or {}).items():
            kk = str(pk).strip()
            vv = "" if pv is None else str(pv).strip()
            if not kk or not vv:
                continue
            facts.append({"subject": e.label, "predicate": kk, "object": vv,
                          "hop": 1, "score": 0.95, "entity_ids": [e.id]})
    frontier_ids = [e.id for e in seeds]
    seen_edges: set[tuple[int, str, int]] = set()
    # 实体一次拉全（N+1 根治：旧实现对每条边再各查 2 次实体——大语料下 BFS
    # 退化为数百次点查，图模式检索 240s 超时（项目 253 实测），RAG 只要 7s）
    all_ids = {e.id for e in db.query(Entity.id).filter(Entity.project_id == query.project_id).all()}
    ent_by_id = {e.id: e for e in db.query(Entity).filter(
        Entity.project_id == query.project_id,
        Entity.id.in_(all_ids)).all()} if all_ids else {}
    for hop in range(1, hops + 1):
        if not frontier_ids:
            break
        rows = (
            db.query(Relation)
            .filter(
                Relation.project_id == query.project_id,
                Relation.subject_id.in_(frontier_ids),
                Relation.status != "rejected",
            )
            .all()
        )
        rows += (
            db.query(Relation)
            .filter(
                Relation.project_id == query.project_id,
                Relation.object_id.in_(frontier_ids),
                Relation.status != "rejected",
            )
            .all()
        )
        next_ids: list[int] = []
        seen_ids: set[int] = set()
        for rel in rows:
            key = (rel.subject_id, rel.predicate, rel.object_id)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            subj = ent_by_id.get(rel.subject_id)
            obj = ent_by_id.get(rel.object_id)
            if not subj or not obj:
                continue
            facts.append({
                "subject": subj.label,
                "predicate": rel.predicate,
                "object": obj.label,
                "hop": hop,
                "score": 1.0 / hop,
                "entity_ids": [rel.subject_id, rel.object_id],
            })
            other = obj if rel.subject_id in frontier_ids else subj
            if other.id not in seen_ids and other.id not in frontier_ids:
                seen_ids.add(other.id)
                next_ids.append(other.id)
        frontier_ids = next_ids[:20]  # 邻域扩展宽度限制，防大图爆炸
    return facts[:120]  # 枚举聚合按 (主体,谓词) 分组再限量——40 截断会吃掉组内对象（346 实测 30 边被截）


def _prov_by_entity(db: Session, project_id: int, entity_ids: list[int]) -> dict[int, ProvenanceRecord]:
    """实体 → 溯源记录（evidence 原句 + char 定位，07 §4 引用升级）。"""
    if not entity_ids:
        return {}
    rows = (
        db.query(ProvenanceRecord)
        .filter(
            ProvenanceRecord.project_id == project_id,
            ProvenanceRecord.target_type == "entity",
            ProvenanceRecord.target_id.in_(entity_ids[:200]),
        )
        .order_by(ProvenanceRecord.char_start.is_(None), ProvenanceRecord.id)
        .all()
    )
    return {r.target_id: r for r in rows}


def _vector_sources(query: RetrievalQuery) -> list[RetrievedSource]:
    """路径 A：Milvus 向量召回（强制 project_id 表达式）。"""
    try:
        vm = VectorStoreManager()
        hits = vm.search_with_expr(query.question, _milvus_expr(query), top_k=min(query.top_k, 20))
    except Exception as e:  # 向量库抖动不阻断（08 §3 观察项：Milvus 2.3.5 租约脆弱）
        logger.warning(f"[retrieval] 向量召回失败，仅图扩展：{e}")
        return []
    out: list[RetrievedSource] = []
    for h in hits:
        meta = h.get("metadata") or {}
        distance = float(h.get("distance") or 0.0)
        # Milvus COSINE 度量的 distance 字段即相似度（越大越近），夹紧到 [0,1]
        sim = max(0.0, min(1.0, distance))
        out.append(RetrievedSource(
            doc_file=h.get("source_file") or meta.get("source_file") or "未知来源",
            quote=_quote_clip(h.get("source_quote") or meta.get("source_quote") or h.get("text") or ""),
            ref_type="vector_chunk",
            score=round(sim, 4),
            source_document_id=meta.get("source_document_id"),
        ))
    return out


def _question_terms(question: str) -> list[str]:
    """问题词项化：ASCII 词（≥2 字符）+ CJK 连续段 2~4 元滑窗（短段保留整段）。

    只切二元组会把"投资范围"这类决定性短语打碎（长问题串里它只是窗口一员），
    3~4 元窗口让短语整体成词，IDF 加权后才有足够区分度。
    """
    import re
    terms: list[str] = []
    for w in re.findall(r"[A-Za-z0-9]{2,}", question):
        terms.append(w.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", question):
        run = run.strip()
        if 2 <= len(run) <= 6:
            terms.append(run)
        for n in (2, 3, 4):
            for i in range(len(run) - n + 1):
                terms.append(run[i:i + n])
    seen: set[str] = set()
    return [t for t in terms if not (t in seen or seen.add(t))]


def _keyword_sources(query: RetrievalQuery, db: Session) -> list[RetrievedSource]:
    """路径 B 兜底：MySQL document_chunks 关键词打分（IDF 加权，向量空/弱时保底召回原文）。

    产品名等全文档高频词会被均权词频淹没目标段落，按 log(1 + N/df) 加权后
    “投资范围”这类稀有且决定性的词项才能把正确切片顶进名额。
    """
    import math
    from app.infrastructure.database import DocumentChunk, UploadedDocument
    terms = _question_terms(query.question)
    if not terms:
        return []
    try:
        rows = (
            db.query(DocumentChunk, UploadedDocument.filename)
            .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
            .filter(UploadedDocument.project_id == query.project_id,
                    UploadedDocument.deleted_at.is_(None))
            .limit(5000)
            .all()
        )
    except Exception as e:
        logger.warning(f"[retrieval] 关键词兜底查询失败：{e}")
        return []
    if not rows:
        return []
    # 词项 → 命中切片集合（df）
    hits_by_chunk: list[tuple[DocumentChunk, str, set[str]]] = []
    df: dict[str, int] = {}
    for chunk, fname in rows:
        text = chunk.text or ""
        hit = {t for t in terms if t in text}
        if not hit:
            continue
        hits_by_chunk.append((chunk, fname or "未知来源", hit))
        for t in hit:
            df[t] = df.get(t, 0) + 1
    if not hits_by_chunk:
        return []
    n_docs = len(rows)
    weight = {t: math.log(1 + n_docs / d) for t, d in df.items()}
    scored = [
        (sum(weight[t] for t in hit), chunk, fname)
        for chunk, fname, hit in hits_by_chunk
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    top_score = scored[0][0] or 1.0
    out: list[RetrievedSource] = []
    for w, chunk, fname in scored[:min(query.top_k, 12)]:
        out.append(RetrievedSource(
            doc_file=fname,
            quote=_quote_clip(chunk.text or ""),
            ref_type="keyword_chunk",
            score=round(min(0.99, 0.3 + 0.6 * w / top_score), 4),
            source_document_id=chunk.document_id,
        ))
    return out


def _inference_sources(query: RetrievalQuery, db: Session) -> list[RetrievedSource]:
    """路径 C 补充：语义推理引用（reasoning_results 最新批次，IDF 无需——三元组文本短，
    直接用问题词项命中率打分）。推导产物不得冒充事实：doc_file/ref_type 双标注，
    quote 尾部带"语义推理产物"提示，前端引用卡按 inference 类型着色。"""
    terms = [t for t in _question_terms(query.question) if len(t) >= 2]
    if not terms:
        return []
    rows = (
        db.query(ReasoningResult)
        .filter(ReasoningResult.project_id == query.project_id)
        .order_by(ReasoningResult.id.desc())
        .limit(5000)
        .all()
    )
    if not rows:
        return []
    scored: list[tuple[int, ReasoningResult]] = []
    for r in rows:
        text = f"{r.subject_label}{r.predicate_label}{r.object_label}"
        hits = sum(1 for t in terms if t in text)
        if hits:
            scored.append((hits, r))
    if not scored:
        return []
    scored.sort(key=lambda x: (x[0], x[1].id), reverse=True)
    out: list[RetrievedSource] = []
    for hits, r in scored[:MAX_INFERRED_SOURCES]:
        # 规则推理带规则名；蕴含推理标档位（owlrl/rdfs）
        origin = f"规则「{r.rule_name}」" if r.rule_name else f"蕴含推理（{r.source}）"
        out.append(RetrievedSource(
            doc_file="语义推理（非原始事实）",
            quote=f"{r.subject_label} —[{r.predicate_label}]→ {r.object_label}（{origin}推导，语义推理产物）",
            ref_type="inference",
            score=round(min(0.8, 0.3 + 0.12 * hits), 4),
        ))
    return out



def _group_edge_facts(facts: list[dict]) -> tuple[list[dict], list[dict]]:
    """图事实聚合：正向 (主体,谓词) 或反向 (宾语,谓词) 达到 ≥2 条即合并。

    纯函数（可测）：输入 _graph_expand 产物，返回 (聚合组列表, 单条事实列表)。
    反向聚合是 2026-11-19 新增：接口字段表是“字段 —属于→ 接口”，按正向分组
    每组只有 1 条，70 个字段全部落到散条后被 MAX_GRAPH_FACTS 挤掉，问答
    答不出“C2504051 的输入项”。反向聚合把同一接口的字段合成一条清单引用。
    """
    fwd: dict[tuple[str, str], list[dict]] = {}
    rev: dict[tuple[str, str], list[dict]] = {}
    for f in facts:
        fwd.setdefault((str(f["subject"]), str(f["predicate"])), []).append(f)
        rev.setdefault((str(f["object"]), str(f["predicate"])), []).append(f)
    agg: list[dict] = []
    used: set[int] = set()

    def _add(fs: list[dict], reverse: bool) -> None:
        if not fs:
            return
        agg.append({"facts": fs, "reverse": reverse})
        used.update(id(x) for x in fs)

    # 正向组优先（既有语义：主体—谓词→对象清单）
    for fs in fwd.values():
        if len(fs) >= 2:
            _add(fs, False)
    # 反向组：只收未被正向组消费的、≥3 条的大组（字段/成员清单）
    for fs in rev.values():
        if len(fs) >= 3 and any(id(x) not in used for x in fs):
            _add(fs, True)
    singles = [f for f in facts if id(f) not in used]
    return agg, singles


def _quote_clip(text: str, cap: int = 1500) -> str:
    """引用截断（根修 3454"数据对不上"）：

    - 表格型引用（markdown 表格行 ≥3 行）：cap 提高到 4000——大表被 2000 字切片后
      quote[:1500] 会把尾部行从 LLM 上下文里整个吃掉（实测 51 个未接入系统丢 5 个）；
    - 截断点回退到最近行边界（不拦腰切行/切单元格）；
    - 截断时保留末尾"…"提示（LLM 知道有后续，不再静默丢失）。
    """
    if not text or len(text) <= cap:
        return text or ""
    # 行式枚举检测：markdown 表格行 / 列表行（- * • 数字. 数字）——枚举型内容
    # （接入清单/限额表/操作步骤清单）截尾=数据点丢失，等同表格待遇（PPT/PDF/Word 常见）
    enum_lines = sum(1 for ln in text.splitlines()
                     if ln.strip().startswith("|")
                     or re.match(r"^(?:[-*•]|\d+[.、)])\s*", ln.strip()))
    limit = 4000 if enum_lines >= 3 else cap
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > limit * 0.6:
        cut = cut[:nl]
    return cut + "…"


def retrieve(query: RetrievalQuery, db: Session | None = None) -> list[RetrievedSource]:
    """双路召回 + proximity 融合（07 §4）。db 为 None 时仅向量路径。

    融合规则：文本证据（向量 + 关键词兜底）按分数排满剩余名额，图事实最多占
    MAX_SOURCES//2——此前图事实整体排前曾把向量块全部挤出 10 条引用，
    LLM 拿不到原文只能答"上下文没有"。
    """
    sources: list[RetrievedSource] = []
    class_labels: list[str] = []
    axiom_hits: list[RetrievedSource] = []
    if db is not None:
        # 类锚定枚举：词法优先（问题逐字含类名），未命中走 TBox 向量回退
        class_labels = _match_class_labels(db, query) if query.use_graph else []
        if class_labels:
            sources.extend(_class_inventory_sources(db, query, class_labels))
        # 已声明公理：问题命中约束涉及的类/关系名时注入（高信任，保留名额）
        axiom_hits = _axiom_sources(query, db) if query.use_graph else []
        seeds = _match_entities(db, query)
        facts = _graph_expand(db, query, seeds) if query.use_graph else []
        if facts:
            prov = _prov_by_entity(db, query.project_id,
                                   sorted({i for f in facts for i in f["entity_ids"]}))
            doc_names: dict[int, str] = {}

            def _doc_of(entity_ids: list[int]) -> tuple[str | None, int | None]:
                p = next((prov.get(i) for i in entity_ids if prov.get(i)), None)
                doc_id = p.source_document_id if p else None
                if doc_id and doc_id not in doc_names:
                    d = db.query(UploadedDocument).filter(UploadedDocument.id == doc_id).first()
                    doc_names[doc_id] = d.filename if d else "未知来源"
                return doc_names.get(doc_id, "未知来源" if doc_id else "本体图谱"), doc_id

            def _single_fact(f: dict) -> RetrievedSource:
                # 事实引用 = 端点实体的溯源证据（有 evidence 用原句，无则用三元组文本）
                doc_file, doc_id = _doc_of(f["entity_ids"])
                p = next((prov.get(i) for i in f["entity_ids"] if prov.get(i)), None)
                quote = (p.evidence_text if p and p.evidence_text else
                         f"{f['subject']} —{f['predicate']}→ {f['object']}")
                return RetrievedSource(
                    doc_file=doc_file,
                    quote=_quote_clip(quote),
                    char_start=p.char_start if p else None,
                    char_end=p.char_end if p else None,
                    ref_type="graph_edge",
                    score=round(f["score"], 4),
                    source_document_id=doc_id,
                )

            # 枚举聚合（计数题防零头）：同 (主体,谓词) ≥2 条 → 合并成一条对象清单
            # 引用（"X —[调用]→ 30 个对象：C250505A、…"）。"流程涉及哪些接口"类
            # 问题的决定性证据是对象全集，逐条引用会被名额挤掉大半（346 实测
            # 33 条调用边只露 3 条 → 答"3 个接口"）
            agg_groups, singles = _group_edge_facts(facts)
            edge_sources: list[RetrievedSource] = []
            # 聚合引用排在散条前面：融合按 score 排序（同分稳定保序），聚合清单
            # 是枚举问题的决定性证据，必须先进 MAX_GRAPH_FACTS 名额
            for group in agg_groups:
                fs = group["facts"]
                reverse = bool(group.get("reverse"))
                doc_file, doc_id = _doc_of([i for f in fs for i in f["entity_ids"]])
                names = "、".join(str(f["subject"] if reverse else f["object"])
                                 for f in fs[:200])
                more = (f"（共{len(fs)}个，完整清单见图谱）" if len(fs) > 200 else "")
                first_p = next((prov.get(i) for i in fs[0]["entity_ids"] if prov.get(i)), None)
                ev = (first_p.evidence_text[:80] if first_p and first_p.evidence_text
                      else f"{fs[0]['subject']} —{fs[0]['predicate']}→ {fs[0]['object']}")
                if reverse:
                    desc = (f"{fs[0]['object']} ←[{fs[0]['predicate']}]— 共 {len(fs)} 个主体："
                            f"{names}{more}（证据样例：{ev}）")
                else:
                    desc = (f"{fs[0]['subject']} —[{fs[0]['predicate']}]→ 共 {len(fs)} 个对象："
                            f"{names}{more}（证据样例：{ev}）")
                edge_sources.append(RetrievedSource(
                    doc_file=doc_file,
                    quote=_quote_clip(desc, cap=4000),
                    char_start=first_p.char_start if first_p else None,
                    char_end=first_p.char_end if first_p else None,
                    ref_type="graph_edge",
                    score=round(min(1.0, max(f["score"] for f in fs) + 0.01), 4),
                    source_document_id=doc_id,
                ))
            for f in singles:
                edge_sources.append(_single_fact(f))
            sources.extend(edge_sources)
    if not class_labels and query.use_graph and db is not None:
        vec_labels = _tbox_class_anchor(query, db)
        if vec_labels:
            sources.extend(_class_inventory_sources(db, query, vec_labels))
    # 属性分布聚合：独立于类锚定的计数证据通道（349 修复）
    if query.use_graph and db is not None:
        sources.extend(_prop_distribution_sources(db, query))
    sources.extend(_vector_sources(query))
    # 关键词兜底始终参与融合（混合检索）：向量相似度对短段落/专有词弱的场景由
    # 词项命中补位；向量正常时两路结果按 (doc_file, quote) 去重互补
    if db is not None:
        sources.extend(_keyword_sources(query, db))

    inventory = [s for s in sources if s.ref_type == "graph_node"]
    text_hits = sorted((s for s in sources if s.ref_type not in ("graph_edge", "graph_node")),
                       key=lambda s: s.score, reverse=True)
    graph_facts = sorted((s for s in sources if s.ref_type == "graph_edge"),
                         key=lambda s: s.score, reverse=True)

    # 先去重再限量：图事实大量共用同一实体证据原文，先截后去会把 5 个名额
    # 全花在重复引用上；图事实去重后最多占半数名额，文本证据保底另一半
    def _dedup(items: list[RetrievedSource]) -> list[RetrievedSource]:
        out: list[RetrievedSource] = []
        key_seen: set[tuple[str, str]] = set()
        for s in items:
            key = (s.doc_file, s.quote[:50])
            if key in key_seen:
                continue
            key_seen.add(key)
            out.append(s)
        return out

    # 类锚定实例清单有保留名额（最多 3 条：锚定清单 2 条 + 全图分布概览 1 条）——
    # 枚举问题的决定性证据；全图计数类问题（"总共有多少实例"）依赖概览入选
    fused = (_dedup(inventory)[:3] + axiom_hits[:2] + _dedup(graph_facts)[:MAX_GRAPH_FACTS]
             + _dedup(text_hits))
    result = fused[:MAX_SOURCES]
    # 语义推理引用垫底补充（不与事实混排）：
    # 推理引用有保留名额——事实满额时压缩事实到 8 条，保证层级/归属类问题
    # 能拿到推理证据；事实不足时推理最多补 MAX_INFERRED_SOURCES 条
    if db is not None and query.use_inferred:
        try:
            inferred = _inference_sources(query, db)
        except Exception as e:
            logger.warning(f"[retrieval] 推理引用注入失败，忽略：{e}")
            inferred = []
        if inferred:
            result = result[:MAX_SOURCES - MAX_INFERRED_RESERVED]
            result.extend(inferred[:min(MAX_INFERRED_SOURCES, MAX_SOURCES - len(result))])
    return result


_QA_SYSTEM_PROMPT = """你是一位熟悉这套业务系统的数据分析同事，基于提供的上下文信息回答用户问题。回答要像同事当面给你讲数据，自然、直接，不要写成系统报告。

【语气与节奏】
- 用说话的方式组织回答，少用"首先/其次/综上所述"这类报告腔和排比句式。
- 篇幅跟着问题的分量走：简单事实问一句答一句就够；构成复杂的问题才分层展开。
- 不要每次都套同一副骨架，让结构跟随问题本身。

【硬约束】（任何情况下不可违反）
1. 只根据上下文回答，不编造；上下文没有就直说没有，并给出最接近的相关信息，不要空泛拒答。
2. 引用标注 [1][2][3] 放在相关语句的末尾。
3. 说业务人话：不出现"实例/类节点/锚定/通道/概览引用"这类实现词汇；类名可以直接用，但句子要像人在解释数据。
4. 引用里写"共 N 个/共 N 项"但只列出其中一部分时，**必须明确说出总数**，并说明"当前列出的是其中 M 个/示例"，不得把已列项当成全集；需要完整清单时提示到图谱或原始文档查看。
5. 来源标注为"本体图谱 · query_facts（确定性查询）"的引用是工具查出的全集/计数，**优先于**其它图谱摘要；清单/字段题必须按它的 items 逐项或按业务分组列全（total > 200 时列前 50 并说明剩余），不得用"等"省略，也不得说"工具没给/去图谱查"。

【计数与构成】（仅计数、统计、构成类问题适用）
- 数字要能对上账：引用里带"来源：文档×数量"时，按"总共 N，其中 A 文档 X 个、B 文档 Y 个"讲清楚。
- 引用给出"类族去重口径"时以去重总数为准，相近类的数量可一并提及并说明差别；"某流程涉及哪些 X"以对象清单型引用（如 X —[调用]→ 30 个对象）的全集计数，不要数引用条数。
- 长清单被上下文预算截断时，回答"共 N 个"并列出可见部分；不要把"当前可见的 M 个"说成"全部 N 个"。
- 按属性分组计数时，各组之和必须等于总数；属性缺失的实例归入"未标注"单列，不得漏掉这一项。

【语义推理引用】
来自公理/规则推导的结论可用于归属、层级类问题，表述成"根据语义推导"，和文档原文区分开。

【引用格式说明】：[1] 表示引用第 1 条参考信息，以此类推。
"""

_ANSWER_TAIL = "\n\n请根据以上信息回答问题，并在适当位置标注引用标号："


def _build_user_prompt(question: str, sources: list[RetrievedSource]) -> str:
    context = "\n\n".join(
        f"[{i + 1}] 来源：{s.doc_file}（{s.ref_type}）\n    内容：{s.quote}"
        for i, s in enumerate(sources)
    )
    return f"【用户问题】\n{question}\n\n【参考信息列表】\n{context}{_ANSWER_TAIL}"


def _confidence(sources: list[RetrievedSource]) -> float:
    """启发式置信：top 引用相似度均值，上限 0.95（不伪造确定感）。"""
    if not sources:
        return 0.0
    top = sources[:5]
    return round(min(0.95, sum(s.score for s in top) / len(top) * 0.9 + 0.05), 3)


def _tool_observations_to_sources(observations: list[dict]) -> list[RetrievedSource]:
    """query_facts 工具结果 → 一条高信任图谱引用（带 total/翻页提示）。"""
    out: list[RetrievedSource] = []
    seen: set[str] = set()
    for obs in observations or []:
        res = obs.get("result") or {}
        if not isinstance(res, dict) or res.get("error"):
            continue
        total = int(res.get("total") or 0)
        returned = int(res.get("returned") or 0)
        items = res.get("items") or []
        if total <= 0 or not items:
            continue
        args = obs.get("args") or {}
        key = json.dumps({"args": args, "total": total, "returned": returned},
                         ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        lines: list[str] = []
        for x in items[:300]:
            if isinstance(x, dict) and "predicate" in x:
                lines.append(f"{x.get('subject') or '?'} —[{x.get('predicate')}]→ "
                             f"{x.get('object') or '?'}")
            elif isinstance(x, dict):
                props = x.get("props") or {}
                prop_txt = "；".join(f"{k}={v}" for k, v in list(props.items())[:8])
                lines.append(f"{x.get('label') or '?'}（{x.get('class_label') or ''}）"
                             + (f"：{prop_txt}" if prop_txt else ""))
        more = ""
        if res.get("has_more"):
            more = (f"；还有 {total - returned} 条未展示，next_offset="
                    f"{res.get('next_offset')}")
        quote = (f"query_facts({json.dumps(args, ensure_ascii=False, default=str)}) "
                 f"共 {total} 条，本页 {returned} 条{more}。\n" + "\n".join(lines))
        if len(quote) > 12000:
            quote = quote[:12000] + "\n（工具结果过长已截断，按 next_offset 翻页取余量）"
        out.append(RetrievedSource(
            doc_file="本体图谱 · query_facts（确定性查询）",
            quote=quote,
            ref_type="tool_query",
            score=0.995,
        ))
    return out


def _augment_with_qa_tools(query: RetrievalQuery, sources: list[RetrievedSource],
                           db: Session | None, llm: LLMClient | None
                           ) -> tuple[list[RetrievedSource], list[dict], int, list[dict]]:
    """清单/计数类问题先走 query_facts 工具；失败静默回落原检索。"""
    if db is None or llm is None or not query.use_graph:
        return sources, [], 0, []
    try:
        from app.services.qa_tool_loop import run_qa_tool_loop, should_use_tools

        if not should_use_tools(query.question):
            return sources, [], 0, []
        info = run_qa_tool_loop(db, query.project_id, query.question, llm)
        observations = info.get("observations") or []
        tool_sources = _tool_observations_to_sources(observations)
        tool_meta: list[dict] = []
        for obs in observations:
            res = obs.get("result") or {}
            if int(res.get("total") or 0) <= 0:
                continue
            tool_meta.append({
                "args": obs.get("args") or {},
                "total": int(res.get("total") or 0),
                "returned": int(res.get("returned") or 0),
                "has_more": bool(res.get("has_more")),
            })
        if not tool_sources:
            return sources, info.get("calls") or [], 0, tool_meta
        # 工具结果是确定性全集，放最前：模型先看到 total/items，再读普通检索引用，
        # 避免被后面的类清单/边引用带偏（C2504051 实测工具在第 11 条时模型只说“去图谱查”）。
        return (tool_sources + sources, info.get("calls") or [],
                len(tool_sources), tool_meta)
    except Exception as e:  # noqa: BLE001 —— 工具失败不阻断问答
        logger.warning(f"[retrieval] QA 工具查询失败，回落原检索：{e}")
        return sources, [], 0, []


def query_with_reasoning(query: RetrievalQuery, db: Session | None = None,
                         llm: LLMClient | None = None,
                         system_prompt: str | None = None,
                         temperature: float | None = None) -> QAResult:
    """检索 + LLM 接地答案（AgentContext.query_with_reasoning 语义，非流式聚合）。

    system_prompt：调用方自定义提示词（对外 MCP/REST 检索能力，2026-11-09）——
    替换内置 _QA_SYSTEM_PROMPT；None 用内置。temperature 同理（缺省 0.1）。
    """
    t0 = time.time()
    sources = retrieve(query, db=db)
    sources, tool_calls, tool_source_count, tool_meta = _augment_with_qa_tools(
        query, sources, db, llm)
    # 推理路径 = 真实检索过程（通道构成+锚定类），不再是泛化描述
    inv = [s for s in sources if s.ref_type == "graph_node"]
    axm = [s for s in sources if s.ref_type == "axiom"]
    gef = [s for s in sources if s.ref_type == "graph_edge"]
    txt = [s for s in sources if s.ref_type not in ("graph_node", "graph_edge", "axiom")]
    anchored = [m.group(1) for s in inv if (m := re.match(r"「(.+?)」类共", s.quote))]
    reasoning: list[str] = []
    if tool_calls or tool_source_count:
        qf = [c for c in tool_calls if c.get("phase") == "end"
              and c.get("tool") == "query_facts"]
        reasoning.append(f"工具 query_facts 调用 {max(len(qf), 1 if tool_source_count else 0)} 次，"
                         f"补充确定性清单引用 {tool_source_count} 条"
                         f"（total/next_offset 可用于翻页）")
    if anchored:
        reasoning.append(f"类锚定命中：{'、'.join(anchored[:4])}（实例清单/分布概览 {len(inv)} 条）")
    else:
        reasoning.append("未命中类锚定，走文本通道")
    if axm:
        reasoning.append(f"公理约束参与 {len(axm)} 条（定义域/值域/互斥等）")
    reasoning.append(f"图谱事实 {len(gef)} 条 | 文本切片 {len(txt)} 条 → 融合排序取 {len(sources)} 条")
    answer = ""
    model_name = None
    if sources and llm is not None:
        try:
            resp = llm.call_llm_text(
                system_prompt=system_prompt or _QA_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(query.question, sources),
                stream=None,  # 跟随模型行 params.streaming_enabled（349 修复：硬编码 False 使行配置对 QA 不生效）
                timeout=300.0,
                temperature=temperature,
            )
            answer = (resp.get("content", "") if isinstance(resp, dict) else str(resp)).strip()
            model_name = getattr(llm, "model", None)
            reasoning.append("LLM 接地生成：结论先行 + 口径构成推理 + [n] 溯源标注")
        except Exception as e:
            logger.error(f"[retrieval] LLM 生成失败：{e}")
            reasoning.append(f"LLM 生成失败，回退引用聚合（{e}）")
    if not answer:
        answer = ("未检索到足够的相关信息，无法回答该问题。" if not sources else
                  "根据检索到的信息：\n" + "\n".join(f"[{i + 1}] {s.quote}" for i, s in enumerate(sources[:5])))
    return QAResult(
        answer=answer,
        sources=sources,
        reasoning_path=reasoning,
        retrieval_trace=_retrieval_trace(sources, query.question),
        confidence=_confidence(sources),
        model=model_name,
        latency_ms=int((time.time() - t0) * 1000),
    )


def retrieve_structured(query: RetrievalQuery, db: Session | None = None) -> dict:
    """结构化检索（非问答场景，零 LLM）：向量召回 + 图扩展 + 推理引用 → 结构化 JSON。

    对外 MCP retrieve 工具 / REST /retrieval?mode=retrieve 的执行体：
    输出 {results: [{rank, ref_type, quote, doc_file, source_document_id,
    char_start, char_end, score, knowledge_domain}], total, query{...}}——
    与问答模式的差异：无 answer/reasoning_path，纯来源数据，适合 Agent 二次加工。
    """
    t0 = time.time()
    sources = retrieve(query, db=db)
    results = [{
        "rank": i + 1,
        "ref_type": src.ref_type,
        "quote": src.quote,
        "doc_file": src.doc_file,
        "source_document_id": src.source_document_id,
        "char_start": src.char_start,
        "char_end": src.char_end,
        "score": round(float(src.score or 0), 4),
    } for i, src in enumerate(sources)]
    return {"results": results, "total": len(results),
            "mode": "retrieve",
            "query": {"question": query.question, "top_k": query.top_k,
                      "use_graph": query.use_graph,
                      "use_inferred": query.use_inferred,
                      "knowledge_domain": query.knowledge_domain},
            "latency_ms": int((time.time() - t0) * 1000)}


def query_with_repair(query: RetrievalQuery, db: Session | None = None,
                      llm: LLMClient | None = None) -> QAResult:
    """带自修复的问答（O3-B15①）：常规流水线失败检测 → 换策略重试，最多 2 轮。

    失败判定：检索 0 引用 / LLM 生成异常回退聚合 / 答案为"未检索到"兜底话术。
    修复策略（确定性，不引框架）：R1 核心词重述 + top_k×2 + 关推理引用；
    R2 最宽召回（top_k 上限 + 图扩展 + 推理引用）。修复成功 repair_rounds=轮数。
    """
    t0 = time.time()
    result = query_with_reasoning(query, db=db, llm=llm)
    llm_failed = any("LLM 生成失败" in r for r in result.reasoning_path)
    if not _is_failed(result, llm_failed):
        return result

    strategies = [
        # R1：核心词重述 + 扩大召回 + 只看事实
        RetrievalQuery(project_id=query.project_id,
                       question=_core_question(query.question),
                       top_k=min(30, max(query.top_k * 2, 20)),
                       use_graph=query.use_graph, use_inferred=False,
                       max_expansion_hops=query.max_expansion_hops,
                       knowledge_domain=query.knowledge_domain),
        # R2：最宽召回（原始问题 + 推理引用）
        RetrievalQuery(project_id=query.project_id,
                       question=query.question, top_k=30,
                       use_graph=True, use_inferred=True,
                       max_expansion_hops=query.max_expansion_hops,
                       knowledge_domain=query.knowledge_domain),
    ]
    for round_no, q2 in enumerate(strategies, start=1):
        result = query_with_reasoning(q2, db=db, llm=llm)
        llm_failed = any("LLM 生成失败" in r for r in result.reasoning_path)
        result.repair_rounds = round_no
        result.latency_ms = int((time.time() - t0) * 1000)
        result.reasoning_path = [f"自修复第 {round_no} 轮：{_strategy_label(q2, query)}"] + result.reasoning_path
        if not _is_failed(result, llm_failed):
            return result
    result.failed = True
    result.latency_ms = int((time.time() - t0) * 1000)
    return result


def _is_failed(result: QAResult, llm_failed: bool) -> bool:
    if llm_failed:
        return True
    if not result.sources:
        return True
    return result.answer.strip().startswith(("未检索到足够的相关信息", "根据检索到的信息"))


def _core_question(question: str) -> str:
    """核心词重述：去掉疑问框架词，只留实体/关系词（提升向量与关键词命中率）。"""
    import re
    q = re.sub(r"(请问|一下|什么|哪些|哪个|如何|怎么|怎样|是否|多少|有没有|介绍一下|说明|谢谢|吗|呢|吧|的)", " ", question)
    q = re.sub(r"\s+", " ", q).strip()
    return q or question


def _is_rate_limit(e: Exception) -> bool:
    """供应商限流（429/TPM/RPM/TooManyRequests）。"""
    return "429" in str(e) or "RateLimit" in str(e) or "TooManyRequests" in str(e)


def _friendly_llm_error(e: Exception) -> str:
    """供应商报错 → 可操作的人话（429"流式生成未完成"这类黑话用户没法行动）。"""
    msg = str(e)
    if _is_rate_limit(e):
        return ("模型限流（429）：请求过于频繁或账号配额暂时超限，已自动重试仍未成功。"
                "请稍等一两分钟再问，或在右上「对话模型」里换一个模型。")
    if "401" in msg or "Unauthorized" in msg or "Authentication" in msg:
        return "模型鉴权失败（401）：请到「系统配置→模型配置」检查该模型的 API Key。"
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return "模型响应超时：请稍后重试，或更换更快的对话模型。"
    return f"模型调用失败：{msg[:200]}"


def _strategy_label(repaired: RetrievalQuery, original: RetrievalQuery) -> str:
    if repaired.question != original.question:
        return "核心词重述 + 扩大召回 + 关推理引用"
    return "最宽召回（top_k=30 + 图扩展 + 推理引用）"


def iter_answer(query: RetrievalQuery, db: Session | None = None,
                llm: LLMClient | None = None) -> Iterator[tuple[str, Any]]:
    """流式问答（SSE 后端，07 §4）：yield ("token", str) 增量 ... ("done", QAResult)。

    引用先检索再生成（引用集合在 token 流期间不变，done 事件一次性下发）。
    """
    t0 = time.time()
    sources = retrieve(query, db=db)
    sources, tool_calls, tool_source_count, tool_meta = _augment_with_qa_tools(
        query, sources, db, llm)
    yield ("meta", {"source_count": len(sources)})
    # 检索过程先行：LLM 生成前就把步骤卡发给前端（用户先看到"怎么查"，再看到答案）
    trace = _retrieval_trace(sources, query.question)
    yield ("trace", trace)
    if not sources or llm is None:
        result = query_with_reasoning(query, db=db, llm=llm)
        result.failed = not result.sources  # O3-B16：空引用问答计失败轨迹
        yield ("token", result.answer)
        yield ("done", result)
        return
    answer_parts: list[str] = []
    attempt = 0
    while True:
        attempt += 1
        try:
            for delta in llm.call_llm_stream(
                system_prompt=_QA_SYSTEM_PROMPT,
                user_prompt=_build_user_prompt(query.question, sources),
                timeout=300.0,
            ):
                answer_parts.append(delta)
                yield ("token", delta)
            break
        except Exception as e:  # 流中断：把已收内容聚合返回，不静默吞
            # 限流（429）且尚未吐字：退避后整段重试一次（已有增量不能重试——会重复输出）
            if _is_rate_limit(e) and not answer_parts and attempt == 1:
                logger.warning("[retrieval] 模型限流（429），30s 后自动重试一次")
                time.sleep(30)
                continue
            logger.error(f"[retrieval] 流式生成中断：{e}")
            yield ("error", _friendly_llm_error(e))
            if not answer_parts:
                return
            break
    answer = "".join(answer_parts).strip() or "（模型未返回内容）"
    # 推理路径 = 真实检索过程（与 query_with_reasoning 同款细粒度）
    inv = [s for s in sources if s.ref_type == "graph_node"]
    axm = [s for s in sources if s.ref_type == "axiom"]
    gef = [s for s in sources if s.ref_type == "graph_edge"]
    txt = [s for s in sources if s.ref_type not in ("graph_node", "graph_edge", "axiom")]
    anchored = [m.group(1) for s in inv if (m := re.match(r"「(.+?)」类共", s.quote))]
    rp: list[str] = []
    if anchored:
        rp.append(f"类锚定命中：{'、'.join(anchored[:4])}（实例清单/分布概览 {len(inv)} 条）")
    else:
        rp.append("未命中类锚定，走文本通道")
    if axm:
        rp.append(f"公理约束参与 {len(axm)} 条（定义域/值域/互斥等）")
    rp.append(f"图谱事实 {len(gef)} 条 | 文本切片 {len(txt)} 条 → 融合排序取 {len(sources)} 条")
    rp.append("LLM 接地生成：结论先行 + 口径构成推理 + [n] 溯源标注")
    yield ("done", QAResult(
        answer=answer,
        sources=sources,
        reasoning_path=rp,
        retrieval_trace=_retrieval_trace(sources, query.question),
        confidence=_confidence(sources),
        model=getattr(llm, "model", None),
        latency_ms=int((time.time() - t0) * 1000),
    ))


__all__ = ["RetrievedSource", "QAResult", "RetrievalQuery", "retrieve", "retrieve_structured", "query_with_reasoning",
           "query_with_repair", "iter_answer", "_milvus_expr"]
