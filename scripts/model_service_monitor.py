#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模型服务 24h 连续拨测脚本（配置写死版，仅标准库）。

对下方 MODELS 中每个模型的 OpenAI 兼容 /chat/completions 发起流式请求，
线程池并行拨测（每模型独立线程，互不拖累），统计：
  成功率、首字延迟 TTFT（含 reasoning，p50/p90/p99/avg/max）、正文首字延迟、
  总耗时、输出 token 数、错误分布。

⚠️ 本文件含明文 api_key（已加入 .gitignore），切勿提交/外发。
   改模型配置只用改下方 MODELS 列表。

用法：
  python3 scripts/model_service_monitor.py                 # 默认 24h，前台跑，结束后打印并落盘报告
  python3 scripts/model_service_monitor.py --duration 1m   # 冒烟

产物（默认 ./logs/ 下，按启动时间命名）：
  *.jsonl         每次请求一行明细（ts/model/ok/ttft/ttft_content/total/out_tokens/error/err_detail）
  *_window.csv    分段记录：自动切成每 10 分钟（--window 可调）一段，每段带精确起止时间，
                  逐段打印并落盘成功率/TTFT 各分位/错误（24h = 144 段 × 5 模型）
  *_hourly.csv    每小时滚动汇总（宏观趋势）
  *_report.txt    跑完（或 Ctrl-C）自动生成的最终统计报告
  控制台          每段结束打印 [HH:MM:SS – HH:MM:SS] 时段记录行，结束时打印最终报告并落盘
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────── 模型配置（唯一要改的地方） ───────────────────────────
MODELS = [
    {"model_name": "glm-5",
     "base_url": "http://28.221.28.7:9082/v1",
     "api_key": "konghuajie_specific_glm_5_key"},
    {"model_name": "deepseek-v4pro",
     "base_url": "http://28.221.28.7:9082/v1",
     "api_key": "34966877-0a42-4a98-92e6-19a6f385ac68"},
    {"model_name": "Qwen36-35B-A3B",
     "base_url": "http://28.221.28.7:9082/v1",
     "api_key": "onto_key"},
    {"model_name": "deepseek-v4flash",
     "base_url": "http://28.221.28.7:9082/v1",
     "api_key": "citic_claw_deepseek_v4_flash_key"},
    {"model_name": "Qwen3.8-27B",
     "base_url": "http://28.221.28.7:9082/v1",
     "api_key": "d4a3b2c1-5e6f-7a8b-9c0d-1e2f3a4b5c6d"},
]

PROBE_PROMPT = "用一句话解释什么是本体（Ontology）。"
PROBE_MAX_TOKENS = 256  # 思考型模型先吐 reasoning，太小会被思考耗光导致正文空流


def clean_base_url(url: str) -> str:
    """去尾斜杠与多余 /chat/completions。"""
    url = (url or "").strip().rstrip("/")
    for suffix in ("/chat/completions", "/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


# ─────────────────────────── 拨测请求（urllib 流式） ───────────────────────────

def classify_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return "connect_error"
    return type(exc).__name__


def _stream_chunks(resp) -> list[dict]:
    """读取 SSE 流，解析出 data: 行的 JSON chunk 列表。"""
    chunks: list[dict] = []
    buf = ""
    while True:
        raw = resp.read(4096)
        if not raw:
            break
        buf += raw.decode("utf-8", "replace")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return chunks
            try:
                chunks.append(json.loads(data))
            except json.JSONDecodeError:
                pass
    return chunks


def probe_once(endpoint: str, api_key: str, model_name: str, timeout: float,
               want_usage: bool) -> dict:
    """发一次流式请求（同步 urllib，线程内调用）。

    返回 {ok, ttft, ttft_content, total, out_tokens, error, err_detail}。
    ttft=首个 token 延迟（含 reasoning）；ttft_content=首个正文 token 延迟
    （思考型模型两者可能差很远，正文也可能不出现）；ok=流里只要有任意 token
    即为服务正常；只有完全无输出的流才记为 empty_stream。
    """
    payload: dict = {
        "model": model_name,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": PROBE_MAX_TOKENS,
        "temperature": 0,
        "stream": True,
    }
    if want_usage:  # 部分网关不支持 stream_options，失败后自动降级
        payload["stream_options"] = {"include_usage": True}
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                body = resp.read(300).decode("utf-8", "replace")
                return {"ok": False, "ttft": None, "ttft_content": None,
                        "total": time.perf_counter() - t0, "out_tokens": None,
                        "error": f"http_{resp.status}", "err_detail": body}
            ttft = None
            ttft_content = None
            usage_tokens = None
            for chunk in _stream_chunks(resp):
                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                token_text = (delta.get("content") or delta.get("reasoning_content")
                              or delta.get("reasoning") or delta.get("thinking"))
                if token_text:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    if ttft_content is None and delta.get("content"):
                        ttft_content = time.perf_counter() - t0
                if chunk.get("usage") and isinstance(chunk["usage"], dict):
                    usage_tokens = chunk["usage"].get("completion_tokens")
            total = time.perf_counter() - t0
            if ttft is None:  # 流结束什么都没吐：服务异常（200 空流）
                return {"ok": False, "ttft": None, "ttft_content": None, "total": total,
                        "out_tokens": None, "error": "empty_stream", "err_detail": ""}
            return {"ok": True, "ttft": ttft, "ttft_content": ttft_content, "total": total,
                    "out_tokens": usage_tokens, "error": None, "err_detail": ""}
    except Exception as exc:
        return {"ok": False, "ttft": None, "ttft_content": None,
                "total": time.perf_counter() - t0, "out_tokens": None,
                "error": classify_error(exc), "err_detail": str(exc)[:300]}


# ─────────────────────────── 统计 ───────────────────────────

def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(p / 100 * (len(s) - 1))))
    return s[idx]


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r["ok"]]
    ttfts = [r["ttft"] for r in ok if r["ttft"] is not None]
    t_contents = [r["ttft_content"] for r in ok if r.get("ttft_content") is not None]
    totals = [r["total"] for r in ok]
    return {
        "n": len(records),
        "ok": len(ok),
        "fail": len(records) - len(ok),
        "success_rate": len(ok) / len(records) if records else float("nan"),
        "ttft_avg": statistics.fmean(ttfts) if ttfts else None,
        "ttft_p50": pct(ttfts, 50) if ttfts else None,
        "ttft_p90": pct(ttfts, 90) if ttfts else None,
        "ttft_p99": pct(ttfts, 99) if ttfts else None,
        "ttft_max": max(ttfts) if ttfts else None,
        "content_avg": statistics.fmean(t_contents) if t_contents else None,
        "no_content": len(ok) - len(t_contents),
        "total_avg": statistics.fmean(totals) if totals else None,
        "errors": dict(Counter(r["error"] for r in records if not r["ok"])),
    }


def render_summary(targets: list[dict], recs_by_model: dict[str, list[dict]]) -> str:
    header = (f"{'模型':<18}{'请求':>6}{'成功':>6}{'成功率':>8}{'TTFT均':>9}{'p50':>8}"
              f"{'p90':>8}{'p99':>8}{'总耗时均':>9}  错误分布")
    lines = [header, "-" * len(header) + "-" * 30]
    for t in targets:
        name = t["model_name"]
        s = summarize(recs_by_model.get(name, []))
        ttft = lambda v: f"{v * 1000:.0f}ms" if v is not None else "-"
        errs = " ".join(f"{k}:{v}" for k, v in sorted(s["errors"].items(), key=lambda x: -x[1])[:4]) or "-"
        lines.append(
            f"{name:<18}{s['n']:>6}{s['ok']:>6}{s['success_rate'] * 100:>7.1f}%"
            f"{ttft(s['ttft_avg']):>9}{ttft(s['ttft_p50']):>8}{ttft(s['ttft_p90']):>8}"
            f"{ttft(s['ttft_p99']):>8}{(s['total_avg'] or 0) * 1000:>8.0f}ms  {errs}")
    return "\n".join(lines)


# ─────────────────────────── 主循环（线程池并行） ───────────────────────────

def run(args) -> None:
    targets = [dict(t) for t in MODELS]
    if args.models:
        wanted = {n.lower() for n in args.models.split(",")}
        targets = [t for t in targets if t["model_name"].lower() in wanted]
        if not targets:
            sys.exit(f"错误：MODELS 里没有 --models 指定的 {args.models}")
    for t in targets:
        t["endpoint"] = clean_base_url(t["base_url"]) + "/chat/completions"

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"model_probe_{stamp}.jsonl"
    csv_path = out_dir / f"model_probe_{stamp}_hourly.csv"
    win_csv_path = out_dir / f"model_probe_{stamp}_window.csv"
    jsonl_f = jsonl_path.open("a", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8") as f:
        f.write("window_start,window_end,model,n,ok,success_rate,ttft_avg_s\n")
    with win_csv_path.open("w", encoding="utf-8") as f:
        f.write("window_start,window_end,model,n,ok,success_rate,"
                "ttft_avg_s,ttft_p50_s,ttft_p90_s,ttft_p99_s,total_avg_s,errors\n")

    print(f"明细：{jsonl_path}\n小时汇总：{csv_path}\n时段记录：{win_csv_path}")
    for t in targets:
        print(f"目标：{t['model_name']:<18} -> {t['endpoint']}  (key {t['api_key'][:8]}...)")

    recs_by_model: dict[str, list[dict]] = defaultdict(list)
    usage_supported: dict[str, bool] = defaultdict(lambda: True)  # 按 endpoint 探测降级
    stop_evt = threading.Event()
    write_lock = threading.Lock()
    deadline = datetime.now().timestamp() + args.duration
    started = datetime.now().timestamp()

    def _sigint(_sig, _frm):
        print("\n收到中断，等当前拨测收尾后出报告……")
        stop_evt.set()

    signal.signal(signal.SIGINT, _sigint)
    signal.signal(signal.SIGTERM, _sigint)

    print(f"\n开始拨测：{len(targets)} 个模型 × 每 {args.interval}s 一轮 × {args.duration / 3600:.1f}h，"
          f"各模型并行\n")

    def worker(t: dict) -> None:
        label = t["model_name"]
        seq = 0
        while not stop_evt.is_set() and datetime.now().timestamp() < deadline:
            seq += 1
            r = probe_once(t["endpoint"], t["api_key"], t["model_name"],
                           args.timeout, usage_supported[t["endpoint"]])
            if r["error"] == "http_400" and "stream_options" in (r.get("err_detail") or ""):
                usage_supported[t["endpoint"]] = False  # 网关不支持 usage，降级重试
                r = probe_once(t["endpoint"], t["api_key"], t["model_name"], args.timeout, False)
            rec = {"ts": datetime.now().isoformat(timespec="seconds"), "model": label,
                   "endpoint": t["endpoint"], "seq": seq, **r}
            with write_lock:
                recs_by_model[label].append(rec)
                jsonl_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                jsonl_f.flush()
            if r["ok"] and seq % 20 == 0:
                c = f" content={r['ttft_content'] * 1000:.0f}ms" if r["ttft_content"] else " (无正文)"
                print(f"  [{label}] 第{seq}次 OK ttft={r['ttft'] * 1000:.0f}ms{c} total={r['total'] * 1000:.0f}ms")
            # 距下一轮剩余时间按 1s 切片睡，保证中断/到时能立刻退出
            next_at = datetime.now().timestamp() + args.interval
            while datetime.now().timestamp() < next_at and not stop_evt.is_set() and datetime.now().timestamp() < deadline:
                time.sleep(min(1.0, next_at - datetime.now().timestamp()))

    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = [pool.submit(worker, t) for t in targets]

        # 主线程：逐段 flush（每 window 秒一段，整点对齐带起止时间）+ 累计概览 + 小时 CSV
        last_hour_key = int(time.time() // 3600) - 1
        next_bucket = int(datetime.now().timestamp() // args.window)  # 锚定到启动时刻所在的整点段，之后逐段追
        next_report = datetime.now().timestamp() + args.report_every
        while not stop_evt.is_set() and datetime.now().timestamp() < deadline:
            if all(f.done() for f in futures):
                break
            time.sleep(1)
            now = datetime.now().timestamp()
            done_bucket = int(now // args.window)  # 已完整结束的段号（epoch 整点对齐）
            # 时钟异常跳变时限制单轮追平量，避免失控（空段静默，多跑几轮自愈）
            while next_bucket < done_bucket and done_bucket - next_bucket <= 1000:
                flush_bucket(win_csv_path, targets, recs_by_model, args.window, next_bucket)
                next_bucket += 1
            if datetime.now().timestamp() >= next_report:
                print(f"\n== 进度 {(now - started) / 60:.0f}min / {args.duration / 3600:.1f}h ==")
                with write_lock:
                    snap = {k: list(v) for k, v in recs_by_model.items()}
                print("── 累计概览 ──")
                print(render_summary(targets, snap))
                hour_key = int(now // 3600)
                if hour_key > last_hour_key:  # 钟表整点跨过：把上一小时落盘
                    _append_hourly_csv(csv_path, targets, recs_by_model, hour_key - 1)
                    last_hour_key = hour_key
                next_report = datetime.now().timestamp() + args.report_every

        stop_evt.set()  # 到时/中断：让 worker 全部退出
        for f in futures:
            f.result()

    # 收尾：flush 最后不完整的时段（起止用实际时间），补小时 CSV，出最终报告
    now = datetime.now().timestamp()
    last_partial = int(now // args.window)
    while next_bucket < last_partial and last_partial - next_bucket <= 1000:
        flush_bucket(win_csv_path, targets, recs_by_model, args.window, next_bucket)
        next_bucket += 1
    if next_bucket <= last_partial:
        flush_bucket(win_csv_path, targets, recs_by_model, args.window,
                     last_partial, end_ts=now)
    hour_key = int(now // 3600)
    if hour_key > last_hour_key:
        _append_hourly_csv(csv_path, targets, recs_by_model, hour_key - 1)
    jsonl_f.close()
    with write_lock:
        snap = {k: list(v) for k, v in recs_by_model.items()}
    report = (f"{'=' * 70}\n最终报告（{datetime.now().isoformat(timespec='seconds')}，"
              f"历时 {(time.time() - started) / 3600:.2f}h）\n{'=' * 70}\n"
              f"{render_summary(targets, snap)}\n"
              f"\n明细 JSONL：{jsonl_path}\n时分段记录：{win_csv_path}\n小时汇总 CSV：{csv_path}\n")
    report_path = jsonl_path.with_name(jsonl_path.stem + "_report.txt")
    report_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已落盘：{report_path}")


def _append_hourly_csv(csv_path: Path, targets: list[dict],
                       recs_by_model: dict[str, list[dict]], hour_idx: int) -> None:
    """把第 hour_idx 个钟表小时（如 20:00–21:00）内每个模型的记录汇总成一行 CSV。"""
    lo, hi = hour_idx * 3600, (hour_idx + 1) * 3600  # epoch 对齐，段带精确起止
    rows = []
    for t in targets:
        bucket = [r for r in recs_by_model[t["model_name"]]
                  if lo <= datetime.fromisoformat(r["ts"]).timestamp() < hi]
        s = summarize(bucket)
        ttfts = [r["ttft"] for r in bucket if r["ok"] and r["ttft"] is not None]
        ttft_avg = f"{statistics.fmean(ttfts):.3f}" if ttfts else ""
        rows.append(f"{datetime.fromtimestamp(lo).isoformat(timespec='seconds')},"
                    f"{datetime.fromtimestamp(hi).isoformat(timespec='seconds')},"
                    f"{t['model_name']},{s['n']},{s['ok']},"
                    f"{s['success_rate']:.4f},{ttft_avg}")
    with csv_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def flush_bucket(csv_path: Path, targets: list[dict], recs_by_model: dict[str, list[dict]],
                 window_sec: float, bucket_idx: int,
                 end_ts: float | None = None) -> None:
    """把第 bucket_idx 段（钟表整点对齐：[b*w, (b+1)*w)）的统计打印成行并写入 window.csv。

    end_ts 为 None 表示完整段（用理论结束时刻）；否则是收尾的不完整段（用实际时刻）。
    控制台每段打印一行：
      [20:00:00 – 20:10:00] 6 个请求中 6 成功（100.0%）TTFT均 3.9s p50 3.4s p90 6.0s p99 8.2s
    同时写 *_window.csv，列：window_start,window_end,model,n,ok,success_rate,ttft_avg_s,
    ttft_p50_s,ttft_p90_s,ttft_p99_s,total_avg_s,errors
    """
    w_lo = bucket_idx * window_sec
    w_hi = (bucket_idx + 1) * window_sec if end_ts is None else end_ts
    fmt = lambda t: datetime.fromtimestamp(t).strftime("%H:%M:%S")
    buckets = {t["model_name"]: [r for r in recs_by_model.get(t["model_name"], [])
                                 if w_lo <= datetime.fromisoformat(r["ts"]).timestamp() < w_hi]
               for t in targets}
    if not any(buckets.values()):
        return  # 该段没有任何请求：静默跳过，不打印不落盘（防时钟异常时空转刷屏）
    print(f"\n[{fmt(w_lo)} – {fmt(w_hi)}] 时段记录（{window_sec / 60:.0f} 分钟）")
    rows = []
    for t in targets:
        name = t["model_name"]
        bucket = buckets[name]
        s = summarize(bucket)
        ttft = lambda v: f"{v * 1000:.0f}ms" if v is not None else "-"
        errs = " ".join(f"{k}:{v}" for k, v in sorted(s["errors"].items(), key=lambda x: -x[1])[:4]) or "-"
        print(f"  {name:<18} {s['n']:>4} 请求 {s['ok']:>4} 成功 {s['success_rate'] * 100:>7.1f}%"
              f"  TTFT均 {ttft(s['ttft_avg']):>7} p50 {ttft(s['ttft_p50']):>7}"
              f" p90 {ttft(s['ttft_p90']):>7} p99 {ttft(s['ttft_p99']):>7}"
              f" 总耗时均 {(s['total_avg'] or 0) * 1000:>6.0f}ms  {errs}")
        f_ttft = lambda v: f"{v:.3f}" if v is not None else ""
        rows.append(f"{datetime.fromtimestamp(w_lo).isoformat(timespec='seconds')},"
                    f"{datetime.fromtimestamp(w_hi).isoformat(timespec='seconds')},"
                    f"{name},{s['n']},{s['ok']},{s['success_rate']:.4f},"
                    f"{f_ttft(s['ttft_avg'])},{f_ttft(s['ttft_p50'])},"
                    f"{f_ttft(s['ttft_p90'])},{f_ttft(s['ttft_p99'])},"
                    f"{f_ttft(s['total_avg'])},\"{errs}\"")
    with csv_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")


def parse_duration(s: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(f"无法解析时长：{s}")
    v, unit = float(m.group(1)), m.group(2) or "s"
    return v * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def main() -> None:
    ap = argparse.ArgumentParser(description="平台模型服务拨测（配置写死版，仅标准库）")
    ap.add_argument("--duration", type=parse_duration, default=24 * 3600, help="总时长，如 24h/12h/30m（默认 24h）")
    ap.add_argument("--interval", type=float, default=30, help="每个模型两轮拨测间隔秒（默认 30）")
    ap.add_argument("--timeout", type=float, default=90, help="单次请求超时秒（默认 90，deepseek-v4pro 首字 20s+ 需留余量）")
    ap.add_argument("--models", default=None, help="只测部分模型，逗号分隔（默认全测）")
    ap.add_argument("--out-dir", default=str(ROOT / "logs"), help="结果输出目录（默认 ./logs）")
    ap.add_argument("--window", type=float, default=600, help="时段长度秒（默认 600=10 分钟一段，每段带起止时间）")
    ap.add_argument("--report-every", type=float, default=600, help="累计概览打印间隔秒（默认 600）")
    args = ap.parse_args()
    run(args)


if __name__ == "__main__":
    main()
