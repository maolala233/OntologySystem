# app/adapters/provenance.py - 溯源（docs/design/04 §8）
# 里程碑：M3 实现。W3C PROV-O 对齐 + SHA-256 校验和（复用 semantica compute_checksum）。

from __future__ import annotations

from pydantic import BaseModel

from app.core.security import sha256_hex


class ProvenanceData(BaseModel):
    target_type: str            # entity | relation | chunk
    target_id: int
    source_document_id: int
    chunk_index: int | None = None
    evidence_text: str = ""     # 实体所在原句（非 chunk 前 200 字，修复缺陷 #11）
    char_start: int | None = None
    char_end: int | None = None
    extraction_method: str = "llm_ner"  # llm_ner | llm_re | manual | merge | mcp
    checksum: str = ""

    def compute_checksum(self, storage_key: str) -> str:
        """SHA-256(storage_key|char_start|char_end|evidence_text) —— 防篡改。"""
        raw = f"{storage_key}|{self.char_start}|{self.char_end}|{self.evidence_text}"
        self.checksum = sha256_hex(raw)
        return self.checksum


def locate_quote(chunk_text: str, evidence: str) -> tuple[int, int] | None:
    """evidence 原句在 chunk 内的码点偏移定位（04 §8）。

    定位失败返回 None → 上层生成 missing_evidence 审核项并把 evidence 降级为 chunk 摘要。
    纯函数，M0 可测试。
    """
    if not evidence:
        return None
    idx = chunk_text.find(evidence)
    if idx < 0:
        return None
    return idx, idx + len(evidence)


def track_batch(records: list[ProvenanceData]) -> int:
    """TODO(M3): 批量写 provenance_records（semantica ProvenanceManager.track_*_batch 语义）。"""
    raise NotImplementedError("M3 实现")


def get_lineage(project_id: int, target_type: str, target_id: int) -> list[ProvenanceData]:
    """TODO(M3): 实体/关系的完整来源链（get_lineage 语义）。"""
    raise NotImplementedError("M3 实现")


__all__ = ["ProvenanceData", "locate_quote", "track_batch", "get_lineage"]
