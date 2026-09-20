"""Add hidden state for cleared download jobs."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_hidden_download_jobs"
down_revision: str | None = "0005_access_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "download_jobs",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("download_jobs", "hidden")