# SSE keepalive endpoint test

**Status:** Resolved
**Date:** 2026-09-20
**Area:** Backend / native router event streaming

## Problem

The control-plane `/api/server/models/events` endpoint now emits `: keepalive` comments while the native llama.cpp event stream is idle. A direct endpoint-level unit test using Starlette `TestClient` became a long-lived streaming test and could block the test suite instead of completing promptly.

## Current coverage

- Native SSE parsing is covered by deterministic `httpx` transport tests in `backend/tests/test_router_client.py`.
- Bounded event collection is covered by deterministic transport tests.
- Rollback and model-event API behavior are covered by finite fake-client tests.
- Live CUDA acceptance reached the native event endpoint, but PowerShell/curl buffering did not expose an idle frame within the bounded window.

## Deferred work

The stream implementation is now isolated in `services/model_event_stream.py`, and a short deterministic test consumes one keepalive frame and explicitly closes the async generator. It does not use an unbounded `TestClient.get()` call.

The test uses a finite fake idle client and a 10 ms keepalive interval. It completes in under one second and verifies that the upstream iterator is closed.

The stream implementation follows these requirements:

- Complete in well under one second.
- Never start a real `llama-server` process.
- Never wait for an idle SSE stream to close naturally.
- Explicitly cancel and close the response stream.

## Acceptance criteria

- The focused keepalive test observes `: keepalive\\n\\n` and completed as part of a 35-test subset in 7.53 seconds, including test setup.
- The full backend suite completed in 16.62 seconds with 192 tests passing.
- The SSE debt is resolved, but the complete suite remains at 88.43% branch coverage; unrelated existing coverage gaps still prevent the 90% gate.
- No global monkeypatch of `asyncio.wait_for` is used.
