"""Let a benchmark run go without a token limit, bounded only by the context size."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_benchmark_optional_max_tokens"
down_revision: str | None = "0023_benchmark_reasoning_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.alter_column("max_tokens", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Unlimited runs get the old default limit back so the column can be required again.
    op.execute("UPDATE benchmark_runs SET max_tokens = 32768 WHERE max_tokens IS NULL")
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.alter_column("max_tokens", existing_type=sa.Integer(), nullable=False)
