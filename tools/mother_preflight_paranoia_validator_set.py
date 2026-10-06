#!/usr/bin/env python3
"""Read-only paranoia check: canonical Mother validator set vs live QBFT.

The check is intentionally narrow:

* load one acknowledged current-topology evidence document;
* independently query the live shared QBFT RPC route;
* compare the exact validator-address sets;
* inventory exact live Coolify primary nodes and require them to match the live
  QBFT validator-node identities;
* emit a deterministic topology-reseal command only after that physical/live
  topology is internally consistent;
* never vote, restart, deploy, delete, patch Coolify, or write evidence.

A mismatch exits non-zero.  The emitted rectification command is explicit and
operator-triggered; this script never executes it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Mapping
import urllib.error
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import read_private_state
from tools.mother.common.coolify_state import list_coolify_controllers
from tools.mother.common.deployment_validator_rpc_canary_funding import _shared_rpc_route_url
from tools.mother_build_reseal_input import network_document, validator_for_node
from tools.mother_preflight_paranoia import _discover_current_topology_evidence
from tools.mother_detect_topology_v2 import ENDPOINTS, query_live_coolify_inventory


KIND = "main_computer.mother.preflight_paranoia_validator_set.v1"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ValidatorSetParanoiaError(RuntimeError):
    pass


def _operation(network: str) -> OperationIdentity:
    return OperationIdentity(
        operation_id=f"mother-preflight-paranoia-validator-set-{network}",
        request_id=f"mother-preflight-paranoia-validator-set-{network}-request",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def _normalize_address(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if ADDRESS_RE.fullmatch(text) is None:
        raise ValidatorSetParanoiaError(f"{label} is not a validator address: {value!r}")
    return text


def _normalize_validator_set(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ValidatorSetParanoiaError(f"{label} must be a list")
    result = [_normalize_address(item, label) for item in value]
    if len(set(result)) != len(result):
        raise ValidatorSetParanoiaError(f"{label} contains duplicate validator addresses")
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _topology_body(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("final_topology", "current_topology", "post_add_topology", "post_removal_topology"):
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def _load_topology(
    runtime_state_root: Path,
    *,
    network: str,
    topology_evidence: Path,
    acknowledged_sha256: str,
) -> tuple[Path, str, Mapping[str, Any], list[str], list[str]]:
    paths = MotherPaths(runtime_state_root=runtime_state_root)
    try:
        path = paths.validate_contained(topology_evidence)
    except (TypeError, ValueError) as exc:
        raise ValidatorSetParanoiaError(str(exc)) from exc
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except FileNotFoundError as exc:
        raise ValidatorSetParanoiaError(f"topology evidence does not exist: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidatorSetParanoiaError(f"topology evidence is not valid JSON: {path}") from exc
    if not isinstance(document, Mapping):
        raise ValidatorSetParanoiaError("topology evidence root must be an object")

    actual_sha = hashlib.sha256(raw).hexdigest()
    acknowledged = str(acknowledged_sha256 or "").strip().lower()
    if SHA256_RE.fullmatch(acknowledged) is None:
        raise ValidatorSetParanoiaError("acknowledged topology evidence SHA-256 is invalid")
    if actual_sha != acknowledged:
        raise ValidatorSetParanoiaError("acknowledged topology evidence SHA-256 does not match")
    if document.get("network") not in (None, network):
        raise ValidatorSetParanoiaError("topology evidence network does not match --network")

    topology = _topology_body(document)
    expected = _normalize_validator_set(topology.get("validator_set"), "topology validator_set")
    raw_nodes = topology.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ValidatorSetParanoiaError("topology nodes must be a list")
    nodes = [str(item or "").strip() for item in raw_nodes]
    if any(not item for item in nodes) or len(set(nodes)) != len(nodes):
        raise ValidatorSetParanoiaError("topology nodes contains an empty or duplicate node")
    return path, actual_sha, document, nodes, expected


def _rpc(
    url: str,
    method: str,
    params: list[Any],
    *,
    timeout: float,
    max_response_bytes: int,
    host_header: str | None = None,
) -> Any:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "main-computer-mother-validator-rpc-route-preflight/1",
            **({"Host": host_header} if host_header else {}),
        },
        method="POST",
    )
    transient_http_statuses = {502, 503, 504}
    retry_delays = (0.0, 0.5, 1.0, 2.0)
    last_exc: Exception | None = None
    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(max_response_bytes + 1)
            break
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in transient_http_statuses or attempt == len(retry_delays):
                raise ValidatorSetParanoiaError(
                    f"live RPC {method} failed at {url} after {attempt} attempt(s): {exc}"
                ) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_exc = exc
            if attempt == len(retry_delays):
                raise ValidatorSetParanoiaError(
                    f"live RPC {method} failed at {url} after {attempt} attempt(s): {exc}"
                ) from exc
    else:  # pragma: no cover - loop always returns or raises
        raise ValidatorSetParanoiaError(
            f"live RPC {method} failed at {url}: {last_exc}"
        )
    if len(raw) > max_response_bytes:
        raise ValidatorSetParanoiaError(f"live RPC {method} response exceeded --max-response-bytes")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidatorSetParanoiaError(f"live RPC {method} returned invalid JSON") from exc
    if not isinstance(payload, Mapping) or payload.get("error") is not None or "result" not in payload:
        raise ValidatorSetParanoiaError(f"live RPC {method} returned an error: {payload!r}")
    return payload["result"]



def _controller_local_rpc_candidates(private_state: Any, *, network: str, route_url: str) -> list[dict[str, str]]:
    """Return direct per-controller Traefik RPC routes, bypassing public DNS/load balancing."""
    parsed_route = urllib.parse.urlsplit(str(route_url or ""))
    route_host = parsed_route.hostname
    if not route_host:
        raise ValidatorSetParanoiaError(f"shared RPC route URL lacks a hostname: {route_url!r}")

    candidates: list[dict[str, str]] = []
    for controller in list_coolify_controllers(private_state):
        if controller.network != network or not controller.enabled:
            continue
        parsed = urllib.parse.urlsplit(str(controller.base_url or ""))
        host = parsed.hostname
        if not host:
            continue
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        candidates.append({
            "url": f"http://{url_host}/",
            "host_header": route_host,
            "source": "controller-local-traefik-rpc",
            "controller_id": controller.controller_id,
        })
    return candidates


def _observe_live_chain(
    private_state: Any,
    *,
    network: str,
    network_doc: Mapping[str, Any],
    explicit_rpc_url: str | None,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Observe live QBFT without making the public aggregate route a single point of failure."""
    public_url = _shared_rpc_route_url(network_doc)
    if explicit_rpc_url:
        candidates = [{"url": explicit_rpc_url, "source": "explicit", "host_header": ""}]
    else:
        candidates = _controller_local_rpc_candidates(private_state, network=network, route_url=public_url)

    successes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for candidate in candidates:
        url = candidate["url"]
        host_header = candidate.get("host_header") or None
        try:
            chain_raw = _rpc(
                url, "eth_chainId", [], timeout=timeout, max_response_bytes=max_response_bytes,
                host_header=host_header,
            )
            chain_id = int(str(chain_raw), 16)
            validators = _normalize_validator_set(
                _rpc(
                    url, "qbft_getValidatorsByBlockNumber", ["latest"],
                    timeout=timeout, max_response_bytes=max_response_bytes, host_header=host_header,
                ),
                "live QBFT validator set",
            )
            block_number = _rpc(
                url, "eth_blockNumber", [], timeout=timeout, max_response_bytes=max_response_bytes,
                host_header=host_header,
            )
            successes.append({**candidate, "chain_id": chain_id, "validators": validators, "block_number": block_number})
        except (ValidatorSetParanoiaError, TypeError, ValueError) as exc:
            failures.append({**candidate, "error": str(exc)})

    # If no controller-local route answered, retain the public aggregate route as a final fallback.
    if not successes and not explicit_rpc_url:
        try:
            chain_raw = _rpc(public_url, "eth_chainId", [], timeout=timeout, max_response_bytes=max_response_bytes)
            chain_id = int(str(chain_raw), 16)
            validators = _normalize_validator_set(
                _rpc(
                    public_url, "qbft_getValidatorsByBlockNumber", ["latest"],
                    timeout=timeout, max_response_bytes=max_response_bytes,
                ),
                "live QBFT validator set",
            )
            block_number = _rpc(
                public_url, "eth_blockNumber", [], timeout=timeout, max_response_bytes=max_response_bytes,
            )
            successes.append({
                "url": public_url,
                "source": "public-shared-rpc-fallback",
                "host_header": "",
                "chain_id": chain_id,
                "validators": validators,
                "block_number": block_number,
            })
        except (ValidatorSetParanoiaError, TypeError, ValueError) as exc:
            failures.append({"url": public_url, "source": "public-shared-rpc-fallback", "error": str(exc)})

    if not successes:
        detail = "; ".join(
            f"{item.get('source')}[{item.get('controller_id', '-')}] {item.get('url')}: {item.get('error')}"
            for item in failures
        )
        raise ValidatorSetParanoiaError(f"no trusted live QBFT RPC observation succeeded: {detail}")

    first = successes[0]
    first_set = set(first["validators"])
    for observation in successes[1:]:
        if observation["chain_id"] != first["chain_id"]:
            raise ValidatorSetParanoiaError(
                "trusted live QBFT RPC observations disagree on chain_id: "
                + ", ".join(f"{item.get('source')}[{item.get('controller_id', '-')}]={item['chain_id']}" for item in successes)
            )
        if set(observation["validators"]) != first_set:
            raise ValidatorSetParanoiaError(
                "trusted live QBFT RPC observations disagree on validator membership"
            )

    return {
        "chain_id": first["chain_id"],
        "validators": first["validators"],
        "block_number": first["block_number"],
        "authority_url": first["url"],
        "authority_source": first["source"],
        "authority_controller_id": first.get("controller_id"),
        "observations": successes,
        "failures": failures,
        "public_rpc_url": public_url,
    }

def _validator_node_map(network_doc: Mapping[str, Any], *, network: str) -> dict[str, str]:
    raw_nodes = network_doc.get("nodes")
    if not isinstance(raw_nodes, Mapping):
        raise ValidatorSetParanoiaError(f"network {network!r} has no node mapping in Mother private state")
    result: dict[str, str] = {}
    for raw_node in raw_nodes:
        node = str(raw_node)
        try:
            address = validator_for_node(network_doc, network, node)
        except Exception:
            continue
        previous = result.get(address)
        if previous is not None and previous != node:
            raise ValidatorSetParanoiaError(
                f"validator address {address} maps to multiple Mother nodes: {previous}, {node}"
            )
        result[address] = node
    return result


def _live_primary_nodes(
    private_state: Any,
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
    overall_timeout: float,
) -> list[str]:
    controllers = [
        controller
        for controller in list_coolify_controllers(private_state)
        if controller.network == network and controller.enabled
    ]
    if not controllers:
        raise ValidatorSetParanoiaError(
            f"no enabled Coolify controllers for network {network!r} in Mother private state"
        )

    probes, timed_out = query_live_coolify_inventory(
        controllers,
        network=network,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        overall_timeout=overall_timeout,
    )
    expected_probe_keys = {
        (controller.controller_id, label)
        for controller in controllers
        for label, _path in ENDPOINTS
    }
    observed_probe_keys = {(probe.controller_id, probe.endpoint_label) for probe in probes}
    missing = sorted(expected_probe_keys - observed_probe_keys)
    failed = [probe for probe in probes if not probe.ok]
    if timed_out or missing or failed:
        details = []
        if timed_out:
            details.append("overall timeout")
        if missing:
            details.append(
                "missing probes=" + ",".join(f"{controller}/{label}" for controller, label in missing)
            )
        if failed:
            details.append(
                "failed probes="
                + ",".join(
                    f"{probe.controller_id}/{probe.endpoint_label}:{probe.error_code or probe.status}"
                    for probe in failed
                )
            )
        raise ValidatorSetParanoiaError(
            "live Coolify inventory is incomplete; refusing validator-topology remediation: "
            + "; ".join(details)
        )

    return sorted({node for probe in probes for node in probe.exact_service_nodes})


def _classify_remediation(
    *,
    validator_set_matches: bool,
    live_validator_nodes: list[str | None],
    live_primary_nodes: list[str],
    unmapped_live_validators: list[str],
) -> dict[str, Any]:
    mapped_validator_nodes = [node for node in live_validator_nodes if node is not None]
    validator_node_set = set(mapped_validator_nodes)
    primary_node_set = set(live_primary_nodes)
    non_validator_primary_nodes = sorted(primary_node_set - validator_node_set)
    validator_nodes_missing_primary = sorted(validator_node_set - primary_node_set)

    if unmapped_live_validators:
        status = "blocked-by-unmapped-live-validator"
        reseal_ready = False
    elif non_validator_primary_nodes:
        status = "blocked-by-non-validator-primary-nodes"
        reseal_ready = False
    elif validator_nodes_missing_primary:
        status = "blocked-by-validator-without-primary-service"
        reseal_ready = False
    elif validator_set_matches:
        status = "clean"
        reseal_ready = False
    else:
        status = "ready-to-reseal"
        reseal_ready = True

    return {
        "status": status,
        "reseal_ready": reseal_ready,
        "non_validator_primary_nodes": non_validator_primary_nodes,
        "validator_nodes_missing_primary": validator_nodes_missing_primary,
    }




def _powershell_command(argv: list[str]) -> str:
    """Render argv as a copy/paste-safe PowerShell command."""
    def quote(token: str) -> str:
        token = str(token)
        if token and all(ch not in token for ch in " \t\r\n'`$&|;(){}[]<>\""):
            return token
        return "'" + token.replace("'", "''") + "'"

    return " ".join(quote(token) for token in argv)

def _manual_cleanup_command(
    *,
    python_executable: str,
    runtime_state_root: Path,
    network: str,
    nodes: list[str],
    rpc_url: str | None,
    timeout: float,
    max_response_bytes: int,
) -> list[str]:
    argv = [
        python_executable,
        str(REPO_ROOT / "tools" / "mother_delete_non_validator_primary_nodes.py"),
        "--runtime-state-root", str(runtime_state_root),
        "--network", network,
    ]
    for node in nodes:
        argv.extend(["--node", node])
    if rpc_url:
        argv.extend(["--rpc-url", rpc_url])
    argv.extend([
        "--timeout", str(timeout),
        "--max-response-bytes", str(max_response_bytes),
        "--execute",
        "--yes-i-know-this-deletes-live-node-services",
    ])
    return argv


def _rectification_command(
    *,
    python_executable: str,
    runtime_state_root: Path,
    network: str,
    topology_path: Path,
    topology_sha256: str,
    live_validators: list[str],
    validator_to_node: Mapping[str, str],
    timeout: float,
    max_response_bytes: int,
) -> list[str] | None:
    live_nodes: list[str] = []
    for address in live_validators:
        node = validator_to_node.get(address)
        if not node:
            return None
        live_nodes.append(node)
    argv = [
        python_executable,
        str(REPO_ROOT / "tools" / "mother_deploy.py"),
        "seal-live-current-topology",
        "--network", network,
        "--runtime-state-root", str(runtime_state_root),
        "--topology-evidence", str(topology_path),
        "--acknowledge-topology-evidence-sha256", topology_sha256,
    ]
    for node in live_nodes:
        argv.extend(["--actual-node", node])
    argv.extend([
        "--timeout", str(timeout),
        "--max-response-bytes", str(max_response_bytes),
        "--use-live-topology",
        "--write-evidence",
    ])
    return argv


def _resolve_topology_arguments(args: argparse.Namespace) -> tuple[Path, str, bool]:
    runtime_state_root = Path(args.runtime_state_root).resolve(strict=False)
    explicit = bool(args.topology_evidence)
    if args.acknowledge_topology_evidence_sha256 and not explicit:
        raise ValidatorSetParanoiaError(
            "--acknowledge-topology-evidence-sha256 was supplied without --topology-evidence"
        )

    if explicit:
        topology_path = Path(args.topology_evidence)
    else:
        try:
            topology_path = _discover_current_topology_evidence(
                runtime_state_root, network=args.network
            )
        except Exception as exc:
            raise ValidatorSetParanoiaError(
                f"could not auto-select current topology evidence: {exc}"
            ) from exc

    try:
        resolved = MotherPaths(runtime_state_root=runtime_state_root).validate_contained(topology_path)
    except (TypeError, ValueError) as exc:
        raise ValidatorSetParanoiaError(str(exc)) from exc
    if not resolved.is_file():
        raise ValidatorSetParanoiaError(f"topology evidence does not exist: {resolved}")

    actual_sha = _file_sha256(resolved)
    acknowledged = str(args.acknowledge_topology_evidence_sha256 or "").strip().lower()
    if acknowledged:
        if SHA256_RE.fullmatch(acknowledged) is None:
            raise ValidatorSetParanoiaError("acknowledged topology evidence SHA-256 is invalid")
        if acknowledged != actual_sha:
            raise ValidatorSetParanoiaError("acknowledged topology evidence SHA-256 does not match")
    else:
        acknowledged = actual_sha

    return resolved, acknowledged, not explicit


def run(args: argparse.Namespace) -> dict[str, Any]:
    runtime_state_root = Path(args.runtime_state_root).resolve(strict=False)
    selected_topology_path, selected_topology_sha256, auto_selected = _resolve_topology_arguments(args)
    topology_path, topology_sha256, _document, topology_nodes, expected = _load_topology(
        runtime_state_root,
        network=args.network,
        topology_evidence=selected_topology_path,
        acknowledged_sha256=selected_topology_sha256,
    )

    private_paths = MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()
    private_state = read_private_state(private_paths, operation=_operation(args.network))
    network_doc = network_document(private_state, args.network)
    expected_chain_id = network_doc.get("chain_id")
    if not isinstance(expected_chain_id, int) or expected_chain_id <= 0:
        raise ValidatorSetParanoiaError("Mother private state does not contain a valid chain_id")

    chain_observation = _observe_live_chain(
        private_state,
        network=args.network,
        network_doc=network_doc,
        explicit_rpc_url=args.rpc_url,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
    )
    live_chain_id = int(chain_observation["chain_id"])
    if live_chain_id != expected_chain_id:
        raise ValidatorSetParanoiaError(
            f"live chain_id {live_chain_id} does not match Mother chain_id {expected_chain_id}"
        )
    live = list(chain_observation["validators"])
    block_number = chain_observation["block_number"]
    rpc_url = str(chain_observation["authority_url"])

    expected_set = set(expected)
    live_set = set(live)
    matches = expected_set == live_set
    missing_from_live = [address for address in expected if address not in live_set]
    unexpected_live = [address for address in live if address not in expected_set]

    validator_to_node = _validator_node_map(network_doc, network=args.network)
    live_nodes = [validator_to_node.get(address) for address in live]
    unmapped_live_validators = [
        address for address, node in zip(live, live_nodes) if node is None
    ]
    live_primary_nodes = _live_primary_nodes(
        private_state,
        network=args.network,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        overall_timeout=args.overall_timeout,
    )
    remediation_state = _classify_remediation(
        validator_set_matches=matches,
        live_validator_nodes=live_nodes,
        live_primary_nodes=live_primary_nodes,
        unmapped_live_validators=unmapped_live_validators,
    )

    remediation = None
    if remediation_state["reseal_ready"]:
        remediation = _rectification_command(
            python_executable=sys.executable,
            runtime_state_root=runtime_state_root,
            network=args.network,
            topology_path=topology_path,
            topology_sha256=topology_sha256,
            live_validators=live,
            validator_to_node=validator_to_node,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )

    manual_cleanup = None
    if (
        remediation_state["status"] == "blocked-by-non-validator-primary-nodes"
        and not unmapped_live_validators
        and not remediation_state["validator_nodes_missing_primary"]
    ):
        manual_cleanup = _manual_cleanup_command(
            python_executable=sys.executable,
            runtime_state_root=runtime_state_root,
            network=args.network,
            nodes=remediation_state["non_validator_primary_nodes"],
            rpc_url=args.rpc_url,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )

    clean = matches and remediation_state["status"] == "clean"
    return {
        "kind": KIND,
        "schema_version": 1,
        "status": "pass" if clean else remediation_state["status"],
        "read_only": True,
        "network": args.network,
        "rpc_url": rpc_url,
        "rpc_authority_source": chain_observation["authority_source"],
        "rpc_authority_controller_id": chain_observation["authority_controller_id"],
        "rpc_observations": chain_observation["observations"],
        "rpc_failures": chain_observation["failures"],
        "public_rpc_url": chain_observation["public_rpc_url"],
        "chain_id": live_chain_id,
        "observed_block_number": block_number,
        "topology_evidence": {
            "path": str(topology_path),
            "sha256": topology_sha256,
            "nodes": topology_nodes,
            "auto_selected": auto_selected,
        },
        "expected_validator_set": expected,
        "live_validator_set": live,
        "missing_from_live": missing_from_live,
        "unexpected_live": unexpected_live,
        "live_validator_nodes": live_nodes,
        "live_primary_nodes": live_primary_nodes,
        "non_validator_primary_nodes": remediation_state["non_validator_primary_nodes"],
        "validator_nodes_missing_primary": remediation_state["validator_nodes_missing_primary"],
        "unmapped_live_validators": unmapped_live_validators,
        "validator_set_matches": matches,
        "rectification_status": remediation_state["status"],
        "reseal_ready": remediation_state["reseal_ready"],
        "manual_cleanup_command_argv": manual_cleanup,
        "manual_cleanup_command": _powershell_command(manual_cleanup) if manual_cleanup else None,
        "rectification_command_argv": remediation,
        "rectification_command": subprocess.list2cmdline(remediation) if remediation else None,
        "summary": {
            "clean": clean,
            "validator_set_matches": matches,
            "expected_validator_count": len(expected),
            "live_validator_count": len(live),
            "missing_from_live_count": len(missing_from_live),
            "unexpected_live_count": len(unexpected_live),
            "rectification_command_emitted": remediation is not None,
            "manual_cleanup_command_emitted": manual_cleanup is not None,
            "rectification_blocked_by_unmapped_live_validator": bool(unmapped_live_validators),
            "rectification_blocked_by_non_validator_primary_nodes": bool(remediation_state["non_validator_primary_nodes"]),
            "rectification_blocked_by_validator_without_primary_service": bool(remediation_state["validator_nodes_missing_primary"]),
            "live_mutation_performed": False,
            "evidence_mutation_performed": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only check that Mother's expected validator set exactly matches live QBFT.",
        allow_abbrev=False,
    )
    parser.add_argument("--runtime-state-root", default=str(Path("runtime") / "state"))
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--topology-evidence", help="optional; defaults to latest passed, clean, complete current topology evidence on disk")
    parser.add_argument("--acknowledge-topology-evidence-sha256", help="optional; computed automatically when omitted")
    parser.add_argument(
        "--rpc-url",
        help="override the Mother shared RPC URL; primarily for diagnostics/tests",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--overall-timeout", type=float, default=60.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    explicit_topology = bool(args.topology_evidence)
    explicit_sha = bool(args.acknowledge_topology_evidence_sha256)
    try:
        result = run(args)
    except ValidatorSetParanoiaError as exc:
        print(json.dumps({
            "kind": KIND,
            "status": "failed",
            "read_only": True,
            "error": str(exc),
            "live_mutation_performed": False,
            "evidence_mutation_performed": False,
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 2

    topology = result.get("topology_evidence", {})
    if topology.get("auto_selected") is True:
        print("MOTHER_PREFLIGHT_PARANOIA_VALIDATOR_SET_AUTO_TOPOLOGY: latest current topology evidence")
        print(f"topology_evidence={topology.get('path')}")
        print(f"topology_evidence_sha256={topology.get('sha256')}")
    elif explicit_topology and not explicit_sha:
        print(f"MOTHER_PREFLIGHT_PARANOIA_VALIDATOR_SET_AUTO_SHA256: {topology.get('sha256')}")
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["status"] != "pass":
        print("\nMOTHER_PREFLIGHT_PARANOIA_VALIDATOR_SET_BLOCKED", file=sys.stderr)
        if not result.get("validator_set_matches"):
            print("Mother's canonical validator set does not match live QBFT.", file=sys.stderr)

        rectification_status = result.get("rectification_status")
        if rectification_status == "blocked-by-non-validator-primary-nodes":
            print(
                "\nManual remediation is required before evidence can be resealed.",
                file=sys.stderr,
            )
            print(
                "These exact live Coolify primary services are not live QBFT validators and must "
                "be reviewed and removed/decommissioned if they are stale:",
                file=sys.stderr,
            )
            for node in result.get("non_validator_primary_nodes") or []:
                print(f"  - {node}", file=sys.stderr)
            cleanup_command = result.get("manual_cleanup_command")
            if cleanup_command:
                print(
                    "\nTo perform that manual cleanup with the guarded deletion tool, run:",
                    file=sys.stderr,
                )
                print(cleanup_command, file=sys.stderr)
            print(
                "\nAfter manual service cleanup, rerun this same paranoia command. "
                "It will emit the evidence-reseal command only when the live primary-node set "
                "exactly matches the live QBFT validator-node set.",
                file=sys.stderr,
            )
        elif rectification_status == "blocked-by-validator-without-primary-service":
            print(
                "\nManual investigation is required: live QBFT contains validator nodes with no "
                "matching exact live Coolify primary service:",
                file=sys.stderr,
            )
            for node in result.get("validator_nodes_missing_primary") or []:
                print(f"  - {node}", file=sys.stderr)
        elif rectification_status == "blocked-by-unmapped-live-validator":
            print(
                "\nNo evidence rectification is safe because at least one live validator does not "
                "map to a known Mother node identity.",
                file=sys.stderr,
            )
        else:
            command = result.get("rectification_command")
            if command:
                print(
                    "\nLive primary services exactly match live QBFT validator nodes. "
                    "After reviewing the mismatch, rectify Mother evidence with:",
                    file=sys.stderr,
                )
                print(command, file=sys.stderr)
            else:
                print("\nNo safe rectification command is available.", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
