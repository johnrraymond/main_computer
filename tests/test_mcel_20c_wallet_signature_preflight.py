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


def test_20c_signature_request_preflight_is_armed_without_provider_prompt() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet20cSignatureRequestPreflight",
        "mcelWallet20cSignatureRequestPreflight.v1",
        "mcelWallet20cSignaturePreflightEnvelope.v1",
        "mcelWallet20cSignatureRequestPreflightReceipt.v1",
        'unlockVersion: "20C-MCEL"',
        "signature-request-preflight-arm",
        "signature-request-preflight-armed",
        "signature-request-preflight-blocked",
        "signature-request-preflight-armed-no-provider-call",
        "wallet.signatureRequestPreflight",
        "wallet.providerSignaturePrompt",
        "20C-provider-prompt-not-bound",
        "20C-sign-remains-locked",
        "20C-send-remains-locked",
        "20C-broadcast-remains-locked",
        "canArmSignatureRequest",
        "canRequestProviderSignature: false",
        "providerPromptBound: false",
        "providerSignatureRequested: false",
        'providerPromptInvocation: "absent"',
        "readyForProviderExecution: false",
        "mutationExecuted: false",
    ]
    for marker in markers:
        assert marker in lab

    dangerous_methods = [
        "eth_sendTransaction",
        "eth_signTransaction",
        "personal_sign",
        "signTypedData",
        "sendTransaction",
        "broadcastTransaction",
    ]
    for method in dangerous_methods:
        assert method not in lab
        assert method not in studio

    unlock20b_index = lab.index("function mcelWallet20bSignedIntentUnlock")
    unlock20c_index = lab.index("function mcelWallet20cSignatureRequestPreflight")
    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    assert unlock20b_index < unlock20c_index < boundary_index

    unlock_block = lab[unlock20c_index:boundary_index]
    assert "providerPromptBound: false" in unlock_block
    assert "providerSignatureRequested: false" in unlock_block
    assert "wallet.signatureRequestPreflight" in unlock_block
    assert "wallet.providerSignaturePrompt" in unlock_block
    assert re.search(r"canSend\s*:\s*false", unlock_block)
    assert re.search(r"canSign\s*:\s*false", unlock_block)
    assert re.search(r"canBroadcast\s*:\s*false", unlock_block)
    assert "Any actual wallet prompt must be a later explicit provider-action patch." in unlock_block


def test_20c_signature_preflight_is_bound_into_boundary_runtime_and_receipts() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "boundary.wallet20cSignatureRequestPreflight = mcelWallet20cSignatureRequestPreflight(boundary)",
        "boundary.walletSignatureRequestPreflight = boundary.wallet20cSignatureRequestPreflight",
        "boundary.nextAction = boundary.wallet20cSignatureRequestPreflight?.nextAction",
        "instance.runtime.wallet20cSignatureRequestPreflight = boundary.wallet20cSignatureRequestPreflight || null",
        "instance.runtime.walletSignatureRequestPreflight = boundary.walletSignatureRequestPreflight || boundary.wallet20cSignatureRequestPreflight || null",
        "wallet20cSignatureRequestPreflight: null",
        "walletSignatureRequestPreflight: null",
        '"runtime.wallet20cSignatureRequestPreflight"',
        '"runtime.walletSignatureRequestPreflight"',
        "wallet20cSignatureRequestPreflight: boundary.wallet20cSignatureRequestPreflight || boundary.walletSignatureRequestPreflight || {}",
        "walletSignatureRequestPreflight: boundary.walletSignatureRequestPreflight || boundary.wallet20cSignatureRequestPreflight || {}",
        "wallet20cSignatureRequestPreflight",
        "walletSignatureRequestPreflight",
    ]
    for marker in markers:
        assert marker in lab

    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    boundary_block = lab[boundary_index:boundary_index + 14000]
    assert boundary_block.index("boundary.wallet20bSignedIntentUnlock = mcelWallet20bSignedIntentUnlock(boundary)") < boundary_block.index("boundary.wallet20cSignatureRequestPreflight = mcelWallet20cSignatureRequestPreflight(boundary)")
    assert "20C signature-request preflight can be armed without binding or invoking a provider prompt" in boundary_block

    copy_index = lab.index("async function copyMcel18nWalletToolReceipt")
    copy_block = lab[copy_index:copy_index + 5000]
    assert "wallet20cSignatureRequestPreflight" in copy_block
    assert "walletSignatureRequestPreflight" in copy_block


def test_20c_signature_preflight_runtime_fields_are_scm_owned() -> None:
    lab = read_script("mcel-lab.js")
    manifest_start = lab.index("function mcelTinyContractScmManifest")
    manifest_block = lab[manifest_start:manifest_start + 8000]
    owns_runtime_match = re.search(r"runtime:\s*\[(?P<body>.*?)\]\s*,\s*layout:", manifest_block, re.S)
    assert owns_runtime_match is not None
    owned_runtime_fields = set(re.findall(r'"([^"]+)"', owns_runtime_match.group("body")))

    assert "wallet20cSignatureRequestPreflight" in owned_runtime_fields
    assert "walletSignatureRequestPreflight" in owned_runtime_fields
    assert '"runtime.wallet20cSignatureRequestPreflight"' in lab
    assert '"runtime.walletSignatureRequestPreflight"' in lab


def test_20c_signature_preflight_is_visible_on_wallet_board_and_code_studio() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    html = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-20c-wallet-signature-preflight="true"',
        "20C signature preflight",
        "20C signature-request preflight",
        'id="mcel-20c-wallet-signature-preflight-visible-status"',
        'id="mcel-20c-wallet-provider-prompt-visible-status"',
        'id="mcel-20c-wallet-signature-request-preflight"',
        "20C provider prompt",
        "provider prompt, signature, send, and broadcast remain locked",
    ]
    for marker in html_markers:
        assert marker in html

    lab_markers = [
        'document.querySelector("#mcel-20c-wallet-signature-preflight-visible-status")',
        'document.querySelector("#mcel-20c-wallet-provider-prompt-visible-status")',
        'document.querySelector("#mcel-20c-wallet-signature-request-preflight")',
        "20C arm signature preflight",
        "20C says",
        "armed=${wallet20cSignatureRequestPreflight.canArmSignatureRequest === true}",
        "providerPrompt=${wallet20cSignatureRequestPreflight.providerPromptBound === true}",
        "providerRequest=${wallet20cSignatureRequestPreflight.providerSignatureRequested === true}",
        "wallet20cSignatureRequestPreflight.signaturePreflightEnvelope",
        "wallet20cSignatureRequestPreflight.decisionReceipt",
    ]
    for marker in lab_markers:
        assert marker in lab

    studio_markers = [
        "wallet20cSignatureRequestPreflight",
        "wallet20cSignaturePreflightStatus",
        "wallet20cSignaturePreflightStage",
        "wallet20cAllowedCapabilities",
        "wallet20cLockedCapabilities",
        "wallet20cCanArmSignatureRequest",
        "wallet20cProviderPromptBound",
        "wallet20cProviderSignatureRequested",
        "canArmSignatureRequest",
        "canRequestProviderSignature",
        "20C signature-request preflight are visible in Code Studio",
        "provider prompt, signature, and broadcast remain locked",
    ]
    for marker in studio_markers:
        assert marker in studio
