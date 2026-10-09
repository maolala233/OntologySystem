"""R14: user_model_picks —— 用户个人抽取模型选择

普通用户可在抽取时从全局 model_configs 里挑一个作为个人抽取模型
（provider 解析优先级：个人选择 → 项目默认 → 全局默认）。

Revision ID: f4a5b6c7d8e9
Revises: f2a3b4c5d6e7
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_model_picks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.Enum("chat", "extract", "embedding", "vl",
                                     name="model_purpose"), nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_pick_user"),
        sa.ForeignKeyConstraint(["config_id"], ["model_configs.id"], name="fk_pick_config"),
        sa.UniqueConstraint("user_id", "purpose", name="uk_user_pick_purpose"),
    )


def downgrade() -> None:
    op.drop_table("user_model_picks")
