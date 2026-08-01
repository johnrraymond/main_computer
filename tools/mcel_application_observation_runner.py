#!/usr/bin/env python3
"""Run app-scoped MCEL operation-linked browser observation evidence."""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_application_observation_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
