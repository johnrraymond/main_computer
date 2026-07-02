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
import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any


STATE_RELATIVE_PATH = Path("runtime") / "state" / "main_computer.private.yaml"

TOPOLOGY_RELATIVE_PATH = Path("deploy") / "hub-topology" / "testnet-coolify-deployment.json"
QBFT_TOOL_RELATIVE_PATH = Path("tools") / "coolify_qbft_network.py"
DEV_DEPLOYMENT_RELATIVE_PATH = Path("runtime") / "deployments" / "dev" / "latest.json"
LOCAL_COOLIFY_TOKEN_RELATIVE_PATH = Path("runtime") / "coolify-local-docker" / "api-token.txt"

PLACEHOLDER_RE = re.compile(r"^<[^>\n]+>$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")

SENSITIVE_KEYS = {"private_key", "ssh_private_key", "password", "api_token"}
PROVENANCE_COMMENT_COLUMN = 96

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
    ("coolify",): ["primary", "secondary", "local_test"],
    ("wallets",): ["defaults"],
    ("wallets", "defaults"): ["deployer", "captain", "o1", "o2", "o3"],
    ("networks",): ["test", "dev", "local", "testnet", "mainnet"],
    ("networks", "*"): ["chain_id", "rpc", "qbft", "wallets", "contracts", "last_seen"],
    ("networks", "*", "qbft"): ["coolify_host", "validators", "rpc_port", "validator_p2p_ports"],
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
    ("networks", "*", "contracts", "AlphaBetaLockout"): ["address", "version"],
    ("networks", "*", "contracts", "HubCreditBridgeEscrow"): [
        "address",
        "version",
        "owner",
        "bridge_controller",
        "paused",
    ],
    ("networks", "*", "contracts", "XLagBridgeReserve"): ["address", "version", "captain", "crew"],
}


class StateBuilder:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.state: dict[str, Any] = scrub_placeholders(existing or {})
        if not isinstance(self.state, dict):
            self.state = {}
        self.provenance: dict[tuple[str, ...], str] = {}
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync repo-known facts into runtime/state/main_computer.private.yaml.")
    parser.add_argument("--state", type=Path, default=None, help="Private YAML path. Defaults to runtime/state/main_computer.private.yaml.")
    parser.add_argument("--write", action="store_true", help="Write the merged private YAML instead of printing it.")
    parser.add_argument("--show-secrets", action="store_true", help="Print secrets instead of redacting them in preview output.")
    args = parser.parse_args(argv)

    root = find_repo_root(Path.cwd())
    state_path = args.state if args.state is not None else root / STATE_RELATIVE_PATH
    if not state_path.is_absolute():
        state_path = root / state_path

    existing = load_yaml_file(state_path)
    builder = StateBuilder(existing)
    populate_state(builder, root)

    ordered = order_mapping(builder.state, ())
    text_state = ordered if args.show_secrets or args.write else redact_state(ordered)
    yaml_text = emit_yaml(text_state, builder.provenance)

    if args.write:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(yaml_text, encoding="utf-8")
        print(f"Wrote {relative_display(state_path, root)}")
    else:
        print(yaml_text, end="")
    return 0


def populate_state(builder: StateBuilder, root: Path) -> None:
    builder.set_value(("schema_version",), 1, "tool", overwrite=False)
    populate_coolify(builder, root)
    populate_default_wallets(builder)
    populate_test_network(builder, root)
    populate_dev_network(builder, root)


def populate_coolify(builder: StateBuilder, root: Path) -> None:
    topology_path = root / TOPOLOGY_RELATIVE_PATH
    topology = read_json(topology_path)
    if isinstance(topology, dict):
        servers = topology.get("servers")
        if isinstance(servers, list):
            for index, server in enumerate(servers):
                if not isinstance(server, dict):
                    continue
                key = "primary" if index == 0 else "secondary" if index == 1 else str(server.get("name") or f"server_{index + 1}")
                source = f"repo:{TOPOLOGY_RELATIVE_PATH.as_posix()}"
                builder.set_if_known(("coolify", key, "name"), server.get("name"), source)
                builder.set_if_known(("coolify", key, "vpn_ip"), server.get("vpn_ip"), source)

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
            builder.set_value(network + ("qbft", "rpc_port"), rpc_port, source)
        services = seed.get("services")
        if isinstance(services, list):
            validators = [svc for svc in services if isinstance(svc, dict) and svc.get("role") == "validator"]
            builder.set_value(network + ("qbft", "validators"), len(validators), source)
            p2p_ports = [svc.get("p2p_host_port") for svc in validators if isinstance(svc.get("p2p_host_port"), int)]
            builder.set_value(network + ("qbft", "validator_p2p_ports"), p2p_ports, source)
        builder.set_value(network + ("qbft", "coolify_host"), "local_test", source)

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
            builder.set_if_known(network + ("rpc",), chain.get("rpc_url") or chain.get("host_rpc_url"), source)

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


def populate_wallet_secret_from_record(builder: StateBuilder, root: Path, base_path: tuple[str, ...], record: Mapping[str, Any]) -> None:
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
    for key in sorted(str(k) for k in value.keys() if str(k) not in result):
        result[key] = order_mapping(value[key], path + (key,))
    return result


def preferred_order_for_path(path: tuple[str, ...]) -> list[str]:
    if path in PREFERRED_ORDER:
        return PREFERRED_ORDER[path]
    wildcard = tuple("*" if index % 2 == 1 and path[index - 1] in {"networks", "wallets", "contracts"} else part for index, part in enumerate(path))
    if wildcard in PREFERRED_ORDER:
        return PREFERRED_ORDER[wildcard]
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
