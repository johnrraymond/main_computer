    const workerDefaultHubs = [
      {name: "Mainnet Hub", url: "https://mainnet-hub.greatlibrary.io", role: "use-provide", network: "mainnet"},
      {name: "Testnet Hub", url: "https://testnet-hub.greatlibrary.io", role: "use-provide", network: "testnet"},
      {name: "Local QBFT Test Hub", url: "http://127.0.0.1:8780", role: "use-provide", network: "test"},
      {name: "Local Dev Hub", url: "http://127.0.0.1:8770", role: "use-provide", network: "dev"}
    ];
    const WORKER_NETWORK_SESSION_ENDPOINT = "/api/applications/worker/network-session";
    const WORKER_NETWORK_CONNECT_ORDER_ENDPOINT = "/api/applications/worker/network-connect-order";
    const WORKER_STATUS_MESSAGE_MAX_LENGTH = 260;
    const WORKER_NETWORK_ORDER = ["mainnet", "testnet", "test", "dev"];
    const WORKER_NETWORK_NONE = "none";
    const WORKER_DEFAULT_RING = "3";
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
    let workerNetworkSignatureInFlight = false;

    const workerBridgeReadinessStorageKey = "main-computer-worker-bridge-readiness-v1";
    const WORKER_SETTINGS_POLL_INTERVAL_MS = 2500;
    const WORKER_DEV_CHAIN_ID_DECIMAL = 42424242;
    const WORKER_DEV_CHAIN_ID_HEX = "0x28757b2";
    const WORKER_FAUCET_AMOUNT_CREDITS = "1";
    const WORKER_HUB_CREDIT_DEFAULT_AMOUNT = "1";
    const WORKER_WALLET_BALANCE_TIMEOUT_MS = 8000;
    const WORKER_METAMASK_RPC_BACKOFF_TIMEOUT_MS = 75000;
    const WORKER_METAMASK_RPC_BACKOFF_POLL_MS = 3000;
    const WORKER_CREDIT_BASE_UNITS_PER_CREDIT = 1000000000000000000n;
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
      const multisessionKeys = Array.isArray(state.multisessionKeys) ? state.multisessionKeys : [];
      return {
        wallet: {...fallback.wallet},
        recoveryEmails: workerNormalizeList(state.recoveryEmails, "email"),
        recoveryWallets: workerNormalizeList(state.recoveryWallets, "wallet"),
        recoveryConfirmedAt: String(state.recoveryConfirmedAt || state.recovery_confirmed_at || ""),
        multisessionKeys: multisessionKeys
          .map((key) => ({
            id: String(key.id || ""),
            status: String(key.status || "active"),
            createdAt: String(key.createdAt || key.created_at || ""),
            revokedAt: String(key.revokedAt || key.revoked_at || ""),
            walletAddress: String(key.walletAddress || key.wallet_address || "")
          }))
          .filter((key) => key.id),
        activeMultisessionKeyId: String(state.activeMultisessionKeyId || state.active_multisession_key_id || ""),
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
        const {wallet: _liveWalletState, ...serializableState} = workerBridgeState || {};
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
      if (!activeId) return null;
      return workerBridgeState.multisessionKeys.find((key) => (
        key.id === activeId
        && key.status === "active"
        && workerMultisessionKeyMatchesWallet(key)
      )) || null;
    }

    function workerClearActiveMultisessionKeyIfWalletMismatch() {
      if (!workerBridgeState.activeMultisessionKeyId) return;
      if (!workerActiveMultisessionKey()) {
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
        workerMultisessionStatus.textContent = activeKey ? `Active ${activeKey.id}` : "No active key";
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
        workerMultisessionKeyId.textContent = activeKey?.id || "—";
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
      return {
        id: String(key.id || ""),
        status: String(key.status || "active"),
        createdAt: String(key.createdAt || key.created_at || ""),
        revokedAt: String(key.revokedAt || key.revoked_at || ""),
        walletAddress: String(key.walletAddress || key.wallet_address || result?.verification?.wallet_address || "")
      };
    }

    function workerStoreIssuedMultisessionKey(result) {
      const key = workerNormalizeIssuedMultisessionKey(result);
      if (!key.id) {
        throw new Error("Hub did not return a multi-session key id.");
      }
      workerBridgeState.multisessionKeys = workerBridgeState.multisessionKeys.filter((existing) => existing.id !== key.id);
      workerBridgeState.multisessionKeys.push(key);
      workerBridgeState.activeMultisessionKeyId = key.status === "active" ? key.id : "";
      saveWorkerBridgeState();
      return key;
    }

    function workerMergeLoadedMultisessionKeys(result, walletAddress) {
      const wallet = workerLowerAddress(walletAddress);
      const rawKeys = Array.isArray(result?.keys) ? result.keys : [];
      const loadedKeys = rawKeys
        .map((item) => workerNormalizeIssuedMultisessionKey({key: item, verification: {wallet_address: wallet}}))
        .filter((key) => key.id && key.status !== "revoked" && workerMultisessionKeyMatchesWallet(key, wallet));

      workerBridgeState.multisessionKeys = workerBridgeState.multisessionKeys.filter((existing) => {
        if (!existing.id) return false;
        return !workerMultisessionKeyMatchesWallet(existing, wallet);
      });

      loadedKeys.forEach((key) => {
        workerBridgeState.multisessionKeys.push(key);
      });

      const activeKey = loadedKeys.find((key) => key.status === "active") || null;
      workerBridgeState.activeMultisessionKeyId = activeKey ? activeKey.id : "";
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

    function revokeWorkerMultisessionKey() {
      loadWorkerBridgeState();
      const activeKey = workerActiveMultisessionKey();
      if (!activeKey) {
        if (workerSaveStatus) workerSaveStatus.textContent = "No active multi-session key to revoke.";
        renderWorkerBridgeReadiness();
        return;
      }
      activeKey.status = "revoked";
      activeKey.revokedAt = workerNowIso();
      workerBridgeState.activeMultisessionKeyId = "";
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = "Active multi-session key revoked locally. You can request a new key now.";
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

    function workerNetworkHubRegistered() {
      const signed = workerNetworkSignedConnection();
      return signed.status === "hub-registered" || signed.status === "registered" || Boolean(signed.hub_registered);
    }

    function workerNetworkWalletAddress() {
      loadWorkerBridgeState();
      return workerBridgeState.wallet?.address || "";
    }

    function workerNetworkCanSign() {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const walletAddress = workerNetworkWalletAddress();
      return (
        selected !== WORKER_NETWORK_NONE
        && workerNetworkSession.connection_status === "connected"
        && workerWalletValidAddress(walletAddress)
        && workerNetworkWalletConnectedToSelected()
        && !workerNetworkSignatureInFlight
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
      const signedForSelected = (
        signed.network === selected
        && String(signed.requested_ring || "") === String(workerNetworkSession.requested_ring || "")
        && workerWalletValidAddress(signed.wallet_address || "")
        && walletConnectedToSelected
      );
      const hubRegistered = signedForSelected && workerNetworkHubRegistered();
      const assignedRing = String(signed.assigned_ring || registeredWorker.assigned_ring || workerNetworkSession.assigned_ring || "");
      const workerId = String(signed.worker_id || registeredWorker.worker_id || registeredWorker.node_id || workerNetworkSession.worker_id || "");
      const pricingPolicy = String(signed.pricing_policy || registeredWorker.pricing_policy || registeredWorker.pricing?.pricing_policy || workerNetworkSession.pricing_policy || "");

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
      workerNetworkSetText(workerNetworkAssignedRing, hubRegistered ? workerRingLabel(assignedRing || workerNetworkSession.requested_ring) : signedForSelected ? "Pending Hub" : "—");
      workerNetworkSetText(workerNetworkSignatureStatus, hubRegistered ? "Valid; Hub registration accepted." : signedForSelected ? "Valid; Hub registration pending." : "Not signed");
      workerNetworkSetText(workerNetworkHubRegistration, hubRegistered ? "Accepted" : signedForSelected ? "Pending" : "—");
      workerNetworkSetText(workerNetworkWorkerId, hubRegistered ? workerId || "—" : "—");
      workerNetworkSetText(workerNetworkPricingPolicy, hubRegistered ? pricingPolicy || "—" : "—");
      workerNetworkSetText(workerNetworkPool, hubRegistered ? workerPoolCountText(pool) : "—");
      workerNetworkSetText(workerNetworkRuntime, hubRegistered ? "Hub registered; inactive" : signedForSelected && hubConnected && walletConnectedToSelected ? "Registration pending" : "Inactive");

      if (workerNetworkHelp) {
        if (selected === WORKER_NETWORK_NONE) {
          workerNetworkHelp.textContent = "No active worker network selected. Select Mainnet, Testnet, Test, or Dev to connect.";
        } else if (hubConnected && !walletConnectedToSelected) {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} Hub is reachable. Connect your wallet to ${workerNetworkDisplayName(selected)} before accepting jobs.`;
        } else if (hubConnected && walletConnectedToSelected && !signedForSelected) {
          workerNetworkHelp.textContent = `Wallet is connected to ${workerNetworkDisplayName(selected)}. Choose a ring and sign the connect order before accepting jobs.`;
        } else if (hubConnected && hubRegistered) {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} wallet and Hub registration are ready. Activate the worker before accepting jobs.`;
        } else if (hubConnected && signedForSelected) {
          workerNetworkHelp.textContent = `${workerNetworkDisplayName(selected)} connect order is signed, but Hub registration has not been accepted yet. Re-sign to submit it to the Hub.`;
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
            ? "Hub registered / selected"
            : signedForSelected && hubConnected && walletConnectedToSelected
              ? "Registration pending"
              : hubConnected && walletConnectedToSelected
                ? "Selected / needs signature"
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
      if (workerNetworkSignOrder) {
        workerNetworkSignOrder.disabled = !workerNetworkCanSign();
        workerNetworkSignOrder.textContent = workerNetworkSignatureInFlight ? "Signing…" : "Sign Connect Order";
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
              ? `${workerNetworkDisplayName(selected)} Hub is reachable. Connect your wallet to finish the worker connection.`
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

    function workerBuildConnectOrderMessage({issuedAt = workerNowIso(), expiresAt = ""} = {}) {
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const profile = workerNetworkSession.profile || workerNetworkProfile(selected);
      const walletAddress = workerLowerAddress(workerNetworkWalletAddress());
      const expires = expiresAt || new Date(Date.now() + 10 * 60 * 1000).toISOString();
      return JSON.stringify({
        kind: "main_computer_worker_connect_order",
        purpose: "connect_worker_to_hub",
        version: "main-computer-worker-connect-order-v1",
        network: selected,
        hub_url: profile?.hub_url || workerNetworkSession.connected_hub_url || "",
        chain_id: String(profile?.chain_id || ""),
        requested_ring: String(workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
        wallet_address: walletAddress,
        credit_wallet: walletAddress,
        worker_node_id: workerElementValue(workerNodeId, "local-worker-001"),
        issued_at: issuedAt,
        expires_at: expires
      });
    }

    function buildWorkerNetworkRegistrationPayload({message = "", signature = "", walletAddress = ""} = {}) {
      const offerPayload = buildWorkerOfferRegistrationPayload();
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      const profile = workerNetworkSession.profile || workerNetworkProfile(selected);
      const hubUrl = profile?.hub_url || workerNetworkSession.connected_hub_url || offerPayload.hub_url;
      const normalizedWallet = workerLowerAddress(walletAddress || workerNetworkWalletAddress());
      return {
        hub_url: hubUrl,
        network: selected,
        requested_ring: String(workerNetworkSession.requested_ring || WORKER_DEFAULT_RING),
        wallet_address: normalizedWallet,
        message,
        signature,
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
    }

    async function signWorkerNetworkConnectOrder(event) {
      event?.preventDefault?.();
      if (!workerNetworkCanSign()) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Select a connected network and connect a wallet before signing the worker connection order.";
        renderWorkerNetworkSurface();
        return;
      }
      workerNetworkSignatureInFlight = true;
      renderWorkerNetworkSurface();
      try {
        const context = await workerGetWalletProviderContext();
        const signer = await context.browserProvider.getSigner();
        const walletAddress = await signer.getAddress();
        const message = workerBuildConnectOrderMessage();
        const signature = await signer.signMessage(message);
        const data = await workerPostJson(
          WORKER_NETWORK_CONNECT_ORDER_ENDPOINT,
          buildWorkerNetworkRegistrationPayload({message, signature, walletAddress})
        );
        workerApplyNetworkPayload(data);
        workerSetSaveStatus(`Signed ${workerRingLabel(workerNetworkSession.requested_ring)} worker connect order and registered worker with the ${workerNetworkDisplayName(workerNetworkSession.selected_network)} Hub.`);
      } catch (error) {
        workerSetSaveStatus("", {walletError: error, prefix: "Worker connect order signing failed: "});
      } finally {
        workerNetworkSignatureInFlight = false;
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

    function workerPositiveDecimalString(value, fallback = "1") {
      const raw = String(value ?? "").trim();
      const parsed = Number.parseFloat(raw);
      if (!raw || !Number.isFinite(parsed) || parsed <= 0) {
        return String(fallback);
      }
      return raw;
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
      const raw = workerElementValue(workerOfferModels, "mock-ai-model-phase9");
      const models = raw.split(",").map((item) => item.trim()).filter(Boolean);
      return models.length ? [...new Set(models)] : ["mock-ai-model-phase9"];
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
      const hubUrl = String(workerNetworkHubUrl() || selectedUrl || workerElementValue(workerRegistrationHub)).trim();
      if (workerRegistrationHub && "value" in workerRegistrationHub) {
        workerRegistrationHub.value = hubUrl;
      }
      if (!workerRegistrationHubStatus) return;
      const selected = workerNetworkKey(workerNetworkSession.selected_network);
      if (!hubUrl || selected === WORKER_NETWORK_NONE) {
        workerRegistrationHubStatus.textContent = "Select a worker connection below.";
        return;
      }
      const signed = workerNetworkSignedConnection();
      const signedForSelected = (
        signed.network === selected
        && String(signed.requested_ring || "") === String(workerNetworkSession.requested_ring || "")
        && workerWalletValidAddress(signed.wallet_address || "")
      );
      const registrationState = workerNetworkHubRegistered()
        ? "Accepted"
        : signedForSelected
          ? "Signed; hub registration pending"
          : "Selected; not signed yet";
      workerRegistrationHubStatus.textContent = `${registrationState}: ${workerHubDisplayLabel(hubUrl)}`;
    }

    function workerSetRegistrationStatus(message, kind = "") {
      if (workerSaveStatus) {
        workerSaveStatus.textContent = message;
      }
      if (workerRegistrationSummary) {
        workerRegistrationSummary.textContent = message;
      }
      const registrationCard = workerRegistrationSummary?.closest(".worker-registration-card");
      if (registrationCard) {
        registrationCard.classList.toggle("is-ok", kind === "ok");
        registrationCard.classList.toggle("is-error", kind === "error");
      }
      if (workerRegistrationStatusPill) {
        workerRegistrationStatusPill.textContent = kind === "ok"
          ? "Offer: registered"
          : kind === "error"
            ? "Offer: registration failed"
            : "Offer: local only";
      }
    }

    function workerUpdateRegistrationResult(result, hubUrl) {
      const worker = result?.worker || result?.registration?.worker || {};
      const offer = worker.offer || {};
      const models = Array.isArray(worker.models) ? worker.models : [];
      if (workerRegisteredHub) workerRegisteredHub.textContent = hubUrl || "—";
      if (workerRegisteredOfferId) workerRegisteredOfferId.textContent = offer.offer_id || "—";
      if (workerRegisteredPrice) {
        const credits = offer.credits_per_request || worker.credits_per_request || "";
        workerRegisteredPrice.textContent = credits ? `${credits} compute credits` : "—";
      }
      if (workerRegisteredModel) workerRegisteredModel.textContent = models[0] || worker.model || "—";
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
        remoteEnabled: workerSavedBoolean(workerRemoteEnabled?.checked, false),
        remoteMode: workerElementValue(workerRemoteMode, "ask-when-busy"),
        remoteCreditsPerToken: workerPositiveDecimalString(workerElementValue(workerRemoteCreditsPerToken, "0.001"), "0.001"),
        remoteMaxOutputTokens: workerPositiveInteger(workerElementValue(workerRemoteMaxOutputTokens, "1024"), 1024),
        remoteDailyLimit: workerPositiveInteger(workerElementValue(workerRemoteDailyLimit, "100000"), 100000),
        remoteAskBeforeSpend: Boolean(workerRemoteAskBeforeSpend?.checked),
        remoteOnlyWhenBusy: Boolean(workerRemoteOnlyWhenBusy?.checked),
        sellerEnabled: Boolean(workerRentalEnabled?.checked),
        rentalEnabled: Boolean(workerRentalEnabled?.checked),
        sellerOnlyWhenIdle: Boolean(workerSellerOnlyWhenIdle?.checked),
        rentalOnlyWhenIdle: Boolean(workerSellerOnlyWhenIdle?.checked),
        lockAiModel: Boolean(workerLockAiModel?.checked),
        registrationHubUrl: workerSelectedHubUrl(),
        nodeId: workerElementValue(workerNodeId, "local-worker-001"),
        endpoint: workerElementValue(workerEndpoint, "http://127.0.0.1:8771"),
        models: models.join(","),
        capability: workerElementValue(workerOfferCapability, "chat.completions"),
        creditsPerRequest: workerPositiveInteger(workerElementValue(workerOfferPrice, "5500123"), 5500123),
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
      if (workerSellerOnlyWhenIdle) {
        if (typeof parsed.sellerOnlyWhenIdle === "boolean") {
          workerSellerOnlyWhenIdle.checked = parsed.sellerOnlyWhenIdle;
        } else if (typeof parsed.rentalOnlyWhenIdle === "boolean") {
          workerSellerOnlyWhenIdle.checked = parsed.rentalOnlyWhenIdle;
        }
      }
      if (workerLockAiModel && typeof parsed.lockAiModel === "boolean") {
        workerLockAiModel.checked = parsed.lockAiModel;
      }
      assignWorkerValue(workerNodeId, parsed.nodeId);
      assignWorkerValue(workerEndpoint, parsed.endpoint);
      assignWorkerValue(workerOfferModels, parsed.models);
      assignWorkerValue(workerOfferCapability, parsed.capability);
      assignWorkerValue(workerOfferPrice, parsed.creditsPerRequest);
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
      const pricing = {
        pricing_type: "fixed_per_call_v0",
        credits_per_request: settings.creditsPerRequest,
        unit: "compute_credit"
      };
      const execution = {
        mode: settings.executionMode,
        max_concurrency: settings.maxConcurrency
      };
      const availability = {
        accept_paid_jobs: settings.sellerEnabled,
        only_when_idle: settings.sellerOnlyWhenIdle,
        idle_source: "windows_user_activity_v1"
      };
      const worker = {
        node_id: settings.nodeId,
        endpoint: settings.endpoint,
        model: models[0],
        models,
        credits_per_request: settings.creditsPerRequest,
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
        workerUpdateRegistrationResult(data, payload.hub_url);
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
      });
    }

    function initWorkerApp() {
      loadWorkerSettings();
      renderWorkerHubs();
      loadWorkerBridgeState();
      renderWorkerBridgeReadiness();
      const workerNetworkSessionLoadPromise = workerLoadNetworkSessionFromBackend();
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
      if (workerNetworkSignOrder && !workerNetworkSignOrder.dataset.workerBound) {
        workerNetworkSignOrder.dataset.workerBound = "true";
        workerNetworkSignOrder.addEventListener("click", signWorkerNetworkConnectOrder);
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
      if (workerRegistrationHub && !workerRegistrationHub.dataset.workerBound) {
        workerRegistrationHub.dataset.workerBound = "true";
        workerRegistrationHub.addEventListener("change", async () => {
          saveWorkerSettings();
          if (workerBridgeState.wallet.connected && workerBridgeState.wallet.address) {
            await workerLoadMultisessionKeysForWallet(workerBridgeState.wallet.address, "hub-change");
          }
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
      bindWorkerAutosaveSetting(workerRentalEnabled, "change", ["sellerEnabled", "rentalEnabled"]);
      bindWorkerAutosaveSetting(workerSellerOnlyWhenIdle, "change", ["sellerOnlyWhenIdle", "rentalOnlyWhenIdle"]);
      if (workerPauseRentals && !workerPauseRentals.dataset.workerBound) {
        workerPauseRentals.dataset.workerBound = "true";
        workerPauseRentals.addEventListener("click", () => {
          if (workerRentalEnabled) workerRentalEnabled.checked = false;
          saveWorkerSettings({changedFields: ["sellerEnabled", "rentalEnabled"]});
          if (workerSaveStatus) workerSaveStatus.textContent = "Selling paused locally. Paid jobs are being saved as off.";
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

