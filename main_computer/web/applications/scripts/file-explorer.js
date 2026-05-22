    const FILE_EXPLORER_WUNDERBAUM_VERSION = "0.14.1";
    const FILE_EXPLORER_WUNDERBAUM_ASSETS = {
      css: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${FILE_EXPLORER_WUNDERBAUM_VERSION}/dist/wunderbaum.css`,
      icons: "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
      js: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${FILE_EXPLORER_WUNDERBAUM_VERSION}/dist/wunderbaum.umd.min.js`,
    };
    let systemFileExplorerWunderbaumLoadPromise = null;
    let systemFileExplorerWunderbaum = null;
    let systemFileExplorerRenderToken = 0;

    async function systemFileExplorerApi(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.ok === false) {
        throw new Error(data.message || data.error || `file explorer API returned ${response.status}`);
      }
      return data;
    }
    function systemFileExplorerBadges(entry) {
      const badges = [];
      if (entry.category && entry.category !== "other") badges.push(`<span class="file-explorer-badge">${escapeHtml(entry.category)}</span>`);
      if (entry.main_computer_purview) badges.push('<span class="file-explorer-badge purview">Main Computer</span>');
      return badges.join("");
    }
    function systemFileExplorerWunderbaumConstructor() {
      return window.mar10?.Wunderbaum || window.Wunderbaum || window.wunderbaum?.Wunderbaum || null;
    }
    function systemFileExplorerEnsureWunderbaumStylesheet(href, assetName) {
      if (!href || document.querySelector(`link[href="${href}"]`)) return;
      const link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = href;
      link.dataset.fileExplorerWunderbaumAsset = assetName || "css";
      document.head.appendChild(link);
    }
    function systemFileExplorerLoadWunderbaum() {
      const loaded = systemFileExplorerWunderbaumConstructor();
      if (loaded) return Promise.resolve(loaded);
      if (systemFileExplorerWunderbaumLoadPromise) return systemFileExplorerWunderbaumLoadPromise;
      systemFileExplorerWunderbaumLoadPromise = new Promise((resolve, reject) => {
        systemFileExplorerEnsureWunderbaumStylesheet(FILE_EXPLORER_WUNDERBAUM_ASSETS.css, "css");
        systemFileExplorerEnsureWunderbaumStylesheet(FILE_EXPLORER_WUNDERBAUM_ASSETS.icons, "icons");
        const finish = () => {
          const constructor = systemFileExplorerWunderbaumConstructor();
          if (constructor) {
            resolve(constructor);
          } else {
            reject(new Error("Wunderbaum global was not registered."));
          }
        };
        const existing = document.querySelector(`script[src="${FILE_EXPLORER_WUNDERBAUM_ASSETS.js}"]`);
        if (existing) {
          if (systemFileExplorerWunderbaumConstructor()) {
            finish();
            return;
          }
          existing.addEventListener("load", finish, {once: true});
          existing.addEventListener("error", () => reject(new Error("Could not load Wunderbaum.")), {once: true});
          window.setTimeout(() => {
            if (systemFileExplorerWunderbaumConstructor()) {
              finish();
            } else {
              reject(new Error("Existing Wunderbaum script did not register a constructor."));
            }
          }, 3000);
          return;
        }
        const script = document.createElement("script");
        script.src = FILE_EXPLORER_WUNDERBAUM_ASSETS.js;
        script.async = true;
        script.dataset.fileExplorerWunderbaumAsset = "js";
        script.onload = finish;
        script.onerror = () => reject(new Error("Could not load Wunderbaum."));
        document.head.appendChild(script);
      });
      return systemFileExplorerWunderbaumLoadPromise;
    }
    function systemFileExplorerEntryPath(entry = {}) {
      return String(entry.relative_path || entry.path_display || entry.name || "").replace(/\\/g, "/");
    }
    function systemFileExplorerEntryKey(entry = {}, index = 0) {
      const kind = String(entry.kind || "file").toLowerCase();
      const path = systemFileExplorerEntryPath(entry) || String(entry.path_display || entry.name || ".");
      const title = String(entry.name || path || ".");
      const ordinal = Number.isInteger(index) ? index : 0;
      return `file-explorer:${ordinal}:${kind}:${path}:${title}`;
    }
    function systemFileExplorerEntryMeta(entry = {}) {
      return [
        entry.kind || "file",
        entry.relative_path || ".",
        `${Number(entry.bytes || 0)} bytes`,
      ].filter(Boolean).join(" · ");
    }
    function systemFileExplorerEntryTitle(entry = {}) {
      return String(entry.name || entry.relative_path || ".") || ".";
    }
    function systemFileExplorerEntryNodeData(entry = {}, index = 0) {
      const isDirectory = entry.kind === "directory";
      const normalizedEntry = {
        ...entry,
        kind: isDirectory ? "directory" : "file",
        relative_path: String(entry.relative_path || "").replace(/\\/g, "/"),
        path_display: String(entry.path_display || entry.relative_path || entry.name || ""),
        name: String(entry.name || entry.relative_path || "."),
        bytes: Number(entry.bytes || 0),
      };
      return {
        kind: normalizedEntry.kind,
        index,
        name: normalizedEntry.name,
        path: systemFileExplorerEntryPath(normalizedEntry),
        relative_path: normalizedEntry.relative_path,
        path_display: normalizedEntry.path_display,
        bytes: normalizedEntry.bytes,
        mtime: normalizedEntry.mtime,
        category: normalizedEntry.category || "other",
        suggested_app: normalizedEntry.suggested_app || "",
        main_computer_purview: Boolean(normalizedEntry.main_computer_purview),
        mounted_windows_drive: Boolean(normalizedEntry.mounted_windows_drive),
        fileExplorerEntry: normalizedEntry,
        entry: normalizedEntry,
      };
    }
    function systemFileExplorerEntryToTreeNode(entry = {}, index = 0) {
      const isDirectory = entry.kind === "directory";
      const key = systemFileExplorerEntryKey(entry, index);
      const nodeData = systemFileExplorerEntryNodeData(entry, index);
      return {
        title: systemFileExplorerEntryTitle(entry),
        key,
        type: isDirectory ? "directory" : "file",
        classes: isDirectory ? "file-explorer-tree-directory" : "file-explorer-tree-file",
        data: nodeData,
        fileExplorerEntry: nodeData.fileExplorerEntry,
        entry: nodeData.fileExplorerEntry,
      };
    }
    function systemFileExplorerTreeSource(entries = []) {
      const nodes = entries.map((entry, index) => systemFileExplorerEntryToTreeNode(entry, index));
      if (!nodes.length) {
        return [{
          title: "No files found in this folder",
          key: "empty:file-explorer",
          type: "empty",
          unselectable: true,
          checkbox: false,
          data: {kind: "empty", path: "", entry: null},
        }];
      }
      return nodes;
    }
    function systemFileExplorerDestroyWunderbaum() {
      if (systemFileExplorerWunderbaum) {
        try {
          if (typeof systemFileExplorerWunderbaum.destroy === "function") {
            systemFileExplorerWunderbaum.destroy();
          } else if (typeof systemFileExplorerWunderbaum.clear === "function") {
            systemFileExplorerWunderbaum.clear();
          }
        } catch (error) {
          console.warn("File Explorer Wunderbaum cleanup skipped.", error);
        }
      }
      systemFileExplorerWunderbaum = null;
      if (systemFileExplorerList) {
        systemFileExplorerList._wb_tree = null;
        systemFileExplorerList.querySelectorAll("[data-file-explorer-wunderbaum-host]").forEach((host) => {
          host._wb_tree = null;
        });
      }
    }
    function systemFileExplorerCreateWunderbaumHost(token) {
      const host = document.createElement("div");
      host.className = "file-explorer-wunderbaum-host wb-skeleton wb-initializing";
      host.dataset.fileExplorerWunderbaumHost = String(token);
      host.dataset.fileExplorerTreeState = "loading";
      host.setAttribute("aria-label", "Directory listing tree");
      return host;
    }
    function systemFileExplorerRenderStillCurrent(token, host) {
      return token === systemFileExplorerRenderToken && Boolean(host?.isConnected) && systemFileExplorerList?.contains(host);
    }
    function systemFileExplorerSizeWunderbaum(element) {
      if (!element) return;
      const applyToViewport = () => {
        element.style.setProperty("height", "100%", "important");
        element.style.setProperty("min-height", "0", "important");
        element.style.setProperty("max-height", "none", "important");
        element.style.setProperty("overflow-y", "auto", "important");
        element.style.setProperty("overflow-x", "hidden", "important");
        element.style.setProperty("--wb-row-outer-height", "24px", "important");
        element.style.setProperty("--wb-row-inner-height", "22px", "important");
        element.style.setProperty("background-color", "#010201", "important");
        element.style.setProperty("color", "var(--ink)", "important");
        const listContainer = element.querySelector(".wb-list-container");
        const nodeList = element.querySelector(".wb-node-list");
        if (listContainer) {
          listContainer.style.setProperty("min-height", "0", "important");
          listContainer.style.setProperty("max-height", "none", "important");
          listContainer.style.setProperty("overflow", "visible", "important");
          listContainer.style.setProperty("background-color", "#010201", "important");
          listContainer.style.setProperty("color", "var(--ink)", "important");
        }
        if (nodeList) {
          nodeList.style.setProperty("min-width", "0", "important");
          nodeList.style.setProperty("width", "100%", "important");
          nodeList.style.setProperty("overflow", "visible", "important");
          nodeList.style.setProperty("background-color", "#010201", "important");
          nodeList.style.setProperty("color", "var(--ink)", "important");
        }
      };
      applyToViewport();
      window.requestAnimationFrame(applyToViewport);
      window.setTimeout(applyToViewport, 150);
    }
    function systemFileExplorerNotifyWunderbaumViewport(tree, change = "resize") {
      if (!tree?.update) return;
      try {
        tree.update(change, {immediate: true});
      } catch (error) {
        console.warn("File Explorer Wunderbaum viewport update skipped.", error);
      }
    }
    function systemFileExplorerOpenEntry(entry = {}) {
      if (!entry || entry.kind === "empty") return;
      if (entry.kind === "directory") {
        systemFileExplorerSelectLocation(systemFileExplorerRootId, entry.relative_path || "");
      } else {
        readSystemFileExplorerEntry(entry);
      }
    }
    function systemFileExplorerEntryFromNodeData(node = {}) {
      const data = node?.data || {};
      const candidates = [
        data.fileExplorerEntry,
        data.entry,
        node.fileExplorerEntry,
        node.entry,
      ];
      const entry = candidates.find((candidate) => candidate && typeof candidate === "object");
      if (entry) return entry;
      const kind = data.kind || node.type;
      if (!kind || kind === "empty") return null;
      const relativePath = String(data.relative_path || data.path || "").replace(/\\/g, "/");
      return {
        name: String(data.name || node.title || relativePath || "."),
        kind: kind === "directory" ? "directory" : "file",
        category: data.category || "other",
        relative_path: relativePath,
        path_display: data.path_display || relativePath,
        bytes: Number(data.bytes || 0),
        mtime: data.mtime,
        suggested_app: data.suggested_app || "",
        main_computer_purview: Boolean(data.main_computer_purview),
        mounted_windows_drive: Boolean(data.mounted_windows_drive),
      };
    }
    function systemFileExplorerEntryFromTreeEvent(event = {}) {
      return systemFileExplorerEntryFromNodeData(event.node);
    }
    function systemFileExplorerPreviewTreeEvent(event = {}) {
      const entry = systemFileExplorerEntryFromTreeEvent(event);
      if (entry) previewSystemFileExplorerEntry(entry);
      return entry;
    }
    function systemFileExplorerSelectLocation(rootId, relativePath = "") {
      const nextRootId = rootId || systemFileExplorerRootId;
      if (!nextRootId) return;
      systemFileExplorerRootId = nextRootId;
      systemFileExplorerRelativePath = String(relativePath || "").replace(/\\/g, "/").replace(/^\/+/, "");
      systemFileExplorerSelectedEntry = null;
      systemFileExplorerPreview.textContent = "Select a file or folder to preview metadata.";
      systemFileExplorerStatus.textContent = "loading file list…";
      listSystemFileExplorerDirectory();
    }
    function renderSystemFileExplorerFallbackEntries(entries) {
      systemFileExplorerDestroyWunderbaum();
      systemFileExplorerList.classList.remove("wb-skeleton", "wb-initializing", "wunderbaum", "file-explorer-list-with-wunderbaum");
      systemFileExplorerList.textContent = "";
      const fallback = document.createElement("div");
      fallback.className = "file-explorer-fallback-list";
      if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "file-explorer-empty";
        empty.textContent = "No files found in this folder.";
        fallback.append(empty);
      }
      entries.forEach((entry) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "file-explorer-entry";
        button.dataset.fileExplorerEntryPath = systemFileExplorerEntryPath(entry);
        button.classList.toggle("active", systemFileExplorerSelectedEntry?.relative_path === entry.relative_path);
        button.innerHTML = `
          <span>
            <span class="file-explorer-entry-title">${entry.kind === "directory" ? "▸ " : ""}${escapeHtml(entry.name)}</span>
            <span class="file-explorer-entry-meta">${escapeHtml(systemFileExplorerEntryMeta(entry))}</span>
          </span>
          <span class="file-explorer-badges">${systemFileExplorerBadges(entry)}</span>
        `;
        button.addEventListener("click", () => previewSystemFileExplorerEntry(entry));
        button.addEventListener("dblclick", () => systemFileExplorerOpenEntry(entry));
        fallback.append(button);
      });
      systemFileExplorerList.append(fallback);
    }
    function renderSystemFileExplorerRoots() {
      systemFileExplorerRoots.textContent = "";
      systemFileExplorerRootsData.forEach((root) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "file-explorer-root-button";
        button.classList.toggle("active", root.id === systemFileExplorerRootId);
        button.textContent = `${root.label} ${root.main_computer_purview ? "• Main Computer" : ""}\n${root.path_display}`;
        button.addEventListener("click", () => {
          systemFileExplorerSelectLocation(root.id, "");
        });
        systemFileExplorerRoots.append(button);
      });
    }
    function renderSystemFileExplorerEntries(entries) {
      const normalizedEntries = Array.isArray(entries) ? entries : [];
      const token = ++systemFileExplorerRenderToken;
      systemFileExplorerDestroyWunderbaum();
      systemFileExplorerList.textContent = "";
      systemFileExplorerList.classList.remove("wunderbaum");
      systemFileExplorerList.classList.add("wb-skeleton", "file-explorer-list-with-wunderbaum");
      systemFileExplorerList.dataset.fileExplorerTreeState = "loading";
      const treeHost = systemFileExplorerCreateWunderbaumHost(token);
      systemFileExplorerList.append(treeHost);
      systemFileExplorerLoadWunderbaum()
        .then((Wunderbaum) => {
          if (!systemFileExplorerRenderStillCurrent(token, treeHost)) return;
          if (!Wunderbaum) throw new Error("Wunderbaum constructor is unavailable.");
          treeHost.classList.remove("wb-initializing");
          treeHost.dataset.fileExplorerTreeState = "ready";
          systemFileExplorerList.classList.add("wunderbaum");
          systemFileExplorerList.dataset.fileExplorerTreeState = "ready";
          const tree = new Wunderbaum({
            id: `file-explorer-${Math.random().toString(36).slice(2)}`,
            element: treeHost,
            checkbox: false,
            selectMode: "single",
            types: {
              directory: {icon: "bi bi-folder", classes: "file-explorer-tree-directory"},
              file: {icon: "bi bi-file-earmark-text", classes: "file-explorer-tree-file"},
              empty: {icon: "bi bi-dash-circle", classes: "file-explorer-tree-empty"},
            },
            source: {children: systemFileExplorerTreeSource(normalizedEntries)},
            tooltip: (event) => {
              const entry = systemFileExplorerEntryFromTreeEvent(event);
              if (!entry) return "No files found in this folder";
              return [
                entry.path_display || entry.relative_path || entry.name,
                entry.category && entry.category !== "other" ? entry.category : "",
                entry.main_computer_purview ? "Main Computer" : "",
              ].filter(Boolean).join(" · ");
            },
            init: (event) => {
              event.tree.systemFileExplorerRootId = systemFileExplorerRootId;
              event.tree.systemFileExplorerRelativePath = systemFileExplorerRelativePath;
              systemFileExplorerWunderbaum = event.tree;
              treeHost._wb_tree = event.tree;
              systemFileExplorerList._wb_tree = event.tree;
              systemFileExplorerSizeWunderbaum(treeHost);
              systemFileExplorerNotifyWunderbaumViewport(event.tree, "resize");
              systemFileExplorerNotifyWunderbaumViewport(event.tree, "scroll");
            },
            click: (event) => {
              systemFileExplorerPreviewTreeEvent(event);
            },
            activate: (event) => {
              systemFileExplorerPreviewTreeEvent(event);
            },
            select: (event) => {
              systemFileExplorerPreviewTreeEvent(event);
            },
            dblclick: (event) => {
              const entry = systemFileExplorerEntryFromTreeEvent(event);
              if (entry) systemFileExplorerOpenEntry(entry);
              return false;
            },
            keydown: (event) => {
              if (event.event?.key !== "Enter") return;
              const entry = systemFileExplorerEntryFromTreeEvent(event);
              if (entry) systemFileExplorerOpenEntry(entry);
              return false;
            },
          });
          systemFileExplorerWunderbaum = tree;
          treeHost._wb_tree = tree;
          systemFileExplorerList._wb_tree = tree;
          systemFileExplorerSizeWunderbaum(treeHost);
          systemFileExplorerNotifyWunderbaumViewport(tree, "resize");
          systemFileExplorerNotifyWunderbaumViewport(tree, "scroll");
        })
        .catch((error) => {
          if (!systemFileExplorerRenderStillCurrent(token, treeHost)) return;
          renderSystemFileExplorerFallbackEntries(normalizedEntries);
          systemFileExplorerList.dataset.fileExplorerTreeState = "fallback";
          systemFileExplorerStatus.textContent = `Wunderbaum unavailable; using fallback list. ${error.message || error}`;
        });
    }
    function previewSystemFileExplorerEntry(entry, extra = "") {
      systemFileExplorerSelectedEntry = entry;
      const selectedPath = systemFileExplorerEntryPath(entry);
      systemFileExplorerList.querySelectorAll(".file-explorer-entry").forEach((button) => {
        button.classList.toggle("active", button.dataset.fileExplorerEntryPath === selectedPath);
      });
      const openWith = entry.suggested_app ? `<button type="button" data-open-with="${escapeHtml(entry.suggested_app)}">Open with ${escapeHtml(entry.suggested_app)}</button>` : "";
      systemFileExplorerPreview.innerHTML = `
${systemFileExplorerBadges(entry)}
Name: ${escapeHtml(entry.name)}
Kind: ${escapeHtml(entry.kind)}
Category: ${escapeHtml(entry.category || "other")}
Path: ${escapeHtml(entry.path_display || entry.relative_path || "")}
Size: ${entry.bytes || 0} bytes
Modified: ${entry.mtime ? new Date(entry.mtime * 1000).toLocaleString() : "unknown"}
Suggested app: ${escapeHtml(entry.suggested_app || "none")}

${extra ? escapeHtml(extra) : ""}${openWith}
`;
      systemFileExplorerPreview.querySelector("[data-open-with]")?.addEventListener("click", (event) => {
        const app = event.currentTarget.dataset.openWith;
        if (app && routeableApps.has(app)) setActiveApp(app);
      });
    }
    async function readSystemFileExplorerEntry(entry) {
      try {
        const data = await systemFileExplorerApi("/api/applications/file-explorer/read", {
          root_id: systemFileExplorerRootId,
          relative_path: entry.relative_path
        });
        if (!data.readable) {
          previewSystemFileExplorerEntry(data.entry || entry, `Preview unavailable: ${data.reason || "metadata only"}`);
          return;
        }
        previewSystemFileExplorerEntry(data.entry || entry, `\n${data.content}`);
      } catch (error) {
        systemFileExplorerStatus.textContent = error.message;
      }
    }
    async function listSystemFileExplorerDirectory() {
      if (!systemFileExplorerRootId) return;
      try {
        const data = await systemFileExplorerApi("/api/applications/file-explorer/list", {
          root_id: systemFileExplorerRootId,
          relative_path: systemFileExplorerRelativePath
        });
        systemFileExplorerRelativePath = data.relative_path || "";
        systemFileExplorerPath.textContent = `${systemFileExplorerRootId}:/${systemFileExplorerRelativePath}`;
        renderSystemFileExplorerRoots();
        renderSystemFileExplorerEntries(data.entries || []);
        systemFileExplorerStatus.textContent = `listed ${data.count || 0} entries`;
      } catch (error) {
        systemFileExplorerStatus.textContent = error.message;
      }
    }
    async function loadSystemFileExplorerRoots() {
      const data = await systemFileExplorerApi("/api/applications/file-explorer/roots");
      systemFileExplorerRootsData = data.roots || [];
      const preferred = systemFileExplorerRootsData.find((root) => root.id === "workspace") || systemFileExplorerRootsData[0];
      systemFileExplorerRootId = systemFileExplorerRootId || preferred?.id || "";
      renderSystemFileExplorerRoots();
      await listSystemFileExplorerDirectory();
    }
    async function searchSystemFileExplorer() {
      const query = systemFileExplorerSearch.value.trim();
      if (!query) {
        await listSystemFileExplorerDirectory();
        return;
      }
      try {
        const data = await systemFileExplorerApi("/api/applications/file-explorer/search", {
          root_id: systemFileExplorerRootId,
          relative_path: systemFileExplorerRelativePath,
          query,
          limit: 80
        });
        renderSystemFileExplorerEntries(data.results || []);
        systemFileExplorerStatus.textContent = `found ${data.count || 0} entries`;
      } catch (error) {
        systemFileExplorerStatus.textContent = error.message;
      }
    }
    function initSystemFileExplorerApp() {
      if (!systemFileExplorerInitialized) {
        systemFileExplorerInitialized = true;
        systemFileExplorerSearchRun.addEventListener("click", searchSystemFileExplorer);
        systemFileExplorerSearch.addEventListener("keydown", (event) => {
          if (event.key === "Enter") searchSystemFileExplorer();
        });
        systemFileExplorerUp.addEventListener("click", () => {
          const parts = systemFileExplorerRelativePath.split("/").filter(Boolean);
          parts.pop();
          systemFileExplorerSelectLocation(systemFileExplorerRootId, parts.join("/"));
        });
      }
      loadSystemFileExplorerRoots().catch((error) => {
        systemFileExplorerStatus.textContent = error.message;
      });
    }
    function splitAiderFiles(value) {
      return String(value || "")
        .replace(/,/g, "\n")
        .split(/\n+/)
        .map((item) => item.trim())
        .filter(Boolean);
    }
    function saveFileMapMarked() {
      localStorage.setItem("main-computer-aider-map-files-v1", JSON.stringify([...fileMapMarked].sort()));
    }
