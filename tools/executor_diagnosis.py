#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_DOCKER_TIMEOUT_SECONDS = 15


@dataclasses.dataclass(frozen=True)
class RequiredFile:
    path: str
    purpose: str
    markers: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    message: str
    path: str | None = None
    skipped: bool = False


@dataclasses.dataclass(frozen=True)
class ExecutorPreflightReport:
    ok: bool
    repo_root: str
    require_docker: bool
    elapsed_s: float
    checks: tuple[CheckResult, ...]


REQUIRED_FILES: tuple[RequiredFile, ...] = (
    RequiredFile(
        "docker-compose.executor.yml",
        "executor compose file",
        ("executor-image:", "docker/executor/Dockerfile", "main-computer-executor"),
    ),
    RequiredFile(
        "Dockerfile.executor",
        "legacy/full-browser executor Dockerfile",
        ("FROM python:", "playwright", "WORKDIR /workspace"),
    ),
    RequiredFile(
        "Dockerfile.full_executor",
        "full local executor Dockerfile",
        ("main-computer-full-executor", "PLAYWRIGHT_BROWSERS_PATH", "main-computer-fast-check"),
    ),
    RequiredFile(
        "start-main-computer-docker-windows.ps1",
        "Windows Docker launcher",
        ("docker", "HostPort", "Wait-ForMountedWindowsPathMode"),
    ),
    RequiredFile(
        "diagnosis-docker-windows-host-paths-v5.ps1",
        "Windows host-path Docker diagnosis script",
        ("Docker-on-Windows", "main-computer-stage2a-smoke", "Invoke-JsonGet"),
    ),
    RequiredFile(
        "docker/executor/Dockerfile",
        "isolated executor container Dockerfile",
        ("main-computer-exec", "/inputs", "/outputs", "/workspace"),
    ),
    RequiredFile(
        "docker/executor/main-computer-exec",
        "shared executor runtime entrypoint",
        ("main-computer-exec 1", "--cwd", "--timeout-ms", "--artifact-dir"),
    ),
    RequiredFile(
        "main_computer/executor_backend.py",
        "shared executor backend protocol",
        ("ExecutorBackend", "save_upload", "run("),
    ),
    RequiredFile(
        "main_computer/executor_models.py",
        "shared executor request/result models",
        ("ExecutorRequest", "ExecutorResult", "build_executor_runtime_command"),
    ),
    RequiredFile(
        "main_computer/docker_executor.py",
        "Docker executor backend",
        ("DockerExecutor", "docker", "build_executor_runtime_command"),
    ),
    RequiredFile(
        "main_computer/wsl_executor.py",
        "WSL executor backend",
        ("WslExecutor", "wsl", "build_executor_runtime_command"),
    ),
    RequiredFile(
        "main_computer/executor_tool_loop.py",
        "executor AI tool-loop boundary",
        ("ExecutorToolLoopConfig", "execute_shell", "ExecutorBackend"),
    ),
    RequiredFile(
        "main_computer/viewport_routes_executor.py",
        "executor viewport/API routes",
        ("ViewportExecutorRoutesMixin", "_handle_executor_status", "ExecutorRequest"),
    ),
    RequiredFile(
        "tests/test_docker_executor.py",
        "Docker executor tests",
        ("DockerExecutor", "main-computer-exec", "test_docker_executor"),
    ),
    RequiredFile(
        "tests/test_executor_backend.py",
        "shared executor backend tests",
        ("create_executor_backend", "ExecutorRequest", "test_"),
    ),
    RequiredFile(
        "tests/test_dev_docker_assets.py",
        "Docker asset coverage tests",
        ("docker/executor/Dockerfile", "main-computer-exec", "docker-compose.dev.yml"),
    ),
)


def repo_root_from_args(value: str | Path) -> Path:
    return Path(value).resolve(strict=False)


def is_safe_repo_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    if not normalized or normalized.startswith("/"):
        return False
    path = PurePosixPath(normalized)
    if path.is_absolute():
        return False
    return ".." not in path.parts


def required_file_checks(repo_root: Path) -> list[CheckResult]:
    checks: list[CheckResult] = []

    for required in REQUIRED_FILES:
        if not is_safe_repo_relative_path(required.path):
            checks.append(
                CheckResult(
                    name=f"required-file:{required.path}",
                    ok=False,
                    path=required.path,
                    message="required path is not repo-relative and safe",
                )
            )
            continue

        path = repo_root / required.path
        if not path.exists():
            checks.append(
                CheckResult(
                    name=f"required-file:{required.path}",
                    ok=False,
                    path=required.path,
                    message=f"missing {required.purpose}",
                )
            )
            continue

        if not path.is_file():
            checks.append(
                CheckResult(
                    name=f"required-file:{required.path}",
                    ok=False,
                    path=required.path,
                    message=f"expected file for {required.purpose}",
                )
            )
            continue

        if required.markers:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError as exc:
                checks.append(
                    CheckResult(
                        name=f"required-file:{required.path}",
                        ok=False,
                        path=required.path,
                        message=f"could not read {required.purpose}: {exc}",
                    )
                )
                continue

            missing_markers = [marker for marker in required.markers if marker not in text]
            if missing_markers:
                checks.append(
                    CheckResult(
                        name=f"required-file:{required.path}",
                        ok=False,
                        path=required.path,
                        message=(
                            f"{required.purpose} is present but missing expected marker(s): "
                            + ", ".join(missing_markers)
                        ),
                    )
                )
                continue

        checks.append(
            CheckResult(
                name=f"required-file:{required.path}",
                ok=True,
                path=required.path,
                message=f"present: {required.purpose}",
            )
        )

    return checks


def docker_cli_check(*, require_docker: bool, timeout: int = DEFAULT_DOCKER_TIMEOUT_SECONDS) -> CheckResult:
    if not require_docker:
        return CheckResult(
            name="docker-cli",
            ok=True,
            skipped=True,
            message="skipped; pass --require-docker to require Docker client/daemon availability",
        )

    docker = shutil.which("docker")
    if not docker:
        return CheckResult(
            name="docker-cli",
            ok=False,
            message="docker executable not found on PATH",
        )

    command = [docker, "version", "--format", "{{.Server.Version}}"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="docker-cli",
            ok=False,
            message=f"docker version timed out after {timeout} seconds",
        )
    except OSError as exc:
        return CheckResult(
            name="docker-cli",
            ok=False,
            message=f"docker version could not run: {exc}",
        )

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        if detail:
            detail = f": {detail}"
        return CheckResult(
            name="docker-cli",
            ok=False,
            message=f"docker daemon check failed with exit {completed.returncode}{detail}",
        )

    version = completed.stdout.strip() or "unknown"
    return CheckResult(name="docker-cli", ok=True, message=f"Docker daemon available: {version}")


def run_preflight(
    repo_root: Path | str = Path("."),
    *,
    require_docker: bool = False,
    docker_timeout: int = DEFAULT_DOCKER_TIMEOUT_SECONDS,
) -> ExecutorPreflightReport:
    started = time.perf_counter()
    root = repo_root_from_args(repo_root)
    checks = required_file_checks(root)
    checks.append(docker_cli_check(require_docker=require_docker, timeout=docker_timeout))
    ok = all(check.ok for check in checks)
    return ExecutorPreflightReport(
        ok=ok,
        repo_root=str(root),
        require_docker=require_docker,
        elapsed_s=round(time.perf_counter() - started, 4),
        checks=tuple(checks),
    )


def check_result_to_json(check: CheckResult) -> dict[str, Any]:
    return {
        "name": check.name,
        "ok": check.ok,
        "skipped": check.skipped,
        "path": check.path,
        "message": check.message,
    }


def report_to_json(report: ExecutorPreflightReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "repo_root": report.repo_root,
        "require_docker": report.require_docker,
        "elapsed_s": report.elapsed_s,
        "checks": [check_result_to_json(check) for check in report.checks],
    }


def print_text_report(report: ExecutorPreflightReport) -> None:
    outcome = "PASS" if report.ok else "FAIL"
    print(f"{outcome}: executor preflight root={report.repo_root} elapsed={report.elapsed_s:.2f}s")
    for check in report.checks:
        if check.skipped:
            label = "SKIP"
        else:
            label = "PASS" if check.ok else "FAIL"
        suffix = f" ({check.path})" if check.path else ""
        print(f"[{label}] {check.name}{suffix}: {check.message}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run read-only executor/Docker asset checks for the Main Computer release preflight."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Write machine-readable executor preflight output.",
    )
    parser.add_argument(
        "--require-docker",
        action="store_true",
        help=(
            "Require the Docker CLI and daemon to respond to a read-only docker version check. "
            "By default Docker availability is skipped."
        ),
    )
    parser.add_argument(
        "--docker-timeout",
        type=int,
        default=DEFAULT_DOCKER_TIMEOUT_SECONDS,
        help="Timeout in seconds for --require-docker daemon check.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    report = run_preflight(
        args.repo_root,
        require_docker=args.require_docker,
        docker_timeout=args.docker_timeout,
    )

    if args.json:
        print(json.dumps(report_to_json(report), indent=2, sort_keys=True))
    else:
        print_text_report(report)

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
