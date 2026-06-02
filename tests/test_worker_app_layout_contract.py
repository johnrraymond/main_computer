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
    assert '"/api/applications/worker/multisession-key/request"' in js
    assert '"/api/applications/worker/multisession-keys/load"' in js
    assert '"/api/applications/worker/wallet-balance"' in js
    assert '"/api/applications/worker/wallet-funding/import"' in js
    assert '"/api/applications/worker/wallet-funding/balance"' in js
    assert "requestMultiSessionKeySignature" in js
    assert "workerLoadMultisessionKeysForWallet" in js

    assert "workerRegistrationHub" in bindings
    assert "workerEndpoint" in bindings
    assert "workerRegisterOffer" in bindings
    assert "workerRegisteredOfferId" in bindings

    assert '"/api/applications/worker/register-offer"' in dispatch
    assert "self._handle_worker_offer_register()" in dispatch
    assert '"/api/applications/worker/hub-health"' in dispatch
    assert "self._handle_worker_hub_health()" in dispatch
    assert '"/api/applications/worker/multisession-key/request"' in dispatch
    assert "self._handle_worker_multisession_key_request()" in dispatch
    assert '"/api/applications/worker/multisession-keys/load"' in dispatch
    assert "self._handle_worker_multisession_keys_load()" in dispatch
    assert '"/api/applications/worker/wallet-balance"' in dispatch
    assert "self._handle_worker_wallet_balance()" in dispatch
    assert '"/api/applications/worker/wallet-funding/import"' in dispatch
    assert "self._handle_worker_wallet_funding_import()" in dispatch
    assert '"/api/applications/worker/wallet-funding/balance"' in dispatch
    assert "self._handle_worker_wallet_funding_balance()" in dispatch
    assert '"/api/hub/v1/credits/multisession-keys/request"' in energy_routes
    assert '"/api/hub/v1/credits/wallet-funding/import"' in energy_routes
    assert "/api/hub/v1/credits/balance" in energy_routes
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
    removed_account_button_id = 'id="worker-create-' + 'bridge-' + 'account"'
    removed_account_button_label = "Create / Load " + "Bridge " + "Account"
    assert removed_account_button_id not in html
    assert removed_account_button_label not in html
    assert 'id="worker-recovery-card"' in html
    assert 'id="worker-confirm-recovery"' in html

    assert 'id="worker-faucet-card"' in html
    assert 'id="worker-request-faucet"' in html
    assert 'id="worker-faucet-readiness"' in html
    assert 'id="worker-faucet-disabled-reason"' in html
    assert 'id="worker-faucet-result-tx"' in html
    assert 'id="worker-faucet-result-from"' in html
    assert 'id="worker-faucet-result-to"' in html
    assert 'id="worker-faucet-result-amount"' in html
    assert 'id="worker-faucet-result-chain"' in html
    assert 'id="worker-faucet-result-runtime"' in html
    assert 'id="worker-hub-credit-card"' in html
    assert 'id="worker-hub-credit-contract"' in html
    assert 'id="worker-hub-credit-amount"' in html
    assert 'id="worker-hub-credit-wallet-balance"' in html
    assert 'id="worker-check-hub-credit-balance"' in html
    assert 'id="worker-fund-hub-credit"' in html
    assert "My Bridge Account" in html
    assert "Money on the machine is money on the bridge." in html
    assert "Hub Wallet Credit" not in html
    assert "Fund Hub Wallet Credit" not in html
    assert "Fund Hub Wallet Credit" not in js
    assert html.index('id="worker-hub-credit-card"') < html.index('class="worker-card worker-field-grid worker-remote-policy"')
    bridge_card_end = html.index('class="worker-card worker-field-grid worker-remote-policy"')
    bridge_card_start = html.index('id="worker-hub-credit-card"')
    bridge_slice = html[bridge_card_start:bridge_card_end]
    assert "Recovery Methods" not in bridge_slice
    assert "Multi-session Key" not in bridge_slice
    assert "Request 1 local dev-chain credit" in html
    assert "Request Faucet Funds" in html
    assert "verified ethers wallet state" in html
    assert '"/api/xlag/dev/faucet"' in js
    assert "WORKER_DEV_CHAIN_ID_DECIMAL = 42424242" in js
    assert 'WORKER_DEV_CHAIN_ID_HEX = "0x28757b2"' in js
    assert 'WORKER_FAUCET_AMOUNT_CREDITS = "1"' in js
    assert "workerFaucetInFlight" in js
    assert "workerFaucetLastResult" in js
    assert "workerFaucetLastError" in js
    assert "function workerComputeFaucetReadiness()" in js
    assert "workerRefreshFaucetRuntimeStatus" in js
    assert "amount_credits: WORKER_FAUCET_AMOUNT_CREDITS" in js
    assert "WORKER_HUB_CREDIT_BRIDGE_ESCROW_ABI" in js
    assert "function workerComputeHubCreditFundingReadiness()" in js
    assert "async function fundWorkerHubCredit" in js
    assert "async function checkWorkerHubCreditBalance" in js
    assert "async function checkWorkerWalletCreditBalance" in js
    assert "WORKER_WALLET_BALANCE_TIMEOUT_MS = 8000" in js
    assert "workerReadWalletBalanceFromLocalRpc" in js
    assert "workerReadWalletBalanceFromInjectedProvider" in js
    assert '"/api/applications/worker/wallet-balance"' in js
    assert '"eth_getBalance"' in js
    assert "Wallet RPC unavailable." in js
    assert "Could not read my wallet balance. MetaMask says the RPC endpoint is failing" in js
    assert "Checking automatically" not in js
    assert "browserProvider.getBalance(walletAddress)" not in js
    assert "Check my wallet balance before funding." in js
    assert "Request Faucet Funds for this wallet before funding the bridge." in js
    assert "0 credits available — Request Faucet Funds first" in js
    assert "Amount exceeds my wallet balance." in js
    assert "walletFunding" in js
    assert "POST" in js
    assert 'route_path == "/api/xlag/dev/faucet"' in dispatch
    assert "xlag_dev_faucet_status" in dispatch
    assert "api-xlag-dev-faucet-status" in dispatch

    assert 'id="worker-multisession-card"' in html
    assert "Visible before wallet connection" in html
    assert 'id="worker-request-multisession-key"' in html
    assert 'id="worker-revoke-multisession-key"' in html
    assert "main-computer-worker-bridge-readiness-v1" in js
    assert ("bridge" + "Account") not in js
    assert ("bridge" + "_context") not in js
    assert "async function requestWorkerMultisessionKey" in js
    assert "function revokeWorkerMultisessionKey()" in js
    assert "workerMergeLoadedMultisessionKeys" in js
    assert "local-cache.load.start" in js
    assert "No active multi-session key to revoke." in js
    assert "You can request a new key now." in js
    assert "Multi-session key requested and marked active locally." not in js
    assert "workerRandomKeyId" not in js

    assert "workerRequestFaucet" in bindings
    assert "workerHubCreditForm" in bindings
    assert "workerHubCreditContract" in bindings
    assert "workerHubCreditWalletBalance" in bindings
    assert "workerFundHubCredit" in bindings
    assert "workerCheckHubCreditBalance" in bindings
    assert "workerFaucetReadiness" in bindings
    assert "workerFaucetDisabledReason" in bindings
    assert "workerFaucetResultTx" in bindings
    assert "workerDisconnectWallet" in bindings
    assert "workerRequestMultisessionKey" in bindings
    assert "workerRevokeMultisessionKey" in bindings
    assert ".worker-bridge-card" in css
    assert ".worker-token-list" in css

    assert "not Hub-spendable" not in html
    assert "Hub-spendable" not in js


def test_worker_wallet_connect_and_disconnect_use_always_disconnect_cycle() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")

    assert 'id="worker-connect-wallet"' in html
    assert 'id="worker-disconnect-wallet"' in html
    assert "workerConnectWallet" in bindings
    assert "workerDisconnectWallet" in bindings

    assert "async function connectWorkerPrimaryWallet" in js
    assert "async function disconnectWorkerPrimaryWallet" in js
    assert "async function workerGetEthers" in js
    assert "async function workerReadWalletProviderSnapshot" in js
    assert "async function workerEnsureDevWalletChain" in js
    assert "workerConnectWallet.addEventListener" in js
    assert "workerDisconnectWallet.addEventListener" in js
    assert "workerWalletNextOperation()" in js
    assert "workerWalletOperationIsCurrent(token)" in js
    assert "connect.ethers.requestAccounts.start" in js
    assert "connect.ethers.requestAccounts.resolved" in js
    assert "provider.selected" in js
    assert "connect.finalized.connected" in js
    assert "async function workerHydrateConnectedWalletFromProvider" in js
    assert 'workerHydrateConnectedWalletFromProvider("page-load")' in js
    assert "workerReadGrantedWalletProviderSnapshot" in js
    assert '"eth_accounts"' in js
    assert "provider.hydrate.start" in js
    assert "provider.hydrate.connected" in js
    assert "provider.hydrate.no-account" in js
    assert "workerLoadMultisessionKeysForWallet(address, reason)" in js
    assert "disconnect.ethers.revokePermissions.start" in js
    assert "disconnect.done" in js
    assert "provider.accountsChanged.refresh" in js
    assert "provider.chainChanged.refresh" in js
    assert "Force Disconnect / Reset" in js
    assert "Local state cleared. If a wallet popup is still pending, close it manually." in js
    assert "Wallet accepted. Verifying signer and dev chain with ethers." in js

    required_provider_calls = [
        "ethers.BrowserProvider",
        "eth_requestAccounts",
        "wallet_revokePermissions",
        "wallet_switchEthereumChain",
        "wallet_addEthereumChain",
        "eip6963:requestProvider",
    ]
    for token in required_provider_calls:
        assert token in js

    forbidden = [
        "window.ethereum.request",
        "wallet_requestPermissions",
        "Verify / Finalize",
        "document.hasFocus",
        "window.addEventListener(\"focus\"",
        "window.addEventListener(\"blur\"",
        "Wallet connect and disconnect calls are intentionally removed",
        "observed-only",
        "workerWaitForStableWalletProvider",
    ]
    for token in forbidden:
        assert token not in js

    assert "const {wallet: _liveWalletState, ...serializableState}" in js
    assert "workerSetPrimaryWalletState" in js

