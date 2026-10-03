"""M3-1 R4: 文档与切片 —— uploaded_documents 加列（MinIO/秒传/解析状态） + document_chunks

Revises: b2c3d4e5f6a7 (M2 R3)
Create Date: 2026-10-03
docs/design/02 §3.4 / §6（批次 R4）

对在用库安全：只加列（可空/带默认）+ 建新表，不改既有列；
text_content(LONGTEXT) 保留兼容期，text_key 就绪后（M3-2）再清空。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- uploaded_documents 加列（02 §3.4）---
    op.add_column("uploaded_documents", sa.Column("storage_key", sa.String(512), nullable=True))
    op.add_column("uploaded_documents", sa.Column("text_key", sa.String(512), nullable=True))
    op.add_column("uploaded_documents", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("uploaded_documents", sa.Column(
        "parse_status",
        sa.Enum("uploaded", "parsing", "parsed", "failed", name="doc_parse_status"),
        nullable=False, server_default="uploaded"))
    op.add_column("uploaded_documents", sa.Column("parse_error", sa.Text(), nullable=True))
    op.add_column("uploaded_documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column("uploaded_documents", sa.Column("language", sa.String(10), nullable=True))
    op.add_column("uploaded_documents", sa.Column("parse_backend", sa.String(32), nullable=True))
    op.add_column("uploaded_documents", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("idx_doc_project_status", "uploaded_documents", ["project_id", "parse_status"])
    op.create_index("idx_doc_sha", "uploaded_documents", ["sha256"])

    # --- document_chunks（02 §3.4：抽取与向量化的统一单位）---
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(),
                  sa.ForeignKey("uploaded_documents.id", ondelete="CASCADE", name="fk_chunk_doc"),
                  nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("char_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("document_id", "chunk_index", name="uk_doc_chunk"),
    )
    op.create_index("ix_document_chunks_id", "document_chunks", ["id"])
    op.create_index("idx_chunk_doc", "document_chunks", ["document_id", "chunk_index"])


def _index_exists(name: str, table: str) -> bool:
    """MySQL DDL 非事务 + 8.0 无 DROP INDEX IF EXISTS：downgrade 前先查 information_schema，
    对此前失败留下的部分状态幂等。"""
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i"),
        {"t": table, "i": name},
    ).scalar()
    return bool(row and row > 0)


def _table_exists(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": name},
    ).scalar()
    return bool(row and row > 0)


def downgrade() -> None:
    if _table_exists("document_chunks"):
        if _index_exists("idx_chunk_doc", "document_chunks"):
            op.drop_index("idx_chunk_doc", table_name="document_chunks")
        if _index_exists("ix_document_chunks_id", "document_chunks"):
            op.drop_index("ix_document_chunks_id", table_name="document_chunks")
        op.drop_table("document_chunks")
    if _index_exists("idx_doc_sha", "uploaded_documents"):
        op.drop_index("idx_doc_sha", table_name="uploaded_documents")
    # idx_doc_project_status 是 project_id 外键的支撑索引（MySQL 1553），先摘 FK 再删索引，
    # 删除后重建 FK（MySQL 会自动为其生成 project_id 支撑索引，恢复 R1 原状）
    op.drop_constraint("uploaded_documents_ibfk_1", "uploaded_documents", type_="foreignkey")
    op.drop_index("idx_doc_project_status", table_name="uploaded_documents")
    op.create_foreign_key("uploaded_documents_ibfk_1", "uploaded_documents", "projects",
                          ["project_id"], ["id"])
    op.drop_column("uploaded_documents", "deleted_at")
    op.drop_column("uploaded_documents", "parse_backend")
    op.drop_column("uploaded_documents", "language")
    op.drop_column("uploaded_documents", "page_count")
    op.drop_column("uploaded_documents", "parse_error")
    op.drop_column("uploaded_documents", "parse_status")
    op.drop_column("uploaded_documents", "sha256")
    op.drop_column("uploaded_documents", "text_key")
    op.drop_column("uploaded_documents", "storage_key")
