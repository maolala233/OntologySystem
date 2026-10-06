"""M5 R7: 工具层 —— mcp_tokens（07 §3.2 / 02 §3.9）+ qa_history（03 §15 问答历史）

Revises: e6f7a8b9c0d1 (M3-6 R6)
Create Date: 2026-10-04
docs/design/02 §3.9（批次 R7）、03 §15、07 §3

对在用库安全：只建新表，不改既有列。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": name},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    # --- mcp_tokens（02 §3.9，07 §3.2）：user+project+读写范围 绑定 ---
    if not _table_exists("mcp_tokens"):
        op.create_table(
            "mcp_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(64), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),  # SHA-256(sk-mcp-*)
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("can_write", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("idx_mcp_user", "mcp_tokens", ["user_id"])

    # --- qa_history（03 §15 GET /qa/history 契约的存储）：问题/答案/引用/模型/耗时 ---
    if not _table_exists("qa_history"):
        op.create_table(
            "qa_history",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("question", sa.Text(), nullable=False),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("sources", sa.JSON(), nullable=True),      # 引用列表（含溯源定位）
            sa.Column("model", sa.String(128), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False,
                      server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("idx_qa_history_proj", "qa_history", ["project_id", "created_at"])


def downgrade() -> None:
    if _table_exists("qa_history"):
        op.drop_table("qa_history")
    if _table_exists("mcp_tokens"):
        op.drop_table("mcp_tokens")
