# Roles and privileges implementation plan

**Status:** Proposed, not started  
**Plan date:** 2026-09-26  
**Scope:** Three implementation slices for control-plane user roles, API authorization, and the Users/Roles UI.

## 1. Goal and boundaries

Add straightforward role-based authorization to the FastAPI control plane without introducing a separate policy engine or an inference proxy. A signed-in control-plane user has one role. A role has named privileges. Each control-plane API operation is classified as read or write, and the request is allowed only when the current user's role contains the matching privilege.

The feature must provide:

1. A protected `Administrator` role seeded on first run and assigned to the existing `admin` account.
2. A Roles UI tab for listing roles and adding, editing, and deleting non-protected roles, including their privilege assignments.
3. Read/write privileges covering every control-plane API function, with separate read and write grants.
4. User creation with an optional description, plus user editing for description and role assignment.
5. Protection against deleting or modifying protected roles and against leaving the system without an administrator-capable admin account.

Keep llama.cpp bearer-token authorization unchanged. Roles apply to the browser/control-plane session APIs under `/api`; they do not change native llama.cpp endpoint behavior or token scopes.

## 2. Existing seams to reuse

- Login/session identity is already represented by `UserRecord`, `SessionRecord`, `AuthService`, and `_require_session` in `backend/src/llamawebui/app.py`.
- The initial admin account is created by `AuthService.ensure_default_admin()`.
- Existing schema changes use Alembic migrations under `backend/src/llamawebui/migrations/versions/`.
- The frontend already has Users navigation, `api.users`, and `UsersPanel` in `frontend/src/App.tsx`; extend these instead of creating a parallel account surface.
- Control-plane routes are defined in `backend/src/llamawebui/app.py`; keep authorization centralized rather than adding ad hoc checks to individual handlers.
- Existing test conventions are in `backend/tests/test_auth.py`, `test_app.py`, and endpoint-specific tests. Frontend behavior is covered by Vitest/Testing Library in `frontend/src/App.test.tsx` and `frontend/src/test/`.

## 3. Decisions to lock before coding

### 3.1 Role and privilege model

Use a small relational model:

- `roles`: `id`, `name`, `description` nullable, `protected`, timestamps.
- `privileges`: stable `key`, `name`, `description`, `access` (`read` or `write`), and a route/function grouping suitable for display.
- `role_privileges`: role-to-privilege association.
- `users`: add nullable `description` and non-null `role_id` with restrictive deletion behavior.

Use stable privilege keys rather than storing route strings as the authorization contract. Suggested groups are `auth`, `users`, `roles`, `dashboard`, `server`, `models`, `profiles`, `downloads`, `library`, `runtimes`, `huggingface`, `tokens`, `settings`, `diagnostics`, and `integrations`. For each group expose separate `.read` and `.write` privileges where both operations exist. Read-only groups should still have an explicit `.read` privilege; write operations must never imply read access accidentally.

Do not create a privilege for the public health/login/logout endpoints. The session middleware remains responsible for authentication, while authorization protects authenticated control-plane operations.

### 3.2 Protected Administrator behavior

- Seed one protected role named `Administrator` with every defined privilege.
- Assign it to the existing `admin` user during migration/startup reconciliation.
- Make the `admin` account and `Administrator` role protected from deletion.
- Do not allow a protected role to be renamed, edited, or deleted through the API.
- Do not allow a user edit/create/delete operation to remove the last user with an administrator-capable role. If user deletion is not already present, do not add it in this feature unless required by the existing Users workflow.
- New users default to `Administrator` only for backward-compatible creation behavior in the first migration, but the API/UI must make role selection explicit and future-safe.

### 3.3 Authorization semantics

- `read` means safe retrieval/streaming of control-plane state.
- `write` means create/update/delete, lifecycle changes, downloads, imports, model load/unload, password changes, token changes, runtime changes, and diagnostics export.
- Mutating endpoints require the write privilege for their resource group; list/detail/status endpoints require read.
- A request with no matching privilege returns HTTP 403 with a generic message; do not disclose role internals or privilege keys unnecessarily.
- The current user response should include role name and enough privilege information for UI gating, but never password/session/token secrets. Prefer a compact set of granted privilege keys or `{group, access}` values.
- UI hiding/disablement is convenience only; backend authorization is authoritative.
- Keep the existing session middleware and CSRF checks. Apply authorization after session resolution so unauthenticated requests remain 401.

## 4. Slice 1 — Persistence, seeding, and authorization foundation

**Outcome:** The backend has durable roles/privileges, a protected Administrator role, and one centralized way to classify and enforce endpoint access. The existing admin login continues to work with full access.

### Backend work

1. Add SQLAlchemy records for `RoleRecord`, `PrivilegeRecord`, and `RolePrivilegeRecord`; extend `UserRecord` with `description` and `role_id`.
2. Add an Alembic migration after the current head that creates the role/privilege tables, seeds the complete privilege catalog, creates protected `Administrator`, grants all privileges, and assigns existing users to it. Make the migration safe for the current database and reversible where practical.
3. Update startup reconciliation so a partially initialized/older database still obtains the protected Administrator role and any newly introduced privilege rows without duplicating data.
4. Extend `AuthenticatedUser` with role identity and granted privilege information needed by authorization and `/api/auth/me`.
5. Add a small authorization module/service with:
   - canonical privilege catalog;
   - read/write endpoint mapping;
   - `require_privilege(request, key)` and/or a dependency/helper usable by all routes;
   - protected-role and administrator-capability checks.
6. Apply the centralized checks to every authenticated `/api` endpoint. Explicitly audit all routes in `app.py`; do not rely only on the current four user-route checks. Keep `/api/health`, login, and logout public as they are today.
7. Add request models/response payload fields for user description and role metadata, but defer full role-management mutations to Slice 2.

### Tests and acceptance

- Migration from a database containing only the current users/sessions schema creates the role catalog and assigns `admin` to protected Administrator.
- Startup is idempotent and does not duplicate roles or privileges.
- Admin can still call representative read and write endpoints.
- A non-admin fixture/session with a narrow role gets 403 for an ungranted read and an ungranted write, while granted operations succeed.
- Every authenticated route has a classification test or an explicit documented reason for exemption.
- Login, logout, CSRF, session expiry, and existing auth tests remain green.

**Slice gate:** backend full suite, Ruff, and mypy pass; verify the database migration on a fresh database and a copy of the current development schema. No UI work starts until this gate passes.

## 5. Slice 2 — Role and user management APIs

**Outcome:** The backend can safely manage non-protected roles and users, with complete validation and privilege assignment behavior.

### Backend work

1. Add role service/repository operations:
   - list roles with privilege summaries and protected state;
   - create role with unique normalized name and optional description;
   - update name/description/privileges for non-protected roles;
   - delete non-protected roles only when no users are assigned, or return a clear conflict and require reassignment first.
2. Add user service operations:
   - create user with optional description and selected role;
   - edit username only if the existing product permits it, otherwise edit description and role as requested;
   - preserve password semantics and default-credential detection;
   - prevent assigning an invalid role;
   - enforce protected admin/last-administrator invariants.
3. Add explicit endpoints, keeping them under the existing control-plane auth namespace where natural:
   - `GET/POST /api/auth/roles`;
   - `PUT/DELETE /api/auth/roles/{role_id}`;
   - `GET/POST /api/auth/users` extended for description and role;
   - `PUT /api/auth/users/{user_id}` for description/role and any supported identity fields.
4. Add Pydantic validation for role names, descriptions, privilege keys, duplicate keys, and invalid read/write values. Reject unknown privilege keys rather than silently dropping them.
5. Ensure role edits and user edits invalidate/reconcile authorization state for subsequent requests without requiring process restart. Existing sessions may remain valid, but every request must resolve current role privileges from persistence or a safely invalidated cache.
6. Update `/api/auth/me` and user/role payloads so the frontend can render current role and privilege-based controls.

### Tests and acceptance

- CRUD works for a normal custom role.
- Protected Administrator cannot be renamed, privilege-edited, or deleted.
- Unknown/duplicate privileges, duplicate names, invalid role IDs, and deleting an assigned role return stable 4xx responses.
- A user can be created with description and a custom role, then edited and observed through list/me payloads.
- Removing a user's only administrator-capable assignment is rejected.
- A role privilege change takes effect on the next authorized request without restarting the app.
- Endpoint tests cover read and write checks for each resource group, not only a single example.

**Slice gate:** backend full suite, Ruff, and mypy pass; run focused API tests against both fresh and migrated databases. Keep all current tests green.

## 6. Slice 3 — Roles tab, Users editing, and end-to-end acceptance

**Outcome:** Operators can manage roles and user assignments from the browser, with clear disabled/protected/error states and no authorization bypass through the UI.

### Frontend work

1. Add a `Roles` navigation item and a `RolesPanel` using the existing dense operator UI style.
2. Add API client types/functions for current user privileges, role list/create/update/delete, and user update/create fields.
3. Roles panel behavior:
   - list role name, description, protected badge, assigned-user count, and privilege summary;
   - add role dialog with name/description and read/write privilege checkboxes grouped by resource;
   - edit non-protected roles, including privilege changes;
   - delete non-protected roles with confirmation and a clear assigned-users conflict;
   - protected Administrator remains visibly non-editable/non-deletable;
   - represent loading, empty, save error, conflict, success, and disabled states.
4. Extend Users panel:
   - show role and description in each row;
   - add optional description and role selection when creating a user;
   - add edit action/dialog for description and role;
   - disable actions when the current user lacks the corresponding write privilege;
   - show protected-admin constraints without exposing backend internals.
5. Gate navigation and controls using returned privileges, but continue to handle 403 responses as normal UI errors and refresh current user/roles/users after mutations.
6. Preserve mobile layout at 390x844 and desktop layout. Avoid horizontal overflow from privilege matrices; use grouped rows or a responsive two-column read/write grid.

### Tests and manual acceptance

- Vitest tests cover role list, create/edit/delete, protected-role rendering, privilege selection, user create/edit, and 403 error handling.
- Existing App tests are updated for the new `/api/auth/me` shape and Roles navigation.
- Manual desktop check: sign in as admin, create a custom role, grant one read and one write privilege, create a user with description and role, edit the role, edit the user, and verify the user receives/loses access as expected.
- Manual restriction check: attempt protected Administrator edit/delete and assigned-role deletion; verify clear conflict/disabled behavior.
- Manual mobile check at `390x844`: Roles and Users remain usable with readable long descriptions/names and no horizontal overflow.

**Slice gate:** `npm test` and `npm run build` pass; backend required gates remain green; perform browser acceptance against the local app without creating real downloads or tokens.

## 7. Cross-slice route privilege inventory

Maintain this inventory in the authorization module and test it against the actual route table. The exact key names may be adjusted during implementation, but every authenticated operation must land in one resource/access cell.

| Resource group | Read examples | Write examples |
|---|---|---|
| `auth` | current session, user list, role list | password change, user create/update, role create/update/delete |
| `dashboard` | health/status/events | none |
| `server` | status, runs, model list/events, integration config | start, stop, restart, rollback, model load/unload |
| `tokens` | token list | token create/revoke |
| `runtimes` | list/detail/release metadata | install/register/probe/delete |
| `profiles` | list/detail/export/command/validation | create/update/clone/delete/reset/import |
| `huggingface` | search/repository detail | none in current control API |
| `downloads` | list | create/pause/resume/cancel/retry/clear |
| `library` | list/logical/discover | reconcile/import/delete |
| `diagnostics` | none or status if added | export |
| `settings` | settings read | settings update |
| `integrations` | generated client configuration | none in current control API |

The implementation must reconcile this table with the actual route decorators and tests. Do not leave a route implicitly authorized because it was added after the initial mapping.

## 8. Non-goals and deferments

- No multi-role users, role inheritance, deny rules, wildcard privilege editor, audit log, or policy language.
- No authorization of native llama.cpp `/v1/*` requests or per-access-token scopes.
- No password reset workflow beyond the existing password-change behavior unless required to make user editing safe.
- No automatic role deletion/reassignment magic; conflicts should be explicit.
- No broad frontend redesign or unrelated API cleanup.

## 9. Definition of done

- Three numbered slice notes exist under `docs/plans/active/notes/`, one only after each slice passes.
- `sequential-progress.md` records the measured validation, blocker state, and exact next action after each slice.
- Fresh and existing databases migrate successfully; protected Administrator and admin assignment are verified.
- Every authenticated control-plane API function is covered by a read/write privilege decision and backend enforcement.
- Admin can manage roles and users in the browser; protected-role safeguards work.
- A custom role's privileges demonstrably change what its assigned user can read/write.
- Backend and frontend required quality gates pass, `git diff --check` passes, and no generated data, credentials, or secrets are added.

**Suggested eventual commit sequence:**

1. `feat(auth): add role privilege persistence and authorization`
2. `feat(auth): add role and user management APIs`
3. `feat(ui): add roles and user assignment management`
