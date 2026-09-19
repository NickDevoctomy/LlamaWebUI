from pathlib import Path

import pytest

from llamawebui.database import create_database_engine, upgrade_database
from llamawebui.services.token_registry import AccessTokenNotFoundError, TokenRegistry


def registry(tmp_path: Path) -> TokenRegistry:
    database = tmp_path / "app.db"
    upgrade_database(database)
    return TokenRegistry(create_database_engine(database), tmp_path)


def test_token_is_returned_once_and_only_hash_is_persisted(tmp_path: Path) -> None:
    tokens = registry(tmp_path)
    created = tokens.create("OpenCode")

    assert created.token.startswith("lwui_")
    assert created.record.last_four == created.token[-4:]
    assert created.token in tokens.key_file.read_text(encoding="utf-8")
    listed = tokens.list()
    assert listed[0].name == "OpenCode"
    assert listed[0].token_hash != created.token
    assert created.token not in repr(listed[0].__dict__)
    assert tokens.has_enabled()


def test_revoke_removes_plaintext_and_is_idempotent(tmp_path: Path) -> None:
    tokens = registry(tmp_path)
    first = tokens.create("First")
    second = tokens.create("Second")

    revoked = tokens.revoke(first.record.id)
    revoked_again = tokens.revoke(first.record.id)

    assert not revoked.enabled
    assert revoked.revoked_at is not None
    assert not revoked_again.enabled
    contents = tokens.key_file.read_text(encoding="utf-8")
    assert first.token not in contents
    assert second.token in contents
    assert tokens.has_enabled()


def test_registry_validates_name_and_missing_token(tmp_path: Path) -> None:
    tokens = registry(tmp_path)
    with pytest.raises(ValueError, match="name"):
        tokens.create(" ")
    with pytest.raises(AccessTokenNotFoundError):
        tokens.revoke("missing")