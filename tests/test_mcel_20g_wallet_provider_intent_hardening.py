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


def test_20g_provider_intent_hardening_layer_exists_without_transaction_send() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet20gOutcomeFlags",
        "mcelWallet20gProviderIntentHardening",
        "mcelWallet20gProviderIntentHardening.v1",
        "mcelWallet20gProviderIntentHardeningEnvelope.v1",
        "mcelWallet20gProviderIntentHardeningReceipt.v1",
        'unlockVersion: "20G-MCEL"',
        "live-provider-intent-signature-hardening",
        "live-signature-hardening-waiting",
        "live-signature-hardened-blocked",
        "live-signature-hardened-current",
        "provider-intent-negative-path-contained",
        "ready-for-separate-21a-policy-bound-send-design",
        "wallet-absent",
        "user-rejected-signature",
        "provider-request-already-pending",
        "wrong-chain",
        "account-changed-during-or-after-prompt",
        "chain-changed-during-or-after-prompt",
        "signature-received-but-stale",
        "signed-intent-not-current",
        "signature-intent-expired",
        "providerTransactionSendBound: false",
        "sendMethod: \"not-bound\"",
        "transactionSigningMethod: \"not-bound\"",
        "broadcastMethod: \"not-bound\"",
        "canSend: false",
        "canSign: false",
        "canBroadcast: false",
        "mutationExecuted: false",
    ]
    for marker in markers:
        assert marker in lab

    dangerous_transaction_methods = [
        "eth_signTransaction",
        "broadcastTransaction",
    ]
    for method in dangerous_transaction_methods:
        assert method not in lab
        assert method not in studio

    assert "eth_sendTransaction" in lab
    assert "function mcelWallet21aPolicyBoundSendGate" in lab
    assert "eth_sendTransaction" not in studio
    assert not re.search(r"\.sendTransaction\s*\(", lab)
    assert not re.search(r"\.sendTransaction\s*\(", studio)


def test_20g_is_ordered_after_20f_and_before_boundary_refresh() -> None:
    lab = read_script("mcel-lab.js")

    idx_20d = lab.index("function mcelWallet20dProviderIntentSignature")
    idx_20e = lab.index("function mcelWallet20eSignedIntentVerification")
    idx_20f = lab.index("function mcelWallet20fPreSendReviewGate")
    idx_20g = lab.index("function mcelWallet20gProviderIntentHardening")
    idx_21a = lab.index("function mcelWallet21aPolicyBoundSendGate")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")

    assert idx_20d < idx_20e < idx_20f < idx_20g < idx_21a < idx_boundary

    block = lab[idx_20g:idx_21a]
    assert "20G hardens real provider intent-signature outcomes before any transaction mutation." in block
    assert "20G receipts wallet-absent, user-rejected, wrong-chain, account-change, chain-change, stale, mismatched, and expired signature outcomes." in block
    assert "20G still does not bind provider transaction send, transaction signing, or broadcast." in block
    assert re.search(r"canSend\s*:\s*false", block)
    assert re.search(r"canSign\s*:\s*false", block)
    assert re.search(r"canBroadcast\s*:\s*false", block)
    assert re.search(r"mutationExecuted\s*:\s*false", block)


def test_20g_boundary_aliases_runtime_ownership_and_receipt_copy_are_declared() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "boundary.wallet20gProviderIntentHardening = mcelWallet20gProviderIntentHardening(boundary)",
        "boundary.walletProviderIntentHardening = boundary.wallet20gProviderIntentHardening",
        "boundary.nextAction = boundary.wallet20gProviderIntentHardening?.nextAction || boundary.nextAction",
        "instance.runtime.wallet20gProviderIntentHardening = commitBoundary.wallet20gProviderIntentHardening || null",
        "instance.runtime.walletProviderIntentHardening = commitBoundary.walletProviderIntentHardening || commitBoundary.wallet20gProviderIntentHardening || null",
        "wallet20gProviderIntentHardening: null",
        "walletProviderIntentHardening: null",
        '"runtime.wallet20gProviderIntentHardening"',
        '"runtime.walletProviderIntentHardening"',
        '"wallet20gProviderIntentHardening"',
        '"walletProviderIntentHardening"',
        "wallet20gProviderIntentHardening: boundary.wallet20gProviderIntentHardening || boundary.walletProviderIntentHardening || {}",
        "walletProviderIntentHardening: boundary.walletProviderIntentHardening || boundary.wallet20gProviderIntentHardening || {}",
    ]
    for marker in markers:
        assert marker in lab


def test_20g_visible_wallet_board_and_render_surfaces_are_present() -> None:
    lab = read_script("mcel-lab.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-20g-wallet-provider-hardening="true"',
        "20G provider hardening",
        'id="mcel-20g-wallet-provider-hardening-visible-status"',
        'id="mcel-20g-wallet-provider-intent-hardening"',
        "20G provider intent hardening is waiting.",
        "21A can request a policy-bound network-agnostic provider send",
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'const providerIntentHardeningSlot = document.querySelector("#mcel-20g-wallet-provider-intent-hardening")',
        'const visible20gProviderHardeningSlot = document.querySelector("#mcel-20g-wallet-provider-hardening-visible-status")',
        "mcel-20g-wallet-provider-intent-hardening-view",
        "20G harden provider intent outcomes",
        "20G says",
        "wallet20gProviderIntentHardening.blockers",
    ]
    for marker in render_markers:
        assert marker in lab


def test_20g_code_studio_summary_receives_matching_fields() -> None:
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "wallet20gProviderIntentHardening",
        "wallet20gProviderIntentHardeningStatus",
        "wallet20gProviderIntentHardeningStage",
        "wallet20gProviderIntentHardeningBlockers",
        "wallet20gProviderOutcomeObserved",
        "providerIntentHardenedCurrent",
        "20G provider intent hardening is visible in Code Studio before transaction send.",
        "20D/20E/20F/20G",
    ]
    for marker in markers:
        assert marker in studio
