"""R21: O5-B25 Copilot 提案表

O5-S2（docs/design/14 §5）：
- copilot_proposals  B25 提案执行器的持久化与状态机（pending→applied/rejected/expired），
  base_version 支撑并发画布冲突检测（执行前比对版本号）。

Revision ID: b2c3d4e5f6a7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "copilot_proposals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("proposal_id", sa.String(32), nullable=False, unique=True, index=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("session_id", sa.Integer(),
                  sa.ForeignKey("copilot_sessions.id", ondelete="CASCADE"),
                  nullable=True, index=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("targets", sa.JSON(), nullable=True),
        sa.Column("diff_preview", sa.JSON(), nullable=True),
        sa.Column("impact", sa.JSON(), nullable=True),
        sa.Column("llm_reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("risk_tier", sa.String(8), nullable=False, server_default="low"),
        sa.Column("status", sa.Enum("pending", "applied", "rejected", "expired",
                                    name="copilot_proposal_status"),
                  nullable=False, server_default="pending"),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("copilot_proposals")
    sa.Enum(name="copilot_proposal_status").drop(op.get_bind(), checkfirst=True)
