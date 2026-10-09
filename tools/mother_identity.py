#!/usr/bin/env python3
"""Reserve the complete offline starter identity in committed Mother state."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except MotherError as exc:
        print(str(exc), file=sys.stderr)
        return exit_code_for(exc)
    except (OSError, RuntimeError, HubAdminPoolError, KeyError, StarterIdentityError, TypeError, ValueError) as exc:
        print(f"mother-identity error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
