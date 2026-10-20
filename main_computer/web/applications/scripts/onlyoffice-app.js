    const onlyofficeState = {
      initialized: false,
      files: [],
      selectedPath: "",
      documentServerUrl: "",
      editor: null,
      loadingApi: null,
      serverStatus: null,
      advancedInitialized: false,
      advancedChatController: null,
      advancedConnector: null,
    };

    function onlyofficeSetStatus(message) {
      if (onlyofficeStatus) onlyofficeStatus.textContent = message;
    }

    function onlyofficeSetLibraryStatus(message) {
      if (onlyofficeLibraryStatus) onlyofficeLibraryStatus.textContent = message;
    }

    function onlyofficeSetServerStatus(message, {offline = false} = {}) {
      if (onlyofficeServerStatus) {
        onlyofficeServerStatus.textContent = message;
        onlyofficeServerStatus.classList.toggle("offline", Boolean(offline));
      }
    }

    function onlyofficeSetServerUrl(message) {
      if (onlyofficeServerUrl) onlyofficeServerUrl.textContent = message;
    }

    function onlyofficeSetAdvancedPaneOpen(open, {mountChat = true} = {}) {
      const shouldOpen = Boolean(open) && document.body.dataset.activeApp === "onlyoffice";
      if (stageAdvancedPane) stageAdvancedPane.hidden = !shouldOpen;
      if (stageAdvancedToggle) stageAdvancedToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      stageAdvanced?.classList.toggle("open", shouldOpen);
      if (shouldOpen && mountChat) onlyofficeMountAdvancedChat();
    }

    function onlyofficeMountAdvancedChat() {
      if (!onlyofficeAdvancedChatPanel) return null;
      const api = window.MainComputerChatConsole || {};
      const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;
      if (!mount) {
        onlyofficeAdvancedChatPanel.textContent = "Chat Console add-ons are still loading.";
        onlyofficeSetStatus("Advanced Chat Console is not ready yet");
        return null;
      }
      if (onlyofficeState.advancedChatController) {
        onlyofficeState.advancedChatController.render?.();
        api.renderNotebook?.();
        return onlyofficeState.advancedChatController;
      }
      onlyofficeState.advancedChatController = mount(onlyofficeAdvancedChatPanel, {
        embedId: "onlyoffice",
        activeApp: "onlyoffice",
        idPrefix: "onlyoffice-advanced-chat",
        classPrefix: "onlyoffice-advanced",
        title: "Advanced Chat Console",
        subtitle: "Embedded beside ONLYOFFICE. Use the notebook type tabs for AI, JS, Python, BASIC, and more.",
        notebookId: "onlyoffice-advanced-chat-notebook",
        statusId: "onlyoffice-advanced-chat-status",
        threadTitle: "ONLYOFFICE Advanced Chat",
        initialStatus: "advanced chat ready",
        status(message) {
          if (message) onlyofficeSetStatus(message);
        }
      });
      if (onlyofficeState.advancedChatController) {
        onlyofficeSetStatus("advanced tools ready");
      } else {
        onlyofficeSetStatus("Advanced Chat Console could not mount");
      }
      return onlyofficeState.advancedChatController;
    }

    const onlyofficeAdvancedCodeLabels = {
      javascript: "JS",
      python: "Python",
      basic: "BASIC",
    };

    function onlyofficeSelectedAdvancedCodeType() {
      const checked = document.querySelector('input[name="onlyoffice-advanced-code-type"]:checked');
      const cleanType = String(checked?.value || "javascript").toLowerCase();
      return Object.prototype.hasOwnProperty.call(onlyofficeAdvancedCodeLabels, cleanType) ? cleanType : "javascript";
    }

    function onlyofficeSetAdvancedCodeStatus(message) {
      if (onlyofficeAdvancedCodeStatus) onlyofficeAdvancedCodeStatus.textContent = message;
    }

    function onlyofficeCreateAdvancedCodeComment(language, source) {
      const cleanLanguage = onlyofficeAdvancedCodeLabels[language] ? language : "javascript";
      const label = onlyofficeAdvancedCodeLabels[cleanLanguage];
      return [
        "Main Computer ONLYOFFICE code add-on",
        `Language: ${label}`,
        `Workbook: ${onlyofficeState.selectedPath || "(no workbook selected)"}`,
        "",
        String(source || ""),
      ].join("\n");
    }

    function onlyofficeAdvancedConnector() {
      if (!onlyofficeState.editor || typeof onlyofficeState.editor.createConnector !== "function") {
        return null;
      }
      if (!onlyofficeState.advancedConnector) {
        onlyofficeState.advancedConnector = onlyofficeState.editor.createConnector();
      }
      return onlyofficeState.advancedConnector;
    }

    function onlyofficeAttachAdvancedCodeToSelectedCells() {
      const language = onlyofficeSelectedAdvancedCodeType();
      const label = onlyofficeAdvancedCodeLabels[language];
      const source = String(onlyofficeAdvancedCodeSource?.value || "").trim();

      if (!source) {
        onlyofficeSetAdvancedCodeStatus("Paste code into the code area before attaching it to selected cell(s).");
        onlyofficeAdvancedCodeSource?.focus();
        return;
      }
      if (!onlyofficeState.selectedPath || !onlyofficeState.editor) {
        onlyofficeSetAdvancedCodeStatus("Open an ONLYOFFICE workbook and select cell(s) before attaching code.");
        return;
      }

      const connector = onlyofficeAdvancedConnector();
      if (!connector || typeof connector.callCommand !== "function") {
        onlyofficeSetAdvancedCodeStatus("Selected-cell attach needs the ONLYOFFICE Automation connector. The code area is ready, but the current editor cannot receive cell comments from this pane.");
        onlyofficeSetStatus(`${label} code ready; ONLYOFFICE connector unavailable`);
        return;
      }

      const commentText = onlyofficeCreateAdvancedCodeComment(language, source);
      const command = new Function(`
        const language = ${JSON.stringify(language)};
        const label = ${JSON.stringify(label)};
        const commentText = ${JSON.stringify(commentText)};
        let selection = null;
        if (typeof Api !== "undefined" && Api && typeof Api.GetSelection === "function") {
          selection = Api.GetSelection();
        }
        if (!selection && typeof Api !== "undefined" && Api && typeof Api.GetActiveSheet === "function") {
          const sheet = Api.GetActiveSheet();
          if (sheet && typeof sheet.GetSelection === "function") {
            selection = sheet.GetSelection();
          } else if (sheet && typeof sheet.GetActiveCell === "function") {
            selection = sheet.GetActiveCell();
          }
        }
        if (!selection) {
          return "No selected ONLYOFFICE cell range was available.";
        }
        let address = "";
        try {
          if (typeof selection.GetAddress === "function") {
            address = selection.GetAddress() || "";
          }
        } catch (error) {
          address = "";
        }
        if (typeof selection.AddComment === "function") {
          selection.AddComment(commentText, "Main Computer");
        } else if (typeof selection.ForEach === "function") {
          selection.ForEach(function(cell) {
            if (cell && typeof cell.AddComment === "function") {
              cell.AddComment(commentText, "Main Computer");
            }
          });
        } else {
          return "The selected ONLYOFFICE cell range does not support code add-on comments.";
        }
        return label + " add-on attached" + (address ? " to " + address : " to selected cell(s)") + ".";
      `);

      onlyofficeSetAdvancedCodeStatus(`Attaching ${label} add-on to the selected cell(s)...`);
      try {
        connector.callCommand(command, (result) => {
          const message = typeof result === "string" && result.trim()
            ? result.trim()
            : `${label} add-on attach command finished.`;
          onlyofficeSetAdvancedCodeStatus(message);
          onlyofficeSetStatus(message);
        });
      } catch (error) {
        const message = `Attach failed: ${error.message || error}`;
        onlyofficeSetAdvancedCodeStatus(message);
        onlyofficeSetStatus(message);
      }
    }

    function initOnlyOfficeAdvancedPane() {
      if (onlyofficeState.advancedInitialized) return;
      onlyofficeState.advancedInitialized = true;
      stageAdvancedToggle?.addEventListener("click", () => {
        const isOpen = stageAdvancedToggle.getAttribute("aria-expanded") === "true";
        onlyofficeSetAdvancedPaneOpen(!isOpen);
      });
      stageAdvancedClose?.addEventListener("click", () => {
        onlyofficeSetAdvancedPaneOpen(false, {mountChat: false});
        stageAdvancedToggle?.focus();
      });
      onlyofficeAdvancedAttachCode?.addEventListener("click", () => {
        onlyofficeAttachAdvancedCodeToSelectedCells();
      });
      stageAdvanced?.querySelectorAll("[data-stage-advanced-code-type]").forEach((input) => {
        input.addEventListener("change", () => {
          const label = onlyofficeAdvancedCodeLabels[onlyofficeSelectedAdvancedCodeType()];
          onlyofficeSetAdvancedCodeStatus(`${label} code area selected. Paste code, then attach it to the selected cell(s).`);
        });
      });
      document.addEventListener("click", (event) => {
        if (!stageAdvancedPane || stageAdvancedPane.hidden) return;
        const target = event.target;
        if (target instanceof Node && stageAdvanced?.contains(target)) return;
        onlyofficeSetAdvancedPaneOpen(false, {mountChat: false});
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || !stageAdvancedPane || stageAdvancedPane.hidden) return;
        onlyofficeSetAdvancedPaneOpen(false, {mountChat: false});
        stageAdvancedToggle?.focus();
      });
    }

    window.onlyofficeCloseAdvancedPane = () => onlyofficeSetAdvancedPaneOpen(false, {mountChat: false});
    window.onlyofficeAttachAdvancedCodeToSelectedCells = onlyofficeAttachAdvancedCodeToSelectedCells;

    async function onlyofficeApi(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `ONLYOFFICE request failed (${response.status})`);
      }
      return data;
    }

    function onlyofficeFormatBytes(value) {
      const amount = Number(value || 0);
      if (!Number.isFinite(amount) || amount <= 0) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let size = amount;
      let index = 0;
      while (size >= 1024 && index < units.length - 1) {
        size /= 1024;
        index += 1;
      }
      return index === 0 ? `${Math.round(size)} ${units[index]}` : `${size.toFixed(1)} ${units[index]}`;
    }

    function onlyofficeRenderFiles() {
      if (!onlyofficeFileList) return;
      onlyofficeFileList.textContent = "";
      if (!onlyofficeState.files.length) {
        const empty = document.createElement("div");
        empty.className = "onlyoffice-file-empty";
        empty.textContent = "No XLSX workbooks found.";
        onlyofficeFileList.appendChild(empty);
        return;
      }
      onlyofficeState.files.forEach((file) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "onlyoffice-file-item";
        button.classList.toggle("active", file.path === onlyofficeState.selectedPath);
        button.dataset.path = file.path || "";
        const title = document.createElement("strong");
        title.textContent = file.path || "workbook.xlsx";
        const meta = document.createElement("span");
        meta.textContent = `${onlyofficeFormatBytes(file.bytes)} · ${file.display_path || "onlyoffice"}`;
        button.append(title, meta);
        button.addEventListener("click", () => {
          openOnlyOfficeWorkbook(file.path).catch((error) => {
            onlyofficeSetStatus(`Open failed: ${error.message || error}`);
          });
        });
        onlyofficeFileList.appendChild(button);
      });
    }

    async function refreshOnlyOfficeStatus() {
      try {
        const status = await onlyofficeApi("/api/applications/onlyoffice/status", {probe: true, timeout_s: 1.5});
        onlyofficeState.serverStatus = status;
        const serverOk = Boolean(status.server_probe && status.server_probe.ok);
        const publicUrl = status.public_url || status.document_server_url || "http://127.0.0.1:18085";
        const callbackBase = status.callback_base_url || "";
        onlyofficeSetServerStatus(
          serverOk ? "Document Server online" : "Document Server offline",
          {offline: !serverOk}
        );
        onlyofficeSetServerUrl(`ONLYOFFICE: ${publicUrl}${callbackBase ? ` · callback: ${callbackBase}` : ""}`);
        return status;
      } catch (error) {
        onlyofficeSetServerStatus(`Document Server status failed: ${error.message || error}`, {offline: true});
        return null;
      }
    }

    async function refreshOnlyOfficeFiles({quiet = false} = {}) {
      if (!quiet) onlyofficeSetLibraryStatus("Loading ONLYOFFICE workbooks...");
      const data = await onlyofficeApi("/api/applications/onlyoffice/files", {});
      onlyofficeState.files = Array.isArray(data.files) ? data.files : [];
      onlyofficeRenderFiles();
      onlyofficeSetLibraryStatus(data.count ? `${data.count} XLSX workbook${data.count === 1 ? "" : "s"}` : "No XLSX workbooks found.");
      return data;
    }

    const onlyofficeApiScriptPath = "/web-apps/apps/api/documents/api.js";

    function onlyofficeNormalizeServerUrl(value) {
      const text = String(value || "").trim().replace(/\/+$/, "");
      return text || "";
    }

    function onlyofficePushCandidate(candidates, value) {
      const clean = onlyofficeNormalizeServerUrl(value);
      if (clean && !candidates.includes(clean)) {
        candidates.push(clean);
      }
    }

    function onlyofficeServerUrlVariants(value) {
      const clean = onlyofficeNormalizeServerUrl(value);
      if (!clean) return [];

      const variants = [clean];
      let parsed = null;
      try {
        parsed = new URL(clean, window.location.href);
      } catch {
        return variants;
      }

      if (!/^https?:$/.test(parsed.protocol)) {
        return variants;
      }

      const configuredHost = (parsed.hostname || "").toLowerCase();
      const configuredPort = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
      const localHosts = new Set(["127.0.0.1", "localhost", "::1"]);
      const addHostVariant = (host) => {
        if (!host) return;
        try {
          const clone = new URL(parsed.href);
          clone.hostname = host;
          clone.port = configuredPort;
          onlyofficePushCandidate(variants, clone.href);
        } catch {}
      };

      if (localHosts.has(configuredHost)) {
        addHostVariant("127.0.0.1");
        addHostVariant("localhost");
        const pageHost = String(window.location.hostname || "").trim();
        if (pageHost && !localHosts.has(pageHost.toLowerCase())) {
          addHostVariant(pageHost);
        }
      }

      return variants;
    }

    function onlyofficeCandidateDocumentServerUrls(preferredUrl, status = onlyofficeState.serverStatus) {
      const candidates = [];
      const pushAll = (values) => {
        for (const value of values || []) {
          for (const variant of onlyofficeServerUrlVariants(value)) {
            onlyofficePushCandidate(candidates, variant);
          }
        }
      };

      pushAll([preferredUrl]);
      if (status) {
        pushAll(status.browser_public_url_candidates);
        pushAll([status.public_url, status.document_server_url, status.internal_url]);
      }
      pushAll(["http://127.0.0.1:18085", "http://localhost:18085"]);
      return candidates;
    }

    function onlyofficeApiScriptUrl(baseUrl, attempt) {
      return new URL(`${baseUrl}${onlyofficeApiScriptPath}`, window.location.href).href;
    }

    function onlyofficeDescribeApiLoadFailure(failures) {
      const tried = failures.map((failure) => failure.url).join(", ");
      const last = failures.length ? failures[failures.length - 1].error : "no candidate URL was available";
      const mixedContentHint = window.location.protocol === "https:"
        ? " The page is using HTTPS, so the browser may block an HTTP ONLYOFFICE API URL as mixed content."
        : "";
      return `Could not load ONLYOFFICE API. Tried: ${tried || "none"}. Last error: ${last}.${mixedContentHint}`;
    }

    function onlyofficeLoadApiScript(baseUrl, attempt) {
      return new Promise((resolve, reject) => {
        const script = document.createElement("script");
        let settled = false;
        const finish = (callback, value) => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timeout);
          callback(value);
        };
        const timeout = window.setTimeout(() => {
          script.remove();
          finish(reject, new Error(`timed out loading ${baseUrl}`));
        }, 8000);

        script.src = onlyofficeApiScriptUrl(baseUrl, attempt);
        script.async = true;
        script.referrerPolicy = "no-referrer";
        script.dataset.mainComputerOnlyofficeApi = "1";
        script.dataset.onlyofficeBaseUrl = baseUrl;
        script.onload = () => finish(resolve, baseUrl);
        script.onerror = () => {
          script.remove();
          finish(reject, new Error(`network/browser error loading ${baseUrl}`));
        };
        document.head.appendChild(script);
      });
    }

    async function onlyofficeLoadApiFromCandidates(candidates) {
      const failures = [];
      for (let index = 0; index < candidates.length; index += 1) {
        const candidate = candidates[index];
        try {
          await onlyofficeLoadApiScript(candidate, index + 1);
          if (window.DocsAPI && window.DocsAPI.DocEditor) {
            return candidate;
          }
          failures.push({url: candidate, error: "script loaded but DocsAPI.DocEditor was not found"});
        } catch (error) {
          failures.push({url: candidate, error: error.message || String(error)});
        }
      }
      throw new Error(onlyofficeDescribeApiLoadFailure(failures));
    }

    async function loadOnlyOfficeApi(documentServerUrl) {
      const candidates = onlyofficeCandidateDocumentServerUrls(documentServerUrl);
      if (window.DocsAPI && window.DocsAPI.DocEditor && candidates.includes(onlyofficeState.documentServerUrl)) {
        return onlyofficeState.documentServerUrl;
      }
      if (onlyofficeState.loadingApi) {
        return await onlyofficeState.loadingApi;
      }
      onlyofficeState.loadingApi = onlyofficeLoadApiFromCandidates(candidates).then((loadedUrl) => {
        onlyofficeState.documentServerUrl = loadedUrl;
        return loadedUrl;
      }).finally(() => {
        onlyofficeState.loadingApi = null;
      });
      return await onlyofficeState.loadingApi;
    }

    // Keep the Document Server iframe inside the node owned by DocsAPI.
    // Reparenting a live iframe can reload its browsing context, losing editor state.
    const onlyofficeViewportFixState = {observer: null, timers: []};

    function onlyofficeResetEditorViewportFix() {
      for (const timer of onlyofficeViewportFixState.timers.splice(0)) {
        window.clearTimeout(timer);
      }
      onlyofficeViewportFixState.observer?.disconnect();
      onlyofficeViewportFixState.observer = null;
      onlyofficeEditorHost?.classList.remove("onlyoffice-editor-mounted");
    }

    function onlyofficeScheduleEditorViewportFix() {
      onlyofficeResetEditorViewportFix();
      if (!onlyofficeEditorHost) return;
      onlyofficeEditorHost.classList.add("onlyoffice-editor-mounted");
      const notify = () => window.dispatchEvent(new Event("resize"));
      if (typeof ResizeObserver !== "undefined") {
        onlyofficeViewportFixState.observer = new ResizeObserver(notify);
        onlyofficeViewportFixState.observer.observe(onlyofficeEditorHost);
      }
      onlyofficeViewportFixState.timers.push(window.setTimeout(notify, 0));
      onlyofficeViewportFixState.timers.push(window.setTimeout(notify, 350));
    }

    window.onlyofficeScheduleEditorViewportFix = onlyofficeScheduleEditorViewportFix;
    window.onlyofficeResetEditorViewportFix = onlyofficeResetEditorViewportFix;

    async function openOnlyOfficeWorkbook(path) {
      const cleanPath = String(path || "").trim();
      if (!cleanPath) return;
      onlyofficeResetEditorViewportFix();
      onlyofficeSetStatus("Preparing ONLYOFFICE editor...");
      const data = await onlyofficeApi("/api/applications/onlyoffice/config", {path: cleanPath});
      const loadedDocumentServerUrl = await loadOnlyOfficeApi(data.public_url || data.document_server_url);
      if (loadedDocumentServerUrl && loadedDocumentServerUrl !== (data.public_url || data.document_server_url)) {
        onlyofficeSetServerUrl(`ONLYOFFICE: ${data.public_url || data.document_server_url} · browser API: ${loadedDocumentServerUrl}`);
      }
      if (onlyofficeState.editor && typeof onlyofficeState.editor.destroyEditor === "function") {
        onlyofficeState.editor.destroyEditor();
      }
      onlyofficeState.advancedConnector = null;
      // DocsAPI replaces its mount node with an iframe. Keep the MCEL-owned
      // editor host intact by giving DocsAPI a disposable child mount node.
      if (!onlyofficeEditorHost) {
        throw new Error("ONLYOFFICE editor host is missing");
      }
      onlyofficeEditorHost.textContent = "";
      const editorMount = document.createElement("div");
      editorMount.id = "onlyoffice-editor-mount";
      onlyofficeEditorHost.appendChild(editorMount);
      onlyofficeState.selectedPath = cleanPath;
      if (onlyofficeCurrentPath) onlyofficeCurrentPath.textContent = data.display_path || cleanPath;
      onlyofficeRenderFiles();
      onlyofficeState.editor = new DocsAPI.DocEditor("onlyoffice-editor-mount", data.config);
      onlyofficeSetStatus("ONLYOFFICE editor open");
      onlyofficeScheduleEditorViewportFix();
      await refreshOnlyOfficeStatus();
    }

    async function onlyofficeTryOpenAfterStorageWrite(path, successMessage) {
      onlyofficeState.selectedPath = String(path || "");
      onlyofficeRenderFiles();
      if (onlyofficeCurrentPath && onlyofficeState.selectedPath) {
        onlyofficeCurrentPath.textContent = `onlyoffice/${onlyofficeState.selectedPath}`;
      }
      try {
        await openOnlyOfficeWorkbook(onlyofficeState.selectedPath);
      } catch (error) {
        const message = error && error.message ? error.message : String(error);
        onlyofficeSetStatus(`${successMessage}. Document Server is offline or unreachable: ${message}`);
        onlyofficeSetServerStatus("Document Server offline", {offline: true});
        await refreshOnlyOfficeStatus();
      }
    }

    async function createOnlyOfficeWorkbook() {
      const name = window.prompt("New XLSX workbook name", "Book.xlsx");
      if (!name) return;
      onlyofficeSetStatus("Creating workbook...");
      const data = await onlyofficeApi("/api/applications/onlyoffice/create", {path: name});
      await refreshOnlyOfficeFiles({quiet: true});
      await onlyofficeTryOpenAfterStorageWrite(data.path, "Workbook created");
    }

    function onlyofficeArrayBufferToBase64(buffer) {
      const bytes = new Uint8Array(buffer);
      let binary = "";
      const chunkSize = 0x8000;
      for (let offset = 0; offset < bytes.length; offset += chunkSize) {
        const chunk = bytes.subarray(offset, offset + chunkSize);
        binary += String.fromCharCode.apply(null, chunk);
      }
      return window.btoa(binary);
    }

    async function uploadOnlyOfficeWorkbook(file) {
      if (!file) return;
      if (!/\.xlsx$/i.test(file.name || "")) {
        onlyofficeSetStatus("Upload failed: choose an .xlsx file");
        return;
      }
      onlyofficeSetStatus("Uploading workbook...");
      const content_base64 = onlyofficeArrayBufferToBase64(await file.arrayBuffer());
      const data = await onlyofficeApi("/api/applications/onlyoffice/upload", {
        path: file.name,
        content_base64,
      });
      await refreshOnlyOfficeFiles({quiet: true});
      await onlyofficeTryOpenAfterStorageWrite(data.path, "Workbook uploaded");
    }

    async function forceSaveOnlyOfficeWorkbook() {
      if (!onlyofficeState.selectedPath) {
        onlyofficeSetStatus("No ONLYOFFICE workbook selected");
        return;
      }
      onlyofficeSetStatus("Requesting ONLYOFFICE force-save...");
      await onlyofficeApi("/api/applications/onlyoffice/force-save", {path: onlyofficeState.selectedPath});
      onlyofficeSetStatus("Force-save requested");
    }

    function initOnlyOfficeApp() {
      initOnlyOfficeAdvancedPane();
      if (!onlyofficeApp || onlyofficeState.initialized) return;
      onlyofficeState.initialized = true;
      onlyofficeRefreshFiles?.addEventListener("click", () => {
        Promise.all([
          refreshOnlyOfficeFiles(),
          refreshOnlyOfficeStatus(),
        ]).catch((error) => onlyofficeSetLibraryStatus(`Refresh failed: ${error.message || error}`));
      });
      onlyofficeNewWorkbook?.addEventListener("click", () => {
        createOnlyOfficeWorkbook().catch((error) => onlyofficeSetStatus(`Create failed: ${error.message || error}`));
      });
      onlyofficeUploadTrigger?.addEventListener("click", () => {
        onlyofficeUploadFile?.click();
      });
      onlyofficeUploadFile?.addEventListener("change", () => {
        const file = onlyofficeUploadFile.files && onlyofficeUploadFile.files[0];
        uploadOnlyOfficeWorkbook(file).catch((error) => onlyofficeSetStatus(`Upload failed: ${error.message || error}`));
        onlyofficeUploadFile.value = "";
      });
      onlyofficeSaveNow?.addEventListener("click", () => {
        forceSaveOnlyOfficeWorkbook().catch((error) => onlyofficeSetStatus(`Force-save failed: ${error.message || error}`));
      });
      onlyofficeReloadEditor?.addEventListener("click", () => {
        if (!onlyofficeState.selectedPath) {
          onlyofficeSetStatus("No ONLYOFFICE workbook selected");
          return;
        }
        openOnlyOfficeWorkbook(onlyofficeState.selectedPath).catch((error) => onlyofficeSetStatus(`Reload failed: ${error.message || error}`));
      });
      refreshOnlyOfficeStatus();
      refreshOnlyOfficeFiles().catch((error) => {
        onlyofficeSetLibraryStatus(`ONLYOFFICE files failed: ${error.message || error}`);
        onlyofficeSetStatus("ONLYOFFICE storage unavailable");
      });
    }
