from __future__ import annotations

from collections.abc import Callable

import keyring
import keyring.errors
import pytest

from llamawebui.services.secret_store import (
    MAX_SECRET_LENGTH,
    SECRET_SERVICE,
    SecretName,
    SecretStatus,
    SecretStore,
    SecretStoreUnavailableError,
)


def _raise(error: Exception) -> Callable[..., None]:
    def fail(*_args: object) -> None:
        raise error

    return fail


def test_set_get_and_status_round_trip() -> None:
    store = SecretStore()

    status = store.set(SecretName.HUGGINGFACE_TOKEN, "  hf_abcdefgh1234  ")

    assert status == SecretStatus(configured=True, last_four="1234")
    assert store.get(SecretName.HUGGINGFACE_TOKEN) == "hf_abcdefgh1234"
    assert keyring.get_password(SECRET_SERVICE, "huggingface-token") == "hf_abcdefgh1234"
    assert store.status(SecretName.HUGGINGFACE_TOKEN) == status
    assert "hf_abcdefgh1234" not in repr(status)


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("keyring.backends.Windows", "Windows Credential Manager"),
        ("keyring.backends.macOS", "macOS Keychain"),
        ("keyring.backends.SecretService", "Secret Service"),
        ("somewhere.else", "CustomKeyring"),
    ],
)
def test_store_name_describes_the_backend(
    monkeypatch: pytest.MonkeyPatch, module: str, expected: str
) -> None:
    backend = type("CustomKeyring", (), {"__module__": module})
    monkeypatch.setattr(keyring, "get_keyring", backend)

    assert SecretStore.store_name() == expected


def test_status_when_missing() -> None:
    assert SecretStore().status(SecretName.OPENROUTER_API_KEY) == SecretStatus(False, None)


def test_secrets_are_stored_independently() -> None:
    store = SecretStore()
    store.set(SecretName.HUGGINGFACE_TOKEN, "hf_one")
    store.set(SecretName.OPENROUTER_API_KEY, "sk-or-two")

    assert store.get(SecretName.HUGGINGFACE_TOKEN) == "hf_one"
    assert store.get(SecretName.OPENROUTER_API_KEY) == "sk-or-two"
    assert store.delete(SecretName.HUGGINGFACE_TOKEN)
    assert store.get(SecretName.OPENROUTER_API_KEY) == "sk-or-two"


def test_service_name_can_be_overridden() -> None:
    SecretStore("other.service").set(SecretName.HUGGINGFACE_TOKEN, "hf_other")

    assert SecretStore().get(SecretName.HUGGINGFACE_TOKEN) is None


@pytest.mark.parametrize(
    "value",
    ["", "   ", "hf abc", "hf\tabc", "hf\x00abc", "x" * (MAX_SECRET_LENGTH + 1)],
)
def test_set_rejects_invalid_values(value: str) -> None:
    store = SecretStore()

    with pytest.raises(ValueError):
        store.set(SecretName.HUGGINGFACE_TOKEN, value)
    assert store.get(SecretName.HUGGINGFACE_TOKEN) is None


def test_set_accepts_the_maximum_length() -> None:
    value = "x" * MAX_SECRET_LENGTH

    SecretStore().set(SecretName.OPENROUTER_API_KEY, value)

    assert SecretStore().get(SecretName.OPENROUTER_API_KEY) == value


def test_delete_reports_whether_a_secret_was_stored() -> None:
    store = SecretStore()
    store.set(SecretName.OPENROUTER_API_KEY, "sk-or-key")

    assert store.delete(SecretName.OPENROUTER_API_KEY) is True
    assert store.delete(SecretName.OPENROUTER_API_KEY) is False
    assert store.get(SecretName.OPENROUTER_API_KEY) is None


def test_delete_tolerates_a_concurrent_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SecretStore()
    store.set(SecretName.OPENROUTER_API_KEY, "sk-or-key")
    monkeypatch.setattr(
        keyring, "delete_password", _raise(keyring.errors.PasswordDeleteError("gone"))
    )

    assert store.delete(SecretName.OPENROUTER_API_KEY) is False


def test_delete_raises_when_the_store_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    store = SecretStore()
    store.set(SecretName.OPENROUTER_API_KEY, "sk-or-key")
    monkeypatch.setattr(keyring, "delete_password", _raise(keyring.errors.KeyringError("locked")))

    with pytest.raises(SecretStoreUnavailableError):
        store.delete(SecretName.OPENROUTER_API_KEY)


@pytest.mark.parametrize("operation", ["get", "set", "status"])
def test_missing_backend_is_reported_without_the_value(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    error = _raise(keyring.errors.NoKeyringError("no backend"))
    monkeypatch.setattr(keyring, "get_password", error)
    monkeypatch.setattr(keyring, "set_password", error)
    store = SecretStore()

    with pytest.raises(SecretStoreUnavailableError) as raised:
        if operation == "set":
            store.set(SecretName.HUGGINGFACE_TOKEN, "hf_secretvalue")
        else:
            getattr(store, operation)(SecretName.HUGGINGFACE_TOKEN)

    assert "NoKeyringError" in str(raised.value)
    assert "hf_secretvalue" not in str(raised.value)
    assert isinstance(raised.value.__cause__, keyring.errors.NoKeyringError)
