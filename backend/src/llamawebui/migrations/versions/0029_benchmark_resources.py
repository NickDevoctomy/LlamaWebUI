"""Keep a benchmark run's context and memory use over time."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_benchmark_resources"
down_revision: str | None = "0028_benchmark_verbose_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs were not measured, so they keep an empty list.
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("resources", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("resources")
