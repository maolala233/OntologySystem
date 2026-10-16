"""R22: O5-B28 验证报告持久化列

O5-S4（docs/design/14 §5）：
- copilot_messages.report  kind=verify 的验证报告 JSON（三段：CQ 评测/一致性/抽样溯源，
  含与上一版对比 comparison），支撑会话内渲染与回放。

Revision ID: e4b5c6d7e8f9
Revises: d1e2f3a4b5c6
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e4b5c6d7e8f9"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("copilot_messages",
                  sa.Column("report", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("copilot_messages", "report")
