"""R17: review_items.item_type 扩展 new_predicate（O1-B3）

实例抽取遇到 TBox 未定义的谓词时物化 new_predicate 审核项，
批准后回写 graph_data.schema.object_properties。

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None

_OLD = ("entity_merge", "new_class", "low_confidence_entity",
        "low_confidence_relation", "conflict_value", "conflict_type",
        "conflict_relationship", "missing_evidence", "conflict_axiom")
_NEW = ("entity_merge", "new_class", "new_predicate",
        "low_confidence_entity", "low_confidence_relation",
        "conflict_value", "conflict_type", "conflict_relationship",
        "missing_evidence", "conflict_axiom")


def upgrade() -> None:
    op.alter_column(
        "review_items", "item_type",
        existing_type=sa.Enum(*_OLD, name="review_item_type"),
        type_=sa.Enum(*_NEW, name="review_item_type"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "review_items", "item_type",
        existing_type=sa.Enum(*_NEW, name="review_item_type"),
        type_=sa.Enum(*_OLD, name="review_item_type"),
        existing_nullable=False,
    )
