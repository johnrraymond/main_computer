    const webglProjectState = {
      projectId: "webgl-demo",
      project: null,
      assets: [],
      contentHash: "",
      loading: null,
      annotationSavePromise: Promise.resolve(),
      strategicSession: null,
      strategicSessionError: "",
      systemScenarioRuntime: null,
      systemScenarioError: "",
      jsGameplayPackInstallResult: null,
      gameStarted: false,
      pendingSceneId: "",
      lastAutosave: null
    };

    const WEBGL_AUTOSAVE_KEY = "main-computer.webgl.autosave.v1";
    const WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY = "main-computer.webgl.active-gameplay-packs.v1";
    const WEBGL_ENABLED_GAMEPLAY_PACKS_KEY = "main-computer.webgl.enabled-gameplay-packs.v2";
    const WEBGL_ACTIVE_GAMEPLAY_PACKS_KEY = WEBGL_ENABLED_GAMEPLAY_PACKS_KEY;
    const WEBGL_GAMEPLAY_PACK_STORAGE_KEY = WEBGL_ENABLED_GAMEPLAY_PACKS_KEY;
    const WEBGL_ACTIVE_GAMEPLAY_PACKS_STORAGE_KEY = WEBGL_ENABLED_GAMEPLAY_PACKS_KEY;
    const WEBGL_OPENING_SHUTTLE_LEGACY_PLUGIN_ID = "plugin.hand-authored.opening-shuttle-ambush.001";
    const WEBGL_OPENING_SHUTTLE_JS_PACK_ID = "pack.opening-shuttle.elite-boarders";
    const WEBGL_OPENING_SHUTTLE_JS_PACK_LABEL = "Opening Shuttle: Elite Boarders";
    const WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID = "pack.main-ship.bay-boarders";
    const WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_LABEL = "Main Ship: Bay Boarders";
    const WEBGL_JS_GAMEPLAY_PACK_IDS = new Set([
      WEBGL_OPENING_SHUTTLE_JS_PACK_ID,
      WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID
    ]);
    const WEBGL_JS_GAMEPLAY_PACK_SOURCE_ENDPOINT = "/api/applications/game-editor/gameplay-pack/js-source";
    const WEBGL_BUILT_IN_JS_GAMEPLAY_PACKS = Object.freeze([
      Object.freeze({
        pluginId: WEBGL_OPENING_SHUTTLE_JS_PACK_ID,
        label: WEBGL_OPENING_SHUTTLE_JS_PACK_LABEL,
        description: "Triple-strength shuttle boarders and an elite boarding leader.",
        target: "Opening shuttle encounter",
        defaultEnabled: true,
        status: "js-pack"
      }),
      Object.freeze({
        pluginId: WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID,
        label: WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_LABEL,
        description: "Spawns main-ship bay boarders after the bay-entry cutscene resolves.",
        target: "Main ship bay entry",
        defaultEnabled: true,
        status: "js-pack"
      })
    ]);

    function webglGameplayPackDescriptorIsJsPack(pack = {}) {
      const pluginId = webglCanonicalGameplayPackId(pack?.pluginId || pack?.id || "");
      return WEBGL_JS_GAMEPLAY_PACK_IDS.has(pluginId)
        || String(pack?.sourceKind || "").toLowerCase() === "js-gameplay-pack"
        || String(pack?.status || "").toLowerCase() === "js-pack";
    }

    function webglJsGameplayPackIdsForProject(project = webglProjectState.project) {
      const ids = new Set(WEBGL_JS_GAMEPLAY_PACK_IDS);
      const packs = project?.metadata?.availableGameplayPacks?.packs;
      if (Array.isArray(packs)) {
        packs.forEach((pack) => {
          if (!webglGameplayPackDescriptorIsJsPack(pack)) return;
          const pluginId = webglCanonicalGameplayPackId(pack?.pluginId || pack?.id || "");
          if (pluginId) ids.add(pluginId);
        });
      }
      return ids;
    }

    async function webglPost(path, payload = {}) {
      const response = await fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
      });
      let data = null;
      try {
        data = await response.json();
      } catch {
        data = null;
      }
      if (!response.ok || !data?.ok) {
        throw new Error(data?.error || `${path} failed with HTTP ${response.status}`);
      }
      return data;
    }

    function webglDefaultStrategicSystem(project) {
      const navigation = project?.metadata?.spaceNavigation;
      return String(
        navigation?.stateDefaults?.currentSystemId
        || navigation?.startSystem
        || ""
      );
    }

    function webglCanonicalGameplayPackId(value) {
      const id = String(value || "").trim();
      if (!id) return "";
      if (id === WEBGL_OPENING_SHUTTLE_LEGACY_PLUGIN_ID) return WEBGL_OPENING_SHUTTLE_JS_PACK_ID;
      return id;
    }

    function webglNormalizeGameplayPackIds(value) {
      if (value === null || value === undefined) return [];
      if (Array.isArray(value)) {
        return [...new Set(value.flatMap(webglNormalizeGameplayPackIds).filter(Boolean))];
      }
      if (typeof value === "string") {
        const tokens = value.split(",").map((item) => String(item || "").trim()).filter(Boolean);
        if (tokens.some((token) => token.toLowerCase() === "none")) return [];
        return [...new Set(tokens.map(webglCanonicalGameplayPackId).filter(Boolean))];
      }
      if (typeof value === "object") {
        if (String(value.mode || "").toLowerCase() === "none") return [];
        return webglNormalizeGameplayPackIds(
          value.activeGameplayPackIds
          || value.enabledPackIds
          || value.activePluginIds
          || value.pluginIds
          || value.selectedPluginIds
          || value.value
          || []
        );
      }
      return [];
    }

    function webglParseGameplayPackStorageValue(rawValue) {
      if (rawValue === null || rawValue === undefined) return null;
      const rawText = String(rawValue || "");
      if (!rawText) return [];
      try {
        return webglNormalizeGameplayPackIds(JSON.parse(rawText));
      } catch {
        return webglNormalizeGameplayPackIds(rawText);
      }
    }

    function webglDefaultGameplayPackIds(project = webglProjectState.project) {
      const jsPackIds = webglJsGameplayPackIdsForProject(project);
      return webglAvailableGameplayPacks(project)
        .filter((pack) => pack.defaultEnabled === true && pack.loadable !== false)
        .map((pack) => webglCanonicalGameplayPackId(pack.pluginId))
        .filter((pluginId) => jsPackIds.has(pluginId));
    }

    function webglReloadGameplayPackSelection(project) {
      const metadata = project?.metadata || {};
      const configured = metadata.activeGameplayPackIds
        || metadata.activeGeneratedGameplayPackIds
        || metadata.gameplayPackSelection
        || metadata.generatedGameplayPackSelection
        || [];
      let selected = webglNormalizeGameplayPackIds(configured);
      let selectionSource = selected.length ? "project-metadata" : "default-enabled";
      let persisted = false;

      if (!selected.length) {
        selected = webglDefaultGameplayPackIds(project);
      }

      try {
        const rawEnabled = window.localStorage?.getItem?.(WEBGL_ENABLED_GAMEPLAY_PACKS_KEY);
        const rawLegacy = rawEnabled === null || rawEnabled === undefined
          ? window.localStorage?.getItem?.(WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY)
          : null;
        const parsed = rawEnabled !== null && rawEnabled !== undefined
          ? webglParseGameplayPackStorageValue(rawEnabled)
          : webglParseGameplayPackStorageValue(rawLegacy);
        if (parsed !== null) {
          selected = parsed;
          selectionSource = "local-storage";
          persisted = true;
        }
      } catch {
        // Ignore unreadable localStorage; project metadata/defaults remain the fallback.
      }

      try {
        const params = new URLSearchParams(window.location?.search || "");
        const hasGameplayPackQuery = params.has("gameplayPack") || params.has("gameplayPacks");
        const queryValue = params.get("gameplayPack") || params.get("gameplayPacks");
        if (hasGameplayPackQuery) {
          selected = webglNormalizeGameplayPackIds(queryValue);
          selectionSource = "query-param";
          persisted = false;
        }
      } catch {
        // URLSearchParams/window may be unavailable in non-browser smoke tests.
      }

      return {
        schema: "game.reloadGameplayPackSelection.v1",
        kind: "reload-gameplay-pack-selection",
        source: selectionSource,
        readOnly: true,
        runtimeLocal: true,
        persisted,
        mode: selected.length ? "selected" : "none",
        activeGameplayPackIds: selected
      };
    }

    function webglGameplayPackSelectorNodes() {
      const byId = (id) => (
        typeof document.getElementById === "function"
          ? document.getElementById(id)
          : document.querySelector?.(`#${id}`)
      );
      const panel = byId("webgl-gameplay-pack-selector") || document.querySelector?.("[data-webgl-gameplay-pack-controls]");
      const checklist = byId("webgl-gameplay-pack-checklist") || panel?.querySelector?.("[data-webgl-gameplay-pack-checklist]") || null;
      const checkboxes = Array.from(
        checklist?.querySelectorAll?.("[data-webgl-gameplay-pack-checkbox]") || []
      );
      return {
        panel,
        checklist,
        checkboxes,
        select: byId("webgl-gameplay-pack-select"),
        apply: byId("webgl-gameplay-pack-apply") || byId("webgl-gameplay-pack-start"),
        start: byId("webgl-gameplay-pack-start") || byId("webgl-gameplay-pack-apply"),
        status: byId("webgl-gameplay-pack-status"),
        autosaveStatus: byId("webgl-autosave-status") || panel?.querySelector?.("[data-webgl-autosave-status]") || null
      };
    }

    function webglGameplayPackLobbyStageNode(create = false) {
      const existing = typeof document.getElementById === "function"
        ? document.getElementById("webgl-gameplay-pack-lobby-stage")
        : document.querySelector?.("[data-webgl-gameplay-pack-lobby-stage]");
      if (existing || !create) return existing || null;

      const surface = (typeof document.getElementById === "function"
        ? document.getElementById("webgl-demo")
        : null) || document.querySelector?.("[data-scene-viewer]");
      const host = surface?.parentElement || document.querySelector?.(".canvas-wrap") || null;
      if (!host || typeof document.createElement !== "function") return null;

      const stage = document.createElement("div");
      stage.id = "webgl-gameplay-pack-lobby-stage";
      stage.className = "webgl-gameplay-pack-lobby-stage";
      stage.setAttribute?.("data-webgl-gameplay-pack-lobby-stage", "true");
      stage.dataset = stage.dataset || {};
      stage.dataset.visible = "false";
      stage.hidden = true;

      if (surface?.nextSibling && typeof host.insertBefore === "function") {
        host.insertBefore(stage, surface.nextSibling);
      } else if (typeof host.appendChild === "function") {
        host.appendChild(stage);
      }
      return stage;
    }

    function webglMountGameplayPackLobbyInMainView() {
      const panel = (typeof document.getElementById === "function"
        ? document.getElementById("webgl-gameplay-pack-selector")
        : null) || document.querySelector?.("[data-webgl-gameplay-pack-controls]");
      if (!panel) return null;

      const stage = webglGameplayPackLobbyStageNode(true);
      if (!stage) return panel;
      if (panel.parentElement !== stage && typeof stage.appendChild === "function") {
        stage.appendChild(panel);
      }
      panel.dataset = panel.dataset || {};
      panel.dataset.mainViewMounted = "true";
      panel.setAttribute?.("data-webgl-gameplay-pack-main-view", "true");
      stage.dataset = stage.dataset || {};
      stage.dataset.hasGameplayPackLobby = "true";
      return panel;
    }

    function webglSetGameplayPackLobbyVisible(visible) {
      const stage = webglGameplayPackLobbyStageNode(false);
      if (!stage) return;
      const shouldShow = Boolean(visible);
      stage.hidden = !shouldShow;
      stage.dataset = stage.dataset || {};
      stage.dataset.visible = String(shouldShow);
      const panel = stage.querySelector?.("[data-webgl-gameplay-pack-controls]");
      if (panel) {
        panel.dataset = panel.dataset || {};
        panel.dataset.lobbyVisible = String(shouldShow);
      }
    }

    function webglKnownGameplayPackDescriptor(pluginId) {
      const cleanPluginId = webglCanonicalGameplayPackId(pluginId);
      return WEBGL_BUILT_IN_JS_GAMEPLAY_PACKS.find((pack) => pack.pluginId === cleanPluginId) || null;
    }

    function webglGameplayPackDisplayLabel(pluginId, fallback = "") {
      const known = webglKnownGameplayPackDescriptor(pluginId);
      return String(known?.label || fallback || pluginId || "");
    }

    function webglAvailableGameplayPacks(project) {
      const metadata = project?.metadata || {};
      const available = metadata.availableGameplayPacks;
      const packs = Array.isArray(available?.packs) ? available.packs : [];
      const generated = metadata.generatedGameplayPlugins;
      const generatedPlugins = Array.isArray(generated?.plugins) ? generated.plugins : [];
      const byId = new Map();

      const rememberPack = (rawPack = {}, fallbackStatus = "") => {
        const rawPluginId = String(rawPack?.pluginId || rawPack?.id || "").trim();
        const pluginId = webglCanonicalGameplayPackId(rawPluginId);
        if (!pluginId) return;
        const known = webglKnownGameplayPackDescriptor(pluginId);
        byId.set(pluginId, {
          pluginId,
          label: webglGameplayPackDisplayLabel(pluginId, String(rawPack?.label || rawPack?.title || pluginId)),
          description: String(rawPack?.description || known?.description || ""),
          target: String(rawPack?.target || known?.target || ""),
          loadable: rawPack?.loadable !== false,
          defaultEnabled: rawPack?.defaultEnabled === true || known?.defaultEnabled === true,
          sourceKind: String(rawPack?.sourceKind || known?.sourceKind || ""),
          experimental: rawPack?.experimental === true,
          generated: rawPack?.generated === true,
          hiddenFromLobby: rawPack?.hiddenFromLobby === true,
          status: WEBGL_JS_GAMEPLAY_PACK_IDS.has(pluginId)
              || String(rawPack?.sourceKind || "").toLowerCase() === "js-gameplay-pack"
            ? "js-pack"
            : String(rawPack?.status || fallbackStatus || "")
        });
      };

      packs.forEach((pack) => rememberPack(pack));

      generatedPlugins.forEach((plugin) => {
        const scenario = Array.isArray(plugin?.documents)
          ? plugin.documents.find((document) => document?.kind === "scenario")
          : null;
        rememberPack({
          pluginId: plugin?.pluginId || plugin?.id,
          label: scenario?.title || plugin?.title || plugin?.pluginId || plugin?.id,
          description: "",
          loadable: true,
          defaultEnabled: plugin?.defaultEnabled === true,
          status: plugin?.status || "generated"
        }, "generated");
      });

      WEBGL_BUILT_IN_JS_GAMEPLAY_PACKS.forEach((pack) => {
        if (!byId.has(pack.pluginId)) rememberPack(pack);
      });

      return Array.from(byId.values()).sort((left, right) => {
        const leftOrder = left.pluginId === WEBGL_OPENING_SHUTTLE_JS_PACK_ID
          ? 0
          : left.pluginId === WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID
            ? 1
            : 2;
        const rightOrder = right.pluginId === WEBGL_OPENING_SHUTTLE_JS_PACK_ID
          ? 0
          : right.pluginId === WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID
            ? 1
            : 2;
        if (leftOrder !== rightOrder) return leftOrder - rightOrder;
        return left.label.localeCompare(right.label);
      });
    }

    function webglGameplayPackSelectionStorageValue(pluginIds = []) {
      const enabledPackIds = webglNormalizeGameplayPackIds(pluginIds);
      return {
        schema: "game.reloadGameplayPackSelection.v2",
        mode: enabledPackIds.length ? "selected" : "none",
        enabledPackIds,
        activeGameplayPackIds: enabledPackIds,
        updatedAt: Date.now()
      };
    }

    function webglStoreGameplayPackSelection(pluginIds = []) {
      const value = webglGameplayPackSelectionStorageValue(pluginIds);
      try {
        window.localStorage?.setItem?.(WEBGL_ENABLED_GAMEPLAY_PACKS_KEY, JSON.stringify(value));
        window.localStorage?.setItem?.(WEBGL_LEGACY_ACTIVE_GAMEPLAY_PACKS_KEY, JSON.stringify(value));
      } catch {
        // Selection persistence is best-effort; the in-memory start path still proceeds.
      }
      return value;
    }

    function webglReadAutosave() {
      try {
        const raw = window.localStorage?.getItem?.(WEBGL_AUTOSAVE_KEY);
        if (!raw) return null;
        const parsed = JSON.parse(String(raw || ""));
        if (!parsed || typeof parsed !== "object") return null;
        if (parsed.schema !== "game.webglAutosave.v1") return null;
        const enabledPackIds = webglNormalizeGameplayPackIds(parsed.enabledPackIds || parsed.activeGameplayPackIds || []);
        const checkpoint = parsed.checkpoint && typeof parsed.checkpoint === "object"
          ? parsed.checkpoint
          : {};
        return {
          schema: "game.webglAutosave.v1",
          kind: "webgl-autosave",
          projectId: String(parsed.projectId || webglProjectState.projectId || "webgl-demo"),
          savedAt: Math.max(0, Number(parsed.savedAt || 0)),
          checkpoint: {
            id: String(checkpoint.id || "unknown"),
            label: String(checkpoint.label || checkpoint.id || "Unknown checkpoint")
          },
          enabledPackIds,
          activeGameplayPackIds: enabledPackIds
        };
      } catch {
        return null;
      }
    }

    function webglAutosaveCheckpointLabel(checkpoint = {}) {
      const id = String(checkpoint?.id || "");
      if (checkpoint?.label) return String(checkpoint.label);
      if (id === "new-game-start") return "New Game Start";
      if (id === "opening-shuttle-started") return "Opening Shuttle Started";
      if (id === "opening-shuttle-completed") return "Opening Shuttle Completed";
      if (id === "mother-ship-shuttle-bay") return "Mother Ship Shuttle Bay";
      return id || "Unknown checkpoint";
    }

    function webglBuildAutosaveValue(options = {}) {
      const enabledPackIds = webglNormalizeGameplayPackIds(options.enabledPackIds || options.activeGameplayPackIds || []);
      const checkpointId = String(options.checkpointId || options.checkpoint?.id || "new-game-start");
      const checkpointLabel = webglAutosaveCheckpointLabel({
        id: checkpointId,
        label: options.checkpointLabel || options.checkpoint?.label || ""
      });
      return {
        schema: "game.webglAutosave.v1",
        kind: "webgl-autosave",
        projectId: String(options.projectId || webglProjectState.projectId || "webgl-demo"),
        savedAt: Number.isFinite(Number(options.savedAt)) ? Number(options.savedAt) : Date.now(),
        checkpoint: {
          id: checkpointId,
          label: checkpointLabel
        },
        enabledPackIds,
        activeGameplayPackIds: enabledPackIds,
        source: String(options.source || "webgl-game-start"),
        runtimeLocal: true
      };
    }

    function webglStoreAutosave(options = {}) {
      const value = webglBuildAutosaveValue(options);
      webglProjectState.lastAutosave = value;
      try {
        window.localStorage?.setItem?.(WEBGL_AUTOSAVE_KEY, JSON.stringify(value));
      } catch {
        // Autosave is best-effort in browser localStorage.
      }
      return value;
    }

    function webglFormatAutosaveTimestamp(savedAt = 0) {
      const value = Number(savedAt || 0);
      if (!Number.isFinite(value) || value <= 0) return "";
      try {
        return new Date(value).toLocaleString();
      } catch {
        return "";
      }
    }

    function webglAutosaveStatusMessage(autosave = webglReadAutosave(), options = {}) {
      if (!autosave) return "Autosave: none yet.";
      const count = webglNormalizeGameplayPackIds(autosave.enabledPackIds).length;
      const plural = count === 1 ? "pack" : "packs";
      const savedAt = webglFormatAutosaveTimestamp(autosave.savedAt);
      const checkpoint = webglAutosaveCheckpointLabel(autosave.checkpoint);
      const suffix = savedAt ? ` • saved ${savedAt}` : "";
      const prefix = options?.justSaved ? "Autosave saved" : "Autosave";
      return `${prefix}: ${checkpoint} • ${count} ${plural}${suffix}`;
    }

    function webglUpdateAutosaveStatus(autosave = webglReadAutosave(), options = {}) {
      const {autosaveStatus} = webglGameplayPackSelectorNodes();
      if (!autosaveStatus) return autosave;
      autosaveStatus.textContent = webglAutosaveStatusMessage(autosave, options);
      autosaveStatus.dataset = autosaveStatus.dataset || {};
      autosaveStatus.dataset.hasAutosave = String(Boolean(autosave));
      autosaveStatus.dataset.checkpointId = String(autosave?.checkpoint?.id || "");
      autosaveStatus.dataset.packCount = String(webglNormalizeGameplayPackIds(autosave?.enabledPackIds || []).length);
      autosaveStatus.dataset.mode = autosave
        ? (options?.justSaved ? "saved" : "available")
        : "empty";
      autosaveStatus.dataset.lastSavedAt = String(autosave?.savedAt || "");
      return autosave;
    }

    function webglAutosaveToastNode(create = false) {
      const existing = typeof document.getElementById === "function"
        ? document.getElementById("webgl-autosave-toast")
        : document.querySelector?.("[data-webgl-autosave-toast]");
      if (existing || !create) return existing || null;
      if (typeof document.createElement !== "function") return null;

      const surface = (typeof document.getElementById === "function"
        ? document.getElementById("webgl-demo")
        : null) || document.querySelector?.("[data-scene-viewer]");
      const host = surface?.parentElement || document.querySelector?.(".canvas-wrap") || null;
      if (!host) return null;

      const toast = document.createElement("div");
      toast.id = "webgl-autosave-toast";
      toast.className = "webgl-autosave-toast";
      toast.setAttribute?.("data-webgl-autosave-toast", "true");
      toast.dataset = toast.dataset || {};
      toast.dataset.visible = "false";
      toast.hidden = true;
      if (typeof host.appendChild === "function") host.appendChild(toast);
      return toast;
    }

    function webglShowAutosaveToast(message = "") {
      const toast = webglAutosaveToastNode(true);
      if (!toast) return null;
      toast.textContent = String(message || "");
      toast.hidden = false;
      toast.dataset = toast.dataset || {};
      toast.dataset.visible = "true";
      const hide = () => {
        toast.hidden = true;
        toast.dataset.visible = "false";
      };
      if (typeof window?.clearTimeout === "function" && toast.__webglAutosaveToastTimeout) {
        window.clearTimeout(toast.__webglAutosaveToastTimeout);
      }
      if (typeof window?.setTimeout === "function") {
        toast.__webglAutosaveToastTimeout = window.setTimeout(hide, 4200);
      }
      return toast;
    }

    function webglGameplayPackSelectionLabel(pluginIds = []) {
      const ids = webglNormalizeGameplayPackIds(pluginIds);
      if (!ids.length) return "None — base game";
      return ids.map((pluginId) => webglGameplayPackDisplayLabel(pluginId, pluginId)).join(", ");
    }

    function webglGameplayPackLobbyStatusMessage(pluginIds = []) {
      const ids = webglNormalizeGameplayPackIds(pluginIds);
      if (!ids.length) return "No gameplay packs selected. START NEW GAME will run the base game.";
      const plural = ids.length === 1 ? "pack" : "packs";
      return `${ids.length} gameplay ${plural} selected: ${webglGameplayPackSelectionLabel(ids)}. START NEW GAME will save and arm them.`;
    }

    function webglSetGameplayPackSelectorStatus(message = "", mode = "") {
      const {status} = webglGameplayPackSelectorNodes();
      if (!status) return;
      status.textContent = message;
      status.dataset = status.dataset || {};
      status.dataset.mode = String(mode || "");
    }

    function webglGameplayPackInstallStatusMessage(result = null) {
      const raw = result && typeof result === "object" ? result : {};
      if (raw.mode === "none") return "Gameplay Pack runtime: None — base game";
      if (raw.error) return `Gameplay Pack runtime error: ${String(raw.error)}`;
      if (raw.installed === true) {
        const packIds = webglNormalizeGameplayPackIds(raw.packIds || raw.activeGameplayPackIds || raw.packId || []);
        const titles = Array.isArray(raw.results)
          ? raw.results
            .filter((entry) => entry?.installed === true)
            .map((entry) => String(entry?.title || webglGameplayPackDisplayLabel(entry?.packId, entry?.packId) || ""))
            .filter(Boolean)
          : [];
        const label = titles.length
          ? titles.join(", ")
          : String(raw.title || webglGameplayPackSelectionLabel(packIds) || raw.packId || "selected JS pack");
        if (packIds.includes(WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID)) {
          return `Gameplay Pack runtime: ${label} installed — triggers after the bay-entry cutscene; watch SHIP and CHARACTER AI HUD`;
        }
        return `Gameplay Pack runtime: ${label} installed`;
      }
      if (raw.mode === "selected") return "Gameplay Pack runtime: selected packs not installed";
      return "";
    }

    function webglRecordJsGameplayPackInstallResult(result = null) {
      const raw = result && typeof result === "object" ? result : null;
      webglProjectState.jsGameplayPackInstallResult = raw;
      const nodes = webglGameplayPackSelectorNodes();
      if (nodes.panel) {
        nodes.panel.dataset.jsGameplayPackInstalled = String(raw?.installed === true);
        nodes.panel.dataset.jsGameplayPackMode = String(raw?.mode || "");
        nodes.panel.dataset.jsGameplayPackId = String(raw?.packId || "");
        nodes.panel.dataset.jsGameplayPackError = String(raw?.error || "");
      }
      const statusMessage = webglGameplayPackInstallStatusMessage(raw);
      if (statusMessage) {
        webglSetGameplayPackSelectorStatus(
          statusMessage,
          raw?.error ? "error" : raw?.installed === true ? "selected" : raw?.mode === "none" ? "none" : ""
        );
      }
      return raw;
    }

    function webglSelectedGameplayPackIdsFromControls(project = webglProjectState.project) {
      const nodes = webglGameplayPackSelectorNodes();
      if (nodes.checkboxes?.length) {
        return webglNormalizeGameplayPackIds(
          nodes.checkboxes
            .filter((checkbox) => checkbox.checked === true)
            .map((checkbox) => checkbox.value || checkbox.dataset?.gameplayPackId || "")
        );
      }
      if (nodes.select) {
        const pluginId = String(nodes.select.value || "None").trim();
        return webglNormalizeGameplayPackIds(pluginId);
      }
      return webglReloadGameplayPackSelection(project).activeGameplayPackIds;
    }

    function webglBuildGameplayPackCheckbox(pack, selectedIds) {
      const label = document.createElement("label");
      label.className = "webgl-gameplay-pack-option";
      label.dataset = label.dataset || {};
      label.dataset.gameplayPackId = pack.pluginId;

      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = pack.pluginId;
      input.checked = selectedIds.includes(pack.pluginId);
      input.disabled = pack.loadable === false;
      input.dataset = input.dataset || {};
      input.dataset.webglGameplayPackCheckbox = "true";
      input.dataset.gameplayPackId = pack.pluginId;
      input.setAttribute?.("data-webgl-gameplay-pack-checkbox", "true");

      const body = document.createElement("span");
      body.className = "webgl-gameplay-pack-option-copy";

      const title = document.createElement("span");
      title.className = "webgl-gameplay-pack-option-title";
      title.textContent = pack.label || pack.pluginId;

      const meta = document.createElement("span");
      meta.className = "webgl-gameplay-pack-option-meta";
      const defaultSuffix = pack.defaultEnabled === true ? " · default on" : "";
      const target = pack.target ? `${pack.target}` : "Gameplay pack";
      const experimentalSuffix = pack.experimental === true || pack.generated === true ? " · experimental" : "";
      meta.textContent = `${target}${defaultSuffix}${experimentalSuffix}`;

      const description = document.createElement("span");
      description.className = "webgl-gameplay-pack-option-description";
      description.textContent = pack.description || "";

      body.appendChild(title);
      body.appendChild(meta);
      if (pack.description) body.appendChild(description);
      label.appendChild(input);
      label.appendChild(body);
      return label;
    }

    function webglSyncLegacyGameplayPackSelect(nodes, packs, selectedIds) {
      if (!nodes.select) return;
      nodes.select.innerHTML = "";
      const none = document.createElement("option");
      none.value = "None";
      none.textContent = "None — base game";
      nodes.select.appendChild(none);

      packs.forEach((pack) => {
        const option = document.createElement("option");
        option.value = pack.pluginId;
        option.textContent = pack.label || pack.pluginId;
        option.disabled = pack.loadable === false;
        nodes.select.appendChild(option);
      });

      const selectedPluginId = selectedIds[0] || "";
      nodes.select.value = selectedPluginId || "None";
      if (selectedPluginId && nodes.select.value !== selectedPluginId) {
        const option = document.createElement("option");
        option.value = selectedPluginId;
        option.textContent = `${selectedPluginId} (selected)`;
        nodes.select.appendChild(option);
        nodes.select.value = selectedPluginId;
      }
    }

    function syncWebglGameplayPackSelector(project = webglProjectState.project) {
      const nodes = webglGameplayPackSelectorNodes();
      if (!nodes.panel) return null;
      const selection = webglReloadGameplayPackSelection(project);
      const packs = webglAvailableGameplayPacks(project);
      const selectedIds = webglNormalizeGameplayPackIds(selection.activeGameplayPackIds || []);

      if (nodes.checklist) {
        nodes.checklist.innerHTML = "";
        packs.forEach((pack) => {
          nodes.checklist.appendChild(webglBuildGameplayPackCheckbox(pack, selectedIds));
        });
      }

      webglSyncLegacyGameplayPackSelect(nodes, packs, selectedIds);

      nodes.panel.dataset = nodes.panel.dataset || {};
      nodes.panel.dataset.mode = selection.mode;
      nodes.panel.dataset.gameplayPackMode = selection.mode;
      nodes.panel.dataset.selectionSource = selection.source;
      nodes.panel.dataset.selectedGameplayPackCount = String(selectedIds.length);
      webglSetGameplayPackSelectorStatus(
        selection.source === "default-enabled"
          ? `Default packs selected: ${webglGameplayPackSelectionLabel(selectedIds)}. Press START NEW GAME.`
          : webglGameplayPackLobbyStatusMessage(selectedIds),
        selection.mode
      );
      webglUpdateAutosaveStatus();
      return selection;
    }

    function webglSelectedJsGameplayPackIds(
      selection = webglReloadGameplayPackSelection(webglProjectState.project),
      project = webglProjectState.project
    ) {
      const ids = Array.isArray(selection?.activeGameplayPackIds) ? selection.activeGameplayPackIds : [];
      const jsPackIds = webglJsGameplayPackIdsForProject(project);
      return ids.map(webglCanonicalGameplayPackId).filter((id) => jsPackIds.has(id));
    }

    function webglSelectedJsGameplayPackId(selection = webglReloadGameplayPackSelection(webglProjectState.project)) {
      return webglSelectedJsGameplayPackIds(selection)[0] || "";
    }

    async function webglLoadJsGameplayPackSource(packId) {
      const cleanPackId = webglCanonicalGameplayPackId(packId);
      if (!cleanPackId) return null;
      const payload = await webglPost(WEBGL_JS_GAMEPLAY_PACK_SOURCE_ENDPOINT, {
        project_id: webglProjectState.projectId || "webgl-demo",
        pack_id: cleanPackId,
        plugin_id: cleanPackId
      });
      if (!payload?.source) {
        throw new Error(payload?.error || `gameplay pack source unavailable: ${cleanPackId}`);
      }
      return payload;
    }

    function webglJsGameplayPackRuntimeTarget(packId, loaded = {}) {
      const cleanPackId = webglCanonicalGameplayPackId(packId);
      const targets = loaded && typeof loaded === "object" && !Array.isArray(loaded)
        ? loaded.targets || {}
        : {};
      const encounterId = String(targets?.encounter || loaded?.encounterId || "");
      const sectionId = String(targets?.section || loaded?.sectionId || "");
      if (cleanPackId === WEBGL_OPENING_SHUTTLE_JS_PACK_ID || encounterId === "opening-shuttle-ambush") {
        return "opening-shuttle";
      }
      if (cleanPackId === WEBGL_MAIN_SHIP_BAY_BOARDERS_JS_PACK_ID || sectionId === "main-ship-bay") {
        return "main-ship-bay";
      }
      return "";
    }

    async function installSelectedWebglJsGameplayPack(runtime = gameSurfaceRuntime, project = webglProjectState.project) {
      const selection = webglReloadGameplayPackSelection(project);
      const packIds = webglSelectedJsGameplayPackIds(selection, project);
      if (!packIds.length) {
        const clearedOpening = runtime && typeof runtime.openingShuttleClearGameplayPackCommandHarness === "function"
          ? runtime.openingShuttleClearGameplayPackCommandHarness({
            source: "webgl-desktop.selected-js-gameplay-pack",
            reason: "no-active-gameplay-pack"
          })
          : null;
        const clearedMainShip = runtime && typeof runtime.mainShipClearGameplayPackCommandHarness === "function"
          ? runtime.mainShipClearGameplayPackCommandHarness({
            source: "webgl-desktop.selected-js-gameplay-pack",
            reason: "no-active-gameplay-pack"
          })
          : null;
        return {
          schema: "game.webglJsGameplayPackInstallResult.v1",
          kind: "webgl-js-gameplay-pack-install-result",
          mode: "none",
          installed: false,
          cleared: Boolean(
            clearedOpening?.jsGameplayPack?.installed === false
            || clearedOpening?.installed === false
            || clearedMainShip?.installed === false
          ),
          activeGameplayPackIds: [],
          source: selection.source,
          snapshot: clearedOpening || null,
          mainShipSnapshot: clearedMainShip || null
        };
      }

      const results = [];
      for (const packId of packIds) {
        const loaded = await webglLoadJsGameplayPackSource(packId);
        const runtimeTarget = webglJsGameplayPackRuntimeTarget(packId, loaded);
        if (runtimeTarget === "opening-shuttle") {
          if (!runtime || typeof runtime.openingShuttleInstallGameplayPackSource !== "function") {
            results.push({
              packId,
              installed: false,
              error: "shuttle scene runtime unavailable"
            });
            continue;
          }
          const snapshot = runtime.openingShuttleInstallGameplayPackSource(loaded.source, {
            source: "webgl-desktop.selected-js-gameplay-pack",
            selectedGameplayPackId: packId
          });
          results.push({
            packId,
            title: String(snapshot?.jsGameplayPack?.title || loaded.title || ""),
            installed: Boolean(snapshot?.jsGameplayPack?.installed),
            commandsApplied: Math.max(0, Number(snapshot?.jsGameplayPack?.commandsApplied || 0)),
            lastCommandType: String(snapshot?.jsGameplayPack?.lastCommandType || ""),
            runtimeTarget,
            experimental: loaded?.experimental === true || loaded?.generated === true,
            snapshot
          });
        } else if (runtimeTarget === "main-ship-bay") {
          if (!runtime || typeof runtime.mainShipInstallGameplayPackSource !== "function") {
            results.push({
              packId,
              installed: false,
              error: "main-ship scene runtime unavailable"
            });
            continue;
          }
          const snapshot = runtime.mainShipInstallGameplayPackSource(loaded.source, {
            source: "webgl-desktop.selected-js-gameplay-pack",
            selectedGameplayPackId: packId
          });
          results.push({
            packId,
            title: String(snapshot?.title || loaded.title || ""),
            installed: Boolean(snapshot?.installed),
            commandsApplied: Math.max(0, Number(snapshot?.commandsApplied || snapshot?.commandApplyCount || 0)),
            lastCommandType: String(snapshot?.lastCommandType || ""),
            runtimeTarget,
            experimental: loaded?.experimental === true || loaded?.generated === true,
            snapshot
          });
        } else {
          results.push({
            packId,
            installed: false,
            error: `unsupported JS gameplay pack target: ${packId}`
          });
        }
      }

      const installedResults = results.filter((result) => result.installed === true);
      const first = installedResults[0] || results[0] || null;
      const firstError = results.find((result) => result.error)?.error || "";
      return {
        schema: "game.webglJsGameplayPackInstallResult.v1",
        kind: "webgl-js-gameplay-pack-install-result",
        mode: "selected",
        installed: installedResults.length > 0,
        activeGameplayPackIds: packIds,
        packId: String(first?.packId || ""),
        packIds,
        title: String(first?.title || ""),
        commandsApplied: Math.max(0, Number(first?.commandsApplied || 0)),
        lastCommandType: String(first?.lastCommandType || ""),
        results,
        snapshot: first?.snapshot || null,
        error: installedResults.length > 0 ? "" : firstError
      };
    }

    async function startWebglGameFromGameplayPackLobby() {
      const nodes = webglGameplayPackSelectorNodes();
      const packIds = webglSelectedGameplayPackIdsFromControls(webglProjectState.project);
      const saved = webglStoreGameplayPackSelection(packIds);
      const autosave = webglStoreAutosave({
        source: "start-new-game",
        checkpointId: "new-game-start",
        checkpointLabel: "New Game Start",
        enabledPackIds: saved.enabledPackIds || packIds
      });
      webglUpdateAutosaveStatus(autosave, {justSaved: true});
      webglShowAutosaveToast(webglAutosaveStatusMessage(autosave, {justSaved: true}));
      webglProjectState.gameStarted = true;
      webglSetGameplayPackLobbyVisible(false);
      if (nodes.start) nodes.start.disabled = true;
      webglSetGameplayPackSelectorStatus(
        packIds.length
          ? `Starting game with ${webglGameplayPackSelectionLabel(packIds)}…`
          : "Starting base game with no gameplay packs…",
        packIds.length ? "selected" : "none"
      );

      try {
        await initWebgl(webglProjectState.pendingSceneId || "");
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error || "game start failed");
        webglProjectState.gameStarted = false;
        webglSetGameplayPackLobbyVisible(true);
        webglSetGameplayPackSelectorStatus(`START NEW GAME failed: ${message}`, "error");
        if (nodes.start) nodes.start.disabled = false;
      }
      return saved;
    }

    async function applyWebglGameplayPackSelection() {
      return startWebglGameFromGameplayPackLobby();
    }

    function webglHandleGameplayPackCheckboxChange() {
      const ids = webglSelectedGameplayPackIdsFromControls(webglProjectState.project);
      const nodes = webglGameplayPackSelectorNodes();
      if (nodes.panel) {
        nodes.panel.dataset.gameplayPackMode = ids.length ? "selected" : "none";
        nodes.panel.dataset.selectedGameplayPackCount = String(ids.length);
      }
      webglSetGameplayPackSelectorStatus(webglGameplayPackLobbyStatusMessage(ids), ids.length ? "selected" : "none");
    }

    function bindWebglGameplayPackSelector() {
      webglMountGameplayPackLobbyInMainView();
      const nodes = webglGameplayPackSelectorNodes();
      if (!nodes.panel || !nodes.start) return;
      if (!webglProjectState.gameStarted) {
        webglSetGameplayPackLobbyVisible(true);
      }
      if (!nodes.start.dataset.webglGameplayPackBound) {
        nodes.start.dataset.webglGameplayPackBound = "true";
        nodes.start.addEventListener("click", startWebglGameFromGameplayPackLobby);
      }
      if (nodes.checklist && !nodes.checklist.dataset.webglGameplayPackBound) {
        nodes.checklist.dataset.webglGameplayPackBound = "true";
        nodes.checklist.addEventListener("change", webglHandleGameplayPackCheckboxChange);
      }
      if (nodes.select && !nodes.select.dataset.webglGameplayPackBound) {
        nodes.select.dataset.webglGameplayPackBound = "true";
        nodes.select.addEventListener("change", webglHandleGameplayPackCheckboxChange);
      }
      syncWebglGameplayPackSelector();
    }

    function webglReadStoredGameplayPackSelection(project = webglProjectState.project) {
      return webglReloadGameplayPackSelection(project);
    }

    function syncWebglGameplayPackControls(project = webglProjectState.project, runtime = null) {
      const selection = syncWebglGameplayPackSelector(project);
      const runtimeSelection = typeof runtime?.activeGameplayPackSelection === "function"
        ? runtime.activeGameplayPackSelection()
        : null;
      const runtimeIds = webglNormalizeGameplayPackIds(
        Array.isArray(runtimeSelection?.activeGameplayPackIds)
          ? runtimeSelection.activeGameplayPackIds
          : Array.isArray(runtimeSelection?.activePluginIds)
            ? runtimeSelection.activePluginIds
            : []
      );
      if (!runtimeIds.length) return selection;
      const nodes = webglGameplayPackSelectorNodes();
      if (nodes.checklist) {
        Array.from(nodes.checklist.querySelectorAll?.("[data-webgl-gameplay-pack-checkbox]") || [])
          .forEach((checkbox) => {
            checkbox.checked = runtimeIds.includes(webglCanonicalGameplayPackId(checkbox.value || checkbox.dataset?.gameplayPackId || ""));
          });
      }
      if (nodes.select) nodes.select.value = runtimeIds[0] || "None";
      if (nodes.panel) {
        nodes.panel.dataset = nodes.panel.dataset || {};
        nodes.panel.dataset.mode = "selected";
        nodes.panel.dataset.gameplayPackMode = "selected";
        nodes.panel.dataset.selectionSource = "runtime";
        nodes.panel.dataset.selectedGameplayPackCount = String(runtimeIds.length);
      }
      webglSetGameplayPackSelectorStatus(`Runtime packs: ${webglGameplayPackSelectionLabel(runtimeIds)}`, "selected");
      return {
        schema: "game.reloadGameplayPackSelection.v1",
        kind: "reload-gameplay-pack-selection",
        source: "runtime",
        readOnly: true,
        runtimeLocal: true,
        persisted: false,
        mode: "selected",
        activeGameplayPackIds: runtimeIds.map((pluginId) => String(pluginId || "")).filter(Boolean)
      };
    }

    function applyWebglGameplayPackControlSelection() {
      const value = webglStoreGameplayPackSelection(webglSelectedGameplayPackIdsFromControls(webglProjectState.project));
      webglSetGameplayPackSelectorStatus(
        value.activeGameplayPackIds.length
          ? `Saved gameplay packs: ${webglGameplayPackSelectionLabel(value.activeGameplayPackIds)}`
          : "Saved gameplay packs: None — base game",
        value.activeGameplayPackIds.length ? "selected" : "none"
      );
      return value;
    }

    function ensureWebglSystemScenarioRuntime(projectId, project, activeSystemId = "") {
      const api = window.MainComputerSystemScenarioRuntime;
      const definition = project?.metadata?.systemScenarios;
      if (!api?.ensure || !definition) {
        webglProjectState.systemScenarioRuntime = null;
        webglProjectState.systemScenarioError = project
          ? "System-scenario runtime unavailable."
          : "";
        api?.clearCurrent?.();
        window.MainComputerPaxScenarioInteraction?.setRuntime?.(null);
        return null;
      }
      try {
        const packSelection = webglReloadGameplayPackSelection(project);
        const runtime = api.ensure(projectId, definition, {
          activeSystemId: String(activeSystemId || webglDefaultStrategicSystem(project)),
          generatedGameplayCatalog: project?.metadata?.generatedGameplayPlugins,
          activeGameplayPackIds: packSelection.activeGameplayPackIds
        });
        webglProjectState.systemScenarioRuntime = runtime;
        webglProjectState.systemScenarioError = "";
        window.MainComputerPaxScenarioInteraction?.setRuntime?.(runtime);
        const characters = window.MainComputerCharacterAIRuntime?.current?.() || null;
        if (characters) {
          window.MainComputerPaxScenarioInteraction?.setCharacterRuntime?.(characters);
        }
        return runtime;
      } catch (error) {
        webglProjectState.systemScenarioRuntime = null;
        webglProjectState.systemScenarioError = error instanceof Error
          ? error.message
          : String(error || "System-scenario runtime failed.");
        api?.clearCurrent?.();
        window.MainComputerPaxScenarioInteraction?.setRuntime?.(null);
        console.error("System-scenario runtime initialization failed", error);
        return null;
      }
    }

    function ensureWebglStrategicSession(projectId, project, activeSystemId = "") {
      const api = window.MainComputerStrategicAISession;
      if (!api?.ensure || !project?.metadata?.strategicAI) {
        webglProjectState.strategicSession = null;
        webglProjectState.strategicSessionError = project
          ? "Strategic AI session runtime unavailable."
          : "";
        window.MainComputerStrategicAIDebugPanel?.setSession?.(null);
        window.MainComputerStrategicAIVelaInteraction?.setSession?.(null);
        window.MainComputerStrategicAISolaceInteraction?.setSession?.(null);
        window.MainComputerStrategicAITravelIntegration?.setSession?.(null);
        return null;
      }
      try {
        const session = api.ensure(projectId, project, {
          activeSystemId: String(activeSystemId || webglDefaultStrategicSystem(project))
        });
        webglProjectState.strategicSession = session;
        webglProjectState.strategicSessionError = "";
        window.MainComputerStrategicAIDebugPanel?.setSession?.(session);
        window.MainComputerStrategicAIVelaInteraction?.setSession?.(session);
        window.MainComputerStrategicAISolaceInteraction?.setSession?.(session);
        window.MainComputerStrategicAITravelIntegration?.setSession?.(session);
        return session;
      } catch (error) {
        webglProjectState.strategicSession = null;
        webglProjectState.strategicSessionError = error instanceof Error
          ? error.message
          : String(error || "Strategic AI session failed.");
        window.MainComputerStrategicAIDebugPanel?.setSession?.(null);
        window.MainComputerStrategicAIVelaInteraction?.setSession?.(null);
        window.MainComputerStrategicAISolaceInteraction?.setSession?.(null);
        window.MainComputerStrategicAITravelIntegration?.setSession?.(null);
        console.error("Strategic AI session initialization failed", error);
        return null;
      }
    }

    function syncWebglStrategicNavigation(navigation = {}) {
      const session = webglProjectState.strategicSession;
      const scenarioRuntime = webglProjectState.systemScenarioRuntime;
      const systemId = String(navigation?.currentSystemId || "");
      if (!systemId) return null;
      try {
        if (scenarioRuntime?.setActiveSystemId
            && scenarioRuntime.state?.activeSystemId !== systemId) {
          scenarioRuntime.setActiveSystemId(systemId);
        }
        const paxInteraction = window.MainComputerPaxScenarioInteraction;
        paxInteraction?.setRuntime?.(scenarioRuntime || null);
        const paxActivation = paxInteraction?.handleNavigation?.(navigation) || null;
        if (!session) {
          return {
            activeSystemId: systemId,
            strategicSession: false,
            paxActivation
          };
        }
        const integration = window.MainComputerStrategicAITravelIntegration;
        if (integration?.handleNavigation) {
          return integration.handleNavigation(session, navigation);
        }
        if (systemId !== session.activeSystemId) {
          return session.setActiveSystemId(systemId);
        }
        return {reused: true, activeSystemId: systemId};
      } catch (error) {
        webglProjectState.strategicSessionError = error instanceof Error
          ? error.message
          : String(error || "Strategic travel integration failed.");
        console.error("Strategic AI travel integration failed", error);
        return null;
      }
    }

    function normalizeWebglVfxValue(value, fallback = 2) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.min(4, Math.max(1, number));
    }

    function formatWebglVfxValue(value) {
      const number = normalizeWebglVfxValue(value);
      return `${Number.isInteger(number) ? number.toFixed(0) : number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}x`;
    }

    function ensureWebglSceneVfx(scene) {
      if (!scene || typeof scene !== "object") return null;
      scene.metadata = scene.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
      scene.metadata.vfx = scene.metadata.vfx && typeof scene.metadata.vfx === "object" ? scene.metadata.vfx : {};
      scene.metadata.vfx.particleMultiplier = normalizeWebglVfxValue(scene.metadata.vfx.particleMultiplier ?? scene.metadata.particleMultiplier ?? 2);
      scene.metadata.vfx.effectMultiplier = normalizeWebglVfxValue(scene.metadata.vfx.effectMultiplier ?? scene.metadata.effectMultiplier ?? 2);
      scene.metadata.vfx.maxParticlesPerEmitter = Math.max(440, Number(scene.metadata.vfx.maxParticlesPerEmitter) || 440);
      scene.metadata.quadrupleParticles = scene.metadata.vfx.particleMultiplier >= 4;
      scene.metadata.uiParticleControls = true;
      return scene.metadata.vfx;
    }

    function webglVfxNodes() {
      return {
        particle: document.querySelector("#webgl-particle-density"),
        particleValue: document.querySelector("#webgl-particle-density-value"),
        effect: document.querySelector("#webgl-effect-intensity"),
        effectValue: document.querySelector("#webgl-effect-intensity-value"),
        presets: [...document.querySelectorAll("[data-webgl-vfx-preset]")]
      };
    }

    function syncWebglVfxControls(scene) {
      const vfx = ensureWebglSceneVfx(scene);
      const nodes = webglVfxNodes();
      if (!vfx) return;
      if (nodes.particle) nodes.particle.value = String(vfx.particleMultiplier);
      if (nodes.effect) nodes.effect.value = String(vfx.effectMultiplier);
      if (nodes.particleValue) nodes.particleValue.textContent = formatWebglVfxValue(vfx.particleMultiplier);
      if (nodes.effectValue) nodes.effectValue.textContent = formatWebglVfxValue(vfx.effectMultiplier);
      nodes.presets.forEach((button) => {
        const preset = normalizeWebglVfxValue(button.dataset.webglVfxPreset, 1);
        const active = Math.abs(preset - vfx.particleMultiplier) < 0.01 && Math.abs(preset - vfx.effectMultiplier) < 0.01;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function updateWebglVfxScene({particleMultiplier = null, effectMultiplier = null} = {}) {
      const scene = gameSurfaceRuntime?.scene || bestKnownWebglScene()?.scene || null;
      if (!scene) return;
      const vfx = ensureWebglSceneVfx(scene);
      if (!vfx) return;
      if (particleMultiplier !== null) vfx.particleMultiplier = normalizeWebglVfxValue(particleMultiplier);
      if (effectMultiplier !== null) vfx.effectMultiplier = normalizeWebglVfxValue(effectMultiplier);
      scene.metadata.quadrupleParticles = vfx.particleMultiplier >= 4;
      scene.metadata.uiParticleControls = true;
      syncWebglVfxControls(scene);
      renderWebglSceneCandidate({
        source: "scene-store",
        projectId: webglProjectState.projectId,
        project: webglProjectState.project,
        scene,
        assets: webglProjectState.assets,
        dirty: true,
        selectedObjectId: ""
      });
      window.MainComputerSceneStore?.saveScene?.(scene, {source: "webgl-vfx-controls", notify: true});
    }

    function bindWebglVfxControls() {
      const nodes = webglVfxNodes();
      if (nodes.particle && !nodes.particle.dataset.webglVfxBound) {
        nodes.particle.dataset.webglVfxBound = "true";
        nodes.particle.addEventListener("input", () => updateWebglVfxScene({particleMultiplier: nodes.particle.value}));
      }
      if (nodes.effect && !nodes.effect.dataset.webglVfxBound) {
        nodes.effect.dataset.webglVfxBound = "true";
        nodes.effect.addEventListener("input", () => updateWebglVfxScene({effectMultiplier: nodes.effect.value}));
      }
      nodes.presets.forEach((button) => {
        if (button.dataset.webglVfxBound) return;
        button.dataset.webglVfxBound = "true";
        button.addEventListener("click", () => updateWebglVfxScene({
          particleMultiplier: button.dataset.webglVfxPreset,
          effectMultiplier: button.dataset.webglVfxPreset
        }));
      });
    }

    function webglProjectScene(project, sceneId = "") {
      const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
      const selectedId = String(sceneId || project?.activeSceneId || window.MainComputerSceneStore?.selectedSceneId?.() || "default-empty-scene");
      return scenes.find((scene) => scene?.id === selectedId) || scenes[0] || null;
    }

    function upsertWebglPolygonAnnotation(project, sceneId, annotation) {
      if (!project || typeof project !== "object" || !annotation?.targetKey) return null;
      const scene = webglProjectScene(project, sceneId);
      if (!scene || String(scene.id || "") !== String(sceneId || "")) return null;
      scene.metadata = scene.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
      scene.metadata.shuttle3d = scene.metadata.shuttle3d && typeof scene.metadata.shuttle3d === "object"
        ? scene.metadata.shuttle3d
        : {};
      scene.metadata.shuttle3d.polygonAnnotations = Array.isArray(scene.metadata.shuttle3d.polygonAnnotations)
        ? scene.metadata.shuttle3d.polygonAnnotations
        : [];
      const annotations = scene.metadata.shuttle3d.polygonAnnotations;
      const targetKey = String(annotation.targetKey || "");
      const index = annotations.findIndex((candidate) => String(candidate?.targetKey || "") === targetKey);
      const cleanAnnotation = JSON.parse(JSON.stringify(annotation));
      if (index >= 0) annotations[index] = cleanAnnotation;
      else annotations.push(cleanAnnotation);
      return cleanAnnotation;
    }

    async function persistWebglPolygonAnnotation(detail = {}) {
      const projectId = String(detail?.projectId || webglProjectState.projectId || "webgl-demo");
      const sceneId = String(detail?.sceneId || "");
      const annotation = detail?.annotation && typeof detail.annotation === "object" ? detail.annotation : null;
      if (!sceneId) throw new Error("Annotation save is missing the scene id.");
      if (!annotation?.targetKey || !annotation?.id) {
        throw new Error("Annotation save is missing a stable target key or annotation id.");
      }

      const payload = {
        project_id: projectId,
        scene_id: sceneId,
        annotation,
        expected_content_hash: String(webglProjectState.contentHash || "")
      };
      const data = await webglPost("/api/applications/game-editor/project/annotation/write", payload);
      if (data?.annotation_verified !== true) {
        throw new Error("Annotation write returned without disk verification.");
      }

      webglProjectState.projectId = String(data.project_id || projectId);
      webglProjectState.contentHash = String(data.content_hash || webglProjectState.contentHash || "");
      upsertWebglPolygonAnnotation(webglProjectState.project, sceneId, data.annotation || annotation);
      const editorState = window.gameEditorState;
      if (editorState?.project && String(editorState.projectId || "") === webglProjectState.projectId) {
        upsertWebglPolygonAnnotation(editorState.project, sceneId, data.annotation || annotation);
        editorState.contentHash = webglProjectState.contentHash;
      }

      const writePath = String(data.write_path || `game_projects/${webglProjectState.projectId}/project.json`);
      const mergedSuffix = data.stale_hash_merged ? " after merging against newer disk state" : "";
      if (glStatus) {
        glStatus.textContent = `annotation saved to ${writePath}${mergedSuffix}`;
      }
      return {
        persisted: true,
        projectId: webglProjectState.projectId,
        sceneId,
        annotation: data.annotation || annotation,
        contentHash: webglProjectState.contentHash,
        writePath,
        writePathResolved: String(data.write_path_resolved || ""),
        staleHashMerged: Boolean(data.stale_hash_merged)
      };
    }

    function queueWebglPolygonAnnotationSave(detail = {}) {
      const run = Promise.resolve(webglProjectState.annotationSavePromise)
        .catch(() => undefined)
        .then(() => persistWebglPolygonAnnotation(detail));
      webglProjectState.annotationSavePromise = run.catch(() => undefined);
      return run;
    }

    function webglEditorSceneCandidate(sceneId = "") {
      const editorState = window.gameEditorState;
      if (!editorState?.project) return null;
      const scene = webglProjectScene(editorState.project, sceneId);
      if (!scene) return null;
      return {
        source: "game-editor",
        projectId: String(editorState.projectId || editorState.project?.id || webglProjectState.projectId),
        project: editorState.project,
        scene,
        assets: Array.isArray(editorState.assets) ? editorState.assets : [],
        dirty: Boolean(editorState.dirty),
        selectedObjectId: String(editorState.selectedObjectId || "")
      };
    }

    function webglStoredSceneCandidate(sceneId = "") {
      if (!window.MainComputerSceneStore?.hasStoredScenes?.()) return null;
      const selectedSceneId = sceneId || window.MainComputerSceneStore?.selectedSceneId?.() || "default-empty-scene";
      const scene = window.MainComputerSceneStore?.getScene?.(selectedSceneId);
      if (!scene) return null;
      return {
        source: "scene-store",
        projectId: webglProjectState.projectId,
        project: webglProjectState.project,
        scene,
        assets: webglProjectState.assets,
        dirty: false,
        selectedObjectId: ""
      };
    }

    function webglProjectSceneCandidate(sceneId = "") {
      const scene = webglProjectScene(webglProjectState.project, sceneId);
      if (!scene) return null;
      return {
        source: "project",
        projectId: webglProjectState.projectId,
        project: webglProjectState.project,
        scene,
        assets: webglProjectState.assets,
        dirty: false,
        selectedObjectId: ""
      };
    }

    function fallbackWebglScene(sceneId = "default-empty-scene") {
      const scene = {
        "id": "default-empty-scene",
        "name": "Shuttle Boarding Defense",
        "version": 11,
        "background": "radial-gradient(circle at 50% 18%, rgba(59, 130, 246, 0.18), rgba(2, 6, 23, 0.98) 58%, #000 100%)",
        "objects": [
          {
            "id": "hero-sprite",
            "type": "sprite-actor",
            "x": 5.0,
            "y": 6.95,
            "width": 112,
            "height": 154,
            "props": {
              "label": "Player Cadet",
              "role": "player",
              "spawn": true,
              "color": "#93c5fd",
              "z": 26,
              "bob": 5,
              "motion": "idle",
              "spellState": "boarding-defense",
              "spriteSeries": [
                "stand",
                "scan",
                "tap",
                "ready"
              ],
              "spriteRig": {
                "style": "uniform-silhouette",
                "layers": [
                  "shadow",
                  "aura",
                  "core",
                  "mantle",
                  "sparkles"
                ],
                "castFrames": [
                  "stand",
                  "scan",
                  "tap",
                  "ready"
                ],
                "finisher": false
              },
              "firstPerson": true,
              "lookaroundAnchor": true
            }
          },
          {
            "id": "shuttle-floor",
            "type": "shuttle-deck",
            "x": 5.0,
            "y": 7.7,
            "width": 720,
            "height": 260,
            "props": {
              "label": "3D Shuttle Deck",
              "color": "#1e3a8a",
              "z": 0,
              "role": "walkable-floor",
              "lookaroundLayer": "floor"
            }
          },
          {
            "id": "forward-viewer",
            "type": "shuttle-window",
            "x": 5.0,
            "y": 1.35,
            "width": 620,
            "height": 170,
            "props": {
              "label": "Forward Viewport",
              "color": "#38bdf8",
              "z": 110,
              "role": "viewport",
              "showsStars": true,
              "showsMotherShip": true,
              "lookaroundLayer": "forward-view"
            }
          },
          {
            "id": "forward-bulkhead",
            "type": "shuttle-bulkhead",
            "x": 5.0,
            "y": 2.4,
            "width": 680,
            "height": 130,
            "props": {
              "label": "Forward Bulkhead",
              "color": "#475569",
              "z": 70,
              "role": "front-wall",
              "lookaroundLayer": "bulkhead"
            }
          },
          {
            "id": "nav-console",
            "type": "shuttle-console",
            "x": 4.1,
            "y": 4.3,
            "width": 260,
            "height": 86,
            "props": {
              "label": "Helm Console",
              "color": "#f97316",
              "z": 42,
              "role": "helm",
              "lookaroundLayer": "cockpit-controls"
            }
          },
          {
            "id": "science-console",
            "type": "shuttle-console",
            "x": 5.9,
            "y": 4.3,
            "width": 260,
            "height": 86,
            "props": {
              "label": "Science Console",
              "color": "#38bdf8",
              "z": 42,
              "role": "science",
              "lookaroundLayer": "cockpit-controls"
            }
          },
          {
            "id": "port-side-console",
            "type": "shuttle-side-console",
            "x": 2.25,
            "y": 5.75,
            "width": 240,
            "height": 76,
            "props": {
              "label": "Port Systems",
              "color": "#facc15",
              "z": 34,
              "role": "port-panel",
              "lookaroundLayer": "side-controls"
            }
          },
          {
            "id": "starboard-side-console",
            "type": "shuttle-side-console",
            "x": 7.75,
            "y": 5.75,
            "width": 240,
            "height": 76,
            "props": {
              "label": "Starboard Ops",
              "color": "#fb7185",
              "z": 34,
              "role": "starboard-panel",
              "lookaroundLayer": "side-controls"
            }
          },
          {
            "id": "helm-seat",
            "type": "shuttle-seat",
            "x": 4.25,
            "y": 5.35,
            "width": 92,
            "height": 92,
            "props": {
              "label": "Helm Seat",
              "color": "#64748b",
              "z": 24,
              "role": "seat"
            }
          },
          {
            "id": "ops-seat",
            "type": "shuttle-seat",
            "x": 5.75,
            "y": 5.35,
            "width": 92,
            "height": 92,
            "props": {
              "label": "Ops Seat",
              "color": "#64748b",
              "z": 24,
              "role": "seat"
            }
          },
          {
            "id": "aft-hatch",
            "type": "shuttle-hatch",
            "x": 5.0,
            "y": 8.65,
            "width": 220,
            "height": 132,
            "props": {
              "label": "Aft Hatch",
              "color": "#94a3b8",
              "z": 38,
              "role": "aft-wall",
              "lookaroundLayer": "aft"
            }
          },
          {
            "id": "port-hull-rib",
            "type": "shuttle-hull-rib",
            "x": 1.35,
            "y": 6.45,
            "width": 116,
            "height": 250,
            "props": {
              "label": "Port Hull Rib",
              "color": "#334155",
              "z": 44,
              "role": "hull"
            }
          },
          {
            "id": "starboard-hull-rib",
            "type": "shuttle-hull-rib",
            "x": 8.65,
            "y": 6.45,
            "width": 116,
            "height": 250,
            "props": {
              "label": "Starboard Hull Rib",
              "color": "#334155",
              "z": 44,
              "role": "hull"
            }
          },
          {
            "id": "hero-spell-aura",
            "type": "particle-emitter",
            "parentId": "hero-sprite",
            "x": 0,
            "y": 0,
            "width": 160,
            "height": 104,
            "props": {
              "label": "Combadge Glow",
              "role": "player-signal",
              "color": "#7dd3fc",
              "particleCount": 24,
              "particleSize": 3,
              "spread": 0.74,
              "motion": "rune-ring",
              "orbitRadius": 48,
              "verticalLift": 14,
              "zOffset": 44
            }
          },
          {
            "id": "console-status-glow",
            "type": "particle-emitter",
            "parentId": "nav-console",
            "x": 0,
            "y": 0,
            "width": 280,
            "height": 80,
            "props": {
              "label": "Console Status Glow",
              "role": "console-vfx",
              "color": "#fb923c",
              "particleCount": 38,
              "particleSize": 3,
              "spread": 0.88,
              "motion": "stream",
              "orbitRadius": 80,
              "verticalLift": 18,
              "zOffset": 22
            }
          },
          {
            "id": "science-status-glow",
            "type": "particle-emitter",
            "parentId": "science-console",
            "x": 0,
            "y": 0,
            "width": 280,
            "height": 80,
            "props": {
              "label": "Science Status Glow",
              "role": "console-vfx",
              "color": "#22d3ee",
              "particleCount": 34,
              "particleSize": 3,
              "spread": 0.82,
              "motion": "stream",
              "orbitRadius": 72,
              "verticalLift": 16,
              "zOffset": 22
            }
          },
          {
            "id": "hero-arc-bolt",
            "type": "particle-emitter",
            "parentId": "nav-console",
            "x": 0,
            "y": 0,
            "width": 420,
            "height": 74,
            "props": {
              "label": "Forward Sensor Sweep",
              "role": "sensor-pulse",
              "color": "#f59e0b",
              "particleCount": 52,
              "particleSize": 4,
              "spread": 0.92,
              "motion": "spell-bolt",
              "sourceId": "nav-console",
              "targetId": "forward-viewer",
              "sourceZOffset": 28,
              "targetZOffset": 72,
              "zOffset": 54
            }
          },
          {
            "id": "viewer-starfield",
            "type": "particle-emitter",
            "parentId": "forward-viewer",
            "x": 0,
            "y": 0,
            "width": 560,
            "height": 145,
            "props": {
              "label": "Viewport Sparkle Layer",
              "role": "window-vfx",
              "color": "#bfdbfe",
              "particleCount": 70,
              "particleSize": 3,
              "spread": 1.35,
              "motion": "starfall",
              "orbitRadius": 160,
              "verticalLift": 42,
              "zOffset": 12,
              "pulseDelay": 260
            }
          },
          {
            "id": "warp-core-hum",
            "type": "particle-emitter",
            "x": 5.0,
            "y": 8.15,
            "width": 360,
            "height": 118,
            "props": {
              "label": "Impulse Core Hum",
              "role": "ambient-engine",
              "color": "#a78bfa",
              "particleCount": 44,
              "particleSize": 4,
              "spread": 1.08,
              "motion": "nova-ring",
              "orbitRadius": 84,
              "verticalLift": 28,
              "zOffset": 46,
              "pulseDelay": 920
            }
          },
          {
            "id": "cabin-light-haze",
            "type": "particle-emitter",
            "x": 5.0,
            "y": 4.9,
            "width": 680,
            "height": 220,
            "props": {
              "label": "Cabin Light Haze",
              "role": "ambient-light",
              "color": "#93c5fd",
              "particleCount": 32,
              "particleSize": 5,
              "spread": 1.42,
              "motion": "spell-swirl",
              "orbitRadius": 210,
              "verticalLift": 60,
              "zOffset": 92,
              "pulseDelay": -400
            }
          },
          {
            "id": "viewport-starfield",
            "type": "shuttle3d-starfield",
            "x": 5.0,
            "y": 0.95,
            "width": 620,
            "height": 170,
            "props": {
              "label": "Stars Beyond Viewport",
              "role": "starfield",
              "color": "#dbeafe",
              "z": 132,
              "visibleThroughViewport": true,
              "twinkle": true,
              "distribution": "camera-centered-sphere",
              "sphereRadius": 124,
              "placeholderCount": 420,
              "seed": 73129,
              "fixedDistanceFromCamera": true
            }
          },
          {
            "id": "mother-ship",
            "type": "shuttle3d-mother-ship",
            "x": 5.55,
            "y": 1.25,
            "width": 310,
            "height": 96,
            "props": {
              "label": "Mother Ship",
              "role": "mothership",
              "color": "#cbd5e1",
              "z": 150,
              "visibleThroughViewport": true,
              "registry": "NCC-1701-inspired silhouette",
              "dockingDistance": "2.4 km"
            }
          },
          {
            "id": "lookaround-camera",
            "type": "shuttle3d-camera",
            "x": 5.0,
            "y": 6.8,
            "width": 0,
            "height": 0,
            "props": {
              "label": "First-person Camera",
              "role": "player-camera",
              "yaw": 0,
              "pitch": -2,
              "yawLimit": 180,
              "pitchLimit": 28,
              "instructions": "Drag/arrows to look. W/A/S/D moves, Shift sprints, click/Space/F fires the phaser, and R restarts after defeat."
            }
          },
          {
            "id": "alien-raider",
            "type": "shuttle3d-alien-ship",
            "x": 2.1,
            "y": 0.85,
            "width": 250,
            "height": 115,
            "props": {
              "label": "Alien Raider",
              "role": "hostile-ship",
              "color": "#a3e635",
              "accent": "#ef4444",
              "z": 149,
              "visibleThroughViewport": true,
              "registry": "unknown hostile vessel",
              "threat": "boarding transport"
            }
          },
          {
            "id": "player-phaser",
            "type": "shuttle3d-phaser",
            "x": 8.55,
            "y": 7.75,
            "width": 118,
            "height": 56,
            "props": {
              "label": "Type-II Phaser",
              "role": "player-weapon",
              "color": "#f59e0b",
              "damage": 34,
              "range": 28,
              "fireControls": [
                "pointer-click",
                "Space",
                "KeyF"
              ]
            }
          },
          {
            "id": "boarding-transporter",
            "type": "shuttle3d-transporter",
            "x": 5.0,
            "y": 5.2,
            "width": 180,
            "height": 180,
            "props": {
              "label": "Hostile Transport Signatures",
              "role": "enemy-spawner",
              "color": "#84cc16",
              "initialDelayMs": 2200,
              "intervalMs": 5000,
              "maxAlive": 4
            }
          },
          {
            "id": "player-health-hud",
            "type": "shuttle3d-health-hud",
            "x": 1.3,
            "y": 0.75,
            "width": 240,
            "height": 50,
            "props": {
              "label": "Player Health",
              "role": "health-hud",
              "maximum": 100,
              "starting": 100,
              "color": "#22c55e"
            }
          }
        ],
        "metadata": {
          "starter": true,
          "projection": "shuttle-3d",
          "tileWidth": 92,
          "tileHeight": 46,
          "originX": 480,
          "originY": 118,
          "particleOnly": false,
          "includesDefaultPlayer": true,
          "isometric": false,
          "rolloutPhase": "phase-5-shuttle-boarding-combat",
          "setting": "short federation-like shuttle craft interior under attack by alien boarders, with stars, the mother ship, and an alien raider visible through the forward viewport",
          "starterScene": "shuttlecraft-boarding-defense",
          "characterModel": "first-person-cadet-combat-presence",
          "meshActorsEnabled": false,
          "parentedParticles": true,
          "linkedSpellProjectiles": true,
          "linkedSensorPulses": true,
          "targetedParticles": true,
          "shuttleInterior": true,
          "choreography": {
            "title": "Shuttle Boarding Alert",
            "durationMs": 7600,
            "cameraPulse": true,
            "beats": [
              {
                "label": "Cabin lights",
                "timeMs": 0,
                "cue": "cabin-light-haze"
              },
              {
                "label": "Console boot",
                "timeMs": 900,
                "cue": "console-status-glow"
              },
              {
                "label": "Alien ship contact",
                "timeMs": 1800,
                "cue": "alien-raider"
              },
              {
                "label": "Transport signature",
                "timeMs": 3000,
                "cue": "boarding-transporter"
              },
              {
                "label": "Phaser ready",
                "timeMs": 4300,
                "cue": "player-phaser"
              },
              {
                "label": "Defend the shuttle",
                "timeMs": 6000,
                "cue": "lookaround-camera"
              }
            ]
          },
          "controls": {
            "mode": "first-person",
            "pointerDrag": true,
            "keyboard": "wasd-arrows-space-fire",
            "movement": "bounded-first-person-walk",
            "sprint": "shift",
            "fire": "click-space-or-f",
            "restart": "r"
          },
          "movementBounds": {
            "minX": 1.1,
            "maxX": 8.9,
            "minY": 3.0,
            "maxY": 8.4
          },
          "vfx": {
            "particleMultiplier": 2,
            "effectMultiplier": 1.5,
            "maxParticlesPerEmitter": 360
          },
          "quadrupleParticles": false,
          "uiParticleControls": true,
          "lookAroundEnabled": true,
          "viewportShowsStars": true,
          "viewportShowsMotherShip": true,
          "camera": {
            "mode": "first-person",
            "position": [
              0.0,
              0.75,
              2.45
            ],
            "yaw": 0,
            "pitch": -2,
            "yawLimit": 180,
            "pitchLimit": 28,
            "hint": "Drag or use arrow keys to look. Use W/A/S/D to walk, Shift to sprint, and click, Space, or F to fire the phaser."
          },
          "shuttle3d": {
            "mode": "webgl-vertex-mesh",
            "lookAround": true,
            "viewport": "forward-viewer",
            "starfield": "viewport-starfield",
            "motherShip": "mother-ship",
            "motherShipLabel": "Mother Ship",
            "playerAnchor": "hero-sprite",
            "controlsHint": "Click to focus • Drag/arrows look • W/A/S/D move • Shift sprint • Click/Space/F fire • R restart",
            "geometry": {
              "renderer": "raw-webgl",
              "primitive": "triangles",
              "boundsVertexCount": 12,
              "boundsVertices": [
                [
                  -4.5,
                  -1.45,
                  -7.2
                ],
                [
                  -4.5,
                  2.05,
                  -7.2
                ],
                [
                  -3.55,
                  3.15,
                  -7.2
                ],
                [
                  3.55,
                  3.15,
                  -7.2
                ],
                [
                  4.5,
                  2.05,
                  -7.2
                ],
                [
                  4.5,
                  -1.45,
                  -7.2
                ],
                [
                  -4.5,
                  -1.45,
                  4.8
                ],
                [
                  -4.5,
                  2.05,
                  4.8
                ],
                [
                  -3.55,
                  3.15,
                  4.8
                ],
                [
                  3.55,
                  3.15,
                  4.8
                ],
                [
                  4.5,
                  2.05,
                  4.8
                ],
                [
                  4.5,
                  -1.45,
                  4.8
                ]
              ],
              "viewportOpening": {
                "left": -2.92,
                "right": 2.92,
                "bottom": 0.0,
                "top": 2.32,
                "z": -7.2
              },
              "actualHullBounds": true,
              "cabinLength": 12.0
            },
            "movement": {
              "enabled": true,
              "scheme": "wasd",
              "walkSpeed": 2.65,
              "sprintMultiplier": 1.7,
              "radius": 0.28,
              "eyeHeight": 0.75,
              "start": [
                0.0,
                0.75,
                2.45
              ],
              "bounds": {
                "minX": -3.92,
                "maxX": 3.92,
                "minZ": -6.12,
                "maxZ": 3.72
              },
              "colliders": [
                {
                  "id": "helm-console",
                  "minX": -2.95,
                  "maxX": -0.4,
                  "minZ": -5.55,
                  "maxZ": -3.55
                },
                {
                  "id": "science-console",
                  "minX": 0.4,
                  "maxX": 2.95,
                  "minZ": -5.55,
                  "maxZ": -3.55
                },
                {
                  "id": "port-console",
                  "minX": -4.25,
                  "maxX": -3.45,
                  "minZ": -4.05,
                  "maxZ": -0.95
                },
                {
                  "id": "starboard-console",
                  "minX": 3.45,
                  "maxX": 4.25,
                  "minZ": -4.05,
                  "maxZ": -0.95
                },
                {
                  "id": "port-seat",
                  "minX": -2.15,
                  "maxX": -0.7,
                  "minZ": -3.0,
                  "maxZ": -1.05
                },
                {
                  "id": "starboard-seat",
                  "minX": 0.7,
                  "maxX": 2.15,
                  "minZ": -3.0,
                  "maxZ": -1.05
                },
                {
                  "id": "aft-hatch",
                  "minX": -1.45,
                  "maxX": 1.45,
                  "minZ": 3.55,
                  "maxZ": 4.45
                }
              ]
            },
            "starfieldSphere": {
              "mode": "camera-centered-sphere",
              "radius": 124,
              "count": 420,
              "seed": 73129,
              "minimumSize": 0.12,
              "maximumSize": 0.38,
              "fixedDistanceFromCamera": true
            },
            "alienShip": "alien-raider",
            "combat": {
              "enabled": true,
              "player": {
                "maxHealth": 100,
                "startingHealth": 100
              },
              "phaser": {
                "enabled": true,
                "damage": 34,
                "cooldownMs": 280,
                "range": 28,
                "beamDurationMs": 130
              },
              "alienShip": {
                "id": "alien-raider",
                "position": [
                  -6.4,
                  2.8,
                  -48.0
                ],
                "scale": [
                  3.8,
                  0.9,
                  2.5
                ]
              },
              "transport": {
                "initialDelayMs": 2200,
                "intervalMs": 5000,
                "beamDurationMs": 900,
                "maxAlive": 4,
                "spawnPoints": [
                  {
                    "id": "port-aft-pad",
                    "position": [
                      -2.9,
                      -0.55,
                      2.55
                    ]
                  },
                  {
                    "id": "starboard-aft-pad",
                    "position": [
                      2.9,
                      -0.55,
                      2.55
                    ]
                  },
                  {
                    "id": "center-pad",
                    "position": [
                      0.0,
                      -0.55,
                      0.3
                    ]
                  },
                  {
                    "id": "forward-pad",
                    "position": [
                      0.0,
                      -0.55,
                      -3.25
                    ]
                  }
                ]
              },
              "alien": {
                "maxHealth": 60,
                "speed": 1.05,
                "radius": 0.38,
                "attackRange": 1.05,
                "damage": 8,
                "attackCooldownMs": 850
              }
            }
          },
          "combatEnabled": true,
          "healthHud": true,
          "playerWeapon": "hand-phaser"
        }
      };
      if (sceneId && sceneId !== scene.id) return {...scene, id: sceneId, name: scene.name || "Shuttle Boarding Defense"};
      return scene;
    }

    function renderWebglSceneCandidate(candidate) {
      const surface = gameSurface || canvas;
      if (!surface) {
        if (glStatus) glStatus.textContent = "scene surface unavailable";
        return null;
      }
      const scene = candidate?.scene || fallbackWebglScene();
      ensureWebglSceneVfx(scene);
      bindWebglVfxControls();
      syncWebglVfxControls(scene);
      const projectId = String(candidate?.projectId || webglProjectState.projectId || "webgl-demo");
      const project = candidate?.project || webglProjectState.project;
      const selectedObjectId = String(candidate?.selectedObjectId || "");
      ensureWebglStrategicSession(projectId, project);
      ensureWebglSystemScenarioRuntime(projectId, project);
      gameSurfaceRuntime = window.MainComputerSceneViewer?.renderSceneSurface?.(surface, scene, {
        mode: "game-surface",
        label: `Vertex-built shuttle boarding-defense surface: ${scene.name || scene.id}`,
        projectId,
        project,
        spaceNavigation: candidate?.project?.metadata?.spaceNavigation || webglProjectState.project?.metadata?.spaceNavigation || null,
        selectedObjectId,
        onNavigationChanged: syncWebglStrategicNavigation,
        assets: Array.isArray(candidate?.assets) ? candidate.assets : [],
        showLabels: true,
        onPolygonAnnotationSave: (detail = {}) => queueWebglPolygonAnnotationSave({
          ...detail,
          projectId,
          sceneId: String(detail?.sceneId || scene?.id || "")
        }),
        enableClickMovement: true,
        movementObjectId: "hero-sprite",
        onSceneMovement(detail) {
          const movedScene = detail?.scene || scene;
          if (detail?.phase === "finish" && window.MainComputerSceneStore?.saveScene) {
            window.MainComputerSceneStore.saveScene(movedScene, {source: "webgl-demo", notify: false});
            window.MainComputerSceneStore.setSelectedSceneId?.(String(movedScene.id || "default-empty-scene"), {source: "webgl-demo", notify: false});
          }
          if (glStatus) {
            const actorLabel = String(detail?.actor?.props?.label || "Player Cadet");
            const x = Number(detail?.targetX ?? detail?.worldX ?? 0).toFixed(1);
            const y = Number(detail?.targetY ?? detail?.worldY ?? 0).toFixed(1);
            const action = detail?.phase === "finish" ? "arrived at" : "moving steadily toward";
            glStatus.textContent = `${actorLabel} ${action} tile ${x}, ${y}`;
          }
        }
      }) || {scene};
      installSelectedWebglJsGameplayPack(gameSurfaceRuntime, project).then((result) => {
        const recorded = webglRecordJsGameplayPackInstallResult(result);
        if (!glStatus || !recorded) return;
        glStatus.dataset.jsGameplayPack = recorded.installed
          ? "installed"
          : recorded.mode === "none"
            ? "none"
            : "unavailable";
        glStatus.dataset.jsGameplayPackId = String(recorded.packId || "");
        glStatus.dataset.jsGameplayPackCommandsApplied = String(recorded.commandsApplied || 0);
      }).catch((error) => {
        const message = error instanceof Error ? error.message : String(error || "unknown");
        webglRecordJsGameplayPackInstallResult({
          schema: "game.webglJsGameplayPackInstallResult.v1",
          kind: "webgl-js-gameplay-pack-install-result",
          mode: "error",
          installed: false,
          error: message
        });
        if (!glStatus) return;
        glStatus.dataset.jsGameplayPack = "error";
        glStatus.dataset.jsGameplayPackError = message;
      });
      window.MainComputerPaxScenarioInteraction?.setCharacterRuntime?.(
        window.MainComputerCharacterAIRuntime?.current?.() || null
      );
      if (window.MainComputerSceneStore?.saveScene) {
        window.MainComputerSceneStore.saveScene(scene, {source: "webgl-demo", notify: false});
        window.MainComputerSceneStore.setSelectedSceneId?.(String(scene.id || "default-empty-scene"), {source: "webgl-demo", notify: false});
      }
      const count = Array.isArray(scene.objects) ? scene.objects.length : 0;
      const dirtySuffix = candidate?.dirty ? " · unsaved editor changes" : "";
      const source = candidate?.source === "game-editor"
        ? "Game Editor"
        : candidate?.source === "project"
          ? "project.json"
          : candidate?.source === "scene-store"
            ? "local scene store"
            : "fallback scene";
      if (glStatus) {
        const strategicSuffix = webglProjectState.strategicSession
          ? ` • strategic revision ${webglProjectState.strategicSession.summary().canonicalRevision}`
          : webglProjectState.strategicSessionError
            ? ` • strategic unavailable: ${webglProjectState.strategicSessionError}`
            : "";
        const scenarioSuffix = webglProjectState.systemScenarioRuntime
          ? ` • scenario ${webglProjectState.systemScenarioRuntime.activeScenarioContext().stageId || "standby"}`
          : webglProjectState.systemScenarioError
            ? ` • scenario unavailable: ${webglProjectState.systemScenarioError}`
            : "";
        glStatus.textContent = `${projectId} / ${scene.name || scene.id} mirrored from ${source} (${count} ${count === 1 ? "entity" : "entities"})${dirtySuffix}${strategicSuffix}${scenarioSuffix}`;
      }
      return gameSurfaceRuntime;
    }

    async function loadWebglProject(projectId = webglProjectState.projectId, {force = false} = {}) {
      const cleanProjectId = String(projectId || "webgl-demo");
      if (webglProjectState.loading && !force && cleanProjectId === webglProjectState.projectId) {
        return webglProjectState.loading;
      }
      webglProjectState.projectId = cleanProjectId;
      webglProjectState.loading = (async () => {
        const projectData = await webglPost("/api/applications/game-editor/project/read", {project_id: cleanProjectId});
        webglProjectState.projectId = String(projectData.project_id || cleanProjectId);
        webglProjectState.project = projectData.project;
        webglProjectState.contentHash = String(projectData.content_hash || "");
        ensureWebglStrategicSession(
          webglProjectState.projectId,
          webglProjectState.project
        );
        syncWebglGameplayPackSelector(webglProjectState.project);
        const assetData = await webglPost("/api/applications/game-editor/assets", {project_id: webglProjectState.projectId});
        webglProjectState.assets = Array.isArray(assetData.assets) ? assetData.assets : [];
        return webglProjectState;
      })();
      try {
        return await webglProjectState.loading;
      } finally {
        webglProjectState.loading = null;
      }
    }

    function bestKnownWebglScene(sceneId = "") {
      return webglEditorSceneCandidate(sceneId)
        || webglProjectSceneCandidate(sceneId)
        || webglStoredSceneCandidate(sceneId)
        || {
          source: "fallback",
          projectId: webglProjectState.projectId,
          project: null,
          scene: fallbackWebglScene(sceneId || "default-empty-scene"),
          assets: [],
          dirty: false,
          selectedObjectId: ""
        };
    }

    async function initWebgl(sceneId) {
      bindWebglVfxControls();
      bindWebglGameplayPackSelector();
      webglSetGameplayPackLobbyVisible(!webglProjectState.gameStarted);
      pauseGameSurface();
      const surface = gameSurface || canvas;
      if (!surface) {
        if (glStatus) glStatus.textContent = "scene surface unavailable";
        return;
      }
      if (!webglProjectState.gameStarted) {
        webglProjectState.pendingSceneId = String(sceneId || "");
        if (glStatus) glStatus.textContent = "Gameplay pack lobby ready — choose packs and press START NEW GAME";
        try {
          await loadWebglProject(webglProjectState.projectId || "webgl-demo");
        } catch {
          syncWebglGameplayPackSelector(webglProjectState.project);
        }
        return;
      }
      webglSetGameplayPackLobbyVisible(false);
      running = true;
      const liveCandidate = webglEditorSceneCandidate(sceneId);
      if (liveCandidate) {
        webglProjectState.projectId = liveCandidate.projectId;
        webglProjectState.project = liveCandidate.project;
        webglProjectState.assets = liveCandidate.assets;
        ensureWebglStrategicSession(
          webglProjectState.projectId,
          webglProjectState.project
        );
        syncWebglGameplayPackSelector(webglProjectState.project);
        renderWebglSceneCandidate(liveCandidate);
        return;
      }

      const storedCandidate = webglStoredSceneCandidate(sceneId);
      if (storedCandidate) {
        renderWebglSceneCandidate(storedCandidate);
      } else if (glStatus) {
        glStatus.textContent = "loading game editor project scene";
      }

      try {
        await loadWebglProject(webglProjectState.projectId || "webgl-demo");
        renderWebglSceneCandidate(webglProjectSceneCandidate(sceneId) || bestKnownWebglScene(sceneId));
      } catch (error) {
        if (!storedCandidate) renderWebglSceneCandidate(bestKnownWebglScene(sceneId));
        if (glStatus) {
          const message = error instanceof Error ? error.message : String(error || "unknown error");
          glStatus.textContent = `project scene unavailable; showing local scene (${message})`;
        }
      }
    }

    function handleWebglSceneMirrorEvent(event) {
      const detail = event?.detail || {};
      const scene = detail.scene || (detail.sceneId ? window.MainComputerSceneStore?.getScene?.(detail.sceneId) : null);
      if (!scene) return;
      const projectId = String(detail.projectId || webglProjectState.projectId || "webgl-demo");
      webglProjectState.projectId = projectId;
      if (detail.project) webglProjectState.project = detail.project;
      if (Array.isArray(detail.assets)) webglProjectState.assets = detail.assets;
      if (webglProjectState.project) {
        ensureWebglStrategicSession(
          webglProjectState.projectId,
          webglProjectState.project
        );
      }
      if (currentApp === "webgl") {
        if (!webglProjectState.gameStarted) {
          webglProjectState.pendingSceneId = String(scene?.id || webglProjectState.pendingSceneId || "");
          syncWebglGameplayPackSelector(webglProjectState.project);
          if (glStatus) glStatus.textContent = "Gameplay pack lobby ready — press START NEW GAME to launch";
          return;
        }
        renderWebglSceneCandidate({
          source: detail.source === "scene-store" ? "scene-store" : "game-editor",
          projectId,
          project: detail.project || webglProjectState.project,
          scene,
          assets: Array.isArray(detail.assets) ? detail.assets : webglProjectState.assets,
          dirty: Boolean(detail.dirty),
          selectedObjectId: String(detail.selectedObjectId || "")
        });
      }
    }

    window.MainComputerWebglStrategicSession = {
      ensure: ensureWebglStrategicSession,
      current() {
        return webglProjectState.strategicSession;
      },
      projectState: webglProjectState
    };

    window.MainComputerWebglSystemScenario = {
      ensure: ensureWebglSystemScenarioRuntime,
      current() {
        return webglProjectState.systemScenarioRuntime;
      },
      reloadGameplayPackSelection: webglReloadGameplayPackSelection,
      availableGameplayPacks: webglAvailableGameplayPacks,
      syncGameplayPackSelector: syncWebglGameplayPackSelector,
      syncGameplayPackControls: syncWebglGameplayPackControls,
      applyGameplayPackSelection: applyWebglGameplayPackSelection,
      applyGameplayPackControlSelection: applyWebglGameplayPackControlSelection,
      startGameplayPackLobby: startWebglGameFromGameplayPackLobby,
      selectedGameplayPackIdsFromControls: webglSelectedGameplayPackIdsFromControls,
      storeGameplayPackSelection: webglStoreGameplayPackSelection,
      defaultGameplayPackIds: webglDefaultGameplayPackIds,
      selectedJsGameplayPackId: webglSelectedJsGameplayPackId,
      selectedJsGameplayPackIds: webglSelectedJsGameplayPackIds,
      loadJsGameplayPackSource: webglLoadJsGameplayPackSource,
      installSelectedJsGameplayPack: installSelectedWebglJsGameplayPack,
      recordJsGameplayPackInstallResult: webglRecordJsGameplayPackInstallResult,
      gameplayPackInstallStatusMessage: webglGameplayPackInstallStatusMessage,
      projectState: webglProjectState
    };

    window.addEventListener("main-computer-game-editor-scene-change", handleWebglSceneMirrorEvent);
    window.addEventListener("main-computer-scene-change", handleWebglSceneMirrorEvent);
    window.addEventListener("storage", (event) => {
      if (event.key !== window.MainComputerSceneStore?.sceneStorageKey) return;
      if (currentApp !== "webgl") return;
      if (!webglProjectState.gameStarted) {
        syncWebglGameplayPackSelector(webglProjectState.project);
        if (glStatus) glStatus.textContent = "Gameplay pack lobby ready — press START NEW GAME to launch";
        return;
      }
      renderWebglSceneCandidate(webglStoredSceneCandidate() || bestKnownWebglScene());
    });

    function draw() {
      return;
    }

    function isPlainPrimaryAppClick(event) {
      return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    }

    function focusActiveWorkspaceAfterPointerAppSelection(button, event) {
      const launcher = button?.closest?.(".launcher");
      if (!launcher || event.detail === 0) return;
      const activeElement = document.activeElement;
      if (!activeElement || !launcher.contains(activeElement)) return;
      const workspace = document.querySelector("[data-mc-component-id='applications.workspace']");
      if (workspace instanceof HTMLElement) {
        workspace.focus({preventScroll: true});
      }
      if (launcher.contains(document.activeElement)) {
        activeElement.blur?.();
      }
    }

    ensureDesktopIcons();
    document.querySelectorAll("[data-app]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (button instanceof HTMLAnchorElement) {
          if (!isPlainPrimaryAppClick(event)) return;
          event.preventDefault();
        }
        setActiveApp(button.dataset.app);
        focusActiveWorkspaceAfterPointerAppSelection(button, event);
      });
    });
