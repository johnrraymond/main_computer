#!/usr/bin/env python3
"""Copy the auto-selected live-topology evidence forward to today's Mother binding.

This is NOT a topology seal. Only mother_binding, completed_at (the harness's
baseline recency key), and the evidence's own digest are updated. All topology,
service, validator, chain/genesis, and prior observation fields are preserved.
The existing add-node detector decides what, if anything, needs rectification.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mother_mutate_harness as harness
from tools.mother.common.canonical import canonical_json
from tools.mother.common.deployment_topology_rectification import (
    _canonical_file,
    _digest_without,
)


def _latest_add_baseline(runtime_state_root: Path) -> Path | None:
    args = argparse.Namespace(operation="add-node", runtime_state_root=str(runtime_state_root))
    _reason, patterns = harness.auto_baseline_patterns(args)
    return harness.select_auto_baseline_file(args, patterns)


def _binding(runtime_state_root: Path, network: str) -> dict[str, Any]:
    args = argparse.Namespace(runtime_state_root=str(runtime_state_root), network=network)
    return harness._current_private_state_binding(args)


def forward_evidence(
    runtime_state_root: Path,
    *,
    network: str = "mainnet",
    binding: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Forward the currently auto-selected live seal; never modify its source."""
    if network != "mainnet":
        raise ValueError("this script is limited to the harness's mainnet live-topology baseline")
    runtime_state_root = Path(runtime_state_root).resolve()
    source = _latest_add_baseline(runtime_state_root)
    if source is None:
        raise ValueError("no add-node baseline exists; nothing to forward")
    expected_dir = (runtime_state_root / "mother" / "evidence" /
                    harness.LIVE_CURRENT_TOPOLOGY_EVIDENCE_DIRECTORY).resolve()
    if source.parent.resolve() != expected_dir:
        raise ValueError(f"auto-selected baseline is not a live-current-topology seal: {source}")

    document, _raw, source_sha = _canonical_file(source)
    if not harness._successful_live_current_topology_baseline(source):
        raise ValueError("auto-selected live-topology evidence is not a successful baseline")
    if document.get("network") != network:
        raise ValueError("source topology evidence network does not match the requested network")
    if document.get("live_current_topology_sha256") != _digest_without(document, "live_current_topology_sha256"):
        raise ValueError("source evidence internal SHA-256 does not match; refusing to forward")
    old_binding = document.get("mother_binding")
    if not isinstance(old_binding, dict) or set(old_binding) != {"generation", "content_sha256", "manifest_sha256"}:
        raise ValueError("source mother_binding is malformed")

    current_binding = binding if binding is not None else _binding(runtime_state_root, network)
    if not isinstance(current_binding, dict) or set(current_binding) != set(old_binding):
        raise ValueError("current private-state binding is malformed")
    if not all(
        isinstance(item.get("generation"), int)
        and item["generation"] > 0
        and all(isinstance(item.get(key), str) and len(item[key]) == 64
                and all(ch in "0123456789abcdef" for ch in item[key].lower())
                for key in ("content_sha256", "manifest_sha256"))
        for item in (old_binding, current_binding)
    ):
        raise ValueError("old or current private-state binding has invalid generation/hash values")
    if current_binding == old_binding:
        return {
            "action": "unchanged",
            "source": str(source),
            "source_sha256": source_sha,
            "generation": old_binding["generation"],
            "auto_baseline_selected": True,
        }

    timestamp = now if now is not None else datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    timestamp = timestamp.astimezone(timezone.utc)
    completed = timestamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    source_recency = harness._baseline_path_recency(source)
    if timestamp.timestamp() <= source_recency:
        raise ValueError("current time is not newer than the selected baseline; refusing an unselectable copy")

    updated = dict(document)
    updated["mother_binding"] = dict(current_binding)
    # The harness selects by completed_at before filename/mtime. This is the
    # evidence-forwarding time, not a new live-topology observation.
    updated["completed_at"] = completed
    updated["live_current_topology_sha256"] = _digest_without(updated, "live_current_topology_sha256")
    payload = canonical_json(updated)
    digest = hashlib.sha256(payload).hexdigest()
    stamp = harness._stamp_for_filename(completed)
    destination = expected_dir / f"{stamp}-{network}-live-current-topology-{updated['live_current_topology_sha256'][:16]}.json"
    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    # Exclusive creation: never overwrite or alter historical evidence.
    with destination.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())

    selected = _latest_add_baseline(runtime_state_root)
    if selected is None or selected.resolve() != destination.resolve():
        raise RuntimeError(f"new evidence written but harness auto-selection did not choose it: {selected}")
    check, _bytes, file_sha = _canonical_file(destination)
    if file_sha != digest or check["mother_binding"] != current_binding or check["live_current_topology_sha256"] != _digest_without(check, "live_current_topology_sha256"):
        raise RuntimeError("new evidence failed post-write verification")

    return {
        "action": "forwarded",
        "source": str(source),
        "source_sha256": source_sha,
        "old_generation": old_binding["generation"],
        "new_generation": current_binding["generation"],
        "evidence_path": str(destination),
        "evidence_sha256": file_sha,
        "auto_baseline_selected": True,
        "live_topology_reverified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-state-root", default="runtime/state")
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    args = parser.parse_args()
    try:
        result = forward_evidence(Path(args.runtime_state_root), network=args.network)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"MOTHER_FORWARD_TOPOLOGY_EVIDENCE_ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"MOTHER_FORWARD_TOPOLOGY_EVIDENCE: {result['action']}")
    for key, value in result.items():
        if key != "action":
            print(f"{key}={value}")
    print("Topology was NOT resealed. Rerun add-node without changing its safety flags.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
