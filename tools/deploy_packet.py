#!/usr/bin/env python3
"""Build single-file Hub/FDB deploy packets from known topology catalogs.

The committed topology/placement JSON files describe every Hub/FDB component the
operator knows how to deploy.  A deploy packet selects the components enabled for
one local deploy generation while keeping the unselected known components present
with ``enabled: false``.  The packet is local operator state and should not be
committed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKET_DIR = REPO_ROOT / "deploy" / "packets"
PACKET_KIND = "main_computer.hub_fdb_deploy_packet.v1"


class DeployPacketError(RuntimeError):
    """Raised when a Hub/FDB deploy packet cannot be built or loaded."""


def repo_relative_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def repo_relative_posix(value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
    return Path(value).as_posix().replace("\\", "/")


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeployPacketError(f"File does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DeployPacketError(f"Could not parse JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DeployPacketError(f"{path} must contain a JSON object.")
    return data


def clean_required_string(value: Any, field: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise DeployPacketError(f"{field} must be a non-empty string.")
    return clean


def clean_identifier(value: Any, field: str) -> str:
    clean = clean_required_string(value, field)
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
    if any(ch not in allowed for ch in clean):
        raise DeployPacketError(f"{field} contains unsupported characters: {clean!r}")
    return clean


def split_csv(value: str | None, field: str) -> list[str]:
    if not value:
        return []
    result = [item.strip() for item in value.split(",") if item.strip()]
    if not result:
        raise DeployPacketError(f"{field} must name at least one component.")
    if len(set(result)) != len(result):
        raise DeployPacketError(f"{field} contains duplicate component id(s).")
    return result


def posix_dirname(path: str) -> str:
    clean = str(path or "").replace("\\", "/").rstrip("/")
    if "/" not in clean:
        return "."
    dirname = clean.rsplit("/", 1)[0]
    return dirname or "/"


def packet_path_for_network(network: str) -> Path:
    return DEFAULT_PACKET_DIR / f"{network}-packet.json"


def deployed_path_for_network(network: str) -> Path:
    return DEFAULT_PACKET_DIR / f"{network}-deployed.json"


def default_generation(network: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{network}-{stamp}"


def load_catalog(placement_path: Path, topology_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    placement = load_json_object(placement_path)
    network_key = clean_identifier(placement.get("network_key"), "placement.network_key")
    placement_topology_path = repo_relative_path(clean_required_string(placement.get("topology_path"), "placement.topology_path"))
    effective_topology_path = topology_path or placement_topology_path
    topology = load_json_object(effective_topology_path)

    topology_network = topology.get("network") if isinstance(topology.get("network"), dict) else {}
    topology_network_key = str(topology_network.get("network_key") or "").strip()
    if topology_network_key and topology_network_key != network_key:
        raise DeployPacketError(
            f"Placement network_key {network_key!r} does not match topology network_key {topology_network_key!r}."
        )
    return placement, topology, placement_path, effective_topology_path


def catalog_summary(placement: dict[str, Any], topology: dict[str, Any]) -> dict[str, Any]:
    servers = placement.get("servers")
    if not isinstance(servers, list) or not servers:
        raise DeployPacketError("placement.servers must be a non-empty list.")
    fdb = placement.get("foundationdb")
    if not isinstance(fdb, dict):
        raise DeployPacketError("placement.foundationdb must be an object.")
    fdb_instances = fdb.get("instances")
    if not isinstance(fdb_instances, list) or not fdb_instances:
        raise DeployPacketError("placement.foundationdb.instances must be a non-empty list.")
    placement_hubs = placement.get("hubs")
    if not isinstance(placement_hubs, list) or not placement_hubs:
        raise DeployPacketError("placement.hubs must be a non-empty list.")
    topology_hubs = topology.get("hubs")
    if not isinstance(topology_hubs, list) or not topology_hubs:
        raise DeployPacketError("topology.hubs must be a non-empty list.")

    server_names = []
    for index, item in enumerate(servers):
        if not isinstance(item, dict):
            raise DeployPacketError(f"placement.servers[{index}] must be an object.")
        server_names.append(clean_identifier(item.get("name"), f"placement.servers[{index}].name"))

    fdb_ids = []
    fdb_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(fdb_instances):
        if not isinstance(item, dict):
            raise DeployPacketError(f"placement.foundationdb.instances[{index}] must be an object.")
        instance_id = clean_identifier(item.get("id"), f"placement.foundationdb.instances[{index}].id")
        if instance_id in fdb_by_id:
            raise DeployPacketError(f"Duplicate FDB instance id {instance_id!r}.")
        host = clean_identifier(item.get("coolify_server"), f"placement.foundationdb.instances[{index}].coolify_server")
        if host not in server_names:
            raise DeployPacketError(f"FDB instance {instance_id!r} references unknown server {host!r}.")
        fdb_ids.append(instance_id)
        fdb_by_id[instance_id] = item

    topology_hubs_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(topology_hubs):
        if not isinstance(item, dict):
            raise DeployPacketError(f"topology.hubs[{index}] must be an object.")
        hub_id = clean_identifier(item.get("hub_id"), f"topology.hubs[{index}].hub_id")
        if hub_id in topology_hubs_by_id:
            raise DeployPacketError(f"Duplicate topology hub id {hub_id!r}.")
        topology_hubs_by_id[hub_id] = item

    hub_ids = []
    hubs_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(placement_hubs):
        if not isinstance(item, dict):
            raise DeployPacketError(f"placement.hubs[{index}] must be an object.")
        hub_id = clean_identifier(item.get("hub_id"), f"placement.hubs[{index}].hub_id")
        if hub_id in hubs_by_id:
            raise DeployPacketError(f"Duplicate placement hub id {hub_id!r}.")
        if hub_id not in topology_hubs_by_id:
            raise DeployPacketError(f"Hub {hub_id!r} exists in placement.hubs but not topology.hubs.")
        host = clean_identifier(item.get("coolify_server"), f"placement.hubs[{index}].coolify_server")
        if host not in server_names:
            raise DeployPacketError(f"Hub {hub_id!r} references unknown server {host!r}.")
        hub_ids.append(hub_id)
        hubs_by_id[hub_id] = item

    return {
        "servers": server_names,
        "fdb_ids": fdb_ids,
        "hub_ids": hub_ids,
        "fdb_by_id": fdb_by_id,
        "hubs_by_id": hubs_by_id,
        "topology_hubs_by_id": topology_hubs_by_id,
    }


def selected_set(selected: list[str], known: list[str], field: str) -> set[str]:
    unknown = sorted(set(selected) - set(known))
    if unknown:
        raise DeployPacketError(f"{field} references unknown component id(s): {', '.join(unknown)}.")
    if not selected:
        raise DeployPacketError(f"{field} must select at least one known component.")
    return set(selected)


def fdb_cluster_contents(placement: dict[str, Any], servers_by_name: dict[str, dict[str, Any]], enabled_fdb_ids: set[str]) -> str:
    fdb = placement.get("foundationdb") if isinstance(placement.get("foundationdb"), dict) else {}
    description = clean_required_string(fdb.get("cluster_description"), "foundationdb.cluster_description")
    cluster_id = clean_required_string(fdb.get("cluster_id"), "foundationdb.cluster_id")
    coordinators: list[str] = []
    for item in fdb.get("instances") or []:
        if not isinstance(item, dict):
            continue
        instance_id = str(item.get("id") or "").strip()
        if instance_id not in enabled_fdb_ids:
            continue
        server_name = clean_required_string(item.get("coolify_server"), f"foundationdb.instances[{instance_id}].coolify_server")
        server = servers_by_name[server_name]
        vpn_ip = clean_required_string(server.get("vpn_ip"), f"servers[{server_name}].vpn_ip")
        port = int(item.get("port"))
        coordinators.append(f"{vpn_ip}:{port}")
    if not coordinators:
        raise DeployPacketError("At least one selected FDB instance is required to build fdb.cluster.")
    return f"{description}:{cluster_id}@{','.join(coordinators)}"


def build_packet(
    *,
    network: str,
    placement_path: Path,
    topology_path: Path | None,
    selected_hubs: list[str],
    selected_fdb: list[str],
    generation: str | None = None,
    intent: str = "",
) -> dict[str, Any]:
    placement, topology, placement_path, topology_path = load_catalog(placement_path, topology_path)
    network_key = clean_identifier(placement.get("network_key"), "placement.network_key")
    if network_key != network:
        raise DeployPacketError(f"Requested network {network!r} does not match placement network_key {network_key!r}.")

    summary = catalog_summary(placement, topology)
    enabled_hubs = selected_set(selected_hubs, summary["hub_ids"], "--hubs")
    enabled_fdb = selected_set(selected_fdb, summary["fdb_ids"], "--fdb")

    server_items = placement.get("servers") or []
    servers_by_name = {clean_identifier(item.get("name"), "servers[].name"): item for item in server_items if isinstance(item, dict)}
    fdb = placement.get("foundationdb") if isinstance(placement.get("foundationdb"), dict) else {}

    fdb_cluster_file_path = clean_required_string(fdb.get("cluster_file_path"), "foundationdb.cluster_file_path")
    fdb_cluster = fdb_cluster_contents(placement, servers_by_name, enabled_fdb)
    active_topology = dict(topology)
    active_topology["hubs"] = [
        dict(summary["topology_hubs_by_id"][hub_id])
        for hub_id in summary["hub_ids"]
        if hub_id in enabled_hubs
    ]
    active_topology.setdefault("metadata", {})
    if isinstance(active_topology["metadata"], dict):
        active_topology["metadata"]["deploy_packet_generation"] = generation or "pending"
        active_topology["metadata"]["deploy_packet_scope"] = "hub_fdb"

    hub_topology_path = f"{posix_dirname(fdb_cluster_file_path)}/deploy-packet-topology.json"
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    warnings: list[str] = []
    if len(enabled_fdb) == 1:
        warnings.append(f"Only one FoundationDB instance is enabled for {network}.")
    enabled_fdb_hosts = {
        clean_required_string(summary["fdb_by_id"][fid].get("coolify_server"), f"foundationdb.instances[{fid}].coolify_server")
        for fid in enabled_fdb
    }
    if len(enabled_fdb_hosts) == 1 and len(servers_by_name) > 1:
        warnings.append(f"All enabled FoundationDB instances are on {sorted(enabled_fdb_hosts)[0]}.")
    enabled_hub_hosts = {
        clean_required_string(summary["hubs_by_id"][hub_id].get("coolify_server"), f"hubs[{hub_id}].coolify_server")
        for hub_id in enabled_hubs
    }

    hosts: dict[str, Any] = {}
    for server_name in summary["servers"]:
        roles: list[str] = []
        if server_name in enabled_fdb_hosts:
            roles.append("fdb")
        if server_name in enabled_hub_hosts:
            roles.append("hub")
        server = servers_by_name[server_name]
        hosts[server_name] = {
            "enabled": bool(roles),
            "roles": roles,
            "vpn_ip": server.get("vpn_ip"),
        }

    instances = []
    for instance_id in summary["fdb_ids"]:
        item = summary["fdb_by_id"][instance_id]
        server_name = clean_required_string(item.get("coolify_server"), f"foundationdb.instances[{instance_id}].coolify_server")
        server = servers_by_name[server_name]
        instances.append(
            {
                "id": instance_id,
                "host": server_name,
                "enabled": instance_id in enabled_fdb,
                "vpn_ip": server.get("vpn_ip"),
                "port": int(item.get("port")),
                "machine_id": item.get("machine_id") or server_name,
                "zone_id": item.get("zone_id") or server_name,
            }
        )

    hubs = []
    for hub_id in summary["hub_ids"]:
        item = summary["hubs_by_id"][hub_id]
        topology_hub = summary["topology_hubs_by_id"][hub_id]
        hubs.append(
            {
                "hub_id": hub_id,
                "host": item.get("coolify_server"),
                "enabled": hub_id in enabled_hubs,
                "public_url": item.get("public_url") or topology_hub.get("public_url") or topology_hub.get("hub_url"),
                "runtime_dir": item.get("runtime_dir"),
                "cluster_file_path": item.get("cluster_file_path") or fdb_cluster_file_path,
                "namespace": item.get("namespace") or fdb.get("namespace"),
                "roles": topology_hub.get("roles") or [],
            }
        )

    topology_network = topology.get("network") if isinstance(topology.get("network"), dict) else {}
    packet = {
        "kind": PACKET_KIND,
        "scope": "hub_fdb",
        "network_key": network,
        "generation": generation or default_generation(network),
        "generated_at": generated_at,
        "intent": intent,
        "source": {
            "placement_path": repo_relative_posix(placement_path),
            "topology_path": repo_relative_posix(topology_path),
        },
        "hosts": hosts,
        "foundationdb": {
            "image": fdb.get("image"),
            "cluster_description": fdb.get("cluster_description"),
            "cluster_id": fdb.get("cluster_id"),
            "cluster_file_path": fdb_cluster_file_path,
            "namespace": fdb.get("namespace"),
            "configure": fdb.get("configure"),
            "cluster_file_contents": fdb_cluster,
            "instances": instances,
        },
        "hub_topology": {
            "container_path": hub_topology_path,
            "contents": active_topology,
        },
        "hubs": hubs,
        "chain_dependency": {
            "managed_by_packet": False,
            "network_key": network,
            "chain_id": topology_network.get("chain_id"),
            "chain_rpc_url": topology_network.get("chain_rpc_url"),
        },
        "remote": {
            "packet_path": f"/data/main-computer/deploy-packets/{network}/current.json",
            "topology_path": hub_topology_path,
        },
        "warnings": warnings,
        "checksums": {},
    }
    packet["checksums"]["packet_sha256"] = packet_sha256(packet)
    return packet


def canonical_packet_json(packet: dict[str, Any], *, with_checksum: bool = True) -> str:
    clone = json.loads(json.dumps(packet, sort_keys=True))
    if not with_checksum:
        clone.setdefault("checksums", {})["packet_sha256"] = ""
    return json.dumps(clone, indent=2, sort_keys=True) + "\n"


def packet_sha256(packet: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_packet_json(packet, with_checksum=False).encode("utf-8")).hexdigest()


def validate_packet(packet: dict[str, Any]) -> None:
    if packet.get("kind") != PACKET_KIND:
        raise DeployPacketError(f"Unsupported deploy packet kind {packet.get('kind')!r}; expected {PACKET_KIND}.")
    if packet.get("scope") != "hub_fdb":
        raise DeployPacketError(f"Unsupported deploy packet scope {packet.get('scope')!r}; expected hub_fdb.")
    expected = packet_sha256(packet)
    actual = ((packet.get("checksums") or {}) if isinstance(packet.get("checksums"), dict) else {}).get("packet_sha256")
    if actual and actual != expected:
        raise DeployPacketError(f"Deploy packet checksum mismatch: expected {expected}, found {actual}.")
    enabled_hubs = [hub for hub in packet.get("hubs") or [] if isinstance(hub, dict) and hub.get("enabled") is True]
    enabled_fdb = [
        instance
        for instance in ((packet.get("foundationdb") or {}).get("instances") or [])
        if isinstance(instance, dict) and instance.get("enabled") is True
    ]
    if not enabled_hubs:
        raise DeployPacketError("Deploy packet must enable at least one Hub.")
    if not enabled_fdb:
        raise DeployPacketError("Deploy packet must enable at least one FoundationDB instance.")


def load_packet(path: Path) -> dict[str, Any]:
    packet = load_json_object(path)
    validate_packet(packet)
    return packet


def enabled_ids(items: list[Any], id_key: str) -> set[str]:
    result: set[str] = set()
    for item in items:
        if isinstance(item, dict) and item.get("enabled") is True:
            result.add(clean_identifier(item.get(id_key), id_key))
    return result


def packet_enabled_hub_ids(packet: dict[str, Any]) -> set[str]:
    return enabled_ids(packet.get("hubs") or [], "hub_id")


def packet_enabled_fdb_ids(packet: dict[str, Any]) -> set[str]:
    fdb = packet.get("foundationdb") if isinstance(packet.get("foundationdb"), dict) else {}
    return enabled_ids(fdb.get("instances") or [], "id")


def packet_hub_topology_json(packet: dict[str, Any]) -> str:
    hub_topology = packet.get("hub_topology") if isinstance(packet.get("hub_topology"), dict) else {}
    contents = hub_topology.get("contents")
    if not isinstance(contents, dict):
        raise DeployPacketError("Deploy packet hub_topology.contents must be an object.")
    return json.dumps(contents, indent=2, sort_keys=True) + "\n"


def packet_fdb_cluster_contents(packet: dict[str, Any]) -> str:
    fdb = packet.get("foundationdb") if isinstance(packet.get("foundationdb"), dict) else {}
    return clean_required_string(fdb.get("cluster_file_contents"), "foundationdb.cluster_file_contents")


def packet_hub_topology_path(packet: dict[str, Any]) -> str:
    hub_topology = packet.get("hub_topology") if isinstance(packet.get("hub_topology"), dict) else {}
    return clean_required_string(
        hub_topology.get("container_path") or (packet.get("remote") or {}).get("topology_path"),
        "hub_topology.container_path",
    )


def archive_existing_output(path: Path, network: str) -> Path | None:
    if not path.exists():
        return None
    archive_dir = path.parent / "archive" / network
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = archive_dir / f"{stamp}-{path.name}"
    suffix = 1
    while target.exists():
        target = archive_dir / f"{stamp}-{suffix}-{path.name}"
        suffix += 1
    shutil.copy2(path, target)
    return target


def write_packet(packet: dict[str, Any], output_path: Path, *, archive: bool = True) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = canonical_packet_json(packet)
    archived = None
    if output_path.exists() and output_path.read_text(encoding="utf-8") != rendered and archive:
        archived = archive_existing_output(output_path, clean_required_string(packet.get("network_key"), "network_key"))
    output_path.write_text(rendered, encoding="utf-8")
    return {
        "packet_path": str(output_path),
        "archived_previous": str(archived) if archived else "",
        "packet_sha256": packet["checksums"]["packet_sha256"],
    }


def list_components_result(network: str, placement_path: Path, topology_path: Path | None) -> dict[str, Any]:
    placement, topology, placement_path, topology_path = load_catalog(placement_path, topology_path)
    network_key = clean_identifier(placement.get("network_key"), "placement.network_key")
    if network_key != network:
        raise DeployPacketError(f"Requested network {network!r} does not match placement network_key {network_key!r}.")
    summary = catalog_summary(placement, topology)
    fdb = []
    for instance_id in summary["fdb_ids"]:
        item = summary["fdb_by_id"][instance_id]
        fdb.append({"id": instance_id, "host": item.get("coolify_server"), "port": item.get("port")})
    hubs = []
    for hub_id in summary["hub_ids"]:
        item = summary["hubs_by_id"][hub_id]
        hubs.append({"hub_id": hub_id, "host": item.get("coolify_server"), "public_url": item.get("public_url")})
    return {
        "ok": True,
        "network_key": network,
        "placement_path": repo_relative_posix(placement_path),
        "topology_path": repo_relative_posix(topology_path),
        "hosts": summary["servers"],
        "foundationdb": fdb,
        "hubs": hubs,
    }


def prep_packet_result(args: argparse.Namespace) -> dict[str, Any]:
    network = clean_identifier(args.network, "network")
    output = Path(args.out) if str(getattr(args, "out", "") or "").strip() else packet_path_for_network(network)
    packet = build_packet(
        network=network,
        placement_path=repo_relative_path(args.placement),
        topology_path=repo_relative_path(args.topology) if str(getattr(args, "topology", "") or "").strip() else None,
        selected_hubs=split_csv(args.hubs, "--hubs"),
        selected_fdb=split_csv(args.fdb, "--fdb"),
        generation=args.generation or None,
        intent=args.intent or "",
    )
    write_result = write_packet(packet, repo_relative_path(output), archive=not getattr(args, "no_archive", False))
    return {"ok": True, "packet": packet, "write": write_result}


def diff_packets(candidate: dict[str, Any], deployed: dict[str, Any] | None) -> dict[str, Any]:
    if deployed is None:
        return {
            "changed": True,
            "reason": "No deployed packet exists.",
            "candidate_generation": candidate.get("generation"),
            "deployed_generation": "",
        }
    return {
        "changed": packet_sha256(candidate) != packet_sha256(deployed),
        "candidate_generation": candidate.get("generation"),
        "deployed_generation": deployed.get("generation"),
        "candidate_sha256": packet_sha256(candidate),
        "deployed_sha256": packet_sha256(deployed),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build and inspect local Hub/FDB deploy packets.")
    parser.add_argument("action", choices=["list-components", "prep", "diff"], help="Packet action to run.")
    parser.add_argument("network", help="Network key, for example testnet or mainnet.")
    parser.add_argument("--placement", type=Path, default="", help="Path to <network>-coolify-deployment.json.")
    parser.add_argument("--topology", type=Path, default="", help="Optional topology JSON override.")
    parser.add_argument("--hubs", default="", help="Comma-separated Hub ids to enable.")
    parser.add_argument("--fdb", default="", help="Comma-separated FoundationDB instance ids to enable.")
    parser.add_argument("--generation", default="", help="Optional packet generation id. Defaults to <network>-<UTC timestamp>.")
    parser.add_argument("--intent", default="", help="Optional human-readable operator intent.")
    parser.add_argument("--out", default="", help="Output packet path. Defaults to deploy/packets/<network>-packet.json.")
    parser.add_argument("--packet", type=Path, default="", help="Candidate packet path for diff.")
    parser.add_argument("--deployed", type=Path, default="", help="Deployed packet path for diff.")
    parser.add_argument("--no-archive", action="store_true", help="Do not archive an existing different output packet before writing.")
    parser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")
    return parser.parse_args(argv)


def default_placement_path(network: str) -> Path:
    return REPO_ROOT / "deploy" / "hub-topology" / f"{network}-coolify-deployment.json"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not str(args.placement or "").strip():
            args.placement = default_placement_path(args.network)
        if args.action == "list-components":
            result = list_components_result(args.network, repo_relative_path(args.placement), repo_relative_path(args.topology) if str(args.topology or "").strip() else None)
        elif args.action == "prep":
            result = prep_packet_result(args)
        else:
            packet_path = repo_relative_path(args.packet or packet_path_for_network(args.network))
            deployed_path = repo_relative_path(args.deployed or deployed_path_for_network(args.network))
            candidate = load_packet(packet_path)
            deployed = load_packet(deployed_path) if deployed_path.exists() else None
            result = {"ok": True, "diff": diff_packets(candidate, deployed)}
    except DeployPacketError as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        print(json.dumps(result, sort_keys=True) if args.json else json.dumps(result, indent=2, sort_keys=True))
        return 2

    print(json.dumps(result, sort_keys=True) if args.json else json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
