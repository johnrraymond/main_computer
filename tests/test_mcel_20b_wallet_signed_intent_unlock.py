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


def test_20b_signed_intent_gate_is_present_and_provider_safe() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet20bSignedIntentUnlock",
        "mcelWallet20bSignedIntentUnlock.v1",
        "mcelWallet20bSignedIntentEnvelope.v1",
        "mcelWallet20bSignedIntentDecisionReceipt.v1",
        'unlockVersion: "20B-MCEL"',
        "signed-intent-signature-request-gate",
        "signature-intent-eligible",
        "signature-intent-blocked",
        "signature-intent-armed-no-provider-call",
        "wallet.requestSignatureIntent",
        "wallet.providerSignatureRequest",
        "20B-provider-signature-method-not-bound",
        "20B-send-remains-locked",
        "20B-broadcast-remains-locked",
        "canRequestSignatureIntent",
        "canRequestSignature: signatureIntentEligible",
        "providerSignatureRequested: false",
        'signatureRequestMethod: "not-bound-to-provider"',
        "readyForBroadcast: false",
        "readyForProviderExecution: false",
        "mutationExecuted: false",
    ]
    for marker in markers:
        assert marker in lab

    dangerous_methods = [
        "eth_signTransaction",
        "personal_sign",
        "broadcastTransaction",
    ]
    for method in dangerous_methods:
        assert method not in lab
        assert method not in studio

    assert "eth_sendTransaction" in lab
    assert "function mcelWallet21aPolicyBoundSendGate" in lab
    assert "eth_sendTransaction" not in studio
    assert not re.search(r"\.sendTransaction\s*\(", lab)
    assert not re.search(r"\.sendTransaction\s*\(", studio)

    unlock20a_index = lab.index("function mcelWallet20aUnlockContract")
    unlock20b_index = lab.index("function mcelWallet20bSignedIntentUnlock")
    unlock21a_index = lab.index("function mcelWallet21aPolicyBoundSendGate")
    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    assert unlock20a_index < unlock20b_index < unlock21a_index < boundary_index

    unlock_block = lab[unlock20b_index:unlock21a_index]
    assert "providerSignatureRequested: false" in unlock_block
    assert "wallet.requestSignatureIntent" in unlock_block
    assert "wallet.providerSignatureRequest" in unlock_block
    assert re.search(r"canSend\s*:\s*false", unlock_block)
    assert re.search(r"canSign\s*:\s*false", unlock_block)
    assert re.search(r"canBroadcast\s*:\s*false", unlock_block)
    assert "Any actual signature prompt must be a later explicit provider-action patch." in unlock_block


def test_20b_signed_intent_gate_is_bound_into_boundary_and_runtime() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "boundary.wallet20bSignedIntentUnlock = mcelWallet20bSignedIntentUnlock(boundary)",
        "boundary.walletSignedIntentUnlock = boundary.wallet20bSignedIntentUnlock",
        "boundary.nextAction = boundary.wallet20bSignedIntentUnlock?.nextAction",
        "instance.runtime.wallet20bSignedIntentUnlock = boundary.wallet20bSignedIntentUnlock || null",
        "instance.runtime.walletSignedIntentUnlock = boundary.walletSignedIntentUnlock || boundary.wallet20bSignedIntentUnlock || null",
        "wallet20bSignedIntentUnlock: null",
        "walletSignedIntentUnlock: null",
        '"runtime.wallet20bSignedIntentUnlock"',
        '"runtime.walletSignedIntentUnlock"',
        "const wallet20bSignedIntentUnlock = boundary.wallet20bSignedIntentUnlock || boundary.walletSignedIntentUnlock || {}",
        "wallet20bSignedIntentUnlock",
        "walletSignedIntentUnlock",
    ]
    for marker in markers:
        assert marker in lab

    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    boundary_block = lab[boundary_index:boundary_index + 18000]
    assert boundary_block.index("boundary.wallet20aUnlockContract = mcelWallet20aUnlockContract(boundary)") < boundary_block.index("boundary.wallet20bSignedIntentUnlock = mcelWallet20bSignedIntentUnlock(boundary)")
    assert "20B signed-intent gate can mark requestSignature intent eligible without binding a provider signature method" in boundary_block


def test_20b_signed_intent_runtime_fields_are_scm_owned() -> None:
    lab = read_script("mcel-lab.js")
    manifest_start = lab.index("function mcelTinyContractScmManifest")
    manifest_block = lab[manifest_start:manifest_start + 7000]
    owns_runtime_match = re.search(r"runtime:\s*\[(?P<body>.*?)\]\s*,\s*layout:", manifest_block, re.S)
    assert owns_runtime_match is not None
    owned_runtime_fields = set(re.findall(r'"([^"]+)"', owns_runtime_match.group("body")))

    assert "wallet20bSignedIntentUnlock" in owned_runtime_fields
    assert "walletSignedIntentUnlock" in owned_runtime_fields
    assert '"runtime.wallet20bSignedIntentUnlock"' in lab
    assert '"runtime.walletSignedIntentUnlock"' in lab
    assert '"source.devRelease.requests", "state.selectedRequestId", "runtime.txDraft"' in lab


def test_20b_signed_intent_gate_is_visible_on_wallet_board_and_code_studio() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    html = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-20b-wallet-signed-intent-unlock="true"',
        "20B signed-intent gate",
        'id="mcel-20b-wallet-signed-intent-visible-status"',
        'id="mcel-20b-wallet-signature-gate-visible-status"',
        'id="mcel-20b-wallet-signed-intent-unlock"',
        "20B signed intent",
        "20B signature gate",
    ]
    for marker in html_markers:
        assert marker in html

    lab_markers = [
        'document.querySelector("#mcel-20b-wallet-signed-intent-visible-status")',
        'document.querySelector("#mcel-20b-wallet-signature-gate-visible-status")',
        'document.querySelector("#mcel-20b-wallet-signed-intent-unlock")',
        "20B arm signed-intent gate",
        "20B says",
        "intent=${wallet20bSignedIntentUnlock.canRequestSignatureIntent === true}",
        "provider=${wallet20bSignedIntentUnlock.providerSignatureRequested === true}",
        "wallet20bSignedIntentUnlock.signedIntentEnvelope",
        "wallet20bSignedIntentUnlock.decisionReceipt",
    ]
    for marker in lab_markers:
        assert marker in lab

    studio_markers = [
        "wallet20bSignedIntentUnlock",
        "wallet20bSignedIntentStatus",
        "wallet20bSignedIntentStage",
        "wallet20bAllowedCapabilities",
        "wallet20bLockedCapabilities",
        "wallet20bProviderSignatureRequested",
        "20A staged unlock contract, 20B signed-intent gate, 20C signature-request preflight, 20D off-chain provider intent signature, 20E signed-intent verification, and 20F pre-send review are visible in Code Studio",
        "transaction send and broadcast remain locked",
    ]
    for marker in studio_markers:
        assert marker in studio
