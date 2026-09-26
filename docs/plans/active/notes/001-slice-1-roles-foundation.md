# Slice 1 — Roles foundation

**Status:** Complete  
**Date:** 2026-09-26

## Implemented

- Added `roles`, `privileges`, and `role_privileges` persistence tables.
- Added optional user descriptions and role assignments.
- Added migration `0010_roles_privileges` with SQLite-safe batch alteration.
- Seeded the protected `Administrator` role with the complete initial privilege catalog.
- Assigned the existing/default admin account to Administrator.
- Added session payload role and privilege information.
- Added centralized authenticated API request classification with separate read/write privilege keys.
- Unclassified authenticated `/api` routes fail closed with HTTP 403.
- Updated schema and diagnostics expectations.

## Validation

- Focused authentication tests: **14 passed**.
- Full backend tests: **297 passed, 1 skipped**.
- Ruff: **passed**.
- Mypy: **passed** for 53 source files.
- `git diff --check`: **passed**.

The expected Starlette/httpx deprecation warnings remain; they do not fail the suite.

## Manual acceptance

Fresh and existing test databases were exercised through the application startup migration. Repeated startup confirmed the Administrator seed and privilege set are idempotent.

## Next action

Begin Slice 2: implement role/user management APIs, privilege assignment validation, protected-role safeguards, and user description/role editing.

**Suggested commit message:** `feat(auth): add role privilege persistence and authorization`
