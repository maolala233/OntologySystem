"""R25: cq_eval_mode 枚举扩展 sparql（CQ 真执行评测）

mode='sparql'：能力问题以 SPARQL 模板对 Oxigraph 项目命名图真执行出答案行；
'sparql+retrieval'：混合口径（有模板的真执行 + 其余检索式回落）。
旧值 retrieval / gt / llm_judge 不受影响。

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-11-08
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE cq_evaluations MODIFY COLUMN mode "
        "ENUM('retrieval','llm_judge','gt','sparql','sparql+retrieval') "
        "NOT NULL DEFAULT 'retrieval'"
    )


def downgrade() -> None:
    op.execute("UPDATE cq_evaluations SET mode='retrieval' "
               "WHERE mode IN ('sparql','sparql+retrieval')")
    op.execute(
        "ALTER TABLE cq_evaluations MODIFY COLUMN mode "
        "ENUM('retrieval','llm_judge','gt') NOT NULL DEFAULT 'retrieval'"
    )
