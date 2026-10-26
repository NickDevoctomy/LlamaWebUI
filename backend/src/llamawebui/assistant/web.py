"""Web access for the assistant: search through OpenRouter's web plugin, and reading pages.

Fetching is limited to public addresses. Every connection, including each redirect, is
checked after DNS resolution and made to the address that was checked, so a page cannot
point the assistant at this machine, the local network or the control plane.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, cast

import httpcore
import httpx

from llamawebui.assistant.openrouter_client import ChatClient, OpenRouterError

MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 5
SEARCH_RESULTS = 5
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
)
# A search that streams for longer than this is abandoned.
SEARCH_TIMEOUT_SECONDS = 90
SEARCH_PROMPT = (
    "Answer the question using the web search results. Be factual and concise, give exact "
    "figures, versions and flags where the sources state them, and cite the sources you use "
    "as [1], [2]. Say so when the results do not answer the question."
)

Resolver = Callable[[str, int], Awaitable[list[str]]]


class WebError(RuntimeError):
    """A search or fetch that failed or was refused; the message goes to the model."""


class BlockedAddressError(WebError):
    pass


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


async def checked_addresses(host: str, port: int, resolve: Resolver) -> list[str]:
    """The host's addresses, if every one is public; raises BlockedAddressError if not."""
    try:
        addresses = [host] if _is_ip(host) else await resolve(host, port)
    except OSError as error:
        raise WebError(f"could not resolve {host}") from error
    if not addresses:
        raise WebError(f"could not resolve {host}")
    blocked = [address for address in addresses if not public_address(address)]
    if blocked:
        raise BlockedAddressError(
            f"{host} resolves to a private or local address ({blocked[0]}); only public "
            "web addresses can be fetched"
        )
    return addresses


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Connects only to public addresses, and to the address it checked."""

    def __init__(
        self, resolve: Resolver, backend: httpcore.AsyncNetworkBackend | None = None
    ) -> None:
        self._resolve = resolve
        self._backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await checked_addresses(host, port, self._resolve)
        return await self._backend.connect_tcp(
            addresses[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedAddressError("unix sockets cannot be fetched")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport, connecting through GuardedBackend. TLS still verifies the host name."""

    def __init__(self, resolve: Resolver = resolve_host) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=GuardedBackend(resolve),
        )


class _TextExtractor(HTMLParser):
    """A page's readable text: headings, paragraphs, list items and code, without scripts."""

    SKIP = frozenset(
        {"title", "script", "style", "noscript", "svg", "template", "head", "nav", "footer", "form"}
    )
    BLOCK = frozenset(
        {
            "p",
            "div",
            "section",
            "article",
            "main",
            "br",
            "tr",
            "table",
            "ul",
            "ol",
            "pre",
            "blockquote",
            "header",
            "aside",
            "dl",
            "dt",
            "dd",
            "hr",
            "figure",
            "figcaption",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skipping = 0
        self._in_title = False
        self._pre = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self._skipping += 1
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")
        elif tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "pre":
            self._pre += 1
            self.parts.append("\n```\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP:
            self._skipping = max(self._skipping - 1, 0)
        elif tag == "pre":
            self._pre = max(self._pre - 1, 0)
            self.parts.append("\n```\n")
        elif tag in self.BLOCK or tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skipping:
            return
        self.parts.append(data if self._pre else re.sub(r"\s+", " ", data))


def html_text(html: str) -> tuple[str, str]:
    """The title and readable text of an HTML page."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    # Tidy the spacing around lines, except inside code blocks (the odd ``` segments).
    segments = "".join(parser.parts).split("```")
    for index in range(0, len(segments), 2):
        segment = re.sub(r"[ \t]+\n", "\n", segments[index])
        segment = re.sub(r"\n[ \t]+", "\n", segment)
        segments[index] = re.sub(r"\n{3,}", "\n\n", segment)
    return parser.title.strip(), "```".join(segments).strip()


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    content_type: str
    text: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    answer: str
    sources: list[dict[str, str]] = field(default_factory=list)
    cost_usd: float = 0.0


def _take_search_chunk(
    chunk: Mapping[str, object], answer: list[str], sources: dict[str, dict[str, str]]
) -> None:
    """Add a streamed chunk's text to the answer and its URL citations to the sources."""
    choices = chunk.get("choices")
    for choice in choices if isinstance(choices, list) else ():
        for key in ("delta", "message"):
            part = choice.get(key) if isinstance(choice, dict) else None
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("content"), str):
                answer.append(part["content"])
            annotations = part.get("annotations")
            for annotation in annotations if isinstance(annotations, list) else ():
                citation = annotation.get("url_citation") if isinstance(annotation, dict) else None
                if isinstance(citation, dict) and isinstance(citation.get("url"), str):
                    url = citation["url"]
                    sources.setdefault(
                        url, {"title": str(citation.get("title") or url), "url": url}
                    )


class WebAccess:
    def __init__(
        self,
        client: ChatClient,
        model: Callable[[], str | None],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: Resolver = resolve_host,
    ) -> None:
        self._client = client
        self._model = model
        self._transport = transport
        self._resolve = resolve

    async def search(self, query: str) -> SearchResult:
        """Search the web with OpenRouter's web plugin; the answer cites its sources."""
        model = self._model()
        if not model:
            raise WebError("no OpenRouter model is selected")
        answer: list[str] = []
        sources: dict[str, dict[str, str]] = {}
        usage: Mapping[str, object] = {}
        try:
            async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):
                async for chunk in self._client.chat_stream(
                    model,
                    [
                        {"role": "system", "content": SEARCH_PROMPT},
                        {"role": "user", "content": query},
                    ],
                    options={"plugins": [{"id": "web", "max_results": SEARCH_RESULTS}]},
                ):
                    if isinstance(chunk.get("usage"), dict):
                        usage = cast(dict[str, object], chunk["usage"])
                    _take_search_chunk(chunk, answer, sources)
        except TimeoutError as error:
            raise WebError(
                f"the search took longer than {SEARCH_TIMEOUT_SECONDS} s and was abandoned"
            ) from error
        except OpenRouterError as error:
            raise WebError(str(error)) from error
        cost = usage.get("cost")
        return SearchResult(
            "".join(answer).strip(),
            list(sources.values()),
            float(cost) if isinstance(cost, int | float) else 0.0,
        )

    async def fetch(self, url: str) -> Page:
        """A web page as text: HTML is reduced to its readable content; at most 1 MB is read."""
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise WebError("only http and https URLs can be fetched")
        # Checked here for a clear refusal; the transport checks every connection again.
        await checked_addresses(
            parsed.host, parsed.port or (443 if parsed.scheme == "https" else 80), self._resolve
        )
        transport = self._transport or GuardedTransport(self._resolve)
        try:
            async with (
                httpx.AsyncClient(
                    transport=transport,
                    follow_redirects=True,
                    max_redirects=MAX_REDIRECTS,
                    timeout=httpx.Timeout(20.0),
                    headers={
                        "User-Agent": "LlamaWebUI assistant",
                        "Accept": "text/html, text/plain;q=0.9, */*;q=0.1",
                    },
                    trust_env=False,
                ) as client,
                client.stream("GET", url) as response,
            ):
                if response.is_error:
                    raise WebError(f"{response.url} returned HTTP {response.status_code}")
                content_type = (
                    response.headers.get("content-type", "").split(";")[0].strip().lower()
                )
                if content_type and not content_type.startswith(TEXT_TYPES):
                    raise WebError(f"{response.url} is {content_type}, not a text page")
                data = bytearray()
                async for piece in response.aiter_bytes():
                    data.extend(piece)
                    if len(data) >= MAX_PAGE_BYTES:
                        del data[MAX_PAGE_BYTES:]
                        break
                text = data.decode(response.encoding or "utf-8", errors="replace")
                final_url = str(response.url)
        except httpx.TooManyRedirects as error:
            raise WebError(f"more than {MAX_REDIRECTS} redirects") from error
        except httpx.HTTPError as error:
            raise WebError(f"could not fetch {url} ({type(error).__name__})") from error
        title = ""
        if content_type in {"text/html", "application/xhtml+xml"} or (
            not content_type and "<html" in text[:2000].lower()
        ):
            title, text = html_text(text)
        return Page(final_url, title, content_type or "text/plain", text)
