#!/usr/bin/env python3
"""Cleanup of orphaned top-level Ephemeral Mother Coolify services.

This script can be called directly or as the final cleanup step of a successful
Mother add/remove harness. It discovers top-level Coolify service rows in the
configured network/project, selects only rows that are explicitly marked as
ephemeral Mother helpers, and deletes them after an explicit acknowledgement.

It does not inspect or modify validator topology, node identities, routes,
Compose contents of primary node services, Docker containers directly, or
canonical Mother evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping, NoReturn
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.mother.common.coolify_state import (
    CoolifyController,
    CoolifyObservationError,
    get_coolify_json,
    list_coolify_controllers,
)
from tools.mother.common.models import OperationIdentity
from tools.mother.common.paths import MotherPaths
from tools.mother.common.private_state import PrivateStateReadResult, read_private_state


KIND = "main_computer.mother.pristine_cleanup.v1"
EPHEMERAL_DESCRIPTION_PREFIX = "Ephemeral Mother "
KNOWN_EPHEMERAL_PREFIXES = (
    "mother-add-node-validator-admission-voter-",
    "mother-static-node-writer-",
    "mother-helper-cleanup2-",
    "mother-block-advance-watch-",
    "mother-node-remove-helper-setup-runner-",
    "mother-node-remove-helper-setup-smoke-runner-",
    "mother-helper-orphan-cleanup-",
    "mother-replica-guardian-start-",
    "mother-service-line-restart-",
    "mother-qbft-cleanup1-",
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
UUID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")


class MotherPristineCleanupError(RuntimeError):
    pass


def _fail(message: str) -> NoReturn:
    raise MotherPristineCleanupError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or text in {".", ".."} or not IDENTIFIER_RE.fullmatch(text):
        _fail(f"invalid {label}: {value!r}")
    return text


def _uuid(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or not UUID_RE.fullmatch(text):
        _fail(f"invalid {label}: {value!r}")
    return text


def _operation(network: str) -> OperationIdentity:
    network_name = _identifier(network, "network")
    stamp = _stamp()
    return OperationIdentity(
        operation_id=f"mother-pristine-cleanup-{network_name}-{stamp}",
        request_id=f"mother-pristine-cleanup-{network_name}-{stamp}-request",
        network=network_name,
        operation_kind="MOTHER-OP-RESTORE-SERVICE",
    )


def _load_private_state(runtime_state_root: str | Path, network: str) -> PrivateStateReadResult:
    paths = MotherPaths(runtime_state_root=Path(runtime_state_root)).resolve_private_state_paths()
    return read_private_state(paths, operation=_operation(network))


def _private_document(private_state: PrivateStateReadResult) -> Mapping[str, Any]:
    try:
        document = json.loads(private_state.canonical_object_bytes.decode("utf-8"))
    except Exception as exc:
        raise MotherPristineCleanupError("Mother private state is not canonical JSON") from exc
    if not isinstance(document, Mapping):
        _fail("Mother private state is not a JSON object")
    return document


def _controller_project_uuid(private_state: PrivateStateReadResult, network: str, controller_id: str) -> str:
    document = _private_document(private_state)
    networks = document.get("networks")
    body = networks.get(network) if isinstance(networks, Mapping) else None
    coolify = body.get("coolify") if isinstance(body, Mapping) else None
    controllers = coolify.get("controllers") if isinstance(coolify, Mapping) else None
    wire = controllers.get(controller_id) if isinstance(controllers, Mapping) else None
    project_uuid = wire.get("project_uuid") if isinstance(wire, Mapping) else None
    return _uuid(project_uuid, f"{controller_id} project_uuid")


def _items(payload: Any) -> list[Mapping[str, Any]]:
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


def _nested_text(record: Mapping[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        current: Any = record
        for part in path:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(part)
        if isinstance(current, str) and current.strip():
            return current.strip()
    return ""


def _record_uuid(record: Mapping[str, Any]) -> str:
    return str(record.get("uuid") or record.get("id") or "").strip()


def _record_name(record: Mapping[str, Any]) -> str:
    return str(record.get("name") or record.get("service_name") or "").strip()


def _record_description(record: Mapping[str, Any]) -> str:
    return str(record.get("description") or "").strip()


def _record_status(record: Mapping[str, Any]) -> str:
    return str(record.get("status") or record.get("state") or "").strip()


def _record_project_uuid(record: Mapping[str, Any]) -> str:
    return _nested_text(
        record,
        ("project_uuid",),
        ("project", "uuid"),
        ("environment", "project_uuid"),
        ("environment", "project", "uuid"),
    )


def _record_environment_name(record: Mapping[str, Any]) -> str:
    return _nested_text(record, ("environment_name",), ("environment", "name"))


def _merge_records(inventory: Mapping[str, Any], detail: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(inventory)
    merged.update(detail)
    return merged


def _ephemeral_reason(record: Mapping[str, Any]) -> str | None:
    description = _record_description(record)
    if description.startswith(EPHEMERAL_DESCRIPTION_PREFIX):
        return "ephemeral-description"
    name = _record_name(record)
    if any(name.startswith(prefix) for prefix in KNOWN_EPHEMERAL_PREFIXES):
        return "known-ephemeral-prefix"
    return None


def _summary(record: Mapping[str, Any], *, controller_id: str, reason: str) -> dict[str, Any]:
    return {
        "controller_id": controller_id,
        "service_uuid": _uuid(_record_uuid(record), "service_uuid"),
        "service_name": _record_name(record),
        "status": _record_status(record),
        "description": _record_description(record),
        "project_uuid": _record_project_uuid(record),
        "environment_name": _record_environment_name(record),
        "eligibility_reason": reason,
    }


def _open(opener: Any, request: urllib.request.Request, timeout: float):
    if hasattr(opener, "open"):
        return opener.open(request, timeout=timeout)
    if callable(opener):
        return opener(request, timeout=timeout)
    raise TypeError("opener must be callable or provide open(request, timeout=...)")


def _delete_service(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> dict[str, Any]:
    service = _uuid(service_uuid, "service_uuid")
    endpoint = f"/api/v1/services/{urllib.parse.quote(service, safe='')}"
    request = urllib.request.Request(
        controller.base_url + endpoint,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {controller.api_token}",
            "User-Agent": "main-computer-mother-pristine-cleanup/1",
        },
        method="DELETE",
    )
    started = time.monotonic()
    try:
        try:
            response = _open(opener, request, timeout)
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(max_response_bytes + 1)
            response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            raw = exc.read(max_response_bytes + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise MotherPristineCleanupError("Coolify DELETE request failed") from exc
    if len(raw) > max_response_bytes:
        _fail("Coolify DELETE response exceeded max-response-bytes")
    return {
        "method": "DELETE",
        "endpoint": endpoint,
        "status": status,
        "ok": 200 <= status < 300 or status == 404,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
    }


def _detail(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[int, Mapping[str, Any] | None]:
    endpoint = f"/api/v1/services/{urllib.parse.quote(_uuid(service_uuid, 'service_uuid'), safe='')}"
    observation = get_coolify_json(
        controller,
        endpoint,
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if observation.status == 404:
        return observation.status, None
    if not observation.ok:
        _fail(f"Coolify service detail failed with HTTP {observation.status}: {service_uuid}")
    records = _items(observation.payload)
    if len(records) != 1:
        _fail(f"Coolify service detail did not contain exactly one service row: {service_uuid}")
    return observation.status, records[0]


def _discover_controller_candidates(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller: CoolifyController,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    project_uuid = _controller_project_uuid(private_state, network, controller.controller_id)
    observation = get_coolify_json(
        controller,
        "/api/v1/services",
        authenticated=True,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=opener,
    )
    if not observation.ok:
        _fail(f"Coolify service inventory failed with HTTP {observation.status}: {controller.controller_id}")

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for inventory_record in _items(observation.payload):
        raw_uuid = _record_uuid(inventory_record)
        if not raw_uuid or raw_uuid in seen:
            continue
        seen.add(raw_uuid)
        try:
            service_uuid = _uuid(raw_uuid, "service_uuid")
            status, detail_record = _detail(
                controller,
                service_uuid,
                timeout=timeout,
                max_response_bytes=max_response_bytes,
                opener=opener,
            )
        except MotherPristineCleanupError as exc:
            skipped.append({
                "controller_id": controller.controller_id,
                "service_uuid": raw_uuid,
                "reason": f"detail-unusable: {exc}",
            })
            continue
        if status == 404 or detail_record is None:
            continue

        record = _merge_records(inventory_record, detail_record)
        bound_project = _record_project_uuid(record)
        if bound_project != project_uuid:
            skipped.append({
                "controller_id": controller.controller_id,
                "service_uuid": service_uuid,
                "service_name": _record_name(record),
                "reason": "outside-configured-project" if bound_project else "project-binding-missing",
                "observed_project_uuid": bound_project,
                "expected_project_uuid": project_uuid,
            })
            continue
        environment_name = _record_environment_name(record)
        if environment_name and environment_name != network:
            skipped.append({
                "controller_id": controller.controller_id,
                "service_uuid": service_uuid,
                "service_name": _record_name(record),
                "reason": "outside-network-environment",
                "environment_name": environment_name,
            })
            continue

        reason = _ephemeral_reason(record)
        if reason is None:
            continue
        candidates.append(_summary(record, controller_id=controller.controller_id, reason=reason))

    candidates.sort(key=lambda item: (item["controller_id"], item["service_name"], item["service_uuid"]))
    skipped.sort(key=lambda item: (str(item.get("controller_id", "")), str(item.get("service_name", "")), str(item.get("service_uuid", ""))))
    return candidates, skipped


def discover_candidates(
    private_state: PrivateStateReadResult,
    *,
    network: str,
    controller_ids: set[str] | None,
    timeout: float,
    max_response_bytes: int,
    opener: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, CoolifyController]]:
    controllers = [
        item
        for item in list_coolify_controllers(private_state)
        if item.network == network and item.enabled and (controller_ids is None or item.controller_id in controller_ids)
    ]
    if controller_ids is not None:
        found = {item.controller_id for item in controllers}
        missing = sorted(controller_ids - found)
        if missing:
            _fail("configured/enabled Coolify controller(s) not found: " + ", ".join(missing))
    if not controllers:
        _fail(f"no enabled Coolify controllers found for {network}")

    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    controller_map: dict[str, CoolifyController] = {}
    dedupe: set[tuple[str, str]] = set()
    for controller in controllers:
        controller_map[controller.controller_id] = controller
        current, current_skipped = _discover_controller_candidates(
            private_state,
            network=network,
            controller=controller,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        for item in current:
            key = (controller.base_url, item["service_uuid"])
            if key in dedupe:
                continue
            dedupe.add(key)
            candidates.append(item)
        skipped.extend(current_skipped)
    candidates.sort(key=lambda item: (item["controller_id"], item["service_name"], item["service_uuid"]))
    return candidates, skipped, controller_map


def _verify_absent(
    controller: CoolifyController,
    service_uuid: str,
    *,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any,
    sleeper: Any,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    deadline = time.monotonic() + max_wait_seconds
    while True:
        endpoint = f"/api/v1/services/{urllib.parse.quote(service_uuid, safe='')}"
        observation = get_coolify_json(
            controller,
            endpoint,
            authenticated=True,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            opener=opener,
        )
        observations.append({
            "status": observation.status,
            "response_sha256": observation.response_sha256,
            "byte_length": observation.byte_length,
            "elapsed_ms": observation.elapsed_ms,
        })
        if observation.status == 404:
            return observations
        if not observation.ok:
            _fail(f"Coolify absence verification failed with HTTP {observation.status}: {service_uuid}")
        if time.monotonic() >= deadline:
            _fail(f"ephemeral Coolify service remained visible after delete: {service_uuid}")
        if poll_interval_seconds:
            sleeper(poll_interval_seconds)


def run(
    *,
    runtime_state_root: str | Path,
    network: str,
    mode: str,
    controller_ids: list[str],
    acknowledged_delete: bool,
    timeout: float,
    max_response_bytes: int,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    opener: Any = urllib.request.urlopen,
    sleeper: Any = time.sleep,
) -> dict[str, Any]:
    network_name = _identifier(network, "network")
    if mode not in {"inspect", "execute"}:
        _fail("mode must be inspect or execute")
    if timeout <= 0 or max_response_bytes <= 0 or max_wait_seconds < 0 or poll_interval_seconds < 0:
        _fail("timeout/response/wait arguments are invalid")
    selected_controllers = {_identifier(value, "controller-id") for value in controller_ids} or None
    if mode == "execute" and not acknowledged_delete:
        _fail("execute requires --yes-i-know-this-deletes-ephemeral-mother-services")
    if mode == "inspect" and acknowledged_delete:
        _fail("deletion acknowledgement is only valid with execute")

    private_state = _load_private_state(runtime_state_root, network_name)
    try:
        candidates, skipped, controllers = discover_candidates(
            private_state,
            network=network_name,
            controller_ids=selected_controllers,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            opener=opener,
        )
    except CoolifyObservationError as exc:
        raise MotherPristineCleanupError(f"{exc.code}: {exc}") from exc

    result: dict[str, Any] = {
        "kind": KIND,
        "schema_version": 1,
        "status": "ready" if mode == "inspect" else "running",
        "mode": mode,
        "network": network_name,
        "observed_at": _utc_now(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "skipped": skipped,
        "deletions": [],
        "live_mutation_performed": False,
        "policy": {
            "manual_only": False,
            "manual_invocation_supported": True,
            "automatic_harness_final_cleanup_supported": True,
            "allowed_http_methods": ["GET"] if mode == "inspect" else ["GET", "DELETE"],
            "eligibility": "top-level Coolify service belongs to configured project/network and is explicitly Ephemeral Mother",
            "validator_mutation_performed": False,
            "topology_mutation_performed": False,
            "primary_service_compose_modified": False,
        },
    }
    if mode == "inspect":
        result["summary"] = {
            "clean": len(candidates) == 0,
            "eligible_ephemeral_service_count": len(candidates),
            "deleted_count": 0,
            "live_mutation_performed": False,
        }
        return result

    deletions: list[dict[str, Any]] = []
    for candidate in candidates:
        controller_id = candidate["controller_id"]
        controller = controllers[controller_id]
        service_uuid = candidate["service_uuid"]
        # Re-read immediately before DELETE and re-prove the exact eligibility contract.
        _status_code, detail_record = _detail(
            controller,
            service_uuid,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            opener=opener,
        )
        if detail_record is None:
            deletions.append({**candidate, "already_absent": True, "delete": None, "absence_observations": []})
            continue
        project_uuid = _controller_project_uuid(private_state, network_name, controller_id)
        if _record_project_uuid(detail_record) != project_uuid:
            _fail(f"service project binding changed before deletion: {service_uuid}")
        environment_name = _record_environment_name(detail_record)
        if environment_name and environment_name != network_name:
            _fail(f"service environment changed before deletion: {service_uuid}")
        reason = _ephemeral_reason(detail_record)
        if reason is None:
            _fail(f"service is no longer provably ephemeral before deletion: {service_uuid}")
        if _record_name(detail_record) != candidate["service_name"]:
            _fail(f"service name changed before deletion: {service_uuid}")

        deletion = _delete_service(
            controller,
            service_uuid,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            opener=opener,
        )
        if deletion["ok"] is not True:
            _fail(f"Coolify rejected ephemeral service deletion with HTTP {deletion['status']}: {service_uuid}")
        absence = _verify_absent(
            controller,
            service_uuid,
            timeout=float(timeout),
            max_response_bytes=int(max_response_bytes),
            max_wait_seconds=float(max_wait_seconds),
            poll_interval_seconds=float(poll_interval_seconds),
            opener=opener,
            sleeper=sleeper,
        )
        deletions.append({**candidate, "already_absent": deletion["status"] == 404, "delete": deletion, "absence_observations": absence})

    result["status"] = "pass"
    result["deletions"] = deletions
    result["live_mutation_performed"] = bool(deletions)
    result["summary"] = {
        "clean": True,
        "eligible_ephemeral_service_count": len(candidates),
        "deleted_count": sum(1 for item in deletions if not item["already_absent"]),
        "already_absent_count": sum(1 for item in deletions if item["already_absent"]),
        "live_mutation_performed": bool(deletions),
    }
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or delete top-level Ephemeral Mother Coolify service rows from the configured network project.",
        allow_abbrev=False,
    )
    parser.add_argument("mode", choices=["inspect", "execute"])
    parser.add_argument("--runtime-state-root", default=str(Path("runtime") / "state"))
    parser.add_argument("--network", default="mainnet")
    parser.add_argument("--controller-id", action="append", default=[], help="limit cleanup to one configured controller; repeat as needed")
    parser.add_argument("--yes-i-know-this-deletes-ephemeral-mother-services", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=12 * 1024 * 1024)
    parser.add_argument("--max-wait-seconds", type=float, default=60.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run(
            runtime_state_root=args.runtime_state_root,
            network=args.network,
            mode=args.mode,
            controller_ids=args.controller_id,
            acknowledged_delete=args.yes_i_know_this_deletes_ephemeral_mother_services,
            timeout=args.timeout,
            max_response_bytes=args.max_response_bytes,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except MotherPristineCleanupError as exc:
        print(f"MOTHER_PRISTINE_CLEANUP_FAILED: {exc}", file=sys.stderr)
        return 1
    except CoolifyObservationError as exc:
        print(f"MOTHER_PRISTINE_CLEANUP_FAILED: {exc.code}: {exc}", file=sys.stderr)
        return 1

    summary = result.get("summary", {})
    if args.mode == "inspect":
        if summary.get("clean") is True:
            print("MOTHER_PRISTINE_CLEANUP_CLEAN: no Ephemeral Mother Coolify service rows are present.")
        else:
            print(
                "MOTHER_PRISTINE_CLEANUP_READY: "
                f"{summary.get('eligible_ephemeral_service_count', 0)} Ephemeral Mother Coolify service row(s) are eligible for deletion."
            )
    else:
        print(
            "MOTHER_PRISTINE_CLEANUP_CLEAN: "
            f"deleted {summary.get('deleted_count', 0)} Ephemeral Mother Coolify service row(s); absence verified."
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
