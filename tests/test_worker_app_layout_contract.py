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

    assert 'class="worker-market-tabs"' not in html
    assert 'class="worker-market-tab"' not in html
    assert 'href="#worker-sell-work"' not in html
    assert 'href="#worker-use-remote-workers"' not in html
    assert "Sell Work" in html
    assert "How others pay me" in html
    assert "Use Remote Workers" in html
    assert "How I pay others" in html
    assert html.index('id="worker-sell-work"') < html.index('id="worker-use-remote-workers"')

    # The Worker surface owns both marketplace policies, but the labels make it
    # clear which side pays and which side gets paid.
    assert '<main class="worker-pane worker-seller' not in html
    assert '<section class="worker-pane worker-seller' in html
    assert 'class="worker-pane worker-buyer' in html
    assert 'class="worker-pane worker-hubs' in html
    assert "Configure how other hub users pay this machine" in html
    assert "Configure how this machine is allowed to pay other workers" in html
    assert "Enable paid overflow" in html
    assert "Max credits per estimated token" in html
    assert 'id="worker-remote-credits-per-token" type="number" min="0.000001" step="0.001" value="0.001"' in html
    assert "Approximation only: this ceiling is applied to estimated input and output tokens for now." in html
    assert "Approximation only: used with the prompt estimate to compute the maximum remote request budget." in html
    assert "Busy-local overflow flow" not in html
    assert "Show the count to the user, not the workers' private minimum prices." not in html

    # Remote overflow is a privacy-preserving availability check, not a lowest-price browser.
    assert "Lowest compatible offer" not in html
    assert "lowest price" not in html.lower()
    assert "future/requester concern" not in html

    # The sell pane must not be a nested <main>: the application shell applies
    # taskbar-reserved padding to main elements, which would shove the seller
    # content to the right and starve the form for width.
    assert html.count("<main") == 0

    seller_section = html[html.index('id="worker-sell-work"') : html.index('id="worker-use-remote-workers"')]
    network_surface = html[html.index('class="worker-network-surface"') : html.index('id="worker-sell-work"')]
    assert 'class="worker-card worker-connect-order-card"' in seller_section
    assert "Signed Worker Connection" in seller_section
    assert "Signed Worker Connection" not in network_surface
    assert seller_section.index("How others pay me") < seller_section.index("Signed Worker Connection")
    assert '<select id="worker-registration-hub"' not in seller_section
    assert 'id="worker-registration-hub-status"' in seller_section
    assert "Comes from the Signed Worker Connection below; this hub is not edited here." in seller_section
    assert 'id="worker-registration-hub" type="hidden"' in seller_section
    assert 'id="worker-node-id"' not in seller_section
    assert 'id="worker-endpoint"' not in seller_section
    assert 'id="worker-offer-capability"' not in seller_section
    assert 'id="worker-max-concurrency"' not in seller_section
    assert 'id="worker-execution-mode"' not in seller_section
    assert 'id="worker-offer-models" type="text" value="mock-ai-model-phase9" autocomplete="off" disabled aria-disabled="true"' in seller_section
    assert 'id="worker-offer-price" type="number" min="1" step="1" value="5500123"' in seller_section
    assert 'class="worker-card worker-contract-summary"' not in seller_section
    assert "Seller offer contract this UI registers" not in html
    assert "deterministic worker-pull test path" not in html
    assert ".worker-contract-summary" not in css
    assert 'id="worker-rental-enabled"' in seller_section
    assert 'Accept paid jobs' in seller_section
    assert 'id="worker-seller-only-when-idle" checked' in seller_section
    assert 'Only accept jobs when idle' in seller_section
    assert seller_section.index('id="worker-rental-enabled"') < seller_section.index('id="worker-seller-only-when-idle"')
    assert "workerSellerOnlyWhenIdle" in js
    assert "sellerOnlyWhenIdle" in js
    assert "rentalOnlyWhenIdle" in js
    assert 'const workerSellerOnlyWhenIdle = document.querySelector("#worker-seller-only-when-idle");' in bindings
    assert ".worker-seller-controls" in css

    assert '<option value="2">Ring 2 - Public</option>' in html
    assert '<option value="2" selected>Ring 2' not in html
    assert '<option value="3" selected>Ring 3 - Public untrusted</option>' in html
    assert '<dd id="worker-network-requested-ring">Ring 3 - Public untrusted</dd>' in html
    assert 'const WORKER_DEFAULT_RING = "3";' in js
    assert '"3": "Ring 3 - Public untrusted"' in js
    assert '"2": "Ring 2 - Public"' in js
    energy_routes = VIEWPORT_ENERGY_ROUTES.read_text(encoding="utf-8")
    assert "collect_windows_user_activity" in energy_routes
    assert "Only accept jobs when idle is enabled" in energy_routes
    # The layout presents selling first, then buying remote work below it,
    # because a worker must be sell-ready before it can safely use others.
    assert "grid-template-columns: minmax(0, 1fr);" in css
    assert '        "seller"\n        "buyer"\n        "hubs";' in css
    assert '"seller buyer"' not in css
    assert '"tabs tabs"' not in css
    assert '"hubs hubs"' not in css
    assert '"seller seller"' not in css
    assert '"hubs buyer"' not in css
    assert css.index('"seller"') < css.index('"buyer"')
    assert ".worker-market-tabs" not in css
    assert ".worker-market-tab-remote" not in css
    assert ".worker-seller > .worker-pane-head" in css
    assert "grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));" in css
    assert ".worker-remote-policy" in css
    assert ".worker-remote-flow" not in css
    assert "container-type: inline-size" in css
    assert "@container (max-width: 1150px)" in css

    # The remote payment policy is saved to the local backend so Chat Console
    # readiness does not rely on browser localStorage as credit/key truth.
    assert '"/api/applications/worker/settings"' in js
    assert "workerLoadSettingsFromBackend" in js
    assert "applyWorkerSettings" in js
    assert "remoteCreditsPerToken" in js
    assert 'workerPositiveDecimalString(workerElementValue(workerRemoteCreditsPerToken, "0.001"), "0.001")' in js
    assert "workerApplyRemoteEnabledFromBackend(parsed.remoteEnabled, {requestStartedAt})" in js
    assert "workerPollRemoteEnabledFromBackend" in js
    assert "WORKER_SETTINGS_POLL_INTERVAL_MS = 2500" in js
    assert "workerRemoteEnabledLastLocalEditAt" in js
    assert "startedAt < workerRemoteEnabledLastLocalEditAt" in js
    assert 'bindWorkerAutosaveSetting(workerRemoteEnabled, "change", ["remoteEnabled"])' in js
    assert 'bindWorkerAutosaveSetting(workerRemoteCreditsPerToken, "input", ["remoteCreditsPerToken"])' in js
    assert "changed_fields" in js
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

    assert 'id="worker-registration-hub-status"' in html
    assert 'id="worker-registration-hub" type="hidden"' in html
    assert '<select id="worker-registration-hub"' not in html
    assert 'id="worker-node-id"' not in html
    assert 'id="worker-endpoint"' not in html
    assert 'id="worker-offer-capability"' not in html
    assert 'id="worker-max-concurrency"' not in html
    assert 'id="worker-execution-mode"' not in html
    assert 'id="worker-register-offer"' in html
    assert 'id="worker-registered-offer-id"' in html

    assert "buildWorkerOfferRegistrationPayload" in js
    assert "Select a worker connection below." in js
    assert 'pricing_type: "fixed_per_call_v0"' in js
    assert 'unit: "compute_credit"' in js
    assert 'mode: settings.executionMode' in js
    assert '"/api/applications/worker/register-offer"' in js
    assert '"/api/applications/worker/hub-health"' in js
    assert '"/api/applications/worker/settings"' in js
    assert '"/api/applications/worker/multisession-key/request"' in js
    assert '"/api/applications/worker/multisession-keys/load"' in js
    assert '"/api/applications/worker/wallet-balance"' in js
    assert '"/api/applications/worker/wallet-funding/config"' in js
    assert '"/api/applications/worker/wallet-funding/complete"' in js
    assert '"/api/applications/worker/wallet-funding/balance"' in js
    assert "requestMultiSessionKeySignature" in js
    assert "workerLoadMultisessionKeysForWallet" in js

    assert "workerRegistrationHub" in bindings
    assert "workerRegistrationHubStatus" in bindings
    assert "workerRegisterOffer" in bindings
    assert "workerRegisteredOfferId" in bindings

    assert '"/api/applications/worker/register-offer"' in dispatch
    assert "self._handle_worker_offer_register()" in dispatch
    assert '"/api/applications/worker/hub-health"' in dispatch
    assert "self._handle_worker_hub_health()" in dispatch
    assert '"/api/applications/worker/settings"' in dispatch
    assert "self._handle_worker_settings_load()" in dispatch
    assert "self._handle_worker_settings_save()" in dispatch
    assert "changed_fields" in energy_routes
    assert "changed_fields=changed_fields" in energy_routes
    assert '"/api/applications/worker/multisession-key/request"' in dispatch
    assert "self._handle_worker_multisession_key_request()" in dispatch
    assert '"/api/applications/worker/multisession-keys/load"' in dispatch
    assert "self._handle_worker_multisession_keys_load()" in dispatch
    assert '"/api/applications/worker/wallet-balance"' in dispatch
    assert "self._handle_worker_wallet_balance()" in dispatch
    assert '"/api/applications/worker/wallet-funding/config"' in dispatch
    assert "self._handle_worker_wallet_funding_config()" in dispatch
    assert '"/api/applications/worker/wallet-funding/complete"' in dispatch
    assert "self._handle_worker_wallet_funding_complete()" in dispatch
    assert '"/api/applications/worker/wallet-funding/balance"' in dispatch
    assert "self._handle_worker_wallet_funding_balance()" in dispatch
    assert '"/api/hub/v1/credits/multisession-keys/request"' in energy_routes
    assert '"/api/hub/v1/credits/wallet-funding/complete"' in energy_routes
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
    assert 'id="worker-hub-credit-contract"' not in html
    assert 'id="worker-hub-credit-escrow-address"' in html
    assert 'id="worker-hub-credit-amount"' in html
    assert 'id="worker-hub-credit-wallet-balance"' in html
    assert 'id="worker-check-hub-credit-balance"' in html
    assert 'id="worker-fund-hub-credit"' in html
    assert "My Bridge Account" in html
    assert "Money on the machine is money on the bridge." in html
    assert "Bridge address" not in html
    assert "Bridge contract" in html
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
    assert 'WORKER_DEV_CHAIN_RPC_URL = "http://127.0.0.1:18545"' in js
    assert 'WORKER_DEV_CHAIN_NAME = "Main Computer Dev Chain"' in js
    assert 'WORKER_FAUCET_AMOUNT_CREDITS = "1"' in js
    assert "workerFaucetInFlight" in js
    assert "workerFaucetLastResult" in js
    assert "workerFaucetLastError" in js
    assert "function workerComputeFaucetReadiness()" in js
    assert "workerRefreshFaucetRuntimeStatus" in js
    assert "amount_credits: WORKER_FAUCET_AMOUNT_CREDITS" in js
    assert "WORKER_HUB_CREDIT_BRIDGE_ESCROW_ABI" in js
    assert "function workerComputeHubCreditFundingReadiness()" in js
    assert "workerRefreshHubCreditBridgeConfig" in js
    assert "workerRequireBridgeContractCode" in js
    assert "provider.getCode(address)" in js
    assert "Enter the bridge address." not in js
    assert "Bridge deployment config is missing the escrow contract address." in js
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
    assert "workerHubCreditContract" not in bindings
    assert "workerHubCreditEscrowAddress" in bindings
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


def test_worker_network_tabs_drive_selected_network_session() -> None:
    html = WORKER_HTML.read_text(encoding="utf-8")
    css = WORKER_CSS.read_text(encoding="utf-8")
    js = WORKER_JS.read_text(encoding="utf-8")
    bindings = WORKER_BINDINGS_JS.read_text(encoding="utf-8")
    dispatch = VIEWPORT_ROUTES.read_text(encoding="utf-8")
    energy_routes = VIEWPORT_ENERGY_ROUTES.read_text(encoding="utf-8")

    assert 'class="worker-network-tabs"' in html
    assert 'data-worker-network="mainnet"' in html
    assert 'data-worker-network="testnet"' in html
    assert 'data-worker-network="test"' in html
    assert 'data-worker-network="dev"' in html
    assert 'data-worker-network="none"' in html
    assert html.index('data-worker-network="mainnet"') < html.index('data-worker-network="testnet"') < html.index('data-worker-network="test"') < html.index('data-worker-network="dev"') < html.index('data-worker-network="none"')
    assert 'id="worker-network-ring"' in html
    assert 'id="worker-network-sign-order"' in html
    assert 'id="worker-network-connect-wallet"' not in html
    assert "Network selection handles wallet connection" in html
    assert 'id="worker-network-disconnect"' in html
    assert "None / Full Disconnect" in html
    assert "All four worker targets are visible by default" in html

    assert "workerNetworkTabs" in bindings
    assert "workerNetworkRing" in bindings
    assert "workerNetworkSignOrder" in bindings
    assert "workerNetworkConnectWallet" not in bindings
    assert "workerFleetMainnet" in bindings

    assert ".worker-network-surface" in css
    assert ".worker-network-tab.is-selected" in css
    assert ".worker-network-tab-none" in css

    assert "WORKER_NETWORK_ORDER = [\"mainnet\", \"testnet\", \"test\", \"dev\"]" in js
    assert 'WORKER_NETWORK_NONE = "none"' in js
    assert '"/api/applications/worker/network-session"' in js
    assert '"/api/applications/worker/network-connect-order"' in js
    assert "workerLoadNetworkSessionFromBackend" in js
    assert "workerSelectNetwork" in js
    assert "signWorkerNetworkConnectOrder" in js
    assert "workerBuildConnectOrderMessage" in js
    assert "workerSelectedWalletChainIdHex" in js
    assert "workerSelectedWalletRpcUrl" in js
    assert "workerNetworkWalletConnectedToSelected" in js
    assert "workerSelectNetworkAndConnectWallet" in js
    assert "workerDisconnectSelectedNetworkAndWallet" in js
    assert "Wallet required" in js
    assert "Connect your wallet to ${workerNetworkDisplayName(selected)} before accepting jobs." in js
    assert "workerNetworkConnectWallet" not in js
    assert "workerNetworkSignOrder.disabled = !workerNetworkCanSign()" in js
    assert "workerNetworkWalletConnectedToSelected()" in js

    assert "WORKER_STATUS_MESSAGE_MAX_LENGTH = 260" in js
    assert "function workerSetSaveStatus" in js
    assert "function workerUserFacingWalletErrorMessage" in js
    assert "Wallet signature request was rejected." in js
    assert "Worker connect order signing failed: ${error.message || error}" not in js
    assert "#worker-save-status" in css
    assert "Keep wallet/provider errors from widening the worker surface." in css
    assert "overflow-wrap: anywhere" in css
    assert "word-break: break-word" in css

    assert '"/api/applications/worker/network-session"' in dispatch
    assert "self._handle_worker_network_session_load()" in dispatch
    assert "self._handle_worker_network_session_select()" in dispatch
    assert '"/api/applications/worker/network-connect-order"' in dispatch
    assert "self._handle_worker_network_connect_order_sign()" in dispatch

    assert "load_hub_network_registry" in energy_routes
    assert "def _handle_worker_network_session_load" in energy_routes
    assert "def _handle_worker_network_session_select" in energy_routes
    assert "def _handle_worker_network_connect_order_sign" in energy_routes
    assert '"mainnet", "testnet", "test", "dev"' in energy_routes
    assert '"selectedNetwork": selected_network' in energy_routes
    assert '"workerRequestedRing": requested_ring' in energy_routes
    assert 'requested_ring = text(settings.get("workerRequestedRing", settings.get("worker_requested_ring")), "3")' in energy_routes
    assert 'if requested_ring not in {"0", "1", "2", "3"}:' in energy_routes
    assert '{"ring": "3", "label": "Ring 3 - Public untrusted", "description": "public untrusted workers"}' in energy_routes
    assert 'raise ValueError("Worker ring must be one of 0, 1, 2, or 3.")' in energy_routes

    # Cloudflare rejects bare Python urllib requests to the public Hub with 403/1010.
    # Worker Hub probes must send an explicit worker User-Agent and JSON Accept header.
    assert "def _hub_json_request_headers" in energy_routes
    assert '"User-Agent": "MainComputerWorker/0.1"' in energy_routes
    assert '"Accept": "application/json"' in energy_routes
    assert 'headers=self._hub_json_request_headers()' in energy_routes
    assert 'urlopen(self._clean_hub_url(hub_url) + "/api/hub/status"' not in energy_routes


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
    assert '"metamask_getProviderState"' in js
    assert '"eth_blockNumber"' in js
    assert "workerWalletMetadataNeedsRpcRepair" in js
    assert "workerProveInjectedProviderRpc" in js
    assert "workerProveInjectedProviderRpcWithBackoff" in js
    assert "workerRequestDevWalletChainUpdate" in js
    assert "WORKER_METAMASK_RPC_BACKOFF_TIMEOUT_MS = 75000" in js
    assert "WORKER_METAMASK_RPC_BACKOFF_POLL_MS = 3000" in js
    assert 'WORKER_DEV_CHAIN_CURRENCY_SYMBOL = "MCXLAG"' in js
    assert 'WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL = "ENG"' in js
    assert "workerWalletIsNativeCurrencySymbolMismatch" in js
    assert "connect.wallet.addChain.symbolMismatchFallback" in js
    assert "canonical-mcxlag" in js
    assert "legacy-symbol-rpc-repair" in js
    assert "provider.hydrate.rpc-needs-repair" in js
    assert "connect.wallet.networkPreflight.start" in js
    assert "connect.wallet.rpcProof.done" in js
    assert "connect.wallet.rpcProof.backoffWait" in js
    assert "connect.wallet.rpcProof.backoffCleared" in js
    assert "workerWalletIsRpcEndpointBackoff" in js
    assert "workerWalletRpcBackoffMessage" in js
    assert "async function workerBrowserProviderSend" in js
    assert "browserProvider.send(method, params)" in js
    assert "workerInjectedProviderRequest" not in js
    assert "injectedProvider.request" not in js
    assert "funding-preflight" in js
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
    assert "Wallet accepted. Verifying signer and selected worker network with ethers." in js
    assert "Select Mainnet, Testnet, Test, or Dev before connecting the worker wallet." in js

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

