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
        "name": "Arcstorm Finale Showcase",
        "version": 5,
        "background": "radial-gradient(circle at 50% 18%, rgba(56, 189, 248, 0.22), rgba(15, 23, 42, 0.95) 58%, #020617 100%)",
        "objects": [
                {
                        "id": "hero-sprite",
                        "type": "sprite-actor",
                        "x": 4,
                        "y": 4,
                        "width": 124,
                        "height": 166,
                        "props": {
                                "label": "Main Character",
                                "role": "player",
                                "spawn": true,
                                "color": "#7dd3fc",
                                "z": 24,
                                "bob": 12,
                                "motion": "stride",
                                "spellState": "finale-casting",
                                "spriteSeries": [
                                        "idle",
                                        "charge",
                                        "cast",
                                        "release",
                                        "echo",
                                        "recover"
                                ],
                                "spriteRig": {
                                        "style": "energy-silhouette",
                                        "layers": [
                                                "shadow",
                                                "aura",
                                                "afterimage",
                                                "core",
                                                "mantle",
                                                "cast-flare",
                                                "weapon-trail",
                                                "spell-wings",
                                                "sparkles"
                                        ],
                                        "castFrames": [
                                                "idle",
                                                "charge",
                                                "cast",
                                                "release",
                                                "echo",
                                                "recover"
                                        ],
                                        "finisher": true
                                }
                        }
                },
                {
                        "id": "hero-spell-aura",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 210,
                        "height": 168,
                        "props": {
                                "label": "Hero Spell Swirl",
                                "role": "spell",
                                "color": "#facc15",
                                "particleCount": 97,
                                "particleSize": 4,
                                "spread": 1.18,
                                "motion": "spell-swirl",
                                "orbitRadius": 70,
                                "verticalLift": 64,
                                "zOffset": 78
                        }
                },
                {
                        "id": "hero-rune-ring",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 180,
                        "height": 92,
                        "props": {
                                "label": "Casting Rune Ring",
                                "role": "spell",
                                "color": "#67e8f9",
                                "particleCount": 59,
                                "particleSize": 3,
                                "spread": 1.04,
                                "motion": "rune-ring",
                                "orbitRadius": 80,
                                "verticalLift": 20,
                                "zOffset": 12
                        }
                },
                {
                        "id": "arcstorm-nova",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 260,
                        "height": 170,
                        "props": {
                                "label": "Arcstorm Nova",
                                "role": "finisher",
                                "color": "#fef3c7",
                                "particleCount": 113,
                                "particleSize": 4,
                                "spread": 1.32,
                                "motion": "nova-ring",
                                "orbitRadius": 96,
                                "verticalLift": 28,
                                "zOffset": 36,
                                "pulseDelay": 1080
                        }
                },
                {
                        "id": "finish-shockwave",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 320,
                        "height": 110,
                        "props": {
                                "label": "Ground Shockwave",
                                "role": "finisher",
                                "color": "#93c5fd",
                                "particleCount": 86,
                                "particleSize": 3,
                                "spread": 1.4,
                                "motion": "shockwave-ring",
                                "orbitRadius": 112,
                                "verticalLift": 8,
                                "zOffset": 4,
                                "pulseDelay": 1260
                        }
                },
                {
                        "id": "ruin-scout",
                        "type": "sprite-actor",
                        "x": 7,
                        "y": 3,
                        "width": 108,
                        "height": 148,
                        "props": {
                                "label": "Ruin Scout",
                                "color": "#c084fc",
                                "z": 14,
                                "bob": 7,
                                "motion": "glide",
                                "spellState": "staggered",
                                "spriteSeries": [
                                        "watch",
                                        "brace",
                                        "hit",
                                        "fracture",
                                        "recover"
                                ],
                                "spriteRig": {
                                        "style": "void-silhouette",
                                        "layers": [
                                                "shadow",
                                                "aura",
                                                "afterimage",
                                                "core",
                                                "mantle",
                                                "hit-flash",
                                                "sparkles"
                                        ],
                                        "castFrames": [
                                                "watch",
                                                "brace",
                                                "hit",
                                                "fracture",
                                                "recover"
                                        ]
                                }
                        }
                },
                {
                        "id": "ruin-curse",
                        "type": "particle-emitter",
                        "parentId": "ruin-scout",
                        "x": 0,
                        "y": 0,
                        "width": 145,
                        "height": 126,
                        "props": {
                                "label": "Void Curse Swirl",
                                "role": "spell",
                                "color": "#c084fc",
                                "particleCount": 59,
                                "particleSize": 4,
                                "spread": 1.05,
                                "motion": "spell-swirl",
                                "orbitRadius": 52,
                                "verticalLift": 44,
                                "zOffset": 62
                        }
                },
                {
                        "id": "echo-wraith",
                        "type": "sprite-actor",
                        "x": 2,
                        "y": 7,
                        "width": 104,
                        "height": 142,
                        "props": {
                                "label": "Echo Wraith",
                                "color": "#38bdf8",
                                "z": 12,
                                "bob": 8,
                                "motion": "phase",
                                "spellState": "linked",
                                "spriteSeries": [
                                        "materialize",
                                        "aim",
                                        "bind",
                                        "shatter"
                                ],
                                "spriteRig": {
                                        "style": "echo-silhouette",
                                        "layers": [
                                                "shadow",
                                                "aura",
                                                "afterimage",
                                                "core",
                                                "mantle",
                                                "hit-flash",
                                                "sparkles"
                                        ],
                                        "castFrames": [
                                                "materialize",
                                                "aim",
                                                "bind",
                                                "shatter"
                                        ]
                                }
                        }
                },
                {
                        "id": "wraith-curse",
                        "type": "particle-emitter",
                        "parentId": "echo-wraith",
                        "x": 0,
                        "y": 0,
                        "width": 140,
                        "height": 118,
                        "props": {
                                "label": "Echo Curse Swirl",
                                "role": "spell",
                                "color": "#38bdf8",
                                "particleCount": 51,
                                "particleSize": 3,
                                "spread": 1.05,
                                "motion": "spell-swirl",
                                "orbitRadius": 50,
                                "verticalLift": 38,
                                "zOffset": 58
                        }
                },
                {
                        "id": "hero-arc-bolt",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 260,
                        "height": 72,
                        "props": {
                                "label": "Hero Arc Bolt",
                                "role": "projectile",
                                "color": "#fde68a",
                                "particleCount": 62,
                                "particleSize": 4,
                                "spread": 1,
                                "motion": "spell-bolt",
                                "sourceId": "hero-sprite",
                                "targetId": "ruin-scout",
                                "sourceZOffset": 94,
                                "targetZOffset": 68,
                                "zOffset": 84,
                                "pulseDelay": 420
                        }
                },
                {
                        "id": "hero-chain-bolt",
                        "type": "particle-emitter",
                        "parentId": "hero-sprite",
                        "x": 0,
                        "y": 0,
                        "width": 300,
                        "height": 74,
                        "props": {
                                "label": "Chain Bolt",
                                "role": "projectile",
                                "color": "#bfdbfe",
                                "particleCount": 57,
                                "particleSize": 4,
                                "spread": 0.95,
                                "motion": "spell-bolt",
                                "sourceId": "hero-sprite",
                                "targetId": "echo-wraith",
                                "sourceZOffset": 86,
                                "targetZOffset": 64,
                                "zOffset": 78,
                                "pulseDelay": 860
                        }
                },
                {
                        "id": "ruin-impact-burst",
                        "type": "particle-emitter",
                        "parentId": "ruin-scout",
                        "x": 0,
                        "y": 0,
                        "width": 182,
                        "height": 142,
                        "props": {
                                "label": "Impact Burst",
                                "role": "impact",
                                "color": "#fb7185",
                                "particleCount": 84,
                                "particleSize": 4,
                                "spread": 1.22,
                                "motion": "impact-burst",
                                "orbitRadius": 60,
                                "verticalLift": 44,
                                "zOffset": 72,
                                "pulseDelay": 760
                        }
                },
                {
                        "id": "wraith-impact-burst",
                        "type": "particle-emitter",
                        "parentId": "echo-wraith",
                        "x": 0,
                        "y": 0,
                        "width": 164,
                        "height": 132,
                        "props": {
                                "label": "Echo Impact Burst",
                                "role": "impact",
                                "color": "#60a5fa",
                                "particleCount": 68,
                                "particleSize": 3,
                                "spread": 1.18,
                                "motion": "impact-burst",
                                "orbitRadius": 54,
                                "verticalLift": 38,
                                "zOffset": 68,
                                "pulseDelay": 1040
                        }
                },
                {
                        "id": "sky-rune-fall",
                        "type": "particle-emitter",
                        "x": 5,
                        "y": 2,
                        "width": 640,
                        "height": 280,
                        "props": {
                                "label": "Sky Rune Fall",
                                "role": "arena",
                                "color": "#e0f2fe",
                                "particleCount": 97,
                                "particleSize": 3,
                                "spread": 1.15,
                                "motion": "starfall",
                                "z": 132,
                                "verticalLift": 130,
                                "pulseDelay": 640
                        }
                },
                {
                        "id": "leyline-current",
                        "type": "particle-emitter",
                        "x": 4,
                        "y": 7,
                        "width": 500,
                        "height": 96,
                        "props": {
                                "label": "Leyline Current",
                                "color": "#34d399",
                                "particleCount": 76,
                                "particleSize": 3,
                                "spread": 1.35,
                                "motion": "stream",
                                "z": 4
                        }
                }
        ],
        "metadata": {
                "starter": true,
                "projection": "isometric",
                "tileWidth": 92,
                "tileHeight": 46,
                "originX": 480,
                "originY": 124,
                "particleOnly": false,
                "includesDefaultPlayer": true,
                "isometric": true,
                "rolloutPhase": "phase-4-finale-showcase",
                "characterModel": "sprite-particle-rig",
                "meshActorsEnabled": false,
            "controls": {"movement": "left-click", "keyboardMovement": false, "clickToMove": true, "movementActorId": "hero-sprite", "moveSpeed": 3.15},
            "movementBounds": {"minX": 0, "maxX": 10, "minY": 0, "maxY": 10},
            "vfx": {"particleMultiplier": 2, "effectMultiplier": 2, "maxParticlesPerEmitter": 440},
            "quadrupleParticles": false,
            "uiParticleControls": true,
                "parentedParticles": true,
                "linkedSpellProjectiles": true,
                "targetedParticles": true,
                "finaleShowcase": true,
                "choreography": {
                        "title": "Arcstorm Finale",
                        "durationMs": 6400,
                        "cameraPulse": true,
                        "beats": [
                                {
                                        "label": "Charge",
                                        "timeMs": 0,
                                        "cue": "hero-spell-aura"
                                },
                                {
                                        "label": "Bind",
                                        "timeMs": 1200,
                                        "cue": "hero-chain-bolt"
                                },
                                {
                                        "label": "Release",
                                        "timeMs": 2400,
                                        "cue": "hero-arc-bolt"
                                },
                                {
                                        "label": "Nova",
                                        "timeMs": 3600,
                                        "cue": "arcstorm-nova"
                                },
                                {
                                        "label": "Aftershock",
                                        "timeMs": 5000,
                                        "cue": "finish-shockwave"
                                }
                        ]
                }
        }
};
      scene.id = String(sceneId || "default-empty-scene");
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
      const selectedObjectId = String(candidate?.selectedObjectId || "");
      gameSurfaceRuntime = window.MainComputerSceneViewer?.renderSceneSurface?.(surface, scene, {
        mode: "game-surface",
        label: `Game Surface mirror: ${scene.name || scene.id}`,
        projectId,
        selectedObjectId,
        assets: Array.isArray(candidate?.assets) ? candidate.assets : [],
        showLabels: true,
        enableClickMovement: true,
        movementObjectId: "hero-sprite",
        onSceneMovement(detail) {
          const movedScene = detail?.scene || scene;
          if (detail?.phase === "finish" && window.MainComputerSceneStore?.saveScene) {
            window.MainComputerSceneStore.saveScene(movedScene, {source: "webgl-demo", notify: false});
            window.MainComputerSceneStore.setSelectedSceneId?.(String(movedScene.id || "default-empty-scene"), {source: "webgl-demo", notify: false});
          }
          if (glStatus) {
            const actorLabel = String(detail?.actor?.props?.label || "Main Character");
            const x = Number(detail?.targetX ?? detail?.worldX ?? 0).toFixed(1);
            const y = Number(detail?.targetY ?? detail?.worldY ?? 0).toFixed(1);
            const action = detail?.phase === "finish" ? "arrived at" : "moving steadily toward";
            glStatus.textContent = `${actorLabel} ${action} tile ${x}, ${y}`;
          }
        }
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
      bindWebglVfxControls();
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
