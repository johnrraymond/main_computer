"""Shared cleanup-boundary gate for Mother static-node reconciliation.

This module intentionally owns only the orchestration contract around
``mother_bootnode_precleanup``.  It does not compute enodes, write static-node
files, patch Compose, restart parents, or delete services.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
import sys
from typing import Any

from tools.mother_bootnode_precleanup import (
    MotherBootnodePrecleanupError,
    run_bootnode_precleanup,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class StaticNodePrecleanupGateError(RuntimeError):
    """The static-node precleanup gate could not safely pass before cleanup."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def topology_evidence_from_mapping(
    source: Any,
    *,
    invalid_code: str,
    context: str,
) -> tuple[str, str]:
    """Extract a guarded topology evidence locator and sha256 from a mapping."""

    if not isinstance(source, Mapping):
        raise StaticNodePrecleanupGateError(
            invalid_code,
            f"{context} lacks topology evidence mapping for static-node precleanup",
        )

    topology_evidence = source.get("locator")
    topology_sha256 = source.get("sha256")
    if not isinstance(topology_evidence, str) or not topology_evidence.strip():
        raise StaticNodePrecleanupGateError(
            invalid_code,
            f"{context} lacks topology evidence locator for static-node precleanup",
        )
    if not isinstance(topology_sha256, str) or not _SHA256_RE.fullmatch(topology_sha256):
        raise StaticNodePrecleanupGateError(
            invalid_code,
            f"{context} lacks topology evidence sha256 for static-node precleanup",
        )
    return topology_evidence, topology_sha256


def build_static_node_precleanup_argv(
    *,
    repo_root: Path,
    network: str,
    runtime_state_root: str | Path,
    topology_evidence: str | Path,
    acknowledged_topology_evidence_sha256: str,
    exclude_nodes: Sequence[str] = (),
    preserve_services: bool = True,
    probe_node_info: bool = False,
    max_wait_seconds: float = 120.0,
    poll_interval_seconds: float = 5.0,
    timeout: float = 15.0,
    max_response_bytes: int = 12 * 1024 * 1024,
) -> list[str]:
    """Build the canonical subprocess argv for the static-node precleanup gate."""

    argv = [
        sys.executable,
        str(repo_root / "tools" / "mother_bootnode_precleanup.py"),
        str(network),
        "--runtime-state-root",
        str(runtime_state_root),
        "--topology-evidence",
        str(topology_evidence),
        "--acknowledge-topology-evidence-sha256",
        str(acknowledged_topology_evidence_sha256),
        "--execute",
        "--allow-mutation",
        "--max-wait-seconds",
        str(max_wait_seconds),
        "--poll-interval-seconds",
        str(poll_interval_seconds),
        "--timeout",
        str(timeout),
        "--max-response-bytes",
        str(max_response_bytes),
    ]

    for node in exclude_nodes:
        argv.extend(["--exclude-node", str(node)])
    if not probe_node_info:
        argv.append("--no-live-node-info")
    if preserve_services:
        argv.append("--preserve-services")
    return argv


def run_static_node_precleanup_gate(
    *,
    network: str,
    runtime_state_root: str | Path,
    topology_evidence: str | Path,
    acknowledged_topology_evidence_sha256: str,
    exclude_nodes: Sequence[str] = (),
    preserve_services: bool = True,
    probe_node_info: bool = False,
    timeout: float = 15.0,
    max_response_bytes: int = 12 * 1024 * 1024,
    max_wait_seconds: float = 120.0,
    poll_interval_seconds: float = 5.0,
    opener: Any = None,
    failure_code: str,
    context: str,
) -> dict[str, Any]:
    """Run ``mother_bootnode_precleanup`` as the shared cleanup-boundary gate."""

    kwargs: dict[str, Any] = {
        "network": network,
        "runtime_state_root": runtime_state_root,
        "topology_evidence": topology_evidence,
        "acknowledged_topology_evidence_sha256": acknowledged_topology_evidence_sha256,
        "exclude_nodes": tuple(exclude_nodes),
        "execute": True,
        "allow_mutation": True,
        "preserve_services": preserve_services,
        "probe_node_info": probe_node_info,
        "write_evidence": True,
        "timeout": timeout,
        "max_response_bytes": max_response_bytes,
        "max_wait_seconds": max_wait_seconds,
        "poll_interval_seconds": poll_interval_seconds,
    }
    if opener is not None:
        kwargs["opener"] = opener

    try:
        result = run_bootnode_precleanup(**kwargs)
    except MotherBootnodePrecleanupError as exc:
        raise StaticNodePrecleanupGateError(
            failure_code,
            f"static-node precleanup failed before {context}: {exc}",
        ) from exc

    if result.get("status") != "pass":
        raise StaticNodePrecleanupGateError(
            failure_code,
            f"static-node precleanup did not pass before {context}",
        )
    return result
