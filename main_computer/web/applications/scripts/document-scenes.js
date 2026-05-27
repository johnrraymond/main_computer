    (function () {
      const gameSceneProjectCache = new Map();
      const gameSceneChangeEvents = [
        "main-computer-game-editor-scene-change",
        "main-computer-scene-change",
        "main-computer-selected-scene-change"
      ];

      function sceneEmbedId(prefix = "scene-embed") {
        if (window.crypto?.randomUUID) return `${prefix}-${window.crypto.randomUUID()}`;
        return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      }

      function escapeDocumentSceneText(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function normalizeSceneEmbedMode(mode) {
        return mode === "snapshot" ? "snapshot" : "live";
      }

      function normalizeGameSceneProjectId(projectId = "") {
        const clean = String(projectId || "webgl-demo").replace(/\\/g, "/").split("/").filter(Boolean).join("-");
        return clean.replace(/[^a-zA-Z0-9_.-]/g, "-") || "webgl-demo";
      }

      function normalizeGameSceneId(sceneId = "", fallback = "default-empty-scene") {
        const clean = String(sceneId || fallback || "default-empty-scene").trim();
        return clean || "default-empty-scene";
      }

      function selectedSceneIdForEmbed(sceneId = "") {
        return normalizeGameSceneId(sceneId, window.MainComputerSceneStore?.selectedSceneId?.() || window.MainComputerSceneStore?.defaultSceneId || "default-empty-scene");
      }

      function defaultGameSceneMountConfig() {
        const liveProjectId = window.gameEditorState?.projectId || window.MainComputerGameEditorContext?.snapshot?.()?.project_id || "webgl-demo";
        const liveSceneId = window.gameEditorState?.selectedSceneId || window.MainComputerGameEditorContext?.snapshot?.()?.active_scene_id || window.MainComputerSceneStore?.selectedSceneId?.() || "default-empty-scene";
        return {
          projectId: normalizeGameSceneProjectId(liveProjectId),
          sceneId: normalizeGameSceneId(liveSceneId),
          mode: "live"
        };
      }

      function gameSceneMountConfig(element) {
        const defaults = defaultGameSceneMountConfig();
        return {
          projectId: normalizeGameSceneProjectId(element?.dataset?.projectId || defaults.projectId),
          sceneId: normalizeGameSceneId(element?.dataset?.sceneId || defaults.sceneId),
          mode: normalizeSceneEmbedMode(element?.dataset?.sceneEmbedMode || element?.dataset?.gameSceneMode || defaults.mode)
        };
      }

      function hydrateSceneEmbed(element) {
        if (!element) return;
        const sceneId = selectedSceneIdForEmbed(element.dataset.sceneId);
        const mode = normalizeSceneEmbedMode(element.dataset.sceneEmbedMode);
        element.dataset.docObject = "scene-embed";
        element.dataset.docObjectId = element.dataset.docObjectId || sceneEmbedId();
        element.dataset.docObjectLayout = "paragraph";
        element.dataset.sceneEmbed = "true";
        element.dataset.sceneId = sceneId;
        element.dataset.sceneEmbedMode = mode;
        element.contentEditable = "false";
        element.draggable = true;
        element.className = "document-object document-scene-embed scene-surface";
        element.setAttribute("role", "group");
        element.setAttribute("tabindex", "0");
        element.setAttribute("aria-label", `Embedded scene ${sceneId}`);
        window.MainComputerSceneViewer?.renderSceneSurface?.(element, sceneId, {
          embedded: true,
          mode: "document-embed",
          label: `Embedded scene ${sceneId}`,
          showLabels: false
        });
      }

      function serializeSceneEmbed(element) {
        const sceneId = selectedSceneIdForEmbed(element.dataset.sceneId);
        const mode = normalizeSceneEmbedMode(element.dataset.sceneEmbedMode);
        element.replaceChildren();
        element.dataset.docObject = "scene-embed";
        element.dataset.docObjectId = element.dataset.docObjectId || sceneEmbedId();
        element.dataset.docObjectLayout = "paragraph";
        element.dataset.sceneEmbed = "true";
        element.dataset.sceneId = sceneId;
        element.dataset.sceneEmbedMode = mode;
        element.contentEditable = "false";
        element.draggable = true;
        element.className = "document-object document-scene-embed scene-surface";
        element.removeAttribute("role");
        element.removeAttribute("tabindex");
        element.removeAttribute("aria-label");
        element.removeAttribute("title");
      }

      function createSceneEmbed(sceneId = "") {
        const element = document.createElement("figure");
        element.dataset.sceneId = selectedSceneIdForEmbed(sceneId);
        hydrateSceneEmbed(element);
        return element;
      }

      function insertDocumentBlockObject(element) {
        const selection = window.getSelection?.();
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        const editor = getActiveDocumentEditor?.();
        if (!range || !editor || !editor.contains(range.startContainer)) {
          documentEditor?.append(element);
        } else {
          if (!range.collapsed) range.deleteContents();
          const block = documentBlockForRange?.(range, editor);
          if (block?.parentNode === editor) {
            block.after(element);
          } else {
            range.insertNode(element);
          }
        }
        const paragraph = document.createElement("p");
        paragraph.innerHTML = "<br>";
        element.after(paragraph);
        hydrateDocumentObjects?.(documentCanvas);
        saveDocumentDraft?.();
        scheduleDocumentRepagination?.();
        return element;
      }

      function insertDocumentSceneEmbed(sceneId = "") {
        const element = createSceneEmbed(sceneId);
        insertDocumentBlockObject(element);
        if (documentStatus) documentStatus.textContent = "scene embed inserted";
        return element;
      }

      function promptAndInsertDocumentScene() {
        const scenes = window.MainComputerSceneStore?.listScenes?.() || [];
        const defaultId = window.MainComputerSceneStore?.selectedSceneId?.() || scenes[0]?.id || "default-empty-scene";
        const requested = window.prompt?.("Scene ID to embed", defaultId);
        if (requested === null) return null;
        const sceneId = selectedSceneIdForEmbed(requested);
        return insertDocumentSceneEmbed(sceneId);
      }

      function setGameScenePluginDataset(element, config) {
        element.dataset.docObject = "game-scene-plugin";
        element.dataset.docObjectId = element.dataset.docObjectId || sceneEmbedId("game-scene-plugin");
        element.dataset.docObjectLayout = "paragraph";
        element.dataset.docPlugin = "game-scene";
        element.dataset.documentPluginMount = "game-scene";
        element.dataset.gameScenePlugin = "true";
        element.dataset.projectId = normalizeGameSceneProjectId(config.projectId);
        element.dataset.sceneId = normalizeGameSceneId(config.sceneId);
        element.dataset.sceneEmbedMode = normalizeSceneEmbedMode(config.mode);
        element.dataset.gameSceneMode = normalizeSceneEmbedMode(config.mode);
        element.contentEditable = "false";
        element.draggable = true;
      }

      function renderGameScenePluginFrame(element, config, statusText = "loading game scene...") {
        const projectId = normalizeGameSceneProjectId(config.projectId);
        const sceneId = normalizeGameSceneId(config.sceneId);
        element.className = "document-object document-game-scene-plugin";
        element.setAttribute("role", "group");
        element.setAttribute("tabindex", "0");
        element.setAttribute("aria-label", `Game scene plugin ${projectId}/${sceneId}`);
        element.title = "Mounted Game Scene plugin. Double-click to change project or scene.";
        element.replaceChildren();

        const header = document.createElement("figcaption");
        header.className = "document-game-scene-plugin-head";
        header.innerHTML = `
          <div>
            <strong>Game Scene Plugin</strong>
            <span data-game-scene-plugin-title>${escapeDocumentSceneText(projectId)} / ${escapeDocumentSceneText(sceneId)}</span>
          </div>
        `;

        const actions = document.createElement("div");
        actions.className = "document-game-scene-plugin-actions";
        const refresh = document.createElement("button");
        refresh.type = "button";
        refresh.textContent = "Refresh";
        refresh.dataset.documentGameSceneRefresh = "true";
        refresh.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          hydrateGameScenePlugin(element, {force: true});
        });
        const openEditor = document.createElement("button");
        openEditor.type = "button";
        openEditor.textContent = "Open Game Editor";
        openEditor.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          const url = new URL(window.location.href);
          url.pathname = "/applications/game-editor";
          url.searchParams.set("project", projectId);
          window.location.href = url.toString();
        });
        actions.append(refresh, openEditor);
        header.append(actions);

        const surface = document.createElement("div");
        surface.className = "document-game-scene-plugin-surface scene-surface";
        surface.dataset.gameScenePluginSurface = "true";
        surface.dataset.sceneId = sceneId;
        surface.dataset.projectId = projectId;
        surface.setAttribute("aria-label", `Mounted game scene surface ${projectId}/${sceneId}`);

        const status = document.createElement("div");
        status.className = "document-game-scene-plugin-status";
        status.dataset.gameScenePluginStatus = "true";
        status.textContent = statusText;

        element.append(header, surface, status);
        return {surface, status};
      }

      function serializeGameScenePlugin(element) {
        const config = gameSceneMountConfig(element);
        element.replaceChildren();
        setGameScenePluginDataset(element, config);
        element.className = "document-object document-game-scene-plugin";
        element.removeAttribute("role");
        element.removeAttribute("tabindex");
        element.removeAttribute("aria-label");
        element.removeAttribute("title");
      }

      function resolveLiveGameEditorScene(projectId, sceneId) {
        const state = window.gameEditorState;
        if (!state?.project || normalizeGameSceneProjectId(state.projectId) !== normalizeGameSceneProjectId(projectId)) return null;
        const scenes = Array.isArray(state.project.scenes) ? state.project.scenes : [];
        const scene = scenes.find((candidate) => String(candidate?.id || "") === String(sceneId)) || scenes[0] || null;
        if (!scene) return null;
        return window.MainComputerSceneStore?.normalizeScene?.(JSON.parse(JSON.stringify(scene)), scene.id) || JSON.parse(JSON.stringify(scene));
      }

      async function fetchGameProject(projectId, {force = false} = {}) {
        const cleanProjectId = normalizeGameSceneProjectId(projectId);
        const cached = gameSceneProjectCache.get(cleanProjectId);
        if (cached && !force) return cached;
        const request = fetch("/api/applications/game-editor/project/read", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({project_id: cleanProjectId})
        }).then(async (response) => {
          const data = await response.json().catch(() => ({}));
          if (!response.ok || !data?.ok) throw new Error(data?.error || `game project read failed (${response.status})`);
          return data;
        });
        gameSceneProjectCache.set(cleanProjectId, request);
        try {
          return await request;
        } catch (error) {
          gameSceneProjectCache.delete(cleanProjectId);
          throw error;
        }
      }

      function sceneFromGameProject(project, sceneId) {
        const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
        const requested = normalizeGameSceneId(sceneId, project?.activeSceneId || scenes[0]?.id);
        const scene = scenes.find((candidate) => String(candidate?.id || "") === requested)
          || scenes.find((candidate) => String(candidate?.id || "") === String(project?.activeSceneId || ""))
          || scenes[0]
          || null;
        if (!scene) return null;
        return window.MainComputerSceneStore?.normalizeScene?.(JSON.parse(JSON.stringify(scene)), scene.id) || JSON.parse(JSON.stringify(scene));
      }

      function renderGameScenePluginSurface(element, scene, projectId, source = "project") {
        const surface = element.querySelector("[data-game-scene-plugin-surface]");
        const status = element.querySelector("[data-game-scene-plugin-status]");
        if (!surface || !scene) return;
        const runtime = window.MainComputerSceneViewer?.renderSceneSurface?.(surface, scene, {
          embedded: true,
          mode: "document-game-scene-plugin",
          label: `Mounted game scene: ${scene.name || scene.id}`,
          projectId,
          showLabels: false,
          enableClickMovement: false
        });
        element.__mainComputerGameSceneRuntime = runtime || null;
        element.dataset.sceneId = String(scene.id || element.dataset.sceneId || "default-empty-scene");
        surface.dataset.sceneId = String(scene.id || "");
        surface.dataset.sceneName = String(scene.name || "");
        if (status) {
          const objectCount = Array.isArray(scene.objects) ? scene.objects.length : 0;
          status.textContent = `${source === "live-editor" ? "live editor scene" : "repo scene"} mounted · ${objectCount} ${objectCount === 1 ? "entity" : "entities"}`;
        }
        const title = element.querySelector("[data-game-scene-plugin-title]");
        if (title) title.textContent = `${projectId} / ${scene.id || "scene"}`;
      }

      function hydrateGameScenePlugin(element, options = {}) {
        if (!element) return;
        const config = gameSceneMountConfig(element);
        setGameScenePluginDataset(element, config);
        const {status} = renderGameScenePluginFrame(element, config, "loading game scene plugin...");
        if (element.__mainComputerGameSceneRuntime?.dispose) element.__mainComputerGameSceneRuntime.dispose();
        const liveScene = resolveLiveGameEditorScene(config.projectId, config.sceneId);
        if (liveScene && !options.force) {
          renderGameScenePluginSurface(element, liveScene, config.projectId, "live-editor");
          return;
        }
        const token = `${Date.now()}:${Math.random()}`;
        element.__mainComputerGameSceneToken = token;
        fetchGameProject(config.projectId, {force: Boolean(options.force)})
          .then((data) => {
            if (element.__mainComputerGameSceneToken !== token) return;
            const scene = sceneFromGameProject(data.project, config.sceneId);
            if (!scene) throw new Error("project has no scenes");
            renderGameScenePluginSurface(element, scene, config.projectId, "project");
          })
          .catch((error) => {
            if (element.__mainComputerGameSceneToken !== token) return;
            if (status) status.textContent = `game scene mount failed: ${error?.message || error}`;
            element.dataset.sceneState = "mount-error";
          });
      }

      function editGameScenePlugin(element) {
        if (!element) return;
        const config = gameSceneMountConfig(element);
        const projectId = window.prompt?.("Game project ID", config.projectId);
        if (projectId === null) return;
        const sceneId = window.prompt?.("Scene ID", config.sceneId);
        if (sceneId === null) return;
        setGameScenePluginDataset(element, {
          projectId: normalizeGameSceneProjectId(projectId),
          sceneId: normalizeGameSceneId(sceneId),
          mode: config.mode
        });
        hydrateGameScenePlugin(element, {force: true});
        saveDocumentDraft?.();
        scheduleDocumentRepagination?.();
        if (documentStatus) documentStatus.textContent = "game scene plugin updated";
      }

      function createGameScenePlugin(projectId = "", sceneId = "") {
        const element = document.createElement("figure");
        const defaults = defaultGameSceneMountConfig();
        setGameScenePluginDataset(element, {
          projectId: projectId || defaults.projectId,
          sceneId: sceneId || defaults.sceneId,
          mode: "live"
        });
        hydrateGameScenePlugin(element);
        return element;
      }

      function insertDocumentGameScenePlugin(projectId = "", sceneId = "") {
        const element = createGameScenePlugin(projectId, sceneId);
        insertDocumentBlockObject(element);
        if (documentStatus) documentStatus.textContent = "game scene plugin mounted";
        return element;
      }

      function promptAndInsertDocumentGameScenePlugin() {
        const defaults = defaultGameSceneMountConfig();
        const projectId = window.prompt?.("Game project ID to mount", defaults.projectId);
        if (projectId === null) return null;
        const sceneId = window.prompt?.("Scene ID to mount", defaults.sceneId);
        if (sceneId === null) return null;
        return insertDocumentGameScenePlugin(projectId, sceneId);
      }

      function refreshDocumentGameScenePluginMounts(detail = {}, {force = false} = {}) {
        const projectId = normalizeGameSceneProjectId(detail.projectId || detail.project_id || window.gameEditorState?.projectId || "");
        const sceneId = String(detail.sceneId || detail.scene_id || "");
        documentCanvas?.querySelectorAll?.("[data-doc-object='game-scene-plugin'], [data-game-scene-plugin='true']").forEach((element) => {
          const config = gameSceneMountConfig(element);
          if (projectId && config.projectId !== projectId) return;
          if (sceneId && config.sceneId !== sceneId) return;
          if (detail.scene && !force) {
            renderGameScenePluginFrame(element, config, "refreshing live game scene...");
            const scene = window.MainComputerSceneStore?.normalizeScene?.(JSON.parse(JSON.stringify(detail.scene)), detail.scene.id) || JSON.parse(JSON.stringify(detail.scene));
            renderGameScenePluginSurface(element, scene, config.projectId, "live-editor");
          } else {
            hydrateGameScenePlugin(element, {force});
          }
        });
      }

      function bindGameScenePluginEvents() {
        gameSceneChangeEvents.forEach((eventName) => {
          window.addEventListener(eventName, (event) => {
            refreshDocumentGameScenePluginMounts(event?.detail || {});
          });
        });
      }

      documentObjectRuntime?.registerObjectType?.("scene-embed", {
        label: "Scene Embed",
        layout: ["paragraph"],
        capabilities: ["render:block", "scene:live-reference"],
        hydrate: hydrateSceneEmbed,
        serialize: serializeSceneEmbed
      });

      documentObjectRuntime?.registerObjectType?.("game-scene-plugin", {
        label: "Game Scene Plugin",
        layout: ["paragraph"],
        capabilities: ["plugin:mount", "scene:game-project", "render:block"],
        hydrate: hydrateGameScenePlugin,
        serialize: serializeGameScenePlugin,
        edit: editGameScenePlugin
      });

      bindGameScenePluginEvents();

      window.createDocumentSceneEmbed = createSceneEmbed;
      window.insertDocumentSceneEmbed = insertDocumentSceneEmbed;
      window.promptAndInsertDocumentScene = promptAndInsertDocumentScene;
      window.createDocumentGameScenePlugin = createGameScenePlugin;
      window.insertDocumentGameScenePlugin = insertDocumentGameScenePlugin;
      window.promptAndInsertDocumentGameScenePlugin = promptAndInsertDocumentGameScenePlugin;
      window.refreshDocumentGameScenePluginMounts = refreshDocumentGameScenePluginMounts;
    })();
