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


def test_21def_retry_recovery_receipt_integration_and_relock_contracts_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet21dRetryRecoverySafety",
        "mcelWallet21dRetryRecoverySafety.v1",
        "mcelWallet21dRetryRecoverySafetyEnvelope.v1",
        "mcelWallet21dRetryRecoverySafetyReceipt.v1",
        'unlockVersion: "21D-MCEL"',
        "retry-recovery-duplicate-send-safety",
        "same-send-envelope-already-ledgered",
        "duplicate-click-or-retry-request-observed",
        "transaction-still-pending",
        "retry-would-reuse-same-send-envelope",
        "automaticRetryAllowed: false",
        "mcelWallet21ePostConfirmationMcelReceiptIntegration",
        "mcelWallet21ePostConfirmationMcelReceiptIntegration.v1",
        "mcelWallet21ePostConfirmationMcelReceiptIntegrationEnvelope.v1",
        "mcelWallet21ePostConfirmationMcelReceipt.v1",
        'unlockVersion: "21E-MCEL"',
        "post-confirmation-mcel-receipt-integration",
        "mcel-chain-confirmation-integrated",
        "mcel-chain-failure-integrated",
        "copiedReceiptCarriesOutcome: true",
        "codeStudioProofDockCarriesOutcome: true",
        "mcelWallet21fRelockResetLifecycle",
        "mcelWallet21fRelockResetLifecycle.v1",
        "mcelWallet21fRelockResetLifecycleEnvelope.v1",
        'unlockVersion: "21F-MCEL"',
        "post-send-relock-reset-lifecycle",
        "complete-and-relocked",
        "pending-recovery-hold",
        "provider-rejected-and-relocked",
        "requiresFreshDraftForNextSend",
    ]
    for marker in markers:
        assert marker in lab

    assert "eth_sendTransaction" in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_21def_is_ordered_after_21abc_and_bound_into_boundary_runtime_and_scm() -> None:
    lab = read_script("mcel-lab.js")

    idx_21a = lab.index("function mcelWallet21aPolicyBoundSendGate")
    idx_21b = lab.index("function mcelWallet21bProviderOutcomeLedger")
    idx_21c = lab.index("function mcelWallet21cTransactionWatcher")
    idx_21d = lab.index("function mcelWallet21dRetryRecoverySafety")
    idx_21e = lab.index("function mcelWallet21ePostConfirmationMcelReceiptIntegration")
    idx_21f = lab.index("function mcelWallet21fRelockResetLifecycle")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_21a < idx_21b < idx_21c < idx_21d < idx_21e < idx_21f < idx_boundary

    markers = [
        "boundary.wallet21dRetryRecoverySafety = mcelWallet21dRetryRecoverySafety(boundary",
        "boundary.walletRetryRecoverySafety = boundary.wallet21dRetryRecoverySafety",
        "boundary.wallet21ePostConfirmationMcelReceiptIntegration = mcelWallet21ePostConfirmationMcelReceiptIntegration(boundary",
        "boundary.walletPostConfirmationReceiptIntegration = boundary.wallet21ePostConfirmationMcelReceiptIntegration",
        "boundary.wallet21fRelockResetLifecycle = mcelWallet21fRelockResetLifecycle(boundary",
        "boundary.walletRelockResetLifecycle = boundary.wallet21fRelockResetLifecycle",
        "boundary.canSend = boundary.wallet21aPolicyBoundSendGate?.canSend === true && boundary.wallet21dRetryRecoverySafety?.sendRequestAllowed === true",
        "instance.runtime.wallet21dRetryRecoverySafety = boundary.wallet21dRetryRecoverySafety || null",
        "instance.runtime.wallet21ePostConfirmationMcelReceiptIntegration = boundary.wallet21ePostConfirmationMcelReceiptIntegration || null",
        "instance.runtime.wallet21fRelockResetLifecycle = boundary.wallet21fRelockResetLifecycle || null",
        "wallet21dRetryRecoverySafety: null",
        "walletRetryRecoverySafety: null",
        "wallet21ePostConfirmationMcelReceiptIntegration: null",
        "walletPostConfirmationReceiptIntegration: null",
        "wallet21fRelockResetLifecycle: null",
        "walletRelockResetLifecycle: null",
        '"runtime.wallet21dRetryRecoverySafety"',
        '"runtime.walletRetryRecoverySafety"',
        '"runtime.wallet21ePostConfirmationMcelReceiptIntegration"',
        '"runtime.walletPostConfirmationReceiptIntegration"',
        '"runtime.wallet21fRelockResetLifecycle"',
        '"runtime.walletRelockResetLifecycle"',
    ]
    for marker in markers:
        assert marker in lab


def test_21def_visible_wallet_board_receipt_copy_and_code_studio_alignment_exist() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-21def-wallet-recovery-receipts-relock="true"',
        "21D retry safety",
        "21E receipt integration",
        "21F relock lifecycle",
        'id="mcel-21d-wallet-retry-recovery-safety"',
        'id="mcel-21e-wallet-post-confirmation-integration"',
        'id="mcel-21f-wallet-relock-reset-lifecycle"',
        'id="mcel-21d-wallet-retry-recovery-visible-status"',
        'id="mcel-21e-wallet-receipt-integration-visible-status"',
        'id="mcel-21f-wallet-relock-lifecycle-visible-status"',
        "21D blocks duplicate retries",
        "21E integrates chain outcome",
        "21F relocks",
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'const retryRecoverySafetySlot = document.querySelector("#mcel-21d-wallet-retry-recovery-safety")',
        'const postConfirmationIntegrationSlot = document.querySelector("#mcel-21e-wallet-post-confirmation-integration")',
        'const relockResetLifecycleSlot = document.querySelector("#mcel-21f-wallet-relock-reset-lifecycle")',
        "mcel-21d-wallet-retry-recovery-safety-view",
        "mcel-21e-wallet-post-confirmation-integration-view",
        "mcel-21f-wallet-relock-reset-lifecycle-view",
        "21D says",
        "21E says",
        "21F says",
        "wallet21dRetryRecoverySafety: boundary.wallet21dRetryRecoverySafety || boundary.walletRetryRecoverySafety || {}",
        "wallet21ePostConfirmationMcelReceiptIntegration: boundary.wallet21ePostConfirmationMcelReceiptIntegration || boundary.walletPostConfirmationReceiptIntegration || {}",
        "wallet21fRelockResetLifecycle: boundary.wallet21fRelockResetLifecycle || boundary.walletRelockResetLifecycle || {}",
    ]
    for marker in render_markers:
        assert marker in lab

    studio_markers = [
        "wallet21dRetryRecoverySafety",
        "wallet21dRetryRecoveryStatus",
        "wallet21dDuplicateSendBlocked",
        "wallet21ePostConfirmationMcelReceiptIntegration",
        "wallet21eReceiptIntegrationStatus",
        "wallet21eChainOutcome",
        "wallet21fRelockResetLifecycle",
        "wallet21fRelockLifecycleStatus",
        "wallet21fRequiresFreshDraftForNextSend",
        "21D/21E/21F retry safety, post-confirmation receipt integration, and relock lifecycle are visible in Code Studio.",
    ]
    for marker in studio_markers:
        assert marker in studio
