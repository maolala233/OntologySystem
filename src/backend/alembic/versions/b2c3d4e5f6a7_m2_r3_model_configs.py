"""M2 R3: 模型配置 —— model_configs 表 + projects 加列 + knowledge_domains.code

Revises: a1b2c3d4e5f6 (M1 R2)
Create Date: 2026-10-03
docs/design/02 §3.2/§3.3 / §6（批次 R3）

对在用库安全：建新表 + 加列（带 server_default），不改既有列；
旧 system_configs.llm_config/vl_config 的模型数据由一次性导入脚本迁移（scripts/migrate_system_configs.py），
本迁移不动其内容（保留作回滚兜底）。
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- model_configs（02 §3.2）---
    op.create_table(
        "model_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.Enum("global", "project", name="model_scope"),
                  nullable=False, server_default="global"),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.Enum("chat", "extract", "embedding", "vl", name="model_purpose"),
                  nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(512), nullable=False),
        sa.Column("api_key_encrypted", sa.LargeBinary(1024), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("dims", sa.Integer(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        sa.UniqueConstraint("id", name="uk_model_configs_id"),
    )
    op.create_index("ix_model_configs_id", "model_configs", ["id"])
    op.create_index("ix_model_configs_scope_purpose", "model_configs",
                    ["scope", "purpose", "enabled"])

    # --- projects 加列（02 §3.3：状态机 + 版本指针 + 进度缓存）---
    op.add_column("projects", sa.Column(
        "status", sa.Enum("draft", "building", "ready", "published", name="project_status"),
        nullable=False, server_default="draft"))
    op.add_column("projects", sa.Column("current_version_id", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("build_progress", sa.JSON(), nullable=True))

    # --- knowledge_domains 加拼音码（domain_code_converter 产出）---
    op.add_column("knowledge_domains", sa.Column("code", sa.String(32), nullable=True))
    op.create_unique_constraint("uk_kd_code", "knowledge_domains", ["code"])


def downgrade() -> None:
    op.drop_constraint("uk_kd_code", "knowledge_domains", type_="unique")
    op.drop_column("knowledge_domains", "code")
    op.drop_column("projects", "build_progress")
    op.drop_column("projects", "current_version_id")
    op.drop_column("projects", "status")
    op.drop_index("ix_model_configs_scope_purpose", table_name="model_configs")
    op.drop_index("ix_model_configs_id", table_name="model_configs")
    op.drop_table("model_configs")
