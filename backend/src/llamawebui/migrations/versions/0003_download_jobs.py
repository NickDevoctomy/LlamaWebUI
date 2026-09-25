"""Create durable download job table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_download_jobs"
down_revision: str | None = "0002_model_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repo_id", sa.String(length=400), nullable=False),
        sa.Column("revision", sa.String(length=64), nullable=False),
        sa.Column("group_key", sa.Text(), nullable=False),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("total_bytes", sa.Integer(), nullable=False),
        sa.Column("completed_bytes", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("download_jobs")
