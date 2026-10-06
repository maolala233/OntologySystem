"""R12: users.app_role_id — 用户被应用的角色预设

用户管理列表展示"角色"预设名（角色配置 apply 时写入）。
与 grants 的关系：apply = grants 全量覆盖 + 记录 app_role_id；
之后手动微调 grants 不回写角色，编辑角色模块也不自动重放（需再次应用到用户）。

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-10-06
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("users")}
    if "app_role_id" not in cols:
        op.add_column("users", sa.Column("app_role_id", sa.Integer(), nullable=True))
        fks = {fk["name"] for fk in insp.get_foreign_keys("users")}
        if "fk_users_app_role_id" not in fks:
            op.create_foreign_key(
                "fk_users_app_role_id", "users", "roles",
                ["app_role_id"], ["id"],
            )


def downgrade() -> None:
    op.drop_constraint("fk_users_app_role_id", "users", type_="foreignkey")
    op.drop_column("users", "app_role_id")
