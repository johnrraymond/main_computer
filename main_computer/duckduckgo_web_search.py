from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlparse
from urllib.request import ProxyHandler, Request, build_opener

from main_computer.tor_web_search import SearchResult, _strip_tags, parse_duckduckgo_html_results


DEFAULT_DDG_DIRECT_URL = "https://html.duckduckgo.com/html/"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RESULTS = 5

_DIRECT_URL_ENV_NAMES = (
    "MAIN_COMPUTER_DDG_DIRECT_URL",
    "DUCKDUCKGO_DIRECT_URL",
    "DDG_DIRECT_URL",
)


class DuckDuckGoSearchError(RuntimeError):
    """Raised when direct DuckDuckGo search cannot complete."""


@dataclass(frozen=True)
class DuckDuckGoDirectEndpoint:
    """Validated direct DuckDuckGo HTML endpoint settings."""

    raw: str
    host: str
    base_url: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def resolve_duckduckgo_direct_url(
    direct_url: str | None = None,
    env: Mapping[str, str] | None = None,
) -> DuckDuckGoDirectEndpoint:
    """Resolve and validate the direct HTTPS DuckDuckGo HTML endpoint.

    This is intentionally not the Tor onion endpoint. Only DuckDuckGo HTTPS
    hosts are accepted, credentials are rejected, and ``.onion`` hosts are
    rejected so this path stays distinct from ``tor_web_search``.
    """

    values = env if env is not None else os.environ
    raw = str(direct_url or "").strip()
    if not raw:
        for name in _DIRECT_URL_ENV_NAMES:
            raw = str(values.get(name, "")).strip()
            if raw:
                break

    if not raw:
        raw = DEFAULT_DDG_DIRECT_URL

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        raise DuckDuckGoSearchError("Direct DuckDuckGo URL must use https.")
    if not host:
        raise DuckDuckGoSearchError("Direct DuckDuckGo URL must include a host.")
    if host.endswith(".onion"):
        raise DuckDuckGoSearchError("Direct DuckDuckGo search must not use an onion host.")
    if host not in {"duckduckgo.com", "html.duckduckgo.com"}:
        raise DuckDuckGoSearchError("Direct DuckDuckGo URL must use duckduckgo.com or html.duckduckgo.com.")
    if parsed.username or parsed.password:
        raise DuckDuckGoSearchError("Direct DuckDuckGo URL must not include credentials.")

    path = parsed.path or "/html/"
    if not path.endswith("/"):
        path += "/"

    base_url = f"https://{host}{path}"
    return DuckDuckGoDirectEndpoint(raw=raw, host=host, base_url=base_url)


def _http_get_direct(
    url: str,
    *,
    timeout_s: float,
    user_agent: str = "main_computer_direct_duckduckgo_search/1.0",
) -> str:
    """Fetch a URL over direct HTTPS without honoring local proxy env vars."""

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Encoding": "identity",
        },
        method="GET",
    )
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=float(timeout_s)) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status != 200:
                raise DuckDuckGoSearchError(f"Direct DuckDuckGo endpoint returned HTTP {status}.")
            content_type = response.headers.get("content-type", "")
            match = re.search(r"charset=([A-Za-z0-9_.-]+)", content_type)
            charset = match.group(1) if match else "utf-8"
            return response.read().decode(charset, errors="replace")
    except HTTPError as exc:
        raise DuckDuckGoSearchError(f"Direct DuckDuckGo endpoint returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise DuckDuckGoSearchError(f"Could not connect to direct DuckDuckGo endpoint: {exc.reason}") from exc
    except OSError as exc:
        raise DuckDuckGoSearchError(f"Could not connect to direct DuckDuckGo endpoint: {exc}") from exc


def web_search(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    *,
    direct_url: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> list[dict[str, str]]:
    """Search DuckDuckGo's public HTML endpoint over direct HTTPS.

    This function does not use Tor and does not honor proxy environment
    variables. It is meant as the clearnet DuckDuckGo counterpart to
    ``main_computer.tor_web_search.web_search``.
    """

    clean_query = str(query or "").strip()
    if not clean_query:
        return []

    if max_results < 1:
        return []

    endpoint = resolve_duckduckgo_direct_url(direct_url)
    search_url = f"{endpoint.base_url.rstrip('/')}/?q={quote_plus(clean_query)}"
    body = _http_get_direct(search_url, timeout_s=float(timeout_s))
    results: list[SearchResult] = parse_duckduckgo_html_results(body, endpoint.base_url, max_results=max_results)

    if not results:
        sample = _strip_tags(body[:1000]).replace("\n", " ")[:500]
        raise DuckDuckGoSearchError(
            "Direct DuckDuckGo route returned no parseable results. "
            "This may be rate limiting, a challenge page, or an HTML layout change. "
            f"Response sample: {sample!r}"
        )

    return [result.as_dict() for result in results]
