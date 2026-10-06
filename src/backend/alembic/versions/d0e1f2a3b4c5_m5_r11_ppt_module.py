"""M5 R11: PPT 生成独立模块码 —— modules 增加 ppt 行 + MCP 命名 + 平滑补授权

Revises: c9d0e1f2a3b4 (M5 R10)
Create Date: 2026-10-06

"PPT生成"从报告生成（report）拆分为独立模块码 ppt；"MCP 工具层"更名"MCP 工具"。
平滑迁移：显式允许 report 的用户补 ppt allow（升级前能生成 PPT 的用户不受影响）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _module_exists(code: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM modules WHERE code = :c"), {"c": code},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    bind = op.get_bind()
    if not _module_exists("ppt"):
        bind.execute(sa.text(
            "INSERT INTO modules (code, name, description, is_default_on, sort_order) "
            "VALUES ('ppt', 'PPT生成', '基于本体的主题演示文稿（PPT）', 0, 16)"
        ))
    bind.execute(sa.text("UPDATE modules SET name = 'MCP 工具' WHERE code = 'mcp'"))
    # 平滑迁移：显式允许 report 的用户补 ppt allow
    if _module_exists("report"):
        bind.execute(sa.text(
            "INSERT IGNORE INTO user_module_grants (user_id, module_code, allowed, granted_by, granted_at) "
            "SELECT g.user_id, 'ppt', 1, g.granted_by, NOW() "
            "FROM user_module_grants g "
            "JOIN users u ON u.id = g.user_id AND u.role <> 'admin' "
            "WHERE g.module_code = 'report' AND g.allowed = 1 "
            "AND NOT EXISTS (SELECT 1 FROM user_module_grants x "
            "                WHERE x.user_id = g.user_id AND x.module_code = 'ppt')"
        ))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM user_module_grants WHERE module_code = 'ppt'"))
    bind.execute(sa.text("UPDATE modules SET name = 'MCP 工具层' WHERE code = 'mcp'"))
    bind.execute(sa.text("DELETE FROM modules WHERE code = 'ppt'"))
