"""The assistant's web access and research library, without touching the network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpcore
import httpx
import pytest
from conftest import Login
from fastapi.testclient import TestClient
from test_assistant import LOCAL, ScriptedClient, text_reply, tool_reply
from test_assistant_actions import _app, _call, _detail, _setup, _wait

from llamawebui.assistant.openrouter_client import OpenRouterError
from llamawebui.assistant.research import (
    Author,
    ResearchError,
    ResearchLibrary,
    ResearchMeta,
    ResearchNotFoundError,
    parse_document,
    render_document,
    source_checkout_problem,
)
from llamawebui.assistant.web import (
    MAX_PAGE_BYTES,
    SEARCH_PROMPT,
    BlockedAddressError,
    GuardedBackend,
    GuardedTransport,
    WebAccess,
    WebError,
    checked_addresses,
    html_text,
    public_address,
)

# Research documents ---------------------------------------------------------------------------

FIELDS: dict[str, Any] = {
    "slug": "ubatch-on-4090",
    "title": "Micro-batch size on an RTX 4090",
    "summary": "512 beat 256 for prefill on a 27B model.",
    "tags": ["llama.cpp", "Batching", "batching"],
    "sources": [
        {"title": "Benchmark run abc (qwen, b11815)"},
        {"title": "llama.cpp README", "url": "https://github.com/ggml-org/llama.cpp"},
    ],
    "applies_to": {"models": ["Qwen3.8-27B"], "hardware": ["RTX 4090"]},
    "confidence": "medium",
}


def _document(slug: str, title: str, updated: str, body: str = "Body.") -> str:
    return (
        f"---\ntitle: {title}\nslug: {slug}\nsummary: About {title}.\ntags: [speed]\n"
        f"researched: 2026-01-01\nupdated: {updated}\nsources:\n  - title: A page\n"
        f"    url: https://example.com\nconfidence: high\n---\n\n{body}\n"
    )


def test_documents_parse_render_and_refuse_bad_frontmatter() -> None:
    meta, body = parse_document("﻿" + _document("flash-attention", "Flash", "2026-02-01"))
    assert meta.slug == "flash-attention"
    assert body == "Body."
    assert parse_document(render_document(meta, body)) == (meta, body)

    cases = {
        "no frontmatter": "starts with ---",
        "---\ntitle: x\n": "not closed",
        "---\ntitle: [\n---\n": "not valid YAML",
        "---\n- a list\n---\n": "must be a YAML mapping",
        _document("Bad Slug", "x", "2026-01-01"): "slug",
        _document("ok", "x", "2026-01-01").replace("https://example.com", "ftp://x"): "http",
    }
    for text, message in cases.items():
        with pytest.raises(ResearchError, match=message):
            parse_document(text)
    with pytest.raises(ValueError, match="at most 40"):
        ResearchMeta.model_validate(
            {**FIELDS, "tags": ["x" * 41], "researched": "2026-01-01", "updated": "2026-01-01"}
        )


def _library(tmp_path: Path, today: date = date(2026, 10, 26)) -> ResearchLibrary:
    shipped = tmp_path / "repo" / "research"
    shipped.mkdir(parents=True)
    (shipped / "README.md").write_text("# Not a document", encoding="utf-8")
    (shipped / "broken.md").write_text("no frontmatter", encoding="utf-8")
    (shipped / "flash-attention.md").write_text(
        _document("flash-attention", "Flash attention", "2026-03-01", "Turn it on for CUDA."),
        encoding="utf-8",
    )
    (shipped / "kv-cache.md").write_text(
        _document("kv-cache", "KV cache types", "2026-02-01"), encoding="utf-8"
    )
    return ResearchLibrary(tmp_path / "data" / "research", shipped, today=lambda: today)


def test_the_library_merges_shipped_and_local_documents(tmp_path: Path) -> None:
    library = _library(tmp_path)
    assert library.index_text().count("\n") == 1
    written = library.write(
        FIELDS, "## Results\n\n512 wins.", author=Author(model="m", conversation="c")
    )
    document, created = written.document, written.created
    replaced = library.write(
        {**FIELDS, "slug": "kv-cache", "title": "KV cache, measured"}, "q8_0 is fine."
    ).document

    assert created is True
    assert document.meta.tags == ["llama.cpp", "batching"]
    assert document.meta.researched == date(2026, 10, 26)
    assert document.meta.author == Author(model="m", conversation="c")
    # Rewriting a shipped slug keeps when it was first researched, and replaces it locally.
    assert replaced.meta.researched == date(2026, 1, 1)
    assert replaced.replaces_shipped is True
    assert [item.meta.slug for item in library.documents()] == [
        "ubatch-on-4090",
        "kv-cache",
        "flash-attention",
    ]
    assert [item.origin for item in library.documents()] == ["local", "local", "shipped"]
    assert library.search("cuda")[0][0].meta.slug == "flash-attention"
    best, score = library.search("4090 batching")[0]
    assert (best.meta.slug, score > 3) == ("ubatch-on-4090", True)
    assert library.search("nothing-matches") == []
    assert len(library.search("")) == 3
    assert "- `ubatch-on-4090`: Micro-batch size" in library.index_text()
    assert library.index_text(limit=1).endswith("… and 2 more; search to find them.")
    with pytest.raises(ResearchNotFoundError):
        library.get("missing")


def test_writing_validates_and_only_local_documents_can_be_deleted(tmp_path: Path) -> None:
    library = _library(tmp_path)
    with pytest.raises(ResearchError, match="needs a body"):
        library.write(FIELDS, "  ")
    with pytest.raises(ResearchError, match="longer than"):
        library.write(FIELDS, "x" * 60_001)
    with pytest.raises(ResearchError, match="sources"):
        library.write({**FIELDS, "sources": []}, "Body.")
    library.write(FIELDS, "Body.")
    library.delete("ubatch-on-4090")
    with pytest.raises(ResearchError, match="ships with LlamaWebUI"):
        library.delete("kv-cache")
    assert ResearchLibrary(tmp_path / "none", tmp_path / "none").index_text() == "None yet."


def test_publishing_needs_a_source_checkout_and_moves_local_documents(tmp_path: Path) -> None:
    library = _library(tmp_path)
    library.write(FIELDS, "Body.")
    assert library.publish_problem() == "publishing needs LlamaWebUI to run from a source checkout"
    with pytest.raises(ResearchError, match="source checkout"):
        library.publish(["ubatch-on-4090"])

    (tmp_path / "repo" / ".git").mkdir()
    assert "not writable" in str(source_checkout_problem(tmp_path / "repo" / "missing"))
    with pytest.raises(ResearchError, match="already published: kv-cache"):
        library.publish(["ubatch-on-4090", "kv-cache"])
    assert library.publish(["ubatch-on-4090", "ubatch-on-4090"]) == ["ubatch-on-4090"]
    assert (tmp_path / "repo" / "research" / "ubatch-on-4090.md").is_file()
    assert library.get("ubatch-on-4090").origin == "shipped"


# Web access -----------------------------------------------------------------------------------


def _resolver(addresses: dict[str, list[str]]) -> Any:
    async def resolve(host: str, port: int) -> list[str]:
        if host not in addresses:
            raise OSError("no such host")
        return addresses[host]

    return resolve


PUBLIC = _resolver({"example.com": ["93.184.215.14"], "docs.example.com": ["2606:4700::1"]})


def test_only_public_addresses_count_as_fetchable() -> None:
    for address in ("93.184.215.14", "2606:4700::1"):
        assert public_address(address) is True
    for address in (
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.10",
        "169.254.169.254",
        "::1",
        "fe80::1%3",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "0.0.0.0",
    ):
        assert public_address(address) is False, address


@pytest.mark.asyncio
async def test_connections_are_checked_and_pinned_to_the_checked_address() -> None:
    resolve = _resolver(
        {"example.com": ["93.184.215.14"], "intranet": ["93.184.215.14", "10.0.0.2"], "empty": []}
    )
    with pytest.raises(BlockedAddressError, match="private or local address \\(10.0.0.2\\)"):
        await checked_addresses("intranet", 80, resolve)
    with pytest.raises(WebError, match="could not resolve nowhere"):
        await checked_addresses("nowhere", 80, resolve)
    with pytest.raises(WebError, match="could not resolve empty"):
        await checked_addresses("empty", 80, resolve)

    class Recorder(httpcore.AsyncNetworkBackend):
        def __init__(self) -> None:
            self.connected: list[tuple[str, int]] = []
            self.slept = 0.0

        async def connect_tcp(self, host: str, port: int, **_: Any) -> Any:
            self.connected.append((host, port))
            return "stream"

        async def sleep(self, seconds: float) -> None:
            self.slept += seconds

    recorder = Recorder()
    backend = GuardedBackend(resolve, recorder)
    assert await backend.connect_tcp("example.com", 443) == "stream"
    assert recorder.connected == [("93.184.215.14", 443)]
    with pytest.raises(BlockedAddressError):
        await backend.connect_tcp("intranet", 80)
    with pytest.raises(BlockedAddressError, match="unix sockets"):
        await backend.connect_unix_socket("/tmp/socket")
    await backend.sleep(0.5)
    assert recorder.slept == 0.5


@pytest.mark.asyncio
async def test_the_guarded_transport_refuses_local_addresses_before_connecting() -> None:
    async with httpx.AsyncClient(
        transport=GuardedTransport(_resolver({"localhost": ["127.0.0.1"]}))
    ) as client:
        with pytest.raises(BlockedAddressError):
            await client.get("http://localhost:18080/api/status")


def test_html_is_reduced_to_readable_text() -> None:
    title, text = html_text(
        "<html><head><title> Flags </title><style>p{}</style></head><body>"
        "<nav>Menu</nav><h2>Batch   size</h2><p>Use <b>-ub</b>&nbsp;512.</p>"
        "<ul><li>one</li><li>two</li></ul><table><tr><td>a</td><td>b</td></tr></table>"
        "<pre>llama-server\n  -ub 512</pre><script>alert(1)</script>"
        "<footer>©</footer></body></html>"
    )
    assert title == "Flags"
    assert text == (
        "## Batch size\n\nUse -ub 512.\n\n- one\n- two\n\n| a | b\n\n"
        "```\nllama-server\n  -ub 512\n```"
    )


def _web(handler: Any, resolve: Any = PUBLIC) -> WebAccess:
    return WebAccess(
        ScriptedClient(), lambda: None, transport=httpx.MockTransport(handler), resolve=resolve
    )


@pytest.mark.asyncio
async def test_fetching_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/page":
            return httpx.Response(200, html="<title>T</title><p>Hello</p>")
        if path == "/moved":
            return httpx.Response(302, headers={"location": "https://example.com/page"})
        if path == "/loop":
            return httpx.Response(302, headers={"location": "https://example.com/loop"})
        if path == "/pdf":
            return httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
        if path == "/big":
            return httpx.Response(
                200, content=b"a" * (MAX_PAGE_BYTES + 10), headers={"content-type": "text/plain"}
            )
        if path == "/raw":
            return httpx.Response(200, content=b"<html><p>untyped</p></html>")
        if path == "/down":
            raise httpx.ConnectError("refused")
        return httpx.Response(404)

    web = _web(handler)
    page = await web.fetch("https://example.com/moved")
    assert (page.url, page.title, page.content_type, page.text) == (
        "https://example.com/page",
        "T",
        "text/html",
        "Hello",
    )
    assert len((await web.fetch("https://example.com/big")).text) == MAX_PAGE_BYTES
    assert (await web.fetch("https://example.com/raw")).text == "untyped"
    failures = {
        "ftp://example.com/x": "only http and https",
        "https://example.com/missing": "returned HTTP 404",
        "https://example.com/pdf": "application/pdf, not a text page",
        "https://example.com/loop": "more than 5 redirects",
        "https://example.com/down": "could not fetch",
        "http://127.0.0.1:18080/api": "private or local address",
        "https://unknown.example/": "could not resolve",
    }
    for url, message in failures.items():
        with pytest.raises(WebError, match=message):
            await web.fetch(url)


class SearchClient:
    def __init__(self, *replies: list[dict[str, object]] | Exception) -> None:
        self.replies = list(replies)
        self.options: list[Any] = []

    async def chat_stream(
        self, model: str, messages: Any, *, tools: Any = (), options: Any = None
    ) -> Any:
        self.options.append(options)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        for chunk in reply:
            yield chunk


def _citation(url: str, title: str | None = None) -> dict[str, object]:
    return {"type": "url_citation", "url_citation": {"url": url, "title": title}}


SEARCH_REPLY: list[dict[str, object]] = [
    {"choices": [{"delta": {"content": "Use -ub 512 [1]."}}]},
    {
        "choices": [
            {
                "delta": {
                    "annotations": [
                        _citation("https://a.example", "A"),
                        _citation("https://a.example", "A again"),
                        _citation("https://b.example"),
                        {"type": "other"},
                    ]
                }
            }
        ]
    },
    {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"cost": 0.021}},
]


@pytest.mark.asyncio
async def test_web_search_returns_the_answer_sources_and_cost() -> None:
    client = SearchClient(SEARCH_REPLY, OpenRouterError("OpenRouter: no credits"))
    web = WebAccess(client, lambda: "m")
    result = await web.search("best ubatch for a 4090?")

    assert result.answer == "Use -ub 512 [1]."
    assert result.sources == [
        {"title": "A", "url": "https://a.example"},
        {"title": "https://b.example", "url": "https://b.example"},
    ]
    assert result.cost_usd == 0.021
    assert client.options[0] == {"plugins": [{"id": "web", "max_results": 5}]}
    with pytest.raises(WebError, match="no credits"):
        await web.search("again")
    with pytest.raises(WebError, match="no OpenRouter model"):
        await WebAccess(client, lambda: None).search("x")


# Tools and routes ------------------------------------------------------------------------------


def test_a_web_search_costs_are_added_to_the_conversation(tmp_path: Path, login: Login) -> None:
    scripted = ScriptedClient(
        tool_reply("web_search", {"query": "ubatch 4090"}),
        SEARCH_REPLY,
        text_reply("512 is the usual pick.", cost=0.01),
    )
    with TestClient(_app(tmp_path, scripted), client=LOCAL) as client:
        _, conversation_id = _setup(client, login, tmp_path)
        client.post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"content": "search"}
        )
        _wait(client)
        detail = _detail(client, conversation_id)

    result = next(message for message in detail["messages"] if message["role"] == "tool")
    assert json.loads(result["content"])["sources"][0]["url"] == "https://a.example"
    assert result["summary"]["summary"] == "2 sources for 'ubatch 4090'"
    assert result["cost_usd"] == 0.021
    assert detail["cost_usd"] == pytest.approx(0.005 + 0.021 + 0.01)
    assert scripted.requests[1]["messages"][0]["content"] == SEARCH_PROMPT
    assert "## Research library" in scripted.requests[0]["messages"][0]["content"]


def test_fetch_and_research_tools(tmp_path: Path, login: Login) -> None:
    long_text = "word " * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=long_text, headers={"content-type": "text/plain"})

    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        services = app.state.assistant_engine.services
        object.__setattr__(services, "web", _web(handler))
        first = json.loads(_call(client, "fetch_url", {"url": "https://example.com/doc"}).text)
        rest = json.loads(
            _call(client, "fetch_url", {"url": "https://example.com/doc", "offset": 16000}).text
        )
        bad_offset = _call(client, "fetch_url", {"url": "https://example.com", "offset": -1})
        blocked = _call(client, "fetch_url", {"url": "http://localhost:18080"})
        written = _call(client, "write_research", {**FIELDS, "body": "512 wins."})
        rewritten = _call(client, "write_research", {**FIELDS, "body": "512 still wins."})
        no_body = _call(client, "write_research", FIELDS)
        invalid = _call(client, "write_research", {**FIELDS, "slug": "Bad", "body": "x"})
        found = _call(client, "search_research", {"query": "4090"})
        read = _call(client, "read_research", {"slug": "ubatch-on-4090"})
        missing = _call(client, "read_research", {"slug": "nope"})
        failed_search = _call(client, "web_search", {"query": "x"})

    assert (first["total_characters"], first["next_offset"], len(first["text"])) == (
        25000,
        16000,
        16000,
    )
    assert rest["next_offset"] is None
    assert "offset must be" in bad_offset.text
    assert "could not resolve" in blocked.text or "private or local" in blocked.text
    assert written.summary == "wrote ubatch-on-4090"
    assert rewritten.summary == "updated ubatch-on-4090"
    assert "body is required" in no_body.text
    assert "slug" in invalid.text
    assert json.loads(found.text)[0]["slug"] == "ubatch-on-4090"
    assert read.text.startswith("---\ntitle: Micro-batch size")
    assert "512 still wins." in read.text
    assert missing.ok is False
    assert failed_search.ok is False


def test_research_routes(tmp_path: Path, login: Login) -> None:
    app = _app(tmp_path, ScriptedClient())
    with TestClient(app, client=LOCAL) as client:
        _setup(client, login, tmp_path)
        library = _library(tmp_path)
        app.state.research_library = library
        library.write(FIELDS, "Body.")
        listed = client.get("/api/assistant/research").json()
        searched = client.get("/api/assistant/research", params={"q": "cuda"}).json()
        read = client.get("/api/assistant/research/ubatch-on-4090").json()
        missing = client.get("/api/assistant/research/nope")
        not_checkout = client.post(
            "/api/assistant/research/publish", json={"slugs": ["ubatch-on-4090"]}
        )
        (tmp_path / "repo" / ".git").mkdir()
        bad_slug = client.post("/api/assistant/research/publish", json={"slugs": ["../x"]})
        unknown = client.post("/api/assistant/research/publish", json={"slugs": ["nope"]})
        shipped = client.post("/api/assistant/research/publish", json={"slugs": ["kv-cache"]})
        published = client.post(
            "/api/assistant/research/publish", json={"slugs": ["ubatch-on-4090"]}
        )
        library.write({**FIELDS, "slug": "scratch"}, "Body.")
        deleted = client.delete("/api/assistant/research/scratch")
        delete_shipped = client.delete("/api/assistant/research/kv-cache")
        delete_missing = client.delete("/api/assistant/research/nope")
    with TestClient(app, client=("10.0.0.5", 50000)) as remote:
        login(remote)
        remote_problem = remote.get("/api/assistant/research").json()["publish_problem"]

    assert [item["slug"] for item in listed["documents"]] == [
        "ubatch-on-4090",
        "flash-attention",
        "kv-cache",
    ]
    assert listed["publish_problem"] == "publishing needs LlamaWebUI to run from a source checkout"
    assert [item["slug"] for item in searched["documents"]] == ["flash-attention"]
    assert read["sources"][1]["url"] == "https://github.com/ggml-org/llama.cpp"
    assert read["body"] == "Body."
    assert missing.status_code == 404
    assert not_checkout.status_code == 409
    assert bad_slug.status_code == 422
    assert unknown.status_code == 404
    assert shipped.status_code == 409
    assert published.json()["published"] == ["ubatch-on-4090"]
    assert deleted.status_code == 204
    assert delete_shipped.status_code == 409
    assert delete_missing.status_code == 404
    assert remote_problem == "publishing only works from the machine LlamaWebUI runs on"
