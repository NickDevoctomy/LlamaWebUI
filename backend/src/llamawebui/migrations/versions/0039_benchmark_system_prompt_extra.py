"""Record the text a benchmark run added after its chosen system prompt."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0039_benchmark_system_prompt_extra"
down_revision: str | None = "0038_benchmark_compact_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("system_prompt_extra", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("system_prompt_extra")
