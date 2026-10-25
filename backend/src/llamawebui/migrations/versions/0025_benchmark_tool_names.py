"""Record which file tools a benchmark run was given."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_benchmark_tool_names"
down_revision: str | None = "0024_benchmark_optional_max_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs keep None: they were given every tool.
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("tool_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("tool_names")
