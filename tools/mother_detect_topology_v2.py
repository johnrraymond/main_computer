#!/usr/bin/env python3
"""Live-first Mother topology detector.

This script is intentionally narrow:

1. Load the Mother private state from --runtime-state-root.
2. Use that state to resolve every enabled Coolify controller for --network.
3. Ask those Coolify controllers what they currently have.
4. Only after live Coolify inventory completes, look at the latest local topology
   evidence JSON and compare it to the live shape.
5. If the shapes differ, print the concrete Mother command that can seal the
   observed live shape.

It does not discover Coolify controllers from environment variables, broad disk
greps, or old topology target metadata.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (  # noqa: E402
    CoolifyObservationError,
    CoolifyController,
    get_coolify_json,
    list_coolify_controllers,
)
from tools.mother.common.models import OperationIdentity  # noqa: E402
from tools.mother.common.paths import MotherPaths  # noqa: E402
from tools.mother.common.private_state import read_private_state  # noqa: E402


ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("services", "/api/v1/services"),
    ("applications", "/api/v1/applications"),
    ("resources", "/api/v1/resources"),
    ("projects", "/api/v1/projects"),
    ("servers", "/api/v1/servers"),
)

RESOURCE_NAME_KEYS = (
    "name",
    "resourceName",
    "service",
    "service_name",
    "serviceName",
    "fqdn",
    "domain",
    "description",
    "uuid",
    "id",
    "service_uuid",
    "application_uuid",
)

STATUS_KEYS = (
    "status",
    "human_status",
    "state",
    "health",
    "health_status",
    "application_status",
    "service_status",
    "docker_status",
)

TOPOLOGY_KEYS = (
    "final_topology",
    "current_topology",
    "post_add_topology",
    "post_removal_topology",
    "pre_removal_topology",
)


@dataclass(frozen=True)
class EndpointProbe:
    controller_id: str
    endpoint_label: str
    path: str
    ok: bool
    status: int | None
    item_count: int
    network_match_count: int
    live_node_hints: list[str]
    exact_service_nodes: list[str]
    matches: list[dict[str, Any]]
    error_code: str | None
    error: str | None
    elapsed_ms: int


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def log(message: str, **fields: Any) -> None:
    print(
        f"[mother-detect-topology-v2 {timestamp()}] {message} "
        f"{json.dumps(_redact(fields), sort_keys=True, default=str)}",
        file=sys.stderr,
        flush=True,
    )


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if "api_token" in lowered or "token=" in lowered or "bearer " in lowered:
            return "<redacted>"
        return value
    return value


def operation_identity(network: str) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=f"mother-detect-topology-v2-{stamp}",
        request_id=f"mother-detect-topology-v2-{stamp}",
        network=network,
        operation_kind="MOTHER-OP-DIAGNOSE",
    )


def object_text(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False).lower()
    except Exception:
        return repr(value).lower()


def node_regex(network: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(network)}[a-z0-9_-]*-super\d+\b", re.IGNORECASE)


def flatten_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def rec(item: Any) -> None:
        if isinstance(item, Mapping):
            found.append(dict(item))
            for child in item.values():
                rec(child)
        elif isinstance(item, list):
            for child in item:
                rec(child)

    rec(value)
    return found


def primary_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, Mapping):
        for key in ("services", "applications", "resources", "projects", "servers", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return list(value)
        if any(key in payload for key in ("uuid", "id", "name", "resourceName")):
            return [payload]
    return []


def pick_first_string(record: Mapping[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def pick_status(record: Mapping[str, Any]) -> str | None:
    parts: list[str] = []
    for key in STATUS_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(f"{key}={value.strip()}")
    return ", ".join(parts) if parts else None


def summarize_record(record: Mapping[str, Any], *, controller_id: str, endpoint_label: str, network: str) -> dict[str, Any]:
    text = object_text(record)
    names = sorted({match.group(0).replace("_", "-").lower() for match in node_regex(network).finditer(text)})
    return {
        "controller_id": controller_id,
        "endpoint": endpoint_label,
        "name": pick_first_string(record, RESOURCE_NAME_KEYS),
        "uuid": (
            record.get("uuid")
            or record.get("id")
            or record.get("service_uuid")
            or record.get("application_uuid")
        ),
        "fqdn": record.get("fqdn") or record.get("domain"),
        "status": pick_status(record),
        "type": record.get("type") or record.get("resource_type") or record.get("kind"),
        "node_hints": names,
    }


def is_terminal_status(record: Mapping[str, Any]) -> bool:
    status = (pick_status(record) or "").lower()
    return any(part in status for part in ("exited", "stopped", "dead", "removed", "deleted"))


def record_matches_network(record: Mapping[str, Any], network: str) -> bool:
    return network.lower() in object_text(record)


def record_exact_service_nodes(record: Mapping[str, Any], network: str) -> list[str]:
    rx = node_regex(network)
    names: set[str] = set()
    for key in ("name", "resourceName", "service", "service_name", "serviceName"):
        value = record.get(key)
        if isinstance(value, str):
            exact = value.strip().replace("_", "-").lower()
            if rx.fullmatch(exact):
                names.add(exact)
    return sorted(names)


def probe_endpoint(
    controller: CoolifyController,
    label: str,
    path: str,
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
) -> EndpointProbe:
    started = time.monotonic()
    log("coolify endpoint query started", controller_id=controller.controller_id, endpoint=label, path=path)

    try:
        observation = get_coolify_json(
            controller,
            path,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        items = primary_items(observation.payload)
        matches: list[dict[str, Any]] = []
        live_node_hints: set[str] = set()
        exact_service_nodes: set[str] = set()

        for item in items:
            if not isinstance(item, Mapping):
                continue
            if not record_matches_network(item, network):
                continue

            summary = summarize_record(
                item,
                controller_id=controller.controller_id,
                endpoint_label=label,
                network=network,
            )
            matches.append(summary)

            if not is_terminal_status(item):
                live_node_hints.update(str(node) for node in summary.get("node_hints", []) if node)
                exact_service_nodes.update(record_exact_service_nodes(item, network))

        elapsed_ms = int((time.monotonic() - started) * 1000)
        log(
            "coolify endpoint query finished",
            controller_id=controller.controller_id,
            endpoint=label,
            status=observation.status,
            ok=observation.ok,
            item_count=len(items),
            network_match_count=len(matches),
            live_node_hints=sorted(live_node_hints),
            elapsed_ms=elapsed_ms,
        )
        return EndpointProbe(
            controller_id=controller.controller_id,
            endpoint_label=label,
            path=path,
            ok=observation.ok,
            status=observation.status,
            item_count=len(items),
            network_match_count=len(matches),
            live_node_hints=sorted(live_node_hints),
            exact_service_nodes=sorted(exact_service_nodes),
            matches=matches[:200],
            error_code=None,
            error=None,
            elapsed_ms=elapsed_ms,
        )

    except CoolifyObservationError as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log(
            "coolify endpoint query failed",
            controller_id=controller.controller_id,
            endpoint=label,
            error_code=exc.code,
            error=str(exc),
            elapsed_ms=elapsed_ms,
        )
        return EndpointProbe(
            controller_id=controller.controller_id,
            endpoint_label=label,
            path=path,
            ok=False,
            status=None,
            item_count=0,
            network_match_count=0,
            live_node_hints=[],
            exact_service_nodes=[],
            matches=[],
            error_code=exc.code,
            error=str(exc),
            elapsed_ms=elapsed_ms,
        )


def query_live_coolify_inventory(
    controllers: list[CoolifyController],
    *,
    network: str,
    timeout: float,
    max_response_bytes: int,
    overall_timeout: float,
) -> tuple[list[EndpointProbe], bool]:
    log(
        "live Coolify inventory started",
        network=network,
        controller_count=len(controllers),
        controllers=[
            {
                "controller_id": controller.controller_id,
                "base_url": controller.base_url,
                "enabled": controller.enabled,
                "has_api_token": bool(controller.api_token.strip()),
                "project_name_hint": controller.project_name_hint,
            }
            for controller in controllers
        ],
        endpoints=[label for label, _path in ENDPOINTS],
    )

    probes: list[EndpointProbe] = []
    timed_out = False
    max_workers = max(1, min(16, len(controllers) * len(ENDPOINTS)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    futures = [
        executor.submit(
            probe_endpoint,
            controller,
            label,
            path,
            network=network,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        for controller in controllers
        for label, path in ENDPOINTS
    ]

    try:
        for future in concurrent.futures.as_completed(futures, timeout=overall_timeout):
            probes.append(future.result())
    except concurrent.futures.TimeoutError:
        timed_out = True
        log("live Coolify inventory overall timeout hit", overall_timeout=overall_timeout)
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    log(
        "live Coolify inventory finished",
        probe_count=len(probes),
        expected_probe_count=len(futures),
        timed_out=timed_out,
    )
    return probes, timed_out


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_json_file(path: Path, *, max_bytes: int) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def is_topology_evidence_candidate(document: Mapping[str, Any], network: str) -> bool:
    if document.get("network") != network:
        return False
    kind = str(document.get("kind") or "")
    if "topology" in kind:
        return True
    if any(isinstance(document.get(key), Mapping) for key in TOPOLOGY_KEYS):
        return True
    if isinstance(document.get("expected_nodes"), list) or isinstance(document.get("observed_live_node_hints"), list):
        return True
    return False


def latest_local_topology_evidence(
    paths: MotherPaths,
    *,
    network: str,
    explicit_path: str | None,
    max_bytes: int,
) -> dict[str, Any] | None:
    if explicit_path:
        path = Path(explicit_path).resolve(strict=False)
        log("local topology evidence explicit path selected", path=str(path))
        document = parse_json_file(path, max_bytes=max_bytes)
        if not isinstance(document, Mapping):
            return {
                "path": str(path),
                "error": "explicit topology evidence is not a readable JSON object",
            }
        return local_evidence_summary(path, document)

    evidence_root = paths.evidence_root
    log("local topology evidence lookup started", evidence_root=str(evidence_root))
    if not evidence_root.exists():
        log("local topology evidence lookup skipped: evidence root missing", evidence_root=str(evidence_root))
        return None

    candidates: list[tuple[float, Path, dict[str, Any]]] = []
    scanned = 0
    for path in evidence_root.rglob("*.json"):
        scanned += 1
        document = parse_json_file(path, max_bytes=max_bytes)
        if document is None:
            continue
        if not is_topology_evidence_candidate(document, network):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        candidates.append((mtime, path, document))

    log("local topology evidence lookup finished", scanned_json=scanned, candidate_count=len(candidates))
    if not candidates:
        return None

    _mtime, path, document = sorted(candidates, key=lambda item: (item[0], str(item[1])))[-1]
    return local_evidence_summary(path, document)


def topology_from_document(document: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in TOPOLOGY_KEYS:
        value = document.get(key)
        if isinstance(value, Mapping):
            return value
    return document


def nodes_from_topology_document(document: Mapping[str, Any], network: str) -> list[str]:
    topology = topology_from_document(document)
    found: set[str] = set()

    nodes = topology.get("nodes")
    if isinstance(nodes, list):
        for item in nodes:
            if isinstance(item, str) and node_regex(network).fullmatch(item.replace("_", "-").lower()):
                found.add(item.replace("_", "-").lower())

    services = topology.get("services")
    if isinstance(services, Mapping):
        for key in services:
            if isinstance(key, str) and node_regex(network).fullmatch(key.replace("_", "-").lower()):
                found.add(key.replace("_", "-").lower())

    for key in ("expected_nodes", "observed_live_node_hints", "present_expected_nodes"):
        values = document.get(key)
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str) and node_regex(network).fullmatch(item.replace("_", "-").lower()):
                    found.add(item.replace("_", "-").lower())

    return sorted(found)


def local_evidence_summary(path: Path, document: Mapping[str, Any]) -> dict[str, Any]:
    digest = file_sha256(path)
    network = str(document.get("network") or "")
    nodes = nodes_from_topology_document(document, network) if network else []
    return {
        "path": str(path),
        "sha256": digest,
        "kind": document.get("kind"),
        "network": document.get("network"),
        "completed_at": document.get("completed_at") or document.get("observed_at"),
        "next_phase": document.get("next_phase"),
        "nodes": nodes,
        "node_count": len(nodes),
    }


def build_command(
    *,
    subcommand: str,
    runtime_state_root: str,
    network: str,
    topology_evidence_path: str,
    topology_evidence_sha256: str,
    actual_nodes: list[str],
    timeout: float,
    max_response_bytes: int,
    max_age_seconds: int,
) -> list[str]:
    command = [
        sys.executable,
        str(REPO_ROOT / "tools" / "mother_deploy.py"),
        subcommand,
        "--network",
        network,
        "--runtime-state-root",
        runtime_state_root,
        "--topology-evidence",
        topology_evidence_path,
        "--acknowledge-topology-evidence-sha256",
        topology_evidence_sha256,
    ]

    if subcommand == "seal-live-current-topology":
        for node in actual_nodes:
            command.extend(["--actual-node", node])
        command.extend(["--use-live-topology"])

    if subcommand == "adopt-fresh-empty-topology":
        command.extend(["--fresh-chain-reset"])

    command.extend(
        [
            "--max-age-seconds",
            str(max_age_seconds),
            "--timeout",
            str(timeout),
            "--max-response-bytes",
            str(max_response_bytes),
            "--write-evidence",
        ]
    )
    return command


def powershell_command(argv: list[str]) -> str:
    def quote(arg: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_./:\\=-]+", arg):
            return arg
        return "'" + arg.replace("'", "''") + "'"

    return " ".join(quote(str(arg)) for arg in argv)


def compare_shapes(live_nodes: list[str], local_evidence: dict[str, Any] | None) -> dict[str, Any]:
    local_nodes = local_evidence.get("nodes", []) if isinstance(local_evidence, dict) else []
    if not isinstance(local_nodes, list):
        local_nodes = []
    live_set = set(live_nodes)
    local_set = set(str(item) for item in local_nodes)
    return {
        "local_evidence_found": isinstance(local_evidence, dict) and not local_evidence.get("error"),
        "live_nodes": live_nodes,
        "local_nodes": sorted(local_set),
        "same_node_set": live_set == local_set,
        "added_live_nodes": sorted(live_set - local_set),
        "missing_live_nodes": sorted(local_set - live_set),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Live-first Mother/Coolify topology detector v2")
    parser.add_argument("--runtime-state-root", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--overall-timeout", type=float, default=60.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    parser.add_argument("--max-local-evidence-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--max-age-seconds", type=int, default=86400)
    parser.add_argument("--topology-evidence", default=None, help="Optional explicit local topology evidence to compare after live inventory.")
    parser.add_argument("--skip-local-compare", action="store_true")
    parser.add_argument("--require-current", action="store_true", help="Exit nonzero when live Coolify shape differs from local evidence.")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    started = time.monotonic()
    log(
        "detector started",
        runtime_state_root=args.runtime_state_root,
        network=args.network,
        timeout=args.timeout,
        overall_timeout=args.overall_timeout,
    )

    paths = MotherPaths(runtime_state_root=Path(args.runtime_state_root))
    log("Mother paths resolved", mother_root=str(paths.root))

    operation = operation_identity(args.network)
    log("Mother private state load started", operation_id=operation.operation_id)
    private_state = read_private_state(paths.resolve_private_state_paths(), operation=operation)
    log(
        "Mother private state loaded",
        generation=private_state.binding.generation,
        content_sha256=private_state.binding.content_hash.digest,
        manifest_sha256=private_state.binding.recovery_manifest_hash.digest,
    )

    all_controllers = list(list_coolify_controllers(private_state))
    controllers = [
        controller
        for controller in all_controllers
        if controller.network == args.network and controller.enabled
    ]
    log(
        "Coolify controllers resolved from Mother private state",
        network=args.network,
        total_controller_count=len(all_controllers),
        enabled_network_controller_count=len(controllers),
        controllers=[
            {
                "controller_id": controller.controller_id,
                "base_url": controller.base_url,
                "enabled": controller.enabled,
                "has_api_token": bool(controller.api_token.strip()),
                "project_name_hint": controller.project_name_hint,
            }
            for controller in controllers
        ],
    )

    if not controllers:
        result = {
            "kind": "main_computer.mother.coolify_live_network_topology.v2",
            "schema_version": 2,
            "observed_at": timestamp(),
            "network": args.network,
            "status": "fail",
            "fail_closed": True,
            "reasons": ["no enabled Coolify controllers for this network were found in Mother private state"],
            "authority": {
                "mother_private_state_loaded": True,
                "coolify_api_queried": False,
                "local_topology_checked_after_live_inventory": False,
                "live_mutation_performed": False,
                "read_only_detection": True,
                "secrets_in_output": False,
            },
        }
        text = json.dumps(result, indent=2, sort_keys=True)
        print(text)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
        return 1

    probes, timed_out = query_live_coolify_inventory(
        controllers,
        network=args.network,
        timeout=args.timeout,
        max_response_bytes=args.max_response_bytes,
        overall_timeout=args.overall_timeout,
    )

    expected_probe_keys = {(controller.controller_id, label) for controller in controllers for label, _path in ENDPOINTS}
    observed_probe_keys = {(probe.controller_id, probe.endpoint_label) for probe in probes}
    missing_probe_keys = sorted(f"{controller}/{label}" for controller, label in expected_probe_keys - observed_probe_keys)
    failed_probe_keys = sorted(f"{probe.controller_id}/{probe.endpoint_label}:{probe.error_code or probe.status}" for probe in probes if not probe.ok)

    live_node_hints = sorted({node for probe in probes for node in probe.live_node_hints})
    exact_service_nodes = sorted({node for probe in probes for node in probe.exact_service_nodes})
    matches_by_controller: dict[str, list[dict[str, Any]]] = {controller.controller_id: [] for controller in controllers}
    for probe in probes:
        matches_by_controller.setdefault(probe.controller_id, []).extend(probe.matches)

    log(
        "live topology shape derived from Coolify",
        live_node_hints=live_node_hints,
        exact_service_nodes=exact_service_nodes,
        failed_probe_count=len(failed_probe_keys),
        missing_probe_count=len(missing_probe_keys),
    )

    local_evidence = None
    if not args.skip_local_compare:
        log("local topology comparison starting after live Coolify inventory")
        local_evidence = latest_local_topology_evidence(
            paths,
            network=args.network,
            explicit_path=args.topology_evidence,
            max_bytes=args.max_local_evidence_bytes,
        )
        log("local topology comparison input selected", local_evidence=local_evidence)
    else:
        log("local topology comparison skipped by flag")

    topology_diff = compare_shapes(live_node_hints, local_evidence)

    recommended_commands: dict[str, Any] = {}
    if isinstance(local_evidence, dict) and local_evidence.get("path") and local_evidence.get("sha256") and not local_evidence.get("error"):
        if live_node_hints and not topology_diff["same_node_set"]:
            argv = build_command(
                subcommand="seal-live-current-topology",
                runtime_state_root=args.runtime_state_root,
                network=args.network,
                topology_evidence_path=str(local_evidence["path"]),
                topology_evidence_sha256=str(local_evidence["sha256"]),
                actual_nodes=live_node_hints,
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                max_age_seconds=args.max_age_seconds,
            )
            recommended_commands["seal_live_current_topology"] = {
                "why": "live Coolify contains a non-empty topology that differs from the latest local topology evidence",
                "argv": argv,
                "powershell": powershell_command(argv),
            }
        elif not live_node_hints and not topology_diff["same_node_set"]:
            argv = build_command(
                subcommand="adopt-fresh-empty-topology",
                runtime_state_root=args.runtime_state_root,
                network=args.network,
                topology_evidence_path=str(local_evidence["path"]),
                topology_evidence_sha256=str(local_evidence["sha256"]),
                actual_nodes=[],
                timeout=args.timeout,
                max_response_bytes=args.max_response_bytes,
                max_age_seconds=args.max_age_seconds,
            )
            recommended_commands["adopt_fresh_empty_topology"] = {
                "why": "live Coolify contains no super-node services, while local topology evidence still differs",
                "argv": argv,
                "powershell": powershell_command(argv),
            }

    inventory_complete = not timed_out and not missing_probe_keys and not failed_probe_keys
    if not inventory_complete:
        status = "fail"
        reasons = ["live Coolify inventory coverage was incomplete"]
    elif not topology_diff.get("local_evidence_found") and not args.skip_local_compare:
        status = "pass"
        reasons = ["live Coolify inventory completed; no local topology evidence was available to compare"]
    elif not topology_diff["same_node_set"] and not args.skip_local_compare:
        status = "stale"
        reasons = ["live Coolify topology differs from latest local topology evidence"]
    else:
        status = "pass"
        reasons = ["live Coolify topology matches latest local topology evidence" if not args.skip_local_compare else "live Coolify inventory completed"]

    result = {
        "kind": "main_computer.mother.coolify_live_network_topology.v2",
        "schema_version": 2,
        "observed_at": timestamp(),
        "network": args.network,
        "status": status,
        "fail_closed": status == "fail",
        "reasons": reasons,
        "authority": {
            "mother_private_state_loaded": True,
            "coolify_controller_source": "Mother private state networks[].coolify.controllers",
            "coolify_api_queried_before_local_topology_compare": True,
            "local_topology_checked_after_live_inventory": not args.skip_local_compare,
            "local_expected_nodes_used_for_live_verdict": False,
            "live_mutation_performed": False,
            "read_only_detection": True,
            "secrets_in_output": False,
        },
        "mother_binding": {
            "content_sha256": private_state.binding.content_hash.digest,
            "generation": private_state.binding.generation,
            "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
        },
        "summary": {
            "controller_count": len(controllers),
            "inventory_complete": inventory_complete,
            "live_node_count": len(live_node_hints),
            "live_nodes": live_node_hints,
            "exact_service_nodes": exact_service_nodes,
            "network_present_in_coolify": bool(live_node_hints or any(matches_by_controller.values())),
            "local_evidence_found": topology_diff.get("local_evidence_found"),
            "local_nodes": topology_diff.get("local_nodes"),
            "topology_differs_from_local": not topology_diff["same_node_set"] if topology_diff.get("local_evidence_found") else None,
            "recommended_command_available": bool(recommended_commands),
        },
        "controllers": [
            {
                "controller_id": controller.controller_id,
                "base_url": controller.base_url,
                "enabled": controller.enabled,
                "has_api_token": bool(controller.api_token.strip()),
                "project_name_hint": controller.project_name_hint,
            }
            for controller in controllers
        ],
        "coverage": {
            "expected_probe_count": len(expected_probe_keys),
            "observed_probe_count": len(observed_probe_keys),
            "timed_out": timed_out,
            "missing_probes": missing_probe_keys,
            "failed_probes": failed_probe_keys,
        },
        "live_topology": {
            "nodes": live_node_hints,
            "exact_service_nodes": exact_service_nodes,
            "matches_by_controller": matches_by_controller,
        },
        "local_topology_evidence": local_evidence,
        "topology_diff": topology_diff,
        "recommended_commands": recommended_commands,
        "endpoint_probes": [asdict(probe) for probe in sorted(probes, key=lambda item: (item.controller_id, item.endpoint_label))],
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }

    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        log("wrote output", output=str(output))

    log(
        "detector finished",
        status=status,
        inventory_complete=inventory_complete,
        live_nodes=live_node_hints,
        recommended_command_available=bool(recommended_commands),
    )

    if status == "fail":
        return 1
    if args.require_current and status == "stale":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
