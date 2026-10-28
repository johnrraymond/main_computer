"""Fail-closed offline test inventory for the Stable Hub end-to-end pathway.

This is not a crash/failover campaign and does not certify unresolved races.
Every lane invokes the repository's existing authoritative regression tests.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


# Deliberately explicit: additions to the Hub pipeline require a lane owner and
# a regression before a maintainer can claim the relevant boundary is covered.
PATHWAY_LANES: dict[str, tuple[str, ...]] = {
    "identity_msk_topology_transport": (
        "tests/test_stable_hub_multisession_keys.py",
        "tests/test_stable_hub_topology.py",
        "tests/test_stable_hub_worker_live_session.py",
        "tests/test_hub_private_https_routes.py",
    ),
    "market_owner_handoff_idempotency": (
        "tests/test_stable_hub_worker_market.py",
        "tests/test_stable_hub_owner_acceptance.py",
        "tests/test_stable_hub_hub_to_hub_handoff.py",
        "tests/test_stable_hub_hardening.py",
    ),
    "worker_execution_and_browser_contract": (
        "tests/test_worker_live_session_local_ai_execution.py",
        "tests/test_worker_browser_work_cycle_launcher.py",
        "tests/test_stable_hub_lab_worker_cycle_regressions.py",
    ),
    "accounting_claims_and_settlement": (
        "tests/test_stable_hub_fdb_atomic_accounting.py",
        "tests/test_stable_settlement_ownership_p3.py",
        "tests/test_stable_hub_integrated_market_stress.py",
    ),
    "bridge_financial_boundary": (
        "tests/test_stable_hub_credit_bridge_patch2.py",
        "tests/test_stable_hub_historical_funding_acceptance.py",
        "tests/test_stable_hub_bridge_deposit_lookup.py",
    ),
    "crash_lab_fault_harness_contract": (
        "tests/test_hub_crash_lab.py",
        "tests/test_stable_hub_crash_recovery.py",
        "tests/test_stable_hub_crash_recovery_lab_contract.py",
        "tests/test_stable_hub_lab_contract.py",
    ),
}

# These are NOT claimed to be solved by the passing suite.  Keep in machine-
# readable output so a green gate cannot be misconstrued as full certification.
OPEN_ASSURANCE_BOUNDARIES: tuple[str, ...] = (
    "owner_hub_process_killed_after_hold_before_socket_cleanup: patch2 recovery implemented, isolated process-kill campaign PASS required",
    "socket_close_after_failed_FDB_cleanup: retry and rollback tested offline, live FDB fault campaign required",
    "live_deployed_version_and_testnet_bridge/on_chain_payout: not certified by local lab",
)


def run_pathway_audit(
    *, root: Path, timeout_per_lane: float = 300.0,
    progress: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """Execute every lane, preserving failure output and reporting unresolved risks."""
    if timeout_per_lane <= 0:
        raise ValueError("timeout_per_lane must be positive")
    lanes: list[dict[str, Any]] = []
    for name, selectors in PATHWAY_LANES.items():
        if progress is not None:
            progress(name, "running")
        missing = [path for path in selectors if not (root / path).is_file()]
        if missing:
            lanes.append({"lane": name, "ok": False, "exit_code": None,
                          "tests": list(selectors), "missing": missing,
                          "stdout": "", "stderr": "required test files missing"})
            if progress is not None:
                progress(name, "missing")
            continue
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", *selectors], cwd=root,
                capture_output=True, text=True, check=False,
                timeout=timeout_per_lane,
            )
            lanes.append({"lane": name, "ok": completed.returncode == 0,
                          "exit_code": completed.returncode, "tests": list(selectors),
                          "stdout": completed.stdout[-12000:],
                          "stderr": completed.stderr[-6000:]})
        except subprocess.TimeoutExpired as exc:
            lanes.append({"lane": name, "ok": False, "exit_code": 124,
                          "tests": list(selectors),
                          "stdout": str(exc.stdout or "")[-12000:],
                          "stderr": f"regression lane timed out after {timeout_per_lane:g}s"})
        except OSError as exc:
            lanes.append({"lane": name, "ok": False, "exit_code": None,
                          "tests": list(selectors), "stdout": "",
                          "stderr": f"could not run lane: {type(exc).__name__}: {exc}"})
        if progress is not None:
            progress(name, "pass" if lanes[-1]["ok"] else "fail")
    return {
        "ok": bool(lanes) and all(lane["ok"] for lane in lanes),
        "scope": "offline_regressions_only",
        "full_pathway_certified": False,
        "lanes": lanes,
        "open_assurance_boundaries": list(OPEN_ASSURANCE_BOUNDARIES),
    }
