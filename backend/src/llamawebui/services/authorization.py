"""Stable control-plane privilege names and request classification."""

# The catalog entries are intentionally kept readable as one row per resource.
# ruff: noqa: E501

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PrivilegeDefinition:
    key: str
    name: str
    description: str
    resource: str
    access: str


def _privilege(resource: str, access: str, name: str, description: str) -> PrivilegeDefinition:
    return PrivilegeDefinition(f"{resource}.{access}", name, description, resource, access)


PRIVILEGES: tuple[PrivilegeDefinition, ...] = tuple(
    privilege
    for resource, name, description, read, write in (
        ("auth", "Authentication", "Control-plane accounts and sessions", "View account information", "Manage accounts and passwords"),
        ("server", "Server", "Managed llama.cpp router", "View server state and models", "Control the router and model loading"),
        ("tokens", "Access tokens", "Native llama.cpp bearer tokens", "View access-token metadata", "Create and revoke access tokens"),
        ("runtimes", "Runtimes", "Registered llama.cpp runtimes", "View runtimes and releases", "Install, register, probe, and remove runtimes"),
        ("profiles", "Profiles", "Model launch profiles", "View and export profiles", "Create and change model profiles"),
        ("huggingface", "Hugging Face", "Model repository discovery", "Search repositories and manifests", "No write operations currently exist"),
        ("downloads", "Downloads", "Model downloads", "View download jobs", "Create and control downloads"),
        ("library", "Library", "Local model library", "View local models", "Reconcile, import, and delete library records"),
        ("diagnostics", "Diagnostics", "Application diagnostics", "View diagnostic information", "Export diagnostics"),
        ("integrations", "Integrations", "Client integration helpers", "View generated client configuration", "No write operations currently exist"),
        ("settings", "Settings", "Application settings", "View application settings", "Change application settings"),
    )
    for privilege in (
        _privilege(resource, "read", f"{name} read", read),
        _privilege(resource, "write", f"{name} write", write),
    )
)

PRIVILEGE_KEYS = frozenset(item.key for item in PRIVILEGES)


def privilege_for_request(path: str, method: str) -> str | None:
    """Return the privilege required by an authenticated control-plane request."""
    if path in {"/api/health", "/api/auth/login", "/api/auth/logout"}:
        return None
    if path.startswith("/api/auth/"):
        resource = "auth"
    elif path.startswith("/api/server/"):
        resource = "server"
    elif path.startswith("/api/tokens"):
        resource = "tokens"
    elif path.startswith("/api/runtimes"):
        resource = "runtimes"
    elif path.startswith("/api/profiles"):
        resource = "profiles"
    elif path.startswith("/api/huggingface/"):
        resource = "huggingface"
    elif path.startswith("/api/downloads"):
        resource = "downloads"
    elif path.startswith("/api/library"):
        resource = "library"
    elif path.startswith("/api/diagnostics/"):
        resource = "diagnostics"
    elif path.startswith("/api/integrations/"):
        resource = "integrations"
    elif path.startswith("/api/settings"):
        resource = "settings"
    elif path.startswith("/api/events"):
        resource = "server"
    else:
        # Keep newly added authenticated endpoints closed until classified.
        return "__unclassified__"
    access = "read" if method in {"GET", "HEAD", "OPTIONS"} else "write"
    return f"{resource}.{access}"