"""INFRA_ADMIN role and infra_hub_user_id link

Revision ID: 002
Revises: 001
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_type t
                JOIN pg_enum e ON t.oid = e.enumtypid
                WHERE t.typname = 'userrole' AND e.enumlabel = 'INFRA_ADMIN'
            ) THEN
                ALTER TYPE userrole ADD VALUE 'INFRA_ADMIN';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS infra_hub_user_id INTEGER;
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_users_infra_hub_user_id
        ON users (infra_hub_user_id)
        WHERE infra_hub_user_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_infra_hub_user_id")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS infra_hub_user_id")
