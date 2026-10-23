"""Record the reasoning level a benchmark run asked for."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_benchmark_reasoning_level"
down_revision: str | None = "0022_benchmark_timeline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("reasoning_level", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("reasoning_level")
