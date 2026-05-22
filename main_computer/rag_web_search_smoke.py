from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from main_computer.catalog import ProjectCatalog
from main_computer.config import MainComputerConfig
from main_computer.models import ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_harness import run_rag_harness
from main_computer.router import MainComputer
from main_computer.ai_web_search import WebSearchFn


DEFAULT_RAG_WEB_SEARCH_SMOKE_RUN_ID_PREFIX = "rag_web_search_smoke"


WEB_SEARCH_RAG_SMOKE_PROMPT = (
    "Search the web for current Main Computer Tor DuckDuckGo onion search context, "
    "then answer from the web results and the local RAG context."
)

WEB_SEARCH_RAG_QUERIES = [
    "tor web search",
    "duckduckgo onion",
    "web_search",
    "ai web search context",
    "router chat",
    "rag harness",
]


_TOR_PROXY_ENV_NAMES = (
    "TOR_PROXY",
    "MAIN_COMPUTER_TOR_PROXY",
)


def _configured_tor_proxy_env(env: dict[str, str] | None = None) -> str:
    values = env if env is not None else os.environ
    for name in _TOR_PROXY_ENV_NAMES:
        value = str(values.get(name, "") or "").strip()
        if value:
            return name
    return ""


@dataclass(frozen=True)
class RagWebSearchSmokeReport:
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
    live_web_search_required: bool = False

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


def _new_default_run_id(prefix: str = DEFAULT_RAG_WEB_SEARCH_SMOKE_RUN_ID_PREFIX) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}_{timestamp}_{uuid4().hex[:8]}"


def _new_unique_run_id(output_base: Path, prefix: str = DEFAULT_RAG_WEB_SEARCH_SMOKE_RUN_ID_PREFIX) -> str:
    for _ in range(100):
        run_id = _new_default_run_id(prefix)
        if not (output_base / run_id).exists() and not (output_base / f"{run_id}_rag").exists():
            return run_id
    return _new_default_run_id(prefix)


def _retrieved_paths(result: Any) -> list[str]:
    retrieval = getattr(result, "retrieval", None)
    candidates = getattr(retrieval, "candidates", []) if retrieval is not None else []
    return [str(getattr(candidate, "path", "")) for candidate in candidates if str(getattr(candidate, "path", ""))]


def _validate_web_search_smoke(
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
        "main_computer/tor_web_search.py",
        "main_computer/ai_web_search.py",
        "main_computer/router.py",
    }
    if not retrieved.intersection(expected_any):
        warnings.append(
            "RAG retrieval did not surface the expected Tor/search/router connector files; "
            f"retrieved {retrieved_paths[:8] or 'none'}."
        )

    web_search = response.metadata.get("web_search")
    if not isinstance(web_search, dict):
        failures.append("AI response metadata did not include web_search details.")
        web_search = {}

    # These assertions prove the user's AI query reached the Tor-only
    # DuckDuckGo connector path. They are always hard failures because they do
    # not depend on a live Tor daemon.
    if web_search.get("attempted") is not True:
        failures.append("The user AI query did not trigger a web-search attempt.")
    if web_search.get("provider") != "duckduckgo_onion_tor":
        failures.append(f"Expected DuckDuckGo-onion Tor provider metadata, got {web_search.get('provider')!r}.")
    if web_search.get("mode") != "tor_only":
        failures.append(f"Expected tor_only web-search mode, got {web_search.get('mode')!r}.")
    if str(web_search.get("query") or "").strip() != prompt:
        failures.append("Web-search query did not preserve the user's AI prompt.")

    # A live DuckDuckGo-onion result requires the caller to have Tor running and
    # TOR_PROXY/MAIN_COMPUTER_TOR_PROXY configured. In default non-strict mode,
    # a completely unconfigured proxy is reported as a warning so this smoke can
    # still validate AI -> router -> Tor-search wiring on offline machines.
    #
    # If a proxy is configured, though, an unavailable live result is a hard
    # failure even without --strict; otherwise the smoke can hide a broken Tor
    # setup behind a passing status.
    require_live_result = bool(strict or live_web_search_required)

    if web_search.get("ok") is not True:
        message = f"Tor DuckDuckGo web-search context was not available: {web_search.get('error') or 'no results'}"
        if require_live_result:
            failures.append(message)
        else:
            warnings.append(message)

    if not web_search.get("results"):
        message = "No live web-search result was present in response metadata."
        if require_live_result:
            failures.append("Expected at least one web-search result in response metadata.")
        else:
            warnings.append(message)

    if strict and warnings:
        failures.extend(warnings)
    ok = not failures
    return ok, warnings, failures


def run_rag_web_search_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    provider: LLMProvider | None = None,
    search_fn: WebSearchFn | None = None,
    prompt: str = WEB_SEARCH_RAG_SMOKE_PROMPT,
    run_id: str | None = None,
    strict: bool = True,
    max_results: int = 3,
    verbose: bool = True,
    dump_json: bool = False,
) -> RagWebSearchSmokeReport:
    """Run a smoke test that connects a user AI query to Tor-only DuckDuckGo search.

    The RAG phase proves the repository context can find the connector/search
    code. The AI phase sends the same user prompt through ``MainComputer.chat``.
    In default non-strict mode the smoke only requires proof that the AI query
    reached the Tor-only DuckDuckGo connector when no Tor proxy is configured.
    If a Tor proxy is configured, or with ``strict=True``, the smoke also
    requires live DuckDuckGo-onion results through that proxy.
    """

    repo_path = Path(repo_dir).resolve()
    output_base = Path(output_root or (repo_path / "diagnostics_output" / "rag_runs"))
    run_id = run_id or _new_unique_run_id(output_base)
    rag_run_id = f"{run_id}_rag"
    smoke_output_dir = output_base / run_id

    if provider is None:
        provider = _get_local_ollama_provider()

    if verbose:
        print(
            "[rag-web-search-smoke] starting "
            f"repo_dir={repo_path} output_root={output_base} run_id={run_id} strict={strict}"
        )

    rag_result = run_rag_harness(
        prompt=prompt,
        repo_dir=repo_path,
        queries=WEB_SEARCH_RAG_QUERIES,
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
    )
    response = computer.chat(prompt)

    configured_proxy_env = _configured_tor_proxy_env() if search_fn is None else ""
    live_web_search_required = bool(strict or configured_proxy_env)

    ok, warnings, failures = _validate_web_search_smoke(
        rag_ok=rag_result.ok,
        rag_error=rag_result.error,
        retrieved_paths=retrieved_paths,
        response=response,
        prompt=prompt,
        strict=strict,
        live_web_search_required=live_web_search_required,
    )

    report_path = smoke_output_dir / "rag_web_search_smoke_report.json"
    report = RagWebSearchSmokeReport(
        ok=ok,
        run_id=run_id,
        rag_run_id=rag_run_id,
        scenario="rag_ai_query_tor_duckduckgo_web_search",
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
        print("[rag-web-search-smoke] validation report:")
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
    parser = argparse.ArgumentParser(description="Run the RAG-to-Tor DuckDuckGo AI web-search smoke test.")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd(), help="Repository root to inspect.")
    parser.add_argument("--output-root", type=Path, default=None, help="Optional diagnostics output root.")
    parser.add_argument("--prompt", default=WEB_SEARCH_RAG_SMOKE_PROMPT, help="User AI query to smoke-test.")
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional deterministic smoke run id. Defaults to a unique timestamped id to avoid output collisions.",
    )
    parser.add_argument("--strict", action="store_true", help="Require live Tor DuckDuckGo results and treat warnings as failures. Live results are also required automatically when a Tor proxy env var is configured.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose diagnostics.")
    parser.add_argument("--dump-json", action="store_true", help="Print the full smoke report JSON.")
    parser.add_argument("--max-results", type=int, default=3, help="Maximum DuckDuckGo results to attach.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_rag_web_search_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        prompt=args.prompt,
        run_id=args.run_id,
        strict=args.strict,
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
