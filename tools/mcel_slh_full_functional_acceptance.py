"""Evidence-driven whole-SLH functional acceptance (no production mutations).

Inventory is derived from the compiled semantic application, not a frozen list of
screens. Each behavioral claim has an explicit existing pytest node. A passing
static/contract test is never silently substituted for a UI or backend test.

Run:
    python -m tools.mcel_slh_full_functional_acceptance --mode inventory
    python -m tools.mcel_slh_full_functional_acceptance --mode isolated
    python -m tools.mcel_slh_full_functional_acceptance --mode isolated --case identity-ui

Default runs curated mocked-browser or disposable-service tests plus the
opt-in-by-default isolated reactivity shadow diagnostic lane. The
optional real-host lane delegates to the existing read-only, allowlisted live
runner. No destructive live lane is implemented or implicitly enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import ast
from dataclasses import dataclass
import json
import signal
import tempfile
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Literal

from main_computer.mcel_slh_application import compile_slh_application

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "runtime/reports/mcel-slh-full-functional/report.json"
Lane = Literal["ui", "adapter", "isolated-effect", "contract"]


@dataclass(frozen=True)
class Case:
    id: str
    lane: Lane
    node: str
    intents: tuple[str, ...]
    proves: str


def case(name: str, lane: Lane, node: str, intents: str, proves: str) -> Case:
    return Case(name, lane, node, tuple("intent:"+value for value in intents.split()), proves)


# Every test below is selected for an asserted *behavior* in its named function.
# A missing second lane remains NOT_TESTED; catalog coverage is not certification.
CASES: tuple[Case, ...] = (
    case("identity-ui", "ui", "tests/test_mcel_slh_persistent_identity_target_context.py::test_selected_identity_survives_target_change_and_remote_pin_stays_distinct", "choose-identity select-target", "selected identity and target persist independently"),
    case("identity-discovery", "adapter", "tests/test_mcel_slh_identity_oracle_v4b.py::test_v4b_real_local_identity_api_recovers_default_without_new_keys", "refresh-identities refresh-identity-status", "local identity discovery preserves canonical keys"),
    case("identity-rail-ui", "ui", "tests/test_mcel_slh_identity_oracle_v4b.py::test_v4b_browser_reflects_selected_identity_without_exposing_login", "refresh-identities refresh-identity-status", "browser projects verified identity inventory"),
    case("lifecycle-setup-route-adapter", "adapter", "tests/test_mcel_slh_setup_action_adapter_contract.py::test_declared_lifecycle_actions_require_same_origin_and_dispatch_correctly", "configure-and-enable-organization toggle-organization toggle-organization-enablement", "actual same-origin setup route dispatches declared lifecycle actions and rejects forged requests"),
    case("lifecycle-observation-ui", "ui", "tests/test_mcel_slh_lifecycle_disable_enablement_oracle.py::test_configure_footer_actions_follow_authoritative_lifecycle", "refresh-lifecycle", "lifecycle observation updates controls"),
    case("lifecycle-configuration-ui", "ui", "tests/test_mcel_slh_configure_enable_disable.py::test_lifecycle_browser_explicit_transitions_preserve_selected_identity", "configure-and-enable-organization toggle-organization-enablement", "form submission and enabled/disabled state reconcile"),
    case("lifecycle-configuration-isolated", "isolated-effect", "tests/test_mcel_slh_configure_enable_disable.py::test_real_disposable_configure_starts_persists_disable_and_reconfigure", "configure-and-enable-organization toggle-organization-enablement", "disposable organization process and durable configuration"),
    case("lifecycle-startstop-ui", "ui", "tests/test_mcel_slh_lifecycle_disable_enablement_oracle.py::test_configure_footer_actions_follow_authoritative_lifecycle", "toggle-organization", "Start/Stop control follows authoritative organization state"),
    case("lifecycle-startstop-isolated", "isolated-effect", "tests/test_mcel_slh_configure_enable_disable.py::test_temporary_stop_suspends_watchdog_but_keeps_autostart_policy", "toggle-organization", "temporary stop preserves explicit autostart policy"),
    case("network-connect-route-adapter", "adapter", "tests/test_mcel_slh_network_connection_integration.py::test_network_connect_http_route_requires_loopback_same_origin_and_header", "connect-network-slh", "same-origin network connect route enforces operation header and target fields"),
    case("network-connect-ui", "ui", "tests/test_mcel_slh_network_connection_integration.py::test_network_browser_connect_uses_current_selected_identity_and_inspector", "connect-network-slh", "network connection form uses selected signer and updates inspector"),
    case("network-connect-isolated", "isolated-effect", "tests/test_mcel_slh_network_connection_integration.py::test_first_connection_prompts_before_signing_then_pins_and_authenticates", "connect-network-slh", "pin and authentication require explicit first trust"),
    case("network-disconnect-ui", "ui", "tests/test_mcel_slh_identity_notebook_oracle_v1.py::test_browser_three_tabs_fixed_footer_scope_and_actions", "disconnect-network-slh", "disconnect revokes protected network notebook projection"),
    case("remote-seh-connect-ui", "ui", "tests/test_mcel_seh_management_oracle_v1.py::test_browser_navigation_notebooks_and_target_specific_authority", "connect-remote-seh", "remote-SEH connection uses its dedicated target and hides local controls"),
    case("remote-seh-connect-isolated", "adapter", "tests/test_mcel_seh_management_oracle_v1.py::test_remote_seh_connect_route_allows_only_exact_session_operations", "connect-remote-seh disconnect-remote-seh", "remote-SEH route admits only declared session operations"),
    case("organization-notebook-ui", "ui", "tests/test_mcel_slh_identity_notebook_oracle_v1.py::test_browser_three_tabs_fixed_footer_scope_and_actions", "read-organization-pending read-organization-enrolled read-organization-trash read-organization-retention organization-accept organization-reject organization-delete organization-reactivate organization-remove set-organization-trash-retention empty-organization-trash", "Pending/Enrolled/Trash UI transitions, row identity, footer and failure cases"),
    case("organization-notebook-adapter", "adapter", "tests/test_mcel_slh_identity_notebook_oracle_v1.py::test_fixed_adapter_mapping_filters_status_and_preserves_real_ids", "read-organization-pending read-organization-enrolled read-organization-trash read-organization-retention organization-accept organization-reject organization-delete organization-reactivate organization-remove set-organization-trash-retention empty-organization-trash", "adapter maps operations and validates record inputs"),
    case("organization-notebook-service", "isolated-effect", "tests/test_slh_identity_trash_backend.py::test_three_lists_and_working_row_operations", "organization-accept organization-reject organization-delete organization-reactivate organization-remove read-organization-pending read-organization-enrolled read-organization-trash", "disposable backend processes identity lifecycle actions"),
    case("organization-retention-service", "isolated-effect", "tests/test_slh_identity_trash_backend.py::test_retention_durable_30_day_boundary_and_browser_independence", "set-organization-trash-retention read-organization-retention empty-organization-trash", "retention policy survives restart and enforces cleanup boundary"),
    case("enrollment-real-http-isolated", "isolated-effect", "tests/test_slh_enrollment_lifecycle.py::TestEnrollmentLifecycle::test_two_paths_use_one_live_http_approval_endpoint_and_revoke", "organization-accept organization-reject", "disposable HTTP enrollment and authorization boundary"),
    case("identity-lazy-authorization-ui", "ui", "tests/test_mcel_record_notebook_async_reconciliation.py::test_initial_pending_tab_hydrates_after_identity_and_target_restore_without_click", "read-organization-pending", "application-owned initial Pending hydration"),
    case("seh-observation-ui", "ui", "tests/test_mcel_slh_seh_deployment_ui.py::test_local_seh_notebook_reads_coolify_directly_without_authentication", "read-seh-deployment", "LMS deployment inspection does not depend on organization authentication"),
    case("seh-connection-ui", "ui", "tests/test_mcel_seh_management_oracle_v1.py::test_browser_navigation_notebooks_and_target_specific_authority", "read-seh-connection seh-connection-stop", "connection tab reads and invokes Stop for selected target"),
    case("seh-connection-adapter", "adapter", "tests/test_mcel_seh_management_oracle_v1.py::test_lifecycle_authority_scope_preserves_transport_and_denials", "read-seh-connection seh-connection-stop seh-connection-restart", "local/network connection adapter scope and refusal"),
    case("seh-connection-bridge", "adapter", "tests/test_mcel_seh_connection_bridge_admission.py::test_connection_read_and_controls_reach_authenticated_pinned_org_slh", "seh-connection-start seh-connection-stop seh-connection-restart", "pinned authorized connection bridge accepts exact lifecycle operations"),
    case("seh-deploy-ui", "ui", "tests/test_mcel_seh_two_action_lifecycle_ui.py::test_two_action_browser_modal_and_durable_receipt", "seh-deployment-deploy seh-deployment-undeploy", "deploy and undeploy buttons, modal and receipts"),
    case("seh-deploy-adapter", "adapter", "tests/test_mcel_slh_seh_deployment_ui.py::test_local_and_network_deploy_plans_use_selected_slh_without_relay", "seh-deployment-deploy seh-deployment-undeploy", "correct target, plan-only verification and mutation controls"),
    case("seh-undeploy-adapter", "adapter", "tests/test_mcel_slh_seh_deployment_ui.py::test_undeploy_dispatches_to_lms_without_blocking_on_preflight", "seh-deployment-undeploy", "LMS receives undeploy without UI-side false preflight"),
    case("seh-controller-state-isolated", "isolated-effect", "tests/test_slh_seh_coolify_management_backend.py::test_registration_and_selection_use_verified_mother_and_never_expose_token", "seh-deployment-register seh-deployment-select", "temporary Mother authority accepts registration and selection without exposing secrets"),
    case("seh-undeploy-state-isolated", "isolated-effect", "tests/test_seh_canonical_undeploy.py::test_undeploy_deletes_only_owned_uuid_and_keeps_tombstone_identity", "seh-deployment-undeploy", "fixture-backed owned undeploy leaves canonical tombstone"),
    case("seh-deploy-harness-simulated", "adapter", "tests/test_seh_two_action_harness.py::test_deploy_creates_once_waits_for_build_and_activates", "seh-deployment-deploy", "harness enforces create/build/activation ordering against mocked Coolify"),
    case("network-disconnect-adapter", "adapter", "tests/test_mcel_slh_network_connection_integration.py::test_first_connection_prompts_before_signing_then_pins_and_authenticates", "disconnect-network-slh", "authenticated network session can be disconnected"),
    case("network-disconnect-isolated", "isolated-effect", "tests/test_mcel_slh_network_connection_integration.py::test_first_connection_prompts_before_signing_then_pins_and_authenticates", "disconnect-network-slh", "fixture-backed network transport revokes its session"),
    case("seh-controllers-ui", "ui", "tests/test_mcel_slh_seh_deployment_ui.py::test_browser_deployment_controls_are_selected_target_scoped", "seh-deployment-register seh-deployment-select", "controller forms are present on correct target; submission not asserted"),
    case("seh-controllers-adapter", "adapter", "tests/test_mcel_slh_seh_deployment_ui.py::test_local_and_network_deploy_plans_use_selected_slh_without_relay", "seh-deployment-register seh-deployment-select", "controller registration and selection are routed to LMS"),
    case("seh-public-health-ui", "ui", "tests/test_mcel_operation_notebook_outcome_reactivity.py::test_undeploy_eligibility_tracks_authoritative_deployment_phase_in_place", "read-seh-deployment", "deployment phases update Undeploy eligibility without remounting"),
    # Certification closure: asserting submission and state reconciliation, not merely mounted forms.
    case("remote-seh-disconnect-ui-execution", "ui", "tests/test_mcel_slh_certification_closure.py::test_remote_seh_disconnect_ui_revokes_ticket_and_protected_projection", "disconnect-remote-seh", "browser disconnects the exact remote SEH ticket and removes protected projection"),
    case("remote-seh-session-real-isolated", "isolated-effect", "tests/test_seh_remote_admin_backend.py::test_admin_login_and_identity_read_through_opaque_relay", "connect-remote-seh disconnect-remote-seh", "real disposable ORG-SLH TLS and opaque relay authenticate, read, revoke and reject reused session"),
    case("seh-connection-startstoprestart-ui-execution", "ui", "tests/test_mcel_slh_certification_closure.py::test_seh_connection_start_stop_restart_ui_reconciles_authoritative_state", "seh-connection-start seh-connection-stop seh-connection-restart", "browser submits three connection operations and reconciles each selected target"),
    case("seh-connection-startstoprestart-http-isolated", "isolated-effect", "tests/test_seh_remote_admin_backend.py::test_org_slh_admin_outbound_lifecycle_persists_and_does_not_stop_org", "seh-connection-start seh-connection-stop seh-connection-restart", "disposable ORG-SLH TLS HTTP enforces admin, persisted stop/start/restart, and leaves organization running"),
    case("seh-controller-registerselect-ui-execution", "ui", "tests/test_mcel_slh_certification_closure.py::test_seh_controller_register_select_ui_persists_in_authoritative_inspection", "seh-deployment-register seh-deployment-select", "browser posts real register/select forms on local and network targets, refreshes controller inventory and selection"),
    case("seh-deploy-canonical-disposable-execution", "isolated-effect", "tests/test_seh_canonical_deploy_ui_harness.py::test_verified_undeploy_plan_and_explicit_deploy_use_exact_existing_harness", "seh-deployment-deploy", "real guarded harness on disposable canonical state with simulated Coolify, verified replacement and signed-activation contract; not evidence of production runtime"),
)

# A passing form-presence test is insufficient: each previously missing facet
# requires its particular execution test, even if other UI tests are green.
MANDATORY_CASES = {
    ("intent:connect-remote-seh", "isolated-effect"): {"remote-seh-session-real-isolated"},
    ("intent:disconnect-remote-seh", "ui"): {"remote-seh-disconnect-ui-execution"},
    ("intent:disconnect-remote-seh", "isolated-effect"): {"remote-seh-session-real-isolated"},
    ("intent:seh-connection-start", "ui"): {"seh-connection-startstoprestart-ui-execution"},
    ("intent:seh-connection-start", "isolated-effect"): {"seh-connection-startstoprestart-http-isolated"},
    ("intent:seh-connection-stop", "isolated-effect"): {"seh-connection-startstoprestart-http-isolated"},
    ("intent:seh-connection-restart", "ui"): {"seh-connection-startstoprestart-ui-execution"},
    ("intent:seh-connection-restart", "isolated-effect"): {"seh-connection-startstoprestart-http-isolated"},
    ("intent:seh-deployment-register", "ui"): {"seh-controller-registerselect-ui-execution"},
    ("intent:seh-deployment-select", "ui"): {"seh-controller-registerselect-ui-execution"},
    ("intent:seh-deployment-deploy", "isolated-effect"): {"seh-deploy-canonical-disposable-execution"},
}


def required_lanes(intent: dict) -> set[str]:
    required = {"ui"}
    if intent.get("risk") == "consequential":
        required.update(("adapter", "isolated-effect"))
    return required


def declared_intents() -> list[dict]:
    ir, _ = compile_slh_application()
    return sorted(ir["intents"], key=lambda item: item["id"])


def inventory() -> dict:
    declared = declared_intents()
    current = {intent["id"] for intent in declared}
    for record in CASES:
        source_path, *selectors = record.node.split("::")
        source_file = ROOT / source_path
        if not source_file.is_file():
            raise RuntimeError("FUNCTIONAL_TEST_SOURCE_MISSING:" + record.node)
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=source_path)
        named = {node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        if selectors and any(selector.split("[", 1)[0] not in named for selector in selectors):
            raise RuntimeError("FUNCTIONAL_TEST_NODE_MISSING:" + record.node)
        for intent_id in record.intents:
            if intent_id not in current:
                raise RuntimeError("STALE_FUNCTIONAL_EVIDENCE:" + intent_id)
    case_map = {item.id: item for item in CASES}
    if len(case_map) != len(CASES):
        raise RuntimeError("DUPLICATE_FUNCTIONAL_CASE_ID")
    return {"schema": "mcel.slh.full-functional.v1",
            "source": "compiled-semantic-ir", "declared_count": len(declared),
            "declared": declared, "cases": CASES}


def source_fingerprint() -> str:
    """Invalidate resumable evidence when code or an asserted test changes."""
    paths = {Path(__file__).resolve(),
             ROOT / "mcel_apps/slh/application.js",
             ROOT / "main_computer/mcel_slh_application.py",
             ROOT / "main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js"}
    paths.update((ROOT / "main_computer/slh").rglob("*.py"))
    paths.update((ROOT / "main_computer").glob("mcel_slh*.py"))
    paths.update((ROOT / "main_computer/web/applications/scripts").glob("mcel-semantic-slh*.js"))
    paths.update(ROOT / item.node.split("::", 1)[0] for item in CASES)
    # Shadow evidence must be invalidated if the recorder, test fixture, or
    # diagnostic runner changes. A resume cannot reuse a different oracle.
    paths.update(ROOT / name for name in (
        "tools/mcel_slh_functional_shadow.py",
        "main_computer/web/applications/scripts/mcel-reactivity-shadow-plugin.js",
        "main_computer/web/applications/scripts/mcel-diagnostic-plugins.js",
        "tests/test_mcel_slh_lifecycle_disable_enablement_oracle.py",
    ))
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_case(item: Case, *, timeout: int) -> dict:
    """Run in a bounded process group so a stalled browser cannot hang the battery."""
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", item.node]
    start = time.monotonic()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    # A file, not PIPE: browser and service descendants may inherit stdout.
    # Killing the pytest parent alone must never block Python in communicate().
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, creationflags=flags,
                                   start_new_session=(os.name != "nt"))
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                # Windows: /T terminates spawned Chromium and worker descendants.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            exit_code = None
        log.seek(0)
        message = log.read()[-2200:]
        if timed_out:
            return {"status": "UNVERIFIED", "reason": "CASE_TIMEOUT", "seconds": round(time.monotonic()-start, 2),
                    "output_tail": message}
        skipped = (exit_code == 5 or " skipped" in message)
        status = "NOT_TESTED" if skipped else ("PASS" if exit_code == 0 else "FAIL")
        return {"status": status, "exit_code": exit_code,
                "seconds": round(time.monotonic()-start, 2), "output_tail": message}



def run_shadow_lane(*, destination: Path, timeout: int) -> dict:
    """Isolate mocked-browser diagnostics in their own bounded process group.

    Never reuse an old evidence file after a failed or timed-out invocation.
    The reported status comes from the diagnostic verdict, not subprocess
    exit code or the mere existence of a JSON report.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    command = [sys.executable, "-m", "tools.mcel_slh_functional_shadow",
               "--output", str(destination)]
    start = time.monotonic()
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as log:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, creationflags=flags,
                                   start_new_session=(os.name != "nt"))
        timed_out = False
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=10, check=False)
            else:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()
            exit_code = None
        log.seek(0)
        output_tail = log.read()[-1500:]
    result = {"status": "UNVERIFIED", "coverageStatus": "INCOMPLETE",
              "selectiveSchedulingCertified": False,
              "seconds": round(time.monotonic()-start, 2), "exit_code": exit_code,
              "output_tail": output_tail, "report": str(destination)}
    if timed_out:
        result["reason"] = "SHADOW_TIMEOUT"
        return result
    if not destination.is_file():
        result["reason"] = "SHADOW_EVIDENCE_MISSING"
        return result
    try:
        evidence = json.loads(destination.read_text(encoding="utf-8"))
        if evidence.get("schema") != "mcel.slh.functional-shadow.v1":
            raise ValueError("unexpected shadow evidence schema")
        verdict = evidence["evaluation"]
        result.update({"status": verdict["status"],
                       "coverageStatus": verdict.get("coverageStatus", "INCOMPLETE"),
                       "selectiveSchedulingCertified":False,
                       "failures": verdict.get("failures", []),
                       "coverageBlockers": verdict.get("coverageBlockers", []),
                       "metrics": verdict.get("metrics", {}),
                       "stages": [{"phase": s["phase"], "functionalPass": s["functionalPass"],
                                   "deltaNotifications": s["deltaNotifications"],
                                   "deltaPredictedSkips": s.get("deltaCounters",{}).get("predictedSkips",0),
                                   "deltaContradictions": s.get("deltaCounters",{}).get("contradictions",0),
                                   "deltaDOMMutations": s.get("deltaCounters",{}).get("observedMutations",0),
                                   "deltaPropertyChanges": s.get("deltaCounters",{}).get("propertyChanges",0)}
                                  for s in evidence.get("stages", [])]})
        if (exit_code != 0 and result["status"] == "PASS_OBSERVATION") or (
                exit_code == 0 and result["status"] != "PASS_OBSERVATION"):
            result["status"] = "FAIL"
            result["reason"] = "SHADOW_EXIT_VERDICT_MISMATCH"
    except (ValueError, KeyError, TypeError) as exc:
        result["status"] = "FAIL"
        result["reason"] = "SHADOW_EVIDENCE_INVALID:" + str(exc)
    return result


def report_for(results: dict[str, dict], *, mode: str) -> dict:
    data = inventory()
    rows = []
    for intent in data["declared"]:
        intent_id = intent["id"]
        required = required_lanes(intent)
        lanes = {}
        for lane in sorted(required):
            supporting = [item for item in CASES if intent_id in item.intents and item.lane == lane]
            # A fixture that checks only the existence of a form is insufficient
            # to certify its user-facing operation.
            mandatory = MANDATORY_CASES.get((intent_id, lane), set())
            # Never satisfy a required facet with an unrelated passing case.
            if not mandatory.issubset({item.id for item in supporting}):
                supporting = []
            observed = [results[item.id]["status"] for item in supporting if item.id in results]
            if any(x == "FAIL" for x in observed): status = "FAIL"
            elif any(x == "UNVERIFIED" for x in observed): status = "UNVERIFIED"
            elif observed and all(x == "PASS" for x in observed) and mandatory.issubset({item.id for item in supporting if results.get(item.id, {}).get("status") == "PASS"}): status = "PASS"
            else: status = "NOT_TESTED"
            lanes[lane] = {"status": status, "evidence": [item.id for item in supporting]}
        statuses = {v["status"] for v in lanes.values()}
        final = ("FAIL" if "FAIL" in statuses else
                 "UNVERIFIED" if "UNVERIFIED" in statuses else
                 "PASS" if statuses == {"PASS"} else "NOT_TESTED")
        rows.append({"intent": intent_id, "operationKind": intent.get("operationKind"),
                     "risk": intent.get("risk"), "status": final, "lanes": lanes})
    counts = {status: sum(row["status"] == status for row in rows)
              for status in ("PASS", "FAIL", "NOT_TESTED", "UNVERIFIED")}
    overall = ("FAIL" if counts["FAIL"] else "INCOMPLETE" if counts["NOT_TESTED"] or counts["UNVERIFIED"]
               else "PASS_ISOLATED_FUNCTIONAL")
    return {"schema": data["schema"], "mode": mode, "source": data["source"],
            "declared_intents": data["declared_count"], "status": overall, "counts": counts,
            "intents": rows,
            "cases": [{"id": item.id, "lane": item.lane, "test": item.node,
                       "assertion": item.proves, "result": results.get(item.id, {"status":"NOT_RUN"})}
                      for item in CASES],
            "live": {"status": "NOT_RUN", "reason": "Live host and disposable mutating fixtures are separate acceptance lanes"},
            "shadow": {"status": "NOT_RUN", "coverageStatus": "NOT_RUN",
                       "selectiveSchedulingCertified": False},
            "safety": {"production_mutations": "NEVER_DISPATCHED", "test_modes": "mocked-browser-and-isolated-backend-fixtures",
                       "screenshots_committed": False}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("inventory", "isolated"), default="isolated")
    parser.add_argument("--case", action="append", default=[], help="run selected case ID(s); can repeat")
    parser.add_argument("--profile", choices=("smoke", "full"), default="full",
                        help="smoke selects fast representative cases; full attempts all curated cases")
    parser.add_argument("--timeout", type=int, default=45, help="timeout per isolated test case, seconds")
    parser.add_argument("--max-cases", type=int, default=0,
                        help="run at most this many new cases; 0 means no batch limit")
    parser.add_argument("--resume", action="store_true", help="resume only when source fingerprint matches")
    parser.add_argument("--shadow", choices=("on", "off"), default="on",
                        help="run independent mocked SLH lifecycle with shadow oracle (default: on)")
    parser.add_argument("--shadow-timeout", type=int, default=120,
                        help="bounded timeout for companion browser diagnostic, seconds")
    parser.add_argument("--no-supervisor", action="store_true",
                        help="debug full battery in one process (not recommended)")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--live-host-url", help="Explicit loopback host for separate read-only acceptance")
    parser.add_argument("--live-protected-reads", action="store_true", help="Opt into protected reads")
    parser.add_argument("--live-admin-id", help="Explicit administrator to select for protected reads")
    args = parser.parse_args(argv)
    if args.max_cases < 0:
        parser.error("--max-cases must be nonnegative")
    if args.shadow_timeout < 5 or args.shadow_timeout > 900:
        parser.error("--shadow-timeout must be between 5 and 900 seconds")
    if args.timeout < 5 or args.timeout > 900:
        parser.error("timeout must be between 5 and 900 seconds")
    if args.live_protected_reads and (not args.live_host_url or not args.live_admin_id):
        parser.error("--live-protected-reads requires --live-host-url and --live-admin-id")
    if args.live_admin_id and not args.live_protected_reads:
        parser.error("--live-admin-id requires --live-protected-reads")
    case_names = {item.id for item in CASES}
    if unknown := (set(args.case) - case_names):
        parser.error("unknown case(s): " + ", ".join(sorted(unknown)))
    try:
        inventory()
    except Exception as exc:
        print("SLH_FUNCTIONAL_INVENTORY_ERROR", exc)
        return 2
    results = {}
    target = args.report if args.report.is_absolute() else ROOT / args.report
    target.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = source_fingerprint()
    if args.resume and target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if previous.get("source_fingerprint") != fingerprint:
            print("SLH_FUNCTIONAL_RESUME_REFUSED: source or test changed; start a fresh report")
            return 2
        results.update({item["id"]: item["result"] for item in previous.get("cases", [])
                        if item.get("result", {}).get("status") not in (None, "NOT_RUN")})
    def checkpoint() -> dict:
        result = report_for(results, mode=args.mode)
        result["source_fingerprint"] = fingerprint
        result["completed_cases"] = len(results)
        result["total_cases"] = len(CASES)
        target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result
    checkpoint()  # Retain a truthful partial report even if execution is interrupted.
    if args.mode == "isolated":
        smoke = {"identity-ui", "identity-discovery", "organization-notebook-ui",
                 "organization-notebook-adapter", "seh-connection-adapter",
                 "seh-deploy-adapter", "seh-observation-ui"}
        priority = {"isolated-effect": 0, "adapter": 1, "ui": 2, "contract": 3}
        ordered = sorted(CASES, key=lambda item: (priority[item.lane], item.id))
        ordered = [item for item in ordered if (not args.case or item.id in args.case)
                   and (args.case or args.profile != "smoke" or item.id in smoke)]
        starting_count = len(results)
        if args.profile == "full" and not args.case and not args.no_supervisor:
            # Each behavioral case runs under a fresh supervisor process. A
            # stalled descendant cannot block the rest of the acceptance run.
            for item in ordered:
                if item.id in results:
                    continue
                if args.max_cases and len(results) - starting_count >= args.max_cases:
                    break
                cmd = [sys.executable, "-m", "tools.mcel_slh_full_functional_acceptance",
                       "--mode", "isolated", "--profile", "full", "--case", item.id,
                       "--resume", "--timeout", str(args.timeout), "--report", str(target),
                       "--shadow", "off"]
                with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as log:
                    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    child = subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                             creationflags=flags, start_new_session=(os.name != "nt"))
                    timed_out = False
                    try:
                        child.wait(timeout=args.timeout + 20)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=10, check=False)
                        else:
                            try:
                                os.killpg(child.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            child.kill()
                if target.exists():
                    interim = json.loads(target.read_text(encoding="utf-8"))
                    if interim.get("source_fingerprint") != fingerprint:
                        print("SLH_FUNCTIONAL_SOURCE_CHANGED_DURING_RUN")
                        return 2
                    results.update({entry["id"]: entry["result"] for entry in interim["cases"]
                                    if entry.get("result", {}).get("status") not in ("NOT_RUN", None)})
                if item.id not in results:
                    results[item.id] = {"status": "UNVERIFIED",
                                        "reason": "SUPERVISED_CASE_TIMEOUT" if timed_out else "SUPERVISED_CASE_NO_RESULT"}
                checkpoint()
                print("SLH_FUNCTIONAL", item.id, results[item.id]["status"], flush=True)
        else:
            by_node: dict[str, dict] = {}
            for item in ordered:
                if item.id in results:
                    by_node.setdefault(item.node, results[item.id])
                    continue
                if args.max_cases and len(results) - starting_count >= args.max_cases:
                    break
                if item.node in by_node:
                    results[item.id] = {**by_node[item.node], "reused_from_previous_execution": True}
                else:
                    results[item.id] = run_case(item, timeout=args.timeout)
                    by_node[item.node] = results[item.id]
                checkpoint()
                print("SLH_FUNCTIONAL", item.id, results[item.id]["status"], flush=True)
    output = report_for(results, mode=args.mode)
    if args.mode == "isolated" and args.shadow == "on":
        shadow_path = target.with_name(target.stem + "-shadow.json")
        output["shadow"] = run_shadow_lane(destination=shadow_path, timeout=args.shadow_timeout)
        for phase in output["shadow"].get("stages", []):
            print("SLH_SHADOW_PHASE", phase["phase"],
                  "PASS" if phase["functionalPass"] and not phase["deltaContradictions"] else "FAIL",
                  "notifications=" + str(phase["deltaNotifications"]),
                  "predictedSkips=" + str(phase["deltaPredictedSkips"]),
                  "domMutations=" + str(phase["deltaDOMMutations"]),
                  "propertyChanges=" + str(phase["deltaPropertyChanges"]),
                  "contradictions=" + str(phase["deltaContradictions"]), flush=True)
        print("SLH_FUNCTIONAL_SHADOW", output["shadow"]["status"],
              "coverage=" + output["shadow"]["coverageStatus"],
              "report:", str(shadow_path), flush=True)
    else:
        output["shadow"] = {"status":"NOT_REQUESTED", "coverageStatus":"NOT_RUN",
                            "selectiveSchedulingCertified":False}
    output["combined_status"] = (
        "FAIL" if output["status"] == "FAIL" or output["shadow"]["status"] == "FAIL"
        else "INCOMPLETE" if output["status"] != "PASS_ISOLATED_FUNCTIONAL" or
             output["shadow"]["status"] == "UNVERIFIED"
        else "PASS_FUNCTIONAL_WITH_SHADOW_OBSERVATION" if output["shadow"]["status"] == "PASS_OBSERVATION"
        else output["status"])
    output["source_fingerprint"] = fingerprint
    output["completed_cases"] = len(results)
    output["total_cases"] = len(CASES)
    if args.live_host_url:
        live_report = target.parent / "slh-live-read-only.json"
        command = [sys.executable, "-m", "tools.mcel_functional_live_acceptance",
                   "--host-url", args.live_host_url, "--app", "slh", "--report", str(live_report)]
        if args.live_protected_reads:
            command += ["--protected-reads", "--admin-id", args.live_admin_id]
        try:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150)
            status = "INCOMPLETE"
            if live_report.exists():
                parsed = json.loads(live_report.read_text(encoding="utf-8"))
                status = parsed.get("apps", {}).get("slh", {}).get("status", "INCOMPLETE")
            output["live"] = {"status": status, "report": str(live_report),
                              "mode": "protected-read-only" if args.live_protected_reads else "public-read-only",
                              "exit_code": result.returncode, "output_tail": (result.stdout + result.stderr)[-1000:]}
        except (subprocess.TimeoutExpired, OSError) as exc:
            output["live"] = {"status":"INCOMPLETE", "reason": type(exc).__name__}
    target.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("SLH_FUNCTIONAL_OVERALL", output["status"], output["counts"],
          f"cases={len(results)}/{len(CASES)}", "report:", str(target), flush=True)
    if output["live"]["status"] == "INCOMPLETE" and args.live_host_url: return 3
    if output["shadow"]["status"] == "FAIL": return 1
    if output["shadow"]["status"] == "UNVERIFIED": return 3
    if any(r.get("status") == "FAIL" for r in results.values()): return 1
    if any(r.get("status") == "UNVERIFIED" for r in results.values()): return 3
    return 0 if output["status"] == "PASS_ISOLATED_FUNCTIONAL" else 4


if __name__ == "__main__":
    raise SystemExit(main())
