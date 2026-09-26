# Active Sequential Plan

This folder contains the only active execution documents for the remaining implementation work.

## Source of truth

- `sequential-completion-plan.md` — ordered phases, slices, gates, and acceptance criteria.
- `sequential-progress.md` — current phase, current slice, completed evidence, validation results, blockers, and next action.
- `notes/NNNN-<slice>.md` — one numbered, append-only evidence note per completed slice. Keep detailed results here instead of growing `sequential-progress.md`.
- `../llama-web-ui-plan.md` — original overall design and requirements reference. Do not rewrite or use it as the day-to-day progress tracker.

The old handoff document is retained under `../stale/` for historical reference only.

## New-session prompt

Use this prompt when starting a new coding session:

> Read `README.md` and follow the current slice handoff exactly.

## Sequential workflow

### Service-process rule

- Run at most one LlamaWebUI backend and one frontend development server at a time.
- Before starting services, check whether the documented ports `18080` and `5173` are already owned by this workspace's processes and reuse the existing services when possible.
- Do not start a second copy for a browser tab or retry. If a restart is required, stop the existing workspace-owned backend/frontend first, verify the ports are clear, then start exactly one replacement pair.
- Keep browser acceptance in the existing shared page whenever possible; additional browser tabs do not justify additional servers.
- At the end of acceptance, leave the single intended development pair running only when the user is expected to test; otherwise stop it and record that state.

1. Open `sequential-progress.md`.
2. Read the current phase and next action in `sequential-progress.md`.
3. Read the matching requirements and acceptance criteria in `sequential-completion-plan.md`.
4. Inspect the repository and existing tests before editing.
5. Run the required baseline check before making changes when the slice is substantial:
   - `git status --short`
   - `git log -3 --oneline --decorate`
6. Implement only the current slice. Do not begin a later phase or unrelated feature.
7. Add focused deterministic tests for the changed behavior.
8. Run focused validation immediately after the edit.
9. Run the relevant full quality gates before closing the slice.
10. Perform manual acceptance when the slice requires a browser, installed runtime, process, or live integration.
11. Add one numbered note under `notes/` only after the slice passes all required gates, then update `sequential-progress.md` with only a short link and the next handoff state.
12. Include a suggested commit message in the progress entry and final response.

## Required deliverable for every slice

Every completed slice must provide all of the following:

### 1. Implementation summary

Record:

- What changed.
- Which files or subsystems changed.
- What was deliberately not changed.
- Any compatibility, security, or data-preservation considerations.

### 2. Tests

Record:

- Focused tests added or updated.
- What each important test proves.
- Whether dependencies were mocked.
- Confirmation that tests do not require live upstream services, secrets, fixed ports, installed llama-server processes, or large downloads.

### 3. Validation results

Record exact results for applicable checks:

- Focused test command and result.
- Full backend test command and result.
- Coverage percentage and configured threshold.
- Ruff result.
- Mypy result.
- Frontend test/build result.
- `git diff --check` result.
- Start time, end time, and elapsed time for timed test runs.

If a check fails, do not mark the slice complete. Record the failure and either fix it within the slice or identify the blocker explicitly.

### 4. Manual acceptance steps

Only include manual steps when the slice needs human/browser/runtime verification. Steps must be numbered and executable from a clean starting state. Include:

- Preconditions.
- Exact UI/API action.
- Expected result.
- Failure symptoms to watch for.
- Cleanup or safe-stop steps.

Do not create real download jobs, tokens, or large model transfers during automated acceptance. For destructive or secret-creating actions, clearly identify the intentional user action required.

### 5. Next action

State exactly one next slice, using the plan's numbering. Do not list several alternatives. The next slice must be in the same phase unless the current phase gate has been completed.

### 6. Suggested commit message

Use a concise Conventional Commit-style message, for example:

- `feat: add single-instance application locking`
- `test: record api key reload semantics`
- `fix: preserve logical model links during reconciliation`
- `docs: record phase 0 feasibility acceptance`

## Manual test format

Use this format when manual acceptance is required:

```text
Manual acceptance — Phase X.Y: <slice title>

Preconditions:
1. Start from ...
2. Ensure ...

Steps:
1. Open ...
2. Select ...
3. Confirm ...

Expected results:
- ...
- ...

Safe cleanup:
1. Stop ...
2. Remove only ... if explicitly safe.
```

Manual tests must not expose tokens or secret contents in screenshots, logs, terminal output, or progress notes.

## Closing a slice

A slice may be marked complete only when:

- Its implementation is complete.
- Its focused tests pass.
- Relevant full quality gates pass.
- Required manual acceptance is complete or explicitly blocked with evidence.
- `sequential-progress.md` records the result.
- A numbered note under `notes/` records the detailed evidence.
- The next slice is named.
- A suggested commit message is recorded.

Until then, leave the current slice marked **IN PROGRESS** and do not advance the plan.
