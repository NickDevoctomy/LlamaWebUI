"""Let a profile carry a short operator-facing description."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_profile_description"
down_revision: str | None = "0016_profile_exclusive"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("model_profiles", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch:
        batch.drop_column("description")
