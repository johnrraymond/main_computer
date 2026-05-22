    function applicationFromPath(pathname = window.location.pathname) {
      const cleaned = String(pathname || "").replace(/\/+$/, "") || "/";
      const parts = cleaned.split("/").filter(Boolean);
      if (!parts.length) return "calculator";
      if (!["applications", "apps", "app"].includes(parts[0])) return "calculator";
      if (parts.length < 2) return "calculator";
      const candidate = parts[1] === "layout-builder" ? "game-editor" : parts[1];
      return routeableApps.has(candidate) ? candidate : "calculator";
    }

    function syncApplicationRoute(appName, {replace = false} = {}) {
      const selectedWebsiteBuilderSite = typeof websiteBuilderStateModel !== "undefined"
        ? websiteBuilderStateModel.selectedSiteId
        : "";
      const nextPath = appName === "task-manager"
        ? taskManagerTabPath(taskNotebookTabFromPath(window.location.pathname))
        : appName === "website-builder"
          ? websiteBuilderPath(selectedWebsiteBuilderSite || websiteBuilderSiteIdFromPath(window.location.pathname))
          : applicationPath(appName);
      if (window.location.pathname === nextPath) return;
      const state = {app: appName};
      if (replace) {
        window.history.replaceState(state, "", nextPath);
      } else {
        window.history.pushState(state, "", nextPath);
      }
    }

    function syncWebsiteBuilderRoute(siteId, {replace = false} = {}) {
      if (applicationFromPath(window.location.pathname) !== "website-builder") return;
      const routeSiteId = normalizeWebsiteBuilderRouteSiteId(siteId);
      const nextPath = websiteBuilderPath(routeSiteId);
      if (window.location.pathname === nextPath) return;
      const state = {app: "website-builder", websiteSite: routeSiteId};
      if (replace) {
        window.history.replaceState(state, "", nextPath);
      } else {
        window.history.pushState(state, "", nextPath);
      }
    }
    const aiderThreadQueryKey = "aider-thread";
    const aiderInstructionDraftStoragePrefix = "main-computer-aider-instruction-v1";
    let aiderThreadLoadInFlight = false;
    function aiderThreadIdFromLocation(search = window.location.search) {
      try {
        return (new URLSearchParams(String(search || "")).get(aiderThreadQueryKey) || "").trim();
      } catch {
        return "";
      }
    }
    function syncAiderThreadRoute(archiveId, {replace = true} = {}) {
      if (applicationFromPath(window.location.pathname) !== "code-editor") return;
      const url = new URL(window.location.href);
      if (archiveId) {
        url.searchParams.set(aiderThreadQueryKey, archiveId);
      } else {
        url.searchParams.delete(aiderThreadQueryKey);
      }
      const nextUrl = `${url.pathname}${url.search}${url.hash}`;
      if (`${window.location.pathname}${window.location.search}${window.location.hash}` === nextUrl) return;
      const state = {app: "code-editor", aiderThread: archiveId || ""};
      if (replace) {
        window.history.replaceState(state, "", nextUrl);
      } else {
        window.history.pushState(state, "", nextUrl);
      }
    }
    function syncTaskManagerTabRoute(tabName, {replace = false} = {}) {
      if (applicationFromPath(window.location.pathname) !== "task-manager") return;
      const activeTab = normalizedTaskNotebookTab(tabName);
      const nextPath = taskManagerTabPath(activeTab);
      if (window.location.pathname === nextPath) return;
      const state = {app: "task-manager", taskTab: activeTab};
      if (replace) {
        window.history.replaceState(state, "", nextPath);
      } else {
        window.history.pushState(state, "", nextPath);
      }
    }
    function aiderInstructionDraftKey(threadId = "") {
      const clean = String(threadId || "").trim();
      return clean ? `${aiderInstructionDraftStoragePrefix}:${clean}` : "";
    }
    function loadAiderInstructionDraft(threadId = "") {
      const key = aiderInstructionDraftKey(threadId);
      if (!key) return "";
      try {
        return String(localStorage.getItem(key) || "");
      } catch {
        return "";
      }
    }
    function saveAiderInstructionDraft(threadId, value) {
      const key = aiderInstructionDraftKey(threadId);
      if (!key) return;
      try {
        const text = String(value || "");
        if (text.trim()) {
          localStorage.setItem(key, text);
        } else {
          localStorage.removeItem(key);
        }
      } catch {
        // best-effort only
      }
    }
    function clearAiderInstructionDraft(threadId) {
      const key = aiderInstructionDraftKey(threadId);
      if (!key) return;
      try {
        localStorage.removeItem(key);
      } catch {
        // best-effort only
      }
    }
    function setActiveApp(appName, options = {}) {
      const normalizedApp = routeableApps.has(appName) ? appName : "webgl";
      const syncRoute = options.syncRoute !== false;
      const replaceRoute = Boolean(options.replaceRoute);
      const previousApp = currentApp;
      currentApp = normalizedApp;
      document.body.dataset.activeApp = normalizedApp;
      if (syncRoute) {
        syncApplicationRoute(normalizedApp, {replace: replaceRoute});
      }
      document.querySelectorAll("[data-app]").forEach((button) => {
        button.classList.toggle("active", button.dataset.app === normalizedApp);
      });
      const [title, summary, state] = appCopy[normalizedApp];
      activeTitle.textContent = title;
      activeSummary.textContent = summary;
      activeState.textContent = state;
      const isWebgl = normalizedApp === "webgl";
      const isCalculator = normalizedApp === "calculator";
      const isDocument = normalizedApp === "document";
      const isSpreadsheet = normalizedApp === "spreadsheet";
      const isOnlyOffice = normalizedApp === "onlyoffice";
      const isTaskManager = normalizedApp === "task-manager";
      const isTerminal = normalizedApp === "terminal";
      const isChatConsole = normalizedApp === "chat-console";
      const isGitTools = normalizedApp === "git-tools";
      const isCodeEditor = normalizedApp === "code-editor";
      const isFileExplorer = normalizedApp === "file-explorer";
      const isGameEditor = normalizedApp === "game-editor";
      const isWebsiteBuilder = normalizedApp === "website-builder";
      const isWorker = normalizedApp === "worker";
      if (previousApp === "task-manager" && normalizedApp !== "task-manager") {
        stopTaskManagerAutoRefresh();
      }
      if (previousApp === "webgl" && normalizedApp !== "webgl") {
        pauseGameSurface();
      }
      if (previousApp === "game-editor" && normalizedApp !== "game-editor") {
        disposeGameEditorSurface();
      }
      if (previousApp === "onlyoffice" && normalizedApp !== "onlyoffice") {
        window.onlyofficeResetEditorViewportFix?.();
        window.onlyofficeCloseAdvancedPane?.();
      }
      canvas.style.display = isWebgl ? "block" : "none";
      if (desktopOverlay) desktopOverlay.style.display = isWebgl ? "block" : "none";
      calculatorApp.style.display = isCalculator ? "grid" : "none";
      documentApp.style.display = isDocument ? "grid" : "none";
      spreadsheetApp.style.display = isSpreadsheet ? "grid" : "none";
      if (onlyofficeApp) onlyofficeApp.style.display = isOnlyOffice ? "grid" : "none";
      taskManagerApp.style.display = isTaskManager ? "grid" : "none";
      terminalApp.style.display = isTerminal ? "grid" : "none";
      chatConsoleApp.style.display = isChatConsole ? "grid" : "none";
      gitToolsApp.style.display = isGitTools ? "grid" : "none";
      codeEditorApp.style.display = isCodeEditor ? "grid" : "none";
      systemFileExplorerApp.style.display = isFileExplorer ? "grid" : "none";
      gameEditorApp.style.display = isGameEditor ? "grid" : "none";
      if (websiteBuilderApp) websiteBuilderApp.style.display = isWebsiteBuilder ? "grid" : "none";
      if (workerApp) workerApp.style.display = isWorker ? "grid" : "none";
      stubMessage.style.display = isWebgl || isCalculator || isDocument || isSpreadsheet || isOnlyOffice || isTaskManager || isTerminal || isChatConsole || isGitTools || isCodeEditor || isFileExplorer || isGameEditor || isWebsiteBuilder || isWorker ? "none" : "grid";
      demoControls.style.display = isWebgl ? "grid" : "none";
      layoutDesktopIcons(normalizedApp);
      if (isWebgl) {
        running = true;
        glStatus.textContent = "game surface loading";
        initWebgl();
      } else if (isCalculator) {
        running = false;
        glStatus.textContent = "calculator ready";
        calculatorShell.classList.add("chat-docked");
        calculatorShell.classList.remove("chat-active");
        if (calculatorChatPanel) calculatorChatPanel.hidden = false;
        initChatConsoleApp();
        renderChatConsoleNotebook();
        if (calculatorGraphingPanel.hidden) {
          calculatorDisplay.focus();
        } else {
          calculatorGraphExpression.focus();
        }
        if (!calculatorGraphingPanel.hidden) setTimeout(drawCalculatorGraph, 0);
      } else if (isDocument) {
        running = false;
        glStatus.textContent = "document ready";
        initDocumentApp();
        documentEditor.focus();
      } else if (isSpreadsheet) {
        running = false;
        glStatus.textContent = "spreadsheet ready";
        initSpreadsheetApp();
      } else if (isOnlyOffice) {
        running = false;
        glStatus.textContent = "onlyoffice ready";
        initOnlyOfficeApp();
        window.onlyofficeScheduleEditorViewportFix?.();
      } else if (isTaskManager) {
        running = false;
        glStatus.textContent = "task manager ready";
        setTaskNotebookTab(taskNotebookTabFromPath(window.location.pathname), {syncRoute: false});
        initTaskManagerApp();
        taskQuery.focus();
      } else if (isTerminal) {
        running = false;
        glStatus.textContent = "terminal ready";
        initXtermTerminal();
        setTimeout(() => {
          fitXterm();
          if (xterm) xterm.focus();
        }, 0);
      } else if (isChatConsole) {
        running = false;
        glStatus.textContent = "chat console ready";
        initChatConsoleApp();
      } else if (isGitTools) {
        running = false;
        glStatus.textContent = "git tools ready";
        initGitToolsApp();
      } else if (isCodeEditor) {
        running = false;
        glStatus.textContent = "aider dock ready";
        aiderInstruction.focus();
        loadAiderContext().catch(() => {});
        if (!fileExplorerAutoLoaded) {
          fileExplorerAutoLoaded = true;
          loadFileMap();
        }
      } else if (isFileExplorer) {
        running = false;
        glStatus.textContent = "file explorer read-only";
        initSystemFileExplorerApp();
        systemFileExplorerSearch.focus();
      } else if (isGameEditor) {
        running = false;
        glStatus.textContent = "scene builder ready";
        initGameEditorApp().catch(() => {});
      } else if (isWebsiteBuilder) {
        running = false;
        glStatus.textContent = "website builder ready";
        initWebsiteBuilderApp();
      } else if (isWorker) {
        running = false;
        glStatus.textContent = "worker configuration ready";
        initWorkerApp();
      } else {
        glStatus.textContent = "stub selected";
      }
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 120});
    }


    ensureApplicationWidgets();
    ensureWidgetEditorChrome();
    applyWidgetOverrides();

    function markWidgetEditorChromeReady() {
      widgetEditorChromeReady = true;
      scheduleWidgetEditorHandleRefresh({delay: 120});
    }

    if (document.readyState === "complete") {
      markWidgetEditorChromeReady();
    } else {
      window.addEventListener("load", markWidgetEditorChromeReady, {once: true});
    }

    document.fonts?.ready?.then(() => {
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 60});
    }).catch(() => {});
    const widgetEditorMutationObserver = new MutationObserver((mutations) => {
      if (!widgetEditorChromeReady) return;
      const changedWidgets = mutations.some((mutation) =>
        [...mutation.addedNodes, ...mutation.removedNodes].some((node) =>
          node instanceof HTMLElement &&
          (node.matches?.("[data-mc-widget-id]") || node.querySelector?.("[data-mc-widget-id]"))
        )
      );
      if (changedWidgets) {
        mutations.forEach((mutation) => {
          mutation.addedNodes.forEach((node) => {
            if (node instanceof HTMLElement) MainComputerWidgets.hydrate(node);
          });
        });
        scheduleWidgetEditorHandleRefresh({delay: 80});
      }
    });
    widgetEditorMutationObserver.observe(document.body, {
      childList: true,
      subtree: true
    });
    document.addEventListener("mousedown", handleWidgetEditorCtrlClick, true);
    document.addEventListener("click", handleWidgetEditorCtrlClick, true);
    document.addEventListener("click", (event) => {
      const button = event.target.closest("[data-fullscreen-target]");
      if (!button || !taskManagerApp?.closest("#desktop-stage")) {
        // continue even outside task manager; the apps page owns this listener.
      }
      if (!button) return;
      const widget = button.closest(".app-fullscreen-widget");
      if (!widget) return;
      event.preventDefault();
      toggleApplicationWidgetFullscreen(widget);
    });
    document.addEventListener("fullscreenchange", () => {
      document.querySelectorAll(".app-fullscreen-widget [data-fullscreen-target]").forEach((button) => {
        button.textContent = document.fullscreenElement === button.closest(".app-fullscreen-widget") ? "Exit Full Screen" : "Full Screen";
      });
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 80});
    });
    document.addEventListener("scroll", () => {
      updateWidgetEditorSelection();
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh();
    }, true);
    window.addEventListener("resize", () => {
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 60});
    });

    function pauseGameSurface() {
      cancelAnimationFrame(animationFrame);
      animationFrame = null;
      if (gameSurfaceRuntime?.dispose) {
        gameSurfaceRuntime.dispose();
      }
      gameSurfaceRuntime = null;
      running = false;
    }
