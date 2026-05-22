#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable


SOURCE_ROOTS = ("main_computer", "tests", "tools")
SOURCE_EXTENSIONS = {
    ".py",
    ".ps1",
    ".md",
    ".toml",
    ".json",
    ".yml",
    ".yaml",
    ".txt",
    ".html",
    ".css",
    ".js",
}
HARD_POLLUTION_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
HARD_POLLUTION_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}
GENERATED_PATH_PARTS = {
    ".pytest_cache",
    "debug_assets",
    "diagnostics_output",
    "generated_component_docs",
    "harness_output",
    "new_patch_runs",
    "release_reports",
    "runtime",
}


def is_generated_or_runtime_path(path: Path, repo_root: Path) -> bool:
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        parts = path.parts
    if any(part in GENERATED_PATH_PARTS for part in parts):
        return True
    if "tools" in parts and "patching" in parts and "reports" in parts:
        return True
    return False


@dataclasses.dataclass
class HealthCheck:
    name: str
    status: str
    summary: str
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class HealthReport:
    ok: bool
    stage: str
    repo_root: str
    elapsed_s: float
    checks: list[HealthCheck]


def relpath(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def iter_source_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for source_root in SOURCE_ROOTS:
        base = repo_root / source_root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            if any(part in HARD_POLLUTION_DIRS for part in path.relative_to(repo_root).parts):
                continue
            if is_generated_or_runtime_path(path, repo_root):
                continue
            files.append(path)

    for path in repo_root.iterdir() if repo_root.exists() else []:
        if path.is_file() and path.suffix.lower() in {".py", ".ps1", ".md", ".toml", ".txt", ".yml", ".yaml"}:
            files.append(path)

    return sorted(set(files), key=lambda item: relpath(repo_root, item))


def check_source_file_sizes(repo_root: Path, *, max_source_bytes: int = 250_000) -> HealthCheck:
    rows = [
        {"path": relpath(repo_root, path), "bytes": path.stat().st_size}
        for path in iter_source_files(repo_root)
    ]
    rows.sort(key=lambda item: item["path"])
    largest = sorted(rows, key=lambda item: item["bytes"], reverse=True)[:20]
    oversized = [row for row in rows if row["bytes"] > max_source_bytes]
    status = "pass" if not oversized else "fail"
    if oversized:
        summary = f"{len(rows)} source files checked; {len(oversized)} oversized above {max_source_bytes} B."
    else:
        summary = f"{len(rows)} source files checked; none oversized above {max_source_bytes} B."
    return HealthCheck(
        name="source-file-sizes",
        status=status,
        summary=summary,
        detail={"files": rows, "largest_files": largest, "oversized": oversized},
    )


def check_python_syntax(repo_root: Path) -> HealthCheck:
    failures: list[dict[str, Any]] = []
    checked = 0
    for path in iter_source_files(repo_root):
        if path.suffix.lower() != ".py":
            continue
        checked += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            failures.append(
                {
                    "path": relpath(repo_root, path),
                    "line": exc.lineno,
                    "offset": exc.offset,
                    "message": exc.msg,
                }
            )
    status = "pass" if not failures else "fail"
    summary = f"{checked} Python files parsed without syntax errors." if not failures else f"{len(failures)} Python syntax failure(s)."
    return HealthCheck(
        name="python-syntax",
        status=status,
        summary=summary,
        detail={"failures": failures, "checked": checked},
    )


def check_source_tree_pollution(repo_root: Path) -> HealthCheck:
    hard: list[dict[str, str]] = []
    roots = [repo_root / item for item in SOURCE_ROOTS if (repo_root / item).exists()]
    roots.append(repo_root)

    seen: set[Path] = set()
    for base in roots:
        for path in base.rglob("*") if base.exists() else []:
            if path in seen:
                continue
            seen.add(path)
            if is_generated_or_runtime_path(path, repo_root):
                continue
            if path.is_dir() and path.name in HARD_POLLUTION_DIRS:
                hard.append({"path": relpath(repo_root, path), "kind": "directory"})
            elif path.is_file() and path.suffix.lower() in HARD_POLLUTION_SUFFIXES:
                hard.append({"path": relpath(repo_root, path), "kind": "file"})

    hard.sort(key=lambda item: item["path"])
    status = "pass" if not hard else "fail"
    summary = "no hard source-tree pollution found." if not hard else f"found {len(hard)} hard pollution item(s)."
    return HealthCheck(
        name="source-tree-pollution",
        status=status,
        summary=summary,
        detail={"hard": hard},
    )


def check_required_release_files(repo_root: Path) -> HealthCheck:
    required = [
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "prod-command.py",
        "main_computer/prod_lock.py",
        "tools/project_diagnosis.py",
        "tools/release_diagnosis.py",
        "tools/ollama_prompt_space_tester.py",
        "new_patch.py",
        "export-main-computer-test.ps1",
    ]
    missing = [path for path in required if not (repo_root / path).exists()]
    status = "pass" if not missing else "fail"
    summary = "required release/source files are present." if not missing else f"missing required file(s): {', '.join(missing)}"
    return HealthCheck(
        name="required-release-files",
        status=status,
        summary=summary,
        detail={"missing": missing, "required": required},
    )


def check_git_status(repo_root: Path, *, strict_git: bool = False) -> HealthCheck:
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return HealthCheck("git-status", "skip", f"git status skipped: {exc}", {})

    if probe.returncode != 0 or probe.stdout.strip().lower() != "true":
        return HealthCheck("git-status", "skip", "not inside a git work tree; git status skipped.", {})

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    entries = [line for line in status.stdout.splitlines() if line.strip()]
    if status.returncode != 0:
        check_status = "fail" if strict_git else "warn"
        return HealthCheck("git-status", check_status, "git status failed.", {"stderr": status.stderr})
    if entries and strict_git:
        return HealthCheck("git-status", "fail", f"git work tree has {len(entries)} pending change(s).", {"entries": entries})
    if entries:
        return HealthCheck("git-status", "warn", f"git work tree has {len(entries)} pending change(s).", {"entries": entries})
    return HealthCheck("git-status", "pass", "git work tree is clean.", {"entries": []})


def run_health(repo_root: Path | str = Path("."), *, stage: str = "all", max_source_bytes: int = 250_000, strict_git: bool = False) -> HealthReport:
    started = time.perf_counter()
    root = Path(repo_root).resolve(strict=False)
    checks: list[HealthCheck] = []

    if stage not in {"simple", "cleanliness", "all"}:
        raise ValueError("stage must be one of: simple, cleanliness, all")

    if stage in {"simple", "all"}:
        if stage == "all":
            checks.append(check_required_release_files(root))
        checks.extend(
            [
                check_source_file_sizes(root, max_source_bytes=max_source_bytes),
                check_python_syntax(root),
            ]
        )

    if stage in {"cleanliness", "all"}:
        checks.append(check_source_tree_pollution(root))

    if stage == "all":
        checks.append(check_git_status(root, strict_git=strict_git))

    hard_failure_statuses = {"fail"}
    ok = not any(check.status in hard_failure_statuses for check in checks)
    return HealthReport(
        ok=ok,
        stage=stage,
        repo_root=str(root),
        elapsed_s=round(time.perf_counter() - started, 4),
        checks=checks,
    )


def check_to_json(check: HealthCheck) -> dict[str, Any]:
    return dataclasses.asdict(check)


def report_to_json(report: HealthReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "stage": report.stage,
        "repo_root": report.repo_root,
        "elapsed_s": report.elapsed_s,
        "checks": [check_to_json(check) for check in report.checks],
    }


def print_text_report(report: HealthReport, *, verbose: bool = False) -> None:
    outcome = "PASS" if report.ok else "FAIL"
    print(f"{outcome}: project health stage={report.stage} root={report.repo_root} elapsed={report.elapsed_s:.2f}s")
    for check in report.checks:
        label = check.status.upper()
        print(f"[{label}] {check.name}: {check.summary}")

        if not verbose:
            continue

        if check.name == "source-file-sizes":
            largest = check.detail.get("largest_files") or []
            oversized = check.detail.get("oversized") or []
            if largest:
                print("  largest source files:")
                for item in largest[:10]:
                    print(f"    {item['path']} ({item['bytes']} B)")
            if oversized:
                print("  oversized source files:")
                for item in oversized:
                    print(f"    {item['path']} ({item['bytes']} B)")
        elif check.name == "source-tree-pollution":
            hard = check.detail.get("hard") or []
            if hard:
                print("  hard pollution:")
                for item in hard:
                    print(f"    {item['path']} ({item['kind']})")
        elif check.detail:
            print(json.dumps(check.detail, indent=2, sort_keys=True))

    if not verbose and any(check.detail for check in report.checks):
        print("Run again with --verbose for detailed paths and diagnostics.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run release-oriented Main Computer source health checks.")
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument("--stage", choices=["simple", "cleanliness", "all"], default="all")
    parser.add_argument("--max-source-bytes", type=int, default=250_000)
    parser.add_argument("--strict-git", action="store_true", help="Fail when git status has pending changes.")
    parser.add_argument("--json", action="store_true", help="Write the health report as JSON.")
    parser.add_argument("--verbose", action="store_true", help="Include detailed diagnostic paths in text output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_health(
        args.repo_root,
        stage=args.stage,
        max_source_bytes=args.max_source_bytes,
        strict_git=args.strict_git,
    )
    if args.json:
        print(json.dumps(report_to_json(report), indent=2, sort_keys=True))
    else:
        print_text_report(report, verbose=args.verbose)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
