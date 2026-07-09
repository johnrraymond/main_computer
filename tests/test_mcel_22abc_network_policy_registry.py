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


def test_22abc_policy_registry_import_surface_and_target_binding_contracts_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet22aPolicyProfileFromCurrentEvidence",
        "mcelWallet22aNetworkExecutionPolicyRegistry",
        "mcelNetworkExecutionPolicyRegistry.v1",
        "mcelNetworkExecutionPolicyProfile.v1",
        "mcelNetworkExecutionPolicyDecision.v1",
        "mcelNetworkExecutionPolicyReceipt.v1",
        'unlockVersion: "22A-MCEL"',
        "configurable-network-policy-registry",
        "explicit-configurable-policy-profile",
        "send-eligible-by-policy",
        "send-blocked-by-policy",
        "policy-missing",
        "chain-not-allowed",
        "target-not-allowed",
        "contract-kind-not-allowed",
        "release-not-executable-on-this-chain",
        "simulation-required",
        "signed-intent-required",
        "fresh-probes-required",
        "mcelWallet22bPolicyProfileImportSurface",
        "mcelNetworkExecutionPolicyProfileImportEnvelope.v1",
        "mcelNetworkExecutionPolicyProfileImportSurface.v1",
        'unlockVersion: "22B-MCEL"',
        "policy-profile-editor-import-surface",
        "policy-profile-import-ready",
        "policy-profile-import-blocked",
        "jsonImportSupported: true",
        "manualEditSupported: true",
        "noSilentPolicyActivation: true",
        "mcelWallet22cTargetRegistryContractBinding",
        "mcelNetworkTargetRegistry.v1",
        "mcelNetworkContractBinding.v1",
        "mcelNetworkTargetRegistryContractBinding.v1",
        'unlockVersion: "22C-MCEL"',
        "per-network-target-registry-contract-binding",
        "target-contract-binding-matched",
        "target-contract-binding-blocked",
        "target-binding-not-found",
    ]
    for marker in markers:
        assert marker in lab

    assert "eth_sendTransaction" in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_22abc_is_ordered_after_21f_and_bound_into_boundary_runtime_and_send_gate() -> None:
    lab = read_script("mcel-lab.js")

    idx_21f = lab.index("function mcelWallet21fRelockResetLifecycle")
    idx_22a = lab.index("function mcelWallet22aPolicyProfileFromCurrentEvidence")
    idx_22b = lab.index("function mcelWallet22bPolicyProfileImportSurface")
    idx_22c = lab.index("function mcelWallet22cTargetRegistryContractBinding")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_21f < idx_22a < idx_22b < idx_22c < idx_boundary

    markers = [
        "boundary.wallet22aNetworkExecutionPolicyRegistry = mcelWallet22aNetworkExecutionPolicyRegistry(boundary",
        "boundary.walletNetworkExecutionPolicyRegistry = boundary.wallet22aNetworkExecutionPolicyRegistry",
        "boundary.wallet22aNetworkExecutionPolicyDecision = mcelWallet22aNetworkExecutionPolicyDecision(boundary",
        "boundary.walletNetworkExecutionPolicyDecision = boundary.wallet22aNetworkExecutionPolicyDecision",
        "boundary.wallet22aNetworkExecutionPolicyReceipt = mcelWallet22aNetworkExecutionPolicyReceipt(boundary)",
        "boundary.wallet22bPolicyProfileImportSurface = mcelWallet22bPolicyProfileImportSurface(boundary",
        "boundary.walletPolicyProfileImportSurface = boundary.wallet22bPolicyProfileImportSurface",
        "boundary.wallet22cTargetRegistryContractBinding = mcelWallet22cTargetRegistryContractBinding(boundary",
        "boundary.walletTargetRegistryContractBinding = boundary.wallet22cTargetRegistryContractBinding",
        "boundary.canSend = boundary.wallet21aPolicyBoundSendGate?.canSend === true && boundary.wallet21dRetryRecoverySafety?.sendRequestAllowed === true && boundary.wallet22aNetworkExecutionPolicyDecision?.allowSendByPolicy === true && boundary.wallet22cTargetRegistryContractBinding?.targetAllowed === true",
        "policyDecision.allowSendByPolicy === true",
        "targetBinding.targetAllowed === true",
        "wallet22aNetworkExecutionPolicyRegistry: null",
        "walletNetworkExecutionPolicyRegistry: null",
        "wallet22bPolicyProfileImportSurface: null",
        "walletPolicyProfileImportSurface: null",
        "wallet22cTargetRegistryContractBinding: null",
        "walletTargetRegistryContractBinding: null",
        '"runtime.wallet22aNetworkExecutionPolicyRegistry"',
        '"runtime.wallet22aNetworkExecutionPolicyDecision"',
        '"runtime.wallet22bPolicyProfileImportSurface"',
        '"runtime.wallet22cTargetRegistryContractBinding"',
    ]
    for marker in markers:
        assert marker in lab


def test_22abc_visible_wallet_board_receipt_copy_and_code_studio_alignment_exist() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-22abc-network-policy-registry="true"',
        "22A policy decision",
        "22B profile import",
        "22C target binding",
        'id="mcel-22a-wallet-network-policy-decision"',
        'id="mcel-22b-wallet-policy-profile-import"',
        'id="mcel-22c-wallet-target-registry-binding"',
        'id="mcel-22a-wallet-policy-decision-visible-status"',
        'id="mcel-22b-wallet-policy-import-visible-status"',
        'id="mcel-22c-wallet-target-binding-visible-status"',
        "22A/B/C formalize configurable network policy, importable profiles, and per-network target bindings.",
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'const networkPolicyDecisionSlot = document.querySelector("#mcel-22a-wallet-network-policy-decision")',
        'const policyProfileImportSlot = document.querySelector("#mcel-22b-wallet-policy-profile-import")',
        'const targetRegistryBindingSlot = document.querySelector("#mcel-22c-wallet-target-registry-binding")',
        "mcel-22a-wallet-network-policy-decision-view",
        "mcel-22b-wallet-policy-profile-import-view",
        "mcel-22c-wallet-target-registry-binding-view",
        "22A says",
        "22B says",
        "22C says",
        "wallet22aNetworkExecutionPolicyRegistry: boundary.wallet22aNetworkExecutionPolicyRegistry || boundary.walletNetworkExecutionPolicyRegistry || {}",
        "wallet22bPolicyProfileImportSurface: boundary.wallet22bPolicyProfileImportSurface || boundary.walletPolicyProfileImportSurface || {}",
        "wallet22cTargetRegistryContractBinding: boundary.wallet22cTargetRegistryContractBinding || boundary.walletTargetRegistryContractBinding || {}",
    ]
    for marker in render_markers:
        assert marker in lab

    studio_markers = [
        "wallet22aNetworkExecutionPolicyRegistry",
        "wallet22aNetworkExecutionPolicyDecision",
        "wallet22aNetworkExecutionPolicyStatus",
        "wallet22aNetworkPolicyMatched",
        "wallet22aAllowSendByPolicy",
        "wallet22bPolicyProfileImportSurface",
        "wallet22bPolicyProfileImportStatus",
        "wallet22bCanImportProfile",
        "wallet22cTargetRegistryContractBinding",
        "wallet22cTargetBindingStatus",
        "wallet22cTargetAllowed",
        "22A/22B/22C configurable network policy registry, profile import surface, and target binding are visible in Code Studio.",
    ]
    for marker in studio_markers:
        assert marker in studio
