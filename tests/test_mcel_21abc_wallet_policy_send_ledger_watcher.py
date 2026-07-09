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


def test_21abc_policy_bound_network_agnostic_send_ledger_and_watcher_exist() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")

    markers = [
        "mcelNetworkExecutionPolicy.v1",
        "mcelWallet21aPolicyBoundSendGate",
        "mcelWallet21aPolicyBoundSendGate.v1",
        "mcelWallet21aPolicyBoundSendEnvelope.v1",
        'unlockVersion: "21A-MCEL"',
        "policy-bound-network-agnostic-transaction-send",
        "network-agnostic-policy-bound",
        "networkAgnostic: true",
        "devnetOnly: false",
        "providerTransactionSendMethod: \"eth_sendTransaction\"",
        "ethSendTransactionBound: true",
        "wallet.sendPolicyBoundTransaction",
        "mcelWallet21bProviderOutcomeLedger",
        "mcelWallet21bProviderOutcomeLedger.v1",
        "mcelWallet21bProviderOutcomeReceipt.v1",
        'unlockVersion: "21B-MCEL"',
        "provider-outcome-receipt-ledger",
        "provider-accepted-tx-hash",
        "provider-rejected-send",
        "duplicateSendBlocked: true",
        "canRetryAutomatically: false",
        "mcelWallet21cTransactionWatcher",
        "mcelWallet21cTransactionWatcher.v1",
        "mcelWallet21cTransactionWatcherEnvelope.v1",
        'unlockVersion: "21C-MCEL"',
        "transaction-receipt-watcher",
        "eth_getTransactionReceipt",
        "tx-hash-received-pending-receipt",
        "transaction-confirmed",
        "transaction-failed-or-reverted",
    ]
    for marker in markers:
        assert marker in lab

    assert "eth_sendTransaction" in lab
    assert "eth_sendTransaction" not in studio
    assert "eth_signTransaction" not in lab
    assert "eth_signTransaction" not in studio
    assert "personal_sign" not in lab
    assert "personal_sign" not in studio
    assert not re.search(r"\.sendTransaction\s*\(", lab)
    assert not re.search(r"\.sendTransaction\s*\(", studio)


def test_21abc_is_ordered_after_20g_and_bound_into_boundary_runtime_and_scm() -> None:
    lab = read_script("mcel-lab.js")

    idx_20g = lab.index("function mcelWallet20gProviderIntentHardening")
    idx_21a = lab.index("function mcelWallet21aPolicyBoundSendGate")
    idx_21b = lab.index("function mcelWallet21bProviderOutcomeLedger")
    idx_21c = lab.index("function mcelWallet21cTransactionWatcher")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_20g < idx_21a < idx_21b < idx_21c < idx_boundary

    markers = [
        "boundary.wallet21aPolicyBoundSendGate = mcelWallet21aPolicyBoundSendGate(boundary",
        "boundary.walletTransactionSendGate = boundary.wallet21aPolicyBoundSendGate",
        "boundary.wallet21bProviderOutcomeLedger = mcelWallet21bProviderOutcomeLedger(boundary",
        "boundary.walletProviderOutcomeLedger = boundary.wallet21bProviderOutcomeLedger",
        "boundary.wallet21cTransactionWatcher = mcelWallet21cTransactionWatcher(boundary",
        "boundary.walletTransactionWatcher = boundary.wallet21cTransactionWatcher",
        "instance.runtime.wallet21aPolicyBoundSendGate = boundary.wallet21aPolicyBoundSendGate || null",
        "instance.runtime.walletTransactionSendGate = boundary.walletTransactionSendGate || boundary.wallet21aPolicyBoundSendGate || null",
        "wallet21aPolicyBoundSendGate: null",
        "walletTransactionSendGate: null",
        '"runtime.wallet21aPolicyBoundSendGate"',
        '"runtime.walletTransactionSendGate"',
        '"runtime.wallet21bProviderOutcomeLedger"',
        '"runtime.walletProviderOutcomeLedger"',
        '"runtime.wallet21cTransactionWatcher"',
        '"runtime.walletTransactionWatcher"',
        '"wallet.sendPolicyBoundTransaction"',
        "policy-bound-provider-transaction-send-effect",
        'operation: "eth_sendTransaction"',
    ]
    for marker in markers:
        assert marker in lab


def test_21abc_visible_wallet_board_and_code_studio_alignment_exist() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    html_markers = [
        'data-mcel-21abc-wallet-transaction-execution="true"',
        "21A policy send",
        "21B provider outcome",
        "21C tx watcher",
        'id="mcel-21a-wallet-send-policy-bound"',
        'data-mc-effect="wallet.sendPolicyBoundTransaction"',
        'id="mcel-21a-wallet-policy-bound-send-gate"',
        'id="mcel-21b-wallet-provider-outcome-ledger"',
        'id="mcel-21c-wallet-transaction-watcher"',
        "21A can request a policy-bound network-agnostic provider send",
        "21B ledgers provider outcome and 21C watches receipts",
    ]
    for marker in html_markers:
        assert marker in app

    render_markers = [
        'const policyBoundSendGateSlot = document.querySelector("#mcel-21a-wallet-policy-bound-send-gate")',
        'const providerOutcomeLedgerSlot = document.querySelector("#mcel-21b-wallet-provider-outcome-ledger")',
        'const transactionWatcherSlot = document.querySelector("#mcel-21c-wallet-transaction-watcher")',
        "mcel-21a-wallet-policy-bound-send-gate-view",
        "mcel-21b-wallet-provider-outcome-ledger-view",
        "mcel-21c-wallet-transaction-watcher-view",
        "21A says",
        "21B says",
        "21C says",
    ]
    for marker in render_markers:
        assert marker in lab

    studio_markers = [
        "wallet21aPolicyBoundSendGate",
        "wallet21aPolicyBoundSendStatus",
        "wallet21aNetworkAgnostic",
        "wallet21aDevnetOnly",
        "wallet21bProviderOutcomeLedger",
        "wallet21bProviderOutcomeStatus",
        "wallet21cTransactionWatcher",
        "wallet21cTransactionWatcherStatus",
        "21A/21B/21C policy-bound network-agnostic send, provider outcome ledger, and transaction watcher are visible in Code Studio.",
        "21A is not devnet-only; the connected chain must satisfy the MCEL network execution policy.",
    ]
    for marker in studio_markers:
        assert marker in studio
