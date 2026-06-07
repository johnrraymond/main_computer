    const emailOauthProviderCatalog = Object.freeze({
      gmail: {
        id: "gmail",
        label: "Gmail",
        domainHints: ["gmail.com", "googlemail.com"],
        authEndpoint: "https://accounts.google.com/o/oauth2/v2/auth",
        tokenEndpoint: "https://oauth2.googleapis.com/token",
        scopes: [
          "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.send"
        ],
        oauthExtras: {
          access_type: "offline",
          include_granted_scopes: "true",
          prompt: "consent"
        }
      },
      yahoo: {
        id: "yahoo",
        label: "Yahoo Mail",
        domainHints: ["yahoo.com", "ymail.com", "rocketmail.com"],
        authEndpoint: "https://api.login.yahoo.com/oauth2/request_auth",
        tokenEndpoint: "https://api.login.yahoo.com/oauth2/get_token",
        scopes: ["openid", "mail-r"],
        oauthExtras: {
          prompt: "consent",
          language: "en-us"
        }
      }
    });

    const emailMailServicePresets = Object.freeze({
      system: {
        id: "system",
        label: "Email setup",
        domainHints: [],
        defaultProtocol: "imap",
        imap: {host: "", port: 993, security: "ssl"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "", port: 587, security: "starttls"},
        authHint: "These are local system messages that explain how to set up the email client."
      },
      custom: {
        id: "custom",
        label: "Custom mail server",
        domainHints: [],
        defaultProtocol: "imap",
        imap: {host: "", port: 993, security: "ssl"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "", port: 587, security: "starttls"},
        authHint: "Use the username, password, app password, or bridge credential supplied by your mail provider."
      },
      gmail: {
        id: "gmail",
        label: "Gmail",
        domainHints: ["gmail.com", "googlemail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.gmail.com", port: 993, security: "ssl"},
        pop3: {host: "pop.gmail.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.gmail.com", port: 587, security: "starttls"},
        authHint: "Gmail usually requires OAuth or an app password for external clients."
      },
      outlook: {
        id: "outlook",
        label: "Outlook.com",
        domainHints: ["outlook.com", "hotmail.com", "live.com", "msn.com"],
        defaultProtocol: "imap",
        imap: {host: "outlook.office365.com", port: 993, security: "ssl"},
        pop3: {host: "outlook.office365.com", port: 995, security: "ssl"},
        smtp: {host: "smtp-mail.outlook.com", port: 587, security: "starttls"},
        authHint: "Enable POP or IMAP access in Outlook.com settings before checking mail."
      },
      microsoft365: {
        id: "microsoft365",
        label: "Microsoft 365 / Exchange Online",
        domainHints: [],
        defaultProtocol: "imap",
        imap: {host: "outlook.office365.com", port: 993, security: "ssl"},
        pop3: {host: "outlook.office365.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.office365.com", port: 587, security: "starttls"},
        authHint: "Tenant policy may require OAuth or app-password/SMTP AUTH enablement."
      },
      yahoo: {
        id: "yahoo",
        label: "Yahoo Mail",
        domainHints: ["yahoo.com", "ymail.com", "rocketmail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.mail.yahoo.com", port: 993, security: "ssl"},
        pop3: {host: "pop.mail.yahoo.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.mail.yahoo.com", port: 465, security: "ssl"},
        authHint: "Yahoo third-party clients typically use an app password or OAuth handoff."
      },
      icloud: {
        id: "icloud",
        label: "iCloud Mail",
        domainHints: ["icloud.com", "me.com", "mac.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.mail.me.com", port: 993, security: "ssl"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "smtp.mail.me.com", port: 587, security: "starttls"},
        authHint: "iCloud Mail uses app-specific passwords for many third-party clients."
      },
      aol: {
        id: "aol",
        label: "AOL Mail",
        domainHints: ["aol.com", "verizon.net"],
        defaultProtocol: "imap",
        imap: {host: "imap.aol.com", port: 993, security: "ssl"},
        pop3: {host: "pop.aol.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.aol.com", port: 465, security: "ssl"},
        authHint: "AOL Mail uses SSL/TLS and may require app-password access."
      },
      fastmail: {
        id: "fastmail",
        label: "Fastmail",
        domainHints: ["fastmail.com", "fastmail.fm"],
        defaultProtocol: "imap",
        imap: {host: "imap.fastmail.com", port: 993, security: "ssl"},
        pop3: {host: "pop.fastmail.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.fastmail.com", port: 465, security: "ssl"},
        authHint: "Fastmail recommends app-specific passwords for external mail clients."
      },
      zoho: {
        id: "zoho",
        label: "Zoho Mail",
        domainHints: ["zoho.com", "zohomail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.zoho.com", port: 993, security: "ssl"},
        pop3: {host: "pop.zoho.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.zoho.com", port: 465, security: "ssl"},
        authHint: "Some Zoho accounts use pro/datacenter-specific hosts; check the account's server configuration page when needed."
      },
      protonbridge: {
        id: "protonbridge",
        label: "Proton Mail Bridge",
        domainHints: ["proton.me", "protonmail.com"],
        defaultProtocol: "imap",
        imap: {host: "127.0.0.1", port: 1143, security: "none"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "127.0.0.1", port: 1025, security: "none"},
        authHint: "Proton Mail requires the local Proton Mail Bridge credentials shown inside Bridge."
      }
    });

    const emailStorageKey = "main-computer-email-app-v5";
    const emailCheckEndpoint = "/api/applications/email/check";

    const emailSeedMessages = Object.freeze([
      {
        id: "system-welcome",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Email setup",
        to: "you",
        subject: "Welcome to the Email app",
        excerpt: "Start here to learn how the Email app works before connecting an account.",
        body: "Welcome to the Email app.\n\nThis inbox starts with local system messages instead of fake mail. Click each message to learn how setup, privacy, account checks, composing, and replies work.\n\nWhen you connect a real account from Config, live mailbox previews can replace these local instructions.",
        date: "2026-06-07T16:23:00.000Z",
        labels: ["System"],
        priority: false,
        unread: true
      },
      {
        id: "system-config",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Email setup",
        to: "you",
        subject: "Connect Gmail, Yahoo, or POP/IMAP",
        excerpt: "Use Config to add Gmail, Yahoo, Outlook, iCloud, Fastmail, Zoho, AOL, Proton Bridge, or custom servers.",
        body: "Open Config to connect accounts.\n\nUse the POP/IMAP tab for common mail services such as Outlook.com, Microsoft 365, iCloud Mail, AOL Mail, Fastmail, Zoho Mail, Proton Mail Bridge, Yahoo Mail, Gmail IMAP, or a custom server.\n\nUse the Gmail and Yahoo tabs when you want to stage OAuth handoff details for those providers.",
        date: "2026-06-07T16:22:00.000Z",
        labels: ["Config"],
        priority: false,
        unread: true
      },
      {
        id: "system-check-mail",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Email setup",
        to: "you",
        subject: "How Check mail works",
        excerpt: "The backend bridge can do a one-time POP/IMAP header check without saving the password.",
        body: "Check mail is a one-time backend bridge action.\n\nFor POP/IMAP accounts, enter the password or app password only when you click Check mail. The app sends it to the local backend for that check and does not save or echo it back in browser state.\n\nThe current bridge returns a safe header preview; full sync, attachments, and durable token storage can be layered behind the same account configuration.",
        date: "2026-06-07T16:21:00.000Z",
        labels: ["Privacy"],
        priority: false,
        unread: false
      },
      {
        id: "system-compose-reply",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Email setup",
        to: "you",
        subject: "Compose is a modal; replies stay inline",
        excerpt: "New messages open in a compose modal, while replies appear inside the selected message view.",
        body: "New message opens a compose modal so the mailbox list stays clean.\n\nWhen you select a message and click Reply, the reply editor opens inline below that message. Queue Send stores the draft locally until a provider bridge is ready to deliver it.",
        date: "2026-06-07T16:20:00.000Z",
        labels: ["Compose"],
        priority: false,
        unread: false
      },
      {
        id: "system-local-state",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Email setup",
        to: "you",
        subject: "What is stored locally",
        excerpt: "The app keeps local UI state, staged account settings, and draft queues in browser storage.",
        body: "The Email app keeps local UI state, staged account settings, and drafts in browser storage.\n\nPasswords are not stored by the Email UI. For real sending and syncing, connect a provider bridge that handles OAuth tokens or server credentials on the backend side.",
        date: "2026-06-07T16:19:00.000Z",
        labels: ["Local"],
        priority: false,
        unread: false
      }
    ]);

    const emailSeedAccounts = Object.freeze([]);

    const emailAppState = window.emailAppState || (window.emailAppState = {
      initialized: false,
      activeTab: "mail",
      activeConfigTab: "gmail",
      activeMailView: "list",
      accounts: [],
      messages: [],
      drafts: [],
      selectedAccountId: "all",
      selectedFolder: "inbox",
      selectedMessageId: "",
      search: "",
      replyDraft: "",
      lastHandoffUrl: "",
      lastProvider: "gmail",
      lastSyncAt: "",
      mcel: {
        status: "pending",
        failed: false
      }
    });

    function emailNowIso() {
      return new Date().toISOString();
    }

    function emailFormatTime(value) {
      const date = new Date(value || Date.now());
      if (Number.isNaN(date.getTime())) return String(value || "unknown time");
      const now = new Date();
      if (date.toDateString() === now.toDateString()) {
        return date.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
      }
      if (date.getFullYear() === now.getFullYear()) {
        return date.toLocaleDateString([], {month: "short", day: "numeric"});
      }
      return date.toLocaleDateString([], {month: "short", day: "numeric", year: "2-digit"});
    }

    function emailEscapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
      })[char]);
    }

    function emailPreset(presetId) {
      return emailMailServicePresets[presetId] || emailMailServicePresets.custom;
    }

    function emailOauthProvider(providerId) {
      return emailOauthProviderCatalog[providerId] || emailOauthProviderCatalog.gmail;
    }

    function emailProviderLabel(providerId) {
      return (emailMailServicePresets[providerId] || emailOauthProviderCatalog[providerId] || emailMailServicePresets.custom).label;
    }

    function emailNormalizeAddress(value) {
      return String(value || "").trim().toLowerCase();
    }

    function emailAddressProviderGuess(address) {
      const domain = emailNormalizeAddress(address).split("@").pop() || "";
      return Object.values(emailMailServicePresets).find((provider) => (provider.domainHints || []).includes(domain))?.id || "";
    }

    function emailValidAddress(address) {
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailNormalizeAddress(address));
    }

    function emailNewState() {
      return {
        initialized: false,
        activeTab: "mail",
        activeConfigTab: "gmail",
        activeMailView: "list",
        accounts: emailSeedAccounts.map((account) => ({
          ...account,
          scopes: Array.isArray(account.scopes) ? account.scopes.slice() : undefined,
          incoming: account.incoming ? {...account.incoming} : undefined,
          smtp: account.smtp ? {...account.smtp} : undefined
        })),
        messages: emailSeedMessages.map((message) => ({...message, labels: message.labels.slice()})),
        drafts: [],
        selectedAccountId: "all",
        selectedFolder: "inbox",
        selectedMessageId: "",
        search: "",
        replyDraft: "",
        lastHandoffUrl: "",
        lastProvider: "gmail",
        lastSyncAt: emailNowIso(),
        mcel: {
          status: "pending",
          failed: false
        }
      };
    }

    function emailEnsureStateShape() {
      if (!Array.isArray(emailAppState.accounts)) emailAppState.accounts = [];
      if (!Array.isArray(emailAppState.messages)) emailAppState.messages = [];
      if (!Array.isArray(emailAppState.drafts)) emailAppState.drafts = [];
      emailAppState.activeTab = ["mail", "config"].includes(emailAppState.activeTab) ? emailAppState.activeTab : "mail";
      emailAppState.activeConfigTab = emailAppState.activeConfigTab === "imap" ? "raw" : emailAppState.activeConfigTab;
      emailAppState.activeConfigTab = ["gmail", "yahoo", "raw"].includes(emailAppState.activeConfigTab) ? emailAppState.activeConfigTab : "gmail";
      emailAppState.activeMailView = ["list", "thread"].includes(emailAppState.activeMailView) ? emailAppState.activeMailView : "list";
      emailAppState.selectedAccountId = String(emailAppState.selectedAccountId || "all");
      emailAppState.selectedFolder = String(emailAppState.selectedFolder || "inbox");
      emailAppState.selectedMessageId = String(emailAppState.selectedMessageId || "");
      emailAppState.search = String(emailAppState.search || "");
      emailAppState.replyDraft = String(emailAppState.replyDraft || "");
      emailAppState.lastHandoffUrl = String(emailAppState.lastHandoffUrl || "");
      emailAppState.lastProvider = String(emailAppState.lastProvider || "gmail");
      emailAppState.lastSyncAt = String(emailAppState.lastSyncAt || "");
      if (!emailAppState.mcel || typeof emailAppState.mcel !== "object") {
        emailAppState.mcel = {status: "pending", failed: false};
      }
    }

    function emailLoadState() {
      try {
        const raw = localStorage.getItem(emailStorageKey);
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed && typeof parsed === "object") {
            Object.assign(emailAppState, parsed);
          }
        }
      } catch {
        // Use seeded state when local storage is unavailable or corrupt.
      }
      emailEnsureStateShape();
      if (!emailAppState.accounts.length && !emailAppState.messages.length) {
        Object.assign(emailAppState, emailNewState());
      }
      emailEnsureStateShape();
    }

    function emailSaveState() {
      emailEnsureStateShape();
      try {
        localStorage.setItem(emailStorageKey, JSON.stringify({
          activeTab: emailAppState.activeTab,
          activeConfigTab: emailAppState.activeConfigTab,
          activeMailView: emailAppState.activeMailView,
          accounts: emailAppState.accounts,
          messages: emailAppState.messages,
          drafts: emailAppState.drafts,
          selectedAccountId: emailAppState.selectedAccountId,
          selectedFolder: emailAppState.selectedFolder,
          selectedMessageId: emailAppState.selectedMessageId,
          search: emailAppState.search,
          replyDraft: emailAppState.replyDraft,
          lastHandoffUrl: emailAppState.lastHandoffUrl,
          lastProvider: emailAppState.lastProvider,
          lastSyncAt: emailAppState.lastSyncAt,
          mcel: emailAppState.mcel
        }));
      } catch {
        // Local state is best-effort.
      }
    }

    function emailSetStatus(message) {
      if (emailSyncStatus) emailSyncStatus.textContent = String(message || "email ready");
    }

    function emailOutputForProvider(providerId) {
      if (providerId === "gmail") return emailGmailConfigStatus;
      if (providerId === "yahoo") return emailYahooConfigStatus;
      return emailServerConfigStatus;
    }

    function emailRenderTabs() {
      emailTabButtons.forEach((button) => {
        const active = button.dataset.emailTab === emailAppState.activeTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
      emailTabPanels.forEach((panel) => {
        const active = panel.dataset.emailPanel === emailAppState.activeTab;
        panel.hidden = !active;
        panel.classList.toggle("email-tab-panel-active", active);
      });
      emailConfigTabButtons.forEach((button) => {
        const active = button.dataset.emailConfigTab === emailAppState.activeConfigTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
      emailConfigPanels.forEach((panel) => {
        const active = panel.dataset.emailConfigPanel === emailAppState.activeConfigTab;
        panel.hidden = !active;
        panel.classList.toggle("active", active);
      });
    }

    function emailSwitchTab(tabName) {
      emailAppState.activeTab = tabName === "config" ? "config" : "mail";
      emailSaveState();
      emailRenderTabs();
      if (emailAppState.activeTab === "mail") {
        emailSearchInput?.focus();
      } else if (emailAppState.activeConfigTab === "raw") {
        emailImapAddressInput?.focus();
      } else if (emailAppState.activeConfigTab === "yahoo") {
        emailYahooAddressInput?.focus();
      } else {
        emailGmailAddressInput?.focus();
      }
    }

    function emailSwitchConfigTab(tabName) {
      emailAppState.activeConfigTab = ["gmail", "yahoo", "raw"].includes(tabName) ? tabName : "gmail";
      emailAppState.activeTab = "config";
      emailSaveState();
      emailRenderTabs();
      if (emailAppState.activeConfigTab === "raw") {
        emailImapAddressInput?.focus();
      } else if (emailAppState.activeConfigTab === "yahoo") {
        emailYahooAddressInput?.focus();
      } else {
        emailGmailAddressInput?.focus();
      }
    }

    function emailStateToken(prefix = "email") {
      const raw = `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      try {
        return btoa(raw).replace(/=+$/g, "").replace(/\+/g, "-").replace(/\//g, "_");
      } catch {
        return raw.replace(/[^a-z0-9_-]/gi, "");
      }
    }

    function emailBuildOAuthPlan(providerId, address, clientId) {
      const provider = emailOauthProvider(providerId);
      const state = emailStateToken(provider.id);
      const nonce = emailStateToken(`${provider.id}-nonce`);
      const redirectUri = `${window.location.origin}/applications/email`;
      const params = new URLSearchParams({
        client_id: clientId,
        redirect_uri: redirectUri,
        response_type: "code",
        scope: provider.scopes.join(" "),
        state
      });
      Object.entries(provider.oauthExtras || {}).forEach(([key, value]) => {
        params.set(key, value);
      });
      if (provider.id === "yahoo") {
        params.set("nonce", nonce);
      }
      const url = `${provider.authEndpoint}?${params.toString()}`;
      return {
        provider: provider.id,
        providerLabel: provider.label,
        address,
        clientId,
        redirectUri,
        state,
        nonce,
        scopes: provider.scopes.slice(),
        authEndpoint: provider.authEndpoint,
        tokenEndpoint: provider.tokenEndpoint,
        url
      };
    }

    function emailOAuthFormValues(providerId) {
      if (providerId === "yahoo") {
        return {
          address: emailNormalizeAddress(emailYahooAddressInput?.value || ""),
          clientId: String(emailYahooClientIdInput?.value || "").trim()
        };
      }
      return {
        address: emailNormalizeAddress(emailGmailAddressInput?.value || ""),
        clientId: String(emailGmailClientIdInput?.value || "").trim()
      };
    }

    function emailConnectProvider(providerId = "gmail") {
      const provider = emailOauthProvider(providerId);
      const output = emailOutputForProvider(provider.id);
      const {address, clientId} = emailOAuthFormValues(provider.id);
      const guessedProvider = emailAddressProviderGuess(address);
      if (!emailValidAddress(address)) {
        emailSetStatus("email address required");
        if (output) output.textContent = `Enter a valid ${provider.label} address before preparing OAuth.`;
        (provider.id === "yahoo" ? emailYahooAddressInput : emailGmailAddressInput)?.focus();
        return null;
      }
      const plan = emailBuildOAuthPlan(provider.id, address, clientId || "CLIENT_ID_REQUIRED");
      const mismatch = guessedProvider && guessedProvider !== provider.id;
      const existing = emailAppState.accounts.find((account) => account.address === address && account.provider === provider.id);
      const account = existing || {
        id: `${provider.id}-${Date.now()}`,
        provider: provider.id,
        address,
        displayName: address,
        connectionType: "oauth",
        connectedAt: emailNowIso(),
        scopes: provider.scopes.slice(),
        handoffUrl: "",
        tokenEndpoint: provider.tokenEndpoint
      };
      account.status = clientId ? "pending-oauth" : "needs-client-id";
      account.handoffUrl = clientId ? plan.url : "";
      account.redirectUri = plan.redirectUri;
      account.state = plan.state;
      account.tokenEndpoint = plan.tokenEndpoint;
      account.scopes = provider.scopes.slice();
      if (!existing) emailAppState.accounts.unshift(account);
      emailAppState.selectedAccountId = account.id;
      emailAppState.lastHandoffUrl = account.handoffUrl;
      emailAppState.lastProvider = provider.id;
      emailAppState.lastSyncAt = emailNowIso();
      const warning = mismatch ? ` Address domain looks like ${emailProviderLabel(guessedProvider)}.` : "";
      const ready = clientId
        ? `${provider.label} handoff URL prepared for ${address}.${warning}`
        : `${provider.label} account staged for ${address}; paste an OAuth client ID to open provider consent.${warning}`;
      emailSetStatus(clientId ? `${provider.label} handoff ready` : `${provider.label} needs client ID`);
      if (output) output.textContent = ready;
      emailSaveState();
      renderEmailApp();
      return plan;
    }

    function emailOpenProviderConsent(providerId = "gmail") {
      const plan = emailConnectProvider(providerId);
      const output = emailOutputForProvider(providerId);
      const url = plan?.url || "";
      if (!url || url.includes("CLIENT_ID_REQUIRED")) {
        if (output) output.textContent = "Provider consent needs a real OAuth client ID first.";
        emailSetStatus("client ID required");
        (providerId === "yahoo" ? emailYahooClientIdInput : emailGmailClientIdInput)?.focus();
        return;
      }
      window.open(url, "main-computer-email-oauth", "noopener,noreferrer,width=980,height=760");
      if (output) output.textContent = `Opened ${plan.providerLabel} authorization handoff. Token exchange remains a backend bridge boundary.`;
    }

    function emailApplyServicePreset(presetId = emailImapPresetSelect?.value || "custom", {preserveAddress = true} = {}) {
      const preset = emailPreset(presetId);
      if (emailImapPresetSelect) emailImapPresetSelect.value = preset.id;
      if (emailImapProtocolSelect) emailImapProtocolSelect.value = preset.defaultProtocol || "imap";
      const incoming = preset[emailImapProtocolSelect?.value || preset.defaultProtocol || "imap"] || preset.imap || {};
      if (emailIncomingHostInput) emailIncomingHostInput.value = incoming.host || "";
      if (emailIncomingPortInput) emailIncomingPortInput.value = String(incoming.port || "");
      if (emailIncomingSecuritySelect) emailIncomingSecuritySelect.value = incoming.security || "ssl";
      if (emailSmtpHostInput) emailSmtpHostInput.value = preset.smtp?.host || "";
      if (emailSmtpPortInput) emailSmtpPortInput.value = String(preset.smtp?.port || "");
      if (emailSmtpSecuritySelect) emailSmtpSecuritySelect.value = preset.smtp?.security || "starttls";
      if (!preserveAddress && emailImapAddressInput) emailImapAddressInput.value = "";
      if (emailServerConfigStatus) {
        emailServerConfigStatus.textContent = `${preset.label} preset loaded. ${preset.authHint || ""}`.trim();
      }
    }

    function emailApplyProtocolForCurrentPreset() {
      const preset = emailPreset(emailImapPresetSelect?.value || "custom");
      const protocol = emailImapProtocolSelect?.value || preset.defaultProtocol || "imap";
      const incoming = preset[protocol] || preset.imap || {};
      if (emailIncomingHostInput) emailIncomingHostInput.value = incoming.host || "";
      if (emailIncomingPortInput) emailIncomingPortInput.value = String(incoming.port || "");
      if (emailIncomingSecuritySelect) emailIncomingSecuritySelect.value = incoming.security || "ssl";
      if (emailServerConfigStatus) {
        emailServerConfigStatus.textContent = `${preset.label} ${protocol.toUpperCase()} settings loaded. ${preset.authHint || ""}`.trim();
      }
    }

    function emailMaybeApplyPresetFromAddress(address) {
      const guess = emailAddressProviderGuess(address);
      if (guess && emailImapPresetSelect && emailImapPresetSelect.value !== guess) {
        emailApplyServicePreset(guess);
      }
      if (emailImapUsernameInput && !emailImapUsernameInput.value.trim()) {
        emailImapUsernameInput.value = emailNormalizeAddress(address);
      }
    }

    function emailServerFormValues({includePassword = false} = {}) {
      const provider = emailImapPresetSelect?.value || "custom";
      const protocol = emailImapProtocolSelect?.value || "imap";
      const address = emailNormalizeAddress(emailImapAddressInput?.value || "");
      const username = String(emailImapUsernameInput?.value || address).trim();
      const incoming = {
        host: String(emailIncomingHostInput?.value || "").trim(),
        port: Number.parseInt(String(emailIncomingPortInput?.value || ""), 10),
        security: emailIncomingSecuritySelect?.value || "ssl"
      };
      const smtp = {
        host: String(emailSmtpHostInput?.value || "").trim(),
        port: Number.parseInt(String(emailSmtpPortInput?.value || ""), 10),
        security: emailSmtpSecuritySelect?.value || "starttls"
      };
      const config = {
        provider,
        providerLabel: emailProviderLabel(provider),
        address,
        displayName: String(emailImapDisplayNameInput?.value || "").trim(),
        username,
        protocol,
        incoming,
        smtp
      };
      if (includePassword) {
        config.password = String(emailImapPasswordInput?.value || "");
      }
      return config;
    }

    function emailValidateServerConfig(config, {passwordRequired = false} = {}) {
      if (!emailValidAddress(config.address)) return "Enter a valid account email address.";
      if (!config.username) return "Username is required.";
      if (!["imap", "pop3"].includes(config.protocol)) return "Protocol must be IMAP or POP3.";
      if (!config.incoming.host) return "Incoming server is required.";
      if (!Number.isFinite(config.incoming.port) || config.incoming.port < 1 || config.incoming.port > 65535) return "Incoming port must be between 1 and 65535.";
      if (!config.smtp.host) return "SMTP server is required.";
      if (!Number.isFinite(config.smtp.port) || config.smtp.port < 1 || config.smtp.port > 65535) return "SMTP port must be between 1 and 65535.";
      if (passwordRequired && !config.password) return "Enter the password or app password for this one-time Check mail action.";
      return "";
    }

    function emailUpsertServerAccount(config, status = "configured") {
      const existing = emailAppState.accounts.find((account) => account.address === config.address && account.provider === config.provider && account.connectionType !== "oauth");
      const account = existing || {
        id: `${config.provider}-${Date.now()}`,
        provider: config.provider,
        address: config.address,
        connectedAt: emailNowIso()
      };
      account.displayName = config.displayName || config.address;
      account.connectionType = config.protocol;
      account.protocol = config.protocol;
      account.username = config.username;
      account.status = status;
      account.incoming = {...config.incoming};
      account.smtp = {...config.smtp};
      account.authHint = emailPreset(config.provider).authHint || "";
      account.lastCheckedAt = status.includes("checked") ? emailNowIso() : account.lastCheckedAt || "";
      if (!existing) emailAppState.accounts.unshift(account);
      emailAppState.selectedAccountId = account.id;
      emailAppState.lastSyncAt = emailNowIso();
      return account;
    }

    function emailSaveServerAccountConfig() {
      const config = emailServerFormValues();
      const error = emailValidateServerConfig(config);
      if (error) {
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = error;
        emailSetStatus("server config needs attention");
        return null;
      }
      const account = emailUpsertServerAccount(config, "configured");
      emailSaveState();
      renderEmailApp();
      if (emailServerConfigStatus) {
        emailServerConfigStatus.textContent = `${config.providerLabel} account saved for ${config.address}. Use Check mail when you are ready to send the password to the backend bridge.`;
      }
      emailSetStatus(`${config.providerLabel} account configured`);
      return account;
    }

    function emailLiveMessageToState(message, account, fallbackProvider) {
      return {
        id: String(message.id || `live-${Date.now()}-${Math.random().toString(16).slice(2)}`),
        accountId: account.id,
        provider: account.provider || fallbackProvider || "custom",
        folder: "inbox",
        from: String(message.from || "(unknown sender)"),
        to: String(message.to || account.address),
        subject: String(message.subject || "(no subject)"),
        excerpt: String(message.excerpt || "Fetched through the local backend mail bridge.").slice(0, 220),
        body: String(message.body || message.excerpt || "Fetched through the local backend mail bridge."),
        date: message.date || emailNowIso(),
        labels: Array.isArray(message.labels) ? message.labels.slice(0, 6) : ["Live"],
        priority: Boolean(message.priority),
        unread: message.unread !== false
      };
    }

    async function emailCheckConfiguredServerAccount() {
      const config = emailServerFormValues({includePassword: true});
      const error = emailValidateServerConfig(config, {passwordRequired: true});
      if (error) {
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = error;
        emailSetStatus("check mail needs credentials");
        return null;
      }
      const account = emailUpsertServerAccount(config, "checking");
      emailSaveState();
      renderEmailApp();
      if (emailServerConfigStatus) emailServerConfigStatus.textContent = `Checking ${config.providerLabel} through ${config.protocol.toUpperCase()} bridge…`;
      emailSetStatus(`checking ${config.providerLabel}`);
      try {
        const response = await fetch(emailCheckEndpoint, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            accountId: account.id,
            provider: account.provider,
            address: account.address,
            protocol: account.protocol,
            host: account.incoming.host,
            port: account.incoming.port,
            security: account.incoming.security,
            username: config.username,
            password: config.password
          })
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          throw new Error(payload.error || `mail bridge returned ${response.status}`);
        }
        account.status = "checked";
        account.lastCheckedAt = emailNowIso();
        const liveMessages = Array.isArray(payload.messages) ? payload.messages.map((message) => emailLiveMessageToState(message, account, config.provider)) : [];
        const liveIds = new Set(liveMessages.map((message) => message.id));
        emailAppState.messages = [
          ...liveMessages,
          ...emailAppState.messages.filter((message) => !liveIds.has(message.id))
        ];
        if (liveMessages[0]) {
          emailAppState.selectedMessageId = "";
          emailAppState.selectedFolder = "inbox";
          emailAppState.activeTab = "mail";
          emailAppState.activeMailView = "list";
          emailAppState.replyDraft = "";
        }
        emailAppState.lastSyncAt = emailNowIso();
        emailSaveState();
        renderEmailApp();
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = `Checked ${config.providerLabel}: ${liveMessages.length} header preview${liveMessages.length === 1 ? "" : "s"} fetched. Password was not saved.`;
        emailSetStatus(`${config.providerLabel} checked`);
        if (emailImapPasswordInput) emailImapPasswordInput.value = "";
        return payload;
      } catch (error) {
        account.status = "check-failed";
        emailSaveState();
        renderEmailApp();
        const message = String(error?.message || error || "mail check failed");
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = message;
        emailSetStatus("mail check failed");
        return null;
      }
    }

    function emailMessagesForView() {
      const search = String(emailAppState.search || "").trim().toLowerCase();
      const accountId = emailAppState.selectedAccountId;
      const folder = emailAppState.selectedFolder;
      const visibleDrafts = emailAppState.drafts
        .filter((draft) => accountId === "all" || draft.accountId === accountId)
        .map((draft) => ({
          ...draft,
          id: draft.id,
          folder: "drafts",
          from: draft.from,
          excerpt: draft.body,
          labels: ["Draft", draft.tone || "concise"],
          unread: false,
          draft: true
        }));
      const messages = folder === "drafts"
        ? visibleDrafts
        : emailAppState.messages
            .filter((message) => accountId === "all" || message.accountId === accountId)
            .filter((message) => {
              if (folder === "all") return true;
              if (folder === "priority") return Boolean(message.priority);
              return message.folder === folder;
            });
      return messages
        .filter((message) => {
          if (!search) return true;
          return [
            message.from,
            message.to,
            message.subject,
            message.excerpt,
            message.body,
            ...(message.labels || [])
          ].join(" ").toLowerCase().includes(search);
        })
        .sort((left, right) => String(right.date || "").localeCompare(String(left.date || "")));
    }

    function emailFolderCounts() {
      const accountId = emailAppState.selectedAccountId;
      const messages = emailAppState.messages.filter((message) => accountId === "all" || message.accountId === accountId);
      return {
        inbox: messages.filter((message) => message.folder === "inbox").length,
        priority: messages.filter((message) => message.priority).length,
        sent: messages.filter((message) => message.folder === "sent").length,
        drafts: emailAppState.drafts.filter((draft) => accountId === "all" || draft.accountId === accountId).length,
        all: messages.length
      };
    }

    function emailSelectedMessage() {
      return emailMessagesForView().find((message) => message.id === emailAppState.selectedMessageId)
        || emailAppState.messages.find((message) => message.id === emailAppState.selectedMessageId)
        || emailMessagesForView()[0]
        || null;
    }

    function emailAccountConnectionLabel(account) {
      if (account.connectionType === "oauth") return "OAuth";
      return String(account.protocol || account.connectionType || "raw").toUpperCase();
    }

    function emailRenderConfigAccountBucket(node, accounts, emptyText) {
      if (!node) return;
      if (!accounts.length) {
        node.textContent = emptyText;
        return;
      }
      node.innerHTML = accounts.map((account) => {
        const status = account.status || "staged";
        const connection = emailAccountConnectionLabel(account);
        return `<article class="email-config-account-item">
          <strong>${emailEscapeHtml(account.address || account.displayName || "(no address)")}</strong>
          <span>${emailEscapeHtml(connection)} · ${emailEscapeHtml(status)}</span>
        </article>`;
      }).join("");
    }

    function emailRenderConfigAccountLists() {
      const gmailAccounts = emailAppState.accounts.filter((account) => account.provider === "gmail" && account.connectionType === "oauth");
      const yahooAccounts = emailAppState.accounts.filter((account) => account.provider === "yahoo" && account.connectionType === "oauth");
      const rawAccounts = emailAppState.accounts.filter((account) => account.connectionType !== "oauth");
      emailRenderConfigAccountBucket(emailGmailAccountList, gmailAccounts, "No Gmail accounts staged yet.");
      emailRenderConfigAccountBucket(emailYahooAccountList, yahooAccounts, "No Yahoo accounts staged yet.");
      emailRenderConfigAccountBucket(emailRawAccountList, rawAccounts, "No raw accounts staged yet.");
    }

    function emailRenderAccounts() {
      if (!emailAccountList) return;
      const accountCount = emailAppState.accounts.length;
      if (emailAccountSummary) {
        emailAccountSummary.textContent = accountCount ? `${accountCount} account${accountCount === 1 ? "" : "s"} available.` : "No accounts connected.";
      }
      const allActive = emailAppState.selectedAccountId === "all";
      const buttons = [
        `<button type="button" class="email-account-button ${allActive ? "active" : ""}" data-email-account="all"><strong>All accounts</strong><span>Unified inbox</span></button>`,
        ...emailAppState.accounts.map((account) => {
          const active = emailAppState.selectedAccountId === account.id;
          const connection = emailAccountConnectionLabel(account);
          return `<button type="button" class="email-account-button ${active ? "active" : ""}" data-email-account="${emailEscapeHtml(account.id)}"><strong>${emailEscapeHtml(account.address)}</strong><span>${emailEscapeHtml(emailProviderLabel(account.provider))} · ${emailEscapeHtml(connection)} · ${emailEscapeHtml(account.status || "ready")}</span></button>`;
        })
      ];
      emailAccountList.innerHTML = buttons.join("");
      emailAccountList.querySelectorAll("[data-email-account]").forEach((button) => {
        button.addEventListener("click", () => {
          emailAppState.selectedAccountId = button.dataset.emailAccount || "all";
          emailAppState.selectedMessageId = "";
          emailAppState.activeMailView = "list";
          emailAppState.replyDraft = "";
          emailSaveState();
          renderEmailApp();
        });
      });
    }

    function emailRenderFolders() {
      const counts = emailFolderCounts();
      Object.entries(counts).forEach(([folder, count]) => {
        const node = document.querySelector(`#email-count-${folder}`);
        if (node) node.textContent = String(count);
      });
      emailFolderButtons.forEach((button) => {
        button.classList.toggle("active", button.dataset.emailFolder === emailAppState.selectedFolder);
      });
    }

    function emailRenderMessages() {
      if (!emailMessageList) return;
      const messages = emailMessagesForView();
      if (!messages.length) {
        emailMessageList.innerHTML = `<div class="email-empty-state" role="listitem"><strong>No messages</strong><span>Try All Mail, clear search, or configure another account.</span></div>`;
        return;
      }
      emailMessageList.innerHTML = messages.map((message) => {
        const active = message.id === emailAppState.selectedMessageId;
        const unread = message.unread && !message.draft;
        const excerpt = String(message.excerpt || message.body || "").replace(/\s+/g, " ").trim();
        const subject = message.draft ? `Draft: ${message.subject || "(no subject)"}` : (message.subject || "(no subject)");
        const dateText = emailFormatTime(message.date);
        return `<button type="button" class="email-message-item ${active ? "active" : ""} ${unread ? "unread" : ""}" data-email-message="${emailEscapeHtml(message.id)}" role="listitem" aria-label="${emailEscapeHtml(subject)} from ${emailEscapeHtml(message.from)}">
          <span class="email-row-check" aria-hidden="true"></span>
          <span class="email-row-unread" aria-hidden="true">${unread ? "•" : ""}</span>
          <strong class="email-row-sender">${emailEscapeHtml(message.from || "(unknown sender)")}</strong>
          <span class="email-row-subject"><strong>${emailEscapeHtml(subject)}</strong><span>${excerpt ? ` · ${emailEscapeHtml(excerpt)}` : ""}</span></span>
          <time class="email-row-date" datetime="${emailEscapeHtml(message.date || "")}">${emailEscapeHtml(dateText)}</time>
        </button>`;
      }).join("");
      emailMessageList.querySelectorAll("[data-email-message]").forEach((button) => {
        button.addEventListener("click", () => {
          emailAppState.selectedMessageId = button.dataset.emailMessage || "";
          const message = emailSelectedMessage();
          if (message && !message.draft) message.unread = false;
          emailAppState.activeMailView = "thread";
          emailAppState.replyDraft = "";
          emailSaveState();
          renderEmailApp();
        });
      });
    }

    function emailRenderMailView() {
      const showingThread = emailAppState.activeMailView === "thread" && Boolean(emailAppState.selectedMessageId);
      if (emailMailMain) {
        emailMailMain.dataset.emailMailView = showingThread ? "thread" : "list";
      }
      if (emailListView) {
        emailListView.hidden = showingThread;
      }
      if (emailThreadView) {
        emailThreadView.hidden = !showingThread;
      }
      if (!showingThread && emailReplyForm) {
        emailReplyForm.hidden = true;
      }
    }

    function emailRenderThread() {
      const message = emailAppState.selectedMessageId
        ? (emailAppState.messages.find((item) => item.id === emailAppState.selectedMessageId)
          || emailAppState.drafts.find((item) => item.id === emailAppState.selectedMessageId)
          || null)
        : null;
      if (!message) {
        if (emailThreadProvider) emailThreadProvider.textContent = "No message selected";
        if (emailThreadSubject) emailThreadSubject.textContent = "Select a message";
        if (emailThreadMeta) emailThreadMeta.textContent = "Open a message from the list.";
        if (emailThreadLabels) emailThreadLabels.innerHTML = "";
        if (emailThreadBody) emailThreadBody.textContent = "Choose a message to read it here.";
        if (emailReplyForm) emailReplyForm.hidden = true;
        return;
      }
      if (emailThreadProvider) emailThreadProvider.textContent = `${emailProviderLabel(message.provider)} · ${message.folder || "mail"}`;
      if (emailThreadSubject) emailThreadSubject.textContent = message.subject || "(no subject)";
      if (emailThreadMeta) emailThreadMeta.textContent = `From ${message.from} to ${message.to} · ${emailFormatTime(message.date)}`;
      if (emailThreadLabels) {
        emailThreadLabels.innerHTML = (message.labels || []).map((label) => `<span class="email-label">${emailEscapeHtml(label)}</span>`).join("");
      }
      if (emailThreadBody) emailThreadBody.textContent = message.body || message.excerpt || "";
      if (emailReplyForm) {
        emailReplyForm.hidden = !emailAppState.replyDraft;
      }
      if (emailReplyBody && emailReplyBody.value !== emailAppState.replyDraft) {
        emailReplyBody.value = emailAppState.replyDraft;
      }
      if (emailReplyStatus && emailAppState.replyDraft) {
        emailReplyStatus.textContent = "Reply ready.";
      }
    }

    function emailRenderSmartLayer() {
      const priorityCount = emailAppState.messages.filter((message) => message.priority).length;
      const draftCount = emailAppState.drafts.length;
      const selected = emailSelectedMessage();
      if (emailSmartProvider) {
        const providers = [...new Set(emailAppState.accounts.map((account) => emailProviderLabel(account.provider)))];
        emailSmartProvider.textContent = providers.length ? providers.join(" + ") : "Common mail ready";
      }
      if (emailSmartPriority) emailSmartPriority.textContent = `${priorityCount} thread${priorityCount === 1 ? "" : "s"}`;
      if (emailSmartDrafts) emailSmartDrafts.textContent = `${draftCount} saved`;
      if (emailSmartReport) {
        const report = {
          selectedThread: selected?.id || "",
          folder: emailAppState.selectedFolder,
          account: emailAppState.selectedAccountId,
          visibleMessages: emailMessagesForView().length,
          accounts: emailAppState.accounts.map((account) => ({
            provider: account.provider,
            address: account.address,
            connectionType: account.connectionType,
            protocol: account.protocol,
            status: account.status,
            incoming: account.incoming ? {...account.incoming} : undefined
          })),
          checkEndpoint: emailCheckEndpoint,
          mcel: emailAppState.mcel,
          lastSyncAt: emailAppState.lastSyncAt
        };
        emailSmartReport.textContent = JSON.stringify(report, null, 2);
      }
    }

    function emailRenderMcelProof() {
      const source = `
        <section data-mc="panel" data-mc-kind="mailbox" data-mc-flow="task" data-mc-rank="primary" data-mc-state="ready" data-mc-density="compact" data-mc-words="Email mailbox inbox reply compose POP IMAP Gmail Yahoo Outlook iCloud">
          <button data-mc="command" data-mc-kind="primary" data-mc-flow="forward" data-mc-rank="primary" data-mc-state="ready">Open message</button>
        </section>`;
      try {
        if (window.MCEL?.compile) {
          const compiled = window.MCEL.compile(source, {theme: "theme-machine", reason: "email-app:hidden-contract"});
          const auditNode = document.createElement("div");
          auditNode.hidden = true;
          auditNode.innerHTML = compiled.runtimeHtml || source;
          const audit = window.MCEL.audit?.(source, auditNode, {reason: "email-app:hidden-audit"});
          emailAppState.mcel = {
            status: audit?.failed ? "audit-warning" : "compiled",
            failed: Boolean(audit?.failed),
            contractVersion: window.MCEL.version || ""
          };
        } else {
          emailAppState.mcel = {status: "fallback-source", failed: false};
        }
      } catch (error) {
        emailAppState.mcel = {
          status: "fallback-after-error",
          failed: true,
          error: String(error?.message || error || "MCEL failed")
        };
      }
    }

    function emailRenderServerFormDefaults() {
      if (!emailImapPresetSelect) return;
      const preset = emailPreset(emailImapPresetSelect.value || "custom");
      if (!emailIncomingHostInput?.value && preset.id !== "custom") {
        emailApplyServicePreset(preset.id);
      }
      const address = emailNormalizeAddress(emailImapAddressInput?.value || "");
      if (address && emailImapUsernameInput && !emailImapUsernameInput.value.trim()) {
        emailImapUsernameInput.value = address;
      }
    }

    function renderEmailApp() {
      if (!emailApp) return;
      emailEnsureStateShape();
      if (emailSearchInput && emailSearchInput.value !== emailAppState.search) {
        emailSearchInput.value = emailAppState.search;
      }
      emailRenderTabs();
      emailRenderServerFormDefaults();
      emailRenderAccounts();
      emailRenderConfigAccountLists();
      emailRenderFolders();
      emailRenderMessages();
      emailRenderThread();
      emailRenderMailView();
      emailRenderMcelProof();
      emailRenderSmartLayer();
      emailSaveState();
    }

    function emailSetComposeModalOpen(open) {
      if (!emailComposeModal) return;
      if (open) {
        emailComposeModal.hidden = false;
        if (typeof emailComposeModal.showModal === "function" && !emailComposeModal.open) {
          emailComposeModal.showModal();
        } else {
          emailComposeModal.setAttribute("open", "");
        }
        window.setTimeout(() => emailComposeTo?.focus(), 0);
        return;
      }
      if (typeof emailComposeModal.close === "function" && emailComposeModal.open) {
        emailComposeModal.close();
      }
      emailComposeModal.hidden = true;
      emailComposeModal.removeAttribute("open");
    }

    function emailDismissComposeModal() {
      emailSetComposeModalOpen(false);
      emailSetStatus("compose closed");
    }

    function emailReturnToMessageList() {
      emailAppState.activeMailView = "list";
      emailAppState.replyDraft = "";
      emailSaveState();
      renderEmailApp();
      emailSearchInput?.focus();
    }

    function emailArchiveSelected() {
      const message = emailSelectedMessage();
      if (!message || message.draft) return;
      message.folder = "all";
      message.unread = false;
      message.labels = [...new Set([...(message.labels || []), "Archived"])];
      emailAppState.selectedMessageId = "";
      emailAppState.activeMailView = "list";
      emailAppState.replyDraft = "";
      emailSetStatus("message archived locally");
      emailSaveState();
      renderEmailApp();
    }

    function emailTogglePrioritySelected() {
      const message = emailSelectedMessage();
      if (!message || message.draft) return;
      message.priority = !message.priority;
      const labels = new Set(message.labels || []);
      if (message.priority) {
        labels.add("Priority");
      } else {
        labels.delete("Priority");
      }
      message.labels = [...labels];
      emailSetStatus(message.priority ? "marked priority" : "priority cleared");
      emailSaveState();
      renderEmailApp();
    }

    function emailReplySelected() {
      const message = emailSelectedMessage();
      if (!message) return;
      emailAppState.activeMailView = "thread";
      emailAppState.replyDraft = `\n\nOn ${emailFormatTime(message.date)}, ${message.from} wrote:\n> ${(message.body || message.excerpt || "").split("\n").join("\n> ")}`;
      emailSaveState();
      renderEmailApp();
      window.setTimeout(() => emailReplyBody?.focus(), 0);
      emailSetStatus("inline reply ready");
    }

    function emailNewCompose() {
      if (emailComposeTo) emailComposeTo.value = "";
      if (emailComposeSubject) emailComposeSubject.value = "";
      if (emailComposeBody) emailComposeBody.value = "";
      emailSwitchTab("mail");
      emailSetComposeModalOpen(true);
      emailSetStatus("compose ready");
    }

    function emailSelectedAccountForCompose() {
      if (emailAppState.selectedAccountId !== "all") {
        return emailAppState.accounts.find((account) => account.id === emailAppState.selectedAccountId) || emailAppState.accounts[0] || null;
      }
      return emailAppState.accounts[0] || null;
    }

    function emailSaveDraftFromCompose({queued = false} = {}) {
      const account = emailSelectedAccountForCompose();
      const to = String(emailComposeTo?.value || "").trim();
      const subject = String(emailComposeSubject?.value || "").trim();
      const body = String(emailComposeBody?.value || "").trim();
      if (!to || !subject || !body) {
        if (emailComposeStatus) emailComposeStatus.textContent = "To, subject, and message are required.";
        return null;
      }
      const draft = {
        id: `draft-${Date.now()}`,
        accountId: account?.id || "local",
        provider: account?.provider || "custom",
        from: account?.address || "local@maincomputer.local",
        to,
        subject,
        body,
        date: emailNowIso(),
        queued: Boolean(queued),
        tone: emailComposeTone?.value || "concise"
      };
      emailAppState.drafts.unshift(draft);
      if (queued) {
        emailAppState.messages.unshift({
          id: `queued-${Date.now()}`,
          accountId: draft.accountId,
          provider: draft.provider,
          folder: "sent",
          from: draft.from,
          to: draft.to,
          subject: draft.subject,
          excerpt: draft.body.slice(0, 140),
          body: draft.body,
          date: draft.date,
          labels: ["Queued", "Local"],
          priority: false,
          unread: false
        });
      }
      emailSaveState();
      renderEmailApp();
      if (emailComposeStatus) {
        emailComposeStatus.textContent = queued
          ? "Message queued locally. A provider bridge can deliver it after authentication."
          : "Draft saved locally.";
      }
      emailSetStatus(queued ? "send queued locally" : "draft saved");
      if (queued) {
        emailSetComposeModalOpen(false);
      }
      return draft;
    }

    function emailSaveInlineReply({queued = true} = {}) {
      const message = emailSelectedMessage();
      const body = String(emailReplyBody?.value || "").trim();
      if (!message) {
        if (emailReplyStatus) emailReplyStatus.textContent = "Select a message before replying.";
        return null;
      }
      if (!body) {
        if (emailReplyStatus) emailReplyStatus.textContent = "Write a reply before sending.";
        return null;
      }
      const account = emailSelectedAccountForCompose();
      const subject = message.subject.startsWith("Re:") ? message.subject : `Re: ${message.subject}`;
      const draft = {
        id: `reply-${Date.now()}`,
        accountId: account?.id || message.accountId || "local",
        provider: account?.provider || message.provider || "custom",
        from: account?.address || message.to || "local@maincomputer.local",
        to: message.from,
        subject,
        body,
        date: emailNowIso(),
        queued: Boolean(queued),
        tone: "reply"
      };
      emailAppState.drafts.unshift(draft);
      if (queued) {
        emailAppState.messages.unshift({
          id: `queued-reply-${Date.now()}`,
          accountId: draft.accountId,
          provider: draft.provider,
          folder: "sent",
          from: draft.from,
          to: draft.to,
          subject: draft.subject,
          excerpt: draft.body.slice(0, 140),
          body: draft.body,
          date: draft.date,
          labels: ["Queued", "Reply"],
          priority: false,
          unread: false
        });
      }
      emailAppState.replyDraft = "";
      if (emailReplyBody) emailReplyBody.value = "";
      if (emailReplyStatus) emailReplyStatus.textContent = queued ? "Reply queued locally." : "Reply draft saved.";
      emailSetStatus(queued ? "reply queued locally" : "reply draft saved");
      emailSaveState();
      renderEmailApp();
      return draft;
    }

    function emailSmartDraftForSelected() {
      const message = emailSelectedMessage();
      const tone = emailComposeTone?.value || "concise";
      const greeting = tone === "formal" ? "Hello," : "Hi,";
      const close = tone === "formal" ? "Best regards," : tone === "friendly" ? "Thanks!" : "Thanks,";
      const subject = message ? (message.subject.startsWith("Re:") ? message.subject : `Re: ${message.subject}`) : "Follow-up";
      const to = message?.from || "";
      const body = message
        ? `${greeting}\n\nI read your note about "${message.subject}". I’ll review the details and follow up with the next step.\n\n${close}`
        : `${greeting}\n\nI’m following up with the requested details.\n\n${close}`;
      if (emailReplyForm && !emailReplyForm.hidden && emailReplyBody) {
        emailReplyBody.value = body;
        emailAppState.replyDraft = body;
        if (emailReplyStatus) emailReplyStatus.textContent = `Smart ${tone} reply created locally.`;
        emailSetStatus("smart reply ready");
        emailSaveState();
        return;
      }
      if (emailComposeTo) emailComposeTo.value = to;
      if (emailComposeSubject) emailComposeSubject.value = subject;
      if (emailComposeBody) emailComposeBody.value = body;
      if (emailComposeStatus) emailComposeStatus.textContent = `Smart ${tone} draft created locally.`;
      emailSetStatus("smart draft ready");
    }

    function emailRefreshLocal() {
      emailAppState.lastSyncAt = emailNowIso();
      emailSetStatus("local mail state refreshed");
      renderEmailApp();
    }

    function emailCheckSelectedAccount() {
      const selected = emailAppState.accounts.find((account) => account.id === emailAppState.selectedAccountId) || emailAppState.accounts.find((account) => account.connectionType !== "oauth");
      if (!selected || selected.connectionType === "oauth") {
        emailSwitchConfigTab("raw");
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = "Use Raw accounts for POP/IMAP checks, or Gmail/Yahoo accounts for OAuth handoff."
        emailSetStatus("open config to check mail");
        return;
      }
      emailSwitchConfigTab("raw");
      const preset = emailPreset(selected.provider);
      if (emailImapPresetSelect) emailImapPresetSelect.value = selected.provider || "custom";
      if (emailImapProtocolSelect) emailImapProtocolSelect.value = selected.protocol || preset.defaultProtocol || "imap";
      if (emailImapAddressInput) emailImapAddressInput.value = selected.address || "";
      if (emailImapDisplayNameInput) emailImapDisplayNameInput.value = selected.displayName || "";
      if (emailImapUsernameInput) emailImapUsernameInput.value = selected.username || selected.address || "";
      if (emailIncomingHostInput) emailIncomingHostInput.value = selected.incoming?.host || "";
      if (emailIncomingPortInput) emailIncomingPortInput.value = String(selected.incoming?.port || "");
      if (emailIncomingSecuritySelect) emailIncomingSecuritySelect.value = selected.incoming?.security || "ssl";
      if (emailSmtpHostInput) emailSmtpHostInput.value = selected.smtp?.host || preset.smtp?.host || "";
      if (emailSmtpPortInput) emailSmtpPortInput.value = String(selected.smtp?.port || preset.smtp?.port || "");
      if (emailSmtpSecuritySelect) emailSmtpSecuritySelect.value = selected.smtp?.security || preset.smtp?.security || "starttls";
      if (emailServerConfigStatus) emailServerConfigStatus.textContent = "Enter this account's password or app password, then click Check mail. Passwords are not saved.";
      emailImapPasswordInput?.focus();
    }

    function emailResetDemoState() {
      Object.assign(emailAppState, emailNewState(), {initialized: true});
      try {
        localStorage.removeItem(emailStorageKey);
      } catch {
        // best-effort
      }
      emailApplyServicePreset("outlook");
      emailSetStatus("email demo reset");
      renderEmailApp();
    }

    function emailBindEvents() {
      emailTabButtons.forEach((button) => {
        button.addEventListener("click", () => emailSwitchTab(button.dataset.emailTab || "mail"));
      });
      emailConfigTabButtons.forEach((button) => {
        button.addEventListener("click", () => emailSwitchConfigTab(button.dataset.emailConfigTab || "gmail"));
      });
      emailOpenConfig?.addEventListener("click", () => emailSwitchConfigTab("gmail"));
      emailNewMessage?.addEventListener("click", emailNewCompose);
      emailCloseCompose?.addEventListener("click", emailDismissComposeModal);
      emailComposeModal?.addEventListener("cancel", () => {
        emailComposeModal.hidden = true;
        emailSetStatus("compose closed");
      });
      emailBackToList?.addEventListener("click", emailReturnToMessageList);
      emailCancelReply?.addEventListener("click", () => {
        emailAppState.replyDraft = "";
        if (emailReplyBody) emailReplyBody.value = "";
        emailSaveState();
        renderEmailApp();
        emailSetStatus("reply cancelled");
      });
      emailReplyBody?.addEventListener("input", () => {
        emailAppState.replyDraft = emailReplyBody.value || "";
        emailSaveState();
      });
      emailReplyForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        emailSaveInlineReply({queued: true});
      });
      emailImapPresetSelect?.addEventListener("change", () => emailApplyServicePreset(emailImapPresetSelect.value || "custom"));
      emailImapProtocolSelect?.addEventListener("change", emailApplyProtocolForCurrentPreset);
      emailImapAddressInput?.addEventListener("input", () => emailMaybeApplyPresetFromAddress(emailImapAddressInput.value));
      emailConnectGmail?.addEventListener("click", () => emailConnectProvider("gmail"));
      emailConnectYahoo?.addEventListener("click", () => emailConnectProvider("yahoo"));
      emailOpenGmailOauth?.addEventListener("click", () => emailOpenProviderConsent("gmail"));
      emailOpenYahooOauth?.addEventListener("click", () => emailOpenProviderConsent("yahoo"));
      emailSaveServerAccount?.addEventListener("click", emailSaveServerAccountConfig);
      emailCheckServerAccount?.addEventListener("click", emailCheckConfiguredServerAccount);
      emailCheckMail?.addEventListener("click", emailCheckSelectedAccount);
      emailResetDemo?.addEventListener("click", emailResetDemoState);
      emailRefresh?.addEventListener("click", emailRefreshLocal);
      emailFolderButtons.forEach((button) => {
        button.addEventListener("click", () => {
          emailAppState.selectedFolder = button.dataset.emailFolder || "inbox";
          emailAppState.selectedMessageId = "";
          emailAppState.activeMailView = "list";
          emailAppState.replyDraft = "";
          emailSaveState();
          renderEmailApp();
        });
      });
      emailSearchInput?.addEventListener("input", () => {
        emailAppState.search = emailSearchInput.value || "";
        emailAppState.selectedMessageId = "";
        emailAppState.activeMailView = "list";
        emailAppState.replyDraft = "";
        renderEmailApp();
      });
      emailArchiveMessage?.addEventListener("click", emailArchiveSelected);
      emailMarkPriority?.addEventListener("click", emailTogglePrioritySelected);
      emailReplyMessage?.addEventListener("click", emailReplySelected);
      emailSmartDraft?.addEventListener("click", emailSmartDraftForSelected);
      emailSaveDraft?.addEventListener("click", () => emailSaveDraftFromCompose());
      emailComposeForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        emailSaveDraftFromCompose({queued: true});
      });
    }

    function initEmailApp() {
      if (!emailApp) return;
      if (!emailAppState.initialized) {
        emailLoadState();
        emailBindEvents();
        emailApplyServicePreset(emailImapPresetSelect?.value || "outlook");
        emailAppState.initialized = true;
      }
      emailSetStatus("email ready");
      renderEmailApp();
      emailSearchInput?.focus();
    }

    if (typeof window !== "undefined") {
      window.initEmailApp = initEmailApp;
      window.renderEmailApp = renderEmailApp;
      window.emailBuildOAuthPlan = emailBuildOAuthPlan;
      window.emailApplyServicePreset = emailApplyServicePreset;
      window.emailCheckServerAccount = emailCheckConfiguredServerAccount;
    }
