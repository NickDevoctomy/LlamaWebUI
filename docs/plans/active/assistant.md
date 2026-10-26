# Active: the Assistant, an OpenRouter agent that tunes llama.cpp profiles

**Status:** Phases 1 to 4 done, plus memory and snapshots; awaiting review  
**Raised:** 2026-10-26  
**Scope:** New `backend/src/llamawebui/assistant/` package, `services/control_plane.py`, assistant API routes in `app.py`, `frontend/src/AssistantPanel.tsx`, Settings > Assistant, bundled research docs in `backend/src/llamawebui/research/`

This plan is shared across sessions. Before starting work, read **Progress** and **Handoff notes**. When you stop, tick what you finished and add a dated note with anything the next session needs.

## Goal

The Assistant page already exists but has only a header. It appears once an OpenRouter key is saved and a model is selected (`GET /api/assistant/status`, privileges `assistant.read`/`assistant.write`).

The aim is an agent the operator chats with. It can:

- search Hugging Face and download models
- install and register llama.cpp runtimes
- create, configure and tweak profiles
- run benchmarks and read their results
- search the web
- write reports with graphs on the improvements it made

As it works it keeps a library of research documents. The useful ones ship with each release, so future agents start with that knowledge instead of researching again.

## Decisions

- **Interaction:** a chat page for now. The agent engine is kept separate from the chat UI so a goal-driven, unattended "optimisation job" mode can be added later. That mode needs more design before it is built.
- **Web search:** OpenRouter's web plugin, using the saved key, plus a `fetch_url` tool for reading pages. No new provider account.
- **Research docs:** markdown with YAML frontmatter that records sources and the date researched.
  - Written at runtime to `data/research/`.
  - Shipped docs live in `backend/src/llamawebui/research/` and are read-only.
  - A "Publish to repository" action copies chosen docs there for the maintainer to review and commit. It only works in a source checkout.
- **Approvals:** each category can be switched in Settings > Assistant.
  - On by default: model downloads, profile create/edit/delete, and benchmarks and router restarts.
  - Off by default: runtime installs, which are optional.
- **Name:** the assistant's name can be set in Settings, and is "Lama" by default. It reaches the system prompt through token replacement.
- **Brief:** every conversation starts with an optimisation brief, filled in on a form before any work begins.

## Design

### 0. Optimisation brief

- **Form:** "New conversation" opens a **New optimisation** form.
- **Where it goes:** the brief is stored on the conversation and placed in the system prompt. It is pinned at the top of the chat and stays editable. Each edit is logged in the chat as an event message.
- **Follow-up questions:** the agent may still ask for more detail in the chat.
- **Future job mode:** the same brief can later drive the unattended mode.

| Field | Required | Measured against |
|---|---|---|
| Subject | One of two | **Model to get working:** a library model, or a Hugging Face repo picked with the existing `/api/huggingface/models` search, with an optional quantization. **Existing profile to optimise:** a profile picker. |
| Max load time (s) | Optional | benchmark `stats.load_seconds` |
| Min prefill speed (tok/s, average) | Optional | `stats.prompt_tokens_per_second` |
| Min decode speed (tok/s, average) | Optional | `stats.decode_tokens_per_second` |
| RAM / VRAM ceilings (GB) | Optional | peak `resources[].ram_bytes` / `vram_bytes` |
| Min context length (tokens) | Optional | A floor the agent may not shrink below to meet other targets |
| Parallel slots | Optional, default 1 | Concurrent requests the profile must support |
| Workload | Default `tetris-no-details` | The benchmark prompt and reasoning level that judge every result |
| Priority | Default order: decode, prefill, load, memory | The set targets in ranked order (up/down buttons). It decides which target wins when they conflict. |

- **Validation:** the brief is stored as `brief` JSON on `assistant_conversations` and checked by a pydantic model. A conversation can't be created without a subject.
- **Scoring:** `evaluate_run(run_id)` (Phase 2) scores a benchmark run against the brief: pass or fail for each target, plus the gap. Reports (Phase 4) chart progress against the target lines.

### 1. Move app.py-only rules into a service (do this first)

The assistant must follow the same rules as the HTTP API. Several of those rules exist only inside `app.py`. They move into a new `services/control_plane.py` (`ControlPlane`), built in the lifespan from the existing registries, the router supervisor and `router_lifecycle_lock`. The HTTP handlers then call it too.

| Method | Today |
|---|---|
| `start_router(runtime_id)` | The `start_router` closure inside `create_app` |
| `stop_router()` | Handler code under `router_lifecycle_lock` |
| `require_router_stopped()` | Repeated in the profile update, enable, delete and reset handlers |
| `next_profile_version()` | `_next_version` in `app.py` |
| `exclusive_conflict()` | `_exclusive_conflict` in `app.py` |
| `queue_download(repo_id, group_key, revision, include_projector)` | `hub.repository` → `DownloadRegistry.create` → `DownloadCoordinator.start` in the `/api/downloads` handler |

`ControlPlane` raises domain errors, and the handlers map them to the same HTTP status codes as today. Behaviour must not change: the existing test suite has to pass with no edits to its assertions.

### 2. OpenRouter chat client: `assistant/openrouter_client.py`

- **Request:** streams `POST https://openrouter.ai/api/v1/chat/completions` with `tools` and `usage: {include: true}`. OpenRouter then reports the exact `usage.cost` of each response, so cost is measured rather than estimated.
- **Shared streaming code:** the code that assembles streamed tool calls (`_take_delta` in `services/benchmark_runner.py`) moves into a shared `services/chat_stream.py`, which both clients use.
- **Key:** read from `SecretStore` on every call, and sent only to openrouter.ai.
- **Errors:** OpenRouter error bodies are surfaced as readable messages.
- **Reasoning:** settings use OpenRouter's `reasoning: {effort}` shape, not llama.cpp's.

### 3. Agent engine: `assistant/engine.py`

- **Turns:** each running turn is one background asyncio task, so it keeps going if the browser closes. Only one conversation runs at a time, the same rule the benchmark runner follows.
- **Loop:** call the model, run its tool calls, persist everything, repeat. It stops when:
  - the model replies without tool calls
  - the operator stops it
  - it reaches the per-turn round limit
  - it reaches the conversation's spend cap. It pauses and asks whether to continue.
- **Approvals:**
  - A tool in an approval category creates a pending approval, and the turn waits on it.
  - The operator approves or rejects in the chat. A rejection goes back to the model as the tool's result.
  - Pending approvals are stored. After a restart they show as "interrupted, ask again".
- **Callbacks (Phase 2):** the assistant hears about the outcome of long jobs it started, so it isn't left polling.
  - **What counts:** only terminal or key events for work the conversation itself started:
    - a benchmark run completes, fails or is cancelled
    - a download completes or fails
    - a runtime install finishes
    - the router crashes while the assistant is using it
  - **Not reported:** progress ticks and events for work started elsewhere.
  - **Delivery:** each event is posted to the conversation as an `event` message with a short summary (for a benchmark, its key stats).
  - **Waking:** if the conversation is idle, a turn is started so the assistant can react, using the privileges of whoever started the job. Waking is skipped while the conversation is paused or stopped.
  - **Rate limit:** at most one wake turn per job. Events that arrive while a turn is running are queued and added to the history before the next model call.
  - **Wiring:** the benchmark runner and download coordinator take an `on_finished` callback, as the coordinator already does with `on_completed`. The engine keeps a record of which job IDs each conversation started.
- **Live progress:** kept in memory, like `BenchmarkRunner._progress`. The UI polls once a second while a turn runs, which is the existing convention. Nothing in the frontend reads SSE yet.
- **Startup:** turns still marked running are marked interrupted, as `BenchmarkRunRegistry.fail_interrupted` does for benchmark runs.

### 4. Tools: `assistant/tools/`

- **Definition:** each tool is a dataclass with:
  - `name`, `description`, and `parameters` (a JSON schema)
  - `privilege`, for example `downloads.write`
  - an optional `approval` category
  - an async `handler(ctx, args) -> ToolResult(text_for_model, summary_for_ui)`
- **Privileges:** a tool is offered only if the conversation's owner has its privilege. The check uses the `AuthenticatedUser.privileges` captured when the turn starts.
- **Output size:** tool output is truncated, as in the benchmark runner's `_call_tool`.

| Group | Tools | Built on | Approval |
|---|---|---|---|
| System | `get_hardware`, `router_status`, `list_runtimes`, `list_profiles`, `get_profile`, `list_library`, `list_downloads` | `detect_hardware`, registries | none |
| Arguments | `search_arguments(query)`, `get_argument(id)` | `ArgumentCatalog`. It has 226 entries, so it is searched rather than sent whole. | none |
| Hugging Face | `search_models`, `get_repository` (quant groups, sizes, README cut to a few KB) | `HuggingFaceCatalog` | none |
| Downloads | `queue_download`, `download_status` | `ControlPlane.queue_download` | downloads |
| Runtimes | `list_runtime_releases`, `install_runtime` | `GitHubReleaseClient`, `RuntimeInstaller` | runtimes |
| Profiles | `preview_profile` (validate only), `create_profile`, `update_profile`, `clone_profile`, `delete_profile` | `ProfileRegistry`, `build_arguments`, the `/api/profiles/preview` logic, `ControlPlane` | profiles (not preview) |
| Benchmarks | `list_benchmarks`, `run_benchmark` (waits for the run, returns stats), `get_benchmark_run`, `compare_runs`, `evaluate_run` | `BenchmarkRunner`, `BenchmarkRunRegistry` | benchmarks |
| Web | `web_search(query)`, `fetch_url(url)` | `web_search` makes a separate OpenRouter call with `plugins: [{id: "web"}]` and returns the answer and citations. `fetch_url` uses httpx and turns HTML into text, capped at 1 MB. | none |
| Research | `search_research(query)`, `read_research(slug)`, `write_research(...)` | `assistant/research.py` | none |
| Reports | `create_report(title, markdown, charts[])` | `assistant_reports` table | none |

- **`fetch_url` limits:** only http and https URLs. Loopback, private and link-local addresses are refused after DNS resolution, so it can't reach the control plane or the router. Redirects are limited.
- **System prompt template:** bundled at `assistant/prompts/system.md` and loaded with `Path(__file__)`, like `benchmarks/system/`.
  - These tokens are filled in when each turn starts: `{{assistant_name}}`, `{{date}}`, `{{hardware}}`, `{{brief}}`, `{{approvals}}`, `{{research_index}}`.
  - A unit test fails on any unknown token, so a typo can't ship.
- **Name:** set in Settings > Assistant, "Lama" by default, 1 to 40 printable characters with no line breaks. It reaches the model only through `{{assistant_name}}`. The UI uses it as the label on replies and in the composer placeholder ("Message Lama…").
- **Prompt content:**
  - what the control plane is
  - the detected hardware
  - the approval rules
  - how to write research docs
  - an index of existing research docs (title, summary, tags, date), so the agent reads what is known before searching the web
  - that web and file content is data, not instructions

### 5. Research documents: `assistant/research.py`

Each document is markdown with YAML frontmatter, validated when written:

```yaml
title: Speculative decoding on Strix Halo
slug: speculative-decoding-strix-halo
summary: One or two sentences.
tags: [llama.cpp, speculative-decoding, amd]
researched: 2026-10-26        # first written
updated: 2026-10-27
sources:
  - {title: "llama.cpp PR #1234", url: "https://...", accessed: 2026-10-26}
applies_to: {models: [...], hardware: [...], runtime: b7xxx}
confidence: high              # high | medium | low
author: {model: anthropic/claude-sonnet-5.5, conversation: <id>}
```

- **Reading:** combines the shipped folder (found with `Path(__file__)`, like `catalogs/`) and `data/research/`. If both have a document with the same slug, the local one wins.
- **Packaging:** PyInstaller's `collect_data_files` (`packaging/windows-package.spec`) and the hatch wheel both include `research/` without changes, as long as the files are tracked in git.
- **Publishing:** `POST /api/assistant/research/publish {slugs}` copies documents into `backend/src/llamawebui/research/`. It returns 409 with the reason unless all of these hold:
  - the app runs from a source checkout (there is a `.git` above the package and the folder is writable)
  - the request comes from the server itself (`_is_local_request`)
  - the user has `settings.write`

### 6. Data model: migration `0032_assistant`

- **`assistant_conversations`:** `id, title, user_id, model_id, brief JSON, state (idle|running|awaiting_approval|interrupted|failed), cost_usd, prompt_tokens, completion_tokens, spend_limit_usd, created_at, updated_at`
- **`assistant_messages`:** `id, conversation_id, seq, role (user|assistant|tool|event), content, reasoning, tool_calls JSON, tool_call_id, tool_name, summary JSON, cost_usd, usage JSON, created_at`
- **`assistant_approvals`:** `id, conversation_id, tool_call_id, category, title, details JSON, state (pending|approved|rejected|expired), decided_by, decided_at`
- **`assistant_reports`:** `id, conversation_id, title, markdown, charts JSON, created_at`
- **Settings keys** in the existing `settings` table (`services/settings_store.py`):
  - `assistant.name`: default "Lama"
  - `assistant.approvals`: a true/false flag for each category
  - `assistant.limits`: `spend_limit_usd` (default 2.00) and `max_rounds_per_turn` (default 40)

### 7. API

`/api/assistant/*` routes already map to `assistant.read`/`assistant.write` in `services/authorization.py`.

- **Conversations:**
  - `GET` and `POST /api/assistant/conversations`; the POST takes the brief
  - `GET` and `DELETE /api/assistant/conversations/{id}`; the GET includes messages and live progress
  - `PUT .../{id}/brief`
  - `POST .../{id}/messages` returns 202 and starts a turn
  - `POST .../{id}/stop`
  - `POST .../{id}/continue` resumes after a spend-cap pause
- **Approvals:** `POST /api/assistant/approvals/{id} {approve}`. Approving also needs the action's own privilege, for example `downloads.write`.
- **Research:** `GET /api/assistant/research`, `GET /api/assistant/research/{slug}`, `POST /api/assistant/research/publish`
- **Reports:** `GET /api/assistant/reports`, `GET /api/assistant/reports/{id}`
- **Settings:** `GET` and `PUT /api/settings/assistant` for the name, approvals and limits, under the settings privileges. `/api/assistant/status` also returns the name, so users without `settings.read` see it in the chat.

### 8. Frontend

- **`AssistantPanel.tsx`** has three tabs: Chat, Research and Reports.
  - **Chat:**
    - A conversation list, which becomes a dropdown at phone width, and the New optimisation form, which opens in the existing `Dialog`.
    - The brief, pinned at the top, collapsible and editable.
    - Messages rendered with the existing lazy-loaded `MarkdownContent`.
    - Tool calls as collapsible rows styled like the benchmark `GenerationTimeline`.
    - Approval cards showing the details, with Approve and Reject buttons.
    - A running indicator with a Stop button.
    - The composer.
    - A header with the model, its saved price and the conversation's measured cost.
  - **Research:** a searchable list marked shipped or local, a reader that shows the sources from the frontmatter, and multi-select publishing when it is allowed.
  - **Reports:** markdown plus charts. A new `ReportChart` (line and bar) is generalised from `BenchmarkCharts.TimeChart` and stays hand-drawn SVG.
- **Settings > Assistant tab:** the name, a toggle for each approval category, and the limits.
- **Design:** existing design tokens and components only. Every new state must be checked in a real browser, at desktop and phone widths, in light and dark themes.

## Progress

Stop for maintainer review at the end of each phase.

### Phase 1: Foundation
- [x] `ControlPlane` refactor, with the existing tests passing unchanged
- [x] `services/chat_stream.py` extracted from the benchmark runner
- [x] OpenRouter client with measured cost
- [x] Migration `0032_assistant_conversations` (conversations and messages) and the models
- [x] Brief: pydantic model, form, storage, pinned display, use in the prompt
- [x] System prompt template with token replacement; assistant name setting ("Lama")
- [x] Engine: turns, stop, round limit, spend cap and continue, interrupted on restart
- [x] Read-only tools: System and Arguments
- [x] Conversation API
- [x] Chat UI
- [x] Settings > Assistant tab with the name and limits
- [x] Tests, quality gates, browser check

### Phase 2: Actions and approvals
- [x] Hugging Face, download, runtime, profile and benchmark tools
- [x] `evaluate_run` against the brief
- [x] Approval flow (engine, API, approval cards)
- [x] Approval toggles in Settings > Assistant
- [x] Callbacks for jobs the assistant started (benchmark and download outcomes), with an idle conversation woken once per job. Router-crash callbacks are not done (see the handoff note).

### Phase 3: Knowledge
- [x] `web_search` and `fetch_url`, with the SSRF guard
- [x] Research docs: write, search and read, with frontmatter validation
- [x] Research tab and publishing
- [x] Seed `backend/src/llamawebui/research/README.md`

### Phase 4: Reports
- [x] `write_report` and its storage (markdown files like research, not a table)
- [x] `ReportChart` and the Reports tab

### After Phase 4: memory and safety (from the maintainer's second real run)
- [x] Fuzzy argument search
- [x] Profile snapshots (API) with automatic snapshots and restore tools
- [x] Reports and research readable by the assistant (indexes in the prompt, search and read tools)
- [x] Auto-publish setting
- [x] "Work without asking questions" brief option
- [x] Start-of-work routine: read reports and research, then look up speeds others report

### After the third real run: comparable runs and clean-up (2026-10-26)
- [x] Runs record their launch settings (`benchmark_runs.launch_settings`, `settings_fingerprint`, migration 0036) and the conversation that started them (`conversation_id`). A recommended profile keeps its version through edits, so the version could not tell runs apart. `services/launch_settings.py` holds the fingerprint and diff.
- [x] `run_benchmark` always uses the brief's benchmark and reasoning level; it no longer takes either as an argument. The brief form starts the level at the one the profile's last result used.
- [x] `evaluate_run` compares with a baseline (this conversation's first comparable run, else the latest earlier one; or `baseline_run_id`): settings that differ, the change in each figure, and a warning when prompt or generated tokens or peak context differ by more than 25%. `get_benchmark_run` lists `differs_from_profile_now`.
- [x] The `unmeasured` check is skipped when a completed run measured the profile's current settings (`measured_now`), e.g. after a revert.
- [x] No snapshot for profiles the conversation created (`ToolContext.created_profiles`); snapshot note no longer cut short.
- [x] `list_runtimes` shows the commit and release tag; `list_runtime_releases` shows only this machine's builds unless `all_platforms`.
- [x] Leaner output: argument search drops matches under 35% of the best score, caps at 12 and puts removed arguments last; `list_benchmark_runs` takes `limit` (default 10) and `benchmark_id`; `get_repository` leaves out unremarkable group fields.
- [x] Web search: `search_model` setting (falls back to the assistant's model), a 90 s timeout, and a warning on answers with no sources (or a raw tool-call reply) telling the model not to cite them. Why the plugin returned no citations for `qwen/qwen3.8-omni-flash` is unconfirmed: it needs one real call, which only the maintainer runs.
- [x] Reports gain `alternatives` and `left_behind`, shown in the reader.
- [x] Benchmark runs can be selected and deleted together (`POST /api/benchmarks/runs/delete`), and a conversation's runs deleted from its header (`POST /api/assistant/conversations/{id}/benchmark-runs/delete`, which also needs `server.lifecycle.write`).
- [x] Tests point the shipped research and report folders at a temporary folder (autouse fixture in `conftest.py`), since the libraries now read `SHIPPED_DIRECTORY` when built.
- [x] Router logs for failures: the supervisor counts lines (`log_mark`, `logs_since`), a failed run keeps its own last 200 lines (`benchmark_runs.server_log`, migration 0037), the failure notice ends with the last 20 and `get_benchmark_run` shows them as `server_log_tail`. New `read_server_log` tool (server.read): a failed run's lines by `run_id`, or the live router log, with `lines` and a `contains` filter. Not shown in the Benchmarks UI yet.
- [ ] Not done: a fixed-workload speed probe (the maintainer put this aside; the big swings in the third run came from runs at different reasoning levels).

## Verification

- **Engine tests:** a scripted fake OpenRouter (`httpx.MockTransport`) returns streamed tool calls. It drives the engine through:
  - a tool that succeeds and one that fails
  - an approval that is approved, and one that is rejected
  - stop
  - the round limit
  - the spend-cap pause
  - restart recovery
- **Other unit tests:**
  - tools are filtered by privilege
  - `fetch_url` refuses private and loopback addresses
  - research frontmatter is validated, and shipped and local docs merge correctly
  - prompt tokens are all replaced (the name appears and no `{{...}}` is left)
  - the name is validated
  - the brief is validated (exactly one subject, targets in range)
  - `evaluate_run` scores sample benchmark stats correctly
  - the publish guards hold
- **Regression:** the existing suite passes unchanged after the `ControlPlane` refactor.
- **Quality gates:** `pytest` (85% branch coverage floor), `ruff`, `mypy`, `npm test`, `npm run build`.
- **Browser check:** use a scratch backend on another port with a scratch data folder.
  - **No real model calls.** Agents must not send real OpenRouter completion requests, because they cost money. Drive the UI with a scripted fake chat client: `create_app(assistant_client=...)`, like `ScriptedClient` in `backend/tests/test_assistant.py`. Free endpoints such as the model list are fine.
  - Only the maintainer runs real conversations, or explicitly asks an agent to.
  - Check the UI: chat, approval cards, research and reports, at phone width and in dark mode.

## Handoff notes

- **2026-10-26, Phase 4 done, plus memory and snapshots.**
  - **Quality gates:** all pass (backend 779 tests, 93.1% coverage; frontend 162 tests).
  - **Browser check:** a scratch backend with seeded reports and research and a fake model, so no OpenRouter calls were made. Its research and reports folders pointed at a scratch repo. Checked: the Reports tab with the results table, line and bar charts, hover tooltips and data tables; the brief's new option; the auto-publish switch. All at desktop and phone widths, in light and dark.
  - **Documents:** `assistant/library.py` is the shared library: frontmatter base model, parse and render, merging local over shipped, search, write (with `publish=` for auto-publish), delete, publish, and the prompt index. Both kinds build on it:
    - `research.py`: `ResearchMeta` plus `ResearchLibrary`; the old names remain as aliases
    - `reports.py`: `ReportMeta` (created, subject, hardware, runtime, benchmark, outcome, results) plus `ReportLibrary`. The shipped folder is `backend/src/llamawebui/reports/`.
  - **Charts:** a report's charts are fenced ```` ```chart ```` JSON blocks in the body, validated on write by `reports.Chart` (`type` line or bar, `categories`, `series[{name, values}]`, `y_label`, optional `x_label` and `targets`). `MarkdownContent` draws them with `ReportChart.tsx` when given `charts`; anything else stays a code block.
  - **Chart colours:** the series colours are new tokens, `--series-1` to `--series-6`. They were checked as a categorical set with the dataviz validator against the app's light and dark surfaces. Light mode has a contrast warning, answered by a data table under every chart.
  - **Routes:** `assistant/routes.py` registers the same list, read, delete and publish routes for `/api/assistant/research` and `/api/assistant/reports` (`_library_routes`).
  - **Tools:** `search_reports`, `read_report` and `write_report` sit beside the research tools in `tools/knowledge.py`; one factory serves both kinds. The prompt gains `{{report_index}}` and `{{snapshots}}`, a "Before you start" routine (read reports and research, search for speeds others report on similar hardware, measure a baseline), and an explanation of reports versus research.
  - **Auto-publish:** `AssistantSettings.auto_publish`, off by default. When on, writes go straight into the shipped folders if `source_checkout_problem` allows; otherwise the tool result says why not. As with manual publishing, switching it on is refused from remote requests.
  - **Snapshots:**
    - `services/profile_snapshots.py` (`ProfileSnapshotRegistry`; migration `0035_profile_snapshots`) keeps runtime, model, configuration, enabled, exclusive, description and version. Restoring keeps the current alias and needs the router stopped.
    - API: `GET`/`POST /api/profiles/{id}/snapshots`, `POST .../{sid}/restore`, `DELETE .../{sid}`.
    - Automatic snapshots: the engine snapshots the brief's profile on the first turn (`_snapshot_subject`), and `update_profile` snapshots any other profile the first time a conversation changes it (`snapshot_once`).
    - Tools: `list_snapshots`, `create_snapshot`, and `restore_snapshot` (approval category "profiles").
  - **Reminders** (`checks.py`): each reminder now has a kind, stored as `summary.reminders` on the check event. New kinds:
    - `report`: benchmarks ran since the last report
    - `question`: an autonomous conversation ended its turn with a question; nudged once per operator message
  - The unmeasured-change reminder ignores edits with `launch_changed: false`, such as a description-only change.
  - **Brief:** `autonomous` (default true). `brief_text` adds autonomous or attended instructions, and the form has a "Work without asking questions" checkbox.
  - **Argument search:** a query now matches any of its words instead of needing all of them. It catches partial words and typos (difflib, 0.8 cutoff), knows a few llama.cpp synonyms (mtp, kv, ctx, ngl and so on), takes an optional `category`, and lists the categories when nothing matches.

- **2026-10-26, Phase 3 done.**
  - **Quality gates:** all pass (backend 771 tests, 93.0% coverage; frontend 158 tests).
  - **Browser check:** run on a scratch backend with a scripted fake model, so no OpenRouter calls were made. The shipped research folder pointed at a scratch "repository" with its own `.git`. Checked: a chat that searched the library, searched the web and wrote a document; the Research tab (reader, multi-select publish, delete dialog, empty states); the reminder row. All at desktop and phone widths, in light and dark.
  - **Fixes from the maintainer's first real run** (conversation "qwen 3.8 27b"):
    - **End-of-turn checks** (`assistant/checks.py`): when the model ends a turn with no job running, the engine looks for forgotten steps and sends at most one `[Check]` reminder per lapse. It is stored as an event with `summary.kind == "check"`, which the UI shows as "Reminder to Lama". Two checks so far:
      - a profile created or updated since its last benchmark
      - the router left in a different state from when the conversation began, but only when the assistant's own last router action caused it
    - **Router state at start:** stored on the conversation as `router_at_start` (migration `0034_assistant_router_at_start`; `ControlPlane.router_runtime_id`).
    - **Tool and prompt wording:** profile edits during a benchmark get a clear "wait for its notification" refusal. `update_profile` notes that a change is unmeasured until benchmarked. `start_router` says to leave the router as it was found. The system prompt states all three rules.
    - **Summary fix:** the `update_profile` summary no longer claims "is now vN" when the version didn't change. Recommended profiles keep their recommendation's version.
  - **Where things are:**
    - `assistant/web.py`: `WebAccess.search` (OpenRouter `plugins: [{id: "web", max_results: 5}]`, collecting `url_citation` annotations and `usage.cost`) and `WebAccess.fetch`.
    - **Fetch guard:** `GuardedTransport` replaces httpx's connection pool with one whose `GuardedBackend` resolves each host, refuses any non-public address, and connects to the checked IP. This covers redirects too and leaves no DNS-rebinding gap. TLS still verifies the host name. It runs with `trust_env=False`, so a proxy can't bypass it. Other limits: 1 MB, 5 redirects, text types only; HTML is reduced to text by `html_text`.
    - `assistant/research.py`: `ResearchLibrary` (shipped plus local merge, search, write, delete, publish, `index_text`) and the frontmatter models. Shipped documents live in `backend/src/llamawebui/research/`, local ones in `data/research/`.
    - `assistant/tools/knowledge.py`: `web_search` (needs `assistant.write`), `fetch_url`, `search_research`, `read_research` (all `assistant.read`) and `write_research` (`assistant.write`).
    - **Cost tracking:** `ToolResult.cost_usd` lets a tool add its own cost; web searches use it, and the engine adds it to the conversation.
    - **Prompt context:** `ToolContext` gains `conversation_id` and `model_id`, which are recorded as the document's author. The system prompt gains `{{research_index}}` and a Research library section.
    - **Routes** (in `assistant/routes.py`):
      - `GET /api/assistant/research?q=` returns the documents plus `publish_problem`
      - `GET` and `DELETE /api/assistant/research/{slug}` (only local documents can be deleted)
      - `POST /api/assistant/research/publish` needs `settings.write`, a local request and a source checkout. Publishing moves the files into the shipped folder.
    - `local_requests.py`: `is_local_request`, moved out of `app.py` so the routes can use it.
    - **Frontend:**
      - `AssistantPanel` now has Chat and Research tabs.
      - New `ResearchPanel.tsx` and `LazyMarkdown.tsx`.
      - Assistant markdown tables no longer inherit the global 62 px `td` height.
  - **Phase 4 starting points:** add `create_report` as a knowledge-style tool with an `assistant_reports` table (migration 0035), a Reports tab beside Research, and `ReportChart` generalised from `BenchmarkCharts`.

- **2026-10-26, between phases:** provider failures and re-running a brief.
  - **Transient failures:** `OpenRouterError.transient` marks provider failures and busy responses (HTTP 408, 429 and 5xx, and error codes such as `internal_server_error` or `overloaded`). The engine retries those up to twice (2 s, then 5 s), but only if nothing had streamed yet, so a partly paid reply is never requested twice.
  - **Nested errors:** OpenRouter can wrap the provider's raw `data: {...}` error inside its own error message. The provider's message is now pulled out and shown.
  - **Try again:** `POST /api/assistant/conversations/{id}/retry` carries on a failed or interrupted turn. The UI shows it as a "Try again" button, replacing the old error banner that repeated the error event.
  - **Run again:** opens the New optimisation form filled in with the brief and the first message. The form gained an optional "First message", which is sent as soon as the conversation is created.

- **2026-10-26, Phase 2 done.**
  - **Quality gates:** all pass (backend 751 tests, 92.6% coverage; frontend 152 tests).
  - **Browser check:** run with a scripted fake model (no OpenRouter calls). A real approval card for a Hugging Face download was rejected, and the result was checked at desktop and phone widths, in light and dark.
  - **New tools:** in `assistant/tools/`:
    - `models.py`: `search_models`, `get_repository`, `queue_download`, `list_runtime_releases`, `install_runtime`
    - `profiles.py`: `preview_profile`, `create_profile`, `update_profile` (`set_arguments` and `remove_arguments` merge into the current arguments), `clone_profile`, `delete_profile`
    - `benchmarks.py`: `list_benchmarks`, `run_benchmark`, `get_benchmark_run`, `list_benchmark_runs`, `evaluate_run`, `stop_router`, `start_router`
  - **Approvals:**
    - A tool's `approval` category, plus `describe` to check the arguments and write the approval card text.
    - The engine waits on a future (`AssistantEngine._ask`) and the conversation state becomes `awaiting_approval`.
    - `POST /api/assistant/approvals/{id}` decides; it needs `assistant.write` plus the tool's own privilege.
    - Migration `0033_assistant_approvals`. Approvals expire when a turn is stopped or the app restarts.
    - The settings record gains `approvals`. Defaults: downloads, profiles and benchmarks on; runtimes off.
  - **Callbacks:**
    - `BenchmarkRunner(on_finished=...)` and `DownloadCoordinator(on_finished=...)` call `notify_assistant` in `app.py`, which calls `AssistantEngine.notify`.
    - A tool registers its job with `context.watch(kind, job_id)`.
    - A notification is stored as an event with `summary.kind == "notification"`. `history()` sends it to the model as `[Notification] ...`, held back until any pending tool results are in.
    - An idle conversation is woken; paused, failed and stopped ones are not.
  - **Differences from the design:**
    - `install_runtime` waits until the install finishes (the existing installer runs within the request), so it needs no callback.
    - Router-crash callbacks are not implemented. A crash during a benchmark already arrives as a failed-benchmark notification. A crash at any other time is not reported to the assistant.
    - `compare_runs` is covered by `list_benchmark_runs`, which returns the key figures of recent runs.
    - Benchmark results reach the model as scalar stats and peak memory only. Output, thinking, timeline, samples and tool logs are never sent to it.
  - **Moved into `ControlPlane`:** runtime install, used by `/api/runtimes/install` too.
  - **System prompt:** now explains how the router works: enabled profiles are published but not loaded until requested, and exclusive profiles can be enabled together. It also covers approvals and notifications.

- **2026-10-26, Phase 1 done.** All gates pass (backend 738 tests, 92.8% coverage; frontend 149 tests). A real OpenRouter run was made without the maintainer's approval (about $0.0016). That was a mistake; future checks use a scripted fake client (see Verification).
  - **Where things are:**
    - `backend/src/llamawebui/assistant/`:
      - `openrouter_client.py`, `engine.py`, `conversations.py`, `brief.py`, `prompt.py` plus `prompts/system.md`, `settings.py`, `setup.py` (builds the engine and each turn's prompt)
      - `routes.py`, an `APIRouter` mounted in `app.py`
      - `tools/` (`__init__` framework, `system.py`, `arguments.py`)
    - `services/control_plane.py`: router start/stop, the stopped-router check, profile version bumps, the exclusive-model check and `queue_download`. The HTTP handlers use it too.
    - `services/chat_stream.py`: SSE parsing, tool-call assembly and history helpers, shared with the benchmark runner and the llama.cpp client.
    - Frontend: `frontend/src/AssistantPanel.tsx`, plus the Assistant tab in `SettingsPanel.tsx`.
  - **Differences from the design above:**
    - Settings are stored as one record, `assistant.settings` (name, `spend_limit_usd`, `max_rounds_per_turn`), not separate keys.
    - Migration `0032` creates only the conversations and messages tables. Approvals (Phase 2) and reports (Phase 4) get their own migrations.
    - The conversation state `awaiting_approval` arrives in Phase 2; Phase 1 has idle, running, paused, interrupted and failed.
    - The auth middleware now sets `request.state.user`, which the assistant routes use.
    - `DEFAULT_BENCHMARK_ID` and `DEFAULT_SYSTEM_PROMPT_ID` moved to `domain/benchmark.py`.
  - **Phase 2 starting points:**
    - Add action tools under `assistant/tools/`, using `ControlPlane` and the existing registries.
    - Add the approval category to `Tool.approval`, with an engine wait plus an approvals table and route.
    - Add callbacks per the Engine section.
    - Add `evaluate_run`.

- **2026-10-26:** plan written. Prerequisites already in place:
  - the OpenRouter key in `SecretStore`
  - the model and its pricing in the `assistant.openrouter_model` setting (`services/openrouter_catalog.py`, `OpenRouterModelPicker.tsx`)
  - the `assistant.read`/`assistant.write` privileges (migration `0031`)
  - the Assistant nav item, which shows only when `/api/assistant/status` reports `available`
