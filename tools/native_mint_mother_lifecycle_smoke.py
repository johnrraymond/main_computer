#!/usr/bin/env python3
"""API-only native-mint lifecycle smoke built on Mother Coolify primitives.

This smoke exists to diagnose the boundary between native-mint helper creation
and Coolify materialization without SSH or host-side Docker access.

It performs three bounded, probe-only checks:

1. Run Mother's existing no-secret/no-chain Coolify service lifecycle probe on
   the selected controller.  This establishes whether the controller can
   materialize an ordinary long-running Compose service at all.
2. Create the exact native-mint MODE=probe helper, start it through the same
   /services/<uuid>/start route used by native_mint_control.py, and observe it
   through Mother's service/deployment/server-resource inventory channels.
   Initial ``exited`` is treated as PRE-MATERIALIZATION, never as terminal.
3. If the exact helper still has no proof marker, issue Mother's established
   forced deployment path, POST /api/v1/deploy {uuid, force:true}, against the
   SAME service row and observe again.

The native-mint helper mounts genesis read-only and cannot write it.  The exact
helper service is preserved by default so later API/UI inspection remains
possible.  Pass --cleanup to delete it at the end.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping
import urllib.parse

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import native_mint_control as nm
from tools.mother.common import deployment_coolify_service_lifecycle_probe as mother_probe
from tools.mother.common.coolify_state import _DEFAULT_OPENER as MOTHER_DEFAULT_OPENER


ACK = "NO_SECRET_NO_CHAIN_ONE_TEMPORARY_SERVICE"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
        return
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _safe_payload_fields(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    return {
        key: payload.get(key)
        for key in ("message", "uuid", "service_uuid", "deployment_uuid")
        if key in payload
    }


def _resolve_parent_and_sha(
    runtime_state_root: Path,
    *,
    network: str,
    controller_id: str,
    node: str | None,
) -> tuple[str, str, str, Path]:
    baseline_path, baseline = nm._latest_baseline(runtime_state_root)
    topology = nm._extract_topology(baseline)
    _chain_id, genesis_sha = nm._chain_identity(baseline, topology)
    services = topology.get("services")
    nodes = topology.get("nodes")
    if not isinstance(services, Mapping) or not isinstance(nodes, list):
        raise RuntimeError("accepted topology has no usable services/nodes")

    candidates: list[tuple[str, str]] = []
    for raw_node in nodes:
        if not isinstance(raw_node, str):
            continue
        service = services.get(raw_node)
        if not isinstance(service, Mapping):
            continue
        service_controller = service.get("controller_id")
        service_uuid = service.get("service_uuid") or service.get("created_service_uuid")
        if service_controller == controller_id and isinstance(service_uuid, str):
            candidates.append((raw_node, service_uuid))

    if node:
        candidates = [item for item in candidates if item[0] == node]
    if not candidates:
        raise RuntimeError(
            f"accepted topology has no service for controller={controller_id!r}"
            + (f" node={node!r}" if node else "")
        )
    candidates.sort()
    chosen_node, parent_uuid = candidates[0]
    return (
        chosen_node,
        nm._identifier(parent_uuid, "parent service uuid"),
        genesis_sha,
        baseline_path,
    )


def _mother_request(
    controller: Any,
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    return mother_probe._http(
        controller,
        method,
        endpoint,
        body=body,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        opener=MOTHER_DEFAULT_OPENER,
    )


def _inventory_snapshot(
    *,
    controller: Any,
    controller_meta: Mapping[str, Any],
    service_uuid: str,
    service_name: str,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    service_q = urllib.parse.quote(service_uuid, safe="")
    endpoints = (
        ("service-detail", f"/api/v1/services/{service_q}"),
        ("deployment-list", "/api/v1/deployments"),
        ("server-resources", f"/api/v1/servers/{controller_meta['server_uuid']}/resources"),
    )
    channels: list[dict[str, Any]] = []
    detail_payload: Any = None
    for channel, endpoint in endpoints:
        response = _mother_request(
            controller,
            "GET",
            endpoint,
            body=None,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        if channel == "service-detail" and response.get("ok") is True:
            detail_payload = response.get("payload")
        matches = mother_probe._matching_records(
            response.get("payload"),
            service_uuid,
            service_name,
        )
        channels.append(
            {
                "channel": channel,
                "endpoint": endpoint,
                "http_status": response.get("status"),
                "elapsed_ms": response.get("elapsed_ms"),
                "matches": matches,
            }
        )

    app_uuid, app_name, app_status = nm._helper_application(detail_payload, service_name)
    root_status = detail_payload.get("status") if isinstance(detail_payload, Mapping) else None
    subresource = mother_probe._service_subresource_diagnostics(detail_payload, service_name)

    deployment_matches = next(
        (item["matches"] for item in channels if item["channel"] == "deployment-list"),
        [],
    )
    server_matches = next(
        (item["matches"] for item in channels if item["channel"] == "server-resources"),
        [],
    )
    deployment_statuses = sorted(
        {
            str(item.get("status"))
            for item in deployment_matches
            if isinstance(item, Mapping) and item.get("status") is not None
        }
    )
    server_statuses = sorted(
        {
            str(item.get("status"))
            for item in server_matches
            if isinstance(item, Mapping) and item.get("status") is not None
        }
    )

    return {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "service_status": root_status,
        "application_uuid": app_uuid,
        "application_name": app_name,
        "application_status": app_status,
        "deployment_statuses": deployment_statuses,
        "server_resource_statuses": server_statuses,
        "subresource_diagnostics": subresource,
        "channels": channels,
    }


def _observe_exact_helper(
    *,
    controller: Any,
    controller_meta: Mapping[str, Any],
    service_uuid: str,
    service_name: str,
    expected_sha: str,
    phase: str,
    output_dir: Path,
    observe_seconds: float,
    poll_interval_seconds: float,
    log_poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + observe_seconds
    next_log_probe = started
    timeline: list[dict[str, Any]] = []
    marker: dict[str, Any] | None = None
    last_print_key: tuple[Any, ...] | None = None
    sequence = 0

    while True:
        sequence += 1
        snap = _inventory_snapshot(
            controller=controller,
            controller_meta=controller_meta,
            service_uuid=service_uuid,
            service_name=service_name,
            timeout=timeout,
            max_response_bytes=max_response_bytes,
        )
        elapsed = time.monotonic() - started
        snap["sequence"] = sequence
        snap["elapsed_seconds"] = round(elapsed, 3)
        snap["phase"] = phase

        app_status = str(snap.get("application_status") or "")
        root_status = str(snap.get("service_status") or "")
        # Do not probe dead-container log endpoints during queue/materialization.
        # Once Coolify reports a running state, use native-mint's exact marker parser.
        running_seen = root_status.startswith("running") or app_status.startswith("running")
        if running_seen and time.monotonic() >= next_log_probe:
            found, attempts, log_text = nm._helper_log_probe(
                controller,
                helper_uuid=service_uuid,
                application_uuid=snap.get("application_uuid") if isinstance(snap.get("application_uuid"), str) else None,
                application_name=snap.get("application_name") if isinstance(snap.get("application_name"), str) else None,
                timeout=timeout,
            )
            snap["log_attempts"] = attempts
            snap["log_tail"] = log_text[-1200:] if log_text else ""
            if found is not None:
                snap["marker"] = found
                marker = found
            next_log_probe = time.monotonic() + log_poll_interval_seconds

        timeline.append(snap)
        _dump(output_dir / phase / "timeline" / f"{sequence:04d}.json", snap)

        print_key = (
            snap.get("service_status"),
            snap.get("application_status"),
            tuple(snap.get("deployment_statuses") or []),
            tuple(snap.get("server_resource_statuses") or []),
            marker is not None,
        )
        if print_key != last_print_key or sequence == 1:
            print(
                f"{phase} T+{elapsed:06.1f}s "
                f"service={snap.get('service_status')!r} "
                f"app={snap.get('application_status')!r} "
                f"deployments={snap.get('deployment_statuses')} "
                f"server_resources={snap.get('server_resource_statuses')} "
                f"marker={'YES' if marker else 'no'}",
                flush=True,
            )
            last_print_key = print_key

        if marker is not None:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_interval_seconds)

    result = {
        "phase": phase,
        "marker": marker,
        "marker_ok": marker.get("ok") if isinstance(marker, Mapping) else None,
        "timeline": timeline,
        "final": timeline[-1] if timeline else None,
    }
    _dump(output_dir / phase / "result.json", result)
    return result


def _generic_mother_baseline(
    *,
    paths: Any,
    private: Any,
    network: str,
    controller_id: str,
    observe_seconds: float,
    poll_interval_seconds: float,
    timeout: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    operation = nm._operation_identity(
        "inspect",
        network,
        f"native-mint-mother-lifecycle-baseline-{_stamp()}",
    )
    print("\n=== MOTHER GENERIC LIFECYCLE BASELINE ===", flush=True)
    result = mother_probe.execute_coolify_service_lifecycle_probe(
        paths,
        private,
        network=network,
        controller_id=controller_id,
        environment_name=network,
        acknowledged_probe=ACK,
        observe_seconds=observe_seconds,
        poll_interval_seconds=poll_interval_seconds,
        timeout=timeout,
        max_response_bytes=max_response_bytes,
        operation=operation,
    )
    summary = result.get("summary") if isinstance(result, Mapping) else {}
    print(
        "MOTHER_BASELINE "
        f"status={result.get('status')!r} "
        f"healthy={summary.get('healthy_running_observed')!r} "
        f"deployment_record={summary.get('deployment_record_observed')!r} "
        f"runtime_channel={summary.get('runtime_result_channel')!r}",
        flush=True,
    )
    return dict(result)


def _diagnosis(
    baseline: Mapping[str, Any] | None,
    start_result: Mapping[str, Any],
    forced_result: Mapping[str, Any] | None,
) -> str:
    baseline_pass = isinstance(baseline, Mapping) and baseline.get("status") == "pass"
    start_marker = start_result.get("marker") if isinstance(start_result, Mapping) else None
    forced_marker = forced_result.get("marker") if isinstance(forced_result, Mapping) else None

    if isinstance(start_marker, Mapping) and start_marker.get("ok") is True:
        return (
            "exact native-mint helper succeeds when initial exited is treated as pre-materialization; "
            "native_mint_control._run_helper terminal-state logic is aborting too early"
        )
    if isinstance(forced_marker, Mapping) and forced_marker.get("ok") is True:
        return (
            "service-level /start did not materialize the exact helper, but Mother forced deploy did; "
            "native-mint should use/track the deploy materialization path rather than treating the queued start as execution"
        )
    if baseline_pass:
        return (
            "generic Mother Coolify lifecycle succeeds but the exact native-mint helper does not materialize through either start/deploy path; "
            "inspect the preserved exact-helper service and timeline for helper-specific Coolify treatment"
        )
    return (
        "generic Mother Coolify lifecycle also failed on this controller; the fault is broader than native-mint helper logic"
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--network", default="mainnet")
    p.add_argument("--controller-id", default="coolify-c", choices=("coolify-a", "coolify-c"))
    p.add_argument("--node", help="accepted topology node used as native-mint read-only genesis source")
    p.add_argument("--runtime-state-root", default=str(REPO_ROOT / "runtime" / "state"))
    p.add_argument("--baseline-observe-seconds", type=float, default=60.0)
    p.add_argument("--start-observe-seconds", type=float, default=90.0)
    p.add_argument("--forced-observe-seconds", type=float, default=120.0)
    p.add_argument("--poll-interval-seconds", type=float, default=2.0)
    p.add_argument("--log-poll-interval-seconds", type=float, default=2.0)
    p.add_argument("--http-timeout", type=float, default=10.0)
    p.add_argument("--max-response-bytes", type=int, default=1048576)
    p.add_argument("--skip-mother-baseline", action="store_true")
    p.add_argument("--no-forced-deploy-on-miss", action="store_true")
    p.add_argument("--cleanup", action="store_true", help="delete the exact native-mint helper service after the smoke; default preserves it")
    p.add_argument("--output-dir")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if min(
        args.baseline_observe_seconds,
        args.start_observe_seconds,
        args.forced_observe_seconds,
        args.poll_interval_seconds,
        args.log_poll_interval_seconds,
        args.http_timeout,
    ) <= 0:
        print("ERROR timing values must all be > 0", file=sys.stderr)
        return 2

    runtime_root = Path(args.runtime_state_root).resolve()
    operation = nm._operation_identity(
        "inspect",
        args.network,
        f"native-mint-mother-lifecycle-smoke-{_stamp()}",
    )
    paths, private, _private_doc = nm._load_private(runtime_root, operation)
    controller = nm.resolve_coolify_controller(
        private,
        args.network,
        args.controller_id,
        require_enabled=True,
        require_token=True,
    )
    controller_meta = mother_probe._controller_config(
        private,
        network=args.network,
        controller_id=args.controller_id,
    )
    chosen_node, parent_uuid, expected_sha, baseline_path = _resolve_parent_and_sha(
        runtime_root,
        network=args.network,
        controller_id=args.controller_id,
        node=args.node,
    )

    run_name = f"{_stamp()}-{args.controller_id}-{parent_uuid[:8]}"
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else REPO_ROOT / "runtime" / "native-mint-mother-lifecycle-smoke" / run_name
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    print("NATIVE_MINT_MOTHER_LIFECYCLE_SMOKE", flush=True)
    print(f"network={args.network}", flush=True)
    print(f"controller={args.controller_id} base_url={getattr(controller, 'base_url', '<unknown>')}", flush=True)
    print(f"node={chosen_node} parent_service={parent_uuid}", flush=True)
    print(f"expected_genesis_sha256={expected_sha}", flush=True)
    print(f"baseline={baseline_path}", flush=True)
    print(f"output={output_dir}", flush=True)
    print("transport=COOLIFY API ONLY; SSH=NEVER", flush=True)
    print("native-mint mode=PROBE ONLY; genesis mount is read-only", flush=True)

    baseline_result: dict[str, Any] | None = None
    if not args.skip_mother_baseline:
        baseline_result = _generic_mother_baseline(
            paths=paths,
            private=private,
            network=args.network,
            controller_id=args.controller_id,
            observe_seconds=args.baseline_observe_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout=args.http_timeout,
            max_response_bytes=args.max_response_bytes,
        )
        _dump(output_dir / "mother-baseline.json", baseline_result)

    helper_name = f"mother-native-mint-mother-smoke-{parent_uuid[:8]}-{time.time_ns() % 1_000_000:06d}"
    compose = nm._helper_compose(
        helper_name=helper_name,
        parent_service_uuid=parent_uuid,
        mode="probe",
        expected_old_sha=expected_sha,
        new_sha="",
        new_genesis_b64="",
    )
    _dump(output_dir / "submitted-native-mint-compose.yml", compose)

    create_body = nm._helper_body(
        nm._controller_config(private, args.network, args.controller_id),
        helper_name=helper_name,
        compose=compose,
        network=args.network,
    )
    print("\n=== EXACT NATIVE-MINT HELPER ===", flush=True)
    create = _mother_request(
        controller,
        "POST",
        "/api/v1/services",
        body=create_body,
        timeout=args.http_timeout,
        max_response_bytes=args.max_response_bytes,
    )
    _dump(
        output_dir / "create-receipt.json",
        {
            "http_status": create.get("status"),
            "ok": create.get("ok"),
            "elapsed_ms": create.get("elapsed_ms"),
            "response_fields": _safe_payload_fields(create.get("payload")),
        },
    )
    if create.get("ok") is not True:
        print(f"CREATE FAILED http={create.get('status')} fields={_safe_payload_fields(create.get('payload'))}", flush=True)
        return 1

    service_uuid = mother_probe._one_uuid(create.get("payload"))
    service_q = urllib.parse.quote(service_uuid, safe="")
    print(f"helper={helper_name}", flush=True)
    print(f"service_uuid={service_uuid}", flush=True)

    start_result: dict[str, Any] = {}
    forced_result: dict[str, Any] | None = None
    try:
        start_endpoint = f"/api/v1/services/{service_q}/start"
        start = _mother_request(
            controller,
            "POST",
            start_endpoint,
            body=None,
            timeout=args.http_timeout,
            max_response_bytes=args.max_response_bytes,
        )
        start_receipt = mother_probe._mutation_receipt(
            mutation_id=f"{helper_name}.start",
            controller_id=args.controller_id,
            method="POST",
            endpoint=start_endpoint,
            response=start,
            service_uuid=service_uuid,
        )
        _dump(output_dir / "start-receipt.json", start_receipt)
        print(
            f"START http={start.get('status')} fields={_safe_payload_fields(start.get('payload'))}",
            flush=True,
        )
        if start.get("ok") is not True:
            raise RuntimeError(f"Coolify rejected service start HTTP {start.get('status')}")

        start_result = _observe_exact_helper(
            controller=controller,
            controller_meta=controller_meta,
            service_uuid=service_uuid,
            service_name=helper_name,
            expected_sha=expected_sha,
            phase="service-start",
            output_dir=output_dir,
            observe_seconds=args.start_observe_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            log_poll_interval_seconds=args.log_poll_interval_seconds,
            timeout=args.http_timeout,
            max_response_bytes=args.max_response_bytes,
        )

        marker = start_result.get("marker")
        if (
            not (isinstance(marker, Mapping) and marker.get("ok") is True)
            and not args.no_forced_deploy_on_miss
        ):
            print("\n=== SAME SERVICE: MOTHER FORCED DEPLOY ===", flush=True)
            forced = _mother_request(
                controller,
                "POST",
                "/api/v1/deploy",
                body={"uuid": service_uuid, "force": True},
                timeout=args.http_timeout,
                max_response_bytes=args.max_response_bytes,
            )
            forced_receipt = mother_probe._mutation_receipt(
                mutation_id=f"{helper_name}.forced-deploy",
                controller_id=args.controller_id,
                method="POST",
                endpoint="/api/v1/deploy",
                response=forced,
                service_uuid=service_uuid,
            )
            _dump(output_dir / "forced-deploy-receipt.json", forced_receipt)
            print(
                f"FORCED_DEPLOY http={forced.get('status')} fields={_safe_payload_fields(forced.get('payload'))}",
                flush=True,
            )
            if forced.get("ok") is True:
                forced_result = _observe_exact_helper(
                    controller=controller,
                    controller_meta=controller_meta,
                    service_uuid=service_uuid,
                    service_name=helper_name,
                    expected_sha=expected_sha,
                    phase="forced-deploy",
                    output_dir=output_dir,
                    observe_seconds=args.forced_observe_seconds,
                    poll_interval_seconds=args.poll_interval_seconds,
                    log_poll_interval_seconds=args.log_poll_interval_seconds,
                    timeout=args.http_timeout,
                    max_response_bytes=args.max_response_bytes,
                )
            else:
                forced_result = {
                    "phase": "forced-deploy",
                    "marker": None,
                    "http_status": forced.get("status"),
                    "response_fields": _safe_payload_fields(forced.get("payload")),
                }

        diagnosis = _diagnosis(baseline_result, start_result, forced_result)
        summary = {
            "schema": "main-computer.native-mint-mother-lifecycle-smoke.v1",
            "network": args.network,
            "controller_id": args.controller_id,
            "node": chosen_node,
            "parent_service_uuid": parent_uuid,
            "expected_genesis_sha256": expected_sha,
            "helper_name": helper_name,
            "service_uuid": service_uuid,
            "mother_baseline": baseline_result,
            "service_start": {
                "marker": start_result.get("marker"),
                "final": start_result.get("final"),
            },
            "forced_deploy": (
                {
                    "marker": forced_result.get("marker"),
                    "final": forced_result.get("final"),
                }
                if isinstance(forced_result, Mapping)
                else None
            ),
            "diagnosis": diagnosis,
            "cleanup_requested": bool(args.cleanup),
        }
        _dump(output_dir / "summary.json", summary)

        print("\n=== DIAGNOSIS ===", flush=True)
        print(diagnosis, flush=True)
        print(f"ARTIFACTS: {output_dir}", flush=True)
        if not args.cleanup:
            print(f"PRESERVED exact helper service_uuid={service_uuid}", flush=True)

        start_ok = isinstance(start_result.get("marker"), Mapping) and start_result["marker"].get("ok") is True
        force_ok = isinstance(forced_result, Mapping) and isinstance(forced_result.get("marker"), Mapping) and forced_result["marker"].get("ok") is True
        return 0 if start_ok or force_ok else 1
    finally:
        if args.cleanup:
            deleted = _mother_request(
                controller,
                "DELETE",
                f"/api/v1/services/{service_q}",
                body=None,
                timeout=args.http_timeout,
                max_response_bytes=args.max_response_bytes,
            )
            print(f"CLEANUP service_uuid={service_uuid} http={deleted.get('status')}", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
