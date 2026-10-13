"""Live Experimental Hub stress acceptance against the actual three-Hub runtime.

Each scenario has its own funded account, signed worker identity, and unique
market capability, so concurrent runs cannot accidentally use another
scenario's worker. No Stable Hub server is substituted or started here.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import secrets
from typing import Any, Sequence


def verify_experimental_stress(
    *,
    hub_urls: Sequence[str],
    runs: int = 10,
    parallel: int = 3,
    timeout: float = 30.0,
) -> dict[str, Any]:
    from tools.exp_hub_lab import run_lab

    if runs < 1 or runs > 100:
        raise ValueError("stress runs must be 1 through 100")
    if parallel < 1 or parallel > 12:
        raise ValueError("stress parallel workers must be 1 through 12")
    if len(hub_urls) != 3 or len(set(hub_urls)) != 3:
        raise ValueError("Experimental stress requires three distinct Hub URLs")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    # Check the real Experimental cluster before allocating worker identities.
    cluster = run_lab.check_cluster(hub_urls=hub_urls, timeout=timeout, require_multisession_auth=True)
    if cluster.get("ok") is not True:
        return {"ok": False, "mode": "verify-exp-stress", "error": "cluster_check_failed", "cluster": cluster}

    def _one(index: int) -> dict[str, Any]:
        try:
            result = run_lab.verify_handoff(
                hub_urls=hub_urls,
                requester_account=f"exp-stress-requester-{index}-{secrets.token_urlsafe(6)}",
                funding_credits=12,
                stress_requests=3,
                worker_credits=1,
                timeout=timeout,
                failure_indices=(1,),
                replay_success_index=0,
                replay_failed_index_as_success=1,
                reconnect_after_index=0,
                isolate_worker=True,
                require_multisession_auth=True,
                replay_http_index=0,
            )
            proof = dict(result.get("proof") or {})
            metrics = dict(result.get("metrics") or {})
            expected = {
                "requests_attempted": 3,
                "requests_accepted": 3,
                "charged_results": 2,
                "released_failures": 1,
                "injected_worker_failures": 1,
                "invariant_violations": 0,
            }
            checks = {
                "request_accounting": all(metrics.get(key) == value for key, value in expected.items()),
                "cross_hub_forwarding": metrics.get("remote_handoffs", 0) >= 2,
                "owner_continuation": proof.get("continuations_point_to_owner_hub") is True,
                "replay_idempotent": proof.get("duplicate_success_was_idempotent") is True,
                "http_retry_idempotent": proof.get("cross_hub_http_retry_idempotent") is True,
                "authenticated_reconnect": proof.get("authenticated_worker_migrated_between_hubs") is True,
                "strict_requester_auth": all(proof.get(key) is True for key in (
                    "multi_session_auth_required", "requester_msk_issued", "worker_msk_issued",
                    "worker_connected_with_msk", "requests_signed_with_msk",
                )),
                "hold_charge_release": proof.get("accepted_work_is_held_not_charged") is True
                and proof.get("owner_hub_charged_exp_ledger") is True
                and proof.get("no_outstanding_holds_after_stress") is True,
                "late_result_after_failure": proof.get("late_success_after_failure_did_not_charge") is True,
            }
            return {
                "run": index + 1,
                "ok": result.get("ok") is True and all(checks.values()),
                "checks": checks,
                "metrics": metrics,
                "error": result.get("error", ""),
            }
        except Exception as exc:  # surface individual load failures, not a false aggregate PASS
            return {"run": index + 1, "ok": False, "error": f"{type(exc).__name__}: {exc}", "checks": {}, "metrics": {}}

    reports: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(parallel, runs)) as pool:
        futures = [pool.submit(_one, i) for i in range(runs)]
        for future in as_completed(futures):
            reports.append(future.result())
    reports.sort(key=lambda item: item["run"])
    keys = ("requests_attempted", "requests_accepted", "charged_results", "released_failures", "remote_handoffs", "invariant_violations")
    totals = {key: sum(int(report.get("metrics", {}).get(key) or 0) for report in reports) for key in keys}
    passes = sum(report["ok"] is True for report in reports)
    return {
        "ok": passes == runs and totals["invariant_violations"] == 0,
        "mode": "verify-exp-stress",
        "runtime": "main_computer.exp_fdb_hub",
        "scope": "live-local-3-hub-fdb; no blockchain or Temporal recovery",
        "hub_urls": list(hub_urls),
        "runs_requested": runs,
        "runs_passed": passes,
        "parallel_scenarios": min(parallel, runs),
        "totals": totals,
        "runs": reports,
    }


def render_stress(result: dict[str, Any]) -> str:
    totals = result.get("totals") or {}
    lines = [
        f"Experimental Hub integrated market stress: {'PASS' if result.get('ok') else 'FAIL'}",
        f"  real runtime: {result.get('runtime', 'not available')}",
        f"  runs: {result.get('runs_passed', 0)}/{result.get('runs_requested', 0)} passed",
        f"  parallel scenarios: {result.get('parallel_scenarios', 0)}",
        f"  requests attempted: {totals.get('requests_attempted', 0)}",
        f"  accepted sessions: {totals.get('requests_accepted', 0)}",
        f"  successful charges: {totals.get('charged_results', 0)}",
        f"  released failures: {totals.get('released_failures', 0)}",
        f"  remote handoffs: {totals.get('remote_handoffs', 0)}",
        f"  invariant violations: {totals.get('invariant_violations', 0)}",
    ]
    for report in result.get("runs") or []:
        if not report.get("ok"):
            failures = [key for key, ok in report.get("checks", {}).items() if not ok]
            lines.append(f"  run {report.get('run')} FAIL: {report.get('error') or ', '.join(failures) or 'incomplete proof'}")
    if result.get("error"):
        lines.append(f"  error: {result['error']}")
    cluster = result.get("cluster") if isinstance(result.get("cluster"), dict) else {}
    for check in cluster.get("checks") or []:
        if not check.get("ok"):
            lines.append(
                f"  hub {check.get('hub_url', '?')} FAIL: {check.get('error', 'unknown')}"
                f" health_http={check.get('health_status', '?')}"
                f" temporal_configured={check.get('temporal_configured', '?')}"
                f" temporal_ready={check.get('temporal_ready', '?')}"
            )
    return "\n".join(lines)


def verify_experimental_recovery(*, hub_urls: Sequence[str], timeout: float = 30.0) -> dict[str, Any]:
    """P1 fault-injection gate: in-flight socket loss, FDB hold release, HTTP replay."""
    from tools.exp_hub_lab import run_lab
    cluster = run_lab.check_cluster(hub_urls=hub_urls, timeout=timeout, require_multisession_auth=True)
    if not cluster.get("ok"):
        return {"ok": False, "mode": "verify-exp-recovery", "error": "cluster_check_failed", "cluster": cluster}
    result = run_lab.verify_handoff(
        hub_urls=hub_urls,
        requester_account="exp-recovery-" + secrets.token_urlsafe(6),
        funding_credits=12, stress_requests=3, worker_credits=1, timeout=timeout,
        failure_indices=(1,), replay_success_index=0, replay_failed_index_as_success=1,
        reconnect_after_index=0, isolate_worker=True, require_multisession_auth=True,
        disconnect_failure_index=1, replay_http_index=1, competing_reconnect=True,
    )
    proof = dict(result.get("proof") or {})
    metrics = dict(result.get("metrics") or {})
    checks = {
        "inflight_disconnect_released_hold": proof.get("inflight_disconnect_released_hold") is True,
        "cross_hub_http_retry_idempotent": proof.get("cross_hub_http_retry_idempotent") is True,
        "authenticated_reconnect": proof.get("authenticated_worker_migrated_between_hubs") is True,
        "stale_owner_epoch_fenced": proof.get("stale_owner_epoch_fenced") is True,
        "accounting": metrics.get("charged_results") == 2 and metrics.get("released_failures") == 1,
        "no_residual_holds": proof.get("no_outstanding_holds_after_stress") is True,
        "zero_violations": metrics.get("invariant_violations") == 0,
    }
    return {"ok": result.get("ok") is True and all(checks.values()),
            "mode": "verify-exp-recovery", "runtime": "main_computer.exp_fdb_hub",
            "checks": checks, "metrics": metrics, "proof": proof, "error": result.get("error", "")}
