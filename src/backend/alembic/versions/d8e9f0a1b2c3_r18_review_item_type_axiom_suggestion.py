"""R18: review_items.item_type 扩展 axiom_suggestion（O2-B8）

公理建议引擎候选物化为审核项，批准后写入画布公理（functional/inverse_of/disjointWith）。

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None

_OLD = ("entity_merge", "new_class", "new_predicate",
        "low_confidence_entity", "low_confidence_relation",
        "conflict_value", "conflict_type", "conflict_relationship",
        "missing_evidence", "conflict_axiom")
_NEW = ("entity_merge", "new_class", "new_predicate",
        "low_confidence_entity", "low_confidence_relation",
        "conflict_value", "conflict_type", "conflict_relationship",
        "missing_evidence", "conflict_axiom", "axiom_suggestion")


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
