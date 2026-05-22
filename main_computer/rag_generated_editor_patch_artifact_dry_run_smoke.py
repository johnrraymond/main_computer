#!/usr/bin/env python3
"""
Adapter-level smoke for generated-editor patch artifacts.

This smoke is intentionally one step beyond the terminal-result contract checks:
it proves that an opt-in generated-editor patch_artifact result produces a
snapshot zip that the project adapter, new_patch.py, can actually dry-run.

It does not call a model.  It drives the generated-editor offline self-check,
extracts the materialized artifact path from the JSON report, and then runs:

    python new_patch.py <generated_editor_snapshot_patch.zip> --dry-run

The dry-run must succeed, report one changed file, write an actual.patch that
targets the expected repository-relative file, and leave the real target file
unchanged.

The child processes are forced to UTF-8 stdio.  On Windows, patch/diff output can
otherwise fail or become misleading when a subprocess inherits a legacy console
encoding.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

MODE = "rag_generated_editor_patch_artifact_dry_run_smoke"
DISCOVERY_SMOKE = Path("main_computer/rag_generated_editor_discovery_grounding_smoke.py")
TARGET_FILE = Path("main_computer/web/applications/scripts/chat-console.js")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_first_json_object(stdout: str) -> dict[str, Any] | None:
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def run_command(repo: Path, args: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        args,
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        env=command_env(),
    )
    return {
        "args": args,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "stdout_bytes": len(proc.stdout.encode("utf-8", errors="replace")),
        "stderr_bytes": len(proc.stderr.encode("utf-8", errors="replace")),
    }


def stdout_has_line(stdout: str, expected: str) -> bool:
    return any(line.strip() == expected for line in stdout.splitlines())


def extract_summary_path(stdout: str, label: str) -> Path | None:
    prefix = f"{label}:"
    for line in stdout.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix) :].strip().strip('"')
            if value:
                return Path(value)
    return None


def tail_text(text: str, max_chars: int = 1600) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def validate_generated_artifact_report(report: dict[str, Any], repo: Path) -> dict[str, bool]:
    artifact_packaging = report.get("artifact_packaging")
    terminal_result = report.get("terminal_result")
    artifact_report = terminal_result.get("artifact_report") if isinstance(terminal_result, dict) else None
    artifact_contract = artifact_report.get("artifact_contract") if isinstance(artifact_report, dict) else None

    artifact_path = None
    if isinstance(artifact_packaging, dict) and isinstance(artifact_packaging.get("artifact_path"), str):
        artifact_path = Path(artifact_packaging["artifact_path"])

    target_file = None
    if isinstance(artifact_packaging, dict) and isinstance(artifact_packaging.get("target_file"), str):
        target_file = Path(artifact_packaging["target_file"])

    return {
        "ok_field": report.get("ok") is True,
        "declared_result_mode": report.get("declared_result_mode") == "patch_artifact",
        "result_mode": report.get("result_mode") == "patch_artifact",
        "terminal_state": report.get("terminal_state") == "accepted_terminal_result",
        "result_contract_passed": report.get("result_contract_passed") is True,
        "artifact_ready": report.get("artifact_ready") is True,
        "promotable": report.get("promotable") is True,
        "artifact_packaging_ok": isinstance(artifact_packaging, dict) and artifact_packaging.get("ok") is True,
        "artifact_contract_passed": isinstance(artifact_report, dict)
        and artifact_report.get("artifact_contract_passed") is True,
        "artifact_report_promotable": isinstance(artifact_report, dict) and artifact_report.get("promotable") is True,
        "new_patch_usable": isinstance(artifact_contract, dict) and artifact_contract.get("new_patch_usable") is True,
        "root_contract_valid": isinstance(artifact_contract, dict) and artifact_contract.get("root_contract_valid") is True,
        "replacement_files_exist": isinstance(artifact_contract, dict)
        and artifact_contract.get("replacement_files_exist") is True,
        "repo_relative_paths_safe": isinstance(artifact_contract, dict)
        and artifact_contract.get("repo_relative_paths_safe") is True,
        "artifact_path_exists": artifact_path is not None and artifact_path.exists() and artifact_path.is_file(),
        "target_file_expected": target_file == TARGET_FILE,
        "target_file_exists": (repo / TARGET_FILE).exists(),
    }


def main() -> int:
    repo = repo_root()
    report_dir = repo / "debug_assets" / MODE / datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)

    target_path = repo / TARGET_FILE
    target_hash_before = sha256_file(target_path)

    generated = run_command(
        repo,
        [
            sys.executable,
            str(DISCOVERY_SMOKE),
            "--offline-self-check",
            "--result-mode",
            "patch_artifact",
            "--require-promotable",
        ],
    )
    generated_report = parse_first_json_object(generated["stdout"])
    generated_checks: dict[str, bool] = {}
    artifact_path: Path | None = None

    if generated_report is not None:
        generated_checks = validate_generated_artifact_report(generated_report, repo)
        artifact_packaging = generated_report.get("artifact_packaging")
        if isinstance(artifact_packaging, dict) and isinstance(artifact_packaging.get("artifact_path"), str):
            artifact_path = Path(artifact_packaging["artifact_path"])

    dry_run: dict[str, Any] | None = None
    dry_run_stdout_path: Path | None = None
    dry_run_stderr_path: Path | None = None
    actual_patch_path: Path | None = None
    actual_patch_text = ""
    dry_run_checks: dict[str, bool] = {
        "not_skipped": False,
        "returncode": False,
        "verification_no_reference": False,
        "changed_files_one": False,
        "actual_patch_written": False,
        "target_file_in_actual_patch": False,
        "dry_run_only": False,
        "target_hash_unchanged": False,
    }

    if artifact_path is not None:
        dry_run = run_command(
            repo,
            [
                sys.executable,
                "new_patch.py",
                str(artifact_path),
                "--target-root",
                str(repo),
                "--dry-run",
            ],
        )
        dry_run_stdout_path = report_dir / "new_patch_dry_run_stdout.txt"
        dry_run_stderr_path = report_dir / "new_patch_dry_run_stderr.txt"
        dry_run_stdout_path.write_text(dry_run["stdout"], encoding="utf-8")
        dry_run_stderr_path.write_text(dry_run["stderr"], encoding="utf-8")

        actual_patch_path = extract_summary_path(dry_run["stdout"], "actual.patch")
        if actual_patch_path is not None and actual_patch_path.exists():
            actual_patch_text = actual_patch_path.read_text(encoding="utf-8", errors="replace")

        target_hash_after = sha256_file(target_path)
        dry_run_checks = {
            "not_skipped": True,
            "returncode": dry_run["returncode"] == 0,
            "verification_no_reference": stdout_has_line(dry_run["stdout"], "verification: no_reference"),
            "changed_files_one": stdout_has_line(dry_run["stdout"], "changed_files: 1"),
            "actual_patch_written": actual_patch_path is not None and actual_patch_path.exists(),
            "target_file_in_actual_patch": TARGET_FILE.as_posix() in actual_patch_text,
            "dry_run_only": "dry-run only; no files were copied." in dry_run["stdout"],
            "target_hash_unchanged": target_hash_before is not None and target_hash_before == target_hash_after,
        }

    case_ok = (
        generated["returncode"] == 0
        and generated_report is not None
        and all(generated_checks.values())
        and all(dry_run_checks.values())
    )

    case = {
        "name": "generated_patch_artifact_survives_new_patch_dry_run",
        "ok": case_ok,
        "generated_exit_code": generated["returncode"],
        "generated_stdout_bytes": generated["stdout_bytes"],
        "generated_stderr_bytes": generated["stderr_bytes"],
        "generated_checks": generated_checks,
        "dry_run_exit_code": dry_run["returncode"] if dry_run else None,
        "dry_run_stdout_bytes": dry_run["stdout_bytes"] if dry_run else 0,
        "dry_run_stderr_bytes": dry_run["stderr_bytes"] if dry_run else 0,
        "dry_run_checks": dry_run_checks,
        "dry_run_stdout_path": str(dry_run_stdout_path) if dry_run_stdout_path else None,
        "dry_run_stderr_path": str(dry_run_stderr_path) if dry_run_stderr_path else None,
        "dry_run_stderr_tail": tail_text(dry_run["stderr"]) if dry_run else "",
        "actual_patch_path": str(actual_patch_path) if actual_patch_path else None,
        "artifact_path": str(artifact_path) if artifact_path else None,
        "target_file": TARGET_FILE.as_posix(),
        "target_hash_before": target_hash_before,
        "target_hash_after": sha256_file(target_path),
    }

    report = {
        "mode": MODE,
        "ok": case_ok,
        "case_count": 1,
        "passed_case_count": 1 if case_ok else 0,
        "failed_case_count": 0 if case_ok else 1,
        "repo_root": str(repo),
        "report_dir": str(report_dir),
        "cases": [case],
    }

    (report_dir / "generated_stdout.txt").write_text(generated["stdout"], encoding="utf-8")
    (report_dir / "generated_stderr.txt").write_text(generated["stderr"], encoding="utf-8")
    if generated_report is not None:
        (report_dir / "generated_report.json").write_text(
            json.dumps(generated_report, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    final_report = report_dir / "final_report.json"
    final_report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {final_report}")

    return 0 if case_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
