"""M3-6 R6: 版本与发布基础设施 —— ontology_versions + graph_snapshots + publications
+ outbox_events + audit_logs + graph_layouts + projects.status/current_version_id

Revises: d5e6f7a8b9c0 (M3-3 R5)
Create Date: 2026-10-03
docs/design/02 §3.7 / §3.8 / §3.3（批次 R6）

对在用库安全：只建新表 + projects 加可空/带默认新列，不改既有列。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": name},
    ).scalar()
    return bool(row and row > 0)


def _column_exists(table: str, column: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    # --- projects 状态机 + 当前版本指针（02 §3.3）---
    if not _column_exists("projects", "status"):
        op.add_column(
            "projects",
            sa.Column("status",
                      sa.Enum("draft", "building", "ready", "published",
                              name="project_status"),
                      nullable=False, server_default="draft"))
    if not _column_exists("projects", "current_version_id"):
        op.add_column("projects",
                      sa.Column("current_version_id", sa.Integer(), nullable=True))

    # --- ontology_versions（02 §3.7）---
    if not _table_exists("ontology_versions"):
        op.create_table(
            "ontology_versions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(64), nullable=True),
            sa.Column("kind", sa.Enum("schema", "full", "publication", "rollback",
                                      name="version_kind"), nullable=False),
            sa.Column("schema_snapshot_key", sa.String(512), nullable=True),
            sa.Column("full_snapshot_key", sa.String(512), nullable=True),
            sa.Column("stats", sa.JSON(), nullable=True),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("parent_version_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("description", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.UniqueConstraint("project_id", "version_no", name="uk_proj_ver"),
        )
        op.create_index("ix_ontology_versions_id", "ontology_versions", ["id"])
        op.create_index("idx_ver_proj", "ontology_versions", ["project_id", "version_no"])

    # --- graph_snapshots（02 §3.7）---
    if not _table_exists("graph_snapshots"):
        op.create_table(
            "graph_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=True),
            sa.Column("purpose", sa.Enum("version", "publication", "pre_rollback", "backup",
                                         name="snapshot_purpose"), nullable=False),
            sa.Column("storage_key", sa.String(512), nullable=False),
            sa.Column("node_count", sa.Integer(), nullable=False),
            sa.Column("edge_count", sa.Integer(), nullable=False),
            sa.Column("checksum", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_graph_snapshots_id", "graph_snapshots", ["id"])
        op.create_index("idx_snap_proj", "graph_snapshots", ["project_id", "purpose"])

    # --- publications（02 §3.7）---
    if not _table_exists("publications"):
        op.create_table(
            "publications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("version_id", sa.Integer(), nullable=False),
            sa.Column("snapshot_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.Enum("published", "unpublished",
                                        name="publication_status"),
                      nullable=False, server_default="published"),
            sa.Column("note", sa.String(500), nullable=True),
            sa.Column("published_by", sa.Integer(), nullable=False),
            sa.Column("published_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("unpublished_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_publications_id", "publications", ["id"])
        op.create_index("idx_pub_status", "publications", ["status", "project_id"])

    # --- outbox_events（02 §3.8；消费链 M4 接线，本期只建表+写入）---
    if not _table_exists("outbox_events"):
        op.create_table(
            "outbox_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("aggregate_type",
                      sa.Enum("project", "entity", "relation", "document",
                              "publication", "version", name="outbox_aggregate"),
                      nullable=False),
            sa.Column("aggregate_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.Enum("pending", "processed", "failed",
                                        name="outbox_status"),
                      nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("trace_id", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("processed_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_outbox_events_id", "outbox_events", ["id"])
        op.create_index("idx_outbox_status", "outbox_events", ["status", "id"])

    # --- audit_logs（02 §3.8；modules.py 的"本期以结构化日志替代"就此收口）---
    if not _table_exists("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("resource_type", sa.String(32), nullable=True),
            sa.Column("resource_id", sa.String(64), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=True),
            sa.Column("ip", sa.String(45), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_audit_logs_id", "audit_logs", ["id"])
        op.create_index("idx_audit_user", "audit_logs", ["user_id", "created_at"])
        op.create_index("idx_audit_action", "audit_logs", ["action", "created_at"])

    # --- graph_layouts（02 §3.8；>25k 服务端预布局，M4 消费）---
    if not _table_exists("graph_layouts"):
        op.create_table(
            "graph_layouts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("algorithm", sa.String(32), nullable=False, server_default="fa2"),
            sa.Column("layout", sa.JSON(), nullable=True),
            sa.Column("node_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.Enum("pending", "done", "failed",
                                        name="layout_status"),
                      nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_graph_layouts_id", "graph_layouts", ["id"])
        op.create_index("idx_layout_proj", "graph_layouts", ["project_id", "status"])


def downgrade() -> None:
    op.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in ("graph_layouts", "audit_logs", "outbox_events", "publications",
              "graph_snapshots", "ontology_versions"):
        if _table_exists(t):
            op.drop_table(t)
    op.execute("SET FOREIGN_KEY_CHECKS = 1")
    if _column_exists("projects", "current_version_id"):
        op.drop_column("projects", "current_version_id")
    if _column_exists("projects", "status"):
        op.drop_column("projects", "status")
