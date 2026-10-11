#!/usr/bin/env python3
"""Production Stable Hub launcher for Hub Control's accepted topology projection.

The Hub Control bootstrap writes the FDB cluster and topology files before
this entrypoint starts.  The concrete Hub identity must be explicit: never
choose a Hub based on an unspecified port or a development topology.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from main_computer.stable_hub import main as stable_hub_main


def main() -> int:
    topology = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", "").strip()
    hub_id = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "").strip()
    port = os.environ.get("MAIN_COMPUTER_HUB_PORT", os.environ.get("PORT", "8790")).strip()
    if not topology or not hub_id:
        print("Stable Hub requires Hub Control topology path and concrete Hub ID", file=sys.stderr)
        return 2
    if not Path(topology).is_file():
        print(f"Stable Hub topology not found: {topology}", file=sys.stderr)
        return 2
    try:
        parsed_port = int(port)
        if not 1 <= parsed_port <= 65535:
            raise ValueError("port outside TCP range")
    except ValueError:
        print(f"Stable Hub port is invalid: {port}", file=sys.stderr)
        return 2
    return stable_hub_main([
        "--topology", topology, "--hub-id", hub_id,
        "--bind-host", "0.0.0.0", "--bind-port", str(parsed_port),
    ])


if __name__ == "__main__":
    raise SystemExit(main())
