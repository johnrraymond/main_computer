    const webglProjectState = {
      projectId: "webgl-demo",
      project: null,
      assets: [],
      contentHash: "",
      loading: null
    };

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

    function webglProjectScene(project, sceneId = "") {
      const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
      const selectedId = String(sceneId || project?.activeSceneId || window.MainComputerSceneStore?.selectedSceneId?.() || "default-empty-scene");
      return scenes.find((scene) => scene?.id === selectedId) || scenes[0] || null;
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
      return {
        id: sceneId,
        name: "Isometric Battle Floor",
        version: 2,
        background: "radial-gradient(circle at 50% 24%, rgba(56, 189, 248, 0.16), rgba(15, 23, 42, 0.92) 55%, #020617 100%)",
        objects: [{
          id: "hero-sprite",
          type: "sprite-actor",
          x: 4,
          y: 4,
          width: 104,
          height: 144,
          props: {
            label: "Main Character",
            role: "player",
            spawn: true,
            color: "#7dd3fc",
            z: 18,
            bob: 10,
            motion: "stride",
            spriteSeries: ["idle-nw", "step-left", "idle-ne", "step-right"]
          }
        }, {
          id: "hero-aura",
          type: "particle-emitter",
          x: 4,
          y: 4,
          width: 120,
          height: 96,
          props: {
            label: "Arc Halo",
            role: "support",
            color: "#facc15",
            particleCount: 30,
            particleSize: 4,
            spread: 0.9,
            motion: "orbit",
            z: 44
          }
        }],
        metadata: {
          starter: true,
          projection: "isometric",
          tileWidth: 92,
          tileHeight: 46,
          originX: 480,
          originY: 124,
          particleOnly: false,
          includesDefaultPlayer: true,
          isometric: true
        }
      };
    }

    function renderWebglSceneCandidate(candidate) {
      const surface = gameSurface || canvas;
      if (!surface) {
        if (glStatus) glStatus.textContent = "scene surface unavailable";
        return null;
      }
      const scene = candidate?.scene || fallbackWebglScene();
      const projectId = String(candidate?.projectId || webglProjectState.projectId || "webgl-demo");
      const selectedObjectId = String(candidate?.selectedObjectId || "");
      gameSurfaceRuntime = window.MainComputerSceneViewer?.renderSceneSurface?.(surface, scene, {
        mode: "game-surface",
        label: `Game Surface mirror: ${scene.name || scene.id}`,
        projectId,
        selectedObjectId,
        assets: Array.isArray(candidate?.assets) ? candidate.assets : [],
        showLabels: true
      }) || {scene};
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
      if (glStatus) glStatus.textContent = `${projectId} / ${scene.name || scene.id} mirrored from ${source} (${count} ${count === 1 ? "entity" : "entities"})${dirtySuffix}`;
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
      pauseGameSurface();
      const surface = gameSurface || canvas;
      if (!surface) {
        if (glStatus) glStatus.textContent = "scene surface unavailable";
        return;
      }
      running = true;
      const liveCandidate = webglEditorSceneCandidate(sceneId);
      if (liveCandidate) {
        webglProjectState.projectId = liveCandidate.projectId;
        webglProjectState.project = liveCandidate.project;
        webglProjectState.assets = liveCandidate.assets;
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
      if (currentApp === "webgl") {
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

    window.addEventListener("main-computer-game-editor-scene-change", handleWebglSceneMirrorEvent);
    window.addEventListener("main-computer-scene-change", handleWebglSceneMirrorEvent);
    window.addEventListener("storage", (event) => {
      if (event.key !== window.MainComputerSceneStore?.sceneStorageKey) return;
      if (currentApp !== "webgl") return;
      renderWebglSceneCandidate(webglStoredSceneCandidate() || bestKnownWebglScene());
    });

    function draw() {
      return;
    }

    function isPlainPrimaryAppClick(event) {
      return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    }

    ensureDesktopIcons();
    document.querySelectorAll("[data-app]").forEach((button) => {
      button.addEventListener("click", (event) => {
        if (button instanceof HTMLAnchorElement) {
          if (!isPlainPrimaryAppClick(event)) return;
          event.preventDefault();
        }
        setActiveApp(button.dataset.app);
      });
    });
