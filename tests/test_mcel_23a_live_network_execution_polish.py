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


def test_23a_live_network_execution_polish_contracts_and_blockers_exist() -> None:
    lab = read_script("mcel-lab.js")

    markers = [
        "mcelWallet23aLiveNetworkExecutionPolish",
        "mcelWallet23aLiveNetworkExecutionPolish.v1",
        "mcelLiveProviderNetworkSnapshot.v1",
        "mcelPolicyNetworkCompatibilityDecision.v1",
        "mcelPolicyTargetExecutionRoute.v1",
        "mcelLiveNetworkExecutionPolishReceipt.v1",
        'unlockVersion: "23A-MCEL"',
        "pre-send-live-route-readiness",
        "live-route-ready",
        "live-route-blocked",
        "canExecuteRoute",
        "routeExecutionReady",
        "observerFinalityRequiredBeforeSend: false",
        "23A says this wallet/account/chain/policy/target route can execute",
    ]
    for marker in markers:
        assert marker in lab

    blockers = [
        "wrong-chain",
        "policy-profile-missing",
        "policy-profile-inactive",
        "target-binding-missing",
        "target-binding-disallowed",
        "contract-kind-disallowed",
        "execution-route-blocked",
    ]
    for blocker in blockers:
        assert blocker in lab

    assert "wallet_switchEthereumChain" not in lab
    assert "wallet_addEthereumChain" not in lab
    assert "eth_signTransaction" not in lab
    assert "personal_sign" not in lab
    assert "broadcastTransaction" not in lab
    assert not re.search(r"\.sendTransaction\s*\(", lab)


def test_23a_is_after_22f_and_bound_into_runtime_receipt_copy_and_visible_surfaces() -> None:
    lab = read_script("mcel-lab.js")
    studio = read_script("code-editor-mcel-studio.js")
    app = read_app("mcel-lab.html")

    idx_22f = lab.index("function mcelWallet22fPolicyBoundExecutionReadinessSurface")
    idx_23a = lab.index("function mcelWallet23aLiveNetworkExecutionPolish")
    idx_23b = lab.index("function mcelWallet23bTransactionObserver")
    idx_boundary = lab.index("function mcelWalletToolCommitBoundary")
    assert idx_22f < idx_23a < idx_23b < idx_boundary

    runtime_markers = [
        "boundary.wallet23aLiveNetworkExecutionPolish = mcelWallet23aLiveNetworkExecutionPolish(boundary",
        "boundary.walletLiveNetworkExecutionPolish = boundary.wallet23aLiveNetworkExecutionPolish",
        "boundary.wallet23aLiveNetworkExecutionPolish?.canExecuteRoute === true",
        "wallet23aLiveNetworkExecutionPolish: null",
        "walletLiveNetworkExecutionPolish: null",
        "instance.runtime.wallet23aLiveNetworkExecutionPolish = boundary.wallet23aLiveNetworkExecutionPolish || null",
        "instance.runtime.walletLiveNetworkExecutionPolish = boundary.walletLiveNetworkExecutionPolish || boundary.wallet23aLiveNetworkExecutionPolish || null",
        '"runtime.wallet23aLiveNetworkExecutionPolish"',
        '"runtime.walletLiveNetworkExecutionPolish"',
    ]
    for marker in runtime_markers:
        assert marker in lab

    render_markers = [
        'const liveNetworkExecutionPolishSlot = document.querySelector("#mcel-23a-wallet-live-network-execution-polish")',
        'const visible23aLiveRouteSlot = document.querySelector("#mcel-23a-wallet-live-route-visible-status")',
        "mcel-23a-wallet-live-network-execution-polish-view",
        "wallet23aLiveNetworkExecutionPolish",
        "liveProviderNetworkSnapshot",
        "compatibilityDecision",
        "executionRoute",
        "liveNetworkExecutionPolishReceipt",
    ]
    for marker in render_markers:
        assert marker in lab

    html_markers = [
        'data-mcel-23ab-live-route-finality="true"',
        "23A live route readiness",
        "23A pre-send live route readiness",
        'id="mcel-23a-wallet-live-route-visible-status"',
        'id="mcel-23a-wallet-live-network-execution-polish"',
    ]
    for marker in html_markers:
        assert marker in app

    studio_markers = [
        "wallet23aLiveNetworkExecutionPolish",
        "wallet23aLiveRouteStatus",
        "wallet23aCanExecuteRoute",
        "liveNetworkExecutionPolishActive",
        "23A/23B pre-send live route readiness",
    ]
    for marker in studio_markers:
        assert marker in studio
