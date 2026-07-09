from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APPS = ROOT / "main_computer" / "web" / "applications" / "apps"


def read_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def read_app(name: str) -> str:
    return (APPS / name).read_text(encoding="utf-8")


def test_23c_safe_recovery_planner_contracts_outcomes_and_no_retry_boundary_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet23cSafeRecoveryPlanner",
        "mcelWallet23cSafeRecoveryPlanner.v1",
        "mcelExecutionRecoveryPlan.v1",
        "mcelExecutionRecoveryRequirement.v1",
        "mcelExecutionRecoveryReceipt.v1",
        'unlockVersion: "23C-MCEL"',
        "safe-recovery-planner-no-retry",
        "executionRecoveryPlan",
        "executionRecoveryRequirements",
        "executionRecoveryReceipt",
        "duplicateSendSafetyAuthority",
        "mcelWallet21dRetryRecoverySafety.v1",
        "23C plans safe recovery; it never retries",
    ]
    for marker in markers:
        assert marker in lab

    outcomes = [
        "no-recovery-needed",
        "recovery-blocked-pending-transaction",
        "recovery-blocked-same-envelope",
        "recovery-blocked-policy-revoked",
        "recovery-blocked-target-changed",
        "recovery-blocked-account-changed",
        "recovery-blocked-chain-changed",
        "fresh-draft-required",
        "fresh-signed-intent-required",
        "fresh-policy-decision-required",
        "fresh-target-binding-required",
        "fresh-send-envelope-required",
        "recovery-plan-ready",
    ]
    for outcome in outcomes:
        assert outcome in lab

    no_retry_markers = [
        "canSendRecovery: false",
        "retryAllowed: false",
        "automaticRetryAllowed: false",
        "sameEnvelopeRetryAllowed: false",
        "sameEnvelopeRetryBlocked",
        "providerMutationRequested: false",
        "mutationExecuted: false",
        "canRetryAutomatically: false",
    ]
    for marker in no_retry_markers:
        assert marker in lab

    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_23c_is_after_23b_and_bound_into_runtime_lab_and_code_studio() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    idx_23b = lab.index("function mcelWallet23bTransactionObserver")
    idx_23c = lab.index("function mcelWallet23cSafeRecoveryPlanner")
    idx_23d = lab.index("function mcelWallet23dExecutionAuditExport")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_23b < idx_23c < idx_23d < idx_boundary

    boundary_markers = [
        "boundary.wallet23cSafeRecoveryPlanner = mcelWallet23cSafeRecoveryPlanner(boundary",
        "boundary.walletSafeRecoveryPlanner = boundary.wallet23cSafeRecoveryPlanner",
        "wallet23cSafeRecoveryPlanner: null",
        "walletSafeRecoveryPlanner: null",
        "instance.runtime.wallet23cSafeRecoveryPlanner = boundary.wallet23cSafeRecoveryPlanner || null",
        "instance.runtime.walletSafeRecoveryPlanner = boundary.walletSafeRecoveryPlanner || boundary.wallet23cSafeRecoveryPlanner || null",
        '"runtime.wallet23cSafeRecoveryPlanner"',
        '"runtime.walletSafeRecoveryPlanner"',
    ]
    for marker in boundary_markers:
        assert marker in lab

    render_markers = [
        'const safeRecoveryPlannerSlot = document.querySelector("#mcel-23c-wallet-safe-recovery-planner")',
        'const visible23cRecoveryPlannerSlot = document.querySelector("#mcel-23c-wallet-recovery-planner-visible-status")',
        "mcel-23c-wallet-safe-recovery-planner-view",
        "wallet23cSafeRecoveryPlanner",
        "executionRecoveryPlan",
        "executionRecoveryRequirements",
        "executionRecoveryReceipt",
    ]
    for marker in render_markers:
        assert marker in lab

    html_markers = [
        'data-mcel-23cd-recovery-audit="true"',
        "23C recovery planner",
        "23C safe recovery planner",
        'id="mcel-23c-wallet-recovery-planner-visible-status"',
        'id="mcel-23c-wallet-safe-recovery-planner"',
    ]
    for marker in html_markers:
        assert marker in app

    studio_markers = [
        "wallet23cSafeRecoveryPlanner",
        "wallet23cRecoveryPlannerStatus",
        "wallet23cSameEnvelopeRetryBlocked",
        "wallet23cCanRetryAutomatically",
        "safeRecoveryPlannerActive",
        "23A/23B/23C/23D route, finality, recovery, and audit proof",
    ]
    for marker in studio_markers:
        assert marker in studio

    recovery_defs = lab[
        lab.index("function mcelWallet23cSafeRecoveryPlanner") : lab.index("function mcelWalletToolCommitBoundary")
    ]
    assert "eth_sendTransaction" not in recovery_defs
    assert "wallet_switchEthereumChain" not in recovery_defs
    assert "wallet_addEthereumChain" not in recovery_defs
