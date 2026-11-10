# app/services/pipeline_profiler.py - 一键构建管线耗时剖析（2026-11）
#
# 目标（对应产品诉求：定位 /api/projects/{id}/pipeline/run 到底耗在哪）：
#   1) 阶段耗时 + 总耗时严格对账：timeline 是连续区间，sum(段耗时) == 总耗时；
#   2) 每次 LLM 调用单独记录：开始/结束、耗时、首 token 延迟、重试次数/等待、
#      prompt/completion token（接口返回 usage 优先，缺失时按字符估算并标记）；
#   3) 每次管线运行写一个本地 JSON 文件（默认 logs/pipeline_profiles/），
#      阶段结束即落盘，进程被杀也能看到已完成阶段。
#
# 实现要点：
#   - ContextVar 持有当前 run / stage / 当前 LLM 调用作用域；
#   - ThreadPoolExecutor 不会自动继承上下文，抽取并行提交改用
#     submit_with_context()（extraction.py 三处）；
#   - 任何采集/写文件异常都吞掉并 warning，绝不影响管线本体。

from __future__ import annotations

import contextvars
import inspect
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

try:  # 后端根目录（src/backend），用于把相对配置路径解析到稳定位置
    _BACKEND_ROOT = Path(__file__).resolve().parents[2]
except Exception:  # pragma: no cover - 异常打包方式下的兜底
    _BACKEND_ROOT = Path.cwd()

_logger = None


def _log_warning(msg: str) -> None:
    global _logger
    try:
        if _logger is None:
            from app.core.logging import logger as _l
            _logger = _l
        _logger.warning(msg)
    except Exception:
        pass


# ── ContextVar：run / stage / 当前 LLM 调用 ─────────────────────────────
_current_profile: contextvars.ContextVar[Optional["PipelineRunProfile"]] = \
    contextvars.ContextVar("pipeline_profile", default=None)
_current_stage: contextvars.ContextVar[Optional[dict]] = \
    contextvars.ContextVar("pipeline_stage", default=None)
_current_call_scope: contextvars.ContextVar[Optional["LLMCallScope"]] = \
    contextvars.ContextVar("pipeline_llm_call", default=None)

_ACTIVE_RUNS: dict[str, "PipelineRunProfile"] = {}
_ACTIVE_LOCK = threading.Lock()


def current_profile() -> Optional["PipelineRunProfile"]:
    return _current_profile.get()


def current_stage_key() -> Optional[str]:
    st = _current_stage.get()
    return st.get("key") if isinstance(st, dict) else None


def current_stage_label() -> Optional[str]:
    st = _current_stage.get()
    return st.get("label") if isinstance(st, dict) else None


def set_current_stage(key: str, label: Optional[str] = None) -> None:
    """在复制出的上下文中标记“异步/后台”阶段（如抽取完成后的诊断线程）。

    这类调用并发于主阶段之外，不能算进任何主阶段的墙钟；单独归组到
    stage=async_diagnosis，由 _stage_summary 以 kind=async 呈现。
    """
    _current_stage.set({"key": key, "label": label or key})


def submit_with_context(pool, fn: Callable, *args, **kwargs):
    """把当前 ContextVar（run/stage）传播到线程池任务。

    Python 的 ThreadPoolExecutor 默认不复制 context——抽取的并行切片线程若直接
    submit，LLM 调用会归不到当前阶段（profiler 只能看到“无阶段”）。这里显式
    copy_context，零侵入替换原有 pool.submit。
    """
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


# ── 工具函数 ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """轻量 token 估算：CJK 按字计 1，其余按 4 字符 1 token。

    与 document_pipeline.estimate_tokens 同一口径；仅用于模型未返回 usage 时
    的兜底，结果会标记 tokens_estimated=true，不冒充精确值。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿"
              or "㐀" <= ch <= "䶿")
    other = len(text) - cjk
    return cjk + (other // 4 if other else 0)


def _iso_now(ts: Optional[float] = None) -> str:
    t = datetime.fromtimestamp(ts if ts is not None else time.time()).astimezone()
    return t.isoformat(timespec="milliseconds")


def _safe_component(v: Any, max_len: int = 80) -> str:
    s = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(v or "unknown")).strip("_")
    return (s or "unknown")[:max_len]


def _jsonable(value: Any) -> Any:
    """把任意对象转成可 json 序列化的值（失败一律转 str，不抛）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _truncate(value: Any, limit: int = 500) -> str:
    s = str(value)
    return s if len(s) <= limit else s[:limit] + "…"


def _union_seconds(intervals: list[tuple[float, float]]) -> float:
    """多个并行 LLM 调用的时间并集（真实“模型占用墙钟”），而非简单求和。"""
    spans = sorted((max(0.0, a), max(0.0, b)) for a, b in intervals if b > a)
    if not spans:
        return 0.0
    total = 0.0
    cur_s, cur_e = spans[0]
    for s, e in spans[1:]:
        if s > cur_e:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    total += cur_e - cur_s
    return total


def _caller_hint() -> str:
    """LLM 调用发生在哪个业务函数（module.function:line），便于归因。"""
    try:
        f = inspect.currentframe()
        # 跳过错综的 profiler / llm_client 帧
        while f is not None:
            mod = f.f_globals.get("__name__", "")
            fn = f.f_code.co_name
            if (mod.startswith("app.") and "pipeline_profiler" not in mod
                    and "llm_client" not in mod):
                return f"{mod}.{fn}:{f.f_lineno}"
            f = f.f_back
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


# ── LLM 单次调用作用域 ───────────────────────────────────────────────────

class LLMCallScope:
    """一次 LLMClient.call_llm* 调用的计时/token 采集器。"""

    def __init__(self, profile: "PipelineRunProfile", model: str, stream: bool,
                 system_prompt: str, user_prompt: str,
                 task_id: Optional[str], caller: str):
        self.profile = profile
        self.t0 = time.perf_counter()
        self.wall_start = time.time()
        self._first_token_at: Optional[float] = None
        self._content_chars = 0
        self._est_completion_tokens = 0
        self._usage_seen = False
        prompt_text = (system_prompt or "") + "\n" + (user_prompt or "")
        self._est_prompt_tokens = estimate_tokens(prompt_text)
        self.record: dict[str, Any] = {
            "model": model or "unknown",
            "stream": bool(stream),
            "task_id": task_id,
            "caller": caller,
            "caller_hint": _caller_hint(),
            "thread": threading.current_thread().name,
            "stage": current_stage_key(),
            "stage_label": current_stage_label(),
            "started_at": _iso_now(self.wall_start),
            "duration_seconds": None,
            "first_token_seconds": None,
            "system_chars": len(system_prompt or ""),
            "user_chars": len(user_prompt or ""),
            "prompt_chars": len(prompt_text),
            "response_chars": 0,
            "attempts": 0,
            "retry_wait_seconds": 0.0,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "tokens_estimated": False,
            "success": True,
            "error": None,
            "error_type": None,
        }
        self._token = _current_call_scope.set(self)

    # -- 采集点 --
    def note_attempt(self) -> None:
        self.record["attempts"] = int(self.record["attempts"]) + 1

    def note_first_token(self) -> None:
        if self._first_token_at is None:
            self._first_token_at = time.perf_counter()
            self.record["first_token_seconds"] = round(
                self._first_token_at - self.t0, 6)

    def note_content(self, text: Optional[str]) -> None:
        if not text:
            return
        self.note_first_token()
        self._content_chars += len(text)
        self._est_completion_tokens += estimate_tokens(text)

    def note_retry_wait(self, seconds: float) -> None:
        try:
            self.record["retry_wait_seconds"] = round(
                float(self.record["retry_wait_seconds"]) + max(0.0, float(seconds)), 6)
        except Exception:  # noqa: BLE001
            pass

    def note_usage(self, usage: Any) -> None:
        if usage is None:
            return
        try:
            def _get(key: str) -> Optional[int]:
                v = getattr(usage, key, None)
                if v is None and isinstance(usage, dict):
                    v = usage.get(key)
                return int(v) if v is not None else None

            prompt = _get("prompt_tokens")
            completion = _get("completion_tokens")
            total = _get("total_tokens")
            if prompt is None and completion is None and total is None:
                return
            if prompt is not None:
                self.record["prompt_tokens"] = prompt
            if completion is not None:
                self.record["completion_tokens"] = completion
            if total is not None:
                self.record["total_tokens"] = total
            elif prompt is not None or completion is not None:
                self.record["total_tokens"] = (prompt or 0) + (completion or 0)
            self._usage_seen = True
        except Exception:  # noqa: BLE001
            pass

    # -- 结束 --
    def finish(self, error: Optional[BaseException] = None) -> None:
        t1 = time.perf_counter()
        rec = self.record
        rec["duration_seconds"] = round(t1 - self.t0, 6)
        # 扣掉限流/重试退避后的有效调用耗时（区分“模型慢”与“在睡觉等重试”）
        rec["effective_seconds"] = round(max(
            0.0, rec["duration_seconds"] - float(rec.get("retry_wait_seconds") or 0)), 6)
        rec["response_chars"] = int(self._content_chars)
        if error is not None:
            rec["success"] = False
            rec["error_type"] = type(error).__name__
            rec["error"] = _truncate(error)
        if not self._usage_seen:
            # 模型未回 usage（流式/部分兼容端点）：按字符估算并明确标记
            rec["prompt_tokens"] = int(self._est_prompt_tokens)
            rec["completion_tokens"] = int(self._est_completion_tokens)
            rec["total_tokens"] = int(self._est_prompt_tokens + self._est_completion_tokens)
            rec["tokens_estimated"] = True
        else:
            if rec.get("prompt_tokens") is None:
                rec["prompt_tokens"] = int(self._est_prompt_tokens)
                rec["tokens_estimated"] = True
            if rec.get("completion_tokens") is None:
                rec["completion_tokens"] = int(self._est_completion_tokens)
                rec["tokens_estimated"] = True
            if rec.get("total_tokens") is None:
                rec["total_tokens"] = int((rec.get("prompt_tokens") or 0)
                                          + (rec.get("completion_tokens") or 0))
        if self.profile is not None:
            try:
                rec["start_offset"] = round(self.t0 - self.profile.started_perf, 6)
                rec["end_offset"] = round(t1 - self.profile.started_perf, 6)
                self.profile.record_llm_call(dict(rec))
            except Exception as e:  # noqa: BLE001
                _log_warning(f"[profiler] 记录 LLM 调用失败: {e}")
        # 恢复上一层作用域（续写/嵌套调用）
        try:
            _current_call_scope.reset(self._token)
        except Exception:  # noqa: BLE001
            _current_call_scope.set(None)


def begin_llm_call(model: str, stream: bool, system_prompt: str,
                   user_prompt: str, task_id: Optional[str] = None,
                   caller: str = "call_llm") -> Optional[LLMCallScope]:
    prof = _current_profile.get()
    if prof is None or prof.finished:
        return None
    try:
        return LLMCallScope(prof, model=model, stream=stream,
                            system_prompt=system_prompt, user_prompt=user_prompt,
                            task_id=task_id, caller=caller)
    except Exception as e:  # noqa: BLE001
        _log_warning(f"[profiler] 初始化 LLM 计时失败: {e}")
        return None


def finish_llm_call(scope: Optional[LLMCallScope],
                    error: Optional[BaseException] = None) -> None:
    if scope is None:
        return
    try:
        scope.finish(error)
    except Exception as e:  # noqa: BLE001
        _log_warning(f"[profiler] 结束 LLM 计时失败: {e}")


# 供 llm_client 在调用体内打点（自动找当前作用域，无作用域时零成本）
def note_llm_attempt() -> None:
    s = _current_call_scope.get()
    if s is not None:
        s.note_attempt()


def note_llm_first_token() -> None:
    s = _current_call_scope.get()
    if s is not None:
        s.note_first_token()


def note_llm_content(text: Optional[str]) -> None:
    s = _current_call_scope.get()
    if s is not None:
        s.note_content(text)


def note_llm_chunk(chunk: Any) -> None:
    """流式 chunk：内容 + 首 token 延迟 + 可能的 usage（include_usage 端点）。"""
    s = _current_call_scope.get()
    if s is None:
        return
    try:
        text = None
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            if delta is not None:
                text = getattr(delta, "content", None) or \
                    getattr(delta, "reasoning_content", None)
        if text:
            s.note_content(text)
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            s.note_usage(usage)
    except Exception:  # noqa: BLE001
        pass


def note_llm_response(response: Any) -> None:
    """非流式响应：正文长度 + usage。"""
    s = _current_call_scope.get()
    if s is None:
        return
    try:
        content = None
        choices = getattr(response, "choices", None) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg is not None:
                content = getattr(msg, "content", None)
        if content:
            s.note_content(content)
        s.note_usage(getattr(response, "usage", None))
    except Exception:  # noqa: BLE001
        pass


def note_llm_retry_wait(seconds: float) -> None:
    s = _current_call_scope.get()
    if s is not None:
        s.note_retry_wait(seconds)


# ── 管线运行级剖析 ───────────────────────────────────────────────────────

@dataclass
class _Segment:
    key: str
    label: str
    kind: str            # stage | aux | gap | bootstrap | finalize
    start_offset: float
    started_at: str
    tracked: bool = True
    status: str = "running"
    detail: str = ""
    duration_seconds: Optional[float] = None
    end_offset: Optional[float] = None
    ended_at: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "tracked": self.tracked,
            "status": self.status,
            "detail": self.detail,
            "start_offset": round(self.start_offset, 6),
            "started_at": self.started_at,
            "duration_seconds": (round(self.duration_seconds, 6)
                                 if self.duration_seconds is not None else None),
            "end_offset": (round(self.end_offset, 6)
                           if self.end_offset is not None else None),
            "ended_at": self.ended_at,
        }
        d.update(_jsonable(self.extra))
        return d


class PipelineRunProfile:
    """一次一键构建的完整剖析；finish() 时写 JSON 文件。"""

    def __init__(self, project_id: int, task_id: str,
                 trigger_user_id: Optional[int] = None,
                 options: Optional[dict] = None):
        self.project_id = project_id
        self.task_id = str(task_id)
        self.trigger_user_id = trigger_user_id
        self.options = dict(options or {})
        self.status = "running"
        self.error: Optional[str] = None
        self.finished = False

        self.started_perf = time.perf_counter()
        self.started_wall = time.time()
        self._last_closed = self.started_perf
        self._open: Optional[_Segment] = None
        self._open_perf: Optional[float] = None
        self._lock = threading.RLock()

        self.timeline: list[_Segment] = []
        self.stage_segments: list[_Segment] = []
        self.llm_calls: list[dict] = []
        self.metrics: dict[str, Any] = {}
        self.extra: dict[str, Any] = {}

        self._enabled, out_dir = _profile_settings()
        self.file_path = out_dir / (
            f"pipeline_profile_{_safe_component(project_id)}_"
            f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(self.started_wall))}_"
            f"{_safe_component(self.task_id, 64)}.json")

    # -- 指标 --
    def add_metric(self, key: str, value: Any) -> None:
        try:
            with self._lock:
                self.metrics[str(key)] = _jsonable(value)
        except Exception:  # noqa: BLE001
            pass

    def add_metrics(self, values: dict) -> None:
        for k, v in (values or {}).items():
            self.add_metric(k, v)

    def add_metric_accumulate(self, key: str, values: dict) -> None:
        """同一指标被多次调用时累加（如 extract_instances 主抽取 + 修复/展开多轮）。

        数值键相加、其余键保留最新值；避免后一次调用把前一次的 coref/抽取耗时覆盖掉。
        """
        try:
            with self._lock:
                cur = self.metrics.get(key)
                values = _jsonable(values or {})
                if not isinstance(cur, dict):
                    self.metrics[key] = values
                    return
                for k, v in values.items():
                    if (isinstance(v, (int, float)) and not isinstance(v, bool)
                            and isinstance(cur.get(k), (int, float))
                            and not isinstance(cur.get(k), bool)):
                        cur[k] = cur[k] + v
                    else:
                        cur[k] = v
        except Exception:  # noqa: BLE001
            pass

    def record_llm_call(self, record: dict) -> None:
        with self._lock:
            self.llm_calls.append(record)

    # -- 时间线 --
    def stage_begin(self, key: str, label: Optional[str] = None,
                    tracked: bool = True) -> None:
        with self._lock:
            if self.finished:
                return
            now = time.perf_counter()
            if self._open is not None:
                # 上一个阶段没有显式结束：按 unknown 收口，保证 timeline 连续
                self._close_open(now, status="unknown",
                                 detail="阶段未显式结束（自动收口）")
            self._add_gap(now)
            seg = _Segment(
                key=key,
                label=label or key,
                kind="stage" if tracked else "aux",
                start_offset=now - self.started_perf,
                started_at=_iso_now(),
                tracked=tracked,
            )
            self._open = seg
            self._open_perf = now
            _current_stage.set({"key": key, "label": seg.label})

    def stage_end(self, key: Optional[str] = None, status: str = "done",
                  detail: str = "", **extra: Any) -> None:
        with self._lock:
            if self._open is None:
                return
            if key is not None and self._open.key != key:
                # 阶段错位：仍收口当前段，避免时间线断裂
                key = self._open.key
            now = time.perf_counter()
            self._close_open(now, status=status, detail=detail, extra=extra)
            _current_stage.set(None)

    def _add_gap(self, now: float) -> None:
        if now <= self._last_closed + 1e-9:
            return
        has_stage = any(s.kind in ("stage", "aux") for s in self.timeline)
        seg = _Segment(
            key="__gap__" if has_stage else "__bootstrap__",
            label="阶段间开销" if has_stage else "启动/排队开销",
            kind="gap" if has_stage else "bootstrap",
            start_offset=self._last_closed - self.started_perf,
            started_at=_iso_now(time.time() - (now - self._last_closed)),
            tracked=False,
            status="done",
            duration_seconds=now - self._last_closed,
            end_offset=now - self.started_perf,
            ended_at=_iso_now(),
        )
        self.timeline.append(seg)
        self._last_closed = now

    def _close_open(self, now: float, status: str, detail: str,
                    extra: Optional[dict] = None) -> None:
        seg = self._open
        if seg is None:
            return
        seg.duration_seconds = now - (self._open_perf or now)
        seg.end_offset = now - self.started_perf
        seg.status = status
        seg.detail = detail or ""
        seg.ended_at = _iso_now()
        if extra:
            seg.extra.update(extra)
        self.timeline.append(seg)
        self.stage_segments.append(seg)
        self._open = None
        self._open_perf = None
        self._last_closed = now
        # 阶段边界落盘：长管线中途被杀也能看到已完成阶段
        self._write()

    def _segment_view(self, seg: _Segment, now: float,
                      in_progress: bool = False) -> dict:
        d = seg.to_dict()
        if in_progress:
            d["duration_seconds"] = round(now - (self._open_perf or now), 6)
            d["end_offset"] = round(now - self.started_perf, 6)
            d["status"] = "running"
            d["in_progress"] = True
        return d

    def write_snapshot(self) -> None:
        """半途快照：不结束当前阶段，把已发生的耗时（含正在跑的阶段）落盘。

        正常路径由阶段边界 + finish() 落盘；这个入口给心跳线程调用，用于
        进程被强杀/容器 OOM（finally 不会执行）时仍能看到约 60s 粒度的现场。
        """
        try:
            with self._lock:
                if self.finished:
                    return
                self._write()
        except Exception as e:  # noqa: BLE001 —— 快照失败不影响管线
            _log_warning(f"[profiler] 半途快照失败 project={self.project_id}: {e}")

    # -- 收尾 --
    def finish(self, status: Optional[str] = None,
               error: Optional[str] = None,
               extra: Optional[dict] = None) -> Optional[str]:
        with self._lock:
            if self.finished:
                return str(self.file_path) if self._enabled else None
            now = time.perf_counter()
            # 先落状态再收口：_close_open 会立即写一次盘，避免中间文件显示 running
            if status is not None:
                self.status = status
            if error:
                self.error = _truncate(error)
            if extra:
                self.extra.update(extra)
            if self._open is not None:
                final_stage_status = {
                    "completed": "done", "cancelled": "cancelled",
                    "failed": "failed",
                }.get(status or self.status, "unknown")
                if final_stage_status == "failed" and self.error:
                    _detail = self.error
                elif final_stage_status == "cancelled":
                    _detail = "已取消（阶段未显式收口）"
                else:
                    _detail = "管线结束时仍未显式收口"
                self._close_open(now, status=final_stage_status, detail=_detail)
                # _close_open 会立刻落盘，耗时归到收尾段而不是丢失
                now = time.perf_counter()
            # 收尾段：最后阶段结束 → 写终态之间的时间（Redis 终态/释放锁/写文件等）
            if now > self._last_closed + 1e-9:
                self.timeline.append(_Segment(
                    key="__finalize__", label="收尾落库/释放锁", kind="finalize",
                    start_offset=self._last_closed - self.started_perf,
                    started_at=_iso_now(time.time() - (now - self._last_closed)),
                    tracked=False, status="done",
                    duration_seconds=now - self._last_closed,
                    end_offset=now - self.started_perf, ended_at=_iso_now()))
                self._last_closed = now
            self.finished = True
            self._write()
            path = str(self.file_path) if self._enabled else None
        try:
            _current_profile.set(None)
            _current_stage.set(None)
        except Exception:  # noqa: BLE001
            pass
        return path

    # -- 输出 --
    def _stage_summary(self, now: Optional[float] = None) -> list[dict]:
        llm_by_stage = self._llm_by_stage()
        out: list[dict] = []
        now = now if now is not None else time.perf_counter()
        total = max(now - self.started_perf, 1e-9)
        # 快照时把正在跑的阶段也带上（duration=已运行时长，status=running）
        segments = list(self.stage_segments)
        if self._open is not None and not self.finished:
            segments.append(self._open)
        for seg in segments:
            d = self._segment_view(seg, now, in_progress=(seg is self._open))
            agg = llm_by_stage.get(seg.key, {})
            dur = float(d.get("duration_seconds") or 0)
            union = float(agg.get("wall_union_seconds", 0.0) or 0.0)
            calls_sum = float(agg.get("duration_seconds_sum", 0.0) or 0.0)
            tokens = int(agg.get("total_tokens", 0) or 0)
            d["share_of_total"] = round(dur / total, 6)
            d["llm_calls"] = agg.get("calls", 0)
            d["llm_calls_ok"] = agg.get("calls_ok", 0)
            d["llm_calls_failed"] = agg.get("calls_failed", 0)
            d["llm_seconds_sum"] = agg.get("duration_seconds_sum", 0.0)
            d["llm_effective_seconds_sum"] = agg.get("effective_seconds_sum", 0.0)
            d["llm_wall_union_seconds"] = agg.get("wall_union_seconds", 0.0)
            d["llm_retry_wait_seconds"] = agg.get("retry_wait_seconds", 0.0)
            d["prompt_tokens"] = agg.get("prompt_tokens", 0)
            d["completion_tokens"] = agg.get("completion_tokens", 0)
            d["total_tokens"] = agg.get("total_tokens", 0)
            # 便于直接回答“这一步里模型占了多少、非模型占了多少”
            # 个别调用可能跨过阶段边界，占比封顶 1.0，避免出现 115% 的误导值
            d["llm_share_of_stage"] = round(min(1.0, union / dur), 4) if dur > 0 else 0.0
            d["non_llm_seconds"] = round(max(0.0, dur - union), 6)
            d["llm_speedup"] = round(calls_sum / union, 2) if union > 0 else None
            d["tokens_per_llm_second"] = round(tokens / union, 1) if union > 0 else None
            out.append(d)

        # 主时间线之外的异步 LLM 调用（如抽取完成后的后台诊断）：单独成组，
        # 不参与阶段墙钟/占比，但 token 与调用次数必须可见。
        known = {seg.key for seg in segments}
        for key, agg in llm_by_stage.items():
            if key in known or key == "__no_stage__":
                continue
            calls = [c for c in self.llm_calls
                     if (c.get("stage") or "__no_stage__") == key]
            starts = [float(c.get("start_offset") or 0.0) for c in calls]
            ends = [float(c.get("end_offset") or 0.0) for c in calls]
            union = float(agg.get("wall_union_seconds", 0.0) or 0.0)
            tokens = int(agg.get("total_tokens", 0) or 0)
            out.append({
                "key": key,
                "label": ("异步诊断（后台）" if key == "async_diagnosis" else key),
                "kind": "async", "tracked": False, "status": "done",
                "detail": "并发于主阶段之外，不计入阶段墙钟",
                "start_offset": round(min(starts), 6) if starts else None,
                "started_at": None, "duration_seconds": None,
                "end_offset": round(max(ends), 6) if ends else None,
                "ended_at": None, "share_of_total": 0.0,
                "llm_calls": agg.get("calls", 0),
                "llm_calls_ok": agg.get("calls_ok", 0),
                "llm_calls_failed": agg.get("calls_failed", 0),
                "llm_seconds_sum": agg.get("duration_seconds_sum", 0.0),
                "llm_effective_seconds_sum": agg.get("effective_seconds_sum", 0.0),
                "llm_wall_union_seconds": agg.get("wall_union_seconds", 0.0),
                "llm_retry_wait_seconds": agg.get("retry_wait_seconds", 0.0),
                "prompt_tokens": agg.get("prompt_tokens", 0),
                "completion_tokens": agg.get("completion_tokens", 0),
                "total_tokens": agg.get("total_tokens", 0),
                "llm_share_of_stage": None,
                "non_llm_seconds": None,
                "llm_speedup": None,
                "tokens_per_llm_second": round(tokens / union, 1) if union > 0 else None,
            })
        return out

    def _llm_by_stage(self) -> dict[str, dict]:
        groups: dict[str, list[dict]] = {}
        for c in self.llm_calls:
            groups.setdefault(c.get("stage") or "__no_stage__", []).append(c)
        out: dict[str, dict] = {}
        for key, calls in groups.items():
            intervals = [(c.get("start_offset") or 0.0, c.get("end_offset") or 0.0)
                         for c in calls]
            out[key] = {
                "calls": len(calls),
                "calls_ok": sum(1 for c in calls if c.get("success")),
                "calls_failed": sum(1 for c in calls if not c.get("success")),
                "duration_seconds_sum": round(sum(
                    float(c.get("duration_seconds") or 0) for c in calls), 6),
                "effective_seconds_sum": round(sum(
                    float(c.get("effective_seconds") or 0) for c in calls), 6),
                "wall_union_seconds": round(_union_seconds(intervals), 6),
                "retry_wait_seconds": round(sum(
                    float(c.get("retry_wait_seconds") or 0) for c in calls), 6),
                "prompt_tokens": sum(int(c.get("prompt_tokens") or 0) for c in calls),
                "completion_tokens": sum(int(c.get("completion_tokens") or 0) for c in calls),
                "total_tokens": sum(int(c.get("total_tokens") or 0) for c in calls),
                "tokens_estimated_calls": sum(
                    1 for c in calls if c.get("tokens_estimated")),
            }
        return out

    def _llm_totals(self) -> dict:
        calls = self.llm_calls
        intervals = [(c.get("start_offset") or 0.0, c.get("end_offset") or 0.0)
                     for c in calls]
        by_model: dict[str, dict] = {}
        for c in calls:
            m = by_model.setdefault(c.get("model") or "unknown",
                                    {"calls": 0, "duration_seconds_sum": 0.0,
                                     "prompt_tokens": 0, "completion_tokens": 0,
                                     "total_tokens": 0, "failures": 0})
            m["calls"] += 1
            m["duration_seconds_sum"] += float(c.get("duration_seconds") or 0)
            m["prompt_tokens"] += int(c.get("prompt_tokens") or 0)
            m["completion_tokens"] += int(c.get("completion_tokens") or 0)
            m["total_tokens"] += int(c.get("total_tokens") or 0)
            m["failures"] += 0 if c.get("success") else 1
        for m in by_model.values():
            m["duration_seconds_sum"] = round(m["duration_seconds_sum"], 6)
        duration_sum = sum(float(c.get("duration_seconds") or 0) for c in calls)
        union = _union_seconds(intervals)
        return {
            "calls": len(calls),
            "calls_ok": sum(1 for c in calls if c.get("success")),
            "calls_failed": sum(1 for c in calls if not c.get("success")),
            "attempts": sum(int(c.get("attempts") or 0) for c in calls),
            "duration_seconds_sum": round(duration_sum, 6),
            "effective_seconds_sum": round(sum(
                float(c.get("effective_seconds") or 0) for c in calls), 6),
            "wall_union_seconds": round(union, 6),
            "llm_speedup": round(duration_sum / union, 2) if union > 0 else None,
            "retry_wait_seconds": round(sum(
                float(c.get("retry_wait_seconds") or 0) for c in calls), 6),
            "prompt_tokens": sum(int(c.get("prompt_tokens") or 0) for c in calls),
            "completion_tokens": sum(int(c.get("completion_tokens") or 0) for c in calls),
            "total_tokens": sum(int(c.get("total_tokens") or 0) for c in calls),
            "tokens_estimated_calls": sum(
                1 for c in calls if c.get("tokens_estimated")),
            "by_model": by_model,
            "by_stage": self._llm_by_stage(),
        }

    def _build_payload(self) -> dict:
        now = time.perf_counter()
        total = now - self.started_perf
        timeline = [s.to_dict() for s in self.timeline]
        # 半途快照/异常收尾时当前阶段可能尚未显式 end：按“已运行时长”补进时间线，
        # 保证 timeline 仍然连续且 sum == total
        if self._open is not None and not self.finished:
            timeline.append(self._segment_view(self._open, now, in_progress=True))
        timeline_sum = sum(float(s.get("duration_seconds") or 0) for s in timeline)
        stage_segments = self._stage_summary(now)
        tracked_segs = [s for s in stage_segments if s.get("tracked")]
        tracked_sum = sum(float(s.get("duration_seconds") or 0) for s in tracked_segs)
        aux_sum = sum(float(s.get("duration_seconds") or 0)
                      for s in stage_segments if not s.get("tracked"))
        # 只统计“业务阶段内”的 LLM 占用：总 LLM 并集还包含 cq_eval 等辅助阶段
        # 和异步诊断；直接拿总并集除以业务阶段时长会出现 >100% 的假值。
        tracked_llm_union = 0.0
        non_llm_stage = 0.0
        for s in tracked_segs:
            dur = float(s.get("duration_seconds") or 0)
            used = min(dur, float(s.get("llm_wall_union_seconds") or 0))
            tracked_llm_union += used
            non_llm_stage += max(0.0, dur - used)
        gap_sum = sum(float(s.get("duration_seconds") or 0)
                      for s in timeline if s.get("kind") in ("gap", "bootstrap", "finalize"))
        llm = self._llm_totals()
        queue_wait = self.metrics.get("queue_wait_seconds")
        return {
            "schema_version": 1,
            "run": {
                "project_id": self.project_id,
                "task_id": self.task_id,
                "trigger_user_id": self.trigger_user_id,
                "options": _jsonable(self.options),
                "status": self.status,
                "error": self.error,
                "started_at": _iso_now(self.started_wall),
                "finished_at": _iso_now() if self.finished else None,
                "duration_seconds": round(total, 6),
                # 派发→worker 开工的排队时间（Celery/inline 调度开销）
                "queue_wait_seconds": queue_wait,
                "enqueue_to_finish_seconds": (
                    round(total + float(queue_wait), 6)
                    if queue_wait is not None else None),
                "pid": os.getpid(),
                "host": socket.gethostname(),
            },
            "totals": {
                "duration_seconds": round(total, 6),
                "timeline_seconds_sum": round(timeline_sum, 6),
                "timeline_covers_total": abs(timeline_sum - total) < 0.001,
                "unattributed_seconds": round(max(0.0, total - timeline_sum), 6),
                "stage_seconds_sum": round(tracked_sum, 6),
                "aux_seconds_sum": round(aux_sum, 6),
                "gap_seconds_sum": round(gap_sum, 6),
                "stage_share_of_total": round(tracked_sum / total, 6) if total else 0,
                # 业务阶段内的 LLM 占用（不含辅助阶段/异步诊断）
                "llm_wall_union_seconds_tracked": round(tracked_llm_union, 6),
                "llm_share_of_stage": round(
                    tracked_llm_union / tracked_sum, 4) if tracked_sum else 0,
                "non_llm_stage_seconds": round(non_llm_stage, 6),
                # 全管线口径：所有 LLM 调用（含辅助阶段）占用了多少墙钟
                "llm_share_of_total": round(min(
                    1.0, float(llm.get("wall_union_seconds") or 0) / total
                ), 4) if total else 0,
                "llm": llm,
            },
            "stages": stage_segments,
            "timeline": timeline,
            "llm_calls": self.llm_calls,
            "metrics": _jsonable(self.metrics),
            "extra": _jsonable(self.extra),
            "files": {"profile_json": str(self.file_path) if self._enabled else None},
        }

    def _write(self) -> None:
        if not self._enabled:
            return
        try:
            payload = self._build_payload()
            path = Path(self.file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001
            _log_warning(f"[profiler] 写剖析文件失败 project={self.project_id}: {e}")


# ── 对外 API ─────────────────────────────────────────────────────────────

def _profile_settings() -> tuple[bool, Path]:
    enabled = True
    raw: Any = "logs/pipeline_profiles"
    try:
        from app.core.config import settings
        enabled = bool(getattr(settings, "PIPELINE_PROFILE_ENABLED", True))
        raw = getattr(settings, "PIPELINE_PROFILE_DIR", raw) or raw
    except Exception:  # noqa: BLE001 —— 配置不可用时仍允许剖析
        pass
    out = Path(str(raw))
    if not out.is_absolute():
        out = _BACKEND_ROOT / out
    return enabled, out


def begin_pipeline_profile(project_id: int, task_id: str,
                           trigger_user_id: Optional[int] = None,
                           options: Optional[dict] = None) -> PipelineRunProfile:
    prof = PipelineRunProfile(project_id=project_id, task_id=task_id,
                              trigger_user_id=trigger_user_id,
                              options=options)
    _current_profile.set(prof)
    _current_stage.set(None)
    with _ACTIVE_LOCK:
        _ACTIVE_RUNS[prof.task_id] = prof
    prof.add_metrics({
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "thread": threading.current_thread().name,
    })
    return prof


def finish_pipeline_profile(prof: Optional[PipelineRunProfile],
                            status: str = "completed",
                            error: Optional[str] = None,
                            extra: Optional[dict] = None) -> Optional[str]:
    if prof is None:
        return None
    try:
        return prof.finish(status=status, error=error, extra=extra)
    finally:
        # 即便写文件失败也要清上下文，否则同一 worker 的后续任务会把 LLM 调用
        # 归到这次已经结束的 run 上
        with _ACTIVE_LOCK:
            _ACTIVE_RUNS.pop(prof.task_id, None)
        try:
            if _current_profile.get() is prof:
                _current_profile.set(None)
            _current_stage.set(None)
        except Exception:  # noqa: BLE001
            pass


def profile_stage_begin(key: str, label: Optional[str] = None,
                        tracked: bool = True) -> None:
    prof = _current_profile.get()
    if prof is not None and not prof.finished:
        prof.stage_begin(key, label=label, tracked=tracked)


def profile_stage_end(key: Optional[str] = None, status: str = "done",
                      detail: str = "", **extra: Any) -> None:
    prof = _current_profile.get()
    if prof is not None and not prof.finished:
        prof.stage_end(key=key, status=status, detail=detail, **extra)


def profile_metric(key: str, value: Any) -> None:
    prof = _current_profile.get()
    if prof is not None and not prof.finished:
        prof.add_metric(key, value)


def profile_metrics(values: dict) -> None:
    prof = _current_profile.get()
    if prof is not None and not prof.finished:
        prof.add_metrics(values)


def profile_metric_accumulate(key: str, values: dict) -> None:
    prof = _current_profile.get()
    if prof is not None and not prof.finished:
        prof.add_metric_accumulate(key, values)


def active_profile(task_id: str) -> Optional[PipelineRunProfile]:
    with _ACTIVE_LOCK:
        return _ACTIVE_RUNS.get(str(task_id))
