#!/usr/bin/env python3
"""Repository-root entry point for the MCEL app proof runner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_computer.mcel_app_prove import main


if __name__ == "__main__":
    raise SystemExit(main())
