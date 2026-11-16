"""r26: qa_history 加 retrieval_trace 列（检索过程卡片数据，历史对话可回看）

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-11-16
"""
from alembic import op
import sqlalchemy as sa


revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("qa_history", sa.Column("retrieval_trace", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("qa_history", "retrieval_trace")
