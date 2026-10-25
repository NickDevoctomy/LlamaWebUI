"""Record whether a TypeScript benchmark run type-checked in strict mode."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_benchmark_typescript_strict"
down_revision: str | None = "0026_benchmark_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing runs had no TypeScript tools, so they keep None.
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("typescript_strict", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_column("typescript_strict")
