from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import sys
import traceback
from typing import Any, Sequence

from main_computer.config import MainComputerConfig
from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_harness import parse_json_object, run_rag_harness
from main_computer.thinking_models import RagHarnessResult


CSV_AUDIT_SCRIPT_PROMPT = (
    "Create a standalone Python script called csv_audit.py that accepts an input CSV path "
    "and an output JSON path, validates the CSV header and row widths, reports row_count, "
    "column_count, blank_cell_count, duplicate_header_names, and writes a JSON audit report. "
    "Use only the Python standard library. Do not claim you created or ran files unless an "
    "executor actually did it. Return a grounded plan with evidence from the repository."
)

CSV_AUDIT_QUERIES = [
    "docker executor",
    "executor tool loop",
    "command_output",
    "artifact outputs",
    "python csv audit",
    "run tests",
]

BROKEN_CODE_REPAIR_PROMPT = (
    "Hard JSON-repair smoke test: plan a Docker-backed repair of a tiny Python module. "
    "The target task is to create intentionally broken calc_stats.py and test_calc_stats.py, "
    "run tests that fail for mean, median, and normalize, repair calc_stats.py, rerun tests, "
    "print INITIAL_FAILURE_CONFIRMED and FINAL_REPAIR_PASSED, and publish /outputs/repair_report.json. "
    "This JSON smoke does not need to execute Docker; it exists to prove that malformed model JSON "
    "is detected and repaired by asking the model for corrected JSON."
)

BROKEN_CODE_REPAIR_QUERIES = [
    "docker executor",
    "executor tool loop",
    "command_output",
    "artifact outputs",
    "run tests",
    "repair code",
    "python unittest",
]

JSON_REPAIR_SYSTEM_PROMPT = """You are Main Computer's strict JSON repair step.

Return exactly one valid JSON object and nothing else.
Do not use markdown fences.
Do not include comments or trailing commas.
Do not use Python string concatenation.
Escape backslashes and newlines correctly for JSON.
Preserve the intended meaning, keys, and shape of the broken object.
"""


@dataclass(frozen=True)
class JsonRepairEvent:
    index: int
    stage_hint: str
    forced_corruption: str
    original_chars: int
    corrupted_chars: int
    initial_parse_error: str
    repair_attempts: int
    repaired: bool
    final_parse_error: str | None = None
    original_preview: str = ""
    corrupted_preview: str = ""
    repaired_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RagModelJsonSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    provider: str
    model: str
    repair_event_count: int
    repaired_event_count: int
    forced_corruption_count: int
    retrieved_paths: list[str]
    final_plan: dict[str, Any]
    repair_events: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

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


def _preview_list(items: list[str], *, limit: int = 12) -> str:
    preview = items[:limit]
    suffix = "" if len(items) <= limit else f", ... +{len(items) - limit} more"
    return ", ".join(preview) + suffix


def _provider_summary(provider: LLMProvider) -> str:
    name = getattr(provider, "name", provider.__class__.__name__)
    model = getattr(provider, "model", "")
    return f"provider={name} model={model}".strip()


def _get_local_ollama_provider() -> LLMProvider:
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=config.model or "gemma4:26b",
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        fallback=config.fallback,
    )


def _enable_provider_streaming(provider: LLMProvider, *, stream_model: bool) -> LLMProvider:
    if not stream_model:
        return provider
    if hasattr(provider, "fallback"):
        try:
            return replace(provider, fallback=True)  # type: ignore[arg-type]
        except TypeError:
            return provider
    return provider


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _result_retrieved_paths(result: RagHarnessResult) -> list[str]:
    retrieval = getattr(result, "retrieval", None)
    candidates = getattr(retrieval, "candidates", []) if retrieval is not None else []
    paths: list[str] = []
    for candidate in candidates:
        path = getattr(candidate, "path", "")
        if path:
            paths.append(str(path))
    if not paths:
        direct = getattr(result, "retrieved_paths", [])
        paths = [str(item) for item in direct or []]
    return paths


def _stage_hint_from_messages(messages: Sequence[ChatMessage]) -> str:
    joined = "\n".join(str(message.content or "")[:1200] for message in messages)
    lower = joined.lower()
    if "task decomposition" in lower or "retrieval_queries" in lower:
        return "task_decomposition"
    if "grounded plan" in lower or "final_plan" in lower or "evidence" in lower:
        return "grounded_plan"
    if "json repair" in lower:
        return "json_repair"
    return "model_json"


class JsonRepairingProvider(LLMProvider):
    """Force malformed model JSON, then ask the model to repair it."""

    name: str
    model: str

    def __init__(
        self,
        inner: LLMProvider,
        *,
        corrupt_suffix: str = "}",
        max_repair_attempts: int = 2,
        verbose: bool = True,
    ) -> None:
        self.inner = inner
        self.name = f"{getattr(inner, 'name', inner.__class__.__name__)}+json-repair"
        self.model = getattr(inner, "model", "")
        self.corrupt_suffix = corrupt_suffix
        self.max_repair_attempts = max(1, int(max_repair_attempts))
        self.verbose = verbose
        self.events: list[JsonRepairEvent] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        stage_hint = _stage_hint_from_messages(messages)
        original = self.inner.chat(messages)

        corrupted_content = str(original.content or "") + self.corrupt_suffix
        forced_corruption = self.corrupt_suffix

        try:
            parse_json_object(corrupted_content)
        except Exception as initial_error:
            if self.verbose:
                print(
                    "[rag-model-smoke-json] forced parse failure "
                    f"stage={stage_hint} error={_shorten(initial_error, limit=300)!r}"
                )

            repaired_response, attempts, final_error = self._repair_json(
                original_messages=messages,
                malformed_content=corrupted_content,
                parse_error=initial_error,
                provider=original.provider,
                model=original.model,
                stage_hint=stage_hint,
            )
            repaired = final_error is None

            self.events.append(
                JsonRepairEvent(
                    index=len(self.events) + 1,
                    stage_hint=stage_hint,
                    forced_corruption=forced_corruption,
                    original_chars=len(str(original.content or "")),
                    corrupted_chars=len(corrupted_content),
                    initial_parse_error=f"{type(initial_error).__name__}: {initial_error}",
                    repair_attempts=attempts,
                    repaired=repaired,
                    final_parse_error=None if final_error is None else f"{type(final_error).__name__}: {final_error}",
                    original_preview=_shorten(original.content, limit=500),
                    corrupted_preview=_shorten(corrupted_content, limit=500),
                    repaired_preview=_shorten(repaired_response.content, limit=500),
                )
            )
            return repaired_response

        if self.verbose:
            print(
                "[rag-model-smoke-json] warning: forced suffix did not break parser "
                f"stage={stage_hint} suffix={forced_corruption!r}"
            )

        self.events.append(
            JsonRepairEvent(
                index=len(self.events) + 1,
                stage_hint=stage_hint,
                forced_corruption=forced_corruption,
                original_chars=len(str(original.content or "")),
                corrupted_chars=len(corrupted_content),
                initial_parse_error="forced corruption unexpectedly parsed",
                repair_attempts=0,
                repaired=False,
                final_parse_error="forced corruption unexpectedly parsed",
                original_preview=_shorten(original.content, limit=500),
                corrupted_preview=_shorten(corrupted_content, limit=500),
                repaired_preview="",
            )
        )
        return ChatResponse(content=corrupted_content, provider=original.provider, model=original.model)

    def _repair_json(
        self,
        *,
        original_messages: Sequence[ChatMessage],
        malformed_content: str,
        parse_error: Exception,
        provider: str,
        model: str,
        stage_hint: str,
    ) -> tuple[ChatResponse, int, Exception | None]:
        last_content = malformed_content
        last_error: Exception = parse_error

        original_task = "\n\n".join(
            [
                f"{message.role.upper()}:\n{str(message.content or '')[:4000]}"
                for message in original_messages[-3:]
            ]
        )

        for attempt in range(1, self.max_repair_attempts + 1):
            repair_prompt = (
                "The previous model response was supposed to be one JSON object, but parsing failed.\n\n"
                f"Stage: {stage_hint}\n"
                f"Parse error: {type(last_error).__name__}: {last_error}\n\n"
                "Original task context:\n"
                f"{original_task}\n\n"
                "Malformed JSON-like response to repair:\n"
                "<<<MALFORMED_JSON\n"
                f"{last_content}\n"
                "MALFORMED_JSON\n\n"
                "Return the corrected JSON object only. Preserve the intended keys and values. "
                "Remove the spurious extra brace and repair invalid escapes if present."
            )

            if self.verbose:
                print(f"[rag-model-smoke-json] asking model to repair JSON stage={stage_hint} attempt={attempt}")

            repaired = self.inner.chat(
                [
                    ChatMessage(role="system", content=JSON_REPAIR_SYSTEM_PROMPT),
                    ChatMessage(role="user", content=repair_prompt),
                ]
            )

            candidate_content = str(repaired.content or "").strip()
            try:
                parsed = parse_json_object(candidate_content)
            except Exception as repair_error:
                last_content = candidate_content
                last_error = repair_error
                if self.verbose:
                    print(
                        "[rag-model-smoke-json] repair attempt failed "
                        f"stage={stage_hint} attempt={attempt} "
                        f"error={_shorten(repair_error, limit=300)!r}"
                    )
                continue

            normalized = _json_dumps(parsed)
            if self.verbose:
                print(
                    "[rag-model-smoke-json] repair succeeded "
                    f"stage={stage_hint} attempt={attempt} chars={len(normalized)}"
                )
            return (
                ChatResponse(
                    content=normalized,
                    provider=f"{provider}+json-repair",
                    model=model,
                    metadata={
                        "json_repaired": True,
                        "repair_attempt": attempt,
                        "stage_hint": stage_hint,
                    },
                ),
                attempt,
                None,
            )

        return (
            ChatResponse(
                content=last_content,
                provider=f"{provider}+json-repair-failed",
                model=model,
                metadata={
                    "json_repaired": False,
                    "repair_attempts": self.max_repair_attempts,
                    "stage_hint": stage_hint,
                },
            ),
            self.max_repair_attempts,
            last_error,
        )


def validate_json_repair_smoke(
    result: RagHarnessResult,
    *,
    provider: JsonRepairingProvider,
    strict: bool,
) -> RagModelJsonSmokeReport:
    warnings: list[str] = []
    failures: list[str] = []

    events = list(provider.events)
    repaired_events = [event for event in events if event.repaired]
    forced_corruptions = [event for event in events if event.forced_corruption]

    if not result.ok:
        failures.append(f"RAG run failed: {result.error or result.status}")

    if result.no_model:
        failures.append("JSON repair smoke expected model-backed RAG, but result.no_model is true.")

    if not events:
        failures.append("Expected JsonRepairingProvider to record at least one forced JSON parse failure.")

    if len(repaired_events) != len(events):
        failures.append(
            f"Expected every forced JSON parse failure to be repaired; "
            f"repaired {len(repaired_events)} of {len(events)}."
        )

    if not forced_corruptions:
        failures.append("Expected the smoke to append a spurious '}' to model JSON before repair.")

    if not any(event.repair_attempts > 0 for event in events):
        failures.append("Expected at least one repair attempt that asks the model to rebuild JSON.")

    final_plan = _coerce_dict(result.final_plan)
    if not final_plan:
        failures.append("Expected a final_plan object after JSON repair.")

    summary = str(final_plan.get("summary", "")).strip()
    if not summary:
        failures.append("Expected final_plan.summary to be non-empty after JSON repair.")

    if any(event.final_parse_error for event in events):
        warnings.append("At least one JSON repair event retained a final parse error.")

    if strict and warnings:
        failures.extend(warnings)

    ok = not failures and (not strict or not warnings)
    report_path = Path(result.output_dir) / "json_repair_smoke_report.json"

    report = RagModelJsonSmokeReport(
        ok=ok,
        run_id=result.run_id,
        scenario="json_repair_forced_corruption",
        output_dir=result.output_dir,
        report_path=str(report_path),
        provider=provider.name,
        model=provider.model,
        repair_event_count=len(events),
        repaired_event_count=len(repaired_events),
        forced_corruption_count=len(forced_corruptions),
        retrieved_paths=_result_retrieved_paths(result),
        final_plan=final_plan,
        repair_events=[event.to_dict() for event in events],
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())
    return report


def _log_rag_result(label: str, result: RagHarnessResult) -> None:
    print(
        f"[rag-model-smoke-json] {label} RAG run complete "
        f"run_id={result.run_id} ok={result.ok} status={result.status} output_dir={result.output_dir}"
    )

    retrieved = _result_retrieved_paths(result)
    print(
        f"[rag-model-smoke-json] {label} RAG retrieved_paths "
        f"count={len(retrieved)} preview={_preview_list(retrieved)}"
    )

    final_plan = _coerce_dict(result.final_plan)
    if final_plan:
        print(
            f"[rag-model-smoke-json] {label} RAG final_plan "
            f"type={final_plan.get('type')!r} summary={_shorten(final_plan.get('summary'), limit=300)!r}"
        )

    for step in getattr(result, "steps", []) or []:
        print(
            f"[rag-model-smoke-json] {label} RAG step {getattr(step, 'index', '?'):02d} "
            f"{getattr(step, 'kind', '?')} status={getattr(step, 'status', '?')} "
            f"started={getattr(step, 'started_at', '')} completed={getattr(step, 'completed_at', '')}"
        )
        if getattr(step, "error", None):
            print(f"[rag-model-smoke-json] {label} RAG step error={_shorten(step.error, limit=500)!r}")


def run_json_repair_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    scenario: str = "broken-code-repair-json",
    provider: LLMProvider | None = None,
    strict: bool = True,
    run_id: str | None = None,
    verbose: bool = True,
    stream_model: bool = True,
    dump_json: bool = False,
    max_repair_attempts: int = 2,
    corrupt_suffix: str = "}",
) -> RagModelJsonSmokeReport:
    if provider is None:
        provider = _get_local_ollama_provider()

    provider = _enable_provider_streaming(provider, stream_model=stream_model)
    repairing_provider = JsonRepairingProvider(
        provider,
        corrupt_suffix=corrupt_suffix,
        max_repair_attempts=max_repair_attempts,
        verbose=verbose,
    )

    if scenario == "csv-audit-json":
        prompt = CSV_AUDIT_SCRIPT_PROMPT
        queries = CSV_AUDIT_QUERIES
    elif scenario == "broken-code-repair-json":
        prompt = BROKEN_CODE_REPAIR_PROMPT
        queries = BROKEN_CODE_REPAIR_QUERIES
    else:
        raise ValueError(f"Unsupported JSON smoke scenario: {scenario!r}")

    if verbose:
        print(
            f"[rag-model-smoke-json] starting JSON repair smoke "
            f"scenario={scenario} repo_dir={repo_dir} output_root={output_root or '<default>'} "
            f"strict={strict} stream_model={stream_model} corrupt_suffix={corrupt_suffix!r} "
            f"max_repair_attempts={max_repair_attempts}"
        )
        print(f"[rag-model-smoke-json] using {_provider_summary(repairing_provider)}")

    try:
        result = run_rag_harness(
            prompt=prompt,
            repo_dir=repo_dir,
            queries=queries,
            output_root=output_root,
            provider=repairing_provider,
            use_model=True,
            run_id=run_id,
        )
    except Exception:
        base = Path(output_root or (repo_dir / "diagnostics_output" / "rag_runs"))
        failed_run_id = run_id or "json_repair_failed"
        output_dir = base / failed_run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "json_repair_smoke_report.json"
        failure = traceback.format_exc()

        report = RagModelJsonSmokeReport(
            ok=False,
            run_id=failed_run_id,
            scenario="json_repair_forced_corruption",
            output_dir=str(output_dir),
            report_path=str(report_path),
            provider=repairing_provider.name,
            model=repairing_provider.model,
            repair_event_count=len(repairing_provider.events),
            repaired_event_count=len([event for event in repairing_provider.events if event.repaired]),
            forced_corruption_count=len([event for event in repairing_provider.events if event.forced_corruption]),
            retrieved_paths=[],
            final_plan={},
            repair_events=[event.to_dict() for event in repairing_provider.events],
            failures=["RAG harness raised before returning a result.", failure],
            strict=strict,
        )
        _write_json(report_path, report.to_dict())

        if verbose:
            print("[rag-model-smoke-json] RAG harness failed before returning a result.")
            print(_shorten(failure, limit=2000))
            print("[rag-model-smoke-json] JSON repair validation report:")
            print(_json_dumps(report.to_dict()))
        return report

    if verbose:
        _log_rag_result("json-repair", result)

    report = validate_json_repair_smoke(result, provider=repairing_provider, strict=strict)

    if verbose:
        print("[rag-model-smoke-json] JSON repair events:")
        for event in repairing_provider.events:
            print(
                f"  - #{event.index} stage={event.stage_hint} "
                f"repaired={event.repaired} attempts={event.repair_attempts} "
                f"initial_error={_shorten(event.initial_parse_error, limit=180)!r}"
            )

        print("[rag-model-smoke-json] JSON repair validation report:")
        if dump_json:
            print(_json_dumps(report.to_dict()))
        else:
            concise = report.to_dict()
            concise["repair_events"] = [
                {
                    "index": event["index"],
                    "stage_hint": event["stage_hint"],
                    "repair_attempts": event["repair_attempts"],
                    "repaired": event["repaired"],
                    "initial_parse_error": _shorten(event["initial_parse_error"], limit=240),
                    "final_parse_error": event["final_parse_error"],
                }
                for event in concise.get("repair_events", [])
            ]
            print(_json_dumps(concise))

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a model-backed RAG smoke that intentionally corrupts model JSON, "
            "then asks the model to repair the JSON before the harness parser sees it."
        )
    )
    parser.add_argument(
        "--scenario",
        choices=["broken-code-repair-json", "csv-audit-json"],
        default="broken-code-repair-json",
        help="JSON repair smoke scenario to run.",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Repository root to inspect. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_runs.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional deterministic run id.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress verbose smoke-test diagnostics.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Do not force provider streaming/fallback mode for model calls.",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Print full JSON repair event previews in the console report.",
    )
    parser.add_argument(
        "--max-repair-attempts",
        type=int,
        default=2,
        help="Maximum times to ask the model to rebuild malformed JSON.",
    )
    parser.add_argument(
        "--corrupt-suffix",
        default="}",
        help="Suffix appended to every first model JSON response to force repair.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_json_repair_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        scenario=args.scenario,
        strict=args.strict,
        run_id=args.run_id,
        verbose=not args.quiet,
        stream_model=not args.no_stream,
        dump_json=args.dump_json,
        max_repair_attempts=args.max_repair_attempts,
        corrupt_suffix=args.corrupt_suffix,
    )

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Provider: {report.provider}")
    print(f"Model: {report.model}")
    print(f"Forced corruptions: {report.forced_corruption_count}")
    print(f"Repair events: {report.repaired_event_count}/{report.repair_event_count}")
    print(f"Status: {'passed' if report.ok else 'failed'}")

    if report.warnings:
        print("Warnings:")
        for warning in report.warnings:
            print(f"  - {warning}")

    if report.failures:
        print("Failures:")
        for failure in report.failures:
            print(f"  - {_shorten(failure, limit=1200)}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())