"""Record the system prompt a benchmark run was sent with."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_benchmark_system_prompt"
down_revision: str | None = "0020_benchmark_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("system_prompt_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("system_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("system_prompt")
        batch.drop_column("system_prompt_id")
