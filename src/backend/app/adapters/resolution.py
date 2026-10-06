# app/adapters/resolution.py - 实体消解三层流水线（docs/design/04 §5，M3-5）
# 系统性替代 extractor.py 的前缀去重（修复缺陷 #1）与 names_are_similar
# snake_case 比较（修复缺陷 #10：中文消解失效）。
# 第 1 层 确定性归一（同 label_normalized+class 必合并）
# 第 2 层 拼音 blocking（pypinyin 全拼/首字母 + jaro_winkler/leva 候选对）
# 第 3 层 向量相似（bge-m3，≥0.85 自动合并 / 0.60-0.85 转人工 / <0.60 放行）
# detect_duplicates 纯函数可测；merge_entities/split_entities 走行表（可逆，溯源保留）。

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# 消解阈值（README §4.3 全局常量）
AUTO_MERGE_THRESHOLD = 0.85   # 相似度 >= 0.85 自动合并
MANUAL_REVIEW_THRESHOLD = 0.60  # 0.60–0.85 生成 review_items(entity_merge)

# 拼音层候选判定（04 §5：jaro_winkler≥0.9 或 leva≤2）
PINYIN_JW_THRESHOLD = 0.9
PINYIN_LEVA_MAX = 2


class DuplicateCluster(BaseModel):
    canonical_label: str
    class_label: str
    member_labels: list[str] = Field(default_factory=list)
    member_ids: list[int] = Field(default_factory=list)   # 行表 id（内存探测时可为空）
    canonical_id: Optional[int] = None
    similarity: float = 0.0
    layer: str = "normalized"   # normalized | pinyin | vector
    needs_review: bool = False   # 落在 0.60–0.85 区间


class MergeResult(BaseModel):
    canonical_id: int
    merged_ids: list[int] = Field(default_factory=list)
    relations_migrated: int = 0
    provenance_preserved: bool = True


def normalize_label(label: str) -> str:
    """第 1 层·确定性归一（04 §5）：去空白 → 全角转半角 → NFKC → 小写（仅 ASCII）。

    ★ isascii 守卫：中文经 NFKC 后保持原样，禁止 isalnum() 类判定（修复缺陷 #10）。
    纯函数，M0 可测试。
    """
    import unicodedata

    out = []
    for ch in label.strip():
        if ch == "　":  # 全角空格
            continue
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII 区转半角
            ch = chr(code - 0xFEE0)
        out.append(ch)
    text = unicodedata.normalize("NFKC", "".join(out))
    return "".join(c.lower() if c.isascii() else c for c in text if not c.isspace())


def pinyin_keys(label: str) -> list[str]:
    """第 2 层·拼音 blocking 键（中文专属）：返回 [全拼, 首字母串] 两种键。

    非中文字符（ASCII）原样拼入（" Fund2024" → "fund2024"）。
    """
    from pypinyin import Style, lazy_pinyin

    text = (label or "").strip()
    if not text:
        return []
    full = lazy_pinyin(text, style=Style.NORMAL, errors=lambda x: [x.lower()])
    initial = lazy_pinyin(text, style=Style.FIRST_LETTER, errors=lambda x: [x.lower()])
    return ["".join(full), "".join(initial)]


def jaro_winkler(s1: str, s2: str) -> float:
    """Jaro-Winkler 相似度（本地实现，避免额外依赖；短中文串常用）。"""
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    match_dist = max(len1, len2) // 2 - 1
    match_dist = max(match_dist, 0)
    m1 = [False] * len1
    m2 = [False] * len2
    matches = 0
    for i, c in enumerate(s1):
        lo = max(0, i - match_dist)
        hi = min(len2, i + match_dist + 1)
        for j in range(lo, hi):
            if not m2[j] and s2[j] == c:
                m1[i] = m2[j] = True
                matches += 1
                break
    if matches == 0:
        return 0.0
    # 逆序对
    t = 0
    k = 0
    for i in range(len1):
        if m1[i]:
            while not m2[k]:
                k += 1
            if s1[i] != s2[k]:
                t += 1
            k += 1
    t //= 2
    jaro = (matches / len1 + matches / len2 + (matches - t) / matches) / 3
    # Winkler 前缀加权
    prefix = 0
    for a, b in zip(s1, s2):
        if a != b or prefix == 4:
            break
        prefix += 1
    return jaro + prefix * 0.1 * (1 - jaro)


def levenshtein(s1: str, s2: str, cap: int = PINYIN_LEVA_MAX) -> int:
    """编辑距离（早停 cap）。"""
    if abs(len(s1) - len(s2)) > cap:
        return cap + 1
    prev = list(range(len(s2) + 1))
    for i, a in enumerate(s1, 1):
        cur = [i]
        row_min = i
        for j, b in enumerate(s2, 1):
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b))
            cur.append(v)
            row_min = min(row_min, v)
        if row_min > cap:
            return cap + 1
        prev = cur
    return prev[-1] if prev[-1] <= cap else cap + 1


def _as_entity(e: Any) -> dict:
    """Entity 行 / dict 统一为内部表示。"""
    if isinstance(e, dict):
        return {"id": e.get("id"), "label": str(e.get("label") or ""),
                "class_label": str(e.get("class_label") or ""),
                "props": e.get("props") or {}}
    return {"id": getattr(e, "id", None), "label": str(getattr(e, "label", "") or ""),
            "class_label": str(getattr(e, "class_label", "") or ""),
            "props": getattr(e, "props", None) or {}}


def _embed_text(e: dict) -> str:
    return f"{e['label']}｜{e['class_label']}｜{' '.join(f'{k}:{v}' for k, v in list(e['props'].items())[:8])}"


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1e-9
    db = math.sqrt(sum(y * y for y in b)) or 1e-9
    return num / (da * db)


def _pinyin_similar(n1: str, n2: str) -> Optional[float]:
    """拼音层判定：jaro_winkler≥0.9 或 leva≤2 → 返回相似度（取 JW）；否则 None。"""
    k1, k2 = pinyin_keys(n1), pinyin_keys(n2)
    for a, b in zip(k1, k2):
        jw = jaro_winkler(a, b)
        if jw >= PINYIN_JW_THRESHOLD or levenshtein(a, b) <= PINYIN_LEVA_MAX:
            return jw
    return None


def detect_duplicates(entities: list, blocking: str = "pinyin",
                      embeddings_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
                      auto_threshold: float = AUTO_MERGE_THRESHOLD,
                      review_threshold: float = MANUAL_REVIEW_THRESHOLD,
                      max_vector_pairs: int = 200) -> list[DuplicateCluster]:
    """三层流水线（04 §5）。entities 为 Entity 行或 dict 列表；同 class 内比较。

    - L1 同 label_normalized+class → 必合并（similarity 1.0）
    - L2 pinyin blocking 候选对（jw≥0.9 或 leva≤2）
    - L3 embeddings_fn 注入时向量余弦判定；未注入时沿用拼音 JW 兜底
      ≥auto 自动合并；review≤x<auto 转人工；其余放行。
    增量模式（只与新批次+canonical 池比对）由调用方裁剪 entities 列表实现。
    """
    ents = [_as_entity(e) for e in (entities or [])]
    ents = [e for e in ents if e["label"].strip()]
    # 同 class 分组（跨类不消解）
    by_class: dict[str, list[dict]] = {}
    for e in ents:
        by_class.setdefault(e["class_label"], []).append(e)

    # 并查集（同 class 内）
    index_of = {id(e): i for i, e in enumerate(ents)}
    parent = list(range(len(ents)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # union 事件表：簇的相似度/层在输出时按成员聚合（路径压缩会改根，不能按根记）
    events: list[tuple[int, int, float, str]] = []  # (i, j, sim, layer)

    def union(a: int, b: int, sim: float, layer: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
        events.append((a, b, sim, layer))

    # L1：同 (norm, class) 直接合并
    norm_groups: dict[tuple, int] = {}
    for i, e in enumerate(ents):
        key = (normalize_label(e["label"]), normalize_label(e["class_label"]))
        if key in norm_groups:
            union(norm_groups[key], i, 1.0, "normalized")
        else:
            norm_groups[key] = i

    # L2/L3：组内候选对
    vector_pairs: list[tuple[int, int]] = []  # (i, j) 待向量判定
    for _cls, members in by_class.items():
        if len(members) < 2:
            continue
        if blocking != "pinyin":
            continue
        if embeddings_fn is not None:
            # L3 可用时类内全对交向量终判（max_vector_pairs 封顶）：
            # 拼音 blocking 只是无向量时的粗筛，会漏"音远义近"的候选
            for x in range(len(members)):
                for y in range(x + 1, len(members)):
                    i, j = index_of[id(members[x])], index_of[id(members[y])]
                    if find(i) != find(j):
                        vector_pairs.append((min(i, j), max(i, j)))
            continue
        # blocking 桶：全拼键 + 首字母键 + 音节键（防"星辰1号/星辰一号"这类接续差异漏配）
        buckets: dict[str, list[int]] = {}
        from pypinyin import lazy_pinyin

        for e in members:
            i = index_of[id(e)]
            keys = list(pinyin_keys(e["label"]) or [normalize_label(e["label"])[:1]])
            keys += [s for s in lazy_pinyin(e["label"],
                                            errors=lambda x: [x.lower()]) if s]
            for k in set(keys):
                buckets.setdefault(k, []).append(i)
        seen_pairs: set[tuple[int, int]] = set()
        for bucket in buckets.values():
            for x in range(len(bucket)):
                for y in range(x + 1, len(bucket)):
                    i, j = bucket[x], bucket[y]
                    if find(i) == find(j):
                        continue
                    pair = (min(i, j), max(i, j))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    sim = _pinyin_similar(ents[i]["label"], ents[j]["label"])
                    if sim is None:
                        continue
                    if embeddings_fn is not None:
                        vector_pairs.append(pair)  # 交给 L3 终判
                    elif sim >= review_threshold:
                        union(i, j, sim, "pinyin")  # <review_threshold 的直接放行（不成簇）

    # L3：向量余弦终判（候选对去重后批量嵌入）
    if embeddings_fn is not None and vector_pairs:
        uniq_pairs = sorted(set(vector_pairs))[:max_vector_pairs]
        texts = {i: _embed_text(ents[i]) for pair in uniq_pairs for i in pair}
        vecs = embeddings_fn(list(texts.values()))
        vec_by_text = dict(zip(texts.keys(), vecs))  # 键=实体下标 i
        for i, j in uniq_pairs:
            sim = _cosine(vec_by_text[i], vec_by_text[j])
            if sim >= review_threshold:
                union(i, j, sim, "vector")  # <0.60 放行

    # 输出簇：根 → 成员；相似度/层取该簇 union 事件的最大值
    groups: dict[int, list[int]] = {}
    for i in range(len(ents)):
        groups.setdefault(find(i), []).append(i)
    clusters: list[DuplicateCluster] = []
    for root, members in groups.items():
        if len(members) < 2:
            continue
        member_set = set(members)
        best_sim, best_layer = 0.0, "normalized"
        for i, j, sim, layer in events:
            if i in member_set and j in member_set and sim > best_sim:
                best_sim, best_layer = sim, layer
        if best_sim <= 0.0:  # 簇必有 union 事件；防御缺省回 L1
            best_sim, best_layer = 1.0, "normalized"
        cluster = DuplicateCluster(
            canonical_label=ents[root]["label"],
            class_label=ents[root]["class_label"],
            member_labels=[ents[i]["label"] for i in members],
            member_ids=[ents[i]["id"] for i in members if ents[i]["id"] is not None],
            canonical_id=ents[root]["id"],
            similarity=round(best_sim, 4),
            layer=best_layer,
            needs_review=(best_layer != "normalized" and best_sim < auto_threshold),
        )
        clusters.append(cluster)
    clusters.sort(key=lambda c: (-c.similarity, c.canonical_label))
    return clusters


def merge_entities(canonical_id: int, duplicate_ids: list[int],
                   property_strategy: str = "keep_most_complete",
                   db: Optional[Session] = None) -> MergeResult:
    """合并执行（04 §5）：canonical 保留 + 属性合并 + 关系迁移去重 + 溯源保留 + 可逆登记。

    行表操作（S1 期以行表为消解事实源；blob 投影 M4 收敛）。
    被合并实体 status=merged、canonical_id 指向 canonical；
    指向被合并实体的 relation 全部改指 canonical（同 (s,p,o) 去重）；
    provenance_records.target_id 改指 canonical（保留各来源记录）；
    合并历史写 review_items(entity_merge, status=approved, result_ref) 可逆。
    """
    if db is None:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
        own = True
    else:
        own = False
    try:
        from app.infrastructure.database import Entity, ProvenanceRecord, Relation, ReviewItem

        canonical = db.query(Entity).filter(Entity.id == canonical_id).first()
        if canonical is None:
            raise ValueError(f"canonical 实体不存在: {canonical_id}")
        dupes = db.query(Entity).filter(
            Entity.id.in_([i for i in duplicate_ids if i != canonical_id])).all()
        if not dupes:
            return MergeResult(canonical_id=canonical_id)

        # 属性合并（keep_most_complete：canonical 为底，缺失键由成员补齐）
        merged_props = dict(canonical.props or {})
        for d in dupes:
            for k, v in (d.props or {}).items():
                if k not in merged_props or merged_props[k] in (None, ""):
                    merged_props[k] = v
        if property_strategy == "keep_first":
            merged_props = dict(dupes[-1].props or {}) or dict(canonical.props or {})
        canonical.props = merged_props
        # 别名收集：被合并实体的原 label 进入 canonical 别名（消解可逆的关键）
        aliases = list(canonical.aliases or [])
        for d in dupes:
            if d.label != canonical.label and d.label not in aliases:
                aliases.append(d.label)
        canonical.aliases = aliases[:64]

        merged_ids = []
        relations_migrated = 0
        seen_edges: set[tuple[int, str, int]] = {
            (r.subject_id, r.predicate, r.object_id)
            for r in db.query(Relation).filter(
                (Relation.subject_id == canonical_id) | (Relation.object_id == canonical_id)).all()}
        for d in dupes:
            d_id = d.id
            # 关系迁移：改指 canonical + 去重
            for r in db.query(Relation).filter(Relation.subject_id == d_id).all():
                key = (canonical_id, r.predicate, r.object_id)
                if key in seen_edges:
                    db.delete(r)
                else:
                    r.subject_id = canonical_id
                    seen_edges.add(key)
                    relations_migrated += 1
            for r in db.query(Relation).filter(Relation.object_id == d_id).all():
                key = (r.subject_id, r.predicate, canonical_id)
                if key in seen_edges:
                    db.delete(r)
                else:
                    r.object_id = canonical_id
                    seen_edges.add(key)
                    relations_migrated += 1
            # 溯源保留（改指 canonical，各来源记录保留）
            db.query(ProvenanceRecord).filter(
                ProvenanceRecord.target_type == "entity",
                ProvenanceRecord.target_id == d_id,
            ).update({"target_id": canonical_id}, synchronize_session=False)
            d.status = "merged"
            d.canonical_id = canonical_id
            merged_ids.append(d_id)

        # 可逆登记（审核队列留痕，状态直接 approved——合并不需要人再裁决）
        db.add(ReviewItem(
            project_id=canonical.project_id,
            item_type="entity_merge",
            payload={"canonical_id": canonical_id, "merged_ids": merged_ids,
                     "canonical_label": canonical.label,
                     "strategy": property_strategy},
            reason="自动/手动合并留痕（可逆拆分依据）",
            status="approved",
            suggested_action={"action": "split"},
            result_ref={"canonical_id": canonical_id, "merged_ids": merged_ids},
        ))
        db.commit()
        return MergeResult(canonical_id=canonical_id, merged_ids=merged_ids,
                           relations_migrated=relations_migrated,
                           provenance_preserved=True)
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()


def split_entities(canonical_id: int, split_ids: list[int],
                   db: Optional[Session] = None) -> bool:
    """拆分误合并（04 §5 可逆）：成员恢复 auto、canonical_id 清空。

    关系不自动迁回（拆分后由下次消解/画布修订处理），合并留痕 review_item 保留原历史。
    """
    if db is None:
        from app.infrastructure.database import SessionLocal

        db = SessionLocal()
        own = True
    else:
        own = False
    try:
        from app.infrastructure.database import Entity

        rows = db.query(Entity).filter(
            Entity.id.in_([i for i in split_ids if i != canonical_id]),
            Entity.canonical_id == canonical_id).all()
        for r in rows:
            r.status = "auto"
            r.canonical_id = None
        db.commit()
        return len(rows) > 0
    except Exception:
        db.rollback()
        raise
    finally:
        if own:
            db.close()


__all__ = [
    "AUTO_MERGE_THRESHOLD", "MANUAL_REVIEW_THRESHOLD",
    "DuplicateCluster", "MergeResult", "normalize_label", "pinyin_keys",
    "jaro_winkler", "levenshtein", "detect_duplicates", "merge_entities", "split_entities",
]
