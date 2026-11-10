# app/tasks/pipeline_tasks.py - 一键构建管线（新手简化，2026-11）
# 编排：解析(待解析文档, backend=auto) → 骨架抽取(已有骨架跳过, guidance 从领域描述生成)
#       → 实例抽取(promote_policy=review) → 实体消解 → 冲突检测 → AI 公理扫描(llm 通道)
#       → AI 自动裁决(含公理自动申报+一致性复验撤回)。
#
# 执行模型：本任务体自身跑在 extract 队列（或无 worker 时 inline 线程），各阶段经
# task.apply(task_id=子id) 就地同步执行——子任务照常写各自的 progress:{task_id} 键，
# 管线任务把阶段清单聚合写进自己的 progress stats.stages；前端轮询
# GET /pipeline/status 聚合展示，切页/刷新不丢（任务 id 存 Redis 锁，不再依赖组件 state）。
#
# 幂等：Redis 锁 pipeline:running:{project_id}（SET NX EX 6h）；重复 run 返回进行中
# task_id 而非再起一条（StrictMode 双发安全）。
#
# LLM 硬约束：所有 LLM 调用点均复用既有分批/素材预算链路（骨架/实例按切片分批、
# 公理素材 12000 字符预算降采样），管线自身不新增任何全量输入调用。

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import finish_task_progress, get_task_progress, set_task_progress

_RUNNING_KEY = "pipeline:running:{project_id}"
_RUNNING_TTL = 6 * 3600
# 最近一次构建登记（dispatch 时写；锁释放后 status 端点回落查它——
# 否则失败/完成后一刷新页面就只剩前端 localStorage 猜测，跨设备/清缓存即丢）
_LAST_KEY = "pipeline:last:{project_id}"
_LAST_TTL = 7 * 24 * 3600
_SCHEDULE_KEY = "pipeline:schedule:{project_id}"   # 定时构建登记（未开跑）
_SCHEDULE_TTL = 7 * 24 * 3600

STAGE_DEFS: list[tuple[str, str]] = [
    ("parse", "解析文档"),
    ("schema", "骨架抽取"),
    ("instances", "实例抽取"),
    ("resolution", "实体消解"),
    ("conflicts", "冲突检测"),
    # 更名（2026-11-08 用户反馈）："自检修复"与"冲突检测"并列时像是同一件事的
    # 重复——本阶段只查建模结构（层次环/悬空引用等），改名"结构自检"划清边界
    ("selfcheck", "结构自检"),
    ("axioms", "AI 公理扫描"),
    # 2026-11-08 两轮更名：原「AI 自动处理」易被误解成"整个构建都是 AI 自动弄的"——
    # 实际只做一件事：低风险审核项由 AI 初审自动确认，高风险项仍等人拍板
    ("adjudicate", "AI 结果审查"),
]

# 构建深度档位（用户新建项目时可选）：自检修复轮数 + 是否跑公理扫描
DEPTH_PROFILES: dict[str, dict] = {
    "fast":     {"selfcheck_rounds": 1, "include_axioms": False, "label": "快速"},
    "standard": {"selfcheck_rounds": 2, "include_axioms": True,  "label": "标准"},
    "deep":     {"selfcheck_rounds": 3, "include_axioms": True,  "label": "深度"},
}
DEFAULT_DEPTH = "standard"


class _PipelineCancelled(Exception):
    """管线级取消（cancel:extract:{task_id} 键，阶段边界生效）。"""


def _stages_reset() -> list[dict]:
    return [{"key": k, "label": lbl, "status": "pending", "detail": "", "task_id": None}
            for k, lbl in STAGE_DEFS]


def _stage(stages: list[dict], key: str) -> dict:
    return next(s for s in stages if s["key"] == key)


def _task_index() -> dict:
    """懒加载既有任务对象（apply 就地执行用）。"""
    from app.tasks.extract_tasks import (
        run_axiom_suggestion_task,
        run_conflict_detection_task,
        run_instance_extraction,
        run_resolution_task,
        run_schema_extraction,
    )

    return {
        "schema": run_schema_extraction,
        "instances": run_instance_extraction,
        "resolution": run_resolution_task,
        "conflicts": run_conflict_detection_task,
        "axioms": run_axiom_suggestion_task,
    }


def _extract_parallelism(db, project_id: int, model_config_id: Optional[int] = None) -> int:
    """抽取并行度：extract 行 params.parallelism 可配置（远程服务慢时调小避免并发排队），
    缺省 4。管线各 LLM 阶段共用（骨架/实例分批并发）。"""
    try:
        from app.adapters.provider import ModelPurpose, resolve_provider

        resolved = resolve_provider(ModelPurpose.EXTRACT, project_id=project_id,
                                    db=db, config_id=model_config_id)
        return max(1, int((resolved.params or {}).get("parallelism") or 4))
    except Exception:  # noqa: BLE001 —— 配置异常回落到默认并发
        return 4


def _extract_request_interval(db, project_id: int, model_config_id: Optional[int] = None) -> float:
    """批间隔秒：extract 行 params.request_interval（远程限速配置；2026-11-08
    前是死字段——组装后全仓无消费者）。异常回落 0（不限速）。"""
    try:
        from app.adapters.provider import ModelPurpose, resolve_provider

        resolved = resolve_provider(ModelPurpose.EXTRACT, project_id=project_id,
                                    db=db, config_id=model_config_id)
        return max(0.0, float((resolved.params or {}).get("request_interval") or 0))
    except Exception:  # noqa: BLE001
        return 0.0


def _friendly_error(e: BaseException | str) -> str:
    """构建失败原因 → 小白能看懂能行动的话（2026-11-08 用户反馈 #2）。

    原样透传的 "LLM 调用硬超时（330s 无任何响应）"/"Connection refused" 是黑话；
    超时/断连/限流/鉴权四类占构建失败绝大多数，逐类翻译成人话+下一步动作。
    """
    msg = str(e)
    low = msg.lower()
    if "硬超时" in msg or "timeout" in low or "timed out" in low:
        return ("模型请求超时：模型服务长时间没有响应，已自动重试仍未成功。"
                "请稍后重新构建，或在「系统配置 → 模型配置」里换更快的模型 / 调大超时时间。")
    if any(k in low for k in ("connection", "connect", "refused", "unreachable",
                              "getaddrinfo failed", "连接异常", "连接失败", "不可达",
                              "熔断", "all_chunks_failed")):
        return ("无法连接模型服务：请检查本机网络，并到「系统配置 → 模型配置」"
                "确认服务地址是否可达，然后重新构建。")
    if "429" in msg or "rate limit" in low or "限流" in msg:
        return ("模型限流（429）：请求过于频繁或账号配额暂时超限。"
                "请等一两分钟再重新构建，或换一个模型。")
    if "401" in msg or "unauthorized" in low or "forbidden" in low or "鉴权" in msg or "api key" in low:
        return "模型鉴权失败：请到「系统配置 → 模型配置」检查该模型的 API Key 是否有效。"
    return msg[:300]


def _run_stage_task(task, stage_key: str, stages: list[dict], task_id: str,
                    **kwargs) -> dict:
    """经 task.apply 就地执行子任务；失败转异常（子任务体已写 failed 进度）。
    成功后把子任务进度键的最终 message 存进阶段 detail——那是各阶段的产出摘要
    （"15 检出/15 新建待审"、"91 实体/5 关系"），前端结果摘要行渲染它，
    否则冲突检测/自检修复"一闪而过"用户根本不知道它们干了什么。"""
    sub_id = f"{task_id}:{stage_key}"
    _stage(stages, stage_key)["task_id"] = sub_id
    # 立即重推一份快照：上面的 task_id 只改了内存里的 stages，Redis 里停留在
    # 子任务入场前的版本（task_id=null）——cancel 端点照旧快照遍历会整段漏写
    # 本阶段的子任务取消键（2026-11-10"停止了还在跑"根因之一）。重推失败不影响阶段。
    try:
        cur = get_task_progress(task_id)
        if cur:
            set_task_progress(task_id, cur.get("stage") or stage_key,
                              cur.get("percent") or 0,
                              cur.get("message") or "",
                              cur.get("stats") or {})
    except Exception:  # noqa: BLE001 —— 快照推送失败不阻断阶段
        pass
    res = task.apply(kwargs=kwargs, task_id=sub_id)
    if res.failed():
        err = res.result
        msg = str(getattr(err, "message", None) or err)[:300]
        raise RuntimeError(f"{stage_key} 阶段失败: {_friendly_error(msg)}")
    try:
        sub_prog = get_task_progress(sub_id)
        if sub_prog and sub_prog.get("message"):
            # 摘要行自带"{阶段名}："前缀，剥掉子消息里的"XX完成："头，
            # 否则渲染成"骨架抽取：骨架抽取完成：12 类…"（2026-11-08 实测）
            detail = re.sub(r"^(骨架抽取|实例抽取|冲突检测|消解|公理扫描|结构自检|裁决)完成：",
                            "", str(sub_prog["message"]))
            _stage(stages, stage_key)["detail"] = detail[:80]
    except Exception:  # noqa: BLE001 —— 摘要拿不到不影响主流程
        pass
    return res.result if isinstance(res.result, dict) else {}


def _mine_business_rules(db, project_id: int) -> dict:
    """业务规则挖掘（对标外部 SWRL 规则层）：LLM 通道候选 → 落推理规则表。

    rule_llm 的存在性门禁保证 IF 侧逐字命中骨架白名单（引用素材外的名字弃单），
    THEN 侧是推导性新关系——规则只在推理引擎里派生新边，不写回事实，错误可停用。
    与既有采纳门禁（POST /reasoning/rules）差异：管线内自动采纳（全 AI 裁决模式）。
    """
    from app.infrastructure.database import OntologyRule, Project
    from app.services.reasoning_service import llm_rule_suggestions

    proj = db.query(Project).filter(Project.id == project_id).first()
    if proj is None:
        return {"rules_added": 0, "skipped": "project_not_found"}
    # 语义采集表·规则层（骨架阶段逐片采集的文档级规则）作先验素材：
    # IF 侧存在性门禁照常兜底，文档证据只引导不放行
    gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
    schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
    doc_rules = [r for r in (schema.get("business_rules") or [])
                 if isinstance(r, dict) and str(r.get("statement") or "").strip()]
    candidates, stats = llm_rule_suggestions(db, proj, doc_rules=doc_rules)
    existing_n = (db.query(OntologyRule)
                  .filter(OntologyRule.project_id == project_id).count())
    added = 0
    for i, cand in enumerate(candidates or []):
        try:
            db.add(OntologyRule(
                project_id=project_id,
                name=str(cand.get("name") or f"自动规则{existing_n + i + 1}")[:128],
                if_subject_class=str(cand["if_subject_class"])[:128],
                if_predicate=str(cand["if_predicate"])[:128],
                if_object_class=str(cand["if_object_class"])[:128],
                then_predicate=str(cand["then_predicate"])[:128],
                enabled=True))
            added += 1
        except (KeyError, TypeError):  # noqa: PERF203 —— 单条形状异常跳过
            continue
    if added:
        db.commit()
    return {"rules_added": added, "mined": len(candidates or []),
            "doc_rules": len(doc_rules), "skipped": stats.get("skipped")}


def _default_guidance(proj) -> Optional[str]:
    """guidance 默认值：从项目知识域/描述自动生成（新手免填；CQ 追加注入）。"""
    parts = []
    if (proj.domains or "").strip():
        parts.append(f"本项目属于知识域「{proj.domains.strip()}」。")
    if (proj.description or "").strip():
        parts.append(f"项目背景：{proj.description.strip()}")
    if not parts:
        return None
    parts.append("请围绕上述领域抽取领域本体骨架，类与关系命名使用业务术语（中文），"
                 "避免发明与领域无关的泛化类型。")
    return " ".join(parts)


# 管线任务 6h 时限（2026-11-09）：全局 1h 上限对大文档（500+ 切片 × 两轮抽取）
# 不够——单任务串联下整条管线被强杀。仅管线本体放宽（软 6h / 硬 6h5m），
# 其余任务保持全局 1h 不变。
PIPELINE_SOFT_TIME_LIMIT = 6 * 3600
PIPELINE_TIME_LIMIT = 6 * 3600 + 300


@celery_app.task(name="app.tasks.pipeline_tasks.run_pipeline",
                 bind=True, max_retries=0, acks_late=True,
                 soft_time_limit=PIPELINE_SOFT_TIME_LIMIT,
                 time_limit=PIPELINE_TIME_LIMIT)
def run_pipeline(self, project_id: int, trigger_user_id: Optional[int] = None,
                 options: Optional[dict] = None) -> dict:
    """一键构建管线（阶段同步串联；进度/失败语义见模块头注释）。"""
    task_id = self.request.id
    options = options or {}
    stages = _stages_reset()
    _stage_index = {k: i for i, (k, _) in enumerate(STAGE_DEFS)}

    def _prog(stage_key: str, msg: str = "", pct_in_stage: float | None = None,
              status_change: bool = True) -> None:
        base = _stage_index[stage_key]
        frac = (pct_in_stage if pct_in_stage is not None else (1.0 if status_change else 0.0))
        percent = min(99, int((base + max(0.0, min(1.0, frac))) * 100 / len(STAGE_DEFS)))
        set_task_progress(task_id, stage_key, percent, msg,
                          {"project_id": project_id, "pipeline": True,
                           "stages": stages},
                          queue="extract")

    def _cancel_check():
        from app.tasks.extract_tasks import is_cancelled

        if is_cancelled(task_id):
            raise _PipelineCancelled()

    restart = bool(options.get("restart"))
    from app.tasks.extract_tasks import is_cancelled
    depth = str(options.get("depth") or DEFAULT_DEPTH)
    if depth not in DEPTH_PROFILES:
        depth = DEFAULT_DEPTH
    profile = DEPTH_PROFILES[depth]

    def _check_stage_result(res: Any, stage_key: str) -> None:
        """子任务结果传导：cancelled 停管线；failed 同样必须停并报错。

        子任务（如 run_instance_extraction 全切片失败）以 res.result={'status':
        'failed', 'reason': ...} 字典返回而非抛异常——旧版只拦 cancelled，
        failed 被无视后阶段照样标 done、管线假装继续跑，最终假 completed
        （2026-11-06 测试2 实测：LLM 拒连，实例全丢却无任何报错）。

        重跑语义：该阶段从头发起（抽取产物只在阶段末尾落库，中断不留半成品）；
        已完成阶段不重跑（parse_status=parsed 的文档不再解析、已有骨架默认跳过）。
        """
        if not isinstance(res, dict):
            return
        if res.get("status") == "cancelled":
            st = _stage(stages, stage_key)
            st["status"] = "pending"
            st["detail"] = "已取消（重新构建将重跑本阶段）"
            raise _PipelineCancelled()
        if res.get("status") == "failed":
            # 子任务进度键的最终 message 信息量最大（"全部 31 个切片抽取失败…
            # 首因：chunk #0 …Connection error"）——res.reason 只有裸的
            # all_chunks_failed，翻译不出人话；优先取前者再分类
            try:
                sub = get_task_progress(f"{task_id}:{stage_key}")
            except Exception:  # noqa: BLE001
                sub = None
            raw = str((sub or {}).get("message") or res.get("reason") or "未知原因")
            reason = _friendly_error(raw)
            st = _stage(stages, stage_key)
            st["status"] = "failed"
            st["detail"] = reason
            raise RuntimeError(f"{stage_key} 阶段失败: {reason}")

    summary: dict[str, Any] = {}
    db = None
    # 主进度键心跳（2026-11-06 项目 258 实测）：主键只在阶段边界更新，骨架缝合等
    # 长阶段安静期可超 15 分钟——GET /pipeline/status 的「15 分钟无更新=worker 已死」
    # 看门狗会误清 pipeline:running 锁 → 项目列表算成「草稿」+ 幂等锁失效可被二次
    # 并发构建。60s 读改写一次主键刷新 updated_at；读到终态即自停（绝不覆盖
    # cancel 端点已落的「已停止」）。
    import threading as _threading

    hb_stop = _threading.Event()

    def _heartbeat() -> None:
        while not hb_stop.wait(60):
            try:
                cur = get_task_progress(task_id)
                if cur is None or cur.get("status") not in ("running", "queued"):
                    return
                set_task_progress(task_id, cur.get("stage") or "",
                                  int(cur.get("percent") or 0),
                                  cur.get("message") or "",
                                  stats=cur.get("stats"),
                                  queue=cur.get("queue") or "",
                                  status=cur.get("status") or "running")
            except Exception:  # noqa: BLE001 —— 心跳失败不影响管线本体
                pass

    _threading.Thread(target=_heartbeat, name=f"pipeline-hb-{task_id}",
                      daemon=True).start()
    try:
        from app.infrastructure.database import Project, SessionLocal

        db = SessionLocal()

        def _fresh_read() -> None:
            """阶段边界刷新本会话的读视图（项目 245 实测事故）。

            管线主会话从首个查询起持有读事务，MySQL REPEATABLE READ 下快照
            固定在事务起点——子任务（各自持会话）提交的骨架/审核项在主会话里
            不可见：自检读到空骨架（0 轮假通过）、收尾裁决 scanned=0、收尾统计
            全 0。commit 结束读事务 + expire_all 强制 ORM 重读。
            """
            try:
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
            db.expire_all()

        try:
            proj = db.query(Project).filter(Project.id == project_id).first()
            if proj is None:
                finish_task_progress(task_id, "failed", f"项目不存在: {project_id}",
                                     stats={"project_id": project_id, "stages": stages})
                return {"status": "failed", "reason": "project_not_found"}

            # ── 阶段 1：解析待解析文档（backend=auto，自动路由含扫描件嗅探）──
            from app.infrastructure.database import UploadedDocument
            from app.services.document_pipeline import run_parse

            st = _stage(stages, "parse")
            st["status"] = "running"
            _prog("parse", "检查待解析文档…", 0.0)
            # 常规：uploaded/failed 待解析 + parsing 在途（上传 auto_parse 已在跑的，
            # 管线等它结束而不是跳过——否则骨架阶段读到 0 切片直接失败）；
            # 重头开始（restart）：已解析文档也全部重解析
            parse_statuses = ("uploaded", "failed", "parsing", "parsed") if restart \
                else ("uploaded", "failed", "parsing")
            docs = (db.query(UploadedDocument)
                    .filter(UploadedDocument.project_id == project_id,
                            UploadedDocument.deleted_at.is_(None),
                            UploadedDocument.parse_status.in_(parse_statuses))
                    .all())
            # 阶段 A：等待在途解析（10s 轮询，上限 30 分钟；取消可中断）
            parsing_ids = [d.id for d in docs if d.parse_status == "parsing"]
            if parsing_ids:
                st["detail"] = f"等待 {len(parsing_ids)} 个解析中的文档…"
                st["task_id"] = ",".join(f"{task_id}:parse:{i}" for i in parsing_ids[:3])
                _prog("parse", st["detail"], 0.0)
                import time as _time

                for _ in range(180):
                    _cancel_check()
                    _time.sleep(10)
                    # commit 刷新 REPEATABLE READ 快照——只 expire_all 看不到子任务
                    # 已提交的 parsed（快照仍固定在事务起点，白等 30 分钟）
                    try:
                        db.commit()
                    except Exception:  # noqa: BLE001
                        db.rollback()
                    db.expire_all()
                    still = (db.query(UploadedDocument)
                             .filter(UploadedDocument.id.in_(parsing_ids))
                             .filter(UploadedDocument.parse_status == "parsing")
                             .count())
                    if not still:
                        break
                db.expire_all()
                docs = (db.query(UploadedDocument)
                        .filter(UploadedDocument.project_id == project_id,
                                UploadedDocument.deleted_at.is_(None),
                                UploadedDocument.parse_status.in_(parse_statuses))
                        .all())
            # 阶段 B：逐文档执行解析（uploaded/failed；restart 含 parsed）
            todo = [d for d in docs
                    if d.parse_status in ("uploaded", "failed")
                    or (restart and d.parse_status == "parsed")]
            if not todo:
                if docs:  # 只有在途解析且已全部结束 → 文档就绪
                    st["status"] = "done"
                    st["detail"] = f"{len(docs)} 个文档已就绪"
                    _prog("parse", st["detail"])
                else:
                    st["status"] = "skipped"
                    st["detail"] = "没有待解析文档"
                    _prog("parse", "没有待解析文档")
            else:
                parse_failed: list[str] = []
                for i, d in enumerate(todo):
                    _cancel_check()
                    sub_id = f"{task_id}:parse:{d.id}"
                    st["task_id"] = sub_id
                    st["detail"] = f"{i + 1}/{len(todo)}"
                    _prog("parse", f"解析中 {i + 1}/{len(todo)}：{d.filename}",
                          i / len(todo))
                    try:
                        run_parse(d.id, backend="auto", task_id=sub_id)
                    except Exception as e:  # noqa: BLE001 —— 单文档失败继续其余
                        parse_failed.append(f"{d.filename}: {str(e)[:120]}")
                if len(parse_failed) == len(todo):
                    st["status"] = "failed"
                    st["detail"] = f"全部 {len(todo)} 个文档解析失败"
                    raise RuntimeError("全部文档解析失败，首因：" + parse_failed[0])
                st["status"] = "done"
                st["detail"] = f"{len(todo) - len(parse_failed)}/{len(todo)} 个文档"
                if parse_failed:
                    st["detail"] += f"（{len(parse_failed)} 个失败）"
                summary["parse"] = {"total": len(todo), "failed": parse_failed}
                _prog("parse", f"解析完成（{st['detail']}）")

            # 模型前置检查：解析不需要 LLM，抽取需要——缺配置时文档已解析完，
            # 报错提示人话（不浪费已完成的解析成果）
            from app.adapters.provider import ModelPurpose, resolve_provider

            try:
                _llm = resolve_provider(ModelPurpose.EXTRACT, project_id, db=db)
                has_llm = bool(_llm.model_name and _llm.base_url)
            except Exception:  # noqa: BLE001 —— 未配置/密钥解密失败都按缺模型处理
                has_llm = False
            if not has_llm:
                finish_task_progress(
                    task_id, "failed",
                    "文档解析已完成，但尚未配置可用的抽取模型：请在「模型配置」中"
                    "添加并启用抽取模型后重新一键构建",
                    stats={"project_id": project_id, "stages": stages})
                return {"status": "failed", "reason": "no_extract_model"}

            model_config_id = None
            if trigger_user_id:
                from app.infrastructure.database import UserModelPick

                pick = (db.query(UserModelPick)
                        .filter(UserModelPick.user_id == trigger_user_id,
                                UserModelPick.purpose == "extract").first())
                model_config_id = pick.config_id if pick else None

            tasks = _task_index()

            # ── 阶段 2：骨架抽取（已有骨架跳过，避免覆盖人工修订）──
            _cancel_check()
            _fresh_read()
            st = _stage(stages, "schema")
            proj = db.query(Project).filter(Project.id == project_id).first()
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
            if schema.get("classes") and not restart:
                st["status"] = "skipped"
                st["detail"] = f"已有骨架（{len(schema['classes'])} 类），跳过重抽"
                _prog("schema", st["detail"])
            else:
                st["status"] = "running"
                _prog("schema", "AI 抽取骨架中…", 0.0)
                # CQ 前置（外部方法论「能力问题法」）：解析完成后、建模前先圈定
                # "本体要能回答什么"，经 guidance 注入骨架抽取（抽取受它约束）
                try:
                    from app.services.cq_service import generate_cqs

                    from app.infrastructure.database import DocumentChunk

                    doc_heads = [str(t) for (t,) in db.query(DocumentChunk.text)
                                 .join(UploadedDocument,
                                       DocumentChunk.document_id == UploadedDocument.id)
                                 .filter(UploadedDocument.project_id == project_id,
                                         UploadedDocument.deleted_at.is_(None))
                                 .order_by(UploadedDocument.id,
                                           DocumentChunk.chunk_index).limit(30).all()]
                    cq_res = generate_cqs(db, project_id, created_by=trigger_user_id,
                                          doc_texts=doc_heads)
                    summary["cq_generation"] = cq_res
                    n_cq = int(cq_res.get("generated") or 0)
                    if n_cq:
                        st["detail"] = f"已设计能力问题 {n_cq} 条"
                        _prog("schema", st["detail"], 0.02)
                except Exception as e:  # noqa: BLE001 —— CQ 生成失败不阻断构建
                    summary["cq_generation"] = {"generated": 0, "skipped": str(e)[:120]}
                guidance = _default_guidance(proj)
                try:
                    from app.services.cq_service import guidance_from_cqs

                    guidance = guidance_from_cqs(db, project_id, guidance)
                except Exception:  # noqa: BLE001
                    pass
                _check_stage_result(_run_stage_task(
                    tasks["schema"], "schema", stages, task_id,
                    project_id=project_id, document_ids=None,
                    parallelism=_extract_parallelism(db, project_id, model_config_id),
                    request_interval=_extract_request_interval(db, project_id, model_config_id),
                    guidance=guidance,
                    model_config_id=model_config_id, strict_config=None), "schema")
                st["status"] = "done"
                _prog("schema", "骨架抽取完成")

            # ── 阶段 3：实例抽取 ──
            _cancel_check()
            st = _stage(stages, "instances")
            st["status"] = "running"
            _prog("instances", "AI 抽取实例中…", 0.0)
            _check_stage_result(_run_stage_task(
                tasks["instances"], "instances", stages, task_id,
                project_id=project_id, document_ids=None,
                strict_gate=True, promote_policy="review",
                parallelism=_extract_parallelism(db, project_id, model_config_id),
                request_interval=_extract_request_interval(db, project_id, model_config_id),
                model_config_id=model_config_id), "instances")
            st["status"] = "done"
            _prog("instances", "实例抽取完成")

            # ── 阶段 4：实体消解 ──
            _cancel_check()
            st = _stage(stages, "resolution")
            st["status"] = "running"
            _prog("resolution", "实体消解中…", 0.0)
            _check_stage_result(_run_stage_task(
                tasks["resolution"], "resolution", stages, task_id,
                project_id=project_id), "resolution")
            st["status"] = "done"
            _prog("resolution", "实体消解完成")

            # ── 阶段 5：冲突检测 ──
            _cancel_check()
            st = _stage(stages, "conflicts")
            st["status"] = "running"
            _prog("conflicts", "冲突检测中…", 0.0)
            _check_stage_result(_run_stage_task(
                tasks["conflicts"], "conflicts", stages, task_id,
                project_id=project_id), "conflicts")
            st["status"] = "done"
            _prog("conflicts", "冲突检测完成")

            # ── 阶段 6：自检修复环（准出标准：机检无硬伤；LLM 定向修复，最小动作）──
            st = _stage(stages, "selfcheck")
            st["status"] = "running"
            _prog("selfcheck", "本体质量自检中…", 0.0)
            _cancel_check()
            _fresh_read()
            try:
                from app.services.pipeline_selfcheck import run_selfcheck_repair

                def _sc_prog(msg: str, frac: float) -> None:
                    _prog("selfcheck", msg, frac)

                sc = run_selfcheck_repair(project_id, db=db,
                                          rounds=profile["selfcheck_rounds"],
                                          model_config_id=model_config_id,
                                          progress_cb=_sc_prog,
                                          cancel_fn=lambda: is_cancelled(task_id))
                summary["selfcheck"] = sc
                st["status"] = "done"
                # 摘要行只说完成（2026-11-08 用户反馈）：修了几处、术语统计都不要——
                # 需人工的数量由完成消息统一交代
                st["detail"] = "完成修复"
                _prog("selfcheck", st["detail"])
            except _PipelineCancelled:
                raise
            except Exception as e:  # noqa: BLE001 —— 自检失败不阻断后续（机检结论照常出）
                st["status"] = "failed"
                st["detail"] = f"自检修复失败（不影响其余结果）: {str(e)[:120]}"
                _prog("selfcheck", st["detail"])

            # ── 阶段 7：AI 公理扫描（深度=fast 跳过；失败/跳过不阻断收尾）──
            _cancel_check()
            st = _stage(stages, "axioms")
            if not profile["include_axioms"]:
                st["status"] = "skipped"
                st["detail"] = "快速档跳过公理扫描"
                _prog("axioms", st["detail"])
            else:
                st["status"] = "running"
                _prog("axioms", "AI 公理扫描中…", 0.0)
                try:
                    axiom_res = _run_stage_task(tasks["axioms"], "axioms", stages, task_id,
                                                project_id=project_id, channel="llm")
                    _check_stage_result(axiom_res, "axioms")
                    if (axiom_res.get("llm") or {}).get("skipped") or axiom_res.get("skipped"):
                        st["status"] = "skipped"
                        st["detail"] = "模型不可用，已跳过公理扫描"
                    else:
                        st["status"] = "done"
                        llm_ch = axiom_res.get("llm") or {}
                        if axiom_res.get("review_created", 0) > 0:
                            st["detail"] = "完成扫描"
                        elif llm_ch.get("skipped"):
                            st["detail"] = f"未产出公理（{str(llm_ch['skipped'])[:50]}）"
                        else:
                            # "完成扫描"掩盖空手而归（2026-11-08 项目 265 实测：
                            # 方舟限流时段空响应，parsed=0 用户以为扫描没用）
                            st["detail"] = "完成扫描，未产出公理（模型响应为空，稍后重新构建可重试）"
                    _prog("axioms", "公理扫描结束")
                except _PipelineCancelled:
                    raise  # 取消传导：不得当作阶段失败吞掉
                except Exception as e:  # noqa: BLE001 —— 公理扫描失败不阻碍自动裁决其余项
                    st["status"] = "failed"
                    st["detail"] = f"公理扫描失败（不影响其余结果）: {str(e)[:120]}"
                    _prog("axioms", st["detail"])

                # ── 业务规则挖掘（外部方法论对照：SWRL 控制规则层）：
                #    IF-THEN 产生式候选（IF 侧逐字命中白名单的存在性门禁在
                #    rule_llm 内做）→ 直接落推理规则表（错误规则可停用，风险低）。
                #    失败静默，不影响公理阶段主产物。
                try:
                    _fresh_read()
                    rules_res = _mine_business_rules(db, project_id)
                    summary["business_rules"] = rules_res
                    n_rules = int(rules_res.get("rules_added") or 0)
                    if n_rules:
                        st["detail"] += f"，业务规则 {n_rules} 条"
                        _prog("axioms", st["detail"])
                except Exception as e:  # noqa: BLE001
                    summary["business_rules"] = {"rules_added": 0, "skipped": str(e)[:120]}

            # ── 阶段 8：AI 自动裁决（含公理自动申报 + 一致性复验撤回）──
            _cancel_check()
            _fresh_read()
            st = _stage(stages, "adjudicate")
            st["status"] = "running"
            _prog("adjudicate", "结果确认中…", 0.0)
            from app.services.auto_adjudication import run_auto_adjudication

            # fast 档（2026-11-09 用户决策）：模型抽取结果不再 LLM 复核，默认通过；
            # 保留零成本的确定性安全检查（矛盾公理撤回/低相似度合并不执行），
            # "结果确认"阶段从分钟级缩到 10-30s
            adj = run_auto_adjudication(project_id, db=db, level="fast")
            summary["adjudication"] = adj
            st["status"] = "done"
            st["detail"] = (f"自动确认 {adj['auto_approved']} 项"
                            + (f"，撤回 {adj['rolled_back']} 条引发矛盾的公理"
                               if adj.get("rolled_back") else ""))

            # 收尾统计：剩余需人裁决的数量
            from sqlalchemy import func as sa_func

            from app.infrastructure.database import ReviewItem

            pending_rows = (db.query(ReviewItem.priority, sa_func.count())
                            .filter(ReviewItem.project_id == project_id,
                                    ReviewItem.status == "pending")
                            .group_by(ReviewItem.priority).all())
            by_priority = {p: int(c) for p, c in pending_rows}
            need_human = by_priority.get("critical", 0) + by_priority.get("high", 0)

            # ── 能力问题评测（外部方法论对照：CQ 在真实产物上验证）：
            #    先补 SPARQL 模板（骨架就绪后才能生成），再双口径评测——
            #    有模板且图可查 = SPARQL 真执行出答案行；否则回落检索式。
            #    任一环节失败都不影响构建终态 ──
            cq_note = ""
            try:
                from app.services.cq_service import (
                    attach_sparql, ensure_rdf_graphs, evaluate_cqs, list_cqs,
                )

                if list_cqs(db, project_id, enabled_only=True):
                    _fresh_read()
                    attach_sparql(db, project_id)
                    # rebuild=True：构建刚改完全部图谱数据，评测必须用新图
                    graph_state = ensure_rdf_graphs(project_id, rebuild=True)
                    _fresh_read()
                    ev = evaluate_cqs(db, project_id, trigger="auto_post_extract")
                    db.commit()  # CQ 模板/评测结果落库（attach_sparql 无自带提交）
                    if ev is not None:
                        summary["cq_evaluation_id"] = ev.id
                        summary["cq_graph_state"] = graph_state
                        ok = ev.answerable_count == ev.total_cq
                        mode_note = "（SPARQL 实查）" if ev.mode == "sparql+retrieval" else ""
                        cq_note = (f"，能力问题验证通过 {ev.answerable_count}/{ev.total_cq}{mode_note}"
                                   + ("" if ok else "（部分问题暂无证据，可补充文档后重新构建）"))
            except Exception as e:  # noqa: BLE001
                from app.core.logging import logger

                logger.warning(f"[pipeline] CQ 评测失败 project={project_id}: {e}")

            # ── 构建报告（外部方法论交付物对照：人话版，零 LLM）──
            try:
                from app.services.build_report import generate_build_report

                model_name = ""
                try:
                    from app.adapters.provider import ModelPurpose, resolve_provider

                    model_name = resolve_provider(
                        ModelPurpose.EXTRACT, project_id, db=db).model_name
                except Exception:  # noqa: BLE001
                    pass
                report_res = generate_build_report(db, project_id,
                                                   depth_label=profile["label"],
                                                   model_name=model_name)
                summary["build_report"] = {k: report_res[k] for k in
                                           ("sections", "chars", "pending",
                                            "rules", "doc_rules") if k in report_res}
            except Exception as e:  # noqa: BLE001 —— 报告失败不影响构建终态
                summary["build_report"] = {"skipped": str(e)[:120]}

            sc = summary.get("selfcheck") or {}
            remaining = int(sc.get("remaining") or 0)
            sc_note = ("结构自检通过" if remaining == 0
                       else f"结构自检后剩 {remaining} 处需人工确认") if sc else ""
            finish_task_progress(
                task_id, "completed",
                f"一键构建完成（{profile['label']}档{'，' + sc_note if sc_note else ''}）："
                f"还剩 {need_human} 个高优先级结果等你确认{cq_note}",
                stats={"project_id": project_id, "pipeline": True, "stages": stages,
                       "depth": depth, "pending_by_priority": by_priority,
                       "need_human": need_human, **summary})
            return {"status": "completed", "need_human": need_human,
                    "pending_by_priority": by_priority, "adjudication": adj}
        finally:
            hb_stop.set()  # 停心跳：终态已落，别再刷新主键
            if db is not None:
                db.close()
            release_pipeline_lock(project_id)  # celery 模式的锁清理（inline 在线程 finally 兜底）
    except _PipelineCancelled:
        # 终态落库前收尾阶段状态：正在跑的阶段快照仍是 running，不改会被
        # 前端渲染成"已停止但格子还在转圈"（2026-11-06 测试2 实测）
        for st in stages:
            if st["status"] == "running":
                st["status"] = "cancelled"
        finish_task_progress(task_id, "cancelled", "已取消：重新构建将从被中断的阶段继续",
                             stats={"project_id": project_id, "stages": stages})
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        # 取消优先：子任务可能因 LLM 调用硬超时抛异常返回，但用户已点停止——
        # 此时按取消收场（格子标 cancelled），不得以 failed 覆盖"已停止"终态
        if is_cancelled(task_id):
            for st in stages:
                if st["status"] == "running":
                    st["status"] = "cancelled"
            finish_task_progress(task_id, "cancelled", "已停止（用户取消）",
                                 stats={"project_id": project_id, "stages": stages})
            return {"status": "cancelled"}
        reason = _friendly_error(e)
        for st in stages:
            if st["status"] == "running":
                st["status"] = "failed"
                st["detail"] = reason  # 红格子上直接写人话原因，不用点开猜
        finish_task_progress(task_id, "failed", reason,
                             stats={"project_id": project_id, "stages": stages})
        return {"status": "failed", "reason": reason}


def _redis():
    import redis

    from app.services.env_config_service import redis_url

    return redis.Redis.from_url(redis_url(), decode_responses=True,
                                socket_connect_timeout=2)


def _worker_alive() -> bool:
    """extract 队列 worker 存活探测（复用 extract_tasks 实现）。"""
    try:
        from app.tasks.extract_tasks import _extract_worker_alive

        return _extract_worker_alive()
    except Exception:  # noqa: BLE001
        return False


def dispatch_pipeline_run(project_id: int, trigger_user_id: Optional[int] = None,
                          options: Optional[dict] = None) -> dict:
    """派发一键管线（幂等）：已有进行中管线 → 返回其 task_id 不再新起。

    options.schedule_at（ISO 时间串，未来时刻）→ 定时构建：登记 schedule 键 +
    celery eta 派发（inline 模式用 threading.Timer 兜底，进程重启会丢——状态页可见），
    到点才真正抢锁起跑；已登记定时 → 返回 already_scheduled。
    立即构建返回 {"mode", "task_id", "already_running"}。
    """
    from app.core.logging import logger

    options = options or {}
    r = _redis()
    key = _RUNNING_KEY.format(project_id=project_id)
    sched_key = _SCHEDULE_KEY.format(project_id=project_id)

    # 定时构建（2026-11-06 根治）：只登记 Redis schedule 键（fire 时间 + kwargs JSON），
    # 由 beat 每 30s 的 check_scheduled_pipelines 轮询到点触发——
    # 旧实现 eta 派发（redis broker 不持久化，worker 重启即丢）+ threading.Timer 兜底
    # （挂 backend 进程内存，backend 重启即蒸发）都会让定时构建无声失效；
    # 登记表方案在任何进程重启后自愈。
    sched_at_raw = str(options.get("schedule_at") or "").strip()
    if sched_at_raw:
        import datetime as _dt

        fire = _dt.datetime.fromisoformat(sched_at_raw.replace("Z", "+00:00"))
        if fire.tzinfo is None:
            # 无时区输入（datetime-local 表单）按服务器本地时间解释
            fire = fire.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
        now_utc = _dt.datetime.now(_dt.timezone.utc)
        if fire <= now_utc:
            return {"mode": "invalid", "task_id": None, "error": "schedule_at 必须是未来时间"}
        existing_sched = r.get(sched_key)
        if existing_sched:
            return {"mode": "scheduled", "task_id": None,
                    "scheduled_at": sched_at_raw, "already_scheduled": True}
        payload = json.dumps({"fire_at": fire.astimezone(_dt.timezone.utc).isoformat(),
                              "trigger_user_id": trigger_user_id,
                              "options": {k: v for k, v in options.items() if k != "schedule_at"}},
                             ensure_ascii=False)
        r.set(sched_key, payload, ex=_SCHEDULE_TTL)
        return {"mode": "scheduled", "task_id": None,
                "scheduled_at": sched_at_raw, "already_scheduled": False}

    existing = r.get(key)
    if existing:
        prog = get_task_progress(existing)
        if prog is not None and prog.get("status") not in ("completed", "failed", "cancelled"):
            return {"mode": "existing", "task_id": existing, "already_running": True}
        r.delete(key)  # 残留锁（worker 崩溃未清理）→ 重新发起

    task_id = f"pipeline-{uuid.uuid4().hex[:12]}"
    locked = r.set(key, task_id, nx=True, ex=_RUNNING_TTL)
    if not locked:
        winner = r.get(key) or ""
        return {"mode": "existing", "task_id": winner, "already_running": True}
    # 手动立即构建取代已登记的定时：清登记，防 beat 到点把项目重复拉起
    # （beat 触发=删键+dispatch 抢锁；运行中能幂等挡住，跑完后到点照样二跑）
    r.delete(sched_key)
    # 登记最近一次构建（status 端点在锁释放后回落查它，失败态刷新页面不丢）
    r.set(_LAST_KEY.format(project_id=project_id), task_id, ex=_LAST_TTL)

    # 预写「已入队」进度：锁存在即进度可查，幂等判定不依赖 worker 已开工
    set_task_progress(task_id, "queued", 0, "管线已入队",
                      {"project_id": project_id, "pipeline": True,
                       "stages": _stages_reset()},
                      queue="extract")

    kwargs = {"project_id": project_id, "trigger_user_id": trigger_user_id,
              "options": options}
    if _worker_alive():
        try:
            run_pipeline.apply_async(kwargs=kwargs, task_id=task_id)
            return {"mode": "celery", "task_id": task_id, "already_running": False}
        except Exception as e:  # noqa: BLE001 —— 派发失败降级进程内（锁继续沿用）
            logger.warning(f"[pipeline dispatch] Celery 派发失败，降级进程内执行: {e}")

    import threading

    def _worker() -> None:
        try:
            run_pipeline.apply(kwargs=kwargs, task_id=task_id)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[pipeline dispatch] 进程内管线 project={project_id} 失败: {e}")
        finally:
            try:
                _redis().delete(key)
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_worker, name=f"pipeline-inline-{project_id}",
                     daemon=True).start()
    return {"mode": "inline", "task_id": task_id, "already_running": False}


@celery_app.task(name="app.tasks.pipeline_tasks.run_scheduled_pipeline",
                 bind=True, max_retries=0)
def run_scheduled_pipeline(self, project_id: int, trigger_user_id: Optional[int] = None,
                           options: Optional[dict] = None) -> dict:
    """定时构建的到点载体（兼容保留：beat 轮询也复用本入口）。"""
    from app.core.logging import logger

    try:
        _redis().delete(_SCHEDULE_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        pass
    logger.info(f"[pipeline] 定时构建到点 project={project_id}")
    return dispatch_pipeline_run(project_id, trigger_user_id=trigger_user_id,
                                 options=options)


@celery_app.task(name="app.tasks.pipeline_tasks.check_scheduled_pipelines")
def check_scheduled_pipelines() -> dict:
    """beat 每 30s 轮询：扫描定时构建登记表，到点的触发真正执行（重启自愈）。

    登记键值 = JSON{fire_at(UTC ISO), trigger_user_id, options}；到点 → 删键 →
    dispatch（抢锁幂等）。未到点跳过；解析失败/派发失败的键保留到下轮再试
    （SCHEDULE_TTL 兜底过期）。
    """
    import datetime as _dt

    from app.core.logging import logger

    r = _redis()
    fired = []
    try:
        for k in r.scan_iter("pipeline:schedule:*"):
            key = k.decode() if isinstance(k, bytes) else k
            if key.endswith(":task"):
                continue  # 兼容旧 eta 载体键
            raw = r.get(key)
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                fire = _dt.datetime.fromisoformat(str(payload["fire_at"]).replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001 —— 脏数据（旧版纯时间串）按到点处理
                try:
                    fire = _dt.datetime.fromisoformat(raw.decode() if isinstance(raw, bytes) else str(raw))
                    if fire.tzinfo is None:
                        fire = fire.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
                    payload = None
                except Exception:  # noqa: BLE001
                    r.delete(key)
                    continue
            if fire > _dt.datetime.now(_dt.timezone.utc):
                continue  # 未到点
            pid_match = key.rsplit(":", 1)[-1]
            try:
                project_id = int(pid_match)
            except ValueError:
                r.delete(key)
                continue
            r.delete(key)  # 先删登记（防重复触发），失败由下轮登记消失兜底
            kwargs = {"project_id": project_id,
                      "trigger_user_id": (payload or {}).get("trigger_user_id"),
                      "options": (payload or {}).get("options") or {}}
            try:
                run_scheduled_pipeline.apply(kwargs=kwargs)
                fired.append(project_id)
                logger.info(f"[pipeline] beat 触发定时构建 project={project_id}")
            except Exception as e:  # noqa: BLE001 —— 派发失败登记已删，日志留痕
                logger.error(f"[pipeline] beat 触发定时构建 project={project_id} 失败: {e}")
    except Exception as e:  # noqa: BLE001 —— redis 抖动下轮再扫
        logger.warning(f"[pipeline] 定时构建扫描失败: {e}")
    return {"fired": fired}


def cancel_scheduled_pipeline(project_id: int) -> bool:
    """取消定时构建（还没开跑的）：清登记键 + revoke eta 任务。返回是否有定时被取消。"""
    r = _redis()
    sched_key = _SCHEDULE_KEY.format(project_id=project_id)
    had = r.get(sched_key) is not None
    if not had:
        return False
    r.delete(sched_key)
    carrier = r.get(sched_key + ":task")
    if carrier:
        r.delete(sched_key + ":task")
        try:
            from app.tasks.celery_app import celery_app

            celery_app.control.revoke(carrier, terminate=False)
        except Exception:  # noqa: BLE001
            pass
    return True


def pipeline_scheduled_at(project_id: int) -> Optional[str]:
    """已登记的定时构建时间（未开跑）；无则 None。"""
    try:
        return _redis().get(_SCHEDULE_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        return None


def pipeline_running_task_id(project_id: int) -> Optional[str]:
    """当前（或最近 6h 内）管线的 task_id；锁过期/不存在返回 None。"""
    try:
        return _redis().get(_RUNNING_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        return None


def pipeline_last_task_id(project_id: int) -> Optional[str]:
    """最近一次发起的管线 task_id（7d）；无登记返回 None。"""
    try:
        return _redis().get(_LAST_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        return None


def release_pipeline_lock(project_id: int) -> None:
    try:
        _redis().delete(_RUNNING_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        pass


__all__ = ["STAGE_DEFS", "DEPTH_PROFILES", "run_pipeline", "dispatch_pipeline_run",
           "run_scheduled_pipeline", "cancel_scheduled_pipeline",
           "pipeline_scheduled_at", "pipeline_running_task_id", "release_pipeline_lock"]
