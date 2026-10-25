"""Record whether a benchmark run kept a verbose tool call log."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_benchmark_verbose_log"
down_revision: str | None = "0027_benchmark_typescript_strict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs kept the short log.
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("verbose_log", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("verbose_log")
