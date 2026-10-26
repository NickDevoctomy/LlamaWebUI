"""Keep control-plane secrets in the operating system's credential store.

keyring picks the platform store: Windows Credential Manager, macOS Keychain
or the Linux Secret Service.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

import keyring
import keyring.errors

SECRET_SERVICE = "llamawebui.secret"
# Windows Credential Manager caps a secret at 2560 bytes and keyring stores UTF-16.
MAX_SECRET_LENGTH = 1024
_STORE_NAMES = {
    "keyring.backends.Windows": "Windows Credential Manager",
    "keyring.backends.macOS": "macOS Keychain",
    "keyring.backends.SecretService": "Secret Service",
    "keyring.backends.libsecret": "Secret Service",
    "keyring.backends.kwallet": "KWallet",
}


class SecretName(StrEnum):
    HUGGINGFACE_TOKEN = "huggingface-token"
    OPENROUTER_API_KEY = "openrouter-api-key"


class SecretStoreUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SecretStatus:
    configured: bool
    last_four: str | None


class SecretStore:
    def __init__(self, service: str = SECRET_SERVICE) -> None:
        self._service = service

    def get(self, name: SecretName) -> str | None:
        return self._call(lambda: keyring.get_password(self._service, name.value))

    def set(self, name: SecretName, value: str) -> SecretStatus:
        clean = value.strip()
        if not clean:
            raise ValueError("secret must not be empty")
        if len(clean) > MAX_SECRET_LENGTH:
            raise ValueError(f"secret must be at most {MAX_SECRET_LENGTH} characters")
        if any(character.isspace() or not character.isprintable() for character in clean):
            raise ValueError("secret must not contain spaces or control characters")
        self._call(lambda: keyring.set_password(self._service, name.value, clean))
        return _status(clean)

    def delete(self, name: SecretName) -> bool:
        """Remove the secret and report whether one was stored."""
        if self.get(name) is None:
            return False
        try:
            self._call(lambda: keyring.delete_password(self._service, name.value))
        except SecretStoreUnavailableError as error:
            # Another caller removed it between the read and the delete.
            if isinstance(error.__cause__, keyring.errors.PasswordDeleteError):
                return False
            raise
        return True

    def status(self, name: SecretName) -> SecretStatus:
        return _status(self.get(name))

    @staticmethod
    def store_name() -> str:
        """Name the credential store keyring picked, for display."""
        backend = type(keyring.get_keyring())
        module = backend.__module__
        return next(
            (label for prefix, label in _STORE_NAMES.items() if module.startswith(prefix)),
            backend.__name__,
        )

    @staticmethod
    def _call[T](operation: Callable[[], T]) -> T:
        try:
            return operation()
        except keyring.errors.KeyringError as error:
            raise SecretStoreUnavailableError(
                f"the system credential store is unavailable ({type(error).__name__})"
            ) from error


def _status(value: str | None) -> SecretStatus:
    if not value:
        return SecretStatus(configured=False, last_four=None)
    return SecretStatus(configured=True, last_four=value[-4:])
