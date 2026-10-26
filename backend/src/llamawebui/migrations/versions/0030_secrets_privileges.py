"""Add the secrets privileges and grant them to administrators."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_secrets_privileges"
down_revision: str | None = "0029_benchmark_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# New databases already have these rows: 0010 seeds the current privilege catalog.
PRIVILEGES = (
    ("secrets.read", "Secrets read", "See which credentials are saved", "read"),
    ("secrets.write", "Secrets write", "Save and remove credentials", "write"),
)


def upgrade() -> None:
    connection = op.get_bind()
    administrator_id = connection.execute(
        sa.text("SELECT id FROM roles WHERE name = 'Administrator'")
    ).scalar_one_or_none()
    for key, name, description, access in PRIVILEGES:
        connection.execute(
            sa.text(
                """
                INSERT OR IGNORE INTO privileges (key, name, description, resource, access)
                VALUES (:key, :name, :description, 'secrets', :access)
                """
            ),
            {"key": key, "name": name, "description": description, "access": access},
        )
        if administrator_id is not None:
            connection.execute(
                sa.text(
                    """
                    INSERT OR IGNORE INTO role_privileges (role_id, privilege_key)
                    VALUES (:role_id, :key)
                    """
                ),
                {"role_id": administrator_id, "key": key},
            )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("DELETE FROM role_privileges WHERE privilege_key LIKE 'secrets.%'"))
    connection.execute(sa.text("DELETE FROM privileges WHERE resource = 'secrets'"))
