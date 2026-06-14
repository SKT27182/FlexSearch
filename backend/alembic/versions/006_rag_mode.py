"""Add rag_mode and graph_index to projects

Revision ID: 006
Revises: 005
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "rag_mode",
            sa.String(16),
            nullable=False,
            server_default="vector",
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "graph_index",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "graph_index")
    op.drop_column("projects", "rag_mode")
