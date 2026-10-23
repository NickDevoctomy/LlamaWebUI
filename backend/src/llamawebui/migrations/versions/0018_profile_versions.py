"""Version profiles and remember which recommendation a profile was added from."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_profile_versions"
down_revision: str | None = "0017_profile_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch:
        batch.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1"))
        )
        batch.add_column(sa.Column("recommendation_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("recommendation_baseline", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch:
        batch.drop_column("recommendation_baseline")
        batch.drop_column("recommendation_id")
        batch.drop_column("version")
