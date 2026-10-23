"""Record whether a benchmark run offered file tools, and the calls its model made."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_benchmark_tools"
down_revision: str | None = "0019_benchmarks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("tools", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("tool_calls", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("tool_calls")
        batch.drop_column("tools")
