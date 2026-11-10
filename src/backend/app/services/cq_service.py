# app/services/cq_service.py - Competency Question 资产化（O3-B12，docs/design/09 §2）
# ①抽取前：CQ 注入 guidance 约束抽取范围（压噪声、省 token）；
# ②抽取后：CQ 自动转评测集（检索式跑通率/证据覆盖率，确定性无 LLM）并回写 cq_evaluations。
from __future__ import annotations

import datetime
import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.adapters.retrieval import RetrievalQuery, retrieve as default_retrieve
from app.infrastructure.database import CompetencyQuestion, CqEvaluation

logger = logging.getLogger(__name__)

# 单条 CQ 判定"可回答"所需的最低引用数（检索式口径，无 LLM）
ANSWERABLE_MIN_SOURCES = 1


def list_cqs(db: Session, project_id: int, enabled_only: bool = False) -> list[CompetencyQuestion]:
    q = (db.query(CompetencyQuestion)
         .filter(CompetencyQuestion.project_id == project_id))
    if enabled_only:
        q = q.filter(CompetencyQuestion.enabled.is_(True))
    return q.order_by(CompetencyQuestion.sort_order.asc(),
                      CompetencyQuestion.id.asc()).all()


def create_cqs(db: Session, project_id: int, items: list[dict],
               created_by: int | None, source: str = "manual") -> list[CompetencyQuestion]:
    """批量建 CQ（单条也走这里）。question 必填去重（项目内归一化后重复的跳过）。"""
    base = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.project_id == project_id).all()
    existing = {str(c.question or "").strip().lower() for c in base}
    out: list[CompetencyQuestion] = []
    next_sort = max((c.sort_order or 0) for c in base) + 1 if base else 1
    for item in items:
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        key = question.lower()
        if key in existing:
            continue
        existing.add(key)
        row = CompetencyQuestion(
            project_id=project_id,
            question=question[:2000],
            answer_criteria=(str(item.get("answer_criteria") or "").strip() or None),
            source=source if source in ("manual", "evolution", "auto") else "manual",
            sort_order=int(item.get("sort_order") or next_sort),
            enabled=bool(item.get("enabled", True)),
            created_by=created_by,
        )
        next_sort += 1
        db.add(row)
        out.append(row)
    db.flush()
    return out


def update_cq(db: Session, project_id: int, cq_id: int, patch: dict) -> CompetencyQuestion | None:
    row = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.id == cq_id,
        CompetencyQuestion.project_id == project_id).first()
    if row is None:
        return None
    if "question" in patch and str(patch["question"] or "").strip():
        row.question = str(patch["question"]).strip()[:2000]
    if "answer_criteria" in patch:
        row.answer_criteria = (str(patch["answer_criteria"] or "").strip() or None)
    if "enabled" in patch:
        row.enabled = bool(patch["enabled"])
    if "sort_order" in patch and isinstance(patch["sort_order"], int):
        row.sort_order = patch["sort_order"]
    row.updated_at = datetime.datetime.utcnow()
    db.flush()
    return row


def delete_cq(db: Session, project_id: int, cq_id: int) -> bool:
    row = db.query(CompetencyQuestion).filter(
        CompetencyQuestion.id == cq_id,
        CompetencyQuestion.project_id == project_id).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def reorder_cqs(db: Session, project_id: int, ordered_ids: list[int]) -> int:
    rows = {c.id: c for c in db.query(CompetencyQuestion).filter(
        CompetencyQuestion.project_id == project_id).all()}
    n = 0
    for i, cid in enumerate(ordered_ids, start=1):
        row = rows.get(int(cid))
        if row is not None:
            row.sort_order = i
            n += 1
    db.flush()
    return n


def guidance_from_cqs(db: Session, project_id: int,
                      base_guidance: str | None) -> str | None:
    """抽取 guidance 注入（O3-B12①）：项目有启用 CQ 时，拼装 CQ 引导段。

    引导语义：优先抽取与 CQ 主体/关系相关的类与实例，抑制与问题无关的泛化类型。
    """
    cqs = list_cqs(db, project_id, enabled_only=True)
    if not cqs:
        return base_guidance
    lines = [f"{i}. {c.question}" for i, c in enumerate(cqs[:20], start=1)]
    cq_block = ("【Competency Questions 引导】本项目的本体要能回答以下能力问题，"
                "抽取时优先覆盖这些问题涉及的实体类型、关系与属性，"
                "与问题无关的泛化名词不要建类：\n" + "\n".join(lines))
    if base_guidance:
        return f"{base_guidance}\n\n{cq_block}"
    return cq_block


def _question_terms(question: str) -> list[str]:
    """查询词项：长度≥2 的连续 CJK 段与 ASCII 词，去虚词。"""
    import re
    stopwords = {"什么", "哪些", "如何", "怎么", "是否", "多少", "哪些个", "请问", "的", "了", "吗"}
    tokens: list[str] = []
    for seg in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", question or ""):
        if seg.isascii():
            if len(seg) >= 2:
                tokens.append(seg.lower())
            continue
        # 中文：整段 + 2-gram（无分词器，2-gram 提命中鲁棒性）
        if seg not in stopwords:
            tokens.append(seg)
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if bg not in stopwords:
                tokens.append(bg)
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _relax_cq_sparql(sparql: str) -> str:
    """参数放宽：剥掉绑定占位参数（"某贷款合同"等）的 FILTER 行。

    参数化 CQ（"某 X 的 Y"）按字面执行永远 0 行——占位符不是真实实例名。
    放宽后仍有行 = 图谱具备回答该问题的数据路径（关系边/类型在），即本体
    结构层面"能答"；具体到某个实例的取值属运行时查询，不在构建验收范围。
    """
    import re as _re

    lines = sparql.split("\n")
    out = [ln for ln in lines
           if not _re.search(r'FILTER\s*\(\s*STR\s*\(\s*\w+\s*\)\s*=\s*"[^"]*某', ln)]
    return "\n".join(out)


def _run_cq_sparql(sparql: str, project_id: int,
                   execute_fn=None) -> dict | None:
    """执行单条 CQ 的 SPARQL（失败返回 None → 调用方回落检索式）。

    0 行且模板含参数占位 → 放宽重试一次（验证数据路径）。
    """
    import app.services.cq_service as _self

    _exec = execute_fn or _self._default_sparql_exec
    try:
        payload = _exec(sparql, project_id)
    except Exception as exc:  # noqa: BLE001 —— 单条执行失败不阻断评测
        logger.warning(f"[cq-eval] SPARQL 执行失败 project={project_id}: {exc}")
        return None
    relaxed = _relax_cq_sparql(sparql)
    if (payload or {}).get("row_count") == 0 and relaxed != sparql:
        try:
            payload = _exec(relaxed, project_id)
            if (payload or {}).get("row_count", 0) > 0:
                payload["relaxed"] = True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[cq-eval] 放宽执行失败 project={project_id}: {exc}")
    return payload


def _default_sparql_exec(sparql: str, project_id: int) -> dict:
    from app.services import oxigraph_sink

    if oxigraph_sink._STORE is not None:
        # 本进程已持 RocksDB 锁（刚做完命名图重建）：必须走单例连接，
        # 再开短连接会撞自己的锁（"lock hold by current process"）
        return oxigraph_sink.query_sparql(sparql, project_id, 50)
    return oxigraph_sink.query_sparql_standalone(sparql, project_id=project_id, limit=50)


def evaluate_cqs(db: Session, project_id: int, trigger: str = "manual",
                 retrieve_fn=None, created_by: int | None = None,
                 baseline_version_id: int | None = None,
                 candidate_version_id: int | None = None,
                 sparql_exec_fn=None) -> CqEvaluation | None:
    """CQ 评测（O3-B12②；2026-11-08 升级双口径）。

    有 SPARQL 模板的 CQ → 真执行（mode='sparql'，answerable=SELECT 有行 / ASK 真，
    source_count=行数，coverage=1.0）；执行失败或无模板 → 回落检索式
    （mode='retrieval'，answerable = 检索返回 ≥ANSWERABLE_MIN_SOURCES 条引用，
    coverage = 真实证据占比）。整体 rate = 各 CQ coverage 均值。
    retrieve_fn / sparql_exec_fn 可注入（测试/离线评估）。
    """
    cqs = list_cqs(db, project_id, enabled_only=True)
    if not cqs:
        return None
    _retrieve = retrieve_fn or default_retrieve
    results: list[dict] = []
    n_sparql = 0
    for cq in cqs:
        tpl = (getattr(cq, "sparql", None) or "").strip()
        executed = False
        if tpl:
            payload = _run_cq_sparql(tpl, project_id, execute_fn=sparql_exec_fn)
            if payload is not None:
                executed = True
                n_sparql += 1
                if payload.get("type") == "boolean":
                    ok = bool(payload.get("value"))
                    rows = 1 if ok else 0
                else:
                    rows = int(payload.get("row_count") or 0)
                    ok = rows > 0
                if ok:
                    results.append({
                        "cq_id": cq.id, "question": cq.question,
                        "mode": "sparql", "answerable": True,
                        "source_count": rows, "coverage": 1.0,
                        "relaxed": bool(payload.get("relaxed")),
                    })
                    continue
                # SPARQL 0 行 ≠ 一定无据（模板写错/属性值不在图都会 0 行）：
                # 回落检索式，两条路都不通才判 False
                executed = False
        if executed:
            continue
        try:
            sources = _retrieve(RetrievalQuery(
                project_id=project_id, question=cq.question,
                top_k=8, use_graph=True, use_inferred=False), db=db) or []
        except Exception as exc:  # noqa: BLE001 —— 单条失败不影响整份报告
            logger.warning(f"[cq-eval] project={project_id} cq={cq.id} 检索失败：{exc}")
            sources = []
        n = len(sources)
        evidence = 0
        for s in sources:
            doc = getattr(s, "source_document_id", None)
            quote = getattr(s, "quote", None)
            if doc or (quote and str(quote).strip()):
                evidence += 1
        coverage = round(evidence / n, 3) if n else 0.0
        results.append({
            "cq_id": cq.id, "question": cq.question,
            "mode": "retrieval", "answerable": n >= ANSWERABLE_MIN_SOURCES,
            "source_count": n, "coverage": coverage,
        })
    answerable = sum(1 for r in results if r["answerable"])
    rate = round(sum(r["coverage"] for r in results) / len(results), 3) if results else 0.0
    row = CqEvaluation(
        project_id=project_id,
        trigger=trigger if trigger in ("manual", "auto_post_extract", "publish_gate") else "manual",
        mode="sparql+retrieval" if n_sparql else "retrieval",
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        total_cq=len(results),
        answerable_count=answerable,
        coverage_rate=rate,
        results=results,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def serialize_cq(cq: CompetencyQuestion) -> dict:
    return {"id": cq.id, "project_id": cq.project_id, "question": cq.question,
            "answer_criteria": cq.answer_criteria, "sparql": getattr(cq, "sparql", None),
            "source": cq.source,
            "sort_order": cq.sort_order, "enabled": bool(cq.enabled),
            "created_at": cq.created_at.isoformat() if cq.created_at else None}


def serialize_evaluation(ev: CqEvaluation) -> dict:
    return {"id": ev.id, "project_id": ev.project_id, "trigger": ev.trigger,
            "mode": ev.mode, "total_cq": ev.total_cq,
            "answerable_count": ev.answerable_count,
            "coverage_rate": float(ev.coverage_rate or 0),
            "results": ev.results or [],
            "created_at": ev.created_at.isoformat() if ev.created_at else None}


__all__ = ["list_cqs", "create_cqs", "update_cq", "delete_cq", "reorder_cqs",
           "guidance_from_cqs", "evaluate_cqs", "serialize_cq", "serialize_evaluation",
           "generate_cqs", "attach_sparql", "ensure_rdf_graphs", "ANSWERABLE_MIN_SOURCES"]

# ── CQ 自动生成（外部方法论对照「能力问题法」：本体必须先声明它能回答什么）──

MAX_CQ_MATERIAL_CLASSES = 150
MAX_CQ_MATERIAL_CHARS = 12000   # 对齐 axiom_llm/selfcheck 素材预算口径
MAX_GENERATED_CQS = 12
# 项目已有这么多启用 CQ 时不再自动生成（尊重手工/演进资产）
GENERATE_SKIP_THRESHOLD = 5

_CQ_GEN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "能力问题，中文，可带具体类名/实例名"},
                    "answer_criteria": {"type": "string",
                                        "description": "判定标准：怎样的本体/数据算能回答这个问题"},
                },
                "required": ["question"],
            },
        },
    },
    "required": ["questions"],
}

_CQ_GEN_SYSTEM_PROMPT = (
    "你是本体工程专家（能力问题法/CQ）。给定一份业务本体的类清单与关系清单，"
    "请设计本体的【能力问题】：建这个本体就是为了回答的业务问题。\n"
    "规则：\n"
    "1. 问题必须只使用清单中的类名/关系名与业务术语，禁止发明清单外的业务概念；"
    "若素材中没有类清单（前置模式），问题直接从文档业务内容设计，使用文中业务术语；\n"
    "2. 覆盖面：对象查询（某类有哪些 X）、关系追溯（X 关联到哪些 Y）、"
    "属性查询（某 X 的某属性）、规则判断（满足什么条件会怎样）四类都要有；\n"
    "3. 问题具体可判（能想清楚答案长什么样），不要空泛（如「这个领域有什么」）；\n"
    f"4. 最多 {MAX_GENERATED_CQS} 条，按业务重要性排序；每条给 answer_criteria（一句话判定标准）。\n"
    "只输出一个 JSON 对象：{\"questions\": [{\"question\": \"...\", \"answer_criteria\": \"...\"}]}。"
)


def _cq_doc_material(doc_texts: list[str], domain: str = "", description: str = "") -> str:
    """CQ 生成素材（前置模式）：文档头部抽样 + 领域描述（无骨架可用，预算内截断）。"""
    parts = []
    if domain.strip():
        parts.append(f"【知识域】{domain.strip()}")
    if description.strip():
        parts.append(f"【项目背景】{description.strip()[:400]}")
    total = 0
    excerpt = []
    for t in doc_texts or []:
        piece = str(t or "").strip()[:800]
        if not piece or total + len(piece) > MAX_CQ_MATERIAL_CHARS:
            break
        excerpt.append(piece)
        total += len(piece)
    parts.append("【文档内容抽样】\n" + "\n---\n".join(excerpt))
    return "\n\n".join(parts)


def _cq_material(schema: dict) -> str:
    """CQ 生成素材：类清单（含定义）+ 关系清单，预算内截断（纯函数可测）。"""
    class_lines = []
    chars = 0
    for c in schema.get("classes") or []:
        label = str((c or {}).get("label") or "").strip()
        if not label:
            continue
        d = str((c or {}).get("definition") or "").strip()[:40]
        line = f"- {label}" + (f"：{d}" if d else "")
        if chars + len(line) > MAX_CQ_MATERIAL_CHARS or len(class_lines) >= MAX_CQ_MATERIAL_CLASSES:
            break
        class_lines.append(line)
        chars += len(line)
    rel_lines = []
    for op in (schema.get("object_properties") or [])[:80]:
        if not isinstance(op, dict) or not str(op.get("label") or "").strip():
            continue
        d = str(op.get("domain") or "").strip() or "?"
        r = str(op.get("range") or "").strip() or "?"
        rel_lines.append(f"- {d} -[{op.get('label')}]-> {r}")
    return ("【类清单】\n" + "\n".join(class_lines)
            + "\n\n【关系清单】\n" + ("\n".join(rel_lines) if rel_lines else "（无）"))


# URI 约定（oxigraph_sink.build_project_quads 的落图口径）——SPARQL 模板生成依据
_SPARQL_URI_GUIDE = (
    "平台图谱 URI 约定：\n"
    "- 实例类型：?x a <urn:onto:{pid}:class/类名>（如 class/贷款合同）\n"
    "- 实例关系：?x <urn:onto:{pid}:predicate/关系名> ?y\n"
    "- 实例名：?x rdfs:label ?label\n"
    "- 问父类的实例时用属性路径：?x a ?t . ?t rdfs:subClassOf* <urn:onto:{pid}:class/父类名>"
)

_SPARQL_GEN_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "与给定能力问题逐字一致"},
                    "sparql": {"type": "string",
                               "description": "SELECT 或 ASK 查询（平台 URI 约定）"},
                },
                "required": ["question", "sparql"],
            },
        },
    },
    "required": ["queries"],
}

_SPARQL_GEN_SYSTEM_PROMPT = (
    "你是 SPARQL 专家。把给定的能力问题翻译成可在平台图谱上执行的 SELECT/ASK 查询。\n"
    "规则：\n"
    "1. 只允许 SELECT 或 ASK；禁止 UPDATE/CONSTRUCT 之外的写语法（CONSTRUCT 也不允许）；\n"
    "2. URI 严格按平台约定拼装，类名/关系名逐字取自类清单；\n"
    "3. 前缀声明：PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> 、"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>；\n"
    "4. 答不了的问（素材里完全没有依据）直接跳过，不要硬编。\n"
    "只输出一个 JSON 对象：{\"queries\": [{\"question\": \"...\", \"sparql\": \"...\"}]}。"
)


def _valid_cq_sparql(q: str) -> bool:
    """模板合法性：PREFIX 声明后接 SELECT/ASK，禁一切更新语法。"""
    import re as _re

    t = (q or "").strip()
    if not t:
        return False
    if _re.search(r"\b(INSERT|DELETE|DROP|CLEAR|LOAD|CREATE|MOVE|COPY)\b", t, _re.I):
        return False
    body = t
    for _ in range(8):  # 逐条剥掉全部 PREFIX 声明
        stripped = _re.sub(r"^\s*PREFIX\s+\S+:\s*<[^>]*>\s*", "", body, flags=_re.I)
        if stripped == body:
            break
        body = stripped
    return bool(_re.match(r"^(SELECT|ASK)\b", body, _re.I))


def attach_sparql(db: Session, project_id: int, llm=None) -> dict:
    """为缺模板的启用 CQ 生成 SPARQL（骨架就绪后一次调用；评测前跑）。

    失败静默（返回 skipped），评测自然回落检索式口径。
    """
    from app.infrastructure.database import Project

    cqs = [c for c in list_cqs(db, project_id, enabled_only=True)
           if not (c.sparql or "").strip()]
    if not cqs:
        return {"attached": 0, "skipped": "已有模板"}
    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"attached": 0, "skipped": "project_not_found"}
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
    material = _cq_material(schema)
    uri_guide = _SPARQL_URI_GUIDE.replace("{pid}", str(project_id))
    if llm is None:
        try:
            from app.adapters.provider import build_legacy_llm_config
            from app.infrastructure.llm_client import LLMClient

            cfg = build_legacy_llm_config(db, project_id)
            llm = LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                            model=cfg.get("model"),
                            think_mode="disabled" if cfg.get("disable_think") else None,
                            streaming_enabled=cfg.get("streaming_enabled"))
        except Exception as exc:  # noqa: BLE001
            return {"attached": 0, "skipped": f"模型不可用: {exc}"}
    user = (uri_guide + "\n\n【类清单】\n"
            + "\n".join(f"- {c.question}" for c in cqs[:MAX_GENERATED_CQS])
            + "\n\n为上面每条能力问题生成 sparql。")
    user += "\n\n【类与关系清单（URI 拼装用）】\n" + material
    try:
        from app.services.axiom_suggestion_service import AXIOM_LLM_TIMEOUT

        resp = llm.call_llm(system_prompt=_SPARQL_GEN_SYSTEM_PROMPT, user_prompt=user,
                            json_schema=_SPARQL_GEN_JSON_SCHEMA, timeout=AXIOM_LLM_TIMEOUT, max_retries=2)
    except Exception as exc:  # noqa: BLE001
        return {"attached": 0, "skipped": f"LLM 调用失败: {exc}"}
    if isinstance(resp, dict) and isinstance(resp.get("content"), str):
        import json as _json

        try:
            resp = _json.loads(resp["content"].strip().strip("`").removeprefix("json"))
        except ValueError:
            resp = None
    attached = 0
    by_q = {str(c.question or "").strip(): c for c in cqs}
    for item in (resp or {}).get("queries") or []:
        if not isinstance(item, dict):
            continue
        q = str(item.get("question") or "").strip()
        tpl = str(item.get("sparql") or "").strip()
        row = by_q.get(q)
        if row is None or not _valid_cq_sparql(tpl):
            continue
        row.sparql = tpl[:4000]
        attached += 1
    return {"attached": attached}


def ensure_rdf_graphs(project_id: int, rebuild: bool = False) -> str:
    """确保项目命名图可查且新鲜（SPARQL 评测前置）。

    rebuild=True（管线刚改完全部图谱数据时必须）：无条件先重建命名图——只查
    "图存在"会拿着上一轮的旧图评测（2026-11-08 实测：构建后评测查到陈旧 ABox）。
    RocksDB 独占锁约束下的安全策略：锁被 worker-rdf 持有（celery 部署）→ 登记
    outbox 事件由 worker-rdf 重建，返回 "queued"（本轮评测回落检索式）；
    锁空闲（本地 inline 部署）→ 就地重建，返回 "ready"。
    """
    from app.services.oxigraph_sink import query_sparql_standalone

    def _rebuild_now() -> str:
        try:
            from app.infrastructure.database import SessionLocal
            from app.services.oxigraph_sink import write_project_graphs

            db = SessionLocal()
            try:
                write_project_graphs(db, project_id)
            finally:
                db.close()
            return "ready"
        except Exception as exc:  # noqa: BLE001 —— StoreBusy（worker-rdf 在写）等
            _emit_ttl_rebuild(project_id, reason=str(exc)[:120])
            return "queued"

    if rebuild:
        return _rebuild_now()

    probe = "SELECT ?s WHERE { ?s ?p ?o } LIMIT 1"
    try:
        res = query_sparql_standalone(probe, project_id=project_id, limit=1)
    except Exception as exc:  # noqa: BLE001 —— StoreBusy/其他：锁被持有
        _emit_ttl_rebuild(project_id, reason=str(exc)[:120])
        return "queued"
    note = str(res.get("note") or "")
    if "尚无命名图" in note:
        return _rebuild_now()
    return "ready" if res.get("rows") else "empty"


def _emit_ttl_rebuild(project_id: int, reason: str = "") -> None:
    """登记 ttl.rebuild outbox 事件（worker-rdf 的 drain 任务会消费重建）。"""
    try:
        from app.infrastructure.database import OutboxEvent, SessionLocal

        db = SessionLocal()
        try:
            db.add(OutboxEvent(aggregate_type="project", aggregate_id=project_id,
                               event_type="ttl.rebuild", payload={"reason": reason}))
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        pass


def _parse_cq_payload(payload: Any) -> list[dict]:
    """解析 LLM 输出 → create_cqs 入参（宽容：非 dict 元素跳过）。"""
    items = []
    if not isinstance(payload, dict):
        return items
    if not isinstance(payload.get("questions"), list):
        return items
    for q in payload["questions"]:
        if isinstance(q, dict) and str(q.get("question") or "").strip():
            items.append({"question": str(q["question"]).strip(),
                          "answer_criteria": str(q.get("answer_criteria") or "").strip()})
    return items[:MAX_GENERATED_CQS]


def generate_cqs(db: Session, project_id: int, created_by: int | None = None,
                 llm=None, doc_texts: Optional[list[str]] = None) -> dict:
    """自动生成能力问题并入库（CQ 前置：管线解析完成后、骨架抽取前调用）。

    doc_texts 给定时走文档模式（外部方法论：CQ 先于建模圈定"本体要能回答什么"，
    经 guidance_from_cqs 注入骨架抽取 prompt）；否则回退从当前骨架生成。
    项目已有 ≥GENERATE_SKIP_THRESHOLD 条启用 CQ 时跳过（不覆盖手工资产）；
    模型不可用/调用失败静默跳过（返回 skipped 原因），不阻断管线。
    """
    enabled = list_cqs(db, project_id, enabled_only=True)
    if len(enabled) >= GENERATE_SKIP_THRESHOLD:
        return {"generated": 0, "skipped": f"已有 {len(enabled)} 条能力问题"}
    from app.infrastructure.database import Project

    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"generated": 0, "skipped": "project_not_found"}
    if doc_texts:
        material = _cq_doc_material(doc_texts,
                                    domain=str(proj.domains or ""),
                                    description=str(proj.description or ""))
    else:
        gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
        schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
        material = _cq_material(schema)
    if not material.strip("- \n"):
        return {"generated": 0, "skipped": "没有可用的素材（骨架为空或文档未解析）"}
    if llm is None:
        try:
            from app.adapters.provider import build_legacy_llm_config
            from app.infrastructure.llm_client import LLMClient

            cfg = build_legacy_llm_config(db, project_id)
            llm = LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
                            model=cfg.get("model"),
                            think_mode="disabled" if cfg.get("disable_think") else None,
                            streaming_enabled=cfg.get("streaming_enabled"))
        except Exception as exc:  # noqa: BLE001
            return {"generated": 0, "skipped": f"模型不可用: {exc}"}
    try:
        from app.services.axiom_suggestion_service import AXIOM_LLM_TIMEOUT

        resp = llm.call_llm(system_prompt=_CQ_GEN_SYSTEM_PROMPT, user_prompt=material,
                            json_schema=_CQ_GEN_JSON_SCHEMA, timeout=AXIOM_LLM_TIMEOUT, max_retries=2)
    except Exception as exc:  # noqa: BLE001
        return {"generated": 0, "skipped": f"LLM 调用失败: {exc}"}
    if isinstance(resp, dict) and isinstance(resp.get("content"), str):
        import json as _json

        try:
            resp = _json.loads(resp["content"].strip().strip("`").removeprefix("json"))
        except ValueError:
            resp = None
    items = _parse_cq_payload(resp)
    if not items:
        return {"generated": 0, "skipped": "模型未产出能力问题"}
    rows = create_cqs(db, project_id, items, created_by, source="auto")
    return {"generated": len(rows)}
