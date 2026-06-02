    const workerSettingsStorageKey = "main-computer-worker-settings-v4";
    const workerLegacySettingsStorageKeys = [
      "main-computer-worker-settings-v3",
      "main-computer-worker-settings-v2",
      "main-computer-worker-settings-v1"
    ];
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
    const WORKER_DEV_CHAIN_NAME = "Main Computer Dev";
    const WORKER_DEV_CHAIN_RPC_URL = "http://127.0.0.1:18545";
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
    let workerMultisessionInFlight = false;

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
      workerBridgeState.wallet = {
        address: connected ? String(address || "") : "",
        chainId: connected ? workerNormalizeChainIdHex(chainId) : "",
        connected: Boolean(connected && address),
        connectedAt: connected && address ? workerNowIso() : ""
      };
    }

    function workerRenderWalletControls() {
      const connected = Boolean(workerBridgeState.wallet.connected && workerBridgeState.wallet.address);
      const busy = ["requesting", "stabilizing", "disconnecting"].includes(workerWalletHookState);
      if (workerConnectWallet) {
        workerConnectWallet.disabled = busy || connected;
        workerConnectWallet.textContent = connected
          ? "Connected"
          : workerWalletHookState === "requesting"
            ? "Wallet Open…"
            : workerWalletHookState === "stabilizing"
              ? "Finalizing…"
              : "Connect Wallet";

        if (["requesting", "stabilizing"].includes(workerWalletHookState)) {
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
    }

    function saveWorkerBridgeState() {
      try {
        const {wallet: _liveWalletState, ...serializableState} = workerBridgeState || {};
        localStorage.setItem(workerBridgeReadinessStorageKey, JSON.stringify(serializableState));
      } catch {}
    }

    function workerActiveMultisessionKey() {
      const activeId = String(workerBridgeState.activeMultisessionKeyId || "");
      if (!activeId) return null;
      return workerBridgeState.multisessionKeys.find((key) => key.id === activeId && key.status === "active") || null;
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
      const walletAddress = workerBridgeState.wallet.address;
      const faucetReadiness = workerComputeFaucetReadiness();
      const recoveryEmailCount = workerBridgeState.recoveryEmails.filter((item) => item.status === "active").length;
      const recoveryWalletCount = workerBridgeState.recoveryWallets.filter((item) => item.status === "active").length;
      const activeKey = workerActiveMultisessionKey();
      const revokedKey = [...workerBridgeState.multisessionKeys].reverse().find((key) => key.status === "revoked");

      if (workerPrimaryWalletStatus) {
        workerPrimaryWalletStatus.textContent = walletAddress
          ? `${workerShortAddress(walletAddress)}${workerBridgeState.wallet.chainId ? ` on ${workerBridgeState.wallet.chainId}` : ""}`
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

    function workerWalletRecordEvent(type, detail = {}) {
      const entry = {
        at: workerNowIso(),
        type: String(type || "event"),
        detail: detail && typeof detail === "object" ? detail : {}
      };
      console.log("[worker-wallet]", entry.type, entry.detail);
      if (workerSaveStatus) {
        if (entry.type === "connect.ethers.requestAccounts.start") {
          workerSaveStatus.textContent = "Opening browser wallet account request...";
        } else if (entry.type === "connect.ethers.requestAccounts.resolved") {
          workerSaveStatus.textContent = "Wallet account request accepted; verifying signer and chain with ethers.";
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

    async function workerEnsureDevWalletChain(browserProvider) {
      let snapshot = await workerReadWalletProviderSnapshot(browserProvider);
      if (snapshot.chainId === WORKER_DEV_CHAIN_ID_HEX) {
        return snapshot.chainId;
      }

      workerWalletRecordEvent("connect.ethers.switchChain.start", {
        from: snapshot.chainId || "unknown",
        to: WORKER_DEV_CHAIN_ID_HEX
      });

      try {
        await browserProvider.send("wallet_switchEthereumChain", [{chainId: WORKER_DEV_CHAIN_ID_HEX}]);
      } catch (error) {
        const code = error && typeof error === "object" ? error.code : undefined;
        const message = error && typeof error === "object" ? String(error.message || "") : String(error || "");
        if (code !== 4902 && !message.includes("4902")) {
          throw error;
        }
        workerWalletRecordEvent("connect.ethers.addChain.start", {chainId: WORKER_DEV_CHAIN_ID_HEX});
        await browserProvider.send("wallet_addEthereumChain", [{
          chainId: WORKER_DEV_CHAIN_ID_HEX,
          chainName: WORKER_DEV_CHAIN_NAME,
          nativeCurrency: {
            name: "MC Dev ETH",
            symbol: "ETH",
            decimals: 18
          },
          rpcUrls: [WORKER_DEV_CHAIN_RPC_URL],
          blockExplorerUrls: []
        }]);
      }

      snapshot = await workerReadWalletProviderSnapshot(browserProvider);
      if (snapshot.chainId !== WORKER_DEV_CHAIN_ID_HEX) {
        throw new Error(`Wrong chain after wallet switch. Expected ${WORKER_DEV_CHAIN_ID_HEX}, got ${snapshot.chainId || "unknown"}.`);
      }
      return snapshot.chainId;
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
          workerSetPrimaryWalletState({connected: true, address, chainId});
          workerWalletLastAction = `Connected ${workerShortAddress(address)} on ${chainId}.`;
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
        workerWalletRecordEvent("connect.ethers.requestAccounts.start", {provider: context.providerInfo});

        await context.browserProvider.send("eth_requestAccounts", []);

        if (!workerWalletOperationIsCurrent(token)) {
          throw new Error("Connect result ignored because Force Disconnect / Reset was clicked.");
        }

        workerWalletRecordEvent("connect.ethers.requestAccounts.resolved");

        workerSetWalletOperationState("stabilizing", "Wallet accepted. Verifying signer and dev chain with ethers.");

        await workerEnsureDevWalletChain(context.browserProvider);

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
        remoteEnabled: Boolean(workerRemoteEnabled?.checked),
        remoteMode: workerElementValue(workerRemoteMode, "ask-when-busy"),
        remoteCreditsPerToken: workerPositiveInteger(workerElementValue(workerRemoteCreditsPerToken, "12"), 12),
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
      try {
        localStorage.setItem(workerSettingsStorageKey, JSON.stringify(settings));
        if (workerSaveStatus) {
          workerSaveStatus.textContent = "Worker marketplace buy/sell settings saved locally.";
        }
      } catch {
        if (workerSaveStatus) {
          workerSaveStatus.textContent = "Worker settings could not be saved in this browser.";
        }
      }
    }

    function assignWorkerValue(element, value) {
      if (!element || !("value" in element) || value === undefined || value === null) return;
      element.value = String(value);
    }

    function loadWorkerSettings() {
      if (workerSettingsLoaded) return;
      workerSettingsLoaded = true;
      try {
        const raw = [workerSettingsStorageKey, ...workerLegacySettingsStorageKeys]
          .map((key) => localStorage.getItem(key))
          .find((value) => value);
        const parsed = raw ? JSON.parse(raw) : null;
        if (parsed && Array.isArray(parsed.hubs)) {
          workerHubs = parsed.hubs
            .map((hub) => ({
              name: String(hub.name || "").trim(),
              url: String(hub.url || "").trim(),
              role: String(hub.role || "use-provide")
            }))
            .filter((hub) => hub.name || hub.url);
        }
        if (workerRemoteEnabled && parsed && typeof parsed.remoteEnabled === "boolean") {
          workerRemoteEnabled.checked = parsed.remoteEnabled;
        }
        if (parsed) {
          assignWorkerValue(workerRemoteMode, parsed.remoteMode);
          assignWorkerValue(workerRemoteCreditsPerToken, parsed.remoteCreditsPerToken);
          assignWorkerValue(workerRemoteMaxOutputTokens, parsed.remoteMaxOutputTokens);
          assignWorkerValue(workerRemoteDailyLimit, parsed.remoteDailyLimit);
        }
        if (workerRemoteAskBeforeSpend && parsed && typeof parsed.remoteAskBeforeSpend === "boolean") {
          workerRemoteAskBeforeSpend.checked = parsed.remoteAskBeforeSpend;
        }
        if (workerRemoteOnlyWhenBusy && parsed && typeof parsed.remoteOnlyWhenBusy === "boolean") {
          workerRemoteOnlyWhenBusy.checked = parsed.remoteOnlyWhenBusy;
        }
        if (workerRentalEnabled && parsed) {
          if (typeof parsed.sellerEnabled === "boolean") {
            workerRentalEnabled.checked = parsed.sellerEnabled;
          } else if (typeof parsed.rentalEnabled === "boolean") {
            workerRentalEnabled.checked = parsed.rentalEnabled;
          }
        }
        if (workerLockAiModel && parsed && typeof parsed.lockAiModel === "boolean") {
          workerLockAiModel.checked = parsed.lockAiModel;
        }
        if (parsed) {
          assignWorkerValue(workerNodeId, parsed.nodeId);
          assignWorkerValue(workerEndpoint, parsed.endpoint);
          assignWorkerValue(workerOfferModels, parsed.models);
          assignWorkerValue(workerOfferCapability, parsed.capability);
          assignWorkerValue(workerOfferPrice, parsed.creditsPerRequest);
          assignWorkerValue(workerMaxConcurrency, parsed.maxConcurrency);
          assignWorkerValue(workerExecutionMode, parsed.executionMode);
          workerRenderRegistrationHubOptions(parsed.registrationHubUrl);
        }
      } catch {
        workerHubs = [...workerDefaultHubs];
      }
      renderWorkerHubs();
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

    function initWorkerApp() {
      loadWorkerSettings();
      renderWorkerHubs();
      loadWorkerBridgeState();
      renderWorkerBridgeReadiness();
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
        workerRegistrationHub.addEventListener("change", saveWorkerSettings);
      }
      if (workerRegisterOffer && !workerRegisterOffer.dataset.workerBound) {
        workerRegisterOffer.dataset.workerBound = "true";
        workerRegisterOffer.addEventListener("click", registerWorkerOffer);
      }
      if (workerSaveSettings && !workerSaveSettings.dataset.workerBound) {
        workerSaveSettings.dataset.workerBound = "true";
        workerSaveSettings.addEventListener("click", saveWorkerSettings);
      }
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
      if (workerRefreshBridgeReadiness && !workerRefreshBridgeReadiness.dataset.workerBound) {
        workerRefreshBridgeReadiness.dataset.workerBound = "true";
        workerRefreshBridgeReadiness.addEventListener("click", () => {
          loadWorkerBridgeState();
          renderWorkerBridgeReadiness();
          workerRefreshFaucetRuntimeStatus();
          if (workerSaveStatus) workerSaveStatus.textContent = "Wallet and faucet readiness refreshed from local storage and runtime status.";
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

