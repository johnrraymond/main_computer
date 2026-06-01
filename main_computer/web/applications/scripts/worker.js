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
    let workerBridgeStateLoaded = false;
    let workerBridgeState = workerDefaultBridgeState();
    let workerWalletConnectInFlight = null;
    let workerWalletDisconnectInFlight = null;
    let workerWalletProviderEventsBound = false;
    let workerWalletProviderSyncInFlight = null;
    let workerWalletProviderSyncQueued = false;
    let workerWalletOperationSerial = 0;
    let workerExpectedChainCache = null;

    function workerDefaultBridgeState() {
      return {
        wallet: {
          address: "",
          chainId: "",
          connected: false,
          connectedAt: ""
        },
        bridgeAccount: {
          id: "",
          status: "not-created",
          primaryWallet: "",
          createdAt: "",
          updatedAt: ""
        },
        recoveryEmails: [],
        recoveryWallets: [],
        recoveryConfirmedAt: "",
        multisessionKeys: [],
        activeMultisessionKeyId: "",
        faucet: {
          amountCredits: "1",
          lastStatus: "Not requested",
          lastTxHash: "",
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

    function workerCleanWalletAddress(value) {
      const clean = String(value || "").trim();
      return /^0x[0-9a-f]{40}$/i.test(clean) ? clean : "";
    }

    function workerWalletAddressMatches(left, right) {
      const leftClean = workerCleanWalletAddress(left).toLowerCase();
      const rightClean = workerCleanWalletAddress(right).toLowerCase();
      return Boolean(leftClean && rightClean && leftClean === rightClean);
    }

    function workerNextWalletOperationToken() {
      workerWalletOperationSerial += 1;
      return workerWalletOperationSerial;
    }

    function workerWalletOperationIsCurrent(token) {
      return token === workerWalletOperationSerial;
    }

    function workerWalletControlsBusy() {
      return Boolean(workerWalletConnectInFlight || workerWalletDisconnectInFlight);
    }

    function workerWalletLocallyConnected() {
      return Boolean(workerBridgeState.wallet?.connected && workerBridgeState.wallet?.address);
    }

    function workerRenderWalletControls() {
      const busy = workerWalletControlsBusy();
      const connected = workerWalletLocallyConnected();
      if (workerConnectWallet) {
        workerConnectWallet.disabled = busy || connected;
        workerConnectWallet.textContent = connected ? "Connected" : "Connect Wallet";
        if (busy) workerConnectWallet.setAttribute("aria-busy", "true");
        else workerConnectWallet.removeAttribute("aria-busy");
      }
      if (workerDisconnectWallet) {
        workerDisconnectWallet.disabled = busy || !connected;
        if (busy) workerDisconnectWallet.setAttribute("aria-busy", "true");
        else workerDisconnectWallet.removeAttribute("aria-busy");
      }
    }

    function workerSetWalletControlsBusy(busy) {
      if (workerConnectWallet) {
        workerConnectWallet.disabled = Boolean(busy) || workerWalletLocallyConnected();
        if (busy) workerConnectWallet.setAttribute("aria-busy", "true");
        else workerConnectWallet.removeAttribute("aria-busy");
      }
      if (workerDisconnectWallet) {
        workerDisconnectWallet.disabled = Boolean(busy) || !workerWalletLocallyConnected();
        if (busy) workerDisconnectWallet.setAttribute("aria-busy", "true");
        else workerDisconnectWallet.removeAttribute("aria-busy");
      }
    }

    async function workerRequestFreshWalletPermission() {
      if (!window.ethereum || typeof window.ethereum.request !== "function") {
        throw new Error("No browser wallet provider was found.");
      }
      try {
        await window.ethereum.request({
          method: "wallet_requestPermissions",
          params: [{eth_accounts: {}}]
        });
      } catch (error) {
        if (error && typeof error === "object" && error.code === -32601) {
          return;
        }
        throw error;
      }
    }

    async function workerRevokeWalletPermission() {
      if (!window.ethereum || typeof window.ethereum.request !== "function") {
        return false;
      }
      try {
        await window.ethereum.request({
          method: "wallet_revokePermissions",
          params: [{eth_accounts: {}}]
        });
        return true;
      } catch (error) {
        if (error && typeof error === "object" && error.code === -32601) {
          return false;
        }
        throw error;
      }
    }

    async function workerReadWalletProviderSnapshot() {
      const accounts = await window.ethereum.request({method: "eth_accounts"});
      const address = Array.isArray(accounts) && accounts[0] ? workerCleanWalletAddress(accounts[0]) : "";
      const chainId = workerNormalizeChainHex(await window.ethereum.request({method: "eth_chainId"}));
      return {address, chainId};
    }

    function workerNormalizeChainHex(value) {
      const clean = String(value || "").trim();
      if (!clean) return "";
      try {
        if (/^0x[0-9a-f]+$/i.test(clean)) {
          return `0x${BigInt(clean).toString(16)}`;
        }
        if (/^[0-9]+$/.test(clean)) {
          return `0x${BigInt(clean).toString(16)}`;
        }
      } catch {}
      return clean.toLowerCase();
    }

    function workerChainIdMatches(left, right) {
      const leftHex = workerNormalizeChainHex(left);
      const rightHex = workerNormalizeChainHex(right);
      return Boolean(leftHex && rightHex && leftHex === rightHex);
    }

    async function workerExpectedChainDetails() {
      const now = Date.now();
      if (workerExpectedChainCache && now - workerExpectedChainCache.fetchedAt < 15000) {
        return workerExpectedChainCache.details;
      }
      const fallback = {
        expectedHex: "",
        rpcUrl: "http://127.0.0.1:8545",
        chainName: "Main Computer Dev Chain",
        currencySymbol: "Compute Credits"
      };
      try {
        const response = await fetch("/api/xlag/contract/status", {cache: "no-store"});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();
        const live = data && typeof data === "object" && data.live && typeof data.live === "object" ? data.live : {};
        const expectedRaw = live.expected_chain_id || data.chain_id_expected || data.chain_id || live.chain_id || "";
        const details = {
          expectedHex: workerNormalizeChainHex(expectedRaw),
          rpcUrl: String(live.rpc_url || data.rpc_url || fallback.rpcUrl),
          chainName: String(live.chain_name || data.chain_name || fallback.chainName),
          currencySymbol: String(live.currency_symbol || data.currency_symbol || fallback.currencySymbol)
        };
        workerExpectedChainCache = {fetchedAt: now, details};
        return details;
      } catch {
        workerExpectedChainCache = {fetchedAt: now, details: fallback};
        return fallback;
      }
    }

    async function workerEnsureExpectedWalletChain() {
      const details = await workerExpectedChainDetails();
      const expectedHex = details.expectedHex;
      const actualHex = workerNormalizeChainHex(await window.ethereum.request({method: "eth_chainId"}));
      if (!expectedHex || workerChainIdMatches(actualHex, expectedHex)) {
        return actualHex;
      }
      try {
        await window.ethereum.request({method: "wallet_switchEthereumChain", params: [{chainId: expectedHex}]});
      } catch (error) {
        if (error && typeof error === "object" && error.code === 4902 && details.rpcUrl) {
          await window.ethereum.request({
            method: "wallet_addEthereumChain",
            params: [{
              chainId: expectedHex,
              chainName: details.chainName,
              nativeCurrency: {name: details.currencySymbol, symbol: details.currencySymbol, decimals: 18},
              rpcUrls: [details.rpcUrl]
            }]
          });
        } else {
          throw error;
        }
      }
      const confirmedHex = workerNormalizeChainHex(await window.ethereum.request({method: "eth_chainId"}));
      if (!workerChainIdMatches(confirmedHex, expectedHex)) {
        throw new Error(`Wallet is on ${confirmedHex || "an unknown chain"}; expected ${expectedHex}.`);
      }
      return confirmedHex;
    }

    function workerApplyWalletSnapshot(address, chainId, options = {}) {
      if (Number.isInteger(options.operationToken) && !workerWalletOperationIsCurrent(options.operationToken)) {
        return false;
      }
      loadWorkerBridgeState();
      const cleanAddress = workerCleanWalletAddress(address);
      const previousWallet = workerBridgeState.wallet || {};
      const sameAddress = cleanAddress && workerWalletAddressMatches(cleanAddress, previousWallet.address);
      workerBridgeState.wallet = {
        address: cleanAddress,
        chainId: cleanAddress ? workerNormalizeChainHex(chainId) : "",
        connected: Boolean(cleanAddress),
        connectedAt: cleanAddress ? (sameAddress && previousWallet.connectedAt ? previousWallet.connectedAt : workerNowIso()) : ""
      };
      if (cleanAddress && workerBridgeState.bridgeAccount.id && !workerBridgeState.bridgeAccount.primaryWallet) {
        workerBridgeState.bridgeAccount.primaryWallet = cleanAddress;
      }
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      return true;
    }

    function workerClearPrimaryWalletState() {
      loadWorkerBridgeState();
      workerBridgeState.wallet = {...workerDefaultBridgeState().wallet};
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
    }

    async function workerDisconnectPrimaryWallet() {
      if (workerWalletDisconnectInFlight) return workerWalletDisconnectInFlight;
      const token = workerNextWalletOperationToken();
      workerWalletProviderSyncQueued = false;
      workerSetWalletControlsBusy(true);
      workerWalletDisconnectInFlight = (async () => {
        let revokedBrowserPermission = false;
        try {
          if (workerSaveStatus) workerSaveStatus.textContent = "Disconnecting wallet and clearing browser account permission...";
          revokedBrowserPermission = await workerRevokeWalletPermission();
        } catch (error) {
          if (workerSaveStatus) {
            workerSaveStatus.textContent = `Browser wallet permission revoke failed; cleared Worker local state only: ${error.message || error}`;
          }
        } finally {
          if (workerWalletOperationIsCurrent(token)) {
            workerClearPrimaryWalletState();
            if (workerSaveStatus && revokedBrowserPermission) {
              workerSaveStatus.textContent = "Wallet disconnected and browser account permission was revoked. Connect Wallet should ask MetaMask again.";
            } else if (workerSaveStatus && !revokedBrowserPermission) {
              workerSaveStatus.textContent = "Wallet disconnected locally. Your browser wallet did not support permission revoke; use MetaMask's site disconnect if reconnect does not prompt.";
            }
            workerWalletDisconnectInFlight = null;
            workerSetWalletControlsBusy(false);
            renderWorkerBridgeReadiness();
          }
        }
      })();
      return workerWalletDisconnectInFlight;
    }

    function workerScheduleWalletProviderSync(reason = "wallet event") {
      if (!window.ethereum || typeof window.ethereum.request !== "function") return null;
      if (workerWalletConnectInFlight) {
        workerWalletProviderSyncQueued = true;
        return workerWalletConnectInFlight;
      }
      if (workerWalletDisconnectInFlight) {
        workerWalletProviderSyncQueued = true;
        return workerWalletDisconnectInFlight;
      }
      if (workerWalletProviderSyncInFlight) {
        workerWalletProviderSyncQueued = true;
        return workerWalletProviderSyncInFlight;
      }
      loadWorkerBridgeState();
      if (!workerWalletLocallyConnected()) return null;

      const token = workerNextWalletOperationToken();
      const savedAddress = workerCleanWalletAddress(workerBridgeState.wallet.address);
      workerWalletProviderSyncQueued = false;
      workerWalletProviderSyncInFlight = (async () => {
        try {
          const snapshot = await workerReadWalletProviderSnapshot();
          if (!workerWalletOperationIsCurrent(token)) return;

          if (!snapshot.address) {
            workerApplyWalletSnapshot("", "", {operationToken: token});
            if (workerSaveStatus) workerSaveStatus.textContent = "Wallet provider reported no selected account; local wallet state was cleared.";
            return;
          }

          if (savedAddress && !workerWalletAddressMatches(snapshot.address, savedAddress)) {
            renderWorkerBridgeReadiness();
            if (workerSaveStatus) {
              workerSaveStatus.textContent = `Wallet provider selected ${workerShortAddress(snapshot.address)}; Worker kept ${workerShortAddress(savedAddress)}. Use Disconnect Wallet before changing accounts.`;
            }
            return;
          }

          const details = await workerExpectedChainDetails();
          if (!workerWalletOperationIsCurrent(token)) return;
          if (details.expectedHex && snapshot.chainId && !workerChainIdMatches(snapshot.chainId, details.expectedHex)) {
            renderWorkerBridgeReadiness();
            if (workerSaveStatus) {
              workerSaveStatus.textContent = `Wallet provider moved to ${snapshot.chainId}; Worker kept ${workerShortAddress(savedAddress)} on the last dev-chain wallet. Switch back to ${details.expectedHex} or disconnect before reconnecting.`;
            }
            return;
          }

          const applied = workerApplyWalletSnapshot(savedAddress || snapshot.address, snapshot.chainId, {operationToken: token});
          if (applied && workerSaveStatus) {
            workerSaveStatus.textContent = `${reason}: synced ${workerShortAddress(savedAddress || snapshot.address)} on ${snapshot.chainId || "the selected chain"}.`;
          }
        } catch (error) {
          if (workerSaveStatus) workerSaveStatus.textContent = `Wallet sync failed: ${error.message || error}`;
        } finally {
          workerWalletProviderSyncInFlight = null;
          renderWorkerBridgeReadiness();
          if (workerWalletProviderSyncQueued) {
            workerWalletProviderSyncQueued = false;
            window.setTimeout(() => workerScheduleWalletProviderSync("wallet event"), 0);
          }
        }
      })();

      return workerWalletProviderSyncInFlight;
    }

    function workerBindWalletProviderEvents() {
      if (workerWalletProviderEventsBound || !window.ethereum || typeof window.ethereum.on !== "function") return;
      workerWalletProviderEventsBound = true;
      window.ethereum.on("accountsChanged", () => {
        workerScheduleWalletProviderSync("Wallet account changed");
      });
      window.ethereum.on("chainChanged", () => {
        workerScheduleWalletProviderSync("Wallet chain changed");
      });
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
      const wallet = state.wallet && typeof state.wallet === "object" ? state.wallet : {};
      const bridgeAccount = state.bridgeAccount && typeof state.bridgeAccount === "object" ? state.bridgeAccount : {};
      const faucet = state.faucet && typeof state.faucet === "object" ? state.faucet : {};
      const multisessionKeys = Array.isArray(state.multisessionKeys) ? state.multisessionKeys : [];
      return {
        wallet: {
          address: String(wallet.address || ""),
          chainId: String(wallet.chainId || ""),
          connected: Boolean(wallet.connected && wallet.address),
          connectedAt: String(wallet.connectedAt || "")
        },
        bridgeAccount: {
          id: String(bridgeAccount.id || ""),
          status: String(bridgeAccount.status || fallback.bridgeAccount.status),
          primaryWallet: String(bridgeAccount.primaryWallet || bridgeAccount.primary_wallet || ""),
          createdAt: String(bridgeAccount.createdAt || bridgeAccount.created_at || ""),
          updatedAt: String(bridgeAccount.updatedAt || bridgeAccount.updated_at || "")
        },
        recoveryEmails: workerNormalizeList(state.recoveryEmails, "email"),
        recoveryWallets: workerNormalizeList(state.recoveryWallets, "wallet"),
        recoveryConfirmedAt: String(state.recoveryConfirmedAt || state.recovery_confirmed_at || ""),
        multisessionKeys: multisessionKeys
          .map((key) => ({
            id: String(key.id || ""),
            status: String(key.status || "active"),
            createdAt: String(key.createdAt || key.created_at || ""),
            revokedAt: String(key.revokedAt || key.revoked_at || "")
          }))
          .filter((key) => key.id),
        activeMultisessionKeyId: String(state.activeMultisessionKeyId || state.active_multisession_key_id || ""),
        faucet: {
          amountCredits: String(faucet.amountCredits || faucet.amount_credits || "1"),
          lastStatus: String(faucet.lastStatus || faucet.last_status || fallback.faucet.lastStatus),
          lastTxHash: String(faucet.lastTxHash || faucet.last_tx_hash || ""),
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
      if (workerFaucetAmount) {
        workerFaucetAmount.value = workerBridgeState.faucet.amountCredits || "1";
      }
    }

    function saveWorkerBridgeState() {
      try {
        localStorage.setItem(workerBridgeReadinessStorageKey, JSON.stringify(workerBridgeState));
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

    function workerBridgeAccountReady() {
      return Boolean(workerBridgeState.bridgeAccount.id && workerBridgeState.bridgeAccount.status === "prepared");
    }

    function workerReadinessLabel() {
      if (workerBridgeAccountReady() && workerRecoveryMethodsReady() && workerActiveMultisessionKey()) {
        return "Bridge account prepared — ready for later funding";
      }
      if (!workerBridgeState.wallet.address) {
        return "Wallet not connected";
      }
      if (!workerBridgeAccountReady()) {
        return "Create or load bridge account";
      }
      if (!workerRecoveryMethodsReady()) {
        return "Confirm recovery methods";
      }
      if (!workerActiveMultisessionKey()) {
        return "Request multi-session key";
      }
      return "Setup in progress";
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
      const bridgeAccount = workerBridgeState.bridgeAccount;
      const recoveryEmailCount = workerBridgeState.recoveryEmails.filter((item) => item.status === "active").length;
      const recoveryWalletCount = workerBridgeState.recoveryWallets.filter((item) => item.status === "active").length;
      const activeKey = workerActiveMultisessionKey();
      const revokedKey = [...workerBridgeState.multisessionKeys].reverse().find((key) => key.status === "revoked");

      if (workerPrimaryWalletStatus) {
        workerPrimaryWalletStatus.textContent = walletAddress
          ? `${workerShortAddress(walletAddress)}${workerBridgeState.wallet.chainId ? ` on ${workerBridgeState.wallet.chainId}` : ""}`
          : "Not connected";
      }
      if (workerBridgeAccountStatus) {
        workerBridgeAccountStatus.textContent = bridgeAccount.id
          ? `${bridgeAccount.id} (${bridgeAccount.status})`
          : "Not created";
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
        workerFaucetTarget.textContent = walletAddress ? workerShortAddress(walletAddress) : "Connect wallet first";
      }
      if (workerFaucetStatus) {
        workerFaucetStatus.textContent = workerBridgeState.faucet.lastStatus || "Not requested";
      }
      if (workerFaucetTx) {
        workerFaucetTx.textContent = workerBridgeState.faucet.lastTxHash || "—";
      }
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
      if (workerRevokeMultisessionKey) {
        workerRevokeMultisessionKey.disabled = !activeKey;
      }
      workerRenderWalletControls();
      workerRenderTokenList(workerRecoveryEmailList, workerBridgeState.recoveryEmails, "No recovery emails added.", "data-worker-remove-recovery-email");
      workerRenderTokenList(workerRecoveryWalletList, workerBridgeState.recoveryWallets, "No recovery wallets added.", "data-worker-remove-recovery-wallet");
    }

    function workerRandomKeyId() {
      const random = window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID().replace(/-/g, "").slice(0, 12)
        : Math.random().toString(36).slice(2, 14);
      return `msk_${Date.now().toString(36)}_${random}`;
    }

    async function connectWorkerPrimaryWallet() {
      loadWorkerBridgeState();
      if (!window.ethereum || typeof window.ethereum.request !== "function") {
        if (workerSaveStatus) workerSaveStatus.textContent = "No browser wallet provider was found.";
        return;
      }
      if (workerWalletConnectInFlight) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Wallet connection already in progress.";
        return workerWalletConnectInFlight;
      }
      if (workerWalletDisconnectInFlight) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Wallet disconnect is still in progress.";
        return workerWalletDisconnectInFlight;
      }
      if (workerWalletLocallyConnected()) {
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = "Wallet is already connected. Use Disconnect Wallet before choosing a different account.";
        return;
      }

      workerBindWalletProviderEvents();
      const token = workerNextWalletOperationToken();
      workerSetWalletControlsBusy(true);
      workerWalletConnectInFlight = (async () => {
        try {
          if (workerSaveStatus) workerSaveStatus.textContent = "Opening MetaMask account chooser...";
          await workerRequestFreshWalletPermission();
          if (!workerWalletOperationIsCurrent(token)) return;

          if (workerSaveStatus) workerSaveStatus.textContent = "Reading selected wallet account...";
          const requestedAccounts = await window.ethereum.request({method: "eth_requestAccounts"});
          if (!workerWalletOperationIsCurrent(token)) return;
          const requestedAddress = Array.isArray(requestedAccounts) && requestedAccounts[0]
            ? workerCleanWalletAddress(requestedAccounts[0])
            : "";
          if (!requestedAddress) throw new Error("No valid wallet account was returned.");

          if (workerSaveStatus) workerSaveStatus.textContent = "Checking Worker dev chain...";
          const switchedChainId = await workerEnsureExpectedWalletChain();
          if (!workerWalletOperationIsCurrent(token)) return;

          const snapshot = await workerReadWalletProviderSnapshot();
          if (!workerWalletOperationIsCurrent(token)) return;
          const finalAddress = snapshot.address || requestedAddress;
          const finalChainId = snapshot.chainId || switchedChainId;
          if (!finalAddress) throw new Error("No valid wallet account is selected after chain confirmation.");
          if (!workerWalletAddressMatches(finalAddress, requestedAddress)) {
            throw new Error(`Wallet account changed during connect from ${workerShortAddress(requestedAddress)} to ${workerShortAddress(finalAddress)}. Retry Connect Wallet after MetaMask settles.`);
          }

          const details = await workerExpectedChainDetails();
          if (!workerWalletOperationIsCurrent(token)) return;
          if (details.expectedHex && finalChainId && !workerChainIdMatches(finalChainId, details.expectedHex)) {
            throw new Error(`Wallet is on ${finalChainId || "an unknown chain"}; expected ${details.expectedHex}.`);
          }

          const applied = workerApplyWalletSnapshot(requestedAddress, finalChainId, {operationToken: token});
          if (applied && workerSaveStatus) workerSaveStatus.textContent = `Connected wallet ${workerShortAddress(requestedAddress)} on ${finalChainId || "the selected chain"}.`;
        } catch (error) {
          if (workerWalletOperationIsCurrent(token) && workerSaveStatus) {
            workerSaveStatus.textContent = `Wallet connection failed: ${error.message || error}`;
          }
        } finally {
          if (workerWalletOperationIsCurrent(token)) {
            workerWalletConnectInFlight = null;
            workerSetWalletControlsBusy(false);
            renderWorkerBridgeReadiness();
            if (workerWalletProviderSyncQueued) {
              workerWalletProviderSyncQueued = false;
              workerScheduleWalletProviderSync("wallet event");
            }
          }
        }
      })();
      return workerWalletConnectInFlight;
    }

    function createOrLoadWorkerBridgeAccount() {
      loadWorkerBridgeState();
      const address = String(workerBridgeState.wallet.address || "").trim();
      if (!address) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Connect a primary wallet before creating the local bridge account.";
        renderWorkerBridgeReadiness();
        return;
      }
      const now = workerNowIso();
      if (!workerBridgeState.bridgeAccount.id) {
        const slug = address.toLowerCase().replace(/^0x/, "").slice(0, 12) || "wallet";
        workerBridgeState.bridgeAccount = {
          id: `bridge_local_${slug}`,
          status: "prepared",
          primaryWallet: address,
          createdAt: now,
          updatedAt: now
        };
      } else {
        workerBridgeState.bridgeAccount = {
          ...workerBridgeState.bridgeAccount,
          status: "prepared",
          primaryWallet: workerBridgeState.bridgeAccount.primaryWallet || address,
          updatedAt: now
        };
      }
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = "Local bridge account readiness record prepared.";
    }

    async function requestWorkerFaucetCredits() {
      loadWorkerBridgeState();
      const address = String(workerBridgeState.wallet.address || "").trim();
      if (!address) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Connect a wallet before requesting faucet credits.";
        renderWorkerBridgeReadiness();
        return;
      }
      const amountCredits = String(workerPositiveInteger(workerElementValue(workerFaucetAmount, workerBridgeState.faucet.amountCredits || "1"), 1));
      if (workerRequestFaucet) workerRequestFaucet.disabled = true;
      workerBridgeState.faucet = {
        ...workerBridgeState.faucet,
        amountCredits,
        lastStatus: `Requesting ${amountCredits} credit${amountCredits === "1" ? "" : "s"}...`,
        updatedAt: workerNowIso()
      };
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      try {
        const data = await workerPostJson("/api/xlag/dev/faucet", {
          address,
          amount_credits: amountCredits
        });
        workerBridgeState.faucet = {
          amountCredits,
          lastStatus: "Faucet request submitted",
          lastTxHash: String(data.tx_hash || data.transaction_hash || data.hash || ""),
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = workerBridgeState.faucet.lastTxHash
          ? `Faucet request submitted: ${workerBridgeState.faucet.lastTxHash}`
          : "Faucet request submitted.";
      } catch (error) {
        workerBridgeState.faucet = {
          ...workerBridgeState.faucet,
          lastStatus: `Faucet request failed: ${error.message || error}`,
          updatedAt: workerNowIso()
        };
        saveWorkerBridgeState();
        renderWorkerBridgeReadiness();
        if (workerSaveStatus) workerSaveStatus.textContent = workerBridgeState.faucet.lastStatus;
      } finally {
        if (workerRequestFaucet) workerRequestFaucet.disabled = false;
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

    function requestWorkerMultisessionKey() {
      loadWorkerBridgeState();
      if (workerActiveMultisessionKey()) {
        if (workerSaveStatus) workerSaveStatus.textContent = "Revoke the active multi-session key before requesting a replacement.";
        renderWorkerBridgeReadiness();
        return;
      }
      const now = workerNowIso();
      const key = {
        id: workerRandomKeyId(),
        status: "active",
        createdAt: now,
        revokedAt: ""
      };
      workerBridgeState.multisessionKeys.push(key);
      workerBridgeState.activeMultisessionKeyId = key.id;
      saveWorkerBridgeState();
      renderWorkerBridgeReadiness();
      if (workerSaveStatus) workerSaveStatus.textContent = "Multi-session key requested and marked active locally.";
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
      workerBindWalletProviderEvents();
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
        workerConnectWallet.addEventListener("click", connectWorkerPrimaryWallet);
      }
      if (workerDisconnectWallet && !workerDisconnectWallet.dataset.workerBound) {
        workerDisconnectWallet.dataset.workerBound = "true";
        workerDisconnectWallet.addEventListener("click", workerDisconnectPrimaryWallet);
      }
      if (workerCreateBridgeAccount && !workerCreateBridgeAccount.dataset.workerBound) {
        workerCreateBridgeAccount.dataset.workerBound = "true";
        workerCreateBridgeAccount.addEventListener("click", createOrLoadWorkerBridgeAccount);
      }
      if (workerRefreshBridgeReadiness && !workerRefreshBridgeReadiness.dataset.workerBound) {
        workerRefreshBridgeReadiness.dataset.workerBound = "true";
        workerRefreshBridgeReadiness.addEventListener("click", () => {
          loadWorkerBridgeState();
          renderWorkerBridgeReadiness();
          if (workerSaveStatus) workerSaveStatus.textContent = "Bridge readiness state refreshed from local storage.";
        });
      }
      if (workerFaucetAmount && !workerFaucetAmount.dataset.workerBound) {
        workerFaucetAmount.dataset.workerBound = "true";
        workerFaucetAmount.addEventListener("change", () => {
          loadWorkerBridgeState();
          workerBridgeState.faucet.amountCredits = String(workerPositiveInteger(workerElementValue(workerFaucetAmount, "1"), 1));
          saveWorkerBridgeState();
          renderWorkerBridgeReadiness();
        });
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

