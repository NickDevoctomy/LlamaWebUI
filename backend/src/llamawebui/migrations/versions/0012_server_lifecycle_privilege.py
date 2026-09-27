"""Reconcile the server lifecycle privilege for existing databases."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_server_lifecycle_privilege"
down_revision: str | None = "0011_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    user_role_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE name = 'User'")
    ).scalar_one_or_none()
    if user_role_id is not None:
        connection.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO role_privileges (role_id, privilege_key)
                VALUES (:role_id, 'server.lifecycle.write')
                """
            ),
            {"role_id": user_role_id},
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_privileges WHERE role_id = "
            "(SELECT id FROM roles WHERE name = 'User') "
            "AND privilege_key = 'server.lifecycle.write'"
        )
    )