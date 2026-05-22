from __future__ import annotations

"""Functional smoke for a generated new_patch.py candidate.

This is the next level after the latest-output compile/repair smoke.  It does
not auto-discover the first smoke-test run; pass that output directory
explicitly.  For each invocation it creates a fresh child run directory and
records both a human-readable render stream and raw provider/process evidence.

The gates are intentionally small and concrete:
1. verify the selected candidate compiles in Docker;
2. run the candidate against a tiny zip artifact in Docker with --dry-run;
3. verify the dry-run reports a real diff and leaves the fixture file unchanged;
4. write test_passed.txt only when all gates pass.

The original proposed_new_patch.py is never overwritten.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DOCKER_IMAGE = "main-computer-executor:latest"
PASS_FILE_NAME = "test_passed.txt"


@dataclass(frozen=True)
class CandidateContext:
    repo_root: Path
    output_dir: Path
    candidate: Path
    run_dir: Path
    provider_raw: Path
    render_raw: Path


@dataclass(frozen=True)
class ProcessResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def log(message: str = "") -> None:
    print(message, flush=True)


def append_raw(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(text)


def raw_header(title: str) -> str:
    return "\n" + ("=" * 20) + f" {title} " + ("=" * 20) + "\n"


def render(ctx: CandidateContext, message: str = "") -> None:
    log(message)
    append_raw(ctx.render_raw, message + "\n")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_root_from_cwd() -> Path:
    cwd = Path.cwd().resolve()
    if (cwd / "new_patch.py").exists() and (cwd / "debug_assets").exists():
        return cwd
    raise SystemExit("Run from repository root. Expected new_patch.py and debug_assets/.")


def resolve_output_dir(repo_root: Path, output_dir_arg: str) -> Path:
    raw = Path(output_dir_arg)
    output_dir = raw if raw.is_absolute() else repo_root / raw
    output_dir = output_dir.resolve()
    if not output_dir.exists() or not output_dir.is_dir():
        raise SystemExit(f"First smoke-test output dir does not exist: {output_dir}")
    if not (output_dir / "master_results.json").exists():
        raise SystemExit(f"Expected master_results.json in first smoke-test output dir: {output_dir}")
    if not (output_dir / "proposed_new_patch.py").exists():
        raise SystemExit(f"Expected proposed_new_patch.py in first smoke-test output dir: {output_dir}")
    return output_dir


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_candidate(output_dir: Path, candidate_arg: str | None) -> Path:
    if not candidate_arg:
        candidate = output_dir / "proposed_new_patch.py"
    else:
        raw = Path(candidate_arg)
        candidate = raw if raw.is_absolute() else output_dir / raw
    candidate = candidate.resolve()
    if not candidate.exists() or not candidate.is_file():
        raise SystemExit(f"Candidate file does not exist: {candidate}")
    if not _is_relative_to(candidate, output_dir):
        raise SystemExit(
            "Candidate must be inside the first smoke-test output directory. "
            f"candidate={candidate} output_dir={output_dir}"
        )
    return candidate


def make_run_dir(output_dir: Path, candidate: Path, run_id: str | None = None) -> Path:
    base = output_dir / "candidate_functional_smoke_runs"
    base.mkdir(parents=True, exist_ok=True)
    candidate_label = candidate.stem.replace(" ", "_")
    name = run_id or f"functional_{candidate_label}_{utc_stamp()}"
    run_dir = base / name
    if not run_dir.exists():
        run_dir.mkdir(parents=True)
        return run_dir
    suffix = 2
    while True:
        alternative = base / f"{name}_{suffix}"
        if not alternative.exists():
            alternative.mkdir(parents=True)
            return alternative
        suffix += 1


def build_context(repo_root: Path, output_dir_arg: str, candidate_arg: str | None, run_id: str | None) -> CandidateContext:
    output_dir = resolve_output_dir(repo_root, output_dir_arg)
    candidate = resolve_candidate(output_dir, candidate_arg)
    run_dir = make_run_dir(output_dir, candidate, run_id)
    return CandidateContext(
        repo_root=repo_root,
        output_dir=output_dir,
        candidate=candidate,
        run_dir=run_dir,
        provider_raw=run_dir / "provider.raw",
        render_raw=run_dir / "render.raw",
    )


def docker_relpath(repo_root: Path, target: Path) -> str:
    try:
        return target.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise SystemExit(f"Target is outside repo root: {target}") from exc


def run_process(argv: list[str], *, timeout_s: float) -> ProcessResult:
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
        )
        return ProcessResult(
            argv=argv,
            returncode=int(result.returncode),
            stdout=result.stdout,
            stderr=result.stderr,
            elapsed_s=round(time.monotonic() - started, 3),
        )
    except subprocess.TimeoutExpired as exc:
        return ProcessResult(
            argv=argv,
            returncode=124,
            stdout=str(exc.stdout or ""),
            stderr=(str(exc.stderr or "") + f"\nTimeoutExpired after {timeout_s}s").strip(),
            elapsed_s=round(time.monotonic() - started, 3),
        )


def docker_compile(ctx: CandidateContext, docker_image: str, *, timeout_s: float) -> ProcessResult:
    rel_candidate = docker_relpath(ctx.repo_root, ctx.candidate)
    command = (
        "python - <<'PY'\n"
        "import py_compile\n"
        f"py_compile.compile({rel_candidate!r}, doraise=True)\n"
        f"print('PY_COMPILE_OK {rel_candidate}')\n"
        "PY"
    )
    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{str(ctx.repo_root)}:/workspace",
        "-w",
        "/workspace",
        docker_image,
        "sh",
        "-lc",
        command,
    ]
    return run_process(argv, timeout_s=timeout_s)


def create_fixture(ctx: CandidateContext) -> dict[str, str]:
    fixture_root = ctx.run_dir / "fixture_repo"
    fixture_root.mkdir(parents=True, exist_ok=True)
    target = fixture_root / "sample.txt"
    target.write_text("old line\n", encoding="utf-8", newline="\n")

    artifact = ctx.run_dir / "fixture_patch.zip"
    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("sample.txt", "new line\n")

    return {
        "fixture_root": str(fixture_root),
        "target": str(target),
        "artifact": str(artifact),
        "target_before": target.read_text(encoding="utf-8"),
    }


def docker_dry_run_fixture(ctx: CandidateContext, docker_image: str, *, timeout_s: float) -> ProcessResult:
    fixture = create_fixture(ctx)
    write_json(ctx.run_dir / "fixture.json", fixture)

    rel_candidate = docker_relpath(ctx.repo_root, ctx.candidate)
    rel_fixture_root = docker_relpath(ctx.repo_root, Path(fixture["fixture_root"]))
    rel_artifact = docker_relpath(ctx.repo_root, Path(fixture["artifact"]))

    command = (
        "set -e\n"
        f"python /workspace/{rel_candidate} /workspace/{rel_artifact} --dry-run\n"
    )
    argv = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{str(ctx.repo_root)}:/workspace",
        "-w",
        f"/workspace/{rel_fixture_root}",
        docker_image,
        "sh",
        "-lc",
        command,
    ]
    return run_process(argv, timeout_s=timeout_s)


def dry_run_output_has_diff(result: ProcessResult) -> bool:
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    lowered = combined.lower()
    return (
        result.returncode == 0
        and "old line" in combined
        and "new line" in combined
        and (
            "---" in combined
            or "+++" in combined
            or "-old line" in combined
            or "+new line" in combined
            or "diff" in lowered
        )
    )


def fixture_target_unchanged(ctx: CandidateContext) -> bool:
    fixture_path = ctx.run_dir / "fixture.json"
    if not fixture_path.exists():
        return False
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    target = Path(fixture["target"])
    return target.exists() and target.read_text(encoding="utf-8") == fixture.get("target_before")


def write_process_raw(ctx: CandidateContext, label: str, result: ProcessResult) -> None:
    append_raw(ctx.provider_raw, raw_header(label))
    append_raw(ctx.provider_raw, "argv=" + json.dumps(result.argv) + "\n")
    append_raw(ctx.provider_raw, f"returncode={result.returncode}\nelapsed_s={result.elapsed_s}\n")
    append_raw(ctx.provider_raw, raw_header(f"{label} stdout") + (result.stdout or ""))
    append_raw(ctx.provider_raw, raw_header(f"{label} stderr") + (result.stderr or ""))


def print_process_result(ctx: CandidateContext, label: str, result: ProcessResult) -> None:
    render(ctx, f"--- {label} ---")
    render(ctx, f"returncode={result.returncode}")
    render(ctx, f"elapsed_s={result.elapsed_s}")
    if result.stdout:
        render(ctx, "--- stdout ---")
        render(ctx, result.stdout.rstrip())
    if result.stderr:
        render(ctx, "--- stderr ---")
        render(ctx, result.stderr.rstrip())


def compile_error_line_number(stderr: str) -> int | None:
    matches = list(re.finditer(r'line\s+(\d+)', str(stderr or "")))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except ValueError:
        return None


def source_excerpt_around_line(source: str, line_number: int | None, *, radius: int = 8) -> str:
    if line_number is None or line_number < 1:
        return ""
    lines = str(source or "").splitlines()
    if not lines:
        return ""
    start = max(1, line_number - radius)
    end = min(len(lines), line_number + radius)
    rendered: list[str] = []
    for number in range(start, end + 1):
        marker = ">>" if number == line_number else "  "
        rendered.append(f"{marker} {number:4}: {lines[number - 1]}")
    return "\n".join(rendered)


def process_failure_context(
    *,
    ctx: CandidateContext,
    stage: str,
    result: ProcessResult,
) -> dict[str, Any]:
    source = ctx.candidate.read_text(encoding="utf-8", errors="replace")
    line_number = compile_error_line_number(result.stderr) if stage == "compile" else None
    return {
        "stage": stage,
        "candidate": str(ctx.candidate),
        "candidate_name": ctx.candidate.name,
        "returncode": result.returncode,
        "stdout_preview": result.stdout[:4000],
        "stderr_preview": result.stderr[:8000],
        "line_number": line_number,
        "source_excerpt": source_excerpt_around_line(source, line_number),
        "candidate_chars": len(source),
        "candidate_sha256": sha256_file(ctx.candidate),
    }


def build_ai_repair_handoff_prompt(ctx: CandidateContext, failure_context: dict[str, Any]) -> str:
    source = ctx.candidate.read_text(encoding="utf-8", errors="replace")
    return (
        "You are repairing a generated candidate new_patch.py from the RAG new_patch smoke chain.\n"
        "\n"
        "Goal:\n"
        "- Return a complete replacement implementation for path new_patch.py.\n"
        "- The replacement must pass python -m py_compile.\n"
        "- Preserve the CLI intent of the candidate where possible.\n"
        "- Do not return a patch, diff, explanation, or markdown fence.\n"
        "- Prefer content_base64 if this control plane supports it.\n"
        "\n"
        "Failure stage:\n"
        f"{failure_context.get('stage')}\n"
        "\n"
        "Candidate path:\n"
        f"{ctx.candidate}\n"
        "\n"
        "Failure line:\n"
        f"{failure_context.get('line_number')}\n"
        "\n"
        "Source excerpt around failure:\n"
        "----- BEGIN FAILURE EXCERPT -----\n"
        f"{failure_context.get('source_excerpt') or ''}\n"
        "----- END FAILURE EXCERPT -----\n"
        "\n"
        "Failure stderr:\n"
        "----- BEGIN STDERR -----\n"
        f"{failure_context.get('stderr_preview') or ''}\n"
        "----- END STDERR -----\n"
        "\n"
        "Complete candidate source:\n"
        "----- BEGIN CANDIDATE SOURCE -----\n"
        f"{source}\n"
        "----- END CANDIDATE SOURCE -----\n"
    )


def write_ai_repair_handoff(ctx: CandidateContext, failure_context: dict[str, Any], *, base_url: str) -> dict[str, str]:
    prompt_path = ctx.run_dir / "functional_ai_repair_prompt.txt"
    context_path = ctx.run_dir / "functional_failure_context.json"
    command_path = ctx.run_dir / "run_ai_repair_from_functional_failure.ps1"

    prompt = build_ai_repair_handoff_prompt(ctx, failure_context)
    write_json(context_path, failure_context)
    prompt_path.write_text(prompt, encoding="utf-8")

    output_arg = str(ctx.output_dir)
    candidate_arg = str(ctx.candidate)
    base_url_arg = str(base_url or "http://127.0.0.1:8765")
    command = (
        "python main_computer/rag_new_patch_latest_output_compile_smoke.py `\n"
        f"  \"{output_arg}\" `\n"
        f"  --candidate \"{candidate_arg}\" `\n"
        f"  --base-url \"{base_url_arg}\"\n"
    )
    command_path.write_text(command, encoding="utf-8")

    append_raw(ctx.provider_raw, raw_header("functional failure context") + json.dumps(failure_context, indent=2, sort_keys=True) + "\n")
    append_raw(ctx.render_raw, raw_header("functional ai repair handoff") + f"prompt={prompt_path}\ncontext={context_path}\ncommand={command_path}\n")

    return {
        "functional_failure_context": str(context_path),
        "functional_ai_repair_prompt": str(prompt_path),
        "functional_ai_repair_command": str(command_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run a functional smoke test for a generated new_patch.py candidate from an explicit "
            "rag_new_patch recreation output directory."
        )
    )
    parser.add_argument(
        "output_dir",
        help="First rag_new_patch_recreation_tester output directory containing proposed_new_patch.py.",
    )
    parser.add_argument(
        "--candidate",
        default=None,
        help=(
            "Candidate file to test, relative to output_dir. Defaults to proposed_new_patch.py. "
            "Use this for ai_repaired_new_patch_candidate.py files inside the output dir."
        ),
    )
    parser.add_argument("--run-id", default=None, help="Optional child functional-smoke run id.")
    parser.add_argument("--docker-image", default=DEFAULT_DOCKER_IMAGE)
    parser.add_argument("--compile-timeout-s", type=float, default=120.0)
    parser.add_argument("--dry-run-timeout-s", type=float, default=180.0)
    parser.add_argument(
        "--repair-base-url",
        default="http://127.0.0.1:8765",
        help="Base URL to include in the generated AI-repair handoff command. The functional smoke does not call it.",
    )
    args = parser.parse_args()

    repo_root = repo_root_from_cwd()
    ctx = build_context(repo_root, args.output_dir, args.candidate, args.run_id)

    original_hash = sha256_file(ctx.candidate)
    append_raw(ctx.provider_raw, raw_header("candidate source") + ctx.candidate.read_text(encoding="utf-8", errors="replace"))
    append_raw(ctx.render_raw, raw_header("functional candidate smoke render"))

    render(ctx, "--- rag_new_patch candidate functional smoke ---")
    render(ctx, f"repo_root={ctx.repo_root}")
    render(ctx, f"input_output_dir={ctx.output_dir}")
    render(ctx, f"candidate={ctx.candidate}")
    render(ctx, f"run_dir={ctx.run_dir}")
    render(ctx, f"provider_raw={ctx.provider_raw}")
    render(ctx, f"render_raw={ctx.render_raw}")
    render(ctx, f"candidate_sha256_before={original_hash}")

    compile_result = docker_compile(ctx, args.docker_image, timeout_s=args.compile_timeout_s)
    write_json(ctx.run_dir / "compile_result.json", compile_result.as_dict())
    write_process_raw(ctx, "docker py_compile candidate", compile_result)
    print_process_result(ctx, "docker py_compile candidate", compile_result)

    status: dict[str, Any] = {
        "ok": False,
        "candidate": str(ctx.candidate),
        "candidate_sha256_before": original_hash,
        "candidate_sha256_after": "",
        "compile_ok": compile_result.returncode == 0,
        "dry_run_ok": False,
        "dry_run_diff_ok": False,
        "dry_run_left_fixture_unchanged": False,
        "test_passed_file": "",
        "run_dir": str(ctx.run_dir),
    }

    if compile_result.returncode == 0:
        dry_run_result = docker_dry_run_fixture(ctx, args.docker_image, timeout_s=args.dry_run_timeout_s)
        write_json(ctx.run_dir / "dry_run_result.json", dry_run_result.as_dict())
        write_process_raw(ctx, "docker candidate fixture dry-run", dry_run_result)
        print_process_result(ctx, "docker candidate fixture dry-run", dry_run_result)

        status["dry_run_ok"] = dry_run_result.returncode == 0
        status["dry_run_diff_ok"] = dry_run_output_has_diff(dry_run_result)
        status["dry_run_left_fixture_unchanged"] = fixture_target_unchanged(ctx)
        if not (
            status["dry_run_ok"]
            and status["dry_run_diff_ok"]
            and status["dry_run_left_fixture_unchanged"]
        ):
            dry_run_failure_context = process_failure_context(ctx=ctx, stage="dry_run", result=dry_run_result)
            dry_run_failure_context["dry_run_diff_ok"] = status["dry_run_diff_ok"]
            dry_run_failure_context["dry_run_left_fixture_unchanged"] = status["dry_run_left_fixture_unchanged"]
            status.update(write_ai_repair_handoff(ctx, dry_run_failure_context, base_url=args.repair_base_url))
    else:
        render(ctx, "--- dry-run fixture skipped ---")
        render(ctx, "Skipped because candidate did not compile.")
        failure_context = process_failure_context(ctx=ctx, stage="compile", result=compile_result)
        status.update(write_ai_repair_handoff(ctx, failure_context, base_url=args.repair_base_url))

    after_hash = sha256_file(ctx.candidate)
    status["candidate_sha256_after"] = after_hash
    if after_hash != original_hash:
        status["ok"] = False
        status["failure"] = "candidate file changed during smoke test"
        write_json(ctx.run_dir / "functional_smoke_status.json", status)
        render(ctx, "--- smoke result ---")
        render(ctx, "FAILED: candidate changed during smoke test.")
        return 1

    status["ok"] = bool(
        status["compile_ok"]
        and status["dry_run_ok"]
        and status["dry_run_diff_ok"]
        and status["dry_run_left_fixture_unchanged"]
    )

    if status["ok"]:
        pass_file = ctx.run_dir / PASS_FILE_NAME
        pass_file.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        status["test_passed_file"] = str(pass_file)
        write_json(ctx.run_dir / "functional_smoke_status.json", status)
        render(ctx, "--- smoke result ---")
        render(ctx, "PASSED: candidate compiles and handles a fixture --dry-run without mutating the target.")
        render(ctx, f"wrote={pass_file}")
        return 0

    write_json(ctx.run_dir / "functional_smoke_status.json", status)
    render(ctx, "--- smoke result ---")
    if not status["compile_ok"]:
        render(ctx, "FAILED: candidate does not compile in Docker.")
    elif not status["dry_run_ok"]:
        render(ctx, "FAILED: candidate dry-run command failed in Docker.")
    elif not status["dry_run_diff_ok"]:
        render(ctx, "FAILED: candidate dry-run did not emit the expected old/new diff evidence.")
    elif not status["dry_run_left_fixture_unchanged"]:
        render(ctx, "FAILED: candidate dry-run mutated the fixture target file.")
    else:
        render(ctx, "FAILED: unknown functional-smoke failure.")
    render(ctx, "No test_passed.txt was written.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
