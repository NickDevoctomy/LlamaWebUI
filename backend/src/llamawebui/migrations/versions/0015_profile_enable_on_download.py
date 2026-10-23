"""Remember which download enables a profile created ahead of its model files."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_profile_enable_on_download"
down_revision: str | None = "0014_runtime_option_details"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "model_profiles",
        sa.Column("enable_on_download", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch:
        batch.drop_column("enable_on_download")
