    const activeTitle = document.querySelector("#active-app-title");
    const activeSummary = document.querySelector("#active-app-summary");
    const activeState = document.querySelector("#active-app-state");
    const terminalAnalysis = document.querySelector("#terminal-analysis");
    const terminalAnalysisToggle = document.querySelector("#terminal-analysis-toggle");
    const appCopy = {
      desktop: ["Desktop", "Choose an app from the desktop grid.", "Desktop app launcher is ready."],
      webgl: ["Game Surface", "Shuttle boarding defense is ready.", "Walk the vertex-built shuttle, fire the phaser, and repel aliens transporting in from the raider outside."],
      astrometric: ["Astrometric 3D", "Docker-backed C++/GPU Schwarzschild renderer is ready.", "Astrometric 3D streams backend-rendered frames and forwards mouse camera controls."],
      calculator: ["Calculator", "Local arithmetic tool is ready.", "Calculator is running."],
      document: ["Document Editor", "Editable writing workspace is ready.", "Document Editor is running."],
      spreadsheet: ["Spreadsheet", "Editable sheet workspace is ready.", "Spreadsheet is running."],
      onlyoffice: ["ONLYOFFICE", "Native XLSX workbook editor is ready.", "ONLYOFFICE is running."],
      "task-manager": ["Task Manager", "Process monitor, server control, connection watch, and AI operations are ready.", "Task Manager is running."],
      conductor: ["Conductor", "Scheduled subprocess worker control for DNS, SSL keys, local keys, and mutable state.", "Conductor is running."],
      terminal: ["Terminal", "Local command runner is ready.", "Terminal is running."],
      "chat-console": ["Chat Console", "Typed AI/code/Terminal/Mathics notebook cells are ready.", "Chat Console notebook is running."],
      "ai-control": ["AI Control", "Inspect and edit AI prompt templates and message structures.", "AI Control prompt structure catalog is ready."],
      email: ["Email", "Unified Gmail, Yahoo, POP/IMAP, Outlook, iCloud, drafts, and compose are ready.", "Email is running with local-first state and a backend POP/IMAP check bridge."],
      "git-tools": ["Git Tools", "Repository status, patch inbox, and harness actions are ready.", "Git Tools are ready."],
      "code-editor": ["Code Editor", "Aider action dock is ready for the future editor.", "Code Editor Aider setup is running."],
      "file-explorer": ["File Explorer", "Read-only system file browser is ready.", "File Explorer is running read-only."],
      "game-editor": ["Game Editor", "Project-backed scene editor is ready.", "Game Editor is scene-backed."],
      "website-builder": ["Website Builder", "Manage site manifests, save website files, and publish local Docker lanes.", "Website Builder is running."],
      "mcel-lab": ["MCEL Lab", "App blueprint and point-inspection workbench is ready.", "MCEL Lab mounts inspectable app previews and records selected-element evidence."],
      worker: ["Worker", "Configure remote AI use and local AI rental behavior.", "Worker configuration is ready."],
      wallet: ["Wallet", "Standalone wallet connect/disconnect workbench is ready.", "Wallet connection workbench is running."]
    };
    const desktopApps = [
      {app: "webgl", glyph: "G", title: "Game Surface", summary: "project preview"},
      {app: "astrometric", glyph: "BH", title: "Astrometric 3D", summary: "GPU ray render"},
      {app: "calculator", glyph: "C", title: "Calculator", summary: "arithmetic tool"},
      {app: "document", glyph: "D", title: "Document Editor", summary: "writing workspace"},
      {app: "spreadsheet", glyph: "S", title: "Spreadsheet", summary: "sheet workspace"},
      {app: "onlyoffice", glyph: "O", title: "ONLYOFFICE", summary: "XLSX editor"},
      {app: "task-manager", glyph: "T", title: "Task Manager", summary: "operations deck"},
      {app: "conductor", glyph: "K", title: "Conductor", summary: "scheduled worker"},
      {app: "terminal", glyph: ">", title: "Terminal", summary: "command shell"},
      {app: "chat-console", glyph: "N", title: "Chat Console", summary: "notebook cells"},
      {app: "ai-control", glyph: "AI", title: "AI Control", summary: "prompt structure"},
      {app: "email", glyph: "@", title: "Email", summary: "unified mail client"},
      {app: "git-tools", glyph: "G", title: "Git Tools", summary: "revision tools"},
      {app: "code-editor", glyph: "E", title: "Code Editor", summary: "Aider dock"},
      {app: "file-explorer", glyph: "F", title: "File Explorer", summary: "system files"},
      {app: "game-editor", glyph: "P", title: "Game Editor", summary: "scene builder"},
      {app: "website-builder", glyph: "W", title: "Website Builder", summary: "site manager"},
      {app: "mcel-lab", glyph: "M", title: "MCEL Lab", summary: "semantic compiler"},
      {app: "worker", glyph: "A", title: "Worker", summary: "AI rental config"},
      {app: "wallet", glyph: "W", title: "Wallet", summary: "connect hooks"}
    ];
    const routeableApps = new Set(Object.keys(appCopy));
    const applicationRouteAliases = {"layout-builder": "game-editor", "web-test-bed": "mcel-lab"};
    const websiteBuilderRouteSitePattern = /^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$/;

    function normalizeWebsiteBuilderRouteSiteId(siteId = "") {
      const value = String(siteId || "").trim().toLowerCase();
      return websiteBuilderRouteSitePattern.test(value) ? value : "";
    }

    function websiteBuilderSiteIdFromPath(pathname = window.location.pathname) {
      const cleaned = String(pathname || "").replace(/\/+$/, "") || "/";
      const parts = cleaned.split("/").filter(Boolean);
      if (!parts.length || !["applications", "apps", "app"].includes(parts[0])) return "";
      if (parts[1] !== "website-builder") return "";
      return normalizeWebsiteBuilderRouteSiteId(parts[2] || "");
    }

    function websiteBuilderPath(siteId = "") {
      const normalizedSiteId = normalizeWebsiteBuilderRouteSiteId(siteId);
      return normalizedSiteId
        ? `/applications/website-builder/${encodeURIComponent(normalizedSiteId)}`
        : "/applications/website-builder";
    }

    function applicationPath(appName, options = {}) {
      const normalized = routeableApps.has(appName) ? appName : "desktop";
      if (normalized === "website-builder") {
        return websiteBuilderPath(options.siteId || "");
      }
      return normalized === "desktop" ? "/applications" : `/applications/${normalized}`;
    }

    function normalizedTaskNotebookTab(tabName) {
      return tabName === "connections"
        ? "connections"
        : tabName === "all-processes"
          ? "all-processes"
          : tabName === "hardware"
            ? "hardware"
            : "server-processes";
    }

    function taskNotebookTabFromPath(pathname = window.location.pathname) {
      const cleaned = String(pathname || "").replace(/\/+$/, "") || "/";
      const parts = cleaned.split("/").filter(Boolean);
      if (parts.length < 3 || parts[1] !== "task-manager") return "server-processes";
      return normalizedTaskNotebookTab(parts[2]);
    }

    function taskManagerTabPath(tabName) {
      return `/applications/task-manager/${normalizedTaskNotebookTab(tabName)}`;
    }