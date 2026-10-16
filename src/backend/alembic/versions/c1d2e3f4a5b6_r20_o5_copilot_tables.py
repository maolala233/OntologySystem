"""R20: O5 人机协同 Copilot 会话表

O5-S1（docs/design/14 §5）：
- copilot_sessions   B22 Copilot 会话（项目内分组，模型选择留痕）
- copilot_messages   B22 会话消息（阶段事件/工具调用留痕，kind 区分 chat/后续提案等）

Revision ID: c1d2e3f4a5b6
Revises: e9f0a1b2c3d4
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c1d2e3f4a5b6"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "copilot_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), nullable=True, index=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="新会话"),
        sa.Column("model_config_id", sa.Integer(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=True,
                  server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP")),
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(),
                  sa.ForeignKey("copilot_sessions.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("project_id", sa.Integer(), nullable=False, index=True),
        sa.Column("role", sa.Enum("user", "assistant", name="copilot_message_role"),
                  nullable=False),
        # chat=分析问答；clarify=澄清；proposal=编辑提案；suggestion=建议卡；verify=验证报告（S2+ 扩展）
        sa.Column("kind", sa.String(16), nullable=False, server_default="chat"),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("stages", sa.JSON(), nullable=True),       # 阶段事件记录（回放用）
        sa.Column("tool_calls", sa.JSON(), nullable=True),   # tool-loop 调用轨迹
        sa.Column("status", sa.String(16), nullable=False, server_default="ok"),  # ok/failed/stopped
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("copilot_messages")
    op.drop_table("copilot_sessions")
    sa.Enum(name="copilot_message_role").drop(op.get_bind(), checkfirst=True)
