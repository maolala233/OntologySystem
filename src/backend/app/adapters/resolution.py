# app/adapters/resolution.py - 实体消解三层流水线（docs/design/04 §5）
# 里程碑：M3 实现。系统性替代 extractor.py 的前缀去重（修复缺陷 #1）与
# names_are_similar snake_case 比较（修复缺陷 #10：中文消解失效）。

from __future__ import annotations

from pydantic import BaseModel, Field

# 消解阈值（README §4.3 全局常量）
AUTO_MERGE_THRESHOLD = 0.85   # 相似度 >= 0.85 自动合并
MANUAL_REVIEW_THRESHOLD = 0.60  # 0.60–0.85 生成 review_items(entity_merge)


class DuplicateCluster(BaseModel):
    canonical_label: str
    class_label: str
    member_labels: list[str] = Field(default_factory=list)
    similarity: float = 0.0
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

    M0 骨架：pypinyin 在 requirements 中（>=0.50），M3 实现调用与缓存。
    """
    raise NotImplementedError("M3 实现")


def detect_duplicates(entities: list, blocking: str = "pinyin") -> list[DuplicateCluster]:
    """TODO(M3): 三层流水线 —— 归一必合并 → 拼音 blocking + 编辑距离 → 向量相似
    （bge-m3，AUTO_MERGE_THRESHOLD 自动合并 / MANUAL_REVIEW_THRESHOLD 转人工）。
    增量模式只与新批次 + canonical 池比对（semantica incremental_detect）。"""
    raise NotImplementedError("M3 实现（docs/design/04 §5）")


def merge_entities(canonical_id: int, duplicate_ids: list[int],
                   property_strategy: str = "keep_most_complete") -> MergeResult:
    """TODO(M3): 合并执行 —— canonical 指向 + 关系迁移去重 + 溯源保留
    （EntityMerger preserve_provenance=True 语义），产物写 review_items.result_ref 可逆。"""
    raise NotImplementedError("M3 实现")


def split_entities(canonical_id: int, split_ids: list[int]) -> bool:
    """TODO(M3): 误合并拆分（合并历史回放，保留 audit_logs）。"""
    raise NotImplementedError("M3 实现")


__all__ = [
    "AUTO_MERGE_THRESHOLD", "MANUAL_REVIEW_THRESHOLD",
    "DuplicateCluster", "MergeResult", "normalize_label", "pinyin_keys",
    "detect_duplicates", "merge_entities", "split_entities",
]
