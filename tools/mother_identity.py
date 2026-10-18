#!/usr/bin/env python3
"""Reserve the complete offline starter identity in committed Mother state."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
from urllib.parse import urlsplit
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.hub_admin_pool import (
    POOL_SIZE, HubAdminPoolError, complete_pool, inspect_pool,
)
from tools.mother.common.private_state import (
    prepare_private_state_successor,
    read_private_state,
    replace_verified_starter_private_state,
    replace_verified_private_state,
)
from tools.mother.common.testnet_registration_precheck import (
    TestnetPrecheckError, run_testnet_precheck,
)
from tools.mother.common.starter_identity import (
    StarterIdentityError,
    analyze_starter_identity,
    reserve_starter_identity,
)


DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _operation(operation_id: str | None) -> OperationIdentity:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-reserve-starter-{timestamp}",
        request_id="mother-reserve-starter",
        network="mainnet",
        operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )


def _paths(runtime_state_root: Path):
    return MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()


def _document(result) -> dict[str, object]:
    value = json.loads(result.canonical_object_bytes.decode("utf-8"))
    if type(value) is not dict:
        raise RuntimeError("committed Mother identity did not decode to an object")
    return value


def _print_labels(prefix: str, labels: tuple[str, ...]) -> None:
    print(f"{prefix}: " + (", ".join(labels) if labels else "none"))


def _reserve_starter(args: argparse.Namespace) -> int:
    operation = _operation(args.operation_id)
    paths = _paths(Path(args.runtime_state_root))
    current = read_private_state(paths, operation=operation)
    document = _document(current)
    analysis = analyze_starter_identity(document, network=args.network)

    print("starter identity: valid")
    print(f"current generation: {current.binding.generation}")
    print(f"changes required: {'yes' if analysis.changes_required else 'no'}")
    _print_labels("would generate", analysis.generation_labels)
    _print_labels("would derive or repair addresses", analysis.derived_address_labels)
    _print_labels("would reserve nodes", analysis.node_reservations)
    print(f"would add Mother-owned first genesis: {'yes' if analysis.genesis_required else 'no'}")
    print("network access performed: no")
    print("live mutation performed: no")
    print("secrets printed: 0")

    if not analysis.changes_required:
        print("write performed: no (already reserved)")
        return 0
    if not args.write:
        print("write performed: no (dry-run)")
        return 0
    if current.binding.generation != 1:
        raise StarterIdentityError(
            "reserve-starter is limited to the clean generation-one initialization boundary"
        )

    updated_at = args.updated_at or _utc_now()
    reservation = reserve_starter_identity(
        document,
        generated_at=updated_at,
        network=args.network,
    )
    closure = prepare_private_state_successor(
        current,
        reservation.document,
        updated_at=updated_at,
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    installed = replace_verified_starter_private_state(
        paths,
        closure,
        current.binding,
        operation=operation,
    )
    verified = read_private_state(paths, operation=operation)
    if verified.binding != installed.binding:
        raise RuntimeError("installed starter identity did not verify")

    print("write performed: yes")
    print("stable read: passed")
    print(f"generated identities: {len(reservation.generated_labels)}")
    print(f"derived or repaired addresses: {len(reservation.derived_address_labels)}")
    print(f"installed generation: {installed.binding.generation}")
    print(f"installed content_sha256: {installed.binding.content_hash.digest}")
    print(f"installed manifest_sha256: {installed.binding.recovery_manifest_hash.digest}")
    return 0


def _prepare_genesis_hub_admin_pool(args: argparse.Namespace) -> int:
    """Prepare exact genesis-funded admin set at *any* Mother state generation.

    Must run before identity execution / genesis staging because those artifacts
    bind the current private-state generation. Never repair old genesis silently.
    """
    operation = OperationIdentity(
        operation_id=args.operation_id or f"mother-genesis-hub-admin-prep-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        request_id="mother-genesis-hub-admin-prep",
        network=args.network, operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )
    paths = _paths(Path(args.runtime_state_root))
    current = read_private_state(paths, operation=operation)
    document = _document(current)
    original = document["networks"][args.network]
    view = inspect_pool(original)
    print(f"hub admins existing: {len(view['addresses'])}")
    print(f"hub admins missing: {view['new_reservations_needed']}")
    print(f"hub admins target: {POOL_SIZE}")
    print(f"current Mother generation: {current.binding.generation}")
    print("network access performed: no")
    print("private keys printed: 0")
    if not args.write:
        print("write performed: no (dry-run)")
        return 0
    successor = deepcopy(document)
    net = successor["networks"][args.network]
    timestamp = args.updated_at or _utc_now()
    labels = complete_pool(net, generated_at=timestamp)
    # This marker is the explicit opt-in to a *new* genesis. Old accepted
    # genesis receipts are intentionally NOT mutated or reinterpreted.
    descriptor = net.get("genesis")
    if not isinstance(descriptor, dict):
        raise HubAdminPoolError("first genesis descriptor is not yet reserved")
    descriptor["hub_admin_pool_count"] = POOL_SIZE
    if successor == document:
        print("write performed: no (already prepared)")
        return 0
    closure = prepare_private_state_successor(
        current, successor, updated_at=timestamp,
        updated_by_action_id=operation.operation_id, operation=operation,
    )
    installed = replace_verified_private_state(
        paths, closure, current.binding, operation=operation,
    )
    if not installed.installed:
        raise RuntimeError("verified Hub administrator prep successor not installed")
    verified = read_private_state(paths, operation=operation)
    if verified.binding != installed.binding:
        raise RuntimeError("committed Hub administrator prep did not verify")
    print(f"generated new hub admins: {len(labels)}")
    print(f"installed generation: {installed.binding.generation}")
    print("write performed: yes")
    return 0



def _testnet_controller_coordinates(args: argparse.Namespace) -> tuple[str, str, str]:
    """Fail closed on deployment coordinates before reporting a valid preview.

    Public address must agree with the explicitly supplied Coolify origin when
    that origin uses a numeric IPv4 address; VPN address is independently
    supplied from host observations, not guessed from a historical record.
    """
    try:
        public_ip = ipaddress.IPv4Address(args.public_ip.strip())
    except (ValueError, AttributeError) as exc:
        raise StarterIdentityError("--public-ip must be a single IPv4 address") from exc
    if not public_ip.is_global:
        raise StarterIdentityError("--public-ip must be a public, globally routable IPv4 address")

    try:
        vpn_ip = ipaddress.IPv4Address(args.vpn_ip.strip())
    except (ValueError, AttributeError) as exc:
        raise StarterIdentityError("--vpn-ip must be a single IPv4 address") from exc
    shared_vpn_range = ipaddress.IPv4Network("100.64.0.0/10")
    if (
        (not vpn_ip.is_private and vpn_ip not in shared_vpn_range)
        or vpn_ip.is_loopback or vpn_ip.is_link_local
        or vpn_ip.is_unspecified or vpn_ip.is_multicast or vpn_ip.is_reserved
        or vpn_ip == public_ip
    ):
        raise StarterIdentityError("--vpn-ip must be a usable private/VPN IPv4 address distinct from --public-ip")

    hostname = args.droplet_hostname.strip() if isinstance(args.droplet_hostname, str) else ""
    hostname_pattern = r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*"
    if len(hostname) > 253 or not re.fullmatch(hostname_pattern, hostname):
        raise StarterIdentityError("--droplet-hostname must be a valid DNS hostname")

    controller_host = urlsplit(args.controller_url).hostname
    if controller_host:
        try:
            controller_address = ipaddress.ip_address(controller_host)
        except ValueError:
            controller_address = None
        if controller_address is not None and controller_address != public_ip:
            raise StarterIdentityError("--public-ip differs from the numeric --controller-url host")
    return str(public_ip), str(vpn_ip), hostname


def _register_testnet(args: argparse.Namespace) -> int:
    """Reserve a separate B-only testnet in one verified Mother successor.

    Dry-run reads existing committed state without generating key material.
    The write is an explicit compare-and-swap operation and touches no remote host.
    """
    operation = OperationIdentity(
        operation_id=args.operation_id or f"mother-register-testnet-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        request_id="mother-register-testnet",
        network="testnet",
        operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )
    paths = _paths(Path(args.runtime_state_root))
    current = read_private_state(paths, operation=operation)
    document = _document(current)
    networks = document.get("networks")
    if type(networks) is not dict or "mainnet" not in networks:
        raise StarterIdentityError("existing Mother mainnet identity is required")
    if "testnet" in networks and networks["testnet"] is not None:
        raise StarterIdentityError("networks.testnet already exists; refusing to overwrite or rekey it")
    mainnet = networks["mainnet"]
    if type(mainnet) is not dict:
        raise StarterIdentityError("existing networks.mainnet must be a mapping")
    if mainnet.get("chain_id") == 42424241:
        raise StarterIdentityError("mainnet chain ID conflicts with testnet")
    if args.acknowledge_generation is not None and current.binding.generation != args.acknowledge_generation:
        raise StarterIdentityError("Mother generation differs from explicitly acknowledged predecessor")

    for label, value in (("controller-url", args.controller_url),
                         ("project-uuid", args.project_uuid),
                         ("server-uuid", args.server_uuid)):
        if type(value) is not str or not value.strip():
            raise StarterIdentityError(f"--{label} must be explicitly provided")
    url = urlsplit(args.controller_url)
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in {"", "/"}):
        raise StarterIdentityError("--controller-url must be a plain http(s) origin without credentials")
    for label, value in (("project-uuid", args.project_uuid), ("server-uuid", args.server_uuid)):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise StarterIdentityError(f"--{label} must be a Coolify identifier")
    if not args.project_name_hint.strip():
        raise StarterIdentityError("--project-name-hint must be nonempty")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_token_env):
        raise StarterIdentityError("--api-token-env must name an environment variable")
    public_ip, vpn_ip, droplet_hostname = _testnet_controller_coordinates(args)
    # Precheck is mandatory for previews AND writes. A missing token must not
    # leave the operator with a misleading green offline preview.
    token = os.environ.get(args.api_token_env, "").strip()
    if not token or "\n" in token or "\r" in token:
        raise StarterIdentityError(
            "testnet Coolify API token environment variable is required for precheck and registration"
        )
    if args.precheck_timeout <= 0 or args.precheck_timeout > 60:
        raise StarterIdentityError("--precheck-timeout must be within (0, 60] seconds")
    if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
        if not args.allow_insecure_http_api:
            raise StarterIdentityError(
                "Coolify API URL uses plaintext HTTP: use HTTPS, or explicitly pass "
                "--allow-insecure-http-api to send the API token over an unencrypted connection"
            )
        print("WARNING: Coolify API token will traverse an unencrypted HTTP connection")
    report = run_testnet_precheck(
        controller_url=args.controller_url.strip(), token=token,
        project_uuid=args.project_uuid.strip(), server_uuid=args.server_uuid.strip(),
        project_name=args.project_name_hint.strip(), public_ip=public_ip,
        vpn_ip=vpn_ip, droplet_hostname=droplet_hostname,
        timeout=args.precheck_timeout,
    )
    print("Coolify precheck: PASS (authenticated, read-only GET)")
    print(f"Coolify project verified: {args.project_uuid.strip()} ({report.project_name})")
    print(f"Coolify server verified: {args.server_uuid.strip()}")
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    print(f"current Mother generation: {current.binding.generation}")
    print("new network: testnet; chain_id: 42424241")
    print("target: testnetb-super1; controller: coolify-b; one validator/RPC identity")
    print(f"controller public_ip: {public_ip}; vpn_ip: {vpn_ip}; droplet_hostname: {droplet_hostname}")
    print("mainnet modification requested: no")
    print("Coolify operations performed: read-only authenticated GET; no mutations")
    print("secrets printed: 0")
    if not args.write:
        print("write performed: no (dry-run; no identities generated)")
        return 0
    # The verified predecessor was read at the start of this operation. The
    # installer compares its complete binding (not just its generation) with
    # the state still on disk before committing. If any concurrent writer
    # advances Mother during the API precheck or key reservation, the install
    # fails closed with MOTHER_STATE_PRIVATE_STATE_CONFLICT.
    timestamp = args.updated_at or _utc_now()
    successor = deepcopy(document)
    successor["networks"]["testnet"] = {
        "chain_id": 42424241,
        "coolify": {
            "controllers": {
                "coolify-b": {
                    "api_token": token,
                    "enabled": True,
                    "mutation_authority": "observe-only",
                    "observed_environments": {},
                    "public_ip": public_ip,
                    "vpn_ip": vpn_ip,
                    "droplet_hostname": droplet_hostname,
                    "project_name": args.project_name_hint.strip(),
                    "project_uuid": args.project_uuid.strip(),
                    "server_uuid": args.server_uuid.strip(),
                    "project_name_hint": args.project_name_hint,
                    # Persist the canonical origin so every consumer sees
                    # the same URL, regardless of CLI trailing slash spelling.
                    "url": args.controller_url.strip().rstrip("/"),
                },
            },
            "mutation_authority": "observe-only",
        },
        "deployment": {
            "mode": "clean-start",
            "status": "awaiting-offline-plan",
            "targets": {
                "testnetb-super1": {
                    "controller_ref": "networks.testnet.coolify.controllers.coolify-b",
                    "desired_environment_name": "testnet",
                    "desired_service_name": "testnetb-super1",
                    "hub_admin_address": None,
                    "hub_admin_private_key_path": "networks.testnet.node_seed_material.testnetb-super1.wallets.hub_admin.private_key",
                    "key_material_status": "missing-awaiting-reservation",
                    "live_resource_uuid": None,
                    "status": "absent-awaiting-redeployment",
                },
            },
        },
        "foundationdb": {},
        "node_seed_material": {},
        "nodes": {},
        "validators": {},
        "wallets": {},
    }
    reserved = reserve_starter_identity(successor, generated_at=timestamp, network="testnet")
    # No pre-existing network identities may be reused for a new network.
    new_net = reserved.document["networks"]["testnet"]
    def collect_keys(node):
        if isinstance(node, dict):
            for key, val in node.items():
                if key == "private_key" and isinstance(val, str):
                    yield val.lower()
                else:
                    yield from collect_keys(val)
        elif isinstance(node, list):
            for val in node:
                yield from collect_keys(val)
    old_keys = set(collect_keys(document["networks"]))
    new_keys = list(collect_keys(new_net))
    if len(new_keys) != len(set(new_keys)):
        raise StarterIdentityError("testnet reserved duplicate private keys")
    if old_keys.intersection(new_keys):
        raise StarterIdentityError("testnet generated identity overlaps an existing network private key")
    if reserved.document["networks"]["mainnet"] != mainnet:
        raise StarterIdentityError("mainnet identity changed during testnet reservation")
    closure = prepare_private_state_successor(
        current, reserved.document, updated_at=timestamp,
        updated_by_action_id=operation.operation_id, operation=operation,
    )
    installed = replace_verified_private_state(
        paths, closure, current.binding, operation=operation,
    )
    verified = read_private_state(paths, operation=operation)
    if not installed.installed or verified.binding != closure.binding:
        raise RuntimeError("Mother testnet identity successor failed verification")
    if _document(verified)["networks"]["mainnet"] != mainnet:
        raise RuntimeError("Mother mainnet identity changed")
    print(f"reserved testnet identities: {len(reserved.generated_labels)}")
    print(f"installed Mother generation: {installed.binding.generation}")
    print("write performed: yes; verified successor: yes")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve = subparsers.add_parser(
        "reserve-starter",
        help="reserve validator, governance, Hub, node-route, and first-genesis identity",
    )
    reserve.add_argument("--network", default="mainnet")
    reserve.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    reserve.add_argument("--updated-at", help="explicit UTC timestamp")
    reserve.add_argument("--operation-id")
    reserve.add_argument("--write", action="store_true")
    reserve.set_defaults(handler=_reserve_starter)
    prepare = subparsers.add_parser(
        "prepare-genesis-hub-admins",
        help="complete 15 allocated hub_admin identities before identity execution and genesis staging",
    )
    prepare.add_argument("--network", default="mainnet")
    prepare.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    prepare.add_argument("--updated-at")
    prepare.add_argument("--operation-id")
    prepare.add_argument("--write", action="store_true")
    prepare.set_defaults(handler=_prepare_genesis_hub_admin_pool)
    register = subparsers.add_parser(
        "register-testnet",
        help="commit an independent single-node Coolify B testnet identity to mature Mother state",
    )
    register.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    register.add_argument("--controller-url", required=True)
    register.add_argument("--public-ip", required=True, help="verified public IPv4 address of Coolify B")
    register.add_argument("--vpn-ip", required=True, help="verified private/VPN IPv4 address of Coolify B")
    register.add_argument("--droplet-hostname", required=True, help="verified hostname from Coolify B")
    register.add_argument("--project-uuid", required=True)
    register.add_argument("--server-uuid", required=True)
    register.add_argument("--project-name-hint", default="My first project")
    register.add_argument("--api-token-env", default="MC_TESTNET_COOLIFY_B_API_TOKEN",
                          help="required environment variable holding Coolify B token; never pass token as CLI argument")
    register.add_argument("--precheck-timeout", type=float, default=5.0,
                          help="per-request seconds for authenticated read-only precheck")
    register.add_argument("--allow-insecure-http-api", action="store_true",
                          help="explicitly allow transmitting bearer token over plaintext remote HTTP")
    register.add_argument(
        "--acknowledge-generation", type=int,
        help="optional additional generation check; Mother automatically protects --write with its verified predecessor binding",
    )
    register.add_argument("--updated-at")
    register.add_argument("--operation-id")
    register.add_argument("--write", action="store_true")
    register.set_defaults(handler=_register_testnet)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except MotherError as exc:
        print(str(exc), file=sys.stderr)
        return exit_code_for(exc)
    except (OSError, RuntimeError, HubAdminPoolError, KeyError, StarterIdentityError, TestnetPrecheckError, TypeError, ValueError) as exc:
        print(f"mother-identity error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
