"""Remember whether the router was running when an assistant conversation began."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_assistant_router_at_start"
down_revision: str | None = "0033_assistant_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assistant_conversations") as batch:
        batch.add_column(sa.Column("router_at_start", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_conversations") as batch:
        batch.drop_column("router_at_start")
