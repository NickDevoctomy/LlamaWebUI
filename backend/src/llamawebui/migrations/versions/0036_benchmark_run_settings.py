"""Record the launch settings each benchmark run measured, and the conversation that ran it."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036_benchmark_run_settings"
down_revision: str | None = "0035_profile_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.add_column(sa.Column("launch_settings", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("settings_fingerprint", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("conversation_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_benchmark_runs_conversation_id", ["conversation_id"])


def downgrade() -> None:
    with op.batch_alter_table("benchmark_runs") as batch:
        batch.drop_index("ix_benchmark_runs_conversation_id")
        batch.drop_column("conversation_id")
        batch.drop_column("settings_fingerprint")
        batch.drop_column("launch_settings")
