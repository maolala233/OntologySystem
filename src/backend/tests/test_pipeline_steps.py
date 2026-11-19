"""阶段内子步骤（build_sub_steps）边界单测。

2026-11-16 实测：骨架抽取进入跨切片缝合（82%，消息「跨切片关系缝合 0/3」）时，
缝合步骤仍显示「待开始」——边界判断用了 <=，把 pct==lo 当成了未开始。
"""
from __future__ import annotations


def _by_key(steps):
    return {s["key"]: s for s in steps}


def _sub(pct, status="running", steps=None):
    sub = {"percent": pct, "status": status, "stats": {}}
    if steps is not None:
        sub["stats"]["steps"] = steps
    return sub


def test_schema_stitch_starts_at_lower_bound(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("schema", _sub(82)))

    assert steps["structure"]["state"] == "done"
    assert steps["stitch"]["state"] == "running"
    assert steps["stitch"]["percent"] == 0
    assert steps["write"]["state"] == "pending"


def test_schema_stitch_progress(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("schema", _sub(85)))

    assert steps["stitch"]["state"] == "running"
    assert steps["stitch"]["percent"] == 30


def test_schema_assemble_starts_at_lower_bound(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("schema", _sub(55)))

    assert steps["extract"]["state"] == "done"
    assert steps["assemble"]["state"] == "running"
    assert steps["assemble"]["percent"] == 0


def test_schema_completed_marks_all_done(pipeline_module):
    steps = pipeline_module.build_sub_steps("schema", _sub(100, "completed"))

    assert all(s["state"] == "done" and s["percent"] == 100 for s in steps)


def test_schema_failed_marks_current_step(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("schema", _sub(82, "failed")))

    assert steps["structure"]["state"] == "done"
    assert steps["stitch"]["state"] == "failed"
    assert steps["write"]["state"] == "pending"


def test_explicit_steps_pass_through(pipeline_module):
    explicit = [{"key": "scan", "label": "公理扫描", "state": "done", "percent": 100},
                {"key": "business_rules", "label": "业务规则挖掘",
                 "state": "running", "percent": 0}]
    assert pipeline_module.build_sub_steps("axioms", _sub(0, steps=explicit)) == explicit


def test_axioms_without_explicit_steps_synthesizes_two_steps(pipeline_module):
    # 子任务只写 percent（公理扫描 35%）时，前端应看到「公理扫描进行中」，
    # 而不是退化成静态清单里两步全 pending（2026-11-16 实测）
    steps = pipeline_module.build_sub_steps("axioms", _sub(35))

    assert len(steps) == 2
    assert steps[0]["key"] == "scan"
    assert steps[0]["state"] == "running"
    assert steps[0]["percent"] == 35
    assert steps[1]["key"] == "business_rules"
    assert steps[1]["state"] == "pending"
    assert steps[1]["percent"] == 0


def test_axioms_scan_completed_keeps_business_rules_pending(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("axioms", _sub(100, "completed")))

    assert steps["scan"]["state"] == "done"
    assert steps["business_rules"]["state"] == "pending"


def test_axioms_scan_failed_marks_scan_failed(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("axioms", _sub(35, "failed")))

    assert steps["scan"]["state"] == "failed"
    assert steps["business_rules"]["state"] == "pending"


def test_adjudicate_without_explicit_steps_keeps_cq_pending(pipeline_module):
    steps = pipeline_module.build_sub_steps("adjudicate", _sub(40))

    assert len(steps) == 2
    assert steps[0]["key"] == "adjudicate"
    assert steps[0]["state"] == "running"
    assert steps[0]["percent"] == 40
    assert steps[1]["key"] == "cq_eval"
    assert steps[1]["state"] == "pending"


def test_resolution_has_three_steps(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("resolution", _sub(20)))

    assert steps["load"]["state"] == "done"
    assert steps["detect"]["state"] == "running"
    assert steps["detect"]["percent"] == 0
    assert steps["merge"]["state"] == "pending"


def test_conflicts_has_two_steps(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("conflicts", _sub(55)))

    assert steps["scan"]["state"] == "done"
    assert steps["materialize"]["state"] == "running"
    assert steps["materialize"]["percent"] == 0


def test_selfcheck_has_three_steps(pipeline_module):
    steps = _by_key(pipeline_module.build_sub_steps("selfcheck", _sub(30)))

    assert steps["inspect"]["state"] == "done"
    assert steps["repair"]["state"] == "running"
    assert steps["verify"]["state"] == "pending"


def test_silent_overall_maps_segment_weight(pipeline_module):
    # 公理扫描占阶段 35%，段内 50% → 阶段整体 17%；业务规则段内 50% → 67%
    assert pipeline_module._silent_overall("axioms", "scan", 50) == 17
    assert pipeline_module._silent_overall("axioms", "business_rules", 50) == 67
    # 审查占 40%，段内 50% → 20%；CQ 段内 50% → 70%
    assert pipeline_module._silent_overall("adjudicate", "adjudicate", 50) == 20
    assert pipeline_module._silent_overall("adjudicate", "cq_eval", 50) == 70


def test_display_percent_maps_axiom_scan_phase(pipeline_module):
    # 公理扫描子任务只写段内 percent、不带显式 steps → 展示值折算成阶段整体
    assert pipeline_module.display_sub_percent("axioms", _sub(50)) == 17
    # 业务规则段由管线写显式 steps + 已折算的整体 percent → 原样返回
    explicit = [{"key": "scan", "label": "公理扫描", "state": "done", "percent": 100},
                {"key": "business_rules", "label": "业务规则挖掘",
                 "state": "running", "percent": 30}]
    assert pipeline_module.display_sub_percent("axioms", _sub(55, steps=explicit)) == 55
    # 非静默段不受影响
    assert pipeline_module.display_sub_percent("schema", _sub(50)) == 50
    assert pipeline_module.display_sub_percent("axioms", None) is None


def test_axioms_scan_step_keeps_segment_percent(pipeline_module):
    # 子步骤自身仍显示段内百分比（50%），阶段主进度另行折算（17%）
    steps = _by_key(pipeline_module.build_sub_steps("axioms", _sub(50)))

    assert steps["scan"]["percent"] == 50
    assert steps["business_rules"]["state"] == "pending"


def test_unknown_stage_falls_back_to_single_step(pipeline_module):
    steps = pipeline_module.build_sub_steps("finalize_stats", _sub(20))

    assert len(steps) == 1
    assert steps[0]["key"] == "finalize_stats"
    assert steps[0]["state"] == "running"


def test_failed_step_label_uses_current_substep(pipeline_module):
    # schema 30% 失败 → 正在跑「批次抽取」，报错文案应准确到子步骤
    assert pipeline_module._failed_step_label("schema", _sub(30, "failed")) == "批次抽取"
    # instances 82% 失败 → 「指代消解与结构展开」
    assert (pipeline_module._failed_step_label("instances", _sub(82, "failed"))
            == "指代消解与结构展开")
    # 没有子进度可判断 → 空串，调用方回退阶段中文名
    assert pipeline_module._failed_step_label("schema", None) == ""
    assert pipeline_module._failed_step_label("schema", {}) == ""


def test_stage_failed_message_prefers_substep(pipeline_module):
    err = pipeline_module._StageFailed("schema", "无法连接模型服务", "批次抽取")
    assert str(err) == "批次抽取失败：无法连接模型服务"
    fallback = pipeline_module._StageFailed("schema", "无法连接模型服务")
    assert str(fallback) == "骨架抽取失败：无法连接模型服务"


def test_summarize_stage_detail_drops_brackets(pipeline_module):
    # 2026-11-19 反馈：详情区一行塞进「386 类 / 1002 关系（含跨切片缝合…）」太长
    msg = ("骨架抽取完成：386 类 / 1002 关系（含跨切片缝合 12 条/3 段）"
           "（层次组装 5 条，清理伪类 3 个）；机检 2 硬伤 / 4 提醒")
    assert pipeline_module.summarize_stage_detail(msg) == "386 类 / 1002 关系"


def test_summarize_stage_detail_handles_ascii_bracket(pipeline_module):
    assert (pipeline_module.summarize_stage_detail(
        "实例抽取完成：120 实体 / 80 关系（含反向补全 8 条）")
        == "120 实体 / 80 关系")


def test_summarize_stage_detail_keeps_short_text(pipeline_module):
    assert (pipeline_module.summarize_stage_detail("消解完成：自动合并 12 处")
            == "自动合并 12 处")
    assert (pipeline_module.summarize_stage_detail("冲突检测完成：发现 15 处结果冲突，已转审核确认")
            == "发现 15 处结果冲突，已转审核确认")


def test_summarize_stage_detail_limits_length(pipeline_module):
    out = pipeline_module.summarize_stage_detail("结构自检完成：" + "很长的说明" * 20)
    assert len(out) == 41 and out.endswith("…")
    # 括号在开头时不能整段切空
    assert pipeline_module.summarize_stage_detail("（备注）通过") == "（备注）通过"


def test_cancel_timeout_forces_finish(monkeypatch, pipeline_module, fake_redis):
    import time

    tid, pid = "pipeline-cancel", 9
    fake_redis.store[f"pipeline:cancelling:{tid}"] = str(int(time.time()) - 999)
    calls = {}
    monkeypatch.setattr(pipeline_module, "finish_task_progress",
                        lambda *a, **k: calls.update(finish=a))
    monkeypatch.setattr(pipeline_module, "release_pipeline_lock",
                        lambda p: calls.update(release=p))
    prog = {"status": "cancelling", "stats": {"project_id": pid}}

    assert pipeline_module.force_finish_cancel_timeout(pid, tid, prog) is True
    assert calls.get("release") == pid
    assert calls.get("finish")


def test_cancel_timeout_keeps_waiting_before_deadline(monkeypatch, pipeline_module, fake_redis):
    import time

    tid, pid = "pipeline-cancel2", 10
    fake_redis.store[f"pipeline:cancelling:{tid}"] = str(int(time.time()))
    monkeypatch.setattr(pipeline_module, "finish_task_progress",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应收尾")))
    prog = {"status": "cancelling", "stats": {"project_id": pid}}

    assert pipeline_module.force_finish_cancel_timeout(pid, tid, prog) is False


def test_build_stage_subs_replays_all_stages(pipeline_module, progress_state):
    tid = "pipeline-test"
    progress_state[f"{tid}:parse"] = {
        "percent": 100, "status": "completed", "message": "解析完成",
        "stats": {"steps": [
            {"key": "inspect", "label": "检查已解析文档", "state": "done", "percent": 100},
            {"key": "resync", "label": "补写向量", "state": "skipped", "percent": 0},
            {"key": "parse", "label": "逐份解析", "state": "done", "percent": 100},
        ]},
    }
    progress_state[f"{tid}:schema"] = {"percent": 55, "status": "running",
                                       "message": "骨架装配中", "stats": {}}
    stages = [{"key": "parse", "status": "done"},
              {"key": "schema", "status": "running"},
              {"key": "instances", "status": "pending"}]

    subs = pipeline_module.build_stage_subs(tid, stages)

    assert set(subs) == {"parse", "schema"}
    assert subs["parse"]["percent"] == 100
    assert len(subs["parse"]["steps"]) == 3
    # 无显式 steps 的阶段按区间生成（55% → 骨架装配刚开始）
    assert subs["schema"]["steps"][2]["key"] == "assemble"
    assert subs["schema"]["steps"][2]["state"] == "running"
    assert "instances" not in subs
