"""Existing Docker worker lab: P0/P1 live-session execution lane.

This lane uses the *real* authenticated P1 WebSocket + requester transaction
client instead of retired HTTP worker-poll APIs. Each generated worker runs an
isolated three-request P1 scenario against the same three Experimental Hubs.
Legacy process/node timing simulations remain separate and cannot count as
live-session acceptance. No wallet private keys are written to artifacts.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from tools.scheduler_lab.node_list import build_nodes, normalize_hub_base_urls


def _lab_urls(urls: list[str]) -> list[str]:
    """Preserve the authoritative host-loopback Hub IDs for P1 proof checks.

    In Docker only, actual socket transport uses host.docker.internal via
    tools.exp_hub_lab.run_lab._docker_lab_transport_url.
    """
    if os.environ.get("EXP_HUB_LAB_DOCKER_HOST_LOOPBACK_MAP") != "1":
        raise ValueError("live_session_v1 is only supported inside the integrated Docker worker lab")
    result: list[str] = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname != "host.docker.internal" or not parsed.port:
            raise ValueError(f"Unexpected Docker lab Hub URL: {url!r}; require host.docker.internal:port")
        result.append(f"http://127.0.0.1:{parsed.port}")
    if len(result) != 3 or len(set(result)) != 3:
        raise ValueError("P1 Docker live-session lab requires three distinct Hub URLs")
    return result


def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def run_live_session_lab(args: Any) -> int:
    from tools.exp_hub_lab import run_lab as p1

    output_dir = Path(os.environ.get("LAB_OUTPUT_DIR") or args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(getattr(args, "run_id", "") or os.environ.get("LAB_RUN_ID") or uuid.uuid4().hex[:12])
    safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in run_id)[:80]
    concurrency = int(os.environ.get("LAB_LIVE_CONCURRENCY", "3"))
    timeout = float(os.environ.get("LAB_LIVE_TIMEOUT_SECONDS", "30"))
    if not (1 <= concurrency <= 32) or timeout <= 0:
        raise ValueError("LAB_LIVE_CONCURRENCY must be 1..32 and LAB_LIVE_TIMEOUT_SECONDS > 0")
    urls = normalize_hub_base_urls(
        (getattr(args, "hub_base_urls", "") or "").split(","),
        getattr(args, "hub_base_url", "http://host.docker.internal:8970"),
    )
    authoritative_urls = _lab_urls(urls)
    if str(getattr(args, "request_mode", "")) != "live_session_v1" or str(getattr(args, "auth_mode", "")) != "multisession-wallet":
        raise ValueError("Live-session lab requires strict wallet/MSK mode")
    if int(args.ring) != 3:
        raise ValueError("P1 live-session lab requires ring 3")

    nodes = build_nodes(
        total=int(args.nodes if args.nodes is not None else args.total or 12),
        workers=args.workers, requesters=args.requesters,
        seed=int(args.seed), hub_base_urls=urls, network=args.network,
        ring=3, chain_id=args.chain_id,
        problematic_worker_rate=args.problematic_worker_rate,
        problematic_requester_rate=args.problematic_requester_rate,
        problematic_failure_multiplier=args.problematic_failure_multiplier,
        disable_problematic=args.disable_problematic,
    )
    workers = [n for n in nodes if n["kind"] == "worker"]
    requesters = [n for n in nodes if n["kind"] == "requester"]
    if not workers or not requesters:
        raise ValueError("At least one worker and one requester are required")
    summary_path = output_dir / f"{safe_id}-live-summary.json"
    events_path = output_dir / f"{safe_id}-live-events.jsonl"
    nodes_path = output_dir / f"{safe_id}-live-nodes.jsonl"
    with nodes_path.open("w", encoding="utf-8") as stream:
        for node in nodes:
            stream.write(json.dumps(node, sort_keys=True) + "\n")
    started = time.monotonic()
    summary: dict[str, Any] = {
        "ok": False, "mode": "live_session_v1", "runtime": "main_computer.exp_fdb_hub",
        "run_id": safe_id, "worker_nodes_generated": len(workers),
        "requester_nodes_generated": len(requesters),
        "worker_scenarios_requested": len(workers), "worker_scenarios_passed": 0,
        "parallel_scenarios": min(concurrency, len(workers)),
        "hub_urls": authoritative_urls, "requests_attempted": 0,
        "requests_accepted": 0, "charged_results": 0,
        "released_failures": 0, "remote_handoffs": 0,
        "invariant_violations": 0, "errors": [],
        "scope": "P1 authenticated worker/requester scenarios; not P2-A process-restart or P2-B settlement",
        "nodes_path": str(nodes_path), "events_path": str(events_path),
    }
    _write_json_atomic(summary_path, summary)

    # Preflight BEFORE creating paid sessions. Never turn missing Temporal into
    # a zero-request PASS or fall back to the unauthenticated legacy protocol.
    try:
        cluster = p1.check_cluster(hub_urls=authoritative_urls, timeout=timeout, require_multisession_auth=True)
        if not cluster.get("ok"):
            summary["errors"].append({"gate": "cluster_preflight", "checks": cluster.get("checks", [])})
            return 1

        def one_worker(index: int, worker: dict[str, Any]) -> dict[str, Any]:
            requester = requesters[index % len(requesters)]
            identity = f"{safe_id}:{requester['node_id']}:{worker['node_id']}"
            try:
                result = p1.verify_handoff(
                    hub_urls=authoritative_urls,
                    requester_account=identity,
                    funding_credits=12,
                    worker_credits=1,
                    stress_requests=3,
                    timeout=timeout,
                    failure_indices=(1,), replay_success_index=0,
                    replay_failed_index_as_success=1,
                    reconnect_after_index=0, replay_http_index=0,
                    isolate_worker=True, require_multisession_auth=True,
                )
                proof = result.get("proof") or {}
                metrics = result.get("metrics") or {}
                required = {
                    "requests_attempted": 3, "requests_accepted": 3,
                    "charged_results": 2, "released_failures": 1,
                    "invariant_violations": 0,
                }
                must_prove = (
                    "cluster_has_three_hubs", "continuations_point_to_owner_hub",
                    "remote_entries_handoff_to_owner",
                    "cross_hub_http_retry_idempotent", "duplicate_success_was_idempotent",
                    "late_success_after_failure_did_not_charge",
                    "authenticated_worker_migrated_between_hubs",
                    "no_outstanding_holds_after_stress",
                    "worker_connected_with_msk", "requests_signed_with_msk",
                    "multi_session_auth_required", "owner_hub_charged_exp_ledger",
                )
                ok = (
                    result.get("ok") is True
                    and int(metrics.get("remote_handoffs") or 0) >= 1
                    and all(metrics.get(k) == v for k, v in required.items())
                    and all(proof.get(k) is True for k in must_prove)
                )
                # No MSK tokens, wallet private keys, or full request payloads in artifacts.
                return {"worker": worker["node_id"], "requester": requester["node_id"],
                        "ok": ok, "scenario_id": result.get("scenario_id", ""),
                        "session_ids": [x.get("response", {}).get("session_id", "") for x in result.get("requests", [])],
                        "metrics": metrics, "proof": {k: proof.get(k) for k in must_prove},
                        "error": str(result.get("error") or ("P1 invariant not met" if not ok else ""))}
            except Exception as exc:
                return {"worker": worker["node_id"], "requester": requester["node_id"],
                        "ok": False, "metrics": {}, "proof": {}, "error": f"{type(exc).__name__}: {exc}"}

        with ThreadPoolExecutor(max_workers=min(concurrency, len(workers))) as pool, events_path.open("w", encoding="utf-8") as stream:
            futures = [pool.submit(one_worker, i, worker) for i, worker in enumerate(workers)]
            for future in as_completed(futures):
                report = future.result()
                stream.write(json.dumps(report, sort_keys=True) + "\n")
                stream.flush()
                if report["ok"]:
                    summary["worker_scenarios_passed"] += 1
                else:
                    summary["errors"].append({"worker": report["worker"], "error": report["error"]})
                for name in ("requests_attempted", "requests_accepted", "charged_results", "released_failures", "remote_handoffs", "invariant_violations"):
                    summary[name] += int(report.get("metrics", {}).get(name) or 0)
                if not report["ok"] and not int(report.get("metrics", {}).get("invariant_violations") or 0):
                    summary["invariant_violations"] += 1
                _write_json_atomic(summary_path, summary)
        summary["ok"] = summary["worker_scenarios_passed"] == len(workers) and summary["invariant_violations"] == 0
        return 0 if summary["ok"] else 1
    except Exception as exc:
        summary["errors"].append({"gate": "unhandled", "error": f"{type(exc).__name__}: {exc}"})
        return 1
    finally:
        summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_json_atomic(summary_path, summary)
        print(f"Experimental Docker live-session lab: {'PASS' if summary['ok'] else 'FAIL'}", flush=True)
        print(f"  authenticated worker scenarios: {summary['worker_scenarios_passed']}/{len(workers)}", flush=True)
        print(f"  charged: {summary['charged_results']} released: {summary['released_failures']} violations: {summary['invariant_violations']}", flush=True)
        print(f"  artifacts: {summary_path}", flush=True)
