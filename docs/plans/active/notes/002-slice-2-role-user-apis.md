# Slice 2 — Role and user management APIs

**Status:** Complete  
**Date:** 2026-09-26

## Implemented

- Added role listing, creation, update, and deletion APIs.
- Added user creation fields for optional description and role assignment.
- Added user update API for description and role changes.
- Added role privilege validation, duplicate rejection, and unknown-key rejection.
- Protected Administrator roles cannot be edited or deleted.
- Assigned roles cannot be deleted.
- Prevented removing the last administrator-capable user or role.
- Authorization is resolved from persistence on every request, so role privilege changes take effect without restart.
- Added role and user payloads for frontend consumption.

## Validation

- Focused Slice 2 auth/role tests: **16 passed**.
- Full backend tests: **299 passed, 1 skipped**.
- Ruff: **passed**.
- Mypy: **passed**.
- `git diff --check`: **passed**.

The existing Starlette/httpx deprecation warnings remain and do not fail the suite.

## Next action

Begin Slice 3: add the Roles tab, privilege editor, and Users role/description editing workflow.

**Suggested commit message:** `feat(auth): add role and user management APIs`
