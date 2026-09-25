"""Manage token metadata and llama.cpp's native API-key file."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

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
        self._write_tokens((*self._read_tokens(), token))
        try:
            with self._sessions() as session:
                session.add(record)
                session.commit()
        except Exception:
            self._write_tokens(self._read_tokens()[:-1])
            raise
        return CreatedAccessToken(record, token)

    def revoke(self, token_id: str) -> AccessTokenRecord:
        with self._sessions() as session:
            record = session.get(AccessTokenRecord, token_id)
            if record is None:
                raise AccessTokenNotFoundError(f"access token not found: {token_id}")
            if not record.enabled:
                return record
            tokens = tuple(
                token
                for token in self._read_tokens()
                if not hmac.compare_digest(self._hash(token), record.token_hash)
            )
            self._write_tokens(tokens)
            record.enabled = False
            record.revoked_at = datetime.now()
            session.commit()
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
        tokens = self._read_tokens()
        return tokens[0] if tokens else None

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