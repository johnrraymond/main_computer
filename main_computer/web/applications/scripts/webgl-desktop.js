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
      const selectedObjectId = String(candidate?.selectedObjectId || "");
      gameSurfaceRuntime = window.MainComputerSceneViewer?.renderSceneSurface?.(surface, scene, {
        mode: "game-surface",
        label: `Vertex-built shuttle boarding-defense surface: ${scene.name || scene.id}`,
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
            const actorLabel = String(detail?.actor?.props?.label || "Player Cadet");
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
