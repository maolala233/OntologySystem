"""M5 R10: 报告生成独立模块码 —— modules 增加 report 行 + 平滑补授权

Revises: b8c9d0e1f2a3 (M5 R9)
Create Date: 2026-10-06

"报告生成"原挂在 qa 模块下（工具子菜单），现独立为 report 模块码，
进入模块授权矩阵统一配置。平滑迁移：对显式允许 qa 的用户补 report allow 授权，
保证升级前能用报告生成的用户升级后不受影响。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _module_exists(code: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM modules WHERE code = :c"), {"c": code},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    bind = op.get_bind()
    if not _module_exists("report"):
        bind.execute(sa.text(
            "INSERT INTO modules (code, name, description, is_default_on, sort_order) "
            "VALUES ('report', '报告生成', '基于本体的主题分析报告/PPT', 0, 15)"
        ))
    # 平滑迁移：显式允许 qa 的用户补 report allow（与原"挂载在 qa 下"的行为一致）
    if _module_exists("qa"):
        bind.execute(sa.text(
            "INSERT IGNORE INTO user_module_grants (user_id, module_code, allowed, granted_by, granted_at) "
            "SELECT g.user_id, 'report', 1, g.granted_by, NOW() "
            "FROM user_module_grants g "
            "JOIN users u ON u.id = g.user_id AND u.role <> 'admin' "
            "WHERE g.module_code = 'qa' AND g.allowed = 1 "
            "AND NOT EXISTS (SELECT 1 FROM user_module_grants x "
            "                WHERE x.user_id = g.user_id AND x.module_code = 'report')"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM user_module_grants WHERE module_code = 'report'"))
    bind.execute(sa.text("DELETE FROM modules WHERE code = 'report'"))
