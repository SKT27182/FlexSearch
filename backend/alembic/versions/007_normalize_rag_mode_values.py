"""Normalize legacy rag_mode enum names to lowercase values

Revision ID: 007
Revises: 006
"""

from typing import Sequence, Union

from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE projects SET rag_mode = 'vector' WHERE rag_mode = 'VECTOR'")
    op.execute("UPDATE projects SET rag_mode = 'graph' WHERE rag_mode = 'GRAPH'")


def downgrade() -> None:
    pass
