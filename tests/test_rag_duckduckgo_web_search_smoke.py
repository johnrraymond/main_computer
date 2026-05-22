from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest

from main_computer.ai_web_search import build_ai_web_search_context, format_ai_web_search_context
from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.duckduckgo_web_search import DuckDuckGoSearchError, resolve_duckduckgo_direct_url, web_search
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_duckduckgo_web_search_smoke import (
    DIRECT_DUCKDUCKGO_LABEL,
    DIRECT_DUCKDUCKGO_MODE,
    DIRECT_DUCKDUCKGO_PROVIDER,
    DUCKDUCKGO_RAG_SMOKE_PROMPT,
    run_rag_duckduckgo_web_search_smoke,
)
from main_computer.router import MainComputer


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.messages: Sequence[ChatMessage] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.messages = list(messages)
        web_context = "\n".join(
            message.content for message in self.messages if "Direct DuckDuckGo web search context:" in message.content
        )
        content = json.dumps({"saw_web_context": bool(web_context), "answer": "direct web grounded"})
        return ChatResponse(content=content, provider=self.name, model=self.model)


class FakeDirectDuckDuckGoSearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, query: str, max_results: int = 5, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append({"query": query, "max_results": max_results, "kwargs": kwargs})
        return [
            {
                "title": "DuckDuckGo direct search result",
                "url": "https://example.com/current-result",
                "content": "Current result returned through the injected direct DuckDuckGo pathway.",
            }
        ]


class MissingDirectDuckDuckGoSearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, query: str, max_results: int = 5, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append({"query": query, "max_results": max_results, "kwargs": kwargs})
        raise DuckDuckGoSearchError("Could not connect to direct DuckDuckGo endpoint: offline")


def _write_fixture_repo(repo: Path) -> None:
    (repo / "main_computer").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "main_computer" / "duckduckgo_web_search.py").write_text(
        "def web_search(query, max_results=5):\n"
        "    return [{'title': 'direct', 'url': 'https://example.com', 'content': 'duckduckgo html'}]\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "ai_web_search.py").write_text(
        "def build_ai_web_search_context(prompt):\n"
        "    return 'duckduckgo_direct direct_https web search context'\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "router.py").write_text(
        "class MainComputer:\n"
        "    def chat(self, prompt):\n"
        "        return 'router chat calls direct duckduckgo web_search for ai query'\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "rag_harness.py").write_text("def run_rag_harness(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_duckduckgo_web_search.py").write_text(
        "def test_duckduckgo_web_search(): pass\n",
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Main Computer Test\nDirect DuckDuckGo web search smoke fixture.\n", encoding="utf-8")


def test_direct_duckduckgo_resolver_rejects_tor_onion_url() -> None:
    with pytest.raises(DuckDuckGoSearchError, match="must not use an onion host"):
        resolve_duckduckgo_direct_url("https://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion/html/")


def test_direct_duckduckgo_web_search_parses_html_without_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_get(url: str, *, timeout_s: float, user_agent: str = "") -> str:
        calls.append({"url": url, "timeout_s": timeout_s, "user_agent": user_agent})
        return """
        <html><body>
          <a class="result__a" href="/l/?uddg=https%3A%2F%2Fexample.com%2Fdirect">Example Direct</a>
          <a class="result__snippet">Direct DuckDuckGo result snippet.</a>
        </body></html>
        """

    monkeypatch.setenv("TOR_PROXY", "socks5h://127.0.0.1:9150")
    monkeypatch.setattr("main_computer.duckduckgo_web_search._http_get_direct", fake_get)

    results = web_search("direct duckduckgo", max_results=1)

    assert calls
    assert calls[0]["url"] == "https://html.duckduckgo.com/html/?q=direct+duckduckgo"
    assert results == [
        {
            "title": "Example Direct",
            "url": "https://example.com/direct",
            "content": "Direct DuckDuckGo result snippet.",
        }
    ]


def test_ai_web_search_context_can_label_direct_duckduckgo_search() -> None:
    search = FakeDirectDuckDuckGoSearch()

    context = build_ai_web_search_context(
        "Search the web for current status",
        search_fn=search,
        max_results=2,
        provider=DIRECT_DUCKDUCKGO_PROVIDER,
        mode=DIRECT_DUCKDUCKGO_MODE,
        label=DIRECT_DUCKDUCKGO_LABEL,
    )
    rendered = format_ai_web_search_context(context)

    assert context.attempted
    assert context.ok
    assert context.provider == DIRECT_DUCKDUCKGO_PROVIDER
    assert context.mode == DIRECT_DUCKDUCKGO_MODE
    assert context.label == DIRECT_DUCKDUCKGO_LABEL
    assert search.calls == [{"query": "Search the web for current status", "max_results": 2, "kwargs": {}}]
    assert "Direct DuckDuckGo web search context:" in rendered
    assert "DuckDuckGo direct search result" in rendered


def test_router_attaches_direct_duckduckgo_results_to_ai_query(tmp_path: Path) -> None:
    workspace = tmp_path
    repo = workspace / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)

    provider = FakeProvider()
    search = FakeDirectDuckDuckGoSearch()
    computer = MainComputer(
        MainComputerConfig(workspace=workspace),
        ProjectCatalog(workspace),
        provider,
        web_search_fn=search,
        web_search_max_results=1,
        web_search_provider=DIRECT_DUCKDUCKGO_PROVIDER,
        web_search_mode=DIRECT_DUCKDUCKGO_MODE,
        web_search_label=DIRECT_DUCKDUCKGO_LABEL,
    )

    response = computer.chat("Search the web for current direct DuckDuckGo status")

    assert search.calls[0]["query"] == "Search the web for current direct DuckDuckGo status"
    assert search.calls[0]["max_results"] == 1
    prompt_text = "\n".join(message.content for message in provider.messages)
    assert "Direct DuckDuckGo web search context:" in prompt_text
    assert "DuckDuckGo direct search result" in prompt_text
    assert response.metadata["web_search"]["ok"] is True
    assert response.metadata["web_search"]["provider"] == DIRECT_DUCKDUCKGO_PROVIDER
    assert response.metadata["web_search"]["mode"] == DIRECT_DUCKDUCKGO_MODE


def test_rag_duckduckgo_web_search_smoke_connects_ai_query_to_direct_duckduckgo_search(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = FakeDirectDuckDuckGoSearch()

    report = run_rag_duckduckgo_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=DUCKDUCKGO_RAG_SMOKE_PROMPT,
        run_id="duckduckgo_web_search_smoke",
        max_results=1,
        verbose=False,
    )

    assert report.ok
    assert search.calls == [{"query": DUCKDUCKGO_RAG_SMOKE_PROMPT, "max_results": 1, "kwargs": {}}]
    assert report.scenario == "rag_ai_query_direct_duckduckgo_web_search"
    assert report.web_search["provider"] == DIRECT_DUCKDUCKGO_PROVIDER
    assert report.web_search["mode"] == DIRECT_DUCKDUCKGO_MODE
    assert report.web_search["label"] == DIRECT_DUCKDUCKGO_LABEL
    assert report.web_search["ok"] is True
    assert any(path == "main_computer/duckduckgo_web_search.py" for path in report.retrieved_paths)
    assert Path(report.report_path).exists()


def test_rag_duckduckgo_web_search_smoke_default_run_ids_do_not_collide(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    output_root = tmp_path / "runs"

    first = run_rag_duckduckgo_web_search_smoke(
        repo_dir=repo,
        output_root=output_root,
        provider=FakeProvider(),
        search_fn=FakeDirectDuckDuckGoSearch(),
        prompt=DUCKDUCKGO_RAG_SMOKE_PROMPT,
        max_results=1,
        verbose=False,
    )
    second = run_rag_duckduckgo_web_search_smoke(
        repo_dir=repo,
        output_root=output_root,
        provider=FakeProvider(),
        search_fn=FakeDirectDuckDuckGoSearch(),
        prompt=DUCKDUCKGO_RAG_SMOKE_PROMPT,
        max_results=1,
        verbose=False,
    )

    assert first.ok
    assert second.ok
    assert first.run_id != second.run_id
    assert first.rag_run_id != second.rag_run_id
    assert first.output_dir != second.output_dir
    assert Path(first.output_dir).name == first.run_id
    assert Path(second.output_dir).name == second.run_id
    assert Path(first.report_path).parent == Path(first.output_dir)
    assert Path(second.report_path).parent == Path(second.output_dir)
    assert Path(first.report_path).exists()
    assert Path(second.report_path).exists()


def test_rag_duckduckgo_web_search_smoke_requires_live_results_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = MissingDirectDuckDuckGoSearch()

    report = run_rag_duckduckgo_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=DUCKDUCKGO_RAG_SMOKE_PROMPT,
        run_id="duckduckgo_web_search_smoke_missing_live",
        max_results=1,
        verbose=False,
    )

    assert not report.ok
    assert report.live_web_search_required is True
    assert search.calls == [{"query": DUCKDUCKGO_RAG_SMOKE_PROMPT, "max_results": 1, "kwargs": {}}]
    assert report.web_search["attempted"] is True
    assert report.web_search["ok"] is False
    assert any("Direct DuckDuckGo web-search context was not available" in failure for failure in report.failures)
    assert "Expected at least one direct DuckDuckGo web-search result in response metadata." in report.failures


def test_rag_duckduckgo_web_search_smoke_allow_offline_downgrades_missing_live_result(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = MissingDirectDuckDuckGoSearch()

    report = run_rag_duckduckgo_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=DUCKDUCKGO_RAG_SMOKE_PROMPT,
        run_id="duckduckgo_web_search_smoke_allow_offline",
        allow_offline=True,
        max_results=1,
        verbose=False,
    )

    assert report.ok
    assert report.live_web_search_required is False
    assert report.failures == []
    assert any("Direct DuckDuckGo web-search context was not available" in warning for warning in report.warnings)
    assert any("No live direct DuckDuckGo web-search result" in warning for warning in report.warnings)
