#!/usr/bin/env python3
"""
Hostile smoke: malformed raw snapshot zips must fail before dry-run approval.

This deterministic, no-model smoke exercises the actual new_patch.py zip intake
boundary.  It builds raw snapshot zip files with unsafe member names and proves
that `python new_patch.py <zip> --dry-run` rejects them instead of silently
normalizing them into apparent repository-relative changes.

This intentionally does not treat a different single top-level snapshot folder
name as hostile by itself: raw snapshot mode strips one top-level directory by
design.  The invariant tested here is path safety after zip member normalization.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


MODE = "rag_new_patch_raw_snapshot_hostile_smoke"


@dataclass(frozen=True)
class HostileZipCase:
    name: str
    entries: tuple[tuple[str, str], ...]
    expected_error_fragment: str


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


def text_tail(text: str, max_chars: int = 1600) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def write_zip(zip_path: Path, entries: tuple[tuple[str, str], ...]) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member_name, payload in entries:
            archive.writestr(member_name, payload)


def run_new_patch_dry_run(repo: Path, zip_path: Path) -> dict[str, Any]:
    args = [sys.executable, "-S", "new_patch.py", str(zip_path), "--dry-run"]
    try:
        proc = subprocess.run(
            args,
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=command_env(),
            timeout=20.0,
        )
        return {
            "args": args,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "stdout_tail": text_tail(proc.stdout),
            "stderr_tail": text_tail(proc.stderr),
            "stdout_bytes": len(proc.stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(proc.stderr.encode("utf-8", errors="replace")),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return {
            "args": args,
            "returncode": None,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_tail": text_tail(stdout),
            "stderr_tail": text_tail(stderr),
            "stdout_bytes": len(stdout.encode("utf-8", errors="replace")),
            "stderr_bytes": len(stderr.encode("utf-8", errors="replace")),
            "timed_out": True,
        }


def hostile_cases() -> list[HostileZipCase]:
    return [
        HostileZipCase(
            name="leading_parent_traversal_is_rejected",
            entries=(("../outside.py", "hostile traversal\n"),),
            expected_error_fragment="Unsafe zip member path contains parent traversal",
        ),
        HostileZipCase(
            name="snapshot_root_parent_traversal_is_rejected",
            entries=(("main_computer_test/../outside.py", "hostile traversal\n"),),
            expected_error_fragment="Unsafe zip member path contains parent traversal",
        ),
        HostileZipCase(
            name="windows_backslash_parent_traversal_is_rejected",
            entries=(("main_computer_test\\..\\outside.py", "hostile traversal\n"),),
            expected_error_fragment="Unsafe zip member path contains parent traversal",
        ),
        HostileZipCase(
            name="posix_absolute_path_is_rejected",
            entries=(("/outside.py", "hostile absolute path\n"),),
            expected_error_fragment="Unsafe zip member path is absolute",
        ),
        HostileZipCase(
            name="windows_drive_path_is_rejected",
            entries=(("C:\\outside.py", "hostile windows drive path\n"),),
            expected_error_fragment="Unsafe zip member path contains a Windows drive designator",
        ),
        HostileZipCase(
            name="windows_drive_relative_path_is_rejected",
            entries=(("C:outside.py", "hostile windows drive-relative path\n"),),
            expected_error_fragment="Unsafe zip member path contains a Windows drive designator",
        ),
    ]


def evaluate_case(repo: Path, out_dir: Path, case: HostileZipCase) -> dict[str, Any]:
    case_dir = out_dir / case.name
    case_dir.mkdir(parents=True, exist_ok=True)
    zip_path = case_dir / f"{case.name}.zip"
    write_zip(zip_path, case.entries)

    result = run_new_patch_dry_run(repo, zip_path)
    stderr = str(result["stderr"])
    stdout = str(result["stdout"])

    checks = {
        "dry_run_rejected": result["returncode"] != 0,
        "expected_error_reported": case.expected_error_fragment in stderr,
        "did_not_report_dry_run_success": "dry-run only; no files were copied." not in stdout,
        "did_not_report_changed_files_summary": "changed_files:" not in stdout,
    }

    return {
        "name": case.name,
        "ok": all(checks.values()),
        "zip_path": str(zip_path),
        "entries": [member_name for member_name, _payload in case.entries],
        "expected_error_fragment": case.expected_error_fragment,
        "checks": checks,
        "returncode": result["returncode"],
        "stdout_bytes": result["stdout_bytes"],
        "stderr_bytes": result["stderr_bytes"],
        "stdout_tail": result["stdout_tail"],
        "stderr_tail": result["stderr_tail"],
    }


def main() -> int:
    repo = repo_root()
    out_dir = output_root(repo)

    cases = [evaluate_case(repo, out_dir, case) for case in hostile_cases()]
    report = {
        "mode": MODE,
        "ok": all(case["ok"] for case in cases),
        "repo_root": str(repo),
        "case_count": len(cases),
        "passed_case_count": sum(1 for case in cases if case["ok"]),
        "failed_case_count": sum(1 for case in cases if not case["ok"]),
        "cases": cases,
    }

    report_path = out_dir / "final_report.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"\nWrote report: {report_path}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
