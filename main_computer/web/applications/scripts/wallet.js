    const walletAppState = window.walletAppState || (window.walletAppState = {
      initialized: false,
      hookState: "idle",
      providerState: "not-installed",
      lastAction: "none",
      wallet: {
        connected: false,
        address: "",
        chainId: "",
        updatedAt: ""
      },
      events: [],
      agentCredits: {
        hubUrl: "http://127.0.0.1:8770",
        status: "idle",
        statusMessage: "Connect wallet, enter a helper wallet, then grant credits.",
        grants: [],
        lastGrant: null
      },
      dnsControl: {
        status: "idle",
        statusMessage: "Connect wallet, choose Cloudflare or self-hosted DNS, then save a control profile.",
        providerMode: "cloudflare",
        zone: "",
        recordName: "@",
        recordType: "A",
        recordValue: "",
        ttl: 300,
        proxied: false,
        nameserverHost: "",
        adminUrl: "",
        profiles: [],
        lastProfile: null
      }
    });

    let walletOperationSerial = 0;
    let walletProviderEventsBound = false;

    const WALLET_DEV_CHAIN_ID_DECIMAL = 42424242;
    const WALLET_DEV_CHAIN_ID_HEX = "0x28757b2";
    const WALLET_DEV_CHAIN_RPC_URL = "http://127.0.0.1:18545";
    const WALLET_DEV_CHAIN_NAME = "Main Computer Dev Chain";
    const WALLET_DEV_CHAIN_CURRENCY_NAME = "Main Computer XLAG Credit";
    const WALLET_DEV_CHAIN_CURRENCY_SYMBOL = "MCXLAG";
    const WALLET_AGENT_CREDIT_GRANT_ENDPOINT = "/api/applications/wallet/agent-credit-grants";
    const WALLET_DNS_CONTROL_ENDPOINT = "/api/applications/wallet/dns-control";
    const WALLET_AGENT_CREDIT_DEFAULT_HUB_URL = "http://127.0.0.1:8770";
    const WALLET_AGENT_CREDIT_DEFAULT_MEMO = "Agent helper credits for parallel verification workers.";
    const WALLET_AGENT_CREDIT_MAX_GRANT = 100;
    const WALLET_DNS_CONTROL_DEFAULT_STATUS = "Connect wallet, choose Cloudflare or self-hosted DNS, then save a control profile.";
    const WALLET_DNS_CONTROL_RECORD_TYPES = new Set(["A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV"]);

    function walletNowIso() {
      return new Date().toISOString();
    }

    function walletNextOperation() {
      walletOperationSerial += 1;
      return walletOperationSerial;
    }

    function walletOperationIsCurrent(token) {
      return token === walletOperationSerial;
    }

    function walletValidAddress(value) {
      return /^0x[0-9a-fA-F]{40}$/.test(String(value || ""));
    }

    function walletNormalizeAccountAddress(value) {
      const text = String(value || "").trim();
      return walletValidAddress(text) ? `0x${text.slice(2).toLowerCase()}` : "";
    }

    function walletNormalizeGrantCredits(value) {
      const parsed = Number.parseInt(String(value || "").trim(), 10);
      return Number.isFinite(parsed) ? parsed : 0;
    }

    function walletNormalizeHubUrl(value) {
      const text = String(value || "").trim().replace(/\/+$/, "");
      return text || WALLET_AGENT_CREDIT_DEFAULT_HUB_URL;
    }

    function walletNormalizeDnsMode(value) {
      const text = String(value || "").trim().toLowerCase();
      return text === "self-hosted" ? "self-hosted" : "cloudflare";
    }

    function walletDnsModeLabel(value) {
      return walletNormalizeDnsMode(value) === "self-hosted" ? "self-hosted DNS" : "Cloudflare DNS";
    }

    function walletNormalizeDnsText(value, fallback = "") {
      const text = String(value || "").trim();
      return text || fallback;
    }

    function walletNormalizeDnsRecordType(value) {
      const text = String(value || "").trim().toUpperCase();
      return WALLET_DNS_CONTROL_RECORD_TYPES.has(text) ? text : "A";
    }

    function walletNormalizeDnsTtl(value) {
      const parsed = Number.parseInt(String(value || "").trim(), 10);
      if (!Number.isFinite(parsed)) return 300;
      return Math.min(86400, Math.max(60, parsed));
    }

    function walletNormalizeChainIdHex(value) {
      const text = String(value || "").trim().toLowerCase();
      if (!text) return "";
      try {
        if (/^0x[0-9a-f]+$/.test(text)) {
          return `0x${BigInt(text).toString(16)}`;
        }
        if (/^[0-9]+$/.test(text)) {
          return `0x${BigInt(text).toString(16)}`;
        }
      } catch {
        return text;
      }
      return text;
    }

    function walletErrorCode(error) {
      const candidates = [
        error?.code,
        error?.error?.code,
        error?.info?.error?.code,
        error?.info?.payload?.code,
        error?.data?.code
      ];
      const match = candidates.find((value) => value !== undefined && value !== null && value !== "");
      const asNumber = Number(match);
      return Number.isFinite(asNumber) ? asNumber : match;
    }

    function walletErrorMessage(error) {
      const code = walletErrorCode(error);
      const messages = [
        error?.shortMessage,
        error?.message,
        error?.error?.message,
        error?.info?.error?.message,
        error?.data?.message,
        String(error || "")
      ].filter(Boolean);
      const uniqueMessages = [...new Set(messages.map(String))];
      const pieces = [];
      if (code !== undefined && code !== null && code !== "") {
        pieces.push(`code ${code}`);
      }
      pieces.push(uniqueMessages[0] || "unknown wallet error");
      return pieces.join(": ");
    }

    function walletChainMatchesExpected(chainId) {
      return walletNormalizeChainIdHex(chainId) === WALLET_DEV_CHAIN_ID_HEX;
    }

    function walletShortAddress(value) {
      const text = String(value || "");
      return text.length > 18 ? `${text.slice(0, 10)}…${text.slice(-6)}` : text;
    }

    function walletSleep(ms) {
      return new Promise((resolve) => window.setTimeout(resolve, ms));
    }

    function walletGetEthers() {
      if (!window.ethers?.BrowserProvider) {
        throw new Error("ethers.js BrowserProvider is not loaded.");
      }
      return window.ethers;
    }

    function walletInjectedProvider() {
      const injected = window.ethereum;
      if (!injected) {
        throw new Error("Browser wallet provider not found.");
      }
      return injected;
    }

    function walletProviderAvailable() {
      return Boolean(window.ethereum);
    }

    function walletEthersReady() {
      return Boolean(window.ethers?.BrowserProvider);
    }

    function walletBrowserProvider() {
      const ethersLib = walletGetEthers();
      return new ethersLib.BrowserProvider(walletInjectedProvider());
    }

    function walletFormatAddress(value) {
      const text = String(value || "");
      if (!text) return "";
      try {
        return walletGetEthers().getAddress(text);
      } catch {
        return text;
      }
    }

    async function walletAddressFromSignerLike(account) {
      if (!account) return "";
      if (typeof account === "string") return walletFormatAddress(account);
      if (typeof account.address === "string") return walletFormatAddress(account.address);
      if (typeof account.getAddress === "function") {
        return walletFormatAddress(await account.getAddress());
      }
      return "";
    }

    async function walletListAccountAddresses(provider = walletBrowserProvider()) {
      const accounts = await provider.listAccounts();
      const addresses = await Promise.all((Array.isArray(accounts) ? accounts : []).map(walletAddressFromSignerLike));
      return addresses.filter(walletValidAddress);
    }

    async function walletNetworkChainId(provider = walletBrowserProvider()) {
      const network = await provider.getNetwork();
      return walletNormalizeChainIdHex(network?.chainId);
    }

    function walletEnsureStateShape() {
      if (!walletAppState.wallet || typeof walletAppState.wallet !== "object") {
        walletAppState.wallet = {
          connected: false,
          address: "",
          chainId: "",
          updatedAt: ""
        };
      }
      if (!Array.isArray(walletAppState.events)) {
        walletAppState.events = [];
      }
      if (!walletAppState.agentCredits || typeof walletAppState.agentCredits !== "object") {
        walletAppState.agentCredits = {};
      }
      walletAppState.agentCredits.hubUrl = walletNormalizeHubUrl(walletAppState.agentCredits.hubUrl);
      walletAppState.agentCredits.status = String(walletAppState.agentCredits.status || "idle");
      walletAppState.agentCredits.statusMessage = String(
        walletAppState.agentCredits.statusMessage || "Connect wallet, enter a helper wallet, then grant credits."
      );
      walletAppState.agentCredits.grants = Array.isArray(walletAppState.agentCredits.grants)
        ? walletAppState.agentCredits.grants.slice(0, 20)
        : [];
      walletAppState.agentCredits.lastGrant = walletAppState.agentCredits.lastGrant && typeof walletAppState.agentCredits.lastGrant === "object"
        ? walletAppState.agentCredits.lastGrant
        : null;
      if (!walletAppState.dnsControl || typeof walletAppState.dnsControl !== "object") {
        walletAppState.dnsControl = {};
      }
      walletAppState.dnsControl.status = String(walletAppState.dnsControl.status || "idle");
      walletAppState.dnsControl.statusMessage = String(walletAppState.dnsControl.statusMessage || WALLET_DNS_CONTROL_DEFAULT_STATUS);
      walletAppState.dnsControl.providerMode = walletNormalizeDnsMode(walletAppState.dnsControl.providerMode);
      walletAppState.dnsControl.zone = walletNormalizeDnsText(walletAppState.dnsControl.zone);
      walletAppState.dnsControl.recordName = walletNormalizeDnsText(walletAppState.dnsControl.recordName, "@");
      walletAppState.dnsControl.recordType = walletNormalizeDnsRecordType(walletAppState.dnsControl.recordType);
      walletAppState.dnsControl.recordValue = walletNormalizeDnsText(walletAppState.dnsControl.recordValue);
      walletAppState.dnsControl.ttl = walletNormalizeDnsTtl(walletAppState.dnsControl.ttl);
      walletAppState.dnsControl.proxied = Boolean(walletAppState.dnsControl.proxied);
      walletAppState.dnsControl.nameserverHost = walletNormalizeDnsText(walletAppState.dnsControl.nameserverHost);
      walletAppState.dnsControl.adminUrl = walletNormalizeDnsText(walletAppState.dnsControl.adminUrl);
      walletAppState.dnsControl.profiles = Array.isArray(walletAppState.dnsControl.profiles)
        ? walletAppState.dnsControl.profiles.slice(0, 20)
        : [];
      walletAppState.dnsControl.lastProfile = walletAppState.dnsControl.lastProfile && typeof walletAppState.dnsControl.lastProfile === "object"
        ? walletAppState.dnsControl.lastProfile
        : null;
      walletAppState.hookState = String(walletAppState.hookState || "idle");
      walletAppState.providerState = String(walletAppState.providerState || "not-installed");
      walletAppState.lastAction = String(walletAppState.lastAction || "none");
    }

    function walletRecordEvent(type, detail = {}) {
      walletEnsureStateShape();
      const event = {
        at: walletNowIso(),
        type: String(type || "event"),
        detail: detail && typeof detail === "object" ? detail : {}
      };
      walletAppState.events.unshift(event);
      walletAppState.events = walletAppState.events.slice(0, 80);
      window.dispatchEvent(new CustomEvent("main-computer-wallet-hook", {detail: event}));
      renderWalletApp();
      return event;
    }

    function walletSetWalletState({connected = false, address = "", chainId = ""} = {}) {
      walletAppState.wallet = {
        connected: Boolean(connected),
        address: connected ? String(address || "") : "",
        chainId: connected ? String(chainId || "") : "",
        updatedAt: walletNowIso()
      };
    }

    function walletSetHookState(nextState, action) {
      walletEnsureStateShape();
      walletAppState.hookState = String(nextState || "idle");
      walletAppState.lastAction = String(action || walletAppState.lastAction || "none");
      renderWalletApp();
    }

    function walletSyncDisplayState() {
      walletEnsureStateShape();
      const wallet = walletAppState.wallet;
      const connected = Boolean(wallet.connected && wallet.address);

      if (connected) {
        walletAppState.providerState = `${walletShortAddress(wallet.address)} on ${wallet.chainId || "unknown chain"}`;
        walletAppState.lastAction = `connected ${walletShortAddress(wallet.address)}`;
      } else if (walletAppState.hookState === "requesting") {
        walletAppState.providerState = "browser wallet request open; not connected";
        walletAppState.lastAction = "Finish the wallet prompt. This page will not mark connected early.";
      } else if (walletAppState.hookState === "switching-chain") {
        walletAppState.providerState = `requesting ${WALLET_DEV_CHAIN_NAME} ${WALLET_DEV_CHAIN_ID_HEX}`;
        walletAppState.lastAction = "Waiting for the browser wallet to switch to the Main Computer dev chain.";
      } else if (walletAppState.hookState === "stabilizing") {
        walletAppState.providerState = "wallet returned; stabilizing ethers provider state";
        walletAppState.lastAction = "Waiting for stable ethers account + network on the expected dev chain.";
      } else if (walletAppState.hookState === "disconnecting") {
        walletAppState.providerState = "force disconnecting";
        walletAppState.lastAction = "Revoking wallet permission and clearing local state.";
      } else if (!walletEthersReady()) {
        walletAppState.providerState = "ethers.js missing";
        walletAppState.lastAction = "ethers.js BrowserProvider did not load.";
      } else if (!walletAppState.lastAction || walletAppState.lastAction === "none") {
        walletAppState.providerState = walletProviderAvailable() ? "ethers provider available; not connected" : "browser wallet provider missing";
        walletAppState.lastAction = "not connected";
      } else {
        walletAppState.providerState = walletProviderAvailable() ? "ethers provider available; not connected" : "browser wallet provider missing";
      }
    }

    function renderWalletApp() {
      walletEnsureStateShape();
      walletSyncDisplayState();

      const wallet = walletAppState.wallet;
      const connected = Boolean(wallet.connected && wallet.address);
      const busy = ["requesting", "switching-chain", "stabilizing", "disconnecting"].includes(walletAppState.hookState);

      if (walletStatusPill) {
        walletStatusPill.textContent = connected
          ? "connected"
          : walletAppState.hookState === "requesting"
            ? "wallet open"
            : walletAppState.hookState === "switching-chain"
              ? "switching chain"
              : walletAppState.hookState === "stabilizing"
                ? "stabilizing"
                : walletAppState.hookState === "disconnecting"
                  ? "disconnecting"
                  : "ethers wallet hook";
      }

      if (walletHookState) walletHookState.textContent = walletAppState.hookState;
      if (walletProviderState) walletProviderState.textContent = walletAppState.providerState;
      if (walletLastAction) walletLastAction.textContent = walletAppState.lastAction;

      if (walletConnectButton) {
        walletConnectButton.disabled = busy || connected;
        walletConnectButton.textContent = connected
          ? "Connected"
          : walletAppState.hookState === "requesting"
            ? "Wallet Open…"
            : walletAppState.hookState === "switching-chain"
              ? "Switching Chain…"
              : walletAppState.hookState === "stabilizing"
                ? "Finalizing…"
                : "Connect Wallet";

        if (["requesting", "switching-chain", "stabilizing"].includes(walletAppState.hookState)) {
          walletConnectButton.setAttribute("aria-busy", "true");
        } else {
          walletConnectButton.removeAttribute("aria-busy");
        }
      }

      if (walletDisconnectButton) {
        walletDisconnectButton.disabled = walletAppState.hookState === "disconnecting";
        walletDisconnectButton.textContent = walletAppState.hookState === "disconnecting"
          ? "Disconnecting…"
          : connected
            ? "Disconnect Wallet"
            : "Force Disconnect / Reset";

        if (walletAppState.hookState === "disconnecting") {
          walletDisconnectButton.setAttribute("aria-busy", "true");
        } else {
          walletDisconnectButton.removeAttribute("aria-busy");
        }
      }

      const grantState = walletAppState.agentCredits;
      const grantBusy = grantState.status === "submitting";

      if (walletAgentCreditHubUrl && !walletAgentCreditHubUrl.value) {
        walletAgentCreditHubUrl.value = grantState.hubUrl || WALLET_AGENT_CREDIT_DEFAULT_HUB_URL;
      }
      if (walletAgentCreditMemo && !walletAgentCreditMemo.value) {
        walletAgentCreditMemo.value = WALLET_AGENT_CREDIT_DEFAULT_MEMO;
      }
      if (walletAgentCreditStatus) {
        walletAgentCreditStatus.textContent = grantState.statusMessage;
      }
      if (walletAgentCreditLastGrant) {
        const last = grantState.lastGrant;
        walletAgentCreditLastGrant.textContent = last
          ? `${last.credits} credits to ${walletShortAddress(last.recipient_wallet || last.account_id || "")}`
          : "none";
      }
      if (walletAgentCreditGrantButton) {
        walletAgentCreditGrantButton.disabled = grantBusy || !connected;
        walletAgentCreditGrantButton.textContent = grantBusy
          ? "Granting…"
          : connected
            ? "Grant Helper Credits"
            : "Connect Wallet First";
        if (grantBusy) {
          walletAgentCreditGrantButton.setAttribute("aria-busy", "true");
        } else {
          walletAgentCreditGrantButton.removeAttribute("aria-busy");
        }
      }
      if (walletAgentCreditList) {
        walletAgentCreditList.innerHTML = "";
        const grants = Array.isArray(grantState.grants) ? grantState.grants.slice(0, 6) : [];
        if (!grants.length) {
          const item = document.createElement("li");
          item.textContent = "No helper credit grants yet.";
          walletAgentCreditList.appendChild(item);
        } else {
          grants.forEach((grant) => {
            const item = document.createElement("li");
            const created = String(grant.created_at || grant.createdAt || "").replace("T", " ").slice(0, 19);
            item.textContent = `${created || "recent"} · ${grant.credits} credits · ${walletShortAddress(grant.recipient_wallet || grant.account_id || "")}`;
            walletAgentCreditList.appendChild(item);
          });
        }
      }

      const dnsState = walletAppState.dnsControl;
      const dnsBusy = dnsState.status === "saving" || dnsState.status === "loading";

      if (walletDnsControlMode) walletDnsControlMode.value = dnsState.providerMode || "cloudflare";
      if (walletDnsControlZone && !walletDnsControlZone.dataset.walletUserEdited) walletDnsControlZone.value = dnsState.zone || "";
      if (walletDnsControlRecordName && !walletDnsControlRecordName.dataset.walletUserEdited) walletDnsControlRecordName.value = dnsState.recordName || "@";
      if (walletDnsControlRecordType) walletDnsControlRecordType.value = dnsState.recordType || "A";
      if (walletDnsControlRecordValue && !walletDnsControlRecordValue.dataset.walletUserEdited) walletDnsControlRecordValue.value = dnsState.recordValue || "";
      if (walletDnsControlTtl && !walletDnsControlTtl.dataset.walletUserEdited) walletDnsControlTtl.value = String(dnsState.ttl || 300);
      if (walletDnsControlProxied) walletDnsControlProxied.checked = Boolean(dnsState.proxied);
      if (walletDnsControlNameserver && !walletDnsControlNameserver.dataset.walletUserEdited) walletDnsControlNameserver.value = dnsState.nameserverHost || "";
      if (walletDnsControlAdminUrl && !walletDnsControlAdminUrl.dataset.walletUserEdited) walletDnsControlAdminUrl.value = dnsState.adminUrl || "";

      if (walletDnsControlStatus) {
        walletDnsControlStatus.textContent = dnsState.statusMessage;
      }
      if (walletDnsControlLastProfile) {
        const last = dnsState.lastProfile;
        walletDnsControlLastProfile.textContent = last
          ? `${walletDnsModeLabel(last.provider_mode)} · ${last.zone} · ${last.record_name || "@"} ${last.record_type || "A"}`
          : "none";
      }
      if (walletDnsControlSaveButton) {
        walletDnsControlSaveButton.disabled = dnsBusy || !connected;
        walletDnsControlSaveButton.textContent = dnsBusy
          ? "Saving…"
          : connected
            ? "Save DNS Control Profile"
            : "Connect Wallet First";
        if (dnsBusy) {
          walletDnsControlSaveButton.setAttribute("aria-busy", "true");
        } else {
          walletDnsControlSaveButton.removeAttribute("aria-busy");
        }
      }
      if (walletDnsControlRefreshButton) {
        walletDnsControlRefreshButton.disabled = dnsBusy;
        walletDnsControlRefreshButton.textContent = dnsBusy ? "Refreshing…" : "Refresh DNS Profiles";
      }
      if (walletDnsControlList) {
        walletDnsControlList.innerHTML = "";
        const profiles = Array.isArray(dnsState.profiles) ? dnsState.profiles.slice(0, 6) : [];
        if (!profiles.length) {
          const item = document.createElement("li");
          item.textContent = "No DNS control profiles yet.";
          walletDnsControlList.appendChild(item);
        } else {
          profiles.forEach((profile) => {
            const item = document.createElement("li");
            const created = String(profile.created_at || profile.createdAt || "").replace("T", " ").slice(0, 19);
            item.textContent = `${created || "recent"} · ${walletDnsModeLabel(profile.provider_mode)} · ${profile.zone} · ${profile.record_name || "@"} ${profile.record_type || "A"}`;
            walletDnsControlList.appendChild(item);
          });
        }
      }

      if (walletEventLog) {
        walletEventLog.innerHTML = "";
        const events = walletAppState.events.length
          ? walletAppState.events
          : [{at: walletNowIso(), type: "wallet.app.ready", detail: {}}];
        events.slice(0, 35).forEach((event) => {
          const item = document.createElement("li");
          item.textContent = `${event.at} · ${event.type}`;
          walletEventLog.appendChild(item);
        });
      }
    }

    async function walletProviderSnapshot() {
      const provider = walletBrowserProvider();
      const [accounts, chainId] = await Promise.all([
        walletListAccountAddresses(provider),
        walletNetworkChainId(provider)
      ]);

      return {
        accounts,
        address: accounts[0] || "",
        chainId
      };
    }

    async function walletEnsureExpectedChain(token) {
      const beforeChainId = await walletNetworkChainId();

      if (walletChainMatchesExpected(beforeChainId)) {
        walletRecordEvent("ethers.chain.ready", {
          chainId: beforeChainId,
          expectedChainId: WALLET_DEV_CHAIN_ID_HEX
        });
        return beforeChainId;
      }

      walletSetHookState(
        "switching-chain",
        `Switch browser wallet to ${WALLET_DEV_CHAIN_NAME} (${WALLET_DEV_CHAIN_ID_HEX}).`
      );
      walletRecordEvent("ethers.chain.switch.start", {
        fromChainId: beforeChainId,
        expectedChainId: WALLET_DEV_CHAIN_ID_HEX
      });

      try {
        await walletBrowserProvider().send("wallet_switchEthereumChain", [{chainId: WALLET_DEV_CHAIN_ID_HEX}]);
      } catch (error) {
        if (walletErrorCode(error) === 4902) {
          walletRecordEvent("ethers.chain.add.start", {
            expectedChainId: WALLET_DEV_CHAIN_ID_HEX,
            rpcUrl: WALLET_DEV_CHAIN_RPC_URL
          });
          await walletBrowserProvider().send("wallet_addEthereumChain", [{
            chainId: WALLET_DEV_CHAIN_ID_HEX,
            chainName: WALLET_DEV_CHAIN_NAME,
            nativeCurrency: {
              name: WALLET_DEV_CHAIN_CURRENCY_NAME,
              symbol: WALLET_DEV_CHAIN_CURRENCY_SYMBOL,
              decimals: 18
            },
            rpcUrls: [WALLET_DEV_CHAIN_RPC_URL]
          }]);
        } else {
          throw new Error(
            `Switch browser wallet to ${WALLET_DEV_CHAIN_NAME} (${WALLET_DEV_CHAIN_ID_HEX}) before connecting. ${walletErrorMessage(error)}`
          );
        }
      }

      if (token !== undefined && token !== null && !walletOperationIsCurrent(token)) {
        throw new Error("Chain switch result ignored because Force Disconnect / Reset was clicked.");
      }

      const confirmedChainId = await walletNetworkChainId();
      if (!walletChainMatchesExpected(confirmedChainId)) {
        throw new Error(
          `Wallet is on ${confirmedChainId || "unknown chain"}; expected ${WALLET_DEV_CHAIN_ID_HEX}.`
        );
      }

      walletRecordEvent("ethers.chain.ready", {
        chainId: confirmedChainId,
        expectedChainId: WALLET_DEV_CHAIN_ID_HEX
      });
      return confirmedChainId;
    }

    async function walletWaitForStableProvider(token) {
      walletSetHookState("stabilizing", "Wallet returned. Waiting for stable ethers provider state.");

      const started = Date.now();
      let previousKey = "";
      let stableCount = 0;
      let lastGood = null;

      while (walletOperationIsCurrent(token) && Date.now() - started < 20000) {
        const snapshot = await walletProviderSnapshot();
        const address = walletValidAddress(snapshot.address) ? snapshot.address : "";
        const chainId = walletNormalizeChainIdHex(snapshot.chainId);
        const chainMatchesExpected = walletChainMatchesExpected(chainId);
        const key = `${address.toLowerCase()}|${chainId.toLowerCase()}`;

        walletRecordEvent("ethers.sample.after-connect", {
          address: address ? walletShortAddress(address) : "",
          chainId,
          expectedChainId: WALLET_DEV_CHAIN_ID_HEX,
          chainMatchesExpected,
          stableCount
        });

        if (address && chainMatchesExpected && key === previousKey) {
          stableCount += 1;
        } else {
          stableCount = address && chainMatchesExpected ? 1 : 0;
          previousKey = key;
        }

        if (address && chainMatchesExpected) {
          lastGood = {...snapshot, address, chainId};
        }

        if (stableCount >= 3) {
          return {...snapshot, address, chainId};
        }

        await walletSleep(350);
      }

      if (!walletOperationIsCurrent(token)) {
        throw new Error("Connect was cancelled by Force Disconnect / Reset.");
      }

      if (lastGood) {
        walletRecordEvent("ethers.stability.timeout.using-last-good", {
          address: walletShortAddress(lastGood.address),
          chainId: lastGood.chainId
        });
        return lastGood;
      }

      throw new Error(
        `Timed out waiting for ethers to expose a stable account on ${WALLET_DEV_CHAIN_ID_HEX}.`
      );
    }

    async function requestWalletConnectHook(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      event?.stopImmediatePropagation?.();

      walletEnsureStateShape();

      try {
        walletBrowserProvider();
      } catch (error) {
        walletSetWalletState({connected: false});
        walletSetHookState("idle", error.message || String(error));
        walletRecordEvent("connect.failed.no-ethers-provider", {
          message: error && typeof error === "object" ? error.message || String(error) : String(error)
        });
        return;
      }

      if (walletAppState.hookState !== "idle") {
        walletRecordEvent("connect.ignored.busy", {phase: walletAppState.hookState});
        return;
      }

      const token = walletNextOperation();

      walletSetWalletState({connected: false});
      walletSetHookState("requesting", "Browser wallet opened through ethers. No wallet state will be accepted until getSigner resolves.");

      try {
        walletRecordEvent("connect.ethers.getSigner.start");

        const signer = await walletBrowserProvider().getSigner();
        const signerAddress = await signer.getAddress();

        if (!walletOperationIsCurrent(token)) {
          throw new Error("Connect result ignored because Force Disconnect / Reset was clicked.");
        }

        walletRecordEvent("connect.ethers.getSigner.resolved", {
          address: walletShortAddress(signerAddress)
        });

        await walletEnsureExpectedChain(token);

        const finalSnapshot = await walletWaitForStableProvider(token);

        if (!walletOperationIsCurrent(token)) {
          throw new Error("Connect finalization ignored because Force Disconnect / Reset was clicked.");
        }

        walletSetWalletState({
          connected: true,
          address: finalSnapshot.address,
          chainId: finalSnapshot.chainId
        });
        walletSetHookState("connected", `Connected ${walletShortAddress(finalSnapshot.address)} on ${finalSnapshot.chainId}.`);
        walletRecordEvent("connect.finalized.connected", {
          address: finalSnapshot.address,
          chainId: finalSnapshot.chainId
        });
      } catch (error) {
        if (walletOperationIsCurrent(token)) {
          walletSetWalletState({connected: false});
          walletSetHookState("idle", `Connect failed: ${walletErrorMessage(error)}`);
          walletRecordEvent("connect.failed", {
            code: walletErrorCode(error),
            message: walletErrorMessage(error)
          });
        }
      }
    }

    async function hydrateWalletAgentCreditGrants() {
      walletEnsureStateShape();

      try {
        const response = await fetch(WALLET_AGENT_CREDIT_GRANT_ENDPOINT, {
          headers: {"Accept": "application/json"}
        });
        const payload = await response.json();
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        walletAppState.agentCredits.hubUrl = walletNormalizeHubUrl(payload.hub_url || walletAppState.agentCredits.hubUrl);
        walletAppState.agentCredits.grants = Array.isArray(payload.grants) ? payload.grants.slice(0, 20) : [];
        walletAppState.agentCredits.lastGrant = walletAppState.agentCredits.grants[0] || walletAppState.agentCredits.lastGrant;
        if (walletAgentCreditHubUrl && !walletAgentCreditHubUrl.dataset.walletUserEdited) {
          walletAgentCreditHubUrl.value = walletAppState.agentCredits.hubUrl;
        }
        renderWalletApp();
      } catch (error) {
        walletAppState.agentCredits.status = "idle";
        walletAppState.agentCredits.statusMessage = `Grant history unavailable: ${walletErrorMessage(error)}`;
        renderWalletApp();
      }
    }

    async function requestWalletAgentCreditGrant(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();

      walletEnsureStateShape();

      const issuerWallet = walletNormalizeAccountAddress(walletAppState.wallet.address);
      const recipientWallet = walletNormalizeAccountAddress(walletAgentCreditRecipient?.value);
      const credits = walletNormalizeGrantCredits(walletAgentCreditAmount?.value);
      const hubUrl = walletNormalizeHubUrl(walletAgentCreditHubUrl?.value);
      const memo = String(walletAgentCreditMemo?.value || WALLET_AGENT_CREDIT_DEFAULT_MEMO).trim() || WALLET_AGENT_CREDIT_DEFAULT_MEMO;

      if (!issuerWallet) {
        walletAppState.agentCredits.status = "blocked";
        walletAppState.agentCredits.statusMessage = "Connect the primary wallet before granting helper credits.";
        walletRecordEvent("agent-credit-grant.blocked.not-connected");
        return;
      }
      if (!recipientWallet) {
        walletAppState.agentCredits.status = "blocked";
        walletAppState.agentCredits.statusMessage = "Enter a valid 0x recipient wallet for the helper user.";
        walletRecordEvent("agent-credit-grant.blocked.invalid-recipient");
        return;
      }
      if (credits < 1 || credits > WALLET_AGENT_CREDIT_MAX_GRANT) {
        walletAppState.agentCredits.status = "blocked";
        walletAppState.agentCredits.statusMessage = `Credits must be between 1 and ${WALLET_AGENT_CREDIT_MAX_GRANT}.`;
        walletRecordEvent("agent-credit-grant.blocked.invalid-credits", {credits});
        return;
      }

      walletAppState.agentCredits.status = "submitting";
      walletAppState.agentCredits.statusMessage = `Granting ${credits} helper credits to ${walletShortAddress(recipientWallet)}…`;
      renderWalletApp();

      try {
        const response = await fetch(WALLET_AGENT_CREDIT_GRANT_ENDPOINT, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            hub_url: hubUrl,
            issuer_wallet: issuerWallet,
            recipient_wallet: recipientWallet,
            credits,
            memo
          })
        });
        const payload = await response.json();
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }

        const grant = payload.grant || {
          created_at: walletNowIso(),
          issuer_wallet: issuerWallet,
          recipient_wallet: recipientWallet,
          credits,
          memo
        };
        walletAppState.agentCredits.hubUrl = walletNormalizeHubUrl(payload.hub_url || hubUrl);
        walletAppState.agentCredits.status = "issued";
        walletAppState.agentCredits.statusMessage = `Granted ${credits} helper credits to ${walletShortAddress(recipientWallet)}.`;
        walletAppState.agentCredits.lastGrant = grant;
        walletAppState.agentCredits.grants = [grant, ...walletAppState.agentCredits.grants].slice(0, 20);
        walletRecordEvent("agent-credit-grant.issued", {
          recipient: walletShortAddress(recipientWallet),
          credits,
          hubUrl: walletAppState.agentCredits.hubUrl
        });
      } catch (error) {
        walletAppState.agentCredits.status = "failed";
        walletAppState.agentCredits.statusMessage = `Grant failed: ${walletErrorMessage(error)}`;
        walletRecordEvent("agent-credit-grant.failed", {
          message: walletErrorMessage(error)
        });
      } finally {
        renderWalletApp();
      }
    }

    async function hydrateWalletDnsControlProfiles() {
      walletEnsureStateShape();

      walletAppState.dnsControl.status = "loading";
      walletAppState.dnsControl.statusMessage = "Loading wallet DNS control profiles…";
      renderWalletApp();

      try {
        const response = await fetch(WALLET_DNS_CONTROL_ENDPOINT, {
          headers: {"Accept": "application/json"}
        });
        const payload = await response.json();
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }
        walletAppState.dnsControl.status = "idle";
        walletAppState.dnsControl.statusMessage = payload.status_message || WALLET_DNS_CONTROL_DEFAULT_STATUS;
        walletAppState.dnsControl.profiles = Array.isArray(payload.profiles) ? payload.profiles.slice(0, 20) : [];
        walletAppState.dnsControl.lastProfile = walletAppState.dnsControl.profiles[0] || walletAppState.dnsControl.lastProfile;
        const defaults = payload.defaults && typeof payload.defaults === "object" ? payload.defaults : {};
        walletAppState.dnsControl.providerMode = walletNormalizeDnsMode(defaults.provider_mode || walletAppState.dnsControl.providerMode);
        walletAppState.dnsControl.ttl = walletNormalizeDnsTtl(defaults.ttl || walletAppState.dnsControl.ttl);
        walletRecordEvent("dns-control-profiles.loaded", {
          count: walletAppState.dnsControl.profiles.length
        });
      } catch (error) {
        walletAppState.dnsControl.status = "failed";
        walletAppState.dnsControl.statusMessage = `DNS profiles unavailable: ${walletErrorMessage(error)}`;
        walletRecordEvent("dns-control-profiles.failed", {
          message: walletErrorMessage(error)
        });
      } finally {
        renderWalletApp();
      }
    }

    async function requestWalletDnsControlSave(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();

      walletEnsureStateShape();

      const ownerWallet = walletNormalizeAccountAddress(walletAppState.wallet.address);
      const providerMode = walletNormalizeDnsMode(walletDnsControlMode?.value);
      const zone = walletNormalizeDnsText(walletDnsControlZone?.value);
      const recordName = walletNormalizeDnsText(walletDnsControlRecordName?.value, "@");
      const recordType = walletNormalizeDnsRecordType(walletDnsControlRecordType?.value);
      const recordValue = walletNormalizeDnsText(walletDnsControlRecordValue?.value);
      const ttl = walletNormalizeDnsTtl(walletDnsControlTtl?.value);
      const proxied = Boolean(walletDnsControlProxied?.checked);
      const nameserverHost = walletNormalizeDnsText(walletDnsControlNameserver?.value);
      const adminUrl = walletNormalizeDnsText(walletDnsControlAdminUrl?.value);

      if (!ownerWallet) {
        walletAppState.dnsControl.status = "blocked";
        walletAppState.dnsControl.statusMessage = "Connect the primary wallet before saving DNS control.";
        walletRecordEvent("dns-control.blocked.not-connected");
        return;
      }
      if (!zone) {
        walletAppState.dnsControl.status = "blocked";
        walletAppState.dnsControl.statusMessage = "Enter the DNS zone or domain to control.";
        walletRecordEvent("dns-control.blocked.missing-zone");
        return;
      }
      if (!recordValue) {
        walletAppState.dnsControl.status = "blocked";
        walletAppState.dnsControl.statusMessage = "Enter the DNS record value to publish or stage.";
        walletRecordEvent("dns-control.blocked.missing-record-value");
        return;
      }
      if (providerMode === "self-hosted" && !nameserverHost && !adminUrl) {
        walletAppState.dnsControl.status = "blocked";
        walletAppState.dnsControl.statusMessage = "Self-hosted DNS needs an authoritative nameserver or admin URL.";
        walletRecordEvent("dns-control.blocked.self-hosted-target");
        return;
      }

      walletAppState.dnsControl.status = "saving";
      walletAppState.dnsControl.statusMessage = `Saving ${walletDnsModeLabel(providerMode)} control profile for ${zone}…`;
      renderWalletApp();

      try {
        const response = await fetch(WALLET_DNS_CONTROL_ENDPOINT, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            owner_wallet: ownerWallet,
            provider_mode: providerMode,
            zone,
            record_name: recordName,
            record_type: recordType,
            record_value: recordValue,
            ttl,
            proxied,
            nameserver_host: nameserverHost,
            admin_url: adminUrl
          })
        });
        const payload = await response.json();
        if (!response.ok || payload?.ok === false) {
          throw new Error(payload?.error || `HTTP ${response.status}`);
        }

        const profile = payload.profile || {
          created_at: walletNowIso(),
          owner_wallet: ownerWallet,
          provider_mode: providerMode,
          zone,
          record_name: recordName,
          record_type: recordType,
          record_value: recordValue,
          ttl,
          proxied,
          nameserver_host: nameserverHost,
          admin_url: adminUrl
        };
        walletAppState.dnsControl.status = "saved";
        walletAppState.dnsControl.statusMessage = payload.status_message || `Saved ${walletDnsModeLabel(providerMode)} control profile for ${zone}.`;
        walletAppState.dnsControl.providerMode = providerMode;
        walletAppState.dnsControl.zone = zone;
        walletAppState.dnsControl.recordName = recordName;
        walletAppState.dnsControl.recordType = recordType;
        walletAppState.dnsControl.recordValue = recordValue;
        walletAppState.dnsControl.ttl = ttl;
        walletAppState.dnsControl.proxied = proxied;
        walletAppState.dnsControl.nameserverHost = nameserverHost;
        walletAppState.dnsControl.adminUrl = adminUrl;
        walletAppState.dnsControl.lastProfile = profile;
        walletAppState.dnsControl.profiles = [profile, ...walletAppState.dnsControl.profiles].slice(0, 20);
        walletRecordEvent("dns-control-profile.saved", {
          mode: walletDnsModeLabel(providerMode),
          zone,
          record: `${recordName} ${recordType}`
        });
      } catch (error) {
        walletAppState.dnsControl.status = "failed";
        walletAppState.dnsControl.statusMessage = `DNS control save failed: ${walletErrorMessage(error)}`;
        walletRecordEvent("dns-control-profile.failed", {
          message: walletErrorMessage(error)
        });
      } finally {
        renderWalletApp();
      }
    }

    async function requestWalletDisconnectHook(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      event?.stopImmediatePropagation?.();

      const token = walletNextOperation();

      walletSetWalletState({connected: false});
      walletSetHookState("disconnecting", "Force disconnect/reset requested.");

      let revoked = false;

      try {
        if (walletProviderAvailable() && walletEthersReady()) {
          try {
            walletRecordEvent("disconnect.ethers.revokePermissions.start");
            await walletBrowserProvider().send("wallet_revokePermissions", [{eth_accounts: {}}]);
            revoked = true;
            walletRecordEvent("disconnect.ethers.revokePermissions.done");
          } catch (error) {
            walletRecordEvent("disconnect.ethers.revokePermissions.failed", {
              code: walletErrorCode(error),
              message: walletErrorMessage(error)
            });
          }
        }
      } finally {
        if (walletOperationIsCurrent(token)) {
          walletSetWalletState({connected: false});
          walletSetHookState(
            "idle",
            revoked
              ? "Disconnected and wallet permission revoked through ethers."
              : "Local state cleared. If the wallet still has a pending popup, close it manually."
          );
          walletRecordEvent("disconnect.done", {revoked});
        }
      }
    }

    function resetWalletHookLog(event) {
      event?.preventDefault?.();
      event?.stopPropagation?.();
      event?.stopImmediatePropagation?.();

      walletAppState.events = [];
      walletSetHookState("idle", "reset-log");
      walletRecordEvent("wallet.log.reset");
    }

    async function walletRefreshProviderStateAfterEvent(source, detail = {}) {
      if (["requesting", "switching-chain", "stabilizing", "disconnecting"].includes(walletAppState.hookState)) {
        walletRecordEvent(`ethers.${source}.refresh.skipped.busy`, {
          ...detail,
          phase: walletAppState.hookState
        });
        return;
      }

      try {
        const snapshot = await walletProviderSnapshot();
        const address = walletValidAddress(snapshot.address) ? snapshot.address : "";
        const chainId = walletNormalizeChainIdHex(snapshot.chainId);

        if (address && walletChainMatchesExpected(chainId)) {
          walletSetWalletState({connected: true, address, chainId});
          walletSetHookState(
            "connected",
            `Provider ${source}: connected ${walletShortAddress(address)} on ${chainId}.`
          );
          walletRecordEvent(`ethers.${source}.refreshed.connected`, {
            address,
            chainId,
            expectedChainId: WALLET_DEV_CHAIN_ID_HEX
          });
          return;
        }

        walletSetWalletState({connected: false});
        walletSetHookState(
          "idle",
          address
            ? `Provider ${source}: wallet is on ${chainId || "unknown chain"}, expected ${WALLET_DEV_CHAIN_ID_HEX}.`
            : `Provider ${source}: no connected account.`
        );
        walletRecordEvent(`ethers.${source}.refreshed.cleared`, {
          address: address ? walletShortAddress(address) : "",
          chainId,
          expectedChainId: WALLET_DEV_CHAIN_ID_HEX
        });
      } catch (error) {
        walletSetWalletState({connected: false});
        walletSetHookState("idle", `Provider ${source}: refresh failed: ${walletErrorMessage(error)}`);
        walletRecordEvent(`ethers.${source}.refresh.failed`, {
          code: walletErrorCode(error),
          message: walletErrorMessage(error)
        });
      }
    }

    function walletHandleAccountsChanged(accounts) {
      walletRecordEvent("wallet.accountsChanged.observed", {accounts});
      void walletRefreshProviderStateAfterEvent("accountsChanged", {accounts});
    }

    function walletHandleChainChanged(chainId) {
      const normalizedChainId = walletNormalizeChainIdHex(chainId);
      walletRecordEvent("wallet.chainChanged.observed", {
        chainId: normalizedChainId,
        expectedChainId: WALLET_DEV_CHAIN_ID_HEX
      });
      void walletRefreshProviderStateAfterEvent("chainChanged", {chainId: normalizedChainId});
    }

    function walletBindProviderEvents() {
      const injected = window.ethereum;
      if (walletProviderEventsBound || !injected?.on) return;
      walletProviderEventsBound = true;
      injected.on("accountsChanged", walletHandleAccountsChanged);
      injected.on("chainChanged", walletHandleChainChanged);
    }

    function walletTextToHex(value) {
      const bytes = new TextEncoder().encode(String(value || ""));
      return `0x${Array.from(bytes).map((byte) => byte.toString(16).padStart(2, "0")).join("")}`;
    }

    function walletBuildMultiSessionKeyMessage({walletAddress = "", chainId = "", origin = ""} = {}) {
      const now = walletNowIso();
      const expiresAt = new Date(Date.now() + 10 * 60 * 1000).toISOString();
      const requestEntropy = window.crypto && typeof window.crypto.randomUUID === "function"
        ? window.crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
      return {
        purpose: "request_multi_session_key",
        request_id: `msk_req_${requestEntropy}`,
        wallet_address: walletAddress,
        chain_id: walletNormalizeChainIdHex(chainId),
        origin: String(origin || window.location?.origin || "main-computer-worker"),
        issued_at: now,
        expires_at: expiresAt,
        version: "main-computer-multisession-key-request-v1"
      };
    }

    async function requestMultiSessionKeySignature({requestContext = {}, origin = ""} = {}) {
      const provider = walletBrowserProvider();
      const signer = await provider.getSigner();
      const walletAddress = await signer.getAddress();
      await walletEnsureExpectedChain();

      const chainId = await walletNetworkChainId(provider);
      const normalizedChainId = walletNormalizeChainIdHex(chainId);
      const expectedWallet = String(requestContext.wallet_address || requestContext.walletAddress || "").trim();

      if (!walletValidAddress(walletAddress)) {
        throw new Error("Browser wallet did not provide a valid 0x address.");
      }
      if (expectedWallet && expectedWallet.toLowerCase() !== walletAddress.toLowerCase()) {
        throw new Error(`Connected wallet ${walletShortAddress(walletAddress)} does not match Worker request ${walletShortAddress(expectedWallet)}.`);
      }
      if (!walletChainMatchesExpected(normalizedChainId)) {
        throw new Error(`Wallet is on ${normalizedChainId || "unknown chain"}; expected ${WALLET_DEV_CHAIN_ID_HEX}.`);
      }

      const message = walletBuildMultiSessionKeyMessage({
        walletAddress,
        chainId: normalizedChainId,
        origin
      });
      const messageText = JSON.stringify(message);
      const signature = await signer.signMessage(messageText);

      return {
        kind: "main_computer_multisession_key_request",
        signing_method: "personal_sign",
        wallet_address: walletAddress,
        chain_id: normalizedChainId,
        message,
        message_text: messageText,
        message_hex: walletTextToHex(messageText),
        signature
      };
    }

    window.MainComputerWalletLibrary = {
      ...(window.MainComputerWalletLibrary || {}),
      requestMultiSessionKeySignature,
      buildMultiSessionKeyMessage: walletBuildMultiSessionKeyMessage,
      constants: {
        devChainIdHex: WALLET_DEV_CHAIN_ID_HEX,
        purpose: "request_multi_session_key",
        kind: "main_computer_multisession_key_request"
      }
    };

    function initWalletApp() {
      if (!walletApp) return;
      walletEnsureStateShape();

      if (!walletAppState.initialized) {
        walletAppState.initialized = true;
        walletSetWalletState({connected: false});
        walletRecordEvent("wallet.app.initialized", {
          note: "Standalone Wallet app loaded with ethers BrowserProvider connect/disconnect finalization."
        });
      } else {
        renderWalletApp();
      }

      if (walletConnectButton && !walletConnectButton.dataset.walletBound) {
        walletConnectButton.dataset.walletBound = "true";
        walletConnectButton.addEventListener("click", requestWalletConnectHook, true);
      }
      if (walletDisconnectButton && !walletDisconnectButton.dataset.walletBound) {
        walletDisconnectButton.dataset.walletBound = "true";
        walletDisconnectButton.addEventListener("click", requestWalletDisconnectHook, true);
      }
      if (walletResetLogButton && !walletResetLogButton.dataset.walletBound) {
        walletResetLogButton.dataset.walletBound = "true";
        walletResetLogButton.addEventListener("click", resetWalletHookLog, true);
      }
      if (walletAgentCreditForm && !walletAgentCreditForm.dataset.walletBound) {
        walletAgentCreditForm.dataset.walletBound = "true";
        walletAgentCreditForm.addEventListener("submit", requestWalletAgentCreditGrant, true);
      }
      if (walletAgentCreditHubUrl && !walletAgentCreditHubUrl.dataset.walletBound) {
        walletAgentCreditHubUrl.dataset.walletBound = "true";
        walletAgentCreditHubUrl.addEventListener("input", () => {
          walletAgentCreditHubUrl.dataset.walletUserEdited = "true";
        });
      }
      if (walletDnsControlForm && !walletDnsControlForm.dataset.walletBound) {
        walletDnsControlForm.dataset.walletBound = "true";
        walletDnsControlForm.addEventListener("submit", requestWalletDnsControlSave, true);
      }
      if (walletDnsControlRefreshButton && !walletDnsControlRefreshButton.dataset.walletBound) {
        walletDnsControlRefreshButton.dataset.walletBound = "true";
        walletDnsControlRefreshButton.addEventListener("click", hydrateWalletDnsControlProfiles, true);
      }
      [
        walletDnsControlZone,
        walletDnsControlRecordName,
        walletDnsControlRecordValue,
        walletDnsControlTtl,
        walletDnsControlNameserver,
        walletDnsControlAdminUrl
      ].forEach((field) => {
        if (field && !field.dataset.walletBound) {
          field.dataset.walletBound = "true";
          field.addEventListener("input", () => {
            field.dataset.walletUserEdited = "true";
          });
        }
      });

      hydrateWalletAgentCreditGrants();
      hydrateWalletDnsControlProfiles();
      walletBindProviderEvents();
      renderWalletApp();

      window.MainComputerWalletApp = {
        state: walletAppState,
        render: renderWalletApp,
        recordEvent: walletRecordEvent,
        requestConnect: requestWalletConnectHook,
        requestDisconnect: requestWalletDisconnectHook,
        resetLog: resetWalletHookLog,
        providerSnapshot: walletProviderSnapshot,
        ensureExpectedChain: walletEnsureExpectedChain,
        refreshProviderState: walletRefreshProviderStateAfterEvent,
        waitForStableProvider: walletWaitForStableProvider,
        hydrateAgentCreditGrants: hydrateWalletAgentCreditGrants,
        requestAgentCreditGrant: requestWalletAgentCreditGrant,
        hydrateDnsControlProfiles: hydrateWalletDnsControlProfiles,
        requestDnsControlSave: requestWalletDnsControlSave,
        requestMultiSessionKeySignature,
        buildMultiSessionKeyMessage: walletBuildMultiSessionKeyMessage
      };
    }
