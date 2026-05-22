from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from main_computer.ai_web_search import WebSearchFn
from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.duckduckgo_web_search import web_search as duckduckgo_direct_web_search
from main_computer.models import ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_harness import run_rag_harness
from main_computer.router import MainComputer


DEFAULT_RAG_DUCKDUCKGO_WEB_SEARCH_SMOKE_RUN_ID_PREFIX = "rag_duckduckgo_web_search_smoke"
DIRECT_DUCKDUCKGO_PROVIDER = "duckduckgo_direct"
DIRECT_DUCKDUCKGO_MODE = "direct_https"
DIRECT_DUCKDUCKGO_LABEL = "Direct DuckDuckGo"


DUCKDUCKGO_RAG_SMOKE_PROMPT = (
    "Search the web with direct DuckDuckGo for current Main Computer DuckDuckGo search context, "
    "then answer from the web results and the local RAG context."
)

DUCKDUCKGO_RAG_QUERIES = [
    "direct duckduckgo web search",
    "duckduckgo html search",
    "web_search",
    "ai web search context",
    "router chat",
    "rag harness",
]


@dataclass(frozen=True)
class RagDuckDuckGoWebSearchSmokeReport:
    ok: bool
    run_id: str
    rag_run_id: str
    scenario: str
    output_dir: str
    report_path: str
    prompt: str
    retrieved_paths: list[str]
    web_search: dict[str, Any]
    response_provider: str
    response_model: str
    response_preview: str
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False
    live_web_search_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_path_count"] = len(self.retrieved_paths)
        data["retrieved_path_preview"] = self.retrieved_paths[:12]
        return data


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, default=str)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _shorten(value: Any, *, limit: int = 500) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _get_local_ollama_provider() -> LLMProvider:
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=config.model or "gemma4:26b",
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        fallback=config.fallback,
    )


def _workspace_for_repo(repo_dir: Path) -> Path:
    return repo_dir.parent if repo_dir.parent.exists() else repo_dir


def _new_default_run_id(prefix: str = DEFAULT_RAG_DUCKDUCKGO_WEB_SEARCH_SMOKE_RUN_ID_PREFIX) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}_{timestamp}_{uuid4().hex[:8]}"


def _new_unique_run_id(
    output_base: Path,
    prefix: str = DEFAULT_RAG_DUCKDUCKGO_WEB_SEARCH_SMOKE_RUN_ID_PREFIX,
) -> str:
    for _ in range(100):
        run_id = _new_default_run_id(prefix)
        if not (output_base / run_id).exists() and not (output_base / f"{run_id}_rag").exists():
            return run_id
    return _new_default_run_id(prefix)


def _retrieved_paths(result: Any) -> list[str]:
    retrieval = getattr(result, "retrieval", None)
    candidates = getattr(retrieval, "candidates", []) if retrieval is not None else []
    return [str(getattr(candidate, "path", "")) for candidate in candidates if str(getattr(candidate, "path", ""))]


def _validate_duckduckgo_web_search_smoke(
    *,
    rag_ok: bool,
    rag_error: str | None,
    retrieved_paths: list[str],
    response: ChatResponse,
    prompt: str,
    strict: bool,
    live_web_search_required: bool,
) -> tuple[bool, list[str], list[str]]:
    warnings: list[str] = []
    failures: list[str] = []

    if not rag_ok:
        failures.append(f"RAG setup failed before web-search chat: {rag_error or 'unknown error'}")

    retrieved = set(retrieved_paths)
    expected_any = {
        "main_computer/duckduckgo_web_search.py",
        "main_computer/ai_web_search.py",
        "main_computer/router.py",
    }
    if not retrieved.intersection(expected_any):
        warnings.append(
            "RAG retrieval did not surface the expected direct DuckDuckGo/search/router connector files; "
            f"retrieved {retrieved_paths[:8] or 'none'}."
        )

    web_search = response.metadata.get("web_search")
    if not isinstance(web_search, dict):
        failures.append("AI response metadata did not include web_search details.")
        web_search = {}

    # These assertions prove the user's AI query reached the direct DuckDuckGo
    # connector path. They do not depend on external network success.
    if web_search.get("attempted") is not True:
        failures.append("The user AI query did not trigger a web-search attempt.")
    if web_search.get("provider") != DIRECT_DUCKDUCKGO_PROVIDER:
        failures.append(f"Expected direct DuckDuckGo provider metadata, got {web_search.get('provider')!r}.")
    if web_search.get("mode") != DIRECT_DUCKDUCKGO_MODE:
        failures.append(f"Expected direct_https web-search mode, got {web_search.get('mode')!r}.")
    if str(web_search.get("query") or "").strip() != prompt:
        failures.append("Web-search query did not preserve the user's AI prompt.")

    if web_search.get("ok") is not True:
        message = f"Direct DuckDuckGo web-search context was not available: {web_search.get('error') or 'no results'}"
        if live_web_search_required:
            failures.append(message)
        else:
            warnings.append(message)

    if not web_search.get("results"):
        message = "No live direct DuckDuckGo web-search result was present in response metadata."
        if live_web_search_required:
            failures.append("Expected at least one direct DuckDuckGo web-search result in response metadata.")
        else:
            warnings.append(message)

    if strict and warnings:
        failures.extend(warnings)

    ok = not failures
    return ok, warnings, failures


def run_rag_duckduckgo_web_search_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    provider: LLMProvider | None = None,
    search_fn: WebSearchFn | None = None,
    prompt: str = DUCKDUCKGO_RAG_SMOKE_PROMPT,
    run_id: str | None = None,
    strict: bool = False,
    allow_offline: bool = False,
    max_results: int = 3,
    verbose: bool = True,
    dump_json: bool = False,
) -> RagDuckDuckGoWebSearchSmokeReport:
    """Run a smoke test that connects a user AI query to direct DuckDuckGo search.

    The RAG phase proves the repository context can find the direct DuckDuckGo
    connector/search code. The AI phase sends the same user prompt through
    ``MainComputer.chat`` with the Tor search function replaced by the direct
    DuckDuckGo HTTPS function. By default, this smoke requires live DuckDuckGo
    results because no Tor proxy setup is needed. Use ``allow_offline=True`` only
    when you intentionally want a wiring-only smoke on an offline machine.
    """

    repo_path = Path(repo_dir).resolve()
    output_base = Path(output_root or (repo_path / "diagnostics_output" / "rag_runs"))
    run_id = run_id or _new_unique_run_id(output_base)
    rag_run_id = f"{run_id}_rag"
    smoke_output_dir = output_base / run_id

    if provider is None:
        provider = _get_local_ollama_provider()

    if search_fn is None:
        search_fn = duckduckgo_direct_web_search

    live_web_search_required = not allow_offline

    if verbose:
        print(
            "[rag-duckduckgo-web-search-smoke] starting "
            f"repo_dir={repo_path} output_root={output_base} run_id={run_id} "
            f"strict={strict} allow_offline={allow_offline}"
        )

    rag_result = run_rag_harness(
        prompt=prompt,
        repo_dir=repo_path,
        queries=DUCKDUCKGO_RAG_QUERIES,
        output_root=output_base,
        use_model=False,
        run_id=rag_run_id,
    )
    retrieved_paths = _retrieved_paths(rag_result)

    workspace = _workspace_for_repo(repo_path)
    config = MainComputerConfig(workspace=workspace)
    computer = MainComputer(
        config=config,
        catalog=ProjectCatalog(workspace),
        provider=provider,
        web_search_fn=search_fn,
        web_search_max_results=max_results,
        web_search_provider=DIRECT_DUCKDUCKGO_PROVIDER,
        web_search_mode=DIRECT_DUCKDUCKGO_MODE,
        web_search_label=DIRECT_DUCKDUCKGO_LABEL,
    )
    response = computer.chat(prompt)

    ok, warnings, failures = _validate_duckduckgo_web_search_smoke(
        rag_ok=rag_result.ok,
        rag_error=rag_result.error,
        retrieved_paths=retrieved_paths,
        response=response,
        prompt=prompt,
        strict=strict,
        live_web_search_required=live_web_search_required,
    )

    report_path = smoke_output_dir / "rag_duckduckgo_web_search_smoke_report.json"
    report = RagDuckDuckGoWebSearchSmokeReport(
        ok=ok,
        run_id=run_id,
        rag_run_id=rag_run_id,
        scenario="rag_ai_query_direct_duckduckgo_web_search",
        output_dir=str(smoke_output_dir),
        report_path=str(report_path),
        prompt=prompt,
        retrieved_paths=retrieved_paths,
        web_search=dict(response.metadata.get("web_search") if isinstance(response.metadata.get("web_search"), dict) else {}),
        response_provider=response.provider,
        response_model=response.model,
        response_preview=_shorten(response.content, limit=500),
        warnings=warnings,
        failures=failures,
        strict=strict,
        live_web_search_required=live_web_search_required,
    )
    _write_json(report_path, report.to_dict())

    if verbose:
        print("[rag-duckduckgo-web-search-smoke] validation report:")
        print(_json_dumps(report.to_dict() if dump_json else {
            "ok": report.ok,
            "scenario": report.scenario,
            "run_id": report.run_id,
            "rag_run_id": report.rag_run_id,
            "output_dir": report.output_dir,
            "report_path": report.report_path,
            "web_search_ok": report.web_search.get("ok"),
            "result_count": len(report.web_search.get("results") or []),
            "live_web_search_required": report.live_web_search_required,
            "warnings": report.warnings,
            "failures": report.failures,
        }))

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RAG-to-direct-DuckDuckGo AI web-search smoke test.")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Repository root to inspect.")
    parser.add_argument("--output-root", type=Path, default=None, help="Optional diagnostics output root.")
    parser.add_argument("--prompt", default=DUCKDUCKGO_RAG_SMOKE_PROMPT, help="User AI query to smoke-test.")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional deterministic smoke run id. Defaults to a unique timestamped id to avoid output collisions.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument(
        "--allow-offline",
        action="store_true",
        help="Allow missing live direct DuckDuckGo results to pass as a wiring-only smoke.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose diagnostics.")
    parser.add_argument("--dump-json", action="store_true", help="Print the full smoke report JSON.")
    parser.add_argument("--max-results", type=int, default=3, help="Maximum DuckDuckGo results to attach.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_rag_duckduckgo_web_search_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        prompt=args.prompt,
        run_id=args.run_id,
        strict=args.strict,
        allow_offline=args.allow_offline,
        max_results=args.max_results,
        verbose=not args.quiet,
        dump_json=args.dump_json,
    )
    print(f"Scenario: {report.scenario}")
    print(f"Smoke run: {report.run_id}")
    print(f"RAG run: {report.rag_run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Live web search required: {report.live_web_search_required}")
    print(f"Status: {'passed' if report.ok else 'failed'}")
    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")
    if report.failures:
        print("Failures:")
        for failure in report.failures:
            print(f"  - {failure}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
