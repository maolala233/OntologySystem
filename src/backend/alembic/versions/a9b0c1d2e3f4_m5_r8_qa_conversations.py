"""M5 R8: 问答对话分组 —— qa_history.conversation_id（QA 页面：新建/历史对话）

Revises: f7a8b9c0d1e2 (M5 R7)
Create Date: 2026-10-03

对在用库安全：只加可空列 + 索引，不改既有数据（旧行 conversation_id 为 NULL，
不出现在对话列表中，仍可经 GET /qa/history 平铺查看）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"),
        {"t": table, "c": column},
    ).scalar()
    return bool(row and row > 0)


def _index_exists(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND index_name = :n"),
        {"n": name},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    if not _column_exists("qa_history", "conversation_id"):
        op.add_column("qa_history",
                      sa.Column("conversation_id", sa.String(36), nullable=True))
    if not _index_exists("idx_qa_history_conv"):
        op.create_index("idx_qa_history_conv", "qa_history",
                        ["project_id", "user_id", "conversation_id"])


def downgrade() -> None:
    if _index_exists("idx_qa_history_conv"):
        op.drop_index("idx_qa_history_conv", table_name="qa_history")
    if _column_exists("qa_history", "conversation_id"):
        op.drop_column("qa_history", "conversation_id")
