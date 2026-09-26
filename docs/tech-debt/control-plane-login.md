# Control-plane accounts and login

## Goal

The LlamaWebUI control API currently has no authentication. It binds to loopback by default, but any client that can reach it can use management endpoints, including creating inference access keys. Add local account login and in-UI user management so the control plane can be used safely by its intended users rather than relying on network location alone.

## Implementation plan

1. Add persistent user accounts with unique usernames and salted Argon2id password hashes using a maintained library. Never store or log plaintext passwords. Start with a single default administrator account, `admin` / `admin`. Do not provide open registration.
2. Add login and logout with server-validated sessions. Require an authenticated session for every control-plane API, including token management and event streams. Keep these accounts and sessions separate from llama.cpp inference bearer keys.
3. Add a User Management screen in the UI for administrators to change the admin password. Include login/logout UI and clear unauthenticated/unauthorized states. Show the signed-in username and logout button at the bottom left of the application sidebar, directly above the “Local control plane / version” section. When the default `admin` / `admin` credentials are still in use, show a clear warning beside the username/logout controls; do not force a password change. Do not expose password hashes or existing passwords; password changes set a new password.
4. Use secure, HttpOnly, SameSite cookies and CSRF protection for state-changing requests. Require HTTPS for non-loopback access; password hashing does not secure credentials sent over plaintext HTTP.

The OpenAI-compatible inference endpoint is explicitly out of scope for user-account authentication. It continues to be protected by the existing inference access tokens; management login must not replace or be required in addition to those tokens for inference clients.

## Decisions (confirmed 2026-09-26)

- **Argon2id library:** `argon2-cffi` (established, maintained). Added to `backend/requirements.txt` and `backend/pyproject.toml` `dependencies`.
- **Session storage:** DB-backed server sessions (rows in SQLite, revocable, matches "server-validated sessions"). A session cookie carries only an opaque random session ID; the server looks up the session row and its owning user on each request.
- **CSRF strategy:** Custom-header requirement. All state-changing (non-GET/HEAD/OPTIONS) control-plane requests must include a custom header (e.g. `X-Requested-With: LlamaWebUI`). Same-origin SPA requests always send it; cross-origin browsers cannot set it without CORS preflight, which the API does not allow. This is simpler and more robust than a double-submit token for a same-origin SPA.
- **HTTPS enforcement:** Documentation only in this slice. Add a note to the plan/README that non-loopback access must be served over HTTPS; no runtime enforcement is added now (loopback is the default deployment).
- **Delivery:** Three small slices, each a separate commit:
  1. Backend auth (migration, Argon2id, sessions, login/logout, auth gate, CSRF).
  2. Frontend login UI (login/logout, sidebar user, password change, unauthenticated/unauthorized states).
  3. Docs (HTTPS note), full quality gates, browser acceptance, progress notes.

## Architecture notes

- **Models:** Add `UserRecord` (`users` table: id, username unique, password_hash, created_at, updated_at) and `SessionRecord` (`sessions` table: id, user_id FK, created_at, expires_at, revoked_at). Add Alembic migration `0009_users_sessions.py` (down_revision `0008_logical_model_profiles`).
- **Default admin:** On first run, seed `admin` / `admin` if no users exist. Do not force a password change. No open registration endpoint.
- **Password hashing:** `argon2-cffi` `PasswordHasher` (Argon2id). Store only the encoded hash. Never log or return hashes or plaintext.
- **Session cookie:** HttpOnly, SameSite=Lax, `secure` only when the request is HTTPS (loopback HTTP default stays usable). Opaque random session ID; server-validated against the `sessions` table.
- **Auth gate:** A FastAPI dependency applied to every control-plane route (including `/api/events` and token management) that rejects unauthenticated requests with `401`. `/api/health` and the login/logout endpoints remain public. Inference endpoints are untouched and continue to use bearer access tokens.
- **CSRF:** A dependency on all state-changing routes requiring the custom header; returns `403` when absent.
- **Password change:** Only the signed-in `admin` can change the admin password. Requires the current password; sets a new password (new hash). Never returns the hash or existing password.
- **Frontend:** Add login/logout UI, a User Management screen (password change), and show the signed-in username + logout button at the bottom-left of the sidebar directly above the "Local control plane / version" section. Show a warning while default `admin` / `admin` credentials are active. Represent loading, empty, success, error, disabled, confirmation, and destructive states.
- **Additional users:** The Users tab now lists accounts and lets an authenticated administrator create additional administrator accounts. Role separation, user deletion, and per-user password administration remain out of scope; all current accounts have administrator access.

## Acceptance

- First run provides the default `admin` / `admin` administrator credentials without forcing a password change; no unauthenticated account-creation endpoint is available.
- The sidebar shows the signed-in username and logout control in the specified bottom-left position. A warning is visible while default credentials remain active.
- The UI allows the administrator to change the admin password. Initially, only the `admin` account/role exists; additional users, roles, and permissions are not in scope.
- Unauthenticated clients cannot read or change control-plane state. Login, logout, and authorized UI workflows work, while the default loopback deployment remains usable.
- OpenAI-compatible inference requests continue to authenticate with access tokens and do not require a LlamaWebUI user session.
- Passwords are stored only as Argon2id hashes. No OIDC, external identity provider, public registration, additional roles, or fine-grained permissions are required.