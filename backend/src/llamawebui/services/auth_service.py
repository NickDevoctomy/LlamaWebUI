"""Control-plane user accounts and server-validated sessions.

Accounts and sessions are kept entirely separate from llama.cpp inference
bearer keys (see ``token_registry``). Passwords are stored only as Argon2id
hashes; plaintext passwords are never persisted or logged.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from llamawebui.models import (
    PrivilegeRecord,
    RolePrivilegeRecord,
    RoleRecord,
    SessionRecord,
    UserRecord,
)
from llamawebui.services.authorization import PRIVILEGE_KEYS

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"
SESSION_COOKIE_NAME = "llamawebui_session"
SESSION_TTL = timedelta(days=7)
CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "LlamaWebUI"


def _utcnow() -> datetime:
    """Return a timezone-naive UTC datetime for SQLite storage."""
    return datetime.now(UTC).replace(tzinfo=None)


class AuthenticationError(LookupError):
    pass


class SessionNotFoundError(LookupError):
    pass


class UserAlreadyExistsError(ValueError):
    pass


class RoleAlreadyExistsError(ValueError):
    pass


class RoleNotFoundError(LookupError):
    pass


class RoleProtectedError(ValueError):
    pass


class RoleInUseError(ValueError):
    pass


class InvalidPrivilegesError(ValueError):
    pass


class LastAdministratorError(ValueError):
    pass


class ProtectedUserError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: str
    username: str
    default_credentials: bool
    description: str | None
    role_id: str
    role_name: str
    privileges: frozenset[str]


@dataclass(frozen=True, slots=True)
class ManagedRole:
    id: str
    name: str
    description: str | None
    protected: bool
    privileges: tuple[str, ...]
    user_count: int


@dataclass(frozen=True, slots=True)
class ManagedUser:
    id: str
    username: str
    description: str | None
    role_id: str
    role_name: str
    default_credentials: bool


class AuthService:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._hasher = PasswordHasher()

    def ensure_default_admin(self) -> None:
        """Seed the default ``admin`` / ``admin`` account on first run."""
        with self._sessions() as session:
            self._purge_inactive_sessions(session)
            administrator = session.scalar(
                select(RoleRecord).where(RoleRecord.name == "Administrator")
            )
            if administrator is None:
                raise RuntimeError("Administrator role is not available")
            existing = session.scalar(select(UserRecord.id).limit(1))
            if existing is not None:
                return
            session.add(
                UserRecord(
                    id=str(uuid4()),
                    username=DEFAULT_ADMIN_USERNAME,
                    password_hash=self._hasher.hash(DEFAULT_ADMIN_PASSWORD),
                    role_id=administrator.id,
                )
            )
            session.commit()

    def authenticate(self, username: str, password: str) -> UserRecord:
        clean_username = username.strip()
        with self._sessions() as session:
            user = session.scalar(
                select(UserRecord).where(UserRecord.username == clean_username)
            )
            if user is None:
                raise AuthenticationError("invalid username or password")
            try:
                self._hasher.verify(user.password_hash, password)
            except (VerifyMismatchError, InvalidHashError) as error:
                raise AuthenticationError("invalid username or password") from error
            if self._hasher.check_needs_rehash(user.password_hash):
                user.password_hash = self._hasher.hash(password)
                session.commit()
            return user

    def list_users(self) -> tuple[AuthenticatedUser, ...]:
        with self._sessions() as session:
            users = session.scalars(select(UserRecord).order_by(UserRecord.username)).all()
            return tuple(
                self._authenticated_user(session, user)
                for user in users
            )

    def create_user(self, username: str, password: str) -> AuthenticatedUser:
        return self.create_managed_user(username, password)

    def create_managed_user(
        self,
        username: str,
        password: str,
        *,
        description: str | None = None,
        role_id: str | None = None,
    ) -> AuthenticatedUser:
        clean_username = username.strip()
        with self._sessions() as session:
            existing = session.scalar(
                select(UserRecord).where(UserRecord.username == clean_username)
            )
            if existing is not None:
                raise UserAlreadyExistsError("username is already in use")
            selected_role_id = role_id or session.scalar(
                select(RoleRecord.id).where(RoleRecord.name == "Administrator")
            )
            if selected_role_id is None or session.get(RoleRecord, selected_role_id) is None:
                raise RoleNotFoundError("role not found")
            user = UserRecord(
                id=str(uuid4()),
                username=clean_username,
                password_hash=self._hasher.hash(password),
                description=description,
                role_id=selected_role_id,
            )
            session.add(user)
            session.commit()
            return self._authenticated_user(session, user, default_credentials=False)

    def list_roles(self) -> tuple[ManagedRole, ...]:
        with self._sessions() as session:
            roles = session.scalars(select(RoleRecord).order_by(RoleRecord.name)).all()
            return tuple(self._managed_role(session, role) for role in roles)

    def create_role(
        self, name: str, description: str | None, privileges: tuple[str, ...]
    ) -> ManagedRole:
        clean_name = name.strip()
        clean_privileges = self._validate_privileges(privileges)
        with self._sessions() as session:
            if session.scalar(select(RoleRecord).where(RoleRecord.name == clean_name)) is not None:
                raise RoleAlreadyExistsError("role name is already in use")
            role = RoleRecord(id=str(uuid4()), name=clean_name, description=description, protected=False)
            session.add(role)
            session.flush()
            session.add_all(
                RolePrivilegeRecord(role_id=role.id, privilege_key=key)
                for key in clean_privileges
            )
            session.commit()
            return self._managed_role(session, role)

    def update_role(
        self, role_id: str, name: str, description: str | None, privileges: tuple[str, ...]
    ) -> ManagedRole:
        clean_privileges = self._validate_privileges(privileges)
        with self._sessions() as session:
            role = session.get(RoleRecord, role_id)
            if role is None:
                raise RoleNotFoundError("role not found")
            if role.protected:
                raise RoleProtectedError("protected roles cannot be edited")
            if session.scalar(
                select(RoleRecord).where(RoleRecord.name == name.strip(), RoleRecord.id != role_id)
            ) is not None:
                raise RoleAlreadyExistsError("role name is already in use")
            if self._is_administrator(role_id, session) and not self._is_full_privilege_set(clean_privileges):
                raise LastAdministratorError("at least one administrator role must retain full access")
            role.name = name.strip()
            role.description = description
            session.execute(delete(RolePrivilegeRecord).where(RolePrivilegeRecord.role_id == role_id))
            session.add_all(
                RolePrivilegeRecord(role_id=role_id, privilege_key=key) for key in clean_privileges
            )
            session.commit()
            return self._managed_role(session, role)

    def delete_role(self, role_id: str) -> None:
        with self._sessions() as session:
            role = session.get(RoleRecord, role_id)
            if role is None:
                raise RoleNotFoundError("role not found")
            if role.protected:
                raise RoleProtectedError("protected roles cannot be deleted")
            if session.scalar(select(UserRecord.id).where(UserRecord.role_id == role_id)) is not None:
                raise RoleInUseError("role is assigned to one or more users")
            session.delete(role)
            session.commit()

    def list_managed_users(self) -> tuple[ManagedUser, ...]:
        with self._sessions() as session:
            users = session.scalars(select(UserRecord).order_by(UserRecord.username)).all()
            return tuple(self._managed_user(session, user) for user in users)

    def update_user(
        self, user_id: str, *, description: str | None, role_id: str
    ) -> ManagedUser:
        with self._sessions() as session:
            user = session.get(UserRecord, user_id)
            if user is None:
                raise AuthenticationError("user not found")
            if session.get(RoleRecord, role_id) is None:
                raise RoleNotFoundError("role not found")
            if user.role_id != role_id and self._is_administrator(user.role_id, session):
                if self._administrator_count(session) <= 1:
                    raise LastAdministratorError("at least one administrator user is required")
            user.description = description
            user.role_id = role_id
            session.commit()
            return self._managed_user(session, user)

    def delete_user(self, user_id: str, *, requesting_user_id: str) -> None:
        with self._sessions() as session:
            user = session.get(UserRecord, user_id)
            if user is None:
                raise AuthenticationError("user not found")
            if user.username == DEFAULT_ADMIN_USERNAME:
                raise ProtectedUserError("the default admin account cannot be deleted")
            if user.id == requesting_user_id:
                raise ProtectedUserError("the signed-in account cannot be deleted")
            if self._is_administrator(user.role_id, session) and self._administrator_count(session) <= 1:
                raise LastAdministratorError("at least one administrator user is required")
            session.execute(delete(SessionRecord).where(SessionRecord.user_id == user_id))
            session.delete(user)
            session.commit()

    def create_session(self, user_id: str) -> SessionRecord:
        session_id = secrets.token_urlsafe(48)
        record = SessionRecord(
            id=session_id,
            user_id=user_id,
            expires_at=_utcnow() + SESSION_TTL,
            revoked_at=None,
        )
        with self._sessions() as session:
            session.add(record)
            session.commit()
            return record

    def resolve_session(self, session_id: str) -> AuthenticatedUser:
        """Return the authenticated user for a session id, or raise."""
        with self._sessions() as session:
            record = session.get(SessionRecord, session_id)
            if record is None:
                raise SessionNotFoundError("session is not valid")
            if record.revoked_at is not None:
                raise SessionNotFoundError("session has been revoked")
            if record.expires_at <= _utcnow():
                raise SessionNotFoundError("session has expired")
            user = session.get(UserRecord, record.user_id)
            if user is None:
                raise SessionNotFoundError("session user no longer exists")
            default_credentials = self._is_default_hash(user.password_hash)
            return self._authenticated_user(session, user, default_credentials=default_credentials)

    def revoke_session(self, session_id: str) -> None:
        with self._sessions() as session:
            record = session.get(SessionRecord, session_id)
            if record is None:
                raise SessionNotFoundError("session is not valid")
            if record.revoked_at is None:
                record.revoked_at = _utcnow()
                session.commit()

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        keep_session_id: str | None = None,
    ) -> None:
        """Change a user's password, requiring the current password."""
        with self._sessions() as session:
            user = session.get(UserRecord, user_id)
            if user is None:
                raise AuthenticationError("user not found")
            try:
                self._hasher.verify(user.password_hash, current_password)
            except (VerifyMismatchError, InvalidHashError) as error:
                raise AuthenticationError("current password is incorrect") from error
            user.password_hash = self._hasher.hash(new_password)
            now = _utcnow()
            statement = select(SessionRecord).where(
                SessionRecord.user_id == user_id,
                SessionRecord.revoked_at.is_(None),
            )
            for record in session.scalars(statement):
                if record.id != keep_session_id:
                    record.revoked_at = now
            session.commit()

    def _purge_inactive_sessions(self, session: Session) -> None:
        """Remove expired and revoked sessions during application startup."""
        from sqlalchemy import delete

        session.execute(
            delete(SessionRecord).where(
                (SessionRecord.expires_at <= _utcnow())
                | SessionRecord.revoked_at.is_not(None)
            )
        )

    def is_default_credentials(self, user_id: str) -> bool:
        with self._sessions() as session:
            user = session.get(UserRecord, user_id)
            if user is None:
                return False
            return self._is_default_hash(user.password_hash)

    def _is_default_hash(self, password_hash: str) -> bool:
        try:
            return self._hasher.verify(password_hash, DEFAULT_ADMIN_PASSWORD)
        except (VerifyMismatchError, InvalidHashError):
            return False

    def _authenticated_user(
        self,
        session: Session,
        user: UserRecord,
        *,
        default_credentials: bool | None = None,
    ) -> AuthenticatedUser:
        return AuthenticatedUser(
            id=user.id,
            username=user.username,
            default_credentials=(
                self._is_default_hash(user.password_hash)
                if default_credentials is None
                else default_credentials
            ),
            description=user.description,
            role_id=user.role_id,
            role_name=self._role_name(session, user.role_id),
            privileges=self._privileges(session, user.role_id),
        )

    def _role_name(self, session: Session, role_id: str) -> str:
        role = session.get(RoleRecord, role_id)
        return role.name if role is not None else "Unknown"

    def _managed_role(self, session: Session, role: RoleRecord) -> ManagedRole:
        privilege_statement = select(RolePrivilegeRecord.privilege_key).where(
            RolePrivilegeRecord.role_id == role.id
        )
        user_count = session.scalar(
            select(func.count()).select_from(UserRecord).where(UserRecord.role_id == role.id)
        ) or 0
        return ManagedRole(
            id=role.id,
            name=role.name,
            description=role.description,
            protected=role.protected,
            privileges=tuple(sorted(session.scalars(privilege_statement).all())),
            user_count=user_count,
        )

    def _managed_user(self, session: Session, user: UserRecord) -> ManagedUser:
        return ManagedUser(
            id=user.id,
            username=user.username,
            description=user.description,
            role_id=user.role_id,
            role_name=self._role_name(session, user.role_id),
            default_credentials=self._is_default_hash(user.password_hash),
        )

    def _validate_privileges(self, privileges: tuple[str, ...]) -> tuple[str, ...]:
        clean = tuple(dict.fromkeys(privileges))
        if len(clean) != len(privileges) or not set(clean).issubset(PRIVILEGE_KEYS):
            raise InvalidPrivilegesError("privileges must be known and unique")
        return clean

    def _is_full_privilege_set(self, privileges: tuple[str, ...]) -> bool:
        return set(privileges) == set(PRIVILEGE_KEYS)

    def _is_administrator(self, role_id: str, session: Session) -> bool:
        role = session.get(RoleRecord, role_id)
        if role is not None and role.protected:
            return True
        privileges = set(session.scalars(
            select(RolePrivilegeRecord.privilege_key).where(RolePrivilegeRecord.role_id == role_id)
        ).all())
        return self._is_full_privilege_set(tuple(privileges))

    def _administrator_count(self, session: Session) -> int:
        return sum(self._is_administrator(user.role_id, session) for user in session.scalars(select(UserRecord)))

    def _privileges(self, session: Session, role_id: str) -> frozenset[str]:
        statement = (
            select(PrivilegeRecord.key)
            .join(RolePrivilegeRecord, RolePrivilegeRecord.privilege_key == PrivilegeRecord.key)
            .where(RolePrivilegeRecord.role_id == role_id)
        )
        return frozenset(session.scalars(statement).all())