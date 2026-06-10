from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


def _split_launcher_args(argv: list[str]) -> tuple[list[str], list[str]]:
    try:
        separator = argv.index("--")
    except ValueError as exc:
        raise SystemExit("missing -- separator before target command") from exc
    launcher_args = argv[:separator]
    command = argv[separator + 1 :]
    if not command:
        raise SystemExit("missing target command after -- separator")
    return launcher_args, command


def _launch_creation_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        flags = 0
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": flags}
    return {"start_new_session": True}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    launcher_argv, command = _split_launcher_args(raw_argv)

    parser = argparse.ArgumentParser(description="Launch a long-running Python process without creating a console window.")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    parser.add_argument("--pid-json", required=True)
    args = parser.parse_args(launcher_argv)

    cwd = Path(args.cwd).resolve()
    stdout_path = Path(args.stdout)
    stderr_path = Path(args.stderr)
    pid_json_path = Path(args.pid_json)

    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    pid_json_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("ab") as stdout_handle, stderr_path.open("ab") as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            **_launch_creation_kwargs(),
        )

    payload = {
        "ok": True,
        "pid": process.pid,
        "cwd": str(cwd),
        "command": command,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    _write_json(pid_json_path, payload)
    print(json.dumps(payload, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
