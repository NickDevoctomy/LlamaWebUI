"""Store benchmark runs, and the latest results on each profile."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_benchmarks"
down_revision: str | None = "0018_profile_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_profiles", sa.Column("benchmark_results", sa.JSON(), nullable=True))
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(length=36),
            sa.ForeignKey("model_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_alias", sa.String(length=64), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("runtime_id", sa.String(length=36), nullable=False),
        sa.Column("benchmark_id", sa.String(length=64), nullable=False),
        sa.Column("benchmark_title", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stats", sa.JSON(), nullable=False),
        sa.Column("samples", sa.JSON(), nullable=False),
        sa.Column("output", sa.Text(), nullable=False),
        sa.Column("reasoning", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_benchmark_runs_profile_id", "benchmark_runs", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_benchmark_runs_profile_id", table_name="benchmark_runs")
    op.drop_table("benchmark_runs")
    with op.batch_alter_table("model_profiles") as batch:
        batch.drop_column("benchmark_results")
