"""R23: cq_source 枚举扩展 auto（管线自动生成能力问题）

外部方法论对照（能力问题法/CQ）：一键构建在骨架就绪后自动生成本体应答的
能力问题（source='auto'），收尾统一检索式评测——手工（manual）与问答进化
（evolution）资产不受影响，生成前已有 ≥5 条启用 CQ 则跳过。

Revision ID: f5a6b7c8d9e0
Revises: e4b5c6d7e8f9
Create Date: 2026-11-08
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "f5a6b7c8d9e0"
down_revision = "e4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # MySQL ENUM 扩展取值需整列重定义；与 ORM 模型保持同序（旧值在前，向后兼容）
    op.execute(
        "ALTER TABLE competency_questions MODIFY COLUMN source "
        "ENUM('manual','evolution','auto') NOT NULL DEFAULT 'manual'"
    )


def downgrade() -> None:
    op.execute("DELETE FROM competency_questions WHERE source = 'auto'")
    op.execute(
        "ALTER TABLE competency_questions MODIFY COLUMN source "
        "ENUM('manual','evolution') NOT NULL DEFAULT 'manual'"
    )
