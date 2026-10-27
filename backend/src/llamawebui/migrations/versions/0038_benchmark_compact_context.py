"""Record whether a benchmark run compacted its context when it filled."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_benchmark_compact_context"
down_revision: str | None = "0037_benchmark_run_server_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("compact_context", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("compact_context")
