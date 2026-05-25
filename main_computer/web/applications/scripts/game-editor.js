    const gameEditorApi = {
      listScenes: () => window.MainComputerSceneStore?.listScenes?.() || [],
      getScene: (sceneId) => window.MainComputerSceneStore?.getScene?.(sceneId),
      saveScene: (scene) => window.MainComputerSceneStore?.saveScene?.(scene),
      createScene: (name) => window.MainComputerSceneStore?.createScene?.(name)
    };

    const gameEditorState = {
      initialized: false,
      loading: false,
      projectId: "webgl-demo",
      selectedSceneId: "default-empty-scene",
      selectedObjectId: "",
      project: null,
      projects: [],
      assets: [],
      contentHash: "",
      runtime: null,
      chatController: null,
      chatOpen: false,
      dirty: false,
      nodes: {}
    };

    const gameEditorLinkedChatThreads = new Map();

    window.gameEditorApi = gameEditorApi;
    window.gameEditorState = gameEditorState;

    function buildGameEditorShell() {
      if (!gameEditorApp) return;
      gameEditorApp.dataset.sceneBuilder = "project-backed";
      gameEditorApp.dataset.selectedSceneId = gameEditorState.selectedSceneId;
      gameEditorApp.setAttribute("aria-label", "Project-backed Game Editor");
      gameEditorApp.innerHTML = `
        <section class="game-editor-shell" aria-label="Game Editor project workspace">
          <aside class="game-editor-sidebar" aria-label="Game projects">
            <div class="game-editor-section-head">
              <div>
                <h3>Projects</h3>
                <p>Repo-backed scene projects</p>
              </div>
              <button type="button" id="game-editor-refresh-projects">Refresh</button>
            </div>
            <div class="game-editor-project-list" id="game-editor-project-list" role="list" aria-label="Game projects"></div>
            <div class="game-editor-assets">
              <div class="game-editor-section-head">
                <div>
                  <h3>Assets</h3>
                  <p>Upload and assign project files</p>
                </div>
              </div>
              <input type="file" id="game-editor-asset-upload" aria-label="Choose asset to upload">
              <button type="button" id="game-editor-upload-asset">Upload Asset</button>
              <div class="game-editor-asset-list" id="game-editor-asset-list" aria-label="Project assets"></div>
            </div>
          </aside>

          <section class="game-editor-main" aria-label="Scene editor">
            <div class="game-editor-toolbar">
              <label>
                <span>Project name</span>
                <input id="game-editor-project-name" autocomplete="off">
              </label>
              <div class="game-editor-actions">
                <button type="button" id="game-editor-save-project">Save Project</button>
                <button type="button" id="game-editor-reset-project">Reset</button>
                <button
                  type="button"
                  id="game-editor-chat-toggle"
                  class="game-editor-chat-toggle"
                  aria-haspopup="dialog"
                  aria-expanded="false"
                  aria-controls="game-editor-chat-popout">
                  Game Chat
                </button>
              </div>
            </div>

            <div class="game-editor-workbench">
              <section class="game-editor-preview-card" id="game-editor-preview" aria-label="Game editor preview">
                <div class="game-editor-preview-head">
                  <div>
                    <h3>Live Surface</h3>
                    <p id="game-editor-webgl-status">webgl preview booting</p>
                  </div>
                  <button type="button" id="game-editor-frame-selected">Frame Selected</button>
                </div>
                <div id="game-editor-webgl-viewport" class="game-editor-webgl-viewport" aria-label="Game editor WebGL viewport">
                  <div id="game-editor-webgl-canvas" class="scene-surface game-editor-webgl-canvas" aria-label="Game editor scene surface"></div>
                </div>
              </section>

              <aside class="game-editor-inspector" aria-label="Entity inspector">
                <div class="game-editor-vfx-controls" aria-label="Scene VFX controls">
                  <div>
                    <h3>VFX Density</h3>
                    <p>Quickly scale particle counts and glow strength for the current scene.</p>
                  </div>
                  <label>
                    <span>Particles <output id="game-editor-particle-density-value">2x</output></span>
                    <input id="game-editor-particle-density" type="range" min="1" max="4" step="0.25" value="2" aria-label="Particle density multiplier">
                  </label>
                  <label>
                    <span>Effects <output id="game-editor-effect-intensity-value">2x</output></span>
                    <input id="game-editor-effect-intensity" type="range" min="1" max="4" step="0.25" value="2" aria-label="Effect intensity multiplier">
                  </label>
                  <div class="game-editor-vfx-presets" aria-label="VFX presets">
                    <button type="button" data-vfx-preset="1">1x</button>
                    <button type="button" data-vfx-preset="2">2x</button>
                    <button type="button" data-vfx-preset="4">4x</button>
                  </div>
                </div>
                <div class="game-editor-section-head">
                  <div>
                    <h3>Entities</h3>
                    <p>Scene objects in the selected project</p>
                  </div>
                </div>
                <div id="game-editor-entity-list" class="game-editor-entity-list" aria-label="Scene entities"></div>
                <label>
                  <span>Name</span>
                  <input id="game-editor-entity-name" autocomplete="off">
                </label>
                <label>
                  <span>X position</span>
                  <input id="game-editor-entity-x" type="number" step="1">
                </label>
                <label>
                  <span>Color</span>
                  <input id="game-editor-entity-color" type="color">
                </label>
                <label>
                  <span>Asset</span>
                  <select id="game-editor-entity-asset"></select>
                </label>
              </aside>
            </div>

            <output id="game-editor-status" class="game-editor-status" role="status" aria-live="polite">Project-backed scene editor is ready.</output>
          </section>

          <section
            id="game-editor-chat-popout"
            class="game-editor-chat-popout"
            role="dialog"
            aria-label="Game Assistant"
            hidden>
            <div class="game-editor-chat-popout-head">
              <div>
                <strong>Game Assistant</strong>
                <span>Chat Console for the active game project, scene, selected entity, scripts, and assets.</span>
              </div>
              <button type="button" id="game-editor-chat-close" class="game-editor-chat-close">Close</button>
            </div>
            <aside
              id="game-editor-chat-panel"
              class="game-editor-chat-panel"
              data-chat-console-embed="game-editor"
              data-chat-console-active-app="game-editor"
              data-chat-console-id-prefix="game-editor-chat"
              data-chat-console-class-prefix="game-editor"
              data-chat-console-title="Game Assistant"
              data-chat-console-subtitle="Ask about this project, scene, selected entity, scripts, and assets."
              data-chat-console-notebook-id="game-editor-chat-notebook"
              data-chat-console-status-id="game-editor-chat-status"
              data-chat-console-thread-title="Game Builder Chat"
              data-chat-console-target-kind="game-project"
              data-chat-console-layout="full"
              data-chat-console-show-thread-rail="1"
              data-chat-console-show-current-thread-bar="1"
              aria-label="Game Assistant popout Chat Console">
            </aside>
          </section>
        </section>
      `;
      gameEditorState.nodes = {
        shell: gameEditorApp.querySelector(".game-editor-shell"),
        projectList: gameEditorApp.querySelector("#game-editor-project-list"),
        refreshProjects: gameEditorApp.querySelector("#game-editor-refresh-projects"),
        projectName: gameEditorApp.querySelector("#game-editor-project-name"),
        saveProject: gameEditorApp.querySelector("#game-editor-save-project"),
        resetProject: gameEditorApp.querySelector("#game-editor-reset-project"),
        preview: gameEditorApp.querySelector("#game-editor-preview"),
        viewport: gameEditorApp.querySelector("#game-editor-webgl-viewport"),
        canvas: gameEditorApp.querySelector("#game-editor-webgl-canvas"),
        webglStatus: gameEditorApp.querySelector("#game-editor-webgl-status"),
        frameSelected: gameEditorApp.querySelector("#game-editor-frame-selected"),
        entityList: gameEditorApp.querySelector("#game-editor-entity-list"),
        entityName: gameEditorApp.querySelector("#game-editor-entity-name"),
        entityX: gameEditorApp.querySelector("#game-editor-entity-x"),
        entityColor: gameEditorApp.querySelector("#game-editor-entity-color"),
        entityAsset: gameEditorApp.querySelector("#game-editor-entity-asset"),
        particleDensity: gameEditorApp.querySelector("#game-editor-particle-density"),
        particleDensityValue: gameEditorApp.querySelector("#game-editor-particle-density-value"),
        effectIntensity: gameEditorApp.querySelector("#game-editor-effect-intensity"),
        effectIntensityValue: gameEditorApp.querySelector("#game-editor-effect-intensity-value"),
        vfxPresetButtons: [...gameEditorApp.querySelectorAll("[data-vfx-preset]")],
        assetUpload: gameEditorApp.querySelector("#game-editor-asset-upload"),
        uploadAsset: gameEditorApp.querySelector("#game-editor-upload-asset"),
        assetList: gameEditorApp.querySelector("#game-editor-asset-list"),
        chatToggle: gameEditorApp.querySelector("#game-editor-chat-toggle"),
        chatPopout: gameEditorApp.querySelector("#game-editor-chat-popout"),
        chatClose: gameEditorApp.querySelector("#game-editor-chat-close"),
        chatPanel: gameEditorApp.querySelector("#game-editor-chat-panel"),
        status: gameEditorApp.querySelector("#game-editor-status")
      };
      bindGameEditorShell();
    }

    function bindGameEditorShell() {
      const nodes = gameEditorState.nodes;
      nodes.refreshProjects?.addEventListener("click", () => loadGameEditorProjects({force: true}).catch(reportGameEditorError));
      nodes.saveProject?.addEventListener("click", () => saveGameEditorProject().catch(reportGameEditorError));
      nodes.resetProject?.addEventListener("click", () => readGameEditorProject(gameEditorState.projectId, {reason: "reset"}).catch(reportGameEditorError));
      nodes.uploadAsset?.addEventListener("click", () => uploadGameEditorAsset().catch(reportGameEditorError));
      nodes.frameSelected?.addEventListener("click", frameSelectedGameEditorObject);
      nodes.chatToggle?.addEventListener("click", () => setGameEditorChatOpen(!gameEditorState.chatOpen));
      nodes.chatClose?.addEventListener("click", () => {
        setGameEditorChatOpen(false, {mountChat: false});
        nodes.chatToggle?.focus();
      });
      nodes.chatPopout?.addEventListener("click", (event) => {
        event.stopPropagation();
      });
      document.addEventListener("click", (event) => {
        if (!nodes.chatPopout || nodes.chatPopout.hidden) return;
        const target = event.target;
        if (!(target instanceof Node)) return;
        if (nodes.chatPopout.contains(target) || nodes.chatToggle?.contains(target)) return;
        setGameEditorChatOpen(false, {mountChat: false});
      });
      document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || !nodes.chatPopout || nodes.chatPopout.hidden) return;
        setGameEditorChatOpen(false, {mountChat: false});
        nodes.chatToggle?.focus();
      });
      nodes.projectName?.addEventListener("input", () => {
        if (!gameEditorState.project) return;
        gameEditorState.project.name = nodes.projectName.value;
        markGameEditorDirty("dirty - disk save needed");
        renderGameEditorProjectList();
      });
      nodes.entityName?.addEventListener("input", () => {
        const object = selectedGameEditorObject();
        if (!object) return;
        object.props = object.props && typeof object.props === "object" ? object.props : {};
        object.props.label = nodes.entityName.value;
        markGameEditorDirty("dirty - disk save needed");
        renderGameEditorEntityList();
        renderGameEditorPreview();
      });
      nodes.entityX?.addEventListener("input", () => {
        const object = selectedGameEditorObject();
        if (!object) return;
        const value = Number(nodes.entityX.value);
        if (Number.isFinite(value)) object.x = value;
        markGameEditorDirty("dirty - disk save needed");
        renderGameEditorPreview();
      });
      nodes.entityColor?.addEventListener("input", () => {
        const object = selectedGameEditorObject();
        if (!object) return;
        object.props = object.props && typeof object.props === "object" ? object.props : {};
        object.props.color = nodes.entityColor.value;
        markGameEditorDirty("dirty - disk save needed");
        renderGameEditorPreview();
      });
      nodes.entityAsset?.addEventListener("change", () => {
        const object = selectedGameEditorObject();
        if (!object) return;
        object.props = object.props && typeof object.props === "object" ? object.props : {};
        if (nodes.entityAsset.value) object.props.asset = nodes.entityAsset.value;
        else delete object.props.asset;
        markGameEditorDirty("dirty - disk save needed");
        renderGameEditorPreview();
      });
      nodes.particleDensity?.addEventListener("input", () => updateGameEditorVfxSettings({particleMultiplier: nodes.particleDensity.value}));
      nodes.effectIntensity?.addEventListener("input", () => updateGameEditorVfxSettings({effectMultiplier: nodes.effectIntensity.value}));
      nodes.vfxPresetButtons?.forEach((button) => {
        button.addEventListener("click", () => updateGameEditorVfxSettings({
          particleMultiplier: button.dataset.vfxPreset,
          effectMultiplier: button.dataset.vfxPreset
        }));
      });
    }

    function setGameEditorChatOpen(open, {mountChat = true} = {}) {
      const nodes = gameEditorState.nodes || {};
      const shouldOpen = Boolean(open) && document.body.dataset.activeApp === "game-editor";
      gameEditorState.chatOpen = shouldOpen;
      if (nodes.chatPopout) nodes.chatPopout.hidden = !shouldOpen;
      nodes.chatToggle?.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
      nodes.shell?.classList.toggle("chat-open", shouldOpen);
      if (shouldOpen && mountChat) mountGameEditorChat();
    }

    function safeGameEditorProjectId(value = gameEditorState.projectId) {
      const clean = String(value || "webgl-demo").replace(/\\/g, "/").split("/").filter(Boolean).join("-");
      return clean.replace(/[^a-zA-Z0-9_.-]/g, "-") || "webgl-demo";
    }

    function gameEditorProjectPath(projectId = gameEditorState.projectId) {
      return `game_projects/${safeGameEditorProjectId(projectId)}`;
    }

    function gameEditorChatThreadKey(projectId = gameEditorState.projectId) {
      return `game-project:${safeGameEditorProjectId(projectId)}`;
    }

    function gameEditorChatLinkedTarget(context = null) {
      const projectId = safeGameEditorProjectId(context?.project_id || gameEditorState.projectId);
      return {
        app: "game-editor",
        kind: "game-project",
        id: projectId,
        path: gameEditorProjectPath(projectId)
      };
    }

    function gameEditorBuildChatThreadMetadata(context = null) {
      const target = gameEditorChatLinkedTarget(context);
      return {
        origin_app: "game-editor",
        embedded_chat: true,
        target_kind: "game-project",
        target_id: target.id,
        linked_targets: [target],
        game_builder_phase: "scoped-editor-context"
      };
    }

    function findGameEditorLinkedChatThread(store, projectId = gameEditorState.projectId) {
      const key = gameEditorChatThreadKey(projectId);
      const linkedId = gameEditorLinkedChatThreads.get(key);
      let thread = linkedId ? store?.get?.(linkedId) : null;
      if (thread) return thread;
      const target = gameEditorChatLinkedTarget({project_id: projectId});
      thread = (store?.list?.() || []).find((candidate) => {
        const metadata = candidate?.metadata || {};
        const linkedTargets = Array.isArray(metadata.linked_targets) ? metadata.linked_targets : [];
        return metadata.origin_app === "game-editor" && linkedTargets.some((linked) => (
          linked?.kind === "game-project"
          && String(linked?.id || "") === target.id
          && String(linked?.path || "") === target.path
        ));
      }) || null;
      if (thread?.id) gameEditorLinkedChatThreads.set(key, thread.id);
      return thread;
    }

    function ensureGameEditorLinkedChatThread() {
      const store = window.MainComputerChatThreads;
      if (!store?.load) return null;
      store.load();
      const projectId = safeGameEditorProjectId(gameEditorState.projectId);
      let thread = findGameEditorLinkedChatThread(store, projectId);
      if (!thread && store?.create) {
        thread = store.create({
          title: "Game Builder Chat",
          metadata: gameEditorBuildChatThreadMetadata(gameEditorChatContextSnapshot()),
          makeActive: false
        });
      }
      if (thread?.id) gameEditorLinkedChatThreads.set(gameEditorChatThreadKey(projectId), thread.id);
      return thread || null;
    }

    function getGameEditorLinkedChatThreadId() {
      const key = gameEditorChatThreadKey();
      return gameEditorLinkedChatThreads.get(key) || "";
    }

    function setGameEditorLinkedChatThreadId(threadId, thread, context = {}) {
      const id = String(threadId || thread?.id || "");
      if (!id) return;
      gameEditorLinkedChatThreads.set(gameEditorChatThreadKey(), id);
      const panel = gameEditorState.nodes.chatPanel;
      if (panel) {
        panel.dataset.linkedThreadId = id;
        panel.dataset.chatConsoleTargetId = safeGameEditorProjectId(gameEditorState.projectId);
        if (context?.reason) panel.dataset.linkReason = String(context.reason);
      }
    }

    function buildGameEditorChatThreadLink(thread) {
      const url = new URL(window.location.href);
      url.pathname = "/applications/game-editor";
      url.searchParams.set("thread", thread?.id || getGameEditorLinkedChatThreadId());
      url.searchParams.set("project", safeGameEditorProjectId(gameEditorState.projectId));
      return url.toString();
    }

    function gameEditorChatContextSnapshot() {
      const projectId = safeGameEditorProjectId(gameEditorState.projectId);
      const projectPath = gameEditorProjectPath(projectId);
      const project = gameEditorState.project || {};
      const scene = activeGameEditorScene();
      const objects = sceneObjects();
      const selected = selectedGameEditorObject();
      const selectedIndex = selected ? objects.indexOf(selected) : -1;
      return {
        app: "game-editor",
        target_kind: "game-project",
        target_id: projectId,
        project_id: projectId,
        project_path: projectPath,
        allowed_root: projectPath,
        allowed_paths: [
          `${projectPath}/project.json`,
          `${projectPath}/scripts/**`,
          `${projectPath}/data/**`,
          `${projectPath}/assets/**`
        ],
        edit_mode: "read-only-context",
        dirty: Boolean(gameEditorState.dirty),
        content_hash: String(gameEditorState.contentHash || ""),
        active_scene_id: String(gameEditorState.selectedSceneId || scene?.id || project.activeSceneId || "default-empty-scene"),
        selected_entity_id: String(gameEditorState.selectedObjectId || selected?.id || ""),
        project: {
          id: projectId,
          name: displayProjectName(project),
          description: String(project.description || ""),
          scene_count: Array.isArray(project.scenes) ? project.scenes.length : 0
        },
        scenes: (Array.isArray(project.scenes) ? project.scenes : []).map((candidate) => ({
          id: String(candidate?.id || ""),
          name: String(candidate?.name || ""),
          active: String(candidate?.id || "") === String(gameEditorState.selectedSceneId || project.activeSceneId || ""),
          object_count: Array.isArray(candidate?.objects) ? candidate.objects.length : 0
        })),
        active_scene: scene ? {
          id: String(scene.id || ""),
          name: String(scene.name || ""),
          object_count: objects.length,
          background: scene.background || null
        } : null,
        selected_entity: selected ? {
          id: String(selected.id || ""),
          type: String(selected.type || ""),
          label: displayObjectLabel(selected, selectedIndex),
          x: Number(selected.x) || 0,
          y: Number(selected.y) || 0,
          width: Number(selected.width) || 0,
          height: Number(selected.height) || 0,
          props: cloneGameEditorData(selected.props || {})
        } : null,
        assets: (gameEditorState.assets || []).map((asset) => ({
          name: String(asset?.name || ""),
          path: String(asset?.path || asset?.name || ""),
          kind: String(asset?.kind || "asset")
        })),
        scripts: (Array.isArray(project.scripts) ? project.scripts : []).map((script) => (
          typeof script === "string"
            ? {path: script}
            : {
              name: String(script?.name || ""),
              path: String(script?.path || script?.name || ""),
              kind: String(script?.kind || "script")
            }
        )),
        guardrails: {
          target_policy: "selected_game_project_only",
          phase: "ui-context-only",
          mutation_allowed: false
        }
      };
    }

    window.MainComputerGameEditorContext = {
      snapshot: gameEditorChatContextSnapshot
    };

    function mountGameEditorChat({force = false} = {}) {
      const panel = gameEditorState.nodes.chatPanel;
      if (!panel) return null;
      if (force) {
        gameEditorState.chatController?.destroy?.();
        gameEditorState.chatController = null;
        panel.dataset.chatConsoleEmbeddedShell = "";
      }
      if (gameEditorState.chatController && !force) return gameEditorState.chatController;
      const api = window.MainComputerChatConsole || {};
      const mount = api.mountEmbedded || window.chatConsoleMountEmbedded;
      if (!mount) {
        panel.textContent = "Game Assistant is loading...";
        return null;
      }
      const thread = ensureGameEditorLinkedChatThread();
      const projectId = safeGameEditorProjectId(gameEditorState.projectId);
      panel.dataset.chatConsoleTargetId = projectId;
      gameEditorState.chatController = mount(panel, {
        embedId: "game-editor",
        activeApp: "game-editor",
        idPrefix: "game-editor-chat",
        classPrefix: "game-editor",
        title: "Game Assistant",
        subtitle: "Ask about this project, scene, selected entity, scripts, and assets.",
        notebookId: "game-editor-chat-notebook",
        statusId: "game-editor-chat-status",
        threadTitle: "Game Builder Chat",
        targetKind: "game-project",
        targetId: projectId,
        layout: "full",
        showThreadRail: true,
        showCurrentThreadBar: true,
        threadId: thread?.id || getGameEditorLinkedChatThreadId(),
        getLinkedThreadId: getGameEditorLinkedChatThreadId,
        setLinkedThreadId: setGameEditorLinkedChatThreadId,
        buildThreadLink: buildGameEditorChatThreadLink,
        getEmbeddedContext: gameEditorChatContextSnapshot,
        buildThreadMetadata: gameEditorBuildChatThreadMetadata,
        plugins: [
          {
            id: "game-editor-edit",
            label: "Edit this game",
            checkedLabel: "Editing this game",
            hint: "Route this AI request through the Game Editor edit pathway, locked to the active game project.",
            appliesTo: "ai",
            defaultEnabled: true,
            endpoint: "/api/applications/game-editor/chat/edit",
            pathway: "game-editor-rag-edit-smoke",
            targetKind: "game-project",
            targetId: projectId,
            lockedTarget: true,
            buildPayload({embedded_context: embeddedContext, config}) {
              const context = embeddedContext && typeof embeddedContext === "object" && !Array.isArray(embeddedContext) ? embeddedContext : {};
              const lockedProjectId = safeGameEditorProjectId(context.project_id || config?.targetId || projectId);
              return {
                edit_mode: "game-project",
                editor_edit_mode: "game-editor",
                requested_pathway: "game-editor-rag-edit-smoke",
                target_kind: "game-project",
                target_id: lockedProjectId,
                project_id: lockedProjectId,
                locked_to_mount: true,
                auto_apply: false
              };
            }
          }
        ],
        status(message) {
          if (message && panel) panel.dataset.chatStatus = message;
        }
      });
      return gameEditorState.chatController;
    }

    function refreshGameEditorChatMount(previousProjectId = gameEditorState.projectId) {
      const previous = safeGameEditorProjectId(previousProjectId);
      const current = safeGameEditorProjectId(gameEditorState.projectId);
      const panel = gameEditorState.nodes.chatPanel;
      if (panel) panel.dataset.chatConsoleTargetId = current;
      if (!gameEditorState.chatOpen) return gameEditorState.chatController || null;
      if (previous !== current && gameEditorState.chatController) return mountGameEditorChat({force: true});
      return mountGameEditorChat();
    }

    async function gameEditorPost(path, payload = {}) {
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

    function setGameEditorStatus(message) {
      if (gameEditorState.nodes.status) gameEditorState.nodes.status.textContent = message;
    }

    function setGameEditorWebglStatus(message) {
      if (gameEditorState.nodes.webglStatus) gameEditorState.nodes.webglStatus.textContent = message;
    }

    function reportGameEditorError(error) {
      const message = error instanceof Error ? error.message : String(error || "unknown error");
      setGameEditorStatus(`failed: ${message}`);
      setGameEditorWebglStatus(`webgl preview failed: ${message}`);
    }

    function markGameEditorDirty(message = "dirty - disk save needed") {
      gameEditorState.dirty = true;
      setGameEditorStatus(message);
      syncGameEditorSceneStore({reason: message});
    }

    function cloneGameEditorData(value) {
      try {
        return JSON.parse(JSON.stringify(value));
      } catch {
        return value;
      }
    }

    function dispatchGameEditorSceneChange(scene, reason = "update") {
      if (!scene) return;
      try {
        window.dispatchEvent(new CustomEvent("main-computer-game-editor-scene-change", {
          detail: {
            projectId: gameEditorState.projectId,
            sceneId: String(scene.id || gameEditorState.selectedSceneId || "default-empty-scene"),
            selectedObjectId: gameEditorState.selectedObjectId,
            dirty: gameEditorState.dirty,
            reason,
            scene: cloneGameEditorData(scene),
            project: cloneGameEditorData(gameEditorState.project),
            assets: cloneGameEditorData(gameEditorState.assets)
          }
        }));
      } catch {
        // Live Game Surface mirroring is best-effort; the editor preview still renders locally.
      }
    }

    function titleFromSlug(value) {
      return String(value || "game-project")
        .split(/[-_\s]+/)
        .filter(Boolean)
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(" ");
    }

    function displayProjectName(project) {
      const id = String(project?.id || gameEditorState.projectId || "webgl-demo");
      const name = String(project?.name || "").trim();
      if (id === "webgl-demo" && (!name || name === "Empty Game Surface")) return "WebGL Demo";
      return name || titleFromSlug(id);
    }

    function displayObjectLabel(object, index = 0) {
      const label = String(object?.props?.label || "").trim();
      if (object?.id === "hero-sprite" && (!label || label === "Main Character")) return "Main Character";
      if (object?.id === "hero-spell-aura" && (!label || label === "Hero Spell Swirl")) return "Hero Spell Swirl";
      if (object?.id === "hero-rune-ring" && (!label || label === "Casting Rune Ring")) return "Casting Rune Ring";
      if (object?.id === "particle-player-core" && (!label || label === "Player Particle Core")) return "Particle Core";
      return label || titleFromSlug(object?.id || `entity-${index + 1}`);
    }

    function normalizeColor(value, fallback = "#61d394") {
      const clean = String(value || "").trim();
      return /^#[0-9a-fA-F]{6}$/.test(clean) ? clean : fallback;
    }

    function activeGameEditorSceneMetadata() {
      const scene = activeGameEditorScene();
      if (!scene) return null;
      scene.metadata = scene.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
      scene.metadata.vfx = scene.metadata.vfx && typeof scene.metadata.vfx === "object" ? scene.metadata.vfx : {};
      return scene.metadata;
    }

    function normalizeGameEditorVfxValue(value, fallback = 2) {
      const number = Number(value);
      if (!Number.isFinite(number)) return fallback;
      return Math.min(4, Math.max(1, number));
    }

    function formatGameEditorMultiplier(value) {
      const number = normalizeGameEditorVfxValue(value);
      return `${Number.isInteger(number) ? number.toFixed(0) : number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}x`;
    }

    function syncGameEditorVfxControls() {
      const metadata = activeGameEditorSceneMetadata();
      const nodes = gameEditorState.nodes;
      const particleMultiplier = normalizeGameEditorVfxValue(metadata?.vfx?.particleMultiplier ?? metadata?.particleMultiplier ?? 2);
      const effectMultiplier = normalizeGameEditorVfxValue(metadata?.vfx?.effectMultiplier ?? metadata?.effectMultiplier ?? 2);
      if (nodes.particleDensity) nodes.particleDensity.value = String(particleMultiplier);
      if (nodes.effectIntensity) nodes.effectIntensity.value = String(effectMultiplier);
      if (nodes.particleDensityValue) nodes.particleDensityValue.textContent = formatGameEditorMultiplier(particleMultiplier);
      if (nodes.effectIntensityValue) nodes.effectIntensityValue.textContent = formatGameEditorMultiplier(effectMultiplier);
      nodes.vfxPresetButtons?.forEach((button) => {
        const preset = normalizeGameEditorVfxValue(button.dataset.vfxPreset, 1);
        const active = Math.abs(preset - particleMultiplier) < 0.01 && Math.abs(preset - effectMultiplier) < 0.01;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function updateGameEditorVfxSettings({particleMultiplier = null, effectMultiplier = null} = {}) {
      const metadata = activeGameEditorSceneMetadata();
      if (!metadata) return;
      if (particleMultiplier !== null) metadata.vfx.particleMultiplier = normalizeGameEditorVfxValue(particleMultiplier);
      if (effectMultiplier !== null) metadata.vfx.effectMultiplier = normalizeGameEditorVfxValue(effectMultiplier);
      metadata.vfx.maxParticlesPerEmitter = Math.max(440, Number(metadata.vfx.maxParticlesPerEmitter) || 440);
      syncGameEditorVfxControls();
      markGameEditorDirty("dirty - VFX density changed");
      renderGameEditorPreview();
      syncGameEditorSceneStore({reason: "vfx-density"});
    }

    function activeGameEditorScene() {
      const project = gameEditorState.project;
      const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
      const selected = String(project?.activeSceneId || gameEditorState.selectedSceneId || "default-empty-scene");
      return scenes.find((scene) => scene?.id === selected) || scenes[0] || window.MainComputerSceneStore?.defaultScene?.();
    }

    function sceneObjects() {
      const scene = activeGameEditorScene();
      if (!scene) return [];
      scene.objects = Array.isArray(scene.objects) ? scene.objects : [];
      return scene.objects;
    }

    function selectedGameEditorObject() {
      const objects = sceneObjects();
      return objects.find((object) => object?.id === gameEditorState.selectedObjectId) || objects[0] || null;
    }

    function selectGameEditorObject(objectId) {
      const objects = sceneObjects();
      gameEditorState.selectedObjectId = String(objectId || objects[0]?.id || "");
      syncGameEditorInspector();
      renderGameEditorEntityList();
      renderGameEditorPreview();
      syncGameEditorSceneStore({reason: "selection"});
    }

    function syncGameEditorSceneStore({reason = "sync", notify = true} = {}) {
      const scene = activeGameEditorScene();
      if (!scene) return null;
      gameEditorState.selectedSceneId = String(scene.id || "default-empty-scene");
      if (gameEditorApp) gameEditorApp.dataset.selectedSceneId = gameEditorState.selectedSceneId;
      const savedScene = window.MainComputerSceneStore?.saveScene?.(scene, {source: "game-editor"}) || scene;
      window.MainComputerSceneStore?.setSelectedSceneId?.(gameEditorState.selectedSceneId, {source: "game-editor"});
      if (notify) dispatchGameEditorSceneChange(savedScene, reason);
      return savedScene;
    }

    async function loadGameEditorProjects({force = false} = {}) {
      if (gameEditorState.loading && !force) return gameEditorState;
      gameEditorState.loading = true;
      try {
        setGameEditorStatus("loading game projects...");
        const data = await gameEditorPost("/api/applications/game-editor/projects", {});
        gameEditorState.projects = Array.isArray(data.projects) ? data.projects : [];
        if (!gameEditorState.projects.some((project) => project.id === gameEditorState.projectId)) {
          gameEditorState.projectId = gameEditorState.projects[0]?.id || "webgl-demo";
        }
        renderGameEditorProjectList();
        await readGameEditorProject(gameEditorState.projectId);
        return gameEditorState;
      } finally {
        gameEditorState.loading = false;
      }
    }

    async function readGameEditorProject(projectId, {reason = "load"} = {}) {
      const previousProjectId = gameEditorState.projectId;
      const data = await gameEditorPost("/api/applications/game-editor/project/read", {project_id: projectId || gameEditorState.projectId});
      gameEditorState.projectId = String(data.project_id || projectId || gameEditorState.projectId);
      gameEditorState.project = data.project;
      gameEditorState.contentHash = String(data.content_hash || "");
      gameEditorState.dirty = false;
      const scene = activeGameEditorScene();
      gameEditorState.selectedSceneId = String(scene?.id || "default-empty-scene");
      const objects = sceneObjects();
      gameEditorState.selectedObjectId = objects[0]?.id || "";
      await loadGameEditorAssets();
      syncGameEditorSceneStore();
      renderGameEditorProject();
      refreshGameEditorChatMount(previousProjectId);
      setGameEditorStatus(reason === "reset" ? "project reloaded" : "project loaded");
      return gameEditorState;
    }

    async function loadGameEditorAssets() {
      const data = await gameEditorPost("/api/applications/game-editor/assets", {project_id: gameEditorState.projectId});
      gameEditorState.assets = Array.isArray(data.assets) ? data.assets : [];
      renderGameEditorAssets();
    }

    function renderGameEditorProjectList() {
      const list = gameEditorState.nodes.projectList;
      if (!list) return;
      list.replaceChildren();
      gameEditorState.projects.forEach((project) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = displayProjectName(project);
        button.dataset.projectId = project.id;
        button.className = project.id === gameEditorState.projectId ? "active" : "";
        button.addEventListener("click", () => readGameEditorProject(project.id).catch(reportGameEditorError));
        list.append(button);
      });
      if (!list.childElementCount) {
        const empty = document.createElement("p");
        empty.textContent = "No game projects found.";
        list.append(empty);
      }
    }

    function renderGameEditorAssets() {
      const list = gameEditorState.nodes.assetList;
      if (list) {
        list.replaceChildren();
        gameEditorState.assets.forEach((asset) => {
          const item = document.createElement("div");
          item.className = "game-editor-asset-item";
          item.textContent = `${asset.path || asset.name} · ${asset.kind || "asset"}`;
          list.append(item);
        });
        if (!list.childElementCount) {
          const empty = document.createElement("p");
          empty.textContent = "No assets uploaded.";
          list.append(empty);
        }
      }
      const select = gameEditorState.nodes.entityAsset;
      if (select) {
        const selected = selectedGameEditorObject()?.props?.asset || "";
        select.replaceChildren();
        const none = document.createElement("option");
        none.value = "";
        none.textContent = "No asset";
        select.append(none);
        gameEditorState.assets.forEach((asset) => {
          const option = document.createElement("option");
          option.value = asset.path || asset.name;
          option.textContent = asset.path || asset.name;
          select.append(option);
        });
        select.value = [...select.options].some((option) => option.value === selected) ? selected : "";
      }
    }

    function renderGameEditorProject() {
      const project = gameEditorState.project;
      if (!project) return;
      if (gameEditorState.nodes.projectName) {
        gameEditorState.nodes.projectName.value = displayProjectName(project);
      }
      syncGameEditorInspector();
      syncGameEditorVfxControls();
      renderGameEditorProjectList();
      renderGameEditorEntityList();
      renderGameEditorAssets();
      renderGameEditorPreview();
    }

    function renderGameEditorEntityList() {
      const list = gameEditorState.nodes.entityList;
      if (!list) return;
      const objects = sceneObjects();
      list.replaceChildren();
      objects.forEach((object, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = displayObjectLabel(object, index);
        button.dataset.objectId = object.id || "";
        button.className = object.id === gameEditorState.selectedObjectId ? "active" : "";
        button.addEventListener("click", () => selectGameEditorObject(object.id));
        list.append(button);
      });
      if (!list.childElementCount) {
        const empty = document.createElement("p");
        empty.textContent = "No entities in this scene.";
        list.append(empty);
      }
    }

    function syncGameEditorInspector() {
      syncGameEditorVfxControls();
      const object = selectedGameEditorObject();
      const disabled = !object;
      const nodes = gameEditorState.nodes;
      [nodes.entityName, nodes.entityX, nodes.entityColor, nodes.entityAsset, nodes.frameSelected].forEach((node) => {
        if (node) node.disabled = disabled;
      });
      if (!object) {
        if (nodes.entityName) nodes.entityName.value = "";
        if (nodes.entityX) nodes.entityX.value = "";
        if (nodes.entityColor) nodes.entityColor.value = "#61d394";
        if (nodes.entityAsset) nodes.entityAsset.value = "";
        return;
      }
      const objectIndex = sceneObjects().indexOf(object);
      if (nodes.entityName) nodes.entityName.value = displayObjectLabel(object, objectIndex);
      if (nodes.entityX) nodes.entityX.value = String(Number(object.x) || 0);
      if (nodes.entityColor) nodes.entityColor.value = normalizeColor(object.props?.color);
      renderGameEditorAssets();
    }

    function renderGameEditorPreview() {
      const canvas = gameEditorState.nodes.canvas;
      if (!canvas) return;
      if (gameEditorState.runtime?.dispose) {
        gameEditorState.runtime.dispose();
      }
      const scene = activeGameEditorScene();
      if (!scene) {
        canvas.replaceChildren();
        setGameEditorWebglStatus("webgl preview has no scene");
        return;
      }
      const normalizedScene = window.MainComputerSceneStore?.normalizeScene?.(scene, scene.id) || scene;
      gameEditorState.runtime = window.MainComputerSceneViewer?.renderSceneSurface?.(canvas, normalizedScene, {
        mode: "game-editor",
        label: `Game Editor scene: ${normalizedScene.name || normalizedScene.id}`,
        projectId: gameEditorState.projectId,
        selectedObjectId: gameEditorState.selectedObjectId,
        assets: gameEditorState.assets,
        showLabels: true
      }) || {scene: normalizedScene, dispose() { canvas.replaceChildren(); }};
      decorateGameEditorPreviewObjects(normalizedScene);
      const count = Array.isArray(normalizedScene.objects) ? normalizedScene.objects.length : 0;
      setGameEditorWebglStatus(`webgl scene preview ready (${count} ${count === 1 ? "entity" : "entities"})`);
    }

    function decorateGameEditorPreviewObjects(scene) {
      const objects = new Map((scene.objects || []).map((object) => [String(object.id || ""), object]));
      gameEditorState.nodes.canvas?.querySelectorAll?.(".scene-object").forEach((element) => {
        const object = objects.get(element.dataset.sceneObjectId || "");
        if (!object) return;
        const color = normalizeColor(object.props?.color);
        element.style.setProperty("--mint", color);
        element.style.borderColor = color;
        element.classList.toggle("selected", object.id === gameEditorState.selectedObjectId);
        if (object.props?.asset) {
          const asset = gameEditorState.assets.find((candidate) => (candidate.path || candidate.name) === object.props.asset);
          if (asset?.kind === "image") {
            element.style.backgroundImage = `url("${asset.url}")`;
            element.style.backgroundSize = "cover";
            element.style.backgroundPosition = "center";
          }
        }
        if (!element.textContent.trim()) {
          const label = document.createElement("span");
          label.className = "scene-object-label";
          label.textContent = displayObjectLabel(object, scene.objects.indexOf(object));
          element.append(label);
        }
      });
    }

    function frameSelectedGameEditorObject() {
      const object = selectedGameEditorObject();
      if (!object) return;
      renderGameEditorPreview();
      const element = [...(gameEditorState.nodes.canvas?.querySelectorAll?.(".scene-object") || [])]
        .find((candidate) => candidate.dataset.sceneObjectId === String(object.id || ""));
      element?.scrollIntoView?.({block: "center", inline: "center", behavior: "smooth"});
      setGameEditorStatus(`framed ${displayObjectLabel(object)}`);
    }

    async function saveGameEditorProject() {
      const project = gameEditorState.project;
      if (!project) return;
      project.name = gameEditorState.nodes.projectName?.value.trim() || displayProjectName(project);
      syncGameEditorSceneStore();
      const data = await gameEditorPost("/api/applications/game-editor/project/write", {
        project_id: gameEditorState.projectId,
        expected_content_hash: gameEditorState.contentHash,
        project
      });
      gameEditorState.contentHash = String(data.content_hash || gameEditorState.contentHash);
      gameEditorState.dirty = false;
      setGameEditorStatus("project saved");
      renderGameEditorProjectList();
    }

    function readFileAsBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("Could not read selected asset."));
        reader.onload = () => {
          const result = String(reader.result || "");
          resolve(result.includes(",") ? result.split(",", 2)[1] : result);
        };
        reader.readAsDataURL(file);
      });
    }

    async function uploadGameEditorAsset() {
      const input = gameEditorState.nodes.assetUpload;
      const file = input?.files?.[0];
      if (!file) {
        setGameEditorStatus("choose an asset to upload");
        return;
      }
      setGameEditorStatus(`uploading ${file.name}...`);
      const contentBase64 = await readFileAsBase64(file);
      await gameEditorPost("/api/applications/game-editor/asset/upload", {
        project_id: gameEditorState.projectId,
        path: file.name,
        content_base64: contentBase64
      });
      input.value = "";
      await loadGameEditorAssets();
      setGameEditorStatus(`asset uploaded: ${file.name}`);
    }

    async function initGameEditorApp() {
      if (!gameEditorApp) return gameEditorState;
      if (!gameEditorState.initialized) {
        buildGameEditorShell();
        gameEditorState.initialized = true;
      }
      await loadGameEditorProjects();
      return gameEditorState;
    }

    function disposeGameEditorSurface() {
      setGameEditorChatOpen(false, {mountChat: false});
      if (gameEditorState.runtime?.dispose) {
        gameEditorState.runtime.dispose();
      }
      gameEditorState.runtime = null;
    }

