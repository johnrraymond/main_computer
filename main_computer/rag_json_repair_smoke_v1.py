from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import re
import sys
from typing import Any, Callable

from main_computer.config import MainComputerConfig
from main_computer.docker_executor import DockerExecutor
from main_computer.executor_models import ExecutorRequest, ExecutorResult
from main_computer.models import ChatMessage
from main_computer.providers import LLMProvider, OllamaProvider


WORKING_JSON_VALUE: dict[str, Any] = {
    "enabled": True,
    "items": [
        {"id": "alpha", "score": 0.91, "tags": ["retrieved", "ranked"]},
        {"id": "beta", "score": 0.42, "tags": ["fallback"]},
    ],
    "metadata": {
        "scenario": "rag-json-repair-smoke",
        "source": "model-output-fragment",
        "version": 1,
    },
    "threshold": 0.75,
}


def working_json_fragment() -> str:
    return json.dumps(WORKING_JSON_VALUE, indent=2, sort_keys=True)


@dataclass(frozen=True)
class DockerJsonParseResult:
    ok: bool
    error_string: str
    canonical_json: str | None
    stdout: str
    stderr: str
    exit_code: int | None
    job_id: str
    duration_ms: int
    raw_result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JsonRepairAttemptReport:
    index: int
    break_count: int
    seed: int
    techniques: list[str]
    broken_json: str
    first_parse: DockerJsonParseResult
    model_response: str
    repaired_json: str
    final_parse: DockerJsonParseResult
    semantic_match: bool

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["first_parse"] = self.first_parse.to_dict()
        data["final_parse"] = self.final_parse.to_dict()
        return data


@dataclass(frozen=True)
class JsonRepairSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    docker_status: dict[str, Any]
    attempts: list[JsonRepairAttemptReport]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = True
    seed: int = 1729

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        data["attempt_count"] = len(self.attempts)
        data["break_counts"] = [attempt.break_count for attempt in self.attempts]
        return data


BreakageFn = Callable[[str, random.Random], str]


def _json_dumps(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=True)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(data) + "\n", encoding="utf-8")


def _enable_provider_streaming(provider: LLMProvider, *, stream_model: bool) -> LLMProvider:
    if not stream_model:
        return provider
    if hasattr(provider, "fallback"):
        try:
            return replace(provider, fallback=True)  # type: ignore[arg-type]
        except TypeError:
            return provider
    return provider


def _get_local_ollama_provider() -> LLMProvider:
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=config.model or "gemma4:26b",
        base_url=config.ollama_base_url,
        timeout_s=config.ollama_timeout_s,
        fallback=config.fallback,
    )


def _provider_summary(provider: LLMProvider) -> str:
    name = getattr(provider, "name", provider.__class__.__name__)
    model = getattr(provider, "model", "")
    return f"provider={name} model={model}".strip()


def _docker_status_summary(status: dict[str, Any]) -> str:
    return (
        f"enabled={status.get('enabled')} "
        f"ok={status.get('ok')} "
        f"docker_available={status.get('docker_available')} "
        f"image={status.get('image')} "
        f"runtime_root={status.get('runtime_root')}"
    )


def _new_docker_executor() -> DockerExecutor:
    config = MainComputerConfig.from_env()
    return DockerExecutor(
        image=config.executor_image,
        runtime_root=config.executor_root,
        enabled=True,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )


def _is_invalid_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return True
    return False


def _random_index(text: str, chars: str, rng: random.Random) -> int:
    positions = [index for index, char in enumerate(text) if char in chars]
    if not positions:
        return -1
    return rng.choice(positions)


def _remove_random_quote(text: str, rng: random.Random) -> str:
    index = _random_index(text, '"', rng)
    if index < 0:
        return text + "\n'"
    return text[:index] + text[index + 1 :]


def _remove_random_comma(text: str, rng: random.Random) -> str:
    index = _random_index(text, ",", rng)
    if index < 0:
        return text + ","
    return text[:index] + text[index + 1 :]


def _replace_random_colon_with_equals(text: str, rng: random.Random) -> str:
    index = _random_index(text, ":", rng)
    if index < 0:
        return text + "="
    return text[:index] + "=" + text[index + 1 :]


def _insert_trailing_comma(text: str, rng: random.Random) -> str:
    closers = [match.start() for match in re.finditer(r"(?m)^(\s*)([]}])", text)]
    if not closers:
        return text + ","
    index = rng.choice(closers)
    return text[:index] + "," + text[index:]


def _truncate_final_closer(text: str, rng: random.Random) -> str:
    stripped = text.rstrip()
    if not stripped:
        return text
    return stripped[:-1] + "\n"


def _insert_bare_token(text: str, rng: random.Random) -> str:
    lines = text.splitlines()
    if not lines:
        return "BROKEN_TOKEN"
    insert_at = rng.randrange(1, max(2, len(lines)))
    lines.insert(insert_at, "  BROKEN_TOKEN")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


BREAKAGE_TECHNIQUES: list[tuple[str, BreakageFn]] = [
    ("remove_random_quote", _remove_random_quote),
    ("remove_random_comma", _remove_random_comma),
    ("replace_random_colon_with_equals", _replace_random_colon_with_equals),
    ("insert_trailing_comma", _insert_trailing_comma),
    ("truncate_final_closer", _truncate_final_closer),
    ("insert_bare_token", _insert_bare_token),
]


def break_json_fragment(*, break_count: int, seed: int, text: str | None = None) -> tuple[str, list[str]]:
    """Apply deterministic random syntax breakages to a working JSON fragment."""

    if break_count < 1:
        raise ValueError("break_count must be at least 1.")
    if break_count > len(BREAKAGE_TECHNIQUES):
        raise ValueError(f"break_count is limited to {len(BREAKAGE_TECHNIQUES)} techniques.")

    rng = random.Random(seed)
    broken = text if text is not None else working_json_fragment()
    available = list(BREAKAGE_TECHNIQUES)
    applied: list[str] = []

    for _ in range(break_count):
        rng.shuffle(available)
        accepted = False
        for index, (name, fn) in enumerate(list(available)):
            candidate = fn(broken, rng)
            if candidate != broken:
                broken = candidate
                applied.append(name)
                del available[index]
                accepted = True
                break
        if not accepted:
            raise RuntimeError("Could not apply requested JSON breakage.")

    if not _is_invalid_json(broken):
        # Extremely defensive fallback: make the final fragment unquestionably invalid.
        broken = _insert_bare_token(broken, rng)
        applied.append("insert_bare_token_fallback")

    return broken, applied


def _parse_json_command() -> str:
    return r"""python - <<'PY'
import json
import os
import sys

text = os.environ.get("JSON_TEXT", "")
try:
    parsed = json.loads(text)
except json.JSONDecodeError as exc:
    print(
        f"JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno} (pos {exc.pos})",
        file=sys.stderr,
    )
    raise SystemExit(1)

print("JSON_PARSE_OK")
print("CANONICAL_JSON=" + json.dumps(parsed, sort_keys=True, separators=(",", ":")))
PY"""


def _extract_canonical_json(stdout: str) -> str | None:
    prefix = "CANONICAL_JSON="
    for line in str(stdout or "").splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :]
    return None


def _error_string_from_executor_result(result: ExecutorResult) -> str:
    pieces = [result.stderr, result.stdout, result.error or ""]
    text = "\n".join(piece for piece in pieces if piece).strip()
    return text or f"JSON parser exited with code {result.exit_code!r}."


def parse_json_with_docker(
    *,
    docker_executor: DockerExecutor,
    json_text: str,
    timeout_s: float = 30.0,
) -> DockerJsonParseResult:
    request = ExecutorRequest(
        command=_parse_json_command(),
        cwd="/workspace",
        timeout_s=timeout_s,
        network=False,
        input_ids=[],
        artifact_globs=[],
        description="Parse a JSON fragment inside the Docker executor.",
        env={"JSON_TEXT": json_text},
    )
    result = docker_executor.run(request)
    canonical_json = _extract_canonical_json(result.stdout) if result.ok else None
    error_string = "" if result.ok else _error_string_from_executor_result(result)
    return DockerJsonParseResult(
        ok=result.ok,
        error_string=error_string,
        canonical_json=canonical_json,
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        job_id=result.job_id,
        duration_ms=result.duration_ms,
        raw_result=result.as_dict(),
    )


def _repair_prompt(*, broken_json: str, error_string: str) -> str:
    return (
        "You are repairing a JSON fragment that came back from a previous model call.\n"
        "The fragment was produced by applying random syntax damage to an otherwise valid JSON object.\n"
        "Use the parser error to repair the JSON while preserving the same keys, values, arrays, and object shape.\n"
        "Return only the corrected JSON text. Do not wrap it in markdown. Do not add commentary.\n\n"
        "Broken JSON:\n"
        f"{broken_json}\n\n"
        "Docker JSON parser error:\n"
        f"{error_string}\n"
    )


def ask_model_to_fix_json(
    *,
    provider: LLMProvider,
    broken_json: str,
    error_string: str,
) -> str:
    response = provider.chat(
        [
            ChatMessage(
                role="system",
                content=(
                    "You repair invalid JSON. Return exactly one valid JSON value and no prose. "
                    "Preserve the original data whenever it can be inferred."
                ),
            ),
            ChatMessage(role="user", content=_repair_prompt(broken_json=broken_json, error_string=error_string)),
        ]
    )
    return response.content


def extract_json_text(model_response: str) -> str:
    text = str(model_response or "").strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    candidate = _first_json_value(text)
    if candidate is not None:
        return candidate
    return text


def _first_json_value(text: str) -> str | None:
    starts = [(index, char) for index, char in enumerate(text) if char in "{["]
    for start, opener in starts:
        closer_for = {"{": "}", "[": "]"}
        stack = [closer_for[opener]]
        in_string = False
        escape = False
        for index in range(start + 1, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char in "{[":
                stack.append(closer_for[char])
                continue
            if stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    candidate = text[start : index + 1]
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    return candidate
        continue
    return None


def _canonical_matches_working(canonical_json: str | None) -> bool:
    if not canonical_json:
        return False
    try:
        return json.loads(canonical_json) == WORKING_JSON_VALUE
    except json.JSONDecodeError:
        return False


def _default_run_id() -> str:
    return "json_repair_smoke_" + datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")


def run_json_repair_model_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None = None,
    provider: LLMProvider | None = None,
    docker_executor: DockerExecutor | None = None,
    strict: bool = True,
    run_id: str | None = None,
    seed: int = 1729,
    verbose: bool = True,
    stream_model: bool = True,
    dump_json: bool = False,
) -> JsonRepairSmokeReport:
    """Run the broken-JSON repair smoke through Docker parse failures and model fixes."""

    run_id = run_id or _default_run_id()
    output_dir = (output_root or (repo_dir / "diagnostics_output" / "rag_runs")) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    if provider is None:
        provider = _get_local_ollama_provider()
    provider = _enable_provider_streaming(provider, stream_model=stream_model)

    if docker_executor is None:
        docker_executor = _new_docker_executor()

    docker_status = docker_executor.status()
    if verbose:
        print(f"[rag-json-repair-smoke] using {_provider_summary(provider)}")
        print(f"[rag-json-repair-smoke] docker executor status: {_docker_status_summary(docker_status)}")
        print(
            f"[rag-json-repair-smoke] starting scenario run_id={run_id} "
            f"repo_dir={repo_dir} output_dir={output_dir} seed={seed}"
        )

    attempts: list[JsonRepairAttemptReport] = []
    failures: list[str] = []
    warnings: list[str] = []

    if not docker_status.get("docker_available"):
        failures.append("Docker is required for this smoke because both broken and repaired JSON must be parsed in Docker.")

    if not failures:
        for index, break_count in enumerate((1, 2, 3), start=1):
            case_seed = seed + (index * 1009)
            broken_json, techniques = break_json_fragment(break_count=break_count, seed=case_seed)
            first_parse = parse_json_with_docker(docker_executor=docker_executor, json_text=broken_json)

            if first_parse.ok:
                failures.append(f"Attempt {index}: broken JSON unexpectedly parsed successfully in Docker.")
                error_string = "Broken JSON unexpectedly parsed successfully."
            else:
                error_string = first_parse.error_string

            model_response = ask_model_to_fix_json(
                provider=provider,
                broken_json=broken_json,
                error_string=error_string,
            )
            repaired_json = extract_json_text(model_response)
            final_parse = parse_json_with_docker(docker_executor=docker_executor, json_text=repaired_json)
            semantic_match = _canonical_matches_working(final_parse.canonical_json)

            if not final_parse.ok:
                failures.append(f"Attempt {index}: repaired JSON did not parse in Docker: {final_parse.error_string}")
            elif not semantic_match:
                failures.append(f"Attempt {index}: repaired JSON parsed but did not preserve the original JSON value.")

            attempt = JsonRepairAttemptReport(
                index=index,
                break_count=break_count,
                seed=case_seed,
                techniques=techniques,
                broken_json=broken_json,
                first_parse=first_parse,
                model_response=model_response,
                repaired_json=repaired_json,
                final_parse=final_parse,
                semantic_match=semantic_match,
            )
            attempts.append(attempt)

            if verbose:
                print(
                    f"[rag-json-repair-smoke] attempt={index} breaks={break_count} "
                    f"techniques={','.join(techniques)} first_ok={first_parse.ok} "
                    f"final_ok={final_parse.ok} semantic_match={semantic_match}"
                )
                if dump_json:
                    print(_json_dumps(attempt.to_dict()))

    if strict and warnings:
        failures.extend(warnings)

    report_path = output_dir / "json_repair_smoke_report.json"
    report = JsonRepairSmokeReport(
        ok=not failures,
        run_id=run_id,
        scenario="json_repair_model_docker",
        output_dir=str(output_dir),
        report_path=str(report_path),
        docker_status=dict(docker_status),
        attempts=attempts,
        warnings=warnings,
        failures=failures,
        strict=strict,
        seed=seed,
    )
    _write_json(report_path, report.to_dict())

    if verbose:
        print("[rag-json-repair-smoke] validation report:")
        print(_json_dumps(report.to_dict() if dump_json else {
            "ok": report.ok,
            "run_id": report.run_id,
            "scenario": report.scenario,
            "attempt_count": len(report.attempts),
            "break_counts": [attempt.break_count for attempt in report.attempts],
            "warnings": report.warnings,
            "failures": report.failures,
            "report_path": report.report_path,
        }))

    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the broken JSON repair RAG/model smoke test.")
    parser.add_argument(
        "--repo-dir",
        type=Path,
        default=Path.cwd(),
        help="Repository root. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Optional diagnostics output root. Defaults to diagnostics_output/rag_runs.",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--run-id", default=None, help="Optional deterministic run id.")
    parser.add_argument("--seed", type=int, default=1729, help="Deterministic seed for random breakage selection.")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose smoke diagnostics.")
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Do not force provider streaming/fallback mode for the model repair calls.",
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Print full raw attempt JSON diagnostics. By default verbose output is summarized.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_json_repair_model_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        strict=args.strict,
        run_id=args.run_id,
        seed=args.seed,
        verbose=not args.quiet,
        stream_model=not args.no_stream,
        dump_json=args.dump_json,
    )

    print(f"Scenario: {report.scenario}")
    print(f"Run: {report.run_id}")
    print(f"Output: {report.output_dir}")
    print(f"Report: {report.report_path}")
    print(f"Docker available: {report.docker_status.get('docker_available')}")
    print(f"Attempts: {len(report.attempts)}")
    print(f"Break counts: {[attempt.break_count for attempt in report.attempts]}")
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


if __name__ == "__main__":
    raise SystemExit(main())
