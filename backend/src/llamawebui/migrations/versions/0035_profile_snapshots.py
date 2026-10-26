"""Store named snapshots of model profiles, to return to later."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_profile_snapshots"
down_revision: str | None = "0034_assistant_router_at_start"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile_snapshots",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "profile_id",
            sa.String(length=36),
            sa.ForeignKey("model_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("runtime_id", sa.String(length=36), nullable=False),
        sa.Column("model_path", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("exclusive", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_profile_snapshots_profile_id", "profile_snapshots", ["profile_id"])
    op.create_index(
        "ix_profile_snapshots_conversation_id", "profile_snapshots", ["conversation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_profile_snapshots_conversation_id", table_name="profile_snapshots")
    op.drop_index("ix_profile_snapshots_profile_id", table_name="profile_snapshots")
    op.drop_table("profile_snapshots")
