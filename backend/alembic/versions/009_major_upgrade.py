"""Major upgrade security, generation, and outbox schema.

Revision ID: 009
Revises: 008
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column("rag_generation", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects",
        sa.Column(
            "rag_transition_status",
            sa.String(32),
            nullable=False,
            server_default="ready",
        ),
    )
    op.add_column(
        "projects", sa.Column("rag_transition_error", sa.Text(), nullable=True)
    )
    op.add_column(
        "projects",
        sa.Column(
            "rag_transition_started_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "projects", sa.Column("rag_previous_mode", sa.String(32), nullable=True)
    )
    op.add_column(
        "projects", sa.Column("rag_previous_backend", sa.String(32), nullable=True)
    )
    op.add_column(
        "projects", sa.Column("rag_previous_generation", sa.Integer(), nullable=True)
    )
    op.add_column(
        "projects", sa.Column("deleting_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_outbox_events_event_type", "outbox_events", ["event_type"])
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_project_id", "outbox_events", ["project_id"])
    op.create_index("ix_outbox_events_state", "outbox_events", ["state"])


def downgrade() -> None:
    raise RuntimeError("Revision 009 is a forward-only major-upgrade migration")
