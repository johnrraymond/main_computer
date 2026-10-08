#!/usr/bin/env python3
"""Diagnose the Coolify lifecycle used by native-mint probe helpers.

This smoke NEVER writes genesis.  Every helper is MODE=probe and mounts the
accepted node's mother-config volume read-only.  It compares four Coolify
lifecycle paths while preserving the created resources by default:

  create-only    POST /services with instant_deploy=false, no start action
  service-start  current native-mint behavior: POST /services/{uuid}/start
  app-start      POST /services/{uuid}/applications/{app_uuid}/start
  instant-deploy POST /services with instant_deploy=true, no later start

The smoke records raw API responses, status timelines, log-probe responses,
and both source/deployable Compose returned by Coolify.  It intentionally does
not clean up unless --cleanup is supplied so failed resources remain available
for host-side inspection under /data/coolify/services/<service_uuid>.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
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


CASES = ("create-only", "service-start", "app-start", "instant-deploy")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _short(value: Any, limit: int = 220) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            text = repr(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _decode_compose_raw(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except Exception:
        return value


def _save_service_compose(case_dir: Path, payload: Any, suffix: str) -> None:
    if not isinstance(payload, Mapping):
        return
    raw = _decode_compose_raw(payload.get("docker_compose_raw"))
    deployable = payload.get("docker_compose")
    if isinstance(raw, str):
        _dump(case_dir / f"coolify-source-compose-{suffix}.yml", raw)
    if isinstance(deployable, str):
        _dump(case_dir / f"coolify-deployable-compose-{suffix}.yml", deployable)


def _resolve_parent_and_sha(
    runtime_state_root: Path,
    *,
    network: str,
    controller_id: str,
    parent_service_uuid: str | None,
    node: str | None,
    expected_genesis_sha: str | None,
) -> tuple[str, str, str | None, Path]:
    baseline_path, baseline = nm._latest_baseline(runtime_state_root)
    topology = nm._extract_topology(baseline)
    _chain_id, baseline_sha = nm._chain_identity(baseline, topology)
    expected = expected_genesis_sha or baseline_sha

    if parent_service_uuid:
        return nm._identifier(parent_service_uuid, "parent service uuid"), expected, node, baseline_path

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
            f"no accepted topology service found for controller={controller_id!r}"
            + (f" node={node!r}" if node else "")
        )
    candidates.sort()
    chosen_node, chosen_uuid = candidates[0]
    return nm._identifier(chosen_uuid, "parent service uuid"), expected, chosen_node, baseline_path


def _request(
    controller: Any,
    case_dir: Path,
    seq: list[int],
    method: str,
    endpoint: str,
    *,
    body: Mapping[str, Any] | None = None,
    timeout: float,
) -> dict[str, Any]:
    seq[0] += 1
    n = seq[0]
    started = time.monotonic()
    result = nm._coolify_http(controller, method, endpoint, body=body, timeout=timeout)
    elapsed = time.monotonic() - started
    record = {
        "sequence": n,
        "method": method,
        "endpoint": endpoint,
        "elapsed_seconds": round(elapsed, 6),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "payload": result.get("payload"),
    }
    _dump(case_dir / "api" / f"{n:04d}-{method.lower()}.json", record)
    return result


def _application_snapshot(payload: Any, helper_name: str) -> dict[str, Any]:
    app_uuid, app_name, app_status = nm._helper_application(payload, helper_name)
    root_status = payload.get("status") if isinstance(payload, Mapping) else None
    return {
        "service_status": root_status,
        "application_uuid": app_uuid,
        "application_name": app_name,
        "application_status": app_status,
    }


def _wait_for_application(
    controller: Any,
    case_dir: Path,
    seq: list[int],
    *,
    helper_uuid: str,
    helper_name: str,
    timeout: float,
    wait_seconds: float,
) -> tuple[str, str | None, str | None]:
    deadline = time.monotonic() + wait_seconds
    service_q = urllib.parse.quote(helper_uuid, safe="")
    latest_uuid: str | None = None
    latest_name: str | None = None
    latest_status: str | None = None
    while time.monotonic() < deadline:
        detail = _request(controller, case_dir, seq, "GET", f"/api/v1/services/{service_q}", timeout=timeout)
        if detail.get("ok"):
            latest_uuid, latest_name, latest_status = nm._helper_application(detail.get("payload"), helper_name)
            if latest_uuid:
                return latest_uuid, latest_name, latest_status
        time.sleep(0.25)
    raise RuntimeError(
        f"application UUID did not resolve within {wait_seconds:.1f}s; "
        f"last_name={latest_name!r} last_status={latest_status!r}"
    )


def _run_case(
    *,
    case: str,
    root_out: Path,
    private: Any,
    config: Mapping[str, Any],
    controller: Any,
    network: str,
    controller_id: str,
    parent_service_uuid: str,
    expected_sha: str,
    timeout: float,
    poll_seconds: float,
    log_poll_seconds: float,
    observe_seconds: float,
    create_only_seconds: float,
    cleanup: bool,
) -> dict[str, Any]:
    case_dir = root_out / case
    case_dir.mkdir(parents=True, exist_ok=True)
    seq = [0]
    suffix = f"{int(time.time() * 1000) % 1_000_000:06d}"
    helper_name = f"mother-native-mint-smoke-{case.replace('-', '')}-{parent_service_uuid[:8]}-{suffix}"
    compose = nm._helper_compose(
        helper_name=helper_name,
        parent_service_uuid=parent_service_uuid,
        mode="probe",
        expected_old_sha=expected_sha,
        new_sha="",
        new_genesis_b64="",
    )
    _dump(case_dir / "submitted-compose.yml", compose)
    _dump(case_dir / "submitted-compose.sha256", hashlib.sha256(compose.encode("utf-8")).hexdigest() + "\n")

    body = nm._helper_body(config, helper_name=helper_name, compose=compose, network=network)
    body["instant_deploy"] = case == "instant-deploy"
    body_summary = dict(body)
    encoded_compose = body_summary.pop("docker_compose_raw", "")
    body_summary["docker_compose_raw_sha256"] = hashlib.sha256(str(encoded_compose).encode("utf-8")).hexdigest()
    _dump(case_dir / "create-request-summary.json", body_summary)

    print(f"\n=== CASE {case} ===", flush=True)
    print(f"helper={helper_name}", flush=True)
    print(f"create instant_deploy={body['instant_deploy']}", flush=True)

    create = _request(controller, case_dir, seq, "POST", "/api/v1/services", body=body, timeout=timeout)
    if not create.get("ok"):
        print(f"CREATE FAILED http={create.get('status')} payload={_short(create.get('payload'))}", flush=True)
        return {"case": case, "ok": False, "stage": "create", "http": create.get("status")}

    helper_uuid = nm._response_uuid(create.get("payload"))
    service_q = urllib.parse.quote(helper_uuid, safe="")
    print(f"service_uuid={helper_uuid}", flush=True)

    result: dict[str, Any] = {
        "case": case,
        "helper_name": helper_name,
        "service_uuid": helper_uuid,
        "instant_deploy": body["instant_deploy"],
        "action": None,
        "timeline": [],
        "preserved": not cleanup,
    }

    try:
        # Capture the service object immediately after creation, before any explicit action.
        before = _request(controller, case_dir, seq, "GET", f"/api/v1/services/{service_q}", timeout=timeout)
        if before.get("ok"):
            _save_service_compose(case_dir, before.get("payload"), "before-action")
            snap = _application_snapshot(before.get("payload"), helper_name)
            print(
                "before-action "
                f"service_status={snap['service_status']!r} "
                f"app_uuid={snap['application_uuid'] or 'unresolved'} "
                f"app_status={snap['application_status'] or 'unknown'}",
                flush=True,
            )

        if case == "service-start":
            endpoint = f"/api/v1/services/{service_q}/start"
            action = _request(controller, case_dir, seq, "POST", endpoint, timeout=timeout)
            result["action"] = {"endpoint": endpoint, "status": action.get("status"), "payload": action.get("payload")}
            print(f"ACTION service-start http={action.get('status')} payload={_short(action.get('payload'))}", flush=True)

        elif case == "app-start":
            app_uuid, app_name, app_status = _wait_for_application(
                controller,
                case_dir,
                seq,
                helper_uuid=helper_uuid,
                helper_name=helper_name,
                timeout=timeout,
                wait_seconds=10.0,
            )
            app_q = urllib.parse.quote(app_uuid, safe="")
            endpoint = f"/api/v1/services/{service_q}/applications/{app_q}/start"
            action = _request(controller, case_dir, seq, "POST", endpoint, timeout=timeout)
            result["action"] = {
                "endpoint": endpoint,
                "status": action.get("status"),
                "payload": action.get("payload"),
                "application_uuid": app_uuid,
                "application_name": app_name,
                "pre_action_status": app_status,
            }
            print(
                f"ACTION app-start app_uuid={app_uuid} pre_status={app_status!r} "
                f"http={action.get('status')} payload={_short(action.get('payload'))}",
                flush=True,
            )

        elif case == "instant-deploy":
            result["action"] = {"endpoint": "create.instant_deploy", "status": create.get("status")}
            print("ACTION embedded instant_deploy=true", flush=True)

        else:
            result["action"] = {"endpoint": None, "status": None}
            print("ACTION none; observing create-only resource", flush=True)

        started = time.monotonic()
        duration = create_only_seconds if case == "create-only" else observe_seconds
        next_log = started
        last_key: tuple[Any, ...] | None = None
        sample_index = 0
        marker_seen: dict[str, Any] | None = None

        while time.monotonic() - started < duration:
            sample_index += 1
            now = time.monotonic()
            elapsed = now - started
            detail = _request(controller, case_dir, seq, "GET", f"/api/v1/services/{service_q}", timeout=timeout)
            payload = detail.get("payload") if detail.get("ok") else None
            snap = _application_snapshot(payload, helper_name)
            snap.update({
                "t": round(elapsed, 3),
                "detail_http": detail.get("status"),
            })

            if detail.get("ok"):
                _save_service_compose(case_dir, payload, "latest")

            if now >= next_log:
                app_uuid = snap.get("application_uuid")
                app_name = snap.get("application_name")
                marker, attempts, log_text = nm._helper_log_probe(
                    controller,
                    helper_uuid=helper_uuid,
                    application_uuid=app_uuid if isinstance(app_uuid, str) else None,
                    application_name=app_name if isinstance(app_name, str) else None,
                    timeout=timeout,
                )
                snap["log_attempts"] = attempts
                snap["log_tail"] = log_text[-1200:] if log_text else ""
                if marker is not None:
                    snap["marker"] = marker
                    marker_seen = marker
                next_log = now + log_poll_seconds

            result["timeline"].append(snap)
            _dump(case_dir / "timeline" / f"{sample_index:04d}.json", snap)

            key = (
                snap.get("detail_http"),
                snap.get("service_status"),
                snap.get("application_uuid"),
                snap.get("application_status"),
                bool(snap.get("marker")),
            )
            if key != last_key or sample_index == 1 or sample_index % max(1, int(2.0 / poll_seconds)) == 0:
                print(
                    f"T+{elapsed:05.2f}s detail={snap.get('detail_http')} "
                    f"service_status={snap.get('service_status')!r} "
                    f"app_uuid={snap.get('application_uuid') or 'unresolved'} "
                    f"app_status={snap.get('application_status') or 'unknown'} "
                    f"marker={'YES' if snap.get('marker') else 'no'}",
                    flush=True,
                )
                if snap.get("log_attempts"):
                    print(f"          logs=[{nm._helper_attempt_summary(snap['log_attempts'])}]", flush=True)
                last_key = key

            if marker_seen is not None:
                print(f"MARKER {json.dumps(marker_seen, sort_keys=True)}", flush=True)
                # Keep observing briefly to catch lifecycle transitions after proof.
                if elapsed >= min(3.0, duration):
                    break

            time.sleep(poll_seconds)

        # Durable API snapshots after observation.
        final_detail = _request(controller, case_dir, seq, "GET", f"/api/v1/services/{service_q}", timeout=timeout)
        _dump(case_dir / "final-service.json", final_detail)
        if final_detail.get("ok"):
            _save_service_compose(case_dir, final_detail.get("payload"), "final")

        deployments = _request(controller, case_dir, seq, "GET", "/api/v1/deployments", timeout=timeout)
        _dump(case_dir / "deployments-response.json", deployments)

        result["marker"] = marker_seen
        result["final"] = _application_snapshot(final_detail.get("payload") if final_detail.get("ok") else None, helper_name)
        result["ok"] = True
        _dump(case_dir / "case-result.json", result)
        return result

    finally:
        if cleanup:
            deleted = _request(controller, case_dir, seq, "DELETE", f"/api/v1/services/{service_q}", timeout=timeout)
            print(f"cleanup service_uuid={helper_uuid} http={deleted.get('status')}", flush=True)
        else:
            print(f"PRESERVED service_uuid={helper_uuid} helper={helper_name}", flush=True)


def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in results:
        final = item.get("final") if isinstance(item.get("final"), Mapping) else {}
        marker = item.get("marker") if isinstance(item.get("marker"), Mapping) else None
        action = item.get("action") if isinstance(item.get("action"), Mapping) else {}
        rows.append({
            "case": item.get("case"),
            "service_uuid": item.get("service_uuid"),
            "action_endpoint": action.get("endpoint"),
            "action_http": action.get("status"),
            "final_application_status": final.get("application_status"),
            "marker_seen": marker is not None,
            "marker_ok": marker.get("ok") if marker else None,
            "preserved": item.get("preserved"),
        })

    diagnosis = "inspect matrix"
    by_case = {row["case"]: row for row in rows}
    service = by_case.get("service-start")
    app = by_case.get("app-start")
    instant = by_case.get("instant-deploy")
    create_only = by_case.get("create-only")

    if service and not service["marker_seen"] and app and app["marker_seen"]:
        diagnosis = "service-level /start path fails while application-level /start succeeds"
    elif service and not service["marker_seen"] and instant and instant["marker_seen"]:
        diagnosis = "explicit service-level /start path fails while instant_deploy succeeds"
    elif service and service["marker_seen"] and service.get("marker_ok") is True:
        diagnosis = "current service-level /start path succeeded in isolated smoke"
    elif all(row.get("marker_seen") is False for row in rows if row["case"] != "create-only"):
        diagnosis = "all Coolify-driven launch paths failed; inspect preserved resources and API artifacts"
    elif create_only and create_only.get("marker_seen"):
        diagnosis = "create-only unexpectedly launched the helper; create endpoint has deployment side effects"

    return {"diagnosis": diagnosis, "cases": rows}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--network", default="mainnet")
    p.add_argument("--controller-id", default="coolify-c")
    p.add_argument("--node", help="accepted topology node to use as the read-only genesis source")
    p.add_argument("--parent-service-uuid", help="override accepted topology service UUID")
    p.add_argument("--expected-genesis-sha", help="override accepted topology genesis SHA-256")
    p.add_argument("--runtime-state-root", default=str(REPO_ROOT / "runtime" / "state"))
    p.add_argument(
        "--cases",
        default=",".join(CASES),
        help="comma-separated subset of: " + ",".join(CASES),
    )
    p.add_argument("--observe-seconds", type=float, default=12.0)
    p.add_argument("--create-only-seconds", type=float, default=5.0)
    p.add_argument("--poll-seconds", type=float, default=0.25)
    p.add_argument("--log-poll-seconds", type=float, default=1.0)
    p.add_argument("--http-timeout", type=float, default=30.0)
    p.add_argument("--cleanup", action="store_true", help="delete created helper services after each case; default preserves them")
    p.add_argument("--output-dir", help="diagnostic output directory; default runtime/native-mint-coolify-lifecycle-smoke/<run>")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    requested = [part.strip() for part in args.cases.split(",") if part.strip()]
    invalid = [case for case in requested if case not in CASES]
    if invalid or not requested:
        print(f"ERROR invalid --cases: {invalid or requested}; allowed={CASES}", file=sys.stderr)
        return 2
    if args.poll_seconds <= 0 or args.log_poll_seconds <= 0 or args.observe_seconds <= 0 or args.create_only_seconds <= 0:
        print("ERROR polling/observation durations must be > 0", file=sys.stderr)
        return 2

    runtime_root = Path(args.runtime_state_root).resolve()
    operation = nm._operation_identity("inspect", args.network, f"native-mint-coolify-lifecycle-smoke-{_stamp()}")
    _paths, private, private_doc = nm._load_private(runtime_root, operation)
    controller = nm.resolve_coolify_controller(private, args.network, args.controller_id, require_enabled=True, require_token=True)
    config = nm._controller_config(private, args.network, args.controller_id)

    parent_uuid, expected_sha, chosen_node, baseline_path = _resolve_parent_and_sha(
        runtime_root,
        network=args.network,
        controller_id=args.controller_id,
        parent_service_uuid=args.parent_service_uuid,
        node=args.node,
        expected_genesis_sha=args.expected_genesis_sha,
    )

    run_name = f"{_stamp()}-{args.controller_id}-{parent_uuid[:8]}"
    root_out = Path(args.output_dir).resolve() if args.output_dir else (REPO_ROOT / "runtime" / "native-mint-coolify-lifecycle-smoke" / run_name)
    root_out.mkdir(parents=True, exist_ok=True)

    meta = {
        "schema": "main-computer.native-mint-coolify-lifecycle-smoke.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "network": args.network,
        "controller_id": args.controller_id,
        "controller_base_url": getattr(controller, "base_url", None),
        "server_uuid": config.get("server_uuid"),
        "project_uuid": config.get("project_uuid"),
        "parent_service_uuid": parent_uuid,
        "node": chosen_node,
        "expected_genesis_sha256": expected_sha,
        "baseline_evidence": str(baseline_path),
        "cases": requested,
        "cleanup": bool(args.cleanup),
        "probe_only": True,
    }
    _dump(root_out / "run.json", meta)

    print("NATIVE_MINT_COOLIFY_LIFECYCLE_SMOKE", flush=True)
    print(f"network={args.network}", flush=True)
    print(f"controller={args.controller_id} base_url={getattr(controller, 'base_url', '<unknown>')}", flush=True)
    print(f"node={chosen_node or '<override>'} parent_service={parent_uuid}", flush=True)
    print(f"expected_genesis_sha256={expected_sha}", flush=True)
    print(f"baseline={baseline_path}", flush=True)
    print(f"output={root_out}", flush=True)
    print(f"cases={','.join(requested)}", flush=True)
    print("mode=PROBE ONLY; genesis is mounted read-only", flush=True)
    print(f"cleanup={'yes' if args.cleanup else 'NO (resources preserved)'}", flush=True)

    results: list[dict[str, Any]] = []
    for case in requested:
        try:
            result = _run_case(
                case=case,
                root_out=root_out,
                private=private,
                config=config,
                controller=controller,
                network=args.network,
                controller_id=args.controller_id,
                parent_service_uuid=parent_uuid,
                expected_sha=expected_sha,
                timeout=args.http_timeout,
                poll_seconds=args.poll_seconds,
                log_poll_seconds=args.log_poll_seconds,
                observe_seconds=args.observe_seconds,
                create_only_seconds=args.create_only_seconds,
                cleanup=args.cleanup,
            )
        except Exception as exc:
            result = {
                "case": case,
                "ok": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            _dump(root_out / case / "uncaught-error.json", result)
            print(f"CASE ERROR {case}: {type(exc).__name__}: {exc}", flush=True)
        results.append(result)

    summary = _summary(results)
    _dump(root_out / "summary.json", summary)
    print("\n=== SUMMARY ===", flush=True)
    for row in summary["cases"]:
        print(
            f"{row['case']:<14} action_http={row.get('action_http')} "
            f"final={row.get('final_application_status')!r} "
            f"marker={'YES' if row.get('marker_seen') else 'no'} "
            f"marker_ok={row.get('marker_ok')!r} "
            f"service_uuid={row.get('service_uuid')}",
            flush=True,
        )
    print(f"DIAGNOSIS: {summary['diagnosis']}", flush=True)
    print(f"ARTIFACTS: {root_out}", flush=True)
    if not args.cleanup:
        print("NOTE: helper services were intentionally preserved for host-side inspection.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
