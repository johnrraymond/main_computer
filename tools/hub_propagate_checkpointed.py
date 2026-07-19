#!/usr/bin/env python3
"""Checkpointed, fail-fast hub-propagate runner.

This script intentionally owns the orchestration loop instead of delegating to
allfather_control.hub_propagate().  It reuses the low-level Coolify/render/wait
helpers from allfather_control, but checkpoints after every irreversible or
long-running step so a later run can resume from the last safe point.

Typical use:
    python tools/hub_propagate_checkpointed.py mainnet --allow-mainnet

The checkpoint defaults to:
    .allfather-hub-propagate-checkpoints/<network>.json
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

try:
    import allfather_control as af
except ModuleNotFoundError:  # pragma: no cover - helpful when imported as tools.*
    from tools import allfather_control as af  # type: ignore


SCHEMA_VERSION = "main-computer.checkpointed-hub-propagate.v1"
DEFAULT_CHECKPOINT_DIR = Path(".allfather-hub-propagate-checkpoints")

SENSITIVE_KEY_FRAGMENTS = (
    "private",
    "secret",
    "token",
    "password",
    "dynamic_config_b64",
    "callback_token",
    "owner_private_key",
    "deployer_private_key",
    "hub_admin_private_key",
)


class FailFast(RuntimeError):
    """A non-waitable condition was observed; operator action is required."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return repr(value)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_s = str(key)
            if any(fragment in key_s.lower() for fragment in SENSITIVE_KEY_FRAGMENTS):
                out[key_s] = "<redacted>"
            else:
                out[key_s] = _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def print_json(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(_redact(payload), indent=2, sort_keys=True, default=_json_default) + "\n")
    sys.stdout.flush()


LOG_PREFIX = "[checkpointed-hub-propagate]"


def _short_value(value: Any, *, max_text: int = 180) -> Any:
    """Return a compact, safe value for one-line stdout progress logs."""

    value = _redact(value)
    if isinstance(value, Mapping):
        preferred = (
            "ok",
            "ready",
            "observed",
            "status",
            "reason",
            "stage",
            "host",
            "service_name",
            "domain",
            "service_uuid",
            "application_uuid",
            "deployment_id",
            "node_count",
            "super_node_count",
            "local_super_node_count",
            "checked_hosts",
            "admin_count",
            "source",
        )
        out: dict[str, Any] = {}
        for key in preferred:
            if key in value:
                out[key] = _short_value(value[key], max_text=max_text)
        if out:
            return out
        return {"keys": sorted(str(key) for key in value.keys())[:12], "key_count": len(value)}
    if isinstance(value, (list, tuple)):
        if len(value) <= 6 and all(not isinstance(item, (Mapping, list, tuple)) for item in value):
            return [_short_value(item, max_text=max_text) for item in value]
        return {"item_count": len(value)}
    if isinstance(value, str):
        if len(value) > max_text:
            return value[: max_text - 3] + "..."
        return value
    return value


def log_stdout(event: str, **fields: Any) -> None:
    """Emit a compact checkpoint/progress line to stdout immediately."""

    payload = {"event": event, **{key: _short_value(value) for key, value in fields.items()}}
    sys.stdout.write(LOG_PREFIX + " " + json.dumps(payload, sort_keys=True, default=_json_default) + "\n")
    sys.stdout.flush()


def log_checkpoint_event(event: str, path: Path, fields: Mapping[str, Any]) -> None:
    visible: dict[str, Any] = {"checkpoint_file": str(path)}
    for key in ("stage", "reason", "network", "selected_hosts", "ok", "errors"):
        if key in fields:
            visible[key] = fields[key]
    payload = fields.get("payload")
    if isinstance(payload, Mapping):
        for key in (
            "reason",
            "ok",
            "ready",
            "observed",
            "service_name",
            "host",
            "domain",
            "service_uuid",
            "application_uuid",
            "deployment_id",
            "node_count",
            "super_node_count",
            "local_super_node_count",
            "checked_hosts",
            "admin_count",
            "coolify_summary",
        ):
            if key in payload and key not in visible:
                visible[key] = payload[key]
    log_stdout(event, **visible)


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_checkpoint(path: Path, *, network: str, reset: bool = False) -> dict[str, Any]:
    if reset and path.exists():
        path.unlink()
    if not path.exists():
        return {
            "schema": SCHEMA_VERSION,
            "network": network,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "status": "new",
            "completed": {},
            "failed": {},
            "events": [],
        }
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FailFast(f"cannot read checkpoint {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(state, dict):
        raise FailFast(f"checkpoint {path} is not a JSON object")
    if state.get("schema") != SCHEMA_VERSION:
        raise FailFast(f"checkpoint {path} has schema={state.get('schema')!r}; expected {SCHEMA_VERSION!r}")
    if str(state.get("network") or "") != network:
        raise FailFast(f"checkpoint {path} is for network={state.get('network')!r}, not {network!r}")
    state.setdefault("completed", {})
    state.setdefault("failed", {})
    state.setdefault("events", [])
    return state


def save_event(state: dict[str, Any], path: Path, event: str, **fields: Any) -> None:
    state["updated_at"] = _now_iso()
    state.setdefault("events", []).append(
        {
            "at": state["updated_at"],
            "event": event,
            **_redact(fields),
        }
    )
    # Keep the checkpoint useful but bounded.
    if len(state["events"]) > 400:
        state["events"] = state["events"][-400:]
    atomic_write_json(path, state)
    log_checkpoint_event(event, path, fields)


def mark_completed(state: dict[str, Any], path: Path, key: str, payload: Mapping[str, Any] | None = None) -> None:
    state.setdefault("completed", {})[key] = {
        "at": _now_iso(),
        **(_redact(dict(payload or {}))),
    }
    state.setdefault("failed", {}).pop(key, None)
    state["status"] = "running"
    save_event(state, path, "completed", stage=key, payload=payload or {})


def mark_failed(state: dict[str, Any], path: Path, key: str, reason: str, payload: Mapping[str, Any] | None = None) -> None:
    state.setdefault("failed", {})[key] = {
        "at": _now_iso(),
        "reason": reason,
        **(_redact(dict(payload or {}))),
    }
    state["status"] = "failed"
    save_event(state, path, "failed", stage=key, reason=reason, payload=payload or {})


def completed(state: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    value = (state.get("completed") or {}).get(key) if isinstance(state.get("completed"), Mapping) else None
    return value if isinstance(value, Mapping) else None


def checkpoint_path_for(cp_args: argparse.Namespace, network: str) -> Path:
    if cp_args.checkpoint_file:
        return Path(cp_args.checkpoint_file)
    return Path(cp_args.checkpoint_dir) / f"{network}.json"


def parse_checkpoint_args(argv: Sequence[str] | None = None) -> tuple[argparse.Namespace, argparse.Namespace]:
    parser = argparse.ArgumentParser(
        description="Checkpointed, fail-fast wrapper for tools/allfather_control.py hub-propagate",
        allow_abbrev=False,
        add_help=True,
    )
    parser.add_argument("--checkpoint-file", default="", help="Checkpoint JSON path. Default: .allfather-hub-propagate-checkpoints/<network>.json")
    parser.add_argument("--checkpoint-dir", default=str(DEFAULT_CHECKPOINT_DIR), help="Directory used when --checkpoint-file is omitted.")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Delete the checkpoint before running.")
    parser.add_argument(
        "--stop-after-stage",
        default="",
        help=(
            "Testing/debug only. Stop successfully after a stage key is completed, "
            "for example host:coolify-a:full-runtime or host:coolify-a:head-agent."
        ),
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore completed checkpoint stages for this run, but still write a checkpoint.",
    )
    cp_args, rest = parser.parse_known_args(argv)

    base_argv = list(rest)
    if not base_argv or base_argv[0] not in {"hub-propagate", "traefik-propagate"}:
        base_argv = ["hub-propagate", *base_argv]
    base_args = af.parse_args(base_argv)
    base_args.command = "hub-propagate"
    return cp_args, base_args


def _operator_log(args: argparse.Namespace, message: str) -> None:
    log_stdout("progress", message=message)
    af.operator_log(args, f"checkpointed: {message}")


def stage_host_prefix(host: str) -> str:
    return f"host:{host}"


def stage_node(host: str, service_name: str, suffix: str) -> str:
    return f"{stage_host_prefix(host)}:node:{service_name}:{suffix}"


def stop_after_if_requested(cp_args: argparse.Namespace, stage: str) -> None:
    if cp_args.stop_after_stage and cp_args.stop_after_stage == stage:
        raise SystemExit(0)


def assert_generated_super_compose_is_waitable(compose: str, *, service_name: str, deployment_id: str) -> None:
    """Catch known non-waitable generated compose failures before touching Coolify."""

    problems: list[str] = []
    if "printf '%s\n'" in compose:
        problems.append("generated Dockerfile contains a real newline inside printf '%s\\n' metadata writer")
    if "hub-full" not in compose:
        problems.append("generated compose is missing hub-full capability")
    if "hub-bootstrap-fallback" not in compose:
        problems.append("generated compose is missing hub-bootstrap-fallback capability")
    if "PYTHONPATH=/opt/main-computer-src" not in compose:
        problems.append("generated compose is missing PYTHONPATH=/opt/main-computer-src")
    if "MC_ALLFATHER_FULL_HUB_RUNTIME_REQUESTED" not in compose:
        problems.append("generated compose is missing MC_ALLFATHER_FULL_HUB_RUNTIME_REQUESTED")
    if deployment_id and deployment_id not in compose:
        problems.append("generated compose does not contain the expected deployment_id")
    if problems:
        raise FailFast(
            f"{service_name}: non-waitable generated full-hub compose problem(s): "
            + "; ".join(problems)
            + ". Apply the Dockerfile escaping/full-runtime generation fix before deploying."
        )


def assert_service_snapshot_is_waitable(snapshot: Mapping[str, Any], *, service_name: str, expected_deployment_id: str) -> None:
    """Fail before a long public-health wait when Coolify clearly lacks the desired compose."""

    compose = snapshot.get("compose") if isinstance(snapshot.get("compose"), Mapping) else {}
    summary = af.coolify_full_hub_runtime_snapshot_summary(snapshot)
    if not bool(compose.get("observed")):
        raise FailFast(f"{service_name}: Coolify service detail did not expose compose after sync; {summary}")
    if not bool(compose.get("any_contains_full_runtime")):
        raise FailFast(f"{service_name}: Coolify compose after sync does not contain full-runtime markers; {summary}")
    if expected_deployment_id and not bool(compose.get("any_contains_deployment_id")):
        raise FailFast(f"{service_name}: Coolify compose after sync does not contain deployment_id={expected_deployment_id}; {summary}")


def public_hub_is_full(node: Mapping[str, Any], domain: str) -> tuple[bool, dict[str, Any]]:
    probe = af.probe_public_hub_health(domain)
    service_name = str(node.get("service_name") or "").strip()
    ready = (
        bool(probe.get("ok"))
        and bool(probe.get("full_main_computer_hub"))
        and (not service_name or str(probe.get("cell_id") or "") == service_name)
    )
    return ready, probe


def build_admin_sync_request(
    args: argparse.Namespace,
    network: str,
    selected_heads: Sequence[Any],
    inventory: Mapping[str, Any],
    private_state: Mapping[str, Any],
    hub_admin_records: Sequence[Mapping[str, Any]],
    errors: list[dict[str, Any]],
) -> tuple[dict[str, Any], Any | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    enabled = not bool(getattr(args, "no_contract_admin_sync", False))
    contract_address = str(getattr(args, "hub_admin_contract_address", "") or "").strip() or af.hub_credit_bridge_escrow_address_for_network(network)
    result: dict[str, Any] = {
        "enabled": bool(enabled),
        "ok": None,
        "reason": "not requested" if not enabled else "not started",
        "contract_address": contract_address,
        "admin_count": len(hub_admin_records),
    }
    if not enabled:
        return result, None, None, None

    admin_sync_head = None
    admin_sync_executor: Mapping[str, Any] | None = None
    for head in selected_heads:
        local_nodes = list((inventory.get("local_nodes_by_host") or {}).get(head.coolify_server, []))
        if local_nodes:
            admin_sync_head = head
            admin_sync_executor = sorted(local_nodes, key=af.network_super_node_sort_key)[0]
            break

    missing = [
        str(item.get("cell_id") or item.get("service_name") or "")
        for item in hub_admin_records
        if not str(item.get("address") or "").strip()
    ]
    if missing:
        result.update(
            {
                "ok": False,
                "reason": "Hub admin address derivation failed; refusing contract sync with incomplete huddle records",
                "missing_hub_admin_addresses": missing,
            }
        )
        errors.append({"host": "huddle", "error": result["reason"]})
        return result, admin_sync_head, admin_sync_executor, None

    if getattr(args, "dry_run", False):
        result.update(
            {
                "ok": None,
                "dry_run": True,
                "reason": "dry-run; would sync Hub admin addresses to HubCreditBridgeEscrow",
                "executor_service_name": str((admin_sync_executor or {}).get("service_name") or ""),
            }
        )
        return result, admin_sync_head, admin_sync_executor, None

    network_wallets = af.network_wallets_from_private_state(private_state, network)
    owner_private_key = af.wallet_private_key(network_wallets, "deployer")
    if not contract_address:
        result.update({"ok": False, "reason": "hub_credit_bridge_escrow contract address not found"})
        errors.append({"host": "contract", "error": result["reason"]})
        return result, admin_sync_head, admin_sync_executor, None
    if not owner_private_key:
        result.update({"ok": False, "reason": "network deployer private key is missing; cannot authorize Hub admins on contract"})
        errors.append({"host": "contract", "error": result["reason"]})
        return result, admin_sync_head, admin_sync_executor, None
    if not admin_sync_head or not admin_sync_executor:
        result.update({"ok": False, "reason": "no selected host has a local super-node that can execute contract admin sync"})
        errors.append({"host": "contract", "error": result["reason"]})
        return result, admin_sync_head, admin_sync_executor, None

    request = af.hub_admin_sync_request_payload(
        network,
        admin_sync_head,
        admin_sync_executor,
        hub_admin_records,
        contract_address=contract_address,
        owner_private_key=owner_private_key,
    )
    if not request.get("admins"):
        result.update({"ok": False, "reason": "no Hub admin addresses are available for contract sync"})
        errors.append({"host": "contract", "error": result["reason"]})
        return result, admin_sync_head, admin_sync_executor, None

    return result, admin_sync_head, admin_sync_executor, request


def sync_full_runtime_for_host_checkpointed(
    *,
    plan: Any,
    network: str,
    head: Any,
    local_nodes: Sequence[Mapping[str, Any]],
    all_nodes: Sequence[Mapping[str, Any]],
    private_state: Mapping[str, Any],
    domain_suffix: str,
    client: Any,
    context: Mapping[str, Any],
    args: argparse.Namespace,
    cp_args: argparse.Namespace,
    state: dict[str, Any],
    state_path: Path,
    tried: list[dict[str, Any]],
) -> dict[str, Any]:
    host = str(head.coolify_server)
    host_stage = f"{stage_host_prefix(host)}:full-runtime"
    result: dict[str, Any] = {
        "enabled": not bool(getattr(args, "no_full_hub_runtime_sync", False)),
        "ok": True,
        "reason": "all local hubs already full or synced",
        "nodes": [],
    }
    if not result["enabled"]:
        result.update({"ok": None, "reason": "disabled by --no-full-hub-runtime-sync"})
        mark_completed(state, state_path, host_stage, {"reason": result["reason"]})
        return result
    if not local_nodes:
        result.update({"ok": True, "reason": "no local super-nodes on this host"})
        mark_completed(state, state_path, host_stage, {"reason": result["reason"]})
        return result

    wait_s = float(getattr(args, "full_hub_runtime_wait_s", None) or getattr(args, "hub_propagate_wait_s", af.DEFAULT_TRAEFIK_PROPAGATE_WAIT_S) or 0.0)
    super_context: dict[str, Any] | None = None

    for node in sorted(local_nodes, key=af.network_super_node_sort_key):
        service_name = str(node.get("service_name") or "").strip()
        if not service_name:
            raise FailFast(f"{host}: inventory node is missing service_name")
        domain = af.hub_public_health_domain_for_node(network, node, domain_suffix=domain_suffix)
        ready_stage = stage_node(host, service_name, "full-ready")
        sync_stage = stage_node(host, service_name, "service-synced")
        deploy_stage = stage_node(host, service_name, "deploy-triggered")

        node_result: dict[str, Any] = {
            "service_name": service_name,
            "domain": domain,
        }

        # Checkpoint completion is trusted only if public health still confirms it.
        if completed(state, ready_stage) and not cp_args.no_resume:
            ready, probe = public_hub_is_full(node, domain)
            node_result["resume_probe"] = {key: value for key, value in probe.items() if key != "payload"}
            if ready:
                _operator_log(args, f"{host}/{service_name}: checkpoint says full-ready and public health still confirms it; skipping")
                node_result.update({"ok": True, "already_full": True, "resumed": True, "reason": "checkpointed full hub still healthy"})
                result["nodes"].append(node_result)
                continue
            _operator_log(args, f"{host}/{service_name}: checkpoint full-ready no longer validates; retrying from current node")

        _operator_log(args, f"{host}/{service_name}: checking public hub health")
        before_ready, before = public_hub_is_full(node, domain)
        node_result["health_before"] = {key: value for key, value in before.items() if key != "payload"}
        if before_ready:
            node_result.update({"ok": True, "already_full": True, "reason": "full Main Computer hub already observed"})
            mark_completed(state, state_path, ready_stage, node_result)
            result["nodes"].append(node_result)
            continue

        if getattr(args, "dry_run", False):
            node_result.update({"ok": None, "dry_run": True, "reason": "dry-run; would sync full hub runtime"})
            result["nodes"].append(node_result)
            continue

        previous_sync = completed(state, sync_stage) if not cp_args.no_resume else None
        service_uuid = str((previous_sync or {}).get("service_uuid") or "")
        application_uuid = str((previous_sync or {}).get("application_uuid") or "")
        expected_deployment_id = str((previous_sync or {}).get("deployment_id") or "")

        if not service_uuid:
            _operator_log(args, f"{host}/{service_name}: building full-runtime manifest")
            manifest, wallets = af.manifest_for_existing_super_node_runtime_sync(
                network,
                head,
                node,
                all_nodes=all_nodes,
                private_state=private_state,
            )
            expected_deployment_id = str(manifest.get("deployment_id") or "")
            compose = af.render_super_node_compose(
                manifest,
                image=getattr(args, "super_image", af.DEFAULT_SUPER_IMAGE),
                hub_admin_private_key=af.wallet_private_key(wallets, "hub_admin"),
                deployer_private_key=af.wallet_private_key(wallets, "deployer"),
                publish_routes=bool(getattr(args, "publish_routes", False)),
            )
            assert_generated_super_compose_is_waitable(
                compose,
                service_name=service_name,
                deployment_id=expected_deployment_id,
            )
            if super_context is None:
                super_context = af.resolve_super_context(client, args, head, tried)
            _operator_log(args, f"{host}/{service_name}: syncing Coolify super-node service")
            service_uuid, action, _existing = af.sync_super_node_service(
                client,
                manifest,
                args,
                super_context,
                tried,
                hub_admin_private_key=af.wallet_private_key(wallets, "hub_admin"),
                deployer_private_key=af.wallet_private_key(wallets, "deployer"),
            )
            snapshot = af.coolify_full_hub_runtime_snapshot(
                client,
                service_uuid=service_uuid,
                tried=tried,
                expected_deployment_id=expected_deployment_id,
            )
            assert_service_snapshot_is_waitable(snapshot, service_name=service_name, expected_deployment_id=expected_deployment_id)
            application_uuid = str(snapshot.get("application_uuid") or "")
            sync_payload = {
                "service_uuid": service_uuid,
                "service_action": action,
                "application_uuid": application_uuid,
                "deployment_id": expected_deployment_id,
                "coolify_summary": af.coolify_full_hub_runtime_snapshot_summary(snapshot),
            }
            mark_completed(state, state_path, sync_stage, sync_payload)
            node_result.update(sync_payload)
        else:
            _operator_log(args, f"{host}/{service_name}: resuming after service sync uuid={service_uuid} deployment_id={expected_deployment_id}")
            node_result.update(
                {
                    "service_uuid": service_uuid,
                    "application_uuid": application_uuid,
                    "deployment_id": expected_deployment_id,
                    "resumed_after_service_sync": True,
                }
            )

        if getattr(args, "no_deploy", False):
            node_result.update({"ok": None, "deployed": False, "reason": "deployment disabled by --no-deploy"})
            result["nodes"].append(node_result)
            continue

        if not completed(state, deploy_stage) or cp_args.no_resume:
            _operator_log(args, f"{host}/{service_name}: triggering Coolify deploy")
            af.hub_service_tool().trigger_deploy_service(client, service_uuid=service_uuid, force=True, tried=tried)
            mark_completed(state, state_path, deploy_stage, {"service_uuid": service_uuid, "deployment_id": expected_deployment_id})
        else:
            _operator_log(args, f"{host}/{service_name}: resuming after deploy trigger")

        _operator_log(args, f"{host}/{service_name}: waiting for public full hub health")
        wait = af.wait_for_public_full_hub(
            node,
            domain,
            wait_s=wait_s,
            poll_s=5.0,
            args=args,
            client=client,
            tried=tried,
            service_uuid=service_uuid,
            application_uuid=application_uuid,
            expected_deployment_id=expected_deployment_id,
        )
        node_result["wait"] = wait
        if not bool(wait.get("ready")):
            reason = str(wait.get("reason") or "full hub runtime did not become ready")
            # This is exactly the failure that kept consuming time in the normal path.
            if "running/healthy with full-runtime compose" in reason or "stale" in reason.lower():
                mark_failed(state, state_path, ready_stage, reason, node_result)
                raise FailFast(f"{host}/{service_name}: {reason}")
            mark_failed(state, state_path, ready_stage, reason, node_result)
            raise FailFast(f"{host}/{service_name}: full hub wait failed: {reason}")

        node_result.update({"ok": True, "reason": "full Main Computer hub health observed"})
        mark_completed(state, state_path, ready_stage, node_result)
        result["nodes"].append(node_result)

    failed = [item for item in result["nodes"] if item.get("ok") is False]
    pending = [item for item in result["nodes"] if item.get("ok") is None]
    if failed:
        result.update({"ok": False, "reason": "; ".join(str(item.get("reason") or item.get("service_name")) for item in failed)})
    elif pending:
        result.update({"ok": None, "reason": "full hub runtime sync was planned but not deployed"})
    else:
        result.update({"ok": True, "reason": "full Main Computer hub runtime synced or already healthy"})
        mark_completed(state, state_path, host_stage, {"node_count": len(local_nodes), "reason": result["reason"]})
        stop_after_if_requested(cp_args, host_stage)
    return result


def checkpointed_hub_propagate(plan: Any, args: argparse.Namespace, cp_args: argparse.Namespace, state: dict[str, Any], state_path: Path) -> dict[str, Any]:
    network = af.clean_node_network_key(args.network)
    if network == "mainnet" and not getattr(args, "allow_mainnet", False):
        raise af.AllfatherControlError("Refusing to publish, rewrite, or authorize mainnet Hub routes/admins without --allow-mainnet.")

    domain_suffix = af.public_domain_suffix(args)
    selected_heads = af.heads_selected_for_traefik(plan, getattr(args, "host", []) or None)
    tried_by_host: dict[str, list[dict[str, Any]]] = {}

    save_event(state, state_path, "start", network=network, selected_hosts=[head.coolify_server for head in selected_heads])

    _operator_log(args, "collecting live super-node inventory")
    inventory = af.collect_network_super_inventory_for_traefik(plan, network, args, tried_by_host)
    all_nodes = list(inventory.get("nodes") or [])
    errors: list[dict[str, Any]] = list(inventory.get("errors") or [])
    mark_completed(
        state,
        state_path,
        "inventory",
        {
            "checked_hosts": inventory.get("checked_hosts"),
            "super_node_count": len(all_nodes),
            "inventory_errors": inventory.get("errors") or [],
        },
    )

    private_state_path = af.repo_relative_path(args.private_state)
    private_state = af.load_yaml_mapping(private_state_path)
    private_state, hub_admin_records, huddle_updates = af.materialize_private_state_for_hub_propagate(
        private_state,
        private_state_path,
        network,
        all_nodes,
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    mark_completed(
        state,
        state_path,
        "huddle",
        {
            "admin_count": len(hub_admin_records),
            "private_state_updates": huddle_updates,
        },
    )

    admin_sync_result, admin_sync_head, _admin_sync_executor, admin_sync_request = build_admin_sync_request(
        args,
        network,
        selected_heads,
        inventory,
        private_state,
        hub_admin_records,
        errors,
    )
    if admin_sync_result.get("ok") is False:
        raise FailFast(str(admin_sync_result.get("reason") or "admin sync precondition failed"))

    propagations: list[dict[str, Any]] = []

    for head in selected_heads:
        host = str(head.coolify_server)
        tried = tried_by_host.setdefault(host, [])
        local_nodes = list((inventory.get("local_nodes_by_host") or {}).get(host, []))
        request_payload = af.traefik_propagate_request_payload(network, head, local_nodes, domain_suffix=domain_suffix)
        head_admin_sync_request = admin_sync_request if (admin_sync_head and host == str(admin_sync_head.coolify_server)) else None
        host_stage = f"{stage_host_prefix(host)}:head-agent"

        entry: dict[str, Any] = {
            "host": host,
            "host_slot": head.slot,
            "local_super_node_count": len(local_nodes),
            "service_name": head.service_name,
            "aggregate_domain": af.public_network_hub_domain(network, domain_suffix),
            "propagation_transport": "checkpointed-allfather-head-host-agent",
        }
        propagations.append(entry)

        if not local_nodes:
            entry.update({"ok": True, "skipped": True, "reason": "no local super-nodes for this network on this Coolify host"})
            mark_completed(state, state_path, host_stage, {"skipped": True, "reason": entry["reason"]})
            continue

        if getattr(args, "dry_run", False):
            entry.update({"ok": True, "dry_run": True, "reason": "dry-run; would sync full hubs and update head host-agent"})
            mark_completed(state, state_path, host_stage, {"dry_run": True, "reason": entry["reason"]})
            continue

        if completed(state, host_stage) and not cp_args.no_resume:
            _operator_log(args, f"{host}: checkpoint says head-agent stage completed; skipping")
            entry.update({"ok": True, "resumed": True, "reason": "checkpointed head-agent handoff already completed"})
            continue

        _operator_log(args, f"{host}: resolving Coolify client")
        client, token_source = af.fdb_tool().client_for_server(host, args)
        entry["token_source"] = token_source

        _operator_log(args, f"{host}: checking Coolify API version")
        version = af.request_coolify_version(
            client,
            tried,
            args=args,
            host=host,
            operation="checkpointed-hub-propagate",
        )
        if not version.ok:
            raise FailFast(f"{host}: Coolify API version check failed with HTTP {version.status}: {version.body}")

        _operator_log(args, f"{host}: resolving head service context")
        context = af.resolve_context(client, args, head, tried)

        _operator_log(args, f"{host}: syncing full hub runtime before Traefik/admin handoff")
        full_sync = sync_full_runtime_for_host_checkpointed(
            plan=plan,
            network=network,
            head=head,
            local_nodes=local_nodes,
            all_nodes=all_nodes,
            private_state=private_state,
            domain_suffix=domain_suffix,
            client=client,
            context=context,
            args=args,
            cp_args=cp_args,
            state=state,
            state_path=state_path,
            tried=tried,
        )
        entry["full_hub_runtime_sync"] = full_sync
        if full_sync.get("ok") is False:
            raise FailFast(f"{host}: full hub runtime sync failed: {full_sync.get('reason') or 'unknown'}")

        _operator_log(args, f"{host}: syncing head host-agent service")
        service_uuid, action, _existing = af.sync_head_service(
            client,
            plan,
            head,
            args,
            context,
            tried,
            probe_targets=af.probe_target_records_for_plan(plan, super_inventory=inventory.get("nodes") or []),
            traefik_propagate_request=request_payload,
            hub_admin_sync_request=head_admin_sync_request,
        )
        entry["service_uuid"] = service_uuid
        entry["service_action"] = f"head-agent-{action}"

        if not getattr(args, "no_deploy", False):
            _operator_log(args, f"{host}: triggering head host-agent deploy")
            af.hub_service_tool().trigger_deploy_service(client, service_uuid=service_uuid, force=True, tried=tried)
            entry["deployed"] = True

            wait_s = float(getattr(args, "hub_propagate_wait_s", getattr(args, "traefik_propagate_wait_s", af.DEFAULT_TRAEFIK_PROPAGATE_WAIT_S)) or 0.0)
            _operator_log(args, f"{host}: waiting for Traefik propagation marker request_id={request_payload.get('request_id') or '<unknown>'}")
            wait_result = af.wait_for_head_traefik_propagate_ready(
                client,
                service_uuid,
                tried,
                wait_s=wait_s,
                poll_s=2.0,
                expected_request_id=str(request_payload.get("request_id") or ""),
                args=args,
            )
            payload = wait_result.get("result") if isinstance(wait_result.get("result"), Mapping) else {}
            entry["propagator_result"] = {
                "ok": bool(wait_result.get("ready")),
                "observed": bool(wait_result.get("observed")),
                "source": wait_result.get("source"),
                "reason": wait_result.get("reason"),
                "result": dict(payload),
            }
            if not bool(wait_result.get("ready")):
                reason = str(wait_result.get("reason") or "Traefik propagation did not report ready")
                mark_failed(state, state_path, host_stage, reason, entry)
                raise FailFast(f"{host}: {reason}")

            if head_admin_sync_request:
                _operator_log(args, f"{host}: waiting for Hub admin contract sync marker request_id={head_admin_sync_request.get('request_id') or '<unknown>'}")
                admin_wait = af.wait_for_head_hub_admin_sync_ready(
                    client,
                    service_uuid,
                    tried,
                    wait_s=float(getattr(args, "hub_admin_contract_sync_wait_s", af.DEFAULT_HUB_ADMIN_CONTRACT_SYNC_WAIT_S) or wait_s),
                    poll_s=2.0,
                    expected_request_id=str(head_admin_sync_request.get("request_id") or ""),
                    args=args,
                )
                admin_payload = admin_wait.get("result") if isinstance(admin_wait.get("result"), Mapping) else {}
                entry["hub_admin_contract_sync"] = {
                    "ok": bool(admin_wait.get("ready")),
                    "observed": bool(admin_wait.get("observed")),
                    "source": admin_wait.get("source"),
                    "reason": admin_wait.get("reason"),
                    "result": dict(admin_payload),
                }
                admin_sync_result = {
                    "enabled": True,
                    "ok": bool(admin_wait.get("ready")),
                    "observed": bool(admin_wait.get("observed")),
                    "source": admin_wait.get("source"),
                    "reason": admin_wait.get("reason"),
                    "result": dict(admin_payload),
                }
                if not bool(admin_wait.get("ready")):
                    reason = str(admin_wait.get("reason") or "Hub admin sync did not report ready")
                    mark_failed(state, state_path, host_stage, reason, entry)
                    raise FailFast(f"{host}: {reason}")

                address_updates = af.apply_hub_admin_sync_result_to_private_state(
                    private_state,
                    private_state_path,
                    network,
                    admin_payload,
                    dry_run=False,
                )
                entry["hub_admin_contract_sync"]["private_state_updates"] = address_updates
                admin_sync_result["private_state_updates"] = address_updates
        else:
            entry["deployed"] = False
            entry["propagator_result"] = {"ok": None, "reason": "deployment disabled by --no-deploy"}

        entry["ok"] = True
        mark_completed(state, state_path, host_stage, entry)
        stop_after_if_requested(cp_args, host_stage)

    ok = not errors and all(bool(item.get("ok")) for item in propagations)
    payload = {
        "ok": ok,
        "operation": "checkpointed-hub-propagate",
        "network": network,
        "domain_suffix": domain_suffix,
        "checkpoint_file": str(state_path),
        "selected_hosts": [head.coolify_server for head in selected_heads],
        "inventory": {
            "checked_hosts": inventory.get("checked_hosts"),
            "super_node_count": len(all_nodes),
            "errors": inventory.get("errors"),
        },
        "huddle": {
            "private_state_updates": huddle_updates,
            "admin_count": len(hub_admin_records),
            "admin_records": [
                {key: value for key, value in item.items() if key != "private_key"}
                for item in hub_admin_records
            ],
        },
        "hub_admin_contract_sync": admin_sync_result,
        "propagations": propagations,
        "errors": errors,
    }
    state["status"] = "complete" if ok else "failed"
    save_event(state, state_path, "finish", ok=ok, errors=errors)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    cp_args, args = parse_checkpoint_args(argv)
    network = af.clean_node_network_key(args.network)
    state_path = checkpoint_path_for(cp_args, network)
    try:
        log_stdout(
            "start",
            network=network,
            checkpoint_file=str(state_path),
            resume=not bool(cp_args.no_resume),
            reset_checkpoint=bool(cp_args.reset_checkpoint),
        )
        state = load_checkpoint(state_path, network=network, reset=bool(cp_args.reset_checkpoint))
        log_stdout(
            "checkpoint-loaded",
            checkpoint_file=str(state_path),
            status=state.get("status"),
            completed_stage_count=len(state.get("completed") or {}),
            failed_stage_count=len(state.get("failed") or {}),
        )
        plan = af.build_plan_from_args(args)
        payload = checkpointed_hub_propagate(plan, args, cp_args, state, state_path)
        log_stdout("finish", network=network, checkpoint_file=str(state_path), ok=bool(payload.get("ok", True)))
        if getattr(args, "json", False) or getattr(args, "verbose", False) or getattr(args, "dry_run", False):
            print_json(payload)
        else:
            # compact_hub_propagate_for_operator expects the normal operation name/shape.
            compact_input = copy.deepcopy(payload)
            compact_input["operation"] = "hub-propagate"
            print_json(af.compact_hub_propagate_for_operator(compact_input))
        return 0 if bool(payload.get("ok", True)) else 1
    except FailFast as exc:
        log_stdout("fail-fast", network=network, checkpoint_file=str(state_path), error=str(exc))
        state = load_checkpoint(state_path, network=network, reset=False)
        mark_failed(state, state_path, "fail-fast", str(exc), {"checkpoint_file": str(state_path)})
        print_json(
            {
                "ok": False,
                "operation": "checkpointed-hub-propagate",
                "network": network,
                "checkpoint_file": str(state_path),
                "error": str(exc),
                "resume": f"Fix the condition above, then rerun this same command. Completed checkpoint stages will be skipped unless --no-resume is passed.",
            }
        )
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        log_stdout("exception", network=network, checkpoint_file=str(state_path), error=f"{type(exc).__name__}: {exc}")
        try:
            state = load_checkpoint(state_path, network=network, reset=False)
            mark_failed(state, state_path, "exception", f"{type(exc).__name__}: {exc}", {"checkpoint_file": str(state_path)})
        except Exception:
            pass
        print_json(
            {
                "ok": False,
                "operation": "checkpointed-hub-propagate",
                "network": network,
                "checkpoint_file": str(state_path),
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
