# app/adapters/versioning.py - 版本控制与时间轴（docs/design/04 §9 / 03 §11）
# 里程碑：M3 实现。封装 semantica TemporalVersionManager / OntologyVersionManager，
# 持久化走 ontology_versions + graph_snapshots + MinIO（02 §3.7）。

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class VersionKind(str, Enum):
    SCHEMA = "schema"
    FULL = "full"
    PUBLICATION = "publication"
    ROLLBACK = "rollback"


class VersionDiff(BaseModel):
    classes: dict = Field(default_factory=dict)      # {added, removed, modified}
    properties: dict = Field(default_factory=dict)
    instances: dict = Field(default_factory=dict)
    relations: dict = Field(default_factory=dict)


class VersionSnapshot(BaseModel):
    project_id: int
    version_no: int
    kind: VersionKind
    label: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    checksum: str = ""


def create_version(project_id: int, kind: VersionKind, author_id: int,
                   label: str | None = None, description: str | None = None) -> VersionSnapshot:
    """TODO(M3): 落版本 —— schema/full 快照入 MinIO + 行表记录 + stats 统计。"""
    raise NotImplementedError("M3 实现（docs/design/04 §9）")


def diff_versions(project_id: int, version_a: int, version_b: int) -> VersionDiff:
    """TODO(M3): 复用 OntologyVersionManager.compare_versions 语义 + 行表对账，
    返回类/属性/实例/关系四级 added/removed/modified（字段级 from→to）。"""
    raise NotImplementedError("M3 实现")


def restore_version(project_id: int, version_no: int, operator_id: int) -> VersionSnapshot:
    """TODO(M3): 回滚 —— 先落 pre_rollback 快照与 rollback 版本，再整表替换 + outbox 全量重建。"""
    raise NotImplementedError("M3 实现")


def entity_timeline(project_id: int, entity_uri: str,
                    time_axis: Literal["valid", "transaction", "both"] = "both") -> list[dict]:
    """TODO(M3): 双时间轴查询（TemporalGraphQuery.query_at_time 语义，
    valid_from/valid_until + recorded_at/superseded_at，02 §3.5 行表维护）。"""
    raise NotImplementedError("M3 实现")


__all__ = [
    "VersionKind", "VersionDiff", "VersionSnapshot",
    "create_version", "diff_versions", "restore_version", "entity_timeline",
]
