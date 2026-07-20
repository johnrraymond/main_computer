#!/usr/bin/env python3
"""Compatibility entrypoint for the Allfather Stage 1/2 image reconciler.

The canonical implementation now lives in tools/allfather_control.py so the main
Allfather control process owns control-surface reconciliation, four-checkpoint
image builds, fail-fast semantics, and verified image handoff state.  This file
is intentionally only a thin wrapper to preserve the existing operator command:

    python tools/allfather_control_1_2.py mainnet --allow-mainnet ...
"""

from __future__ import annotations

from typing import Sequence

import allfather_control as afc


def main(argv: Sequence[str] | None = None) -> int:
    return afc.stage_1_2_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
