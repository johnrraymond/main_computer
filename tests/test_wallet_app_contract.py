from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
WALLET_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "wallet.html"
WALLET_CSS = ROOT / "main_computer" / "web" / "applications" / "styles" / "wallet.css"
WALLET_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "wallet.js"
WALLET_BINDINGS_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "wallet.js"
NAVIGATION_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "navigation.js"
APP_ROUTING_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "app-routing.js"
DOM_BINDINGS_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings.js"
VIEWPORT_ROUTE_DISPATCH = ROOT / "main_computer" / "viewport_route_dispatch.py"
VIEWPORT_ROUTES_ENERGY = ROOT / "main_computer" / "viewport_routes_energy.py"


def test_wallet_app_is_registered_as_a_standalone_application() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    navigation = NAVIGATION_JS.read_text(encoding="utf-8")
    routing = APP_ROUTING_JS.read_text(encoding="utf-8")
    dom_bindings = DOM_BINDINGS_JS.read_text(encoding="utf-8")

    assert 'href="/applications/wallet" data-app="wallet"' in html
    assert "<!-- @include applications/apps/wallet.html -->" in html
    assert "<!-- @include applications/styles/wallet.css -->" in html
    assert "<!-- @include applications/scripts/wallet.js -->" in html
    assert "<!-- @include applications/scripts/dom-bindings/wallet.js -->" in dom_bindings

    assert 'wallet: ["Wallet"' in navigation
    assert '{app: "wallet", glyph: "W", title: "Wallet", summary: "connect hooks"}' in navigation
    assert 'const isWallet = normalizedApp === "wallet";' in routing
    assert 'walletApp.style.display = isWallet ? "grid" : "none"' in routing
    assert "initWalletApp();" in routing


def test_wallet_ui_has_buttons_status_surfaces_and_ethers_loader() -> None:
    html = WALLET_HTML.read_text(encoding="utf-8")
    bindings = WALLET_BINDINGS_JS.read_text(encoding="utf-8")
    css = WALLET_CSS.read_text(encoding="utf-8")

    expected_ids = [
        'id="wallet-app"',
        'id="wallet-status-pill"',
        'id="wallet-connect-button"',
        'id="wallet-disconnect-button"',
        'id="wallet-reset-log-button"',
        'id="wallet-hook-state"',
        'id="wallet-provider-state"',
        'id="wallet-last-action"',
        'id="wallet-event-log"',
        'id="wallet-agent-credit-form"',
        'id="wallet-agent-credit-hub-url"',
        'id="wallet-agent-credit-recipient"',
        'id="wallet-agent-credit-amount"',
        'id="wallet-agent-credit-memo"',
        'id="wallet-agent-credit-grant-button"',
        'id="wallet-agent-credit-status"',
        'id="wallet-agent-credit-last-grant"',
        'id="wallet-agent-credit-list"',
    ]
    for expected_id in expected_ids:
        assert expected_id in html

    assert "ethers@6.16.0/dist/ethers.umd.min.js" in html
    assert "ethers BrowserProvider" in html
    assert "stable signer/network reads" in html
    assert "Agent Helper Credits" in html
    assert "small Compute Credit balance" in html
    assert "const walletApp = document.querySelector" in bindings
    assert "const walletConnectButton = document.querySelector" in bindings
    assert "const walletDisconnectButton = document.querySelector" in bindings
    assert "const walletAgentCreditForm = document.querySelector" in bindings
    assert "const walletAgentCreditGrantButton = document.querySelector" in bindings
    assert ".wallet-app" in css
    assert ".wallet-actions" in css
    assert ".wallet-agent-credit-form" in css
    assert ".wallet-agent-credit-list" in css


def test_wallet_app_uses_ethers_for_connect_disconnect_and_chain_policy() -> None:
    js = WALLET_JS.read_text(encoding="utf-8")
    html = WALLET_HTML.read_text(encoding="utf-8")

    assert "function initWalletApp()" in js
    assert "async function requestWalletConnectHook" in js
    assert "async function requestWalletDisconnectHook" in js
    assert "async function walletWaitForStableProvider" in js
    assert "async function walletProviderSnapshot" in js
    assert "async function walletEnsureExpectedChain" in js
    assert "async function walletRefreshProviderStateAfterEvent" in js
    assert "function walletBrowserProvider()" in js
    assert "new ethersLib.BrowserProvider" in js
    assert ".getSigner()" in js
    assert ".getNetwork()" in js
    assert ".listAccounts()" in js
    assert '.send("wallet_switchEthereumChain"' in js
    assert '.send("wallet_addEthereumChain"' in js
    assert '.send("wallet_revokePermissions"' in js
    assert 'const WALLET_DEV_CHAIN_ID_HEX = "0x28757b2";' in js
    assert "walletChainMatchesExpected" in js
    assert "walletConnectButton.addEventListener" in js
    assert "walletDisconnectButton.addEventListener" in js
    assert "walletNextOperation()" in js
    assert "walletOperationIsCurrent(token)" in js
    assert "connect.ethers.getSigner.start" in js
    assert "connect.ethers.getSigner.resolved" in js
    assert "ethers.sample.after-connect" in js
    assert "connect.finalized.connected" in js
    assert "disconnect.ethers.revokePermissions.start" in js
    assert "disconnect.done" in js
    assert 'const WALLET_AGENT_CREDIT_GRANT_ENDPOINT = "/api/applications/wallet/agent-credit-grants";' in js
    assert "async function hydrateWalletAgentCreditGrants" in js
    assert "async function requestWalletAgentCreditGrant" in js
    assert "walletAgentCreditForm.addEventListener" in js
    assert "agent-credit-grant.issued" in js
    assert "hydrateAgentCreditGrants" in js
    assert "requestAgentCreditGrant" in js
    assert "wallet.accountsChanged.observed" in js
    assert "wallet.chainChanged.observed" in js
    assert "Force Disconnect / Reset" in js
    assert "Local state cleared. If the wallet still has a pending popup, close it manually." in js
    assert "Connect uses ethers BrowserProvider" in html

    forbidden = [
        "window.ethereum.request",
        ".request({",
        "eth_requestAccounts",
        "eth_chainId",
        "provider.accountsChanged.observed-only",
        "provider.chainChanged.observed-only",
        "MetaMask",
        "Verify / Finalize",
        "document.hasFocus",
        "window.addEventListener(\"focus\"",
        "window.addEventListener(\"blur\"",
    ]
    for token in forbidden:
        assert token not in js


def test_wallet_agent_credit_grant_api_contract_is_routed_to_hub_admin_issue() -> None:
    dispatch = VIEWPORT_ROUTE_DISPATCH.read_text(encoding="utf-8")
    energy = VIEWPORT_ROUTES_ENERGY.read_text(encoding="utf-8")

    assert 'route_path == "/api/applications/wallet/agent-credit-grants"' in dispatch
    assert "self._handle_wallet_agent_credit_grants_load()" in dispatch
    assert "self._handle_wallet_agent_credit_grant_create()" in dispatch

    assert "def _handle_wallet_agent_credit_grants_load" in energy
    assert "def _handle_wallet_agent_credit_grant_create" in energy
    assert "def _post_wallet_agent_credit_grant_to_hub" in energy
    assert '"/api/hub/v1/credits/admin/issue"' in energy
    assert '"source": "wallet_agent_credit_grant"' in energy
    assert '"agent_credit_grant": True' in energy
    assert "credits must be between 1 and 100" in energy

