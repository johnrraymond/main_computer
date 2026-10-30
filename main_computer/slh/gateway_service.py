"""Compatibility entry point for the supervisor's resident SLH child.

The supervisor's historical ``slh`` child now runs ONLY the localhost control
plane. It no longer starts the organization/worker gateway after bootstrap.
The organization service will be an explicitly enabled child in Patch 2.
"""
from __future__ import annotations

from pathlib import Path
from main_computer.slh.local_service import main as _main, run as _local_run
from main_computer.slh.storage import slh_state_dir


def default_state_dir() -> Path:
    return slh_state_dir()


def run(*, state_dir: Path | None = None, **kwargs) -> int:
    return _local_run(state_dir=state_dir, attach_existing=True, **kwargs)


def main(argv: list[str] | None = None) -> int:
    # The supervisor attaches if an installed Windows boot task already owns
    # the loopback listener, instead of spawning a second copy.
    values = list(argv) if argv is not None else None
    if values is None:
        import sys
        values = sys.argv[1:]
    return _main(['--attach-existing', *values])


if __name__ == '__main__':
    raise SystemExit(main())
