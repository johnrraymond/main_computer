#!/usr/bin/env python3
"""
Build live Foundry contracts.

Save as:
  tools/build_contracts.py

Run:
  python tools/build_contracts.py

Useful:
  python tools/build_contracts.py --clean
  python tools/build_contracts.py --test
  python tools/build_contracts.py --project contracts

Notes:
  - revision_control is skipped by default.
  - local forge is used when available.
  - otherwise Docker runs ghcr.io/foundry-rs/foundry:latest.
  - subprocess output is decoded as UTF-8 with replacement so Windows cp1252 cannot crash.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

FOUNDRY_IMAGE = "ghcr.io/foundry-rs/foundry:latest"

SKIP_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "lib", "vendor",
    "dist", "build", "out", "cache", "coverage",
    "runtime", "diagnostics_output",
    "generated_component_docs", "pretty_docs",
    "revision_control",
    "aider.log",
}


def emit(text: str, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    try:
        stream.write(text)
        stream.flush()
    except UnicodeEncodeError:
        encoding = stream.encoding or "utf-8"
        stream.write(
            text.encode(encoding, errors="replace").decode(encoding, errors="replace")
        )
        stream.flush()


def log(text: str = "") -> None:
    emit(text + "\n")


def repo_rel(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def tail(text: str | None, limit: int = 4000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def find_repo_root(start: Path) -> Path:
    current = start.resolve()

    if current.is_file():
        current = current.parent

    for candidate in (current, *current.parents):
        if (
            (candidate / "new_patch.py").exists()
            or (candidate / "pyproject.toml").exists()
            or (candidate / "docker-compose.dev.yml").exists()
            or (candidate / ".git").exists()
        ):
            return candidate

    return current


def walk_files(repo: Path, *, include_revision_control: bool):
    skip = set(SKIP_DIRS)

    if include_revision_control:
        skip.discard("revision_control")

    for current, dirs, files in os.walk(repo):
        dirs[:] = [name for name in dirs if name not in skip]

        for name in files:
            yield Path(current) / name


def discover_foundry_projects(
    repo: Path,
    *,
    include_revision_control: bool,
    only_projects: list[str],
) -> list[Path]:
    wanted = {Path(value).as_posix().rstrip("/") or "." for value in only_projects}
    projects: list[Path] = []

    for file_path in walk_files(repo, include_revision_control=include_revision_control):
        if file_path.name != "foundry.toml":
            continue

        project = file_path.parent
        label = repo_rel(project, repo)

        if wanted and label not in wanted:
            continue

        projects.append(project)

    return sorted(projects, key=lambda path: repo_rel(path, repo))


def docker_mount_path(repo: Path) -> str:
    resolved = repo.resolve()

    if os.name == "nt":
        return resolved.as_posix()

    return str(resolved)


def docker_workdir(repo: Path, project: Path) -> str:
    label = repo_rel(project, repo)

    if label == ".":
        return "/workspace"

    return "/workspace/" + label


def forge_command(
    repo: Path,
    project: Path,
    forge_args: list[str],
    *,
    no_docker: bool,
) -> tuple[list[str], Path]:
    forge = shutil.which("forge")

    if forge:
        return [forge, *forge_args], project

    docker = shutil.which("docker")

    if docker and not no_docker:
        return (
            [
                docker,
                "run",
                "--rm",
                "-e",
                "NO_COLOR=1",
                "-e",
                "CLICOLOR=0",
                "-v",
                f"{docker_mount_path(repo)}:/workspace",
                "-w",
                docker_workdir(repo, project),
                "--entrypoint",
                "forge",
                FOUNDRY_IMAGE,
                *forge_args,
            ],
            repo,
        )

    raise RuntimeError(
        "Foundry is required. Install `forge`, or run with Docker available."
    )


def run_command(command: list[str], cwd: Path, label: str) -> dict:
    log()
    log("$ " + " ".join(command))
    log(f"  cwd: {cwd}")

    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("CLICOLOR", "0")
    env.setdefault("TERM", "dumb")

    completed = subprocess.run(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    if completed.stdout:
        emit(completed.stdout)

        if not completed.stdout.endswith("\n"):
            log()

    if completed.stderr:
        emit(completed.stderr, err=True)

        if not completed.stderr.endswith("\n"):
            emit("\n", err=True)

    return {
        "label": label,
        "command": command,
        "cwd": str(cwd),
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout_tail": tail(completed.stdout),
        "stderr_tail": tail(completed.stderr),
    }


def run_forge(repo: Path, project: Path, forge_args: list[str], *, no_docker: bool) -> dict:
    command, cwd = forge_command(repo, project, forge_args, no_docker=no_docker)
    return run_command(command, cwd, repo_rel(project, repo))


def write_report(repo: Path, projects: list[Path], results: list[dict], report: Path) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "repo_root": str(repo),
        "ok": all(result["ok"] for result in results),
        "project_count": len(projects),
        "projects": [repo_rel(project, repo) for project in projects],
        "results": results,
    }

    report.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    log()
    log(f"Wrote report: {report}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build live Foundry contract projects.")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="Repo-relative project root, e.g. contracts. May be repeated.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Run forge clean before forge build.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run forge test after successful forge build.",
    )
    parser.add_argument(
        "--include-revision-control",
        action="store_true",
        help="Also build historical revision_control projects.",
    )
    parser.add_argument(
        "--no-docker",
        action="store_true",
        help="Disable Docker fallback.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero if no projects are found.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("runtime/contract_build/report.json"),
    )

    args = parser.parse_args(argv)

    repo = find_repo_root(args.repo_root or Path.cwd())
    report = args.report if args.report.is_absolute() else repo / args.report

    log(f"Repository root: {repo}")

    projects = discover_foundry_projects(
        repo,
        include_revision_control=args.include_revision_control,
        only_projects=args.project,
    )

    if not projects:
        log("No live Foundry contract projects found.")
        log("Expected at least one foundry.toml outside skipped folders.")
        log("revision_control is skipped unless --include-revision-control is used.")
        write_report(repo, projects, [], report)
        return 2 if args.strict else 0

    log()
    log("Discovered live Foundry projects:")

    for project in projects:
        log(f"  - {repo_rel(project, repo)}")

    results: list[dict] = []

    try:
        for project in projects:
            if args.clean:
                result = run_forge(repo, project, ["clean"], no_docker=args.no_docker)
                results.append(result)

                if not result["ok"]:
                    continue

            build_result = run_forge(repo, project, ["build"], no_docker=args.no_docker)
            results.append(build_result)

            if args.test and build_result["ok"]:
                results.append(
                    run_forge(repo, project, ["test"], no_docker=args.no_docker)
                )

    except KeyboardInterrupt:
        log()
        log("Interrupted.")
        return 130

    except Exception as exc:
        log()
        log(f"ERROR: {exc}")
        results.append(
            {
                "label": "setup",
                "command": [],
                "cwd": str(repo),
                "ok": False,
                "returncode": 1,
                "stdout_tail": "",
                "stderr_tail": str(exc),
            }
        )

    write_report(repo, projects, results, report)

    failures = [result for result in results if not result["ok"]]

    if failures:
        log()
        log(f"Contract build failed: {len(failures)} failed command(s).")

        for failure in failures:
            log(f"  - {failure['label']}: exit {failure['returncode']}")

        return 1

    log()
    log(
        f"Contract build succeeded: {len(results)} command(s) "
        f"across {len(projects)} project(s)."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())