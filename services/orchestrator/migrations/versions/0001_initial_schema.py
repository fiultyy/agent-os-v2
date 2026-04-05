"""Initial schema — memory_items, memory_blocks, sessions.

Revision ID: 0001
Revises:
Create Date: 2026-04-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Agent registry ──────────────────────────────────────────────
    op.create_table(
        "agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, server_default="New Agent"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="idle"),
        sa.Column("model", sa.String(100), nullable=False, server_default="gpt-4o-mini"),
        sa.Column("tools_json", sa.Text, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )

    # ── Memory items ────────────────────────────────────────────────
    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), sa.Index("ix_memory_agent_id"), nullable=False),
        sa.Column("session_id", sa.String(36), sa.Index("ix_memory_session_id"), nullable=False, server_default=""),
        sa.Column("memory_type", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("importance", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("accessed_at", sa.String(40), nullable=False),
        sa.Column("archived", sa.Boolean, nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "memory_blocks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("agent_id", sa.String(36), sa.Index("ix_blocks_agent_id"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("char_limit", sa.Integer, nullable=False, server_default="2000"),
        sa.UniqueConstraint("agent_id", "label", name="uq_block_agent_label"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("message_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(40), nullable=False),
    )

    op.create_table(
        "memory_access_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("accessor_id", sa.String(36), sa.Index("ix_access_log_accessor"), nullable=False),
        sa.Column("target_agent_id", sa.String(36), nullable=False),
        sa.Column("memory_id", sa.String(36), sa.Index("ix_access_log_memory"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("level_granted", sa.Integer, nullable=False),
        sa.Column("granted_at", sa.String(40), nullable=False),
    )

    op.create_table(
        "permission_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("grantor_id", sa.String(36), nullable=False),
        sa.Column("grantee_id", sa.String(36), sa.Index("ix_grants_grantee"), nullable=False),
        sa.Column("target_agent_id", sa.String(36), nullable=False),
        sa.Column("level", sa.Integer, nullable=False),
        sa.Column("memory_type_filter", sa.String(20), nullable=True),
        sa.Column("expires_at", sa.String(40), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("permission_grants")
    op.drop_table("memory_access_log")
    op.drop_table("sessions")
    op.drop_table("memory_blocks")
    op.drop_table("memory_items")
    op.drop_table("agents")
