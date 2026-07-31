from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml


EXPECTED_SOURCE_KIND = "main_computer.all_father.private_state.v1"
MOTHER_KIND = "main_computer.mother.private_state.v1"

# Verified through the read-only Mother/Coolify inventory.
VERIFIED_COOLIFY_BINDINGS: dict[str, dict[str, Any]] = {
    "A": {
        "expected_name": "coolify-a",
        "project_uuid": "n13kbpfclbkugcww8opjc973",
        "server_uuid": "c11j1nrxs7m2q6of6jmbxoxm",
        "observed_environments": {
            "hub-site": {
                "environment_uuid": "e12qof1qrm584v1a56wzvmd3",
                "reserved_for": "existing-hub-service",
                "available_for_mainnet_nodes": False,
            }
        },
    },
    "C": {
        "expected_name": "coolify-c",
        "project_uuid": "vbja82onq0xpuzy6mwi3v11r",
        "server_uuid": "xsuyj7sxd0kymhhyz2ctcwl4",
        "observed_environments": {},
    },
}

CHAIN_ID_MAINNET = 42424240


class ConversionError(RuntimeError):
    pass


def require_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConversionError(f"{path} must be a mapping")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConversionError(f"cannot read {path}: {exc}") from exc

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConversionError(f"invalid YAML in {path}: {exc}") from exc

    return require_mapping(parsed, "document root")


def convert_controller(
    allfather_hosts: dict[str, Any],
    slot: str,
) -> dict[str, Any]:
    source = require_mapping(
        allfather_hosts.get(slot),
        f"coolify.hosts.{slot}",
    )
    binding = VERIFIED_COOLIFY_BINDINGS[slot]

    expected_name = binding["expected_name"]
    actual_name = source.get("name")
    if actual_name != expected_name:
        raise ConversionError(
            f"coolify.hosts.{slot}.name must be {expected_name!r}; "
            f"found {actual_name!r}"
        )

    required_source_fields = (
        "api_token",
        "droplet_hostname",
        "project_name",
        "public_ip",
        "url",
        "vpn_ip",
    )
    missing = [
        field
        for field in required_source_fields
        if source.get(field) in (None, "")
    ]
    if missing:
        raise ConversionError(
            f"coolify.hosts.{slot} is missing: {', '.join(missing)}"
        )

    # Transient fields such as api_reachable, api_version, and last_seen
    # are deliberately not copied into authoritative Mother state.
    return {
        "api_token": source["api_token"],
        "droplet_hostname": source["droplet_hostname"],
        "enabled": True,
        "legacy_host_slot": slot,
        "observed_environments": binding["observed_environments"],
        "project_name": source["project_name"],
        "project_uuid": binding["project_uuid"],
        "public_ip": source["public_ip"],
        "server_uuid": binding["server_uuid"],
        "url": source["url"],
        "vpn_ip": source["vpn_ip"],
    }


def convert_allfather(source: dict[str, Any]) -> dict[str, Any]:
    if source.get("schema_version") != 1:
        raise ConversionError(
            "source schema_version must be 1"
        )

    if source.get("kind") != EXPECTED_SOURCE_KIND:
        raise ConversionError(
            f"source kind must be {EXPECTED_SOURCE_KIND!r}"
        )

    coolify = require_mapping(source.get("coolify"), "coolify")
    hosts = require_mapping(coolify.get("hosts"), "coolify.hosts")

    networks = require_mapping(source.get("networks"), "networks")
    allfather_mainnet = require_mapping(
        networks.get("mainnet"),
        "networks.mainnet",
    )

    wallets = require_mapping(
        allfather_mainnet.get("wallets"),
        "networks.mainnet.wallets",
    )
    deployer = require_mapping(
        wallets.get("deployer"),
        "networks.mainnet.wallets.deployer",
    )

    if not deployer.get("private_key"):
        raise ConversionError(
            "networks.mainnet.wallets.deployer.private_key is required"
        )

    foundationdb = require_mapping(
        allfather_mainnet.get("foundationdb"),
        "networks.mainnet.foundationdb",
    )

    mother = {
        "kind": MOTHER_KIND,
        "networks": {
            "mainnet": {
                "chain_id": CHAIN_ID_MAINNET,
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
                    "targets": {
                        "mainneta-super1": {
                            "controller_ref": (
                                "networks.mainnet.coolify.controllers.coolify-a"
                            ),
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainneta-super1",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                        "mainnetc-super1": {
                            "controller_ref": (
                                "networks.mainnet.coolify.controllers.coolify-c"
                            ),
                            "desired_environment_name": "mainnet",
                            "desired_service_name": "mainnetc-super1",
                            "live_resource_uuid": None,
                            "status": "absent-awaiting-redeployment",
                        },
                    },
                },
                "foundationdb": {
                    "cluster_description": foundationdb.get(
                        "cluster_description"
                    ),
                    "cluster_id": foundationdb.get("cluster_id"),
                    "coordinator_policy": foundationdb.get(
                        "coordinator_policy"
                    ),
                    "deployment_status": "absent-awaiting-redeployment",
                    "reconfigure_after_join": bool(
                        foundationdb.get("reconfigure_after_join", True)
                    ),
                },
                # Old live topology and hub-admin status are deliberately
                # discarded. Mother begins with no asserted live nodes.
                "node_seed_material": {},
                "nodes": {},
                "validators": {},
                "wallets": {
                    "deployer": deployer,
                },
            }
        },
        "schema_version": 1,
    }

    validate_result(mother)
    return mother


def validate_result(mother: dict[str, Any]) -> None:
    if mother.get("kind") != MOTHER_KIND:
        raise ConversionError("generated Mother kind is invalid")

    if mother.get("schema_version") != 1:
        raise ConversionError("generated Mother schema_version is invalid")

    networks = require_mapping(mother.get("networks"), "generated.networks")
    if set(networks) != {"mainnet"}:
        raise ConversionError(
            "generated state must contain only mainnet"
        )

    mainnet = require_mapping(networks["mainnet"], "generated.mainnet")
    coolify = require_mapping(mainnet.get("coolify"), "generated.coolify")

    if coolify.get("mutation_authority") != "observe-only":
        raise ConversionError(
            "generated state must remain observe-only"
        )

    controllers = require_mapping(
        coolify.get("controllers"),
        "generated.coolify.controllers",
    )
    if set(controllers) != {"coolify-a", "coolify-c"}:
        raise ConversionError(
            "generated controllers must be coolify-a and coolify-c"
        )

    for controller_name, controller in controllers.items():
        controller = require_mapping(
            controller,
            f"generated controller {controller_name}",
        )
        required = (
            "api_token",
            "project_uuid",
            "server_uuid",
            "url",
        )
        missing = [
            field
            for field in required
            if controller.get(field) in (None, "")
        ]
        if missing:
            raise ConversionError(
                f"{controller_name} missing: {', '.join(missing)}"
            )

    targets = require_mapping(
        mainnet.get("deployment", {}).get("targets"),
        "generated deployment targets",
    )
    for target_name, target in targets.items():
        target = require_mapping(
            target,
            f"generated target {target_name}",
        )
        if target.get("status") != "absent-awaiting-redeployment":
            raise ConversionError(
                f"{target_name} incorrectly claims a live deployment"
            )
        if target.get("live_resource_uuid") is not None:
            raise ConversionError(
                f"{target_name} must not have a live resource UUID"
            )


def write_yaml(path: Path, document: dict[str, Any], force: bool) -> None:
    if path.exists() and not force:
        raise ConversionError(
            f"{path} already exists; pass --force to replace it"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    rendered = yaml.safe_dump(
        document,
        sort_keys=True,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )

    path.write_text(rendered, encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert legacy Allfather private state into a clean-start "
            "Mother bootstrap source."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to the Allfather private-state YAML.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime/state/mother-bootstrap.private.yaml"),
        help=(
            "Mother bootstrap YAML to write. "
            "Default: runtime/state/mother-bootstrap.private.yaml"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace the output file if it already exists.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        source = load_yaml(args.source)
        mother = convert_allfather(source)
        write_yaml(args.output, mother, args.force)
    except ConversionError as exc:
        print(f"conversion failed: {exc}", file=sys.stderr)
        return 1

    print("conversion: passed")
    print(f"source: {args.source}")
    print(f"output: {args.output}")
    print("networks: mainnet")
    print("controllers: mainnet/coolify-a, mainnet/coolify-c")
    print("mutation authority: mainnet=observe-only")
    print("live deployments claimed: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())