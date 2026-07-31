#!/usr/bin/env python3
"""Create fresh committed Mother state directly from Allfather private state.

The Allfather document is converted in memory. No intermediate Mother YAML is
written. By default, this command performs a dry run. Pass --write to create:

    runtime/state/mother/identity.private.yaml
    runtime/state/mother/identity.private.meta.json
    runtime/state/mother/private-recovery/manifest.json
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)


from tools.mother.common.starter_identity import reserve_starter_identity

ALLFATHER_KIND = "main_computer.all_father.private_state.v1"
MOTHER_KIND = "main_computer.mother.private_state.v1"

DEFAULT_SOURCE = Path("runtime/state/all_father.private.yaml")
DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")

MAINNET_CHAIN_ID = 42424240


# Verified through the real read-only Coolify observations.
VERIFIED_COOLIFY_BINDINGS: dict[str, dict[str, Any]] = {
    "A": {
        "controller_id": "coolify-a",
        "project_uuid": "n13kbpfclbkugcww8opjc973",
        "server_uuid": "c11j1nrxs7m2q6of6jmbxoxm",
        "observed_environments": {
            "hub-site": {
                "available_for_mainnet_nodes": False,
                "environment_uuid": "e12qof1qrm584v1a56wzvmd3",
                "reserved_for": "existing-hub-service",
            }
        },
    },
    "C": {
        "controller_id": "coolify-c",
        "project_uuid": "vbja82onq0xpuzy6mwi3v11r",
        "server_uuid": "xsuyj7sxd0kymhhyz2ctcwl4",
        "observed_environments": {},
    },
}


TARGETS: dict[str, dict[str, str]] = {
    "mainneta-super1": {
        "slot": "A",
        "controller_id": "coolify-a",
    },
    "mainnetc-super1": {
        "slot": "C",
        "controller_id": "coolify-c",
    },
}


class ConversionError(ValueError):
    """The Allfather source cannot be safely converted."""


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def operation_identity(operation_id: str | None) -> OperationIdentity:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    return OperationIdentity(
        operation_id=operation_id or f"mother-from-allfather-{timestamp}",
        request_id="mother-from-allfather",
        network="mainnet",
        operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ConversionError(f"{path} must be a mapping")

    return value


def require_text(
    mapping: dict[str, Any],
    key: str,
    path: str,
) -> str:
    value = mapping.get(key)

    if type(value) is not str or not value.strip():
        raise ConversionError(f"{path}.{key} must be a non-empty string")

    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConversionError(
            f"could not read Allfather state: {path}"
        ) from exc

    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConversionError(
            "Allfather state is malformed YAML"
        ) from exc

    return require_mapping(document, "document")


def convert_controller(
    hosts: dict[str, Any],
    slot: str,
) -> dict[str, Any]:
    source_path = f"coolify.hosts.{slot}"
    source = require_mapping(hosts.get(slot), source_path)

    binding = VERIFIED_COOLIFY_BINDINGS[slot]
    expected_name = binding["controller_id"]

    actual_name = require_text(source, "name", source_path)

    if actual_name != expected_name:
        raise ConversionError(
            f"{source_path}.name must be {expected_name!r}; "
            f"found {actual_name!r}"
        )

    # api_reachable, api_version, and last_seen are deliberately omitted.
    # They are transient observations, not durable identity.
    return {
        "api_token": require_text(source, "api_token", source_path),
        "droplet_hostname": require_text(
            source,
            "droplet_hostname",
            source_path,
        ),
        "enabled": True,
        "legacy_host_slot": slot,
        "observed_environments": deepcopy(
            binding["observed_environments"]
        ),
        "project_name": require_text(
            source,
            "project_name",
            source_path,
        ),
        "project_uuid": binding["project_uuid"],
        "public_ip": require_text(
            source,
            "public_ip",
            source_path,
        ),
        "server_uuid": binding["server_uuid"],
        "url": require_text(source, "url", source_path),
        "vpn_ip": require_text(source, "vpn_ip", source_path),
    }


def convert_targets(
    mainnet: dict[str, Any],
) -> dict[str, Any]:
    huddle = require_mapping(
        mainnet.get("huddle"),
        "networks.mainnet.huddle",
    )

    hub_admins = require_mapping(
        huddle.get("hub_admins"),
        "networks.mainnet.huddle.hub_admins",
    )

    active = require_mapping(
        hub_admins.get("active"),
        "networks.mainnet.huddle.hub_admins.active",
    )

    seed_material = require_mapping(
        mainnet.get("node_seed_material"),
        "networks.mainnet.node_seed_material",
    )

    converted: dict[str, Any] = {}

    for service_name, specification in TARGETS.items():
        source_path = (
            "networks.mainnet.huddle.hub_admins.active."
            f"{service_name}"
        )

        legacy = require_mapping(
            active.get(service_name),
            source_path,
        )

        slot = specification["slot"]
        controller_id = specification["controller_id"]

        if legacy.get("host_slot") != slot:
            raise ConversionError(
                f"{source_path}.host_slot must be {slot!r}"
            )

        if legacy.get("coolify_server") != controller_id:
            raise ConversionError(
                f"{source_path}.coolify_server must be "
                f"{controller_id!r}"
            )

        converted[service_name] = {
            "controller_ref": (
                "networks.mainnet.coolify.controllers."
                f"{controller_id}"
            ),
            "desired_environment_name": "mainnet",
            "desired_service_name": service_name,
            "hub_admin_address": require_text(
                legacy,
                "address",
                source_path,
            ),
            "hub_admin_private_key_path": require_text(
                legacy,
                "private_key_path",
                source_path,
            ),
            "key_material_status": (
                "present"
                if service_name in seed_material
                else "missing-from-allfather-source"
            ),
            "live_resource_uuid": None,
            "status": "absent-awaiting-redeployment",
        }

    return converted


def convert_allfather(
    source: dict[str, Any],
) -> dict[str, Any]:
    if source.get("schema_version") != 1:
        raise ConversionError("schema_version must be 1")

    if source.get("kind") != ALLFATHER_KIND:
        raise ConversionError(
            f"kind must be {ALLFATHER_KIND!r}"
        )

    coolify = require_mapping(
        source.get("coolify"),
        "coolify",
    )

    hosts = require_mapping(
        coolify.get("hosts"),
        "coolify.hosts",
    )

    networks = require_mapping(
        source.get("networks"),
        "networks",
    )

    mainnet = require_mapping(
        networks.get("mainnet"),
        "networks.mainnet",
    )

    wallets = require_mapping(
        mainnet.get("wallets"),
        "networks.mainnet.wallets",
    )

    deployer = require_mapping(
        wallets.get("deployer"),
        "networks.mainnet.wallets.deployer",
    )

    require_text(
        deployer,
        "private_key",
        "networks.mainnet.wallets.deployer",
    )

    foundationdb = require_mapping(
        mainnet.get("foundationdb"),
        "networks.mainnet.foundationdb",
    )

    seed_material = require_mapping(
        mainnet.get("node_seed_material"),
        "networks.mainnet.node_seed_material",
    )

    return {
        "kind": MOTHER_KIND,
        "networks": {
            "mainnet": {
                "chain_id": MAINNET_CHAIN_ID,
                "coolify": {
                    "controllers": {
                        "coolify-a": convert_controller(hosts, "A"),
                        "coolify-c": convert_controller(hosts, "C"),
                    },
                    "mutation_authority": "observe-only",
                },
                "deployment": {
                    "mode": "clean-start",
                    "status": "awaiting-offline-plan",
                    "targets": convert_targets(mainnet),
                },
                "foundationdb": {
                    "cluster_description": require_text(
                        foundationdb,
                        "cluster_description",
                        "networks.mainnet.foundationdb",
                    ),
                    "cluster_id": require_text(
                        foundationdb,
                        "cluster_id",
                        "networks.mainnet.foundationdb",
                    ),
                    "coordinator_policy": require_text(
                        foundationdb,
                        "coordinator_policy",
                        "networks.mainnet.foundationdb",
                    ),
                    "deployment_status": (
                        "absent-awaiting-redeployment"
                    ),
                    "reconfigure_after_join": bool(
                        foundationdb.get(
                            "reconfigure_after_join",
                            True,
                        )
                    ),
                },

                # Preserve available key material.
                # Do not preserve obsolete active/retired service status.
                "node_seed_material": deepcopy(seed_material),

                # No live Mother topology is asserted before redeployment.
                "nodes": {},
                "validators": {},

                "wallets": {
                    "deployer": deepcopy(deployer),
                },
            }
        },
        "schema_version": 1,
    }


def resolve_paths(runtime_state_root: Path):
    return MotherPaths(
        runtime_state_root=runtime_state_root
    ).resolve_private_state_paths()


def target_contains_files(root: Path) -> bool:
    return root.exists() and any(
        path.is_file()
        for path in root.rglob("*")
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help=(
            "Allfather private-state YAML. "
            "Default: runtime/state/all_father.private.yaml"
        ),
    )

    parser.add_argument(
        "--runtime-state-root",
        default=str(DEFAULT_RUNTIME_STATE_ROOT),
        help="Default: runtime/state",
    )

    parser.add_argument(
        "--updated-at",
        help="Optional explicit UTC timestamp",
    )

    parser.add_argument(
        "--operation-id",
    )

    parser.add_argument(
        "--write",
        action="store_true",
        help="Install the fresh generation-one Mother state",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        source_path = Path(args.source)
        runtime_state_root = Path(args.runtime_state_root)

        allfather = load_yaml(source_path)
        mother = convert_allfather(allfather)

        operation = operation_identity(args.operation_id)
        paths = resolve_paths(runtime_state_root)
        updated_at = args.updated_at or utc_now()
        reservation = reserve_starter_identity(
            mother,
            generated_at=updated_at,
            network="mainnet",
        )

        # This creates the complete generation-one closure in memory.
        # It does not create an intermediate YAML file.
        closure = prepare_private_state_bootstrap(
            paths,
            reservation.document,
            updated_at=updated_at,
            updated_by_action_id=operation.operation_id,
            operation=operation,
        )

        print("Allfather conversion: valid")
        print("Mother document: valid")
        print("networks: mainnet")
        print(
            "controllers: "
            "mainnet/coolify-a, mainnet/coolify-c"
        )
        print(
            "mutation authority: "
            "mainnet=observe-only"
        )
        print("live deployments claimed: 0")
        print(f"starter identities generated: {len(reservation.generated_labels)}")
        print(f"starter addresses derived or repaired: {len(reservation.derived_address_labels)}")
        print(
            f"planned generation: "
            f"{closure.binding.generation}"
        )
        print(
            "planned content_sha256: "
            f"{closure.binding.content_hash.digest}"
        )
        print(
            "planned manifest_sha256: "
            f"{closure.binding.recovery_manifest_hash.digest}"
        )

        target_present = target_contains_files(paths.root)

        print(
            f"target: "
            f"{'present' if target_present else 'absent'}"
        )

        print("would write:")
        print(f"  {paths.identity_file}")
        print(f"  {paths.metadata_file}")
        print(f"  {paths.recovery_manifest}")

        if not args.write:
            print("write performed: no (dry-run)")
            return 0

        if target_present:
            raise ConversionError(
                "committed Mother state already exists; "
                "move the entire directory out of the way first: "
                f"{paths.root}"
            )

        installed = install_verified_private_state(
            paths,
            closure,
            None,
            operation=operation,
        )

        verified = read_private_state(
            paths,
            operation=operation,
        )

        if verified.binding != installed.binding:
            raise RuntimeError(
                "installed Mother binding did not verify"
            )

        print("write performed: yes")
        print("stable read: passed")
        print(
            f"installed generation: "
            f"{installed.binding.generation}"
        )
        print(
            "installed content_sha256: "
            f"{installed.binding.content_hash.digest}"
        )
        print(
            "installed manifest_sha256: "
            f"{installed.binding.recovery_manifest_hash.digest}"
        )

        return 0

    except MotherError as exc:
        print(str(exc), file=sys.stderr)
        return exit_code_for(exc)

    except (
        ConversionError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ) as exc:
        print(
            f"mother-from-allfather error: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())