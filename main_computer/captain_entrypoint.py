"""Lightweight Captain smoke entrypoint independent of viewport/MCEL imports.

Do not import main_computer.cli here: its general command graph loads the
viewport and virtual browser application catalog before Captain can prepare.
"""
from __future__ import annotations

import sys
from pathlib import Path

from main_computer.captain_cli import CaptainCliError, run_captain


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return run_captain(args, cwd=Path.cwd())
    except CaptainCliError as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
