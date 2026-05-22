from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import re
from typing import Any

from main_computer.tor_web_search import TorOnlySearchError, web_search


DEFAULT_WEB_SEARCH_MAX_RESULTS = 3
DEFAULT_WEB_SEARCH_QUERY_MAX_CHARS = 500
DEFAULT_WEB_SEARCH_PROVIDER = "duckduckgo_onion_tor"
DEFAULT_WEB_SEARCH_MODE = "tor_only"
DEFAULT_WEB_SEARCH_LABEL = "Tor-only DuckDuckGo"

_WEB_SEARCH_TRIGGER_PHRASES = (
    "search the web",
    "web search",
    "search online",
    "look up",
    "lookup",
    "duckduckgo",
    "internet search",
)

_WEB_SEARCH_TRIGGER_TOKENS = {
    "current",
    "latest",
    "news",
    "today",
    "recent",
    "online",
    "internet",
    "web",
}


@dataclass(frozen=True)
class AiWebSearchContext:
    """Web-search context attached to an AI query.

    The default search function is ``main_computer.tor_web_search.web_search``,
    which only talks to DuckDuckGo's onion HTML endpoint through a configured
    local Tor SOCKS5h proxy and has no direct-web fallback.
    """

    query: str
    attempted: bool
    ok: bool
    provider: str = DEFAULT_WEB_SEARCH_PROVIDER
    mode: str = DEFAULT_WEB_SEARCH_MODE
    label: str = DEFAULT_WEB_SEARCH_LABEL
    results: list[dict[str, str]] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


WebSearchFn = Callable[..., list[dict[str, str]]]


def should_search_web(prompt: str) -> bool:
    """Return true when a user AI prompt explicitly asks for online/current context."""

    lowered = str(prompt or "").strip().lower()
    if not lowered:
        return False

    if any(phrase in lowered for phrase in _WEB_SEARCH_TRIGGER_PHRASES):
        return True

    tokens = set(re.findall(r"[a-z0-9_'-]+", lowered))
    return bool(tokens.intersection(_WEB_SEARCH_TRIGGER_TOKENS))


def normalize_web_search_query(prompt: str, *, max_chars: int = DEFAULT_WEB_SEARCH_QUERY_MAX_CHARS) -> str:
    """Convert a user AI prompt into a bounded DuckDuckGo query string."""

    query = re.sub(r"\s+", " ", str(prompt or "")).strip()
    if len(query) <= max_chars:
        return query
    return query[:max_chars].rstrip()


def build_ai_web_search_context(
    prompt: str,
    *,
    search_fn: WebSearchFn | None = None,
    max_results: int = DEFAULT_WEB_SEARCH_MAX_RESULTS,
    provider: str = DEFAULT_WEB_SEARCH_PROVIDER,
    mode: str = DEFAULT_WEB_SEARCH_MODE,
    label: str = DEFAULT_WEB_SEARCH_LABEL,
) -> AiWebSearchContext:
    """Connect a user AI query to the configured DuckDuckGo search pathway when requested.

    Search failures are returned as context instead of escaping, so a missing
    or unreachable web-search route cannot break unrelated AI chat.
    """

    query = normalize_web_search_query(prompt)
    if not query or not should_search_web(query):
        return AiWebSearchContext(query=query, attempted=False, ok=False, provider=provider, mode=mode, label=label)

    search = search_fn or web_search
    try:
        results = search(query, max_results=max(1, int(max_results)))
    except TorOnlySearchError as exc:
        return AiWebSearchContext(query=query, attempted=True, ok=False, provider=provider, mode=mode, label=label, error=str(exc))
    except Exception as exc:  # pragma: no cover - defensive around injected/live providers.
        return AiWebSearchContext(
            query=query,
            attempted=True,
            ok=False,
            provider=provider,
            mode=mode,
            label=label,
            error=f"{type(exc).__name__}: {exc}",
        )

    cleaned_results: list[dict[str, str]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        title = str(result.get("title", "") or "").strip()
        url = str(result.get("url", "") or "").strip()
        content = str(result.get("content", "") or "").strip()
        if title or url or content:
            cleaned_results.append({"title": title, "url": url, "content": content})
        if len(cleaned_results) >= max_results:
            break

    return AiWebSearchContext(
        query=query,
        attempted=True,
        ok=bool(cleaned_results),
        provider=provider,
        mode=mode,
        label=label,
        results=cleaned_results,
    )


def format_ai_web_search_context(context: AiWebSearchContext) -> str:
    """Format search results as a model-readable system context block."""

    if not context.attempted:
        return ""

    label = context.label or context.provider or "DuckDuckGo"
    header = [
        f"{label} web search context:",
        f"- Query: {context.query}",
        f"- Route: {context.provider}",
        f"- Mode: {context.mode}",
    ]

    if not context.ok:
        error = context.error or "No parseable results were returned."
        return "\n".join(
            [
                *header,
                f"- Status: unavailable ({error})",
                "Do not claim live web search succeeded. Tell the user the web search context was unavailable.",
            ]
        )

    lines = [
        *header,
        f"- Status: ok ({len(context.results)} result(s))",
        "Use these results as current web context. Do not invent uncited web facts beyond them.",
    ]
    for index, result in enumerate(context.results, start=1):
        lines.extend(
            [
                f"Result {index}:",
                f"Title: {result.get('title', '')}",
                f"URL: {result.get('url', '')}",
                f"Snippet: {result.get('content', '')}",
            ]
        )
    return "\n".join(lines)
