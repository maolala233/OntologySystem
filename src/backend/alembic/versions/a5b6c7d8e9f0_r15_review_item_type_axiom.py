"""R15: review_items.item_type 增加 conflict_axiom —— 公理违例审核项

公理 2 期：TBox 公理（disjointWith/函数性/基数）违例物化为
review_items(item_type='conflict_axiom')，需扩展 MySQL ENUM 列。

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None

_OLD = ("entity_merge", "new_class", "low_confidence_entity",
        "low_confidence_relation", "conflict_value", "conflict_type",
        "conflict_relationship", "missing_evidence")
_NEW = _OLD + ("conflict_axiom",)


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
