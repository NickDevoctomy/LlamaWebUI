"""Keep a timeline of what each benchmark run's generation spent its time on."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_benchmark_timeline"
down_revision: str | None = "0021_benchmark_system_prompt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("timeline", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("timeline")
