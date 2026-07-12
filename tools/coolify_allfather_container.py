#!/usr/bin/env python3
"""Compile guarded all-father Coolify container manifests from existing topology files.

The compiler deliberately reuses the existing single-concern planners:

* ``tools/coolify_fdb_cluster.py`` for FoundationDB placement and cluster strings.
* ``tools/coolify_hub_cluster.py`` for Hub placement.
* ``tools/coolify_qbft_network.py`` for Besu/QBFT service placement.

It does not replace those tools.  It produces a lower-stage deployment unit: one
guarded "function cell" container per Coolify server.  Each cell is started with
a network identity (``testnet`` or ``mainnet``), a high-port guard endpoint, and
a desired process manifest.  The cell role is always ``function``; behavior is
driven by the manifest capabilities, not by brittle host roles.
"""

from __future__ import annotations

import argparse
import base64
import importlib.util
import hashlib
import json
import re
import shlex
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FDB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_fdb_cluster.py")
HUB_CLUSTER_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_cluster.py")
QBFT_NETWORK_TOOL_PATH = Path(__file__).resolve().with_name("coolify_qbft_network.py")
HUB_SERVICE_TOOL_PATH = Path(__file__).resolve().with_name("coolify_hub_service.py")

DEFAULT_GUARD_CONTAINER_PORT = 41414
DEFAULT_TESTNET_GUARD_HOST_BASE = 41410
DEFAULT_MAINNET_GUARD_HOST_BASE = 41420
DEFAULT_GENERIC_GUARD_HOST_BASE = 41430
RESERVED_HIGH_PORTS = {40010, 40321, 47000}
DEFAULT_TICK_SECONDS = 10.0
DEFAULT_RESTART_COOLDOWN_SECONDS = 30.0
DEFAULT_GLOBAL_RESTART_BUDGET = 1
DEFAULT_HUB_BASE_PORT = 8790
DEFAULT_BESU_RPC_BASE_PORT = 8545
DEFAULT_BESU_P2P_BASE_PORT = 30303
DEFAULT_STATE_ROOT_PREFIX = "/data/main-computer/allfather"
DEFAULT_DOCKERFILE = "docker/allfather/Dockerfile"
DEFAULT_IMAGE_PREFIX = "main-computer-allfather"
DEFAULT_INSTANCE_PORT_STRIDE = 100
DEFAULT_MAINNET_PORT_OFFSET = 1000
DEFAULT_GUARD_SET_STRIDE = 20
DEFAULT_COOLIFY_ENVIRONMENT_SUFFIX = "allfather"


class AllfatherCompileError(ValueError):
    """Raised when the all-father compiler cannot create a safe manifest."""


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AllfatherCompileError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fdb_tool() -> Any:
    return _load_module("coolify_fdb_cluster_for_allfather", FDB_CLUSTER_TOOL_PATH)


def hub_tool() -> Any:
    return _load_module("coolify_hub_cluster_for_allfather", HUB_CLUSTER_TOOL_PATH)


def qbft_tool() -> Any:
    return _load_module("coolify_qbft_network_for_allfather", QBFT_NETWORK_TOOL_PATH)


def hub_service_tool() -> Any:
    return _load_module("coolify_hub_service_for_allfather", HUB_SERVICE_TOOL_PATH)


def yaml_quote(value: Any) -> str:
    return json.dumps(str(value))


def sh_quote(value: Any) -> str:
    return shlex.quote(str(value))


def safe_id(value: str, *, field: str = "id") -> str:
    clean = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip()).strip("-").lower()
    if not clean:
        raise AllfatherCompileError(f"{field} must not be empty.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,90}", clean):
        raise AllfatherCompileError(f"{field} is not a safe identifier: {value!r}")
    return clean


def clean_network_key(value: str) -> str:
    network_key = safe_id(value, field="network")
    if network_key not in {"test", "testnet", "mainnet"}:
        raise AllfatherCompileError(
            f"Unsupported all-father network {network_key!r}; expected test, testnet, or mainnet."
        )
    return network_key


def clean_posix_absolute_path(value: str, *, field: str) -> str:
    clean = str(value or "").replace("\\", "/").strip()
    if not clean.startswith("/"):
        raise AllfatherCompileError(f"{field} must be an absolute POSIX path, got {value!r}.")
    parts = [part for part in clean.split("/") if part]
    if any(part == ".." for part in parts):
        raise AllfatherCompileError(f"{field} must not contain '..': {value!r}.")
    return "/" + "/".join(parts)


def posix_dirname(path: str) -> str:
    clean = clean_posix_absolute_path(path, field="path")
    if clean == "/":
        return "/"
    return clean.rsplit("/", 1)[0] or "/"


def host_aliases(server_name: str, network_key: str) -> set[str]:
    """Return QBFT host aliases that may refer to a Coolify placement server."""

    clean = safe_id(server_name, field="server_name")
    aliases = {clean}
    if clean.startswith("coolify-"):
        suffix = clean.removeprefix("coolify-")
        aliases.add(suffix)
        aliases.add(f"{network_key}-{suffix}")
    if "-" in clean:
        suffix = clean.rsplit("-", 1)[-1]
        aliases.add(suffix)
        aliases.add(f"{network_key}-{suffix}")
    return aliases


def guard_host_base_for_network(network_key: str) -> int:
    if network_key in {"test", "testnet"}:
        return DEFAULT_TESTNET_GUARD_HOST_BASE
    if network_key == "mainnet":
        return DEFAULT_MAINNET_GUARD_HOST_BASE
    return DEFAULT_GENERIC_GUARD_HOST_BASE


def clean_set_id(value: str | None, network_key: str) -> str:
    """Return the running all-father set id.

    ``network_key`` is the behavior profile (testnet/mainnet). ``set_id`` is the
    actual running instance.  Keeping them separate lets one physical host run
    ``testnet-1``, ``mainnet-1``, and ``mainnet-2`` side by side.
    """

    return safe_id(value or network_key, field="set_id")


def set_index_from_id(set_id: str) -> int:
    match = re.search(r"(?:^|-)(\d+)$", set_id)
    if not match:
        return 1
    return max(1, int(match.group(1)))


def guard_host_base_for_set(network_key: str, set_id: str) -> int:
    """Return the first guard host port for a running set.

    The 41400 range is divided into interleaved testnet/mainnet slots so the
    common two-host case can run one testnet and several mainnets on the same
    physical machines without guard port collisions.
    """

    base = guard_host_base_for_network(network_key)
    return base + ((set_index_from_id(set_id) - 1) * DEFAULT_GUARD_SET_STRIDE)


def host_port_offset_for_set(network_key: str, set_id: str) -> int:
    """Return the default port offset for imported service ports.

    Test/testnet keeps the committed ports for the first set. Mainnet starts at
    a +1000 offset by default so a host can run one testnet and one mainnet at
    the same time even when both source placements use FDB port 4550. Additional
    set ids such as ``mainnet-2`` add a smaller stride.
    """

    network_offset = DEFAULT_MAINNET_PORT_OFFSET if network_key == "mainnet" else 0
    return network_offset + ((set_index_from_id(set_id) - 1) * DEFAULT_INSTANCE_PORT_STRIDE)


def shifted_port(port: int | None, offset: int) -> int | None:
    if port is None:
        return None
    return int(port) + int(offset)


def scope_value_for_set(value: str, *, network_key: str, set_id: str) -> str:
    if set_id == network_key:
        return value
    scoped = str(value)
    replacements = (
        (f"/{network_key}-", f"/{set_id}-"),
        (f"/{network_key}/", f"/{set_id}/"),
        (f"-{network_key}-", f"-{set_id}-"),
        (f"{network_key}-", f"{set_id}-"),
    )
    for old, new in replacements:
        scoped = scoped.replace(old, new)
    return scoped


def scope_namespace_for_set(namespace: str, *, network_key: str, set_id: str) -> str:
    if set_id == network_key:
        return namespace
    scoped = str(namespace).replace(network_key, set_id)
    if scoped == namespace:
        scoped = f"{namespace}-{set_id}"
    return safe_id(scoped, field="fdb_namespace")


def scoped_hub_runtime_dir(cell: "AllfatherCell", hub: Any) -> str:
    return scope_value_for_set(hub.runtime_dir, network_key=cell.network_key, set_id=cell.set_id)


def scoped_hub_cluster_file_path(cell: "AllfatherCell", hub: Any) -> str:
    return scope_value_for_set(hub.cluster_file_path, network_key=cell.network_key, set_id=cell.set_id)


def scoped_hub_namespace(cell: "AllfatherCell", hub: Any) -> str:
    return scope_namespace_for_set(hub.namespace, network_key=cell.network_key, set_id=cell.set_id)


def hub_port_for_cell(cell: "AllfatherCell", index: int) -> int:
    return int(cell.hub_base_port) + int(index) + int(cell.host_port_offset)


def fdb_port_for_cell(cell: "AllfatherCell", instance: Any) -> int:
    return int(instance.port) + int(cell.host_port_offset)


def fdb_cluster_description_for_set(placement: Any, *, network_key: str, set_id: str) -> str:
    original = str(getattr(placement, "cluster_description", f"main_computer_{network_key}"))
    if set_id == network_key:
        return original
    return safe_id(f"{original}-{set_id}", field="fdb_cluster_description").replace("-", "_")


def fdb_cluster_id_for_set(placement: Any, *, network_key: str, set_id: str) -> str:
    original = str(getattr(placement, "cluster_id", "allfather"))
    if set_id == network_key:
        return original
    return hashlib.sha1(f"{original}:{set_id}".encode("utf-8")).hexdigest()[:16]


def desired_counts_for_cells(cells: Sequence["AllfatherCell"]) -> dict[str, int]:
    return {
        "allfather_cells": len(cells),
        "foundationdb": sum(len(cell.fdb_instances) for cell in cells),
        "hub": sum(len(cell.hubs) for cell in cells),
        "qbft": sum(len(cell.qbft_services) for cell in cells),
        "processes": sum(len(cell.process_manifest()) for cell in cells),
    }


def peer_descriptor_for_cell(cell: "AllfatherCell") -> dict[str, Any]:
    return {
        "set_id": cell.set_id,
        "network_key": cell.network_key,
        "cell_id": cell.cell_id,
        "coolify_server": cell.coolify_server,
        "vpn_ip": cell.vpn_ip,
        "guard_container_port": cell.guard_container_port,
        "guard_host_port": cell.guard_host_port,
        "guard_publish_host": cell.guard_publish_host,
        "guard_url": f"http://{cell.guard_publish_host}:{cell.guard_host_port}",
        "state_root": cell.state_root,
        "desired_counts": cell.local_desired_counts(),
    }


def fdb_cluster_contents_for_cells(placement: Any, cells: Sequence["AllfatherCell"], *, network_key: str, set_id: str) -> str:
    coordinators: list[str] = []
    for cell in cells:
        for instance in cell.fdb_instances:
            coordinators.append(f"{instance.vpn_ip}:{fdb_port_for_cell(cell, instance)}")
    if not coordinators:
        raise AllfatherCompileError("All-father set has no FoundationDB coordinator ports.")
    description = fdb_cluster_description_for_set(placement, network_key=network_key, set_id=set_id)
    cluster_id = fdb_cluster_id_for_set(placement, network_key=network_key, set_id=set_id)
    return f"{description}:{cluster_id}@{','.join(coordinators)}"


def hub_base_port_for_network(network_key: str) -> int:
    """Return the Hub bind port from the committed Hub network registry.

    The all-father runs multiple Hub processes in one container, so this value is
    treated as the first local Hub port for the cell and later Hubs increment by
    one.  Falling back keeps the compiler usable in stripped test snapshots.
    """

    try:
        from main_computer.hub_networks import load_hub_network_registry

        registry = load_hub_network_registry()
        profile = registry.get(network_key)
        return int(profile.hub_bind_port)
    except Exception:
        return DEFAULT_HUB_BASE_PORT


def placement_path_for_network(network_key: str) -> Path:
    if network_key == "test":
        # The hub/FDB placement files intentionally only model public testnet and
        # mainnet.  Local "test" still compiles against testnet placement so the
        # all-father cell has Hub/FDB state to run while using the local QBFT seed.
        return REPO_ROOT / "deploy" / "hub-topology" / "testnet-coolify-deployment.json"
    return REPO_ROOT / "deploy" / "hub-topology" / f"{network_key}-coolify-deployment.json"


@dataclass(frozen=True)
class AllfatherProcess:
    name: str
    group: str
    command: tuple[str, ...]
    critical: bool = True
    desired: bool = True
    restart_cooldown_s: float = DEFAULT_RESTART_COOLDOWN_SECONDS
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AllfatherCell:
    cell_id: str
    network_key: str
    set_id: str
    coolify_server: str
    vpn_ip: str
    state_root: str
    guard_container_port: int
    guard_host_port: int
    guard_publish_host: str
    host_port_offset: int
    hub_base_port: int
    besu_rpc_base_port: int
    besu_p2p_base_port: int
    fdb_instances: tuple[Any, ...]
    hubs: tuple[Any, ...]
    qbft_services: tuple[Any, ...]
    fdb_cluster_file_path: str
    fdb_cluster_contents: str
    fdb_namespace: str
    fdb_image: str
    public_entry_urls: tuple[str, ...]
    peer_hosts: tuple[Mapping[str, Any], ...] = ()
    set_desired_counts: Mapping[str, int] | None = None

    @property
    def capabilities(self) -> tuple[str, ...]:
        values = ["process-guard"]
        if self.fdb_instances:
            values.append("foundationdb")
        if self.hubs:
            values.append("hub")
        if self.qbft_services:
            values.append("qbft")
        return tuple(values)

    def process_manifest(self) -> tuple[AllfatherProcess, ...]:
        return tuple(_processes_for_cell(self))

    def local_desired_counts(self) -> dict[str, int]:
        return {
            "foundationdb": len(self.fdb_instances),
            "hub": len(self.hubs),
            "qbft": len(self.qbft_services),
            "processes": len(self.process_manifest()),
        }

    def to_manifest(self) -> dict[str, Any]:
        processes = [process.to_dict() for process in self.process_manifest()]
        ports = port_inventory_for_cell(self)
        local_counts = self.local_desired_counts()
        set_counts = dict(self.set_desired_counts or {})
        return {
            "kind": "main_computer.allfather_container.v1",
            "network_key": self.network_key,
            "set_id": self.set_id,
            "cell_id": self.cell_id,
            "coolify_server": self.coolify_server,
            "vpn_ip": self.vpn_ip,
            "state_root": self.state_root,
            "host_port_offset": self.host_port_offset,
            "desired_counts": local_counts,
            "set_desired_counts": set_counts,
            "identity": {
                "service": "main-computer-allfather",
                "role": "function",
                "network_key": self.network_key,
                "set_id": self.set_id,
                "capabilities": list(self.capabilities),
                "desired_counts": local_counts,
                "set_desired_counts": set_counts,
                "ports": ports,
            },
            "topology": {
                "discovery_mode": "guard-peer-advertise-v1",
                "local_guard": peer_descriptor_for_cell(self),
                "peer_hosts": list(self.peer_hosts),
                "all_guard_urls": [peer_descriptor_for_cell(self)["guard_url"]]
                + [str(peer.get("guard_url")) for peer in self.peer_hosts],
                "adjustment_note": (
                    "A cell is only preloaded with its own desired counts and peer guard "
                    "endpoints. Peer guards can exchange /identity or /topology payloads "
                    "and converge their top-level view without host-role assumptions."
                ),
            },
            "guard": {
                "container_port": self.guard_container_port,
                "host_port": self.guard_host_port,
                "publish_host": self.guard_publish_host,
                "tick_s": DEFAULT_TICK_SECONDS,
                "restart_budget_per_tick": DEFAULT_GLOBAL_RESTART_BUDGET,
                "restart_claim_backend": "local-manifest-v1",
                "restart_claim_namespace": self.fdb_namespace,
                "restart_claim_note": (
                    "The v1 container guard serializes local restarts. "
                    "The namespace is carried forward for the FDB-backed peer lease layer."
                ),
            },
            "foundationdb": {
                "cluster_file_path": self.fdb_cluster_file_path,
                "cluster_contents": self.fdb_cluster_contents,
                "namespace": self.fdb_namespace,
                "instances": [
                    {
                        "id": instance.id,
                        "source_port": instance.port,
                        "port": fdb_port_for_cell(self, instance),
                        "vpn_ip": instance.vpn_ip,
                        "machine_id": instance.machine_id,
                        "zone_id": instance.zone_id,
                        "state_dir": fdb_tool().fdb_instance_state_dir(_fdb_placement_for_cell(self), instance),
                    }
                    for instance in self.fdb_instances
                ],
            },
            "hubs": [
                {
                    "hub_id": hub.hub_id,
                    "public_url": hub.public_url,
                    "source_port_base": self.hub_base_port,
                    "port": hub_port_for_cell(self, index),
                    "runtime_dir": scoped_hub_runtime_dir(self, hub),
                    "cluster_file_path": scoped_hub_cluster_file_path(self, hub),
                    "namespace": scoped_hub_namespace(self, hub),
                }
                for index, hub in enumerate(self.hubs)
            ],
            "qbft": [
                {
                    "id": service.id,
                    "role": service.role,
                    "roles": list(service.roles),
                    "rpc_container_port": self.besu_rpc_base_port + index,
                    "p2p_container_port": self.besu_p2p_base_port + index,
                    "rpc_source_host_port": service.rpc_host_port,
                    "p2p_source_host_port": service.p2p_host_port,
                    "rpc_host_port": shifted_port(service.rpc_host_port, self.host_port_offset),
                    "p2p_host_port": shifted_port(service.p2p_host_port, self.host_port_offset),
                    "data_path": f"{self.state_root}/qbft/{service.id}/data",
                    "static_nodes_path": f"{self.state_root}/qbft/{service.id}/static-nodes.json",
                }
                for index, service in enumerate(self.qbft_services)
            ],
            "public_entry_urls": list(self.public_entry_urls),
            "port_inventory": ports,
            "processes": processes,
        }


@dataclass(frozen=True)
class AllfatherPlan:
    network_key: str
    set_id: str
    placement_path: str
    qbft_seed: str
    cells: tuple[AllfatherCell, ...]

    def desired_counts(self) -> dict[str, int]:
        return desired_counts_for_cells(self.cells)

    def to_dict(self) -> dict[str, Any]:
        set_counts = self.desired_counts()
        return {
            "kind": "main_computer.allfather_plan.v1",
            "network_key": self.network_key,
            "set_id": self.set_id,
            "placement_path": self.placement_path,
            "qbft_seed": self.qbft_seed,
            "desired_counts": set_counts,
            "topology": {
                "discovery_mode": "guard-peer-advertise-v1",
                "guard_urls": [peer_descriptor_for_cell(cell)["guard_url"] for cell in self.cells],
                "physical_hosts": sorted({cell.coolify_server for cell in self.cells}),
                "note": (
                    "The guard receives counts plus peer guard endpoints. The physical "
                    "host role does not decide behavior; each cell converges its declared "
                    "functions and exchanges topology with peers."
                ),
            },
            "cells": [
                {
                    "cell_id": cell.cell_id,
                    "network_key": cell.network_key,
                    "set_id": cell.set_id,
                    "coolify_server": cell.coolify_server,
                    "vpn_ip": cell.vpn_ip,
                    "host_port_offset": cell.host_port_offset,
                    "guard": {
                        "host_port": cell.guard_host_port,
                        "container_port": cell.guard_container_port,
                        "publish_host": cell.guard_publish_host,
                    },
                    "identity": {
                        "role": "function",
                        "capabilities": list(cell.capabilities),
                    },
                    "desired_counts": cell.local_desired_counts(),
                    "peer_hosts": list(cell.peer_hosts),
                    "fdb_instances": [instance.id for instance in cell.fdb_instances],
                    "hubs": [hub.hub_id for hub in cell.hubs],
                    "qbft": [service.id for service in cell.qbft_services],
                    "state_root": cell.state_root,
                    "ports": port_inventory_for_cell(cell),
                    "process_count": len(cell.process_manifest()),
                }
                for cell in self.cells
            ],
        }



def _fdb_placement_for_cell(cell: AllfatherCell) -> Any:
    # Small adapter for fdb_instance_state_dir(), which needs placement fields.
    return type(
        "_FdbPlacementForCell",
        (),
        {
            "cluster_file_path": cell.fdb_cluster_file_path,
        },
    )()


def _command_shell(script: str) -> tuple[str, ...]:
    return ("/bin/sh", "-ec", script)


def _processes_for_cell(cell: AllfatherCell) -> list[AllfatherProcess]:
    fdb = fdb_tool()
    processes: list[AllfatherProcess] = []

    fdb_adapter = _fdb_placement_for_cell(cell)
    # The adapter above is sufficient for state dir lookups; the scripts need
    # the full placement metadata, so they are rendered by this compiler instead
    # of reaching through a mutable global.
    for instance in cell.fdb_instances:
        instance_port = fdb_port_for_cell(cell, instance)
        instance_dir = f"{posix_dirname(cell.fdb_cluster_file_path)}/foundationdb/{instance.id}"
        data_dir = f"{instance_dir}/data"
        log_dir = f"{instance_dir}/logs"
        script = "\n".join(
            [
                f"echo 'Starting all-father FDB instance {instance.id} on {instance.vpn_ip}:{instance_port}'",
                f"mkdir -p {sh_quote(posix_dirname(cell.fdb_cluster_file_path))} {sh_quote(data_dir)} {sh_quote(log_dir)}",
                f"printf '%s\\n' {sh_quote(cell.fdb_cluster_contents)} > {sh_quote(cell.fdb_cluster_file_path)}",
                "exec /usr/bin/fdbserver \\",
                f"  --cluster-file {sh_quote(cell.fdb_cluster_file_path)} \\",
                f"  --public-address {sh_quote(f'{instance.vpn_ip}:{instance_port}')} \\",
                f"  --listen-address {sh_quote(f'0.0.0.0:{instance_port}')} \\",
                f"  --datadir {sh_quote(data_dir)} \\",
                f"  --logdir {sh_quote(log_dir)} \\",
                f"  --locality-machineid {sh_quote(instance.machine_id)} \\",
                f"  --locality-zoneid {sh_quote(instance.zone_id)} \\",
                "  --class storage \\",
                "  --knob_disable_posix_kernel_aio 1",
            ]
        )
        processes.append(
            AllfatherProcess(
                name=f"fdb-{instance.id}",
                group="foundationdb",
                command=_command_shell(script),
                critical=True,
                restart_cooldown_s=DEFAULT_RESTART_COOLDOWN_SECONDS,
            )
        )

    if cell.fdb_instances:
        configure = "double ssd"
        script = "\n".join(
            [
                f"mkdir -p {sh_quote(posix_dirname(cell.fdb_cluster_file_path))}",
                f"printf '%s\\n' {sh_quote(cell.fdb_cluster_contents)} > {sh_quote(cell.fdb_cluster_file_path)}",
                f"echo 'Using all-father FDB cluster file: {cell.fdb_cluster_file_path}'",
                "while true; do",
                f"  fdbcli -C {sh_quote(cell.fdb_cluster_file_path)} --exec {sh_quote('configure new ' + configure)} --timeout 10 >/tmp/main-computer-allfather-fdb-configure.log 2>&1 || true",
                f"  fdbcli -C {sh_quote(cell.fdb_cluster_file_path)} --exec status --timeout 10 >/tmp/main-computer-allfather-fdb-status.log 2>&1 || true",
                "  sleep 30",
                "done",
            ]
        )
        processes.append(
            AllfatherProcess(
                name="fdb-configure",
                group="foundationdb",
                command=_command_shell(script),
                critical=False,
                restart_cooldown_s=60.0,
            )
        )

    for index, hub in enumerate(cell.hubs):
        port = str(hub_port_for_cell(cell, index))
        command = (
            "python",
            "/app/run-exp-fdb-hub.py",
            "--network",
            cell.network_key,
            "--host",
            "0.0.0.0",
            "--port",
            port,
            "--hub-url",
            hub.public_url,
            "--root",
            scoped_hub_runtime_dir(cell, hub),
            "--cluster-file",
            scoped_hub_cluster_file_path(cell, hub),
            "--ns",
            scoped_hub_namespace(cell, hub),
        )
        processes.append(
            AllfatherProcess(
                name=f"hub-{hub.hub_id}",
                group="hub",
                command=command,
                critical=True,
                restart_cooldown_s=DEFAULT_RESTART_COOLDOWN_SECONDS,
            )
        )

    for index, service in enumerate(cell.qbft_services):
        rpc_port = cell.besu_rpc_base_port + index
        p2p_port = cell.besu_p2p_base_port + index
        data_path = f"{cell.state_root}/qbft/{service.id}/data"
        static_nodes_path = f"{cell.state_root}/qbft/{service.id}/static-nodes.json"
        genesis_path = f"{cell.state_root}/qbft/genesis.json"
        role_apis = "ETH,NET,QBFT,WEB3" if service.role == "validator" else "ETH,NET,WEB3"
        script = "\n".join(
            [
                f"mkdir -p {sh_quote(data_path)} {sh_quote(str(Path(static_nodes_path).parent))}",
                f"if [ ! -s {sh_quote(genesis_path)} ]; then",
                f"  echo 'Missing QBFT genesis at {genesis_path}; seed it with the existing QBFT tooling before enabling this process.' >&2",
                "  sleep 30",
                "  exit 1",
                "fi",
                "exec /opt/besu/bin/besu \\",
                f"  --data-path={sh_quote(data_path)} \\",
                f"  --genesis-file={sh_quote(genesis_path)} \\",
                "  --host-allowlist='*' \\",
                "  --rpc-http-enabled=true \\",
                "  --rpc-http-host=0.0.0.0 \\",
                f"  --rpc-http-port={rpc_port} \\",
                f"  --rpc-http-api={sh_quote(role_apis)} \\",
                "  --p2p-host=0.0.0.0 \\",
                f"  --p2p-port={p2p_port} \\",
                f"  --static-nodes-file={sh_quote(static_nodes_path)}",
            ]
        )
        processes.append(
            AllfatherProcess(
                name=f"qbft-{service.id}",
                group="qbft",
                command=_command_shell(script),
                critical=True,
                restart_cooldown_s=DEFAULT_RESTART_COOLDOWN_SECONDS,
                notes="Requires pre-seeded all-father QBFT genesis/static-nodes material.",
            )
        )

    return processes


def build_allfather_plan(
    network_key: str,
    *,
    set_id: str | None = None,
    placement_path: str | Path | None = None,
    qbft_seed: str | None = None,
    allow_mainnet: bool = False,
    guard_container_port: int = DEFAULT_GUARD_CONTAINER_PORT,
    guard_host_base: int | None = None,
    host_port_offset: int | None = None,
    state_root_prefix: str = DEFAULT_STATE_ROOT_PREFIX,
) -> AllfatherPlan:
    network_key = clean_network_key(network_key)
    set_id = clean_set_id(set_id, network_key)
    if network_key == "mainnet" and not allow_mainnet:
        raise AllfatherCompileError("mainnet compilation requires --allow-mainnet.")

    resolved_placement_path = Path(placement_path) if placement_path else placement_path_for_network(network_key)
    if not resolved_placement_path.is_absolute():
        resolved_placement_path = REPO_ROOT / resolved_placement_path

    fdb_placement = fdb_tool().load_fdb_placement(resolved_placement_path)
    hub_placement = hub_tool().load_hub_cluster_placement(resolved_placement_path)
    if fdb_placement.network_key != hub_placement.network_key:
        raise AllfatherCompileError("FDB and Hub placement network keys do not match.")

    placement_network = fdb_placement.network_key
    if network_key != "test" and placement_network != network_key:
        raise AllfatherCompileError(
            f"Requested network {network_key!r} does not match placement network {placement_network!r}."
        )

    qbft_seed_name = qbft_seed or network_key
    qbft_plan = qbft_tool().build_plan(qbft_seed_name, allow_mainnet=allow_mainnet)

    base = guard_host_base if guard_host_base is not None else guard_host_base_for_set(network_key, set_id)
    if base in RESERVED_HIGH_PORTS:
        raise AllfatherCompileError(f"Guard host port base {base} collides with reserved port usage.")
    if guard_container_port in RESERVED_HIGH_PORTS:
        raise AllfatherCompileError(f"Guard container port {guard_container_port} collides with reserved port usage.")

    compiled_host_port_offset = (
        int(host_port_offset) if host_port_offset is not None else host_port_offset_for_set(network_key, set_id)
    )

    state_root_prefix = clean_posix_absolute_path(state_root_prefix, field="state_root_prefix")
    servers = list(fdb_placement.servers.values())
    if not servers:
        raise AllfatherCompileError("Placement has no Coolify servers.")

    scoped_cluster_file_path = scope_value_for_set(
        fdb_placement.cluster_file_path, network_key=network_key, set_id=set_id
    )
    scoped_namespace = scope_namespace_for_set(fdb_placement.namespace, network_key=network_key, set_id=set_id)

    cells: list[AllfatherCell] = []
    for index, server in enumerate(servers):
        guard_host_port = base + index
        if guard_host_port in RESERVED_HIGH_PORTS:
            raise AllfatherCompileError(f"Guard host port {guard_host_port} collides with reserved port usage.")
        aliases = host_aliases(server.name, network_key)
        fdb_instances = tuple(instance for instance in fdb_placement.instances if instance.coolify_server == server.name)
        hubs = tuple(hub for hub in hub_placement.hubs if hub.coolify_server == server.name)
        qbft_services = tuple(
            sorted(
                (
                    service
                    for service in qbft_plan.services
                    if host_aliases(service.host, network_key) & aliases
                ),
                key=lambda service: (0 if service.role == "validator" else 1, service.id),
            )
        )
        cell_id = safe_id(f"{set_id}-{server.name}", field="cell_id")
        cells.append(
            AllfatherCell(
                cell_id=cell_id,
                network_key=network_key,
                set_id=set_id,
                coolify_server=server.name,
                vpn_ip=server.vpn_ip,
                state_root=f"{state_root_prefix}/{set_id}/{server.name}",
                guard_container_port=guard_container_port,
                guard_host_port=guard_host_port,
                guard_publish_host=server.vpn_ip,
                host_port_offset=compiled_host_port_offset,
                hub_base_port=hub_base_port_for_network(network_key),
                besu_rpc_base_port=DEFAULT_BESU_RPC_BASE_PORT,
                besu_p2p_base_port=DEFAULT_BESU_P2P_BASE_PORT,
                fdb_instances=fdb_instances,
                hubs=hubs,
                qbft_services=qbft_services,
                fdb_cluster_file_path=scoped_cluster_file_path,
                fdb_cluster_contents="",
                fdb_namespace=scoped_namespace,
                fdb_image=fdb_placement.image,
                public_entry_urls=hub_placement.public_entry_urls,
            )
        )

    cluster_contents = fdb_cluster_contents_for_cells(fdb_placement, cells, network_key=network_key, set_id=set_id)
    set_counts = desired_counts_for_cells(cells)
    completed_cells: list[AllfatherCell] = []
    for cell in cells:
        peers = tuple(peer_descriptor_for_cell(peer) for peer in cells if peer.cell_id != cell.cell_id)
        completed_cells.append(
            replace(
                cell,
                fdb_cluster_contents=cluster_contents,
                peer_hosts=peers,
                set_desired_counts=set_counts,
            )
        )

    return AllfatherPlan(
        network_key=network_key,
        set_id=set_id,
        placement_path=str(resolved_placement_path.relative_to(REPO_ROOT) if resolved_placement_path.is_relative_to(REPO_ROOT) else resolved_placement_path),
        qbft_seed=qbft_seed_name,
        cells=tuple(completed_cells),
    )



def manifest_b64(manifest: Mapping[str, Any]) -> str:
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def port_inventory_for_cell(cell: AllfatherCell) -> list[dict[str, Any]]:
    """Compile every local service port into one operator-facing inventory."""

    ports: list[dict[str, Any]] = [
        {
            "name": "allfather-guard",
            "group": "process-guard",
            "kind": "guard-http",
            "protocol": "tcp",
            "bind_host": "0.0.0.0",
            "publish_host": cell.guard_publish_host,
            "host_port": cell.guard_host_port,
            "container_port": cell.guard_container_port,
            "published": True,
            "visibility": "private-or-operator",
            "notes": "Guard API: /healthz, /identity, /topology, /status, /processes, /up, /down, /drain, /wake.",
        }
    ]

    for instance in cell.fdb_instances:
        compiled_port = fdb_port_for_cell(cell, instance)
        ports.append(
            {
                "name": instance.id,
                "group": "foundationdb",
                "kind": "fdbserver",
                "protocol": "tcp",
                "bind_host": "0.0.0.0",
                "publish_host": instance.vpn_ip,
                "source_host_port": int(instance.port),
                "host_port": compiled_port,
                "container_port": compiled_port,
                "published": True,
                "visibility": "private-vpn",
                "notes": "FoundationDB server port imported from placement and shifted by this set's host_port_offset.",
            }
        )

    for index, hub in enumerate(cell.hubs):
        port = hub_port_for_cell(cell, index)
        ports.append(
            {
                "name": hub.hub_id,
                "group": "hub",
                "kind": "http",
                "protocol": "tcp",
                "bind_host": "0.0.0.0",
                "publish_host": cell.vpn_ip,
                "source_host_port": cell.hub_base_port + index,
                "host_port": port,
                "container_port": port,
                "published": True,
                "visibility": "private-vpn",
                "public_url": hub.public_url,
                "notes": "Hub HTTP port derived from the Hub network bind port and shifted by this set's host_port_offset.",
            }
        )

    for index, service in enumerate(cell.qbft_services):
        rpc_container_port = cell.besu_rpc_base_port + index
        p2p_container_port = cell.besu_p2p_base_port + index
        if service.rpc_host_port:
            ports.append(
                {
                    "name": f"{service.id}-rpc",
                    "group": "qbft",
                    "kind": "besu-json-rpc",
                    "protocol": "tcp",
                    "bind_host": "0.0.0.0",
                    "publish_host": service.rpc_bind_host or "127.0.0.1",
                    "source_host_port": int(service.rpc_host_port),
                    "host_port": shifted_port(service.rpc_host_port, cell.host_port_offset),
                    "container_port": rpc_container_port,
                    "published": True,
                    "visibility": "operator-or-public-rpc",
                    "notes": "Besu JSON-RPC host port imported from the QBFT network plan and shifted by this set's host_port_offset.",
                }
            )
        else:
            ports.append(
                {
                    "name": f"{service.id}-rpc",
                    "group": "qbft",
                    "kind": "besu-json-rpc",
                    "protocol": "tcp",
                    "bind_host": "0.0.0.0",
                    "publish_host": "",
                    "source_host_port": None,
                    "host_port": None,
                    "container_port": rpc_container_port,
                    "published": False,
                    "visibility": "container-local",
                    "notes": "Besu JSON-RPC is internal for this service because the QBFT plan has no host RPC port.",
                }
            )
        if service.p2p_host_port:
            ports.append(
                {
                    "name": f"{service.id}-p2p",
                    "group": "qbft",
                    "kind": "besu-p2p",
                    "protocol": "tcp",
                    "bind_host": "0.0.0.0",
                    "publish_host": service.p2p_bind_host or cell.vpn_ip,
                    "source_host_port": int(service.p2p_host_port),
                    "host_port": shifted_port(service.p2p_host_port, cell.host_port_offset),
                    "container_port": p2p_container_port,
                    "published": True,
                    "visibility": "private-vpn-or-peer",
                    "notes": "Besu P2P host port imported from the QBFT network plan and shifted by this set's host_port_offset.",
                }
            )
        else:
            ports.append(
                {
                    "name": f"{service.id}-p2p",
                    "group": "qbft",
                    "kind": "besu-p2p",
                    "protocol": "tcp",
                    "bind_host": "0.0.0.0",
                    "publish_host": "",
                    "source_host_port": None,
                    "host_port": None,
                    "container_port": p2p_container_port,
                    "published": False,
                    "visibility": "container-local",
                    "notes": "Besu P2P is internal for this service because the QBFT plan has no host P2P port.",
                }
            )

    return ports



def port_inventory_b64(cell: AllfatherCell) -> str:
    payload = json.dumps(port_inventory_for_cell(cell), indent=2, sort_keys=True).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def port_group_summary(cell: AllfatherCell, group: str) -> str:
    parts: list[str] = []
    for port in port_inventory_for_cell(cell):
        if port.get("group") != group:
            continue
        name = str(port.get("name") or "")
        container_port = port.get("container_port")
        host_port = port.get("host_port")
        publish_host = str(port.get("publish_host") or "")
        if port.get("published") and host_port:
            parts.append(f"{name}={publish_host}:{host_port}->{container_port}/tcp")
        else:
            parts.append(f"{name}=container:{container_port}/tcp")
    return ",".join(parts)


def port_inventory_summary(cell: AllfatherCell) -> str:
    return ";".join(
        value
        for value in (
            port_group_summary(cell, "process-guard"),
            port_group_summary(cell, "foundationdb"),
            port_group_summary(cell, "hub"),
            port_group_summary(cell, "qbft"),
        )
        if value
    )


def rendered_container_ports(cell: AllfatherCell) -> list[tuple[str, int, int]]:
    ports: list[tuple[str, int, int]] = []
    for port in port_inventory_for_cell(cell):
        if not port.get("published"):
            continue
        host_port = port.get("host_port")
        container_port = port.get("container_port")
        publish_host = str(port.get("publish_host") or "").strip()
        if not publish_host or host_port is None or container_port is None:
            continue
        ports.append((publish_host, int(host_port), int(container_port)))
    return ports

def render_compose_for_cell(cell: AllfatherCell, *, dockerfile: str = DEFAULT_DOCKERFILE, image_prefix: str = DEFAULT_IMAGE_PREFIX) -> str:
    manifest = cell.to_manifest()
    service_name = safe_id(f"{image_prefix}-{cell.set_id}-{cell.coolify_server}", field="service_name")
    image_name = safe_id(f"{image_prefix}-{cell.network_key}", field="image_name")
    cluster_dir = posix_dirname(cell.fdb_cluster_file_path)
    state_root_parent = posix_dirname(cell.state_root)
    lines = [
        f"name: {service_name}",
        "",
        "services:",
        f"  {service_name}:",
        "    build:",
        "      context: .",
        f"      dockerfile: {yaml_quote(dockerfile)}",
        f"    image: {yaml_quote(f'{image_name}:latest')}",
        "    restart: unless-stopped",
        "    environment:",
        f"      MC_ALLFATHER_NETWORK: {yaml_quote(cell.network_key)}",
        f"      MC_ALLFATHER_SET_ID: {yaml_quote(cell.set_id)}",
        f"      MC_ALLFATHER_CELL_ID: {yaml_quote(cell.cell_id)}",
        f"      MC_ALLFATHER_GUARD_PORT: {yaml_quote(cell.guard_container_port)}",
        f"      MC_ALLFATHER_GUARD_HOST_PORT: {yaml_quote(cell.guard_host_port)}",
        f"      MC_ALLFATHER_HOST_PORT_OFFSET: {yaml_quote(cell.host_port_offset)}",
        f"      MC_ALLFATHER_STATE_ROOT: {yaml_quote(cell.state_root)}",
        f"      MC_ALLFATHER_FDB_NAMESPACE: {yaml_quote(cell.fdb_namespace)}",
        f"      MC_ALLFATHER_CHAIN_PROFILE: {yaml_quote(cell.network_key)}",
        f"      MC_ALLFATHER_DESIRED_COUNTS: {yaml_quote(json.dumps(cell.local_desired_counts(), sort_keys=True))}",
        f"      MC_ALLFATHER_SET_DESIRED_COUNTS: {yaml_quote(json.dumps(dict(cell.set_desired_counts or {}), sort_keys=True))}",
        f"      MC_ALLFATHER_PEER_GUARDS: {yaml_quote(','.join(str(peer.get('guard_url')) for peer in cell.peer_hosts))}",
        f"      MC_ALLFATHER_GUARD_PORTS: {yaml_quote(port_group_summary(cell, 'process-guard'))}",
        f"      MC_ALLFATHER_FDB_PORTS: {yaml_quote(port_group_summary(cell, 'foundationdb'))}",
        f"      MC_ALLFATHER_HUB_PORTS: {yaml_quote(port_group_summary(cell, 'hub'))}",
        f"      MC_ALLFATHER_QBFT_PORTS: {yaml_quote(port_group_summary(cell, 'qbft'))}",
        f"      MC_ALLFATHER_PORT_SUMMARY: {yaml_quote(port_inventory_summary(cell))}",
        f"      MC_ALLFATHER_PORT_INVENTORY_B64: {yaml_quote(port_inventory_b64(cell))}",
        f"      MC_ALLFATHER_MANIFEST_B64: {yaml_quote(manifest_b64(manifest))}",
        "    ports:",
    ]
    for publish_host, host_port, container_port in rendered_container_ports(cell):
        lines.append(f"      - {yaml_quote(f'{publish_host}:{host_port}:{container_port}/tcp')}")
    lines.extend(
        [
            "    volumes:",
            f"      - {yaml_quote(f'{state_root_parent}:{state_root_parent}')}",
            f"      - {yaml_quote(f'{cluster_dir}:{cluster_dir}')}",
            "    healthcheck:",
            "      test:",
            "        - CMD-SHELL",
            f"        - {yaml_quote(f'python /opt/main-computer/allfather/healthcheck.py http://127.0.0.1:{cell.guard_container_port}/healthz')}",
            "      interval: 10s",
            "      timeout: 5s",
            "      start_period: 10s",
            "      retries: 6",
            "",
        ]
    )
    return "\n".join(lines)




def allfather_service_name(cell: AllfatherCell, *, image_prefix: str = DEFAULT_IMAGE_PREFIX) -> str:
    """Return the Coolify service name for one all-father cell."""

    return safe_id(f"{image_prefix}-{cell.set_id}-{cell.coolify_server}", field="service_name")


def allfather_environment_name(plan: AllfatherPlan, args: argparse.Namespace) -> str:
    clean = str(getattr(args, "coolify_environment_name", "") or "").strip()
    if clean:
        return clean
    return f"{plan.set_id}-{DEFAULT_COOLIFY_ENVIRONMENT_SUFFIX}"


def _servers_for_plan(plan: AllfatherPlan) -> dict[str, AllfatherCell]:
    return {cell.coolify_server: cell for cell in plan.cells}


def _destination_uuid_for_cell(cell: AllfatherCell, args: argparse.Namespace) -> str:
    per_server = fdb_tool().parse_binding_map(getattr(args, "set_coolify_destination_uuid", []) or [], "--set-coolify-destination-uuid")
    return str(per_server.get(cell.coolify_server) or getattr(args, "coolify_destination_uuid", "") or "").strip()


def _explicit_service_uuid_for_cell(cell: AllfatherCell, args: argparse.Namespace) -> str:
    per_server = fdb_tool().parse_binding_map(getattr(args, "set_coolify_service_uuid", []) or [], "--set-coolify-service-uuid")
    return str(per_server.get(cell.coolify_server) or getattr(args, "coolify_service_uuid", "") or "").strip()


def coolify_context_args_for_cell(plan: AllfatherPlan, cell: AllfatherCell, args: argparse.Namespace) -> argparse.Namespace:
    context_args = fdb_tool().context_args_for_server(args, cell.coolify_server)
    if not str(context_args.coolify_environment_name or "").strip():
        context_args.coolify_environment_name = allfather_environment_name(plan, args)
    return context_args


def resolve_allfather_context_for_cell(
    client: Any,
    plan: AllfatherPlan,
    cell: AllfatherCell,
    args: argparse.Namespace,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    profile = fdb_tool()._ProfileForContext(plan.set_id)
    context_args = coolify_context_args_for_cell(plan, cell, args)
    return hub_service_tool().resolve_coolify_context(client, profile, context_args, tried)


def coolify_service_payload(
    plan: AllfatherPlan,
    cell: AllfatherCell,
    args: argparse.Namespace,
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    compose = render_compose_for_cell(
        cell,
        dockerfile=getattr(args, "dockerfile", DEFAULT_DOCKERFILE),
        image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX),
    )
    payload: dict[str, Any] = {
        "server_uuid": (context or {}).get("server_uuid") or "",
        "project_uuid": (context or {}).get("project_uuid") or "",
        "environment_name": (context or {}).get("environment_name") or allfather_environment_name(plan, args),
        "environment_uuid": (context or {}).get("environment_uuid") or "",
        "name": allfather_service_name(cell, image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX)),
        "description": (
            f"Main Computer guarded all-father {plan.set_id} cell on {cell.coolify_server}; "
            f"network profile {plan.network_key}; role=function"
        ),
        "docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"),
        "instant_deploy": False,
    }
    destination_uuid = _destination_uuid_for_cell(cell, args)
    if destination_uuid:
        payload["destination_uuid"] = destination_uuid
    return {key: value for key, value in payload.items() if value not in (None, "")}


def redact_coolify_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(payload)
    if "docker_compose_raw" in redacted:
        raw = str(payload["docker_compose_raw"])
        redacted["docker_compose_raw"] = "<base64>"
        redacted["docker_compose_raw_bytes"] = len(raw)
    return redacted


def coolify_cell_plan(plan: AllfatherPlan, cell: AllfatherCell, args: argparse.Namespace) -> dict[str, Any]:
    url, url_source = fdb_tool().coolify_url_for_server(cell.coolify_server, args)
    return {
        "cell_id": cell.cell_id,
        "server": cell.coolify_server,
        "vpn_ip": cell.vpn_ip,
        "service_name": allfather_service_name(cell, image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX)),
        "coolify_url": url,
        "coolify_url_source": url_source,
        "environment_name": allfather_environment_name(plan, args),
        "guard_url": peer_descriptor_for_cell(cell)["guard_url"],
        "desired_counts": cell.local_desired_counts(),
        "set_desired_counts": dict(cell.set_desired_counts or {}),
        "port_inventory": port_inventory_for_cell(cell),
        "service_payload": redact_coolify_payload(coolify_service_payload(plan, cell, args)),
        "docker_compose": render_compose_for_cell(
            cell,
            dockerfile=getattr(args, "dockerfile", DEFAULT_DOCKERFILE),
            image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX),
        ),
    }


def coolify_plan_result(plan: AllfatherPlan, args: argparse.Namespace) -> dict[str, Any]:
    """Return a remote Coolify service plan without making network calls."""

    fdb_tool().validate_coolify_url_bindings(_servers_for_plan(plan), args)
    return {
        "ok": True,
        "mode": "coolify",
        "network_key": plan.network_key,
        "set_id": plan.set_id,
        "environment_name": allfather_environment_name(plan, args),
        "placement_path": plan.placement_path,
        "qbft_seed": plan.qbft_seed,
        "operator_note": (
            "This is the unified remote Coolify plan for the guarded all-father cells. "
            "Use the same network/set-id flags with the apply action to create/update services."
        ),
        "cells": [coolify_cell_plan(plan, cell, args) for cell in plan.cells],
    }


def create_coolify_service(client: Any, payload: Mapping[str, Any], tried: list[dict[str, Any]]) -> str:
    response = client.request("POST", "/api/v1/services", dict(payload))
    tried.append(
        {
            "operation": "create-allfather-service",
            "path": "/api/v1/services",
            "payload_keys": sorted(payload),
            "docker_compose_raw_encoding": "base64",
            "response": hub_service_tool().response_to_dict(response),
        }
    )
    if not response.ok:
        raise hub_service_tool().CoolifyHubDeployError(
            f"Coolify all-father service create failed with HTTP {response.status}: {response.body}"
        )
    uuid = hub_service_tool().service_uuid_from_body(response.body)
    if not uuid:
        raise hub_service_tool().CoolifyHubDeployError(
            f"Coolify all-father service create succeeded but no UUID was returned: {response.body}"
        )
    return uuid


def update_coolify_service(
    client: Any,
    *,
    service_uuid: str,
    service_name: str,
    compose: str,
    tried: list[dict[str, Any]],
) -> None:
    update_payloads = [
        {"docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii"), "name": service_name},
        {"docker_compose_raw": base64.b64encode(compose.encode("utf-8")).decode("ascii")},
        {"docker_compose": compose, "name": service_name},
        {"compose": compose, "name": service_name},
    ]
    paths = [
        f"/api/v1/services/{hub_service_tool().urllib.parse.quote(service_uuid)}",
        f"/api/v1/services/{hub_service_tool().urllib.parse.quote(service_uuid)}/compose",
    ]
    for path in paths:
        for payload in update_payloads:
            response = client.request("PATCH", path, payload)
            tried.append(
                {
                    "operation": "update-allfather-service",
                    "method": "PATCH",
                    "path": path,
                    "payload_keys": sorted(payload),
                    "response": hub_service_tool().response_to_dict(response),
                }
            )
            if response.ok:
                return
            if response.status == 405:
                response = client.request("PUT", path, payload)
                tried.append(
                    {
                        "operation": "update-allfather-service",
                        "method": "PUT",
                        "path": path,
                        "payload_keys": sorted(payload),
                        "response": hub_service_tool().response_to_dict(response),
                    }
                )
                if response.ok:
                    return
            if response.status not in {400, 404, 405, 422}:
                raise hub_service_tool().CoolifyHubDeployError(
                    f"Coolify all-father service update failed with HTTP {response.status}: {response.body}"
                )
    raise hub_service_tool().CoolifyHubDeployError("Coolify all-father service update failed on all known endpoints.")


def sync_coolify_service_for_cell(
    client: Any,
    plan: AllfatherPlan,
    cell: AllfatherCell,
    args: argparse.Namespace,
    *,
    context: Mapping[str, Any],
    tried: list[dict[str, Any]],
) -> tuple[str, str, dict[str, Any]]:
    service_name = allfather_service_name(cell, image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX))
    explicit_uuid = _explicit_service_uuid_for_cell(cell, args)
    service_uuid, existing = hub_service_tool().find_service(
        client,
        service_name=service_name,
        explicit_uuid=explicit_uuid,
        tried=tried,
    )
    compose = render_compose_for_cell(
        cell,
        dockerfile=getattr(args, "dockerfile", DEFAULT_DOCKERFILE),
        image_prefix=getattr(args, "image_prefix", DEFAULT_IMAGE_PREFIX),
    )
    if service_uuid:
        update_coolify_service(client, service_uuid=service_uuid, service_name=service_name, compose=compose, tried=tried)
        return service_uuid, "updated", existing
    payload = coolify_service_payload(plan, cell, args, context=context)
    service_uuid = create_coolify_service(client, payload, tried)
    return service_uuid, "created", existing


def coolify_apply_result(plan: AllfatherPlan, args: argparse.Namespace) -> dict[str, Any]:
    remote_plan = coolify_plan_result(plan, args)
    if getattr(args, "dry_run", False):
        return {"ok": True, "dry_run": True, "plan": remote_plan}

    phases: list[dict[str, Any]] = []
    for cell in plan.cells:
        tried: list[dict[str, Any]] = []
        client, token_source = fdb_tool().client_for_server(cell.coolify_server, args)
        version = client.request("GET", "/api/v1/version")
        tried.append({"operation": "coolify-version", "response": hub_service_tool().response_to_dict(version)})
        if not version.ok:
            raise hub_service_tool().CoolifyHubDeployError(
                f"Coolify API version check failed for {cell.coolify_server!r} with HTTP {version.status}: {version.body}"
            )
        context = resolve_allfather_context_for_cell(client, plan, cell, args, tried)
        service_uuid, action, existing = sync_coolify_service_for_cell(
            client,
            plan,
            cell,
            args,
            context=context,
            tried=tried,
        )
        deploy_result = None
        if not getattr(args, "no_deploy", False):
            deploy_result = hub_service_tool().trigger_deploy_service(
                client,
                service_uuid=service_uuid,
                force=getattr(args, "force_deploy", False),
                tried=tried,
            )
        phases.append(
            {
                "cell_id": cell.cell_id,
                "server": cell.coolify_server,
                "coolify_url": fdb_tool().coolify_url_for_server(cell.coolify_server, args)[0],
                "token_source": token_source,
                "context": context,
                "service_uuid": service_uuid,
                "service_action": action,
                "existing": existing,
                "deployed": deploy_result is not None,
                "deploy_result": deploy_result,
                "tried": tried,
            }
        )
    return {"ok": True, "plan": remote_plan, "phases": phases}



def write_plan(plan: AllfatherPlan, out_dir: Path, *, dockerfile: str = DEFAULT_DOCKERFILE) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    plan_path = out_dir / "allfather-plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written.append(plan_path)
    for cell in plan.cells:
        manifest_path = out_dir / f"{cell.cell_id}.manifest.json"
        manifest_path.write_text(json.dumps(cell.to_manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(manifest_path)

        compose_path = out_dir / f"{cell.cell_id}.compose.yml"
        compose_path.write_text(render_compose_for_cell(cell, dockerfile=dockerfile) + "\n", encoding="utf-8")
        written.append(compose_path)

    readme_path = out_dir / "README.md"
    readme_path.write_text(render_output_readme(plan), encoding="utf-8")
    written.append(readme_path)
    return written


def render_output_readme(plan: AllfatherPlan) -> str:
    lines = [
        f"# Main Computer all-father container output for `{plan.set_id}`",
        "",
        "Generated by `tools/coolify_allfather_container.py`.",
        "",
        "Each compose file contains one guarded function-cell service.  The service",
        "is intentionally identified by network, set, and cell, not by a specialized",
        "host role.  The role of every cell is `function`; capabilities come from",
        "desired counts plus peer guard endpoints in the compiled manifest.",
        "",
        f"Network behavior profile: `{plan.network_key}`",
        f"Running set id: `{plan.set_id}`",
        f"Set desired counts: `{json.dumps(plan.desired_counts(), sort_keys=True)}`",
        "",
        "## Compiled port inventory",
        "",
        "Every generated manifest and compose file carries the same inventory in",
        "`port_inventory`, `identity.ports`, and `MC_ALLFATHER_*_PORTS` environment",
        "variables.  Guard ports stay in the 41400 range.  FDB, Hub, and QBFT ports",
        "are listed because those service structures are imported into the all-father",
        "cell. Imported ports may be shifted by `host_port_offset` so one physical",
        "host can run multiple sets such as one testnet and two mainnets.",
        "",
    ]
    for cell in plan.cells:
        lines.append(f"### `{cell.cell_id}`")
        lines.append("")
        for port in port_inventory_for_cell(cell):
            name = str(port.get("name") or "")
            group = str(port.get("group") or "")
            kind = str(port.get("kind") or "")
            container_port = port.get("container_port")
            host_port = port.get("host_port")
            publish_host = str(port.get("publish_host") or "")
            if port.get("published") and host_port:
                endpoint = f"{publish_host}:{host_port} -> container {container_port}/tcp"
            else:
                endpoint = f"container {container_port}/tcp"
            lines.append(f"- `{group}` `{name}` `{kind}`: {endpoint}")
        lines.append("")
    lines.extend(
        [
            "The raw compose files do not define a custom Docker network.  FDB and Hub",
            "ports stay bound to the configured private/VPN addresses from the existing",
            "placement files.  Public routing can still be layered on by Coolify/Traefik",
            "later, but the all-father compiler keeps the service-cell port map explicit.",
            "",
        ]
    )
    return "\n".join(lines)

def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compile and deploy guarded all-father Coolify container manifests.",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("network", help="Network identity to compile: test, testnet, or mainnet.")
        subparser.add_argument("--set-id", default="", help="Running set id, e.g. testnet-1, mainnet-1, or mainnet-2.")
        subparser.add_argument("--placement", default="", help="Optional hub/FDB placement JSON path.")
        subparser.add_argument("--qbft-seed", default="", help="Optional QBFT seed name/path. Defaults to network.")
        subparser.add_argument("--allow-mainnet", action="store_true", help="Required when compiling mainnet.")
        subparser.add_argument("--guard-container-port", type=int, default=DEFAULT_GUARD_CONTAINER_PORT)
        subparser.add_argument("--guard-host-base", type=int, default=0)
        subparser.add_argument("--host-port-offset", type=int, default=-1, help="Override imported service port offset; default is derived from network and set id.")
        subparser.add_argument("--state-root-prefix", default=DEFAULT_STATE_ROOT_PREFIX)

    def add_remote_coolify(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--private-state",
            type=Path,
            default=None,
            help="Private state YAML with coolify.hosts.<slot>.name/url/api_token. Defaults to runtime/state/main_computer.private.yaml when present.",
        )
        subparser.add_argument(
            "--set-coolify-url",
            action="append",
            default=[],
            help="Bind a symbolic placement server to a Coolify API base URL. Format: <server-name>:<coolify-base-url>",
        )
        subparser.add_argument("--coolify-token", default="", help="One Coolify token for every server. Prefer token env/file/private-state options.")
        subparser.add_argument("--coolify-token-env", default=fdb_tool().DEFAULT_TOKEN_ENV, help="Default env var containing a Coolify token.")
        subparser.add_argument("--coolify-token-file", default="", help="Default file containing a Coolify token.")
        subparser.add_argument("--set-coolify-token", action="append", default=[], help="Per-server token. Format: <server-name>:<token>")
        subparser.add_argument("--set-coolify-token-env", action="append", default=[], help="Per-server token env var. Format: <server-name>:<ENV_VAR>")
        subparser.add_argument("--set-coolify-token-file", action="append", default=[], help="Per-server token file. Format: <server-name>:<path>")

        subparser.add_argument("--coolify-project-uuid", default="", help="Coolify project UUID used by all servers unless overridden.")
        subparser.add_argument("--coolify-project-name", default="", help="Coolify project name resolved on every server.")
        subparser.add_argument("--set-coolify-project-uuid", action="append", default=[], help="Per-server project UUID. Format: <server-name>:<uuid>")
        subparser.add_argument("--coolify-environment-name", default="", help="Coolify environment name. Defaults to <set-id>-allfather.")
        subparser.add_argument("--coolify-environment-uuid", default="", help="Coolify environment UUID used by all servers unless overridden.")
        subparser.add_argument("--set-coolify-environment-uuid", action="append", default=[], help="Per-server environment UUID. Format: <server-name>:<uuid>")
        subparser.add_argument("--no-create-environment", action="store_true", help="Fail if the named environment is missing.")
        subparser.add_argument("--coolify-server-name", default="", help="Coolify server name resolved on every Coolify API.")
        subparser.add_argument("--coolify-server-uuid", default="", help="Coolify server UUID used by all servers unless overridden.")
        subparser.add_argument("--set-coolify-server-name", action="append", default=[], help="Per-server Coolify server name. Format: <server-name>:<coolify-server-name>")
        subparser.add_argument("--set-coolify-server-uuid", action="append", default=[], help="Per-server Coolify server UUID. Format: <server-name>:<uuid>")
        subparser.add_argument("--coolify-destination-uuid", default="", help="Coolify Docker destination UUID used by all servers unless overridden.")
        subparser.add_argument("--set-coolify-destination-uuid", action="append", default=[], help="Per-server destination UUID. Format: <server-name>:<uuid>")
        subparser.add_argument("--coolify-service-uuid", default="", help="Existing Coolify service UUID used by all cells unless overridden.")
        subparser.add_argument("--set-coolify-service-uuid", action="append", default=[], help="Per-server service UUID. Format: <server-name>:<uuid>")

        subparser.add_argument("--coolify-timeout-s", type=float, default=fdb_tool().DEFAULT_TIMEOUT_S)
        subparser.add_argument("--coolify-retries", type=int, default=fdb_tool().DEFAULT_RETRIES)
        subparser.add_argument("--coolify-retry-sleep-s", type=float, default=fdb_tool().DEFAULT_RETRY_SLEEP_S)
        subparser.add_argument("--no-deploy", action="store_true", help="Create/update only; do not trigger a service deploy.")
        subparser.add_argument("--force-deploy", action="store_true", help="Ask Coolify to force rebuild/redeploy services.")
        subparser.add_argument("--dockerfile", default=DEFAULT_DOCKERFILE, help="Dockerfile path used by generated compose.")
        subparser.add_argument("--image-prefix", default=DEFAULT_IMAGE_PREFIX, help="Service/image name prefix for generated all-father cells.")
        subparser.add_argument("--json", action="store_true", help="Print compact machine-readable JSON.")

    plan_parser = subparsers.add_parser("plan", help="Print a JSON summary of the compiled all-father cells.")
    add_common(plan_parser)
    add_remote_coolify(plan_parser)
    plan_parser.add_argument(
        "--coolify",
        action="store_true",
        help="Render the remote Coolify service plan instead of the local compiler summary.",
    )

    manifest_parser = subparsers.add_parser("manifest", help="Print the full manifest for one compiled cell.")
    add_common(manifest_parser)
    manifest_parser.add_argument("--cell", required=True, help="Cell id or Coolify server name.")

    write_parser = subparsers.add_parser("write", help="Write compose files and manifests for all cells.")
    add_common(write_parser)
    write_parser.add_argument("--out", required=True, help="Output directory.")
    write_parser.add_argument("--dockerfile", default=DEFAULT_DOCKERFILE, help="Dockerfile path used by generated compose.")

    apply_parser = subparsers.add_parser("apply", help="Create/update the guarded all-father services through the Coolify API.")
    add_common(apply_parser)
    add_remote_coolify(apply_parser)
    apply_parser.add_argument("--dry-run", action="store_true", help="Render the remote Coolify plan without API/token/deploy calls.")

    return parser.parse_args(argv)


def _plan_from_args(args: argparse.Namespace) -> AllfatherPlan:
    return build_allfather_plan(
        args.network,
        set_id=args.set_id or None,
        placement_path=args.placement or None,
        qbft_seed=args.qbft_seed or None,
        allow_mainnet=bool(args.allow_mainnet),
        guard_container_port=int(args.guard_container_port),
        guard_host_base=(int(args.guard_host_base) if int(args.guard_host_base or 0) else None),
        host_port_offset=(int(args.host_port_offset) if int(args.host_port_offset) >= 0 else None),
        state_root_prefix=args.state_root_prefix,
    )


def _print_result(result: Mapping[str, Any], *, compact: bool = False) -> None:
    if compact:
        print(json.dumps(result, sort_keys=True))
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = _plan_from_args(args)
        if args.command == "plan":
            if getattr(args, "coolify", False):
                _print_result(coolify_plan_result(plan, args), compact=getattr(args, "json", False))
            else:
                _print_result(plan.to_dict(), compact=getattr(args, "json", False))
            return 0
        if args.command == "manifest":
            needle = safe_id(args.cell, field="cell")
            for cell in plan.cells:
                if needle in {safe_id(cell.cell_id), safe_id(cell.coolify_server)}:
                    _print_result(cell.to_manifest())
                    return 0
            raise AllfatherCompileError(f"No compiled cell matches {args.cell!r}.")
        if args.command == "write":
            written = write_plan(plan, Path(args.out), dockerfile=args.dockerfile)
            _print_result({"written": [str(path) for path in written]})
            return 0
        if args.command == "apply":
            _print_result(coolify_apply_result(plan, args), compact=getattr(args, "json", False))
            return 0
        raise AllfatherCompileError(f"Unhandled command {args.command!r}.")
    except (AllfatherCompileError, hub_service_tool().CoolifyHubDeployError) as exc:
        result = {"ok": False, "error": str(exc), "error_type": type(exc).__name__}
        _print_result(result, compact=getattr(args, "json", False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
