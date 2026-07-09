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


def test_22def_policy_hardening_activation_and_readiness_contracts_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet22dProductionPolicyHardening",
        "mcelWallet22dProductionPolicyHardening.v1",
        "mcelProductionPolicyHardeningEnvelope.v1",
        "mcelProductionPolicyAuditReceipt.v1",
        'unlockVersion: "22D-MCEL"',
        "production-policy-hardening-audit-receipts",
        "production-policy-hardening-blocked",
        "production-policy-hardened",
        "wildcard-policy-profile-not-production-safe",
        "policy-import-allows-silent-activation",
        "automatic-retry-not-production-safe",
        "mcelWallet22ePolicyActivationRevocationLifecycle",
        "mcelWallet22ePolicyActivationRevocationLifecycle.v1",
        "mcelNetworkPolicyActivationEnvelope.v1",
        "mcelNetworkPolicyRevocationEnvelope.v1",
        "mcelNetworkPolicyLifecycleReceipt.v1",
        'unlockVersion: "22E-MCEL"',
        "policy-activation-revocation-lifecycle",
        "policy-profile-active",
        "policy-profile-revoked",
        "policy-activation-review-required",
        "explicit-policy-activation-request-required",
        "operator-policy-activation-approval-required",
        "mcelWallet22fPolicyBoundExecutionReadinessSurface",
        "mcelWallet22fPolicyBoundExecutionReadinessSurface.v1",
        "mcelNetworkExecutionReadinessEnvelope.v1",
        "mcelNetworkExecutionReadinessReceipt.v1",
        'unlockVersion: "22F-MCEL"',
        "final-policy-bound-execution-readiness-surface",
        "policy-bound-execution-ready",
        "policy-bound-execution-readiness-blocked",
        "readyFor23aMultiNetworkExecutionPolish",
        "23A-real-multi-network-execution-polish",
    ]
    for marker in markers:
        assert marker in lab

    assert "eth_sendTransaction" in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_22def_is_ordered_after_22abc_and_bound_into_boundary_runtime() -> None:
    lab = read_script("mcel-lab.js")

    idx_22a = lab.index("function mcelWallet22aPolicyProfileFromCurrentEvidence")
    idx_22b = lab.index("function mcelWallet22bPolicyProfileImportSurface")
    idx_22c = lab.index("function mcelWallet22cTargetRegistryContractBinding")
    idx_22d = lab.index("function mcelWallet22dProductionPolicyHardening")
    idx_22e = lab.index("function mcelWallet22ePolicyActivationRevocationLifecycle")
    idx_22f = lab.index("function mcelWallet22fPolicyBoundExecutionReadinessSurface")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_22a < idx_22b < idx_22c < idx_22d < idx_22e < idx_22f < idx_boundary

    markers = [
        "boundary.wallet22dProductionPolicyHardening = mcelWallet22dProductionPolicyHardening(boundary",
        "boundary.walletProductionPolicyHardening = boundary.wallet22dProductionPolicyHardening",
        "boundary.wallet22ePolicyActivationRevocationLifecycle = mcelWallet22ePolicyActivationRevocationLifecycle(boundary",
        "boundary.walletPolicyActivationRevocationLifecycle = boundary.wallet22ePolicyActivationRevocationLifecycle",
        "boundary.wallet22fPolicyBoundExecutionReadinessSurface = mcelWallet22fPolicyBoundExecutionReadinessSurface(boundary",
        "boundary.walletPolicyBoundExecutionReadinessSurface = boundary.wallet22fPolicyBoundExecutionReadinessSurface",
        "boundary.wallet22dProductionPolicyHardening?.productionReady === true",
        "boundary.wallet22ePolicyActivationRevocationLifecycle?.policyActive === true",
        "boundary.wallet22fPolicyBoundExecutionReadinessSurface?.canRequestPolicyBoundSend === true",
        "instance.runtime.wallet22dProductionPolicyHardening = boundary.wallet22dProductionPolicyHardening || null",
        "instance.runtime.wallet22ePolicyActivationRevocationLifecycle = boundary.wallet22ePolicyActivationRevocationLifecycle || null",
        "instance.runtime.wallet22fPolicyBoundExecutionReadinessSurface = boundary.wallet22fPolicyBoundExecutionReadinessSurface || null",
        "wallet22dProductionPolicyHardening: null",
        "walletProductionPolicyHardening: null",
        "wallet22ePolicyActivationRevocationLifecycle: null",
        "walletPolicyActivationRevocationLifecycle: null",
        "wallet22fPolicyBoundExecutionReadinessSurface: null",
        "walletPolicyBoundExecutionReadinessSurface: null",
        '"runtime.wallet22dProductionPolicyHardening"',
        '"runtime.walletProductionPolicyHardening"',
        '"runtime.wallet22ePolicyActivationRevocationLifecycle"',
        '"runtime.walletPolicyActivationRevocationLifecycle"',
        '"runtime.wallet22fPolicyBoundExecutionReadinessSurface"',
        '"runtime.walletPolicyBoundExecutionReadinessSurface"',
    ]
    for marker in markers:
        assert marker in lab


def test_22def_visible_wallet_board_receipts_and_code_studio_alignment_exist() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-22def-policy-hardening-activation-readiness="true"',
        "22D production hardening",
        "22E activation lifecycle",
        "22F execution readiness",
        'id="mcel-22d-wallet-production-policy-hardening"',
        'id="mcel-22e-wallet-policy-activation-lifecycle"',
        'id="mcel-22f-wallet-execution-readiness"',
        'id="mcel-22d-wallet-production-hardening-visible-status"',
        'id="mcel-22e-wallet-activation-lifecycle-visible-status"',
        'id="mcel-22f-wallet-execution-readiness-visible-status"',
        "22D/E/F harden production policy, require explicit activation, and expose final execution readiness.",
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'const productionPolicyHardeningSlot = document.querySelector("#mcel-22d-wallet-production-policy-hardening")',
        'const policyActivationLifecycleSlot = document.querySelector("#mcel-22e-wallet-policy-activation-lifecycle")',
        'const executionReadinessSlot = document.querySelector("#mcel-22f-wallet-execution-readiness")',
        "mcel-22d-wallet-production-policy-hardening-view",
        "mcel-22e-wallet-policy-activation-lifecycle-view",
        "mcel-22f-wallet-execution-readiness-view",
        "22D says",
        "22E says",
        "22F says",
        "wallet22dProductionPolicyHardening: boundary.wallet22dProductionPolicyHardening || boundary.walletProductionPolicyHardening || {}",
        "wallet22ePolicyActivationRevocationLifecycle: boundary.wallet22ePolicyActivationRevocationLifecycle || boundary.walletPolicyActivationRevocationLifecycle || {}",
        "wallet22fPolicyBoundExecutionReadinessSurface: boundary.wallet22fPolicyBoundExecutionReadinessSurface || boundary.walletPolicyBoundExecutionReadinessSurface || {}",
    ]
    for marker in render_markers:
        assert marker in lab

    studio_markers = [
        "wallet22dProductionPolicyHardening",
        "wallet22dProductionPolicyStatus",
        "wallet22dProductionReady",
        "wallet22ePolicyActivationRevocationLifecycle",
        "wallet22ePolicyActivationStatus",
        "wallet22ePolicyActive",
        "wallet22fPolicyBoundExecutionReadinessSurface",
        "wallet22fExecutionReadinessStatus",
        "wallet22fReadyFor23a",
        "22D/22E/22F production hardening, policy activation lifecycle, and final execution readiness are visible in Code Studio.",
    ]
    for marker in studio_markers:
        assert marker in studio
