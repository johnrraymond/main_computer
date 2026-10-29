"""Resident SLH worker gateway, supervised by Main Computer's service supervisor.

The gateway is always an installed service, including before organization
bootstrap. It waits for local initialization instead of thrashing/restarting.
When initialized, the existing managed gateway owns the configured SEH tunnel.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from typing import Callable

from main_computer.slh.authority import SLHAuthority
from main_computer.slh.ports import DEFAULT_SLH_MANAGED_PORT as DEFAULT_MANAGED_PORT


def default_state_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local/share")) / "MainComputer" / "slh"


def run(*, state_dir: Path | None = None, sleep: Callable[[float], None] = time.sleep,
        authority_factory: Callable[[Path], SLHAuthority] = SLHAuthority,
        gateway_main: Callable[[list[str]], int] | None = None,
        poll_seconds: float = 3.0, port: int = DEFAULT_MANAGED_PORT) -> int:
    """Keep the supervised process alive before bootstrap, then serve normally."""
    directory = state_dir or default_state_dir()
    authority = authority_factory(directory)
    if not authority.initialized():
        print(json.dumps({"service": "slh", "state": "waiting-for-admin-bootstrap", "state_dir": str(directory)}), flush=True)
        while not authority.initialized():
            sleep(max(0.1, poll_seconds))
    print(json.dumps({"service": "slh", "state": "starting-worker-gateway", "port": port}), flush=True)
    if gateway_main is None:
        from tools.slh_managed_gateway import main as gateway_main
    return int(gateway_main(["serve", "--state-dir", str(directory), "--port", str(port)]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Supervised SLH worker gateway")
    parser.add_argument("--root", type=Path, default=Path("."), help="Main Computer installation root")
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=int(os.environ.get("MAIN_COMPUTER_SLH_PORT", DEFAULT_MANAGED_PORT)))
    parser.add_argument("command", choices=("run",))
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    return run(state_dir=args.state_dir, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
