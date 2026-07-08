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


def test_20a_unlock_contract_is_staged_and_no_provider_mutation() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelWallet20aUnlockContract",
        "mcelWallet20aUnlockContract.v1",
        "mcelWallet20aUnlockDecisionReceipt.v1",
        'unlockVersion: "20A-MCEL"',
        "staged-provider-capability-contract",
        "staged-capability-contract-present",
        "draft-and-simulation-eligible",
        "contract-present-blocked",
        "wallet.buildDraft",
        "wallet.simulateDraft",
        "wallet.requestSignature",
        "wallet.broadcast",
        "wallet.providerMutation",
        "20A-signature-capability-lock",
        "20A-broadcast-capability-lock",
        "20A-no-provider-mutation",
        "canBuildDraft",
        "canSimulate",
        "canRequestSignature: false",
        "canBroadcast: false",
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

    unlock_index = lab.index("function mcelWallet20aUnlockContract")
    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    assert unlock_index < boundary_index

    unlock_block = lab[unlock_index:boundary_index]
    assert "capabilityContract: capabilities" in unlock_block
    assert "allowedCapabilities" in unlock_block
    assert "lockedCapabilities" in unlock_block
    assert re.search(r"canSend\s*:\s*false", unlock_block)
    assert re.search(r"canSign\s*:\s*false", unlock_block)
    assert re.search(r"canBroadcast\s*:\s*false", unlock_block)
    assert "20A separates build/simulate eligibility from signature and broadcast authority" in unlock_block


def test_20a_contract_is_bound_into_boundary_runtime_and_receipts() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "boundary.wallet20aUnlockContract = mcelWallet20aUnlockContract(boundary)",
        "boundary.walletUnlockContract = boundary.wallet20aUnlockContract",
        "instance.runtime.wallet20aUnlockContract = boundary.wallet20aUnlockContract || null",
        "instance.runtime.walletUnlockContract = boundary.walletUnlockContract || boundary.wallet20aUnlockContract || null",
        "wallet20aUnlockContract: null",
        "walletUnlockContract: null",
        '"wallet20aUnlockContract"',
        '"walletUnlockContract"',
        '"runtime.wallet20aUnlockContract"',
        '"runtime.walletUnlockContract"',
        "wallet20aUnlockContract: boundary.wallet20aUnlockContract || boundary.walletUnlockContract || {}",
        "walletUnlockContract: boundary.walletUnlockContract || boundary.wallet20aUnlockContract || {}",
        "wallet20aUnlockContract",
        "walletUnlockContract",
    ]
    for marker in markers:
        assert marker in lab

    boundary_index = lab.index("function mcelWalletToolCommitBoundary")
    boundary_block = lab[boundary_index:boundary_index + 12000]
    assert boundary_block.index("boundary.wallet19eNegativePathRegression = mcelWallet19eNegativePathRegression(boundary)") < boundary_block.index("boundary.wallet18nCompletionReport = mcelWallet18nCompletionReport(boundary)")
    assert boundary_block.index("boundary.wallet18nCompletionReport = mcelWallet18nCompletionReport(boundary)") < boundary_block.index("boundary.wallet20aUnlockContract = mcelWallet20aUnlockContract(boundary)")


def test_20a_unlock_contract_is_visible_on_wallet_board_and_code_studio() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    html = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-20a-wallet-unlock-contract="true"',
        "20A unlock contract",
        'id="mcel-20a-wallet-unlock-contract-visible-status"',
        'id="mcel-20a-wallet-unlock-eligibility-visible-status"',
        "20A contract",
        "20A eligibility",
        "signature and broadcast remain locked",
    ]
    for marker in html_markers:
        assert marker in html

    lab_markers = [
        'document.querySelector("#mcel-20a-wallet-unlock-contract-visible-status")',
        'document.querySelector("#mcel-20a-wallet-unlock-eligibility-visible-status")',
        "20A stage unlock contract",
        "20A says",
        "build=${wallet20aUnlockContract.canBuildDraft === true}",
        "signature=${wallet20aUnlockContract.canRequestSignature === true}",
        "wallet20aUnlockContract.stage",
        "wallet20aUnlockContract.nextAction",
    ]
    for marker in lab_markers:
        assert marker in lab

    studio_markers = [
        "wallet20aUnlockContract",
        "wallet20aUnlockContractStatus",
        "wallet20aUnlockContractStage",
        "wallet20aAllowedCapabilities",
        "wallet20aLockedCapabilities",
        "canBuildDraft",
        "canSimulate",
        "canRequestSignature",
        "20A staged unlock contract is visible in Code Studio while signature and broadcast remain locked.",
    ]
    for marker in studio_markers:
        assert marker in studio
