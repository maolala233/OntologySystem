# app/adapters/conflicts.py - 冲突检测与解决（docs/design/04 §6）
# 里程碑：M3 实现 VALUE/TYPE/RELATIONSHIP（semantica 直接接入）；
# TEMPORAL/LOGICAL 平台自建（区间重叠检查 / SHACL 校验转审核）。

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ConflictType(str, Enum):
    VALUE = "value"
    TYPE = "type"
    RELATIONSHIP = "relationship"
    TEMPORAL = "temporal"    # 平台自建：valid_from/valid_until 区间重叠
    LOGICAL = "logical"      # 平台自建：SHACL 校验失败项转审核


class ResolutionStrategy(str, Enum):
    VOTING = "voting"
    CREDIBILITY_WEIGHTED = "credibility_weighted"  # 默认
    MOST_RECENT = "most_recent"
    FIRST_SEEN = "first_seen"
    HIGHEST_CONFIDENCE = "highest_confidence"
    MANUAL_REVIEW = "manual_review"                # critical 强制
    EXPERT_REVIEW = "expert_review"


class ConflictRecord(BaseModel):
    conflict_type: ConflictType
    severity: str  # low/medium/high/critical
    entity_uri: str
    property_name: str | None = None
    conflicting_values: list = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    recommended_action: ResolutionStrategy = ResolutionStrategy.CREDIBILITY_WEIGHTED
    guide: dict = Field(default_factory=dict)  # InvestigationGuideGenerator 输出


def detect_conflicts(entities: list, relations: list,
                     types: tuple[ConflictType, ...] = (
                         ConflictType.VALUE, ConflictType.TYPE, ConflictType.RELATIONSHIP),
                     source_credibility: dict[str, float] | None = None) -> list[ConflictRecord]:
    """TODO(M3): 消解之后运行（消解减少假冲突）；critical 强制 MANUAL_REVIEW；
    检出项物化为 review_items(item_type='conflict_*')，payload 附 detector_report。"""
    raise NotImplementedError("M3 实现（docs/design/04 §6）")


def detect_temporal_conflicts(entities: list) -> list[ConflictRecord]:
    """TODO(M3): 自建 —— 对带 valid_from/valid_until 属性的实体做区间重叠/包含检查。"""
    raise NotImplementedError("M3 实现")


__all__ = ["ConflictType", "ResolutionStrategy", "ConflictRecord", "detect_conflicts", "detect_temporal_conflicts"]
