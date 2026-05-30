"""Add users.name column

Revision ID: 003
Revises: 002
"""

from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS name VARCHAR(255);
        """
    )
    op.execute(
        """
        UPDATE users
        SET name = split_part(email, '@', 1)
        WHERE name IS NULL OR name = '';
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ALTER COLUMN name SET NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS name")
