"""R25: S22 进化运行状态机（docs/15 S22 / EvoOntology EvolutionSession 同构）

- evolution_runs：running --reject 循环--> running；--pass--> accepted；--预算/中断--> incomplete。
- evolution_rounds：每轮假设→候选→配对评估→结论留痕（reject 可审计、可重试）。
Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-25
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_runs",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("status", sa.Enum("running", "accepted", "incomplete",
                                    name="evolution_run_status"),
                  nullable=False, server_default="running"),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("budget_rounds", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_version_id", sa.Integer(), nullable=True),
        sa.Column("diagnosis", sa.JSON(), nullable=True),
        sa.Column("incomplete_reason", sa.String(32), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "evolution_rounds",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("run_id", sa.Integer(),
                  sa.ForeignKey("evolution_runs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=True),
        sa.Column("candidate_version_id", sa.Integer(), nullable=True),
        sa.Column("gate_evaluation_id", sa.Integer(), nullable=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="gt"),
        sa.Column("verdict", sa.String(8), nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ties", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hard_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("evolution_rounds")
    op.drop_table("evolution_runs")
