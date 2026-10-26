# Backlog: context compaction for benchmark runs

**Status:** Proposed  
**Raised:** 2026-10-24  
**Scope:** Benchmark runner (`backend/src/llamawebui/services/benchmark_runner.py`), run storage and the benchmark run view

## Problem

A tool-using benchmark run keeps its whole conversation: every tool call's arguments and every tool result go back to llama-server on each round. A coding agent that rewrites a large file with `write_file` adds that file to the history every time, so long runs fill the profile's context and stop unfinished. A model with more room might have finished.

### Evidence

On 2026-10-24, *Tetris medium level* ran on `flint-nvfp4` (`--ctx-size 65536`):

| Round | Tool call | Tokens | Bytes |
|---|---|---|---|
| 1 | `write_file tetris/index.html` | 11,810 | 36,999 |
| 2 | `write_file tetris/index.html` | 12,868 | 40,059 |
| 3 | `write_file tetris/index.html` | 12,916 | 39,914 |
| 4 | `write_file tetris/index.html` | 13,302 | 41,044 |
| 5 | `write_file tetris/index.html` (cut off) | 11,727 | 35,439 |

By round 5 the history held four full copies of the same file, about 54k tokens. Only the latest copy mattered, because the file is on disk. Generation hit the end of the context partway through the fifth write. Since that day the runner ends such a run cleanly with `finish_reason` `context` instead of failing it, but the run is still unfinished.

## Does llama.cpp do this itself?

No. llama-server has **context shift** (`--context-shift`, off by default). When a slot's KV cache is full, it keeps the first `n_keep` tokens, discards a block of the oldest tokens after them, and carries on generating. This is not compaction:

- It cuts blindly by token position. It can remove half of a tool call, a tool result or the original task, and the model is not told.
- It works on the server's cache, not on the conversation the client sends. The next request sends the full history again and fails the same way.
- Nothing in the API reports that a shift happened, so a run could not count it.

Summarising the history is a client-side job in every agent harness: OpenCode, Claude Code and Codex CLI each do it themselves. The benchmark runner is our client, so it has to do it.

## How agents do it

1. **Track usage.** After each request, read the server's token counts. When the history plus room for the next reply would pass a threshold, compact before sending.
2. **Prune first.** Replace old, bulky tool payloads with short placeholders, e.g. `[write_file tetris/index.html: 39,914 bytes, saved]`. This is cheap and needs no model call. OpenCode prunes old tool output this way before it summarises.
3. **Summarise if still too big.** Send the history to the same model with a "summarise this session" prompt and no tools. Replace the older messages with one summary message, then continue with something like "Continue if you have next steps." OpenCode compacts when tokens pass `context_limit − output_limit`, and V2 caps the summary at 4,096 tokens.

Our runs have an advantage: every file the agent wrote is still in its workspace. The summary only needs decisions, progress and remaining work. The agent can `read_file` or `list_files` for anything else.

## Proposed design

### Trigger

- **Context size:** the per-slot `n_ctx` from llama-server's `/props`, read once after the model loads. The profile's `--ctx-size` is a fallback, but the slot value is what applies when the server uses parallel slots.
- **Usage after each round:** cached prompt tokens + newly processed prompt tokens + generated tokens, from the final chunk's `timings` (`cache_n`, `prompt_n`, `predicted_n`). Check that the build reports `cache_n`; if it doesn't, use `usage.prompt_tokens`, or count the history with `/apply-template` and `/tokenize`. `prompt_n` alone undercounts: in the evidence run, round 5 showed only 13,363 prompt tokens because the rest came from the cache.
- **Proactive:** compact before a round when usage + reserve > `n_ctx`. The reserve is the larger of a fixed floor (for example 8,192 tokens) and the largest single reply so far. A model that writes 13k-token files needs 13k free.
- **Reactive:** if a round still ends with the context full (`finish_reason` `length` with no token limit, or llama-server refusing the request as too long), throw away the cut-off reply, compact, and retry the round. Today the run just ends.

### Compaction steps

1. **Prune:** keep the system prompt, the task prompt and the last N rounds (for example 2) intact. In older rounds, replace `write_file`/`edit_file` arguments and `read_file` results with placeholders that give the tool, path and size. Keep every message and tool-call id so the history stays valid for the chat template. Malformed arguments are already sent back as `{}`; placeholders must be valid JSON too.
2. **Summarise:** if pruning frees too little, send one extra request with no tools, asking for a summary of the goal, decisions, files written and what is left. Cap it with `max_tokens` (for example 4,096). The new history is: system prompt, task prompt, the summary as an assistant or user message, then a note that the workspace files are intact and can be read back.
3. **Give up cleanly:** if a compacted history still doesn't leave the reserve free, end the run with `finish_reason` `context` as now. Also cap compactions per run (for example 5), so a model that can't make progress doesn't loop.

### What the run records

- `stats.compactions`: how many times the run compacted. This is the count asked for. It is 0 when none were needed, and missing on older runs.
- Optionally `stats.compactions_pruned` and `stats.compactions_summarised`, to show which kind happened.
- A timeline entry of kind `compacting` for each compaction: start, end, tokens before and after, and `pruned` or `summarised`. A summary request generates tokens. Show those in the timeline, but keep them out of the decode-speed figures, the same way tool time is kept out today.
- A live stage label, "Compacting context", so a long summary request doesn't look like a stall (the same lesson as the tool-call byte progress).
- The run's result on the profile carries `compactions`, so benchmark comparisons show whether a profile only finished by compacting.

### UI

- A **Compactions** fact on the run view, and a count in the run list row when it is above 0.
- A "Compacting context" step in the generation timeline showing tokens before → after.
- In the run form: a **Compact context** checkbox, on by default. Turning it off reproduces today's behaviour, for measuring raw context use.

## Open questions

- Should a compacted run's `completed` outcome look different from one that never compacted? Compaction changes what is being measured.
- Which threshold and reserve fit best, and should they be fixed or shown in the run form?
- Should the summary request use the run's reasoning level, or reasoning off to save tokens?

## Tests to add

- A fake client that reports `cache_n`/`prompt_n`/`predicted_n`, so a run crosses the threshold and compacts. Check the pruned history, the `compactions` count and the timeline entry.
- Reactive compaction after a `length` cut-off, followed by a successful retry.
- The compaction cap, and a history that can't be made small enough, ending as `context`.
- The UI showing the compactions fact, the list-row count and the timeline step.

## References

- [OpenCode session management](https://www.mintlify.com/opencode-ai/opencode/features/session-management)
- [OpenCode compaction notes (gist citing `session/compaction.ts`)](https://gist.github.com/badlogic/cd2ef65b0697c4dbe2d13fbecb0a0a5f)
- [OpenCode V2 compaction internals](https://dev.to/antonio_zhu_e726fd856cd86/opencode-v2-compaction-internals-2a5d)
- [Context compaction in Codex CLI, Claude Code and OpenCode](https://codex.danielvaughan.com/2026/04/14/context-compaction-deep-dive-codex-cli-claude-code-opencode/)
- [llama-server `--context-shift` is off by default (ml-intern issue)](https://github.com/huggingface/ml-intern/issues/259)
