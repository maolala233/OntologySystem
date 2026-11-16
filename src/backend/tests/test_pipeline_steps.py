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


def test_unknown_stage_falls_back_to_single_step(pipeline_module):
    steps = pipeline_module.build_sub_steps("conflicts", _sub(20))

    assert len(steps) == 1
    assert steps[0]["key"] == "conflicts"
    assert steps[0]["state"] == "running"
