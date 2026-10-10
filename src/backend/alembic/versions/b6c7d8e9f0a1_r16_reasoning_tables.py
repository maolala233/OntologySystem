"""R16: 蕴含推理表——ontology_rules（推理规则）+ reasoning_results（推理结果行表）

推理结果与原始事实物理分离：facts 在 entities/relations 行表，推理只进
reasoning_results（source 区分 owlrl 语义闭包与自定义规则），不回写事实表。

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-10
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_rules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("if_subject_class", sa.String(length=128), nullable=False),
        sa.Column("if_predicate", sa.String(length=128), nullable=False),
        sa.Column("if_object_class", sa.String(length=128), nullable=False),
        sa.Column("then_predicate", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )

    op.create_table(
        "reasoning_results",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("batch_id", sa.String(length=36), nullable=False, index=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("rule_name", sa.String(length=128), nullable=True),
        sa.Column("subject_uri", sa.String(length=512), nullable=False),
        sa.Column("subject_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("predicate_uri", sa.String(length=512), nullable=False),
        sa.Column("predicate_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("object_uri", sa.String(length=512), nullable=False),
        sa.Column("object_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("reasoning_results")
    op.drop_table("ontology_rules")
