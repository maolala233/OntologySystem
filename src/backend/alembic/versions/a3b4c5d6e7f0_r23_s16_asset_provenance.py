"""R23: S16 资产来源四态 + 人工认证位（docs/15 S16-Step1）

- entities / relations 新增 source_type ENUM(deterministic|ai|steward_edited|steward_specified)
  与 certified BOOLEAN。语义：机器产物默认 ai/未认证；steward_*/certified 为人工背书，
  graph_data 同步重建（sync_project_rows）时保留，自动流程不得降级。
- 存量行迁移即默认 ai/0（grandfather：新门禁对存量不阻断）。

Revision ID: a3b4c5d6e7f0
Revises: e4b5c6d7e8f9
Create Date: 2026-10-25
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "a3b4c5d6e7f0"
down_revision = "e4b5c6d7e8f9"
branch_labels = None
depends_on = None

SOURCE_ENUM = sa.Enum("deterministic", "ai", "steward_edited", "steward_specified",
                      name="asset_source_type")


def upgrade() -> None:
    op.add_column("entities", sa.Column("source_type", SOURCE_ENUM,
                                        nullable=False, server_default="ai"))
    op.add_column("entities", sa.Column("certified", sa.Boolean(),
                                        nullable=False, server_default=sa.text("0")))
    op.add_column("relations", sa.Column("source_type", SOURCE_ENUM,
                                         nullable=False, server_default="ai"))
    op.add_column("relations", sa.Column("certified", sa.Boolean(),
                                         nullable=False, server_default=sa.text("0")))


def downgrade() -> None:
    op.drop_column("relations", "certified")
    op.drop_column("relations", "source_type")
    op.drop_column("entities", "certified")
    op.drop_column("entities", "source_type")
    # MySQL ENUM 为列内联类型，无需 DROP TYPE（PostgreSQL 才需要）
