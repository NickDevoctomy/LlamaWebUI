"""Add roles, privileges, and protected Administrator seed data."""

# ruff: noqa: E501

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

from llamawebui.services.authorization import PRIVILEGES

revision: str = "0010_roles_privileges"
down_revision: str | None = "0009_users_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("protected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "privileges",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("resource", sa.String(length=50), nullable=False),
        sa.Column("access", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_privileges_resource", "privileges", ["resource"])
    op.create_table(
        "role_privileges",
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("privilege_key", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["privilege_key"], ["privileges.key"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "privilege_key"),
    )
    op.add_column("users", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("role_id", sa.String(length=36), nullable=True))
    with op.batch_alter_table("users") as batch:
        batch.create_index("ix_users_role_id", ["role_id"])
        batch.create_foreign_key(
            "fk_users_role_id", "roles", ["role_id"], ["id"], ondelete="RESTRICT"
        )

    connection = op.get_bind()
    administrator_id = str(uuid4())
    connection.execute(
        sa.insert(sa.table("roles", sa.column("id"), sa.column("name"), sa.column("description"), sa.column("protected"))).values(
            id=administrator_id,
            name="Administrator",
            description="Full control-plane access.",
            protected=True,
        )
    )
    for privilege in PRIVILEGES:
        connection.execute(
            sa.insert(sa.table("privileges", sa.column("key"), sa.column("name"), sa.column("description"), sa.column("resource"), sa.column("access"))).values(
                key=privilege.key,
                name=privilege.name,
                description=privilege.description,
                resource=privilege.resource,
                access=privilege.access,
            )
        )
        connection.execute(
            sa.insert(sa.table("role_privileges", sa.column("role_id"), sa.column("privilege_key"))).values(
                role_id=administrator_id, privilege_key=privilege.key
            )
        )
    connection.execute(sa.table("users", sa.column("role_id")).update().values(role_id=administrator_id))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_role_id", type_="foreignkey")
        batch.drop_index("ix_users_role_id")
        batch.drop_column("role_id")
        batch.drop_column("description")
    op.drop_table("role_privileges")
    op.drop_index("ix_privileges_resource", table_name="privileges")
    op.drop_table("privileges")
    op.drop_table("roles")