from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
import json
from pathlib import Path
import random
import re
from typing import Any, Callable

from main_computer.config import MainComputerConfig
from main_computer.docker_executor import DockerExecutor
from main_computer.executor_models import ExecutorRequest
from main_computer.models import ChatMessage
from main_computer.providers import LLMProvider, OllamaProvider


WORKING_JSON_VALUE: dict[str, Any] = {
    "name": "main-computer-json-repair-smoke",
    "ok": True,
    "items": [
        {"id": 1, "label": "alpha"},
        {"id": 2, "label": "beta"},
    ],
    "metrics": {"row_count": 2, "blank_cell_count": 0},
}


BreakageFn = Callable[[str, random.Random], str]


@dataclass(frozen=True)
class JsonRepairAttempt:
    break_count: int
    seed: int
    techniques: list[str]
    initial_parse_ok: bool
    repaired_parse_ok: bool
    semantic_match: bool
    docker_error: str = ""
    repair_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class JsonRepairSmokeReport:
    ok: bool
    run_id: str
    scenario: str
    output_dir: str
    report_path: str
    attempts: list[JsonRepairAttempt]
    docker_status: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        return data


def working_json_fragment() -> str:
    return json.dumps(WORKING_JSON_VALUE, indent=2, sort_keys=True)


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
        return text + "BROKEN"
    return stripped[:-1] + "\n"


def _insert_bare_token(text: str, rng: random.Random) -> str:
    lines = text.splitlines()
    insert_at = rng.randrange(1, max(2, len(lines))) if lines else 0
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
        broken = _insert_bare_token(broken, rng)
        applied.append("insert_bare_token_fallback")
    return broken, applied


def extract_json_text(raw: str) -> str:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    start_candidates = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if start_candidates:
        start = min(start_candidates)
        end = max(text.rfind("}"), text.rfind("]"))
        if end >= start:
            return text[start : end + 1].strip()
    return text


def _get_provider() -> LLMProvider:
    config = MainComputerConfig.from_env()
    return OllamaProvider(
        model=config.model,
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
            try:
                setattr(provider, "fallback", True)
            except Exception:
                pass
    return provider


def _docker_executor(repo_dir: Path) -> DockerExecutor:
    config = MainComputerConfig.from_env()
    runtime_root = config.executor_root
    if not runtime_root.is_absolute():
        runtime_root = repo_dir / runtime_root
    return DockerExecutor(
        image=config.executor_image,
        runtime_root=runtime_root,
        enabled=True,
        max_timeout_s=config.executor_timeout_s,
        max_upload_bytes=config.executor_max_upload_bytes,
        max_output_chars=config.executor_max_output_chars,
    )


def _parse_in_docker(docker_executor: DockerExecutor, text: str) -> tuple[bool, str, str]:
    request = ExecutorRequest(
        command=(
            "python - <<'PY'\n"
            "import json, os, sys\n"
            "text = os.environ.get('JSON_TEXT', '')\n"
            "try:\n"
            "    parsed = json.loads(text)\n"
            "except json.JSONDecodeError as exc:\n"
            "    print(f'JSONDecodeError: {exc.msg} at line {exc.lineno} column {exc.colno} (pos {exc.pos})', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "print('JSON_PARSE_OK')\n"
            "print('CANONICAL_JSON=' + json.dumps(parsed, sort_keys=True, separators=(',', ':')))\n"
            "PY"
        ),
        cwd="/workspace",
        timeout_s=30,
        network=False,
        input_ids=[],
        artifact_globs=[],
        description="Validate JSON in Docker.",
        env={"JSON_TEXT": text},
    )
    result = docker_executor.run(request)
    return bool(result.ok), result.stdout or "", result.stderr or result.error or ""


def _repair_with_model(provider: LLMProvider, *, broken: str, docker_error: str) -> str:
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Repair the supplied JSON so it parses and preserves the intended object. "
                "Return only the repaired JSON, with no explanation."
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                "Working target semantic shape:\n"
                + working_json_fragment()
                + "\n\nBroken JSON:\n"
                + broken
                + "\n\nDocker JSON parser error:\n"
                + docker_error
            ),
        ),
    ]
    response = provider.chat(messages)
    return extract_json_text(response.content)


def run_json_repair_model_smoke(
    *,
    repo_dir: Path,
    output_root: Path | None,
    provider: LLMProvider | None = None,
    docker_executor: DockerExecutor | None = None,
    run_id: str | None = None,
    strict: bool = False,
    seed: int = 1729,
    verbose: bool = True,
    stream_model: bool = True,
    dump_json: bool = False,
) -> JsonRepairSmokeReport:
    run_id = run_id or "json_repair_smoke"
    output_dir = Path(output_root or (Path(repo_dir) / "diagnostics_output" / "json_repair_smoke")) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    provider = _enable_provider_streaming(provider or _get_provider(), stream_model=stream_model)
    docker_executor = docker_executor or _docker_executor(Path(repo_dir))
    docker_status = docker_executor.status()

    attempts: list[JsonRepairAttempt] = []
    failures: list[str] = []
    warnings: list[str] = []

    if not docker_status.get("docker_available"):
        failures.append(f"Docker is required for JSON repair smoke: {docker_status.get('docker_error') or 'unavailable'}")

    for break_count in (1, 2, 3):
        broken, techniques = break_json_fragment(
            break_count=break_count,
            seed=seed + break_count,
            text=working_json_fragment(),
        )
        initial_ok, initial_stdout, initial_stderr = _parse_in_docker(docker_executor, broken)
        repaired_ok = False
        semantic_match = False
        repair_error = ""
        repaired_text = ""

        if initial_ok:
            repair_error = "broken JSON unexpectedly parsed"
        else:
            repaired_text = _repair_with_model(provider, broken=broken, docker_error=initial_stderr or initial_stdout)
            repaired_ok, repaired_stdout, repaired_stderr = _parse_in_docker(docker_executor, repaired_text)
            if repaired_ok:
                try:
                    semantic_match = json.loads(repaired_text) == WORKING_JSON_VALUE
                except json.JSONDecodeError as exc:
                    repair_error = str(exc)
            else:
                repair_error = repaired_stderr or repaired_stdout or "repaired JSON did not parse in Docker"

        attempt = JsonRepairAttempt(
            break_count=break_count,
            seed=seed + break_count,
            techniques=techniques,
            initial_parse_ok=initial_ok,
            repaired_parse_ok=repaired_ok,
            semantic_match=semantic_match,
            docker_error=initial_stderr or initial_stdout,
            repair_error=repair_error,
        )
        attempts.append(attempt)
        (output_dir / f"broken_{break_count}.json.txt").write_text(broken, encoding="utf-8")
        (output_dir / f"repaired_{break_count}.json").write_text(repaired_text, encoding="utf-8")
        if initial_ok:
            failures.append(f"break_count={break_count}: broken JSON unexpectedly parsed")
        if not repaired_ok:
            failures.append(f"break_count={break_count}: repaired JSON did not parse in Docker")
        if repaired_ok and not semantic_match:
            failures.append(f"break_count={break_count}: repaired JSON parsed but did not match target semantics")

    ok = not failures and (not strict or not warnings)
    if strict and warnings:
        failures.extend(warnings)

    report_path = output_dir / "json_repair_smoke_report.json"
    report = JsonRepairSmokeReport(
        ok=ok,
        run_id=run_id,
        scenario="json_repair_model_docker",
        output_dir=str(output_dir),
        report_path=str(report_path),
        attempts=attempts,
        docker_status=docker_status,
        warnings=warnings,
        failures=failures,
        strict=strict,
    )
    report_path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if verbose:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Docker-backed model JSON repair smoke test.")
    parser.add_argument("--repo-dir", type=Path, default=Path.cwd())
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--no-stream", action="store_true")
    parser.add_argument("--dump-json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_json_repair_model_smoke(
        repo_dir=args.repo_dir,
        output_root=args.output_root,
        run_id=args.run_id,
        strict=args.strict,
        seed=args.seed,
        verbose=not args.quiet,
        stream_model=not args.no_stream,
        dump_json=args.dump_json,
    )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
