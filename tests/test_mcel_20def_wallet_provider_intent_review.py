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


def test_20def_provider_intent_review_layers_exist_without_transaction_mutation() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet20dBuildIntentEnvelope",
        "mcelWallet20dBuildTypedData",
        "mcelWallet20dProviderIntentSignature",
        "mcelWallet20dProviderIntentEnvelope.v1",
        "mcelWallet20dProviderIntentSignature.v1",
        "mcelWallet20dProviderIntentSignatureReceipt.v1",
        'unlockVersion: "20D-MCEL"',
        "live-provider-intent-signature-prompt",
        "eth_signTypedData_v4",
        "wallet.requestIntentSignature",
        "wallet.providerIntentSignaturePrompt",
        "providerSignatureRequested",
        "providerSignatureReceived",
        "mcelWallet20eSignedIntentVerification",
        "mcelWallet20eSignedIntentVerification.v1",
        "mcelWallet20eSignedIntentVerificationEnvelope.v1",
        'unlockVersion: "20E-MCEL"',
        "signed-intent-verification-and-invalidation",
        "signedIntentCurrent",
        "signature-missing",
        "account-changed",
        "chain-changed",
        "source-request-changed",
        "calldata-changed",
        "probe-evidence-changed",
        "signature-intent-expired",
        "mcelWallet20fPreSendReviewGate",
        "mcelWallet20fPreSendReviewGate.v1",
        "mcelWallet20fPreSendReviewEnvelope.v1",
        'unlockVersion: "20F-MCEL"',
        "final-pre-send-review-gate",
        "eligibleForFutureSendPatch",
        "pre-send-review-blocked",
        "locked-pending-21A",
        "canSend: false",
        "canSign: false",
        "canBroadcast: false",
        "mutationExecuted: false",
    ]
    for marker in markers:
        assert marker in lab

    dangerous_transaction_methods = [
        "eth_signTransaction",
        "personal_sign",
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


def test_20def_layers_are_ordered_after_20c_and_before_wallet_boundary() -> None:
    lab = read_script("mcel-lab.js")

    unlock20c_index = lab.index("function mcelWallet20cSignatureRequestPreflight")
    unlock20d_index = lab.index("function mcelWallet20dProviderIntentSignature")
    unlock20e_index = lab.index("function mcelWallet20eSignedIntentVerification")
    unlock20f_index = lab.index("function mcelWallet20fPreSendReviewGate")
    unlock21a_index = lab.index("function mcelWallet21aPolicyBoundSendGate")
    boundary_index = lab.index("function mcelWalletToolCommitBoundary")

    assert unlock20c_index < unlock20d_index < unlock20e_index < unlock20f_index < unlock21a_index < boundary_index

    unlock_block = lab[unlock20d_index:unlock21a_index]
    assert "20D is the first provider-prompt patch." in unlock_block
    assert "20D signs only an off-chain MCEL intent envelope." in unlock_block
    assert "20E invalidates signatures on account, chain, draft, source, calldata, probe, or expiry changes." in unlock_block
    assert "20F is the last no-send review gate before any future transaction send patch." in unlock_block
    assert re.search(r"canSend\s*:\s*false", unlock_block)
    assert re.search(r"canSign\s*:\s*false", unlock_block)
    assert re.search(r"canBroadcast\s*:\s*false", unlock_block)
    assert re.search(r"mutationExecuted\s*:\s*false", unlock_block)


def test_20def_boundary_runtime_aliases_and_scm_contract_are_declared() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "boundary.wallet20dProviderIntentSignature = mcelWallet20dProviderIntentSignature(boundary, runtime.wallet20dProviderIntentSignature || runtime.walletProviderIntentSignature || {})",
        "boundary.walletProviderIntentSignature = boundary.wallet20dProviderIntentSignature",
        "boundary.wallet20eSignedIntentVerification = mcelWallet20eSignedIntentVerification(boundary)",
        "boundary.walletSignedIntentVerification = boundary.wallet20eSignedIntentVerification",
        "boundary.wallet20fPreSendReviewGate = mcelWallet20fPreSendReviewGate(boundary)",
        "boundary.walletPreSendReviewGate = boundary.wallet20fPreSendReviewGate",
        "instance.runtime.wallet20dProviderIntentSignature = boundary.wallet20dProviderIntentSignature || null",
        "instance.runtime.walletProviderIntentSignature = boundary.walletProviderIntentSignature || boundary.wallet20dProviderIntentSignature || null",
        "instance.runtime.wallet20eSignedIntentVerification = boundary.wallet20eSignedIntentVerification || null",
        "instance.runtime.walletSignedIntentVerification = boundary.walletSignedIntentVerification || boundary.wallet20eSignedIntentVerification || null",
        "instance.runtime.wallet20fPreSendReviewGate = boundary.wallet20fPreSendReviewGate || null",
        "instance.runtime.walletPreSendReviewGate = boundary.walletPreSendReviewGate || boundary.wallet20fPreSendReviewGate || null",
        "wallet20dProviderIntentSignature: null",
        "walletProviderIntentSignature: null",
        "wallet20eSignedIntentVerification: null",
        "walletSignedIntentVerification: null",
        "wallet20fPreSendReviewGate: null",
        "walletPreSendReviewGate: null",
        '"runtime.wallet20dProviderIntentSignature"',
        '"runtime.walletProviderIntentSignature"',
        '"runtime.wallet20eSignedIntentVerification"',
        '"runtime.walletSignedIntentVerification"',
        '"runtime.wallet20fPreSendReviewGate"',
        '"runtime.walletPreSendReviewGate"',
                        '"wallet.requestIntentSignature": {',
        '"runtime.wallet20dProviderIntentSignature",',
        '"runtime.walletProviderIntentSignature",',
        'operation: "eth_signTypedData_v4"',
        '"walletIntentSignatureRequested"',
    ]
    for marker in markers:
        assert marker in lab


def test_20def_ui_and_render_surfaces_are_visible() -> None:
    lab = read_script("mcel-lab.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-20def-wallet-provider-review="true"',
        'id="mcel-20d-wallet-request-intent-signature"',
        'data-mc-effect="wallet.requestIntentSignature"',
        '20D intent signature',
        '20E signature verify',
        '20F pre-send review',
        'id="mcel-20d-wallet-intent-signature-visible-status"',
        'id="mcel-20e-wallet-signature-verification-visible-status"',
        'id="mcel-20f-wallet-pre-send-review-visible-status"',
        'id="mcel-20d-wallet-provider-intent-signature"',
        'id="mcel-20e-wallet-signed-intent-verification"',
        'id="mcel-20f-wallet-pre-send-review"',
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'document.querySelector("#mcel-20d-wallet-request-intent-signature")',
        'data-mc-effect="wallet.requestIntentSignature"',
        'const providerIntentSignatureSlot = document.querySelector("#mcel-20d-wallet-provider-intent-signature")',
        'const signedIntentVerificationSlot = document.querySelector("#mcel-20e-wallet-signed-intent-verification")',
        'const preSendReviewSlot = document.querySelector("#mcel-20f-wallet-pre-send-review")',
        "20D/20E/20F",
        "20D request off-chain provider intent signature",
        "20E verify signed intent freshness",
        "20F prepare final no-send review",
        "providerIntentSignatureSlot.textContent = JSON.stringify",
        "signedIntentVerificationSlot.textContent = JSON.stringify",
        "preSendReviewSlot.textContent = JSON.stringify",
    ]
    for marker in render_markers:
        assert marker in lab


def test_20def_code_studio_receives_matching_proof_dock_fields() -> None:
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "wallet20dProviderIntentSignature",
        "wallet20dProviderIntentStatus",
        "wallet20dProviderIntentStage",
        "wallet20dProviderSignatureRequested",
        "wallet20dProviderSignatureReceived",
        "wallet20eSignedIntentVerification",
        "wallet20eSignedIntentVerificationStatus",
        "wallet20eSignedIntentVerificationStage",
        "wallet20eSignedIntentCurrent",
        "wallet20fPreSendReviewGate",
        "wallet20fPreSendReviewStatus",
        "wallet20fPreSendReviewStage",
        "wallet20fEligibleForFutureSendPatch",
        "signedIntentCurrent",
        "eligibleForFutureSendPatch",
        "20D/20E/20F",
        "20A staged unlock contract, 20B signed-intent gate, 20C signature-request preflight, 20D off-chain provider intent signature, 20E signed-intent verification, and 20F pre-send review are visible in Code Studio while transaction send and broadcast remain locked.",
    ]
    for marker in markers:
        assert marker in studio
