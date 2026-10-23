"""Mark profiles whose model must be the only one loaded in the router."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_profile_exclusive"
down_revision: str | None = "0015_profile_enable_on_download"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_profiles",
        sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch:
        batch.drop_column("exclusive")
