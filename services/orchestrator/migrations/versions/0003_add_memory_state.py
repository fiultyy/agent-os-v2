"""Add state + last_state_transition columns to memory_items (state machine / P3).

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P3 deterministic state machine (ACTIVE/STALE/ARCHIVED). Backfill legacy
    # archived rows to ARCHIVED so the new state field matches the old boolean
    # flag (kept for backward compat).
    op.add_column(
        "memory_items",
        sa.Column("state", sa.Text(), nullable=False, server_default="active"),
    )
    op.add_column(
        "memory_items",
        sa.Column(
            "last_state_transition",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.execute("UPDATE memory_items SET state = 'archived' WHERE archived = true")


def downgrade() -> None:
    op.drop_column("memory_items", "last_state_transition")
    op.drop_column("memory_items", "state")
