#!/usr/bin/env python3
"""Build an out-of-band topology input for the existing Mother reseal consumer.

This script deliberately does not modify any existing Mother state-machine code.
It writes a normal, consumer-compatible topology evidence document under
``runtime/state/mother/reseal-inputs`` -- outside ``mother/evidence`` and therefore
outside the mutate harness' automatic baseline discovery paths.

The generated file is intended to be passed explicitly to the existing
``mother_deploy.py seal-live-current-topology`` command.  Nothing in this script
seals topology, mutates Coolify, allocates validator routes, creates identities,
or promotes the generated file into canonical evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common import atomic_files
from tools.mother.common.canonical import canonical_json
from tools.mother.common.coolify_state import (
    CoolifyObservationError,
    get_coolify_json,
    list_coolify_controllers,
)
from tools.mother.common.deployment_validator_routes import (
    MotherDeploymentValidatorRouteError,
    controller_validator_host,
    normalize_validator_route_bindings,
    validator_route_from_record,
    validator_routes_same,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.live_current_topology_evidence.v1"
OUTPUT_SUBDIR = ("mother", "reseal-inputs")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")


class ResealInputError(RuntimeError):
    pass


def fail(message: str) -> "NoReturn":
    raise ResealInputError(message)


def timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text or not IDENTIFIER_RE.fullmatch(text) or text in {".", ".."}:
        fail(f"invalid {label}: {value!r}")
    return text


def sha256_text(value: Any, label: str) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(text):
        fail(f"invalid {label}")
    return text


def address(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not ADDRESS_RE.fullmatch(text):
        fail(f"invalid {label}: {value!r}")
    return text.lower()


def operation(network: str) -> OperationIdentity:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=f"mother-build-reseal-input-{stamp}",
        request_id=f"mother-build-reseal-input-{stamp}-request",
        network=network,
        operation_kind="MOTHER-OP-EVIDENCE-EXPORT",
    )


def private_document(private_state: PrivateStateReadResult) -> dict[str, Any]:
    try:
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResealInputError("Mother private state is not canonical JSON") from exc
    if not isinstance(document, dict):
        fail("Mother private state root is not an object")
    return document


def private_binding(private_state: PrivateStateReadResult) -> dict[str, Any]:
    return {
        "generation": int(private_state.binding.generation),
        "content_sha256": private_state.binding.content_hash.digest,
        "manifest_sha256": private_state.binding.recovery_manifest_hash.digest,
    }


def read_canonical_json(path: Path) -> tuple[dict[str, Any], str]:
    resolved = path.resolve(strict=True)
    raw = resolved.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResealInputError(f"source evidence is not JSON: {resolved}") from exc
    if not isinstance(document, dict):
        fail(f"source evidence root is not an object: {resolved}")
    if canonical_json(document) != raw:
        fail(f"source evidence is not canonical JSON: {resolved}")
    return document, hashlib.sha256(raw).hexdigest()


def topology_candidates(document: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    yielded: set[int] = set()
    for key in (
        "final_topology",
        "current_topology",
        "post_add_topology",
        "post_removal_topology",
        "pre_removal_topology",
    ):
        value = document.get(key)
        if isinstance(value, Mapping) and id(value) not in yielded:
            yielded.add(id(value))
            yield value
    yield document


def add_route_binding(
    bindings: dict[str, dict[str, Any]],
    node: str,
    route: Mapping[str, Any],
    *,
    label: str,
) -> None:
    try:
        normalized = normalize_validator_route_bindings({node: route}, label=label)[node]
    except MotherDeploymentValidatorRouteError as exc:
        raise ResealInputError(f"invalid validator route for {node} from {label}: {exc}") from exc
    previous = bindings.get(node)
    if previous is not None and not validator_routes_same(previous, normalized):
        fail(f"conflicting permanent validator routes for {node}")
    bindings[node] = dict(normalized)


def harvest_sources(paths: list[Path], network: str) -> tuple[str | None, dict[str, dict[str, Any]], list[dict[str, Any]]]:
    genesis_values: set[str] = set()
    bindings: dict[str, dict[str, Any]] = {}
    provenance: list[dict[str, Any]] = []

    for path in paths:
        document, digest = read_canonical_json(path)
        source_network = document.get("network")
        if source_network is not None and source_network != network:
            fail(f"source evidence network mismatch: {path}")
        provenance.append({
            "path": str(path.resolve(strict=True)),
            "sha256": digest,
            "kind": document.get("kind"),
        })

        for candidate in topology_candidates(document):
            genesis = candidate.get("genesis_sha256")
            if genesis is not None:
                genesis_values.add(sha256_text(genesis, "source genesis SHA-256"))

            raw_bindings = candidate.get("validator_route_bindings")
            if isinstance(raw_bindings, Mapping):
                for raw_node, raw_route in raw_bindings.items():
                    node = identifier(raw_node, "validator route node")
                    if not isinstance(raw_route, Mapping):
                        fail(f"validator route for {node} is not an object")
                    add_route_binding(bindings, node, raw_route, label=f"source {path.name}")

            services = candidate.get("services")
            if isinstance(services, Mapping):
                for raw_node, raw_service in services.items():
                    if not isinstance(raw_service, Mapping):
                        continue
                    route = validator_route_from_record(raw_service)
                    if route is None:
                        continue
                    add_route_binding(
                        bindings,
                        identifier(raw_node, "service route node"),
                        route,
                        label=f"service route in {path.name}",
                    )

    if len(genesis_values) > 1:
        fail("source evidence disagrees about genesis SHA-256")
    genesis = next(iter(genesis_values)) if genesis_values else None
    return genesis, bindings, provenance


def parse_service_overrides(values: list[str]) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for value in values:
        try:
            raw_node, remainder = value.split("=", 1)
            raw_controller, raw_uuid = remainder.split(",", 1)
        except ValueError as exc:
            raise ResealInputError("--service must be NODE=CONTROLLER,SERVICE_UUID") from exc
        node = identifier(raw_node, "service override node")
        controller = identifier(raw_controller, "service override controller")
        service_uuid = identifier(raw_uuid, "service override UUID")
        if node in result:
            fail(f"duplicate --service override for {node}")
        result[node] = (controller, service_uuid)
    return result


def parse_route_overrides(
    values: list[str],
    *,
    private_state: PrivateStateReadResult,
    network: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        try:
            raw_node, remainder = value.split("=", 1)
            raw_controller, raw_port = remainder.split(",", 1)
        except ValueError as exc:
            raise ResealInputError("--route must be NODE=CONTROLLER,P2P_PORT") from exc
        node = identifier(raw_node, "route override node")
        controller = identifier(raw_controller, "route override controller")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise ResealInputError(f"invalid route port for {node}") from exc
        if not 1 <= port <= 65535:
            fail(f"invalid route port for {node}")
        if node in result:
            fail(f"duplicate --route override for {node}")
        try:
            host, source = controller_validator_host(private_state, network=network, controller_id=controller)
        except MotherDeploymentValidatorRouteError as exc:
            raise ResealInputError(str(exc)) from exc
        result[node] = {
            "kind": "mother-validator-p2p-route.v1",
            "controller_id": controller,
            "vpn_ip": host,
            "advertised_host": host,
            "p2p_port": port,
            "advertised_port": port,
            "container_p2p_port": port,
            "p2p_endpoint": f"{host}:{port}",
            "source": source,
        }
    return result


def network_document(private_state: PrivateStateReadResult, network: str) -> Mapping[str, Any]:
    document = private_document(private_state)
    networks = document.get("networks")
    body = networks.get(network) if isinstance(networks, Mapping) else None
    if not isinstance(body, Mapping):
        fail(f"network {network!r} is missing from Mother private state")
    return body


def validator_for_node(network_doc: Mapping[str, Any], network: str, node: str) -> str:
    nodes = network_doc.get("nodes")
    validators = network_doc.get("validators")
    if not isinstance(validators, Mapping):
        fail(f"network {network!r} has no validator mapping")

    validator_key = node
    node_record = nodes.get(node) if isinstance(nodes, Mapping) else None
    if isinstance(node_record, Mapping):
        ref = node_record.get("validator_ref")
        if isinstance(ref, str) and ref:
            prefix = f"networks.{network}.validators."
            if not ref.startswith(prefix):
                fail(f"{node} validator_ref is outside network {network}")
            validator_key = ref[len(prefix):]

    record = validators.get(validator_key)
    if not isinstance(record, Mapping):
        fail(f"Mother private state has no validator identity for {node}")
    return address(record.get("address"), f"{node} validator address")


def expected_controller_for_node(network_doc: Mapping[str, Any], node: str) -> str | None:
    nodes = network_doc.get("nodes")
    record = nodes.get(node) if isinstance(nodes, Mapping) else None
    if not isinstance(record, Mapping):
        return None
    host = record.get("host") or record.get("controller_id")
    return identifier(host, f"{node} controller") if isinstance(host, str) and host else None


def payload_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("services", "resources", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
        if any(key in payload for key in ("uuid", "id", "name")):
            return [payload]
    return []


def live_status(value: Any) -> bool:
    status = str(value or "").strip().lower()
    return not (
        status == "exited"
        or status.startswith("exited:")
        or status == "stopped"
        or status.startswith("stopped:")
    )


def observe_live_services(
    private_state: PrivateStateReadResult,
    network: str,
    nodes: list[str],
    *,
    overrides: Mapping[str, tuple[str, str]],
    timeout: float,
    max_response_bytes: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    unresolved = [node for node in nodes if node not in overrides]

    candidates: dict[str, list[dict[str, Any]]] = {node: [] for node in unresolved}
    if unresolved:
        for controller in list_coolify_controllers(private_state):
            if controller.network != network or not controller.enabled:
                continue
            try:
                observation = get_coolify_json(
                    controller,
                    "/api/v1/services",
                    authenticated=True,
                    timeout=timeout,
                    max_response_bytes=max_response_bytes,
                )
            except CoolifyObservationError as exc:
                raise ResealInputError(
                    f"failed to read Coolify service inventory from {controller.controller_id}: {exc.code}"
                ) from exc
            for item in payload_items(observation.payload):
                name = item.get("name") or item.get("resourceName")
                if name not in candidates:
                    continue
                service_uuid = item.get("uuid") or item.get("id")
                if not isinstance(service_uuid, str) or not service_uuid or not live_status(item.get("status") or item.get("state")):
                    continue
                candidates[name].append({
                    "node": name,
                    "controller_id": controller.controller_id,
                    "service_uuid": service_uuid,
                    "service_status": item.get("status") or item.get("state"),
                })

    now = timestamp()
    for node in nodes:
        if node in overrides:
            controller_id, service_uuid = overrides[node]
            result[node] = {
                "node": node,
                "controller_id": controller_id,
                "service_uuid": service_uuid,
                "service_status": None,
                "readiness_source": "operator-service-override",
                "last_observed_at": now,
            }
            continue
        matches = candidates.get(node, [])
        if not matches:
            fail(f"live Coolify inventory has no exact active primary service named {node}; use --service to override")
        if len(matches) != 1:
            detail = ", ".join(f"{item['controller_id']}:{item['service_uuid']}" for item in matches)
            fail(f"live Coolify inventory has ambiguous active primary services for {node}: {detail}; use --service")
        item = dict(matches[0])
        item["readiness_source"] = "out-of-band-reseal-input-live-inventory"
        item["last_observed_at"] = now
        result[node] = item
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a consumer-compatible topology file outside Mother evidence auto-discovery for an explicit reseal.",
        allow_abbrev=False,
    )
    parser.add_argument("--runtime-state-root", default=str(Path("runtime") / "state"))
    parser.add_argument("--network", default="mainnet", choices=["mainnet"])
    parser.add_argument("--node", action="append", required=True, help="exact active Mother node; repeat in validator-set order")
    parser.add_argument("--source-evidence", action="append", default=[], help="canonical historical/current evidence used only to recover genesis and permanent route bindings; repeat as needed")
    parser.add_argument("--genesis-sha256", help="explicit genesis SHA-256; required only when source evidence does not supply it")
    parser.add_argument("--service", action="append", default=[], metavar="NODE=CONTROLLER,SERVICE_UUID", help="override live service discovery for one node")
    parser.add_argument("--route", action="append", default=[], metavar="NODE=CONTROLLER,P2P_PORT", help="explicit permanent route when supplied source evidence does not contain it")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=4 * 1024 * 1024)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        nodes = [identifier(value, "node") for value in args.node]
        if len(set(nodes)) != len(nodes):
            fail("--node contains duplicates")

        runtime_state_root = Path(args.runtime_state_root).resolve(strict=False)
        paths = MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()
        op = operation(args.network)
        private_state = read_private_state(paths, operation=op)
        network_doc = network_document(private_state, args.network)

        chain_id = network_doc.get("chain_id")
        if not isinstance(chain_id, int) or chain_id <= 0:
            fail(f"network {args.network!r} has no valid chain_id in Mother private state")

        source_paths = [Path(value).resolve(strict=True) for value in args.source_evidence]
        source_genesis, route_bindings, source_provenance = harvest_sources(source_paths, args.network)
        explicit_genesis = sha256_text(args.genesis_sha256, "--genesis-sha256") if args.genesis_sha256 else None
        if explicit_genesis is not None and source_genesis is not None and explicit_genesis != source_genesis:
            fail("--genesis-sha256 disagrees with supplied source evidence")
        genesis_sha256 = explicit_genesis or source_genesis
        if genesis_sha256 is None:
            fail("genesis SHA-256 is unavailable; provide --source-evidence or --genesis-sha256")

        route_overrides = parse_route_overrides(args.route, private_state=private_state, network=args.network)
        for node, route in route_overrides.items():
            route_bindings[node] = route
        try:
            route_bindings = normalize_validator_route_bindings(route_bindings)
        except MotherDeploymentValidatorRouteError as exc:
            raise ResealInputError(str(exc)) from exc

        validators = [validator_for_node(network_doc, args.network, node) for node in nodes]
        if len(set(validators)) != len(validators):
            fail("declared nodes resolve to duplicate validator addresses")

        service_overrides = parse_service_overrides(args.service)
        unknown_service_overrides = sorted(set(service_overrides) - set(nodes))
        unknown_route_overrides = sorted(set(route_overrides) - set(nodes))
        if unknown_service_overrides:
            fail("--service specified for undeclared node(s): " + ", ".join(unknown_service_overrides))
        if unknown_route_overrides:
            fail("--route specified for undeclared node(s): " + ", ".join(unknown_route_overrides))

        services = observe_live_services(
            private_state,
            args.network,
            nodes,
            overrides=service_overrides,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
        )

        for node in nodes:
            route = route_bindings.get(node)
            if route is None:
                fail(f"no permanent validator route binding available for active node {node}; supply source evidence or --route")
            service = services[node]
            expected_controller = expected_controller_for_node(network_doc, node)
            controller_id = identifier(service.get("controller_id"), f"{node} service controller")
            if expected_controller is not None and controller_id != expected_controller:
                fail(f"{node} live service controller {controller_id} disagrees with Mother host {expected_controller}")
            if route.get("controller_id") != controller_id:
                fail(f"{node} permanent validator route controller disagrees with live service controller")
            try:
                configured_host, _source = controller_validator_host(private_state, network=args.network, controller_id=controller_id)
            except MotherDeploymentValidatorRouteError as exc:
                raise ResealInputError(str(exc)) from exc
            if route.get("vpn_ip") != configured_host:
                fail(f"{node} permanent validator route host disagrees with current Mother controller VPN host")
            service["validator_route"] = dict(route)
            service["vpn_ip"] = route["vpn_ip"]
            service["p2p_port"] = route["p2p_port"]
            service["p2p_endpoint"] = route["p2p_endpoint"]

        completed = timestamp()
        topology = {
            "source": "out-of-band-reseal-input-builder",
            "chain_id": chain_id,
            "genesis_sha256": genesis_sha256,
            "nodes": nodes,
            "services": services,
            # Preserve every permanent binding supplied by the operator's source
            # evidence, including retired nodes.  The active node set above is
            # intentionally independent of the permanent route ledger.
            "validator_route_bindings": route_bindings,
            "validator_count": len(validators),
            "validator_set": validators,
            "baseline_topology_used_as_live": False,
        }
        document: dict[str, Any] = {
            "kind": KIND,
            "schema_version": 1,
            "completed_at": completed,
            "observed_at": completed,
            "status": "pass",
            "failure": None,
            "mother_binding": private_binding(private_state),
            "network": args.network,
            "mode": "out-of-band-reseal-input",
            "source_previous_topology_evidence": source_provenance,
            "current_topology": topology,
            "final_topology": topology,
            "authority": {
                "read_only_reseal_input_builder": True,
                "operator_declared_nodes": list(nodes),
                "live_mutation_authorized": False,
            },
            "policy": {
                "allowed_http_methods": ["GET"],
                "coolify_control_plane_only": True,
                "network_access_performed": any(node not in service_overrides for node in nodes),
                "live_mutation_performed": False,
                "chain_mutation_performed": False,
                "routing_or_topology_published": False,
                "public_endpoint_created": False,
                "private_keys_materialized": False,
                "private_keys_persisted": False,
                "secrets_in_output": False,
            },
            "summary": {
                "clean": True,
                "complete": True,
                "out_of_band_reseal_input": True,
                "canonical_topology_evidence": False,
                "final_nodes": list(nodes),
                "final_validator_count": len(validators),
                "final_validator_set": list(validators),
                "live_mutation_performed": False,
                "next_phase": "seal-live-current-topology",
            },
            "live_mutation_performed": False,
            "chain_mutation_performed": False,
            "routing_or_topology_published": False,
            "public_endpoint_created": False,
            "next_phase": "seal-live-current-topology",
        }
        digest_document = dict(document)
        document["live_current_topology_sha256"] = hashlib.sha256(canonical_json(digest_document)).hexdigest()
        payload = canonical_json(document)
        payload_sha256 = hashlib.sha256(payload).hexdigest()

        output_root = runtime_state_root.joinpath(*OUTPUT_SUBDIR)
        atomic_files.ensure_durable_directory(output_root, operation=op)
        stamp = re.sub(r"[^0-9A-Za-z]+", "", completed)
        output_path = output_root / f"{stamp}-{args.network}-reseal-input-{payload_sha256[:16]}.json"
        atomic_files.durable_create(output_path, payload, operation=op)

        remediation = [
            sys.executable,
            str(REPO_ROOT / "tools" / "mother_deploy.py"),
            "seal-live-current-topology",
            "--network", args.network,
            "--runtime-state-root", str(runtime_state_root),
            "--topology-evidence", str(output_path),
            "--acknowledge-topology-evidence-sha256", payload_sha256,
            "--use-live-topology",
            "--write-evidence",
        ]
        print(json.dumps({
            "status": "pass",
            "kind": KIND,
            "out_of_bounds": True,
            "auto_baseline_eligible": False,
            "output_directory": str(output_root),
            "reseal_input": str(output_path),
            "reseal_input_sha256": payload_sha256,
            "nodes": nodes,
            "validator_route_binding_nodes": sorted(route_bindings),
            "source_evidence": source_provenance,
            "reseal_command_argv": remediation,
        }, indent=2, sort_keys=True))
        return 0
    except (ResealInputError, FileNotFoundError, CoolifyObservationError, MotherDeploymentValidatorRouteError) as exc:
        print(json.dumps({
            "status": "failed",
            "error": str(exc),
            "live_mutation_performed": False,
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
