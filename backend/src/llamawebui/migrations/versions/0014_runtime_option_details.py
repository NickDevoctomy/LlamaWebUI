"""Store parsed runtime option rows and a fingerprint of the advertised flag surface."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_runtime_option_details"
down_revision: str | None = "0013_administrator_lifecycle_privilege"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtimes",
        sa.Column("option_details", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column("runtimes", sa.Column("flags_sha256", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("runtimes", "flags_sha256")
    op.drop_column("runtimes", "option_details")
