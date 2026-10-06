# app/tasks/extract_tasks.py - extract 队列任务（docs/design/03 §7/§8，M3-4）
# Schema 抽取：读 document_chunks → extraction.extract_schema（并行+缓存）→
# 产物写 graph_data（nodes/edges 画布兼容 + "schema" 键权威 TBox）→ commit 触发
# M3-3 双写监听器自动同步行表。进度键 progress:{task_id}，取消键 cancel:extract:{task_id}。

from __future__ import annotations

import time
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import finish_task_progress, set_task_progress

_CANCEL_KEY = "cancel:extract:{task_id}"


def _redis_url() -> str:
    from app.services.env_config_service import redis_url
    return redis_url()


def request_cancel(task_id: str) -> bool:
    """API 侧取消：写取消标记（worker 在 chunk 间隙检查）+ 软 revoke。"""
    import redis

    r = redis.Redis.from_url(_redis_url(), socket_connect_timeout=2)
    r.set(_CANCEL_KEY.format(task_id=task_id), "1", ex=24 * 3600)
    return True


def is_cancelled(task_id: str) -> bool:
    import redis

    r = redis.Redis.from_url(_redis_url(), socket_connect_timeout=2)
    return bool(r.get(_CANCEL_KEY.format(task_id=task_id)))


def _grid_position(i: int) -> dict:
    """确定性网格布局（04 §3.1 画布兼容；6 列）。"""
    return {"x": 80 + (i % 6) * 220, "y": 80 + (i // 6) * 150}


def _auto_version(db, project_id: int, kind: str, label: str) -> Optional[int]:
    """抽取完成自动打版（04 §9 版本时机；M3-6）：MinIO/行表故障只告警不判任务失败。"""
    try:
        from app.adapters.versioning import create_version
        from app.infrastructure.database import Project

        proj = db.query(Project).filter(Project.id == project_id).first()
        if proj is None:
            return None
        ver = create_version(db, proj, kind, proj.owner_id or 0, label=label)
        db.commit()
        return ver.version_no
    except Exception as exc:  # noqa: BLE001 — 打版容错：不影响抽取产物
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        print(f"⚠️ [auto-version] project={project_id} kind={kind} 打版失败（不影响任务）: {exc}")
        return None


@celery_app.task(name="app.tasks.extract_tasks.run_schema_extraction",
                 bind=True, max_retries=0, acks_late=True)
def run_schema_extraction(self, project_id: int,
                          document_ids: Optional[list[int]] = None,
                          parallelism: int = 4,
                          chunk_limit: int = 200,
                          guidance: Optional[str] = None) -> dict:
    """Schema 阶段抽取（TBox）。失败置 failed 并上抛（需人工诊断模型配置后重跑）。

    guidance：用户注入的抽取引导（规则表单组装的纯文本），进每次切片抽取的 prompt
    并随 prompt 哈希参与缓存键；None/空 = 默认通用模式。
    """
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "任务已入队", {"project_id": project_id},
                      queue="extract")

    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import DocumentChunk, Project, SessionLocal, UploadedDocument

    def _cancel_check():
        if is_cancelled(task_id):
            raise _TaskCancelled()

    try:
        db = SessionLocal()
        try:
            # 1) 读切片（项目未删文档；document_ids 可选过滤）
            q = (db.query(DocumentChunk, UploadedDocument.filename)
                 .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
                 .filter(UploadedDocument.project_id == project_id,
                         UploadedDocument.deleted_at.is_(None))
                 .order_by(UploadedDocument.id, DocumentChunk.chunk_index))
            if document_ids:
                q = q.filter(UploadedDocument.id.in_(document_ids))
            rows = q.limit(chunk_limit).all()
            if not rows:
                finish_task_progress(task_id, "failed", "项目没有可用的已解析切片",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_chunks"}
            chunks = [{"index": i, "text": r.text, "doc": fname}
                      for i, (r, fname) in enumerate(rows)]
            set_task_progress(task_id, "extracting", 5,
                              f"读取到 {len(chunks)} 个切片，开始抽取",
                              {"project_id": project_id, "chunks_total": len(chunks)},
                              queue="extract")

            # 2) LLM 并行抽取（缺陷 #5/#2/#9 在 adapters/extraction 内）
            from app.adapters.extraction import extract_schema

            def _progress(done: int, total: int, cache_hits: int) -> None:
                _cancel_check()
                set_task_progress(
                    task_id, "extracting", 5 + int(85 * done / max(total, 1)),
                    f"骨架抽取中 {done}/{total}",
                    {"project_id": project_id, "chunks_done": done,
                     "chunks_total": total, "cache_hits": cache_hits},
                    queue="extract")

            def _stitch_progress(si: int, segs: int) -> None:
                _cancel_check()
                set_task_progress(
                    task_id, "stitching", 90 + int(2 * si / max(segs, 1)),
                    f"跨切片关系缝合 {si}/{segs}",
                    {"project_id": project_id, "stitch_segment": si,
                     "stitch_segments": segs},
                    queue="extract")

            result = extract_schema(chunks, base_uri=f"urn:onto:{project_id}",
                                    parallelism=parallelism,
                                    progress_cb=_progress, cancel_cb=_cancel_check,
                                    guidance=guidance,
                                    stitch_progress_cb=_stitch_progress)

            # 全部切片失败（如 LLM 连接中断）：不写画布，避免用空骨架覆盖已有图谱
            if result.chunks_processed and len(result.warnings) >= result.chunks_processed \
                    and not result.classes:
                finish_task_progress(
                    task_id, "failed",
                    f"全部 {result.chunks_processed} 个切片抽取失败（多为 LLM 连接异常），"
                    f"首因：{result.warnings[0][:120] if result.warnings else '未知'}",
                    stats={"project_id": project_id,
                           "chunks_processed": result.chunks_processed})
                return {"status": "failed", "reason": "all_chunks_failed",
                        "warnings": result.warnings[:10]}

            # 3) 产物写 graph_data（画布兼容节点/边 + "schema" 权威 TBox 键）
            set_task_progress(task_id, "writing", 92, "写入画布与行表",
                              {"project_id": project_id}, queue="extract")
            nodes, id_by_class = _build_nodes(result.classes, chunks, [r[0] for r in rows])
            edges = _build_edges(result.object_properties, id_by_class)
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is None:
                raise RuntimeError(f"项目不存在: {project_id}")
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            gd["nodes"] = nodes
            gd["edges"] = edges
            gd["schema"] = {
                "classes": result.classes,
                "object_properties": result.object_properties,
                "datatype_properties": result.datatype_properties,
                "task_id": task_id,
                "extracted_at": int(time.time()),
            }
            proj.graph_data = gd
            flag_modified(proj, "graph_data")  # 就地改 JSON 列必须显式标记
            db.commit()  # 触发 graph-rows 监听器 → entities/relations 双写
            version_no = _auto_version(db, project_id, "schema", "Schema 抽取")
            stats = {"project_id": project_id,
                     "classes": len(result.classes),
                     "object_properties": len(result.object_properties),
                     "datatype_properties": len(result.datatype_properties),
                     "chunks_processed": result.chunks_processed,
                     "cache_hits": result.cache_hits,
                     "stitched_relations": result.stitched_relations,
                     "version_no": version_no,
                     "warnings": result.warnings[:10]}
            stitch_note = (f"（含跨切片缝合 {result.stitched_relations} 条）"
                           if result.stitched_relations else "")
            finish_task_progress(task_id, "completed",
                                 f"骨架抽取完成：{len(result.classes)} 类 / "
                                 f"{len(result.object_properties)} 关系{stitch_note}", stats=stats)
            return {"status": "completed", **stats}
        finally:
            db.close()
    except _TaskCancelled:
        finish_task_progress(task_id, "cancelled", "用户取消", stats={"project_id": project_id})
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300], stats={"project_id": project_id})
        raise


class _TaskCancelled(Exception):
    """用户取消（cancel:extract 键触发）。"""


def _load_chunks(db, project_id: int, document_ids=None, chunk_limit: int = 200):
    """项目未删文档的切片（document_ids 可选过滤），按文档+序号稳定排序。"""
    from app.infrastructure.database import DocumentChunk, UploadedDocument

    q = (db.query(DocumentChunk, UploadedDocument.filename)
         .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
         .filter(UploadedDocument.project_id == project_id,
                 UploadedDocument.deleted_at.is_(None))
         .order_by(UploadedDocument.id, DocumentChunk.chunk_index))
    if document_ids:
        q = q.filter(UploadedDocument.id.in_(document_ids))
    return q.limit(chunk_limit).all()


def _tbox_from_graph(gd: dict) -> dict:
    """graph_data["schema"] → extract_instances 的 tbox_summary（04 §4.1）。"""
    schema = gd.get("schema") if isinstance(gd, dict) else None
    if not isinstance(schema, dict) or not schema.get("classes"):
        return {}
    classes = {}
    class_props = {}
    for c in schema.get("classes") or []:
        label = str((c or {}).get("label") or "").strip()
        if not label:
            continue
        classes[label] = list((c or {}).get("aliases") or [])
        props = [{"name": str((p or {}).get("name") or "").strip(),
                  "data_type": str((p or {}).get("data_type") or "string"),
                  "description": str((p or {}).get("description") or "")}
                 for p in (c or {}).get("properties") or []
                 if str((p or {}).get("name") or "").strip()]
        if props:
            class_props[label] = props
    ops = {}
    for op in schema.get("object_properties") or []:
        label = str((op or {}).get("label") or "").strip()
        if label:
            ops[label] = {"domain": op.get("domain"), "range": op.get("range")}
    return {"classes": classes, "object_properties": ops, "class_properties": class_props}


@celery_app.task(name="app.tasks.extract_tasks.run_instance_extraction",
                 bind=True, max_retries=0, acks_late=True)
def run_instance_extraction(self, project_id: int,
                            document_ids: Optional[list[int]] = None,
                            strict_gate: bool = True,
                            promote_policy: str = "review",
                            parallelism: int = 4,
                            chunk_limit: int = 200,
                            schema_version_no: Optional[int] = None) -> dict:
    """Instance 阶段抽取（ABox，04 §4）：读切片+TBox → extract_instances（严格闸门）
    → ABox 节点/边并入 graph_data → 行表双写 → 违例物化 review_items。
    schema_version_no 非空时基于该历史框架版本快照抽取（框架复用），否则用当前画布骨架。"""
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "任务已入队", {"project_id": project_id},
                      queue="extract")

    from sqlalchemy.orm.attributes import flag_modified

    from app.infrastructure.database import Project, SessionLocal

    def _cancel_check():
        if is_cancelled(task_id):
            raise _TaskCancelled()

    try:
        db = SessionLocal()
        try:
            rows = _load_chunks(db, project_id, document_ids, chunk_limit)
            if not rows:
                finish_task_progress(task_id, "failed", "项目没有可用的已解析切片",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_chunks"}
            chunks = [{"index": i, "text": r.text, "doc": fname}
                      for i, (r, fname) in enumerate(rows)]

            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is None:
                raise RuntimeError(f"项目不存在: {project_id}")
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            tbox = _tbox_from_graph(gd)
            schema_source = "当前画布框架"
            if schema_version_no is not None:
                from app.adapters.versioning import load_graph_for
                from app.infrastructure.database import OntologyVersion
                ver = (db.query(OntologyVersion)
                       .filter(OntologyVersion.project_id == project_id,
                               OntologyVersion.version_no == schema_version_no)
                       .first())
                if ver is None:
                    finish_task_progress(task_id, "failed",
                                         f"框架版本不存在: v{schema_version_no}",
                                         stats={"project_id": project_id})
                    return {"status": "failed", "reason": "schema_version_not_found"}
                snap_gd = load_graph_for(db, proj, ver)
                snap_tbox = _tbox_from_graph(snap_gd) if snap_gd else {}
                if not snap_tbox:
                    finish_task_progress(task_id, "failed",
                                         f"版本 v{schema_version_no} 快照中没有可用框架",
                                         stats={"project_id": project_id})
                    return {"status": "failed", "reason": "schema_snapshot_empty"}
                tbox = snap_tbox
                schema_source = f"版本 v{schema_version_no} 快照框架"
            if not tbox:
                finish_task_progress(task_id, "failed",
                                     "项目没有骨架（schema），请先完成骨架抽取或指定框架版本",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_schema"}
            set_task_progress(task_id, "extracting", 5,
                              f"读取到 {len(chunks)} 个切片，开始实例抽取（{schema_source}）",
                              {"project_id": project_id, "chunks_total": len(chunks),
                               "schema_version_no": schema_version_no},
                              queue="extract")

            from app.adapters.extraction import extract_instances

            def _progress(done: int, total: int, cache_hits: int) -> None:
                _cancel_check()
                set_task_progress(
                    task_id, "extracting", 5 + int(75 * done / max(total, 1)),
                    f"实例抽取中 {done}/{total}",
                    {"project_id": project_id, "chunks_done": done,
                     "chunks_total": total, "cache_hits": cache_hits},
                    queue="extract")

            result = extract_instances(
                chunks, tbox, base_uri=f"urn:onto:{project_id}",
                strict_gate=strict_gate, promote_policy=promote_policy,
                parallelism=parallelism, progress_cb=_progress, cancel_cb=_cancel_check)

            # 全部切片失败（如 LLM 连接中断）：不写画布，避免用空实例覆盖已有图谱
            if result.chunks_processed and len(result.warnings) >= result.chunks_processed \
                    and not result.entities:
                finish_task_progress(
                    task_id, "failed",
                    f"全部 {result.chunks_processed} 个切片抽取失败（多为 LLM 连接异常），"
                    f"首因：{result.warnings[0][:120] if result.warnings else '未知'}",
                    stats={"project_id": project_id,
                           "chunks_processed": result.chunks_processed})
                return {"status": "failed", "reason": "all_chunks_failed",
                        "warnings": result.warnings[:10]}

            # ABox 节点/边并入画布（幂等：实例节点全量替换，类节点保留）
            set_task_progress(task_id, "writing", 85, "写入画布与行表",
                              {"project_id": project_id}, queue="extract")
            inst_nodes, label_ids, by_label = _build_instance_nodes(result.entities, chunks, [r[0] for r in rows])
            inst_edges = _build_instance_edges(result.relations, by_label, chunks, [r[0] for r in rows])
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is None:
                raise RuntimeError(f"项目不存在: {project_id}")
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            from app.services.graph_rows import CLASS_TYPES

            old_nodes = gd.get("nodes") or []
            class_nodes = [n for n in old_nodes
                           if (n.get("data") or {}).get("type") in CLASS_TYPES]
            class_ids = {str(n.get("id")) for n in class_nodes}
            old_edges = [e for e in (gd.get("edges") or [])
                         if str(e.get("source")) in class_ids
                         and str(e.get("target")) in class_ids]  # 旧实例边随重建丢弃
            gd["nodes"] = class_nodes + inst_nodes
            gd["edges"] = old_edges + inst_edges + _build_type_edges(label_ids, class_nodes)
            proj.graph_data = gd
            flag_modified(proj, "graph_data")

            # 违例物化 review_items（04 §4.1/03 §8：promote/低置信/缺证据，各类型限额）
            review_created = _materialize_gate_reviews(db, project_id, result)
            db.commit()  # 触发 graph-rows 监听器 → entities/relations 双写
            version_no = _auto_version(db, project_id, "full", "实例抽取")

            stats = {"project_id": project_id,
                     "instances": len(result.entities),
                     "relations": len(result.relations),
                     "discarded_count": result.discarded_count,
                     "promote_count": result.promote_count,
                     "missing_evidence_count": result.missing_evidence_count,
                     "low_confidence_count": result.low_confidence_count,
                     "chunks_processed": result.chunks_processed,
                     "cache_hits": result.cache_hits,
                     "review_items_created": review_created,
                     "version_no": version_no,
                     "schema_version_no": schema_version_no,
                     "warnings": result.warnings[:10]}
            finish_task_progress(task_id, "completed",
                                 f"实例抽取完成（{schema_source}）：{len(result.entities)} 实体 / "
                                 f"{len(result.relations)} 关系", stats=stats)
            return {"status": "completed", **stats}
        finally:
            db.close()
    except _TaskCancelled:
        finish_task_progress(task_id, "cancelled", "用户取消", stats={"project_id": project_id})
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300], stats={"project_id": project_id})
        raise


def _build_instance_nodes(entities: list, chunks: list,
                          chunk_rows: Optional[list] = None) -> tuple[list[dict], dict, dict]:
    """实体 → ABox 画布节点（type=owl:NamedIndividual；溯源字段供 graph_rows 拆行）。

    节点 id 确定性：inst_{md5(label|class)[:16]}（04 §4.1），重跑幂等。
    返回 (nodes, label_ids[(label,class)→id], by_label[label→id 首见])。
    """
    import hashlib

    from app.adapters.provenance import locate_quote

    doc_by_idx = {c["index"]: c.get("doc") or "" for c in chunks}
    nodes = []
    label_ids: dict[tuple, str] = {}
    by_label: dict[str, str] = {}
    for i, e in enumerate(entities):
        key = (e.label, e.class_label)
        if key in label_ids:
            continue
        node_id = f"inst_{hashlib.md5(f'{e.label}|{e.class_label}'.encode()).hexdigest()[:16]}"
        label_ids[key] = node_id
        by_label.setdefault(e.label, node_id)
        data = {"label": e.label, "type": "owl:NamedIndividual",
                "class_label": e.class_label, "properties": e.props or {},
                "confidence": e.confidence,
                "source_document": doc_by_idx.get(e.chunk_index, ""),
                "source_chunk_index": e.chunk_index}
        if e.evidence:
            data["source_quote"] = e.evidence
        # 证据字符定位 + 库内定位键（多文档下 source_chunk_index 是全量序号，与库内 chunk_index 不同）
        _row = chunk_rows[e.chunk_index] if (chunk_rows and isinstance(e.chunk_index, int)
                                             and 0 <= e.chunk_index < len(chunk_rows)) else None
        if _row is not None:
            data["source_document_id"] = _row.document_id
            data["source_chunk_no"] = _row.chunk_index
            if e.evidence and 0 <= e.chunk_index < len(chunks):
                pos = locate_quote(str(chunks[e.chunk_index].get("text") or ""), e.evidence)
                if pos is not None:
                    data["source_char_start"], data["source_char_end"] = pos
        nodes.append({"id": node_id, "type": "default",
                      "position": _grid_position(i), "data": data,
                      "style": {"background": "#f8fafc", "border": "2px solid #10b981",
                                "borderRadius": "8px", "padding": "10px"}})
    return nodes, label_ids, by_label


def _build_instance_edges(relations: list, by_label: dict[str, str],
                          chunks: Optional[list] = None,
                          chunk_rows: Optional[list] = None) -> list[dict]:
    """实例关系 → 画布边（端点缺失跳过计数；同 (s,p,o) 去重）。

    同组 (s,p,o) 保留首条的证据与溯源；chunks/chunk_rows 用于证据字符定位。
    """
    from app.adapters.provenance import locate_quote

    edges = []
    seen = set()
    skipped = 0
    for r in relations:
        s = by_label.get(r.subject_label)
        o = by_label.get(r.object_label)
        if s is None or o is None:
            skipped += 1
            continue
        key = (s, r.predicate, o)
        if key in seen:
            continue
        seen.add(key)
        data: dict[str, Any] = {"relation": r.predicate, "type": "ObjectProperty"}
        if r.evidence:
            data["source_quote"] = r.evidence
            data["source_document"] = next((c.get("doc") or "" for c in (chunks or [])
                                            if c.get("index") == r.chunk_index), "")
            data["source_chunk_index"] = r.chunk_index
            _row = chunk_rows[r.chunk_index] if (chunk_rows and isinstance(r.chunk_index, int)
                                                 and 0 <= r.chunk_index < len(chunk_rows)) else None
            if _row is not None:
                data["source_document_id"] = _row.document_id
                data["source_chunk_no"] = _row.chunk_index
                if r.chunk_index < len(chunks or []):
                    pos = locate_quote(str((chunks or [])[r.chunk_index].get("text") or ""), r.evidence)
                    if pos is not None:
                        data["source_char_start"], data["source_char_end"] = pos
        edges.append({"id": f"e_{len(edges)}_{abs(hash(key)) % 100000}",
                      "source": s, "target": o, "type": "smoothstep",
                      "label": r.predicate, "data": data})
    return edges


def _build_type_edges(label_ids: dict, class_nodes: list) -> list[dict]:
    """实例 → 类的 rdf:type 边（instance_of）。

    实例探索页按类展开实例、行表溯源定位都依赖这组边；
    label_ids 的 (label, class) 元组与 class_nodes.data.label 匹配，匹配不到的跳过。
    """
    class_id_by_label: dict[str, str] = {}
    for n in class_nodes:
        data = n.get("data") or {}
        label = str(data.get("label") or "").strip()
        if label and label not in class_id_by_label:
            class_id_by_label[label] = str(n.get("id"))

    edges = []
    seen: set[tuple[str, str]] = set()
    for (label, class_label), inst_id in label_ids.items():
        class_id = class_id_by_label.get(str(class_label).strip())
        if not class_id or (inst_id, class_id) in seen:
            continue
        seen.add((inst_id, class_id))
        edges.append({"id": f"etype_{len(edges)}_{inst_id[-8:]}",
                      "source": inst_id, "target": class_id, "type": "smoothstep",
                      "label": "rdf:type",
                      "data": {"relation": "instance_of", "label": "rdf:type", "type": "Type"}})
    return edges


def _materialize_gate_reviews(db, project_id: int, result) -> int:
    """闸门违例 → review_items（04 §4.1/03 §8）。

    - new_class：promote_candidates（review/auto 政策）
    - low_confidence_entity/relation：confidence < 0.70 的通过项（04 §7.1）
    - missing_evidence：evidence 被降级为摘要的通过项（[chunk n 摘要] 标记）
    每类型限额 50，防审核队列被单次大批次刷爆。
    """
    from app.adapters.schema_gate import LOW_CONFIDENCE_THRESHOLD
    from app.infrastructure.database import ReviewItem

    created = 0
    per_type: dict[str, int] = {}

    def _add(item_type: str, payload: dict, reason: str, priority: str = "medium"):
        nonlocal created
        if per_type.get(item_type, 0) >= 50:
            return
        per_type[item_type] = per_type.get(item_type, 0) + 1
        db.add(ReviewItem(project_id=project_id, item_type=item_type, payload=payload,
                          reason=reason[:255], priority=priority, status="pending"))
        created += 1

    for cand in result.promote_candidates or []:
        if cand.get("kind") not in ("unknown_class",):
            continue  # new_predicate 无对应审核类型，M3-6 扩展
        _add("new_class",
             {"label": cand.get("label"), "chunk_indices": cand.get("chunk_indices"),
              "policy": cand.get("policy"), "auto_promoted": cand.get("auto_promoted")},
             f"新类型「{cand.get('label')}」待审入 TBox",
             priority="high" if cand.get("auto_promoted") else "medium")
    for e in result.entities:
        if (e.confidence or 0.0) < LOW_CONFIDENCE_THRESHOLD:
            _add("low_confidence_entity",
                 {"label": e.label, "class_label": e.class_label,
                  "confidence": e.confidence, "chunk_index": e.chunk_index,
                  "evidence": e.evidence[:300]},
                 f"实体「{e.label}」置信度 {e.confidence:.2f} 低于阈值")
        if (e.evidence or "").startswith("[chunk "):
            _add("missing_evidence",
                 {"label": e.label, "class_label": e.class_label,
                  "chunk_index": e.chunk_index, "downgraded_evidence": e.evidence[:300]},
                 f"实体「{e.label}」证据未定位，已降级为摘要")
    for r in result.relations:
        if (r.confidence or 0.0) < LOW_CONFIDENCE_THRESHOLD:
            _add("low_confidence_relation",
                 {"subject_label": r.subject_label, "predicate": r.predicate,
                  "object_label": r.object_label, "confidence": r.confidence,
                  "chunk_index": r.chunk_index, "evidence": r.evidence[:300]},
                 f"关系「{r.subject_label}-{r.predicate}->{r.object_label}」"
                 f"置信度 {r.confidence:.2f} 低于阈值")
    return created


def _build_nodes(classes: list[dict], chunks: Optional[list] = None,
                 chunk_rows: Optional[list] = None) -> tuple[list[dict], dict[str, str]]:
    """类 → 画布节点（data.type='Class'，与 graph_rows.CLASS_TYPES 判据对齐）。

    chunks/chunk_rows 与抽取时 enumerate 顺序对齐，用于证据字符定位（locate_quote）
    和补全 source_document_id / source_chunk_no（库内 chunk_index，多文档下与全量序号不同）。
    """
    from app.adapters.provenance import locate_quote

    nodes = []
    id_by_class: dict[str, str] = {}
    for i, c in enumerate(classes):
        label = str(c.get("label") or "").strip()
        if not label:
            continue
        node_id = f"cls_{i}_{abs(hash(label)) % 100000}"
        id_by_class[label] = node_id
        props = {p.get("name"): p.get("data_type") for p in (c.get("properties") or [])
                 if p.get("name")}
        data: dict[str, Any] = {"label": label, "type": "Class", "class_label": label,
                                "properties": props, "aliases": c.get("aliases") or []}
        if c.get("definition"):
            data["definition"] = c["definition"]
        # 溯源字段：graph_rows 据此落 ProvenanceRecord（doc 名需与 uploaded_documents.filename 一致）
        _idx = c.get("_source_chunk_index")
        if c.get("_source_doc"):
            data["source_document"] = c["_source_doc"]
            data["source_chunk_index"] = _idx
        if c.get("definition"):
            data["source_quote"] = str(c["definition"])[:1024]
        # 精确定位：优先在首现切片内定位 definition（多为 LLM 改写，常失败），退而定位类名逐字出现处
        if isinstance(_idx, int) and chunks and 0 <= _idx < len(chunks):
            row = chunk_rows[_idx] if chunk_rows and _idx < len(chunk_rows) else None
            if row is not None:
                data["source_document_id"] = row.document_id
                data["source_chunk_no"] = row.chunk_index
            chunk_text = str(chunks[_idx].get("text") or "")
            pos = locate_quote(chunk_text, str(c.get("definition") or "")) \
                if c.get("definition") else None
            if pos is None:
                pos = locate_quote(chunk_text, label)
            if pos is not None:
                data["source_char_start"], data["source_char_end"] = pos
        if c.get("pending"):
            data["pending"] = True
        nodes.append({"id": node_id, "type": "default",
                      "position": _grid_position(i), "data": data,
                      "style": {"background": "#fff", "border": "2px solid #3b82f6",
                                "borderRadius": "8px", "padding": "10px"}})
    return nodes, id_by_class


def _build_edges(object_properties: list[dict], id_by_class: dict[str, str]) -> list[dict]:
    """对象属性 → 画布边（data.relation 供 graph_rows._edge_relation 读取）。"""
    edges = []
    for i, op in enumerate(object_properties or []):
        s = id_by_class.get(str(op.get("domain") or ""))
        t = id_by_class.get(str(op.get("range") or ""))
        if not s or not t:
            continue  # domain/range 类抽取失败时跳过（schema 键仍保留原文）
        data = {"relation": op.get("label"), "type": "ObjectProperty",
                "domain": op.get("domain"), "range": op.get("range")}
        conf = op.get("confidence")
        if isinstance(conf, (int, float)):
            data["confidence"] = round(float(conf), 2)
        # 缝合关系的逐字证据 → 关系级溯源（graph_rows 落 ProvenanceRecord）
        evidence = str(op.get("evidence") or "").strip()
        if evidence:
            data["source_quote"] = evidence[:1024]
        edges.append({"id": f"edge_{i}", "source": s, "target": t,
                      "type": "smoothstep", "label": op.get("label"),
                      "data": data})
    return edges


@celery_app.task(name="app.tasks.extract_tasks.run_resolution_task",
                 bind=True, max_retries=0, acks_late=True)
def run_resolution_task(self, project_id: int, scope: str = "all",
                        thresholds: Optional[dict] = None,
                        blocking: str = "pinyin") -> dict:
    """三层实体消解（04 §5）：自动合并 + 待审聚类物化 review_items(entity_merge)。"""
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "消解任务已入队", {"project_id": project_id},
                      queue="extract")
    try:
        set_task_progress(task_id, "resolving", 20, "三层流水线执行中",
                          {"project_id": project_id}, queue="extract")
        from app.services.resolution_service import run_resolution

        stats = run_resolution(project_id, scope=scope, thresholds=thresholds,
                               blocking=blocking)
        stats["project_id"] = project_id
        finish_task_progress(task_id, "completed",
                             f"消解完成：{stats['auto_merged']} 自动合并 / "
                             f"{stats['review_created']} 待审", stats=stats)
        return {"status": "completed", **stats}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300], stats={"project_id": project_id})
        raise


@celery_app.task(name="app.tasks.extract_tasks.run_conflict_detection_task",
                 bind=True, max_retries=0, acks_late=True)
def run_conflict_detection_task(self, project_id: int,
                                types: Optional[list[str]] = None) -> dict:
    """冲突检测（04 §6）：消解之后运行，检出项物化 review_items(conflict_*)。"""
    task_id = self.request.id
    set_task_progress(task_id, "queued", 1, "冲突检测已入队", {"project_id": project_id},
                      queue="extract")
    try:
        from app.services.resolution_service import run_conflict_detection

        stats = run_conflict_detection(project_id, types=types)
        stats["project_id"] = project_id
        finish_task_progress(task_id, "completed",
                             f"冲突检测完成：{stats['detected']} 检出 / "
                             f"{stats['review_created']} 新建待审", stats=stats)
        return {"status": "completed", **stats}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300], stats={"project_id": project_id})
        raise
