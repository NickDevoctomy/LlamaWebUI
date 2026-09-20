"""Add logical model to profile relationships."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_logical_model_profiles"
down_revision: str | None = "0007_logical_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "logical_model_profiles",
        sa.Column("logical_model_id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["logical_model_id"], ["logical_models.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"], ["model_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("logical_model_id", "profile_id"),
    )


def downgrade() -> None:
    op.drop_table("logical_model_profiles")