"""Add origin column to memory_items (provenance / P0).

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # P0 provenance: track who created each memory so autonomous
    # consolidation (forgetting / reflect / migrator / dreamer) can be
    # restricted to agent-self-sedimented items. Existing rows backfill to
    # 'foreground' — user-entered memories stay protected by default.
    op.add_column(
        "memory_items",
        sa.Column(
            "origin",
            sa.Text(),
            nullable=False,
            server_default="foreground",
        ),
    )


def downgrade() -> None:
    op.drop_column("memory_items", "origin")
