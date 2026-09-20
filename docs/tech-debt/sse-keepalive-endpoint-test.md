# SSE keepalive endpoint test

**Status:** Deferred
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

Add a short, deterministic endpoint-level keepalive test without using an unbounded `TestClient.get()` call. Prefer one of:

1. A custom ASGI streaming harness that consumes exactly one response body chunk and then disconnects.
2. A finite fake async iterator that yields a keepalive-triggering timeout under controlled scheduling.
3. A dedicated streaming HTTP client with an explicit read timeout and cancellation path.

The test must:

- Complete in well under one second.
- Never start a real `llama-server` process.
- Never wait for an idle SSE stream to close naturally.
- Explicitly cancel and close the response stream.

## Acceptance criteria

- The endpoint-level test observes `: keepalive\\n\\n`.
- Full backend tests complete promptly and pass the 90% branch-coverage gate.
- No global monkeypatch of `asyncio.wait_for` is used.
