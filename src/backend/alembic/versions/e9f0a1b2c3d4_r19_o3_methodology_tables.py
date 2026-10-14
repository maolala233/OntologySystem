"""R19: O3 方法论产品化四新表 + 枚举/列扩展

O3（docs/design/09 §2）：
- competency_questions   B12 CQ 资产化（CRUD/排序/来源）
- cq_evaluations         B12 CQ 评测报告（抽取后自动回写 跑通率/证据覆盖率）
- gate_evaluations       B14 发布门控配对评估记录（评分/verdict 持久化可审计）
- ontology_imports       B17 本体导入与对齐（导入 TTL/OWL + 匹配映射留痕）
- review_items.item_type + schema_match_suggestion, cq_suggestion
- ontology_versions     + is_candidate / gate_status（候选版本 vN-cK，不覆盖正式版本）
- qa_history            + status / repaired_rounds / error（B15 自修复与 B16 失败轨迹数据源）

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-13
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None

_REVIEW_OLD = ("entity_merge", "new_class", "new_predicate",
               "low_confidence_entity", "low_confidence_relation",
               "conflict_value", "conflict_type", "conflict_relationship",
               "missing_evidence", "conflict_axiom", "axiom_suggestion")
_REVIEW_NEW = _REVIEW_OLD + ("schema_match_suggestion", "cq_suggestion")


def upgrade() -> None:
    op.create_table(
        "competency_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer_criteria", sa.Text(), nullable=True),
        sa.Column("source", sa.Enum("manual", "evolution", name="cq_source"),
                  nullable=False, server_default="manual"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "cq_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("trigger", sa.Enum("manual", "auto_post_extract", "publish_gate",
                                     name="cq_eval_trigger"),
                  nullable=False, server_default="manual"),
        sa.Column("mode", sa.Enum("retrieval", "llm_judge", "gt", name="cq_eval_mode"),
                  nullable=False, server_default="retrieval"),
        sa.Column("baseline_version_id", sa.Integer(), nullable=True),
        sa.Column("candidate_version_id", sa.Integer(), nullable=True),
        sa.Column("total_cq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answerable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "gate_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("mode", sa.Enum("gt", "llm_judge", name="gate_eval_mode"),
                  nullable=False),
        sa.Column("baseline_version_id", sa.Integer(), nullable=True),
        sa.Column("candidate_version_id", sa.Integer(), nullable=True),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ties", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hard_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unacceptable_regressions", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("verdict", sa.Enum("pass", "reject", name="gate_verdict"),
                  nullable=False),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "ontology_imports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("format", sa.String(16), nullable=False, server_default="ttl"),
        sa.Column("class_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("property_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mappings", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "evolution_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("clusters", sa.JSON(), nullable=True),
        sa.Column("review_item_ids", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.alter_column(
        "review_items", "item_type",
        existing_type=sa.Enum(*_REVIEW_OLD, name="review_item_type"),
        type_=sa.Enum(*_REVIEW_NEW, name="review_item_type"),
        existing_nullable=False,
    )
    op.add_column("ontology_versions",
                  sa.Column("is_candidate", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.add_column("ontology_versions",
                  sa.Column("gate_status", sa.String(16), nullable=True))
    op.add_column("qa_history",
                  sa.Column("status", sa.Enum("ok", "repaired", "failed",
                                              name="qa_status"),
                            nullable=False, server_default="ok"))
    op.add_column("qa_history",
                  sa.Column("repaired_rounds", sa.Integer(), nullable=False,
                            server_default="0"))
    op.add_column("qa_history", sa.Column("error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("qa_history", "error")
    op.drop_column("qa_history", "repaired_rounds")
    op.drop_column("qa_history", "status")
    op.drop_column("ontology_versions", "gate_status")
    op.drop_column("ontology_versions", "is_candidate")
    op.alter_column(
        "review_items", "item_type",
        existing_type=sa.Enum(*_REVIEW_NEW, name="review_item_type"),
        type_=sa.Enum(*_REVIEW_OLD, name="review_item_type"),
        existing_nullable=False,
    )
    op.drop_table("ontology_imports")
    op.drop_table("evolution_reports")
    op.drop_table("gate_evaluations")
    op.drop_table("cq_evaluations")
    op.drop_table("competency_questions")
