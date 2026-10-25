"""R24: S21 进化触发器状态表（docs/15 S21-2）

每项目一行 evolution_trigger_state：checkpoint（只随发布成功推进）+ 双阈值
（新轨迹 ≥30 条或距 checkpoint ≥7 天 → evolution_due 提醒）。
Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f0
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_trigger_state",
        sa.Column("project_id", sa.Integer(), primary_key=True, index=True),
        sa.Column("checkpoint_time", sa.DateTime(), nullable=False),
        sa.Column("checkpoint_qa_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("checkpoint_copilot_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("evolution_due", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("min_new_trajectories", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("min_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("evolution_trigger_state")
