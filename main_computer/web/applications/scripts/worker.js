    const workerDefaultHubs = [
      {name: "Local Hub", url: "http://127.0.0.1:8770", role: "use-provide"},
      {name: "Friend Hub", url: "https://friend-hub.local", role: "use-only"}
    ];
    let workerSettingsLoaded = false;
    let workerHubs = [...workerDefaultHubs];

    const workerBridgeReadinessStorageKey = "main-computer-worker-bridge-readiness-v1";
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
      if (workerSaveStatus && message) workerSaveStatus.textContent = message;
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
          // from runtime/deployments/current.json through the local viewport.
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
          workerSaveStatus.textContent = `Wallet is on ${detail.chainId || "unknown chain"}; connect to ${WORKER_DEV_CHAIN_ID_HEX} before requesting keys.`;
        } else if (entry.type === "provider.hydrate.failed") {
          workerSaveStatus.textContent = `Wallet hydration failed: ${detail.message || "unknown error"}`;
        } else if (entry.type === "connect.ethers.requestAccounts.start") {
          workerSaveStatus.textContent = "Opening browser wallet account request...";
        } else if (entry.type === "connect.ethers.requestAccounts.resolved") {
          workerSaveStatus.textContent = "Wallet account request accepted; verifying signer and chain with ethers.";
        } else if (entry.type === "connect.wallet.addChain.start") {
          workerSaveStatus.textContent = `Requesting MetaMask network update to ${WORKER_DEV_CHAIN_RPC_URL}.`;
        } else if (entry.type === "connect.wallet.addChain.done") {
          workerSaveStatus.textContent = "MetaMask network update accepted; switching to the Main Computer dev chain.";
        } else if (entry.type === "connect.wallet.rpcProof.done") {
          workerSaveStatus.textContent = `MetaMask RPC ready on ${detail.chainId || WORKER_DEV_CHAIN_ID_HEX}.`;
        } else if (entry.type === "connect.ethers.switchChain.start") {
          workerSaveStatus.textContent = `Switching wallet to ${WORKER_DEV_CHAIN_ID_HEX}.`;
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
      return detail
        ? `MetaMask accepted the Main Computer dev-chain RPC update, but its internal RPC backoff is still clearing. Waiting before retrying provider access. ${detail}`
        : "MetaMask accepted the Main Computer dev-chain RPC update, but its internal RPC backoff is still clearing. Waiting before retrying provider access.";
    }

    function workerWalletRpcRepairMessage(reason = "") {
      const detail = String(reason || "").trim();
      return detail
        ? `MetaMask is selected on the Main Computer dev chain, but its RPC connection is stale/loading. Requesting MetaMask to update this network to ${WORKER_DEV_CHAIN_RPC_URL}. ${detail}`
        : `MetaMask is selected on the Main Computer dev chain, but its RPC connection is stale/loading. Requesting MetaMask to update this network to ${WORKER_DEV_CHAIN_RPC_URL}.`;
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
      if (!metadata || metadata.chainId !== WORKER_DEV_CHAIN_ID_HEX) return false;
      if (metadata.providerConnected === false) return true;
      if (metadata.networkVersion === "loading") return true;
      return false;
    }

    async function workerProveInjectedProviderRpc(browserProvider) {
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

      if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        return {
          ok: false,
          chainId,
          wrongChain: true,
          reason: `Wallet is on ${chainId || "unknown chain"}; expected ${WORKER_DEV_CHAIN_ID_HEX}.`
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
          reason: `MetaMask cannot read the Main Computer dev chain through its current RPC: ${workerWalletErrorMessage(error)}`,
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

      const requestDevChainUpdate = async (params, repairMode) => {
        workerWalletRecordEvent("connect.wallet.addChain.start", {
          reason,
          repairMode,
          chainId: WORKER_DEV_CHAIN_ID_HEX,
          rpcUrl: WORKER_DEV_CHAIN_RPC_URL,
          currencySymbol: params?.nativeCurrency?.symbol || ""
        });
        await workerBrowserProviderSend(
          browserProvider,
          "wallet_addEthereumChain",
          [params],
          "wallet dev-chain update",
          120000
        );
        workerWalletRecordEvent("connect.wallet.addChain.done", {
          reason,
          repairMode,
          chainId: WORKER_DEV_CHAIN_ID_HEX,
          rpcUrl: WORKER_DEV_CHAIN_RPC_URL,
          currencySymbol: params?.nativeCurrency?.symbol || ""
        });
      };

      try {
        await requestDevChainUpdate(workerDevWalletChainParams(), "canonical-mcxlag");
      } catch (error) {
        if (!workerWalletIsNativeCurrencySymbolMismatch(error)) {
          throw error;
        }
        workerWalletRecordEvent("connect.wallet.addChain.symbolMismatchFallback", {
          reason,
          chainId: WORKER_DEV_CHAIN_ID_HEX,
          canonicalSymbol: WORKER_DEV_CHAIN_CURRENCY_SYMBOL,
          repairOnlySymbol: WORKER_DEV_CHAIN_LEGACY_REPAIR_CURRENCY_SYMBOL,
          message: workerWalletErrorMessage(error)
        });
        await requestDevChainUpdate(workerDevWalletLegacyRpcRepairChainParams(), "legacy-symbol-rpc-repair");
      }

      workerWalletRecordEvent("connect.ethers.switchChain.start", {
        from: "wallet-provider",
        to: WORKER_DEV_CHAIN_ID_HEX,
        reason
      });
      await workerBrowserProviderSend(
        browserProvider,
        "wallet_switchEthereumChain",
        [{chainId: WORKER_DEV_CHAIN_ID_HEX}],
        "wallet dev-chain switch",
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
      const reason = String(opts.reason || "ensure-dev-chain");
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

      if (metadata.chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        needsUpdate = true;
        updateReason = metadata.chainId
          ? `wallet is on ${metadata.chainId}; expected ${WORKER_DEV_CHAIN_ID_HEX}`
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
      if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        throw new Error(`Wrong chain after wallet network reconciliation. Expected ${WORKER_DEV_CHAIN_ID_HEX}, got ${chainId || "unknown"}.`);
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
        } else if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
          workerSetPrimaryWalletState({connected: false});
          workerWalletLastAction = `Wallet is on ${chainId || "unknown chain"}; expected ${WORKER_DEV_CHAIN_ID_HEX}.`;
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

          if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
            workerSetPrimaryWalletState({connected: false});
            workerWalletHookState = "idle";
            workerWalletLastAction = `Wallet is on ${chainId || "unknown chain"}; expected ${WORKER_DEV_CHAIN_ID_HEX}.`;
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

        workerSetWalletOperationState("stabilizing", "Wallet accepted. Verifying signer and dev chain with ethers. Checking MetaMask RPC before enabling funding.");

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
        if (chainId !== WORKER_DEV_CHAIN_ID_HEX) {
          throw new Error(`Wrong chain after connect. Expected ${WORKER_DEV_CHAIN_ID_HEX}, got ${chainId || "unknown"}.`);
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

    function workerSelectedHubUrl() {
      const selected = workerElementValue(workerRegistrationHub);
      if (selected) return selected;
      return workerHubs.find(workerHubCanSell)?.url || "";
    }

    function workerRenderRegistrationHubOptions(selectedUrl = "") {
      if (!workerRegistrationHub) return;
      const previous = selectedUrl || workerRegistrationHub.value || "";
      const sellableHubs = workerHubs.filter((hub) => workerHubCanSell(hub) && String(hub.url || "").trim());
      workerRegistrationHub.innerHTML = "";
      if (!sellableHubs.length) {
        const option = document.createElement("option");
        option.value = "";
        option.textContent = "Add a sell-capable hub first";
        workerRegistrationHub.append(option);
        workerRegistrationHub.disabled = true;
        return;
      }
      workerRegistrationHub.disabled = false;
      sellableHubs.forEach((hub) => {
        const option = document.createElement("option");
        option.value = String(hub.url || "").trim();
        option.textContent = `${hub.name || "Hub"} - ${option.value}`;
        workerRegistrationHub.append(option);
      });
      const match = sellableHubs.find((hub) => String(hub.url || "").trim() === previous);
      workerRegistrationHub.value = match ? previous : String(sellableHubs[0].url || "").trim();
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

    function readWorkerFormSettings() {
      const models = workerOfferModelsArray();
      return {
        remoteEnabled: workerSavedBoolean(workerRemoteEnabled?.checked, false),
        remoteMode: workerElementValue(workerRemoteMode, "ask-when-busy"),
        remoteCreditsPerToken: workerPositiveDecimalString(workerElementValue(workerRemoteCreditsPerToken, "0.001"), "0.001"),
        remoteMaxOutputTokens: workerPositiveInteger(workerElementValue(workerRemoteMaxOutputTokens, "1024"), 1024),
        remoteDailyLimit: workerPositiveInteger(workerElementValue(workerRemoteDailyLimit, "100000"), 100000),
        remoteAskBeforeSpend: Boolean(workerRemoteAskBeforeSpend?.checked),
        remoteOnlyWhenBusy: Boolean(workerRemoteOnlyWhenBusy?.checked),
        sellerEnabled: Boolean(workerRentalEnabled?.checked),
        rentalEnabled: Boolean(workerRentalEnabled?.checked),
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

    function saveWorkerSettings() {
      const settings = readWorkerFormSettings();
      workerPostJson("/api/applications/worker/settings", {settings})
        .then(() => {
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

    function applyWorkerSettings(parsed) {
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
      if (workerRemoteEnabled && Object.prototype.hasOwnProperty.call(parsed, "remoteEnabled")) {
        workerRemoteEnabled.checked = workerSavedBoolean(parsed.remoteEnabled, false);
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
      try {
        const data = await workerGetJson("/api/applications/worker/settings");
        applyWorkerSettings(data.settings || {});
      } catch (error) {
        workerHubs = [...workerDefaultHubs];
        renderWorkerHubs();
        if (workerSaveStatus) {
          workerSaveStatus.textContent = `Worker settings could not be loaded from the local backend: ${error.message || error}`;
        }
      }
    }

    function loadWorkerSettings() {
      if (workerSettingsLoaded) return;
      workerSettingsLoaded = true;
      renderWorkerHubs();
      workerLoadSettingsFromBackend();
    }

    function buildWorkerOfferRegistrationPayload() {
      const settings = readWorkerFormSettings();
      const models = workerOfferModelsArray();
      if (!settings.registrationHubUrl) {
        throw new Error("Choose a sell-capable hub before registering an offer.");
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
        capabilities: {
          capabilities: [settings.capability],
          pricing,
          execution,
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

    function bindWorkerAutosaveSetting(element, eventName = "change") {
      if (!element) return;
      const datasetKey = eventName === "input" ? "workerAutosaveInputBound" : "workerAutosaveChangeBound";
      if (element.dataset[datasetKey]) return;
      element.dataset[datasetKey] = "true";
      element.addEventListener(eventName, saveWorkerSettings);
    }

    function initWorkerApp() {
      loadWorkerSettings();
      renderWorkerHubs();
      loadWorkerBridgeState();
      renderWorkerBridgeReadiness();
      workerRefreshHubCreditBridgeConfig();
      workerRefreshFaucetRuntimeStatus();
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
      [
        workerRemoteEnabled,
        workerRemoteMode,
        workerRemoteCreditsPerToken,
        workerRemoteMaxOutputTokens,
        workerRemoteDailyLimit,
        workerRemoteAskBeforeSpend,
        workerRemoteOnlyWhenBusy
      ].forEach((element) => bindWorkerAutosaveSetting(element, "change"));
      bindWorkerAutosaveSetting(workerRemoteCreditsPerToken, "input");
      if (workerPauseRentals && !workerPauseRentals.dataset.workerBound) {
        workerPauseRentals.dataset.workerBound = "true";
        workerPauseRentals.addEventListener("click", () => {
          if (workerRentalEnabled) workerRentalEnabled.checked = false;
          if (workerSaveStatus) workerSaveStatus.textContent = "Selling paused locally. Save settings to keep paid jobs off.";
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
      workerHydrateConnectedWalletFromProvider("page-load");
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

