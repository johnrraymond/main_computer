    const workerDefaultHubs = [
      {name: "Mainnet Hub", url: "https://mainnet-hub.greatlibrary.io", role: "use-provide", network: "mainnet"},
      {name: "Testnet Hub", url: "https://testnet-hub.greatlibrary.io", role: "use-provide", network: "testnet"},
      {name: "Local QBFT Test Hub", url: "http://127.0.0.1:8780", role: "use-provide", network: "test"},
      {name: "Local Dev Hub", url: "http://127.0.0.1:8871", role: "use-provide", network: "dev"}
    ];
    const WORKER_NETWORK_SESSION_ENDPOINT = "/api/applications/worker/network-session";
    const WORKER_NETWORK_WORK_NOW_ENDPOINT = "/api/applications/worker/work-now";
    const WORKER_RUNTIME_STATUS_ENDPOINT = "/api/applications/worker/runtime-status";
    const WORKER_RUNTIME_SYNC_ENDPOINT = "/api/applications/worker/runtime-sync";
    const WORKER_RUNTIME_SYNC_INTERVAL_MS = 10000;
    const WORKER_STATUS_MESSAGE_MAX_LENGTH = 260;
    const WORKER_NETWORK_ORDER = ["mainnet", "testnet", "test", "dev"];
    const WORKER_NETWORK_NONE = "none";
    const WORKER_DEFAULT_RING = "3";
    const WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN = "0.001";
    const WORKER_DEFAULT_SELLER_TARGET_TOKENS = 1024;
    const WORKER_DEFAULT_SELLER_MODEL = "gemma4:26b";
    const WORKER_SELLER_AVAILABILITY_TOTAL_IDLE = "totally_idle";
    const WORKER_SELLER_AVAILABILITY_AI_IDLE = "ai_idle";
    const WORKER_SELLER_AVAILABILITY_MODES = new Set([
      WORKER_SELLER_AVAILABILITY_TOTAL_IDLE,
      WORKER_SELLER_AVAILABILITY_AI_IDLE
    ]);
    const WORKER_LEGACY_SELLER_CREDITS_PER_REQUESTS = new Set(["5500123", "5500123.0", "5500123.00", "1.25", "1.250", "1.2500"]);
    const WORKER_LEGACY_SELLER_MODELS = new Set(["mock-ai-model-phase9"]);
    const WORKER_CREDIT_BASE_UNITS_PER_CREDIT = 1000000000000000000n;
    const WORKER_RING_LABELS = {
      "0": "Ring 0 - Operator",
      "1": "Ring 1 - Protected",
      "2": "Ring 2 - Public",
      "3": "Ring 3 - Public untrusted"
    };
    let workerSettingsLoaded = false;
    let workerHubs = [...workerDefaultHubs];
    let workerNetworkProfiles = {};
    let workerNetworkSession = {
      selected_network: WORKER_NETWORK_NONE,
      connection_status: "disconnected",
      requested_ring: WORKER_DEFAULT_RING,
      assigned_ring: "",
      worker_id: "",
      pricing_policy: "",
      profile: null,
      signed_connection: {},
      hub_status: null,
      hub_registration: null,
      worker_pool: null,
      connected_hub_url: "",
      connection_error: "",
      connected_at: ""
    };
    let workerNetworkSessionInFlight = false;
    let workerNetworkWorkNowInFlight = false;
    let workerRuntimeStatus = {
      enabled: false,
      status: "not_accepting",
      statusLabel: "Not accepting",
      phase: "not_accepting",
      active_jobs: 0,
      allowed_to_accept: false,
      hub_status: "",
      hubAvailability: "not_announced",
      reason: "",
      next: "",
      identity: null,
      signedOrder: null,
      hubRegistration: null,
      localPolicy: null,
      workNowOverride: null,
      worker: null,
      policy: null,
      last_checked_at: "",
      last_heartbeat_at: "",
      lastError: "",
      heartbeat_error: ""
    };
    let workerRuntimeSyncInFlight = false;
    let workerRuntimeSyncTimer = null;
    let workerWorkNowCountdownTimer = null;

    const workerBridgeReadinessStorageKey = "main-computer-worker-bridge-readiness-v1";
    const WORKER_SETTINGS_POLL_INTERVAL_MS = 2500;
    const WORKER_DEV_CHAIN_ID_DECIMAL = 42424242;
    const WORKER_DEV_CHAIN_ID_HEX = "0x28757b2";
    const WORKER_FAUCET_AMOUNT_CREDITS = "1";
    const WORKER_HUB_CREDIT_DEFAULT_AMOUNT = "1";
    const WORKER_WALLET_BALANCE_TIMEOUT_MS = 8000;
    const WORKER_METAMASK_RPC_BACKOFF_TIMEOUT_MS = 75000;
    const WORKER_METAMASK_RPC_BACKOFF_POLL_MS = 3000;
    const WORKER_HUB_CREDIT_BRIDGE_ESCROW_ABI = [
      "function depositFor(address account,uint256 amountUnits,bytes32 depositId,string memo) payable returns (bool)",
      "event CreditDeposited(bytes32 indexed depositId,address indexed account,address indexed payer,uint256 amountUnits,string memo)"
    ];
    const WORKER_DEV_CHAIN_NAME = "Main Computer Dev Chain";
    const WORKER_DEV_CHAIN_RPC_URL = "http://127.0.0.1:18545";
    const WORKER_DEV_CHAIN_CURRENCY_NAME = "Main Computer XLAG Credit";
    const WORKER_DEV_CHAIN_CURRENCY_SYMBOL = "MCXLAG";
    const WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_NAME = "Energy Credits";
    const WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL = "ENG";
    const WORKER_ETHERS_ESM_URL = "https://cdn.jsdelivr.net/npm/ethers@6.13.4/+esm";
    let workerBridgeStateLoaded = false;
    let workerBridgeState = workerDefaultBridgeState();
    let workerWalletOperationSerial = 0;
    let workerWalletBoundProvider = null;
    let workerWalletSelectedProvider = null;
    let workerWalletSelectedProviderInfo = null;
    let workerWalletBrowserProvider = null;
    let workerEthersModulePromise = null;
    let workerWalletHookState = "idle";
    let workerWalletLastAction = "not connected";
    let workerFaucetInFlight = false;
    let workerFaucetLastResult = null;
    let workerFaucetLastError = "";
    let workerFaucetRuntimeStatus = null;
    let workerFaucetRuntimeEndpointReachable = false;
    let workerFaucetRuntimeCheckInFlight = false;
    let workerHubCreditBridgeConfig = null;
    let workerHubCreditBridgeConfigStatus = "idle";
    let workerHubCreditBridgeConfigError = "";
    let workerHubCreditBridgeConfigInFlight = false;
    let workerHubCreditFundingInFlight = false;
    let workerHubCreditBalanceInFlight = false;
    let workerWalletCreditBalanceInFlight = false;
    let workerMultisessionInFlight = false;
    let workerWalletHydrationPromise = null;
    let workerWalletPageLoadHydrationAttempted = false;
    let workerSettingsPollTimer = null;
    let workerSettingsPollInFlight = false;
    let workerRemoteEnabledLastLocalEditAt = 0;
    let workerRemoteEnabledLastSaveCompletedAt = 0;
    let workerRemoteEnabledSaveSerial = 0;

    function workerDefaultBridgeState() {
      return {
        wallet: {
          address: "",
          chainId: "",
          connected: false,
          connectedAt: ""
        },
        recoveryEmails: [],
        recoveryWallets: [],
        recoveryConfirmedAt: "",
        multisessionKeys: [],
        activeMultisessionKeyId: "",
        faucet: {
          amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
          lastStatus: "Not requested",
          lastTxHash: "",
          lastResult: null,
          lastError: "",
          updatedAt: ""
        },
        walletFunding: {
          bridgeContractAddress: "",
          amountCredits: WORKER_HUB_CREDIT_DEFAULT_AMOUNT,
          walletBalance: null,
          walletBalanceStatus: "idle",
          walletBalanceError: "",
          balance: null,
          accountId: "",
          lastStatus: "Not funded",
          lastTxHash: "",
          lastError: "",
          updatedAt: ""
        }
      };
    }

    function workerNowIso() {
      return new Date().toISOString();
    }

    function workerDisplayTime(value) {
      if (!value) return "—";
      try {
        return new Date(value).toLocaleString();
      } catch {
        return String(value);
      }
    }

    function workerShortAddress(value) {
      const clean = String(value || "").trim();
      if (!clean) return "—";
      return clean.length > 18 ? `${clean.slice(0, 10)}…${clean.slice(-6)}` : clean;
    }

    function workerWalletValidAddress(value) {
      return /^0x[0-9a-fA-F]{40}$/.test(String(value || ""));
    }

    function workerPromiseWithTimeout(promise, label, timeoutMs = WORKER_WALLET_BALANCE_TIMEOUT_MS) {
      return Promise.race([
        promise,
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
        })
      ]);
    }

    function workerWalletBalanceErrorDetails(error) {
      const raw = String(error?.message || error || "Unknown wallet balance error.").trim();
      const lower = raw.toLowerCase();
      if (lower.includes("rpc endpoint returned too many errors") || lower.includes("rpc endpoint")) {
        return {
          fieldText: "Wallet RPC unavailable.",
          message: `Could not read my wallet balance. MetaMask says the RPC endpoint is failing: ${raw}`
        };
      }
      if (lower.includes("timed out")) {
        return {
          fieldText: "Wallet balance check timed out.",
          message: `Could not read my wallet balance. ${raw}`
        };
      }
      return {
        fieldText: "Wallet balance unavailable.",
        message: `Wallet balance check failed: ${raw}`
      };
    }

    async function workerBrowserProviderSend(browserProvider, method, params = [], label = method, timeoutMs = WORKER_WALLET_BALANCE_TIMEOUT_MS) {
      if (!browserProvider || typeof browserProvider.send !== "function") {
        throw new Error("No ethers browser wallet provider is available.");
      }
      return await workerPromiseWithTimeout(
        browserProvider.send(method, params),
        label,
        timeoutMs
      );
    }

    async function workerReadWalletBalanceFromInjectedProvider(browserProvider, walletAddress) {
      const [chainIdRaw, accountsRaw] = await Promise.all([
        workerBrowserProviderSend(browserProvider, "eth_chainId", [], "wallet chain check"),
        workerBrowserProviderSend(browserProvider, "eth_accounts", [], "wallet account check")
      ]);
      const accounts = Array.isArray(accountsRaw) ? accountsRaw : [];
      const providerAddress = workerLowerAddress(accounts[0] || "");
      const chainId = workerNormalizeChainIdHex(chainIdRaw);

      if (!workerWalletValidAddress(providerAddress)) {
        throw new Error("Open the wallet and approve account access.");
      }
      if (providerAddress !== walletAddress) {
        throw new Error(`Browser wallet is ${workerShortAddress(providerAddress)}; expected ${workerShortAddress(walletAddress)}.`);
      }
      if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        throw new Error(`Wallet is on ${chainId || "unknown chain"}; expected ${WORKER_DEV_CHAIN_ID_HEX}.`);
      }

      const balanceHex = await workerBrowserProviderSend(
        browserProvider,
        "eth_getBalance",
        [walletAddress, "latest"],
        "wallet balance check"
      );
      return {
        wallet_address: walletAddress,
        balance_base_units: BigInt(balanceHex || "0x0").toString(),
        chain_id: chainId,
        source: "wallet-provider"
      };
    }

    async function workerReadWalletBalanceFromLocalRpc(walletAddress) {
      const data = await workerPostJson("/api/applications/worker/wallet-balance", {
        wallet_address: walletAddress
      });
      if (!data || data.ok === false || data.error) {
        throw new Error(data?.error || "Local wallet balance endpoint failed.");
      }
      return {
        wallet_address: workerLowerAddress(data.wallet_address || walletAddress),
        balance_base_units: String(data.balance_base_units ?? "0"),
        available_credits: String(data.available_credits ?? ""),
        chain_id: workerNormalizeChainIdHex(data.chain_id_hex || data.chain_id || ""),
        source: String(data.source || "local-rpc")
      };
    }

    function workerNormalizeChainIdHex(value) {
      const text = String(value || "").trim().toLowerCase();
      if (!text) return "";
      if (/^0x[0-9a-f]+$/.test(text)) {
        return `0x${BigInt(text).toString(16)}`;
      }
      if (/^[0-9]+$/.test(text)) {
        return `0x${BigInt(text).toString(16)}`;
      }
      return text;
    }

    function workerFormatCreditBaseUnits(value) {
      try {
        const units = BigInt(value?.toString ? value.toString() : value ?? 0);
        const whole = units / WORKER_CREDIT_BASE_UNITS_PER_CREDIT;
        const fractional = units % WORKER_CREDIT_BASE_UNITS_PER_CREDIT;
        if (fractional === 0n) return whole.toString();
        const fractionText = fractional.toString().padStart(18, "0").replace(/0+$/, "");
        const visibleFraction = fractionText.length > 6
          ? fractionText.slice(0, 6).replace(/0+$/, "")
          : fractionText;
        return visibleFraction ? `${whole.toString()}.${visibleFraction}` : whole.toString();
      } catch {
        return "";
      }
    }

    function workerNormalizeWalletCreditBalance(data) {
      if (!data || typeof data !== "object") return null;
      const balanceBaseUnits = String(data.balance_base_units ?? data.base_units ?? "");
      const availableCredits = String(
        data.available_credits
          ?? data.balance_credits
          ?? data.credits
          ?? (balanceBaseUnits ? workerFormatCreditBaseUnits(balanceBaseUnits) : "0")
      );
      return {
        wallet_address: String(data.wallet_address || ""),
        available_credits: availableCredits,
        balance_base_units: balanceBaseUnits,
        chain_id: workerNormalizeChainIdHex(data.chain_id || data.chainId || ""),
        source: String(data.source || ""),
        updated_at: String(data.updated_at || data.updatedAt || workerNowIso())
      };
    }

    function workerNormalizeFaucetResult(data) {
      if (!data || typeof data !== "object") return null;
      const result = {
        tx_hash: String(data.tx_hash || data.transaction_hash || data.hash || ""),
        from: String(data.from || ""),
        to: String(data.to || ""),
        amount_credits: String(data.amount_credits || ""),
        chain_id: String(data.chain_id || ""),
        runtime_source: String(data.runtime_source || "")
      };
      return Object.values(result).some(Boolean) ? result : null;
    }

    function workerNormalizeHubCreditBalance(data) {
      if (!data || typeof data !== "object") return null;
      const account = data.account && typeof data.account === "object" ? data.account : {};
      return {
        wallet_address: String(data.wallet_address || ""),
        account_id: String(data.account_id || account.account_id || ""),
        available_credits: String(account.available_credits ?? data.available_credits ?? "0"),
        held_credits: String(account.held_credits ?? "0"),
        spent_credits: String(account.spent_credits ?? "0"),
        funding_model: String(data.funding_model || ""),
        updated_at: workerNowIso()
      };
    }

    function workerCreditsToBaseUnits(value) {
      const text = String(value || "").trim();
      if (!/^\d+(\.\d{1,18})?$/.test(text)) {
        throw new Error("Credits must be a positive decimal with up to 18 places.");
      }
      const [whole, fractional = ""] = text.split(".");
      const units = (BigInt(whole) * WORKER_CREDIT_BASE_UNITS_PER_CREDIT)
        + BigInt(fractional.padEnd(18, "0"));
      if (units <= 0n) {
        throw new Error("Credits must be greater than zero.");
      }
      return units.toString();
    }

    function workerDecimalChainId(value) {
      const normalized = workerNormalizeChainIdHex(value);
      if (!normalized) return 0;
      try {
        return Number(BigInt(normalized));
      } catch {
        return 0;
      }
    }

    function workerFaucetResultValue(result, key) {
      return result && result[key] ? String(result[key]) : "—";
    }

    function workerWalletNextOperation() {
      workerWalletOperationSerial += 1;
      return workerWalletOperationSerial;
    }

    function workerWalletOperationIsCurrent(token) {
      return token === workerWalletOperationSerial;
    }

    function workerSetWalletOperationState(nextState, message = "") {
      workerWalletHookState = String(nextState || "idle");
      if (message) workerWalletLastAction = String(message);
      if (message) workerSetSaveStatus(message);
      renderWorkerBridgeReadiness();
    }

    function workerSetPrimaryWalletState({connected = false, address = "", chainId = ""} = {}) {
      loadWorkerBridgeState();
      const previousAddress = workerLowerAddress(workerBridgeState.wallet?.address || "");
      const previousChainId = workerNormalizeChainIdHex(workerBridgeState.wallet?.chainId || "");
      const nextAddress = connected ? String(address || "") : "";
      const nextChainId = connected ? workerNormalizeChainIdHex(chainId) : "";
      workerBridgeState.wallet = {
        address: nextAddress,
        chainId: nextChainId,
        connected: Boolean(connected && address),
        connectedAt: connected && address ? workerNowIso() : ""
      };
      const walletChanged = workerLowerAddress(nextAddress) !== previousAddress || nextChainId !== previousChainId;
      if (workerBridgeState.walletFunding && (!connected || walletChanged)) {
        workerBridgeState.walletFunding.walletBalance = null;
        workerBridgeState.walletFunding.walletBalanceStatus = connected ? "idle" : "not_connected";
        workerBridgeState.walletFunding.walletBalanceError = "";
        workerBridgeState.walletFunding.balance = null;
        workerBridgeState.walletFunding.accountId = "";
      }
    }

    function workerRenderWalletControls() {
      const connected = Boolean(workerBridgeState.wallet.connected && workerBridgeState.wallet.address);
      const busy = ["hydrating", "requesting", "stabilizing", "disconnecting"].includes(workerWalletHookState);
      if (workerConnectWallet) {
        workerConnectWallet.disabled = busy || connected;
        workerConnectWallet.textContent = connected
          ? "Connected"
          : workerWalletHookState === "hydrating"
            ? "Checking Wallet…"
            : workerWalletHookState === "requesting"
              ? "Wallet Open…"
              : workerWalletHookState === "stabilizing"
                ? "Finalizing…"
                : "Connect Wallet";

        if (["hydrating", "requesting", "stabilizing"].includes(workerWalletHookState)) {
          workerConnectWallet.setAttribute("aria-busy", "true");
        } else {
          workerConnectWallet.removeAttribute("aria-busy");
        }
        workerConnectWallet.title = connected
          ? `Connected wallet ${workerShortAddress(workerBridgeState.wallet.address)}`
          : "Connect the Worker primary wallet through the selected browser wallet.";
      }
      if (workerDisconnectWallet) {
        workerDisconnectWallet.disabled = workerWalletHookState === "disconnecting";
        workerDisconnectWallet.textContent = workerWalletHookState === "disconnecting"
          ? "Disconnecting…"
          : connected
            ? "Disconnect Wallet"
            : "Force Disconnect / Reset";

        if (workerWalletHookState === "disconnecting") {
          workerDisconnectWallet.setAttribute("aria-busy", "true");
        } else {
          workerDisconnectWallet.removeAttribute("aria-busy");
        }
        workerDisconnectWallet.title = connected
          ? "Disconnect the Worker wallet and revoke account permission when supported."
          : "Clear Worker wallet state and attempt to revoke account permission.";
      }
    }

    function workerNormalizeList(items, type) {
      if (!Array.isArray(items)) return [];
      return items
        .map((item) => {
          const value = String(type === "email" ? item.email || item.value || "" : item.address || item.value || "").trim();
          if (!value) return null;
          return {
            id: String(item.id || `${type}-${value.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`),
            status: String(item.status || "active"),
            value,
            addedAt: String(item.addedAt || item.added_at || ""),
            removedAt: String(item.removedAt || item.removed_at || "")
          };
        })
        .filter(Boolean);
    }

    function workerNormalizeBridgeState(parsed) {
      const fallback = workerDefaultBridgeState();
      const state = parsed && typeof parsed === "object" ? parsed : {};
      const faucet = state.faucet && typeof state.faucet === "object" ? state.faucet : {};
      const walletFunding = state.walletFunding && typeof state.walletFunding === "object"
        ? state.walletFunding
        : state.wallet_funding && typeof state.wallet_funding === "object"
          ? state.wallet_funding
          : {};
      return {
        wallet: {...fallback.wallet},
        recoveryEmails: workerNormalizeList(state.recoveryEmails, "email"),
        recoveryWallets: workerNormalizeList(state.recoveryWallets, "wallet"),
        recoveryConfirmedAt: String(state.recoveryConfirmedAt || state.recovery_confirmed_at || ""),
        // Multi-session key ids are bearer credentials in this flow. Do not
        // revive any legacy browser-stored key material; the local backend is
        // the source of truth and load responses are server-side/redacted.
        multisessionKeys: [],
        activeMultisessionKeyId: "",
        faucet: {
          amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
          lastStatus: String(faucet.lastStatus || faucet.last_status || fallback.faucet.lastStatus),
          lastTxHash: String(faucet.lastTxHash || faucet.last_tx_hash || ""),
          lastResult: workerNormalizeFaucetResult(faucet.lastResult || faucet.last_result),
          lastError: String(faucet.lastError || faucet.last_error || ""),
          updatedAt: String(faucet.updatedAt || faucet.updated_at || "")
        },
        walletFunding: {
          // The escrow address is deploy-owned.  Do not revive stale/manual
          // browser storage here; workerRefreshHubCreditBridgeConfig reloads it
          // from runtime/deployments/dev/latest.json through the local viewport.
          bridgeContractAddress: "",
          amountCredits: String(walletFunding.amountCredits || walletFunding.amount_credits || WORKER_HUB_CREDIT_DEFAULT_AMOUNT),
          walletBalance: workerNormalizeWalletCreditBalance(walletFunding.walletBalance || walletFunding.wallet_balance),
          walletBalanceStatus: String(walletFunding.walletBalanceStatus || walletFunding.wallet_balance_status || "idle"),
          walletBalanceError: String(walletFunding.walletBalanceError || walletFunding.wallet_balance_error || ""),
          balance: workerNormalizeHubCreditBalance(walletFunding.balance),
          accountId: String(walletFunding.accountId || walletFunding.account_id || ""),
          lastStatus: String(walletFunding.lastStatus || walletFunding.last_status || fallback.walletFunding.lastStatus),
          lastTxHash: String(walletFunding.lastTxHash || walletFunding.last_tx_hash || ""),
          lastError: String(walletFunding.lastError || walletFunding.last_error || ""),
          updatedAt: String(walletFunding.updatedAt || walletFunding.updated_at || "")
        }
      };
    }

    function loadWorkerBridgeState() {
      if (workerBridgeStateLoaded) return;
      workerBridgeStateLoaded = true;
      try {
        workerBridgeState = workerNormalizeBridgeState(JSON.parse(localStorage.getItem(workerBridgeReadinessStorageKey) || "null"));
      } catch {
        workerBridgeState = workerDefaultBridgeState();
      }
      workerFaucetLastResult = workerBridgeState.faucet.lastResult || null;
      workerFaucetLastError = workerBridgeState.faucet.lastError || "";
      if (workerFaucetAmount) {
        workerFaucetAmount.value = WORKER_FAUCET_AMOUNT_CREDITS;
        workerFaucetAmount.disabled = true;
      }
      if (workerHubCreditAmount) {
        workerHubCreditAmount.value = workerBridgeState.walletFunding.amountCredits || WORKER_HUB_CREDIT_DEFAULT_AMOUNT;
      }
    }

    function saveWorkerBridgeState() {
      try {
        const {
          wallet: _liveWalletState,
          multisessionKeys: _serverSideMultisessionKeys,
          activeMultisessionKeyId: _serverSideActiveMultisessionKeyId,
          ...serializableState
        } = workerBridgeState || {};
        localStorage.setItem(workerBridgeReadinessStorageKey, JSON.stringify(serializableState));
      } catch {}
    }

    function workerLowerAddress(value) {
      return String(value || "").trim().toLowerCase();
    }

    function workerMultisessionKeyMatchesWallet(key, walletAddress = workerBridgeState.wallet.address) {
      const wallet = workerLowerAddress(walletAddress);
      if (!wallet) return false;
      return workerLowerAddress(key?.walletAddress || key?.wallet_address || "") === wallet;
    }

    function workerActiveMultisessionKey() {
      const activeId = String(workerBridgeState.activeMultisessionKeyId || "");
      const activeKeys = workerBridgeState.multisessionKeys.filter((key) => (
        key.status === "active"
        && workerMultisessionKeyMatchesWallet(key)
      ));
      if (activeId) {
        return activeKeys.find((key) => key.id === activeId || key.localRef === activeId) || activeKeys[0] || null;
      }
      return activeKeys[0] || null;
    }

    function workerErrorText(error) {
      return String(error?.message || error?.error || error || "").trim();
    }

    function workerIsInactiveMultisessionKeyError(error) {
      const text = workerErrorText(error).toLowerCase();
      return (
        text.includes("saved multi-session key")
        && text.includes("not active")
        && text.includes("hub")
      );
    }

    function workerMarkMultisessionKeyInactiveOnHub(keyId = "", errorMessage = "") {
      const id = String(keyId || "").trim();
      let changed = false;
      workerBridgeState.multisessionKeys = workerBridgeState.multisessionKeys.map((key) => {
        const matchesKey = id
          ? (String(key?.id || "") === id || String(key?.localRef || "") === id)
          : (key?.status === "active" && workerMultisessionKeyMatchesWallet(key));
        if (!matchesKey) return key;
        changed = true;
        return {
          ...key,
          status: "inactive_on_hub",
          inactiveOnHubAt: workerNowIso(),
          lastError: String(errorMessage || "The saved multi-session key is not active on this Hub.")
        };
      });
      if (!id || workerBridgeState.activeMultisessionKeyId === id) {
        workerBridgeState.activeMultisessionKeyId = "";
        changed = true;
      }
      if (changed) {
        saveWorkerBridgeState();
      }
    }

    function workerClearActiveMultisessionKeyIfWalletMismatch() {
      if (workerBridgeState.activeMultisessionKeyId && !workerActiveMultisessionKey()) {
        workerBridgeState.activeMultisessionKeyId = "";
      }
    }

    function workerRecoveryMethodsReady() {
      const activeEmails = workerBridgeState.recoveryEmails.filter((item) => item.status === "active");
      const activeWallets = workerBridgeState.recoveryWallets.filter((item) => item.status === "active");
      return Boolean(workerBridgeState.recoveryConfirmedAt && (activeEmails.length || activeWallets.length));
    }

    function workerReadinessLabel() {
      if (!workerBridgeState.wallet.address) {
        return "Wallet not connected";
      }
      if (!workerActiveMultisessionKey()) {
        return "Request multi-session key";
      }
      return "Worker wallet ready";
    }

    function workerComputeFaucetReadiness() {
      loadWorkerBridgeState();

      const wallet = workerBridgeState.wallet || {};
      const address = String(wallet.address || "").trim();
      const chainId = workerNormalizeChainIdHex(wallet.chainId);
      const connected = Boolean(wallet.connected && address);

      if (workerFaucetInFlight) {
        return {
          ready: false,
          reason: "Sending faucet request…",
          address,
          chainId
        };
      }

      if (!connected) {
        return {
          ready: false,
          reason: "Connect Wallet before requesting faucet funds.",
          address,
          chainId
        };
      }

      if (!workerWalletValidAddress(address)) {
        return {
          ready: false,
          reason: "Connected Worker wallet address is not a valid 0x address.",
          address,
          chainId
        };
      }

      if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        return {
          ready: false,
          reason: `Wallet is connected on ${chainId || "unknown chain"}. Switch the browser wallet to ${WORKER_DEV_CHAIN_ID_HEX}.`,
          address,
          chainId
        };
      }

      if (workerFaucetRuntimeCheckInFlight && !workerFaucetRuntimeStatus) {
        return {
          ready: false,
          reason: "Checking faucet endpoint and deployment runtime.",
          address,
          chainId
        };
      }

      if (!workerFaucetRuntimeEndpointReachable) {
        return {
          ready: false,
          reason: "Faucet API endpoint is not reachable.",
          address,
          chainId
        };
      }

      const runtime = workerFaucetRuntimeStatus || {};
      if (!runtime.runtime_ready || !runtime.runtime_exists || !runtime.has_faucet_account) {
        return {
          ready: false,
          reason: "Deployment runtime is missing or has no faucet account.",
          address,
          chainId
        };
      }

      if (runtime.ready === false) {
        return {
          ready: false,
          reason: String(runtime.reason || "Faucet runtime is not ready."),
          address,
          chainId
        };
      }

      return {
        ready: true,
        reason: `Faucet can fund ${workerShortAddress(address)} on ${WORKER_DEV_CHAIN_ID_HEX}.`,
        address,
        chainId
      };
    }


    function workerBridgeConfigContractAddress(config = workerHubCreditBridgeConfig) {
      return String(
        config?.hub_credit_bridge_escrow_address
          || config?.contract_address
          || config?.bridge_contract_address
          || ""
      ).trim();
    }

    function workerBridgeConfigChainIdHex(config = workerHubCreditBridgeConfig) {
      const raw = config?.chain_id_hex || config?.chainIdHex || config?.chain_id || config?.chainId || "";
      return workerNormalizeChainIdHex(raw);
    }

    function workerBridgeConfigDisplayAddress() {
      const address = workerBridgeConfigContractAddress();
      if (workerHubCreditBridgeConfigInFlight || workerHubCreditBridgeConfigStatus === "checking") return "Loading deployment…";
      if (address) return workerShortAddress(address);
      if (workerHubCreditBridgeConfigError) return workerHubCreditBridgeConfigError;
      return "Deployment config not loaded";
    }

    async function workerRefreshHubCreditBridgeConfig({force = false} = {}) {
      if (workerHubCreditBridgeConfigInFlight) return workerHubCreditBridgeConfig;
      if (!force && workerHubCreditBridgeConfigStatus === "ready" && workerWalletValidAddress(workerBridgeConfigContractAddress())) {
        return workerHubCreditBridgeConfig;
      }
      workerHubCreditBridgeConfigInFlight = true;
      workerHubCreditBridgeConfigStatus = "checking";
      workerHubCreditBridgeConfigError = "";
      renderWorkerBridgeReadiness();
      try {
        const data = await workerGetJson("/api/applications/worker/wallet-funding/config");
        const contractAddress = String(data.hub_credit_bridge_escrow_address || data.contract_address || "").trim();
        if (!workerWalletValidAddress(contractAddress)) {
          throw new Error("Deployment config is missing hub_credit_bridge_escrow.address.");
        }
        const configuredChainId = workerNormalizeChainIdHex(data.chain_id_hex || data.chain_id || "");
        if (configuredChainId && configuredChainId !== WORKER_DEV_CHAIN_ID_HEX) {
          throw new Error(`Deployment chain ${configuredChainId} does not match ${WORKER_DEV_CHAIN_ID_HEX}.`);
        }
        workerHubCreditBridgeConfig = {
          ...data,
          hub_credit_bridge_escrow_address: contractAddress,
          chain_id_hex: configuredChainId || WORKER_DEV_CHAIN_ID_HEX
        };
        workerHubCreditBridgeConfigStatus = "ready";
        workerHubCreditBridgeConfigError = "";
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          bridgeContractAddress: contractAddress,
          lastError: String(workerBridgeState.walletFunding?.lastError || "").startsWith("Bridge deployment config")
            ? ""
            : workerBridgeState.walletFunding?.lastError || "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        return workerHubCreditBridgeConfig;
      } catch (error) {
        workerHubCreditBridgeConfig = null;
        workerHubCreditBridgeConfigStatus = "failed";
        workerHubCreditBridgeConfigError = `Bridge deployment config unavailable: ${error.message || error}`;
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          bridgeContractAddress: "",
          lastError: workerHubCreditBridgeConfigError,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        return null;
      } finally {
        workerHubCreditBridgeConfigInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    function workerComputeHubCreditFundingReadiness() {
      loadWorkerBridgeState();

      const wallet = workerBridgeState.wallet || {};
      const address = String(wallet.address || "").trim();
      const chainId = workerNormalizeChainIdHex(wallet.chainId);
      const connected = Boolean(wallet.connected && address);
      const contractAddress = workerBridgeConfigContractAddress();
      const amountCredits = String(workerHubCreditAmount?.value || workerBridgeState.walletFunding.amountCredits || WORKER_HUB_CREDIT_DEFAULT_AMOUNT).trim();

      if (workerHubCreditFundingInFlight) {
        return {
          ready: false,
          reason: "Funding my bridge account is pending.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      if (!connected) {
        return {
          ready: false,
          reason: "Connect Wallet before funding.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      if (!workerWalletValidAddress(address)) {
        return {
          ready: false,
          reason: "Connected Worker wallet address is not a valid 0x address.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        return {
          ready: false,
          reason: `Wallet is connected on ${chainId || "unknown chain"}. Switch the browser wallet to ${WORKER_DEV_CHAIN_ID_HEX}.`,
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      if (workerHubCreditBridgeConfigInFlight || workerHubCreditBridgeConfigStatus === "checking") {
        return {
          ready: false,
          reason: "Loading bridge deployment config.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      if (!workerWalletValidAddress(contractAddress)) {
        return {
          ready: false,
          reason: workerHubCreditBridgeConfigError || "Bridge deployment config is missing the escrow contract address.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      const deployedChainId = workerBridgeConfigChainIdHex();
      if (deployedChainId && deployedChainId !== chainId) {
        return {
          ready: false,
          reason: `Deployment is for ${deployedChainId}, but the wallet is on ${chainId || "unknown chain"}.`,
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      let amountUnits = "";
      try {
        amountUnits = workerCreditsToBaseUnits(amountCredits);
      } catch (error) {
        return {
          ready: false,
          reason: error.message || String(error),
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      const walletBalanceUnits = String(workerBridgeState.walletFunding?.walletBalance?.balance_base_units || "");
      if (!/^\d+$/.test(walletBalanceUnits)) {
        return {
          ready: false,
          reason: "Check my wallet balance before funding.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }
      if (BigInt(amountUnits) > BigInt(walletBalanceUnits)) {
        return {
          ready: false,
          reason: BigInt(walletBalanceUnits) === 0n
            ? "Request Faucet Funds for this wallet before funding the bridge."
            : "Amount exceeds my wallet balance.",
          address,
          chainId,
          contractAddress,
          amountCredits
        };
      }

      return {
        ready: true,
        reason: "Ready to fund my bridge account.",
        address,
        chainId,
        contractAddress,
        amountCredits
      };
    }

    function workerFaucetStatusText(readiness) {
      if (workerFaucetInFlight) return "Sending faucet request…";
      if (workerFaucetLastError) return workerFaucetLastError;
      if (workerFaucetLastResult) {
        const amount = workerFaucetResultValue(workerFaucetLastResult, "amount_credits");
        const to = workerFaucetResultValue(workerFaucetLastResult, "to");
        return `Faucet sent ${amount} credit${amount === "1" ? "" : "s"} to ${workerShortAddress(to)}.`;
      }
      return readiness.ready ? "Ready" : readiness.reason;
    }

    function workerRenderFaucetResult(result) {
      const hasResult = Boolean(result);
      if (workerFaucetResult) {
        workerFaucetResult.hidden = !hasResult;
      }
      if (workerFaucetResultTx) {
        workerFaucetResultTx.textContent = workerFaucetResultValue(result, "tx_hash");
      }
      if (workerFaucetResultFrom) {
        workerFaucetResultFrom.textContent = workerFaucetResultValue(result, "from");
      }
      if (workerFaucetResultTo) {
        workerFaucetResultTo.textContent = workerFaucetResultValue(result, "to");
      }
      if (workerFaucetResultAmount) {
        workerFaucetResultAmount.textContent = workerFaucetResultValue(result, "amount_credits");
      }
      if (workerFaucetResultChain) {
        const chainId = workerFaucetResultValue(result, "chain_id");
        workerFaucetResultChain.textContent = chainId === "—" ? "—" : workerNormalizeChainIdHex(chainId);
      }
      if (workerFaucetResultRuntime) {
        workerFaucetResultRuntime.textContent = workerFaucetResultValue(result, "runtime_source");
      }
    }

    function workerRenderTokenList(container, items, emptyText, removeAttribute) {
      if (!container) return;
      container.innerHTML = "";
      const activeItems = items.filter((item) => item.status === "active");
      if (!activeItems.length) {
        const empty = document.createElement("span");
        empty.textContent = emptyText;
        container.append(empty);
        return;
      }
      activeItems.forEach((item) => {
        const token = document.createElement("span");
        token.className = "worker-token";
        const label = document.createElement("span");
        label.textContent = item.value;
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = "Remove";
        button.setAttribute(removeAttribute, item.id);
        token.append(label, button);
        container.append(token);
      });
    }

    function renderWorkerBridgeReadiness() {
      loadWorkerBridgeState();
      workerClearActiveMultisessionKeyIfWalletMismatch();
      const walletAddress = workerBridgeState.wallet.address;
      const faucetReadiness = workerComputeFaucetReadiness();
      const recoveryEmailCount = workerBridgeState.recoveryEmails.filter((item) => item.status === "active").length;
      const recoveryWalletCount = workerBridgeState.recoveryWallets.filter((item) => item.status === "active").length;
      const activeKey = workerActiveMultisessionKey();
      const revokedKey = [...workerBridgeState.multisessionKeys].reverse().find((key) => key.status === "revoked");

      if (workerPrimaryWalletStatus) {
        workerPrimaryWalletStatus.textContent = walletAddress
          ? `${workerShortAddress(walletAddress)}${workerBridgeState.wallet.chainId ? ` on ${workerBridgeState.wallet.chainId}` : ""}`
          : workerWalletHookState === "hydrating"
            ? "Checking existing browser wallet"
            : workerWalletHookState === "requesting"
              ? "Wallet request open; not connected"
              : workerWalletHookState === "stabilizing"
                ? "Finalizing ethers wallet state"
                : workerWalletHookState === "disconnecting"
                  ? "Force disconnecting"
                  : "Not connected";
      }
      if (workerRecoveryStatus) {
        workerRecoveryStatus.textContent = workerRecoveryMethodsReady()
          ? `Confirmed with ${recoveryEmailCount} email / ${recoveryWalletCount} wallet method${recoveryEmailCount + recoveryWalletCount === 1 ? "" : "s"}`
          : `${recoveryEmailCount} email / ${recoveryWalletCount} wallet method${recoveryEmailCount + recoveryWalletCount === 1 ? "" : "s"}`;
      }
      if (workerMultisessionStatus) {
        workerMultisessionStatus.textContent = activeKey
          ? (activeKey.id ? `Active ${activeKey.id}` : "Active server-side key")
          : "No active key";
      }
      if (workerBridgeReadinessStatus) {
        workerBridgeReadinessStatus.textContent = workerReadinessLabel();
      }
      if (workerFaucetTarget) {
        workerFaucetTarget.textContent = faucetReadiness.address
          ? `${workerShortAddress(faucetReadiness.address)}${faucetReadiness.chainId ? ` on ${faucetReadiness.chainId}` : ""}`
          : "Connect wallet first";
      }
      if (workerFaucetReadiness) {
        workerFaucetReadiness.textContent = faucetReadiness.ready ? "Ready" : faucetReadiness.reason;
      }
      if (workerFaucetStatus) {
        workerFaucetStatus.textContent = workerFaucetStatusText(faucetReadiness) || "Not requested";
      }
      if (workerFaucetTx) {
        workerFaucetTx.textContent = workerFaucetResultValue(workerFaucetLastResult, "tx_hash");
      }
      if (workerFaucetDisabledReason) {
        workerFaucetDisabledReason.textContent = faucetReadiness.reason;
      }
      if (workerRequestFaucet) {
        workerRequestFaucet.disabled = !faucetReadiness.ready;
        workerRequestFaucet.textContent = "Request Faucet Funds";
        if (workerFaucetInFlight) {
          workerRequestFaucet.setAttribute("aria-busy", "true");
        } else {
          workerRequestFaucet.removeAttribute("aria-busy");
        }
      }

      const hubCreditReadiness = workerComputeHubCreditFundingReadiness();
      const walletCreditBalance = workerBridgeState.walletFunding?.walletBalance || null;
      const walletCreditBalanceStatus = String(workerBridgeState.walletFunding?.walletBalanceStatus || "idle");
      const walletCreditBalanceError = String(workerBridgeState.walletFunding?.walletBalanceError || "");
      const hubCreditBalance = workerBridgeState.walletFunding?.balance || null;
      if (workerHubCreditWallet) {
        workerHubCreditWallet.textContent = hubCreditReadiness.address
          ? `${workerShortAddress(hubCreditReadiness.address)}${hubCreditReadiness.chainId ? ` on ${hubCreditReadiness.chainId}` : ""}`
          : "Connect wallet first";
      }
      if (workerHubCreditWalletBalance) {
        if (!hubCreditReadiness.address) {
          workerHubCreditWalletBalance.textContent = "Connect wallet first";
        } else if (workerWalletCreditBalanceInFlight || walletCreditBalanceStatus === "checking") {
          workerHubCreditWalletBalance.textContent = "Checking my wallet balance…";
        } else if (walletCreditBalance) {
          const source = walletCreditBalance.source === "local-rpc" ? " via local RPC" : "";
          workerHubCreditWalletBalance.textContent = walletCreditBalance.balance_base_units === "0"
            ? `0 credits available — Request Faucet Funds first${source}`
            : `${walletCreditBalance.available_credits} credits available to fund${source}`;
        } else if (walletCreditBalanceStatus === "failed" && walletCreditBalanceError) {
          workerHubCreditWalletBalance.textContent = workerWalletBalanceErrorDetails(walletCreditBalanceError).fieldText;
        } else {
          workerHubCreditWalletBalance.textContent = "Check balances to load.";
        }
      }
      if (workerHubCreditBalance) {
        workerHubCreditBalance.textContent = hubCreditBalance
          ? `${hubCreditBalance.available_credits} available / ${hubCreditBalance.held_credits} held / ${hubCreditBalance.spent_credits} spent`
          : "Unknown";
      }
      if (workerHubCreditEscrowAddress) {
        workerHubCreditEscrowAddress.textContent = workerBridgeConfigDisplayAddress();
        workerHubCreditEscrowAddress.title = workerBridgeConfigContractAddress() || workerHubCreditBridgeConfigError || "";
      }
      if (workerHubCreditStatus) {
        if (workerHubCreditFundingInFlight) {
          workerHubCreditStatus.textContent = "Funding my bridge account…";
        } else if (workerHubCreditBalanceInFlight || workerWalletCreditBalanceInFlight) {
          workerHubCreditStatus.textContent = "Checking my balances…";
        } else {
          workerHubCreditStatus.textContent = workerBridgeState.walletFunding?.lastError
            || workerBridgeState.walletFunding?.lastStatus
            || "Not funded";
        }
      }
      if (workerHubCreditTx) {
        workerHubCreditTx.textContent = workerBridgeState.walletFunding?.lastTxHash || "—";
      }
      if (workerHubCreditDisabledReason) {
        workerHubCreditDisabledReason.textContent = hubCreditReadiness.reason;
      }
      if (workerCheckHubCreditBalance) {
        const checkingBalances = workerHubCreditBalanceInFlight || workerWalletCreditBalanceInFlight;
        workerCheckHubCreditBalance.disabled = checkingBalances || !workerWalletValidAddress(hubCreditReadiness.address);
        workerCheckHubCreditBalance.textContent = checkingBalances ? "Checking…" : "Check Balances";
        if (checkingBalances) {
          workerCheckHubCreditBalance.setAttribute("aria-busy", "true");
        } else {
          workerCheckHubCreditBalance.removeAttribute("aria-busy");
        }
      }
      if (workerFundHubCredit) {
        workerFundHubCredit.disabled = !hubCreditReadiness.ready;
        workerFundHubCredit.textContent = workerHubCreditFundingInFlight ? "Funding…" : "Fund";
        if (workerHubCreditFundingInFlight) {
          workerFundHubCredit.setAttribute("aria-busy", "true");
        } else {
          workerFundHubCredit.removeAttribute("aria-busy");
        }
      }
      workerRenderFaucetResult(workerFaucetLastResult);
      if (workerMultisessionKeyState) {
        workerMultisessionKeyState.textContent = activeKey ? "Active" : "No active key";
      }
      if (workerMultisessionKeyId) {
        workerMultisessionKeyId.textContent = activeKey
          ? (activeKey.id || "Stored server-side; key id redacted after creation")
          : "—";
      }
      if (workerMultisessionCreatedAt) {
        workerMultisessionCreatedAt.textContent = workerDisplayTime(activeKey?.createdAt || "");
      }
      if (workerMultisessionRevokedAt) {
        workerMultisessionRevokedAt.textContent = workerDisplayTime(revokedKey?.revokedAt || "");
      }
      if (workerRequestMultisessionKey) {
        workerRequestMultisessionKey.disabled = workerMultisessionInFlight || Boolean(activeKey);
        workerRequestMultisessionKey.textContent = workerMultisessionInFlight ? "Requesting Key…" : "Request New Key";
        if (workerMultisessionInFlight) {
          workerRequestMultisessionKey.setAttribute("aria-busy", "true");
        } else {
          workerRequestMultisessionKey.removeAttribute("aria-busy");
        }
      }
      if (workerRevokeMultisessionKey) {
        workerRevokeMultisessionKey.disabled = workerMultisessionInFlight || !activeKey;
      }
      workerRenderWalletControls();
      workerRenderTokenList(workerRecoveryEmailList, workerBridgeState.recoveryEmails, "No recovery emails added.", "data-worker-remove-recovery-email");
      workerRenderTokenList(workerRecoveryWalletList, workerBridgeState.recoveryWallets, "No recovery wallets added.", "data-worker-remove-recovery-wallet");
      renderWorkerNetworkSurface();
    }

    function workerActiveRecoveryEmails() {
      return workerBridgeState.recoveryEmails
        .filter((item) => item.status === "active")
        .map((item) => item.value);
    }

    function workerActiveRecoveryWallets() {
      return workerBridgeState.recoveryWallets
        .filter((item) => item.status === "active")
        .map((item) => item.value);
    }

    function workerBuildMultisessionRequestContext() {
      const wallet = workerBridgeState.wallet || {};
      return {
        wallet_address: wallet.address || "",
        chain_id: workerNormalizeChainIdHex(wallet.chainId),
        recovery_emails: workerActiveRecoveryEmails(),
        recovery_wallets: workerActiveRecoveryWallets(),
        recovery_confirmed_at: workerBridgeState.recoveryConfirmedAt || ""
      };
    }

    function workerNormalizeIssuedMultisessionKey(result) {
      const key = result?.key && typeof result.key === "object" ? result.key : {};
      const walletAddress = String(key.walletAddress || key.wallet_address || result?.verification?.wallet_address || "");
      const status = String(key.status || "active");
      const id = String(key.id || "");
      const createdAt = String(key.createdAt || key.created_at || "");
      const hubUrl = String(key.hubUrl || key.hub_url || result?.hub_url || "");
      const localRef = id || [
        "server-side-msk",
        workerLowerAddress(walletAddress),
        hubUrl,
        status,
        createdAt
      ].join("|");
      return {
        id,
        localRef,
        status,
        createdAt,
        revokedAt: String(key.revokedAt || key.revoked_at || ""),
        inactiveOnHubAt: String(key.inactiveOnHubAt || key.inactive_on_hub_at || ""),
        walletAddress,
        chainId: String(key.chainId || key.chain_id || ""),
        hubUrl,
        serverSideKey: Boolean(key.serverSideKey || key.server_side_key || key.server_side_key === undefined),
        keyRedacted: Boolean(key.keyRedacted || key.key_redacted || !id),
        lastError: String(key.lastError || key.last_error || "")
      };
    }

    function workerStoreIssuedMultisessionKey(result) {
      const key = workerNormalizeIssuedMultisessionKey(result);
      if (!key.id && !key.serverSideKey) {
        throw new Error("Hub did not return a usable multi-session key record.");
      }
      workerBridgeState.multisessionKeys = workerBridgeState.multisessionKeys.filter((existing) => (
        existing.localRef !== key.localRef
        && (!key.id || existing.id !== key.id)
        && !workerMultisessionKeyMatchesWallet(existing, key.walletAddress)
      ));
      workerBridgeState.multisessionKeys.push(key);
      workerBridgeState.activeMultisessionKeyId = key.status === "active" ? (key.id || key.localRef) : "";
      saveWorkerBridgeState();
      return key;
    }

    function workerMergeLoadedMultisessionKeys(result, walletAddress) {
      const wallet = workerLowerAddress(walletAddress);
      const rawKeys = Array.isArray(result?.keys) ? result.keys : [];
      const loadedKeys = rawKeys
        .map((item) => workerNormalizeIssuedMultisessionKey({key: item, verification: {wallet_address: wallet}, hub_url: result?.hub_url || ""}))
        .filter((key) => workerMultisessionKeyMatchesWallet(key, wallet));

      workerBridgeState.multisessionKeys = workerBridgeState.multisessionKeys.filter((existing) => {
        return !workerMultisessionKeyMatchesWallet(existing, wallet);
      });

      loadedKeys.forEach((key) => {
        workerBridgeState.multisessionKeys.push(key);
      });

      const activeKey = loadedKeys.find((key) => key.status === "active") || null;
      workerBridgeState.activeMultisessionKeyId = activeKey ? (activeKey.id || activeKey.localRef) : "";
      saveWorkerBridgeState();
      return activeKey;
    }

    async function workerLoadMultisessionKeysForWallet(walletAddress, reason = "wallet-connect") {
      const wallet = workerLowerAddress(walletAddress);
      if (!workerWalletValidAddress(wallet)) return null;

      try {
        console.info("[worker-msk] local-cache.load.start", {wallet_address: wallet, reason});
        const result = await workerPostJson("/api/applications/worker/multisession-keys/load", {
          wallet_address: wallet,
          hub_url: workerSelectedHubUrl()
        });
        const activeKey = workerMergeLoadedMultisessionKeys(result, wallet);
        console.info("[worker-msk] local-cache.load.done", {
          wallet_address: wallet,
          reason,
          key_count: Array.isArray(result.keys) ? result.keys.length : 0,
          active_key_id: activeKey?.id || ""
        });
        if (workerSaveStatus && activeKey) {
          workerSaveStatus.textContent = `Loaded multi-session key ${activeKey.id} for ${workerShortAddress(wallet)}.`;
        }
        renderWorkerBridgeReadiness();
        return activeKey;
      } catch (error) {
        console.warn("[worker-msk] local-cache.load.failed", {wallet_address: wallet, reason, error});
        return null;
      }
    }

    function workerWalletRecordEvent(type, detail = {}) {
      const entry = {
        at: workerNowIso(),
        type: String(type || "event"),
        detail: detail && typeof detail === "object" ? detail : {}
      };
      console.log("[worker-wallet]", entry.type, entry.detail);
      if (workerSaveStatus) {
        if (entry.type === "provider.hydrate.start") {
          workerSaveStatus.textContent = "Checking existing browser wallet connection…";
        } else if (entry.type === "provider.hydrate.connected") {
          workerSaveStatus.textContent = `Connected wallet ${workerShortAddress(detail.address)} on ${detail.chainId || "unknown chain"}.`;
        } else if (entry.type === "provider.hydrate.no-account") {
          workerSaveStatus.textContent = "Connect a Worker wallet to request funds and multi-session keys.";
        } else if (entry.type === "provider.hydrate.wrong-chain") {
          workerSaveStatus.textContent = `Wallet is on ${detail.chainId || "unknown chain"}; connect to ${workerSelectedWalletChainIdHex()} before requesting keys.`;
        } else if (entry.type === "provider.hydrate.failed") {
          workerSaveStatus.textContent = `Wallet hydration failed: ${detail.message || "unknown error"}`;
        } else if (entry.type === "connect.ethers.requestAccounts.start") {
          workerSaveStatus.textContent = "Opening browser wallet account request...";
        } else if (entry.type === "connect.ethers.requestAccounts.resolved") {
          workerSaveStatus.textContent = "Wallet account request accepted; verifying signer and chain with ethers.";
        } else if (entry.type === "connect.wallet.addChain.start") {
          workerSaveStatus.textContent = `Requesting MetaMask network update to ${workerSelectedWalletRpcUrl()}.`;
        } else if (entry.type === "connect.wallet.addChain.done") {
          workerSaveStatus.textContent = `MetaMask network update accepted; switching to ${workerSelectedWalletChainName()}.`;
        } else if (entry.type === "connect.wallet.rpcProof.done") {
          workerSaveStatus.textContent = `MetaMask RPC ready on ${detail.chainId || workerSelectedWalletChainIdHex()}.`;
        } else if (entry.type === "connect.ethers.switchChain.start") {
          workerSaveStatus.textContent = `Switching wallet to ${workerSelectedWalletChainIdHex()}.`;
        } else if (entry.type === "connect.finalized.connected") {
          workerSaveStatus.textContent = `Connected wallet ${workerShortAddress(detail.address)} on ${detail.chainId || "unknown chain"}.`;
        } else if (entry.type === "disconnect.done") {
          workerSaveStatus.textContent = detail.revoked
            ? "Worker wallet disconnected and account permission revoked."
            : "Worker wallet state cleared. If a wallet popup is still pending, close it manually.";
        }
      }
      window.dispatchEvent(new CustomEvent("main-computer-worker-wallet", {detail: entry}));
      return entry;
    }

    function workerWalletErrorDetail(error) {
      return {
        code: error && typeof error === "object" ? error.code : undefined,
        message: error && typeof error === "object" ? error.message || String(error) : String(error)
      };
    }

    function workerWalletErrorMessage(error) {
      if (!error) return "unknown wallet error";
      if (error && typeof error === "object") {
        if (error.message) return String(error.message);
        if (error.error && typeof error.error === "object" && error.error.message) {
          return String(error.error.message);
        }
      }
      return String(error);
    }

    function workerErrorCode(error) {
      const seen = new Set();
      const stack = [error];
      while (stack.length) {
        const current = stack.shift();
        if (!current || typeof current !== "object" || seen.has(current)) continue;
        seen.add(current);
        if (current.code !== undefined && current.code !== null && current.code !== "") {
          return String(current.code);
        }
        for (const key of ["error", "info", "data", "cause"]) {
          if (current[key] && typeof current[key] === "object") stack.push(current[key]);
        }
      }
      return "";
    }

    function workerCompactStatusMessage(value, fallback = "Worker action failed.") {
      const raw = String(value || fallback)
        .replace(/\s+/g, " ")
        .trim();
      if (!raw) return fallback;
      if (raw.length <= WORKER_STATUS_MESSAGE_MAX_LENGTH) return raw;
      return `${raw.slice(0, WORKER_STATUS_MESSAGE_MAX_LENGTH - 1).trimEnd()}…`;
    }

    function workerUserFacingWalletErrorMessage(error, fallback = "Wallet action failed.") {
      const code = workerErrorCode(error);
      const raw = workerWalletErrorMessage(error);
      const normalized = String(raw || "").toLowerCase();

      if (code === "4001" || normalized.includes("user rejected") || normalized.includes("user denied")) {
        return "Wallet signature request was rejected.";
      }
      if (code === "-32002" || normalized.includes("request already pending")) {
        return "Wallet already has a pending request. Open the wallet and finish or cancel it.";
      }
      if (normalized.includes("no ethereum provider") || normalized.includes("no wallet provider")) {
        return "No wallet provider is available.";
      }
      if (normalized.includes("unsupported network") || normalized.includes("wrong chain")) {
        return "Wallet is not connected to the selected network.";
      }
      return workerCompactStatusMessage(raw, fallback);
    }

    function workerSetSaveStatus(message, {walletError = null, prefix = ""} = {}) {
      if (!workerSaveStatus) return;
      const text = walletError
        ? `${prefix}${workerUserFacingWalletErrorMessage(walletError)}`
        : workerCompactStatusMessage(message);
      workerSaveStatus.textContent = text;
    }

    function workerSelectedWalletProfile() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      return selected === WORKER_NETWORK_NONE ? null : (workerNetworkSession.profile || workerNetworkProfile(selected));
    }

    function workerSelectedWalletChainIdHex() {
      const profile = workerSelectedWalletProfile();
      const chainId = Number.parseInt(String(profile?.chain_id || ""), 10);
      return Number.isFinite(chainId) && chainId > 0 ? `0x${chainId.toString(16)}` : WORKER_DEV_CHAIN_ID_HEX;
    }

    function workerSelectedWalletRpcUrl() {
      return String(workerSelectedWalletProfile()?.chain_rpc_url || WORKER_DEV_CHAIN_RPC_URL);
    }

    function workerSelectedWalletChainName() {
      return String(workerSelectedWalletProfile()?.display_name || WORKER_DEV_CHAIN_NAME);
    }

    function workerSelectedWalletChainParams(options = {}) {
      const currencyName = String(options.currencyName || WORKER_DEV_CHAIN_CURRENCY_NAME);
      const currencySymbol = String(options.currencySymbol || WORKER_DEV_CHAIN_CURRENCY_SYMBOL);
      return {
        chainId: workerSelectedWalletChainIdHex(),
        chainName: workerSelectedWalletChainName(),
        nativeCurrency: {
          name: currencyName,
          symbol: currencySymbol,
          decimals: 18
        },
        rpcUrls: [workerSelectedWalletRpcUrl()],
        blockExplorerUrls: []
      };
    }

    function workerDevWalletChainParams(options = {}) {
      const currencyName = String(options.currencyName || WORKER_DEV_CHAIN_CURRENCY_NAME);
      const currencySymbol = String(options.currencySymbol || WORKER_DEV_CHAIN_CURRENCY_SYMBOL);
      return {
        chainId: WORKER_DEV_CHAIN_ID_HEX,
        chainName: WORKER_DEV_CHAIN_NAME,
        nativeCurrency: {
          name: currencyName,
          symbol: currencySymbol,
          decimals: 18
        },
        rpcUrls: [WORKER_DEV_CHAIN_RPC_URL],
        blockExplorerUrls: []
      };
    }

    function workerDevWalletLegacyRpcRepairChainParams() {
      return workerDevWalletChainParams({
        currencyName: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_NAME,
        currencySymbol: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL
      });
    }

    function workerWalletIsNativeCurrencySymbolMismatch(error) {
      const message = workerWalletErrorMessage(error).toLowerCase();
      return message.includes("nativecurrency.symbol does not match")
        || (message.includes("currency symbol") && message.includes("same chainid"));
    }

    function workerWalletIsRpcEndpointBackoff(error) {
      const message = workerWalletErrorMessage(error).toLowerCase();
      return (
        (error && typeof error === "object" && error.code === -32002)
        || message.includes("rpc endpoint returned too many errors")
        || (message.includes("retrying in") && message.includes("different rpc endpoint"))
      );
    }

    function workerWalletRpcBackoffMessage(reason = "") {
      const detail = String(reason || "").trim();
      const chainName = workerSelectedWalletChainName();
      return detail
        ? `MetaMask accepted the ${chainName} RPC update, but its internal RPC backoff is still clearing. Waiting before retrying provider access. ${detail}`
        : `MetaMask accepted the ${chainName} RPC update, but its internal RPC backoff is still clearing. Waiting before retrying provider access.`;
    }

    function workerWalletRpcRepairMessage(reason = "") {
      const detail = String(reason || "").trim();
      const chainName = workerSelectedWalletChainName();
      const rpcUrl = workerSelectedWalletRpcUrl();
      return detail
        ? `MetaMask is selected on ${chainName}, but its RPC connection is stale/loading. Requesting MetaMask to update this network to ${rpcUrl}. ${detail}`
        : `MetaMask is selected on ${chainName}, but its RPC connection is stale/loading. Requesting MetaMask to update this network to ${rpcUrl}.`;
    }

    function workerSleep(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }

    function workerProviderStateFlag(provider, key) {
      try {
        return provider && typeof provider[key] === "function" ? provider[key]() : null;
      } catch {
        return null;
      }
    }

    async function workerReadInjectedProviderMetadata(browserProvider, injectedProvider = workerWalletSelectedProvider) {
      const metadata = {
        providerPresent: Boolean(injectedProvider),
        isMetaMask: Boolean(injectedProvider?.isMetaMask),
        providerConnected: workerProviderStateFlag(injectedProvider, "isConnected"),
        chainId: "",
        accounts: [],
        networkVersion: null,
        metamaskState: null,
        errors: {}
      };

      if (!browserProvider || typeof browserProvider.send !== "function") {
        metadata.errors.provider = "No ethers browser wallet provider is available.";
        return metadata;
      }

      try {
        metadata.chainId = workerNormalizeChainIdHex(
          await workerBrowserProviderSend(browserProvider, "eth_chainId", [], "wallet chain metadata")
        );
      } catch (error) {
        metadata.errors.chainId = workerWalletErrorMessage(error);
      }

      try {
        const accounts = await workerBrowserProviderSend(browserProvider, "eth_accounts", [], "wallet account metadata");
        metadata.accounts = (Array.isArray(accounts) ? accounts : [])
          .map((account) => String(account || ""))
          .filter(Boolean);
      } catch (error) {
        metadata.errors.accounts = workerWalletErrorMessage(error);
      }

      try {
        const providerState = await workerBrowserProviderSend(browserProvider, "metamask_getProviderState", [], "MetaMask provider state");
        if (providerState && typeof providerState === "object") {
          metadata.metamaskState = providerState;
          metadata.networkVersion = providerState.networkVersion ?? null;
          if (typeof providerState.isConnected === "boolean") {
            metadata.providerConnected = providerState.isConnected;
          }
          if (!metadata.chainId && providerState.chainId) {
            metadata.chainId = workerNormalizeChainIdHex(providerState.chainId);
          }
          if (!metadata.accounts.length && Array.isArray(providerState.accounts)) {
            metadata.accounts = providerState.accounts
              .map((account) => String(account || ""))
              .filter(Boolean);
          }
        }
      } catch (error) {
        metadata.errors.metamaskProviderState = workerWalletErrorMessage(error);
      }

      return metadata;
    }

    function workerWalletMetadataNeedsRpcRepair(metadata) {
      if (!metadata || metadata.chainId !== workerSelectedWalletChainIdHex()) return false;
      if (metadata.providerConnected === false) return true;
      if (metadata.networkVersion === "loading") return true;
      return false;
    }

    async function workerProveInjectedProviderRpc(browserProvider) {
      const expectedChainId = workerSelectedWalletChainIdHex();
      const chainName = workerSelectedWalletChainName();
      let chainId = "";
      try {
        chainId = workerNormalizeChainIdHex(
          await workerBrowserProviderSend(browserProvider, "eth_chainId", [], "wallet chain proof")
        );
      } catch (error) {
        return {
          ok: false,
          chainId: "",
          reason: `Could not read MetaMask chain id: ${workerWalletErrorMessage(error)}`,
          error
        };
      }

      if (chainId !== expectedChainId) {
        return {
          ok: false,
          chainId,
          wrongChain: true,
          reason: `Wallet is on ${chainId || "unknown chain"}; expected ${expectedChainId} for ${chainName}.`
        };
      }

      try {
        const blockNumber = await workerBrowserProviderSend(browserProvider, "eth_blockNumber", [], "wallet RPC block proof");
        return {
          ok: true,
          chainId,
          blockNumber
        };
      } catch (error) {
        return {
          ok: false,
          chainId,
          rpcUnavailable: true,
          reason: `MetaMask cannot read ${chainName} through its current RPC: ${workerWalletErrorMessage(error)}`,
          error
        };
      }
    }

    async function workerProveInjectedProviderRpcWithBackoff(browserProvider, options = {}) {
      const opts = options && typeof options === "object" ? options : {};
      const reason = String(opts.reason || "rpc-proof");
      const timeoutMs = Number.isFinite(opts.timeoutMs) ? Number(opts.timeoutMs) : WORKER_METAMASK_RPC_BACKOFF_TIMEOUT_MS;
      const pollMs = Number.isFinite(opts.pollMs) ? Number(opts.pollMs) : WORKER_METAMASK_RPC_BACKOFF_POLL_MS;
      const startedAt = Date.now();
      let attempts = 0;
      let lastProof = null;

      while (Date.now() - startedAt <= timeoutMs) {
        attempts += 1;
        const proof = await workerProveInjectedProviderRpc(browserProvider);
        if (proof.ok) {
          if (attempts > 1) {
            workerWalletRecordEvent("connect.wallet.rpcProof.backoffCleared", {
              reason,
              attempts,
              elapsedMs: Date.now() - startedAt,
              chainId: proof.chainId,
              blockNumber: proof.blockNumber
            });
          }
          return {...proof, attempts};
        }

        lastProof = proof;
        if (!proof.rpcUnavailable || !workerWalletIsRpcEndpointBackoff(proof.error || proof.reason)) {
          return {...proof, attempts};
        }

        const elapsedMs = Date.now() - startedAt;
        workerWalletRecordEvent("connect.wallet.rpcProof.backoffWait", {
          reason,
          attempts,
          elapsedMs,
          timeoutMs,
          pollMs,
          message: workerWalletErrorMessage(proof.error || proof.reason)
        });
        if (workerSaveStatus) {
          workerSaveStatus.textContent = workerWalletRpcBackoffMessage(
            `Attempt ${attempts}; retrying in ${Math.round(pollMs / 1000)}s.`
          );
        }
        await workerSleep(pollMs);
      }

      const lastReason = lastProof?.reason || "MetaMask RPC backoff did not clear.";
      return {
        ok: false,
        chainId: lastProof?.chainId || "",
        rpcUnavailable: true,
        backoffExpired: true,
        attempts,
        reason: `MetaMask RPC backoff did not clear after ${Math.round(timeoutMs / 1000)}s: ${lastReason}`,
        error: lastProof?.error || null
      };
    }

    async function workerRequestDevWalletChainUpdate(browserProvider, reason = "network-repair") {
      if (!browserProvider || typeof browserProvider.send !== "function") {
        throw new Error("No ethers browser wallet provider is available.");
      }

      const expectedChainId = workerSelectedWalletChainIdHex();
      const expectedRpcUrl = workerSelectedWalletRpcUrl();

      const requestSelectedChainUpdate = async (params, repairMode) => {
        workerWalletRecordEvent("connect.wallet.addChain.start", {
          reason,
          repairMode,
          chainId: expectedChainId,
          rpcUrl: expectedRpcUrl,
          currencySymbol: params?.nativeCurrency?.symbol || ""
        });
        await workerBrowserProviderSend(
          browserProvider,
          "wallet_addEthereumChain",
          [params],
          "wallet selected-network update",
          120000
        );
        workerWalletRecordEvent("connect.wallet.addChain.done", {
          reason,
          repairMode,
          chainId: expectedChainId,
          rpcUrl: expectedRpcUrl,
          currencySymbol: params?.nativeCurrency?.symbol || ""
        });
      };

      try {
        await requestSelectedChainUpdate(workerSelectedWalletChainParams(), "canonical-mcxlag");
      } catch (error) {
        if (!workerWalletIsNativeCurrencySymbolMismatch(error)) {
          throw error;
        }
        workerWalletRecordEvent("connect.wallet.addChain.symbolMismatchFallback", {
          reason,
          chainId: expectedChainId,
          canonicalSymbol: WORKER_DEV_CHAIN_CURRENCY_SYMBOL,
          repairOnlySymbol: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL,
          message: workerWalletErrorMessage(error)
        });
        await requestSelectedChainUpdate(
          workerSelectedWalletChainParams({
            currencyName: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_NAME,
            currencySymbol: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL
          }),
          "legacy-symbol-rpc-repair"
        );
      }

      workerWalletRecordEvent("connect.ethers.switchChain.start", {
        from: "wallet-provider",
        to: expectedChainId,
        reason
      });
      await workerBrowserProviderSend(
        browserProvider,
        "wallet_switchEthereumChain",
        [{chainId: expectedChainId}],
        "wallet selected-network switch",
        60000
      );
    }

    function workerRebuildWalletBrowserProvider(ethers, injectedProvider) {
      if (!ethers || !injectedProvider) return workerWalletBrowserProvider;
      workerWalletBrowserProvider = new ethers.BrowserProvider(injectedProvider, "any");
      return workerWalletBrowserProvider;
    }

    async function workerGetEthers() {
      if (window.ethers && typeof window.ethers.BrowserProvider === "function") {
        return window.ethers;
      }
      if (!workerEthersModulePromise) {
        workerEthersModulePromise = import(WORKER_ETHERS_ESM_URL);
      }
      const ethersModule = await workerEthersModulePromise;
      if (!ethersModule || typeof ethersModule.BrowserProvider !== "function") {
        throw new Error("ethers BrowserProvider could not be loaded.");
      }
      return ethersModule;
    }

    function workerNormalizeWalletProviderInfo(info = {}, provider = null, fallbackName = "Injected wallet") {
      return {
        uuid: String(info.uuid || ""),
        name: String(info.name || fallbackName),
        icon: String(info.icon || ""),
        rdns: String(info.rdns || ""),
        isMetaMask: Boolean(provider && provider.isMetaMask)
      };
    }

    function workerAddWalletProviderCandidate(candidates, seenProviders, seenUuids, detail, fallbackName) {
      const provider = detail && detail.provider ? detail.provider : detail;
      if (!provider || seenProviders.has(provider)) return;
      const info = workerNormalizeWalletProviderInfo(
        detail && detail.info ? detail.info : {},
        provider,
        fallbackName
      );
      if (info.uuid && seenUuids.has(info.uuid)) return;
      seenProviders.add(provider);
      if (info.uuid) seenUuids.add(info.uuid);
      candidates.push({provider, info});
    }

    async function workerDiscoverInjectedWalletProviders() {
      const candidates = [];
      const seenProviders = new Set();
      const seenUuids = new Set();

      const handleAnnounceProvider = (event) => {
        if (event && event.detail && event.detail.provider) {
          workerAddWalletProviderCandidate(candidates, seenProviders, seenUuids, event.detail, "Injected wallet");
        }
      };

      if (typeof window.addEventListener === "function" && typeof window.dispatchEvent === "function") {
        window.addEventListener("eip6963:announceProvider", handleAnnounceProvider);
        window.dispatchEvent(new Event("eip6963:requestProvider"));
        await new Promise((resolve) => window.setTimeout(resolve, 300));
        window.removeEventListener("eip6963:announceProvider", handleAnnounceProvider);
      }

      const injected = window.ethereum;
      if (injected && Array.isArray(injected.providers)) {
        injected.providers.forEach((provider, index) => {
          workerAddWalletProviderCandidate(
            candidates,
            seenProviders,
            seenUuids,
            {provider, info: workerNormalizeWalletProviderInfo({}, provider, `Injected wallet ${index + 1}`)},
            `Injected wallet ${index + 1}`
          );
        });
      } else if (injected) {
        workerAddWalletProviderCandidate(
          candidates,
          seenProviders,
          seenUuids,
          {provider: injected, info: workerNormalizeWalletProviderInfo({}, injected, "Injected wallet")},
          "Injected wallet"
        );
      }

      return candidates;
    }

    function workerWalletProviderLooksLikeMetaMask(candidate) {
      const info = candidate && candidate.info ? candidate.info : {};
      const provider = candidate && candidate.provider;
      const name = String(info.name || "").toLowerCase();
      const rdns = String(info.rdns || "").toLowerCase();
      return Boolean(provider && provider.isMetaMask) || name.includes("metamask") || rdns.includes("metamask");
    }

    function workerSelectInjectedWalletProvider(candidates) {
      if (!candidates.length) {
        throw new Error("No browser wallet provider found.");
      }

      const metaMaskCandidates = candidates.filter(workerWalletProviderLooksLikeMetaMask);
      if (metaMaskCandidates.length === 1) {
        return metaMaskCandidates[0];
      }
      if (metaMaskCandidates.length > 1) {
        workerWalletRecordEvent("connect.failed.multiple-metamask-providers", {
          providers: metaMaskCandidates.map((candidate) => candidate.info)
        });
        throw new Error("Multiple MetaMask providers detected. Disable one, reload, then connect again.");
      }
      if (candidates.length === 1) {
        return candidates[0];
      }

      workerWalletRecordEvent("connect.failed.multiple-wallet-providers", {
        providers: candidates.map((candidate) => candidate.info)
      });
      throw new Error("Multiple browser wallet providers detected. Disable extras, reload, then connect again.");
    }

    async function workerGetWalletProviderContext() {
      const ethers = await workerGetEthers();
      if (!workerWalletSelectedProvider || !workerWalletBrowserProvider) {
        const selected = workerSelectInjectedWalletProvider(await workerDiscoverInjectedWalletProviders());
        workerWalletSelectedProvider = selected.provider;
        workerWalletSelectedProviderInfo = selected.info;
        workerWalletBrowserProvider = new ethers.BrowserProvider(selected.provider, "any");
        workerWalletRecordEvent("provider.selected", {provider: selected.info});
      }
      return {
        ethers,
        injectedProvider: workerWalletSelectedProvider,
        providerInfo: workerWalletSelectedProviderInfo || {},
        browserProvider: workerWalletBrowserProvider
      };
    }

    function workerResetSelectedWalletProvider() {
      workerWalletSelectedProvider = null;
      workerWalletSelectedProviderInfo = null;
      workerWalletBrowserProvider = null;
    }

    async function workerAddressFromEthersAccount(account) {
      if (!account) return "";
      if (typeof account === "string") return account;
      if (typeof account.address === "string") return account.address;
      if (typeof account.getAddress === "function") return await account.getAddress();
      return "";
    }

    function workerChainIdFromEthersNetwork(network) {
      if (!network || network.chainId === undefined || network.chainId === null) return "";
      return workerNormalizeChainIdHex(`0x${BigInt(network.chainId).toString(16)}`);
    }

    async function workerReadWalletProviderSnapshot(browserProvider = workerWalletBrowserProvider) {
      if (!browserProvider) {
        return {accounts: [], address: "", chainId: ""};
      }

      const accounts = await browserProvider.listAccounts();
      const addresses = [];
      for (const account of Array.isArray(accounts) ? accounts : []) {
        const address = await workerAddressFromEthersAccount(account);
        if (address) addresses.push(address);
      }

      const network = await browserProvider.getNetwork();
      return {
        accounts: addresses,
        address: addresses[0] || "",
        chainId: workerChainIdFromEthersNetwork(network)
      };
    }

    async function workerReadGrantedWalletProviderSnapshot(browserProvider = workerWalletBrowserProvider) {
      if (!browserProvider) {
        return {accounts: [], address: "", chainId: ""};
      }

      const accounts = await browserProvider.send("eth_accounts", []);
      const addresses = (Array.isArray(accounts) ? accounts : [])
        .map((account) => String(account || ""))
        .filter(Boolean);

      const network = await browserProvider.getNetwork();
      return {
        accounts: addresses,
        address: addresses[0] || "",
        chainId: workerChainIdFromEthersNetwork(network)
      };
    }

    async function workerEnsureDevWalletChain(browserProvider, injectedProvider = workerWalletSelectedProvider, options = {}) {
      const opts = options && typeof options === "object" ? options : {};
      const reason = String(opts.reason || "ensure-selected-worker-network");
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      if (selected === WORKER_NETWORK_NONE) {
        throw new Error("Select Mainnet, Testnet, Test, or Dev before connecting the worker wallet.");
      }
      const expectedChainId = workerSelectedWalletChainIdHex();
      const ethers = await workerGetEthers();
      let activeBrowserProvider = browserProvider || workerWalletBrowserProvider;

      if (!injectedProvider && workerWalletSelectedProvider) {
        injectedProvider = workerWalletSelectedProvider;
      }
      if (!activeBrowserProvider && injectedProvider) {
        activeBrowserProvider = workerRebuildWalletBrowserProvider(ethers, injectedProvider);
      }

      let metadata = await workerReadInjectedProviderMetadata(activeBrowserProvider, injectedProvider);
      let needsUpdate = Boolean(opts.forceUpdate);
      let updateReason = needsUpdate ? String(opts.forceReason || reason) : "";

      if (metadata.chainId !== expectedChainId) {
        needsUpdate = true;
        updateReason = metadata.chainId
          ? `wallet is on ${metadata.chainId}; expected ${expectedChainId}`
          : metadata.errors.chainId || "wallet chain is unknown";
      } else if (workerWalletMetadataNeedsRpcRepair(metadata)) {
        needsUpdate = true;
        updateReason = workerWalletRpcRepairMessage(`networkVersion=${metadata.networkVersion || "unknown"} providerConnected=${metadata.providerConnected}`);
      } else if (opts.probeRpc) {
        const proof = await workerProveInjectedProviderRpc(activeBrowserProvider);
        if (!proof.ok) {
          needsUpdate = true;
          updateReason = proof.reason || "MetaMask RPC proof failed.";
        }
      }

      if (needsUpdate) {
        if (workerSaveStatus) workerSaveStatus.textContent = workerWalletRpcRepairMessage(updateReason);
        await workerRequestDevWalletChainUpdate(activeBrowserProvider, updateReason || reason);
        await workerSleep(500);
        activeBrowserProvider = workerRebuildWalletBrowserProvider(ethers, injectedProvider);
        metadata = await workerReadInjectedProviderMetadata(activeBrowserProvider, injectedProvider);
      }

      const chainId = workerNormalizeChainIdHex(metadata.chainId);
      if (chainId !== expectedChainId) {
        throw new Error(`Wrong chain after wallet network reconciliation. Expected ${expectedChainId}, got ${chainId || "unknown"}.`);
      }

      if (opts.probeRpc) {
        const proof = needsUpdate
          ? await workerProveInjectedProviderRpcWithBackoff(activeBrowserProvider, {reason})
          : await workerProveInjectedProviderRpc(activeBrowserProvider);
        if (!proof.ok) {
          throw new Error(
            needsUpdate
              ? `MetaMask network update did not restore RPC access: ${proof.reason}`
              : proof.reason
          );
        }
        workerWalletRecordEvent("connect.wallet.rpcProof.done", {
          reason,
          chainId: proof.chainId,
          blockNumber: proof.blockNumber,
          attempts: proof.attempts || 1
        });
      }

      return {
        chainId,
        browserProvider: activeBrowserProvider,
        metadata,
        repaired: needsUpdate
      };
    }

    async function workerRefreshWalletFromProvider(reason = "refresh") {
      if (!workerWalletBrowserProvider) {
        renderWorkerBridgeReadiness();
        return null;
      }

      try {
        const snapshot = await workerReadWalletProviderSnapshot(workerWalletBrowserProvider);
        const address = workerWalletValidAddress(snapshot.address) ? snapshot.address : "";
        const chainId = workerNormalizeChainIdHex(snapshot.chainId);

        workerWalletRecordEvent("provider.refresh", {
          reason,
          address: address ? workerShortAddress(address) : "",
          chainId
        });

        if (!address) {
          workerSetPrimaryWalletState({connected: false});
          workerWalletLastAction = "Wallet account disconnected.";
        } else if (chainId !== workerSelectedWalletChainIdHex()) {
          workerSetPrimaryWalletState({connected: false});
          workerWalletLastAction = `Wallet is on ${chainId || "unknown chain"}; expected ${workerSelectedWalletChainIdHex()} for ${workerNetworkDisplayName(workerNetworkSession.selected_network)}.`;
        } else {
          const metadata = await workerReadInjectedProviderMetadata(workerWalletBrowserProvider, workerWalletSelectedProvider);
          if (workerWalletMetadataNeedsRpcRepair(metadata)) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletLastAction = workerWalletRpcRepairMessage("Click Connect Wallet to update the saved MetaMask network before funding.");
            workerWalletRecordEvent("provider.refresh.rpc-needs-repair", {
              reason,
              address,
              chainId,
              networkVersion: metadata.networkVersion,
              providerConnected: metadata.providerConnected
            });
          } else {
            workerSetPrimaryWalletState({connected: true, address, chainId});
            workerWalletLastAction = `Connected ${workerShortAddress(address)} on ${chainId}.`;
            await workerLoadMultisessionKeysForWallet(address, reason);
            await checkWorkerWalletCreditBalance({quiet: true});
          }
        }

        workerWalletHookState = "idle";
        renderWorkerBridgeReadiness();
        return snapshot;
      } catch (error) {
        workerSetPrimaryWalletState({connected: false});
        workerWalletHookState = "idle";
        workerWalletLastAction = `Wallet refresh failed: ${error.message || error}`;
        workerWalletRecordEvent("provider.refresh.failed", workerWalletErrorDetail(error));
        renderWorkerBridgeReadiness();
        return null;
      }
    }

    async function workerHydrateConnectedWalletFromProvider(reason = "page-load") {
      loadWorkerBridgeState();

      if (workerNetworkKey(workerNetworkSession.selected_network) === WORKER_NETWORK_NONE) {
        workerSetPrimaryWalletState({connected: false});
        workerWalletLastAction = "Select a worker network before connecting a wallet.";
        renderWorkerBridgeReadiness();
        return null;
      }

      if (reason === "page-load") {
        if (workerWalletPageLoadHydrationAttempted) return workerWalletHydrationPromise || null;
        workerWalletPageLoadHydrationAttempted = true;
      }
      if (workerWalletHydrationPromise) return workerWalletHydrationPromise;
      if (workerWalletHookState !== "idle") {
        workerWalletRecordEvent("provider.hydrate.ignored.busy", {reason, phase: workerWalletHookState});
        return null;
      }

      workerWalletHydrationPromise = (async () => {
        const token = workerWalletNextOperation();
        workerWalletHookState = "hydrating";
        workerWalletLastAction = "Checking existing browser wallet connection.";
        workerWalletRecordEvent("provider.hydrate.start", {reason});
        renderWorkerBridgeReadiness();

        try {
          const context = await workerGetWalletProviderContext();
          workerBindWalletProviderEvents(context.injectedProvider);

          if (!workerWalletOperationIsCurrent(token)) return null;

          const snapshot = await workerReadGrantedWalletProviderSnapshot(context.browserProvider);
          const address = workerWalletValidAddress(snapshot.address) ? snapshot.address : "";
          const chainId = workerNormalizeChainIdHex(snapshot.chainId);

          if (!workerWalletOperationIsCurrent(token)) return snapshot;

          if (!address) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletHookState = "idle";
            workerWalletLastAction = "No already-authorized browser wallet account found.";
            workerWalletRecordEvent("provider.hydrate.no-account", {reason});
            renderWorkerBridgeReadiness();
            return snapshot;
          }

          if (chainId !== workerSelectedWalletChainIdHex()) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletHookState = "idle";
            workerWalletLastAction = `Wallet is on ${chainId || "unknown chain"}; expected ${workerSelectedWalletChainIdHex()} for ${workerNetworkDisplayName(workerNetworkSession.selected_network)}.`;
            workerWalletRecordEvent("provider.hydrate.wrong-chain", {reason, address, chainId});
            renderWorkerBridgeReadiness();
            return snapshot;
          }

          const metadata = await workerReadInjectedProviderMetadata(context.browserProvider, context.injectedProvider);
          if (workerWalletMetadataNeedsRpcRepair(metadata)) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletHookState = "idle";
            workerWalletLastAction = workerWalletRpcRepairMessage("Click Connect Wallet to update the saved MetaMask network before funding.");
            workerWalletRecordEvent("provider.hydrate.rpc-needs-repair", {
              reason,
              address,
              chainId,
              networkVersion: metadata.networkVersion,
              providerConnected: metadata.providerConnected
            });
            renderWorkerBridgeReadiness();
            return snapshot;
          }

          workerSetPrimaryWalletState({connected: true, address, chainId});
          workerWalletHookState = "idle";
          workerWalletLastAction = `Connected ${workerShortAddress(address)} on ${chainId}.`;
          workerWalletRecordEvent("provider.hydrate.connected", {reason, address, chainId});
          const activeKey = await workerLoadMultisessionKeysForWallet(address, reason);
          await checkWorkerWalletCreditBalance({quiet: true});
          if (!activeKey && workerSaveStatus) {
            workerSaveStatus.textContent = `Connected wallet ${workerShortAddress(address)} on ${chainId}; no saved multi-session key loaded.`;
          }
          renderWorkerBridgeReadiness();
          return snapshot;
        } catch (error) {
          if (workerWalletOperationIsCurrent(token)) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletHookState = "idle";
            workerWalletLastAction = `Wallet hydration failed: ${error.message || error}`;
            workerWalletRecordEvent("provider.hydrate.failed", workerWalletErrorDetail(error));
            renderWorkerBridgeReadiness();
          }
          return null;
        } finally {
          workerWalletHydrationPromise = null;
        }
      })();

      return workerWalletHydrationPromise;
    }

    async function connectWorkerPrimaryWallet(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      event?.stopImmediatePropagation?.();

      loadWorkerBridgeState();

      if (workerWalletHookState !== "idle") {
        workerWalletRecordEvent("connect.ignored.busy", {phase: workerWalletHookState});
        return;
      }

      const token = workerWalletNextOperation();

      workerSetPrimaryWalletState({connected: false});
      workerSetWalletOperationState("requesting", "Browser wallet opened. Worker will stay disconnected until ethers verifies signer and chain.");

      try {
        const context = await workerGetWalletProviderContext();
        workerBindWalletProviderEvents(context.injectedProvider);

        workerWalletRecordEvent("connect.wallet.networkPreflight.start", {provider: context.providerInfo});
        workerSetWalletOperationState("requesting", "Checking browser wallet network before requesting accounts.");
        const preAccountNetwork = await workerEnsureDevWalletChain(
          context.browserProvider,
          context.injectedProvider,
          {probeRpc: true, reason: "connect-pre-account"}
        );
        context.browserProvider = preAccountNetwork.browserProvider || context.browserProvider;

        workerWalletRecordEvent("connect.ethers.requestAccounts.start", {provider: context.providerInfo});

        await context.browserProvider.send("eth_requestAccounts", []);

        if (!workerWalletOperationIsCurrent(token)) {
          throw new Error("Connect result ignored because Force Disconnect / Reset was clicked.");
        }

        workerWalletRecordEvent("connect.ethers.requestAccounts.resolved");

        workerSetWalletOperationState("stabilizing", "Wallet accepted. Verifying signer and selected worker network with ethers. Checking MetaMask RPC before enabling funding.");

        const postAccountNetwork = await workerEnsureDevWalletChain(
          context.browserProvider,
          context.injectedProvider,
          {probeRpc: true, reason: "connect-post-account"}
        );
        context.browserProvider = postAccountNetwork.browserProvider || context.browserProvider;

        if (!workerWalletOperationIsCurrent(token)) {
          throw new Error("Connect finalization ignored because Force Disconnect / Reset was clicked.");
        }

        const signer = await context.browserProvider.getSigner();
        const address = await signer.getAddress();
        const network = await context.browserProvider.getNetwork();
        const chainId = workerChainIdFromEthersNetwork(network);

        if (!workerWalletValidAddress(address)) {
          throw new Error("Wallet did not provide a valid 0x address.");
        }
        if (chainId !== workerSelectedWalletChainIdHex()) {
          throw new Error(`Wrong chain after connect. Expected ${workerSelectedWalletChainIdHex()}, got ${chainId || "unknown"}.`);
        }

        workerSetPrimaryWalletState({
          connected: true,
          address,
          chainId
        });
        workerWalletHookState = "idle";
        workerWalletLastAction = `Connected ${workerShortAddress(address)} on ${chainId}.`;
        workerWalletRecordEvent("connect.finalized.connected", {
          address,
          chainId,
          provider: context.providerInfo
        });
        await workerLoadMultisessionKeysForWallet(address, "connect");
        await checkWorkerWalletCreditBalance({quiet: true});
        renderWorkerBridgeReadiness();
      } catch (error) {
        if (workerWalletOperationIsCurrent(token)) {
          workerSetPrimaryWalletState({connected: false});
          workerWalletHookState = "idle";
          workerWalletLastAction = `Connect failed: ${error.message || error}`;
          if (workerSaveStatus) workerSaveStatus.textContent = workerWalletLastAction;
          workerWalletRecordEvent("connect.failed", workerWalletErrorDetail(error));
          renderWorkerBridgeReadiness();
        }
      }
    }

    async function disconnectWorkerPrimaryWallet(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      event?.stopImmediatePropagation?.();

      const token = workerWalletNextOperation();

      workerSetPrimaryWalletState({connected: false});
      workerSetWalletOperationState("disconnecting", "Force disconnect/reset requested.");

      let revoked = false;

      try {
        if (!workerWalletBrowserProvider && workerWalletSelectedProvider) {
          const ethers = await workerGetEthers();
          workerWalletBrowserProvider = new ethers.BrowserProvider(workerWalletSelectedProvider, "any");
        }
        if (workerWalletBrowserProvider) {
          try {
            workerWalletRecordEvent("disconnect.ethers.revokePermissions.start");
            await workerWalletBrowserProvider.send("wallet_revokePermissions", [{eth_accounts: {}}]);
            revoked = true;
            workerWalletRecordEvent("disconnect.ethers.revokePermissions.done");
          } catch (error) {
            workerWalletRecordEvent("disconnect.ethers.revokePermissions.failed", workerWalletErrorDetail(error));
          }
        }
      } finally {
        if (workerWalletOperationIsCurrent(token)) {
          workerSetPrimaryWalletState({connected: false});
          workerResetSelectedWalletProvider();
          workerWalletHookState = "idle";
          workerWalletLastAction = revoked
            ? "Disconnected and account permission revoked."
            : "Local state cleared. If a wallet popup is still pending, close it manually.";
          if (workerSaveStatus) workerSaveStatus.textContent = workerWalletLastAction;
          workerWalletRecordEvent("disconnect.done", {revoked});
          renderWorkerBridgeReadiness();
        }
      }
    }

    function workerHandleWalletAccountsChanged(accounts) {
      workerWalletRecordEvent("provider.accountsChanged.refresh", {accounts});
      workerRefreshWalletFromProvider("accountsChanged");
    }

    function workerHandleWalletChainChanged(chainId) {
      workerWalletRecordEvent("provider.chainChanged.refresh", {chainId});
      workerRefreshWalletFromProvider("chainChanged");
    }

    function workerBindWalletProviderEvents(provider = workerWalletSelectedProvider) {
      if (!provider || typeof provider.on !== "function" || workerWalletBoundProvider === provider) return;
      if (workerWalletBoundProvider && typeof workerWalletBoundProvider.removeListener === "function") {
        workerWalletBoundProvider.removeListener("accountsChanged", workerHandleWalletAccountsChanged);
        workerWalletBoundProvider.removeListener("chainChanged", workerHandleWalletChainChanged);
      }
      workerWalletBoundProvider = provider;
      provider.on("accountsChanged", workerHandleWalletAccountsChanged);
      provider.on("chainChanged", workerHandleWalletChainChanged);
    }

    async function requestWorkerFaucetCredits() {
      loadWorkerBridgeState();
      const readiness = workerComputeFaucetReadiness();
      if (!readiness.ready) {
        workerFaucetLastError = readiness.reason;
        workerBridgeState.faucet = {
          ...workerBridgeState.faucet,
          amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
          lastStatus: readiness.reason,
          lastError: readiness.reason,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = readiness.reason;
        return;
      }

      const address = readiness.address;
      workerFaucetInFlight = true;
      workerFaucetLastError = "";
      workerFaucetLastResult = null;
      workerBridgeState.faucet = {
        ...workerBridgeState.faucet,
        amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
        lastStatus: "Sending faucet request…",
        lastTxHash: "",
        lastResult: null,
        lastError: "",
        updatedAt: workerNowIso()
      };
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();

      try {
        const data = await workerPostJson("/api/xlag/dev/faucet", {
          address,
          amount_credits: WORKER_FAUCET_AMOUNT_CREDITS
        });
        workerFaucetLastResult = workerNormalizeFaucetResult(data);
        workerFaucetLastError = "";
        const txHash = workerFaucetResultValue(workerFaucetLastResult, "tx_hash") === "—" ? "" : workerFaucetLastResult.tx_hash;
        const amount = workerFaucetResultValue(workerFaucetLastResult, "amount_credits");
        const target = workerFaucetResultValue(workerFaucetLastResult, "to") === "—" ? address : workerFaucetLastResult.to;
        workerBridgeState.faucet = {
          amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
          lastStatus: `Faucet sent ${amount} credit${amount === "1" ? "" : "s"} to ${workerShortAddress(target)}.`,
          lastTxHash: txHash,
          lastResult: workerFaucetLastResult,
          lastError: "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) {
          workerSaveStatus.textContent = txHash
            ? `Faucet sent ${amount} credit${amount === "1" ? "" : "s"}: ${txHash}`
            : workerBridgeState.faucet.lastStatus;
        }
        await checkWorkerWalletCreditBalance({quiet: true});
      } catch (error) {
        workerFaucetLastError = `Faucet request failed: ${error.message || error}`;
        workerBridgeState.faucet = {
          ...workerBridgeState.faucet,
          amountCredits: WORKER_FAUCET_AMOUNT_CREDITS,
          lastStatus: workerFaucetLastError,
          lastError: workerFaucetLastError,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) workerSaveStatus.textContent = workerFaucetLastError;
      } finally {
        workerFaucetInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }


    function workerSaveHubCreditInputs() {
      loadWorkerBridgeState();
      const fallback = workerDefaultBridgeState().walletFunding;
      const current = workerBridgeState.walletFunding && typeof workerBridgeState.walletFunding === "object"
        ? workerBridgeState.walletFunding
        : {};
      workerBridgeState.walletFunding = {
        ...fallback,
        ...current,
        bridgeContractAddress: workerBridgeConfigContractAddress(),
        amountCredits: String(workerHubCreditAmount?.value || current.amountCredits || WORKER_HUB_CREDIT_DEFAULT_AMOUNT).trim() || WORKER_HUB_CREDIT_DEFAULT_AMOUNT
      };
      saveWorkerBridgeState();
    }

    function workerRandomBytes32Hex(ethers) {
      if (ethers && typeof ethers.randomBytes === "function" && typeof ethers.hexlify === "function") {
        return ethers.hexlify(ethers.randomBytes(32));
      }
      const bytes = new Uint8Array(32);
      crypto.getRandomValues(bytes);
      return `0x${Array.from(bytes).map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
    }

    function workerReceiptLogIndex(receipt, contractAddress) {
      const target = workerLowerAddress(contractAddress);
      const logs = Array.isArray(receipt?.logs) ? receipt.logs : [];
      const match = logs.find((log) => workerLowerAddress(log?.address || "") === target);
      const index = match?.logIndex ?? match?.index ?? 0;
      const parsed = Number(index);
      return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
    }


    async function workerRequireBridgeContractCode(provider, contractAddress) {
      const address = String(contractAddress || "").trim();
      if (!workerWalletValidAddress(address)) {
        throw new Error("Bridge deployment config is missing a valid escrow contract address.");
      }
      let code = "";
      try {
        if (provider && typeof provider.getCode === "function") {
          code = await workerPromiseWithTimeout(provider.getCode(address), "Bridge contract code check", WORKER_WALLET_BALANCE_TIMEOUT_MS);
        } else if (provider && typeof provider.send === "function") {
          code = await workerPromiseWithTimeout(provider.send("eth_getCode", [address, "latest"]), "Bridge contract code check", WORKER_WALLET_BALANCE_TIMEOUT_MS);
        }
      } catch (error) {
        throw new Error(`Could not verify bridge contract code at ${workerShortAddress(address)}: ${error.message || error}`);
      }
      if (!code || String(code).toLowerCase() === "0x") {
        throw new Error(`Bridge address ${workerShortAddress(address)} has no contract code. Redeploy hub-credit-bridge-escrow and refresh the Worker page.`);
      }
      return code;
    }

    function workerCreditDepositedEventFromReceipt(contract, receipt, expected) {
      const logs = Array.isArray(receipt?.logs) ? receipt.logs : [];
      const expectedContract = workerLowerAddress(expected.contractAddress);
      const expectedDepositId = String(expected.depositId || "").toLowerCase();
      const expectedAccount = workerLowerAddress(expected.account);
      const expectedAmount = BigInt(expected.amountUnits || 0);
      for (const log of logs) {
        if (workerLowerAddress(log?.address || "") !== expectedContract) continue;
        try {
          const parsed = contract.interface.parseLog(log);
          if (!parsed || parsed.name !== "CreditDeposited") continue;
          const args = parsed.args || {};
          const actualDepositId = String(args.depositId || args[0] || "").toLowerCase();
          const actualAccount = workerLowerAddress(args.account || args[1] || "");
          const actualAmount = BigInt(args.amountUnits || args[3] || 0);
          if (actualDepositId !== expectedDepositId) {
            throw new Error("CreditDeposited depositId did not match the submitted deposit id.");
          }
          if (actualAccount !== expectedAccount) {
            throw new Error("CreditDeposited account did not match the connected wallet.");
          }
          if (actualAmount !== expectedAmount) {
            throw new Error("CreditDeposited amount did not match the submitted amount.");
          }
          return {
            depositId: actualDepositId,
            account: actualAccount,
            payer: workerLowerAddress(args.payer || args[2] || ""),
            amountUnits: actualAmount,
            logIndex: Number(log.logIndex ?? log.index ?? 0) || 0
          };
        } catch (error) {
          if (String(error?.message || error).includes("did not match")) {
            throw error;
          }
        }
      }
      throw new Error("Funding receipt did not include the expected CreditDeposited event.");
    }

    async function checkWorkerWalletCreditBalance({quiet = false} = {}) {
      loadWorkerBridgeState();
      const walletAddress = workerLowerAddress(workerBridgeState.wallet.address);
      if (!workerWalletValidAddress(walletAddress)) {
        if (workerBridgeState.walletFunding) {
          workerBridgeState.walletFunding.walletBalance = null;
          workerBridgeState.walletFunding.walletBalanceStatus = "not_connected";
          workerBridgeState.walletFunding.walletBalanceError = "";
        }
        if (!quiet && workerSaveStatus) workerSaveStatus.textContent = "Connect Wallet before checking wallet balance.";
        renderWorkerBridgeReadiness();
        return null;
      }

      workerWalletCreditBalanceInFlight = true;
      workerBridgeState.walletFunding = {
        ...workerBridgeState.walletFunding,
        walletBalance: null,
        walletBalanceStatus: "checking",
        walletBalanceError: "",
        updatedAt: workerNowIso()
      };
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      try {
        let rawBalance;
        let localRpcError = null;
        try {
          rawBalance = await workerReadWalletBalanceFromLocalRpc(walletAddress);
        } catch (error) {
          localRpcError = error;
          try {
            const {browserProvider} = await workerGetWalletProviderContext();
            rawBalance = await workerReadWalletBalanceFromInjectedProvider(browserProvider, walletAddress);
          } catch (fallbackError) {
            const localMessage = localRpcError?.message || String(localRpcError || "");
            const fallbackMessage = fallbackError?.message || String(fallbackError || "");
            throw new Error(localMessage && fallbackMessage ? `${localMessage}; wallet provider fallback failed: ${fallbackMessage}` : fallbackMessage || localMessage);
          }
        }
        const walletBalance = workerNormalizeWalletCreditBalance(rawBalance);

        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          walletBalance,
          walletBalanceStatus: "loaded",
          walletBalanceError: "",
          lastError: "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (!quiet && workerSaveStatus) {
          workerSaveStatus.textContent = walletBalance.balance_base_units === "0"
            ? "My wallet has 0 credits. Request Faucet Funds before funding the bridge."
            : `My wallet has ${walletBalance.available_credits} credits available to fund.`;
        }
        return walletBalance;
      } catch (error) {
        const details = workerWalletBalanceErrorDetails(error);
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          walletBalance: null,
          walletBalanceStatus: "failed",
          walletBalanceError: details.message,
          lastError: details.message,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (!quiet && workerSaveStatus) workerSaveStatus.textContent = details.message;
        return null;
      } finally {
        workerWalletCreditBalanceInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    async function checkWorkerHubCreditBalance() {
      loadWorkerBridgeState();
      workerSaveHubCreditInputs();
      const walletAddress = workerLowerAddress(workerBridgeState.wallet.address);
      if (!workerWalletValidAddress(walletAddress)) {
        const message = "Connect Wallet before checking balance.";
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          lastStatus: message,
          lastError: message,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = message;
        return null;
      }

      await checkWorkerWalletCreditBalance({quiet: true});

      workerHubCreditBalanceInFlight = true;
      renderWorkerBridgeReadiness();
      try {
        const data = await workerPostJson("/api/applications/worker/wallet-funding/balance", {
          hub_url: workerSelectedHubUrl(),
          wallet_address: walletAddress
        });
        const balance = workerNormalizeHubCreditBalance(data);
        const available = balance?.available_credits || "0";
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          balance,
          accountId: balance?.account_id || "",
          lastStatus: `My bridge account has ${available} spendable credits.`,
          lastError: "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) workerSaveStatus.textContent = workerBridgeState.walletFunding.lastStatus;
        return balance;
      } catch (error) {
        const message = `Balance check failed: ${error.message || error}`;
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          lastStatus: message,
          lastError: message,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) workerSaveStatus.textContent = message;
        return null;
      } finally {
        workerHubCreditBalanceInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    async function fundWorkerHubCredit(event) {
      event?.preventDefault?.();
      loadWorkerBridgeState();
      await workerRefreshHubCreditBridgeConfig({force: true});
      workerSaveHubCreditInputs();
      const readiness = workerComputeHubCreditFundingReadiness();
      if (!readiness.ready) {
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          lastStatus: readiness.reason,
          lastError: readiness.reason,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = readiness.reason;
        return;
      }

      workerHubCreditFundingInFlight = true;
      workerBridgeState.walletFunding = {
        ...workerBridgeState.walletFunding,
        lastStatus: "Waiting for wallet confirmation…",
        lastError: "",
        updatedAt: workerNowIso()
      };
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();

      try {
        const context = await workerGetWalletProviderContext();
        const {ethers} = context;
        const fundingNetwork = await workerEnsureDevWalletChain(
          context.browserProvider,
          context.injectedProvider,
          {probeRpc: true, reason: "funding-preflight"}
        );
        const browserProvider = fundingNetwork.browserProvider || context.browserProvider;
        const signer = await browserProvider.getSigner();
        const signerAddress = workerLowerAddress(await signer.getAddress());
        const walletAddress = workerLowerAddress(readiness.address);
        if (signerAddress !== walletAddress) {
          throw new Error(`Wallet signer changed from ${workerShortAddress(walletAddress)} to ${workerShortAddress(signerAddress)}.`);
        }

        const amountUnits = workerCreditsToBaseUnits(readiness.amountCredits);
        await workerRequireBridgeContractCode(browserProvider, readiness.contractAddress);
        const depositId = workerRandomBytes32Hex(ethers);
        const memo = `my bridge account funding ${workerShortAddress(walletAddress)}`;
        const contract = new ethers.Contract(readiness.contractAddress, WORKER_HUB_CREDIT_BRIDGE_ESCROW_ABI, signer);
        const tx = await contract.depositFor(walletAddress, amountUnits, depositId, memo, {value: amountUnits});
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          lastStatus: `Funding transaction submitted: ${tx.hash || "pending"}`,
          lastTxHash: tx.hash || "",
          lastError: "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();

        const receipt = typeof tx.wait === "function" ? await tx.wait() : null;
        const txHash = String(receipt?.hash || receipt?.transactionHash || tx.hash || "");
        const depositedEvent = workerCreditDepositedEventFromReceipt(contract, receipt, {
          contractAddress: readiness.contractAddress,
          depositId,
          account: walletAddress,
          amountUnits
        });
        const completionResult = await workerPostJson("/api/applications/worker/wallet-funding/complete", {
          hub_url: workerSelectedHubUrl(),
          wallet_address: walletAddress,
          deposit_receipt: {
            wallet_address: walletAddress,
            chain_id: workerDecimalChainId(workerBridgeState.wallet.chainId || WORKER_DEV_CHAIN_ID_HEX),
            contract_address: readiness.contractAddress,
            tx_hash: txHash,
            log_index: depositedEvent.logIndex ?? workerReceiptLogIndex(receipt, readiness.contractAddress),
            block_number: Number(receipt?.blockNumber || 0),
            deposit_id: depositId
          }
        });

        const balance = workerNormalizeHubCreditBalance(completionResult);
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          bridgeContractAddress: readiness.contractAddress,
          amountCredits: readiness.amountCredits,
          balance: balance || workerBridgeState.walletFunding.balance,
          accountId: String(completionResult.account_id || balance?.account_id || ""),
          lastStatus: completionResult.idempotent
            ? "Funding was already completed; my bridge balance is current."
            : `My bridge account funded for ${workerShortAddress(walletAddress)}.`,
          lastTxHash: txHash,
          lastCompletionTxHash: String(completionResult.completion_tx_hash || ""),
          lastDepositId: depositId,
          lastError: "",
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) workerSaveStatus.textContent = workerBridgeState.walletFunding.lastStatus;
        await checkWorkerWalletCreditBalance({quiet: true});
        await checkWorkerHubCreditBalance();
      } catch (error) {
        const message = `Funding failed: ${error.message || error}`;
        workerBridgeState.walletFunding = {
          ...workerBridgeState.walletFunding,
          lastStatus: message,
          lastError: message,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        if (workerSaveStatus) workerSaveStatus.textContent = message;
      } finally {
        workerHubCreditFundingInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }



    function addWorkerRecoveryMethod(type, value) {
      loadWorkerBridgeState();
      const clean = String(value || "").trim();
      if (!clean) return;
      const listName = type === "email" ? "recoveryEmails" : "recoveryWallets";
      const normalized = type === "email" ? clean.toLowerCase() : clean.toLowerCase();
      const existing = workerBridgeState[listName].find((item) => item.value.toLowerCase() === normalized);
      if (existing) {
        existing.status = "active";
        existing.removedAt = "";
      } else {
        workerBridgeState[listName].push({
          id: `${type}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
          status: "active",
          value: clean,
          addedAt: workerNowIso(),
          removedAt: ""
        });
      }
      workerBridgeState.recoveryConfirmedAt = "";
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = `${type === "email" ? "Recovery email" : "Recovery wallet"} added locally.`;
    }

    function removeWorkerRecoveryMethod(type, id) {
      loadWorkerBridgeState();
      const listName = type === "email" ? "recoveryEmails" : "recoveryWallets";
      const item = workerBridgeState[listName].find((entry) => entry.id === id);
      if (!item) return;
      item.status = "removed";
      item.removedAt = workerNowIso();
      workerBridgeState.recoveryConfirmedAt = "";
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = `${type === "email" ? "Recovery email" : "Recovery wallet"} removed locally.`;
    }

    function confirmWorkerRecoverySetup() {
      loadWorkerBridgeState();
      const activeCount = workerBridgeState.recoveryEmails.filter((item) => item.status === "active").length
        + workerBridgeState.recoveryWallets.filter((item) => item.status === "active").length;
      if (!activeCount) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Add at least one recovery method before confirming recovery setup.";
        renderWorkerBridgeReadiness();
        return;
      }
      workerBridgeState.recoveryConfirmedAt = workerNowIso();
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = "Local recovery setup confirmed.";
    }

    async function requestWorkerMultisessionKey(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      loadWorkerBridgeState();

      if (workerMultisessionInFlight) {
        console.info("[worker-msk] request.ignored", {reason: "already-in-flight"});
        return;
      }
      if (workerActiveMultisessionKey()) {
        console.info("[worker-msk] request.blocked", {reason: "active-key-present"});
        if (workerSaveStatus) workerSaveStatus.textContent = "Revoke the active multi-session key before requesting a replacement.";
        renderWorkerBridgeReadiness();
        return;
      }
      if (!workerBridgeState.wallet.connected || !workerWalletValidAddress(workerBridgeState.wallet.address)) {
        console.info("[worker-msk] request.blocked", {reason: "wallet-not-connected"});
        if (workerSaveStatus) workerSaveStatus.textContent = "Connect a Worker wallet before requesting a multi-session key.";
        renderWorkerBridgeReadiness();
        return;
      }
      const walletLibrary = window.MainComputerWalletLibrary || window.MainComputerWalletApp || {};
      if (typeof walletLibrary.requestMultiSessionKeySignature !== "function") {
        console.info("[worker-msk] request.blocked", {reason: "wallet-signing-library-missing"});
        if (workerSaveStatus) workerSaveStatus.textContent = "Wallet signing library is not loaded yet; open Wallet once or reload Applications.";
        renderWorkerBridgeReadiness();
        return;
      }

      const requestContext = workerBuildMultisessionRequestContext();
      workerMultisessionInFlight = true;
      renderWorkerBridgeReadiness();

      try {
        console.info("[worker-msk] request.start", requestContext);
        if (workerSaveStatus) workerSaveStatus.textContent = "Waiting for wallet signature for request_multi_session_key…";
        const signedRequest = await walletLibrary.requestMultiSessionKeySignature({
          requestContext,
          origin: window.location?.origin || "main-computer-worker"
        });

        console.info("[worker-msk] signature.created", {
          wallet_address: signedRequest?.wallet_address || "",
          chain_id: signedRequest?.chain_id || "",
          request_id: signedRequest?.message?.request_id || ""
        });
        if (workerSaveStatus) workerSaveStatus.textContent = "Requesting multi-session key from local app; hub will verify the signature.";
        const result = await workerPostJson("/api/applications/worker/multisession-key/request", {
          hub_url: workerSelectedHubUrl(),
          signed_request: signedRequest,
          client_metadata: {
            source: "worker-request-new-key-button",
            requested_at: workerNowIso(),
            wallet_address: requestContext.wallet_address,
            chain_id: requestContext.chain_id
          }
        });
        console.info("[worker-msk] hub.response", result);

        const key = workerStoreIssuedMultisessionKey(result);
        if (workerSaveStatus) {
          const verified = result?.verification?.recovered_address
            ? ` verified ${workerShortAddress(result.verification.recovered_address)}`
            : " verified signature";
          workerSaveStatus.textContent = `Hub issued multi-session key ${key.id};${verified}.`;
        }
      } catch (error) {
        console.error("[worker-msk] request.failed", error);
        if (workerSaveStatus) workerSaveStatus.textContent = `Multi-session key request failed: ${error.message || error}`;
      } finally {
        workerMultisessionInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    async function workerRequestReplacementMultisessionKeyForStart({reason = "start-working-retry", statusMessage = ""} = {}) {
      loadWorkerBridgeState();
      if (workerMultisessionInFlight) {
        throw new Error("A multi-session key request is already in progress.");
      }
      if (!workerBridgeState.wallet.connected || !workerWalletValidAddress(workerBridgeState.wallet.address)) {
        throw new Error("Connect a Worker wallet before requesting a multi-session key.");
      }
      const walletLibrary = window.MainComputerWalletLibrary || window.MainComputerWalletApp || {};
      if (typeof walletLibrary.requestMultiSessionKeySignature !== "function") {
        throw new Error("Wallet signing library is not loaded yet; open Wallet once or reload Applications.");
      }

      const requestContext = workerBuildMultisessionRequestContext();
      workerMultisessionInFlight = true;
      renderWorkerBridgeReadiness();
      try {
        console.info("[worker-msk] connect-path.request.start", {
          reason,
          wallet_address: requestContext.wallet_address,
          chain_id: requestContext.chain_id,
          hub_url: workerSelectedHubUrl()
        });
        if (workerSaveStatus) {
          workerSaveStatus.textContent = statusMessage || "No active multi-session key is loaded for this Hub; signing a key request before worker registration…";
        }
        const signedRequest = await walletLibrary.requestMultiSessionKeySignature({
          requestContext,
          origin: window.location?.origin || "main-computer-worker"
        });
        const result = await workerPostJson("/api/applications/worker/multisession-key/request", {
          hub_url: workerSelectedHubUrl(),
          signed_request: signedRequest,
          client_metadata: {
            source: reason === "hub-reported-saved-key-inactive"
              ? "worker-start-inactive-key-retry"
              : "worker-start-missing-key",
            retry_reason: reason,
            requested_at: workerNowIso(),
            wallet_address: requestContext.wallet_address,
            chain_id: requestContext.chain_id
          }
        });
        const key = workerStoreIssuedMultisessionKey(result);
        console.info("[worker-msk] connect-path.request.done", {
          reason,
          key_id: key.id,
          hub_url: workerSelectedHubUrl()
        });
        return key;
      } finally {
        workerMultisessionInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    async function revokeWorkerMultisessionKey(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      loadWorkerBridgeState();
      const activeKey = workerActiveMultisessionKey();
      if (!activeKey) {
        if (workerSaveStatus) workerSaveStatus.textContent = "No active multi-session key to revoke.";
        renderWorkerBridgeReadiness();
        return;
      }
      if (!workerBridgeState.wallet.connected || !workerWalletValidAddress(workerBridgeState.wallet.address)) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Connect the Worker wallet before revoking the saved multi-session key.";
        renderWorkerBridgeReadiness();
        return;
      }

      workerMultisessionInFlight = true;
      renderWorkerBridgeReadiness();
      try {
        const result = await workerPostJson("/api/applications/worker/multisession-key/revoke", {
          wallet_address: workerBridgeState.wallet.address,
          hub_url: workerSelectedHubUrl()
        });
        workerMergeLoadedMultisessionKeys(result, workerBridgeState.wallet.address);
        if (!Array.isArray(result?.keys)) {
          activeKey.status = "revoked";
          activeKey.revokedAt = workerNowIso();
          workerBridgeState.activeMultisessionKeyId = "";
          saveWorkerBridgeState();
        }
        const hubRevokeOk = Boolean(result?.hub_revoke?.ok);
        if (workerSaveStatus) {
          workerSaveStatus.textContent = hubRevokeOk
            ? "Active multi-session key revoked in the local backend and Hub."
            : "Active multi-session key revoked in the local backend. Hub revoke was unavailable or failed.";
        }
      } catch (error) {
        if (workerSaveStatus) workerSaveStatus.textContent = `Failed to revoke multi-session key: ${workerErrorText(error)}`;
      } finally {
        workerMultisessionInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    function workerRoleLabel(role) {
      return role === "use-only"
        ? "Buy/request only"
        : role === "provide-only"
          ? "Sell work only"
          : role === "disabled"
            ? "Disabled"
            : "Buy + sell";
    }

    function workerNetworkKey(value) {
      const key = String(value || WORKER_NETWORK_NONE).trim().toLowerCase();
      return [...WORKER_NETWORK_ORDER, WORKER_NETWORK_NONE].includes(key) ? key : WORKER_NETWORK_NONE;
    }

    function workerNetworkProfile(key = workerNetworkSession.selected_network) {
      return workerNetworkProfiles[workerNetworkKey(key)] || null;
    }

    function workerNetworkDisplayName(key = workerNetworkSession.selected_network) {
      const normalized = workerNetworkKey(key);
      if (normalized === WORKER_NETWORK_NONE) return "None";
      return workerNetworkProfile(normalized)?.display_name || normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }

    function workerRingLabel(ring = workerNetworkSession.requested_ring) {
      const normalized = String(ring || WORKER_DEFAULT_RING);
      return WORKER_RING_LABELS[normalized] || WORKER_RING_LABELS[WORKER_DEFAULT_RING];
    }

    function workerNetworkWalletConnectedToSelected() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      if (selected === WORKER_NETWORK_NONE) return false;
      loadWorkerBridgeState();
      const wallet = workerBridgeState.wallet || {};
      const walletAddress = String(wallet.address || "");
      const walletChainId = workerNormalizeChainIdHex(wallet.chainId || "");
      return (
        Boolean(wallet.connected)
        && workerWalletValidAddress(walletAddress)
        && walletChainId === workerSelectedWalletChainIdHex()
      );
    }

    function workerNetworkStatusLabel(status = workerNetworkSession.connection_status) {
      const normalized = String(status || "disconnected");
      if (normalized === "connected") {
        return workerNetworkWalletConnectedToSelected() ? "Wallet connected" : "Wallet required";
      }
      if (normalized === "connecting") return "Connecting";
      if (normalized === "failed") return "Connection failed";
      if (normalized === "stale") return "Reconnect required";
      return "Disconnected";
    }

    function workerNetworkSignedConnection() {
      const signed = workerNetworkSession.signed_connection;
      return signed && typeof signed === "object" ? signed : {};
    }

    function workerNetworkSignedConnectionWorker() {
      const signed = workerNetworkSignedConnection();
      const worker = signed.worker && typeof signed.worker === "object"
        ? signed.worker
        : workerNetworkSession.hub_registration && typeof workerNetworkSession.hub_registration === "object" && workerNetworkSession.hub_registration.worker && typeof workerNetworkSession.hub_registration.worker === "object"
          ? workerNetworkSession.hub_registration.worker
          : {};
      return worker;
    }

    function workerNetworkSignedConnectionPool() {
      const signed = workerNetworkSignedConnection();
      const pool = signed.pool && typeof signed.pool === "object"
        ? signed.pool
        : workerNetworkSession.worker_pool && typeof workerNetworkSession.worker_pool === "object"
          ? workerNetworkSession.worker_pool
          : {};
      return pool;
    }

    function workerPoolCountText(pool) {
      const available = pool?.available_worker_count ?? pool?.available_workers ?? "";
      const total = pool?.worker_count ?? pool?.total_workers ?? "";
      if (available !== "" && total !== "") return `${available} available / ${total} total`;
      if (total !== "") return `${total} total`;
      return "—";
    }

    function workerNetworkSignedForSelected() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const signed = workerNetworkSignedConnection();
      return (
        signed.network === selected
        && String(signed.requested_ring || "") === String(workerNetworkSession.requested_ring || "")
        && workerWalletValidAddress(signed.wallet_address || "")
        && workerNetworkWalletConnectedToSelected()
      );
    }

    function workerNetworkHubRegistrationStatus() {
      const signed = workerNetworkSignedConnection();
      const explicit = String(signed.hub_registration_status || "").trim();
      if (explicit) return explicit;
      if (signed.status === "hub-registered" || signed.status === "registered" || Boolean(signed.hub_registered)) return "accepted";
      if (signed.status === "hub-registration-failed" || signed.hub_registration_error || signed.last_error) return "failed";
      return workerNetworkSignedForSelected() ? "not_submitted" : "not_submitted";
    }

    function workerNetworkHubRegistered() {
      const signed = workerNetworkSignedConnection();
      return workerNetworkHubRegistrationStatus() === "accepted" && Boolean(signed.hub_registered);
    }

    function workerNetworkCanRetryHubRegistration() {
      if (!workerNetworkCanWorkNow()) return false;
      if (!workerNetworkSignedForSelected()) return false;
      if (workerNetworkHubRegistered()) return false;
      return ["not_submitted", "failed", "stale"].includes(workerNetworkHubRegistrationStatus());
    }

    function workerRuntimePhaseLabel(phase = workerRuntimeStatus.phase) {
      const normalized = String(phase || "not_accepting");
      if (normalized === "accepting") return "Accepting work";
      if (normalized === "draining") return "Finishing current work";
      return "Not accepting";
    }

    function workerAvailabilityModeLabel(mode) {
      const normalized = String(mode || workerSellerAvailabilityModeFromForm() || WORKER_SELLER_AVAILABILITY_TOTAL_IDLE);
      if (normalized === WORKER_SELLER_AVAILABILITY_AI_IDLE) return "When AI is idle";
      return "Only when totally idle";
    }

    function workerHubAvailabilityLabel(value) {
      const normalized = String(value || "").trim();
      if (!normalized || normalized === "not_announced") return "Not announced";
      if (normalized === "available") return "Available";
      if (normalized === "busy") return "Busy";
      if (normalized === "draining") return "Draining";
      if (normalized === "offline") return "Offline";
      return normalized;
    }

    function workerRuntimePolicyPayload() {
      if (workerRuntimeStatus.localPolicy && typeof workerRuntimeStatus.localPolicy === "object") {
        return workerRuntimeStatus.localPolicy;
      }
      const policy = workerRuntimeStatus.policy && typeof workerRuntimeStatus.policy === "object"
        ? workerRuntimeStatus.policy
        : {};
      if (policy.local_policy && typeof policy.local_policy === "object") {
        return policy.local_policy;
      }
      return {};
    }

    function workerRuntimePolicyLabel() {
      const localPolicy = workerRuntimePolicyPayload();
      if (localPolicy.label) {
        return localPolicy.reason ? `${localPolicy.label} — ${localPolicy.reason}` : String(localPolicy.label);
      }
      const policy = workerRuntimeStatus.policy && typeof workerRuntimeStatus.policy === "object"
        ? workerRuntimeStatus.policy
        : {};
      const userActivity = policy.user_activity && typeof policy.user_activity === "object"
        ? policy.user_activity
        : null;
      if (workerRuntimeStatus.heartbeat_error) {
        return `Hub heartbeat failed: ${workerRuntimeStatus.heartbeat_error}`;
      }
      if (policy.availability_mode === WORKER_SELLER_AVAILABILITY_AI_IDLE) {
        const localAi = policy.local_ai_capacity && typeof policy.local_ai_capacity === "object"
          ? policy.local_ai_capacity
          : null;
        if (localAi?.available_now && workerRuntimeStatus.allowed_to_accept) {
          return "Allowed — AI is idle.";
        }
        if (localAi?.busy) {
          return `Blocked — ${localAi.user_message || "Local AI is busy."}`;
        }
      }
      if (userActivity) {
        if (userActivity.active === false && workerRuntimeStatus.allowed_to_accept) {
          return "Allowed — computer is idle.";
        }
        if (userActivity.active === true) {
          return "Blocked — waiting for computer to be idle.";
        }
        if (userActivity.supported === false) {
          return `Blocked — idle status unavailable: ${userActivity.reason || "non-Windows"}.`;
        }
      }
      return workerRuntimeStatus.reason || "Waiting for setup.";
    }

    function workerRuntimePrimaryDisplay({walletAddress = "", signedForSelected = false, hubRegistered = false} = {}) {
      const localPolicy = workerRuntimePolicyPayload();
      const signedOrder = workerRuntimeStatus.signedOrder && typeof workerRuntimeStatus.signedOrder === "object"
        ? workerRuntimeStatus.signedOrder
        : {};
      const hubRegistration = workerRuntimeStatus.hubRegistration && typeof workerRuntimeStatus.hubRegistration === "object"
        ? workerRuntimeStatus.hubRegistration
        : {};
      const activeJobs = Number(workerRuntimeStatus.active_jobs || 0);
      if (workerRuntimeStatus.heartbeat_error) {
        return {
          status: "Not accepting",
          reason: `Hub heartbeat failed: ${workerRuntimeStatus.heartbeat_error}`,
          next: "Check the Hub connection and retry."
        };
      }
      if (workerRuntimeStatus.phase === "draining" && activeJobs > 0) {
        return {
          status: "Finishing current work",
          reason: "The worker is draining and will disconnect after active work finishes.",
          next: "Wait for the active job to finish."
        };
      }
      if (!workerRentalEnabled?.checked) {
        return {
          status: "Not accepting",
          reason: "Accept paid jobs is off.",
          next: "Turn on Accept paid jobs when you want this computer to work."
        };
      }
      if (!workerWalletValidAddress(walletAddress)) {
        return {
          status: "Not accepting",
          reason: "Wallet is not connected.",
          next: "Connect a wallet."
        };
      }
      if (!signedForSelected) {
        return {
          status: "Not accepting",
          reason: "Worker has not been registered with the Hub.",
          next: "Work now."
        };
      }
      if (signedOrder.status && !["ready", "signed_locally"].includes(signedOrder.status)) {
        if (signedOrder.status === "signing") {
          return {
            status: "Not accepting",
            reason: "Multi-session key request is in progress.",
            next: "Finish the multi-session key wallet prompt."
          };
        }
        if (signedOrder.status === "invalid") {
          return {
            status: "Not accepting",
            reason: "Worker registration is not ready.",
            next: "Work now."
          };
        }
        if (signedOrder.status === "expired") {
          return {
            status: "Not accepting",
            reason: "Worker registration is not ready.",
            next: "Work now."
          };
        }
      }
      if (!hubRegistered) {
        const hubStatus = hubRegistration.status || workerNetworkHubRegistrationStatus();
        if (hubStatus === "failed") {
          return {
            status: "Not accepting",
            reason: hubRegistration.lastError
              ? `Hub registration failed: ${hubRegistration.lastError}`
              : "Hub registration failed.",
            next: "Work now."
          };
        }
        if (hubStatus === "submitting") {
          return {
            status: "Not accepting",
            reason: "Worker registration is being submitted to the Hub.",
            next: "Wait for Hub registration to finish."
          };
        }
        if (hubStatus === "stale") {
          return {
            status: "Not accepting",
            reason: "Hub registration is stale.",
            next: "Work now."
          };
        }
        return {
          status: "Not accepting",
          reason: "Worker registration has not been submitted to the Hub.",
          next: "Work now."
        };
      }
      if (localPolicy.allowed === false) {
        return {
          status: "Not accepting",
          reason: localPolicy.reason || "Local policy blocks work.",
          next: localPolicy.mode === WORKER_SELLER_AVAILABILITY_AI_IDLE
            ? "Wait until local AI work finishes."
            : "Wait until the computer is idle."
        };
      }
      if (workerRuntimeStatus.phase === "accepting" && workerRuntimeStatus.allowed_to_accept) {
        return {
          status: "Accepting work",
          reason: "Hub registration accepted and local policy allows work.",
          next: "Waiting for Hub job assignment."
        };
      }
      return {
        status: workerRuntimeStatus.statusLabel || workerRuntimePhaseLabel(workerRuntimeStatus.phase),
        reason: workerRuntimeStatus.reason || "Worker is not ready.",
        next: workerRuntimeStatus.next || "Check registration and local policy."
      };
    }

    function workerSignedOrderStatusLabel(status) {
      const normalized = String(status || "").trim();
      if (normalized === "ready" || normalized === "signed_locally") return "Ready";
      if (normalized === "starting" || normalized === "signing") return "Starting";
      if (normalized === "expired" || normalized === "invalid") return "Needs restart";
      return "Not started";
    }

    function workerHubRegistrationStatusLabel(status) {
      const normalized = String(status || "").trim();
      if (normalized === "accepted") return "Accepted";
      if (normalized === "failed") return "Failed";
      if (normalized === "submitting") return "Submitting";
      if (normalized === "stale") return "Stale";
      return "Not submitted";
    }

    function workerRuntimeWorkNowOverride() {
      const direct = workerRuntimeStatus.workNowOverride && typeof workerRuntimeStatus.workNowOverride === "object"
        ? workerRuntimeStatus.workNowOverride
        : null;
      if (direct) return direct;
      const runtimePolicy = workerRuntimeStatus.policy && typeof workerRuntimeStatus.policy === "object"
        ? workerRuntimeStatus.policy
        : {};
      if (runtimePolicy.workNowOverride && typeof runtimePolicy.workNowOverride === "object") {
        return runtimePolicy.workNowOverride;
      }
      if (runtimePolicy.work_now_override && typeof runtimePolicy.work_now_override === "object") {
        return runtimePolicy.work_now_override;
      }
      const localPolicy = workerRuntimeStatus.localPolicy && typeof workerRuntimeStatus.localPolicy === "object"
        ? workerRuntimeStatus.localPolicy
        : {};
      if (localPolicy.workNowOverride && typeof localPolicy.workNowOverride === "object") {
        return localPolicy.workNowOverride;
      }
      if (localPolicy.work_now_override && typeof localPolicy.work_now_override === "object") {
        return localPolicy.work_now_override;
      }
      return {};
    }

    function workerParseTimeMs(value) {
      const raw = String(value || "").trim();
      if (!raw) return 0;
      const parsed = Date.parse(raw);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function workerWorkNowOverrideExpiresMs() {
      const override = workerRuntimeWorkNowOverride();
      return workerParseTimeMs(override.expiresAt || override.expires_at);
    }

    function workerWorkNowOverrideActive() {
      const expiresMs = workerWorkNowOverrideExpiresMs();
      return expiresMs > Date.now();
    }

    function workerFormatCountdown(ms) {
      const totalSeconds = Math.max(0, Math.ceil(Number(ms || 0) / 1000));
      const hours = Math.floor(totalSeconds / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const seconds = totalSeconds % 60;
      if (hours > 0) {
        return `${hours}h ${String(minutes).padStart(2, "0")}m`;
      }
      if (minutes > 0) {
        return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
      }
      return `${seconds}s`;
    }

    function workerWorkNowRemainingText() {
      const expiresMs = workerWorkNowOverrideExpiresMs();
      if (!expiresMs) return "";
      return workerFormatCountdown(expiresMs - Date.now());
    }

    function workerWorkNowButtonText() {
      if (workerNetworkWorkNowInFlight) {
        return workerActiveMultisessionKey() ? "Updating…" : "Creating key…";
      }
      if (workerMultisessionInFlight) {
        return "Creating key…";
      }
      if (workerWorkNowOverrideActive()) {
        return `Working now · ${workerWorkNowRemainingText()} left`;
      }
      return "Work now...";
    }

    function workerEnsureWorkNowCountdownTimer() {
      const active = workerWorkNowOverrideActive();
      if (active && !workerWorkNowCountdownTimer) {
        workerWorkNowCountdownTimer = setInterval(() => {
          if (!workerWorkNowOverrideActive()) {
            clearInterval(workerWorkNowCountdownTimer);
            workerWorkNowCountdownTimer = null;
          }
          renderWorkerNetworkSurface();
        }, 1000);
      } else if (!active && workerWorkNowCountdownTimer) {
        clearInterval(workerWorkNowCountdownTimer);
        workerWorkNowCountdownTimer = null;
      }
    }

    function workerNormalPolicyAllowsWorkNow() {
      const localPolicy = workerRuntimeStatus.localPolicy && typeof workerRuntimeStatus.localPolicy === "object"
        ? workerRuntimeStatus.localPolicy
        : {};
      if ("normalAllowed" in localPolicy) return Boolean(localPolicy.normalAllowed);
      if ("normal_allowed" in localPolicy) return Boolean(localPolicy.normal_allowed);
      return Boolean(localPolicy.allowed);
    }

    function workerApplyRuntimePayload(data) {
      if (!data || typeof data !== "object") return;
      const runtime = data.runtime && typeof data.runtime === "object" ? data.runtime : data;
      workerRuntimeStatus = {
        ...workerRuntimeStatus,
        enabled: Boolean(runtime.enabled),
        status: String(data.status || workerRuntimeStatus.status || "not_accepting"),
        statusLabel: String(data.statusLabel || runtime.label || workerRuntimePhaseLabel(runtime.phase)),
        phase: String(runtime.phase || "not_accepting"),
        active_jobs: Number(runtime.active_jobs ?? runtime.activeJobs ?? 0),
        allowed_to_accept: Boolean(runtime.allowed_to_accept ?? runtime.allowedToAccept),
        hub_status: String(runtime.hub_status || ""),
        hubAvailability: String(runtime.hubAvailability || runtime.hub_status || "not_announced"),
        reason: String(data.reason || runtime.reason || ""),
        next: String(data.next || ""),
        identity: data.identity && typeof data.identity === "object" ? data.identity : null,
        signedOrder: data.signedOrder && typeof data.signedOrder === "object" ? data.signedOrder : null,
        hubRegistration: data.hubRegistration && typeof data.hubRegistration === "object" ? data.hubRegistration : null,
        localPolicy: data.localPolicy && typeof data.localPolicy === "object" ? data.localPolicy : null,
        workNowOverride: data.workNowOverride && typeof data.workNowOverride === "object"
          ? data.workNowOverride
          : runtime.workNowOverride && typeof runtime.workNowOverride === "object"
            ? runtime.workNowOverride
            : runtime.work_now_override && typeof runtime.work_now_override === "object"
              ? runtime.work_now_override
              : null,
        worker: data.worker && typeof data.worker === "object" ? data.worker : null,
        policy: runtime.policy && typeof runtime.policy === "object" ? runtime.policy : null,
        last_checked_at: String(runtime.last_checked_at || runtime.lastCheckedAt || ""),
        last_heartbeat_at: String(runtime.last_heartbeat_at || runtime.lastHeartbeatAt || ""),
        lastError: String(runtime.lastError || ""),
        heartbeat_error: String(runtime.heartbeat_error || runtime.lastError || "")
      };
      if (data.settings && typeof data.settings === "object") {
        applyWorkerSettings(data.settings, {source: "runtime"});
      } else {
        renderWorkerNetworkSurface();
      }
    }

    async function workerSyncRuntime(action = "sync", {activeJobs = null, includeSettings = true} = {}) {
      if (workerRuntimeSyncInFlight && action === "sync") return;
      workerRuntimeSyncInFlight = true;
      renderWorkerNetworkSurface();
      const payload = {action};
      if (activeJobs !== null && activeJobs !== undefined) {
        payload.active_jobs = Number(activeJobs || 0);
      }
      if (includeSettings) {
        payload.settings = readWorkerFormSettings();
      }
      try {
        const data = await workerPostJson(WORKER_RUNTIME_SYNC_ENDPOINT, payload);
        workerApplyRuntimePayload(data);
        if (action === "job-start" && workerSaveStatus) {
          workerSaveStatus.textContent = "Worker job started. The app will keep the Hub in sync while work is active.";
        } else if (action === "job-finish" && workerSaveStatus) {
          workerSaveStatus.textContent = workerRuntimeStatus.phase === "draining"
            ? "Worker job finished; disconnecting because local policy no longer allows new work."
            : "Worker job finished. The app will keep accepting work while local policy allows it.";
        }
      } catch (error) {
        workerRuntimeStatus = {
          ...workerRuntimeStatus,
          phase: "not_accepting",
          allowed_to_accept: false,
          heartbeat_error: error.message || String(error),
          reason: error.message || String(error)
        };
        if (workerSaveStatus && action !== "sync") {
          workerSaveStatus.textContent = `Worker runtime sync failed: ${error.message || error}`;
        }
      } finally {
        workerRuntimeSyncInFlight = false;
        renderWorkerNetworkSurface();
      }
    }

    async function workerLoadRuntimeStatus() {
      try {
        const data = await workerGetJson(WORKER_RUNTIME_STATUS_ENDPOINT);
        workerApplyRuntimePayload(data);
      } catch (error) {
        workerRuntimeStatus = {
          ...workerRuntimeStatus,
          phase: "not_accepting",
          allowed_to_accept: false,
          reason: error.message || String(error)
        };
        renderWorkerNetworkSurface();
      }
    }

    function workerRuntimeShouldSync() {
      return (
        Boolean(workerRentalEnabled?.checked)
        || Boolean(workerRuntimeStatus.enabled)
        || workerRuntimeStatus.phase === "accepting"
        || workerRuntimeStatus.phase === "draining"
        || Number(workerRuntimeStatus.active_jobs || 0) > 0
      );
    }

    function workerStartRuntimeSync() {
      if (workerRuntimeSyncTimer) return;
      workerRuntimeSyncTimer = setInterval(() => {
        if (workerRuntimeShouldSync()) {
          workerSyncRuntime("sync", {includeSettings: true});
        }
      }, WORKER_RUNTIME_SYNC_INTERVAL_MS);
    }

    function workerNetworkWalletAddress() {
      loadWorkerBridgeState();
      return workerBridgeState.wallet?.address || "";
    }

    function workerNetworkCanWorkNow() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const walletAddress = workerNetworkWalletAddress();
      return (
        selected !== WORKER_NETWORK_NONE
        && workerNetworkSession.connection_status === "connected"
        && workerWalletValidAddress(walletAddress)
        && workerNetworkWalletConnectedToSelected()
        && !workerNetworkWorkNowInFlight
        && !workerMultisessionInFlight
      );
    }

    function workerNetworkSetText(element, value) {
      if (element) element.textContent = value || "—";
    }

    function workerApplyNetworkPayload(data) {
      if (!data || typeof data !== "object") return;
      workerNetworkProfiles = {};
      const networks = Array.isArray(data.networks) ? data.networks : [];
      networks.forEach((profile) => {
        const key = workerNetworkKey(profile?.network || profile?.network_key);
        if (key !== WORKER_NETWORK_NONE) {
          workerNetworkProfiles[key] = profile;
        }
      });

      const networkHubs = WORKER_NETWORK_ORDER
        .map((key) => workerNetworkProfiles[key])
        .filter(Boolean)
        .map((profile) => ({
          name: `${profile.display_name || workerNetworkDisplayName(profile.network)} Hub`,
          url: profile.hub_url || profile.hub_public_url || "",
          role: "use-provide",
          network: profile.network || profile.network_key
        }))
        .filter((hub) => hub.url);
      if (networkHubs.length) {
        const manualHubs = workerHubs.filter((hub) => !hub.network);
        workerHubs = [...networkHubs, ...manualHubs];
        renderWorkerHubs();
      }

      const session = data.session && typeof data.session === "object" ? data.session : {};
      workerNetworkSession = {
        ...workerNetworkSession,
        selected_network: workerNetworkKey(session.selected_network),
        connection_status: String(session.connection_status || "disconnected"),
        requested_ring: String(session.requested_ring || WORKER_DEFAULT_RING),
        assigned_ring: String(session.assigned_ring || ""),
        worker_id: String(session.worker_id || ""),
        pricing_policy: String(session.pricing_policy || ""),
        profile: session.profile || workerNetworkProfile(session.selected_network),
        signed_connection: session.signed_connection && typeof session.signed_connection === "object" ? session.signed_connection : {},
        hub_status: session.hub_status || null,
        hub_registration: session.hub_registration && typeof session.hub_registration === "object" ? session.hub_registration : null,
        worker_pool: session.worker_pool && typeof session.worker_pool === "object" ? session.worker_pool : null,
        connected_hub_url: String(session.connected_hub_url || ""),
        connection_error: String(session.connection_error || ""),
        connected_at: String(session.connected_at || "")
      };
      if (workerNetworkRing) workerNetworkRing.value = workerNetworkSession.requested_ring;
      renderWorkerNetworkSurface();
    }

    function renderWorkerNetworkSurface() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const profile = workerNetworkSession.profile || workerNetworkProfile(selected);
      const signed = workerNetworkSignedConnection();
      const registeredWorker = workerNetworkSignedConnectionWorker();
      const pool = workerNetworkSignedConnectionPool();
      const walletAddress = workerNetworkWalletAddress();
      const hubConnected = workerNetworkSession.connection_status === "connected";
      const walletConnectedToSelected = workerNetworkWalletConnectedToSelected();
      const signedForSelected = workerNetworkSignedForSelected();
      const hubRegistered = signedForSelected && workerNetworkHubRegistered();
      const assignedRing = String(signed.assigned_ring || registeredWorker.assigned_ring || workerNetworkSession.assigned_ring || "");
      const workerId = String(signed.worker_id || registeredWorker.worker_id || registeredWorker.node_id || workerNetworkSession.worker_id || workerRuntimeStatus.identity?.workerId || "");
      const pricingPolicy = String(signed.pricing_policy || registeredWorker.pricing_policy || registeredWorker.pricing?.pricing_policy || workerNetworkSession.pricing_policy || workerRuntimeStatus.worker?.pricingPolicy || "");
      const localPolicy = workerRuntimePolicyPayload();
      const hubRegistration = workerRuntimeStatus.hubRegistration && typeof workerRuntimeStatus.hubRegistration === "object"
        ? workerRuntimeStatus.hubRegistration
        : {};
      const signedOrder = workerRuntimeStatus.signedOrder && typeof workerRuntimeStatus.signedOrder === "object"
        ? workerRuntimeStatus.signedOrder
        : {};
      const primaryStatus = workerRuntimePrimaryDisplay({walletAddress, signedForSelected, hubRegistered});

      workerNetworkTabs.forEach((tab) => {
        const key = workerNetworkKey(tab.getAttribute("data-worker-network"));
        tab.classList.toggle("is-selected", key === selected);
        tab.setAttribute("aria-pressed", key === selected ? "true" : "false");
        if (workerNetworkSessionInFlight && key === selected) {
          tab.setAttribute("aria-busy", "true");
        } else {
          tab.removeAttribute("aria-busy");
        }
      });

      workerNetworkSetText(workerSelectedNetworkPill, `Network: ${workerNetworkDisplayName(selected)}`);
      workerNetworkSetText(workerNetworkConnectionPill, workerNetworkStatusLabel());
      workerNetworkSetText(workerNetworkSelected, workerNetworkDisplayName(selected));
      workerNetworkSetText(workerNetworkStatus, workerNetworkStatusLabel());
      workerNetworkSetText(workerNetworkHub, selected === WORKER_NETWORK_NONE ? "—" : (profile?.hub_url || workerNetworkSession.connected_hub_url || "—"));
      workerNetworkSetText(workerNetworkRpc, selected === WORKER_NETWORK_NONE ? "—" : (profile?.chain_rpc_url || "—"));
      workerNetworkSetText(workerNetworkChainId, selected === WORKER_NETWORK_NONE ? "—" : (profile?.chain_id ? String(profile.chain_id) : "—"));
      workerNetworkSetText(workerNetworkManifest, selected === WORKER_NETWORK_NONE ? "—" : (profile?.deployment_manifest_path || "runtime/deployments/" + selected + "/latest.json"));
      workerNetworkSetText(workerNetworkWallet, walletAddress ? workerShortAddress(walletAddress) : "Not connected");
      workerNetworkSetText(workerNetworkCreditWallet, signed.credit_wallet ? workerShortAddress(signed.credit_wallet) : walletAddress ? workerShortAddress(walletAddress) : "—");
      workerNetworkSetText(workerNetworkRequestedRing, workerRingLabel(workerNetworkSession.requested_ring));
      workerNetworkSetText(workerNetworkAssignedRing, hubRegistered ? workerRingLabel(assignedRing || workerNetworkSession.requested_ring) : "—");
      workerNetworkSetText(
        workerNetworkSignatureStatus,
        signedForSelected
          ? workerSignedOrderStatusLabel(signedOrder.status || signed.worker_start_status || signed.signed_order_status || "ready")
          : (["ready", "signed_locally"].includes(signedOrder.status) || ["ready", "signed_locally"].includes(signed.worker_start_status || signed.signed_order_status))
            ? "Ready for another selection"
            : workerSignedOrderStatusLabel(signedOrder.status || signed.worker_start_status || signed.signed_order_status)
      );
      workerNetworkSetText(workerNetworkHubRegistration, workerHubRegistrationStatusLabel(hubRegistration.status || signed.hub_registration_status || workerNetworkHubRegistrationStatus()));
      workerNetworkSetText(workerNetworkWorkerId, hubRegistered ? workerId || "—" : "—");
      workerNetworkSetText(workerNetworkPricingPolicy, hubRegistered ? pricingPolicy || "—" : "—");
      workerNetworkSetText(workerNetworkPool, hubRegistered ? workerPoolCountText(pool) : "—");
      workerNetworkSetText(workerRuntimeAcceptPaidJobs, workerRentalEnabled?.checked ? "On" : "Off");
      workerNetworkSetText(workerRuntimeAvailabilityMode, workerAvailabilityModeLabel(localPolicy.mode));
      workerNetworkSetText(workerRuntimePolicy, localPolicy.label ? `${localPolicy.label}${localPolicy.reason ? ` — ${localPolicy.reason}` : ""}` : workerRuntimePolicyLabel());
      workerNetworkSetText(workerRuntimePolicyReason, localPolicy.reason || workerRuntimeStatus.reason || "Waiting for setup.");
      workerNetworkSetText(workerNetworkRuntime, workerRuntimePhaseLabel());
      workerNetworkSetText(workerRuntimeHubAvailability, workerHubAvailabilityLabel(workerRuntimeStatus.hubAvailability || workerRuntimeStatus.hub_status));
      workerNetworkSetText(workerRuntimeActiveJobs, String(workerRuntimeStatus.active_jobs || 0));
      workerNetworkSetText(workerRuntimeLastUpdate, workerRuntimeStatus.last_checked_at || "—");
      workerNetworkSetText(workerRuntimeLastHeartbeat, workerRuntimeStatus.last_heartbeat_at || "—");
      workerNetworkSetText(workerRuntimeLastError, workerRuntimeStatus.lastError || workerRuntimeStatus.heartbeat_error || "—");
      workerNetworkSetText(workerRuntimePrimaryStatus, primaryStatus.status);
      workerNetworkSetText(workerRuntimePrimaryReason, primaryStatus.reason);
      workerNetworkSetText(workerRuntimePrimaryNext, primaryStatus.next);

      if (workerNetworkHelp) {
        if (selected === WORKER_NETWORK_NONE) {
          workerNetworkHelp.textContent = "No active worker network selected. Select Mainnet, Testnet, Test, or Dev to connect.";
        } else if (hubConnected && !walletConnectedToSelected) {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} Hub is reachable. Connect your wallet to ${workerNetworkDisplayName(selected)} before accepting jobs.`;
        } else if (hubConnected && walletConnectedToSelected && !signedForSelected) {
          workerNetworkHelp.textContent = `Wallet is connected to ${workerNetworkDisplayName(selected)}. Choose a ring and start working before accepting jobs.`;
        } else if (hubConnected && hubRegistered && workerRuntimeStatus.phase === "accepting") {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} worker is accepting work while quser/local policy allows it.`;
        } else if (hubConnected && hubRegistered && workerRuntimeStatus.phase === "draining") {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} worker is finishing current work, then it will disconnect.`;
        } else if (hubConnected && hubRegistered) {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} wallet and Hub registration are ready. The app connects automatically while Accept Paid Jobs and quser/local policy allow it.`;
        } else if (hubConnected && signedForSelected) {
          const hubStatus = hubRegistration.status || workerNetworkHubRegistrationStatus();
          if (hubStatus === "failed") {
            workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} worker registration is prepared, but Hub registration failed. Retry Hub registration.`;
          } else if (hubStatus === "submitting") {
            workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} worker registration is prepared and is being submitted to the Hub.`;
          } else {
            workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} worker registration is prepared, but Hub registration has not been accepted yet. Retry Hub registration.`;
          }
        } else if (workerNetworkSession.connection_status === "failed") {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} is selected, but the Hub is unreachable: ${workerNetworkSession.connection_error || "connection failed"}`;
        } else {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} is selected. Retry the connection when the Hub is available.`;
        }
      }

      const fleet = {
        mainnet: workerFleetMainnet,
        testnet: workerFleetTestnet,
        test: workerFleetTest,
        dev: workerFleetDev
      };
      WORKER_NETWORK_ORDER.forEach((key) => {
        const state = selected === key
          ? hubRegistered && hubConnected && walletConnectedToSelected
            ? workerRuntimeStatus.phase === "accepting"
              ? "Accepting work"
              : workerRuntimeStatus.phase === "draining"
                ? "Draining"
                : "Registered / not accepting"
            : signedForSelected && hubConnected && walletConnectedToSelected
              ? "Registration pending"
              : hubConnected && walletConnectedToSelected
                ? "Selected / start required"
                : hubConnected
                  ? "Selected / wallet required"
                  : "Selected / not connected"
          : "Standby";
        workerNetworkSetText(fleet[key], state);
      });

      const canSelectRealNetwork = selected !== WORKER_NETWORK_NONE;
      if (workerNetworkRetry) {
        workerNetworkRetry.disabled = workerNetworkSessionInFlight || !canSelectRealNetwork;
        workerNetworkRetry.textContent = workerNetworkSessionInFlight ? "Connecting…" : "Retry Connection";
      }
      if (workerNetworkDisconnect) {
        workerNetworkDisconnect.disabled = workerNetworkSessionInFlight && selected === WORKER_NETWORK_NONE;
      }
      workerEnsureWorkNowCountdownTimer();
      if (workerNetworkWorkNow) {
        workerNetworkWorkNow.disabled = workerNetworkWorkNowInFlight || workerMultisessionInFlight || (!workerWorkNowOverrideActive() && !workerNetworkCanWorkNow());
        workerNetworkWorkNow.textContent = workerWorkNowButtonText();
      }
      if (workerNetworkRing && workerNetworkRing.value !== workerNetworkSession.requested_ring) {
        workerNetworkRing.value = workerNetworkSession.requested_ring;
      }
      workerRenderRegistrationHubOptions();
    }

    async function workerLoadNetworkSessionFromBackend() {
      try {
        const data = await workerGetJson(WORKER_NETWORK_SESSION_ENDPOINT);
        workerApplyNetworkPayload(data);
      } catch (error) {
        workerNetworkSession = {
          ...workerNetworkSession,
          selected_network: WORKER_NETWORK_NONE,
          connection_status: "failed",
          connection_error: error.message || String(error)
        };
        renderWorkerNetworkSurface();
      }
    }

    async function workerSelectNetwork(network, {requestedRing = null} = {}) {
      const selected = workerNetworkKey(network);
      const ring = String(requestedRing || workerNetworkRing?.value || workerNetworkSession.requested_ring || WORKER_DEFAULT_RING);
      workerNetworkSessionInFlight = true;
      workerNetworkSession = {
        ...workerNetworkSession,
        selected_network: selected,
        requested_ring: ring,
        connection_status: selected === WORKER_NETWORK_NONE ? "disconnected" : "connecting",
        signed_connection: {}
      };
      renderWorkerNetworkSurface();
      try {
        const data = await workerPostJson(WORKER_NETWORK_SESSION_ENDPOINT, {network: selected, requested_ring: ring});
        workerApplyNetworkPayload(data);
        if (workerSaveStatus) {
          workerSaveStatus.textContent = selected === WORKER_NETWORK_NONE
            ? "Worker network fully disconnected."
            : workerNetworkSession.connection_status === "connected"
              ? `${workerNetworkDisplayName(selected)} Hub is reachable. Connect your wallet, then start working.`
              : `${workerNetworkDisplayName(selected)} selected but not reachable.`;
        }
      } catch (error) {
        workerNetworkSession = {
          ...workerNetworkSession,
          selected_network: selected,
          requested_ring: ring,
          connection_status: "failed",
          connection_error: error.message || String(error)
        };
        renderWorkerNetworkSurface();
        if (workerSaveStatus) workerSaveStatus.textContent = `Worker network connection failed: ${error.message || error}`;
      } finally {
        workerNetworkSessionInFlight = false;
        renderWorkerNetworkSurface();
      }
    }

    async function workerSelectNetworkAndConnectWallet(event, network, {requestedRing = null} = {}) {
      const selected = workerNetworkKey(network);
      await workerSelectNetwork(selected, {requestedRing});
      if (selected === WORKER_NETWORK_NONE) {
        await workerDisconnectSelectedNetworkAndWallet(event);
        return;
      }
      if (workerNetworkSession.connection_status !== "connected") {
        return;
      }
      if (workerNetworkWalletConnectedToSelected()) {
        renderWorkerNetworkSurface();
        return;
      }
      await connectWorkerPrimaryWallet(event);
    }

    async function workerDisconnectSelectedNetworkAndWallet(event) {
      await workerSelectNetwork(WORKER_NETWORK_NONE);
      loadWorkerBridgeState();
      if (
        workerBridgeState.wallet?.connected
        || workerWalletBrowserProvider
        || workerWalletSelectedProvider
      ) {
        await disconnectWorkerPrimaryWallet(event);
      }
      renderWorkerNetworkSurface();
    }

    function buildWorkerNetworkRegistrationPayload({walletAddress = "", activeMultisessionKey = null} = {}) {
      const offerPayload = buildWorkerOfferRegistrationPayload();
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const profile = workerNetworkSession.profile || workerNetworkProfile(selected);
      const hubUrl = profile?.hub_url || workerNetworkSession.connected_hub_url || offerPayload.hub_url;
      const normalizedWallet = workerLowerAddress(walletAddress || workerNetworkWalletAddress());
      const keyId = String(
        activeMultisessionKey?.id
        || activeMultisessionKey?.key_id
        || activeMultisessionKey?.multisession_key_id
        || workerNetworkSession.active_multisession_key_id
        || ""
      ).trim();
      const payload = {
        hub_url: hubUrl,
        network: selected,
        chain_id: String(profile?.chain_id || ""),
        requested_ring: String(workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
        wallet_address: normalizedWallet,
        credit_wallet: normalizedWallet,
        worker: {
          ...offerPayload.worker,
          capabilities: {
            ...(offerPayload.worker.capabilities || {}),
            requested_ring: String(workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
            worker_connect_network: selected,
            credit_wallet: normalizedWallet
          }
        }
      };
      if (keyId) {
        payload.active_multisession_key_id = keyId;
        payload.multisession_key_id = keyId;
      }
      return payload;
    }

    function workerNetworkRegistrationReadyForSelected() {
      return workerNetworkSignedForSelected() && workerNetworkHubRegistered();
    }

    function buildWorkerNetworkWorkNowPayload({durationSeconds = 0, action = "work-now", activeMultisessionKey = null} = {}) {
      const walletAddress = workerLowerAddress(workerNetworkWalletAddress());
      if (action !== "finish" && !workerWalletValidAddress(walletAddress)) {
        throw new Error("Connect the matching wallet before using Work now.");
      }
      const payload = buildWorkerNetworkRegistrationPayload({walletAddress, activeMultisessionKey});
      payload.action = action;
      if (durationSeconds) {
        payload.duration_seconds = Number(durationSeconds);
      }
      payload.active_jobs = Number(workerRuntimeStatus.active_jobs || 0);
      return payload;
    }

    async function workerSubmitNetworkWorkNow({durationSeconds = 0, action = "work-now", activeMultisessionKey = null} = {}) {
      return await workerPostJson(
        WORKER_NETWORK_WORK_NOW_ENDPOINT,
        buildWorkerNetworkWorkNowPayload({durationSeconds, action, activeMultisessionKey})
      );
    }

    function workerApplyWorkNowResponse(data) {
      workerApplyNetworkPayload(data);
      if (data?.runtimeStatus && typeof data.runtimeStatus === "object") {
        workerApplyRuntimePayload(data.runtimeStatus);
      } else if (data?.runtime && typeof data.runtime === "object") {
        workerApplyRuntimePayload(data);
      }
    }

    function workerCloseWorkNowDialog() {
      if (workerWorkNowDialog?.open && typeof workerWorkNowDialog.close === "function") {
        workerWorkNowDialog.close();
      }
    }

    function openWorkerWorkNowDialog(event) {
      event?.preventDefault?.();
      const overrideActive = workerWorkNowOverrideActive();
      if (!overrideActive && !workerNetworkCanWorkNow()) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Select a connected network and connect the matching wallet before using Work now.";
        renderWorkerNetworkSurface();
        return;
      }
      if (workerWorkNowDialogMessage) {
        workerWorkNowDialogMessage.textContent = overrideActive
          ? `Work-now override is active with ${workerWorkNowRemainingText()} remaining. Extend it, or finish after the current request.`
          : "Create a temporary Work-now override so this computer can work even if the normal idle policy later blocks new work.";
      }
      if (workerWorkNowFinish) {
        workerWorkNowFinish.hidden = !overrideActive;
      }
      if (workerWorkNowDialog && typeof workerWorkNowDialog.showModal === "function") {
        workerWorkNowDialog.showModal();
        return;
      }
      const minutes = window.prompt("Work now for how many minutes?", "60");
      if (minutes === null) return;
      const parsed = Number.parseInt(String(minutes || "").trim(), 10);
      if (Number.isFinite(parsed) && parsed > 0) {
        workerWorkNowForDuration(parsed * 60);
      }
    }

    async function workerWorkNowForDuration(durationSeconds) {
      const duration = Math.max(60, Math.min(Number(durationSeconds || 0), 7 * 24 * 60 * 60));
      if (!Number.isFinite(duration) || duration <= 0) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Choose a Work-now duration before continuing.";
        return;
      }
      if (!workerNetworkCanWorkNow()) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Select a connected network and connect the matching wallet before using Work now.";
        renderWorkerNetworkSurface();
        return;
      }
      workerCloseWorkNowDialog();
      workerNetworkWorkNowInFlight = true;
      renderWorkerNetworkSurface();
      try {
        let data;
        let activeMultisessionKey = workerActiveMultisessionKey();
        if (!workerNetworkRegistrationReadyForSelected() && !activeMultisessionKey) {
          workerSetSaveStatus("No active multi-session key is loaded for this Hub. Signing a fresh key request before worker registration…");
          activeMultisessionKey = await workerRequestReplacementMultisessionKeyForStart({
            reason: "work-now-no-active-key",
            statusMessage: "No active multi-session key is loaded for this Hub. Signing a fresh key request before worker registration…"
          });
          workerSetSaveStatus("Multi-session key issued. Submitting worker registration to the Hub…");
        }
        try {
          data = await workerSubmitNetworkWorkNow({durationSeconds: duration, activeMultisessionKey});
        } catch (error) {
          if (!workerIsInactiveMultisessionKeyError(error) || workerNetworkRegistrationReadyForSelected()) {
            throw error;
          }
          const staleMessage = workerErrorText(error);
          console.warn("[worker-msk] work-now.key-inactive", {
            hub_url: workerSelectedHubUrl(),
            error: staleMessage
          });
          workerMarkMultisessionKeyInactiveOnHub("", staleMessage);
          await workerLoadMultisessionKeysForWallet(workerBridgeState.wallet.address, "work-now-key-inactive");
          workerSetSaveStatus("Saved multi-session key is inactive on this Hub. Signing a fresh key request and retrying worker registration…");
          activeMultisessionKey = await workerRequestReplacementMultisessionKeyForStart({
            reason: "hub-reported-saved-key-inactive",
            statusMessage: "Saved multi-session key is inactive on this Hub; signing a fresh key request and retrying worker registration…"
          });
          workerSetSaveStatus("Replacement multi-session key issued. Retrying worker registration with the Hub…");
          data = await workerSubmitNetworkWorkNow({durationSeconds: duration, activeMultisessionKey});
        }
        workerApplyWorkNowResponse(data);
        workerSetSaveStatus(`Work-now override active for ${workerFormatCountdown(duration * 1000)} on the ${workerNetworkDisplayName(workerNetworkSession.selected_network)} Hub.`);
      } catch (error) {
        workerSetSaveStatus("", {walletError: error, prefix: "Work now failed: "});
        await Promise.allSettled([
          workerLoadNetworkSessionFromBackend(),
          workerLoadRuntimeStatus()
        ]);
      } finally {
        workerNetworkWorkNowInFlight = false;
        renderWorkerNetworkSurface();
      }
    }

    async function workerFinishWorkNowOverride(event) {
      event?.preventDefault?.();
      if (workerNormalPolicyAllowsWorkNow()) {
        const confirmed = window.confirm("Your normal worker settings currently allow work, so finishing this override may not prevent future work.\n\nFinish anyway?");
        if (!confirmed) return;
      }
      workerCloseWorkNowDialog();
      workerNetworkWorkNowInFlight = true;
      renderWorkerNetworkSurface();
      try {
        const data = await workerSubmitNetworkWorkNow({action: "finish"});
        workerApplyWorkNowResponse(data);
        workerSetSaveStatus("Work-now override will finish after the current request; normal worker policy now controls future work.");
      } catch (error) {
        workerSetSaveStatus("", {walletError: error, prefix: "Finish Work now failed: "});
        await workerLoadRuntimeStatus();
      } finally {
        workerNetworkWorkNowInFlight = false;
        renderWorkerNetworkSurface();
      }
    }

    function workerHubCanSell(hub) {
      const role = String(hub?.role || "use-provide");
      return role === "use-provide" || role === "provide-only";
    }

    function workerHubCanBuy(hub) {
      const role = String(hub?.role || "use-provide");
      return role === "use-provide" || role === "use-only";
    }

    function workerElementValue(element, fallback = "") {
      if (!element || !("value" in element)) return fallback;
      return String(element.value || fallback).trim();
    }

    function workerPositiveInteger(value, fallback = 1) {
      const parsed = Number.parseInt(String(value ?? ""), 10);
      return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
    }

    function workerNormalizeSellerCreditsPerToken(value) {
      const raw = String(value ?? "").trim();
      const normalized = raw.replace(/,/g, "");
      if (!normalized || WORKER_LEGACY_SELLER_CREDITS_PER_REQUESTS.has(normalized)) {
        return WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN;
      }
      return raw;
    }

    function workerNormalizeSellerModels(value) {
      const raw = String(value ?? "").trim();
      const models = raw.split(",").map((item) => item.trim()).filter(Boolean);
      if (!models.length) {
        return WORKER_DEFAULT_SELLER_MODEL;
      }
      if (models.length === 1 && WORKER_LEGACY_SELLER_MODELS.has(models[0])) {
        return WORKER_DEFAULT_SELLER_MODEL;
      }
      return [...new Set(models)].join(",");
    }

    function workerPositiveDecimalString(value, fallback = "1") {
      const raw = String(value ?? "").trim();
      const fallbackText = String(fallback ?? "1").trim() || "1";
      if (!/^\d+(\.\d{1,18})?$/.test(raw)) {
        return fallbackText;
      }
      const significantDigits = raw.replace(".", "").replace(/^0+/, "");
      if (!significantDigits) {
        return fallbackText;
      }
      return raw;
    }

    function workerCreditDecimalToWei(value, fallback = "1") {
      const raw = workerPositiveDecimalString(value, fallback);
      const parts = raw.split(".");
      const whole = parts[0] || "0";
      const fraction = (parts[1] || "").padEnd(18, "0");
      try {
        return (BigInt(whole) * WORKER_CREDIT_BASE_UNITS_PER_CREDIT + BigInt(fraction || "0")).toString();
      } catch {
        const fallbackText = workerPositiveDecimalString(fallback, "1");
        const fallbackParts = fallbackText.split(".");
        const fallbackWhole = fallbackParts[0] || "0";
        const fallbackFraction = (fallbackParts[1] || "").padEnd(18, "0");
        return (BigInt(fallbackWhole) * WORKER_CREDIT_BASE_UNITS_PER_CREDIT + BigInt(fallbackFraction || "0")).toString();
      }
    }

    function workerCreditWeiToDecimal(value) {
      try {
        const amount = BigInt(String(value ?? "0"));
        const whole = amount / WORKER_CREDIT_BASE_UNITS_PER_CREDIT;
        const remainder = amount % WORKER_CREDIT_BASE_UNITS_PER_CREDIT;
        if (remainder === 0n) return whole.toString();
        const fraction = remainder.toString().padStart(18, "0").replace(/0+$/, "");
        return `${whole.toString()}.${fraction}`;
      } catch {
        return "0";
      }
    }

    function workerEstimatedCreditsPerRequestFromTokenRate(creditsPerTokenWei, targetOutputTokens) {
      try {
        const tokenWei = BigInt(String(creditsPerTokenWei ?? "0"));
        const tokenCount = BigInt(Math.max(1, workerPositiveInteger(targetOutputTokens, WORKER_DEFAULT_SELLER_TARGET_TOKENS)));
        return (tokenWei * tokenCount).toString();
      } catch {
        const fallbackWei = BigInt(workerCreditDecimalToWei(WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN, WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN));
        return (fallbackWei * BigInt(WORKER_DEFAULT_SELLER_TARGET_TOKENS)).toString();
      }
    }

    function workerSavedBoolean(value, fallback = false) {
      if (typeof value === "boolean") return value;
      if (typeof value === "string") {
        const normalized = value.trim().toLowerCase();
        if (["true", "1", "yes", "on"].includes(normalized)) return true;
        if (["false", "0", "no", "off"].includes(normalized)) return false;
      }
      return Boolean(fallback);
    }

    function workerOfferModelsArray() {
      const raw = workerNormalizeSellerModels(workerElementValue(workerOfferModels, WORKER_DEFAULT_SELLER_MODEL));
      const models = raw.split(",").map((item) => item.trim()).filter(Boolean);
      return models.length ? [...new Set(models)] : [WORKER_DEFAULT_SELLER_MODEL];
    }

    function workerNetworkHubUrl() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      if (selected === WORKER_NETWORK_NONE) return "";
      const profile = workerNetworkSession.profile || workerNetworkProfile(selected);
      return String(workerNetworkSession.connected_hub_url || profile?.hub_url || "").trim();
    }

    function workerHubDisplayLabel(hubUrl) {
      const cleanUrl = String(hubUrl || "").trim();
      if (!cleanUrl) return "";
      const configured = workerHubs.find((hub) => String(hub?.url || "").trim() === cleanUrl);
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const networkLabel = selected === WORKER_NETWORK_NONE ? "" : `${workerNetworkDisplayName(selected)} Hub`;
      const label = configured?.name || networkLabel || "Connected Hub";
      return `${label} - ${cleanUrl}`;
    }

    function workerSelectedHubUrl() {
      return workerNetworkHubUrl();
    }

    function workerRenderRegistrationHubOptions(selectedUrl = "") {
      return String(workerNetworkHubUrl() || selectedUrl || "").trim();
    }

    function workerSetRegistrationStatus(message, kind = "") {
      if (workerSaveStatus) {
        workerSaveStatus.textContent = message;
      }
      if (workerRegistrationStatusPill) {
        workerRegistrationStatusPill.textContent = kind === "ok"
          ? "Offer: registered"
          : kind === "error"
            ? "Offer: registration failed"
            : "Offer: local only";
      }
    }

    function renderWorkerHubs() {
      if (!workerHubList) return;
      workerHubList.innerHTML = "";
      workerHubs.forEach((hub, index) => {
        const card = document.createElement("article");
        card.className = "worker-hub-card";
        card.innerHTML = `
          <div>
            <strong></strong>
            <span></span>
          </div>
          <div class="worker-hub-badges">
            <span>${hub.role === "disabled" ? "Disabled" : "Configured"}</span>
            <span>${workerRoleLabel(hub.role)}</span>
          </div>
          <button type="button" data-worker-remove-hub="${index}">Remove</button>
        `;
        card.querySelector("strong").textContent = hub.name || "Hub";
        card.querySelector("div > span").textContent = hub.url || "No URL set";
        workerHubList.append(card);
      });
      if (workerHubCount) {
        const sellableCount = workerHubs.filter(workerHubCanSell).length;
        const buyableCount = workerHubs.filter(workerHubCanBuy).length;
        workerHubCount.textContent = `${sellableCount} sell / ${buyableCount} buy hub${workerHubs.length === 1 ? "" : "s"}`;
      }
      workerRenderRegistrationHubOptions();
    }

    function workerSettingsChangedFields(changedFields = null) {
      if (!Array.isArray(changedFields)) return null;
      const fields = changedFields
        .map((field) => String(field || "").trim())
        .filter(Boolean);
      return fields.length ? fields : null;
    }

    function workerApplyRemoteEnabledFromBackend(value, {requestStartedAt = 0, force = false} = {}) {
      if (!workerRemoteEnabled) return false;
      const startedAt = Number(requestStartedAt || 0);
      if (!force && startedAt && startedAt < workerRemoteEnabledLastLocalEditAt) return false;
      if (!force && startedAt && startedAt < workerRemoteEnabledLastSaveCompletedAt) return false;
      workerRemoteEnabled.checked = workerSavedBoolean(value, false);
      return true;
    }

    function workerNormalizeSellerAvailabilityMode(value, fallback = WORKER_SELLER_AVAILABILITY_TOTAL_IDLE) {
      const normalized = String(value || "").trim().toLowerCase();
      if (WORKER_SELLER_AVAILABILITY_MODES.has(normalized)) return normalized;
      const fallbackMode = String(fallback || "").trim().toLowerCase();
      return WORKER_SELLER_AVAILABILITY_MODES.has(fallbackMode)
        ? fallbackMode
        : WORKER_SELLER_AVAILABILITY_TOTAL_IDLE;
    }

    function workerSellerAvailabilityModeFromForm() {
      const selected = (workerSellerAvailabilityModes || []).find((input) => input?.checked);
      return workerNormalizeSellerAvailabilityMode(selected?.value);
    }

    function workerSetSellerAvailabilityMode(mode) {
      const normalized = workerNormalizeSellerAvailabilityMode(mode);
      (workerSellerAvailabilityModes || []).forEach((input) => {
        if (!input) return;
        input.checked = String(input.value || "") === normalized;
      });
      return normalized;
    }

    function workerRefreshSellerAvailabilityControls() {
      const paidJobsEnabled = Boolean(workerRentalEnabled?.checked);
      (workerSellerAvailabilityModes || []).forEach((input) => {
        if (!input) return;
        input.disabled = !paidJobsEnabled;
      });
    }

    function readWorkerFormSettings() {
      const models = workerOfferModelsArray();
      return {
        selectedNetwork: workerNetworkKey(workerNetworkSession.selected_network),
        workerRequestedRing: String(workerNetworkRing?.value || workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
        workerConnectionStatus: String(workerNetworkSession.connection_status || "disconnected"),
        workerAssignedRing: String(workerNetworkSession.assigned_ring || ""),
        workerRegisteredId: String(workerNetworkSession.worker_id || ""),
        workerPricingPolicy: String(workerNetworkSession.pricing_policy || ""),
        workerConnectedHubUrl: String(workerNetworkSession.connected_hub_url || ""),
        workerConnectionError: String(workerNetworkSession.connection_error || ""),
        signedWorkerConnection: workerNetworkSignedConnection(),
        workerHubRegistration: workerNetworkSession.hub_registration || null,
        workerPool: workerNetworkSession.worker_pool || null,
        workerRuntimeEnabled: Boolean(workerRentalEnabled?.checked),
        workerRuntimePhase: String(workerRuntimeStatus.phase || "not_accepting"),
        workerRuntimeActiveJobs: Number(workerRuntimeStatus.active_jobs || 0),
        workerRuntimeLastReason: String(workerRuntimeStatus.reason || ""),
        remoteEnabled: workerSavedBoolean(workerRemoteEnabled?.checked, false),
        remoteMode: workerElementValue(workerRemoteMode, "ask-when-busy"),
        remoteCreditsPerToken: workerPositiveDecimalString(workerElementValue(workerRemoteCreditsPerToken, "0.001"), "0.001"),
        remoteMaxOutputTokens: workerPositiveInteger(workerElementValue(workerRemoteMaxOutputTokens, "1024"), 1024),
        remoteDailyLimit: workerPositiveInteger(workerElementValue(workerRemoteDailyLimit, "100000"), 100000),
        remoteAskBeforeSpend: Boolean(workerRemoteAskBeforeSpend?.checked),
        remoteOnlyWhenBusy: Boolean(workerRemoteOnlyWhenBusy?.checked),
        sellerEnabled: Boolean(workerRentalEnabled?.checked),
        rentalEnabled: Boolean(workerRentalEnabled?.checked),
        sellerAvailabilityMode: workerSellerAvailabilityModeFromForm(),
        sellerOnlyWhenIdle: workerSellerAvailabilityModeFromForm() === WORKER_SELLER_AVAILABILITY_TOTAL_IDLE,
        rentalOnlyWhenIdle: workerSellerAvailabilityModeFromForm() === WORKER_SELLER_AVAILABILITY_TOTAL_IDLE,
        registrationHubUrl: workerSelectedHubUrl(),
        nodeId: workerElementValue(workerNodeId, "local-worker-001"),
        endpoint: workerElementValue(workerEndpoint, "http://127.0.0.1:8771"),
        models: models.join(","),
        sellerTargetTokens: workerPositiveInteger(workerElementValue(workerOfferTargetTokens, WORKER_DEFAULT_SELLER_TARGET_TOKENS), WORKER_DEFAULT_SELLER_TARGET_TOKENS),
        capability: workerElementValue(workerOfferCapability, "chat.completions"),
        sellerCreditsPerToken: workerPositiveDecimalString(
          workerNormalizeSellerCreditsPerToken(workerElementValue(workerOfferCreditsPerToken, WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN)),
          WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN
        ),
        maxConcurrency: workerPositiveInteger(workerElementValue(workerMaxConcurrency, "1"), 1),
        executionMode: workerElementValue(workerExecutionMode, "worker_pull_v0"),
        hubs: workerHubs
      };
    }

    function saveWorkerSettings({changedFields = null, remoteEnabledSerial = 0} = {}) {
      const settings = readWorkerFormSettings();
      const requestStartedAt = Date.now();
      const fields = workerSettingsChangedFields(changedFields);
      const payload = {settings};
      if (fields) payload.changed_fields = fields;
      const tracksRemoteEnabled = !fields || fields.includes("remoteEnabled");
      workerPostJson("/api/applications/worker/settings", payload)
        .then((data) => {
          const responseSettings = data?.settings && typeof data.settings === "object" ? data.settings : {};
          if (tracksRemoteEnabled && Object.prototype.hasOwnProperty.call(responseSettings, "remoteEnabled")) {
            if (remoteEnabledSerial) {
              if (remoteEnabledSerial === workerRemoteEnabledSaveSerial) {
                workerApplyRemoteEnabledFromBackend(responseSettings.remoteEnabled, {force: true});
                workerRemoteEnabledLastSaveCompletedAt = Date.now();
              }
            } else if (workerApplyRemoteEnabledFromBackend(responseSettings.remoteEnabled, {requestStartedAt})) {
              workerRemoteEnabledLastSaveCompletedAt = Date.now();
            }
          }
          if (workerSaveStatus) {
            workerSaveStatus.textContent = "Worker marketplace buy/sell settings saved to the local backend.";
          }
        })
        .catch((error) => {
          if (workerSaveStatus) {
            workerSaveStatus.textContent = `Worker settings could not be saved to the local backend: ${error.message || error}`;
          }
        });
    }

    function assignWorkerValue(element, value) {
      if (!element || !("value" in element) || value === undefined || value === null) return;
      element.value = String(value);
    }

    function applyWorkerSettings(parsed, {requestStartedAt = 0, source = "load"} = {}) {
      if (!parsed || typeof parsed !== "object") return;
      if (Array.isArray(parsed.hubs)) {
        workerHubs = parsed.hubs
          .map((hub) => ({
            name: String(hub.name || "").trim(),
            url: String(hub.url || "").trim(),
            role: String(hub.role || "use-provide")
          }))
          .filter((hub) => hub.name || hub.url);
      }
      if (Object.prototype.hasOwnProperty.call(parsed, "selectedNetwork")) {
        workerNetworkSession = {
          ...workerNetworkSession,
          selected_network: workerNetworkKey(parsed.selectedNetwork),
          requested_ring: String(parsed.workerRequestedRing || workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
          connection_status: String(parsed.workerConnectionStatus || workerNetworkSession.connection_status || "disconnected"),
          assigned_ring: String(parsed.workerAssignedRing || workerNetworkSession.assigned_ring || ""),
          worker_id: String(parsed.workerRegisteredId || workerNetworkSession.worker_id || ""),
          pricing_policy: String(parsed.workerPricingPolicy || workerNetworkSession.pricing_policy || ""),
          connected_hub_url: String(parsed.workerConnectedHubUrl || workerNetworkSession.connected_hub_url || ""),
          connection_error: String(parsed.workerConnectionError || ""),
          signed_connection: parsed.signedWorkerConnection && typeof parsed.signedWorkerConnection === "object" ? parsed.signedWorkerConnection : workerNetworkSignedConnection(),
          hub_registration: parsed.workerHubRegistration && typeof parsed.workerHubRegistration === "object" ? parsed.workerHubRegistration : workerNetworkSession.hub_registration,
          worker_pool: parsed.workerPool && typeof parsed.workerPool === "object" ? parsed.workerPool : workerNetworkSession.worker_pool
        };
        if (Object.prototype.hasOwnProperty.call(parsed, "workerRuntimeEnabled")) {
          workerRuntimeStatus = {
            ...workerRuntimeStatus,
            enabled: Boolean(parsed.workerRuntimeEnabled),
            phase: String(parsed.workerRuntimePhase || "not_accepting"),
            active_jobs: Number(parsed.workerRuntimeActiveJobs || 0),
            reason: String(parsed.workerRuntimeLastReason || ""),
            heartbeat_error: String(parsed.workerRuntimeError || "")
          };
        }
        renderWorkerNetworkSurface();
      }
      if (Object.prototype.hasOwnProperty.call(parsed, "remoteEnabled")) {
        workerApplyRemoteEnabledFromBackend(parsed.remoteEnabled, {requestStartedAt});
      }
      assignWorkerValue(workerRemoteMode, parsed.remoteMode);
      assignWorkerValue(workerRemoteCreditsPerToken, parsed.remoteCreditsPerToken);
      assignWorkerValue(workerRemoteMaxOutputTokens, parsed.remoteMaxOutputTokens);
      assignWorkerValue(workerRemoteDailyLimit, parsed.remoteDailyLimit);
      if (workerRemoteAskBeforeSpend && typeof parsed.remoteAskBeforeSpend === "boolean") {
        workerRemoteAskBeforeSpend.checked = parsed.remoteAskBeforeSpend;
      }
      if (workerRemoteOnlyWhenBusy && typeof parsed.remoteOnlyWhenBusy === "boolean") {
        workerRemoteOnlyWhenBusy.checked = parsed.remoteOnlyWhenBusy;
      }
      if (workerRentalEnabled) {
        if (typeof parsed.sellerEnabled === "boolean") {
          workerRentalEnabled.checked = parsed.sellerEnabled;
        } else if (typeof parsed.rentalEnabled === "boolean") {
          workerRentalEnabled.checked = parsed.rentalEnabled;
        }
      }
      if (workerSellerAvailabilityModes?.length) {
        if (typeof parsed.sellerAvailabilityMode === "string") {
          workerSetSellerAvailabilityMode(parsed.sellerAvailabilityMode);
        } else if (typeof parsed.sellerOnlyWhenIdle === "boolean") {
          workerSetSellerAvailabilityMode(parsed.sellerOnlyWhenIdle ? WORKER_SELLER_AVAILABILITY_TOTAL_IDLE : WORKER_SELLER_AVAILABILITY_AI_IDLE);
        } else if (typeof parsed.rentalOnlyWhenIdle === "boolean") {
          workerSetSellerAvailabilityMode(parsed.rentalOnlyWhenIdle ? WORKER_SELLER_AVAILABILITY_TOTAL_IDLE : WORKER_SELLER_AVAILABILITY_AI_IDLE);
        } else {
          workerSetSellerAvailabilityMode(WORKER_SELLER_AVAILABILITY_TOTAL_IDLE);
        }
      }
      workerRefreshSellerAvailabilityControls();
      assignWorkerValue(workerNodeId, parsed.nodeId);
      assignWorkerValue(workerEndpoint, parsed.endpoint);
      assignWorkerValue(workerOfferModels, workerNormalizeSellerModels(parsed.models));
      assignWorkerValue(workerOfferTargetTokens, parsed.sellerTargetTokens);
      assignWorkerValue(workerOfferCapability, parsed.capability);
      assignWorkerValue(workerOfferCreditsPerToken, workerNormalizeSellerCreditsPerToken(parsed.sellerCreditsPerToken));
      assignWorkerValue(workerMaxConcurrency, parsed.maxConcurrency);
      assignWorkerValue(workerExecutionMode, parsed.executionMode);
      workerRenderRegistrationHubOptions(parsed.registrationHubUrl);
      renderWorkerHubs();
    }

    async function workerLoadSettingsFromBackend() {
      const requestStartedAt = Date.now();
      try {
        const data = await workerGetJson("/api/applications/worker/settings");
        applyWorkerSettings(data.settings || {}, {requestStartedAt, source: "load"});
      } catch (error) {
        workerHubs = [...workerDefaultHubs];
        renderWorkerHubs();
        if (workerSaveStatus) {
          workerSaveStatus.textContent = `Worker settings could not be loaded from the local backend: ${error.message || error}`;
        }
      }
    }

    async function workerPollRemoteEnabledFromBackend() {
      if (workerSettingsPollInFlight) return;
      workerSettingsPollInFlight = true;
      const requestStartedAt = Date.now();
      try {
        const data = await workerGetJson("/api/applications/worker/settings");
        const settings = data.settings && typeof data.settings === "object" ? data.settings : {};
        if (Object.prototype.hasOwnProperty.call(settings, "remoteEnabled")) {
          workerApplyRemoteEnabledFromBackend(settings.remoteEnabled, {requestStartedAt, source: "poll"});
        }
      } catch {
        // Polling is a silent cross-window synchronization path. The initial load
        // and explicit saves keep the visible error/status behavior.
      } finally {
        workerSettingsPollInFlight = false;
      }
    }

    function workerStartSettingsPolling() {
      if (workerSettingsPollTimer) return;
      workerSettingsPollTimer = setInterval(workerPollRemoteEnabledFromBackend, WORKER_SETTINGS_POLL_INTERVAL_MS);
    }

    function loadWorkerSettings() {
      if (workerSettingsLoaded) return;
      workerSettingsLoaded = true;
      renderWorkerHubs();
      workerLoadSettingsFromBackend();
      workerStartSettingsPolling();
    }

    function buildWorkerOfferRegistrationPayload() {
      const settings = readWorkerFormSettings();
      const models = workerOfferModelsArray();
      if (!settings.sellerEnabled) {
        throw new Error("Accept paid jobs is off. Enable it before registering a seller offer.");
      }
      if (!settings.registrationHubUrl) {
        throw new Error("Select a worker connection before registering an offer.");
      }
      if (!settings.nodeId) {
        throw new Error("Worker node id is required.");
      }
      if (!settings.endpoint) {
        throw new Error("Worker callback endpoint is required.");
      }
      if (!models.length) {
        throw new Error("At least one model is required.");
      }
      const targetOutputTokens = workerPositiveInteger(settings.sellerTargetTokens, WORKER_DEFAULT_SELLER_TARGET_TOKENS);
      const creditsPerToken = workerPositiveDecimalString(settings.sellerCreditsPerToken, WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN);
      const creditsPerTokenWei = workerCreditDecimalToWei(creditsPerToken, WORKER_DEFAULT_SELLER_CREDITS_PER_TOKEN);
      const estimatedCreditsPerRequestWei = workerEstimatedCreditsPerRequestFromTokenRate(creditsPerTokenWei, targetOutputTokens);
      const estimatedCreditsPerRequest = workerCreditWeiToDecimal(estimatedCreditsPerRequestWei);
      const pricing = {
        pricing_type: "approx_per_token_v0",
        credits_per_token: creditsPerToken,
        credits_per_token_wei: creditsPerTokenWei,
        target_output_tokens: targetOutputTokens,
        estimated_credits_per_request: estimatedCreditsPerRequest,
        estimated_credits_per_request_wei: estimatedCreditsPerRequestWei,
        credits_per_request: estimatedCreditsPerRequest,
        credits_per_request_wei: estimatedCreditsPerRequestWei,
        unit: "compute_credit"
      };
      const execution = {
        mode: settings.executionMode,
        max_concurrency: settings.maxConcurrency
      };
      const availability = {
        accept_paid_jobs: settings.sellerEnabled,
        availability_mode: settings.sellerAvailabilityMode,
        only_when_idle: settings.sellerOnlyWhenIdle,
        idle_source: settings.sellerOnlyWhenIdle ? "windows_user_activity_v1" : "local_ai_capacity_v1",
        ai_idle_required: settings.sellerAvailabilityMode === WORKER_SELLER_AVAILABILITY_AI_IDLE
      };
      const worker = {
        node_id: settings.nodeId,
        endpoint: settings.endpoint,
        model: models[0],
        models,
        credits_per_token: creditsPerToken,
        credits_per_token_wei: creditsPerTokenWei,
        estimated_credits_per_request: estimatedCreditsPerRequest,
        estimated_credits_per_request_wei: estimatedCreditsPerRequestWei,
        credits_per_request: estimatedCreditsPerRequest,
        credits_per_request_wei: estimatedCreditsPerRequestWei,
        target_output_tokens: targetOutputTokens,
        max_concurrency: settings.maxConcurrency,
        queue_depth: 0,
        active_requests: 0,
        pricing,
        execution,
        availability,
        capabilities: {
          capabilities: [settings.capability],
          pricing,
          execution,
          availability,
          target_output_tokens: targetOutputTokens,
          phase12_worker_seller_offer_ui: true
        }
      };
      return {
        hub_url: settings.registrationHubUrl,
        worker
      };
    }

    async function workerGetJson(path) {
      const response = await fetch(path, {
        method: "GET",
        headers: {"Accept": "application/json"},
        cache: "no-store"
      });
      let data = {};
      try {
        data = await response.json();
      } catch {
        data = {};
      }
      if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    async function workerPostJson(path, payload) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json", "Accept": "application/json"},
        body: JSON.stringify(payload)
      });
      let data = {};
      try {
        data = await response.json();
      } catch {
        data = {};
      }
      if (!response.ok || data.error || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    async function workerRefreshFaucetRuntimeStatus() {
      if (workerFaucetRuntimeCheckInFlight) return;
      workerFaucetRuntimeCheckInFlight = true;
      renderWorkerBridgeReadiness();
      try {
        workerFaucetRuntimeStatus = await workerGetJson("/api/xlag/dev/faucet");
        workerFaucetRuntimeEndpointReachable = true;
        if (workerFaucetLastError && workerFaucetLastError.startsWith("Faucet readiness check failed:")) {
          workerFaucetLastError = "";
        }
      } catch (error) {
        workerFaucetRuntimeStatus = null;
        workerFaucetRuntimeEndpointReachable = false;
        if (!workerFaucetLastError) {
          workerFaucetLastError = `Faucet readiness check failed: ${error.message || error}`;
        }
      } finally {
        workerFaucetRuntimeCheckInFlight = false;
        renderWorkerBridgeReadiness();
      }
    }

    async function registerWorkerOffer() {
      let payload;
      try {
        payload = buildWorkerOfferRegistrationPayload();
      } catch (error) {
        workerSetRegistrationStatus(error.message || String(error), "error");
        return;
      }
      saveWorkerSettings();
      if (workerRegisterOffer) workerRegisterOffer.disabled = true;
      workerSetRegistrationStatus(`Registering ${payload.worker.node_id} with ${payload.hub_url}...`);
      try {
        const data = await workerPostJson("/api/applications/worker/register-offer", payload);
        const offerId = data.offer?.offer_id || data.worker?.offer?.offer_id || data.registration?.worker?.offer?.offer_id || "offer registered";
        workerSetRegistrationStatus(`Registered worker seller offer ${offerId}.`, "ok");
        try {
          localStorage.setItem(
            workerSettingsStorageKey,
            JSON.stringify({...readWorkerFormSettings(), lastRegistration: data})
          );
        } catch {}
      } catch (error) {
        workerSetRegistrationStatus(`Worker offer registration failed: ${error.message || error}`, "error");
      } finally {
        if (workerRegisterOffer) workerRegisterOffer.disabled = false;
      }
    }

    async function testSelectedWorkerHub() {
      const hubUrl = workerSelectedHubUrl();
      if (!hubUrl) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Choose a sell-capable hub before testing.";
        return;
      }
      if (workerTestHubs) workerTestHubs.disabled = true;
      if (workerSaveStatus) workerSaveStatus.textContent = `Testing ${hubUrl}...`;
      try {
        const data = await workerPostJson("/api/applications/worker/hub-health", {hub_url: hubUrl});
        const reachable = Boolean(data.status?.reachable ?? data.reachable);
        if (workerSaveStatus) {
          workerSaveStatus.textContent = reachable
            ? `Hub reachable: ${hubUrl}`
            : `Hub test returned no reachable status for ${hubUrl}.`;
        }
      } catch (error) {
        if (workerSaveStatus) workerSaveStatus.textContent = `Hub test failed: ${error.message || error}`;
      } finally {
        if (workerTestHubs) workerTestHubs.disabled = false;
      }
    }

    function bindWorkerAutosaveSetting(element, eventName = "change", changedFields = null) {
      if (!element) return;
      const datasetKey = eventName === "input" ? "workerAutosaveInputBound" : "workerAutosaveChangeBound";
      if (element.dataset[datasetKey]) return;
      element.dataset[datasetKey] = "true";
      element.addEventListener(eventName, () => {
        const fields = workerSettingsChangedFields(changedFields);
        let remoteEnabledSerial = 0;
        if (fields?.includes("remoteEnabled")) {
          workerRemoteEnabledLastLocalEditAt = Date.now();
          workerRemoteEnabledSaveSerial += 1;
          remoteEnabledSerial = workerRemoteEnabledSaveSerial;
        }
        saveWorkerSettings({changedFields: fields, remoteEnabledSerial});
        if (fields?.some((field) => ["sellerEnabled", "rentalEnabled", "sellerAvailabilityMode", "sellerOnlyWhenIdle", "rentalOnlyWhenIdle"].includes(field))) {
          workerRefreshSellerAvailabilityControls();
          workerSyncRuntime("sync", {includeSettings: true});
        }
      });
    }

    function initWorkerApp() {
      loadWorkerSettings();
      renderWorkerHubs();
      loadWorkerBridgeState();
      renderWorkerBridgeReadiness();
      const workerNetworkSessionLoadPromise = workerLoadNetworkSessionFromBackend();
      workerLoadRuntimeStatus();
      workerStartRuntimeSync();
      workerRefreshHubCreditBridgeConfig();
      workerRefreshFaucetRuntimeStatus();
      workerNetworkTabs.forEach((tab) => {
        if (tab.dataset.workerBound) return;
        tab.dataset.workerBound = "true";
        tab.addEventListener("click", (event) => {
          const network = tab.getAttribute("data-worker-network") || WORKER_NETWORK_NONE;
          if (workerNetworkKey(network) === WORKER_NETWORK_NONE) {
            workerDisconnectSelectedNetworkAndWallet(event);
          } else {
            workerSelectNetworkAndConnectWallet(event, network);
          }
        });
      });
      if (workerNetworkRetry && !workerNetworkRetry.dataset.workerBound) {
        workerNetworkRetry.dataset.workerBound = "true";
        workerNetworkRetry.addEventListener("click", (event) => {
          const selected = workerNetworkSession.selected_network || WORKER_NETWORK_NONE;
          if (workerNetworkKey(selected) === WORKER_NETWORK_NONE) {
            workerDisconnectSelectedNetworkAndWallet(event);
          } else {
            workerSelectNetworkAndConnectWallet(event, selected);
          }
        });
      }
      if (workerNetworkDisconnect && !workerNetworkDisconnect.dataset.workerBound) {
        workerNetworkDisconnect.dataset.workerBound = "true";
        workerNetworkDisconnect.addEventListener("click", (event) => {
          workerDisconnectSelectedNetworkAndWallet(event);
        });
      }
      if (workerNetworkRing && !workerNetworkRing.dataset.workerBound) {
        workerNetworkRing.dataset.workerBound = "true";
        workerNetworkRing.addEventListener("change", () => {
          const selected = workerNetworkKey(workerNetworkSession.selected_network);
          workerNetworkSession = {
            ...workerNetworkSession,
            requested_ring: String(workerNetworkRing.value || WORKER_DEFAULT_RING),
            signed_connection: {}
          };
          renderWorkerNetworkSurface();
          if (selected !== WORKER_NETWORK_NONE) {
            workerSelectNetwork(selected, {requestedRing: workerNetworkRing.value});
          }
        });
      }
      if (workerNetworkWorkNow && !workerNetworkWorkNow.dataset.workerBound) {
        workerNetworkWorkNow.dataset.workerBound = "true";
        workerNetworkWorkNow.addEventListener("click", openWorkerWorkNowDialog);
      }
      if (workerWorkNow15 && !workerWorkNow15.dataset.workerBound) {
        workerWorkNow15.dataset.workerBound = "true";
        workerWorkNow15.addEventListener("click", () => workerWorkNowForDuration(15 * 60));
      }
      if (workerWorkNow30 && !workerWorkNow30.dataset.workerBound) {
        workerWorkNow30.dataset.workerBound = "true";
        workerWorkNow30.addEventListener("click", () => workerWorkNowForDuration(30 * 60));
      }
      if (workerWorkNow60 && !workerWorkNow60.dataset.workerBound) {
        workerWorkNow60.dataset.workerBound = "true";
        workerWorkNow60.addEventListener("click", () => workerWorkNowForDuration(60 * 60));
      }
      if (workerWorkNowCustomApply && !workerWorkNowCustomApply.dataset.workerBound) {
        workerWorkNowCustomApply.dataset.workerBound = "true";
        workerWorkNowCustomApply.addEventListener("click", () => {
          const minutes = Number.parseInt(String(workerWorkNowCustomMinutes?.value || "0"), 10);
          workerWorkNowForDuration(minutes * 60);
        });
      }
      if (workerWorkNowFinish && !workerWorkNowFinish.dataset.workerBound) {
        workerWorkNowFinish.dataset.workerBound = "true";
        workerWorkNowFinish.addEventListener("click", workerFinishWorkNowOverride);
      }
      if (workerWorkNowCancel && !workerWorkNowCancel.dataset.workerBound) {
        workerWorkNowCancel.dataset.workerBound = "true";
        workerWorkNowCancel.addEventListener("click", workerCloseWorkNowDialog);
      }
      if (workerAddHubForm && !workerAddHubForm.dataset.workerBound) {
        workerAddHubForm.dataset.workerBound = "true";
        workerAddHubForm.addEventListener("submit", (event) => {
          event.preventDefault();
          const name = String(workerHubName?.value || "").trim() || "Hub";
          const url = String(workerHubUrl?.value || "").trim();
          const role = String(workerHubRole?.value || "use-provide");
          if (!url) {
            if (workerSaveStatus) workerSaveStatus.textContent = "Enter a hub URL before adding it.";
            return;
          }
          workerHubs.push({name, url, role});
          renderWorkerHubs();
          workerRenderRegistrationHubOptions(url);
          if (workerSaveStatus) workerSaveStatus.textContent = `${name} added. Save settings to keep it.`;
        });
      }
      if (workerHubList && !workerHubList.dataset.workerBound) {
        workerHubList.dataset.workerBound = "true";
        workerHubList.addEventListener("click", (event) => {
          const removeButton = event.target instanceof Element
            ? event.target.closest("[data-worker-remove-hub]")
            : null;
          if (!removeButton) return;
          const index = Number(removeButton.getAttribute("data-worker-remove-hub"));
          if (!Number.isInteger(index) || index < 0 || index >= workerHubs.length) return;
          const [removed] = workerHubs.splice(index, 1);
          renderWorkerHubs();
          if (workerSaveStatus) workerSaveStatus.textContent = `${removed?.name || "Hub"} removed. Save settings to keep the change.`;
        });
      }
      if (workerRegisterOffer && !workerRegisterOffer.dataset.workerBound) {
        workerRegisterOffer.dataset.workerBound = "true";
        workerRegisterOffer.addEventListener("click", registerWorkerOffer);
      }
      if (workerSaveSettings && !workerSaveSettings.dataset.workerBound) {
        workerSaveSettings.dataset.workerBound = "true";
        workerSaveSettings.addEventListener("click", saveWorkerSettings);
      }
      bindWorkerAutosaveSetting(workerRemoteEnabled, "change", ["remoteEnabled"]);
      bindWorkerAutosaveSetting(workerRemoteMode, "change", ["remoteMode"]);
      bindWorkerAutosaveSetting(workerRemoteCreditsPerToken, "change", ["remoteCreditsPerToken"]);
      bindWorkerAutosaveSetting(workerRemoteCreditsPerToken, "input", ["remoteCreditsPerToken"]);
      bindWorkerAutosaveSetting(workerRemoteMaxOutputTokens, "change", ["remoteMaxOutputTokens"]);
      bindWorkerAutosaveSetting(workerRemoteDailyLimit, "change", ["remoteDailyLimit"]);
      bindWorkerAutosaveSetting(workerRemoteAskBeforeSpend, "change", ["remoteAskBeforeSpend"]);
      bindWorkerAutosaveSetting(workerRemoteOnlyWhenBusy, "change", ["remoteOnlyWhenBusy"]);
      bindWorkerAutosaveSetting(workerOfferTargetTokens, "change", ["sellerTargetTokens"]);
      bindWorkerAutosaveSetting(workerOfferTargetTokens, "input", ["sellerTargetTokens"]);
      bindWorkerAutosaveSetting(workerOfferCreditsPerToken, "change", ["sellerCreditsPerToken"]);
      bindWorkerAutosaveSetting(workerOfferCreditsPerToken, "input", ["sellerCreditsPerToken"]);
      bindWorkerAutosaveSetting(workerRentalEnabled, "change", ["sellerEnabled", "rentalEnabled"]);
      (workerSellerAvailabilityModes || []).forEach((input) => {
        bindWorkerAutosaveSetting(input, "change", ["sellerAvailabilityMode", "sellerOnlyWhenIdle", "rentalOnlyWhenIdle"]);
      });
      workerRefreshSellerAvailabilityControls();
      if (workerPauseRentals && !workerPauseRentals.dataset.workerBound) {
        workerPauseRentals.dataset.workerBound = "true";
        workerPauseRentals.addEventListener("click", () => {
          if (workerRentalEnabled) workerRentalEnabled.checked = false;
          saveWorkerSettings({changedFields: ["sellerEnabled", "rentalEnabled"]});
          workerSyncRuntime("sync", {includeSettings: true});
          if (workerSaveStatus) workerSaveStatus.textContent = "Selling paused locally. The worker will drain active work and stop accepting new jobs.";
        });
      }
      if (workerTestHubs && !workerTestHubs.dataset.workerBound) {
        workerTestHubs.dataset.workerBound = "true";
        workerTestHubs.addEventListener("click", testSelectedWorkerHub);
      }
      if (workerConnectWallet && !workerConnectWallet.dataset.workerBound) {
        workerConnectWallet.dataset.workerBound = "true";
        workerConnectWallet.addEventListener("click", connectWorkerPrimaryWallet, true);
      }
      if (workerDisconnectWallet && !workerDisconnectWallet.dataset.workerBound) {
        workerDisconnectWallet.dataset.workerBound = "true";
        workerDisconnectWallet.addEventListener("click", disconnectWorkerPrimaryWallet, true);
      }
      workerBindWalletProviderEvents();
      Promise.resolve(workerNetworkSessionLoadPromise).finally(() => workerHydrateConnectedWalletFromProvider("page-load"));
      if (workerRefreshBridgeReadiness && !workerRefreshBridgeReadiness.dataset.workerBound) {
        workerRefreshBridgeReadiness.dataset.workerBound = "true";
        workerRefreshBridgeReadiness.addEventListener("click", async () => {
          loadWorkerBridgeState();
          if (workerBridgeState.wallet.connected && workerBridgeState.wallet.address) {
            await workerLoadMultisessionKeysForWallet(workerBridgeState.wallet.address, "manual-refresh");
            await checkWorkerHubCreditBalance();
          }
          renderWorkerBridgeReadiness();
          workerRefreshFaucetRuntimeStatus();
          await workerRefreshHubCreditBridgeConfig({force: true});
          if (workerSaveStatus) workerSaveStatus.textContent = "Wallet, key, faucet, bridge deployment, and bridge-account balances refreshed from local app storage and runtime status.";
        });
      }
      if (workerFaucetAmount) {
        workerFaucetAmount.value = WORKER_FAUCET_AMOUNT_CREDITS;
        workerFaucetAmount.disabled = true;
      }
      if (workerRequestFaucet && !workerRequestFaucet.dataset.workerBound) {
        workerRequestFaucet.dataset.workerBound = "true";
        workerRequestFaucet.addEventListener("click", requestWorkerFaucetCredits);
      }
      if (workerHubCreditForm && !workerHubCreditForm.dataset.workerBound) {
        workerHubCreditForm.dataset.workerBound = "true";
        workerHubCreditForm.addEventListener("submit", fundWorkerHubCredit);
      }
      if (workerHubCreditAmount && !workerHubCreditAmount.dataset.workerBound) {
        workerHubCreditAmount.dataset.workerBound = "true";
        workerHubCreditAmount.addEventListener("input", () => {
          workerSaveHubCreditInputs();
          renderWorkerBridgeReadiness();
        });
      }
      if (workerCheckHubCreditBalance && !workerCheckHubCreditBalance.dataset.workerBound) {
        workerCheckHubCreditBalance.dataset.workerBound = "true";
        workerCheckHubCreditBalance.addEventListener("click", checkWorkerHubCreditBalance);
      }
      if (workerFundHubCredit && !workerFundHubCredit.dataset.workerBound) {
        workerFundHubCredit.dataset.workerBound = "true";
        workerFundHubCredit.addEventListener("click", fundWorkerHubCredit);
      }
      if (workerAddRecoveryEmailForm && !workerAddRecoveryEmailForm.dataset.workerBound) {
        workerAddRecoveryEmailForm.dataset.workerBound = "true";
        workerAddRecoveryEmailForm.addEventListener("submit", (event) => {
          event.preventDefault();
          addWorkerRecoveryMethod("email", workerRecoveryEmailInput?.value || "");
          if (workerRecoveryEmailInput) workerRecoveryEmailInput.value = "";
        });
      }
      if (workerAddRecoveryWalletForm && !workerAddRecoveryWalletForm.dataset.workerBound) {
        workerAddRecoveryWalletForm.dataset.workerBound = "true";
        workerAddRecoveryWalletForm.addEventListener("submit", (event) => {
          event.preventDefault();
          addWorkerRecoveryMethod("wallet", workerRecoveryWalletInput?.value || "");
          if (workerRecoveryWalletInput) workerRecoveryWalletInput.value = "";
        });
      }
      if (workerRecoveryEmailList && !workerRecoveryEmailList.dataset.workerBound) {
        workerRecoveryEmailList.dataset.workerBound = "true";
        workerRecoveryEmailList.addEventListener("click", (event) => {
          const button = event.target instanceof Element
            ? event.target.closest("[data-worker-remove-recovery-email]")
            : null;
          if (!button) return;
          removeWorkerRecoveryMethod("email", button.getAttribute("data-worker-remove-recovery-email") || "");
        });
      }
      if (workerRecoveryWalletList && !workerRecoveryWalletList.dataset.workerBound) {
        workerRecoveryWalletList.dataset.workerBound = "true";
        workerRecoveryWalletList.addEventListener("click", (event) => {
          const button = event.target instanceof Element
            ? event.target.closest("[data-worker-remove-recovery-wallet]")
            : null;
          if (!button) return;
          removeWorkerRecoveryMethod("wallet", button.getAttribute("data-worker-remove-recovery-wallet") || "");
        });
      }
      if (workerConfirmRecovery && !workerConfirmRecovery.dataset.workerBound) {
        workerConfirmRecovery.dataset.workerBound = "true";
        workerConfirmRecovery.addEventListener("click", confirmWorkerRecoverySetup);
      }
      if (workerRequestMultisessionKey && !workerRequestMultisessionKey.dataset.workerBound) {
        workerRequestMultisessionKey.dataset.workerBound = "true";
        workerRequestMultisessionKey.addEventListener("click", requestWorkerMultisessionKey);
      }
      if (workerRevokeMultisessionKey && !workerRevokeMultisessionKey.dataset.workerBound) {
        workerRevokeMultisessionKey.dataset.workerBound = "true";
        workerRevokeMultisessionKey.addEventListener("click", revokeWorkerMultisessionKey);
      }
    }

