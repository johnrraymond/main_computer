from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER_HTML = REPO_ROOT / "main_computer" / "web" / "applications" / "apps" / "worker.html"
WORKER_CSS = REPO_ROOT / "main_computer" / "web" / "applications" / "styles" / "worker.css"
WORKER_JS = REPO_ROOT / "main_computer" / "web" / "applications" / "scripts" / "worker.js"
WORKER_BINDINGS_JS = REPO_ROOT / "main_computer" / "web" / "applications" / "scripts" / "dom-bindings" / "worker.js"
VIEWPORT_ROUTES = REPO_ROOT / "main_computer" / "viewport_route_dispatch.py"
VIEWPORT_ENERGY_ROUTES = REPO_ROOT / "main_computer" / "viewport_routes_energy.py"


def test_worker_app_keeps_buy_and_sell_concerns_in_one_clear_worker_surface() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    css = WORKER_CSS.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")

    assert 'class="worker-market-tabs"' in html
    assert 'href="#worker-sell-work"' in html
    assert 'href="#worker-use-remote-workers"' in html
    assert "Sell Work" in html
    assert "How others pay me" in html
    assert "Use Remote Workers" in html
    assert "How I pay others" in html

    # The Worker surface owns both marketplace policies, but the labels make it
    # clear which side pays and which side gets paid.
    assert 'class="worker-pane worker-seller' in html
    assert 'class="worker-pane worker-buyer' in html
    assert 'class="worker-pane worker-hubs' in html
    assert "Configure how other hub users pay this machine" in html
    assert "Configure how this machine is allowed to pay other workers" in html
    assert "Enable paid overflow" in html
    assert "Max credits per token" in html
    assert "Single requester-side number used for estimated input and output tokens for now." in html
    assert "Show the count to the user, not the workers' private minimum prices." in html

    # Remote overflow is a privacy-preserving availability check, not a lowest-price browser.
    assert "Lowest compatible offer" not in html
    assert "lowest price" not in html.lower()
    assert "future/requester concern" not in html

    # The layout presents sell and buy as peer marketplace tabs, then hub
    # connection support below; it does not regress to the old skinny rail.
    assert '"tabs tabs"' in css
    assert '"seller buyer"' in css
    assert '"hubs hubs"' in css
    assert '"seller seller"' not in css
    assert '"hubs buyer"' not in css
    assert ".worker-market-tabs" in css
    assert ".worker-market-tab-remote" in css
    assert ".worker-remote-policy" in css
    assert ".worker-remote-flow ol" in css
    assert "container-type: inline-size" in css
    assert "@container (max-width: 1150px)" in css

    # The remote payment policy is stored locally with the rest of the Worker market settings.
    assert "main-computer-worker-settings-v4" in js
    assert "remoteCreditsPerToken" in js
    assert "remoteMaxOutputTokens" in js
    assert "remoteAskBeforeSpend" in js
    assert "workerRemoteCreditsPerToken" in bindings
    assert "workerRemoteMaxOutputTokens" in bindings
    assert "workerRemoteAskBeforeSpend" in bindings


def test_worker_offer_registration_ui_posts_through_local_proxy() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")
    dispatch = VIEWPORT_ROUTES.read_text(encoding="utf-8")
    energy_routes = VIEWPORT_ENERGY_ROUTES.read_text(encoding="utf-8")

    assert 'id="worker-registration-hub"' in html
    assert 'id="worker-endpoint"' in html
    assert 'id="worker-register-offer"' in html
    assert 'id="worker-registered-offer-id"' in html

    assert "buildWorkerOfferRegistrationPayload" in js
    assert 'pricing_type: "fixed_per_call_v0"' in js
    assert 'unit: "compute_credit"' in js
    assert 'mode: settings.executionMode' in js
    assert '"/api/applications/worker/register-offer"' in js
    assert '"/api/applications/worker/hub-health"' in js

    assert "workerRegistrationHub" in bindings
    assert "workerEndpoint" in bindings
    assert "workerRegisterOffer" in bindings
    assert "workerRegisteredOfferId" in bindings

    assert '"/api/applications/worker/register-offer"' in dispatch
    assert "self._handle_worker_offer_register()" in dispatch
    assert '"/api/applications/worker/hub-health"' in dispatch
    assert "self._handle_worker_hub_health()" in dispatch
    assert '"/api/hub/v1/workers/register"' in energy_routes
    assert "phase12_worker_seller_offer_ui" in energy_routes
    assert "Worker offer registration is only available to local viewport clients." in energy_routes


def test_worker_phase_one_bridge_readiness_reuses_existing_faucet_and_keeps_keys_visible() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    css = WORKER_CSS.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")
    dispatch = VIEWPORT_ROUTES.read_text(encoding="utf-8")

    assert 'id="worker-bridge-readiness-card"' in html
    assert 'id="worker-connect-wallet"' in html
    assert 'id="worker-disconnect-wallet"' in html
    assert 'id="worker-create-bridge-account"' in html
    assert 'id="worker-recovery-card"' in html
    assert 'id="worker-confirm-recovery"' in html

    assert 'id="worker-faucet-card"' in html
    assert 'id="worker-request-faucet"' in html
    assert 'id="worker-faucet-amount"' in html
    assert "Request credits through the existing faucet" in html
    assert '"/api/xlag/dev/faucet"' in js
    assert "amount_credits" in js
    assert "POST" in js
    assert 'self.path == "/api/xlag/dev/faucet"' in dispatch

    assert 'id="worker-multisession-card"' in html
    assert "Visible before wallet connection" in html
    assert 'id="worker-request-multisession-key"' in html
    assert 'id="worker-revoke-multisession-key"' in html
    assert "main-computer-worker-bridge-readiness-v1" in js
    assert "function requestWorkerMultisessionKey()" in js
    assert "function revokeWorkerMultisessionKey()" in js
    assert "No active multi-session key to revoke." in js
    assert "You can request a new key now." in js

    assert "workerRequestFaucet" in bindings
    assert "workerDisconnectWallet" in bindings
    assert "workerRequestMultisessionKey" in bindings
    assert "workerRevokeMultisessionKey" in bindings
    assert ".worker-bridge-card" in css
    assert ".worker-token-list" in css

    assert "not Hub-spendable" not in html
    assert "Hub-spendable" not in js

def test_worker_wallet_connect_is_single_flight_and_dev_chain_aware() -> None:
    js = WORKER_JS.read_text(encoding="utf-8")

    assert "let workerWalletConnectInFlight = null" in js
    assert "let workerWalletDisconnectInFlight = null" in js
    assert "let workerWalletProviderSyncInFlight = null" in js
    assert "let workerWalletOperationSerial = 0" in js
    assert "if (workerWalletConnectInFlight)" in js
    assert "Wallet connection already in progress." in js
    assert "Wallet is already connected. Use Disconnect Wallet before choosing a different account." in js
    assert "Opening MetaMask account chooser..." in js
    assert '"wallet_requestPermissions"' in js
    assert "workerRequestFreshWalletPermission" in js
    assert "Wallet account changed during connect" in js
    assert 'workerConnectWallet.setAttribute("aria-busy", "true")' in js
    assert 'workerConnectWallet.removeAttribute("aria-busy")' in js

    assert "function workerBindWalletProviderEvents()" in js
    assert '"accountsChanged"' in js
    assert '"chainChanged"' in js
    assert "workerScheduleWalletProviderSync" in js
    assert "workerReadWalletProviderSnapshot" in js
    assert "Worker kept" in js
    assert "Use Disconnect Wallet before changing accounts." in js
    assert "workerWalletOperationIsCurrent(token)" in js
    assert "return Boolean(workerWalletConnectInFlight || workerWalletDisconnectInFlight);" in js

    assert "async function workerEnsureExpectedWalletChain()" in js
    assert '"/api/xlag/contract/status"' in js
    assert '"wallet_switchEthereumChain"' in js
    assert '"wallet_addEthereumChain"' in js
    assert "workerNormalizeChainHex(chainId)" in js


def test_worker_wallet_has_local_disconnect_for_repeatable_ui_testing() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")

    assert 'id="worker-disconnect-wallet"' in html
    assert "Disconnect Wallet" in html
    assert "workerDisconnectWallet" in bindings
    assert "async function workerDisconnectPrimaryWallet()" in js
    assert '"wallet_revokePermissions"' in js
    assert "workerRevokeWalletPermission" in js
    assert "Connect Wallet should ask MetaMask again." in js
    assert "use MetaMask's site disconnect if reconnect does not prompt" in js
    assert "workerBridgeState.wallet = {...workerDefaultBridgeState().wallet}" in js
    assert "workerDisconnectWallet.addEventListener" in js
    assert "workerDisconnectWallet.disabled = busy || !connected" in js
