"""M5 R9: 角色配置（模块授权预设）—— roles 表 + 3 个内置角色种子

Revises: a9b0c1d2e3f4 (M5 R8)
Create Date: 2026-10-06

角色 = 模块授权预设：应用角色即按 module_codes 全量生成用户 grants
（允许列表内 allowed=1，其余模块 allowed=0，角色即真相）。
内置角色（建模员/审核员/访客）幂等插入，is_builtin=1 不可删除。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与前端角色卡片一致的内置角色预设（模块码见 MODULE_SEEDS 14 码）
BUILTIN_ROLES = [
    ("建模员", "面向本体构建：文档解析、骨架/实例抽取、消解审核与导出",
     ["documents", "schema_build", "instance_build", "resolution", "review",
      "version_control", "export", "graph_explore"]),
    ("审核员", "面向治理：审核裁决、版本管理、发布与资产浏览问答",
     ["review", "version_control", "publish", "asset_center", "graph_explore", "qa"]),
    ("访客", "只读浏览：公共资产中心与本体问答",
     ["asset_center", "qa"]),
]


def _table_exists(table: str) -> bool:
    row = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": table},
    ).scalar()
    return bool(row and row > 0)


def upgrade() -> None:
    if not _table_exists("roles"):
        op.create_table(
            "roles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("description", sa.String(255), nullable=True),
            sa.Column("module_codes", sa.JSON(), nullable=False),  # MySQL JSON 列不可设默认值，应用层保证非空
            sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_roles_id", "roles", ["id"])
        op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    # 内置角色幂等种子（已存在同名则跳过，不动用户自改内容）
    bind = op.get_bind()
    for name, desc, codes in BUILTIN_ROLES:
        exists = bind.execute(
            sa.text("SELECT COUNT(*) FROM roles WHERE name = :n"), {"n": name},
        ).scalar()
        if not exists:
            import json
            bind.execute(
                sa.text("INSERT INTO roles (name, description, module_codes, is_builtin, created_at) "
                        "VALUES (:n, :d, :c, 1, NOW())"),
                {"n": name, "d": desc, "c": json.dumps(codes, ensure_ascii=False)},
            )


def downgrade() -> None:
    if _table_exists("roles"):
        op.get_bind().execute(sa.text("DELETE FROM roles WHERE is_builtin = 1"))
