#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def coolify_tool(root: Path) -> Path:
    return root / "tools" / "local-prod" / "coolify-local-docker.py"


def docker_available() -> bool:
    return shutil.which("docker") is not None


def common_runtime_args(args: argparse.Namespace) -> list[str]:
    runtime_args: list[str] = []
    if args.project_name:
        runtime_args.extend(["--project-name", args.project_name])
    if args.state_dir:
        runtime_args.extend(["--state-dir", args.state_dir])
    if args.app_port:
        runtime_args.extend(["--app-port", str(args.app_port)])
    if args.soketi_port:
        runtime_args.extend(["--soketi-port", str(args.soketi_port)])
    if args.soketi_terminal_port:
        runtime_args.extend(["--soketi-terminal-port", str(args.soketi_terminal_port)])
    return runtime_args


def run_step(root: Path, tool: Path, action: str, runtime_args: list[str], *, timeout_seconds: int) -> int:
    command = [sys.executable, str(tool), action, *runtime_args]
    print(f"Local Coolify setup: {action}", flush=True)
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n", flush=True)
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr, flush=True)
    return int(completed.returncode)


def action_plan(action: str, *, skip_deploy_smoke: bool) -> list[tuple[str, int]]:
    if action == "setup":
        steps = [("up", 300), ("api-smoke", 300)]
        if not skip_deploy_smoke:
            steps.append(("deploy-smoke", 900))
        return steps
    if action == "ensure":
        return [("up", 300), ("wait", 300), ("ensure-infra", 420)]
    raise ValueError(f"unsupported action: {action}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Set up or repair the mode-scoped Local Coolify stack. "
            "This is the shared entry point used by the installer and by the applications service."
        )
    )
    parser.add_argument("action", choices=["setup", "ensure"], help="setup runs the installer path; ensure starts a missing stack and proves infrastructure.")
    parser.add_argument("--project-name", default="", help="Docker Compose project name for this mode.")
    parser.add_argument("--state-dir", default="", help="Runtime state directory for this mode.")
    parser.add_argument("--app-port", type=int, default=0, help="Host dashboard/API port for this mode.")
    parser.add_argument("--soketi-port", type=int, default=0, help="Host Soketi port for this mode.")
    parser.add_argument("--soketi-terminal-port", type=int, default=0, help="Host Soketi terminal port for this mode.")
    parser.add_argument("--skip-deploy-smoke", action="store_true", help="For setup, skip the remote-prod publish rehearsal.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = repo_root()
    tool = coolify_tool(root)
    if not tool.exists():
        print(f"error: Local Coolify implementation tool is missing: {tool}", file=sys.stderr, flush=True)
        return 2
    if not docker_available():
        print("error: Docker was not found on PATH; Local Coolify cannot be started.", file=sys.stderr, flush=True)
        return 1

    runtime_args = common_runtime_args(args)
    for step, timeout_seconds in action_plan(args.action, skip_deploy_smoke=args.skip_deploy_smoke):
        try:
            returncode = run_step(root, tool, step, runtime_args, timeout_seconds=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            print(f"error: Local Coolify setup step timed out: {step}: {exc}", file=sys.stderr, flush=True)
            return 1
        if returncode != 0:
            print(f"error: Local Coolify setup step failed: {step} exited {returncode}", file=sys.stderr, flush=True)
            return returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
