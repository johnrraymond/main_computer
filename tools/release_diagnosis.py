#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_MAX_SOURCE_BYTES = 1_000_000
DEFAULT_COMMAND_TIMEOUT_SECONDS = 120
DEFAULT_REPORT_DIR = Path("release_reports")
REPORT_SCHEMA = "main-computer.release-candidate-report.v1"

RELEASE_CRITICAL_TESTS = (
    "tests/test_prod_command_script.py",
    "tests/test_prod_lock.py",
    "tests/test_dev_chain_reset_script.py",
    "tests/test_dev_chain_flow_script.py",
    "tests/test_dev_chain_ledger_bridge_script.py",
    "tests/test_export_main_computer_test.py",
    "tests/test_project_health_check.py",
    "tests/test_release_preflight.py",
    "tests/test_executor_preflight.py",
    "tests/test_dev_chain_wallet_smoke_guide_script.py",
    "tests/test_viewport_onlyoffice.py",
)

CLEAN_SOURCE_EXCLUSIONS = (
    ".prod.lock",
    "runtime/",
    "runtime/deployments/dev/latest.json",
    "energy_credits/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".git/",
    "release_reports/",
    ".tmp/",
    "aider.log/",
    "*.pid",
)

KNOWN_NOT_RELEASE_READY_EXCLUSIONS = (
    "real production deploy command is not implemented",
    "backup-before-destroy automation is not implemented",
    "migration workflow is not implemented",
    "unlock and destroy commands are intentionally absent",
    "final production RPC / chain identity policy is not finalized",
    "final operator confirmation policy is not finalized",
)

MANIFEST_EXCLUDED_DIR_NAMES = {
    ".git",
    ".main_computer_browser_profile",
    ".mypy_cache",
    ".tmp",
    "aider.log",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "debug_assets",
    "diagnostics_output",
    "energy_credits",
    "harness_output",
    "harness_output_game_editor",
    "harness_output_pretty_docs",
    "migration",
    "new_patch_runs",
    "release_reports",
    "revision_control",
    "runtime",
    "venv",
}

MANIFEST_EXCLUDED_FILE_SUFFIXES = {
    ".bak",
    ".pid",
    ".pyc",
    ".pyo",
    ".tmp",
}

MANIFEST_EXCLUDED_FILE_NAMES = {
    ".prod.lock",
    "aider.log",
}


def is_manifest_excluded_path(relative: Path) -> bool:
    """Return True for local/debug artifact paths that are not release evidence."""
    parts = relative.parts
    if parts[:2] == ("contracts", "cache"):
        return True
    if parts[:2] == ("tools", "patching"):
        return True
    return False


@dataclasses.dataclass(frozen=True)
class PreflightStep:
    name: str
    command: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class StepResult:
    name: str
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclasses.dataclass(frozen=True)
class PreflightReport:
    ok: bool
    repo_root: str
    elapsed_s: float
    steps: tuple[StepResult, ...]


def repo_root_from_args(value: str | Path) -> Path:
    return Path(value).resolve(strict=False)


def build_steps(
    repo_root: Path,
    *,
    python_executable: str = sys.executable,
    include_health: bool = True,
    include_pytest: bool = True,
    include_executor: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
) -> list[PreflightStep]:
    steps: list[PreflightStep] = []
    if include_health:
        steps.append(
            PreflightStep(
                name="source-health",
                command=(
                    python_executable,
                    "tools/project_diagnosis.py",
                    "--stage",
                    "simple",
                    "--max-source-bytes",
                    str(max_source_bytes),
                ),
            )
        )
    if include_pytest:
        steps.append(
            PreflightStep(
                name="release-critical-tests",
                command=(
                    python_executable,
                    "-m",
                    "pytest",
                    *RELEASE_CRITICAL_TESTS,
                    "-q",
                ),
            )
        )
    if include_executor:
        steps.append(
            PreflightStep(
                name="executor-preflight",
                command=(
                    python_executable,
                    "tools/executor_diagnosis.py",
                ),
            )
        )
    return steps


def run_step(step: PreflightStep, *, cwd: Path, timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS) -> StepResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            list(step.command),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return StepResult(
            name=step.name,
            command=step.command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_s=round(time.perf_counter() - started, 4),
        )
    except subprocess.TimeoutExpired as exc:
        return StepResult(
            name=step.name,
            command=step.command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + f"\nTimed out after {timeout} seconds.",
            elapsed_s=round(time.perf_counter() - started, 4),
        )
    except OSError as exc:
        return StepResult(
            name=step.name,
            command=step.command,
            returncode=127,
            stdout="",
            stderr=str(exc),
            elapsed_s=round(time.perf_counter() - started, 4),
        )


def run_preflight(
    repo_root: Path | str = Path("."),
    *,
    include_health: bool = True,
    include_pytest: bool = True,
    include_executor: bool = False,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    timeout: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
) -> PreflightReport:
    started = time.perf_counter()
    root = repo_root_from_args(repo_root)
    steps = build_steps(
        root,
        include_health=include_health,
        include_pytest=include_pytest,
        include_executor=include_executor,
        max_source_bytes=max_source_bytes,
    )
    results = tuple(run_step(step, cwd=root, timeout=timeout) for step in steps)
    ok = bool(results) and all(result.ok for result in results)
    return PreflightReport(
        ok=ok,
        repo_root=str(root),
        elapsed_s=round(time.perf_counter() - started, 4),
        steps=results,
    )


def command_to_text(command: tuple[str, ...]) -> str:
    return " ".join(command)


def portable_command_for_report(command: tuple[str, ...]) -> tuple[str, ...]:
    """Return a privacy-safe command representation for saved evidence reports."""
    if not command:
        return command

    executable = command[0]
    executable_name = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if executable == sys.executable or executable_name.startswith("python"):
        return ("python", *command[1:])
    return command


def step_result_to_evidence(step: StepResult) -> dict[str, Any]:
    return {
        "name": step.name,
        "command": list(portable_command_for_report(step.command)),
        "returncode": step.returncode,
        "elapsed_s": step.elapsed_s,
        "ok": step.ok,
    }


def preflight_report_to_evidence(report: PreflightReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "repo_root": ".",
        "elapsed_s": report.elapsed_s,
        "steps": [step_result_to_evidence(step) for step in report.steps],
    }


def report_to_json(report: PreflightReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "repo_root": report.repo_root,
        "elapsed_s": report.elapsed_s,
        "steps": [
            {
                "name": step.name,
                "command": list(step.command),
                "returncode": step.returncode,
                "stdout": step.stdout,
                "stderr": step.stderr,
                "elapsed_s": step.elapsed_s,
                "ok": step.ok,
            }
            for step in report.steps
        ],
    }


def should_include_in_source_manifest(path: Path, repo_root: Path) -> bool:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        return False
    if not path.is_file():
        return False
    if is_manifest_excluded_path(relative):
        return False
    if any(part in MANIFEST_EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
        return False
    if path.name in MANIFEST_EXCLUDED_FILE_NAMES:
        return False
    if path.suffix.lower() in MANIFEST_EXCLUDED_FILE_SUFFIXES:
        return False
    return True


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def build_source_manifest(repo_root: Path | str) -> dict[str, Any]:
    root = repo_root_from_args(repo_root)
    rows: list[dict[str, Any]] = []
    total_bytes = 0

    for current_dir, dir_names, file_names in os.walk(root):
        current_path = Path(current_dir)

        # Prune local/generated directories before descending into them.  This
        # keeps release evidence generation fast and avoids Windows reparse
        # points, virtualenvs, browser profiles, runtime state, and old reports.
        dir_names[:] = sorted(
            name
            for name in dir_names
            if name not in MANIFEST_EXCLUDED_DIR_NAMES
            and not is_manifest_excluded_path((current_path / name).relative_to(root))
        )

        for file_name in sorted(file_names):
            path = current_path / file_name
            if not should_include_in_source_manifest(path, root):
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            total_bytes += size
            rows.append(
                {
                    "path": relative,
                    "bytes": size,
                    "sha256": sha256_file(path),
                }
            )

    rows.sort(key=lambda item: item["path"])
    return {
        "file_count": len(rows),
        "total_bytes": total_bytes,
        "files": rows,
    }


def safe_platform_evidence() -> dict[str, str]:
    """Return platform evidence without invoking Windows WMI-backed helpers.

    On some Windows hosts platform.platform()/platform.uname() can block inside
    Python's WMI query path.  Release evidence only needs portable context, so
    use sys/os primitives that do not perform external instrumentation.
    """
    if sys.platform.startswith("win"):
        win_version = sys.getwindowsversion()
        release = f"{win_version.major}.{win_version.minor}.{win_version.build}"
        return {
            "platform": f"Windows-{release}",
            "system": "Windows",
            "release": release,
        }

    if hasattr(os, "uname"):
        uname = os.uname()
        return {
            "platform": f"{uname.sysname}-{uname.release}",
            "system": uname.sysname,
            "release": uname.release,
        }

    return {
        "platform": sys.platform,
        "system": os.name,
        "release": "",
    }


def build_release_evidence(
    repo_root: Path | str,
    report: PreflightReport,
    *,
    created_at: dt.datetime | None = None,
) -> dict[str, Any]:
    root = repo_root_from_args(repo_root)
    created = created_at or dt.datetime.now(dt.timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    created = created.astimezone(dt.timezone.utc)
    return {
        "schema": REPORT_SCHEMA,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "release_status": "candidate-preflight-passed" if report.ok else "candidate-preflight-failed",
        "repo": {
            "name": root.name,
            "root": ".",
        },
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
        },
        "platform": safe_platform_evidence(),
        "preflight": preflight_report_to_evidence(report),
        "known_not_release_ready_exclusions": list(KNOWN_NOT_RELEASE_READY_EXCLUSIONS),
        "source_manifest_scope": "clean-source-exclusions-applied",
        "source_manifest_policy": {
            "name": "clean-release-source",
            "description": "Hashes portable source files and skips local state, generated reports, debug captures, and virtual environments.",
        },
        "source_manifest": build_source_manifest(root),
    }


def unique_report_path(report_dir: Path, *, created_at: dt.datetime | None = None) -> Path:
    created = created_at or dt.datetime.now(dt.timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    created = created.astimezone(dt.timezone.utc)
    stem = "rc-" + created.strftime("%Y%m%d-%H%M%SZ")
    candidate = report_dir / f"{stem}.json"
    if not candidate.exists():
        return candidate
    for index in range(2, 1000):
        alternate = report_dir / f"{stem}-{index}.json"
        if not alternate.exists():
            return alternate
    raise RuntimeError(f"could not allocate a release report path in {report_dir}")


def write_release_evidence_report(
    repo_root: Path | str,
    report: PreflightReport,
    *,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
    created_at: dt.datetime | None = None,
) -> Path:
    root = repo_root_from_args(repo_root)
    output_dir = Path(report_dir)
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    evidence = build_release_evidence(root, report, created_at=created_at)
    output_path = unique_report_path(output_dir, created_at=created_at)
    output_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def print_text_report(report: PreflightReport, *, verbose: bool = False) -> None:
    outcome = "PASS" if report.ok else "FAIL"
    print(f"{outcome}: release preflight root={report.repo_root} elapsed={report.elapsed_s:.2f}s")
    for step in report.steps:
        label = "PASS" if step.ok else "FAIL"
        print(f"[{label}] {step.name}: exit={step.returncode} elapsed={step.elapsed_s:.2f}s")
        print(f"  command: {command_to_text(step.command)}")
        if step.ok and not verbose:
            continue
        if step.stdout.strip():
            print("  stdout:")
            for line in step.stdout.rstrip().splitlines():
                print(f"    {line}")
        if step.stderr.strip():
            print("  stderr:")
            for line in step.stderr.rstrip().splitlines():
                print(f"    {line}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the release-candidate preflight gate for the clean Main Computer source tree."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument("--max-source-bytes", type=int, default=DEFAULT_MAX_SOURCE_BYTES)
    parser.add_argument("--timeout", type=int, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    parser.add_argument("--skip-health", action="store_true", help="Skip the source health check.")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip the release-critical pytest slice.")
    parser.add_argument(
        "--include-executor",
        action="store_true",
        help="Run the read-only executor/Docker asset preflight after the focused pytest slice.",
    )
    parser.add_argument("--json", action="store_true", help="Write the preflight report as JSON.")
    parser.add_argument("--verbose", action="store_true", help="Show stdout/stderr for passing steps too.")
    parser.add_argument(
        "--write-report",
        action="store_true",
        help=(
            "Write an auditable release-candidate evidence report under release_reports/. "
            "This is the only mode that writes a generated file."
        ),
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help="Directory for --write-report output; relative paths are resolved from repo root.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    root = repo_root_from_args(args.repo_root)
    report = run_preflight(
        root,
        include_health=not args.skip_health,
        include_pytest=not args.skip_pytest,
        include_executor=args.include_executor,
        max_source_bytes=args.max_source_bytes,
        timeout=args.timeout,
    )

    report_path: Path | None = None
    if args.write_report:
        report_path = write_release_evidence_report(root, report, report_dir=args.report_dir)

    if args.json:
        payload = report_to_json(report)
        if report_path is not None:
            payload["release_report_path"] = str(report_path)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_text_report(report, verbose=args.verbose)
        if report_path is not None:
            print(f"Release report: {report_path}")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
