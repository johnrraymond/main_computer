#!/usr/bin/env python3
"""
Regression stack smoke for generated-editor terminal-result semantics.

This deterministic, no-model umbrella smoke keeps the terminal-result and
patch-artifact checks together so future changes cannot accidentally run only a
happy path while skipping hostile cases or the project adapter dry-run proof.

The chain includes:

* terminal result contract
* terminal artifact contract
* generated-editor hostile terminal-result cases
* generated-editor result-mode semantics
* generated-editor patch artifact new_patch.py dry-run proof
* generated-editor patch artifact adapter hostile cases
* new_patch.py raw snapshot zip hostile path-safety checks
* WSL-scoped website Git command planning
* Git-aware patch zip lifecycle checkpoint planning
* typed mutator chain semantic leak checks

Child smokes run in-process by default to avoid repeated Python startup cost on
Windows.  Their stdout/stderr are still captured under debug_assets, and the
dry-run smoke still invokes new_patch.py as a real subprocess internally.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import runpy
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_generated_editor_terminal_stack_smoke"

STACK_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "terminal_result_contract",
        ("main_computer/rag_terminal_result_contract_smoke.py",),
    ),
    (
        "terminal_artifact_contract",
        ("main_computer/rag_terminal_artifact_contract_smoke.py",),
    ),
    (
        "generated_editor_terminal_result_hostile",
        ("main_computer/rag_generated_editor_terminal_result_hostile_smoke.py",),
    ),
    (
        "generated_editor_patch_artifact_mode",
        ("main_computer/rag_generated_editor_patch_artifact_mode_smoke.py",),
    ),
    (
        "generated_editor_patch_artifact_dry_run",
        ("main_computer/rag_generated_editor_patch_artifact_dry_run_smoke.py",),
    ),
    (
        "generated_editor_patch_artifact_adapter_hostile",
        ("main_computer/rag_generated_editor_patch_artifact_adapter_hostile_smoke.py",),
    ),
    (
        "new_patch_raw_snapshot_hostile",
        ("main_computer/rag_new_patch_raw_snapshot_hostile_smoke.py",),
    ),
    (
        "wsl_website_git_command_scope",
        ("main_computer/rag_wsl_website_git_command_scope_smoke.py",),
    ),
    (
        "git_aware_patch_zip_lifecycle",
        ("main_computer/rag_git_aware_patch_zip_lifecycle_smoke.py",),
    ),
    (
        "typed_mutator_chain",
        ("main_computer/rag_typed_mutator_chain_smoke.py",),
    ),
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def output_root(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / "debug_assets" / MODE / stamp
    path.mkdir(parents=True, exist_ok=True)
    return path


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def parse_first_json_object(stdout: str) -> dict[str, Any] | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def text_tail(text: str, max_chars: int = 1600) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)


def system_exit_code(exc: SystemExit) -> int:
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    return 1


def run_child_in_process(repo: Path, command: tuple[str, ...]) -> dict[str, Any]:
    script = repo / command[0]
    if not script.exists():
        return {
            "returncode": 1,
            "stdout": "",
            "stderr": f"missing child smoke: {command[0]}\n",
        }

    old_argv = sys.argv[:]
    old_cwd = Path.cwd()
    stdout = io.StringIO()
    stderr = io.StringIO()
    returncode = 0

    try:
        os.chdir(repo)
        sys.argv = [str(script), *command[1:]]
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(str(script), run_name="__main__")
            except SystemExit as exc:
                returncode = system_exit_code(exc)
            except BaseException:
                returncode = 1
                traceback.print_exc(file=stderr)
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)

    return {
        "returncode": returncode,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
    }


def run_child_subprocess(repo: Path, command: tuple[str, ...], timeout_seconds: float) -> dict[str, Any]:
    args = [sys.executable, *command]
    proc = subprocess.run(
        args,
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=command_env(),
        timeout=timeout_seconds,
    )
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def run_child(
    *,
    repo: Path,
    out_dir: Path,
    name: str,
    command: tuple[str, ...],
    timeout_seconds: float,
    use_subprocess: bool,
) -> dict[str, Any]:
    if use_subprocess:
        result = run_child_subprocess(repo, command, timeout_seconds)
    else:
        result = run_child_in_process(repo, command)

    stdout = result["stdout"]
    stderr = result["stderr"]
    returncode = int(result["returncode"])

    stem = safe_name(name)
    stdout_path = out_dir / f"{stem}.stdout.txt"
    stderr_path = out_dir / f"{stem}.stderr.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    parsed = parse_first_json_object(stdout)
    parsed_ok = parsed.get("ok") if isinstance(parsed, dict) else None
    ok = returncode == 0 and parsed_ok is True

    return {
        "name": name,
        "ok": ok,
        "returncode": returncode,
        "parsed_report_ok": parsed_ok,
        "mode": parsed.get("mode") if isinstance(parsed, dict) else None,
        "case_count": parsed.get("case_count") if isinstance(parsed, dict) else None,
        "passed_case_count": parsed.get("passed_case_count") if isinstance(parsed, dict) else None,
        "failed_case_count": parsed.get("failed_case_count") if isinstance(parsed, dict) else None,
        "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stderr_tail": text_tail(stderr),
        "command": [sys.executable, *command] if use_subprocess else [*command],
        "execution": "subprocess" if use_subprocess else "in_process",
    }


def run_stack(
    *,
    repo: Path,
    out_dir: Path,
    timeout_seconds: float,
    stop_on_failure: bool,
    use_subprocess: bool,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for name, command in STACK_COMMANDS:
        try:
            case = run_child(
                repo=repo,
                out_dir=out_dir,
                name=name,
                command=command,
                timeout_seconds=timeout_seconds,
                use_subprocess=use_subprocess,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            case = {
                "name": name,
                "ok": False,
                "returncode": None,
                "parsed_report_ok": None,
                "mode": None,
                "case_count": None,
                "passed_case_count": None,
                "failed_case_count": None,
                "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
                "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
                "stdout_path": None,
                "stderr_path": None,
                "stderr_tail": text_tail(stderr),
                "command": [sys.executable, *command],
                "execution": "subprocess",
                "timed_out": True,
                "timeout_seconds": timeout_seconds,
            }
        cases.append(case)
        if stop_on_failure and not case["ok"]:
            break

    return {
        "mode": MODE,
        "ok": all(case["ok"] for case in cases) and len(cases) == len(STACK_COMMANDS),
        "repo_root": str(repo),
        "case_count": len(cases),
        "passed_case_count": sum(1 for case in cases if case["ok"]),
        "failed_case_count": sum(1 for case in cases if not case["ok"]),
        "expected_case_count": len(STACK_COMMANDS),
        "execution": "subprocess" if use_subprocess else "in_process",
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--subprocess",
        action="store_true",
        help="Run child smokes as separate Python processes instead of in-process.",
    )
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    out_dir = Path(args.output_root).resolve() if args.output_root else output_root(root)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_stack(
        repo=root,
        out_dir=out_dir,
        timeout_seconds=args.timeout_seconds,
        stop_on_failure=args.stop_on_failure,
        use_subprocess=args.subprocess,
    )

    report_path = out_dir / "final_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
