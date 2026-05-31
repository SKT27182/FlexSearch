"""Document processing pipeline columns and status values

Revision ID: 005
Revises: 004
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("processing_step", sa.String(255), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("progress_pct", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column("extracted_text_path", sa.String(512), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("extraction_config_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Migrate status values to new pipeline (varchar-backed enum in app)
    op.execute(
        """
        UPDATE documents SET status = 'uploaded' WHERE status::text = 'pending';
        """
    )
    op.execute(
        """
        UPDATE documents SET status = 'extracting' WHERE status::text = 'processing';
        """
    )
    op.alter_column(
        "documents",
        "status",
        type_=sa.String(32),
        existing_type=sa.Enum(
            "pending",
            "processing",
            "completed",
            "failed",
            name="documentstatus",
        ),
        postgresql_using="status::text",
    )


def downgrade() -> None:
    op.drop_column("documents", "extracted_at")
    op.drop_column("documents", "extraction_config_hash")
    op.drop_column("documents", "extracted_text_path")
    op.drop_column("documents", "progress_pct")
    op.drop_column("documents", "processing_step")
