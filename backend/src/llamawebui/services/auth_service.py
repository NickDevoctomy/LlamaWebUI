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
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from llamawebui.models import SessionRecord, UserRecord

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


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: str
    username: str
    default_credentials: bool


class AuthService:
    def __init__(self, engine: Engine) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self._hasher = PasswordHasher()

    def ensure_default_admin(self) -> None:
        """Seed the default ``admin`` / ``admin`` account on first run."""
        with self._sessions() as session:
            self._purge_inactive_sessions(session)
            existing = session.scalar(select(UserRecord.id).limit(1))
            if existing is not None:
                return
            session.add(
                UserRecord(
                    id=str(uuid4()),
                    username=DEFAULT_ADMIN_USERNAME,
                    password_hash=self._hasher.hash(DEFAULT_ADMIN_PASSWORD),
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
                AuthenticatedUser(
                    id=user.id,
                    username=user.username,
                    default_credentials=self._is_default_hash(user.password_hash),
                )
                for user in users
            )

    def create_user(self, username: str, password: str) -> AuthenticatedUser:
        clean_username = username.strip()
        with self._sessions() as session:
            existing = session.scalar(
                select(UserRecord).where(UserRecord.username == clean_username)
            )
            if existing is not None:
                raise UserAlreadyExistsError("username is already in use")
            user = UserRecord(
                id=str(uuid4()),
                username=clean_username,
                password_hash=self._hasher.hash(password),
            )
            session.add(user)
            session.commit()
            return AuthenticatedUser(
                id=user.id,
                username=user.username,
                default_credentials=False,
            )

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
            return AuthenticatedUser(
                id=user.id,
                username=user.username,
                default_credentials=default_credentials,
            )

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