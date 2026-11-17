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
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.tasks.celery_app import celery_app
from app.tasks.progress import finish_task_progress, get_task_progress, set_task_progress
from app.services.pipeline_profiler import (
    begin_pipeline_profile,
    finish_pipeline_profile,
    profile_metric,
    profile_metrics,
    profile_stage_begin,
    profile_stage_end,
)

_RUNNING_KEY = "pipeline:running:{project_id}"
_RUNNING_TTL = 6 * 3600
# 最近一次构建登记（dispatch 时写；锁释放后 status 端点回落查它——
# 否则失败/完成后一刷新页面就只剩前端 localStorage 猜测，跨设备/清缓存即丢）
_LAST_KEY = "pipeline:last:{project_id}"
_LAST_TTL = 7 * 24 * 3600
_SCHEDULE_KEY = "pipeline:schedule:{project_id}"   # 定时构建登记（未开跑）
_SCHEDULE_TTL = 7 * 24 * 3600
# 进度多久没更新视为 worker 已死：心跳每 60s 刷新一次，留 2 次容错。
# 旧值 900s 会让服务重启后的项目在列表里继续显示「构建中」最多 15 分钟。
PIPELINE_STALE_SECONDS = int(os.environ.get("PIPELINE_STALE_SECONDS", "180"))
PIPELINE_STALE_MESSAGE = "构建已中断：超过 3 分钟没有进度（服务重启或 worker 掉线）"

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
    return [{"key": k, "label": lbl, "status": "pending", "detail": "", "task_id": None,
             "started_at": None, "finished_at": None, "duration_seconds": None}
            for k, lbl in STAGE_DEFS]


def _stage(stages: list[dict], key: str) -> dict:
    return next(s for s in stages if s["key"] == key)


# 阶段内子步骤（前端「阶段时间线 + 单一详情区」逐条渲染进度条，2026-11-16 原型）。
# 只描述「子任务自己写进度键」的阶段：区间是该子步骤在阶段内占的百分比范围，
# 与 extract_tasks 里 _progress/_phase_progress 的落点一一对应；不一致时最多
# 显示偏一点，不影响阶段主状态。
_SUB_STEP_RANGES: dict[str, list[tuple[str, str, int, int]]] = {
    "schema": [
        ("read", "读取切片", 0, 5),
        ("extract", "批次抽取", 5, 55),
        ("assemble", "骨架装配", 55, 65),
        ("structure", "结构映射", 65, 82),
        ("stitch", "跨切片缝合", 82, 92),
        ("write", "写入画布与行表", 92, 100),
    ],
    "instances": [
        ("read", "读取切片", 0, 5),
        ("extract", "批次抽取", 5, 80),
        ("enrich", "指代消解与结构展开", 80, 85),
        ("write", "写入画布与行表", 85, 100),
    ],
    "resolution": [
        ("load", "读取实体与属性键", 0, 20),
        ("detect", "相似度聚类判定", 20, 70),
        ("merge", "合并与待审登记", 70, 100),
    ],
    "conflicts": [
        ("scan", "扫描结果与约束", 0, 55),
        ("materialize", "检出冲突并登记待审", 55, 100),
    ],
    "selfcheck": [
        ("inspect", "机检扫描", 0, 30),
        ("repair", "AI 定向修复", 30, 90),
        ("verify", "复检验证", 90, 100),
    ],
}
# 静默段（无子任务、由管线自己写子进度键）：业务规则挖掘 / 能力问题评测。
# 四元组末两位是阶段内权重区间：子步骤自己的 percent 是「段内百分比」，
# 阶段整体进度必须按区间折算——否则第一段跑到 50% 时阶段总进度也显示 50%，
# 而第二段还一点没开始（2026-11-17 用户反馈）。
_SILENT_STEPS: dict[str, list[tuple[str, str, int, int]]] = {
    "axioms": [("scan", "公理扫描", 0, 35),
               ("business_rules", "业务规则挖掘", 35, 100)],
    "adjudicate": [("adjudicate", "AI 结果审查", 0, 40),
                   ("cq_eval", "能力问题评测", 40, 100)],
}


def _step(key: str, label: str, state: str, percent: int) -> dict:
    return {"key": key, "label": label, "state": state,
            "percent": max(0, min(100, int(percent)))}


def _phase_steps(stage_key: str, current: str, state: str = "running",
                 percent: int = 0) -> list[dict]:
    """静默段子步骤清单：current 之前的 done，之后的 pending。

    percent 是当前步骤的**段内**百分比（0-100），折算阶段整体用 _silent_overall。
    """
    out: list[dict] = []
    seen = False
    for key, label, _lo, _hi in _SILENT_STEPS.get(stage_key) or []:
        if key == current:
            out.append(_step(key, label, state, percent))
            seen = True
        elif not seen:
            out.append(_step(key, label, "done", 100))
        else:
            out.append(_step(key, label, "pending", 0))
    return out


def _silent_overall(stage_key: str, current: str, seg_pct: int) -> int:
    """静默段：把当前步骤的段内百分比折算成阶段整体百分比。"""
    pct = max(0, min(100, int(seg_pct)))
    for key, _label, lo, hi in _SILENT_STEPS.get(stage_key) or []:
        if key == current:
            return max(0, min(100, int(lo + (hi - lo) * pct / 100)))
    return pct


def display_sub_percent(stage_key: str, sub: Optional[dict]) -> Optional[int]:
    """子进度键 percent 的展示值（API/SSE 共用）。

    静默段有两条写入路径：子任务只写段内 percent（公理扫描），管线写显式
    steps + 整体 percent（业务规则/CQ）。前者需要在这里折算成阶段整体，
    否则详情区阶段主进度会与第一个子步骤完全相等。
    """
    if not isinstance(sub, dict):
        return None
    try:
        pct = int(sub.get("percent"))
    except (TypeError, ValueError):
        return None
    stats = sub.get("stats") if isinstance(sub.get("stats"), dict) else {}
    if stats.get("steps"):
        return pct
    steps = _SILENT_STEPS.get(stage_key)
    if steps:
        return _silent_overall(stage_key, steps[0][0], pct)
    return pct


def build_sub_steps(stage_key: str, sub: Optional[dict]) -> list[dict]:
    """把子任务进度摊平成阶段内子步骤（API/SSE 共用，前端详情区直接渲染）。

    优先用进度键里显式写的 stats.steps（静默段）；否则按阶段定义的范围
    从 percent 反推每步状态——子任务本身不需要知道时间线长什么样。
    """
    sub = sub or {}
    stats = sub.get("stats") if isinstance(sub.get("stats"), dict) else {}
    explicit = stats.get("steps") if isinstance(stats, dict) else None
    if isinstance(explicit, list) and explicit:
        return explicit
    try:
        pct = int(sub.get("percent"))
    except (TypeError, ValueError):
        pct = 0
    status = str(sub.get("status") or "running")
    defs = _SUB_STEP_RANGES.get(stage_key)
    if not defs:
        silent = _SILENT_STEPS.get(stage_key)
        if silent:
            # 静默段子任务只写 percent、不带 steps（如公理扫描 35%）：按第一阶段
            # 合成「当前进行中 + 后续待开始」，避免前端退化成静态清单全 pending。
            # 进入后段时管线会写显式 steps，这里的合成结果随即被覆盖。
            state = {"completed": "done", "failed": "failed",
                     "cancelled": "cancelled"}.get(status, "running")
            return _phase_steps(stage_key, silent[0][0], state,
                                100 if state == "done" else pct)
        label = next((lbl for k, lbl in STAGE_DEFS if k == stage_key), stage_key)
        state = {"completed": "done", "failed": "failed",
                 "cancelled": "cancelled"}.get(status, "running")
        return [_step(stage_key, label, state, 100 if state == "done" else pct)]
    steps: list[dict] = []
    for key, label, lo, hi in defs:
        if status == "completed" or pct >= hi:
            steps.append(_step(key, label, "done", 100))
        elif pct < lo:
            # 严格小于：pct == lo 表示该步骤刚开始（如缝合 0/3 → 82%），
            # 应显示 running 0%，而不是还停在「待开始」（2026-11-16 实测）
            steps.append(_step(key, label, "pending", 0))
        else:
            state = {"failed": "failed", "cancelled": "cancelled"}.get(status, "running")
            steps.append(_step(key, label, state,
                               int((pct - lo) * 100 / max(1, hi - lo))))
    if status in ("failed", "cancelled") and not any(
            s["state"] == status for s in steps):
        # 百分比正好落在边界（如 5%）时没有「进行中」步：把第一个未开始的标成失败/取消
        for s in steps:
            if s["state"] == "pending":
                s["state"] = status
                break
    return steps


def _sync_stage_times(stages: list[dict], current: Optional[str] = None) -> None:
    """补齐每阶段 started_at/finished_at/duration_seconds（UTC ISO）。

    只在进度快照前按当前 status 补一次，不要求每个状态赋值点都记得写时间：
    running → 起跑时间；done/failed/cancelled → 收尾时间与耗时；
    skipped/pending 没有实际执行，保持为空（前端显示「—」）。

    current：管线当前阶段。有的阶段先置 done 再做收尾（公理扫描后的业务规则
    挖掘、裁决后的能力问题评测）——只对 current 刷新收尾时间，后续阶段推进后
    即冻结，既不丢收尾耗时也不会被心跳无限拉长。
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for st in stages:
        status = st.get("status")
        if status == "running":
            if not st.get("started_at"):
                st["started_at"] = now
            st["finished_at"] = None
            st["duration_seconds"] = None
        elif status in ("done", "failed", "cancelled"):
            if not st.get("started_at"):
                st["started_at"] = now
            if not st.get("finished_at") or st.get("key") == current:
                st["finished_at"] = now
            if st.get("duration_seconds") is None or st.get("key") == current:
                try:
                    t0 = datetime.fromisoformat(str(st["started_at"]))
                    t1 = datetime.fromisoformat(str(st["finished_at"]))
                    st["duration_seconds"] = round(
                        max(0.0, (t1 - t0).total_seconds()), 1)
                except (TypeError, ValueError):
                    pass


def _init_stage_sub(task_id: str, stage_key: str, message: str = "") -> None:
    """阶段一开始就把子步骤清单写进子进度键。

    子任务启动前可能先做准备工作（如骨架抽取前的 CQ 生成，数秒到数十秒），
    这段时间子键不存在，前端只能显示「等待本阶段子步骤进度…」（2026-11-16 实测）。
    """
    label = next((lbl for k, lbl in STAGE_DEFS if k == stage_key), stage_key)
    _sub_stage_progress(
        task_id, stage_key, 0, message or f"{label}准备中…",
        steps=build_sub_steps(stage_key, {"percent": 0, "status": "running"}))


def _sub_stage_progress(task_id: str, stage_key: str, percent: int,
                        message: str, steps: Optional[list[dict]] = None) -> None:
    """把静默段写进当前阶段的子进度键（前端「当前阶段」区域直接展示）。

    业务规则挖掘/能力问题评测没有独立阶段，但也不能让用户看着阶段完成文案
    干等；写完后 SSE 会把消息带进现有子进度区，不改前端也能看到消息。
    steps：阶段内子步骤清单（时间线详情区逐条进度条），由 _phase_steps 生成；
    目前前端尚未消费，仅随进度键下发（2026-11-16 按原型预留）。
    """
    try:
        set_task_progress(f"{task_id}:{stage_key}", stage_key, percent, message,
                          {"steps": steps} if steps else {}, queue="extract")
    except Exception:  # noqa: BLE001 —— 进度写入失败不影响管线
        pass


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
    # 数据库异常必须先于“连接异常”判定：MySQL 重启/OOM 时会抛 "Lost connection to
    # MySQL server" / "Can't connect to MySQL server"，旧逻辑会误报成模型服务连不上
    # （2026-11-13 项目 17 实测：MySQL 容器 OOM 重启，构建失败提示指向模型配置）。
    if any(k in low for k in ("mysql", "pymysql", "sqlalchemy", "operationalerror",
                              "database", "lost connection", "lock wait timeout")):
        return ("数据库连接异常：构建过程中与数据库的连接中断或超时（数据库可能正在重启"
                "或资源不足）。请稍后重新构建；若反复出现，请检查 MySQL 容器状态与内存限制。")
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
    _sync_stage_times(stages, current=stage_key)
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
        # 剖析：把子任务进度里的统计（切片数/缓存命中/批次数等）带进阶段指标
        if sub_prog and isinstance(sub_prog.get("stats"), dict):
            profile_metric(f"{stage_key}_stats", sub_prog["stats"])
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
        _sync_stage_times(stages, current=stage_key)
        base = _stage_index[stage_key]
        frac = (pct_in_stage if pct_in_stage is not None else (1.0 if status_change else 0.0))
        # round 而非 int：解析阶段第一步只占全程 0.6%，int 会截成 0%，
        # 看起来像「子步骤都完成了总进度还没动」（2026-11-17 用户反馈）
        percent = min(99, round((base + max(0.0, min(1.0, frac)))
                                * 100 / len(STAGE_DEFS)))
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

    # 耗时剖析（2026-11）：每次 run 一个本地 JSON；采集失败绝不影响管线本体
    try:
        _prof = begin_pipeline_profile(project_id, task_id,
                                       trigger_user_id=trigger_user_id,
                                       options=options)
        _prof.add_metrics({
            "depth": depth,
            "depth_label": profile.get("label"),
            "restart": restart,
            "selfcheck_rounds": profile.get("selfcheck_rounds"),
            "include_axioms": profile.get("include_axioms"),
        })
        _enqueued_at = options.get("_enqueued_at")
        if _enqueued_at:
            try:
                _prof.add_metric("queue_wait_seconds",
                                 round(max(0.0, time.time() - float(_enqueued_at)), 6))
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001
        _prof = None

    def _prof_extra() -> dict:
        """把本次剖析文件路径带进进度 stats，页面/日志可直接定位。"""
        try:
            return {"profile_file": str(_prof.file_path)} if _prof is not None else {}
        except Exception:  # noqa: BLE001
            return {}

    def _check_stage_result(res: Any, stage_key: str) -> None:
        """子任务结果传导：cancelled 停管线；failed 同样必须停并报错。

        子任务（如 run_instance_extraction 全切片失败）以 res.result={'status':
        'failed', 'reason': ...} 字典返回而非抛异常——旧版只拦 cancelled，
        failed 被无视后阶段照样标 done、管线假装继续跑，最终假 completed
        （2026-11-06 测试2 实测：LLM 拒连，实例全丢却无任何报错）。

        重跑语义：该阶段从头发起（抽取产物只在阶段末尾落库，中断不留半成品）；
        已完成阶段不重跑（已解析文档按指纹复用，见 document_pipeline；已有骨架默认跳过）。
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
                # 半途快照：进程被强杀（SIGKILL/OOM，finally 不会执行）时，
                # 仍能保留至少到最近 60s 的耗时与 LLM 调用现场
                if _prof is not None:
                    _prof.write_snapshot()
                _sync_stage_times(stages)
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
                if _prof is not None:
                    _prof.status = "failed"
                    _prof.error = "project_not_found"
                finish_task_progress(task_id, "failed", f"项目不存在: {project_id}",
                                     stats={"project_id": project_id, "stages": stages})
                return {"status": "failed", "reason": "project_not_found"}

            # ── 阶段 1：解析文档（backend=auto，自动路由含扫描件嗅探）──
            # 已解析文档是否复用由后端按指纹判定（见 document_pipeline 顶部说明）：
            # 文件/切片策略变 → 重新解析；仅嵌入模型或集合变 → 只重嵌入；否则复用。
            from app.infrastructure.database import UploadedDocument
            from app.services.document_pipeline import (
                build_vector_fingerprint,
                decide_parse_action,
                resync_document_vectors,
                run_parse,
            )

            st = _stage(stages, "parse")
            st["status"] = "running"
            profile_stage_begin("parse", "解析文档")

            def _parse_steps(inspect_state: str = "running",
                             resync_state: str = "pending", resync_pct: int = 0,
                             parse_state: str = "pending", parse_pct: int = 0) -> list[dict]:
                """解析阶段的子步骤：检查复用 → 补写向量 → 逐份解析。

                前端时间线详情区按 steps 逐条渲染；没有这一步时，解析阶段整个
                期间子区为空，会表现为「从 0 直接跳到骨架抽取」（2026-11-16）。
                """
                return [
                    _step("inspect", "检查已解析文档", inspect_state,
                          100 if inspect_state == "done" else 0),
                    _step("resync", "补写向量", resync_state, resync_pct),
                    _step("parse", "逐份解析", parse_state, parse_pct),
                ]

            _sub_stage_progress(
                task_id, "parse", 0, "检查已解析文档与切片…",
                steps=_parse_steps(inspect_state="running"))
            _prog("parse", "检查已解析文档与切片…", 0.0)
            # 所有未删除文档都参与判定：uploaded/failed 必解析；parsing 在途的
            # 等它结束（否则骨架阶段读到 0 切片直接失败）；parsed 的按指纹决定
            # 「直接复用 / 只重嵌入向量 / 重新解析」——restart 不再无条件重解析。
            parse_statuses = ("uploaded", "failed", "parsing", "parsed")
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
                _sub_stage_progress(
                    task_id, "parse", 0, st["detail"],
                    steps=_parse_steps(inspect_state="running"))
                import time as _time

                for _i in range(180):
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
                    _sub_stage_progress(
                        task_id, "parse", 0,
                        f"等待 {still} 个解析中的文档…（已 {(_i + 1) * 10} 秒）",
                        steps=_parse_steps(inspect_state="running"))
                db.expire_all()
                docs = (db.query(UploadedDocument)
                        .filter(UploadedDocument.project_id == project_id,
                                UploadedDocument.deleted_at.is_(None),
                                UploadedDocument.parse_status.in_(parse_statuses))
                        .all())
            # 阶段 B：逐文档判定处置（复用 / 只重嵌入 / 重新解析）
            parsed_docs = [d for d in docs if d.parse_status == "parsed"]
            vector_fp = build_vector_fingerprint() if parsed_docs else ""
            _vm_holder: list = []

            def _vec_manager():
                """惰性连接向量库：只有解析指纹对得上的文档才需要。"""
                if not _vm_holder:
                    from app.services.document_pipeline import _new_vector_manager

                    _vm_holder.append(_new_vector_manager())
                return _vm_holder[0]

            todo: list = []      # 需要重新解析
            resync: list = []    # 切片可复用，只需重嵌入向量
            reused = 0           # 直接复用
            from app.core.logging import logger as _plog
            for d in docs:
                if d.parse_status in ("uploaded", "failed"):
                    todo.append(d)
                elif d.parse_status == "parsed":
                    action = decide_parse_action(
                        d, db, backend="auto", vector_fp=vector_fp,
                        vm_getter=_vec_manager)
                    _plog.info(f"[pipeline] 解析复用判定 doc={d.id}({d.filename}): {action}")
                    if action == "reparse":
                        todo.append(d)
                    elif action == "resync_vectors":
                        resync.append(d)
                    else:
                        reused += 1

            _parse_judge_msg = (f"判定完成：待解析 {len(todo)} 个、"
                                f"补写向量 {len(resync)} 个、复用 {reused} 个")
            _sub_stage_progress(
                task_id, "parse", 5, _parse_judge_msg,
                steps=_parse_steps(
                    inspect_state="done",
                    resync_state="running" if resync else "skipped",
                    parse_state="running" if todo else "skipped"))
            # 第一步（检查已解析文档）已结束，主进度必须跟着走；否则后面
            # 还没开始解析时总进度一直停在 0%（2026-11-17 用户反馈）
            _prog("parse", _parse_judge_msg, 0.05)

            if not todo and not resync:
                if docs:  # 在途解析已全部结束 / 已解析文档全部命中复用
                    st["status"] = "done"
                    st["detail"] = (f"{reused} 个已解析文档直接复用（未重复解析）"
                                    if reused else f"{len(docs)} 个文档已就绪")
                    _sub_stage_progress(
                        task_id, "parse", 100, st["detail"],
                        steps=_parse_steps(inspect_state="done", resync_state="skipped",
                                           parse_state="skipped"))
                    _prog("parse", st["detail"])
                else:
                    st["status"] = "skipped"
                    st["detail"] = "没有待解析文档"
                    _sub_stage_progress(
                        task_id, "parse", 100, st["detail"],
                        steps=_parse_steps(inspect_state="skipped", resync_state="skipped",
                                           parse_state="skipped"))
                    _prog("parse", "没有待解析文档")
            else:
                parse_failed: list[str] = []
                parse_doc_times: list[dict] = []
                resync_times: list[dict] = []
                total_work = max(1, len(resync) + len(todo))
                done_idx = 0
                # 先补写向量：换嵌入模型/重建集合不需要再读原件、再切片
                for i, d in enumerate(resync):
                    _cancel_check()
                    st["detail"] = f"补写向量 {i + 1}/{len(resync)}"
                    _prog("parse", f"补写向量 {i + 1}/{len(resync)}：{d.filename}",
                          max(0.05, done_idx / total_work))
                    _sub_stage_progress(
                        task_id, "parse", max(5, int(100 * done_idx / total_work)),
                        f"补写向量 {i + 1}/{len(resync)}：{d.filename}",
                        steps=_parse_steps(
                            inspect_state="done", resync_state="running",
                            resync_pct=int(100 * i / max(1, len(resync))),
                            parse_state="running" if todo else "skipped"))
                    _doc_t0 = time.perf_counter()
                    try:
                        _n = resync_document_vectors(d, db, vector_fp)
                        _ok = bool(_n)
                        _err = None if _ok else "向量写入未完成（Milvus/嵌入服务）"
                    except Exception as e:  # noqa: BLE001 —— 向量补写失败不阻断管线
                        _n, _ok, _err = 0, False, str(e)[:300]
                    resync_times.append({
                        "document_id": d.id,
                        "filename": d.filename,
                        "seconds": round(time.perf_counter() - _doc_t0, 6),
                        "ok": _ok,
                        "error": _err,
                        "vectors": _n,
                    })
                    done_idx += 1
                profile_metric("parse_vector_resync", resync_times)
                for i, d in enumerate(todo):
                    _cancel_check()
                    sub_id = f"{task_id}:parse:{d.id}"
                    st["task_id"] = sub_id
                    st["detail"] = f"{i + 1}/{len(todo)}"
                    _prog("parse", f"解析中 {i + 1}/{len(todo)}：{d.filename}",
                          max(0.05, done_idx / total_work))
                    _sub_stage_progress(
                        task_id, "parse", max(5, int(100 * done_idx / total_work)),
                        f"解析中 {i + 1}/{len(todo)}：{d.filename}",
                        steps=_parse_steps(
                            inspect_state="done",
                            resync_state="done" if resync else "skipped",
                            resync_pct=100,
                            parse_state="running",
                            parse_pct=int(100 * i / max(1, len(todo)))))
                    # 单文档解析要几十秒、内部有 5/30/55/70/88% 的真实进度
                    # （run_parse 写在 {sub_id} 键里）；不折进主进度的话，第一份
                    # 文档解析全程总进度都停在 0%（2026-11-16 用户反馈）
                    _doc_base = max(0, done_idx - 1)
                    _doc_i, _doc_n, _doc_name = i, len(todo), d.filename
                    _doc_stop = _threading.Event()

                    def _doc_ticker(_sid=sub_id, _base=_doc_base, _i=_doc_i,
                                    _n=_doc_n, _name=_doc_name) -> None:
                        while not _doc_stop.wait(2):
                            p = get_task_progress(_sid) or {}
                            try:
                                dp = int(p.get("percent") or 0)
                            except (TypeError, ValueError):
                                dp = 0
                            frac = max(0.05, min(1.0, (_base + dp / 100) / total_work))
                            msg = f"解析中 {_i + 1}/{_n}：{_name}"
                            _prog("parse", msg, frac)
                            _sub_stage_progress(
                                task_id, "parse", int(100 * frac), msg,
                                steps=_parse_steps(
                                    inspect_state="done",
                                    resync_state="done" if resync else "skipped",
                                    resync_pct=100,
                                    parse_state="running", parse_pct=dp))

                    _doc_thread = _threading.Thread(
                        target=_doc_ticker, name=f"parse-tick-{task_id}", daemon=True)
                    _doc_thread.start()
                    done_idx += 1
                    _doc_t0 = time.perf_counter()
                    _parse_res: dict = {}
                    try:
                        _parse_res = run_parse(d.id, backend="auto", task_id=sub_id) or {}
                        _doc_ok, _doc_err = True, None
                    except Exception as e:  # noqa: BLE001 —— 单文档失败继续其余
                        _doc_ok, _doc_err = False, str(e)[:300]
                        parse_failed.append(f"{d.filename}: {str(e)[:120]}")
                    finally:
                        _doc_stop.set()
                        _doc_thread.join(timeout=1)
                    parse_doc_times.append({
                        "document_id": d.id,
                        "filename": d.filename,
                        "seconds": round(time.perf_counter() - _doc_t0, 6),
                        "ok": _doc_ok,
                        "error": _doc_err,
                        "chunks": _parse_res.get("chunks"),
                        "chars": _parse_res.get("chars"),
                        "backend": _parse_res.get("backend"),
                        "page_count": _parse_res.get("page_count"),
                    })
                profile_metric("parse_documents", parse_doc_times)
                if todo and len(parse_failed) == len(todo):
                    st["status"] = "failed"
                    st["detail"] = f"全部 {len(todo)} 个文档解析失败"
                    _sub_stage_progress(
                        task_id, "parse", int(100 * done_idx / total_work),
                        st["detail"],
                        steps=_parse_steps(
                            inspect_state="done",
                            resync_state="done" if resync else "skipped", resync_pct=100,
                            parse_state="failed", parse_pct=100))
                    profile_stage_end("parse", status="failed", detail=st["detail"])
                    raise RuntimeError("全部文档解析失败，首因：" + parse_failed[0])
                st["status"] = "done"
                parts = []
                if todo:
                    parts.append(f"{len(todo) - len(parse_failed)}/{len(todo)} 个文档解析")
                if reused:
                    parts.append(f"复用 {reused} 个已解析文档")
                if resync:
                    ok_vectors = sum(1 for x in resync_times if x.get("ok"))
                    parts.append(f"重嵌入向量 {ok_vectors}/{len(resync)} 个")
                st["detail"] = "，".join(parts) if parts else "文档已就绪"
                if parse_failed:
                    st["detail"] += f"（{len(parse_failed)} 个解析失败）"
                summary["parse"] = {"total": len(todo), "failed": parse_failed,
                                    "reused": reused, "vector_resync": len(resync)}
                _sub_stage_progress(
                    task_id, "parse", 100, f"解析完成（{st['detail']}）",
                    steps=_parse_steps(
                        inspect_state="done",
                        resync_state="done" if resync else "skipped", resync_pct=100,
                        parse_state="done" if todo else "skipped", parse_pct=100))
                _prog("parse", f"解析完成（{st['detail']}）")
            profile_stage_end("parse", status=st["status"], detail=st["detail"])

            # 模型前置检查：解析不需要 LLM，抽取需要——缺配置时文档已解析完，
            # 报错提示人话（不浪费已完成的解析成果）
            profile_stage_begin("preflight", "构建前置检查", tracked=False)
            from app.adapters.provider import ModelPurpose, resolve_provider

            try:
                _llm = resolve_provider(ModelPurpose.EXTRACT, project_id, db=db)
                has_llm = bool(_llm.model_name and _llm.base_url)
            except Exception:  # noqa: BLE001 —— 未配置/密钥解密失败都按缺模型处理
                has_llm = False
            if not has_llm:
                profile_stage_end("preflight", status="failed",
                                  detail="未配置可用的抽取模型")
                if _prof is not None:
                    _prof.status = "failed"
                    _prof.error = "no_extract_model"
                finish_task_progress(
                    task_id, "failed",
                    "文档解析已完成，但尚未配置可用的抽取模型：请在「模型配置」中"
                    "添加并启用抽取模型后重新一键构建",
                    stats={"project_id": project_id, "stages": stages, **_prof_extra()})
                return {"status": "failed", "reason": "no_extract_model"}

            model_config_id = None
            if trigger_user_id:
                from app.infrastructure.database import UserModelPick

                pick = (db.query(UserModelPick)
                        .filter(UserModelPick.user_id == trigger_user_id,
                                UserModelPick.purpose == "extract").first())
                model_config_id = pick.config_id if pick else None
            profile_metrics({"model_config_id": model_config_id})
            profile_stage_end("preflight", status="done",
                              detail="抽取模型已配置")
            try:
                from app.core.config import settings as _settings

                profile_metrics({
                    "llm_max_concurrency": max(
                        1, int(getattr(_settings, "LLM_MAX_CONCURRENCY", 16) or 16)),
                    "extract_request_interval": _extract_request_interval(
                        db, project_id, model_config_id),
                })
            except Exception:  # noqa: BLE001
                pass

            tasks = _task_index()

            # ── 阶段 2：骨架抽取（已有骨架跳过，避免覆盖人工修订）──
            _cancel_check()
            _fresh_read()
            st = _stage(stages, "schema")
            profile_stage_begin("schema", "骨架抽取")
            proj = db.query(Project).filter(Project.id == project_id).first()
            gd = proj.graph_data if isinstance(proj.graph_data, dict) else {}
            schema = gd.get("schema") if isinstance(gd.get("schema"), dict) else {}
            if schema.get("classes") and not restart:
                st["status"] = "skipped"
                st["detail"] = f"已有骨架（{len(schema['classes'])} 类），跳过重抽"
                _prog("schema", st["detail"])
                profile_stage_end("schema", status="skipped", detail=st["detail"])
            else:
                st["status"] = "running"
                _prog("schema", "AI 抽取骨架中…", 0.0)
                # 骨架抽取前还有 CQ 设计（数秒到数十秒），先把分步清单写出去
                _init_stage_sub(task_id, "schema", "准备抽取骨架…")
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
                    _cq_t0 = time.perf_counter()
                    cq_res = generate_cqs(db, project_id, created_by=trigger_user_id,
                                          doc_texts=doc_heads)
                    profile_metric("cq_generation_seconds",
                                   round(time.perf_counter() - _cq_t0, 6))
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
                    request_interval=_extract_request_interval(db, project_id, model_config_id),
                    guidance=guidance,
                    model_config_id=model_config_id, strict_config=None), "schema")
                st["status"] = "done"
                _prog("schema", "骨架抽取完成")
                profile_stage_end("schema", status="done",
                                  detail=st.get("detail") or "完成")

            # ── 阶段 3：实例抽取 ──
            _cancel_check()
            st = _stage(stages, "instances")
            profile_stage_begin("instances", "实例抽取")
            st["status"] = "running"
            _prog("instances", "AI 抽取实例中…", 0.0)
            _init_stage_sub(task_id, "instances", "准备抽取实例…")
            _check_stage_result(_run_stage_task(
                tasks["instances"], "instances", stages, task_id,
                project_id=project_id, document_ids=None,
                strict_gate=True, promote_policy="review",
                request_interval=_extract_request_interval(db, project_id, model_config_id),
                model_config_id=model_config_id), "instances")
            st["status"] = "done"
            _prog("instances", "实例抽取完成")
            profile_stage_end("instances", status="done",
                              detail=st.get("detail") or "完成")

            # ── 阶段 4：实体消解 ──
            _cancel_check()
            st = _stage(stages, "resolution")
            profile_stage_begin("resolution", "实体消解")
            st["status"] = "running"
            _prog("resolution", "实体消解中…", 0.0)
            _init_stage_sub(task_id, "resolution", "准备消解实体…")
            _check_stage_result(_run_stage_task(
                tasks["resolution"], "resolution", stages, task_id,
                project_id=project_id), "resolution")
            st["status"] = "done"
            _prog("resolution", "实体消解完成")
            profile_stage_end("resolution", status="done",
                              detail=st.get("detail") or "完成")

            # ── 阶段 5：冲突检测 ──
            _cancel_check()
            st = _stage(stages, "conflicts")
            profile_stage_begin("conflicts", "冲突检测")
            st["status"] = "running"
            _prog("conflicts", "冲突检测中…", 0.0)
            _init_stage_sub(task_id, "conflicts", "准备检测冲突…")
            _check_stage_result(_run_stage_task(
                tasks["conflicts"], "conflicts", stages, task_id,
                project_id=project_id), "conflicts")
            st["status"] = "done"
            _prog("conflicts", "冲突检测完成")
            profile_stage_end("conflicts", status="done",
                              detail=st.get("detail") or "完成")

            # ── 阶段 6：自检修复环（准出标准：机检无硬伤；LLM 定向修复，最小动作）──
            st = _stage(stages, "selfcheck")
            profile_stage_begin("selfcheck", "结构自检")
            st["status"] = "running"
            _prog("selfcheck", "本体质量自检中…", 0.0)
            _init_stage_sub(task_id, "selfcheck", "准备质量自检…")
            _cancel_check()
            _fresh_read()
            try:
                from app.services.pipeline_selfcheck import run_selfcheck_repair

                def _sc_prog(msg: str, frac: float) -> None:
                    _prog("selfcheck", msg, frac)
                    # 自检不走子任务，但详情区同样需要分步明细（机检/修复/复检）
                    pct = max(0, min(99, int(frac * 100)))
                    _sub_stage_progress(
                        task_id, "selfcheck", pct, msg,
                        steps=build_sub_steps(
                            "selfcheck", {"percent": pct, "status": "running"}))

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
                _sub_stage_progress(
                    task_id, "selfcheck", 100, st["detail"],
                    steps=build_sub_steps(
                        "selfcheck", {"percent": 100, "status": "completed"}))
                profile_stage_end("selfcheck", status="done", detail=st["detail"])
            except _PipelineCancelled:
                raise
            except Exception as e:  # noqa: BLE001 —— 自检失败不阻断后续（机检结论照常出）
                st["status"] = "failed"
                st["detail"] = f"自检修复失败（不影响其余结果）: {str(e)[:120]}"
                _prog("selfcheck", st["detail"])
                _sub_stage_progress(
                    task_id, "selfcheck", 0, st["detail"],
                    steps=build_sub_steps(
                        "selfcheck", {"percent": 0, "status": "failed"}))
                profile_stage_end("selfcheck", status="failed", detail=st["detail"])

            # ── 阶段 7：AI 公理扫描（深度=fast 跳过；失败/跳过不阻断收尾）──
            _cancel_check()
            st = _stage(stages, "axioms")
            profile_stage_begin("axioms", "AI 公理扫描")
            if not profile["include_axioms"]:
                st["status"] = "skipped"
                st["detail"] = "快速档跳过公理扫描"
                _prog("axioms", st["detail"])
                profile_stage_end("axioms", status="skipped", detail=st["detail"])
            else:
                st["status"] = "running"
                _prog("axioms", "AI 公理扫描中…", 0.0)
                _init_stage_sub(task_id, "axioms", "准备公理扫描…")
                # 公理扫描子任务只写段内 percent（0-100），把它按 35% 权重
                # 折算到阶段主进度，总进度条才会跟着动（2026-11-17）
                _ax_stop = _threading.Event()

                def _ax_ticker() -> None:
                    while not _ax_stop.wait(2):
                        p = get_task_progress(f"{task_id}:axioms") or {}
                        try:
                            sp = int(p.get("percent") or 0)
                        except (TypeError, ValueError):
                            sp = 0
                        _prog("axioms", "AI 公理扫描中…",
                              _silent_overall("axioms", "scan", sp) / 100)

                _ax_thread = _threading.Thread(
                    target=_ax_ticker, name=f"ax-tick-{task_id}", daemon=True)
                _ax_thread.start()
                try:
                    axiom_res = _run_stage_task(tasks["axioms"], "axioms", stages, task_id,
                                                project_id=project_id, channel="llm")
                    _check_stage_result(axiom_res, "axioms")
                    if (axiom_res.get("llm") or {}).get("skipped") or axiom_res.get("skipped"):
                        st["status"] = "skipped"
                        st["detail"] = "模型不可用，已跳过公理扫描"
                    else:
                        # 状态保持 running 到业务规则挖掘结束（2026-11-16 用户反馈：
                        # 先置 done 再挖规则，页面会在「AI 公理扫描」已完成的格子上
                        # 挂着「业务规则挖掘中…」近 1 分钟，状态与进度自相矛盾）
                        llm_ch = axiom_res.get("llm") or {}
                        n_axioms = int(axiom_res.get("review_created") or 0)
                        if n_axioms > 0:
                            # 汇总行保留条数（此前只写「完成扫描」，子进度里的
                            # 「新增公理建议 11 条」在规则挖掘结束后就丢了）
                            st["detail"] = f"完成扫描，新增公理建议 {n_axioms} 条"
                        elif llm_ch.get("skipped"):
                            st["detail"] = f"未产出公理（{str(llm_ch['skipped'])[:50]}）"
                        else:
                            # "完成扫描"掩盖空手而归（2026-11-08 项目 265 实测：
                            # 方舟限流时段空响应，parsed=0 用户以为扫描没用）
                            st["detail"] = "完成扫描，未产出公理（模型响应为空，稍后重新构建可重试）"
                    _prog("axioms", st["detail"],
                          _silent_overall("axioms", "scan", 100) / 100)
                except _PipelineCancelled:
                    raise  # 取消传导：不得当作阶段失败吞掉
                except Exception as e:  # noqa: BLE001 —— 公理扫描失败不阻碍自动裁决其余项
                    st["status"] = "failed"
                    st["detail"] = f"公理扫描失败（不影响其余结果）: {str(e)[:120]}"
                    _prog("axioms", st["detail"],
                          _silent_overall("axioms", "scan", 100) / 100)
                finally:
                    _ax_stop.set()
                    _ax_thread.join(timeout=1)

                # ── 业务规则挖掘（外部方法论对照：SWRL 控制规则层）：
                #    IF-THEN 产生式候选（IF 侧逐字命中白名单的存在性门禁在
                #    rule_llm 内做）→ 直接落推理规则表（错误规则可停用，风险低）。
                #    失败静默，不影响公理阶段主产物；进度写在公理阶段的子进度键，
                #    界面沿用该阶段的子进度区展示，不新增阶段/前端组件。
                #    阶段状态保持 running 直到本段结束：业务规则挖掘属于公理阶段
                #    的一部分，不能先给阶段打勾再继续干活（2026-11-16 用户反馈）。
                _br_start = _silent_overall("axioms", "business_rules", 5)
                _sub_stage_progress(
                    task_id, "axioms", _br_start, "业务规则挖掘中…",
                    steps=_phase_steps("axioms", "business_rules", "running", 5))
                _prog("axioms", "业务规则挖掘中…", _br_start / 100)
                # 单次 LLM 调用要 1 分钟上下，期间没有可报的百分比；
                # 每 10s 把「已用时」和软进度写进子进度，避免看起来卡死（2026-11-16）
                _br_t0 = time.perf_counter()
                _br_stop = _threading.Event()

                def _br_ticker() -> None:
                    while not _br_stop.wait(10):
                        elapsed = int(time.perf_counter() - _br_t0)
                        pct = min(90, 5 + 10 * (elapsed // 10))
                        overall = _silent_overall("axioms", "business_rules", pct)
                        _sub_stage_progress(
                            task_id, "axioms", overall,
                            f"业务规则挖掘中… 已 {elapsed} 秒",
                            steps=_phase_steps(
                                "axioms", "business_rules", "running", pct))
                        _prog("axioms", f"业务规则挖掘中… 已 {elapsed} 秒",
                              overall / 100)

                _br_thread = _threading.Thread(
                    target=_br_ticker, name=f"br-tick-{task_id}", daemon=True)
                _br_thread.start()
                _br_msg = ""
                _br_step_state = "done"
                try:
                    _fresh_read()
                    rules_res = _mine_business_rules(db, project_id)
                    profile_metric("business_rules_seconds",
                                   round(time.perf_counter() - _br_t0, 6))
                    summary["business_rules"] = rules_res
                    n_rules = int(rules_res.get("rules_added") or 0)
                    if rules_res.get("skipped") and not n_rules:
                        _br_msg = f"业务规则挖掘结束（{str(rules_res['skipped'])[:40]}）"
                        _br_step_state = "skipped"
                    else:
                        st["detail"] += f"，业务规则 {n_rules} 条"
                        _br_msg = f"业务规则挖掘完成：新增规则 {n_rules} 条"
                except Exception as e:  # noqa: BLE001
                    summary["business_rules"] = {"rules_added": 0, "skipped": str(e)[:120]}
                    _br_msg = "业务规则挖掘失败（不影响公理结果）"
                    _br_step_state = "failed"
                finally:
                    _br_stop.set()
                    _br_thread.join(timeout=1)
                _sub_stage_progress(
                    task_id, "axioms", 100, _br_msg,
                    steps=_phase_steps("axioms", "business_rules", _br_step_state, 100))
                # 扫描成功才收尾为 done；扫描本身失败/跳过时保持原状态，只补规则结果
                if st["status"] == "running":
                    st["status"] = "done"
                _prog("axioms", st["detail"])
                profile_stage_end("axioms", status=st["status"], detail=st["detail"])

            # ── 阶段 8：AI 自动裁决（含公理自动申报 + 一致性复验撤回）──
            _cancel_check()
            _fresh_read()
            st = _stage(stages, "adjudicate")
            profile_stage_begin("adjudicate", "AI 结果审查")
            st["status"] = "running"
            # 阶段一开始就把子步骤写出来（结果确认 running / 能力问题评测 pending）：
            # 裁决本身没有子任务进度键，等 CQ 开始才写会导致整个裁决期间子区空白
            _adj_start = _silent_overall("adjudicate", "adjudicate", 5)
            _sub_stage_progress(
                task_id, "adjudicate", _adj_start, "结果确认中…",
                steps=_phase_steps("adjudicate", "adjudicate", "running", 5))
            _prog("adjudicate", "结果确认中…", _adj_start / 100)
            from app.services.auto_adjudication import run_auto_adjudication

            # 裁决里可能带 LLM 复核遗留项，耗时不定：每 5s 报已用时
            _adj_t0 = time.perf_counter()
            _adj_stop = _threading.Event()

            def _adj_ticker() -> None:
                while not _adj_stop.wait(5):
                    elapsed = int(time.perf_counter() - _adj_t0)
                    # 审查无真实百分比，按耗时给软进度（每 5s +10%，封顶 90%），
                    # 避免子步骤一直显示 0%（2026-11-16 实测）
                    pct = min(90, 10 * (elapsed // 5))
                    overall = _silent_overall("adjudicate", "adjudicate", pct)
                    _sub_stage_progress(
                        task_id, "adjudicate", overall,
                        f"结果确认中… 已 {elapsed} 秒",
                        steps=_phase_steps("adjudicate", "adjudicate", "running", pct))
                    _prog("adjudicate", f"结果确认中… 已 {elapsed} 秒",
                          overall / 100)

            _adj_thread = _threading.Thread(
                target=_adj_ticker, name=f"adj-tick-{task_id}", daemon=True)
            _adj_thread.start()
            # fast 档（2026-11-09 用户决策）：模型抽取结果不再 LLM 复核，默认通过；
            # 保留零成本的确定性安全检查（矛盾公理撤回/低相似度合并不执行），
            # "结果确认"阶段从分钟级缩到 10-30s
            try:
                adj = run_auto_adjudication(project_id, db=db, level="fast")
            finally:
                _adj_stop.set()
                _adj_thread.join(timeout=1)
            summary["adjudication"] = adj
            # 不在这里置 done：能力问题评测还在同一张阶段卡里跑（2026-11-16
            # 用户反馈——8 个阶段全打勾了，页面还挂着「能力问题评测中…」）
            _adj_parts = [f"自动处理 {adj['auto_approved']} 项"]
            if adj.get("rolled_back"):
                _adj_parts.append(f"撤回 {adj['rolled_back']} 条引发矛盾的公理")
            _llm_left = int((adj.get("llm_leftover") or {}).get("llm_adjudicated") or 0)
            if _llm_left:
                _adj_parts.append(f"模型复核遗留 {_llm_left} 项")
            if adj.get("fallback_closed"):
                _adj_parts.append(f"兜底关闭 {adj['fallback_closed']} 项")
            st["detail"] = "，".join(_adj_parts)
            profile_stage_end("adjudicate", status="done", detail=st["detail"])

            # 收尾统计：剩余需人裁决的数量
            profile_stage_begin("finalize_stats", "收尾统计", tracked=False)
            from sqlalchemy import func as sa_func

            from app.infrastructure.database import ReviewItem

            pending_rows = (db.query(ReviewItem.priority, sa_func.count())
                            .filter(ReviewItem.project_id == project_id,
                                    ReviewItem.status == "pending")
                            .group_by(ReviewItem.priority).all())
            by_priority = {p: int(c) for p, c in pending_rows}
            need_human = by_priority.get("critical", 0) + by_priority.get("high", 0)
            profile_stage_end("finalize_stats", status="done",
                              detail=f"待人工 {need_human} 项")

            # ── 能力问题评测（外部方法论对照：CQ 在真实产物上验证）：
            #    先补 SPARQL 模板（骨架就绪后才能生成），再双口径评测——
            #    有模板且图可查 = SPARQL 真执行出答案行；否则回落检索式。
            #    任一环节失败都不影响构建终态 ──
            profile_stage_begin("cq_eval", "能力问题评测", tracked=False)
            # 进度写进「AI 结果审查」的阶段子进度区（与业务规则挖掘同一展示位）
            _cq_start = _silent_overall("adjudicate", "cq_eval", 5)
            _sub_stage_progress(
                task_id, "adjudicate", _cq_start, "能力问题评测中…",
                steps=_phase_steps("adjudicate", "cq_eval", "running", 5))
            _prog("adjudicate", "能力问题评测中…", _cq_start / 100)
            # CQ 实测约 28s，其中 attach_sparql 占 27s 且没有百分比可报；
            # 每 5s 写已用时，阶段保持 running 直到评测结束
            cq_note = ""
            _cq_msg = ""
            _cq_step_state = "done"
            _cq_t0 = time.perf_counter()
            _cq_stop = _threading.Event()

            def _cq_ticker() -> None:
                while not _cq_stop.wait(5):
                    elapsed = int(time.perf_counter() - _cq_t0)
                    # CQ 无真实百分比（attach_sparql 占大半且不可观测），
                    # 同样按耗时给软进度，封顶 90%，结束时置 100%
                    pct = min(90, 5 + 10 * (elapsed // 5))
                    overall = _silent_overall("adjudicate", "cq_eval", pct)
                    _sub_stage_progress(
                        task_id, "adjudicate", overall,
                        f"能力问题评测中… 已 {elapsed} 秒",
                        steps=_phase_steps("adjudicate", "cq_eval", "running", pct))
                    _prog("adjudicate", f"能力问题评测中… 已 {elapsed} 秒",
                          overall / 100)

            _cq_thread = _threading.Thread(
                target=_cq_ticker, name=f"cq-tick-{task_id}", daemon=True)
            _cq_thread.start()
            try:
                from app.services.cq_service import (
                    attach_sparql, ensure_rdf_graphs, evaluate_cqs, list_cqs,
                )

                if list_cqs(db, project_id, enabled_only=True):
                    _cq_steps: dict = {}
                    _t = time.perf_counter()
                    _fresh_read()
                    _cq_steps["fresh_read_seconds"] = round(time.perf_counter() - _t, 6)
                    _t = time.perf_counter()
                    attach_sparql(db, project_id)
                    _cq_steps["attach_sparql_seconds"] = round(time.perf_counter() - _t, 6)
                    # rebuild=True：构建刚改完全部图谱数据，评测必须用新图
                    _t = time.perf_counter()
                    graph_state = ensure_rdf_graphs(project_id, rebuild=True)
                    _cq_steps["ensure_rdf_graphs_seconds"] = round(
                        time.perf_counter() - _t, 6)
                    _t = time.perf_counter()
                    _fresh_read()
                    _cq_steps["fresh_read2_seconds"] = round(time.perf_counter() - _t, 6)
                    _t = time.perf_counter()
                    ev = evaluate_cqs(db, project_id, trigger="auto_post_extract")
                    _cq_steps["evaluate_cqs_seconds"] = round(time.perf_counter() - _t, 6)
                    db.commit()  # CQ 模板/评测结果落库（attach_sparql 无自带提交）
                    profile_metric("cq_eval_steps", _cq_steps)
                    if ev is not None:
                        summary["cq_evaluation_id"] = ev.id
                        summary["cq_graph_state"] = graph_state
                        ok = ev.answerable_count == ev.total_cq
                        mode_note = "（SPARQL 实查）" if ev.mode == "sparql+retrieval" else ""
                        cq_note = (f"，能力问题验证通过 {ev.answerable_count}/{ev.total_cq}{mode_note}"
                                   + ("" if ok else "（部分问题暂无证据，可补充文档后重新构建）"))
                        _cq_msg = f"能力问题评测完成：通过 {ev.answerable_count}/{ev.total_cq}"
                    else:
                        _cq_msg = "能力问题评测结束：无评测结果"
                else:
                    _cq_msg = "能力问题评测结束：无能力问题清单"
                    _cq_step_state = "skipped"
            except Exception as e:  # noqa: BLE001
                from app.core.logging import logger

                logger.warning(f"[pipeline] CQ 评测失败 project={project_id}: {e}")
                _cq_msg = "能力问题评测失败（不影响构建结果）"
                _cq_step_state = "failed"
            finally:
                _cq_stop.set()
                _cq_thread.join(timeout=1)
            _sub_stage_progress(
                task_id, "adjudicate", 100, _cq_msg,
                steps=_phase_steps("adjudicate", "cq_eval", _cq_step_state, 100))
            if cq_note:
                st["detail"] += cq_note
            st["status"] = "done"
            _prog("adjudicate", f"能力问题评测完成{cq_note}" if cq_note else "能力问题评测结束")
            profile_stage_end("cq_eval", status="done", detail=cq_note or "完成")

            # ── 构建报告（外部方法论交付物对照：人话版，零 LLM）──
            profile_stage_begin("build_report", "构建报告", tracked=False)
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
            profile_stage_end("build_report", status="done",
                              detail=("跳过"
                                      if (summary.get("build_report") or {}).get("skipped")
                                      else "完成"))

            profile_stage_begin("finalize", "落终态", tracked=False)
            sc = summary.get("selfcheck") or {}
            remaining = int(sc.get("remaining") or 0)
            sc_note = ("结构自检通过" if remaining == 0
                       else f"结构自检后剩 {remaining} 处需人工确认") if sc else ""
            if _prof is not None:
                _prof.status = "completed"
            finish_task_progress(
                task_id, "completed",
                f"一键构建完成（{profile['label']}档{'，' + sc_note if sc_note else ''}）："
                f"还剩 {need_human} 个高优先级结果等你确认{cq_note}",
                stats={"project_id": project_id, "pipeline": True, "stages": stages,
                       "depth": depth, "pending_by_priority": by_priority,
                       "need_human": need_human, **_prof_extra(), **summary})
            profile_stage_end("finalize", status="done", detail="完成")
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
        _sync_stage_times(stages)
        if _prof is not None:
            _prof.status = "cancelled"
        finish_task_progress(task_id, "cancelled", "已取消：重新构建将从被中断的阶段继续",
                             stats={"project_id": project_id, "stages": stages,
                                    **_prof_extra()})
        return {"status": "cancelled"}
    except Exception as e:  # noqa: BLE001
        # 取消优先：子任务可能因 LLM 调用硬超时抛异常返回，但用户已点停止——
        # 此时按取消收场（格子标 cancelled），不得以 failed 覆盖"已停止"终态
        if is_cancelled(task_id):
            for st in stages:
                if st["status"] == "running":
                    st["status"] = "cancelled"
            _sync_stage_times(stages)
            if _prof is not None:
                _prof.status = "cancelled"
            finish_task_progress(task_id, "cancelled", "已停止（用户取消）",
                                 stats={"project_id": project_id, "stages": stages,
                                        **_prof_extra()})
            return {"status": "cancelled"}
        reason = _friendly_error(e)
        for st in stages:
            if st["status"] == "running":
                st["status"] = "failed"
                st["detail"] = reason  # 红格子上直接写人话原因，不用点开猜
        _sync_stage_times(stages)
        if _prof is not None:
            _prof.status = "failed"
            _prof.error = reason
        finish_task_progress(task_id, "failed", reason,
                             stats={"project_id": project_id, "stages": stages,
                                    **_prof_extra()})
        return {"status": "failed", "reason": reason}
    finally:
        # 剖析收尾：无论成功/失败/取消都落一个完整的本地 JSON（含未闭合阶段）
        try:
            # 兜底：BaseException（KeyboardInterrupt/SystemExit 等）或异常退出没走
            # 到状态设置时，不能把文件写成 "running"——按 failed 收口并注明
            _final_status = _prof.status if _prof is not None else "failed"
            if _prof is not None and _final_status == "running":
                _final_status = "failed"
                _prof.error = _prof.error or "pipeline_ended_without_terminal_status"
            _path = finish_pipeline_profile(
                _prof,
                status=_final_status,
                error=(_prof.error if _prof is not None else None),
                extra={
                    "summary": summary,
                    "stage_statuses": {s["key"]: s["status"] for s in stages},
                })
            if _path:
                from app.core.logging import logger as _plog

                _plog.info(f"[pipeline] 耗时剖析已写入 {_path}")
        except Exception as _pe:  # noqa: BLE001 —— 剖析绝不影响任务终态
            try:
                from app.core.logging import logger as _plog

                _plog.warning(f"[pipeline] 耗时剖析收尾失败: {_pe}")
            except Exception:
                pass


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
        # 锁 + 进度新鲜度：worker 崩溃/服务重启留下的 running 进度超过 3 分钟
        # 不再挡新构建，否则项目要等 6h 锁过期或打开状态页才能重新发起
        if _progress_alive(prog):
            return {"mode": "existing", "task_id": existing, "already_running": True}
        # 死任务落终态：页面按最近任务回落时不会继续显示「构建中」
        if prog is not None and prog.get("status") in ("running", "queued"):
            try:
                finish_task_progress(existing, "cancelled", PIPELINE_STALE_MESSAGE)
            except Exception:  # noqa: BLE001
                pass
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

    # 登记入队时刻：run_pipeline 一开始据此算「排队/派发等待」（只用于剖析，
    # 不参与幂等/定时判定；下划线键不对外暴露）
    options = dict(options)
    options["_enqueued_at"] = time.time()
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


def _parse_schedule_payload(raw) -> Optional[dict]:
    """Redis 定时登记值 → {fire_at(UTC ISO), depth, restart, trigger_user_id}。

    兼容两种历史格式：JSON {fire_at, trigger_user_id, options} 与旧版纯时间串。
    时间统一转成带 +00:00 的 UTC ISO，前端 new Date() 自动转本地展示。
    """
    import datetime as _dt

    txt = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    txt = txt.strip()
    if not txt:
        return None
    fire_raw, options, trigger_user_id = txt, {}, None
    try:
        data = json.loads(txt)
        if isinstance(data, dict):
            fire_raw = str(data.get("fire_at") or "").strip() or txt
            options = data.get("options") if isinstance(data.get("options"), dict) else {}
            trigger_user_id = data.get("trigger_user_id")
    except Exception:  # noqa: BLE001 —— 旧版纯时间串
        pass
    fire_iso = fire_raw
    try:
        fire = _dt.datetime.fromisoformat(fire_raw.replace("Z", "+00:00"))
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=_dt.datetime.now().astimezone().tzinfo)
        fire_iso = fire.astimezone(_dt.timezone.utc).isoformat()
    except Exception:  # noqa: BLE001 —— 解析失败原样返回，前端自行容错
        pass
    return {
        "fire_at": fire_iso,
        "depth": options.get("depth"),
        "restart": bool(options.get("restart")),
        "trigger_user_id": (int(trigger_user_id)
                            if str(trigger_user_id or "").isdigit() else trigger_user_id),
    }


def pipeline_schedule_for(project_id: int) -> Optional[dict]:
    """单个项目的定时登记信息（详情页用）；未登记返回 None。"""
    try:
        return _parse_schedule_payload(
            _redis().get(_SCHEDULE_KEY.format(project_id=project_id)))
    except Exception:  # noqa: BLE001
        return None


def pipeline_scheduled_info() -> dict[int, dict]:
    """批量读取已登记的定时构建（列表页用）：{project_id: schedule_info}。

    已定时的项目通常很少；Redis 抖动时返回空字典，列表退化为不显示时间。
    """
    out: dict[int, dict] = {}
    try:
        r = _redis()
        for key in r.scan_iter(match="pipeline:schedule:*", count=500):
            k = key.decode() if isinstance(key, bytes) else str(key)
            tail = k.rsplit(":", 1)[-1]
            if not tail.isdigit():
                continue  # 跳过 :task 兼容键
            info = _parse_schedule_payload(r.get(k))
            if info:
                out[int(tail)] = info
    except Exception as e:  # noqa: BLE001
        from app.core.logging import logger

        logger.warning(f"[pipeline] 批量读取定时构建信息失败: {e}")
    return out


def pipeline_scheduled_at(project_id: int) -> Optional[str]:
    """已登记的定时构建时间（UTC ISO，未开跑）；无则 None。

    旧实现直接返回 Redis 原始 JSON（整段 payload），现在解析成 fire_at。
    """
    info = pipeline_schedule_for(project_id)
    return info.get("fire_at") if info else None


def pipeline_active_project_ids() -> tuple[set[int], set[int]]:
    """批量读取「构建中」与「已登记定时」的项目 ID（列表状态筛选用）。

    状态存在 Redis（管线锁/定时登记），不能直接写进 SQL WHERE，所以先批量取出
    再转成 id 集合。Redis 抖动时按「没有构建中项目」降级，不影响列表本身。
    """
    running: set[int] = set()
    scheduled: set[int] = set()
    try:
        r = _redis()
        for key in r.scan_iter(match="pipeline:running:*", count=500):
            tail = str(key).rsplit(":", 1)[-1]
            if not tail.isdigit():
                continue
            # 与状态端点同口径：服务重启留下的死锁不能让列表一直显示「构建中」
            tid = r.get(key)
            try:
                prog = get_task_progress(tid) if tid else None
            except Exception:  # noqa: BLE001 —— Redis 抖动：按构建中处理
                prog = None
            if _progress_alive(prog):
                running.add(int(tail))
        for key in r.scan_iter(match="pipeline:schedule:*", count=500):
            # 同前缀还有 {id}:task 的携带键，只取纯数字的那一类
            tail = str(key).rsplit(":", 1)[-1]
            if tail.isdigit():
                scheduled.add(int(tail))
    except Exception as e:  # noqa: BLE001 —— 状态筛选降级，不影响列表
        logger.warning(f"[pipeline] 批量读取构建状态失败: {e}")
    return running, scheduled


def pipeline_running_task_id(project_id: int) -> Optional[str]:
    """当前（或最近 6h 内）管线的 task_id；锁过期/不存在返回 None。"""
    try:
        return _redis().get(_RUNNING_KEY.format(project_id=project_id))
    except Exception:  # noqa: BLE001
        return None


def reconcile_pipeline_locks(stale_seconds: int = PIPELINE_STALE_SECONDS) -> dict:
    """服务启动自检：核对运行锁与 Celery 实际在跑的任务，立刻收尾死锁。

    服务重启（compose 重建 backend/worker）后，被杀掉的任务不会再写心跳，
    但 Redis 锁仍在——只靠 3 分钟看门狗会让页面持续显示「构建中」。这里
    在启动阶段查询 Celery 的 active/reserved/scheduled：

    - worker 在线且任务在其中 → 仍在跑，保留锁
    - worker 在线但任务不在任何队列 → 死锁，标 cancelled + 释放
    - worker 全不在线 → 无法判断是否会被重新投递，只清理心跳已超时的
    """
    from app.tasks.celery_app import celery_app

    r = _redis()
    guard = "pipeline:reconcile:guard"
    try:
        if not r.set(guard, "1", nx=True, ex=90):
            return {"skipped": "another_instance"}
    except Exception:  # noqa: BLE001 —— 守卫失败不阻断自检
        pass
    try:
        insp = celery_app.control.inspect(timeout=2.0)
        active = insp.active() or {}
        reserved = insp.reserved() or {}
        scheduled = insp.scheduled() or {}
        workers = len(set(active) | set(reserved) | set(scheduled))
        live: set[str] = set()
        for mapping in (active, reserved):
            for tasks in mapping.values():
                for t in tasks or []:
                    tid = t.get("id") if isinstance(t, dict) else None
                    if tid:
                        live.add(str(tid))
        for tasks in scheduled.values():
            for t in tasks or []:
                if not isinstance(t, dict):
                    continue
                tid = (t.get("request") or {}).get("id") or t.get("id")
                if tid:
                    live.add(str(tid))

        released: list[int] = []
        kept: list[int] = []
        unknown: list[int] = []
        now = int(time.time())
        for key in list(r.scan_iter("pipeline:running:*")):
            tail = str(key).rsplit(":", 1)[-1]
            if not tail.isdigit():
                continue
            pid = int(tail)
            tid = r.get(key)
            prog = get_task_progress(tid) if tid else None
            status = (prog or {}).get("status")
            if status in ("completed", "failed", "cancelled"):
                r.delete(key)
                released.append(pid)
                continue
            if tid and str(tid) in live:
                kept.append(pid)
                continue
            if workers == 0:
                idle = now - int((prog or {}).get("updated_at") or 0)
                if idle <= stale_seconds:
                    unknown.append(pid)  # 刚重启、worker 未上线：交给看门狗
                    continue
            # worker 在线但任务不在任何队列（或心跳已超时）→ 死锁
            if tid and status in ("running", "queued"):
                try:
                    finish_task_progress(
                        tid, "cancelled", "构建已中断：服务重启后任务未恢复（启动自检已清理）")
                except Exception:  # noqa: BLE001
                    pass
            r.delete(key)
            released.append(pid)
        return {"workers": workers, "released": released, "kept": kept,
                "unknown": unknown}
    finally:
        try:
            r.delete(guard)
        except Exception:  # noqa: BLE001
            pass


def _progress_alive(prog: Optional[dict],
                    stale_seconds: int = PIPELINE_STALE_SECONDS) -> bool:
    """进度键是否代表还活着的任务（心跳 60s，默认 180s 容错）。

    缺失（刚入队尚未写 / 24h 后过期）按存活处理；终态或连续 3 次心跳没更新
    视为死亡——服务重启/worker 掉线后不能继续把项目显示成「构建中」。
    """
    if not prog:
        return True
    if prog.get("status") in ("completed", "failed", "cancelled"):
        return False
    return int(time.time()) - int(prog.get("updated_at") or 0) <= stale_seconds


def pipeline_build_active(project_id: int,
                          stale_seconds: int = PIPELINE_STALE_SECONDS) -> bool:
    """项目是否正在构建（文档上传/删除的互斥判定）。

    运行锁 + 进度新鲜度双重判定：只看锁会在 worker 崩溃后把项目误锁 6 小时，
    文档接口被无辜拒绝；进度 3 分钟不更新与状态端点的看门狗同口径，视为死任务。
    进度键缺失（24h 后过期）时保守按构建中处理——锁最长 6h，状态页/锁过期会清理。
    """
    tid = pipeline_running_task_id(project_id)
    if not tid:
        return False
    try:
        prog = get_task_progress(tid)
    except Exception:  # noqa: BLE001 —— Redis 抖动：保守拒绝
        return True
    return _progress_alive(prog, stale_seconds)


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


__all__ = ["STAGE_DEFS", "DEPTH_PROFILES", "build_sub_steps", "display_sub_percent",
           "run_pipeline", "dispatch_pipeline_run",
           "run_scheduled_pipeline", "cancel_scheduled_pipeline",
           "pipeline_scheduled_at", "pipeline_schedule_for", "pipeline_scheduled_info",
           "pipeline_running_task_id", "pipeline_build_active",
           "reconcile_pipeline_locks",
           "release_pipeline_lock", "pipeline_active_project_ids"]
