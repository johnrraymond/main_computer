    const emailStorageKey = "main-computer-email-app-v6";
    const emailCheckEndpoint = "/api/applications/email/check";
    const emailTabQueryKey = "tab";
    const emailRestorableStateKeys = Object.freeze([
      "activeMailView",
      "accounts",
      "messages",
      "drafts",
      "selectedAccountId",
      "selectedFolder",
      "selectedMessageId",
      "search",
      "replyDraft",
      "lastSyncAt",
      "mcel"
    ]);

    const emailMailServicePresets = Object.freeze({
      system: {
        id: "system",
        label: "Email setup",
        domainHints: [],
        defaultProtocol: "imap",
        imap: {host: "", port: 993, security: "ssl"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "", port: 587, security: "starttls"},
        authHint: "These local setup notes explain the IMAP / POP3 mail client."
      },
      custom: {
        id: "custom",
        label: "Custom mail server",
        domainHints: [],
        defaultProtocol: "imap",
        imap: {host: "", port: 993, security: "ssl"},
        pop3: {host: "", port: 995, security: "ssl"},
        smtp: {host: "", port: 587, security: "starttls"},
        authHint: "Use the username, password, app password, or bridge credential supplied by the mail provider."
      },
      gmail: {
        id: "gmail",
        label: "Gmail mail servers",
        domainHints: ["gmail.com", "googlemail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.gmail.com", port: 993, security: "ssl"},
        pop3: {host: "pop.gmail.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.gmail.com", port: 587, security: "starttls"},
        authHint: "Use a Google app password when the account allows one."
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
        authHint: "Tenant policy may require an app password or explicit IMAP/POP/SMTP permission."
      },
      yahoo: {
        id: "yahoo",
        label: "Yahoo mail servers",
        domainHints: ["yahoo.com", "ymail.com", "rocketmail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.mail.yahoo.com", port: 993, security: "ssl"},
        pop3: {host: "pop.mail.yahoo.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.mail.yahoo.com", port: 465, security: "ssl"},
        authHint: "Use the provider's app-password mail-server path when available."
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
        authHint: "Fastmail supports app passwords for IMAP, POP, and SMTP."
      },
      zoho: {
        id: "zoho",
        label: "Zoho Mail",
        domainHints: ["zoho.com", "zohomail.com"],
        defaultProtocol: "imap",
        imap: {host: "imap.zoho.com", port: 993, security: "ssl"},
        pop3: {host: "pop.zoho.com", port: 995, security: "ssl"},
        smtp: {host: "smtp.zoho.com", port: 465, security: "ssl"},
        authHint: "Zoho account policy can require app passwords or IMAP/POP enablement."
      },
      protonbridge: {
        id: "protonbridge",
        label: "Proton Mail Bridge",
        domainHints: ["proton.me", "protonmail.com"],
        defaultProtocol: "imap",
        imap: {host: "127.0.0.1", port: 1143, security: "starttls"},
        pop3: {host: "127.0.0.1", port: 995, security: "ssl"},
        smtp: {host: "127.0.0.1", port: 1025, security: "starttls"},
        authHint: "Use the local Proton Mail Bridge credentials and ports."
      }
    });

    const emailSeedAccounts = Object.freeze([]);
    const emailSeedMessages = Object.freeze([
      {
        id: "setup-welcome",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "Main Computer <mail@maincomputer.local>",
        to: "you",
        subject: "Email is IMAP / POP3 only now",
        excerpt: "Provider-specific sign-in paths were removed. Add a mail server account and use Check mail.",
        body: "Open Config to add an IMAP or POP3 account.\n\nUse an app password when a provider requires one. Passwords are never saved by the browser UI; they are sent only for an explicit Check mail action.",
        date: new Date().toISOString(),
        labels: ["Setup", "Mail servers"],
        priority: true,
        unread: true
      },
      {
        id: "setup-mcel",
        accountId: "system",
        provider: "system",
        folder: "inbox",
        from: "MCEL Layout <layout@maincomputer.local>",
        to: "you",
        subject: "Email layout is routed through MCEL",
        excerpt: "The email shell, rail, workspace, forms, and empty states use MCEL layout attributes as the layout layer.",
        body: "MCEL owns the layout contract for this app. The email shell, account rail, mailbox workspace, config form, cards, and empty states are laid out through data-mcel layout regions instead of provider-specific handoff surfaces.",
        date: new Date(Date.now() - 3600_000).toISOString(),
        labels: ["MCEL", "Layout"],
        priority: false,
        unread: false
      }
    ]);

    const emailAppState = {
      initialized: false,
      activeTab: "mail",
      activeConfigTab: "raw",
      activeMailView: "list",
      accounts: [],
      messages: [],
      drafts: [],
      selectedAccountId: "all",
      selectedFolder: "inbox",
      selectedMessageId: "",
      search: "",
      replyDraft: "",
      lastSyncAt: "",
      mcel: {status: "pending", updatedAt: ""}
    };

    function emailNowIso() {
      return new Date().toISOString();
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

    function emailProviderLabel(providerId) {
      return (emailMailServicePresets[providerId] || emailMailServicePresets.custom).label;
    }

    function emailPreset(presetId) {
      return emailMailServicePresets[presetId] || emailMailServicePresets.custom;
    }

    function emailNormalizeAddress(value) {
      return String(value || "").trim().toLowerCase();
    }

    function emailValidAddress(address) {
      return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(emailNormalizeAddress(address));
    }

    function emailAddressProviderGuess(address) {
      const domain = emailNormalizeAddress(address).split("@").pop() || "";
      return Object.values(emailMailServicePresets).find((provider) => (provider.domainHints || []).includes(domain))?.id || "";
    }

    function emailNewState() {
      return {
        initialized: false,
        activeTab: "mail",
        activeConfigTab: "raw",
        activeMailView: "list",
        accounts: emailSeedAccounts.map((account) => ({
          ...account,
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
        lastSyncAt: emailNowIso(),
        mcel: {
          status: "layout-layer-active",
          updatedAt: emailNowIso()
        }
      };
    }

    function emailEnsureStateShape() {
      emailAppState.accounts = Array.isArray(emailAppState.accounts) ? emailAppState.accounts : [];
      emailAppState.messages = Array.isArray(emailAppState.messages) ? emailAppState.messages : [];
      emailAppState.drafts = Array.isArray(emailAppState.drafts) ? emailAppState.drafts : [];
      emailAppState.activeTab = ["mail", "config"].includes(emailAppState.activeTab) ? emailAppState.activeTab : "mail";
      emailAppState.activeConfigTab = "raw";
      emailAppState.activeMailView = ["list", "thread"].includes(emailAppState.activeMailView) ? emailAppState.activeMailView : "list";
      emailAppState.selectedAccountId = String(emailAppState.selectedAccountId || "all");
      emailAppState.selectedFolder = String(emailAppState.selectedFolder || "inbox");
      emailAppState.selectedMessageId = String(emailAppState.selectedMessageId || "");
      emailAppState.search = String(emailAppState.search || "");
      emailAppState.replyDraft = String(emailAppState.replyDraft || "");
      emailAppState.lastSyncAt = String(emailAppState.lastSyncAt || "");
      emailAppState.mcel = {
        status: "layout-layer-active",
        updatedAt: emailNowIso()
      };
    }

    function emailRestorePersistedState(saved) {
      if (!saved || typeof saved !== "object" || Array.isArray(saved)) return;
      emailRestorableStateKeys.forEach((key) => {
        if (Object.prototype.hasOwnProperty.call(saved, key)) {
          emailAppState[key] = saved[key];
        }
      });
    }

    function emailLoadState() {
      Object.assign(emailAppState, emailNewState());
      try {
        emailRestorePersistedState(JSON.parse(localStorage.getItem(emailStorageKey) || "{}"));
      } catch (error) {
        console.warn("Email state reset after load failure", error);
      }
      emailAppState.activeTab = "mail";
      emailAppState.activeConfigTab = "raw";
      emailEnsureStateShape();
    }

    function emailSaveState() {
      emailEnsureStateShape();
      try {
        localStorage.setItem(emailStorageKey, JSON.stringify({
          activeMailView: emailAppState.activeMailView,
          accounts: emailAppState.accounts,
          messages: emailAppState.messages,
          drafts: emailAppState.drafts,
          selectedAccountId: emailAppState.selectedAccountId,
          selectedFolder: emailAppState.selectedFolder,
          selectedMessageId: emailAppState.selectedMessageId,
          search: emailAppState.search,
          replyDraft: emailAppState.replyDraft,
          lastSyncAt: emailAppState.lastSyncAt,
          mcel: emailAppState.mcel
        }));
      } catch (error) {
        console.warn("Email state save failed", error);
      }
    }

    function emailTabFromLocation(search = window.location.search) {
      try {
        const requested = new URLSearchParams(String(search || "")).get(emailTabQueryKey);
        return requested === "config" ? "config" : "mail";
      } catch {
        return "mail";
      }
    }

    function syncEmailTabRoute(tabName, {replace = false} = {}) {
      if (typeof window === "undefined") return;
      if (typeof applicationFromPath === "function" && applicationFromPath(window.location.pathname) !== "email") return;
      const normalizedTab = tabName === "config" ? "config" : "mail";
      const url = new URL(window.location.href);
      if (normalizedTab === "config") {
        url.searchParams.set(emailTabQueryKey, "config");
      } else {
        url.searchParams.delete(emailTabQueryKey);
      }
      const nextUrl = `${url.pathname}${url.search}${url.hash}`;
      if (`${window.location.pathname}${window.location.search}${window.location.hash}` === nextUrl) return;
      const state = {...(window.history.state || {}), app: "email", emailTab: normalizedTab};
      if (replace) {
        window.history.replaceState(state, "", nextUrl);
      } else {
        window.history.pushState(state, "", nextUrl);
      }
    }

    function emailSetStatus(text) {
      if (emailSyncStatus) emailSyncStatus.textContent = text;
    }

    function emailActivateMcelLayout() {
      if (!emailApp) return;
      emailApp.setAttribute("data-mcel-state", "layout-layer-active");
      emailApp.setAttribute("data-mcel-fit", "app-fill");
      emailAppState.mcel = {status: "layout-layer-active", updatedAt: emailNowIso()};
    }

    function emailSwitchTab(tabName, options = {}) {
      const syncRoute = options.syncRoute !== false;
      const saveState = options.saveState !== false;
      emailAppState.activeTab = tabName === "config" ? "config" : "mail";
      emailTabButtons.forEach((button) => {
        const active = button.dataset.emailTab === emailAppState.activeTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      emailTabPanels.forEach((panel) => {
        panel.hidden = panel.dataset.emailPanel !== emailAppState.activeTab;
        panel.classList.toggle("email-tab-panel-active", panel.dataset.emailPanel === emailAppState.activeTab);
      });
      if (emailAppState.activeTab === "config") {
        emailApplyServicePreset(emailImapPresetSelect?.value || "outlook");
      }
      if (syncRoute) syncEmailTabRoute(emailAppState.activeTab, {replace: Boolean(options.replaceRoute)});
      if (saveState) emailSaveState();
    }

    function emailSetMailView(viewName) {
      emailAppState.activeMailView = viewName === "thread" ? "thread" : "list";
      if (emailMailMain) emailMailMain.dataset.emailMailView = emailAppState.activeMailView;
      if (emailListView) emailListView.hidden = emailAppState.activeMailView !== "list";
      if (emailThreadView) emailThreadView.hidden = emailAppState.activeMailView !== "thread";
      emailSaveState();
    }

    function emailSelectedMessage() {
      return emailAppState.messages.find((message) => message.id === emailAppState.selectedMessageId) || null;
    }

    function emailSelectedAccount() {
      if (emailAppState.selectedAccountId === "all") return null;
      return emailAppState.accounts.find((account) => account.id === emailAppState.selectedAccountId) || null;
    }

    function emailFilteredMessages() {
      const query = emailNormalizeAddress(emailAppState.search);
      return emailAppState.messages.filter((message) => {
        if (emailAppState.selectedAccountId !== "all" && message.accountId !== emailAppState.selectedAccountId) return false;
        const folder = emailAppState.selectedFolder;
        if (folder === "priority" && !message.priority) return false;
        if (folder !== "all" && folder !== "priority" && message.folder !== folder) return false;
        if (!query) return true;
        return [message.from, message.to, message.subject, message.excerpt, message.body, ...(message.labels || [])]
          .join(" ")
          .toLowerCase()
          .includes(query);
      }).sort((left, right) => String(right.date || "").localeCompare(String(left.date || "")));
    }

    function emailRenderTabs() {
      emailTabButtons.forEach((button) => {
        const active = button.dataset.emailTab === emailAppState.activeTab;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
      emailTabPanels.forEach((panel) => {
        const active = panel.dataset.emailPanel === emailAppState.activeTab;
        panel.hidden = !active;
        panel.classList.toggle("email-tab-panel-active", active);
      });
    }

    function emailRenderAccounts() {
      if (emailAccountSummary) {
        emailAccountSummary.textContent = emailAppState.accounts.length
          ? `${emailAppState.accounts.length} IMAP/POP3 account${emailAppState.accounts.length === 1 ? "" : "s"}`
          : "No accounts connected.";
      }
      if (emailAccountList) {
        const accountButtons = [
          `<button type="button" class="email-account-button ${emailAppState.selectedAccountId === "all" ? "active" : ""}" data-email-account="all">
            <strong>All accounts</strong>
            <span>${emailAppState.accounts.length || 0} configured</span>
          </button>`,
          ...emailAppState.accounts.map((account) => `
            <button type="button" class="email-account-button ${emailAppState.selectedAccountId === account.id ? "active" : ""}" data-email-account="${emailEscapeHtml(account.id)}">
              <strong>${emailEscapeHtml(account.displayName || account.address)}</strong>
              <span>${emailEscapeHtml(emailProviderLabel(account.provider))} · ${emailEscapeHtml((account.protocol || "imap").toUpperCase())} · ${emailEscapeHtml(account.status || "saved")}</span>
            </button>`)
        ];
        emailAccountList.innerHTML = accountButtons.join("");
        emailAccountList.querySelectorAll("[data-email-account]").forEach((button) => {
          button.addEventListener("click", () => {
            emailAppState.selectedAccountId = button.dataset.emailAccount || "all";
            emailAppState.activeMailView = "list";
            emailAppState.selectedMessageId = "";
            emailSaveState();
            renderEmailApp();
          });
        });
      }
      if (emailRawAccountList) {
        emailRawAccountList.innerHTML = emailAppState.accounts.length
          ? emailAppState.accounts.map((account) => `
              <article class="email-config-account">
                <strong>${emailEscapeHtml(account.displayName || account.address)}</strong>
                <span>${emailEscapeHtml(account.address)} · ${emailEscapeHtml((account.protocol || "imap").toUpperCase())} · ${emailEscapeHtml(account.incoming?.host || "")}</span>
              </article>`).join("")
          : "No mail server accounts staged yet.";
      }
    }

    function emailRenderFolderCounts() {
      const counts = emailAppState.messages.reduce((acc, message) => {
        acc.all += 1;
        acc[message.folder] = (acc[message.folder] || 0) + 1;
        if (message.priority) acc.priority += 1;
        return acc;
      }, {all: 0, inbox: 0, sent: 0, drafts: emailAppState.drafts.length, priority: 0, trash: 0, spam: 0});
      Object.entries(counts).forEach(([folder, count]) => {
        const node = document.querySelector(`#email-count-${CSS.escape(folder)}`);
        if (node) node.textContent = String(count);
      });
      emailFolderButtons.forEach((button) => {
        const active = button.dataset.emailFolder === emailAppState.selectedFolder;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
    }

    function emailRenderMessageList() {
      if (!emailMessageList) return;
      const messages = emailFilteredMessages();
      if (!messages.length) {
        emailMessageList.innerHTML = `
          <article class="email-empty-state" data-mcel-layout-region="email-empty-state" data-mcel-role="mail-empty-state" data-mcel-kind="empty-state" data-mcel-fit="centered-empty-card">
            <strong>No messages here</strong>
            <span>Add an IMAP/POP3 account, enter the password or app password, then Check mail.</span>
          </article>`;
        return;
      }
      emailMessageList.innerHTML = messages.map((message) => `
        <button type="button" class="email-message-item ${message.id === emailAppState.selectedMessageId ? "active" : ""} ${message.unread ? "unread" : ""}" data-email-message="${emailEscapeHtml(message.id)}">
          <span class="email-row-check" aria-hidden="true"></span>
          <span class="email-row-unread" aria-hidden="true">${message.unread ? "•" : ""}</span>
          <span class="email-row-sender">${emailEscapeHtml(message.from || "(unknown sender)")}</span>
          <span class="email-row-subject"><strong>${emailEscapeHtml(message.subject || "(no subject)")}</strong><span>${emailEscapeHtml(message.excerpt || "")}</span></span>
          <span class="email-row-date">${emailEscapeHtml(emailFormatTime(message.date))}</span>
        </button>`).join("");
      emailMessageList.querySelectorAll("[data-email-message]").forEach((button) => {
        button.addEventListener("click", () => {
          emailAppState.selectedMessageId = button.dataset.emailMessage || "";
          const selected = emailSelectedMessage();
          if (selected) selected.unread = false;
          emailSetMailView("thread");
          emailSaveState();
          renderEmailApp();
        });
      });
    }

    function emailRenderThread() {
      const message = emailSelectedMessage();
      if (!message) {
        if (emailThreadProvider) emailThreadProvider.textContent = "No message selected";
        if (emailThreadSubject) emailThreadSubject.textContent = "Select a message";
        if (emailThreadMeta) emailThreadMeta.textContent = "Open a message from the list.";
        if (emailThreadLabels) emailThreadLabels.innerHTML = "";
        if (emailThreadBody) emailThreadBody.textContent = "Choose a message to read it here.";
        return;
      }
      const account = emailAppState.accounts.find((item) => item.id === message.accountId);
      if (emailThreadProvider) emailThreadProvider.textContent = `${emailProviderLabel(message.provider)}${account ? ` · ${account.address}` : ""}`;
      if (emailThreadSubject) emailThreadSubject.textContent = message.subject || "(no subject)";
      if (emailThreadMeta) emailThreadMeta.textContent = `${message.from || "(unknown sender)"} → ${message.to || "you"} · ${emailFormatTime(message.date)}`;
      if (emailThreadLabels) {
        emailThreadLabels.innerHTML = (message.labels || []).map((label) => `<span class="email-label">${emailEscapeHtml(label)}</span>`).join("");
      }
      if (emailThreadBody) emailThreadBody.textContent = message.body || message.excerpt || "";
    }

    function emailRenderServerFormDefaults() {
      const preset = emailPreset(emailImapPresetSelect?.value || "outlook");
      if (emailServerConfigStatus) emailServerConfigStatus.textContent = preset.authHint || "Add an IMAP/POP3 account.";
    }

    function emailLiveMessageToState(message, account, fallbackProvider = "custom") {
      return {
        id: String(message.id || `live-${Date.now()}-${Math.random().toString(16).slice(2)}`),
        accountId: account.id,
        provider: account.provider || fallbackProvider || "custom",
        folder: "inbox",
        from: String(message.from || "(unknown sender)"),
        to: String(message.to || account.address),
        subject: String(message.subject || "(no subject)"),
        excerpt: String(message.excerpt || "Fetched through the local mail bridge.").slice(0, 220),
        body: String(message.body || message.excerpt || "Fetched through the local mail bridge."),
        date: message.date || emailNowIso(),
        labels: Array.isArray(message.labels) ? message.labels.slice(0, 6) : ["Live"],
        priority: Boolean(message.priority),
        unread: message.unread !== false
      };
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
      emailRenderServerFormDefaults();
    }

    function emailApplyProtocolForCurrentPreset() {
      const preset = emailPreset(emailImapPresetSelect?.value || "custom");
      const protocol = emailImapProtocolSelect?.value || preset.defaultProtocol || "imap";
      const incoming = preset[protocol] || preset.imap || {};
      if (emailIncomingHostInput) emailIncomingHostInput.value = incoming.host || "";
      if (emailIncomingPortInput) emailIncomingPortInput.value = String(incoming.port || "");
      if (emailIncomingSecuritySelect) emailIncomingSecuritySelect.value = incoming.security || "ssl";
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
      const preset = emailPreset(provider);
      const protocol = emailImapProtocolSelect?.value || preset.defaultProtocol || "imap";
      const address = emailNormalizeAddress(emailImapAddressInput?.value || "");
      const username = String(emailImapUsernameInput?.value || address).trim();
      const config = {
        provider,
        providerLabel: preset.label,
        address,
        displayName: String(emailImapDisplayNameInput?.value || "").trim(),
        protocol,
        host: String(emailIncomingHostInput?.value || "").trim(),
        port: Number.parseInt(String(emailIncomingPortInput?.value || ""), 10),
        security: emailIncomingSecuritySelect?.value || "ssl",
        username,
        incoming: {
          host: String(emailIncomingHostInput?.value || "").trim(),
          port: Number.parseInt(String(emailIncomingPortInput?.value || ""), 10),
          security: emailIncomingSecuritySelect?.value || "ssl"
        },
        smtp: {
          host: String(emailSmtpHostInput?.value || "").trim(),
          port: Number.parseInt(String(emailSmtpPortInput?.value || ""), 10),
          security: emailSmtpSecuritySelect?.value || "starttls"
        }
      };
      if (includePassword) {
        config.password = String(emailImapPasswordInput?.value || "");
      }
      return config;
    }

    function emailValidateServerConfig(config, {passwordRequired = false} = {}) {
      if (!emailValidAddress(config.address)) return "Enter a valid account email address.";
      if (!["imap", "pop3"].includes(config.protocol)) return "Protocol must be IMAP or POP3.";
      if (!config.host) return "Incoming server is required.";
      if (!Number.isFinite(config.port) || config.port < 1 || config.port > 65535) return "Incoming port must be between 1 and 65535.";
      if (!config.username) return "Username is required.";
      if (!config.smtp.host) return "SMTP server is required.";
      if (!Number.isFinite(config.smtp.port) || config.smtp.port < 1 || config.smtp.port > 65535) return "SMTP port must be between 1 and 65535.";
      if (passwordRequired && !config.password) return "Enter the password or app password for this Check mail action.";
      return "";
    }

    function emailUpsertServerAccount(config, status = "saved") {
      const existing = emailAppState.accounts.find((account) => account.address === config.address && account.provider === config.provider);
      const account = existing || {
        id: `server-${config.provider}-${Date.now()}`,
        provider: config.provider,
        address: config.address,
        displayName: config.displayName || config.address,
        connectionType: "server",
        connectedAt: emailNowIso()
      };
      account.status = status;
      account.protocol = config.protocol;
      account.username = config.username;
      account.incoming = {...config.incoming};
      account.smtp = {...config.smtp};
      if (!existing) emailAppState.accounts.unshift(account);
      emailAppState.selectedAccountId = account.id;
      emailAppState.lastSyncAt = emailNowIso();
      return account;
    }

    function emailSaveConfiguredServerAccount() {
      const config = emailServerFormValues();
      const error = emailValidateServerConfig(config);
      if (error) {
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = error;
        emailSetStatus("account setup incomplete");
        return null;
      }
      const account = emailUpsertServerAccount(config, "saved");
      emailSaveState();
      renderEmailApp();
      if (emailServerConfigStatus) emailServerConfigStatus.textContent = `${config.providerLabel} account saved for ${config.address}. Use Check mail when you are ready to send the password to the backend bridge.`;
      emailSetStatus("account saved");
      return account;
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
      if (emailServerConfigStatus) emailServerConfigStatus.textContent = `Checking ${config.providerLabel} through ${config.protocol.toUpperCase()}…`;
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
        emailAppState.selectedFolder = "inbox";
        emailSwitchTab("mail", {saveState: false});
        emailAppState.activeMailView = "list";
        emailAppState.replyDraft = "";
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

    function emailSelectedAccountForCompose() {
      if (emailAppState.selectedAccountId !== "all") {
        return emailAppState.accounts.find((account) => account.id === emailAppState.selectedAccountId) || emailAppState.accounts[0] || null;
      }
      return emailAppState.accounts[0] || null;
    }

    function emailAddressFromHeader(value) {
      const text = String(value || "").trim();
      const bracketed = text.match(/<([^>]+)>/);
      if (bracketed) return bracketed[1].trim();
      return text.split(/[,;]/)[0].trim();
    }

    function emailLocalSentMessageFromDraft(draft, labels = ["Queued", "Local"], extra = {}) {
      return {
        id: extra.id || `queued-${Date.now()}`,
        accountId: draft.accountId,
        provider: draft.provider,
        folder: "sent",
        from: draft.from,
        to: draft.to,
        subject: draft.subject,
        excerpt: draft.body.slice(0, 140),
        body: draft.body,
        date: draft.date,
        labels,
        priority: false,
        unread: false,
        ...extra
      };
    }

    async function emailSaveDraftFromCompose({queued = false} = {}) {
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
        emailAppState.messages.unshift(emailLocalSentMessageFromDraft(draft));
      }
      emailSaveState();
      renderEmailApp();
      if (emailComposeStatus) {
        emailComposeStatus.textContent = queued
          ? "Message queued locally. SMTP delivery bridge is not active yet."
          : "Draft saved locally.";
      }
      emailSetStatus(queued ? "send queued locally" : "draft saved");
      if (queued) emailSetComposeModalOpen(false);
      return draft;
    }

    async function emailSaveInlineReply({queued = true} = {}) {
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
      const account = emailAppState.accounts.find((item) => item.id === message.accountId) || emailSelectedAccountForCompose();
      const subject = message.subject.startsWith("Re:") ? message.subject : `Re: ${message.subject}`;
      const draft = {
        id: `reply-${Date.now()}`,
        accountId: account?.id || message.accountId || "local",
        provider: account?.provider || message.provider || "custom",
        from: account?.address || message.to || "local@maincomputer.local",
        to: emailAddressFromHeader(message.from),
        subject,
        body,
        date: emailNowIso(),
        queued: Boolean(queued),
        tone: "reply"
      };
      emailAppState.drafts.unshift(draft);
      if (queued) {
        emailAppState.messages.unshift(emailLocalSentMessageFromDraft(draft, ["Queued", "Reply"], {id: `queued-reply-${Date.now()}`}));
      }
      emailAppState.replyDraft = "";
      if (emailReplyBody) emailReplyBody.value = "";
      if (emailReplyStatus) emailReplyStatus.textContent = queued ? "Reply queued locally." : "Reply draft saved.";
      emailSetStatus(queued ? "reply queued locally" : "reply draft saved");
      emailSaveState();
      renderEmailApp();
      return draft;
    }

    function emailSetComposeModalOpen(open) {
      if (!emailComposeModal) return;
      if (open) {
        emailComposeModal.hidden = false;
        if (typeof emailComposeModal.showModal === "function") emailComposeModal.showModal();
        emailComposeTo?.focus();
      } else {
        if (emailComposeModal.open && typeof emailComposeModal.close === "function") emailComposeModal.close();
        emailComposeModal.hidden = true;
      }
    }

    function emailSmartDraftFromContext() {
      const selected = emailSelectedMessage();
      const tone = emailComposeTone?.value || "concise";
      const subject = selected ? (selected.subject.startsWith("Re:") ? selected.subject : `Re: ${selected.subject}`) : "Follow up";
      const body = selected
        ? `Hi,\n\nThanks for the note about "${selected.subject}".\n\nI'll follow up with a ${tone} response shortly.\n\nBest,`
        : `Hi,\n\nWriting a ${tone} note from Main Computer.\n\nBest,`;
      if (emailComposeSubject && !emailComposeSubject.value.trim()) emailComposeSubject.value = subject;
      if (emailComposeBody && !emailComposeBody.value.trim()) emailComposeBody.value = body;
      if (emailComposeStatus) emailComposeStatus.textContent = "Smart draft inserted locally.";
      emailSetStatus("smart draft ready");
    }

    function emailArchiveSelected() {
      const message = emailSelectedMessage();
      if (!message) return;
      message.folder = "all";
      message.unread = false;
      emailSetStatus("message archived locally");
      emailAppState.activeMailView = "list";
      emailSaveState();
      renderEmailApp();
    }

    function emailTogglePrioritySelected() {
      const message = emailSelectedMessage();
      if (!message) return;
      message.priority = !message.priority;
      emailSetStatus(message.priority ? "message starred" : "message unstarred");
      emailSaveState();
      renderEmailApp();
    }

    function emailRefreshLocal() {
      emailAppState.lastSyncAt = emailNowIso();
      emailSetStatus("local mail state refreshed");
      renderEmailApp();
    }

    function emailCheckSelectedAccount() {
      const selected = emailSelectedAccount() || emailAppState.accounts[0] || null;
      if (!selected) {
        emailSwitchTab("config");
        if (emailServerConfigStatus) emailServerConfigStatus.textContent = "Add an IMAP/POP3 account before checking mail.";
        emailSetStatus("add account to check mail");
        return null;
      }
      emailSwitchTab("config");
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
      return null;
    }

    function renderEmailApp() {
      emailActivateMcelLayout();
      emailRenderTabs();
      emailRenderAccounts();
      emailRenderFolderCounts();
      emailRenderMessageList();
      emailRenderThread();
      emailRenderServerFormDefaults();
      if (emailSearchInput && emailSearchInput.value !== emailAppState.search) emailSearchInput.value = emailAppState.search;
      emailSetMailView(emailAppState.activeMailView);
    }

    function emailBindEvents() {
      emailTabButtons.forEach((button) => {
        button.addEventListener("click", () => emailSwitchTab(button.dataset.emailTab || "mail"));
      });
      emailOpenConfig?.addEventListener("click", () => emailSwitchTab("config"));
      emailCheckMail?.addEventListener("click", emailCheckSelectedAccount);
      emailNewMessage?.addEventListener("click", () => emailSetComposeModalOpen(true));
      emailBackToList?.addEventListener("click", () => emailSetMailView("list"));
      emailRefresh?.addEventListener("click", emailRefreshLocal);
      emailSearchInput?.addEventListener("input", () => {
        emailAppState.search = String(emailSearchInput.value || "");
        emailSaveState();
        renderEmailApp();
      });
      emailFolderButtons.forEach((button) => {
        button.addEventListener("click", () => {
          emailAppState.selectedFolder = button.dataset.emailFolder || "inbox";
          emailAppState.activeMailView = "list";
          emailAppState.selectedMessageId = "";
          emailSaveState();
          renderEmailApp();
        });
      });
      emailImapPresetSelect?.addEventListener("change", () => emailApplyServicePreset(emailImapPresetSelect.value || "custom"));
      emailImapProtocolSelect?.addEventListener("change", emailApplyProtocolForCurrentPreset);
      emailImapAddressInput?.addEventListener("input", () => emailMaybeApplyPresetFromAddress(emailImapAddressInput.value));
      emailSaveServerAccount?.addEventListener("click", emailSaveConfiguredServerAccount);
      emailCheckServerAccount?.addEventListener("click", emailCheckConfiguredServerAccount);
      emailCloseCompose?.addEventListener("click", () => emailSetComposeModalOpen(false));
      emailComposeForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        emailSaveDraftFromCompose({queued: true});
      });
      emailSaveDraft?.addEventListener("click", () => emailSaveDraftFromCompose({queued: false}));
      emailSmartDraft?.addEventListener("click", emailSmartDraftFromContext);
      emailArchiveMessage?.addEventListener("click", emailArchiveSelected);
      emailMarkPriority?.addEventListener("click", emailTogglePrioritySelected);
      emailReplyMessage?.addEventListener("click", () => {
        if (!emailSelectedMessage()) return;
        if (emailReplyForm) emailReplyForm.hidden = false;
        emailReplyBody?.focus();
      });
      emailCancelReply?.addEventListener("click", () => {
        if (emailReplyForm) emailReplyForm.hidden = true;
        if (emailReplyBody) emailReplyBody.value = "";
      });
      emailReplyForm?.addEventListener("submit", (event) => {
        event.preventDefault();
        emailSaveInlineReply({queued: true});
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
      emailSwitchTab(emailTabFromLocation(), {syncRoute: false, saveState: false});
      emailSetStatus("email ready");
      renderEmailApp();
      emailSearchInput?.focus();
    }

    if (typeof window !== "undefined") {
      window.initEmailApp = initEmailApp;
      window.emailCheckServerAccount = emailCheckConfiguredServerAccount;
      window.emailSaveServerAccount = emailSaveConfiguredServerAccount;
      window.emailMcelLayoutActive = () => emailApp?.getAttribute("data-mcel-state") === "layout-layer-active";
    }
