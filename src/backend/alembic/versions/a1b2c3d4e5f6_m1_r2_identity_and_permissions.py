"""M1 R2: 身份与权限 —— users 加列 + modules/user_module_grants/project_members

Revises: 26caacd4bdc3 (R1 baseline)
Create Date: 2026-10-03
docs/design/02 §3.1 / §6（批次 R2）

对在用库安全：全部为加列（带 server_default）与建表（新表），不改既有列；
种子与回填幂等（INSERT 忽略已存在 / UPDATE 仅命中 role 缺省行）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "26caacd4bdc3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 14 个功能模块种子（与 app.infrastructure.database.MODULE_SEEDS 保持一致）
MODULE_SEEDS = [
    ("dashboard", "工作台", "首页统计与快捷入口", 1, 1),
    ("projects", "项目管理", "项目创建与列表", 1, 2),
    ("documents", "文档管理", "上传/解析/分块", 0, 3),
    ("schema_build", "骨架构建", "TBox 抽取与画布修订", 0, 4),
    ("instance_build", "实例构建", "ABox 抽取", 0, 5),
    ("resolution", "实体消解与冲突", "消解三层流水线与冲突检测", 0, 6),
    ("review", "人工审核", "审核队列与裁决", 0, 7),
    ("version_control", "版本与时间轴", "快照/diff/回滚/时间线", 0, 8),
    ("publish", "发布管理", "发布到公共区", 0, 9),
    ("asset_center", "公共资产中心", "浏览已发布本体", 1, 10),
    ("graph_explore", "图谱探索", "大规模图只读探索", 0, 11),
    ("qa", "本体问答", "基于本体的 GraphRAG 问答", 0, 12),
    ("mcp", "MCP 工具层", "MCP 令牌与工具调用", 0, 13),
    ("export", "导出", "18 种格式导出", 0, 14),
]


def upgrade() -> None:
    # --- users 加列（02 §3.1）---
    op.add_column("users", sa.Column("role", sa.Enum("admin", "user", name="user_role"),
                                      nullable=False, server_default="user"))
    op.add_column("users", sa.Column("email", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("display_name", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("locale", sa.String(10), nullable=False, server_default="zh-CN"))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))
    op.create_unique_constraint("uk_users_email", "users", ["email"])

    # --- modules 字典 + 种子 ---
    op.create_table(
        "modules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("is_default_on", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_modules_id", "modules", ["id"])
    op.create_index("ix_modules_code", "modules", ["code"], unique=True)
    modules_table = sa.table(
        "modules",
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("is_default_on", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    op.bulk_insert(modules_table,
                   [{"code": c, "name": n, "description": d,
                     "is_default_on": bool(dflt), "sort_order": s}
                    for c, n, d, dflt, s in MODULE_SEEDS])

    # --- user_module_grants ---
    op.create_table(
        "user_module_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("module_code", sa.String(32), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("granted_by", sa.Integer(), nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "module_code", name="uk_user_module"),
    )
    op.create_index("ix_user_module_grants_id", "user_module_grants", ["id"])
    op.create_index("ix_user_module_grants_user_id", "user_module_grants", ["user_id"])

    # --- project_members ---
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Enum("owner", "editor", "viewer", name="project_role"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "user_id", name="uk_proj_user"),
    )
    op.create_index("ix_project_members_id", "project_members", ["id"])
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    # --- 存量用户回填：admin → role=admin（幂等：仅命中仍为 user 的行）---
    op.execute("UPDATE users SET role='admin' WHERE username='admin' AND role='user'")
    # 既有项目 owner 补录 project_members（owner 角色冗余同步）
    op.execute(
        "INSERT IGNORE INTO project_members (project_id, user_id, role, created_by) "
        "SELECT p.id, p.owner_id, 'owner', p.owner_id FROM projects p"
    )


def downgrade() -> None:
    op.drop_index("ix_project_members_user_id", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_index("ix_project_members_id", table_name="project_members")
    op.drop_table("project_members")
    op.drop_index("ix_user_module_grants_user_id", table_name="user_module_grants")
    op.drop_index("ix_user_module_grants_id", table_name="user_module_grants")
    op.drop_table("user_module_grants")
    op.drop_index("ix_modules_code", table_name="modules")
    op.drop_index("ix_modules_id", table_name="modules")
    op.drop_table("modules")
    op.drop_constraint("uk_users_email", "users", type_="unique")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "locale")
    op.drop_column("users", "display_name")
    op.drop_column("users", "email")
    op.drop_column("users", "role")
