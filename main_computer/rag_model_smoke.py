from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import sys
from typing import Any

from main_computer.config import MainComputerConfig
from main_computer.docker_executor import DockerExecutor
from main_computer.executor_tool_loop import (
    ExecutorToolLoopConfig,
    ExecutorToolLoopResult,
    ExecutorToolLoopStep,
    run_executor_tool_loop,
)
from main_computer.executor_models import ExecutorRequest, ExecutorResult
from main_computer.providers import LLMProvider, OllamaProvider
from main_computer.rag_harness import run_rag_harness
from main_computer.thinking_models import RagHarnessResult as RagRunResult


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
    "Hard smoke test: use the Docker executor to create a tiny Python repair workspace under "
    "/workspace/repair_target. First create intentionally broken calc_stats.py and "
    "test_calc_stats.py. The broken implementation must fail tests for mean, median, and "
    "normalize. Run the tests and confirm the initial failure. Then repair calc_stats.py, rerun "
    "the tests, and write /outputs/repair_report.json describing the initial failure, the repair, "
    "and the final success. Print INITIAL_FAILURE_CONFIRMED after the first failing test run and "
    "FINAL_REPAIR_PASSED after the final passing test run. Use only Python standard library. "
    "Important: the Docker executor already wraps your shell command. Do not import "
    "main_computer, DockerExecutor, or run_docker_executors inside the container. Work only with "
    "files under /workspace/repair_target and /outputs."
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


@dataclass(frozen=True)
class RagModelSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    retrieved_paths: list[str]
    final_plan: dict[str, Any]
    baseline_run_id: str | None = None
    final_plan_type: str = ""
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_path_count"] = len(self.retrieved_paths)
        data["retrieved_path_preview"] = self.retrieved_paths[:12]
        return data


@dataclass(frozen=True)
class BrokenCodeRepairSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    retrieved_paths: list[str]
    final_plan: dict[str, Any]
    docker_status: dict[str, Any]
    executor_result: dict[str, Any] | None
    executor_status: str = "not-run"
    docker_available: bool = False
    artifact_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["retrieved_path_count"] = len(self.retrieved_paths)
        data["retrieved_path_preview"] = self.retrieved_paths[:12]
        if self.executor_result:
            steps = self.executor_result.get("steps")
            if isinstance(steps, list):
                data["executor_step_count"] = len(steps)
                data["executor_steps"] = _summarize_executor_steps(steps)
        return data


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _coerce_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _shorten(value: Any, *, limit: int = 300) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"... <truncated {len(text) - limit} chars>"


def _preview_list(items: list[str], *, limit: int = 12) -> str:
    preview = items[:limit]
    suffix = "" if len(items) <= limit else f", ... +{len(items) - limit} more"
    return ", ".join(preview) + suffix


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _enable_provider_streaming(provider: LLMProvider, *, stream_model: bool) -> LLMProvider:
    """Ask compatible providers to use their streaming/fallback path."""

    if not stream_model:
        return provider

    replacement = provider
    if hasattr(provider, "fallback"):
        try:
            replacement = replace(provider, fallback=True)  # type: ignore[arg-type]
        except TypeError:
            try:
                setattr(provider, "fallback", True)
            except Exception:
                pass
            replacement = provider
    return replacement


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

def _docker_status_summary(status: dict[str, Any]) -> str:
    return (
        f"enabled={status.get('enabled')} "
        f"ok={status.get('ok')} "
        f"docker_available={status.get('docker_available')} "
        f"image={status.get('image')} "
        f"runtime_root={status.get('runtime_root')}"
    )


def _summarize_executor_steps(steps: list[Any]) -> list[dict[str, Any]]:
    summarized: list[dict[str, Any]] = []
    for item in steps:
        if hasattr(item, "to_dict"):
            raw = item.to_dict()
        elif isinstance(item, dict):
            raw = item
        else:
            raw = {"value": str(item)}
        step: dict[str, Any] = {
            "index": raw.get("index"),
            "kind": raw.get("kind"),
        }
        if raw.get("error"):
            step["error"] = _shorten(raw.get("error"), limit=300)
        if raw.get("content"):
            step["content"] = _shorten(raw.get("content"), limit=500)
        executor_result = raw.get("executor_result")
        if isinstance(executor_result, dict):
            step["executor_result"] = {
                "ok": executor_result.get("ok"),
                "exit_code": executor_result.get("exit_code"),
                "timed_out": executor_result.get("timed_out"),
                "duration_ms": executor_result.get("duration_ms"),
                "stdout": _shorten(executor_result.get("stdout"), limit=500),
                "stderr": _shorten(executor_result.get("stderr"), limit=500),
                "artifact_count": len(_coerce_list(executor_result.get("artifacts"))),
            }
        summarized.append(step)
    return summarized


def _log_rag_result(label: str, result: RagRunResult) -> None:
    print(
        f"[rag-model-smoke] {label} RAG run complete "
        f"run_id={result.run_id} ok={result.ok} status={result.status} output_dir={result.output_dir}"
    )
    artifacts = _coerce_dict(getattr(result, "artifacts", {}))
    for name in ("run_json", "grounded_prompt", "context_chunks", "final_plan"):
        if artifacts.get(name):
            print(f"[rag-model-smoke] {label} RAG artifacts {name}={artifacts[name]}")
    retrieved = list(getattr(result, "retrieved_paths", []) or [])
    print(
        f"[rag-model-smoke] {label} RAG retrieved_paths "
        f"count={len(retrieved)} preview={_preview_list(retrieved)}"
    )
    final_plan = _coerce_dict(getattr(result, "final_plan", {}))
    if final_plan:
        print(
            f"[rag-model-smoke] {label} RAG final_plan "
            f"type={final_plan.get('type')!r} summary={_shorten(final_plan.get('summary'), limit=300)!r}"
        )
    for step in getattr(result, "steps", []) or []:
        print(
            f"[rag-model-smoke] {label} RAG step {getattr(step, 'index', '?'):02d} "
            f"{getattr(step, 'kind', '?')} status={getattr(step, 'status', '?')} "
            f"started={getattr(step, 'started_at', '')} completed={getattr(step, 'completed_at', '')}"
        )
        if getattr(step, "error", None):
            print(f"[rag-model-smoke] {label} RAG step error={_shorten(step.error, limit=500)!r}")

def _as_plain_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return value
    return {"value": str(value)}

def _result_retrieved_paths(result: Any) -> list[str]:
    direct = getattr(result, "retrieved_paths", None)
    if isinstance(direct, list):
        return [str(item) for item in direct]

    data = _as_plain_dict(result)

    direct = data.get("retrieved_paths")
    if isinstance(direct, list):
        return [str(item) for item in direct]

    retrieval = data.get("retrieval")
    if isinstance(retrieval, dict):
        for key in ("paths", "retrieved_paths", "selected_paths"):
            value = retrieval.get(key)
            if isinstance(value, list):
                return [str(item) for item in value]

        paths: list[str] = []
        files = retrieval.get("files")
        if isinstance(files, list):
            for item in files:
                if isinstance(item, dict) and item.get("path"):
                    paths.append(str(item["path"]))
                elif isinstance(item, str):
                    paths.append(item)

        for key in ("candidates", "chunks"):
            items = retrieval.get(key)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("path"):
                        paths.append(str(item["path"]))
                    elif isinstance(item, str):
                        paths.append(item)
        if paths:
            return sorted(dict.fromkeys(paths))

    steps = data.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            if step.get("kind") != "retrieval":
                continue
            output = step.get("output")
            if isinstance(output, dict):
                for key in ("paths", "retrieved_paths", "selected_paths"):
                    value = output.get(key)
                    if isinstance(value, list):
                        return [str(item) for item in value]
                facts = output.get("facts")
                if isinstance(facts, list):
                    paths = []
                    for fact in facts:
                        if isinstance(fact, dict) and fact.get("path"):
                            paths.append(str(fact["path"]))
                    if paths:
                        return sorted(set(paths))

    return []

def _log_executor_result(result: ExecutorToolLoopResult) -> None:
    print(
        f"[rag-model-smoke] executor tool loop result "
        f"status={result.status} ok={result.ok} steps={len(result.steps)} "
        f"provider={result.provider} model={result.model}"
    )
    if result.error:
        print(f"[rag-model-smoke] executor error: {_shorten(result.error, limit=500)}")
    if result.final_content:
        print(f"[rag-model-smoke] executor final_content={_shorten(result.final_content, limit=800)!r}")

    step_dicts = [_as_plain_dict(item) for item in result.steps]
    for step in _summarize_executor_steps(step_dicts):
        prefix = f"[rag-model-smoke] executor step {step.get('index'):02d} {step.get('kind')}"
        if step.get("content"):
            print(f"{prefix} content={step['content']!r}")
        if step.get("error"):
            print(f"{prefix} error={step['error']!r}")
        executor_result = step.get("executor_result")
        if isinstance(executor_result, dict):
            print(
                f"{prefix} command_output ok={executor_result.get('ok')} "
                f"exit_code={executor_result.get('exit_code')} timed_out={executor_result.get('timed_out')} "
                f"artifacts={executor_result.get('artifact_count')}"
            )
            if executor_result.get("stdout"):
                print(f"{prefix} stdout={executor_result['stdout']!r}")
            if executor_result.get("stderr"):
                print(f"{prefix} stderr={executor_result['stderr']!r}")

def _extract_artifact_paths(executor_result: ExecutorToolLoopResult | None) -> list[str]:
    if not executor_result:
        return []
    paths: list[str] = []
    for step in executor_result.steps:
        if not isinstance(step.executor_result, dict):
            continue
        artifacts = _coerce_list(step.executor_result.get("artifacts"))
        for artifact in artifacts:
            if isinstance(artifact, dict):
                path = artifact.get("relative_path") or artifact.get("name") or artifact.get("path")
                if path:
                    paths.append(str(path))
    return paths


def _executor_stdout_text(executor_result: ExecutorToolLoopResult | None) -> str:
    if not executor_result:
        return ""
    parts: list[str] = []
    for step in executor_result.steps:
        if isinstance(step.executor_result, dict):
            parts.append(str(step.executor_result.get("stdout") or ""))
            parts.append(str(step.executor_result.get("stderr") or ""))
    return "\n".join(parts)


def _has_command_output_step(executor_result: ExecutorToolLoopResult | None) -> bool:
    if not executor_result:
        return False
    return any(step.kind == "command_output" for step in executor_result.steps)


def _has_repo_import_inside_docker(executor_result: ExecutorToolLoopResult | None) -> bool:
    if not executor_result:
        return False
    haystack = "\n".join(
        [
            str(step.content or "")
            + "\n"
            + str(step.tool_request or "")
            + "\n"
            + str(step.executor_result or "")
            for step in executor_result.steps
        ]
    )
    forbidden = [
        "from main_computer",
        "import main_computer",
        "DockerExecutor",
        "run_docker_executors",
        "ModuleNotFoundError: No module named 'main_computer'",
    ]
    return any(item in haystack for item in forbidden)


def _json_safe_executor_result(executor_result: ExecutorToolLoopResult | None) -> dict[str, Any] | None:
    return _as_plain_dict(executor_result) if executor_result else None

def validate_csv_audit_model_smoke(result: RagRunResult, *, strict: bool) -> RagModelSmokeReport:
    warnings: list[str] = []
    failures: list[str] = []

    retrieved_paths = _result_retrieved_paths(result)
    retrieved = set(retrieved_paths)

    final_plan_for_context = _coerce_dict(getattr(result, "final_plan", {}))
    evidence_paths = {
        str(item.get("path", "")).strip()
        for item in _coerce_list(final_plan_for_context.get("evidence"))
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    }

    context_paths = retrieved | evidence_paths

    expected_context = {
        "main_computer/docker_executor.py",
        "main_computer/executor_tool_loop.py",
    }

    found_context = sorted(path for path in expected_context if path in context_paths)

    if len(found_context) < 1:
        warnings.append(
            "Expected Docker executor and executor tool-loop context in retrieval or final_plan evidence; "
            f"found {found_context or 'none'}."
        )

    if not result.ok:
        failures.append(f"RAG run failed: {result.error or result.status}")

    if result.no_model:
        failures.append("Smoke test expected a real model-backed plan, but result.no_model is true.")

    final_plan = _coerce_dict(result.final_plan)
    plan_type = str(final_plan.get("type", "")).strip().lower()
    if plan_type not in {"plan", "answer", "plan | answer"}:
        failures.append(f"Expected final_plan.type to be 'plan' or 'answer', got {final_plan.get('type')!r}.")

    summary = str(final_plan.get("summary", "")).strip()
    if not summary:
        failures.append("Expected final_plan.summary to be non-empty.")

    evidence = _coerce_list(final_plan.get("evidence"))
    if not evidence:
        warnings.append("final_plan evidence is empty; expected citations to retrieved files.")
    else:
        uncited = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            if path and path not in retrieved:
                uncited.append(path)
        if uncited:
            warnings.append(
                "Model cited evidence paths that were not retrieved: " + ", ".join(sorted(set(uncited)))
            )

    next_step = _coerce_dict(final_plan.get("next_step"))
    if next_step:
        if next_step.get("requires_approval") is not True:
            warnings.append("CSV audit script scenario should require approval before execution or file creation.")
        if next_step.get("requires_executor") is not True:
            warnings.append("CSV audit script scenario should likely mark next_step.requires_executor=true.")
    else:
        warnings.append("Expected final_plan.next_step to describe the safe executor-backed next step.")

    false_execution_patterns = [
        r"\bI ran\b",
        r"\bI executed\b",
        r"\btests? passed\b",
        r"\bcreated csv_audit\.py\b",
        r"\bdocker (?:ran|executed)\b",
    ]
    for pattern in false_execution_patterns:
        if re_search(pattern, summary):
            warnings.append(
                "Model summary may claim execution occurred even though this smoke test only validates planning."
            )
            break

    ok = not failures and (not strict or not warnings)
    if strict and warnings:
        failures.extend(warnings)

    report_path = Path(result.output_dir) / "model_smoke_report.json"
    report = RagModelSmokeReport(
        ok=ok,
        run_id=result.run_id,
        scenario="csv_audit_script_builder",
        output_dir=result.output_dir,
        report_path=str(report_path),
        retrieved_paths=retrieved_paths,
        final_plan=final_plan,
        final_plan_type=plan_type,
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())
    return report


def validate_broken_code_repair_docker_smoke(
    result: RagRunResult,
    *,
    executor_result: ExecutorToolLoopResult | None,
    docker_status: dict[str, Any],
    strict: bool,
) -> BrokenCodeRepairSmokeReport:
    warnings: list[str] = []
    failures: list[str] = []

    retrieved_paths = _result_retrieved_paths(result)
    retrieved = set(retrieved_paths)

    final_plan_for_context = _coerce_dict(getattr(result, "final_plan", {}))
    evidence_paths = {
        str(item.get("path", "")).strip()
        for item in _coerce_list(final_plan_for_context.get("evidence"))
        if isinstance(item, dict) and str(item.get("path", "")).strip()
    }

    context_paths = retrieved | evidence_paths

    expected_context = {
        "main_computer/docker_executor.py",
        "main_computer/executor_tool_loop.py",
    }

    found_context = sorted(path for path in expected_context if path in context_paths)

    if len(found_context) < 1:
        warnings.append(
            "Expected Docker executor and executor tool-loop context in retrieval or final_plan evidence; "
            f"found {found_context or 'none'}."
        )

    if not result.ok:
        failures.append(f"RAG run failed before Docker repair smoke: {result.error or result.status}")

    if result.no_model:
        failures.append("Broken-code repair smoke expected a real model-backed RAG phase, but no_model is true.")

    if not docker_status.get("docker_available"):
        failures.append(f"Docker is required for this smoke test: {docker_status.get('docker_error') or 'unavailable'}")

    if not executor_result:
        failures.append("Executor tool loop did not run.")
    else:
        stdout_text = _executor_stdout_text(executor_result)
        artifact_paths = _extract_artifact_paths(executor_result)
        final_or_output_text = "\n".join([stdout_text, executor_result.final_content or ""])

        if _has_repo_import_inside_docker(executor_result):
            failures.append(
                "Executor command tried to import main_computer inside the isolated Docker workspace. "
                "The repair smoke command must be self-contained."
            )

        if not executor_result.ok:
            has_markers = "INITIAL_FAILURE_CONFIRMED" in stdout_text and "FINAL_REPAIR_PASSED" in stdout_text
            has_report = any(path.endswith("repair_report.json") for path in artifact_paths)
            if not (has_markers and has_report):
                failures.append(f"Executor tool loop did not complete successfully: {executor_result.status}")

        if executor_result.status != "complete":
            has_markers = "INITIAL_FAILURE_CONFIRMED" in stdout_text and "FINAL_REPAIR_PASSED" in stdout_text
            has_report = any(path.endswith("repair_report.json") for path in artifact_paths)
            if not (has_markers and has_report):
                failures.append(f"Executor tool loop status should be complete, got {executor_result.status!r}.")

        if not _has_command_output_step(executor_result):
            failures.append("Expected at least one Docker command_output step.")

        if "INITIAL_FAILURE_CONFIRMED" not in stdout_text:
            failures.append("Expected Docker output to prove the intentionally broken code failed first.")

        if "FINAL_REPAIR_PASSED" not in stdout_text:
            failures.append("Expected Docker output to prove repaired code passed tests.")

        if not any(path.endswith("repair_report.json") for path in artifact_paths):
            failures.append("Expected repair_report.json to be published as a Docker artifact.")

        final_lower = final_or_output_text.lower()
        if "passed" not in final_lower and "success" not in final_lower:
            warnings.append("Expected final executor/model output to mention passing tests or success.")

    final_plan = _coerce_dict(result.final_plan)
    ok = not failures and (not strict or not warnings)
    if strict and warnings:
        failures.extend(warnings)

    report_path = Path(result.output_dir) / "broken_code_repair_smoke_report.json"
    report = BrokenCodeRepairSmokeReport(
        ok=ok,
        run_id=result.run_id,
        scenario="broken_code_repair_docker",
        output_dir=result.output_dir,
        report_path=str(report_path),
        retrieved_paths=retrieved_paths,
        final_plan=final_plan,
        docker_status=dict(docker_status),
        executor_result=_json_safe_executor_result(executor_result),
        executor_status=str(getattr(executor_result, "status", "not-run") if executor_result else "not-run"),
        docker_available=bool(docker_status.get("docker_available")),
        artifact_paths=_extract_artifact_paths(executor_result),
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    _write_json(report_path, report.to_dict())
    return report


def re_search(pattern: str, text: str) -> bool:
    import re

    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def run_csv_audit_model_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    provider: LLMProvider | None = None,
    strict: bool = False,
    run_id: str | None = None,
    verbose: bool = True,
    stream_model: bool = True,
    dump_json: bool = False,
    run_baseline: bool = True,
) -> RagModelSmokeReport:
    if provider is None:
        provider = _get_local_ollama_provider()
    provider = _enable_provider_streaming(provider, stream_model=stream_model)

    baseline_run_id: str | None = None
    if run_baseline:
        baseline_run_id = f"{run_id}_baseline" if run_id else None
        run_rag_harness(
            prompt=CSV_AUDIT_SCRIPT_PROMPT,
            repo_dir=repo_dir,
            queries=CSV_AUDIT_QUERIES,
            output_root=output_root,
            provider=None,
            use_model=False,
            run_id=baseline_run_id,
        )
        if baseline_run_id is None:
            baseline_run_id = "baseline"

    if verbose:
        print(f"[rag-model-smoke] enabled provider fallback/streaming {_provider_summary(provider)}", file=sys.stderr)
        print(f"[rag-model-smoke] using {_provider_summary(provider)}", file=sys.stderr)
        print(
            f"[rag-model-smoke] starting csv_audit_script_builder smoke "
            f"repo_dir={repo_dir} output_root={output_root or '<default>'} strict={strict} "
            f"stream_model={stream_model}",
            file=sys.stderr,
        )

    result = run_rag_harness(
        prompt=CSV_AUDIT_SCRIPT_PROMPT,
        repo_dir=repo_dir,
        queries=CSV_AUDIT_QUERIES,
        output_root=output_root,
        provider=provider,
        use_model=True,
        run_id=run_id,
    )

    if verbose:
        for step in getattr(result, "steps", []) or []:
            print(
                f"[rag-model-smoke] model-backed step {getattr(step, 'index', 0):02d} "
                f"{getattr(step, 'kind', 'unknown')} status={getattr(step, 'status', 'unknown')}",
                file=sys.stderr,
            )
        if dump_json:
            print("[rag-model-smoke] raw csv-audit RAG run:", file=sys.stderr)
            print(_json_dumps(result.as_dict() if hasattr(result, "as_dict") else result.to_dict()), file=sys.stderr)

    report = validate_csv_audit_model_smoke(result, strict=strict)
    object.__setattr__(report, "baseline_run_id", baseline_run_id)
    object.__setattr__(report, "final_plan_type", str(report.final_plan.get("type", "")).strip().lower())

    # Rewrite the report JSON after adding compatibility summary fields.
    _write_json(Path(report.report_path), report.to_dict())

    if verbose:
        print("[rag-model-smoke] validation report:", file=sys.stderr)
        print(_json_dumps(report.to_dict()), file=sys.stderr)

    return report

def _broken_code_repair_executor_context(result: RagRunResult) -> str:
    """Keep the tool-loop context small and avoid leaking repo internals as runnable imports."""

    final_plan = _coerce_dict(result.final_plan)
    retrieved_paths = _result_retrieved_paths(result)
    lines = [
        "You are in the Docker executor tool loop for a smoke test.",
        "The backend already runs your command inside the configured Docker executor image.",
        "Do not import main_computer, DockerExecutor, or run_docker_executors.",
        "Do not call Docker from inside the command.",
        "Use only Python standard library and shell/Python available in the container.",
        "Work only under /workspace/repair_target and write downloadable outputs under /outputs.",
        "You must prove the repair by printing INITIAL_FAILURE_CONFIRMED and FINAL_REPAIR_PASSED.",
        "You must create /outputs/repair_report.json.",
        "",
        "Retrieved path summary:",
    ]
    for path in retrieved_paths[:24]:
        lines.append(f"- {path}")
    if len(retrieved_paths) > 24:
        lines.append(f"- ... {len(retrieved_paths) - 24} more paths omitted")
    if final_plan:
        lines.extend(
            [
                "",
                "RAG planning summary:",
                _shorten(final_plan.get("summary", ""), limit=1000),
            ]
        )
    return "\n".join(lines)


def _executor_context_from_rag_result(result: RagRunResult) -> str:
    return _broken_code_repair_executor_context(result)


def _scripted_repair_command() -> str:
    return r"""python - <<'PY'
from pathlib import Path
import json
import subprocess
import sys
import textwrap

work = Path('/workspace/repair_target')
out = Path('/outputs')
work.mkdir(parents=True, exist_ok=True)
out.mkdir(parents=True, exist_ok=True)

calc = work / 'calc_stats.py'
tests = work / 'test_calc_stats.py'

calc.write_text(textwrap.dedent('''
    def mean(values):
        return 0

    def median(values):
        values.sort()
        return values[0]

    def normalize(values):
        return values
''').strip() + '\n', encoding='utf-8')

tests.write_text(textwrap.dedent('''
    import unittest
    from calc_stats import mean, median, normalize

    class CalcStatsTests(unittest.TestCase):
        def test_mean(self):
            self.assertEqual(mean([2, 4, 6]), 4)

        def test_median_odd_does_not_mutate_input(self):
            values = [9, 1, 5]
            self.assertEqual(median(values), 5)
            self.assertEqual(values, [9, 1, 5])

        def test_median_even(self):
            self.assertEqual(median([10, 2, 4, 8]), 6)

        def test_normalize(self):
            self.assertEqual(normalize([1, 1, 2]), [0.25, 0.25, 0.5])

        def test_normalize_zero_total(self):
            with self.assertRaises(ValueError):
                normalize([0, 0])

    if __name__ == '__main__':
        unittest.main()
''').strip() + '\n', encoding='utf-8')

initial = subprocess.run(
    [sys.executable, '-m', 'unittest', '-v', 'test_calc_stats.py'],
    cwd=work,
    text=True,
    capture_output=True,
)
print(initial.stdout, end='')
print(initial.stderr, end='', file=sys.stderr)
if initial.returncode == 0:
    raise SystemExit('Expected intentionally broken implementation to fail tests.')
print('INITIAL_FAILURE_CONFIRMED')

calc.write_text(textwrap.dedent('''
    def mean(values):
        values = list(values)
        if not values:
            raise ValueError('mean requires at least one value')
        return sum(values) / len(values)

    def median(values):
        ordered = sorted(values)
        if not ordered:
            raise ValueError('median requires at least one value')
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2

    def normalize(values):
        values = list(values)
        total = sum(values)
        if total == 0:
            raise ValueError('cannot normalize values with zero total')
        return [value / total for value in values]
''').strip() + '\n', encoding='utf-8')

final = subprocess.run(
    [sys.executable, '-m', 'unittest', '-v', 'test_calc_stats.py'],
    cwd=work,
    text=True,
    capture_output=True,
)
print(final.stdout, end='')
print(final.stderr, end='', file=sys.stderr)
if final.returncode != 0:
    raise SystemExit(final.returncode)
print('FINAL_REPAIR_PASSED')

report = {
    'initial_failure_confirmed': True,
    'final_repair_passed': True,
    'initial_exit_code': initial.returncode,
    'final_exit_code': final.returncode,
    'repaired_files': ['calc_stats.py', 'test_calc_stats.py'],
    'repair_summary': 'Fixed mean, median, and normalize using only Python standard library.',
}
(out / 'repair_report.json').write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print('WROTE_REPAIR_REPORT /outputs/repair_report.json')
PY"""


def _run_scripted_broken_code_repair(
    *,
    docker_executor: DockerExecutor,
    timeout_s: float,
) -> ExecutorToolLoopResult:
    request = ExecutorRequest(
        command=_scripted_repair_command(),
        cwd="/workspace",
        timeout_s=timeout_s,
        network=False,
        input_ids=[],
        artifact_globs=["**/*"],
        description="Scripted Docker repair smoke: create broken code, confirm failure, repair, retest.",
    )
    result: ExecutorResult = docker_executor.run(request)

    request_dict = request.as_dict()
    result_dict = result.as_dict() if hasattr(result, "as_dict") else result.to_dict()

    step_model = ExecutorToolLoopStep(
        index=1,
        kind="model",
        content=json.dumps(
            {
                "action": "execute_shell",
                "description": request.description,
                "command": request.command,
                "cwd": request.cwd,
                "timeout_s": request.timeout_s,
                "network": request.network,
                "input_ids": request.input_ids,
            },
            indent=2,
        ),
        tool_request=request_dict,
    )
    step_output = ExecutorToolLoopStep(
        index=1,
        kind="command_output",
        executor_result=result_dict,
        tool_request=request_dict,
    )

    stdout = result.stdout or ""
    final_content = "Scripted Docker repair completed successfully." if result.ok else ""
    status = "complete" if result.ok else "executor_error"
    return ExecutorToolLoopResult(
        ok=result.ok,
        status=status,
        provider="scripted-repair",
        model="deterministic-docker-driver",
        final_content=final_content,
        steps=[step_model, step_output],
        error=None if result.ok else (result.error or result.stderr or "Scripted Docker repair failed."),
    )


def run_broken_code_repair_docker_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    provider: LLMProvider | None = None,
    strict: bool = True,
    run_id: str | None = None,
    max_executor_steps: int = 4,
    verbose: bool = True,
    stream_model: bool = True,
    dump_json: bool = False,
    executor_driver: str = "scripted-repair",
) -> BrokenCodeRepairSmokeReport:
    if provider is None:
        provider = _get_local_ollama_provider()
    provider = _enable_provider_streaming(provider, stream_model=stream_model)

    if verbose:
        print(f"[rag-model-smoke] enabled provider fallback/streaming {_provider_summary(provider)}")
        print(
            f"[rag-model-smoke] starting broken_code_repair_docker smoke "
            f"repo_dir={repo_dir} output_root={output_root or '<default>'} strict={strict} "
            f"max_executor_steps={max_executor_steps} stream_model={stream_model} "
            f"executor_driver={executor_driver}"
        )

    result = run_rag_harness(
        prompt=BROKEN_CODE_REPAIR_PROMPT,
        repo_dir=repo_dir,
        queries=BROKEN_CODE_REPAIR_QUERIES,
        output_root=output_root,
        provider=provider,
        use_model=True,
        run_id=run_id,
    )

    if verbose:
        _log_rag_result("broken-code-repair", result)
        if dump_json:
            print("[rag-model-smoke] raw broken-code-repair RAG run:")
            print(_json_dumps(result.to_dict()))

    config = MainComputerConfig.from_env()
    executor_root = config.executor_root
    if not executor_root.is_absolute():
        executor_root = repo_dir / executor_root

    docker_executor = DockerExecutor(
        enabled=True,
        image=config.executor_image,
        runtime_root=executor_root,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )

    config = MainComputerConfig.from_env()
    executor_root = config.executor_root
    if not executor_root.is_absolute():
        executor_root = repo_dir / executor_root

    docker_executor = DockerExecutor(
        enabled=True,
        image=config.executor_image,
        runtime_root=executor_root,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )
    docker_status = docker_executor.status()

    if verbose:
        if dump_json:
            print("[rag-model-smoke] docker executor status:")
            print(_json_dumps(docker_status))
        else:
            print(f"[rag-model-smoke] docker executor status: {_docker_status_summary(docker_status)}")

    executor_result: ExecutorToolLoopResult | None = None
    if docker_status.get("docker_available"):
        if executor_driver == "scripted-repair":
            executor_result = _run_scripted_broken_code_repair(
                docker_executor=docker_executor,
                timeout_s=min(120.0, float(docker_status.get("max_timeout_s") or 120.0)),
            )
        elif executor_driver == "model-tool-loop":
            executor_context = _broken_code_repair_executor_context(result)
            executor_prompt = (
                BROKEN_CODE_REPAIR_PROMPT
                + "\n\nReturn exactly one valid JSON object per turn. "
                "The command field must be one JSON string, with embedded newlines escaped as \\n. "
                "Do not use Python string concatenation, markdown fences, comments, or trailing commas. "
                "If using a heredoc, include it inside a single JSON string."
            )
            executor_result = run_executor_tool_loop(
                provider=provider,
                prompt=executor_prompt,
                context_text=executor_context,
                docker_executor=docker_executor,
                config=ExecutorToolLoopConfig(
                    max_steps=max_executor_steps,
                    auto_run=True,
                    allow_network=False,
                    max_timeout_s=float(docker_status.get("max_timeout_s") or 120.0),
                ),
                upload_ids=[],
            )
        else:
            raise ValueError(f"Unsupported executor driver: {executor_driver!r}")

        if verbose:
            _log_executor_result(executor_result)
            if dump_json:
                print("[rag-model-smoke] raw executor tool loop result:")
                print(_json_dumps(executor_result.to_dict()))

    report = validate_broken_code_repair_docker_smoke(
        result,
        executor_result=executor_result,
        docker_status=docker_status,
        strict=strict,
    )

    if verbose:
        print("[rag-model-smoke] broken code repair validation report:")
        print(_json_dumps(report.to_dict()))

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run model-backed RAG smoke tests.")
    parser.add_argument(
        "--scenario",
        choices=["csv-audit", "broken-code-repair"],
        default="csv-audit",
        help="Smoke scenario to run.",
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
        help="Do not force provider streaming/fallback mode for the model calls.",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Print full raw RAG/executor JSON diagnostics. By default verbose output is summarized.",
    )
    parser.add_argument(
        "--max-executor-steps",
        type=int,
        default=4,
        help="Maximum model/executor loop steps for the broken-code-repair scenario.",
    )
    parser.add_argument(
        "--executor-driver",
        choices=["scripted-repair", "model-tool-loop"],
        default="scripted-repair",
        help=(
            "Docker execution driver for broken-code-repair. scripted-repair is the stable smoke; "
            "model-tool-loop exercises raw model-generated execute_shell JSON."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    verbose = not args.quiet
    stream_model = not args.no_stream

    if args.scenario == "csv-audit":
        report = run_csv_audit_model_smoke(
            repo_dir=args.repo_dir,
            output_root=args.output_root,
            strict=args.strict,
            run_id=args.run_id,
            verbose=verbose,
            stream_model=stream_model,
            dump_json=args.dump_json,
        )
        print(f"Scenario: {report.scenario}")
        print(f"Model run: {report.run_id}")
        print(f"Output: {report.output_dir}")
        print(f"Report: {report.report_path}")
        print(f"Verbose diagnostics: {'off' if args.quiet else 'on'}")
        print(f"Model streaming: {'off' if args.no_stream else 'on'}")
        print(f"Raw JSON diagnostics: {'on' if args.dump_json else 'off'}")
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

    report = run_broken_code_repair_docker_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        strict=args.strict,
        run_id=args.run_id,
        max_executor_steps=args.max_executor_steps,
        verbose=verbose,
        stream_model=stream_model,
        dump_json=args.dump_json,
        executor_driver=args.executor_driver,
    )
    executor_status = "not-run"
    if report.executor_result:
        executor_status = str(report.executor_result.get("status") or "unknown")
    print(f"Scenario: {report.scenario}")
    print(f"Model run: {report.run_id}")
    print(f"RAG run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Executor status: {executor_status}")
    print(f"Docker available: {report.docker_status.get('docker_available')}")
    print(f"Report: {report.report_path}")
    print(f"Verbose diagnostics: {'off' if args.quiet else 'on'}")
    print(f"Model streaming: {'off' if args.no_stream else 'on'}")
    print(f"Raw JSON diagnostics: {'on' if args.dump_json else 'off'}")
    print(f"Executor driver: {args.executor_driver}")
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