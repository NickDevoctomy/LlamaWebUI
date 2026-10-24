# Technical debt: benchmark tool-call progress and completion

**Status:** Deferred  
**Observed:** 2026-10-24  
**Scope:** Benchmark runner progress and the benchmark run view

## Evidence

During a coding-agent benchmark the run view sat on "writing a tool call" for over 10 minutes. The run had not stalled: llama-server's `/slots` showed about 15 tokens per second being decoded, all of them going into a single tool call's arguments (most likely one large `write_file`).

`BenchmarkRunner._take_delta` (`backend/src/llamawebui/services/benchmark_runner.py`) appends each streamed `tool_calls[].function.arguments` piece to `pending`, a local of `_generate`. Nothing about the call in progress reaches `self._progress`, so the UI only learns of it once the tool runs and the call is added to `tool_calls`.

## User impact

A long write looks the same as a hung run, which invites cancelling a benchmark that is working. Clients such as OpenCode show the file growing as it streams, so the difference is noticeable.

## Deferred direction

- Mirror the in-progress call into `self._progress` while it streams: at least its tool name and the size of its arguments so far in bytes, updated as deltas arrive.
- Show that byte count beside the "writing a tool call" timeline entry, so a long write visibly advances.
- Later, optionally: parse the partial arguments leniently to show the target path as soon as it is complete, and the content so far, as OpenCode does. The tool still runs only once the call is complete.

## Completion signal

A run that ends today only records llama.cpp's `finish_reason` (`stop`, `length`) or the runner's `tool_round_limit`. A `stop` doesn't say whether the agent finished the task or just gave up, so outright failures and context exhaustion aren't told apart from real completions.

- Add a fourth file tool beside `write_file`, `read_file` and `list_files`, for example `task_complete`. The agent calls it when it considers the task done. `TOOL_SYSTEM_PROMPT` tells it to.
- A run is **completed** only if the agent called it. Any other ending is **unfinished**, whatever the finish reason: a plain stop, running out of tokens or context, the tool-round limit, or an error. The finish reason is still kept for the detail.
- Calling it ends the run: no further rounds are started once it has run.
- Show completed / unfinished on the run and in the benchmark results, so profiles can be compared on whether they finished as well as on speed.
- Context compaction is out of scope for now. Without it, a run that fills the context ends unfinished, which is the intended result until compaction is added.

## Completion criteria

- While a tool call streams, the run view shows its byte count rising.
- The count resets per call and stops when the tool runs; the existing tool-call entry then takes over.
- Covered by a runner test that streams a tool call in several argument deltas and checks the progress payload after each.
- A run whose agent calls the completion tool is recorded as completed, and stops there. Runs ending by stop, length, tool-round limit or error are recorded as unfinished. Each case has a runner test.
