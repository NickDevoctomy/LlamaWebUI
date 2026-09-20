"""Create durable logical model records."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_logical_models"
down_revision: str | None = "0006_hidden_download_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "logical_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("canonical_path", sa.Text(), nullable=False),
        sa.Column("primary_path", sa.Text(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("validation_state", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_path"),
    )


def downgrade() -> None:
    op.drop_table("logical_models")