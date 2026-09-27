"""Manage token metadata and llama.cpp's native API-key file."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

import keyring
from sqlalchemy import Engine, select
from sqlalchemy.orm import sessionmaker

from llamawebui.models import AccessTokenRecord


class AccessTokenNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class CreatedAccessToken:
    record: AccessTokenRecord
    token: str


class TokenRegistry:
    def __init__(self, engine: Engine, data_dir: Path) -> None:
        self._sessions = sessionmaker(engine, expire_on_commit=False)
        self.key_file = data_dir / "generated" / "api-keys.txt"
        self._hash_key_file = data_dir / "generated" / "token-hash.key"
        self._lock = RLock()
        self._materialized = False
        self._keyring_service = "llamawebui.native-api-key"

    def reconcile_legacy_materialized_tokens(self) -> None:
        """Move an older plaintext key file into credential storage at startup."""
        with self._lock:
            legacy_tokens = self._read_tokens()
            if not legacy_tokens:
                return
            with self._sessions() as session:
                records = session.scalars(
                    select(AccessTokenRecord).where(AccessTokenRecord.enabled.is_(True))
                ).all()
            for token in legacy_tokens:
                for record in records:
                    if hmac.compare_digest(self._hash(token), record.token_hash):
                        keyring.set_password(self._keyring_service, record.id, token)
                        break
            self.key_file.unlink(missing_ok=True)

    def list(self) -> list[AccessTokenRecord]:
        with self._sessions() as session:
            statement = select(AccessTokenRecord).order_by(
                AccessTokenRecord.created_at, AccessTokenRecord.id
            )
            return list(session.scalars(statement))

    def create(self, name: str, expiry_note: str | None = None) -> CreatedAccessToken:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("token name must not be empty")
        token = f"lwui_{secrets.token_urlsafe(32)}"
        record = AccessTokenRecord(
            id=str(uuid4()),
            name=clean_name,
            token_hash=self._hash(token),
            last_four=token[-4:],
            expiry_note=expiry_note.strip() if expiry_note and expiry_note.strip() else None,
            enabled=True,
            revoked_at=None,
        )
        with self._lock:
            keyring.set_password(self._keyring_service, record.id, token)
            try:
                with self._sessions() as session:
                    session.add(record)
                    session.commit()
            except Exception:
                keyring.delete_password(self._keyring_service, record.id)
                raise
            if self._materialized:
                self._materialize_locked((token,))
        return CreatedAccessToken(record, token)

    def revoke(self, token_id: str) -> AccessTokenRecord:
        with self._lock, self._sessions() as session:
            record = session.get(AccessTokenRecord, token_id)
            if record is None:
                raise AccessTokenNotFoundError(f"access token not found: {token_id}")
            if not record.enabled:
                return record
            record.enabled = False
            record.revoked_at = datetime.now()
            session.commit()
            with suppress(keyring.errors.PasswordDeleteError):
                keyring.delete_password(self._keyring_service, record.id)
            if self._materialized:
                self._materialize_locked()
            return record

    def has_enabled(self) -> bool:
        with self._sessions() as session:
            return (
                session.scalar(
                    select(AccessTokenRecord.id).where(
                        AccessTokenRecord.enabled.is_(True)
                    )
                )
                is not None
            )

    def control_token(self) -> str | None:
        with self._lock:
            tokens = self._read_tokens()
            return tokens[0] if tokens else None

    def materialize_enabled_tokens(self) -> None:
        """Write active raw keys only while the native router needs them."""
        with self._lock:
            self._materialized = True
            self._materialize_locked()

    def remove_materialized_tokens(self) -> None:
        """Remove the native key file after the router is no longer running."""
        with self._lock:
            self._materialized = False
            self.key_file.unlink(missing_ok=True)

    def _materialize_locked(self, additional_tokens: tuple[str, ...] = ()) -> None:
        with self._sessions() as session:
            enabled_ids = tuple(
                session.scalars(
                    select(AccessTokenRecord.id).where(AccessTokenRecord.enabled.is_(True))
                )
            )
        tokens = tuple(
            token
            for token_id in enabled_ids
            if (token := keyring.get_password(self._keyring_service, token_id)) is not None
        )
        tokens += additional_tokens
        self._write_tokens(tokens)

    def _hash(self, token: str) -> str:
        return hmac.new(self._hash_key(), token.encode(), hashlib.sha256).hexdigest()

    def _hash_key(self) -> bytes:
        if not self._hash_key_file.exists():
            self._write_restricted(self._hash_key_file, secrets.token_bytes(32))
        return self._hash_key_file.read_bytes()

    def _read_tokens(self) -> tuple[str, ...]:
        if not self.key_file.exists():
            return ()
        return tuple(
            line
            for line in self.key_file.read_text(encoding="utf-8").splitlines()
            if line
        )

    def _write_tokens(self, tokens: tuple[str, ...]) -> None:
        payload = "".join(f"{token}\n" for token in tokens).encode()
        self._write_restricted(self.key_file, payload)

    @staticmethod
    def _write_restricted(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o600)
            temporary.replace(path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)