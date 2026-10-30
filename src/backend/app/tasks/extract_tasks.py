# app/tasks/extract_tasks.py - extract 队列任务（docs/design/03 §7/§8，M3-4）
# Schema 抽取：读 document_chunks → extraction.extract_schema（并行+缓存）→
# 产物写 graph_data（nodes/edges 画布兼容 + "schema" 键权威 TBox）→ commit 触发
# M3-3 双写监听器自动同步行表。进度键 progress:{task_id}，取消键 cancel:extract:{task_id}。

from __future__ import annotations

import math
import time
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import finish_task_progress, set_task_progress

_CANCEL_KEY = "cancel:extract:{task_id}"


def _batch_plan(chunk_count: int, batch_size: int) -> dict:
    """批次规划（O1-B1 纯函数，便于单测）。

    chunk_limit 的语义是进度/预估粒度（一批多少切片），不再是截断上限：
    全量切片都会被处理，SSE 按 batch_no/batches_total 汇报批次进度。
    """
    size = max(1, int(batch_size or 0))
    batches = max(1, math.ceil(chunk_count / size)) if chunk_count > 0 else 0
    return {"chunks_total": chunk_count, "batch_size": size, "batches_total": batches}


def _batch_no(done: int, batch_size: int, batches_total: int) -> int:
    """done（已完成切片数）→ 当前批次号（1-based，封顶 batches_total）。"""
    if done <= 0 or batches_total <= 0:
        return 1
    return min(batches_total, (done - 1) // max(1, batch_size) + 1)


def _eta_seconds(done: int, total: int, elapsed: float) -> Optional[int]:
    """按已完成比例线性外推剩余秒数；done=0 或已全部完成时返回 None。"""
    if done <= 0 or total <= done or elapsed < 0:
        return None
    return int(elapsed / done * (total - done))


def _humanize_seconds(s: Optional[int]) -> str:
    """剩余秒数 → 人话（"约 40 秒"/"约 3 分钟"），None 返回空串。"""
    if s is None:
        return ""
    if s < 60:
        return f"约 {max(s, 1)} 秒"
    minutes = s // 60
    if minutes < 60:
        return f"约 {minutes} 分钟"
    return f"约 {minutes // 60} 小时 {minutes % 60} 分"


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


# ---- 派发入口：优先投 Celery extract 队列；无 worker（或 broker 不可达）时进程内后台线程执行 ----
# 与 parse_tasks.dispatch_parse 同一模式：uvicorn 单进程部署（不另起 worker）下，
# 骨架/实例抽取也能直接执行，避免任务投进队列后无人消费、永远停在"已入队"。
# inline 的 task_id 形如 inline-<hex>，作为 celery task.apply 的 task_id 传入，
# 任务体内的 self.request.id 与之对齐 → 进度键/取消键/SSE 与 Celery 模式完全一致。

def _extract_worker_alive(timeout: float = 2.0) -> bool:
    """探测是否有 Celery worker 在线且消费 extract 队列。

    返回 False 表示：无任何 worker / broker 不可达 / 无消费 extract 队列的 worker——
    调用方应降级为进程内执行，避免任务投进队列后无人消费。
    """
    from app.core.logging import logger

    try:
        pong = celery_app.control.ping(timeout=timeout)  # {worker: {'ok': 'pong'}}
        if not pong:
            return False
    except Exception as e:  # noqa: BLE001 —— broker 不可达
        logger.warning(f"[extract dispatch] worker 探测失败（broker 不可达？）: {e}")
        return False
    try:
        queues = celery_app.control.inspect().active_queues() or {}
    except Exception:  # noqa: BLE001 —— 探测异常按无 worker 处理
        queues = {}
    for worker_queues in queues.values():
        if any(str(q.get("name")) == "extract" for q in worker_queues):
            return True
    return False


def _dispatch(task, args: tuple, kwargs: dict, project_id: int) -> dict:
    """通用派发：worker 存活 → Celery 入队；否则进程内后台线程同步执行。

    返回 {"mode": "celery"|"inline", "task_id": str}。
    """
    import threading
    import uuid

    from app.core.logging import logger

    if _extract_worker_alive():
        try:
            ar = task.apply_async(args=args, kwargs=kwargs)
            return {"mode": "celery", "task_id": ar.id}
        except Exception as e:  # noqa: BLE001 —— 派发失败降级进程内
            logger.warning(f"[extract dispatch] Celery 派发失败，降级进程内执行: {e}")
    task_id = f"inline-{uuid.uuid4().hex[:12]}"

    def _worker() -> None:
        try:
            task.apply(args=args, kwargs=kwargs, task_id=task_id)
        except Exception as e:  # noqa: BLE001 —— 任务体已置 failed，兜底记录
            logger.error(f"[extract dispatch] 进程内执行 {task.name} "
                         f"project={project_id} 失败: {e}")

    threading.Thread(target=_worker, name=f"extract-inline-{project_id}",
                     daemon=True).start()
    return {"mode": "inline", "task_id": task_id}


def dispatch_schema_extraction(project_id: int,
                               document_ids: Optional[list[int]] = None,
                               parallelism: int = 4,
                               chunk_limit: int = 200,
                               guidance: Optional[str] = None,
                               model_config_id: Optional[int] = None,
                               strict_config: Optional[list[dict]] = None) -> dict:
    """派发骨架抽取（03 §8）：详见 _dispatch。返回 {"mode", "task_id"}。"""
    return _dispatch(
        run_schema_extraction,
        (project_id, document_ids, parallelism, chunk_limit),
        {"guidance": guidance, "model_config_id": model_config_id,
         "strict_config": strict_config},
        project_id)


def dispatch_instance_extraction(project_id: int,
                                 document_ids: Optional[list[int]] = None,
                                 strict_gate: bool = True,
                                 promote_policy: str = "review",
                                 parallelism: int = 4,
                                 chunk_limit: int = 200,
                                 schema_version_no: Optional[int] = None,
                                 model_config_id: Optional[int] = None) -> dict:
    """派发实例抽取（04 §4）：详见 _dispatch。返回 {"mode", "task_id"}。"""
    return _dispatch(
        run_instance_extraction,
        (project_id, document_ids, strict_gate, promote_policy, parallelism,
         chunk_limit, schema_version_no),
        {"model_config_id": model_config_id},
        project_id)


def dispatch_resolution(project_id: int, scope: str = "all",
                        thresholds: Optional[dict] = None,
                        blocking: str = "pinyin") -> dict:
    """派发三层实体消解（04 §5）：详见 _dispatch。返回 {"mode", "task_id"}。"""
    return _dispatch(
        run_resolution_task,
        (project_id, scope, thresholds, blocking),
        {},
        project_id)


def dispatch_conflict_detection(project_id: int,
                                types: Optional[list[str]] = None) -> dict:
    """派发冲突检测（04 §6）：详见 _dispatch。返回 {"mode", "task_id"}。"""
    return _dispatch(
        run_conflict_detection_task,
        (project_id,),
        {"types": types},
        project_id)


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
                          guidance: Optional[str] = None,
                          model_config_id: Optional[int] = None,
                          strict_config: Optional[list[dict]] = None) -> dict:
    """Schema 阶段抽取（TBox）。失败置 failed 并上抛（需人工诊断模型配置后重跑）。

    guidance：用户注入的抽取引导（规则表单组装的纯文本），进每次切片抽取的 prompt
    并随 prompt 哈希参与缓存键；None/空 = 默认通用模式。
    strict_config：主体配置模板行（严格白名单模式），prompt 强约束 + 归并硬过滤。
    model_config_id：发起者个人选择的 model_configs 行（API 层校验后传入）。
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
            # 1) 读切片（项目未删文档；document_ids 可选过滤）——O1-B1：全量读取，不再
            #    limit(chunk_limit) 静默截断；chunk_limit 只作为批次/预估粒度
            q = (db.query(DocumentChunk, UploadedDocument.filename)
                 .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
                 .filter(UploadedDocument.project_id == project_id,
                         UploadedDocument.deleted_at.is_(None))
                 .order_by(UploadedDocument.id, DocumentChunk.chunk_index))
            if document_ids:
                q = q.filter(UploadedDocument.id.in_(document_ids))
            rows = q.all()
            if not rows:
                finish_task_progress(task_id, "failed", "项目没有可用的已解析切片",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_chunks"}
            chunks = [{"index": i, "text": r.text, "doc": fname}
                      for i, (r, fname) in enumerate(rows)]
            plan = _batch_plan(len(chunks), chunk_limit)
            t0 = time.monotonic()
            set_task_progress(task_id, "extracting", 5,
                              f"读取到 {len(chunks)} 个切片，分 {plan['batches_total']} 批开始抽取",
                              {"project_id": project_id, **plan},
                              queue="extract")

            # 2) LLM 并行抽取（缺陷 #5/#2/#9 在 adapters/extraction 内；全量切片单次
            #    归并+跨切片缝合，保证 200+ 批次边界的类间关系不漏缝）
            from app.adapters.extraction import extract_schema

            def _progress(done: int, total: int, cache_hits: int) -> None:
                _cancel_check()
                eta_s = _eta_seconds(done, total, time.monotonic() - t0)
                eta = _humanize_seconds(eta_s)
                set_task_progress(
                    task_id, "extracting", 5 + int(85 * done / max(total, 1)),
                    f"骨架抽取中 {done}/{total}"
                    f"（批次 {_batch_no(done, plan['batch_size'], plan['batches_total'])}"
                    f"/{plan['batches_total']}）{('，剩余' + eta) if eta else ''}",
                    {"project_id": project_id, "chunks_done": done,
                     "chunks_total": total, "cache_hits": cache_hits,
                     "batch_no": _batch_no(done, plan['batch_size'], plan['batches_total']),
                     "batches_total": plan['batches_total'],
                     "eta_seconds": eta_s},
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
                                    strict_config=strict_config,
                                    model_config_id=model_config_id,
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
            # O1-B2：抽取完成自动出 TBox 机检报告（环/悬空 error，命名/层级 warning）
            from app.adapters.schema_checks import run_schema_checks
            checks = run_schema_checks({"classes": result.classes,
                                        "object_properties": result.object_properties})
            stats = {"project_id": project_id,
                     "classes": len(result.classes),
                     "object_properties": len(result.object_properties),
                     "datatype_properties": len(result.datatype_properties),
                     "chunks_processed": result.chunks_processed,
                     "cache_hits": result.cache_hits,
                     "stitched_relations": result.stitched_relations,
                     "strict_filtered": result.strict_filtered,
                     "version_no": version_no,
                     "schema_checks": checks.summary(),
                     "warnings": result.warnings[:10]}
            stitch_note = (f"（含跨切片缝合 {result.stitched_relations} 条）"
                           if result.stitched_relations else "")
            check_note = ""
            if checks.error_count or checks.warning_count:
                check_note = (f"；机检 {checks.error_count} 硬伤 / "
                              f"{checks.warning_count} 提醒")
            finish_task_progress(task_id, "completed",
                                 f"骨架抽取完成：{len(result.classes)} 类 / "
                                 f"{len(result.object_properties)} 关系{stitch_note}{check_note}",
                                 stats=stats)
            # QA 类锚定索引：骨架更新即重建（失败不阻塞抽取结果）
            try:
                from app.adapters.tbox_index import sync_tbox_index
                sync_tbox_index(project_id, gd)
            except Exception:  # noqa: BLE001
                pass
            # O5-B27：抽取完成回调追加首轮诊断（后台线程，不阻塞任务返回）
            try:
                from app.services.copilot_diagnosis import schedule_first_diagnosis
                schedule_first_diagnosis(project_id, trigger="schema_extract")
            except Exception:  # noqa: BLE001 —— 诊断调度失败不影响抽取结果
                pass
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


def _load_chunks(db, project_id: int, document_ids=None):
    """项目未删文档的全部切片（document_ids 可选过滤），按文档+序号稳定排序。

    O1-B1：废除 chunk_limit 截断——超长项目（500+ 切片）必须完整抽取。
    """
    from app.infrastructure.database import DocumentChunk, UploadedDocument

    q = (db.query(DocumentChunk, UploadedDocument.filename)
         .join(UploadedDocument, DocumentChunk.document_id == UploadedDocument.id)
         .filter(UploadedDocument.project_id == project_id,
                 UploadedDocument.deleted_at.is_(None))
         .order_by(UploadedDocument.id, DocumentChunk.chunk_index))
    if document_ids:
        q = q.filter(UploadedDocument.id.in_(document_ids))
    return q.all()


def _tbox_from_graph(gd: dict) -> dict:
    """graph_data["schema"] → extract_instances 的 tbox_summary（04 §4.1）。"""
    schema = gd.get("schema") if isinstance(gd, dict) else None
    if not isinstance(schema, dict) or not schema.get("classes"):
        return {}
    classes = {}
    class_props = {}
    # schema 级 datatype_properties 挂到类：UI 类面板属性即来源于此（domains 引用类 id），
    # 实例抽取 prompt 的【各类属性清单】必须与 UI 同源，否则模型只能自行发明属性名
    dtype_by_cls_id: dict[str, list[dict]] = {}
    for dp in schema.get("datatype_properties") or []:
        # 两套键约定：画布创建用 label，schema 抽取（M3 契约）用 name
        name = str((dp or {}).get("label") or (dp or {}).get("name") or "").strip()
        if not name:
            continue
        item = {"name": name,
                "data_type": str((dp or {}).get("data_type") or "string"),
                "description": str((dp or {}).get("description") or "")}
        for d in (dp or {}).get("domains") or []:
            dtype_by_cls_id.setdefault(str(d), []).append(item)
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
        props += dtype_by_cls_id.get(str((c or {}).get("id") or ""), [])
        # 按 name 去重（类内 properties 与 schema 级可能重复声明）
        seen_names: set[str] = set()
        uniq = []
        for p in props:
            if p["name"] not in seen_names:
                seen_names.add(p["name"])
                uniq.append(p)
        if uniq:
            class_props[label] = uniq
    ops = {}
    for op in schema.get("object_properties") or []:
        label = str((op or {}).get("label") or "").strip()
        if label:
            ops[label] = {"domain": op.get("domain"), "range": op.get("range")}
    # 未挂靠任何类的 schema 级属性 → 属性建议（类未声明属性时实例抽取可用，
    # 解决"服务细项详情抽不出"：细项声明存在但没归属，模型被键名白名单捆死）
    suggested = [str((dp or {}).get("label") or (dp or {}).get("name") or "").strip()
                 for dp in schema.get("datatype_properties") or []
                 if str((dp or {}).get("label") or (dp or {}).get("name") or "").strip()
                 and not (dp or {}).get("domains")]
    return {"classes": classes, "object_properties": ops, "class_properties": class_props,
            "suggested_properties": suggested[:40]}


@celery_app.task(name="app.tasks.extract_tasks.run_instance_extraction",
                 bind=True, max_retries=0, acks_late=True)
def run_instance_extraction(self, project_id: int,
                            document_ids: Optional[list[int]] = None,
                            strict_gate: bool = True,
                            promote_policy: str = "review",
                            parallelism: int = 4,
                            chunk_limit: int = 200,
                            schema_version_no: Optional[int] = None,
                            model_config_id: Optional[int] = None) -> dict:
    """Instance 阶段抽取（ABox，04 §4）：读切片+TBox → extract_instances（严格闸门）
    → ABox 节点/边并入 graph_data → 行表双写 → 违例物化 review_items。
    schema_version_no 非空时基于该历史框架版本快照抽取（框架复用），否则用当前画布骨架。
    model_config_id：发起者个人选择的 model_configs 行（API 层校验后传入）。"""
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
            rows = _load_chunks(db, project_id, document_ids)
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
            source_gd = gd  # B7：反向属性从抽取所用框架的 schema 读取（版本快照时随快照）
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
                source_gd = snap_gd if isinstance(snap_gd, dict) else {}
                schema_source = f"版本 v{schema_version_no} 快照框架"
            if not tbox:
                finish_task_progress(task_id, "failed",
                                     "项目没有骨架（schema），请先完成骨架抽取或指定框架版本",
                                     stats={"project_id": project_id})
                return {"status": "failed", "reason": "no_schema"}
            plan = _batch_plan(len(chunks), chunk_limit)
            t0 = time.monotonic()
            set_task_progress(task_id, "extracting", 5,
                              f"读取到 {len(chunks)} 个切片，分 {plan['batches_total']} 批开始实例抽取（{schema_source}）",
                              {"project_id": project_id, **plan,
                               "schema_version_no": schema_version_no},
                              queue="extract")

            from app.adapters.extraction import ExtractedEntity, ExtractedRelation, extract_instances

            def _progress(done: int, total: int, cache_hits: int) -> None:
                _cancel_check()
                eta_s = _eta_seconds(done, total, time.monotonic() - t0)
                eta = _humanize_seconds(eta_s)
                set_task_progress(
                    task_id, "extracting", 5 + int(75 * done / max(total, 1)),
                    f"实例抽取中 {done}/{total}"
                    f"（批次 {_batch_no(done, plan['batch_size'], plan['batches_total'])}"
                    f"/{plan['batches_total']}）{('，剩余' + eta) if eta else ''}",
                    {"project_id": project_id, "chunks_done": done,
                     "chunks_total": total, "cache_hits": cache_hits,
                     "batch_no": _batch_no(done, plan['batch_size'], plan['batches_total']),
                     "batches_total": plan['batches_total'],
                     "eta_seconds": eta_s},
                    queue="extract")

            from app.core.config import settings as app_settings

            result = extract_instances(
                chunks, tbox, base_uri=f"urn:onto:{project_id}",
                strict_gate=strict_gate, promote_policy=promote_policy,
                parallelism=parallelism, progress_cb=_progress, cancel_cb=_cancel_check,
                model_config_id=model_config_id,
                coref_enabled=bool(app_settings.COREF_ENABLED),
                pattern_enabled=bool(app_settings.PATTERN_LAYER_ENABLED))

            # O2-B7 反向属性自动补全：声明了 inverse_of 的谓词对，写入一边补另一边
            inverse_added = 0
            source_schema = (source_gd or {}).get("schema") if isinstance(source_gd, dict) \
                else None
            if source_schema:
                from app.adapters.inverse_props import expand_inverse_relations

                result.relations, inverse_added = expand_inverse_relations(
                    result.relations, source_schema)

            # O3-B15 抽取定向修复：闸门违例率超阈值 → 违例最重 chunk 带反馈重抽回并
            repair_stats: dict | None = None
            try:
                from app.services.extraction_repair import targeted_repair

                _passed = len(result.entities) + len(result.relations)
                if _passed + len(result.gate_violations) > 0 and result.gate_violations:
                    def _reextract(idx: int, chunk_text: str, feedback: str) -> list[dict]:
                        _doc = chunks[idx].get("doc") if 0 <= idx < len(chunks) else ""
                        _r = extract_instances(
                            [{"index": idx, "text": chunk_text, "doc": _doc}], tbox,
                            base_uri=f"urn:onto:{project_id}", strict_gate=strict_gate,
                            promote_policy=promote_policy, parallelism=1,
                            model_config_id=model_config_id)
                        return ([e.model_dump() for e in _r.entities]
                                + [r.model_dump() for r in _r.relations])

                    repair_stats = targeted_repair(chunks, result.gate_violations,
                                                   reextract_fn=_reextract,
                                                   passed_count=_passed)
                    for item in repair_stats.get("recovered_entities") or []:
                        if not isinstance(item, dict):
                            continue
                        item.pop("_repaired", None)
                        item.pop("_repaired_chunk", None)
                        try:
                            if "predicate" in item:
                                result.relations.append(ExtractedRelation(**item))
                            elif item.get("label"):
                                result.entities.append(ExtractedEntity(**item))
                        except Exception:  # noqa: BLE001 —— 单条修复产物不合规即丢弃
                            continue
            except Exception as _rep_err:  # noqa: BLE001 —— 修复失败不阻塞抽取
                repair_stats = {"error": str(_rep_err)[:200]}

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
            all_old_edges = gd.get("edges") or []
            old_edges = [e for e in all_old_edges
                         if str(e.get("source")) in class_ids
                         and str(e.get("target")) in class_ids]  # 旧实例边随重建丢弃
            # steward 优先：人工编辑/新增的实例与关系、已批准的合并决策不因重抽丢失
            inst_nodes, inst_edges = _preserve_human_decisions(
                db, project_id, old_nodes, all_old_edges, inst_nodes, inst_edges,
                by_label, label_ids, class_nodes)
            gd["nodes"] = class_nodes + inst_nodes
            gd["edges"] = old_edges + inst_edges + _build_type_edges(label_ids, class_nodes)
            proj.graph_data = gd
            flag_modified(proj, "graph_data")

            # 违例物化 review_items（04 §4.1/03 §8：promote/低置信/缺证据，各类型限额）
            review_created = _materialize_gate_reviews(
                db, project_id, result,
                existing_classes=list((tbox.get("classes") or {}).keys()),
                existing_predicates=list((tbox.get("object_properties") or {}).keys()))
            db.commit()  # 触发 graph-rows 监听器 → entities/relations 双写
            version_no = _auto_version(db, project_id, "full", "实例抽取")

            # O3-B13/B12 收尾钩子（全部非阻塞）：漂移物化审核项 + CQ 自动评测
            drift_count = 0
            cq_eval_id = None
            try:
                import logging

                from app.infrastructure.database import CompetencyQuestion
                from app.services.cq_service import evaluate_cqs
                from app.services.impact_service import materialize_drift_reviews

                drift_count = len(materialize_drift_reviews(db, project_id, gd))
                cq_rows = db.query(CompetencyQuestion).filter(
                    CompetencyQuestion.project_id == project_id,
                    CompetencyQuestion.enabled.is_(True)).all()
                if cq_rows:
                    _eval = evaluate_cqs(db, project_id, trigger="auto_post_extract",
                                         created_by=None)
                    cq_eval_id = _eval.id if _eval is not None else None
                db.commit()
            except Exception as _hook_err:  # noqa: BLE001
                db.rollback()
                logging.getLogger(__name__).warning(
                    f"[extract] O3 收尾钩子失败（不阻塞）: {_hook_err}")

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
                     "inverse_relations_added": inverse_added,
                     "coref": result.coref_stats,
                     "pattern_filled": result.pattern_filled_count,
                     "pattern_calibrated": result.pattern_calibrated_count,
                     "repair": repair_stats,
                     "drift_reviews_created": drift_count,
                     "cq_evaluation_id": cq_eval_id,
                     "warnings": result.warnings[:10]}
            finish_task_progress(task_id, "completed",
                                 f"实例抽取完成（{schema_source}）：{len(result.entities)} 实体 / "
                                 f"{len(result.relations)} 关系"
                                 + (f"（含反向补全 {inverse_added} 条）" if inverse_added else ""),
                                 stats=stats)
            # QA 类锚定索引：实例抽取可能伴随骨架演进，一并重建（失败不阻塞）
            try:
                from app.adapters.tbox_index import sync_tbox_index
                sync_tbox_index(project_id, gd)
            except Exception:  # noqa: BLE001
                pass
            # O5-B27：重抽/首抽完成均追加首轮诊断（后台线程，不阻塞任务返回）
            try:
                from app.services.copilot_diagnosis import schedule_first_diagnosis
                schedule_first_diagnosis(project_id, trigger="instance_extract")
            except Exception:  # noqa: BLE001 —— 诊断调度失败不影响抽取结果
                pass
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


def _similar_candidates(label: str, existing: list[str], top_k: int = 3,
                        cutoff: float = 0.4) -> list[dict]:
    """新类/新谓词的候选归并对象（coa grounding 思想）：低置信不丢弃，
    带着最相似的已有项进审核，人可一键改判"并入已有类"。"""
    import difflib

    l = (label or "").strip()
    if not l or not existing:
        return []
    scored = sorted(
        ((ex, difflib.SequenceMatcher(None, l.lower(), str(ex).lower()).ratio())
         for ex in existing),
        key=lambda t: t[1], reverse=True)
    return [{"label": ex, "score": round(s, 2)}
            for ex, s in scored[:top_k] if s >= cutoff]


def _materialize_gate_reviews(db, project_id: int, result,
                              existing_classes: Optional[list[str]] = None,
                              existing_predicates: Optional[list[str]] = None) -> int:
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

    # promote 候选按 (kind, label) 去重：同一新类/新关系在多个切片反复出现时
    # 只建一条审核项（chunk_indices 合并），否则队列被同名项刷屏
    deduped_cands: dict[tuple[str, str], dict] = {}
    for cand in result.promote_candidates or []:
        key = (str(cand.get("kind") or ""), str(cand.get("label") or ""))
        first = deduped_cands.get(key)
        if first is None:
            merged_cand = dict(cand)
            merged_cand["chunk_indices"] = list(cand.get("chunk_indices") or [])
            deduped_cands[key] = merged_cand
        else:
            first["chunk_indices"] += list(cand.get("chunk_indices") or [])
    for cand in deduped_cands.values():
        if cand.get("kind") == "unknown_class":
            cls_cands = _similar_candidates(cand.get("label"), existing_classes or [])
            reason = (f"从文档中发现了新类型「{cand.get('label')}」，"
                      f"当前本体中没有它，请审核是否加入")
            if cls_cands:
                reason += (f"（与已有类「{cls_cands[0]['label']}」相似度 "
                           f"{cls_cands[0]['score']:.0%}，也可并入）")
            _add("new_class",
                 {"label": cand.get("label"), "chunk_indices": cand.get("chunk_indices"),
                  "policy": cand.get("policy"), "auto_promoted": cand.get("auto_promoted"),
                  "candidates": cls_cands},
                 reason,
                 priority="high" if cand.get("auto_promoted") else "medium")
        elif cand.get("kind") == "unknown_predicate":
            # O1-B3：新谓词物化审核项（此前被静默跳过），批准后回写 TBox
            _add("new_predicate",
                 {"label": cand.get("label"), "chunk_indices": cand.get("chunk_indices"),
                  "policy": cand.get("policy"), "auto_promoted": cand.get("auto_promoted"),
                  "candidates": _similar_candidates(cand.get("label"), existing_predicates or [])},
                 f"从文档中发现了新关系「{cand.get('label')}」，当前本体中没有它，请审核是否加入",
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


def _preserve_human_decisions(db, project_id: int, old_nodes: list, old_edges: list,
                              inst_nodes: list, inst_edges: list, by_label: dict,
                              label_ids: dict, class_nodes: list) -> tuple[list, list]:
    """steward 优先（coa 模式）：实例重抽为全量替换，人工成果在此合并回去。

    1. 人工编辑/新增的实例（data.human_edited）：与重抽结果按节点 id 或
       (归一化 label, 类) 匹配——命中则人工 label/别名/属性优先、AI 只补证据与置信；
       未命中（AI 本轮没抽到）则原样保留节点。
    2. 人工批准合并的标签（entities 行 status=merged，行尚未随本次 commit 重建）：
       重抽不再把已合并标签拆回独立实例——丢弃该节点并把 label 重映射到规范节点。
    3. 人工编辑/新增的关系边（data.human_edited）：两端节点仍在时保留，去重并入。
    返回 (最终实例节点, 最终实例边)；by_label/label_ids 就地修正。
    """
    import hashlib

    from app.adapters.resolution import normalize_label
    from app.infrastructure.database import Entity

    new_by_id = {str(n.get("id")): n for n in inst_nodes}

    # ── 2) 已批准合并：label → 规范实体 ──
    merged_map: dict[tuple[str, str], str] = {}  # (norm_label, class_label) → canonical label
    try:
        merged_rows = (db.query(Entity)
                       .filter(Entity.project_id == project_id,
                               Entity.status == "merged",
                               Entity.is_class_node.is_(False))
                       .all())
        canon_ids = {r.canonical_id for r in merged_rows if r.canonical_id}
        canon_rows = {c.id: c for c in db.query(Entity)
                      .filter(Entity.id.in_(sorted(canon_ids))).all()} if canon_ids else {}
        for r in merged_rows:
            c = canon_rows.get(r.canonical_id)
            if c is not None:
                merged_map[(r.label_normalized, r.class_label)] = (c.label, c.class_label)
    except Exception:  # noqa: BLE001 —— 行表读取失败不阻断重抽
        merged_map = {}

    final_nodes: list[dict] = []
    dropped_label_to: dict[str, str] = {}  # 被合并丢弃的 label → 规范节点 id
    for n in inst_nodes:
        data = n.get("data") or {}
        label = str(data.get("label") or "")
        m = merged_map.get((normalize_label(label), str(data.get("class_label") or "")))
        if m is not None:
            canon_label, _cc = m
            canon_node = next((x for x in inst_nodes
                               if str((x.get("data") or {}).get("label") or "") == canon_label), None)
            if canon_node is not None:
                dropped_label_to[label] = str(canon_node.get("id"))
                cd = canon_node.get("data") or {}
                aliases = list(cd.get("aliases") or [])
                if label not in aliases and label != canon_label:
                    aliases.append(label)
                cd["aliases"] = aliases
                canon_node["data"] = cd
                continue  # 丢弃：人工已裁定它是规范实体的同一事物
        final_nodes.append(n)

    # ── 1) 人工节点：命中合并（人工字段优先），未命中保留 ──
    human_nodes = [n for n in old_nodes
                   if (n.get("data") or {}).get("type") not in ("owl:Class", "Class")
                   and (n.get("data") or {}).get("human_edited")]
    kept_ids = {str(n.get("id")) for n in final_nodes}
    final_ids = {str(n.get("id")) for n in final_nodes}
    for h in human_nodes:
        hd = h.get("data") or {}
        h_label = str(hd.get("label") or "")
        h_key = (normalize_label(h_label), str(hd.get("class_label") or ""))
        twin = next((x for x in final_nodes
                     if str(x.get("id")) == str(h.get("id"))
                     or (normalize_label(str((x.get("data") or {}).get("label") or "")),
                         str((x.get("data") or {}).get("class_label") or "")) == h_key), None)
        if twin is not None:
            td = twin.get("data") or {}
            ai_label = str(td.get("label") or "")
            # 人工字段优先；AI 的 label（可能是原文称呼）进别名，供关系重映射
            aliases = list(hd.get("aliases") or [])
            if ai_label and ai_label != h_label and ai_label not in aliases:
                aliases.append(ai_label)
            hd["aliases"] = aliases
            hd["properties"] = {**{k: v for k, v in (td.get("properties") or {}).items()
                                   if k not in (hd.get("properties") or {})},
                                **(hd.get("properties") or {})}
            for k in ("confidence", "source_quote", "source_document", "source_chunk_index",
                      "source_document_id", "source_chunk_no", "source_char_start", "source_char_end"):
                if td.get(k) is not None:
                    hd[k] = td[k]
            hd["human_edited"] = True
            twin["data"] = hd
            if h_label != ai_label:
                by_label.setdefault(ai_label, str(twin.get("id")))
        elif str(h.get("id")) not in final_ids:
            final_nodes.append(h)  # AI 本轮没抽到：人工新增/确认的实例保留
            final_ids.add(str(h.get("id")))

    # by_label/label_ids 修正：被合并丢弃的 label 重映射；保留节点注册 type 边
    by_label.update(dropped_label_to)
    for label, target in list(by_label.items()):
        if target not in final_ids:
            by_label.pop(label, None)
    label_ids.clear()
    for n in final_nodes:
        d = n.get("data") or {}
        label_ids[(str(d.get("label") or ""), str(d.get("class_label") or ""))] = str(n.get("id"))

    # ── 3) 人工关系边保留（两端仍存在，且不与重抽结果重复） ──
    new_edge_keys = {(str(e.get("source")), str((e.get("data") or {}).get("relation")
                                                or e.get("label") or ""), str(e.get("target")))
                     for e in inst_edges}
    final_node_ids = {str(n.get("id")) for n in final_nodes} \
        | {str(n.get("id")) for n in class_nodes}
    kept_edges: list[dict] = []
    for e in old_edges:
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        if not d.get("human_edited"):
            continue
        s, t = str(e.get("source")), str(e.get("target"))
        if s not in final_node_ids or t not in final_node_ids:
            continue
        key = (s, str(d.get("relation") or d.get("label") or ""), t)
        if key in new_edge_keys:
            continue
        new_edge_keys.add(key)
        kept_edges.append(e)
    return final_nodes, inst_edges + kept_edges


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


@celery_app.task(name="app.tasks.extract_tasks.run_axiom_suggestion_task",
                 bind=True, max_retries=0, acks_late=True)
def run_axiom_suggestion_task(self, project_id: int, channel: str = "all") -> dict:
    """AI 公理扫描（O4-B18 双通道）异步化（S12）：进度分阶段上报 + 阶段边界取消。

    LLM 单次调用本身不可中断（阻塞 HTTP），取消在阶段边界生效：
    统计后 / LLM 返回后 / 物化前——取消即不物化任何审核项（已入画布数据零影响）。
    """
    task_id = self.request.id
    set_task_progress(task_id, "queued", 5, "公理扫描已入队",
                      {"project_id": project_id}, queue="extract")
    db = None
    try:
        from app.infrastructure.database import SessionLocal
        from app.services.axiom_suggestion_service import run_axiom_suggestions

        db = SessionLocal()

        def _prog(pct: int, msg: str) -> None:
            if is_cancelled(task_id):
                return
            set_task_progress(task_id, "extracting", pct, msg,
                              {"project_id": project_id}, queue="extract")

        stats = run_axiom_suggestions(
            db, project_id, channel=channel,
            progress_cb=_prog,
            is_cancelled_fn=lambda: is_cancelled(task_id))
        stats["project_id"] = project_id  # 进度查询端点按 stats.project_id 对账，缺失即 404
        if stats.get("cancelled"):
            finish_task_progress(task_id, "cancelled", "已取消（未物化任何审核项）",
                                 stats={"project_id": project_id})
            return {"status": "cancelled", **stats}
        finish_task_progress(task_id, "completed",
                             f"公理扫描完成：新增建议 {stats.get('review_created', 0)} 条"
                             + (f"，合并双佐证 {stats['merged_both']} 条"
                                if stats.get("merged_both") else ""),
                             stats=stats)
        return {"status": "completed", **stats}
    except Exception as e:  # noqa: BLE001
        finish_task_progress(task_id, "failed", str(e)[:300],
                             stats={"project_id": project_id})
        raise
    finally:
        if db is not None:
            db.close()


def dispatch_axiom_suggestion(project_id: int, channel: str = "all") -> dict:
    """派发 AI 公理扫描（详见 _dispatch）。返回 {"mode", "task_id"}。"""
    return _dispatch(
        run_axiom_suggestion_task,
        (project_id,),
        {"channel": channel},
        project_id)
