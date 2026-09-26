# Control-plane login delivery

## Scope

Completed the three delivery slices from `docs/tech-debt/control-plane-login.md`:

- Backend accounts, Argon2id password hashes, DB-backed sessions, login/logout, auth gate, CSRF protection, stale-session cleanup, and session revocation on password change.
- Frontend login/logout, signed-in sidebar identity, default-credential warning, password change, unauthenticated state, and Users tab for creating additional administrator accounts.
- HTTPS documentation for non-loopback control-plane access, full quality gates, and browser acceptance.

All currently created accounts have administrator access. Role separation, user deletion, and per-user password administration remain out of scope.

## Validation

- Backend focused auth tests: 13 passed.
- Backend full suite: 280 passed, 1 skipped.
- Backend branch coverage: 90.41%, above the configured 85% threshold.
- Ruff: passed.
- Mypy: passed.
- Frontend tests: 27 passed.
- Frontend production build: passed.
- `git diff --check`: passed.

## Browser acceptance

Using the local development services at `http://127.0.0.1:5173/`:

1. Unauthenticated navigation showed the login screen and default `admin / admin` guidance.
2. Logging in as `admin` displayed the signed-in username and `Default password active` warning in the sidebar.
3. The Users tab displayed existing accounts and administrator status.
4. The Add user dialog accepted a new username and password, then refreshed the list with the new administrator account.
5. No password or password hash was displayed.

A test account named `reviewer` was created during acceptance in the existing local development data directory. It was an intentional local state mutation; no token, model, or download was created.

## Documentation

`README.md` now states that credentials and session cookies must be transported over HTTPS whenever the control plane is reachable beyond loopback. Runtime HTTPS enforcement remains deliberately out of scope for this local-first slice.

Suggested commit message: `feat: complete control-plane login delivery`
