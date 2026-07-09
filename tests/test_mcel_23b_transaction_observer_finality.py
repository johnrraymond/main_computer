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


def test_23b_transaction_observer_finality_contracts_states_and_read_only_markers_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet23bTransactionObserver",
        "mcelWallet23bTransactionObserver.v1",
        "mcelTransactionFinalityEnvelope.v1",
        "mcelTransactionFinalityReceipt.v1",
        "mcelTransactionObserverReceipt.v1",
        'unlockVersion: "23B-MCEL"',
        "post-send-read-only-transaction-finality-observer",
        "eth_getTransactionReceipt",
        "eth_blockNumber",
        "eth_getTransactionByHash",
        "txHashAloneIsSuccess: false",
        "successRequiresReceiptAndFinality: true",
        "23B is waiting for a transaction hash",
        "does not treat txHash alone as success",
    ]
    for marker in markers:
        assert marker in lab

    states = [
        "observer-waiting-for-tx-hash",
        "observer-pending",
        "observer-receipt-found",
        "observer-confirmed",
        "observer-finality-satisfied",
        "observer-reverted",
        "observer-timeout",
        "observer-receipt-unavailable",
        "observer-replacement-suspected",
    ]
    for state in states:
        assert state in lab

    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)

    route_and_observer_defs = lab[
        lab.index("function mcelWallet23aLiveNetworkExecutionPolish") : lab.index("function mcelWalletToolCommitBoundary")
    ]
    assert "eth_sendTransaction" not in route_and_observer_defs


def test_23b_is_visible_beside_23a_and_does_not_block_pre_send_route_readiness() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    boundary_markers = [
        "boundary.wallet23bTransactionObserver = mcelWallet23bTransactionObserver(boundary",
        "boundary.walletTransactionFinalityObserver = boundary.wallet23bTransactionObserver",
        "wallet23bTransactionObserver: null",
        "walletTransactionFinalityObserver: null",
        "instance.runtime.wallet23bTransactionObserver = boundary.wallet23bTransactionObserver || null",
        "instance.runtime.walletTransactionFinalityObserver = boundary.walletTransactionFinalityObserver || boundary.wallet23bTransactionObserver || null",
        '"runtime.wallet23bTransactionObserver"',
        '"runtime.walletTransactionFinalityObserver"',
        "observerFinalityRequiredBeforeSend: false",
    ]
    for marker in boundary_markers:
        assert marker in lab

    feed_markers = [
        "feeds21eReceiptIntegration: true",
        "feeds21fRelockResetLifecycle: true",
        "feeds22fReadinessReporting: true",
        "visibleBeside23aLiveRouteReadiness: true",
        "receiptIntegrationStatus",
        "relockLifecycleStatus",
        "readinessStatus",
        "liveRouteStatus",
    ]
    for marker in feed_markers:
        assert marker in lab

    render_markers = [
        'const transactionObserverFinalitySlot = document.querySelector("#mcel-23b-wallet-transaction-observer-finality")',
        'const visible23bFinalityObserverSlot = document.querySelector("#mcel-23b-wallet-finality-observer-visible-status")',
        "mcel-23b-wallet-transaction-observer-finality-view",
        "transactionFinalityEnvelope",
        "transactionFinalityReceipt",
        "transactionObserverReceipt",
    ]
    for marker in render_markers:
        assert marker in lab

    html_markers = [
        "23B transaction finality observer",
        "23B post-send/read-only finality observer",
        'id="mcel-23b-wallet-finality-observer-visible-status"',
        'id="mcel-23b-wallet-transaction-observer-finality"',
    ]
    for marker in html_markers:
        assert marker in app

    studio_markers = [
        "wallet23bTransactionObserver",
        "wallet23bTransactionObserverStatus",
        "wallet23bFinalitySatisfied",
        "wallet23bTxHashAloneIsSuccess",
        "transactionFinalityObserverActive",
    ]
    for marker in studio_markers:
        assert marker in studio
