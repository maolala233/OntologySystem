# tests/test_golden_regression.py - 金样本回归集（docs/design/04 §12，M3-7 入 CI）
# fixtures/golden/：3 个中文文档（合同/机构/产品说明书）+ 期望实体/关系/合并组。
# 脚本化 LLM（按 evidence 归属切片）模拟"完美抽取"，回归对象是管道本身：
#   严格闸门（class/predicate/evidence/confidence）、同 chunk 去重、evidence 原句定位、
#   三层消解阈值（L1 归一必并 / L2 拼音 auto≥0.85）。
# 指标（04 §12）：实体 P/R/F1、关系 F1、evidence 命中率、消解 F1。全离线，CI 可跑。
import json
import os

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "golden")

with open(os.path.join(FIXTURES, "golden.json"), encoding="utf-8") as f:
    GOLDEN = json.load(f)


def _norm_ws(s: str) -> str:
    return "".join((s or "").split())


def _load_chunks(doc_name: str) -> list[dict]:
    """段落级切块（与解析管道同粒度假设：evidence 不跨段）。"""

    with open(os.path.join(FIXTURES, doc_name), encoding="utf-8") as f:
        raw = f.read()
    blocks = [b for b in raw.split("\n\n") if b.strip()]
    return [{"index": i, "text": b} for i, b in enumerate(blocks)]


def _scripted_llm():
    """按 evidence 归属切片的脚本化 LLM：从 prompt 解析【文档切片 #n】正文，
    返回 evidence 落在该切片内的金样本项（模拟完美抽取）。"""
    plans = []  # (evidence, kind, item)
    for doc_name, spec in GOLDEN["documents"].items():
        chunks = _load_chunks(doc_name)
        for item in spec["entities"]:
            assert any(_norm_ws(item["evidence"]) in _norm_ws(c["text"]) for c in chunks), \
                f"{doc_name} 实体 {item['label']} evidence 无法定位到任何切片"
            plans.append((item["evidence"], "entities", item))
        for item in spec["relations"]:
            assert any(_norm_ws(item["evidence"]) in _norm_ws(c["text"]) for c in chunks), \
                f"{doc_name} 关系 {item['subject']}→{item['object']} evidence 无法定位"
            plans.append((item["evidence"], "relations", item))

    def call(system, user, schema):  # 与 LLMClient 形状一致
        ents, rels = [], []
        # 只取当前切片正文（prompt 末段【文档切片 #n】），前块上下文/已知实体不算
        seg = user.rsplit("【文档切片 #", 1)[-1]
        chunk_text = seg.split("】\n", 1)[-1]
        norm_chunk = _norm_ws(chunk_text)
        for evidence, kind, item in plans:
            if _norm_ws(evidence) in norm_chunk:
                if kind == "entities":
                    ents.append({"label": item["label"], "class_label": item["class_label"],
                                 "props": item.get("props", {}), "confidence": 0.95,
                                 "evidence": evidence})
                else:
                    rels.append({"subject_label": item["subject"], "predicate": item["predicate"],
                                 "object_label": item["object"], "confidence": 0.95,
                                 "evidence": evidence})
        return {"entities": ents, "relations": rels, "relationships": []}

    return call


def _f1(pred: set, expect: set) -> float:
    tp = len(pred & expect)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(expect) if expect else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


# ── 管道级金样本回归 ──


def test_golden_extraction_pipeline():
    from app.adapters.extraction import extract_instances

    tbox = {"classes": GOLDEN["tbox"]["classes"],
            "object_properties": GOLDEN["tbox"]["object_properties"]}
    chunks = []
    idx = 0
    for doc_name in GOLDEN["documents"]:  # 全局连续编号：三文档拼接后 chunk_index 不得碰撞
        for c in _load_chunks(doc_name):
            chunks.append({"index": idx, "text": c["text"]})
            idx += 1

    res = extract_instances(chunks, tbox, base_uri="golden", use_cache=False,
                            llm_call=_scripted_llm())
    th = GOLDEN["thresholds"]

    # 实体 P/R/F1（严格闸门 + 同 chunk 去重后仍应完整保留金样本）
    pred_e = {(e.label, e.class_label) for e in res.entities}
    exp_e = {(it["label"], it["class_label"])
             for spec in GOLDEN["documents"].values() for it in spec["entities"]}
    ef1 = _f1(pred_e, exp_e)
    assert ef1 >= th["entity_f1"], \
        f"实体 F1={ef1:.3f} < {th['entity_f1']}；缺失={exp_e - pred_e} 多余={pred_e - exp_e}"

    # 关系 F1
    pred_r = {(r.subject_label, r.predicate, r.object_label) for r in res.relations}
    exp_r = {(it["subject"], it["predicate"], it["object"])
             for spec in GOLDEN["documents"].values() for it in spec["relations"]}
    rf1 = _f1(pred_r, exp_r)
    assert rf1 >= th["relation_f1"], \
        f"关系 F1={rf1:.3f} < {th['relation_f1']}；缺失={exp_r - pred_r} 多余={pred_r - exp_r}"

    # evidence 命中率（04 §12：≥90%；降级摘要 "[chunk n 摘要]" 记为未命中）
    real = [e for e in res.entities if e.evidence and not e.evidence.startswith("[chunk")]
    hit_rate = len(real) / len(res.entities) if res.entities else 0.0
    assert hit_rate >= th["evidence_hit_rate"], \
        f"evidence 命中率={hit_rate:.3f} < {th['evidence_hit_rate']}"

    # 闸门没有误伤（完美输入下 discarded/withheld 应为 0）
    assert res.discarded_count == 0 and res.withheld_count == 0, \
        f"闸门误伤：discarded={res.discarded_count} withheld={res.withheld_count}"
    # LLM 自报置信度透传（缺陷 #3 不伪造）
    assert all(0.7 <= e.confidence <= 1.0 for e in res.entities)


def test_golden_resolution_f1():
    from app.adapters.resolution import detect_duplicates

    ents = []
    uid = 0
    for doc_name, spec in GOLDEN["documents"].items():
        for it in spec["entities"]:
            ents.append({"id": uid, "label": it["label"], "class_label": it["class_label"],
                         "props": it.get("props", {})})
            uid += 1

    clusters = detect_duplicates(ents, blocking="pinyin")  # 无 embeddings → L2 拼音兜底
    # 预测对：auto（sim≥0.85 且非 needs_review）簇内两两（member_ids 含 canonical）
    pred_pairs = set()
    for c in clusters:
        ids = [str(i) for i in sorted(int(x) for x in (c.member_ids or []) if x is not None)]
        if len(ids) > 1 and not c.needs_review and float(c.similarity or 0) >= 0.85:
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pred_pairs.add((ids[i], ids[j]))

    # 期望对：merge_groups 内成员的两两组合
    expect_pairs = set()
    for g in GOLDEN["merge_groups"]:
        labels = set(g["labels"])
        members = [e for e in ents if e["label"] in labels]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                expect_pairs.add((str(members[i]["id"]), str(members[j]["id"])))

    f1 = _f1(pred_pairs, expect_pairs)
    th = GOLDEN["thresholds"]["resolution_f1"]
    assert f1 >= th, f"消解 F1={f1:.3f} < {th}；漏并={expect_pairs - pred_pairs} 误并={pred_pairs - expect_pairs}"
