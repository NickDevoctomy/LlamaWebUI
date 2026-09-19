"""Create persistent server run history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_server_runs"
down_revision: str | None = "0003_download_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "server_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("runtime_id", sa.String(length=36), nullable=True),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtimes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_server_runs_runtime_id", "server_runs", ["runtime_id"])


def downgrade() -> None:
    op.drop_index("ix_server_runs_runtime_id", table_name="server_runs")
    op.drop_table("server_runs")