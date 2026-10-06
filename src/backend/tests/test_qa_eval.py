# tests/test_qa_eval.py - M5 问答金样本评测（docs/design/07 §5，08 §2 M5 验收）
# 标记 integration：需真实 Milvus/LLM/MySQL（默认跳过，`-m integration` 运行）。
# 验收门槛：引用命中率 ≥ 80%。命中 = sources 非空 且 答案含合法引用标号 [n]（n ≤ len(sources)）。
import re

import pytest
import yaml

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def eval_spec():
    with open("tests/fixtures/qa_eval.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cited(answer: str, n_sources: int) -> bool:
    marks = [int(m) for m in re.findall(r"\[(\d+)\]", answer or "")]
    return bool(marks) and all(1 <= m <= n_sources for m in marks)


def test_qa_eval_citation_hit_rate(eval_spec):
    from app.adapters.provider import build_legacy_llm_config
    from app.adapters.retrieval import RetrievalQuery, query_with_reasoning
    from app.infrastructure.database import SessionLocal
    from app.infrastructure.llm_client import LLMClient

    db = SessionLocal()
    try:
        cfg = build_legacy_llm_config(db, eval_spec["project_id"])
        llm = LLMClient(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"), model=cfg.get("model"))
        hits, details = 0, []
        for s in eval_spec["samples"]:
            query = RetrievalQuery(project_id=eval_spec["project_id"], question=s["question"], top_k=12)
            try:
                result = query_with_reasoning(query, db=db, llm=llm)
            except Exception as e:  # 单样本失败计入未命中，不中断评测
                details.append((s["id"], s["type"], False, f"异常：{e}"))
                continue
            hit = bool(result.sources) and _cited(result.answer, len(result.sources))
            hits += hit
            details.append((s["id"], s["type"], hit,
                            f"sources={len(result.sources)} 引用={'有' if _cited(result.answer, len(result.sources)) else '无'}"))
        rate = hits / len(eval_spec["samples"])
        print("\n[qa_eval] 引用命中率明细：")
        for sid, typ, hit, note in details:
            print(f"  {sid} [{typ}] {'✓' if hit else '✗'} {note}")
        print(f"[qa_eval] 引用命中率 = {hits}/{len(eval_spec['samples'])} = {rate:.2%}（门槛 {eval_spec['threshold']:.0%}）")
        assert rate >= eval_spec["threshold"], (
            f"引用命中率 {rate:.2%} 低于门槛 {eval_spec['threshold']:.0%}："
            + "；".join(f"{sid}" for sid, _, hit, _ in details if not hit))
    finally:
        db.close()
