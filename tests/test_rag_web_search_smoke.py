from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from main_computer.ai_web_search import build_ai_web_search_context, format_ai_web_search_context, should_search_web
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_web_search_smoke import WEB_SEARCH_RAG_SMOKE_PROMPT, run_rag_web_search_smoke
from main_computer.router import MainComputer
from main_computer.tor_web_search import TorOnlySearchError
from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.messages: Sequence[ChatMessage] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.messages = list(messages)
        web_context = "\n".join(message.content for message in self.messages if "Tor-only DuckDuckGo web search context:" in message.content)
        content = json.dumps({"saw_web_context": bool(web_context), "answer": "web grounded"})
        return ChatResponse(content=content, provider=self.name, model=self.model)


class FakeTorDuckDuckGoSearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, query: str, max_results: int = 5, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append({"query": query, "max_results": max_results, "kwargs": kwargs})
        return [
            {
                "title": "DuckDuckGo onion search result",
                "url": "https://example.com/current-result",
                "content": "Current result returned through the injected Tor DuckDuckGo pathway.",
            }
        ]


class MissingTorSearch:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, query: str, max_results: int = 5, **kwargs: object) -> list[dict[str, str]]:
        self.calls.append({"query": query, "max_results": max_results, "kwargs": kwargs})
        raise TorOnlySearchError(
            "Tor proxy is not configured. Set TOR_PROXY=socks5h://127.0.0.1:9050 "
            "or TOR_PROXY=socks5h://127.0.0.1:9150."
        )


def _write_fixture_repo(repo: Path) -> None:
    (repo / "main_computer").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "main_computer" / "tor_web_search.py").write_text(
        "def web_search(query, max_results=5):\n"
        "    return [{'title': 'tor', 'url': 'https://example.com', 'content': 'duckduckgo onion'}]\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "ai_web_search.py").write_text(
        "def build_ai_web_search_context(prompt):\n"
        "    return 'duckduckgo_onion_tor web search context'\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "router.py").write_text(
        "class MainComputer:\n"
        "    def chat(self, prompt):\n"
        "        return 'router chat calls web_search for ai query'\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "rag_harness.py").write_text("def run_rag_harness(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_tor_web_search.py").write_text("def test_tor_web_search(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Main Computer Test\nTor DuckDuckGo web search smoke fixture.\n", encoding="utf-8")


def test_ai_web_search_context_uses_injected_tor_duckduckgo_search() -> None:
    search = FakeTorDuckDuckGoSearch()

    context = build_ai_web_search_context("Search the web for current status", search_fn=search, max_results=2)
    rendered = format_ai_web_search_context(context)

    assert context.attempted
    assert context.ok
    assert context.provider == "duckduckgo_onion_tor"
    assert context.mode == "tor_only"
    assert search.calls == [{"query": "Search the web for current status", "max_results": 2, "kwargs": {}}]
    assert "Tor-only DuckDuckGo web search context:" in rendered
    assert "DuckDuckGo onion search result" in rendered


def test_ai_web_search_context_ignores_plain_local_ai_prompt() -> None:
    search = FakeTorDuckDuckGoSearch()

    context = build_ai_web_search_context("Summarize the workspace README", search_fn=search)

    assert not context.attempted
    assert not context.ok
    assert search.calls == []
    assert should_search_web("latest release news")
    assert not should_search_web("summarize local readme")


def test_router_attaches_tor_duckduckgo_results_to_ai_query(tmp_path: Path) -> None:
    workspace = tmp_path
    repo = workspace / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)

    provider = FakeProvider()
    search = FakeTorDuckDuckGoSearch()
    computer = MainComputer(
        MainComputerConfig(workspace=workspace),
        ProjectCatalog(workspace),
        provider,
        web_search_fn=search,
        web_search_max_results=1,
    )

    response = computer.chat("Search the web for current DuckDuckGo onion status")

    assert search.calls[0]["query"] == "Search the web for current DuckDuckGo onion status"
    assert search.calls[0]["max_results"] == 1
    prompt_text = "\n".join(message.content for message in provider.messages)
    assert "Tor-only DuckDuckGo web search context:" in prompt_text
    assert "DuckDuckGo onion search result" in prompt_text
    assert response.metadata["web_search"]["ok"] is True
    assert response.metadata["web_search"]["provider"] == "duckduckgo_onion_tor"
    assert response.metadata["web_search"]["mode"] == "tor_only"


def test_rag_web_search_smoke_connects_ai_query_to_tor_duckduckgo_search(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = FakeTorDuckDuckGoSearch()

    report = run_rag_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=WEB_SEARCH_RAG_SMOKE_PROMPT,
        run_id="web_search_smoke",
        strict=True,
        max_results=1,
        verbose=False,
    )

    assert report.ok
    assert search.calls == [{"query": WEB_SEARCH_RAG_SMOKE_PROMPT, "max_results": 1, "kwargs": {}}]
    assert report.scenario == "rag_ai_query_tor_duckduckgo_web_search"
    assert report.web_search["provider"] == "duckduckgo_onion_tor"
    assert report.web_search["mode"] == "tor_only"
    assert report.web_search["ok"] is True
    assert any(path == "main_computer/tor_web_search.py" for path in report.retrieved_paths)
    assert Path(report.report_path).exists()


def test_rag_web_search_smoke_default_run_ids_do_not_collide(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    output_root = tmp_path / "runs"

    first = run_rag_web_search_smoke(
        repo_dir=repo,
        output_root=output_root,
        provider=FakeProvider(),
        search_fn=FakeTorDuckDuckGoSearch(),
        prompt=WEB_SEARCH_RAG_SMOKE_PROMPT,
        strict=True,
        max_results=1,
        verbose=False,
    )
    second = run_rag_web_search_smoke(
        repo_dir=repo,
        output_root=output_root,
        provider=FakeProvider(),
        search_fn=FakeTorDuckDuckGoSearch(),
        prompt=WEB_SEARCH_RAG_SMOKE_PROMPT,
        strict=True,
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


def test_rag_web_search_smoke_non_strict_passes_when_live_tor_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = MissingTorSearch()

    report = run_rag_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=WEB_SEARCH_RAG_SMOKE_PROMPT,
        run_id="web_search_smoke_missing_tor",
        strict=False,
        max_results=1,
        verbose=False,
    )

    assert report.ok
    assert search.calls == [{"query": WEB_SEARCH_RAG_SMOKE_PROMPT, "max_results": 1, "kwargs": {}}]
    assert report.web_search["attempted"] is True
    assert report.web_search["ok"] is False
    assert "Tor proxy is not configured" in report.web_search["error"]
    assert report.failures == []
    assert any("Tor DuckDuckGo web-search context was not available" in warning for warning in report.warnings)
    assert any("No live web-search result" in warning for warning in report.warnings)


def test_rag_web_search_smoke_strict_fails_when_live_tor_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "main_computer_test"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeProvider()
    search = MissingTorSearch()

    report = run_rag_web_search_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        search_fn=search,
        prompt=WEB_SEARCH_RAG_SMOKE_PROMPT,
        run_id="web_search_smoke_missing_tor_strict",
        strict=True,
        max_results=1,
        verbose=False,
    )

    assert not report.ok
    assert search.calls == [{"query": WEB_SEARCH_RAG_SMOKE_PROMPT, "max_results": 1, "kwargs": {}}]
    assert report.web_search["attempted"] is True
    assert report.web_search["ok"] is False
    assert any("Tor DuckDuckGo web-search context was not available" in failure for failure in report.failures)
    assert "Expected at least one web-search result in response metadata." in report.failures
