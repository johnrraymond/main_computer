#!/usr/bin/env python3
"""Production Stable Hub launcher for Hub Control's accepted topology projection.

Hub Control supplies frozen FDB and topology inputs through the environment.
Materialize them before starting the Hub even when Coolify runs Dockerfile CMD
instead of its configured start_command.  Never select an implicit Hub identity.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
from pathlib import Path

from main_computer.stable_hub import main as stable_hub_main


def _write_if_changed(path: Path, contents: bytes) -> None:
    """Publish startup material atomically, preserving it on repeat launches."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == contents:
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(contents)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _materialize_hub_control_artifacts(topology: Path) -> None:
    """Make Hub Control's frozen environment projection usable with Docker CMD.

    Coolify can ignore an application's start_command and run the Dockerfile
    CMD directly.  The launcher must therefore perform the same projection.
    """
    encoded = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_B64", "").strip()
    short_encoded = os.environ.get("MCT", "").strip()
    if encoded and short_encoded and encoded != short_encoded:
        raise ValueError("Hub Control topology environment aliases disagree")
    encoded = encoded or short_encoded
    if not encoded:
        return  # Local launches may supply an already-materialized topology.

    cluster = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_FDB_CLUSTER_CONTENTS", "").strip()
    short_cluster = os.environ.get("MCF", "").strip()
    if cluster and short_cluster and cluster != short_cluster:
        raise ValueError("Hub Control FDB environment aliases disagree")
    cluster = cluster or short_cluster
    cluster_path = (
        os.environ.get("MAIN_COMPUTER_HUB_CONTROL_CLUSTER_FILE", "").strip()
        or os.environ.get("MAIN_COMPUTER_HUB_FDB_CLUSTER_FILE", "").strip()
    )
    if not cluster or not cluster_path:
        raise ValueError("Hub Control FDB bootstrap configuration is incomplete")
    decoded = base64.b64decode(encoded, validate=True)
    if not isinstance(json.loads(decoded), dict):
        raise ValueError("Hub Control topology is not a JSON object")

    _write_if_changed(Path(cluster_path), (cluster + "\n").encode("utf-8"))
    _write_if_changed(topology, decoded)


def main() -> int:
    topology = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_TOPOLOGY_PATH", "").strip()
    hub_id = os.environ.get("MAIN_COMPUTER_HUB_CONTROL_HUB_ID", "").strip()
    port = os.environ.get("MAIN_COMPUTER_HUB_PORT", os.environ.get("PORT", "8790")).strip()
    if not topology or not hub_id:
        print("Stable Hub requires Hub Control topology path and concrete Hub ID", file=sys.stderr)
        return 2
    try:
        _materialize_hub_control_artifacts(Path(topology))
    except (OSError, ValueError, UnicodeError) as exc:
        print(f"Stable Hub bootstrap failed: {exc}", file=sys.stderr)
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
