"""R13: ontology_versions.kind 增加 'manual' —— 实例探索手动编辑落版

实例探索支持手动增删改实例/关系；每次编辑落一版 kind=manual（时间轴可追溯）。
MySQL ENUM 追加值安全（尾部追加不改已有行存储）。

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-07
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

_OLD = sa.Enum("schema", "full", "publication", "rollback", name="version_kind")
_NEW = sa.Enum("schema", "full", "publication", "rollback", "manual", name="version_kind")


def upgrade() -> None:
    with op.batch_alter_table("ontology_versions") as batch:
        batch.alter_column("kind", existing_type=_OLD, type_=_NEW,
                           existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("ontology_versions") as batch:
        batch.alter_column("kind", existing_type=_NEW, type_=_OLD,
                           existing_nullable=False)
