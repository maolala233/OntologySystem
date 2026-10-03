"""M3-3 R5: 本体行表与治理 —— entities + relations + provenance_records + review_items

Revises: c3d4e5f6a7b8 (M3-1 R4)
Create Date: 2026-10-03
docs/design/02 §3.5 / §3.6 / §6（批次 R5）

对在用库安全：只建新表，不改既有列。
entities/relations 是 graph_data blob 的拆行事实源（02 §4 三阶段迁移 S1 双写），
Neo4j/Milvus/Oxigraph 为其投影；review_items 是人工审核队列（8 种触发原因）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- entities（02 §3.5：消解/审核/导出的事实源）---
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("uri", sa.String(512), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("label_normalized", sa.String(255), nullable=False),
        sa.Column("class_label", sa.String(128), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=True),
        sa.Column("props", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("status",
                  sa.Enum("auto", "merged", "pending_review", "approved", "rejected",
                          name="entity_status"),
                  nullable=False, server_default="auto"),
        sa.Column("canonical_id", sa.Integer(), nullable=True),
        sa.Column("is_class_node", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("project_id", "uri", name="uk_proj_uri"),
        sa.ForeignKeyConstraint(["canonical_id"], ["entities.id"],
                                name="fk_entity_canonical", ondelete="SET NULL"),
    )
    op.create_index("ix_entities_id", "entities", ["id"])
    op.create_index("idx_proj_class", "entities", ["project_id", "class_label"])
    op.create_index("idx_proj_status", "entities", ["project_id", "status"])
    op.create_index("idx_proj_norm", "entities", ["project_id", "label_normalized"])

    # --- relations（02 §3.5：双向边判定在后端，06 §4.4）---
    op.create_table(
        "relations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("predicate", sa.String(128), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("props", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("status",
                  sa.Enum("auto", "pending_review", "approved", "rejected",
                          name="relation_status"),
                  nullable=False, server_default="auto"),
        sa.Column("is_class_edge", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["subject_id"], ["entities.id"],
                                name="fk_rel_subject", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["object_id"], ["entities.id"],
                                name="fk_rel_object", ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "subject_id", "predicate", "object_id",
                            name="uk_proj_edge"),
    )
    op.create_index("ix_relations_id", "relations", ["id"])
    op.create_index("idx_proj_subj", "relations", ["project_id", "subject_id"])
    op.create_index("idx_proj_obj", "relations", ["project_id", "object_id"])
    op.create_index("idx_rel_status", "relations", ["project_id", "status"])

    # --- provenance_records（02 §3.6：W3C PROV-O 对齐）---
    op.create_table(
        "provenance_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("target_type",
                  sa.Enum("entity", "relation", "chunk", name="prov_target_type"),
                  nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("source_document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("evidence_text", sa.String(1024), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("extraction_method", sa.String(32), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["source_document_id"], ["uploaded_documents.id"],
                                name="fk_prov_doc", ondelete="CASCADE"),
    )
    op.create_index("ix_provenance_records_id", "provenance_records", ["id"])
    op.create_index("idx_prov_target", "provenance_records",
                    ["project_id", "target_type", "target_id"])
    op.create_index("idx_prov_doc", "provenance_records", ["source_document_id"])

    # --- review_items（02 §3.6：8 种触发原因；状态机 pending→claimed→approved/rejected/edited）---
    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("item_type",
                  sa.Enum("entity_merge", "new_class", "low_confidence_entity",
                          "low_confidence_relation", "conflict_value", "conflict_type",
                          "conflict_relationship", "missing_evidence",
                          name="review_item_type"),
                  nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("priority",
                  sa.Enum("low", "medium", "high", "critical", name="review_priority"),
                  nullable=False, server_default="medium"),
        sa.Column("status",
                  sa.Enum("pending", "claimed", "approved", "rejected", "edited",
                          name="review_status"),
                  nullable=False, server_default="pending"),
        sa.Column("suggested_action", sa.JSON(), nullable=True),
        sa.Column("assigned_to", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("result_ref", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_review_items_id", "review_items", ["id"])
    op.create_index("idx_review_proj_status", "review_items",
                    ["project_id", "status", "priority"])
    op.create_index("idx_review_type", "review_items", ["item_type"])


def _table_exists(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": name},
    ).scalar()
    return bool(row and row > 0)


def downgrade() -> None:
    # MySQL ENUM 是列级定义（无独立类型对象），删表即随表清理；
    # relations/provenance 持 FK 先删，SET FOREIGN_KEY_CHECKS 兜底对在用库的部分状态幂等
    op.execute("SET FOREIGN_KEY_CHECKS = 0")
    for t in ("review_items", "provenance_records", "relations", "entities"):
        if _table_exists(t):
            op.drop_table(t)
    op.execute("SET FOREIGN_KEY_CHECKS = 1")
