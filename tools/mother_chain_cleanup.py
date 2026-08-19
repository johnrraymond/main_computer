#!/usr/bin/env python3
"""Standalone QBFT pending-vote cleanup for Mother.

This tool is intentionally chain-only.  It does not call Coolify, rewrite
Compose, redeploy services, or remove Docker containers.

The cleanup logic is cloned from the working validator-admission voter guardian:
clear only satisfied pending QBFT votes, and only after a quiet validator-set
window.  A pending add vote is satisfied when the target is already in the live
validator set.  A pending remove vote is satisfied when the target is already
absent from the live validator set.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Mapping
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.canonical import canonical_json
from tools.mother.common.ethereum_identity import checksum_address


KIND = "main_computer.mother.chain_cleanup.v1"
DEFAULT_QBFT_STALE_VOTE_QUIET_SECONDS = 35.0
DEFAULT_QBFT_STALE_VOTE_POLL_SECONDS = 5.0


class MotherChainCleanupError(RuntimeError):
    """Raised when chain cleanup cannot safely continue."""


class JsonRpcClient:
    def __init__(self, url: str, *, timeout: float = 10.0) -> None:
        text = str(url or "").strip()
        if not text:
            raise MotherChainCleanupError("RPC URL is required")
        self.url = text
        self.timeout = float(timeout)

    def rpc(self, method: str, params: list[Any] | None = None) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": str(method),
            "params": [] if params is None else params,
        }
        req = urllib.request.Request(
            self.url,
            data=canonical_json(payload),
            headers={"Content-Type": "application/json", "Host": "localhost"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            value = json.loads(response.read(1048576).decode("utf-8"))
        if not isinstance(value, dict) or value.get("error") is not None or "result" not in value:
            raise MotherChainCleanupError(f"{method} failed: {value.get('error') if isinstance(value, dict) else value!r}")
        return value["result"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _address(value: Any, label: str) -> str:
    text = str(value or "").lower()
    if re.fullmatch(r"0x[0-9a-f]{40}", text) is None:
        raise MotherChainCleanupError(f"{label} is not an Ethereum address")
    return text


def _validator_vote_address(value: Any, label: str) -> str:
    """Return the EIP-55 address form Besu accepts for QBFT vote RPCs.

    This mirrors the validator-admission voter guardian.  Mother compares
    validator addresses in lowercase form, but Besu 26.7 rejects lowercase
    ``qbft_proposeValidatorVote`` / ``qbft_discardValidatorVote`` parameters
    with ``Invalid address params``.
    """

    try:
        return checksum_address(_address(value, label))
    except ValueError as exc:
        raise MotherChainCleanupError(f"{label} cannot be checksummed") from exc


def _flatten_addresses(values: list[str] | None) -> list[str]:
    flattened: list[str] = []
    for raw in values or []:
        for part in str(raw).split(","):
            text = part.strip()
            if text:
                flattened.append(text)
    return flattened


def _vote_address_by_lower(addresses: list[str]) -> dict[str, str]:
    return {
        _address(item, "committed validator address"): _validator_vote_address(item, "committed validator address")
        for item in sorted(set(addresses))
    }


def _same_set(left: list[str], right: list[str]) -> bool:
    return sorted(left) == sorted(right)


def _validators(client: Any) -> list[str]:
    result = client.rpc("qbft_getValidatorsByBlockNumber", ["latest"])
    if not isinstance(result, list):
        raise MotherChainCleanupError("validator response is not a list")
    return [_address(item, "live validator") for item in result]


def _chain_id(client: Any) -> int:
    result = client.rpc("eth_chainId", [])
    try:
        return int(str(result), 16)
    except ValueError as exc:
        raise MotherChainCleanupError(f"chain id response is invalid: {result!r}") from exc


def _pending_votes(client: Any) -> dict[str, bool]:
    result = client.rpc("qbft_getPendingVotes", [])
    if not isinstance(result, dict):
        raise MotherChainCleanupError("pending votes response is not an object")
    normalized: dict[str, bool] = {}
    for address, vote in result.items():
        text = str(address or "").lower()
        if not text.startswith("0x"):
            text = "0x" + text
        if len(text) == 42 and isinstance(vote, bool):
            normalized[text] = vote
    return normalized


def _syncing(client: Any) -> bool:
    return client.rpc("eth_syncing", []) is not False


def _block_number(client: Any) -> int:
    return int(client.rpc("eth_blockNumber", []), 16)


def satisfied_pending_vote_targets(pending: Mapping[str, bool], live_validators: list[str]) -> list[dict[str, Any]]:
    """Clone the v1 guardian's satisfied pending-vote target classifier."""

    live = set(live_validators)
    targets: list[dict[str, Any]] = []
    for address, vote in sorted(pending.items()):
        if vote is True and address in live:
            targets.append({"address": address, "vote": vote, "reason": "add_vote_already_in_validator_set"})
        elif vote is False and address not in live:
            targets.append({"address": address, "vote": vote, "reason": "remove_vote_already_absent_from_validator_set"})
    return targets


def wait_for_quiet_validator_set(
    client: Any,
    reference: list[str],
    *,
    quiet_seconds: float = DEFAULT_QBFT_STALE_VOTE_QUIET_SECONDS,
    poll_seconds: float = DEFAULT_QBFT_STALE_VOTE_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Clone the v1 guardian's quiet-window check before discarding votes."""

    deadline = monotonic() + float(quiet_seconds)
    first = _block_number(client)
    observations: list[dict[str, Any]] = [
        {"elapsed_seconds": 0, "validator_set": reference, "block_number": first, "syncing": _syncing(client)}
    ]
    while monotonic() < deadline:
        sleep(min(float(poll_seconds), max(0.0, deadline - monotonic())))
        current = _validators(client)
        block = _block_number(client)
        node_syncing = _syncing(client)
        observations.append(
            {
                "elapsed_seconds": round(max(0.0, float(quiet_seconds) - max(0.0, deadline - monotonic())), 3),
                "validator_set": current,
                "block_number": block,
                "syncing": node_syncing,
            }
        )
        if not _same_set(current, reference):
            return {
                "quiet": False,
                "reason": "validator_set_changed",
                "reference_validator_set": reference,
                "observations": observations,
            }
    last = observations[-1]
    quiet = (last["block_number"] > first and last["syncing"] is False)
    return {
        "quiet": quiet,
        "reason": "quiet_window_satisfied" if quiet else "quiet_window_without_block_progress",
        "reference_validator_set": reference,
        "observations": observations,
    }


def inspect_satisfied_pending_votes(client: Any, live_validators: list[str], *, phase: str) -> dict[str, Any]:
    before = _pending_votes(client)
    targets = satisfied_pending_vote_targets(before, live_validators)
    return {
        "phase": phase,
        "pending_votes_before": before,
        "cleanup_targets": targets,
        "cleared": [],
        "skipped": [],
        "pending_votes_after": before,
        "status": "would_clear" if targets else "not_needed",
    }


def cleanup_satisfied_pending_votes(
    client: Any,
    live_validators: list[str],
    *,
    phase: str,
    vote_address_by_lower: Mapping[str, str],
    quiet_seconds: float = DEFAULT_QBFT_STALE_VOTE_QUIET_SECONDS,
    poll_seconds: float = DEFAULT_QBFT_STALE_VOTE_POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Clone the v1 guardian's pending-vote cleanup behavior."""

    before = _pending_votes(client)
    targets = satisfied_pending_vote_targets(before, live_validators)
    result: dict[str, Any] = {
        "phase": phase,
        "quiet_seconds": quiet_seconds,
        "pending_votes_before": before,
        "cleanup_targets": targets,
        "cleared": [],
        "skipped": [],
    }
    if not targets:
        result["pending_votes_after"] = before
        result["status"] = "not_needed"
        return result
    quiet = wait_for_quiet_validator_set(
        client,
        live_validators,
        quiet_seconds=quiet_seconds,
        poll_seconds=poll_seconds,
        sleep=sleep,
        monotonic=monotonic,
    )
    result["quiet_observation"] = quiet
    if quiet.get("quiet") is not True:
        result["pending_votes_after"] = _pending_votes(client)
        result["status"] = "deferred_not_quiet"
        return result
    refreshed_live = _validators(client)
    refreshed = _pending_votes(client)
    for target in satisfied_pending_vote_targets(refreshed, refreshed_live):
        address = target["address"]
        vote_address = vote_address_by_lower.get(address)
        if not vote_address:
            skipped = dict(target)
            skipped["reason"] = str(skipped.get("reason", "satisfied_vote")) + "_without_committed_checksum"
            result["skipped"].append(skipped)
            continue
        discard_result = client.rpc("qbft_discardValidatorVote", [vote_address])
        cleared = dict(target)
        cleared["discard_result"] = discard_result
        cleared["discard_vote_address"] = vote_address
        result["cleared"].append(cleared)
    result["pending_votes_after"] = _pending_votes(client)
    result["status"] = "cleared" if result["cleared"] else "no_clearable_targets"
    return result


def _parse_rpc_url(value: str, index: int) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text:
        raise MotherChainCleanupError("--rpc-url cannot be empty")
    if "=" in text:
        name, url = text.split("=", 1)
        name = name.strip()
        url = url.strip()
    else:
        name = f"node-{index + 1}"
        url = text
    if not name:
        raise MotherChainCleanupError("--rpc-url node label cannot be empty")
    if not url:
        raise MotherChainCleanupError("--rpc-url URL cannot be empty")
    return name, url


def _write_evidence(runtime_state_root: str | None, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if not runtime_state_root:
        return None
    root = Path(runtime_state_root)
    directory = root / "mother" / "evidence" / "chain-cleanup"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{stamp}-qbft-pending-vote-cleanup.json"
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    path.write_bytes(body)
    return {"path": str(path), "sha256": hashlib.sha256(body).hexdigest()}


def run_chain_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    mode = str(args.mode)
    execute = mode == "execute"
    if execute and not args.allow_discard_satisfied_pending_votes:
        raise MotherChainCleanupError("execute requires --allow-discard-satisfied-pending-votes")
    if execute and not args.acknowledge_chain_cleanup_only:
        raise MotherChainCleanupError("execute requires --acknowledge-chain-cleanup-only")

    committed = _flatten_addresses(args.committed_validator_address)
    vote_map = _vote_address_by_lower(committed)
    expected_chain_id = int(args.expected_chain_id) if args.expected_chain_id is not None else None

    node_results: list[dict[str, Any]] = []
    for index, raw in enumerate(args.rpc_url or []):
        node, url = _parse_rpc_url(raw, index)
        client = JsonRpcClient(url, timeout=args.timeout)
        chain_id = _chain_id(client)
        if expected_chain_id is not None and chain_id != expected_chain_id:
            raise MotherChainCleanupError(f"{node} chain id mismatch: expected {expected_chain_id}, observed {chain_id}")
        live = _validators(client)
        phase = args.phase or "standalone-chain-cleanup"
        if execute:
            cleanup = cleanup_satisfied_pending_votes(
                client,
                live,
                phase=phase,
                vote_address_by_lower=vote_map,
                quiet_seconds=args.quiet_seconds,
                poll_seconds=args.poll_seconds,
            )
        else:
            cleanup = inspect_satisfied_pending_votes(client, live, phase=phase)
        node_results.append(
            {
                "node": node,
                "rpc_url": url,
                "chain_id": chain_id,
                "live_validator_set": live,
                "cleanup": cleanup,
            }
        )

    payload: dict[str, Any] = {
        "kind": KIND,
        "mode": mode,
        "observed_at": _utc_now(),
        "chain_cleanup_only": True,
        "coolify_touched": False,
        "compose_touched": False,
        "docker_touched": False,
        "service_infrastructure_touched": False,
        "expected_chain_id": expected_chain_id,
        "committed_validator_addresses": sorted(vote_map),
        "quiet_seconds": args.quiet_seconds,
        "poll_seconds": args.poll_seconds,
        "nodes": node_results,
    }
    payload["summary"] = {
        "node_count": len(node_results),
        "cleanup_target_count": sum(len(item["cleanup"].get("cleanup_targets") or []) for item in node_results),
        "cleared_count": sum(len(item["cleanup"].get("cleared") or []) for item in node_results),
        "skipped_count": sum(len(item["cleanup"].get("skipped") or []) for item in node_results),
        "statuses": {item["node"]: item["cleanup"].get("status") for item in node_results},
    }
    evidence = _write_evidence(args.runtime_state_root if args.write_evidence else None, payload)
    if evidence is not None:
        payload["evidence"] = evidence
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone chain-only QBFT pending-vote cleanup")
    sub = parser.add_subparsers(dest="mode", required=True)
    for mode in ("inspect", "execute"):
        child = sub.add_parser(mode)
        child.add_argument("--rpc-url", action="append", required=True, help="node=http://rpc:8545; may be repeated")
        child.add_argument("--committed-validator-address", action="append", default=[], help="address or comma list; used for checksummed discard RPC payloads")
        child.add_argument("--expected-chain-id", type=int)
        child.add_argument("--phase", default="standalone-chain-cleanup")
        child.add_argument("--quiet-seconds", type=float, default=DEFAULT_QBFT_STALE_VOTE_QUIET_SECONDS)
        child.add_argument("--poll-seconds", type=float, default=DEFAULT_QBFT_STALE_VOTE_POLL_SECONDS)
        child.add_argument("--timeout", type=float, default=10.0)
        child.add_argument("--runtime-state-root")
        child.add_argument("--write-evidence", action="store_true")
        if mode == "execute":
            child.add_argument("--allow-discard-satisfied-pending-votes", action="store_true")
            child.add_argument("--acknowledge-chain-cleanup-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
        payload = run_chain_cleanup(args)
    except MotherChainCleanupError as exc:
        print(json.dumps({"kind": KIND, "status": "failed", "error": str(exc)}, sort_keys=True, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
