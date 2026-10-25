"""R26: S23 数据级授权表（docs/15 S23）

data_grants：每用户至多一行；无行=不受限（grandfather），有行=受限
（知识域白名单 fail-closed + 类/属性黑名单裁剪）。
Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-10-26
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_grants",
        sa.Column("user_id", sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  primary_key=True, index=True),
        sa.Column("domain_allowlist", sa.JSON(), nullable=True),
        sa.Column("class_denylist", sa.JSON(), nullable=True),
        sa.Column("property_denylist", sa.JSON(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("data_grants")
