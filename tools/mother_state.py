#!/usr/bin/env python3
"""Bootstrap, validate, and safely inspect local Mother private state."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.canonical import canonical_yaml
from tools.mother.common.errors import MotherError, exit_code_for
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import (
    install_verified_private_state,
    prepare_private_state_bootstrap,
    read_private_state,
)


DEFAULT_SOURCE = Path("runtime/state/mother-bootstrap.private.yaml")
DEFAULT_RUNTIME_STATE_ROOT = Path("runtime/state")
_PRIVATE_KEY_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
_BEARER_TOKEN_RE = re.compile(r"^[0-9]+\|[A-Za-z0-9._~-]{16,}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _operation(command: str, *, operation_id: str | None = None) -> OperationIdentity:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return OperationIdentity(
        operation_id=operation_id or f"mother-state-{command}-{timestamp}",
        request_id=f"mother-state-cli-{command}",
        network="local",
        operation_kind="MOTHER-OP-IDENTITY-ROTATION",
    )


def _load_source(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"could not read bootstrap source: {path}") from exc
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError("bootstrap source is malformed YAML") from exc
    if type(value) is not dict:
        raise ValueError("bootstrap source must contain a YAML mapping")
    return value


def _paths(runtime_state_root: Path):
    return MotherPaths(runtime_state_root=runtime_state_root).resolve_private_state_paths()


def _controller_ids(document: dict[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    networks = document.get("networks")
    if type(networks) is not dict:
        return ()
    for network, body in networks.items():
        if type(network) is not str or type(body) is not dict:
            continue
        coolify = body.get("coolify")
        controllers = coolify.get("controllers") if type(coolify) is dict else None
        if type(controllers) is not dict:
            continue
        found.extend(
            f"{network}/{controller}"
            for controller in controllers
            if type(controller) is str
        )
    return tuple(sorted(found))


def _mutation_authorities(document: dict[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    networks = document.get("networks")
    if type(networks) is not dict:
        return ()
    for network, body in networks.items():
        if type(network) is not str or type(body) is not dict:
            continue
        coolify = body.get("coolify")
        authority = coolify.get("mutation_authority") if type(coolify) is dict else None
        if authority is not None:
            found.append(f"{network}={authority}")
    return tuple(sorted(found))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return lowered in {
        "api_key",
        "api_token",
        "access_token",
        "refresh_token",
        "bearer_token",
        "client_secret",
        "credential",
        "credentials",
        "mnemonic",
        "passphrase",
        "password",
        "private_key",
        "secret",
        "token",
    } or lowered.endswith(("_api_key", "_api_token", "_access_token", "_private_key", "_secret", "_password"))


def _redact(value: Any, *, key: str = "") -> Any:
    if key and _is_sensitive_key(key):
        return "<redacted>"
    if type(value) is dict:
        return {item_key: _redact(item, key=item_key) for item_key, item in value.items()}
    if type(value) is list:
        return [_redact(item) for item in value]
    if type(value) is str and (_PRIVATE_KEY_RE.fullmatch(value) or _BEARER_TOKEN_RE.fullmatch(value)):
        return "<redacted>"
    return value


def _print_binding(prefix: str, binding: Any) -> None:
    print(f"{prefix} generation: {binding.generation}")
    print(f"{prefix} content_sha256: {binding.content_hash.digest}")
    print(f"{prefix} manifest_sha256: {binding.recovery_manifest_hash.digest}")


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    source = Path(args.source)
    runtime_state_root = Path(args.runtime_state_root)
    document = _load_source(source)
    operation = _operation("bootstrap", operation_id=args.operation_id)
    paths = _paths(runtime_state_root)
    updated_at = args.updated_at or _utc_now()
    closure = prepare_private_state_bootstrap(
        paths,
        document,
        updated_at=updated_at,
        updated_by_action_id=operation.operation_id,
        operation=operation,
    )
    canonicalized = canonical_yaml(document) != source.read_bytes()

    if paths.recovery_manifest.exists():
        current = read_private_state(paths, operation=operation)
        if current.document_bytes != closure.document_bytes:
            raise ValueError("a different committed Mother private state already exists")
        print("source schema: valid")
        print(f"source canonicalization: {'required' if canonicalized else 'already-canonical'}")
        print("target: already-committed")
        _print_binding("private-state", current.binding)
        print("recovery objects: 0")
        print("controllers: " + (", ".join(_controller_ids(document)) or "none"))
        print("mutation authority: " + (", ".join(_mutation_authorities(document)) or "unspecified"))
        print("write performed: no")
        return 0

    partial_files = tuple(path for path in paths.root.rglob("*") if path.is_file()) if paths.root.exists() else ()
    if partial_files:
        raise ValueError("incomplete Mother private-state target already contains data")

    print("source schema: valid")
    print(f"source canonicalization: {'required' if canonicalized else 'already-canonical'}")
    print("target: absent")
    _print_binding("planned", closure.binding)
    print("recovery objects: 0")
    print("controllers: " + (", ".join(_controller_ids(document)) or "none"))
    print("mutation authority: " + (", ".join(_mutation_authorities(document)) or "unspecified"))
    print("would write:")
    print(f"  {paths.identity_file}")
    print(f"  {paths.metadata_file}")
    print(f"  {paths.recovery_manifest}")

    if not args.write:
        print("write performed: no (dry-run)")
        return 0

    result = install_verified_private_state(
        paths,
        closure,
        None,
        operation=operation,
    )
    verified = read_private_state(paths, operation=operation)
    if verified.binding != result.binding:
        raise RuntimeError("installed private-state binding did not verify")
    print("write performed: yes")
    print("stable read: passed")
    _print_binding("installed", result.binding)
    return 0


def _read(args: argparse.Namespace, command: str):
    operation = _operation(command, operation_id=getattr(args, "operation_id", None))
    return read_private_state(_paths(Path(args.runtime_state_root)), operation=operation)


def _cmd_validate(args: argparse.Namespace) -> int:
    result = _read(args, "validate")
    document = json.loads(result.canonical_object_bytes.decode("utf-8"))
    print("private-state schema: valid")
    print("stable read: passed")
    print("permissions/owner: verified")
    _print_binding("private-state", result.binding)
    print(f"recovery objects: {len(result.recovery_objects)}")
    print("controllers: " + (", ".join(_controller_ids(document)) or "none"))
    print("mutation authority: " + (", ".join(_mutation_authorities(document)) or "unspecified"))
    print("secrets printed: 0")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    if not args.redacted:
        raise ValueError("show requires --redacted; unredacted display is forbidden")
    result = _read(args, "show")
    document = json.loads(result.canonical_object_bytes.decode("utf-8"))
    sys.stdout.buffer.write(canonical_yaml(_redact(document)))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser("bootstrap", help="plan or install generation-one Mother private state")
    bootstrap.add_argument("--source", default=str(DEFAULT_SOURCE))
    bootstrap.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    bootstrap.add_argument("--updated-at", help="explicit UTC timestamp for reproducible bootstrap material")
    bootstrap.add_argument("--operation-id")
    bootstrap.add_argument("--write", action="store_true", help="perform the manifest-last installation")
    bootstrap.set_defaults(handler=_cmd_bootstrap)

    validate = subparsers.add_parser("validate", help="verify committed Mother private state through the production reader")
    validate.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    validate.add_argument("--operation-id")
    validate.set_defaults(handler=_cmd_validate)

    show = subparsers.add_parser("show", help="display committed Mother private state with secrets removed")
    show.add_argument("--runtime-state-root", default=str(DEFAULT_RUNTIME_STATE_ROOT))
    show.add_argument("--operation-id")
    show.add_argument("--redacted", action="store_true")
    show.set_defaults(handler=_cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except MotherError as exc:
        print(str(exc), file=sys.stderr)
        return exit_code_for(exc)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"mother-state error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
