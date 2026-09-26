"""Add the protected basic User role."""

# ruff: noqa: E501

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0011_user_role"
down_revision: str | None = "0010_roles_privileges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("protected", sa.Boolean()),
    )
    privileges = sa.table(
        "role_privileges",
        sa.column("role_id", sa.String()),
        sa.column("privilege_key", sa.String()),
    )
    user_role_id = str(uuid4())
    connection.execute(
        sa.insert(roles).values(
            id=user_role_id,
            name="User",
            description="Basic server and model access.",
            protected=True,
        )
    )
    connection.execute(
        sa.insert(privileges),
        [
            {"role_id": user_role_id, "privilege_key": "server.lifecycle.write"},
            {"role_id": user_role_id, "privilege_key": "server.read"},
            {"role_id": user_role_id, "privilege_key": "profiles.read"},
            {"role_id": user_role_id, "privilege_key": "library.read"},
        ],
    )


def downgrade() -> None:
    connection = op.get_bind()
    role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE name = 'User'")
    ).scalar_one_or_none()
    if role_id is not None:
        connection.execute(sa.text("DELETE FROM role_privileges WHERE role_id = :role_id"), {"role_id": role_id})
        connection.execute(sa.text("DELETE FROM roles WHERE id = :role_id"), {"role_id": role_id})