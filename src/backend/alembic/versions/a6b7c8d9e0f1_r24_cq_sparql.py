"""R24: competency_questions 加 sparql 列（CQ 前置 + SPARQL 真执行）

外部方法论对照（CQ 法）：能力问题不只是验收清单，还要能直接执行——
构建收尾把 CQ 的 SPARQL 模板（SELECT/ASK，平台 URI 约定）落库，
评测时对 Oxigraph 项目命名图真执行出答案行（检索式覆盖率口径保留为回落）。

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-11-08
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("competency_questions",
                  sa.Column("sparql", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("competency_questions", "sparql")
