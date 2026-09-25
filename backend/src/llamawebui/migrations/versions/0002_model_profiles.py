"""Create model profile table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_model_profiles"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("alias", sa.String(length=64), nullable=False),
        sa.Column("runtime_id", sa.String(length=36), nullable=False),
        sa.Column("model_path", sa.Text(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("preset", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["runtime_id"], ["runtimes.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alias"),
    )
    op.create_index("ix_model_profiles_runtime_id", "model_profiles", ["runtime_id"])


def downgrade() -> None:
    op.drop_index("ix_model_profiles_runtime_id", table_name="model_profiles")
    op.drop_table("model_profiles")