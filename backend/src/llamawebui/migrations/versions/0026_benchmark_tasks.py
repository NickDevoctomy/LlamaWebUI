"""Keep the task list a benchmark agent works through with the planning tools."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_benchmark_tasks"
down_revision: str | None = "0025_benchmark_tool_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs had no planning tools, so their task list is empty.
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(
            sa.Column("tasks", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("tasks")
