#!/usr/bin/env python3
"""Synchronize repo-known facts into the local private state YAML.

The private state file is a human-readable local text database, not a generated
inventory dump.  This tool follows three rules:

1. Repo-known values may be refreshed from repo files.
2. Existing user-entered values are preserved when the repo does not know them.
3. Network sections get stable null slots for important unknown values; other
   sections only get values that are known or already present.

The emitted YAML includes short ``# provenance: ...`` comments so it is clear
whether a value came from the repo, the existing private state file, a local
secret file, or the sparse network template.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any


STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"
LOCAL_SECRETS_RELATIVE_PATH = Path("local.secrets")

TOPOLOGY_RELATIVE_PATH = Path("deploy") / "hub-topology" / "testnet-coolify-deployment.json"
QBFT_TOOL_RELATIVE_PATH = Path("tools") / "coolify_qbft_network.py"
DEV_DEPLOYMENT_RELATIVE_PATH = Path("runtime") / "deployments" / "dev" / "latest.json"
LOCAL_COOLIFY_TOKEN_RELATIVE_PATH = Path("runtime") / "coolify-local-docker" / "api-token.txt"
HUB_NETWORKS_RELATIVE_PATH = Path("main_computer") / "config" / "hub_networks.json"

REMOTE_TOPOLOGY_RELATIVE_PATHS = {
    "testnet": Path("deploy") / "hub-topology" / "testnet-topology.json",
    "mainnet": Path("deploy") / "hub-topology" / "mainnet-topology.json",
}

REMOTE_COOLIFY_DEPLOYMENT_RELATIVE_PATHS = {
    "testnet": Path("deploy") / "hub-topology" / "testnet-coolify-deployment.json",
    "mainnet": Path("deploy") / "hub-topology" / "mainnet-coolify-deployment.json",
}

REMOTE_CONTRACTS_RELATIVE_PATHS = {
    "testnet": Path("main_computer") / "config" / "testnet_contracts.json",
    "mainnet": Path("main_computer") / "config" / "mainnet_contracts.json",
}

PLACEHOLDER_RE = re.compile(r"^<[^>\n]+>$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
IP_ADDRESS_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")

LIVE_NETWORKS = ("test", "dev", "testnet", "mainnet")
SENSITIVE_WALLET_NETWORKS = ("testnet", "mainnet")
OWNER_SELECTOR = "0x8da5cb5b"
PAUSED_SELECTOR = "0x5c975abb"

SENSITIVE_KEYS = {"private_key", "ssh_private_key", "password", "api_token"}
PROVENANCE_COMMENT_COLUMN = 96
QBFT_ALLOWED_ROLES = {"validator", "rpc"}

ANVIL_DEFAULTS = {
    "deployer": {
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    "captain": {
        "address": "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266",
        "private_key": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    },
    "o1": {
        "address": "0x70997970C51812dc3A010C7d01b50e0d17dc79C8",
        "private_key": "0x59c6995e998f97a5a0044966f094538eeb8b1416d61b7aae62a49a6c8f6a3c11",
    },
    "o2": {
        "address": "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC",
        "private_key": "0x5de4111a07a2c0d7c97dfca3fafa4b6e8f4a9cdf764a29a7e1a8cedcf74f4a05",
    },
    "o3": {
        "address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906",
        "private_key": "0x7c85211829426c7553fd53dfebede1b7a8129cf96fbb0d8f7c109e363d7f29e8",
    },
}

MAIN_CONTRACTS = ("AlphaBetaLockout", "HubCreditBridgeEscrow", "XLagBridgeReserve")

DEV_CONTRACT_KEYS = {
    "AlphaBetaLockout": "alpha-beta-lockout",
    "HubCreditBridgeEscrow": "hub_credit_bridge_escrow",
    "XLagBridgeReserve": "xlag-bridge-reserve",
}

PREFERRED_ORDER: dict[tuple[str, ...], list[str]] = {
    (): ["schema_version", "coolify", "wallets", "networks", "contracts", "last_check"],
    ("coolify",): [
        "project_name",
        "project_uuid",
        "fdb_environment_name",
        "hub_environment_name",
        "environment_name",
        "server_name",
        "server_uuid",
        "destination_uuid",
        "hosts",
        "local_test",
    ],
    ("coolify", "local_test"): [
        "name",
        "host",
        "ssh_user",
        "ssh_port",
        "coolify_url",
        "api_token",
        "project_name",
        "project_uuid",
        "server_name",
        "server_uuid",
        "destination_uuid",
        "api_reachable",
        "api_version",
        "last_seen",
    ],
    ("coolify", "hosts", "*"): [
        "name",
        "droplet_hostname",
        "public_ip",
        "vpn_ip",
        "url",
        "api_token",
        "api_token_env",
        "api_token_file",
        "project_name",
        "project_uuid",
        "server_name",
        "server_uuid",
        "destination_uuid",
        "environment_uuid",
        "api_reachable",
        "api_version",
        "last_seen",
        "ssh_user",
        "ssh_port",
        "password",
        "ssh_private_key",
    ],
    ("wallets",): ["defaults"],
    ("wallets", "defaults"): ["deployer", "captain", "o1", "o2", "o3"],
    ("networks",): ["dev", "test", "testnet", "mainnet"],
    ("networks", "*"): [
        "display_name",
        "kind",
        "chain_id",
        "remote_coolify_hosts",
        "rpc",
        "qbft",
        "hub",
        "foundationdb",
        "wallets",
        "contracts",
        "last_seen",
    ],
    ("networks", "*", "qbft"): ["instances"],
    ("networks", "*", "qbft", "instances", "*"): [
        "coolify_host",
        "roles",
        "rpc_host_port",
        "p2p_host_port",
    ],
    ("networks", "*", "hub"): [
        "public_url",
        "bind_host",
        "bind_port",
        "runtime_dir",
        "deployment_manifest_path",
        "entry_urls",
        "instances",
    ],
    ("networks", "*", "hub", "instances", "*"): ["public_url", "hub_url", "coolify_host", "roles"],
    ("networks", "*", "foundationdb"): [
        "cluster_description",
        "cluster_id",
        "cluster_file_path",
        "namespace",
        "configure",
    ],
    ("networks", "*", "wallets"): [
        "deployer",
        "captain",
        "o1",
        "o2",
        "o3",
        "hub_admin",
        "smoke_client",
        "escrow_owner",
    ],
    ("networks", "*", "wallets", "*"): ["address", "private_key", "credits"],
    ("networks", "*", "contracts"): list(MAIN_CONTRACTS),
    ("networks", "*", "contracts", "AlphaBetaLockout"): ["address", "code_present", "version"],
    ("networks", "*", "contracts", "HubCreditBridgeEscrow"): [
        "address",
        "code_present",
        "version",
        "owner",
        "bridge_controller",
        "paused",
    ],
    ("networks", "*", "contracts", "XLagBridgeReserve"): ["address", "code_present", "version", "captain", "crew"],
    ("networks", "*", "last_seen"): ["chain_rpc", "hub", "contracts"],
}


def migrate_coolify_host_slots(state: dict[str, Any]) -> None:
    """Migrate older coolify.primary/secondary records into coolify.hosts.A/B.

    The private state file is user-owned, so slot values that already exist under
    coolify.hosts win.  Older primary/secondary fields are only used to fill
    missing keys and are then removed so the file converges to the host-slot
    schema.
    """

    coolify = state.get("coolify")
    if not isinstance(coolify, MutableMapping):
        return

    hosts = coolify.get("hosts")
    if not isinstance(hosts, MutableMapping):
        hosts = {}
        coolify["hosts"] = hosts

    for legacy_key, slot in (("primary", "A"), ("secondary", "B")):
        legacy = coolify.pop(legacy_key, None)
        if not isinstance(legacy, Mapping):
            continue
        target = hosts.get(slot)
        if not isinstance(target, MutableMapping):
            target = {}
            hosts[slot] = target
        for key, value in legacy.items():
            if not value_is_known(target.get(str(key))) and value_is_known(value):
                target[str(key)] = value


def host_slot_name(index: int) -> str:
    """Return spreadsheet-style host slot labels: A..Z, AA..AZ, BA..."""

    if index < 0:
        raise ValueError("host slot index cannot be negative")
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def host_slot_sort_key(key: str) -> tuple[int, str]:
    if not re.fullmatch(r"[A-Z]+", key):
        return (10**9, key)
    index = 0
    for char in key:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return (index - 1, key)


class StateBuilder:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = scrub_placeholders(existing or {})
        if not isinstance(self.state, dict):
            self.state = {}
        migrate_coolify_host_slots(self.state)
        self.provenance: dict[tuple[str, ...], str] = {}
        self.live_preferences: dict[str, str] = {}
        self.live_mismatches: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []
        mark_existing_provenance(self.state, self.provenance, ())

    def get(self, path: tuple[str, ...]) -> Any:
        node: Any = self.state
        for part in path:
            if not isinstance(node, Mapping) or part not in node:
                return None
            node = node[part]
        return node

    def has_known(self, path: tuple[str, ...]) -> bool:
        return value_is_known(self.get(path))

    def set_value(self, path: tuple[str, ...], value: Any, source: str, *, overwrite: bool = True) -> None:
        if not overwrite and self.has_known(path):
            self.provenance.setdefault(path, "existing-state")
            return
        parent = ensure_mapping_path(self.state, path[:-1])
        parent[path[-1]] = value
        mark_provenance_for_value(value, self.provenance, path, source)

    def set_if_known(self, path: tuple[str, ...], value: Any, source: str, *, overwrite: bool = True) -> None:
        if value_is_known(value):
            self.set_value(path, value, source, overwrite=overwrite)

    def set_network_slot(self, path: tuple[str, ...]) -> None:
        if self.has_known(path):
            self.provenance.setdefault(path, "existing-state")
            return
        self.set_value(path, None, "template", overwrite=True)

    def set_existing_or_null_network_slot(self, path: tuple[str, ...]) -> None:
        if self.has_known(path):
            self.provenance.setdefault(path, "existing-state")
        else:
            self.set_value(path, None, "template", overwrite=True)

    def delete_path(self, path: tuple[str, ...]) -> None:
        if not path:
            return
        parent: Any = self.state
        for part in path[:-1]:
            if not isinstance(parent, MutableMapping) or part not in parent:
                return
            parent = parent[part]
        if not isinstance(parent, MutableMapping):
            return
        parent.pop(path[-1], None)
        stale = [key for key in self.provenance if key[: len(path)] == path]
        for key in stale:
            self.provenance.pop(key, None)

    def live_preference_for_path(self, path: tuple[str, ...]) -> str | None:
        if len(path) >= 2 and path[0] == "networks":
            return self.live_preferences.get(path[1])
        return None

    def record_live_mismatch(self, path: tuple[str, ...], local_value: Any, live_value: Any, source: str) -> None:
        if not should_block_write_for_live_mismatch(path):
            return
        self.live_mismatches.append(
            {
                "network": path[1],
                "path": path,
                "local": local_value,
                "live": live_value,
                "source": source,
                "preference": self.live_preference_for_path(path),
            }
        )

    def unresolved_live_mismatches(self) -> list[dict[str, Any]]:
        return [mismatch for mismatch in self.live_mismatches if mismatch.get("preference") not in {"local", "remote"}]

    def record_warning(self, code: str, path: tuple[str, ...], message: str, **details: Any) -> None:
        payload: dict[str, Any] = {
            "code": code,
            "path": ".".join(str(part) for part in path),
            "message": message,
        }
        payload.update(details)
        self.warnings.append(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync repo-known facts into runtime/state/main_computer.private.yaml.")
    parser.add_argument("--state", type=Path, default=None, help="Private YAML path. Defaults to runtime/state/main_computer.private.yaml.")
    parser.add_argument("--write", action="store_true", help="Write the merged private YAML instead of printing it.")
    parser.add_argument("--show-secrets", action="store_true", help="Print secrets instead of redacting them in preview output.")
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Deprecated compatibility flag. Live checks are now enabled by default.",
    )
    parser.add_argument(
        "--no-live-check",
        action="store_true",
        help="Skip configured Coolify API, Hub URL, and chain RPC probes.",
    )
    parser.add_argument(
        "--live-network",
        action="append",
        choices=LIVE_NETWORKS,
        help="Network to live-check. May be passed more than once. Defaults to test, dev, testnet, and mainnet.",
    )
    parser.add_argument(
        "--prefer-local",
        action="append",
        choices=LIVE_NETWORKS,
        default=[],
        metavar="NETWORK",
        help="Allow --write for NETWORK when live values disagree, keeping local/repo values for that network.",
    )
    parser.add_argument(
        "--prefer-remote",
        action="append",
        choices=LIVE_NETWORKS,
        default=[],
        metavar="NETWORK",
        help="Allow --write for NETWORK when live values disagree, writing live values for that network.",
    )
    parser.add_argument("--live-timeout", type=float, default=4.0, help="Timeout in seconds for each live HTTP/RPC check.")
    args = parser.parse_args(argv)
    local_preference_networks = set(args.prefer_local or [])
    remote_preference_networks = set(args.prefer_remote or [])
    conflicting_preferences = sorted(local_preference_networks & remote_preference_networks)
    if conflicting_preferences:
        parser.error("--prefer-local and --prefer-remote cannot both be set for: " + ", ".join(conflicting_preferences))

    root = find_repo_root(Path.cwd())
    state_path = args.state if args.state is not None else root / STATE_RELATIVE_PATH
    if not state_path.is_absolute():
        state_path = root / state_path

    existing = load_yaml_file(state_path)
    builder = StateBuilder(existing)
    for network_name in local_preference_networks:
        builder.live_preferences[network_name] = "local"
    for network_name in remote_preference_networks:
        builder.live_preferences[network_name] = "remote"

    populate_state(builder, root)
    live_check_enabled = not args.no_live_check
    if live_check_enabled:
        check_live_state(
            builder,
            root,
            networks=tuple(args.live_network or LIVE_NETWORKS),
            timeout_s=max(0.25, float(args.live_timeout)),
        )

    ordered = order_mapping(builder.state, ())
    text_state = ordered if args.show_secrets or args.write else redact_state(ordered)
    yaml_text = emit_yaml(text_state, builder.provenance)

    if args.write:
        unresolved = builder.unresolved_live_mismatches() if live_check_enabled else []
        if unresolved:
            print(format_write_refusal(unresolved), file=sys.stderr)
            return 2
        added_secrets = ensure_local_secrets_for_sensitive_values(root, builder.state)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(yaml_text, encoding="utf-8")
        print(f"Wrote {relative_display(state_path, root)}")
        if added_secrets:
            print(f"Added {added_secrets} sensitive value(s) to {LOCAL_SECRETS_RELATIVE_PATH.as_posix()}")
    else:
        print(yaml_text, end="")

    for warning in builder.warnings:
        print("sync_warning: " + json.dumps(warning, sort_keys=True), file=sys.stderr)
    return 0


def populate_state(builder: StateBuilder, root: Path) -> None:
    builder.set_value(("schema_version",), 1, "tool", overwrite=False)
    populate_coolify(builder, root)
    populate_default_wallets(builder)
    populate_test_network(builder, root)
    populate_dev_network(builder, root)
    populate_remote_network(builder, root, "testnet")
    populate_remote_network(builder, root, "mainnet")
    remove_legacy_qbft_summary_fields(builder)
    sanitize_manual_coolify_host_references(builder)
    validate_qbft_instances(builder)



def populate_coolify(builder: StateBuilder, root: Path) -> None:
    # Remote Coolify host slots are private/manual operator state.  Repo topology
    # may reference a host by name, but it must never create coolify.hosts slots.
    for network_name in ("testnet", "mainnet"):
        populate_remote_coolify_hosts(builder, root, network_name)

    builder.set_value(("coolify", "local_test", "name"), "local-test-coolify", "tool", overwrite=False)

    seed = load_qbft_seed(root, "test")
    hosts = seed.get("hosts") if isinstance(seed, dict) else None
    if isinstance(hosts, dict) and hosts:
        host = hosts.get("local-coolify") if isinstance(hosts.get("local-coolify"), dict) else next(
            (value for value in hosts.values() if isinstance(value, dict) and str(value.get("address")) == "127.0.0.1"),
            None,
        )
        if isinstance(host, dict):
            source = f"repo:{QBFT_TOOL_RELATIVE_PATH.as_posix()}:NETWORK_SEEDS.test"
            builder.set_if_known(("coolify", "local_test", "host"), host.get("address"), source)
            ssh = str(host.get("ssh") or "")
            if "@" in ssh:
                user, _, ssh_host = ssh.partition("@")
                builder.set_if_known(("coolify", "local_test", "ssh_user"), user, source)
                builder.set_if_known(("coolify", "local_test", "host"), ssh_host, source)
            builder.set_if_known(("coolify", "local_test", "coolify_url"), host.get("coolify_url"), source)
            builder.set_if_known(("coolify", "local_test", "ssh_port"), 22, source)

    token_path = root / LOCAL_COOLIFY_TOKEN_RELATIVE_PATH
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        builder.set_if_known(
            ("coolify", "local_test", "api_token"),
            token,
            f"file:{LOCAL_COOLIFY_TOKEN_RELATIVE_PATH.as_posix()}",
        )


def populate_remote_coolify_hosts(builder: StateBuilder, root: Path, network_name: str) -> None:
    deployment_relative_path = REMOTE_COOLIFY_DEPLOYMENT_RELATIVE_PATHS.get(network_name)
    if deployment_relative_path is None:
        return
    deployment = read_json(root / deployment_relative_path)
    if not isinstance(deployment, dict):
        return
    servers = deployment.get("servers")
    if not isinstance(servers, list):
        return

    source = f"repo:{deployment_relative_path.as_posix()}"
    name_to_slot = manual_coolify_host_name_to_slot(builder)
    remote_slots: list[str] = []
    for server in servers:
        if not isinstance(server, dict):
            continue
        name = server.get("name")
        if not value_is_known(name):
            continue
        slot = name_to_slot.get(str(name))
        if slot is not None:
            remote_slots.append(slot)

    path = ("networks", network_name, "remote_coolify_hosts")
    if remote_slots:
        builder.set_value(path, remote_slots, source, overwrite=True)
    else:
        builder.delete_path(path)


def manual_coolify_host_slots(builder: StateBuilder) -> set[str]:
    hosts = builder.get(("coolify", "hosts"))
    if not isinstance(hosts, Mapping):
        return set()
    return {str(slot) for slot, payload in hosts.items() if isinstance(payload, Mapping)}


def manual_coolify_host_name_to_slot(builder: StateBuilder) -> dict[str, str]:
    hosts = builder.get(("coolify", "hosts"))
    if not isinstance(hosts, Mapping):
        return {}
    name_to_slot: dict[str, str] = {}
    for slot, payload in hosts.items():
        if isinstance(payload, Mapping) and value_is_known(payload.get("name")):
            name_to_slot[str(payload["name"])] = str(slot)
    return name_to_slot


def sanitize_manual_coolify_host_references(builder: StateBuilder) -> None:
    """Drop stale network references to Coolify slots that do not exist manually."""

    allowed_slots = manual_coolify_host_slots(builder)
    allowed_slots.add("local_test")
    networks = builder.get(("networks",))
    if not isinstance(networks, Mapping):
        return

    for network_name, network_payload in list(networks.items()):
        if not isinstance(network_payload, Mapping):
            continue

        remote_path = ("networks", str(network_name), "remote_coolify_hosts")
        if str(network_name) not in REMOTE_COOLIFY_DEPLOYMENT_RELATIVE_PATHS:
            builder.delete_path(remote_path)
        else:
            remote_hosts = builder.get(remote_path)
            if isinstance(remote_hosts, list):
                filtered = [str(slot) for slot in remote_hosts if str(slot) in allowed_slots and str(slot) != "local_test"]
                if filtered:
                    if filtered != remote_hosts:
                        builder.set_value(remote_path, filtered, "tool:manual-coolify-hosts", overwrite=True)
                else:
                    builder.delete_path(remote_path)

        instances = builder.get(("networks", str(network_name), "hub", "instances"))
        if isinstance(instances, Mapping):
            for hub_id, instance in list(instances.items()):
                if not isinstance(instance, Mapping):
                    continue
                coolify_host = instance.get("coolify_host")
                if value_is_known(coolify_host) and str(coolify_host) not in allowed_slots:
                    builder.delete_path(("networks", str(network_name), "hub", "instances", str(hub_id), "coolify_host"))

        qbft_instances = builder.get(("networks", str(network_name), "qbft", "instances"))
        if isinstance(qbft_instances, Mapping):
            for instance_id, instance in list(qbft_instances.items()):
                if not isinstance(instance, Mapping):
                    continue
                coolify_host = instance.get("coolify_host")
                if value_is_known(coolify_host) and str(coolify_host) not in allowed_slots:
                    builder.delete_path(
                        (
                            "networks",
                            str(network_name),
                            "qbft",
                            "instances",
                            str(instance_id),
                            "coolify_host",
                        )
                    )


def remove_legacy_qbft_summary_fields(builder: StateBuilder) -> None:
    """Drop the old generated QBFT summary shape without touching manual instances."""

    networks = builder.get(("networks",))
    if not isinstance(networks, Mapping):
        return
    for network_name, network_payload in list(networks.items()):
        if not isinstance(network_payload, Mapping):
            continue
        for field in ("coolify_host", "validators", "rpc_port", "validator_p2p_ports"):
            builder.delete_path(("networks", str(network_name), "qbft", field))


def validate_qbft_instances(builder: StateBuilder) -> None:
    """Emit non-fatal structured warnings for manually-owned QBFT topology."""

    allowed_slots = manual_coolify_host_slots(builder)
    allowed_slots.add("local_test")
    networks = builder.get(("networks",))
    if not isinstance(networks, Mapping):
        return

    for network_name, network_payload in list(networks.items()):
        if not isinstance(network_payload, Mapping):
            continue
        instances = builder.get(("networks", str(network_name), "qbft", "instances"))
        if not isinstance(instances, Mapping):
            continue

        ports_by_host: dict[tuple[str, int], list[str]] = {}
        for instance_id, instance in list(instances.items()):
            path = ("networks", str(network_name), "qbft", "instances", str(instance_id))
            if not isinstance(instance, Mapping):
                builder.record_warning("qbft_instance_not_mapping", path, "QBFT instance must be a mapping.")
                continue

            raw_roles = instance.get("roles")
            roles = [str(role).strip().lower() for role in raw_roles] if isinstance(raw_roles, list) else []
            roles = [role for role in roles if role]
            if not roles:
                builder.record_warning("qbft_instance_missing_roles", path + ("roles",), "QBFT instance has no roles.")
            for role in roles:
                if role not in QBFT_ALLOWED_ROLES:
                    builder.record_warning(
                        "qbft_instance_invalid_role",
                        path + ("roles",),
                        "QBFT instance role is not supported.",
                        role=role,
                        allowed_roles=sorted(QBFT_ALLOWED_ROLES),
                    )

            rpc_port = parse_int_maybe(instance.get("rpc_host_port"))
            p2p_port = parse_int_maybe(instance.get("p2p_host_port"))
            if "rpc" in roles and rpc_port is None:
                builder.record_warning(
                    "qbft_rpc_missing_rpc_host_port",
                    path + ("rpc_host_port",),
                    "QBFT instance with rpc role lacks rpc_host_port.",
                )
            if "validator" in roles and p2p_port is None:
                builder.record_warning(
                    "qbft_validator_missing_p2p_host_port",
                    path + ("p2p_host_port",),
                    "QBFT validator instance lacks p2p_host_port.",
                )

            coolify_host = instance.get("coolify_host")
            if value_is_known(coolify_host):
                host_slot = str(coolify_host)
                if host_slot not in allowed_slots:
                    builder.record_warning(
                        "qbft_missing_coolify_host_slot",
                        path + ("coolify_host",),
                        "QBFT instance references a missing coolify.hosts slot.",
                        coolify_host=host_slot,
                    )
                for field, port in (("rpc_host_port", rpc_port), ("p2p_host_port", p2p_port)):
                    if port is None:
                        continue
                    ports_by_host.setdefault((host_slot, port), []).append(".".join((*path, field)))
            else:
                builder.record_warning(
                    "qbft_instance_missing_coolify_host",
                    path + ("coolify_host",),
                    "QBFT instance has no coolify_host placement.",
                )

        for (host_slot, port), paths in sorted(ports_by_host.items()):
            if len(paths) > 1:
                builder.record_warning(
                    "qbft_duplicate_host_port",
                    ("networks", str(network_name), "qbft", "instances"),
                    "Two QBFT instances on the same coolify_host use the same host port.",
                    coolify_host=host_slot,
                    host_port=port,
                    references=paths,
                )


def populate_default_wallets(builder: StateBuilder) -> None:
    for role, payload in ANVIL_DEFAULTS.items():
        for field, value in payload.items():
            builder.set_value(
                ("wallets", "defaults", role, field),
                value,
                "repo:anvil-default-wallets",
                overwrite=True,
            )


def populate_test_network(builder: StateBuilder, root: Path) -> None:
    network = ("networks", "test")
    seed = load_qbft_seed(root, "test")
    source = f"repo:{QBFT_TOOL_RELATIVE_PATH.as_posix()}:NETWORK_SEEDS.test"
    if isinstance(seed, dict):
        builder.set_if_known(network + ("chain_id",), seed.get("chain_id"), source)
        rpc_port = find_rpc_port(seed)
        if rpc_port is not None:
            builder.set_value(network + ("rpc",), f"http://127.0.0.1:{rpc_port}", source)

    add_default_network_wallets(builder, "test", source="repo:anvil-default-wallets")
    builder.set_existing_or_null_network_slot(network + ("wallets", "hub_admin", "address"))
    builder.set_existing_or_null_network_slot(network + ("wallets", "hub_admin", "private_key"))
    ensure_credits(builder, "test")

    add_network_contract_template(builder, "test")


def populate_dev_network(builder: StateBuilder, root: Path) -> None:
    network = ("networks", "dev")
    deployment_path = root / DEV_DEPLOYMENT_RELATIVE_PATH
    deployment = read_json(deployment_path)
    source = f"repo:{DEV_DEPLOYMENT_RELATIVE_PATH.as_posix()}"

    if isinstance(deployment, dict):
        chain = deployment.get("chain")
        if isinstance(chain, dict):
            builder.set_if_known(network + ("chain_id",), chain.get("chain_id"), source)
    
        add_default_network_wallets(builder, "dev", source="repo:anvil-default-wallets")

        hub_admin = deployment.get("hub_admin")
        if isinstance(hub_admin, dict):
            builder.set_if_known(network + ("wallets", "hub_admin", "address"), hub_admin.get("address"), source)
            populate_wallet_secret_from_record(builder, root, network + ("wallets", "hub_admin"), hub_admin)

        smoke_client = deployment.get("smoke_client")
        if isinstance(smoke_client, dict):
            builder.set_if_known(network + ("wallets", "smoke_client", "address"), smoke_client.get("address"), source)
            populate_wallet_secret_from_record(builder, root, network + ("wallets", "smoke_client"), smoke_client)

        contracts = deployment.get("contracts")
        if isinstance(contracts, dict):
            alpha = contracts.get(DEV_CONTRACT_KEYS["AlphaBetaLockout"])
            if isinstance(alpha, dict):
                builder.set_if_known(network + ("contracts", "AlphaBetaLockout", "address"), alpha.get("address"), source)
            escrow = contracts.get(DEV_CONTRACT_KEYS["HubCreditBridgeEscrow"])
            if isinstance(escrow, dict):
                builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "address"), escrow.get("address"), source)
                builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "bridge_controller"), escrow.get("bridge_controller_address"), source)
                builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "owner"), ANVIL_DEFAULTS["deployer"]["address"], "repo:dev-deployer-default")
                builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "paused"), False, "repo:deployment-default")
            reserve = contracts.get(DEV_CONTRACT_KEYS["XLagBridgeReserve"])
            if isinstance(reserve, dict):
                builder.set_if_known(network + ("contracts", "XLagBridgeReserve", "address"), reserve.get("address"), source)

        offices = deployment.get("offices")
        if isinstance(offices, list):
            offices_by_code: dict[str, str] = {}
            for office in offices:
                if isinstance(office, dict) and ADDRESS_RE.match(str(office.get("address") or "")):
                    code = str(office.get("office") or "").lower()
                    offices_by_code[code] = str(office["address"])
            role_for_office = {"o0": "captain", "o1": "o1", "o2": "o2", "o3": "o3"}
            for office_code, role in role_for_office.items():
                if office_code in offices_by_code:
                    builder.set_value(network + ("wallets", role, "address"), offices_by_code[office_code], source)
            crew = [offices_by_code[k] for k in ("o1", "o2", "o3") if k in offices_by_code]
            if "o0" in offices_by_code:
                builder.set_value(network + ("contracts", "XLagBridgeReserve", "captain"), offices_by_code["o0"], source)
            if crew:
                builder.set_value(network + ("contracts", "XLagBridgeReserve", "crew"), crew, source)

    ensure_credits(builder, "dev")
    add_network_contract_template(builder, "dev")



def populate_remote_network(builder: StateBuilder, root: Path, network_name: str) -> None:
    network = ("networks", network_name)
    populate_network_config(builder, root, network_name)
    populate_remote_topology(builder, root, network_name)
    populate_remote_coolify_deployment(builder, root, network_name)
    populate_contract_config(builder, root, network_name)
    populate_deployment_manifest(builder, root, network_name)

    for role in ("deployer", "escrow_owner", "hub_admin", "captain", "o1", "o2", "o3"):
        builder.set_existing_or_null_network_slot(network + ("wallets", role, "address"))
        builder.set_existing_or_null_network_slot(network + ("wallets", role, "private_key"))
    ensure_credits(builder, network_name)
    add_network_contract_template(builder, network_name)


def populate_network_config(builder: StateBuilder, root: Path, network_name: str) -> None:
    config = read_json(root / HUB_NETWORKS_RELATIVE_PATH)
    if not isinstance(config, dict):
        return
    networks = config.get("networks")
    if not isinstance(networks, dict):
        return
    entry = networks.get(network_name)
    if not isinstance(entry, dict):
        return

    source = f"repo:{HUB_NETWORKS_RELATIVE_PATH.as_posix()}"
    network = ("networks", network_name)
    builder.set_if_known(network + ("display_name",), entry.get("display_name"), source)
    builder.set_if_known(network + ("kind",), entry.get("kind"), source)
    builder.set_if_known(network + ("chain_id",), parse_int_maybe(entry.get("chain_id")), source)
    builder.set_if_known(network + ("rpc",), entry.get("chain_rpc_url"), source)
    builder.set_if_known(network + ("hub", "bind_host"), entry.get("hub_bind_host"), source)
    builder.set_if_known(network + ("hub", "bind_port"), parse_int_maybe(entry.get("hub_bind_port")), source)
    builder.set_if_known(network + ("hub", "public_url"), entry.get("hub_public_url"), source)
    builder.set_if_known(network + ("hub", "runtime_dir"), entry.get("hub_runtime_dir"), source)
    builder.set_if_known(network + ("hub", "deployment_manifest_path"), entry.get("deployment_manifest_path"), source)


def populate_remote_topology(builder: StateBuilder, root: Path, network_name: str) -> None:
    topology_relative_path = REMOTE_TOPOLOGY_RELATIVE_PATHS.get(network_name)
    if topology_relative_path is None:
        return
    topology = read_json(root / topology_relative_path)
    if not isinstance(topology, dict):
        return

    source = f"repo:{topology_relative_path.as_posix()}"
    network = ("networks", network_name)
    network_info = topology.get("network")
    if isinstance(network_info, dict):
        builder.set_if_known(network + ("display_name",), network_info.get("network_display_name"), source, overwrite=False)
        builder.set_if_known(network + ("kind",), network_info.get("network_kind"), source, overwrite=False)
        builder.set_if_known(network + ("chain_id",), parse_int_maybe(network_info.get("chain_id")), source, overwrite=False)

    storage = topology.get("storage")
    if isinstance(storage, dict):
        builder.set_if_known(network + ("foundationdb", "cluster_file_path"), storage.get("cluster_file"), source, overwrite=False)
        builder.set_if_known(network + ("foundationdb", "namespace"), storage.get("namespace"), source, overwrite=False)

    entry_urls = topology.get("entry_urls")
    if isinstance(entry_urls, list) and entry_urls:
        builder.set_value(network + ("hub", "entry_urls"), [str(url) for url in entry_urls if value_is_known(url)], source)

    hubs = topology.get("hubs")
    if isinstance(hubs, list):
        for hub in hubs:
            if not isinstance(hub, dict) or not value_is_known(hub.get("hub_id")):
                continue
            hub_id = str(hub["hub_id"])
            base = network + ("hub", "instances", hub_id)
            builder.set_if_known(base + ("public_url",), hub.get("public_url"), source)
            builder.set_if_known(base + ("hub_url",), hub.get("hub_url"), source)
            roles = hub.get("roles")
            if isinstance(roles, list):
                builder.set_value(base + ("roles",), [str(role) for role in roles if value_is_known(role)], source)


def populate_remote_coolify_deployment(builder: StateBuilder, root: Path, network_name: str) -> None:
    deployment_relative_path = REMOTE_COOLIFY_DEPLOYMENT_RELATIVE_PATHS.get(network_name)
    if deployment_relative_path is None:
        return
    deployment = read_json(root / deployment_relative_path)
    if not isinstance(deployment, dict):
        return

    source = f"repo:{deployment_relative_path.as_posix()}"
    network = ("networks", network_name)

    foundationdb = deployment.get("foundationdb")
    if isinstance(foundationdb, dict):
        builder.set_if_known(network + ("foundationdb", "cluster_description"), foundationdb.get("cluster_description"), source)
        builder.set_if_known(network + ("foundationdb", "cluster_id"), scrub_repo_placeholder_value(foundationdb.get("cluster_id")), source)
        builder.set_if_known(network + ("foundationdb", "cluster_file_path"), foundationdb.get("cluster_file_path"), source)
        builder.set_if_known(network + ("foundationdb", "namespace"), foundationdb.get("namespace"), source)
        builder.set_if_known(network + ("foundationdb", "configure"), foundationdb.get("configure"), source)

    name_to_slot: dict[str, str] = {}
    hosts = builder.get(("coolify", "hosts"))
    if isinstance(hosts, Mapping):
        for slot, payload in hosts.items():
            if isinstance(payload, Mapping) and value_is_known(payload.get("name")):
                name_to_slot[str(payload["name"])] = str(slot)

    hubs = deployment.get("hubs")
    if isinstance(hubs, list):
        for hub in hubs:
            if not isinstance(hub, dict) or not value_is_known(hub.get("hub_id")):
                continue
            hub_id = str(hub["hub_id"])
            base = network + ("hub", "instances", hub_id)
            builder.set_if_known(base + ("public_url",), hub.get("public_url"), source)
            coolify_name = str(hub.get("coolify_server") or "")
            if coolify_name in name_to_slot:
                builder.set_value(base + ("coolify_host",), name_to_slot[coolify_name], source)
            builder.set_if_known(base + ("runtime_dir",), hub.get("runtime_dir"), source)
            builder.set_if_known(base + ("cluster_file_path",), hub.get("cluster_file_path"), source)
            builder.set_if_known(base + ("namespace",), hub.get("namespace"), source)

    public_entry_urls = deployment.get("public_entry_urls")
    if isinstance(public_entry_urls, list) and public_entry_urls:
        builder.set_value(network + ("hub", "entry_urls"), [str(url) for url in public_entry_urls if value_is_known(url)], source)


def populate_contract_config(builder: StateBuilder, root: Path, network_name: str) -> None:
    contracts_relative_path = REMOTE_CONTRACTS_RELATIVE_PATHS.get(network_name)
    if contracts_relative_path is None:
        return
    payload = read_json(root / contracts_relative_path)
    if not isinstance(payload, dict):
        return
    source = f"repo:{contracts_relative_path.as_posix()}"
    base = ("networks", network_name, "contracts")
    for contract_name, key in DEV_CONTRACT_KEYS.items():
        address = payload.get(key)
        if ADDRESS_RE.match(str(address or "")):
            builder.set_value(base + (contract_name, "address"), str(address), source)


def populate_deployment_manifest(builder: StateBuilder, root: Path, network_name: str) -> None:
    manifest_path = deployment_manifest_path_for_network(builder, root, network_name)
    if manifest_path is None:
        return
    deployment = read_json(manifest_path)
    if not isinstance(deployment, dict):
        return

    rel = relative_display(manifest_path, root)
    source = f"repo:{rel}"
    network = ("networks", network_name)

    apply_deployment_payload(builder, root, network_name, deployment, source)

    private_manifest_path = private_deployment_manifest_path(root, deployment)
    if private_manifest_path is not None:
        private_payload = read_json(private_manifest_path)
        if isinstance(private_payload, dict):
            private_source = f"file:{relative_display(private_manifest_path, root)}"
            apply_deployment_payload(builder, root, network_name, private_payload, private_source)
            fill_escrow_owner_from_deployer(builder, network_name, private_source)




def apply_deployment_payload(
    builder: StateBuilder,
    root: Path,
    network_name: str,
    deployment: Mapping[str, Any],
    source: str,
) -> None:
    network = ("networks", network_name)

    chain = deployment.get("chain")
    if isinstance(chain, dict):
        builder.set_if_known(network + ("chain_id",), parse_int_maybe(chain.get("chain_id")), source)

    for role in ("hub_admin", "smoke_client", "deployer", "escrow_owner"):
        record = deployment.get(role)
        if isinstance(record, dict):
            populate_wallet_from_record(builder, root, network + ("wallets", role), record, source)

    contracts = deployment.get("contracts")
    if not isinstance(contracts, dict):
        contracts = deployment.get("deployments")
    if isinstance(contracts, dict):
        for contract_name, key in DEV_CONTRACT_KEYS.items():
            record = contracts.get(key)
            if isinstance(record, dict):
                builder.set_if_known(network + ("contracts", contract_name, "address"), record.get("address"), source)

        escrow = contracts.get(DEV_CONTRACT_KEYS["HubCreditBridgeEscrow"])
        if isinstance(escrow, dict):
            builder.set_if_known(
                network + ("contracts", "HubCreditBridgeEscrow", "bridge_controller"),
                escrow.get("bridge_controller_address"),
                source,
            )
            owner = escrow.get("owner") or escrow.get("owner_address")
            builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "owner"), owner, source)
            if "paused" in escrow:
                builder.set_if_known(network + ("contracts", "HubCreditBridgeEscrow", "paused"), escrow.get("paused"), source)

    offices = deployment.get("offices")
    if isinstance(offices, list):
        apply_offices(builder, root, network_name, offices, source)


def private_deployment_manifest_path(root: Path, public_deployment: Mapping[str, Any]) -> Path | None:
    run_id = public_deployment.get("run_id")
    candidates: list[Path] = []
    if isinstance(run_id, str) and run_id.strip():
        candidates.append(root / "runtime" / "dev-chain" / "runs" / run_id.strip() / "deploy.json")
    candidates.append(root / "runtime" / "dev-chain" / "latest.json")
    public_env = str(public_deployment.get("environment") or "").strip()
    public_run_id = str(run_id or "").strip()
    for candidate in candidates:
        if not candidate.exists():
            continue
        payload = read_json(candidate)
        if not isinstance(payload, dict):
            continue
        candidate_run_id = str(payload.get("run_id") or "").strip()
        candidate_env = str(payload.get("environment") or "").strip()
        if public_run_id and candidate_run_id and candidate_run_id != public_run_id:
            continue
        if public_env and candidate_env and candidate_env != public_env:
            continue
        return candidate
    return None


def fill_escrow_owner_from_deployer(builder: StateBuilder, network_name: str, source: str) -> None:
    network = ("networks", network_name)
    deployer_address = builder.get(network + ("wallets", "deployer", "address"))
    deployer_key = builder.get(network + ("wallets", "deployer", "private_key"))

    if value_is_known(deployer_address):
        builder.set_value(network + ("wallets", "escrow_owner", "address"), deployer_address, source, overwrite=False)
        builder.set_value(
            network + ("contracts", "HubCreditBridgeEscrow", "owner"),
            deployer_address,
            source,
            overwrite=False,
        )
    if value_is_known(deployer_key):
        builder.set_value(network + ("wallets", "escrow_owner", "private_key"), deployer_key, source, overwrite=False)


def deployment_manifest_path_for_network(builder: StateBuilder, root: Path, network_name: str) -> Path | None:
    configured = builder.get(("networks", network_name, "hub", "deployment_manifest_path"))
    candidates: list[Path] = []
    if isinstance(configured, str) and configured:
        candidates.append(root / configured)
    candidates.append(root / "runtime" / "deployments" / network_name / "latest.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def apply_offices(builder: StateBuilder, root: Path, network_name: str, offices: list[Any], source: str) -> None:
    network = ("networks", network_name)
    offices_by_code: dict[str, str] = {}
    role_for_office = {"o0": "captain", "o1": "o1", "o2": "o2", "o3": "o3"}

    for office in offices:
        if not isinstance(office, dict):
            continue
        code = str(office.get("office") or "").lower()
        role = role_for_office.get(code)
        address = office.get("address")
        if role is not None and ADDRESS_RE.match(str(address or "")):
            offices_by_code[code] = str(address)
            base_path = network + ("wallets", role)
            builder.set_value(base_path + ("address",), str(address), source)
            populate_office_private_key(builder, root, base_path, office, source)

    crew = [offices_by_code[k] for k in ("o1", "o2", "o3") if k in offices_by_code]
    if "o0" in offices_by_code:
        builder.set_value(network + ("contracts", "XLagBridgeReserve", "captain"), offices_by_code["o0"], source)
    if crew:
        builder.set_value(network + ("contracts", "XLagBridgeReserve", "crew"), crew, source)


def populate_office_private_key(
    builder: StateBuilder,
    root: Path,
    base_path: tuple[str, ...],
    office: Mapping[str, Any],
    source: str,
) -> None:
    private_key = office.get("private_key")
    if PRIVATE_KEY_RE.match(str(private_key or "")):
        builder.set_value(base_path + ("private_key",), str(private_key), source)
        return

    wallet_path_value = office.get("wallet_path")
    if not isinstance(wallet_path_value, str) or not wallet_path_value:
        return
    wallet_path = root / wallet_path_value
    payload = read_json(wallet_path)
    if not isinstance(payload, dict):
        return
    wallets = payload.get("wallets")
    if not isinstance(wallets, list):
        return

    expected_wallet_id = str(office.get("wallet_id") or "").strip()
    expected_address = str(office.get("address") or "").lower()
    matched: Mapping[str, Any] | None = None
    for item in wallets:
        if not isinstance(item, dict):
            continue
        wallet_id = str(item.get("wallet_id") or "").strip()
        address = str(item.get("address") or "").lower()
        if expected_wallet_id and wallet_id == expected_wallet_id:
            matched = item
            break
        if expected_address and address == expected_address:
            matched = item
            break

    if matched is None:
        return
    private_key = matched.get("private_key")
    if PRIVATE_KEY_RE.match(str(private_key or "")):
        builder.set_value(base_path + ("private_key",), str(private_key), f"file:{Path(wallet_path_value).as_posix()}")
    address = matched.get("address")
    if ADDRESS_RE.match(str(address or "")):
        builder.set_value(base_path + ("address",), str(address), f"file:{Path(wallet_path_value).as_posix()}", overwrite=False)



def add_default_network_wallets(builder: StateBuilder, network_name: str, *, source: str) -> None:
    for role in ("deployer", "captain", "o1", "o2", "o3"):
        payload = ANVIL_DEFAULTS[role]
        builder.set_value(("networks", network_name, "wallets", role, "address"), payload["address"], source)
        builder.set_value(("networks", network_name, "wallets", role, "private_key"), payload["private_key"], source)
        builder.set_value(("networks", network_name, "wallets", role, "credits"), 0, "template", overwrite=False)


def ensure_credits(builder: StateBuilder, network_name: str) -> None:
    wallets = builder.get(("networks", network_name, "wallets"))
    if not isinstance(wallets, Mapping):
        return
    for role in wallets:
        builder.set_value(("networks", network_name, "wallets", str(role), "credits"), 0, "template", overwrite=False)


def add_network_contract_template(builder: StateBuilder, network_name: str) -> None:
    base = ("networks", network_name, "contracts")
    for contract_name in MAIN_CONTRACTS:
        builder.set_existing_or_null_network_slot(base + (contract_name, "address"))
        builder.set_existing_or_null_network_slot(base + (contract_name, "version"))

    escrow = base + ("HubCreditBridgeEscrow",)
    for field in ("owner", "bridge_controller", "paused"):
        builder.set_existing_or_null_network_slot(escrow + (field,))

    reserve = base + ("XLagBridgeReserve",)
    for field in ("captain", "crew"):
        builder.set_existing_or_null_network_slot(reserve + (field,))


def populate_wallet_from_record(
    builder: StateBuilder,
    root: Path,
    base_path: tuple[str, ...],
    record: Mapping[str, Any],
    source: str,
) -> None:
    address = record.get("address")
    if not value_is_known(address):
        inferred = address_for_known_private_key(record.get("private_key"))
        if inferred is not None:
            address = inferred
    builder.set_if_known(base_path + ("address",), address, source)
    populate_wallet_secret_from_record(builder, root, base_path, record, inline_source=source)


def populate_wallet_secret_from_record(
    builder: StateBuilder,
    root: Path,
    base_path: tuple[str, ...],
    record: Mapping[str, Any],
    *,
    inline_source: str = "existing-state",
) -> None:
    inline_private_key = record.get("private_key")
    if PRIVATE_KEY_RE.match(str(inline_private_key or "")):
        builder.set_value(base_path + ("private_key",), str(inline_private_key), inline_source)
        inferred = address_for_known_private_key(inline_private_key)
        if inferred is not None:
            builder.set_value(base_path + ("address",), inferred, inline_source, overwrite=False)
        return

    wallet_path_value = record.get("wallet_path")
    if not isinstance(wallet_path_value, str) or not wallet_path_value:
        builder.set_existing_or_null_network_slot(base_path + ("private_key",))
        return
    wallet_path = root / wallet_path_value
    payload = read_json(wallet_path)
    if isinstance(payload, dict) and PRIVATE_KEY_RE.match(str(payload.get("private_key") or "")):
        source = f"file:{Path(wallet_path_value).as_posix()}"
        builder.set_value(base_path + ("private_key",), str(payload["private_key"]), source)
        if ADDRESS_RE.match(str(payload.get("address") or "")):
            builder.set_value(base_path + ("address",), str(payload["address"]), source)
    else:
        builder.set_existing_or_null_network_slot(base_path + ("private_key",))


def address_for_known_private_key(private_key: Any) -> str | None:
    if not PRIVATE_KEY_RE.match(str(private_key or "")):
        return None
    lowered = str(private_key).lower()
    for payload in ANVIL_DEFAULTS.values():
        if str(payload.get("private_key", "")).lower() == lowered:
            return str(payload["address"])
    return None





def should_block_write_for_live_mismatch(path: tuple[str, ...]) -> bool:
    if len(path) < 2 or path[0] != "networks":
        return False
    if "last_seen" in path:
        return False
    return True


def format_write_refusal(unresolved: list[dict[str, Any]]) -> str:
    lines = [
        "Refusing to write because live checks found repo/local values that disagree with remote values.",
        "Choose an explicit policy for each affected network, for example:",
        "  python tools/sync_private_state.py --prefer-remote testnet --write",
        "  python tools/sync_private_state.py --prefer-local testnet --write",
        "",
        "Mismatches:",
    ]
    for mismatch in unresolved[:20]:
        path = ".".join(str(part) for part in mismatch.get("path", ()))
        network = mismatch.get("network") or "unknown"
        local = summarize_value_for_provenance(mismatch.get("local"))
        live = summarize_value_for_provenance(mismatch.get("live"))
        source = mismatch.get("source") or "live"
        lines.append(f"  - {network}: {path}: local={local}; remote={live}; source={source}")
    if len(unresolved) > 20:
        lines.append(f"  ... and {len(unresolved) - 20} more")
    return "\n".join(lines)


def check_live_state(builder: StateBuilder, root: Path, *, networks: tuple[str, ...], timeout_s: float) -> None:
    """Merge live probe results into the private state.

    This is intentionally opt-in because it can touch local containers, public
    RPC endpoints, remote Hubs, and Coolify APIs.  When a live value disagrees
    with the current repo/private-state value, the live value wins and the
    provenance comment records that the local value was out of date.
    """

    check_live_coolify(builder, timeout_s=timeout_s)
    for network_name in networks:
        if not isinstance(network_name, str) or not network_name:
            continue
        check_live_network(builder, network_name, timeout_s=timeout_s)


def check_live_coolify(builder: StateBuilder, *, timeout_s: float) -> None:
    hosts = builder.get(("coolify", "hosts"))
    if isinstance(hosts, Mapping):
        for slot, payload in hosts.items():
            if not isinstance(payload, Mapping):
                continue
            base = ("coolify", "hosts", str(slot))
            url = payload.get("url") or payload.get("coolify_url")
            token = payload.get("api_token")
            check_one_live_coolify(builder, base, url=url, token=token, source=f"live:coolify:{slot}", timeout_s=timeout_s)

    local = builder.get(("coolify", "local_test"))
    if isinstance(local, Mapping):
        check_one_live_coolify(
            builder,
            ("coolify", "local_test"),
            url=local.get("url") or local.get("coolify_url"),
            token=local.get("api_token"),
            source="live:coolify:local_test",
            timeout_s=timeout_s,
        )


def check_one_live_coolify(
    builder: StateBuilder,
    base: tuple[str, ...],
    *,
    url: Any,
    token: Any,
    source: str,
    timeout_s: float,
) -> None:
    if not value_is_known(url) or not value_is_known(token):
        return
    response = http_json_get(join_url(str(url), "/api/v1/version"), token=str(token), timeout_s=timeout_s)
    ok = response.get("ok") is True
    set_live_value(builder, base + ("api_reachable",), bool(ok), source)
    if ok:
        body = response.get("body")
        version = None
        if isinstance(body, Mapping):
            for key in ("version", "coolify", "data"):
                candidate = body.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    version = candidate.strip()
                    break
            if version is None and body:
                version = json.dumps(body, sort_keys=True)
        elif isinstance(body, str) and body.strip():
            version = body.strip()
        if value_is_known(version):
            set_live_value(builder, base + ("api_version",), version, source)
        set_live_value(builder, base + ("last_seen",), "ok", source)
    else:
        status = response.get("status")
        message = response.get("error") or response.get("message") or "request_failed"
        set_live_value(builder, base + ("last_seen",), f"failed:{status}:{message}", source)


def check_live_network(builder: StateBuilder, network_name: str, *, timeout_s: float) -> None:
    check_live_hub(builder, network_name, timeout_s=timeout_s)
    check_live_chain(builder, network_name, timeout_s=timeout_s)


def check_live_hub(builder: StateBuilder, network_name: str, *, timeout_s: float) -> None:
    urls = live_hub_urls(builder, network_name)
    if not urls:
        return

    failures: list[str] = []
    for url in urls:
        health = http_json_get(join_url(url, "/api/hub/v1/health"), token=None, timeout_s=timeout_s)
        if health.get("ok") is not True:
            failures.append(f"{url}:health:{health.get('status')}:{health.get('error') or health.get('message')}")
            continue
        status = http_json_get(join_url(url, "/api/hub/v1/status"), token=None, timeout_s=timeout_s)
        if status.get("ok") is not True or not isinstance(status.get("body"), Mapping):
            failures.append(f"{url}:status:{status.get('status')}:{status.get('error') or status.get('message')}")
            continue
        apply_live_hub_status(builder, network_name, str(url), status["body"])
        set_live_value(builder, ("networks", network_name, "last_seen", "hub"), f"ok:{url}", f"live:hub:{network_name}")
        return

    if failures:
        set_live_value(
            builder,
            ("networks", network_name, "last_seen", "hub"),
            "failed:" + "; ".join(failures[:3]),
            f"live:hub:{network_name}",
        )


def live_hub_urls(builder: StateBuilder, network_name: str) -> list[str]:
    network = ("networks", network_name)
    candidates: list[Any] = [
        builder.get(network + ("hub", "public_url")),
    ]

    entry_urls = builder.get(network + ("hub", "entry_urls"))
    if isinstance(entry_urls, list):
        candidates.extend(entry_urls)

    instances = builder.get(network + ("hub", "instances"))
    if isinstance(instances, Mapping):
        for payload in instances.values():
            if isinstance(payload, Mapping):
                candidates.append(payload.get("public_url"))
                candidates.append(payload.get("hub_url"))

    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not value_is_known(candidate):
            continue
        url = str(candidate).strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def apply_live_hub_status(builder: StateBuilder, network_name: str, hub_url: str, status: Mapping[str, Any]) -> None:
    source = f"live:hub:{hub_url}"
    network_path = ("networks", network_name)

    network = status.get("network")
    if isinstance(network, Mapping):
        set_live_value(builder, network_path + ("chain_id",), parse_int_maybe(network.get("chain_id")), source)
        set_live_value(builder, network_path + ("rpc",), network.get("chain_rpc_url"), source)
        set_live_value(builder, network_path + ("hub", "public_url"), network.get("hub_public_url") or network.get("hub_url"), source)
        set_live_value(builder, network_path + ("hub", "bind_host"), network.get("hub_bind_host") or network.get("hub_host"), source)
        set_live_value(builder, network_path + ("hub", "bind_port"), parse_int_maybe(network.get("hub_bind_port") or network.get("hub_port")), source)
        set_live_value(builder, network_path + ("hub", "runtime_dir"), network.get("hub_runtime_dir"), source)

    bridge = status.get("bridge_backend")
    if isinstance(bridge, Mapping):
        escrow = bridge.get("escrow_address")
        controller = bridge.get("bridge_controller_address")
        set_live_value(builder, network_path + ("contracts", "HubCreditBridgeEscrow", "address"), escrow, source)
        set_live_value(builder, network_path + ("contracts", "HubCreditBridgeEscrow", "bridge_controller"), controller, source)
        set_live_value(builder, network_path + ("wallets", "hub_admin", "address"), controller, source)


def check_live_chain(builder: StateBuilder, network_name: str, *, timeout_s: float) -> None:
    network = ("networks", network_name)
    rpc = builder.get(network + ("rpc",))
    if not value_is_known(rpc):
        return
    rpc_url = str(rpc).strip()
    source = f"live:rpc:{network_name}"

    chain_id_result = rpc_json(rpc_url, "eth_chainId", [], timeout_s=timeout_s)
    if not chain_id_result.get("ok"):
        set_live_value(
            builder,
            network + ("last_seen", "chain_rpc"),
            f"failed:{chain_id_result.get('error') or chain_id_result.get('message')}",
            source,
        )
        return

    chain_id = parse_rpc_quantity(chain_id_result.get("result"))
    if chain_id is not None:
        set_live_value(builder, network + ("chain_id",), chain_id, source)
    set_live_value(builder, network + ("last_seen", "chain_rpc"), "ok", source)

    contracts = builder.get(network + ("contracts",))
    if not isinstance(contracts, Mapping):
        return

    any_contract_checked = False
    for contract_name in MAIN_CONTRACTS:
        contract = contracts.get(contract_name)
        if not isinstance(contract, Mapping):
            continue
        address = contract.get("address")
        if not ADDRESS_RE.match(str(address or "")):
            continue
        any_contract_checked = True
        contract_path = network + ("contracts", contract_name)
        code_result = rpc_json(rpc_url, "eth_getCode", [str(address), "latest"], timeout_s=timeout_s)
        if code_result.get("ok"):
            code = str(code_result.get("result") or "")
            set_live_value(builder, contract_path + ("code_present",), bool(code and code != "0x"), source)
        if contract_name == "HubCreditBridgeEscrow":
            owner = live_contract_address_call(rpc_url, str(address), OWNER_SELECTOR, timeout_s=timeout_s)
            if owner is not None:
                set_live_value(builder, contract_path + ("owner",), owner, source)
                set_live_value(builder, network + ("wallets", "escrow_owner", "address"), owner, source)
            paused = live_contract_bool_call(rpc_url, str(address), PAUSED_SELECTOR, timeout_s=timeout_s)
            if paused is not None:
                set_live_value(builder, contract_path + ("paused",), paused, source)

    if any_contract_checked:
        set_live_value(builder, network + ("last_seen", "contracts"), "ok", source)


def live_contract_address_call(rpc_url: str, contract_address: str, selector: str, *, timeout_s: float) -> str | None:
    result = rpc_json(
        rpc_url,
        "eth_call",
        [{"to": contract_address, "data": selector}, "latest"],
        timeout_s=timeout_s,
    )
    if not result.get("ok"):
        return None
    raw = str(result.get("result") or "")
    if not raw.startswith("0x") or len(raw) < 66:
        return None
    address_hex = raw[-40:]
    if re.fullmatch(r"[0-9a-fA-F]{40}", address_hex):
        return "0x" + address_hex
    return None


def live_contract_bool_call(rpc_url: str, contract_address: str, selector: str, *, timeout_s: float) -> bool | None:
    result = rpc_json(
        rpc_url,
        "eth_call",
        [{"to": contract_address, "data": selector}, "latest"],
        timeout_s=timeout_s,
    )
    if not result.get("ok"):
        return None
    raw = str(result.get("result") or "")
    if not raw.startswith("0x"):
        return None
    try:
        return int(raw, 16) != 0
    except ValueError:
        return None


def set_live_value(builder: StateBuilder, path: tuple[str, ...], value: Any, source: str) -> None:
    if not value_is_known(value):
        return
    current = builder.get(path)
    existing_source = builder.provenance.get(path)
    live_source = source
    if value_is_known(current):
        if values_equivalent(current, value):
            if existing_source and "local-out-of-date" in existing_source:
                live_source = f"{existing_source}; confirmed-by {source}"
            elif existing_source and "preferred-local" in existing_source:
                live_source = f"{existing_source}; confirmed-by {source}"
            else:
                live_source = f"{source}; agrees-with-local"
        else:
            preference = builder.live_preference_for_path(path)
            builder.record_live_mismatch(path, current, value, source)
            if preference == "local":
                local_source = existing_source or "existing-state"
                builder.provenance[path] = (
                    f"{local_source}; preferred-local; remote-disagrees:{source}: "
                    f"was {summarize_value_for_provenance(value)}"
                )
                return
            live_source = f"{source}; local-out-of-date: was {summarize_value_for_provenance(current)}"
            if preference == "remote":
                live_source = f"{live_source}; preferred-remote"
    builder.set_value(path, value, live_source, overwrite=True)


def values_equivalent(left: Any, right: Any) -> bool:
    if ADDRESS_RE.match(str(left or "")) and ADDRESS_RE.match(str(right or "")):
        return str(left).lower() == str(right).lower()
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    left_int = parse_int_maybe(left)
    right_int = parse_int_maybe(right)
    if left_int is not None and right_int is not None:
        return left_int == right_int
    return left == right


def summarize_value_for_provenance(value: Any) -> str:
    if isinstance(value, str):
        if PRIVATE_KEY_RE.match(value) or len(value) > 80:
            return "<redacted-or-long>"
        return value
    if isinstance(value, (int, bool)) or value is None:
        return str(value).lower() if isinstance(value, bool) else str(value)
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, Mapping):
        return "mapping"
    return str(value)


def rpc_json(rpc_url: str, method: str, params: list[Any], *, timeout_s: float) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = http_json_request(
        "POST",
        rpc_url,
        body=payload,
        token=None,
        timeout_s=timeout_s,
    )
    if response.get("ok") is not True:
        return response
    body = response.get("body")
    if not isinstance(body, Mapping):
        return {"ok": False, "error": "non_json_rpc_body", "message": "RPC response was not a JSON object"}
    if body.get("error"):
        return {"ok": False, "error": "json_rpc_error", "message": str(body.get("error"))}
    return {"ok": True, "result": body.get("result")}


def parse_rpc_quantity(value: Any) -> int | None:
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16)
        except ValueError:
            return None
    return parse_int_maybe(value)


def http_json_get(url: str, *, token: str | None, timeout_s: float) -> dict[str, Any]:
    return http_json_request("GET", url, body=None, token=token, timeout_s=timeout_s)


def http_json_request(
    method: str,
    url: str,
    *,
    body: Any | None,
    token: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    data = None
    headers = {"Accept": "application/json,text/plain,*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(str(url), data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= int(response.status) < 300,
                "status": int(response.status),
                "body": parse_http_response_body(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "body": parse_http_response_body(raw),
            "error": "http_error",
            "message": str(exc),
        }
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "body": None,
            "error": type(exc).__name__,
            "message": str(exc),
        }


def parse_http_response_body(raw: str) -> Any:
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def join_url(base: str, path: str) -> str:
    clean_base = str(base or "").strip().rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    return clean_base + clean_path

def sensitive_local_secret_values(state: Mapping[str, Any]) -> list[str]:
    """Return unique values that should be denylisted in local.secrets.

    Sensitive network wallet private keys are generated outside the repo and must
    never become committable.  Manual Coolify public IPs are operator-owned
    infrastructure coordinates, so they are treated as local-only secrets too.
    """

    values: list[str] = []
    seen: set[str] = set()

    for value in sensitive_wallet_private_keys(state):
        if value not in seen:
            seen.add(value)
            values.append(value)

    for value in manual_coolify_host_ip_values(state):
        if value not in seen:
            seen.add(value)
            values.append(value)

    return values


def sensitive_wallet_private_keys(state: Mapping[str, Any]) -> list[str]:
    """Return unique testnet/mainnet wallet private keys found in state."""

    keys: list[str] = []
    seen: set[str] = set()
    networks = state.get("networks")
    if not isinstance(networks, Mapping):
        return keys

    for network_name in SENSITIVE_WALLET_NETWORKS:
        network = networks.get(network_name)
        if not isinstance(network, Mapping):
            continue
        wallets = network.get("wallets")
        if not isinstance(wallets, Mapping):
            continue
        for wallet in wallets.values():
            if not isinstance(wallet, Mapping):
                continue
            private_key = str(wallet.get("private_key") or "").strip()
            if PRIVATE_KEY_RE.match(private_key) and private_key not in seen:
                seen.add(private_key)
                keys.append(private_key)
    return keys


def manual_coolify_host_ip_values(state: Mapping[str, Any]) -> list[str]:
    """Return unique local-secret IP addresses from manual Coolify host slots.

    10.x VPN addresses are intentionally excluded: they are topology coordinates
    that are safe to commit with repo deployment config, while public Coolify IPs
    remain local-only operator details.
    """

    values: list[str] = []
    seen: set[str] = set()
    coolify = state.get("coolify")
    if not isinstance(coolify, Mapping):
        return values
    hosts = coolify.get("hosts")
    if not isinstance(hosts, Mapping):
        return values

    for payload in hosts.values():
        if not isinstance(payload, Mapping):
            continue
        candidates = [
            payload.get("public_ip"),
            payload.get("vpn_ip"),
            payload.get("host"),
            ip_from_url(payload.get("url")),
            ip_from_url(payload.get("coolify_url")),
        ]
        for candidate in candidates:
            value = normalize_ip_address(candidate)
            if value is not None and not is_commit_safe_vpn_ip(value) and value not in seen:
                seen.add(value)
                values.append(value)

    return values


def is_commit_safe_vpn_ip(value: str) -> bool:
    return value.startswith("10.")


def ip_from_url(value: Any) -> str | None:
    if not value_is_known(value):
        return None
    try:
        parsed = urllib.parse.urlparse(str(value))
    except ValueError:
        return None
    return parsed.hostname


def normalize_ip_address(value: Any) -> str | None:
    if not value_is_known(value):
        return None
    text = str(value).strip()
    if not IP_ADDRESS_RE.match(text):
        return None
    parts = text.split(".")
    try:
        octets = [int(part) for part in parts]
    except ValueError:
        return None
    if all(0 <= octet <= 255 for octet in octets):
        return ".".join(str(octet) for octet in octets)
    return None


def ensure_local_secrets_for_sensitive_values(root: Path, state: Mapping[str, Any]) -> int:
    """Append sensitive wallet keys and Coolify IPs to local.secrets on --write."""

    values = sensitive_local_secret_values(state)
    if not values:
        return 0

    secrets_path = root / LOCAL_SECRETS_RELATIVE_PATH
    existing_values: set[str] = set()
    existing_text = ""
    if secrets_path.exists():
        existing_text = secrets_path.read_text(encoding="utf-8")
        existing_values = {line.strip() for line in existing_text.splitlines() if line.strip()}

    missing = [value for value in values if value not in existing_values]
    if not missing:
        return 0

    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    with secrets_path.open("a", encoding="utf-8") as handle:
        if existing_text and not existing_text.endswith("\n"):
            handle.write("\n")
        for value in missing:
            handle.write(value + "\n")
    try:
        secrets_path.chmod(0o600)
    except OSError:
        pass
    return len(missing)


def parse_int_maybe(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[0-9]+", text):
            return int(text)
    return None


def scrub_repo_placeholder_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered or "replace-with" in lowered or "replace_with" in lowered:
            return None
    return value


def find_rpc_port(seed: Mapping[str, Any]) -> int | None:
    services = seed.get("services")
    if not isinstance(services, list):
        return None
    rpc_services = [svc for svc in services if isinstance(svc, dict) and svc.get("role") == "rpc"]
    if not rpc_services:
        rpc_services = [svc for svc in services if isinstance(svc, dict) and svc.get("id") == "rpc-1"]
    if not rpc_services:
        return None
    port = rpc_services[0].get("rpc_host_port")
    return int(port) if isinstance(port, int) else None


def load_qbft_seed(root: Path, name: str) -> dict[str, Any]:
    module_path = root / QBFT_TOOL_RELATIVE_PATH
    if not module_path.exists():
        return {}
    spec = importlib.util.spec_from_file_location("_main_computer_coolify_qbft_network", module_path)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        return {}
    seeds = getattr(module, "NETWORK_SEEDS", {})
    seed = seeds.get(name) if isinstance(seeds, Mapping) else None
    return copy.deepcopy(seed) if isinstance(seed, dict) else {}


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("PyYAML is required to read the private state YAML.") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise SystemExit(f"State file must contain a YAML mapping: {path}")
    return data


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def scrub_placeholders(value: Any) -> Any:
    if isinstance(value, str) and PLACEHOLDER_RE.match(value.strip()):
        return None
    if isinstance(value, list):
        cleaned = []
        for item in value:
            scrubbed = scrub_placeholders(item)
            if scrubbed is not None:
                cleaned.append(scrubbed)
        return cleaned
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            scrubbed = scrub_placeholders(item)
            if scrubbed is not None:
                cleaned[str(key)] = scrubbed
        return cleaned
    return value


def mark_existing_provenance(value: Any, provenance: dict[tuple[str, ...], str], path: tuple[str, ...]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            mark_existing_provenance(item, provenance, path + (str(key),))
    elif isinstance(value, list):
        provenance[path] = "existing-state"
    elif value_is_known(value):
        provenance[path] = "existing-state"


def mark_provenance_for_value(value: Any, provenance: dict[tuple[str, ...], str], path: tuple[str, ...], source: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            mark_provenance_for_value(item, provenance, path + (str(key),), source)
    elif isinstance(value, list):
        provenance[path] = source
    else:
        provenance[path] = source


def value_is_known(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        return bool(text) and not PLACEHOLDER_RE.match(text)
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def ensure_mapping_path(root: MutableMapping[str, Any], path: tuple[str, ...]) -> MutableMapping[str, Any]:
    node: MutableMapping[str, Any] = root
    for part in path:
        current = node.get(part)
        if not isinstance(current, MutableMapping):
            current = {}
            node[part] = current
        node = current
    return node


def redact_state(value: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        return {str(k): redact_state(v, path + (str(k),)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_state(item, path) for item in value]
    if path and path[-1] in SENSITIVE_KEYS and value_is_known(value):
        return "<redacted>"
    return value


def order_mapping(value: Any, path: tuple[str, ...]) -> Any:
    if isinstance(value, list):
        return [order_mapping(item, path) for item in value]
    if not isinstance(value, Mapping):
        return value

    preferred = preferred_order_for_path(path)
    result: dict[str, Any] = {}
    for key in preferred:
        if key in value:
            result[key] = order_mapping(value[key], path + (key,))

    remaining = [str(k) for k in value.keys() if str(k) not in result]
    if path == ("coolify", "hosts"):
        remaining.sort(key=host_slot_sort_key)
    else:
        remaining.sort()

    for key in remaining:
        result[key] = order_mapping(value[key], path + (key,))
    return result


def preferred_order_for_path(path: tuple[str, ...]) -> list[str]:
    if path in PREFERRED_ORDER:
        return PREFERRED_ORDER[path]
    if len(path) == 3 and path[:2] == ("coolify", "hosts"):
        return PREFERRED_ORDER.get(("coolify", "hosts", "*"), [])
    wildcard = tuple("*" if index % 2 == 1 and path[index - 1] in {"networks", "wallets", "contracts"} else part for index, part in enumerate(path))
    if wildcard in PREFERRED_ORDER:
        return PREFERRED_ORDER[wildcard]
    if len(path) >= 5 and path[0] == "networks" and path[2] in {"hub", "qbft"} and path[3] == "instances":
        collapsed = ("networks", "*", path[2], "instances", "*") + path[5:]
        if collapsed in PREFERRED_ORDER:
            return PREFERRED_ORDER[collapsed]
    if len(path) >= 2 and path[0] == "networks":
        collapsed = ("networks", "*") + path[2:]
        if collapsed in PREFERRED_ORDER:
            return PREFERRED_ORDER[collapsed]
    if len(path) >= 4 and path[0] == "networks" and path[2] in {"wallets", "contracts"}:
        collapsed = ("networks", "*", path[2], "*") + path[4:]
        if collapsed in PREFERRED_ORDER:
            return PREFERRED_ORDER[collapsed]
    return []


def emit_yaml(value: Any, provenance: dict[tuple[str, ...], str]) -> str:
    lines: list[str] = []
    emit_node(lines, value, (), 0, provenance)
    return "\n".join(lines).rstrip() + "\n"


def emit_node(lines: list[str], value: Any, path: tuple[str, ...], indent: int, provenance: dict[tuple[str, ...], str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            child_path = path + (key_text,)
            prefix = " " * indent + f"{key_text}:"
            if isinstance(item, Mapping):
                lines.append(prefix)
                emit_node(lines, item, child_path, indent + 2, provenance)
            elif isinstance(item, list):
                if can_emit_flow_list(item):
                    line = prefix + " " + format_flow_list(item)
                    lines.append(with_provenance_comment(line, provenance, child_path))
                elif not item:
                    line = prefix + " []"
                    lines.append(with_provenance_comment(line, provenance, child_path))
                else:
                    lines.append(with_provenance_comment(prefix, provenance, child_path))
                    for entry in item:
                        if isinstance(entry, Mapping):
                            lines.append(" " * (indent + 2) + "-")
                            emit_node(lines, entry, child_path, indent + 4, provenance)
                        else:
                            lines.append(" " * (indent + 2) + f"- {format_scalar(entry)}")
            else:
                if isinstance(item, str) and "\n" in item:
                    line = prefix + " |"
                    lines.append(with_provenance_comment(line, provenance, child_path))
                    for block_line in item.splitlines():
                        lines.append(" " * (indent + 2) + block_line)
                else:
                    line = prefix + " " + format_scalar(item)
                    lines.append(with_provenance_comment(line, provenance, child_path))
    else:
        lines.append(" " * indent + format_scalar(value))


def with_provenance_comment(line: str, provenance: dict[tuple[str, ...], str], path: tuple[str, ...]) -> str:
    source = provenance.get(path)
    if not source:
        return line
    comment = f"# provenance: {source}"
    if len(line) >= PROVENANCE_COMMENT_COLUMN:
        return f"{line}  {comment}"
    return f"{line}{' ' * (PROVENANCE_COMMENT_COLUMN - len(line))}{comment}"


def can_emit_flow_list(value: list[Any]) -> bool:
    return all(not isinstance(item, (Mapping, list)) and not (isinstance(item, str) and "\n" in item) for item in value)


def format_flow_list(value: list[Any]) -> str:
    return "[" + ", ".join(format_scalar(item) for item in value) + "]"


def format_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = str(value)
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / "tools").is_dir() and (candidate / "contracts").exists() and (candidate / ".gitignore").exists():
            return candidate
    raise SystemExit("Could not find repository root from current directory.")


def relative_display(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
