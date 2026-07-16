    (function () {
      const imageAssetKinds = new Set(["image"]);

      function fallbackScene(sceneId = "default-empty-scene") {
        const scene = {
          "id": "default-empty-scene",
          "name": "Shuttlecraft Walkaround",
          "version": 9,
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
                                        "spellState": "exploring-cabin",
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
                                        "instructions": "Drag or use arrow keys to look. Use W/A/S/D to walk and Shift to sprint."
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
                    "rolloutPhase": "phase-4-shuttle-first-person-movement",
                    "setting": "federation-like shuttle craft interior with stars and mother ship visible through the forward viewport",
                    "starterScene": "shuttlecraft-walkaround-spawn",
                    "characterModel": "first-person-cadet-presence",
                    "meshActorsEnabled": false,
                    "parentedParticles": true,
                    "linkedSpellProjectiles": false,
                    "linkedSensorPulses": true,
                    "targetedParticles": true,
                    "shuttleInterior": true,
                    "choreography": {
                              "title": "Shuttle Walk-Around Boot",
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
                                                  "timeMs": 1100,
                                                  "cue": "console-status-glow"
                                        },
                                        {
                                                  "label": "Viewport stars",
                                                  "timeMs": 2100,
                                                  "cue": "viewport-starfield"
                                        },
                                        {
                                                  "label": "Mother ship contact",
                                                  "timeMs": 3600,
                                                  "cue": "mother-ship"
                                        },
                                        {
                                                  "label": "Ready to explore",
                                                  "timeMs": 6200,
                                                  "cue": "lookaround-camera"
                                        }
                              ]
                    },
                    "controls": {
                              "mode": "first-person",
                              "pointerDrag": true,
                              "keyboard": "wasd-and-arrow-keys",
                              "movement": "bounded-first-person-walk",
                              "sprint": "shift"
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
                              "position": [0.0, 0.75, 3.35],
                              "yaw": 0,
                              "pitch": -2,
                              "yawLimit": 180,
                              "pitchLimit": 28,
                              "hint": "Drag or use arrow keys to look. Use W/A/S/D to walk, and hold Shift to sprint inside the shuttle."
                    },
                    "shuttle3d": {
                              "mode": "webgl-vertex-mesh",
                              "lookAround": true,
                              "viewport": "forward-viewer",
                              "starfield": "viewport-starfield",
                              "starfieldSphere": {
                                    "mode": "camera-centered-sphere",
                                    "radius": 124,
                                    "count": 420,
                                    "seed": 73129,
                                    "minimumSize": 0.12,
                                    "maximumSize": 0.38,
                                    "fixedDistanceFromCamera": true
                              },
                              "motherShip": "mother-ship",
                              "motherShipLabel": "Mother Ship",
                              "playerAnchor": "hero-sprite",
                              "controlsHint": "Click to focus • Drag or arrows to look • W/A/S/D to walk • Shift to sprint",
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
                                  3.35
                                ],
                                "bounds": {
                                  "minX": -3.92,
                                  "maxX": 3.92,
                                  "minZ": -7.72,
                                  "maxZ": 5.82
                                },
                                "colliders": [
                                  {
                                    "id": "helm-console",
                                    "minX": -3.0,
                                    "maxX": -0.3,
                                    "minZ": -5.55,
                                    "maxZ": -3.55
                                  },
                                  {
                                    "id": "science-console",
                                    "minX": 0.3,
                                    "maxX": 3.0,
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
                                    "minZ": 5.7,
                                    "maxZ": 6.45
                                  }
                                ]
                              },
                              "geometry": {
                                  "renderer": "raw-webgl",
                                  "primitive": "triangles",
                                  "boundsVertexCount": 12,
                                  "boundsVertices": [
                                    [
                                      -4.5,
                                      -1.45,
                                      -8.8
                                    ],
                                    [
                                      -4.5,
                                      2.05,
                                      -8.8
                                    ],
                                    [
                                      -3.55,
                                      3.15,
                                      -8.8
                                    ],
                                    [
                                      3.55,
                                      3.15,
                                      -8.8
                                    ],
                                    [
                                      4.5,
                                      2.05,
                                      -8.8
                                    ],
                                    [
                                      4.5,
                                      -1.45,
                                      -8.8
                                    ],
                                    [
                                      -4.5,
                                      -1.45,
                                      6.8
                                    ],
                                    [
                                      -4.5,
                                      2.05,
                                      6.8
                                    ],
                                    [
                                      -3.55,
                                      3.15,
                                      6.8
                                    ],
                                    [
                                      3.55,
                                      3.15,
                                      6.8
                                    ],
                                    [
                                      4.5,
                                      2.05,
                                      6.8
                                    ],
                                    [
                                      4.5,
                                      -1.45,
                                      6.8
                                    ]
                                  ],
                                  "viewportOpening": {
                                    "left": -2.92,
                                    "right": 2.92,
                                    "bottom": 0.0,
                                    "top": 2.32,
                                    "z": -8.8
                                  },
                                  "actualHullBounds": true
                              }
                    }
          }
};
        if (sceneId && sceneId !== scene.id) {
          return {...scene, id: sceneId, name: scene.name || "Shuttlecraft Walkaround"};
        }
        return scene;
      }

      function resolveScene(sceneOrId) {
        if (sceneOrId && typeof sceneOrId === "object") {
          return window.MainComputerSceneStore?.normalizeScene?.(sceneOrId, sceneOrId.id) || sceneOrId;
        }
        const sceneId = String(sceneOrId || window.MainComputerSceneStore?.selectedSceneId?.() || "default-empty-scene");
        return window.MainComputerSceneStore?.getScene?.(sceneId) || fallbackScene(sceneId);
      }

      function normalizeSceneColor(value, fallback = "") {
        const clean = String(value || "").trim();
        return /^#[0-9a-fA-F]{6}$/.test(clean) ? clean : fallback;
      }

      function numericSceneProp(value, fallback, min = -Infinity, max = Infinity) {
        const number = Number(value);
        if (!Number.isFinite(number)) return fallback;
        return Math.min(max, Math.max(min, number));
      }

      function sceneVfxSettings(scene) {
        const metadata = scene?.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
        const vfx = metadata.vfx && typeof metadata.vfx === "object" ? metadata.vfx : {};
        const particleMultiplier = numericSceneProp(
          vfx.particleMultiplier ?? metadata.particleMultiplier,
          1,
          0.25,
          4
        );
        const effectMultiplier = numericSceneProp(
          vfx.effectMultiplier ?? metadata.effectMultiplier,
          1,
          0.5,
          4
        );
        const maxParticlesPerEmitter = Math.round(numericSceneProp(
          vfx.maxParticlesPerEmitter ?? metadata.maxParticlesPerEmitter,
          440,
          32,
          1200
        ));
        return {particleMultiplier, effectMultiplier, maxParticlesPerEmitter};
      }

      function scaledParticleCount(scene, baseCount, minimum = 4) {
        const vfx = sceneVfxSettings(scene);
        return Math.round(Math.min(vfx.maxParticlesPerEmitter, Math.max(minimum, baseCount * vfx.particleMultiplier)));
      }

      function particleEffectScale(scene) {
        const {effectMultiplier} = sceneVfxSettings(scene);
        return {
          intensity: effectMultiplier,
          glow: 0.8 + effectMultiplier * 0.35,
          alpha: Math.min(1.35, 0.72 + effectMultiplier * 0.15)
        };
      }

      function particleHash(value) {
        return String(value || "")
          .split("")
          .reduce((hash, char) => ((hash << 5) - hash + char.charCodeAt(0)) | 0, 0);
      }

      function sceneObjectLabel(object) {
        return String(object?.props?.label || object?.name || object?.id || "").trim();
      }

      function sceneAssetsByPath(options = {}) {
        const assets = Array.isArray(options.assets) ? options.assets : [];
        const byPath = new Map();
        assets.forEach((asset) => {
          const path = String(asset?.path || asset?.name || "").trim();
          if (path) byPath.set(path, asset);
        });
        return byPath;
      }

      function sceneObjectAsset(object, options = {}) {
        const assetPath = String(object?.props?.asset || "").trim();
        if (!assetPath) return null;
        return sceneAssetsByPath(options).get(assetPath) || null;
      }

      function sceneObjectGpuForgeAtlas(object, options = {}) {
        const props = object?.props && typeof object.props === "object" ? object.props : {};
        const atlas = props.gpuForgeAtlas && typeof props.gpuForgeAtlas === "object"
          ? props.gpuForgeAtlas
          : {path: props.gpuForgeAtlas || props.gpuForgeAtlasPath || ""};
        const path = String(atlas.path || "").trim();
        if (!path) return null;
        const asset = sceneAssetsByPath(options).get(path);
        if (!asset || !asset.url) return null;
        return {asset, atlas};
      }

      function sceneProjection(scene) {
        const explicit = String(scene?.metadata?.projection || "").trim().toLowerCase();
        if (explicit) return explicit;
        if (Array.isArray(scene?.objects) && scene.objects.some((object) => object?.type === "sprite-actor")) return "isometric";
        return "surface";
      }

      function sceneProjectionMetrics(scene) {
        return {
          tileWidth: numericSceneProp(scene?.metadata?.tileWidth, 92, 48, 160),
          tileHeight: numericSceneProp(scene?.metadata?.tileHeight, 46, 24, 96),
          originX: numericSceneProp(scene?.metadata?.originX, 480, 0, 4096),
          originY: numericSceneProp(scene?.metadata?.originY, 124, -512, 4096)
        };
      }

      function sceneObjectsById(scene) {
        const objects = Array.isArray(scene?.objects) ? scene.objects : [];
        return new Map(objects.map((object) => [String(object?.id || ""), object]));
      }

      function projectionSourceObject(object, scene) {
        const parentId = String(object?.parentId || object?.props?.parentId || "").trim();
        if (!parentId) return object;
        const parent = sceneObjectsById(scene).get(parentId);
        if (!parent) return object;
        const source = JSON.parse(JSON.stringify(object));
        source.x = (Number(parent.x) || 0) + numericSceneProp(object?.props?.offsetX ?? object.x, 0, -128, 128);
        source.y = (Number(parent.y) || 0) + numericSceneProp(object?.props?.offsetY ?? object.y, 0, -128, 128);
        const parentZ = numericSceneProp(parent?.props?.z ?? parent?.props?.elevation, 0, -256, 512);
        const zOffset = numericSceneProp(object?.props?.zOffset ?? object?.props?.z, 0, -256, 512);
        source.props = source.props && typeof source.props === "object" ? source.props : {};
        source.props.z = parentZ + zOffset;
        return source;
      }

      function projectWorldPoint(worldX, worldY, worldZ, scene) {
        const metrics = sceneProjectionMetrics(scene);
        return {
          left: metrics.originX + ((worldX - worldY) * metrics.tileWidth) / 2,
          top: metrics.originY + ((worldX + worldY) * metrics.tileHeight) / 2 - worldZ
        };
      }

      function sceneObjectWorldPoint(object, scene, zOffset = 0) {
        const source = projectionSourceObject(object, scene);
        const worldX = numericSceneProp(source.x, 0, -256, 256);
        const worldY = numericSceneProp(source.y, 0, -256, 256);
        const worldZ = numericSceneProp(source?.props?.z ?? source?.props?.elevation, 0, -256, 512) + numericSceneProp(zOffset, 0, -256, 512);
        return {worldX, worldY, worldZ, ...projectWorldPoint(worldX, worldY, worldZ, scene)};
      }

      function linkedParticleProjection(object, scene) {
        const motion = String(object?.props?.motion || "");
        const sourceId = String(object?.props?.sourceId || object?.parentId || object?.props?.parentId || "").trim();
        const targetId = String(object?.props?.targetId || "").trim();
        if (motion !== "spell-bolt" || !sourceId || !targetId) return null;
        const objects = sceneObjectsById(scene);
        const sourceObject = objects.get(sourceId);
        const targetObject = objects.get(targetId);
        if (!sourceObject || !targetObject) return null;
        const source = sceneObjectWorldPoint(sourceObject, scene, numericSceneProp(object?.props?.sourceZOffset, 64, -256, 256));
        const target = sceneObjectWorldPoint(targetObject, scene, numericSceneProp(object?.props?.targetZOffset, 52, -256, 256));
        const dx = target.left - source.left;
        const dy = target.top - source.top;
        const length = Math.max(32, Math.sqrt(dx * dx + dy * dy));
        const angle = Math.atan2(dy, dx) * (180 / Math.PI);
        return {
          left: (source.left + target.left) / 2,
          top: (source.top + target.top) / 2,
          width: length,
          height: Math.max(24, Number(object.height) || 48),
          zIndex: Math.round((source.worldX + source.worldY + target.worldX + target.worldY) * 5 + Math.max(source.worldZ, target.worldZ)),
          transform: `translate(-50%, -50%) rotate(${angle.toFixed(2)}deg)`,
          anchor: "linked-spell-path",
          pathLength: length,
          pathAngle: angle,
          sourceLeft: source.left,
          sourceTop: source.top,
          targetLeft: target.left,
          targetTop: target.top
        };
      }

      function projectSceneObject(object, scene) {
        const projection = sceneProjection(scene);
        const linked = linkedParticleProjection(object, scene);
        if (linked) return linked;
        const source = projectionSourceObject(object, scene);
        if (projection !== "isometric") {
          return {
            left: Number(source.x) || 0,
            top: Number(source.y) || 0,
            width: Math.max(0, Number(source.width) || 0),
            height: Math.max(0, Number(source.height) || 0),
            zIndex: 10,
            transform: "",
            anchor: "top-left"
          };
        }
        const worldX = numericSceneProp(source.x, 0, -256, 256);
        const worldY = numericSceneProp(source.y, 0, -256, 256);
        const worldZ = numericSceneProp(source?.props?.z ?? source?.props?.elevation, 0, -256, 512);
        const point = projectWorldPoint(worldX, worldY, worldZ, scene);
        const metrics = sceneProjectionMetrics(scene);
        const width = Math.max(48, Number(source.width) || metrics.tileWidth);
        const height = Math.max(56, Number(source.height) || metrics.tileWidth * 1.25);
        return {
          left: point.left,
          top: point.top,
          width,
          height,
          zIndex: Math.round((worldX + worldY) * 10 + worldZ),
          transform: "translate(-50%, -100%)",
          anchor: "bottom-center"
        };
      }

      function decorateSceneObject(element, object, options = {}) {
        const color = normalizeSceneColor(object?.props?.color);
        if (color) {
          element.style.setProperty("--mint", color);
          element.style.borderColor = color;
        }
        const selectedObjectId = String(options.selectedObjectId || "");
        element.classList.toggle("selected", Boolean(selectedObjectId && object?.id === selectedObjectId));
        const asset = sceneObjectAsset(object, options);
        if (asset && imageAssetKinds.has(String(asset.kind || "")) && asset.url) {
          element.dataset.sceneAsset = String(asset.path || asset.name || "");
          if (String(object?.type || "") === "sprite-actor") {
            element.style.setProperty("--scene-sprite-asset", `url("${asset.url}")`);
          } else {
            element.style.backgroundImage = `url("${asset.url}")`;
            element.style.backgroundSize = "cover";
            element.style.backgroundPosition = "center";
          }
        }
      }

      function appendSceneObjectLabel(element, object, options = {}) {
        if (options.showLabels === false) return;
        const labelText = sceneObjectLabel(object);
        if (!labelText) return;
        const label = document.createElement("span");
        label.className = "scene-object-label";
        label.textContent = labelText;
        element.append(label);
      }


      function sceneWebglParticlesRequested(scene, options = {}) {
        const metadata = scene?.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
        const vfx = metadata.vfx && typeof metadata.vfx === "object" ? metadata.vfx : {};
        const explicit = options.particleRenderer ?? options.particleRenderMode ?? vfx.particleRenderer ?? metadata.particleRenderer;
        if (explicit === false) return false;
        const mode = String(explicit || "webgl").trim().toLowerCase();
        return mode !== "dom" && mode !== "html" && mode !== "css";
      }

      function sceneColorRgb(color) {
        const clean = normalizeSceneColor(color, "#7dd3fc");
        return {
          r: parseInt(clean.slice(1, 3), 16) / 255,
          g: parseInt(clean.slice(3, 5), 16) / 255,
          b: parseInt(clean.slice(5, 7), 16) / 255
        };
      }

      function sceneWebglShader(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          const log = gl.getShaderInfoLog(shader) || "Unknown shader compile failure";
          gl.deleteShader(shader);
          throw new Error(log);
        }
        return shader;
      }

      function sceneWebglProgram(gl, vertexSource, fragmentSource) {
        const vertex = sceneWebglShader(gl, gl.VERTEX_SHADER, vertexSource);
        const fragment = sceneWebglShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
        const program = gl.createProgram();
        gl.attachShader(program, vertex);
        gl.attachShader(program, fragment);
        gl.linkProgram(program);
        gl.deleteShader(vertex);
        gl.deleteShader(fragment);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
          const log = gl.getProgramInfoLog(program) || "Unknown program link failure";
          gl.deleteProgram(program);
          throw new Error(log);
        }
        return program;
      }

      class SceneWebglParticleLayer {
        constructor(canvas) {
          this.canvas = canvas;
          this.gl = canvas.getContext("webgl", {
            alpha: true,
            antialias: false,
            depth: false,
            preserveDrawingBuffer: false,
            premultipliedAlpha: true
          }) || canvas.getContext("experimental-webgl", {
            alpha: true,
            antialias: false,
            depth: false,
            preserveDrawingBuffer: false,
            premultipliedAlpha: true
          });
          if (!this.gl) throw new Error("WebGL particle layer unavailable");
          this.strideFloats = 16;
          this.floatSize = Float32Array.BYTES_PER_ELEMENT;
          this.strideBytes = this.strideFloats * this.floatSize;
          this.maxDpr = 2;
          this.particles = [];
          this.emitters = new Map();
          this.particleCount = 0;
          this.animationFrame = 0;
          this.disposed = false;
          this.startedAt = 0;
          this.compile();
          this.buffer = this.gl.createBuffer();
          this.resizeObserver = typeof ResizeObserver === "function"
            ? new ResizeObserver(() => this.resize())
            : null;
          this.resizeObserver?.observe?.(canvas);
          canvas.addEventListener("webglcontextlost", this.handleContextLost = (event) => {
            event.preventDefault();
            this.dispose();
          });
        }

        compile() {
          const vertexSource = `
            precision mediump float;
            attribute vec2 a_origin;
            attribute vec2 a_vector;
            attribute vec4 a_color;
            attribute vec4 a_particle;
            attribute vec4 a_timing;
            uniform vec2 u_resolution;
            uniform float u_time;
            uniform float u_dpr;
            varying vec4 v_color;
            varying float v_core;
            const float PI = 3.14159265359;

            float saturate(float value) {
              return clamp(value, 0.0, 1.0);
            }

            float fadeLoop(float p) {
              return smoothstep(0.0, 0.16, p) * (1.0 - smoothstep(0.78, 1.0, p));
            }

            void main() {
              float duration = max(120.0, a_timing.x);
              float p = fract(((u_time * 1000.0) + a_timing.y) / duration);
              float motion = a_particle.w;
              float pulse = 0.5 + 0.5 * sin((p + a_particle.z * 0.013) * PI * 2.0);
              float alpha = a_particle.y * (0.62 + pulse * 0.38);
              float scale = 0.82 + pulse * 0.44;
              vec2 pos = a_origin + a_vector;

              if (motion > 0.5 && motion < 1.5) {
                vec2 path = a_vector;
                vec2 normal = normalize(vec2(-path.y, path.x) + vec2(0.0001, 0.0001));
                pos = a_origin + path * p + normal * a_timing.z * mix(1.0, -0.45, p);
                alpha = a_particle.y * fadeLoop(p);
                scale = 1.25;
              } else if (motion > 1.5 && motion < 2.5) {
                pos = a_origin + a_vector + vec2(mix(a_timing.w * -0.4, a_timing.w, p), mix(a_timing.z * -0.65, a_timing.z, p));
                alpha = a_particle.y * fadeLoop(p);
                scale = mix(0.72, 1.18, p);
              } else if (motion > 2.5 && motion < 4.5) {
                float spin = a_timing.w < 0.0 ? -1.0 : 1.0;
                float rise = abs(a_timing.w);
                float angle = a_timing.z + spin * p * PI * 2.0;
                pos = a_origin + vec2(cos(angle) * a_vector.x, sin(angle) * a_vector.y * -1.0 - sin(p * PI) * rise);
                alpha = a_particle.y * (0.54 + pulse * 0.46);
                scale = 0.84 + pulse * 0.28;
              } else if (motion > 4.5) {
                float expansion = max(0.3, a_timing.w);
                float eased = smoothstep(0.0, 1.0, p);
                float ring = mix(0.22, expansion, eased);
                pos = a_origin + vec2(cos(a_timing.z) * a_vector.x * ring, sin(a_timing.z) * a_vector.y * -ring);
                alpha = a_particle.y * fadeLoop(p);
                scale = mix(0.44, 1.28, eased);
              }

              vec2 clip = (pos / max(vec2(1.0), u_resolution)) * 2.0 - 1.0;
              gl_Position = vec4(clip.x, -clip.y, 0.0, 1.0);
              gl_PointSize = max(2.0, a_particle.x * scale * u_dpr);
              v_color = vec4(a_color.rgb, a_color.a * alpha);
              v_core = motion > 2.5 && motion < 3.5 ? 0.62 : 0.44;
            }`;

          const fragmentSource = `
            precision mediump float;
            varying vec4 v_color;
            varying float v_core;

            void main() {
              vec2 coord = gl_PointCoord * 2.0 - 1.0;
              float radius = dot(coord, coord);
              float soft = smoothstep(1.0, v_core, radius);
              float core = smoothstep(0.34, 0.0, radius);
              float alpha = (1.0 - soft) * v_color.a;
              vec3 color = v_color.rgb + core * 0.38;
              if (alpha <= 0.01) discard;
              gl_FragColor = vec4(color, alpha);
            }`;

          this.program = sceneWebglProgram(this.gl, vertexSource, fragmentSource);
          this.locations = {
            origin: this.gl.getAttribLocation(this.program, "a_origin"),
            vector: this.gl.getAttribLocation(this.program, "a_vector"),
            color: this.gl.getAttribLocation(this.program, "a_color"),
            particle: this.gl.getAttribLocation(this.program, "a_particle"),
            timing: this.gl.getAttribLocation(this.program, "a_timing"),
            resolution: this.gl.getUniformLocation(this.program, "u_resolution"),
            time: this.gl.getUniformLocation(this.program, "u_time"),
            dpr: this.gl.getUniformLocation(this.program, "u_dpr")
          };
        }

        emitterKey(object) {
          return String(object?.id || object?.props?.label || "particle-emitter");
        }

        emitterProjectionState(object, scene, projected) {
          const width = Math.max(1, Number(projected.width) || Number(object.width) || 1);
          const height = Math.max(1, Number(projected.height) || Number(object.height) || 1);
          const motionName = String(object.props?.motion || "orbit");
          const projection = sceneProjection(scene);
          let originX = projected.left + width / 2;
          let originY = projected.top + height / 2;
          let pathX = 0;
          let pathY = 0;

          if (projected.anchor === "linked-spell-path" && Number.isFinite(projected.sourceLeft) && Number.isFinite(projected.sourceTop)) {
            originX = Number(projected.sourceLeft);
            originY = Number(projected.sourceTop);
          } else if (projection === "isometric") {
            originX = Number(projected.left) || 0;
            originY = (Number(projected.top) || 0) - height * 0.32;
          }

          if (motionName === "spell-bolt") {
            const targetX = Number(projected.targetLeft);
            const targetY = Number(projected.targetTop);
            pathX = Number.isFinite(targetX) ? targetX - originX : width;
            pathY = Number.isFinite(targetY) ? targetY - originY : 0;
          }

          return {width, height, originX, originY, pathX, pathY, motionName};
        }

        addParticle(originX, originY, vectorX, vectorY, color, alpha, size, seed, motion, duration, delay, paramA = 0, paramB = 0) {
          this.particles.push(
            originX, originY,
            vectorX, vectorY,
            color.r, color.g, color.b, 1,
            size, alpha, seed, motion,
            duration, delay, paramA, paramB
          );
        }

        addEmitter(object, scene, projected) {
          const startParticle = this.particles.length / this.strideFloats;
          const color = sceneColorRgb(object.props?.color);
          const baseCount = Math.round(numericSceneProp(object.props?.particleCount, 32, 4, 300));
          const count = scaledParticleCount(scene, baseCount);
          const effectScale = particleEffectScale(scene);
          const size = numericSceneProp(object.props?.particleSize, 5, 2, 18) * Math.min(1.7, 0.88 + effectScale.intensity * 0.16);
          const spread = numericSceneProp(object.props?.spread, 1, 0.2, 2.8);
          const {width, height, originX, originY, pathX, pathY, motionName} = this.emitterProjectionState(object, scene, projected);
          const seed = Math.abs(particleHash(object.id || object.props?.label || "particle-emitter"));
          const orbitRadius = numericSceneProp(object.props?.orbitRadius, Math.min(width, height) * 0.38, 8, 220);
          const verticalLift = numericSceneProp(object.props?.verticalLift, height * 0.32, 0, 220);
          const pulseDelay = numericSceneProp(object.props?.pulseDelay, 0, -10000, 10000);
          const alphaScale = effectScale.alpha;
          const registerEmitter = () => {
            this.emitters.set(this.emitterKey(object), {
              startParticle,
              particleCount: count,
              motionName
            });
          };

          if (motionName === "spell-bolt") {
            for (let index = 0; index < count; index += 1) {
              const particleSize = Math.max(2, size * (0.78 + ((seed + index * 19) % 6) / 16)) * 2.1;
              const duration = 980 + ((seed + index * 71) % 860);
              const delay = -((seed + index * 137) % duration) + pulseDelay;
              const lane = (((index % 7) - 3) * 3.2 * spread);
              const alpha = Math.min(1, (0.5 + ((index % 9) / 18)) * alphaScale);
              this.addParticle(originX, originY, pathX, pathY, color, alpha, particleSize, seed + index, 1, duration, delay, lane, 0);
            }
            registerEmitter();
            return;
          }

          if (motionName === "starfall") {
            for (let index = 0; index < count; index += 1) {
              const x = (((seed + index * 61) % 1000) / 1000 - 0.5) * width * spread;
              const y = (((seed + index * 37) % 1000) / 1000 - 0.5) * height * 0.38;
              const fall = verticalLift * (0.72 + ((index % 9) / 10));
              const drift = (((index % 7) - 3) * 8 * spread);
              const particleSize = Math.max(2, size * (0.8 + ((seed + index * 13) % 7) / 18));
              const duration = 1500 + ((seed + index * 109) % 1900);
              const delay = -((seed + index * 151) % duration) + pulseDelay;
              const alpha = Math.min(1, (0.38 + ((index % 8) / 12)) * alphaScale);
              this.addParticle(originX, originY, x, y, color, alpha, particleSize, seed + index, 2, duration, delay, fall, drift);
            }
            return;
          }

          const orbitMotions = new Set(["spell-swirl", "rune-ring", "orbit"]);
          for (let index = 0; index < count; index += 1) {
            const particleSize = Math.max(2, size * (0.72 + ((seed + index * 17) % 7) / 18));
            const duration = motionName === "impact-burst"
              ? 900 + ((seed + index * 83) % 900)
              : motionName === "nova-ring" || motionName === "shockwave-ring"
                ? 1700 + ((seed + index * 73) % 1300)
                : 1400 + ((seed + index * 113) % 2200);
            const delay = -((seed + index * 89) % duration) + pulseDelay;
            const alpha = Math.min(1, (0.42 + (((seed + index * 31) % 46) / 100)) * alphaScale);
            if (orbitMotions.has(motionName) || motionName === "impact-burst" || motionName === "nova-ring" || motionName === "shockwave-ring") {
              const angleRad = (index * (Math.PI * 2 / Math.max(1, count)) + (seed % 360) * (Math.PI / 180));
              const lane = motionName === "impact-burst"
                ? 0.5 + ((index % 9) * 0.08)
                : motionName === "nova-ring" || motionName === "shockwave-ring"
                  ? 0.58 + ((index % 11) * 0.06)
                  : 0.68 + ((index % 5) * 0.09);
              const radiusX = orbitRadius * spread * lane;
              const radiusY = (motionName === "rune-ring" || motionName === "shockwave-ring" ? orbitRadius * 0.26 : orbitRadius * 0.48) * spread * lane;
              if (motionName === "impact-burst" || motionName === "nova-ring" || motionName === "shockwave-ring") {
                const expansion = motionName === "impact-burst"
                  ? 1.02 + ((index % 8) / 12)
                  : 1.12 + ((index % 9) / 12);
                this.addParticle(originX, originY, radiusX, radiusY, color, alpha, particleSize * 1.15, seed + index, 5, duration, delay, angleRad, expansion);
              } else {
                const rise = verticalLift * (0.24 + (index % 7) / 9) * (index % 2 ? -1 : 1);
                this.addParticle(originX, originY, radiusX, radiusY, color, alpha, particleSize, seed + index, motionName === "rune-ring" ? 3 : 4, duration, delay, angleRad, rise);
              }
            } else {
              const angle = (index * 137.508 + seed) * (Math.PI / 180);
              const radius = Math.sqrt((index + 1) / count) * spread;
              const x = Math.cos(angle) * width * 0.42 * radius;
              const y = Math.sin(angle) * height * 0.42 * radius;
              this.addParticle(originX, originY, x, y, color, alpha, particleSize, seed + index, 0, duration, delay, 0, 0);
            }
          }
          registerEmitter();
        }

        updateEmitter(object, scene, projected) {
          if (this.disposed) return false;
          const emitter = this.emitters.get(this.emitterKey(object));
          if (!emitter || emitter.particleCount <= 0) return false;
          const {originX, originY, pathX, pathY, motionName} = this.emitterProjectionState(object, scene, projected);
          const startFloat = emitter.startParticle * this.strideFloats;
          const endFloat = startFloat + emitter.particleCount * this.strideFloats;
          const data = this.data && this.data.length === this.particles.length ? this.data : null;
          let changed = false;
          for (let offset = startFloat; offset < endFloat; offset += this.strideFloats) {
            if (this.particles[offset] !== originX) {
              this.particles[offset] = originX;
              if (data) data[offset] = originX;
              changed = true;
            }
            if (this.particles[offset + 1] !== originY) {
              this.particles[offset + 1] = originY;
              if (data) data[offset + 1] = originY;
              changed = true;
            }
            if (motionName === "spell-bolt") {
              if (this.particles[offset + 2] !== pathX) {
                this.particles[offset + 2] = pathX;
                if (data) data[offset + 2] = pathX;
                changed = true;
              }
              if (this.particles[offset + 3] !== pathY) {
                this.particles[offset + 3] = pathY;
                if (data) data[offset + 3] = pathY;
                changed = true;
              }
            }
          }
          if (!changed) return false;
          if (data) {
            this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffer);
            this.gl.bufferSubData(
              this.gl.ARRAY_BUFFER,
              startFloat * this.floatSize,
              data.subarray(startFloat, endFloat)
            );
          } else if (this.particleCount > 0) {
            this.upload();
          }
          this.canvas.dataset.webglParticleLastUpdate = String(Date.now());
          return true;
        }

        upload() {
          const gl = this.gl;
          this.data = new Float32Array(this.particles);
          this.particleCount = this.data.length / this.strideFloats;
          gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
          gl.bufferData(gl.ARRAY_BUFFER, this.data, gl.STATIC_DRAW);
          this.canvas.dataset.webglParticleCount = String(this.particleCount);
        }

        resize() {
          if (this.disposed) return;
          const rect = this.canvas.getBoundingClientRect();
          const dpr = Math.max(1, Math.min(this.maxDpr, window.devicePixelRatio || 1));
          const width = Math.max(1, Math.round((rect.width || this.canvas.clientWidth || 1) * dpr));
          const height = Math.max(1, Math.round((rect.height || this.canvas.clientHeight || 1) * dpr));
          if (this.canvas.width !== width || this.canvas.height !== height) {
            this.canvas.width = width;
            this.canvas.height = height;
          }
          this.dpr = dpr;
          this.gl.viewport(0, 0, width, height);
        }

        bindAttributes() {
          const gl = this.gl;
          const {locations} = this;
          gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
          const attrs = [
            [locations.origin, 2, 0],
            [locations.vector, 2, 2],
            [locations.color, 4, 4],
            [locations.particle, 4, 8],
            [locations.timing, 4, 12]
          ];
          attrs.forEach(([location, size, offsetFloats]) => {
            if (location < 0) return;
            gl.enableVertexAttribArray(location);
            gl.vertexAttribPointer(location, size, gl.FLOAT, false, this.strideBytes, offsetFloats * this.floatSize);
          });
        }

        render(now = performance.now()) {
          if (this.disposed) return;
          const gl = this.gl;
          this.resize();
          gl.clearColor(0, 0, 0, 0);
          gl.clear(gl.COLOR_BUFFER_BIT);
          if (this.particleCount > 0) {
            gl.useProgram(this.program);
            this.bindAttributes();
            gl.uniform2f(this.locations.resolution, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
            gl.uniform1f(this.locations.time, (now - this.startedAt) / 1000);
            gl.uniform1f(this.locations.dpr, this.dpr);
            gl.disable(gl.DEPTH_TEST);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
            gl.drawArrays(gl.POINTS, 0, this.particleCount);
          }
          this.animationFrame = requestAnimationFrame((nextNow) => this.render(nextNow));
        }

        start() {
          if (this.disposed) return;
          this.upload();
          this.startedAt = performance.now();
          this.render(this.startedAt);
        }

        dispose() {
          if (this.disposed) return;
          this.disposed = true;
          if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
          this.resizeObserver?.disconnect?.();
          try {
            this.gl?.deleteBuffer?.(this.buffer);
            this.gl?.deleteProgram?.(this.program);
          } catch (error) {
            // Context may already be lost; disposal is best-effort.
          }
        }
      }

      function createSceneWebglParticleLayer(container, scene, options = {}) {
        if (!sceneWebglParticlesRequested(scene, options)) return null;
        if (typeof document === "undefined") return null;
        const hasParticleEmitters = Array.isArray(scene?.objects) && scene.objects.some((object) => object?.type === "particle-emitter");
        if (!hasParticleEmitters) return null;
        const canvas = document.createElement("canvas");
        canvas.className = "scene-webgl-particle-canvas";
        canvas.dataset.sceneParticleRenderer = "webgl";
        canvas.setAttribute("aria-hidden", "true");
        try {
          const layer = new SceneWebglParticleLayer(canvas);
          container.append(canvas);
          container.dataset.sceneParticleRenderer = "webgl";
          return layer;
        } catch (error) {
          canvas.remove();
          container.dataset.sceneParticleRenderer = "dom";
          return null;
        }
      }

      function renderWebglParticleEmitterMarker(element, object, scene, projected, particleLayer) {
        element.classList.add("scene-object--particle-emitter", "scene-object--particle-emitter-webgl");
        element.dataset.sceneParticleEmitter = "true";
        element.dataset.sceneParticleRenderer = "webgl";
        element.dataset.particleMotion = String(object.props?.motion || "orbit");
        if (object.parentId || object.props?.parentId) element.dataset.parentedParticle = "true";
        if (object.props?.targetId) element.dataset.targetParticle = String(object.props.targetId);
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Particle Emitter"));
        const color = normalizeSceneColor(object.props?.color, "#7dd3fc");
        const baseCount = Math.round(numericSceneProp(object.props?.particleCount, 32, 4, 300));
        const count = scaledParticleCount(scene, baseCount);
        const effectScale = particleEffectScale(scene);
        element.style.setProperty("--mint", color);
        element.style.setProperty("--particle-color", color);
        element.style.setProperty("--scene-effect-intensity", effectScale.intensity.toFixed(2));
        element.style.setProperty("--scene-effect-glow", effectScale.glow.toFixed(2));
        element.style.setProperty("--scene-effect-alpha", effectScale.alpha.toFixed(2));
        element.dataset.particleCount = String(count);
        element.dataset.baseParticleCount = String(baseCount);
        if (sceneProjection(scene) === "isometric" && String(object.props?.motion || "orbit") !== "spell-bolt") {
          element.style.transform = "translate(-50%, -82%)";
        }
        particleLayer?.addEmitter?.(object, scene, projected);
      }


      function gpuForgePlaybackMode(atlas, object) {
        return String(atlas?.playback || object?.props?.gpuForgePlayback || "sprite-sheet").trim().toLowerCase() || "sprite-sheet";
      }

      function renderGpuForgeStormLash(element, object, atlas, asset, playbackState) {
        const frameCount = playbackState?.frameCount || 12;
        const durationMs = playbackState?.durationMs || 1480;
        element.dataset.gpuForgeAtlas = "true";
        element.dataset.gpuForgePlayback = "storm-lash";
        element.dataset.gpuForgeBackend = String(atlas?.backend || "");
        element.classList.add("scene-object--gpu-forge-storm-lash");
        element.style.setProperty("--gpu-forge-frames", String(frameCount));
        element.style.setProperty("--gpu-forge-duration", `${durationMs}ms`);
        element.style.setProperty("--gpu-forge-columns", String(Math.max(1, Math.round(Number(atlas?.columns || frameCount) || frameCount))));
        element.style.setProperty("--gpu-forge-frame-width", `${Math.max(1, Math.round(Number(atlas?.frameWidth || 128) || 128))}px`);
        element.style.setProperty("--gpu-forge-frame-height", `${Math.max(1, Math.round(Number(atlas?.frameHeight || 128) || 128))}px`);

        const lash = document.createElement("span");
        lash.className = "scene-gpu-forge-storm-lash";
        lash.setAttribute("aria-hidden", "true");

        const wake = document.createElement("span");
        wake.className = "scene-gpu-forge-storm-lash__wake";

        const rail = document.createElement("span");
        rail.className = "scene-gpu-forge-storm-lash__rail";

        const texture = document.createElement("span");
        texture.className = "scene-gpu-forge-storm-lash__texture scene-gpu-forge-atlas";
        texture.style.backgroundImage = `url("${asset.url}")`;
        texture.style.setProperty("--gpu-forge-frames", String(frameCount));
        texture.style.setProperty("--gpu-forge-duration", `${durationMs}ms`);
        texture.style.setProperty("--gpu-forge-columns", String(Math.max(1, Math.round(Number(atlas?.columns || frameCount) || frameCount))));

        const head = document.createElement("span");
        head.className = "scene-gpu-forge-storm-lash__head";

        const fangs = document.createElement("span");
        fangs.className = "scene-gpu-forge-storm-lash__fangs";

        const impact = document.createElement("span");
        impact.className = "scene-gpu-forge-storm-lash__impact";

        const slash = document.createElement("span");
        slash.className = "scene-gpu-forge-storm-lash__impact-slashes";

        const runes = document.createElement("span");
        runes.className = "scene-gpu-forge-storm-lash__runes";
        for (let index = 0; index < 9; index += 1) {
          const rune = document.createElement("span");
          rune.className = "scene-gpu-forge-storm-lash__rune";
          rune.style.setProperty("--storm-rune-index", String(index));
          rune.style.setProperty("--storm-rune-at", `${12 + index * 9}%`);
          rune.style.setProperty("--storm-rune-y", `${index % 2 ? -18 - index : 16 + index}px`);
          rune.style.setProperty("--storm-rune-delay", `${-durationMs + index * 115}ms`);
          runes.append(rune);
        }

        impact.append(slash);
        lash.append(wake, rail, texture, runes, head, fangs, impact);
        element.append(lash);
      }


      function renderParticleEmitter(element, object, scene, options = {}) {
        element.classList.add("scene-object--particle-emitter");
        element.dataset.sceneParticleEmitter = "true";
        element.dataset.particleMotion = String(object.props?.motion || "orbit");
        if (object.parentId || object.props?.parentId) element.dataset.parentedParticle = "true";
        if (object.props?.targetId) element.dataset.targetParticle = String(object.props.targetId);
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Particle Emitter"));
        const color = normalizeSceneColor(object.props?.color, "#7dd3fc");
        const baseCount = Math.round(numericSceneProp(object.props?.particleCount, 32, 4, 300));
        const count = scaledParticleCount(scene, baseCount);
        const effectScale = particleEffectScale(scene);
        const size = numericSceneProp(object.props?.particleSize, 5, 2, 18) * Math.min(1.7, 0.88 + effectScale.intensity * 0.16);
        const spread = numericSceneProp(object.props?.spread, 1, 0.2, 2.8);
        const width = Math.max(1, Number(object.width) || 1);
        const height = Math.max(1, Number(object.height) || 1);
        const seed = Math.abs(particleHash(object.id || object.props?.label || "particle-emitter"));
        const projection = sceneProjection(scene);
        const motion = String(object.props?.motion || "orbit");
        const orbitRadius = numericSceneProp(object.props?.orbitRadius, Math.min(width, height) * 0.38, 8, 220);
        const verticalLift = numericSceneProp(object.props?.verticalLift, height * 0.32, 0, 220);
        const pulseDelay = numericSceneProp(object.props?.pulseDelay, 0, -10000, 10000);
        const field = document.createElement("span");
        field.className = "scene-particle-field";
        field.dataset.particleMotion = motion;
        field.setAttribute("aria-hidden", "true");
        field.style.setProperty("--particle-color", color);
        field.style.setProperty("--particle-pulse-delay", `${pulseDelay}ms`);
        field.style.setProperty("--scene-effect-intensity", effectScale.intensity.toFixed(2));
        field.style.setProperty("--scene-effect-glow", effectScale.glow.toFixed(2));
        field.style.setProperty("--scene-effect-alpha", effectScale.alpha.toFixed(2));
        field.dataset.particleCount = String(count);
        field.dataset.baseParticleCount = String(baseCount);
        element.style.setProperty("--mint", color);
        element.style.setProperty("--scene-effect-intensity", effectScale.intensity.toFixed(2));
        element.style.setProperty("--scene-effect-glow", effectScale.glow.toFixed(2));
        element.style.setProperty("--scene-effect-alpha", effectScale.alpha.toFixed(2));
        element.style.color = color;
        if (projection === "isometric" && motion !== "spell-bolt") {
          element.style.transform = "translate(-50%, -82%)";
        }
        const forgeAtlas = sceneObjectGpuForgeAtlas(object, options);
        if (forgeAtlas?.asset?.url) {
          const atlas = forgeAtlas.atlas || {};
          const playback = gpuForgePlaybackMode(atlas, object);
          const fallbackFrameCount = playback === "storm-lash" ? 12 : 8;
          const fallbackDuration = playback === "storm-lash" ? 1480 : 960;
          const frameCount = Math.max(1, Math.round(Number(atlas.frameCount || atlas.columns || fallbackFrameCount) || fallbackFrameCount));
          const durationMs = Math.max(220, Math.round(Number(atlas.durationMs || object.props?.durationMs || fallbackDuration) || fallbackDuration));
          if (playback === "storm-lash") {
            renderGpuForgeStormLash(element, object, atlas, forgeAtlas.asset, {frameCount, durationMs});
            return;
          }
          element.dataset.gpuForgeAtlas = "true";
          element.dataset.gpuForgePlayback = playback;
          element.dataset.gpuForgeBackend = String(atlas.backend || "");
          const sheet = document.createElement("span");
          sheet.className = "scene-gpu-forge-atlas";
          sheet.setAttribute("aria-hidden", "true");
          sheet.style.backgroundImage = `url("${forgeAtlas.asset.url}")`;
          sheet.style.setProperty("--gpu-forge-frames", String(frameCount));
          sheet.style.setProperty("--gpu-forge-duration", `${durationMs}ms`);
          sheet.style.setProperty("--gpu-forge-columns", String(Math.max(1, Math.round(Number(atlas.columns || frameCount) || frameCount))));
          element.append(sheet);
          return;
        }
        if (motion === "spell-bolt") {
          field.classList.add("scene-particle-field--linked-spell");
          for (let index = 0; index < count; index += 1) {
            const particle = document.createElement("span");
            particle.className = "scene-particle scene-particle--bolt";
            const particleSize = Math.max(2, size * (0.78 + ((seed + index * 19) % 6) / 16));
            const duration = 980 + ((seed + index * 71) % 860);
            const delay = -((seed + index * 137) % duration) + pulseDelay;
            const lane = (((index % 7) - 3) * 3.2 * spread);
            const progress = (index % count) / Math.max(1, count - 1);
            particle.style.setProperty("--particle-size", `${particleSize.toFixed(2)}px`);
            particle.style.setProperty("--particle-alpha", `${(0.5 + ((index % 9) / 18)).toFixed(2)}`);
            particle.style.setProperty("--particle-duration", `${duration}ms`);
            particle.style.setProperty("--particle-delay", `${delay}ms`);
            particle.style.setProperty("--particle-lane", `${lane.toFixed(2)}px`);
            particle.style.setProperty("--particle-progress", progress.toFixed(3));
            field.append(particle);
          }
          element.append(field);
          return;
        }
        if (motion === "starfall") {
          field.classList.add("scene-particle-field--starfall");
          for (let index = 0; index < count; index += 1) {
            const particle = document.createElement("span");
            particle.className = "scene-particle scene-particle--starfall";
            const x = (((seed + index * 61) % 1000) / 1000 - 0.5) * width * spread;
            const y = (((seed + index * 37) % 1000) / 1000 - 0.5) * height * 0.38;
            const fall = verticalLift * (0.72 + ((index % 9) / 10));
            const drift = (((index % 7) - 3) * 8 * spread);
            const particleSize = Math.max(2, size * (0.8 + ((seed + index * 13) % 7) / 18));
            const duration = 1500 + ((seed + index * 109) % 1900);
            const delay = -((seed + index * 151) % duration) + pulseDelay;
            particle.style.setProperty("--particle-x", `${x.toFixed(2)}px`);
            particle.style.setProperty("--particle-y", `${y.toFixed(2)}px`);
            particle.style.setProperty("--particle-fall", `${fall.toFixed(2)}px`);
            particle.style.setProperty("--particle-drift", `${drift.toFixed(2)}px`);
            particle.style.setProperty("--particle-size", `${particleSize.toFixed(2)}px`);
            particle.style.setProperty("--particle-duration", `${duration}ms`);
            particle.style.setProperty("--particle-delay", `${delay}ms`);
            particle.style.setProperty("--particle-alpha", `${(0.38 + ((index % 8) / 12)).toFixed(2)}`);
            field.append(particle);
          }
          element.append(field);
          return;
        }
        const orbitMotions = new Set(["spell-swirl", "rune-ring", "impact-burst", "nova-ring", "shockwave-ring"]);
        for (let index = 0; index < count; index += 1) {
          const particleSize = Math.max(2, size * (0.72 + ((seed + index * 17) % 7) / 18));
          const duration = motion === "impact-burst"
            ? 900 + ((seed + index * 83) % 900)
            : motion === "nova-ring" || motion === "shockwave-ring"
              ? 1700 + ((seed + index * 73) % 1300)
              : 1400 + ((seed + index * 113) % 2200);
          const delay = -((seed + index * 89) % duration) + pulseDelay;
          const alpha = 0.42 + (((seed + index * 31) % 46) / 100);
          if (orbitMotions.has(motion)) {
            const orbit = document.createElement("span");
            orbit.className = `scene-particle-orbit scene-particle-orbit--${motion}`;
            const angleDeg = (index * (360 / count) + (seed % 360));
            const lane = motion === "impact-burst"
              ? 0.5 + ((index % 9) * 0.08)
              : motion === "nova-ring" || motion === "shockwave-ring"
                ? 0.58 + ((index % 11) * 0.06)
                : 0.68 + ((index % 5) * 0.09);
            const radiusX = orbitRadius * spread * lane;
            const radiusY = (motion === "rune-ring" || motion === "shockwave-ring" ? orbitRadius * 0.26 : orbitRadius * 0.48) * spread * lane;
            orbit.style.setProperty("--particle-angle", `${angleDeg.toFixed(2)}deg`);
            orbit.style.setProperty("--particle-radius-x", `${radiusX.toFixed(2)}px`);
            orbit.style.setProperty("--particle-radius-y", `${radiusY.toFixed(2)}px`);
            orbit.style.setProperty("--particle-rise", `${(verticalLift * (0.24 + (index % 7) / 9)).toFixed(2)}px`);
            orbit.style.setProperty("--particle-duration", `${duration}ms`);
            orbit.style.setProperty("--particle-delay", `${delay}ms`);
            orbit.style.setProperty("--particle-spin", index % 2 ? "-1" : "1");
            orbit.style.setProperty("--particle-phase", `${((index % 8) / 8).toFixed(3)}`);
            orbit.style.setProperty("--particle-expansion", `${(1.12 + ((index % 9) / 12)).toFixed(2)}`);
            const particle = document.createElement("span");
            particle.className = `scene-particle scene-particle--${motion}`;
            particle.style.setProperty("--particle-size", `${particleSize.toFixed(2)}px`);
            particle.style.setProperty("--particle-alpha", alpha.toFixed(2));
            orbit.append(particle);
            field.append(orbit);
          } else {
            const particle = document.createElement("span");
            particle.className = "scene-particle";
            const angle = (index * 137.508 + seed) * (Math.PI / 180);
            const radius = Math.sqrt((index + 1) / count) * spread;
            const x = Math.cos(angle) * width * 0.42 * radius;
            const y = Math.sin(angle) * height * 0.42 * radius;
            particle.style.setProperty("--particle-x", `${x.toFixed(2)}px`);
            particle.style.setProperty("--particle-y", `${y.toFixed(2)}px`);
            particle.style.setProperty("--particle-size", `${particleSize.toFixed(2)}px`);
            particle.style.setProperty("--particle-duration", `${duration}ms`);
            particle.style.setProperty("--particle-delay", `${delay}ms`);
            particle.style.setProperty("--particle-alpha", alpha.toFixed(2));
            field.append(particle);
          }
        }
        element.append(field);
      }

      function spriteSeries(object) {
        const rigFrames = Array.isArray(object?.props?.spriteRig?.castFrames) ? object.props.spriteRig.castFrames : [];
        const frames = Array.isArray(object?.props?.spriteSeries) ? object.props.spriteSeries : rigFrames;
        return frames.length ? frames : ["idle", "step-left", "step-right", "cast"];
      }

      function spriteRigLayers(object) {
        const layers = Array.isArray(object?.props?.spriteRig?.layers) ? object.props.spriteRig.layers : [];
        return layers.length ? layers : ["shadow", "aura", "core", "weapon-trail", "sparkles"];
      }

      function appendSpriteRigLayer(parent, layerName) {
        const clean = String(layerName || "").trim().toLowerCase();
        if (!clean || clean === "shadow") return;
        const layer = document.createElement("span");
        layer.className = `scene-sprite-rig-layer scene-sprite-${clean.replace(/[^a-z0-9]+/g, "-")}`;
        layer.dataset.spriteLayer = clean;
        layer.setAttribute("aria-hidden", "true");
        parent.append(layer);
      }

      function renderSpriteActor(element, object) {
        element.classList.add("scene-object--sprite-actor");
        if (object?.props?.role === "player") element.dataset.scenePlayer = "true";
        element.dataset.spriteSeries = "true";
        element.dataset.spriteRig = String(object?.props?.spriteRig?.style || "energy-silhouette");
        element.dataset.spellState = String(object?.props?.spellState || object?.props?.motion || "idle");
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Sprite Actor"));
        element.style.setProperty("--scene-bob-height", `${numericSceneProp(object?.props?.bob, 8, 0, 24)}px`);

        const shadow = document.createElement("span");
        shadow.className = "scene-sprite-shadow";
        shadow.setAttribute("aria-hidden", "true");

        const body = document.createElement("span");
        body.className = "scene-sprite-body";
        body.setAttribute("aria-hidden", "true");

        spriteRigLayers(object).forEach((layerName) => appendSpriteRigLayer(body, layerName));

        const illustration = document.createElement("span");
        illustration.className = "scene-sprite-illustration scene-sprite-core-layer";
        illustration.setAttribute("aria-hidden", "true");

        const series = document.createElement("span");
        series.className = "scene-sprite-series";
        series.setAttribute("aria-hidden", "true");
        spriteSeries(object).forEach((pose, index) => {
          const frame = document.createElement("span");
          frame.className = "scene-sprite-frame";
          frame.dataset.spritePose = String(pose || `frame-${index + 1}`);
          frame.style.setProperty("--scene-frame-index", String(index));

          const silhouette = document.createElement("span");
          silhouette.className = "scene-sprite-silhouette";
          const spark = document.createElement("span");
          spark.className = "scene-sprite-spark";
          frame.append(silhouette, spark);
          series.append(frame);
        });

        const trail = document.createElement("span");
        trail.className = "scene-sprite-trail";
        trail.setAttribute("aria-hidden", "true");

        body.append(illustration, series, trail);
        element.append(shadow, body);
      }

      function sceneChoreographyBeats(scene) {
        const beats = Array.isArray(scene?.metadata?.choreography?.beats) ? scene.metadata.choreography.beats : [];
        return beats
          .filter((beat) => beat && typeof beat === "object")
          .map((beat, index) => ({
            label: String(beat.label || `Beat ${index + 1}`),
            cue: String(beat.cue || ""),
            timeMs: numericSceneProp(beat.timeMs, index * 1000, 0, 60000)
          }));
      }

      function renderSceneChoreographyOverlay(container, scene) {
        const choreography = scene?.metadata?.choreography;
        const beats = sceneChoreographyBeats(scene);
        if (!choreography || !beats.length) return;
        const duration = numericSceneProp(choreography.durationMs, 6000, 1000, 120000);
        const overlay = document.createElement("div");
        overlay.className = "scene-choreography-overlay";
        overlay.setAttribute("aria-hidden", "true");
        overlay.style.setProperty("--scene-choreo-duration", `${duration}ms`);
        if (choreography.cameraPulse) overlay.dataset.cameraPulse = "true";

        const clock = document.createElement("span");
        clock.className = "scene-cast-clock";
        overlay.append(clock);

        const title = document.createElement("span");
        title.className = "scene-choreography-title";
        title.textContent = String(choreography.title || scene.name || "Spell choreography");
        overlay.append(title);

        const rail = document.createElement("span");
        rail.className = "scene-beat-rail";
        beats.forEach((beat, index) => {
          const marker = document.createElement("span");
          marker.className = "scene-beat-marker";
          marker.dataset.beatCue = beat.cue;
          marker.style.setProperty("--scene-beat-index", String(index));
          marker.style.setProperty("--scene-beat-at", `${Math.min(100, Math.max(0, (beat.timeMs / duration) * 100)).toFixed(2)}%`);
          marker.style.setProperty("--scene-beat-delay", `${beat.timeMs}ms`);
          marker.textContent = beat.label;
          rail.append(marker);
        });
        overlay.append(rail);
        container.append(overlay);
      }


      function shuttle3dCameraConfig(scene) {
        const camera = scene?.metadata?.camera && typeof scene.metadata.camera === "object" ? scene.metadata.camera : {};
        const suppliedPosition = camera.position;
        const position = Array.isArray(suppliedPosition)
          && suppliedPosition.length === 3
          && suppliedPosition.every((value) => Number.isFinite(Number(value)))
          ? suppliedPosition.map(Number)
          : [0, 0.75, 3.35];
        return {
          position,
          yaw: numericSceneProp(camera.yaw, 0, -180, 180),
          pitch: numericSceneProp(camera.pitch, -2, -45, 45),
          yawLimit: numericSceneProp(camera.yawLimit, 180, 8, 180),
          pitchLimit: numericSceneProp(camera.pitchLimit, 28, 4, 60)
        };
      }

      function clampShuttle3dLook(value, limit) {
        const number = Number(value);
        if (!Number.isFinite(number)) return 0;
        return Math.min(limit, Math.max(-limit, number));
      }

      function normalizeShuttle3dYaw(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return 0;
        return ((number + 180) % 360 + 360) % 360 - 180;
      }

      function shuttle3dNormalizeVector(vector) {
        const length = Math.hypot(vector[0], vector[1], vector[2]) || 1;
        return [vector[0] / length, vector[1] / length, vector[2] / length];
      }

      function shuttle3dCross(a, b) {
        return [
          a[1] * b[2] - a[2] * b[1],
          a[2] * b[0] - a[0] * b[2],
          a[0] * b[1] - a[1] * b[0]
        ];
      }

      function shuttle3dSubtract(a, b) {
        return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
      }

      function shuttle3dDot(a, b) {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
      }

      function shuttle3dPerspectiveMatrix(fieldOfViewRadians, aspect, near, far) {
        const f = 1 / Math.tan(fieldOfViewRadians / 2);
        const range = 1 / (near - far);
        return new Float32Array([
          f / Math.max(0.01, aspect), 0, 0, 0,
          0, f, 0, 0,
          0, 0, (far + near) * range, -1,
          0, 0, 2 * far * near * range, 0
        ]);
      }

      function shuttle3dLookAtMatrix(eye, center, up) {
        const zAxis = shuttle3dNormalizeVector(shuttle3dSubtract(eye, center));
        const xAxis = shuttle3dNormalizeVector(shuttle3dCross(up, zAxis));
        const yAxis = shuttle3dCross(zAxis, xAxis);
        return new Float32Array([
          xAxis[0], yAxis[0], zAxis[0], 0,
          xAxis[1], yAxis[1], zAxis[1], 0,
          xAxis[2], yAxis[2], zAxis[2], 0,
          -shuttle3dDot(xAxis, eye), -shuttle3dDot(yAxis, eye), -shuttle3dDot(zAxis, eye), 1
        ]);
      }

      function shuttle3dBoundsVertices(scene) {
        const supplied = scene?.metadata?.shuttle3d?.geometry?.boundsVertices;
        const valid = Array.isArray(supplied)
          && supplied.length === 12
          && supplied.every((vertex) => Array.isArray(vertex) && vertex.length === 3 && vertex.every(Number.isFinite));
        if (valid) return supplied.map((vertex) => vertex.map(Number));
        return [
          [-4.5, -1.45, -8.8],
          [-4.5, 2.05, -8.8],
          [-3.55, 3.15, -8.8],
          [3.55, 3.15, -8.8],
          [4.5, 2.05, -8.8],
          [4.5, -1.45, -8.8],
          [-4.5, -1.45, 6.8],
          [-4.5, 2.05, 6.8],
          [-3.55, 3.15, 6.8],
          [3.55, 3.15, 6.8],
          [4.5, 2.05, 6.8],
          [4.5, -1.45, 6.8]
        ];
      }

      function shuttle3dMovementConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.movement;
        const movement = supplied && typeof supplied === "object" ? supplied : {};
        const camera = shuttle3dCameraConfig(scene);
        const suppliedStart = movement.start;
        const start = Array.isArray(suppliedStart)
          && suppliedStart.length === 3
          && suppliedStart.every((value) => Number.isFinite(Number(value)))
          ? suppliedStart.map(Number)
          : camera.position.slice();
        const suppliedBounds = movement.bounds && typeof movement.bounds === "object" ? movement.bounds : {};
        const number = (value, fallback, minimum, maximum) => {
          const parsed = Number(value);
          if (!Number.isFinite(parsed)) return fallback;
          return Math.min(maximum, Math.max(minimum, parsed));
        };
        const bounds = {
          minX: number(suppliedBounds.minX, -3.92, -20, 20),
          maxX: number(suppliedBounds.maxX, 3.92, -20, 20),
          minZ: number(suppliedBounds.minZ, -7.72, -40, 40),
          maxZ: number(suppliedBounds.maxZ, 5.82, -40, 40)
        };
        if (bounds.minX > bounds.maxX) [bounds.minX, bounds.maxX] = [bounds.maxX, bounds.minX];
        if (bounds.minZ > bounds.maxZ) [bounds.minZ, bounds.maxZ] = [bounds.maxZ, bounds.minZ];
        const colliders = Array.isArray(movement.colliders)
          ? movement.colliders
              .filter((collider) => collider && typeof collider === "object")
              .map((collider, index) => ({
                id: String(collider.id || `fixture-${index + 1}`),
                minX: number(collider.minX, 0, -20, 20),
                maxX: number(collider.maxX, 0, -20, 20),
                minZ: number(collider.minZ, 0, -40, 40),
                maxZ: number(collider.maxZ, 0, -40, 40)
              }))
              .map((collider) => ({
                ...collider,
                minX: Math.min(collider.minX, collider.maxX),
                maxX: Math.max(collider.minX, collider.maxX),
                minZ: Math.min(collider.minZ, collider.maxZ),
                maxZ: Math.max(collider.minZ, collider.maxZ)
              }))
          : [];
        return {
          enabled: movement.enabled !== false,
          start,
          eyeHeight: number(movement.eyeHeight, start[1], -1.2, 2.8),
          walkSpeed: number(movement.walkSpeed, 2.65, 0.25, 12),
          sprintMultiplier: number(movement.sprintMultiplier, 1.7, 1, 4),
          radius: number(movement.radius, 0.28, 0.08, 1.2),
          bounds,
          colliders
        };
      }

      function shuttle3dStarfieldConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.starfieldSphere;
        const starfield = supplied && typeof supplied === "object" ? supplied : {};
        const number = (value, fallback, minimum, maximum) => {
          const parsed = Number(value);
          if (!Number.isFinite(parsed)) return fallback;
          return Math.min(maximum, Math.max(minimum, parsed));
        };
        const minimumSize = number(starfield.minimumSize, 0.12, 0.02, 2);
        return {
          mode: "camera-centered-sphere",
          radius: number(starfield.radius, 124, 48, 500),
          count: Math.round(number(starfield.count, 420, 24, 2400)),
          seed: Math.floor(number(starfield.seed, 73129, 1, 4294967295)) >>> 0,
          minimumSize,
          maximumSize: Math.max(minimumSize, number(starfield.maximumSize, 0.38, 0.02, 3)),
          fixedDistanceFromCamera: starfield.fixedDistanceFromCamera !== false
        };
      }

      class Shuttle3dGeometryWriter {
        constructor() {
          this.values = [];
        }

        color(value, emissive = false) {
          const rgb = sceneColorRgb(value);
          return [rgb.r, rgb.g, rgb.b, emissive ? 1 : 0];
        }

        normal(a, b, c) {
          return shuttle3dNormalizeVector(shuttle3dCross(shuttle3dSubtract(b, a), shuttle3dSubtract(c, a)));
        }

        vertex(position, normal, color) {
          this.values.push(
            position[0], position[1], position[2],
            normal[0], normal[1], normal[2],
            color[0], color[1], color[2], color[3]
          );
        }

        triangle(a, b, c, color, normal = null) {
          const faceNormal = normal || this.normal(a, b, c);
          this.vertex(a, faceNormal, color);
          this.vertex(b, faceNormal, color);
          this.vertex(c, faceNormal, color);
        }

        quad(a, b, c, d, color, normal = null) {
          const faceNormal = normal || this.normal(a, b, c);
          this.triangle(a, b, c, color, faceNormal);
          this.triangle(a, c, d, color, faceNormal);
        }

        box(minimum, maximum, color) {
          const [x0, y0, z0] = minimum;
          const [x1, y1, z1] = maximum;
          const p000 = [x0, y0, z0];
          const p100 = [x1, y0, z0];
          const p010 = [x0, y1, z0];
          const p110 = [x1, y1, z0];
          const p001 = [x0, y0, z1];
          const p101 = [x1, y0, z1];
          const p011 = [x0, y1, z1];
          const p111 = [x1, y1, z1];
          this.quad(p001, p101, p111, p011, color);
          this.quad(p100, p000, p010, p110, color);
          this.quad(p000, p001, p011, p010, color);
          this.quad(p101, p100, p110, p111, color);
          this.quad(p010, p011, p111, p110, color);
          this.quad(p000, p100, p101, p001, color);
        }

        consoleWedge(centerX, centerZ, width, depth, baseY, frontY, backY, color) {
          const left = centerX - width / 2;
          const right = centerX + width / 2;
          const front = centerZ + depth / 2;
          const back = centerZ - depth / 2;
          const a = [left, baseY, front];
          const b = [right, baseY, front];
          const c = [right, baseY, back];
          const d = [left, baseY, back];
          const e = [left, frontY, front];
          const f = [right, frontY, front];
          const g = [right, backY, back];
          const h = [left, backY, back];
          this.quad(a, b, f, e, color);
          this.quad(b, c, g, f, color);
          this.quad(c, d, h, g, color);
          this.quad(d, a, e, h, color);
          this.quad(e, f, g, h, color);
          this.quad(d, c, b, a, color);
        }

        ellipsoid(center, radii, segments, rings, color) {
          for (let ring = 0; ring < rings; ring += 1) {
            const v0 = ring / rings;
            const v1 = (ring + 1) / rings;
            const phi0 = -Math.PI / 2 + v0 * Math.PI;
            const phi1 = -Math.PI / 2 + v1 * Math.PI;
            for (let segment = 0; segment < segments; segment += 1) {
              const u0 = segment / segments;
              const u1 = (segment + 1) / segments;
              const theta0 = u0 * Math.PI * 2;
              const theta1 = u1 * Math.PI * 2;
              const point = (theta, phi) => [
                center[0] + Math.cos(phi) * Math.cos(theta) * radii[0],
                center[1] + Math.sin(phi) * radii[1],
                center[2] + Math.cos(phi) * Math.sin(theta) * radii[2]
              ];
              const p00 = point(theta0, phi0);
              const p10 = point(theta1, phi0);
              const p11 = point(theta1, phi1);
              const p01 = point(theta0, phi1);
              this.quad(p00, p10, p11, p01, color);
            }
          }
        }

        toFloat32Array() {
          return new Float32Array(this.values);
        }
      }

      class Shuttle3dVertexRenderer {
        constructor(canvas, scene) {
          this.canvas = canvas;
          this.scene = scene;
          this.gl = canvas.getContext("webgl", {
            alpha: false,
            antialias: true,
            depth: true,
            preserveDrawingBuffer: false,
            premultipliedAlpha: false
          }) || canvas.getContext("experimental-webgl", {
            alpha: false,
            antialias: true,
            depth: true,
            preserveDrawingBuffer: false,
            premultipliedAlpha: false
          });
          if (!this.gl) throw new Error("WebGL is unavailable for the shuttle vertex renderer.");
          this.disposed = false;
          this.animationFrame = 0;
          this.look = {yaw: 0, pitch: -2};
          this.movement = shuttle3dMovementConfig(scene);
          this.camera = this.movement.start.slice();
          this.camera[1] = this.movement.eyeHeight;
          this.movementKeys = new Set();
          this.lastFrameTime = null;
          this.onCameraMoved = null;
          this.maxDpr = 2;
          this.compile();
          this.starfield = shuttle3dStarfieldConfig(scene);
          this.geometry = this.buildGeometry();
          this.worldVertexCount = this.geometry.length / 10;
          this.starGeometry = this.buildStarfieldGeometry();
          this.starVertexCount = this.starGeometry.length / 10;
          this.vertexCount = this.worldVertexCount + this.starVertexCount;
          this.buffer = this.gl.createBuffer();
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.geometry, this.gl.STATIC_DRAW);
          this.starBuffer = this.gl.createBuffer();
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.starBuffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.starGeometry, this.gl.STATIC_DRAW);
          this.resizeObserver = typeof ResizeObserver === "function"
            ? new ResizeObserver(() => this.resize())
            : null;
          this.resizeObserver?.observe?.(canvas);
          canvas.addEventListener("webglcontextlost", this.handleContextLost = (event) => {
            event.preventDefault();
            this.dispose();
          });
          this.resize();
          this.draw = this.draw.bind(this);
          this.animationFrame = requestAnimationFrame(this.draw);
        }

        compile() {
          const vertexSource = `
            precision mediump float;
            attribute vec3 a_position;
            attribute vec3 a_normal;
            attribute vec4 a_color;
            uniform mat4 u_projection;
            uniform mat4 u_view;
            uniform vec3 u_camera;
            uniform vec3 u_offset;
            uniform float u_time;
            varying vec3 v_color;
            varying float v_emissive;
            varying float v_depth;

            void main() {
              vec3 worldPosition = a_position + u_offset;
              vec3 lightDirection = normalize(vec3(-0.35, 0.82, 0.46));
              float diffuse = 0.34 + 0.66 * abs(dot(normalize(a_normal), lightDirection));
              float pulse = 0.88 + 0.12 * sin(u_time * 1.8 + a_position.x * 0.7 + a_position.z * 0.15);
              float light = mix(diffuse, pulse, a_color.a);
              v_color = a_color.rgb * light;
              v_emissive = a_color.a;
              v_depth = length(worldPosition - u_camera);
              gl_Position = u_projection * u_view * vec4(worldPosition, 1.0);
            }`;

          const fragmentSource = `
            precision mediump float;
            varying vec3 v_color;
            varying float v_emissive;
            varying float v_depth;

            void main() {
              float fog = smoothstep(34.0, 105.0, v_depth) * (1.0 - v_emissive * 0.72);
              vec3 fogColor = vec3(0.003, 0.008, 0.025);
              vec3 color = mix(v_color, fogColor, fog * 0.78);
              gl_FragColor = vec4(color, 1.0);
            }`;

          this.program = sceneWebglProgram(this.gl, vertexSource, fragmentSource);
          this.locations = {
            position: this.gl.getAttribLocation(this.program, "a_position"),
            normal: this.gl.getAttribLocation(this.program, "a_normal"),
            color: this.gl.getAttribLocation(this.program, "a_color"),
            projection: this.gl.getUniformLocation(this.program, "u_projection"),
            view: this.gl.getUniformLocation(this.program, "u_view"),
            camera: this.gl.getUniformLocation(this.program, "u_camera"),
            offset: this.gl.getUniformLocation(this.program, "u_offset"),
            time: this.gl.getUniformLocation(this.program, "u_time")
          };
        }

        buildGeometry() {
          const builder = new Shuttle3dGeometryWriter();
          const hull = shuttle3dBoundsVertices(this.scene);
          const forward = hull.slice(0, 6);
          const aft = hull.slice(6, 12);
          const hullColors = [
            builder.color("#243b55"),
            builder.color("#172a46"),
            builder.color("#1d3553"),
            builder.color("#172a46"),
            builder.color("#243b55"),
            builder.color("#263f5b")
          ];

          for (let index = 0; index < 6; index += 1) {
            const next = (index + 1) % 6;
            builder.quad(forward[index], forward[next], aft[next], aft[index], hullColors[index]);
          }

          const frontZ = forward[0][2] + 0.02;
          const frameZ0 = frontZ + 0.06;
          const frameZ1 = frontZ + 0.26;
          const bulkhead = builder.color("#33465f");
          const trim = builder.color("#4f6f8f");
          const glow = builder.color("#55c8ff", true);
          builder.quad([-4.48, -1.44, frontZ], [4.48, -1.44, frontZ], [4.48, 0.0, frontZ], [-4.48, 0.0, frontZ], bulkhead);
          builder.quad([-3.58, 2.32, frontZ], [3.58, 2.32, frontZ], [3.54, 3.12, frontZ], [-3.54, 3.12, frontZ], bulkhead);
          builder.quad([-4.48, 0.0, frontZ], [-2.92, 0.0, frontZ], [-2.92, 2.32, frontZ], [-4.12, 2.32, frontZ], bulkhead);
          builder.quad([2.92, 0.0, frontZ], [4.48, 0.0, frontZ], [4.12, 2.32, frontZ], [2.92, 2.32, frontZ], bulkhead);
          builder.box([-3.08, -0.08, frameZ0], [-2.9, 2.4, frameZ1], trim);
          builder.box([2.9, -0.08, frameZ0], [3.08, 2.4, frameZ1], trim);
          builder.box([-3.08, 2.28, frameZ0], [3.08, 2.46, frameZ1], trim);
          builder.box([-3.08, -0.14, frameZ0], [3.08, 0.04, frameZ1], trim);
          builder.box([-2.82, 2.23, frameZ1], [2.82, 2.28, frameZ1 + 0.04], glow);

          const aftZ = aft[0][2] - 0.02;
          builder.quad(
            [-4.48, -1.44, aftZ],
            [4.48, -1.44, aftZ],
            [3.54, 3.12, aftZ],
            [-3.54, 3.12, aftZ],
            builder.color("#27384f")
          );
          builder.box([-1.2, -1.28, aftZ - 0.24], [1.2, 1.55, aftZ - 0.04], builder.color("#43536a"));
          builder.box([-0.05, -1.2, aftZ - 0.27], [0.05, 1.48, aftZ - 0.01], glow);
          builder.box([-1.0, 1.28, aftZ - 0.28], [1.0, 1.42, aftZ - 0.01], builder.color("#70849b"));

          [-7.0, -4.5, -2.0, 0.5, 3.0, 5.4].forEach((z) => {
            builder.box([-4.44, -1.34, z - 0.08], [-4.25, 2.1, z + 0.08], trim);
            builder.box([4.25, -1.34, z - 0.08], [4.44, 2.1, z + 0.08], trim);
            builder.box([-3.48, 3.0, z - 0.08], [3.48, 3.14, z + 0.08], trim);
          });

          [-2.55, -1.28, 0, 1.28, 2.55].forEach((x) => {
            builder.box([x - 0.025, -1.405, -8.15], [x + 0.025, -1.365, 6.1], builder.color("#52708c"));
          });
          builder.box([-0.42, -1.39, -8.15], [0.42, -1.34, 6.1], builder.color("#315875"));

          const consoleColor = builder.color("#213a52");
          const consoleGlow = builder.color("#24b7ef", true);
          builder.consoleWedge(-1.65, -4.55, 2.55, 1.65, -1.25, -0.35, 0.55, consoleColor);
          builder.consoleWedge(1.65, -4.55, 2.55, 1.65, -1.25, -0.35, 0.55, consoleColor);
          builder.box([-2.6, 0.5, -5.45], [-0.7, 0.58, -5.15], consoleGlow);
          builder.box([0.7, 0.5, -5.45], [2.6, 0.58, -5.15], consoleGlow);
          builder.box([-4.22, -0.55, -3.8], [-3.75, 1.0, -1.2], consoleColor);
          builder.box([3.75, -0.55, -3.8], [4.22, 1.0, -1.2], consoleColor);
          builder.box([-4.18, 0.82, -3.65], [-3.72, 0.9, -1.35], consoleGlow);
          builder.box([3.72, 0.82, -3.65], [4.18, 0.9, -1.35], consoleGlow);

          const seatColor = builder.color("#35445b");
          [-1.45, 1.45].forEach((x) => {
            builder.box([x - 0.52, -1.25, -2.72], [x + 0.52, -0.82, -1.68], seatColor);
            builder.box([x - 0.52, -0.82, -1.55], [x + 0.52, 0.6, -1.28], seatColor);
            builder.box([x - 0.11, -1.38, -2.25], [x + 0.11, -1.05, -1.9], trim);
          });

          const shipHull = builder.color("#aebdca");
          const shipDark = builder.color("#66798e");
          const shipGlow = builder.color("#4da6ff", true);
          builder.ellipsoid([0.85, 1.0, -36.5], [3.35, 0.48, 1.75], 18, 8, shipHull);
          builder.ellipsoid([0.85, 0.08, -35.2], [1.15, 0.62, 1.65], 14, 7, shipDark);
          builder.box([0.55, 0.35, -36.0], [1.15, 0.9, -34.7], shipHull);
          builder.box([-2.25, -0.5, -35.2], [-1.78, -0.12, -31.7], shipDark);
          builder.box([3.48, -0.5, -35.2], [3.95, -0.12, -31.7], shipDark);
          builder.box([-2.3, -0.46, -32.0], [-1.73, -0.14, -31.55], shipGlow);
          builder.box([3.43, -0.46, -32.0], [4.0, -0.14, -31.55], shipGlow);
          builder.box([-1.88, -0.34, -34.7], [3.58, -0.22, -34.45], shipHull);

          return builder.toFloat32Array();
        }

        buildStarfieldGeometry() {
          const builder = new Shuttle3dGeometryWriter();
          const {count, radius, seed, minimumSize, maximumSize} = this.starfield;
          const palette = [
            builder.color("#f8fbff", true),
            builder.color("#d9f4ff", true),
            builder.color("#b8d9ff", true),
            builder.color("#fff1cf", true)
          ];
          let state = seed || 73129;
          const random = () => {
            state = (state * 1664525 + 1013904223) >>> 0;
            return state / 4294967296;
          };
          for (let index = 0; index < count; index += 1) {
            const vertical = 1 - random() * 2;
            const azimuth = random() * Math.PI * 2;
            const horizontal = Math.sqrt(Math.max(0, 1 - vertical * vertical));
            const center = [
              Math.cos(azimuth) * horizontal * radius,
              vertical * radius,
              Math.sin(azimuth) * horizontal * radius
            ];
            const size = minimumSize + random() * (maximumSize - minimumSize);
            const half = size * 0.5;
            const color = palette[Math.min(palette.length - 1, Math.floor(random() * palette.length))];
            builder.box(
              [center[0] - half, center[1] - half, center[2] - half],
              [center[0] + half, center[1] + half, center[2] + half],
              color
            );
          }
          return builder.toFloat32Array();
        }

        bindGeometryBuffer(buffer) {
          const gl = this.gl;
          const stride = 10 * Float32Array.BYTES_PER_ELEMENT;
          gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
          gl.enableVertexAttribArray(this.locations.position);
          gl.vertexAttribPointer(this.locations.position, 3, gl.FLOAT, false, stride, 0);
          gl.enableVertexAttribArray(this.locations.normal);
          gl.vertexAttribPointer(this.locations.normal, 3, gl.FLOAT, false, stride, 3 * Float32Array.BYTES_PER_ELEMENT);
          gl.enableVertexAttribArray(this.locations.color);
          gl.vertexAttribPointer(this.locations.color, 4, gl.FLOAT, false, stride, 6 * Float32Array.BYTES_PER_ELEMENT);
        }

        resize() {
          if (this.disposed) return;
          const width = Math.max(1, this.canvas.clientWidth || this.canvas.parentElement?.clientWidth || 960);
          const height = Math.max(1, this.canvas.clientHeight || this.canvas.parentElement?.clientHeight || 540);
          const dpr = Math.min(this.maxDpr, Math.max(1, window.devicePixelRatio || 1));
          const pixelWidth = Math.round(width * dpr);
          const pixelHeight = Math.round(height * dpr);
          if (this.canvas.width !== pixelWidth || this.canvas.height !== pixelHeight) {
            this.canvas.width = pixelWidth;
            this.canvas.height = pixelHeight;
          }
          this.gl.viewport(0, 0, pixelWidth, pixelHeight);
          this.aspect = pixelWidth / Math.max(1, pixelHeight);
        }

        setLook(yaw, pitch) {
          this.look = {yaw, pitch};
        }

        setMovementKey(code, active) {
          if (!this.movement.enabled) return;
          if (active) this.movementKeys.add(code);
          else this.movementKeys.delete(code);
        }

        clearMovementKeys() {
          this.movementKeys.clear();
        }

        canOccupy(x, z) {
          const {bounds, radius, colliders} = this.movement;
          if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) return false;
          return !colliders.some((collider) => (
            x > collider.minX - radius
            && x < collider.maxX + radius
            && z > collider.minZ - radius
            && z < collider.maxZ + radius
          ));
        }

        moveCamera(deltaX, deltaZ) {
          if (!this.movement.enabled) return;
          const nextX = this.camera[0] + deltaX;
          const nextZ = this.camera[2] + deltaZ;
          let changed = false;
          if (this.canOccupy(nextX, this.camera[2])) {
            this.camera[0] = nextX;
            changed = true;
          }
          if (this.canOccupy(this.camera[0], nextZ)) {
            this.camera[2] = nextZ;
            changed = true;
          }
          if (changed && typeof this.onCameraMoved === "function") {
            this.onCameraMoved(this.camera.slice());
          }
        }

        updateMovement(deltaSeconds) {
          if (!this.movement.enabled || !this.movementKeys.size || deltaSeconds <= 0) return;
          let forwardInput = 0;
          let strafeInput = 0;
          if (this.movementKeys.has("KeyW")) forwardInput += 1;
          if (this.movementKeys.has("KeyS")) forwardInput -= 1;
          if (this.movementKeys.has("KeyD")) strafeInput += 1;
          if (this.movementKeys.has("KeyA")) strafeInput -= 1;
          if (!forwardInput && !strafeInput) return;
          const inputLength = Math.hypot(forwardInput, strafeInput) || 1;
          forwardInput /= inputLength;
          strafeInput /= inputLength;
          const yaw = this.look.yaw * Math.PI / 180;
          const forwardX = Math.sin(yaw);
          const forwardZ = -Math.cos(yaw);
          const rightX = Math.cos(yaw);
          const rightZ = Math.sin(yaw);
          const sprinting = this.movementKeys.has("ShiftLeft") || this.movementKeys.has("ShiftRight");
          const speed = this.movement.walkSpeed * (sprinting ? this.movement.sprintMultiplier : 1);
          const distance = speed * Math.min(0.05, deltaSeconds);
          this.moveCamera(
            (forwardX * forwardInput + rightX * strafeInput) * distance,
            (forwardZ * forwardInput + rightZ * strafeInput) * distance
          );
        }

        draw(now = 0) {
          if (this.disposed) return;
          const frameTime = Number.isFinite(now) ? now : 0;
          const deltaSeconds = this.lastFrameTime === null ? 0 : Math.max(0, (frameTime - this.lastFrameTime) / 1000);
          this.lastFrameTime = frameTime;
          this.updateMovement(deltaSeconds);
          this.resize();
          const gl = this.gl;
          const yaw = this.look.yaw * Math.PI / 180;
          const pitch = this.look.pitch * Math.PI / 180;
          const direction = [
            Math.sin(yaw) * Math.cos(pitch),
            Math.sin(pitch),
            -Math.cos(yaw) * Math.cos(pitch)
          ];
          const target = [
            this.camera[0] + direction[0],
            this.camera[1] + direction[1],
            this.camera[2] + direction[2]
          ];
          const farPlane = Math.max(140, this.starfield.radius + this.starfield.maximumSize + 8);
          const projection = shuttle3dPerspectiveMatrix(66 * Math.PI / 180, this.aspect || 16 / 9, 0.08, farPlane);
          const view = shuttle3dLookAtMatrix(this.camera, target, [0, 1, 0]);

          gl.clearColor(0.002, 0.006, 0.02, 1);
          gl.clearDepth(1);
          gl.enable(gl.DEPTH_TEST);
          gl.depthFunc(gl.LEQUAL);
          gl.disable(gl.CULL_FACE);
          gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
          gl.useProgram(this.program);
          gl.uniformMatrix4fv(this.locations.projection, false, projection);
          gl.uniformMatrix4fv(this.locations.view, false, view);
          gl.uniform3fv(this.locations.camera, new Float32Array(this.camera));
          gl.uniform1f(this.locations.time, now / 1000);

          this.bindGeometryBuffer(this.buffer);
          gl.uniform3f(this.locations.offset, 0, 0, 0);
          gl.drawArrays(gl.TRIANGLES, 0, this.worldVertexCount);

          this.bindGeometryBuffer(this.starBuffer);
          gl.uniform3fv(this.locations.offset, new Float32Array(this.camera));
          gl.drawArrays(gl.TRIANGLES, 0, this.starVertexCount);
          this.animationFrame = requestAnimationFrame(this.draw);
        }

        dispose() {
          if (this.disposed) return;
          this.disposed = true;
          cancelAnimationFrame(this.animationFrame);
          this.resizeObserver?.disconnect?.();
          this.clearMovementKeys();
          this.canvas.removeEventListener("webglcontextlost", this.handleContextLost);
          if (this.buffer) this.gl.deleteBuffer(this.buffer);
          if (this.starBuffer) this.gl.deleteBuffer(this.starBuffer);
          if (this.program) this.gl.deleteProgram(this.program);
        }
      }

      function setShuttle3dLook(container, yaw, pitch, config = shuttle3dCameraConfig(null)) {
        const yawLimit = config.yawLimit || 180;
        const nextYaw = yawLimit >= 179 ? normalizeShuttle3dYaw(yaw) : clampShuttle3dLook(yaw, yawLimit);
        const nextPitch = clampShuttle3dLook(pitch, config.pitchLimit || 28);
        container.__mainComputerShuttle3dLook = {yaw: nextYaw, pitch: nextPitch};
        container.style.setProperty("--shuttle-look-yaw", `${nextYaw.toFixed(2)}deg`);
        container.style.setProperty("--shuttle-look-pitch", `${nextPitch.toFixed(2)}deg`);
        container.__mainComputerShuttle3dRenderer?.setLook?.(nextYaw, nextPitch);
        const shell = container.querySelector(".scene-shuttle3d");
        if (shell) {
          shell.dataset.lookYaw = nextYaw.toFixed(1);
          shell.dataset.lookPitch = nextPitch.toFixed(1);
        }
      }

      function disposeShuttle3dLookaround(container) {
        const handler = container?.__mainComputerShuttle3dLookHandler;
        if (handler) {
          container.removeEventListener("pointerdown", handler.pointerDown);
          container.removeEventListener("keydown", handler.keyDown);
          container.removeEventListener("keyup", handler.keyUp);
          container.removeEventListener("blur", handler.blur);
          window.removeEventListener("pointermove", handler.pointerMove);
          window.removeEventListener("pointerup", handler.pointerUp);
          window.removeEventListener("blur", handler.blur);
          container.__mainComputerShuttle3dLookHandler = null;
        }
        if (container?.__mainComputerShuttle3dRenderer) {
          container.__mainComputerShuttle3dRenderer.dispose();
          container.__mainComputerShuttle3dRenderer = null;
        }
        if (container?.dataset) {
          delete container.dataset.shuttle3dLookaround;
          delete container.dataset.shuttle3dMovement;
          delete container.dataset.shuttle3dDragging;
        }
      }

      function bindShuttle3dLookaround(container, scene) {
        disposeShuttle3dLookaround(container);
        const config = shuttle3dCameraConfig(scene);
        const movementCodes = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "ShiftRight"]);
        setShuttle3dLook(container, config.yaw, config.pitch, config);
        container.dataset.shuttle3dLookaround = "enabled";
        container.dataset.shuttle3dMovement = "wasd";
        container.tabIndex = container.tabIndex >= 0 ? container.tabIndex : 0;
        let dragging = false;
        let startX = 0;
        let startY = 0;
        let startYaw = config.yaw;
        let startPitch = config.pitch;
        const applyDelta = (dx, dy) => {
          const nextYaw = startYaw + dx * 0.14;
          const nextPitch = startPitch - dy * 0.11;
          setShuttle3dLook(container, nextYaw, nextPitch, config);
        };
        const pointerDown = (event) => {
          if (event.button !== 0 || event.defaultPrevented) return;
          const target = event.target;
          if (target?.closest?.("button, a, input, select, textarea")) return;
          event.preventDefault();
          dragging = true;
          startX = event.clientX;
          startY = event.clientY;
          const current = container.__mainComputerShuttle3dLook || {yaw: config.yaw, pitch: config.pitch};
          startYaw = current.yaw;
          startPitch = current.pitch;
          container.dataset.shuttle3dDragging = "true";
          container.focus({preventScroll: true});
        };
        const pointerMove = (event) => {
          if (!dragging) return;
          applyDelta(event.clientX - startX, event.clientY - startY);
        };
        const pointerUp = () => {
          if (!dragging) return;
          dragging = false;
          delete container.dataset.shuttle3dDragging;
        };
        const keyDown = (event) => {
          if (movementCodes.has(event.code)) {
            event.preventDefault();
            container.__mainComputerShuttle3dRenderer?.setMovementKey?.(event.code, true);
            return;
          }
          const current = container.__mainComputerShuttle3dLook || {yaw: config.yaw, pitch: config.pitch};
          let yaw = current.yaw;
          let pitch = current.pitch;
          if (event.key === "ArrowLeft") yaw -= 3;
          else if (event.key === "ArrowRight") yaw += 3;
          else if (event.key === "ArrowUp") pitch += 2;
          else if (event.key === "ArrowDown") pitch -= 2;
          else return;
          event.preventDefault();
          setShuttle3dLook(container, yaw, pitch, config);
        };
        const keyUp = (event) => {
          if (!movementCodes.has(event.code)) return;
          event.preventDefault();
          container.__mainComputerShuttle3dRenderer?.setMovementKey?.(event.code, false);
        };
        const blur = () => {
          dragging = false;
          delete container.dataset.shuttle3dDragging;
          container.__mainComputerShuttle3dRenderer?.clearMovementKeys?.();
        };
        const handler = {pointerDown, pointerMove, pointerUp, keyDown, keyUp, blur};
        container.__mainComputerShuttle3dLookHandler = handler;
        container.addEventListener("pointerdown", pointerDown);
        container.addEventListener("keydown", keyDown);
        container.addEventListener("keyup", keyUp);
        container.addEventListener("blur", blur);
        window.addEventListener("pointermove", pointerMove);
        window.addEventListener("pointerup", pointerUp);
        window.addEventListener("blur", blur);
      }

      function shuttle3dObjectLabel(scene, objectId, fallback) {
        const object = sceneObjectsById(scene).get(objectId);
        return sceneObjectLabel(object) || fallback;
      }

      function renderShuttle3dScene(container, scene, options = {}) {
        const shuttle = scene?.metadata?.shuttle3d && typeof scene.metadata.shuttle3d === "object" ? scene.metadata.shuttle3d : {};
        container.dataset.sceneState = "shuttle3d-first-person";
        container.dataset.shuttle3d = "webgl-vertex-mesh";
        container.dataset.sceneLookaround = "enabled";

        const shell = document.createElement("div");
        shell.className = "scene-shuttle3d";
        shell.setAttribute("role", "application");
        shell.setAttribute("aria-label", shuttle.controlsHint || "Vertex-built 3D shuttlecraft interior. Click to focus, use W A S D to walk, and drag or use arrow keys to look.");
        shell.tabIndex = 0;

        const canvas = document.createElement("canvas");
        canvas.className = "scene-shuttle3d-canvas";
        canvas.dataset.sceneObjectId = String(shuttle.viewport || "forward-viewer");
        canvas.setAttribute("role", "img");
        canvas.setAttribute(
          "aria-label",
          `${shuttle3dObjectLabel(scene, "shuttle-floor", "Shuttle interior")} rendered from real hull vertices, with stars and ${shuttle3dObjectLabel(scene, String(shuttle.motherShip || "mother-ship"), "the mother ship")} beyond the forward viewport.`
        );

        const hint = document.createElement("div");
        hint.className = "scene-shuttle3d-look-hint";
        hint.textContent = shuttle.controlsHint || "Click to focus • Drag or arrows to look • W/A/S/D to walk • Shift to sprint";

        const status = document.createElement("div");
        status.className = "scene-shuttle3d-mesh-status";
        status.textContent = "Building shuttle hull vertices…";

        shell.append(canvas, hint, status);
        container.append(shell);
        bindShuttle3dLookaround(container, scene);

        try {
          const renderer = new Shuttle3dVertexRenderer(canvas, scene);
          container.__mainComputerShuttle3dRenderer = renderer;
          const current = container.__mainComputerShuttle3dLook || shuttle3dCameraConfig(scene);
          renderer.setLook(current.yaw, current.pitch);
          canvas.dataset.shuttleVertexCount = String(renderer.vertexCount);
          canvas.dataset.starfieldMode = renderer.starfield.mode;
          canvas.dataset.starfieldCount = String(renderer.starfield.count);
          canvas.dataset.starfieldRadius = String(renderer.starfield.radius);
          const updateMovementStatus = (camera) => {
            status.textContent = `${renderer.worldVertexCount.toLocaleString()} shuttle vertices • ${renderer.starfield.count} stars at ${renderer.starfield.radius}u • x ${camera[0].toFixed(1)} • z ${camera[2].toFixed(1)}`;
            canvas.dataset.cameraX = camera[0].toFixed(3);
            canvas.dataset.cameraZ = camera[2].toFixed(3);
          };
          renderer.onCameraMoved = updateMovementStatus;
          updateMovementStatus(renderer.camera);
        } catch (error) {
          shell.dataset.rendererError = "true";
          status.textContent = "WebGL shuttle renderer unavailable";
          const fallback = document.createElement("div");
          fallback.className = "scene-shuttle3d-renderer-error";
          fallback.textContent = error instanceof Error ? error.message : "Unable to initialize the shuttle vertex renderer.";
          shell.append(fallback);
        }
        return shell;
      }

      function renderSceneBackdrop(container, scene) {
        const projection = sceneProjection(scene);
        if (projection !== "isometric") return;
        const stage = document.createElement("div");
        stage.className = "scene-stage scene-stage--isometric";
        stage.setAttribute("aria-hidden", "true");
        const grid = document.createElement("div");
        grid.className = "scene-stage-grid";
        stage.append(grid);
        container.append(stage);
      }

      function sceneMovementBounds(scene) {
        const bounds = scene?.metadata?.movementBounds || scene?.metadata?.clickMovementBounds || {};
        return {
          minX: numericSceneProp(bounds.minX, 0, -256, 256),
          maxX: numericSceneProp(bounds.maxX, 10, -256, 256),
          minY: numericSceneProp(bounds.minY, 0, -256, 256),
          maxY: numericSceneProp(bounds.maxY, 10, -256, 256)
        };
      }

      function clampSceneWorldPoint(point, scene) {
        const bounds = sceneMovementBounds(scene);
        return {
          x: Math.min(bounds.maxX, Math.max(bounds.minX, point.x)),
          y: Math.min(bounds.maxY, Math.max(bounds.minY, point.y))
        };
      }

      function screenPointToIsoWorld(container, clientX, clientY, scene) {
        const rect = container.getBoundingClientRect();
        const metrics = sceneProjectionMetrics(scene);
        const screenX = clientX - rect.left - metrics.originX;
        const screenY = clientY - rect.top - metrics.originY;
        const worldX = (screenX / metrics.tileWidth) + (screenY / metrics.tileHeight);
        const worldY = (screenY / metrics.tileHeight) - (screenX / metrics.tileWidth);
        return clampSceneWorldPoint({x: worldX, y: worldY}, scene);
      }

      function sceneMovementActor(scene, options = {}) {
        const objects = Array.isArray(scene?.objects) ? scene.objects : [];
        const explicitId = String(options.movementObjectId || scene?.metadata?.playerObjectId || scene?.metadata?.controls?.movementActorId || "hero-sprite").trim();
        return objects.find((object) => object?.id === explicitId)
          || objects.find((object) => object?.type === "sprite-actor" && object?.props?.role === "player")
          || objects.find((object) => object?.type === "sprite-actor")
          || null;
      }

      function movementSpeed(scene) {
        const controls = scene?.metadata?.controls && typeof scene.metadata.controls === "object" ? scene.metadata.controls : {};
        return numericSceneProp(controls.moveSpeed ?? scene?.metadata?.movementSpeed, 3.15, 0.4, 18);
      }

      function setSceneMovementDestination(actor, destination, scene) {
        if (!actor) return null;
        const startX = Number.isFinite(Number(actor.x)) ? Number(actor.x) : 0;
        const startY = Number.isFinite(Number(actor.y)) ? Number(actor.y) : 0;
        const endX = Number(destination.x.toFixed(2));
        const endY = Number(destination.y.toFixed(2));
        const distance = Math.hypot(endX - startX, endY - startY);
        if (distance < 0.03) return null;
        actor.props = actor.props && typeof actor.props === "object" ? actor.props : {};
        actor.props.motion = String(actor.props.motion || "stride");
        actor.props.spellState = "moving";
        actor.props.moveTarget = {
          x: endX,
          y: endY,
          mode: "left-click",
          timestamp: Date.now()
        };
        actor.props.moveFrom = {
          x: Number(startX.toFixed(2)),
          y: Number(startY.toFixed(2)),
          distance: Number(distance.toFixed(3)),
          speed: movementSpeed(scene)
        };
        return actor.props.moveFrom;
      }

      function sceneObjectDependsOnActor(object, actorId) {
        if (!actorId || !object) return false;
        if (String(object.id || "") === actorId) return true;
        const parentId = String(object.parentId || object.props?.parentId || "").trim();
        if (parentId === actorId) return true;
        const sourceId = String(object.props?.sourceId || "").trim();
        const targetId = String(object.props?.targetId || "").trim();
        return sourceId === actorId || targetId === actorId;
      }

      function applyProjectedElementPosition(element, projected) {
        if (!element || !projected) return;
        element.style.left = `${projected.left}px`;
        element.style.top = `${projected.top}px`;
        element.style.width = `${Math.max(0, projected.width)}px`;
        element.style.height = `${Math.max(0, projected.height)}px`;
        element.style.zIndex = String(projected.zIndex);
        if (Number.isFinite(projected.pathLength)) element.style.setProperty("--scene-path-length", `${projected.pathLength.toFixed(2)}px`);
        else element.style.removeProperty("--scene-path-length");
        if (Number.isFinite(projected.pathAngle)) element.style.setProperty("--scene-path-angle", `${projected.pathAngle.toFixed(2)}deg`);
        else element.style.removeProperty("--scene-path-angle");
        if (projected.sourceLeft !== undefined) element.style.setProperty("--scene-source-left", `${Number(projected.sourceLeft).toFixed(2)}px`);
        else element.style.removeProperty("--scene-source-left");
        if (projected.targetLeft !== undefined) element.style.setProperty("--scene-target-left", `${Number(projected.targetLeft).toFixed(2)}px`);
        else element.style.removeProperty("--scene-target-left");
        if (projected.transform) element.style.transform = projected.transform;
        else element.style.removeProperty("transform");
        element.dataset.sceneAnchor = projected.anchor;
      }

      function updateSceneMovementMarker(container, scene, options = {}) {
        let marker = container.querySelector("[data-scene-movement-marker='true']");
        const actor = sceneMovementActor(scene, options);
        const target = actor?.props?.moveTarget;
        if (!target || !Number.isFinite(Number(target.x)) || !Number.isFinite(Number(target.y))) {
          marker?.remove();
          return;
        }
        if (!marker) {
          marker = document.createElement("span");
          marker.className = "scene-movement-marker";
          marker.dataset.sceneMovementMarker = "true";
          marker.setAttribute("aria-hidden", "true");
          marker.innerHTML = "<span></span><span></span>";
          container.append(marker);
        }
        const point = projectWorldPoint(Number(target.x), Number(target.y), 0, scene);
        marker.style.left = `${point.left}px`;
        marker.style.top = `${point.top}px`;
        marker.style.zIndex = String(Math.round((Number(target.x) + Number(target.y)) * 10 + 1));
      }

      function updateSceneMovementElements(container, scene, actor, options = {}) {
        const actorId = String(actor?.id || "");
        const objects = Array.isArray(scene?.objects) ? scene.objects : [];
        objects.forEach((object) => {
          if (!sceneObjectDependsOnActor(object, actorId)) return;
          const element = container.querySelector(`[data-scene-object-id="${CSS.escape(String(object.id || ""))}"]`);
          if (!element) return;
          const projected = projectSceneObject(object, scene);
          applyProjectedElementPosition(element, projected);
          if (object?.type === "particle-emitter") {
            const particleLayer = container.__mainComputerWebglParticleLayer;
            if (particleLayer?.updateEmitter) particleLayer.updateEmitter(object, scene, projected);
          }
          element.dataset.sceneMoving = object === actor && actor?.props?.moveTarget ? "true" : "false";
        });
        updateSceneMovementMarker(container, scene, options);
      }

      function stopSceneMovement(container) {
        if (container.__mainComputerMovementFrame) {
          cancelAnimationFrame(container.__mainComputerMovementFrame);
          container.__mainComputerMovementFrame = 0;
        }
      }

      function startSceneMovement(container, scene, actor, options = {}) {
        if (!actor?.props?.moveTarget) return;
        stopSceneMovement(container);
        let lastTime = 0;
        const speed = movementSpeed(scene);
        const tick = (timestamp) => {
          if (!actor?.props?.moveTarget) {
            updateSceneMovementElements(container, scene, actor, options);
            container.__mainComputerMovementFrame = 0;
            return;
          }
          if (!lastTime) lastTime = timestamp;
          const deltaSeconds = Math.min(0.08, Math.max(0, (timestamp - lastTime) / 1000));
          lastTime = timestamp;
          const targetX = Number(actor.props.moveTarget.x);
          const targetY = Number(actor.props.moveTarget.y);
          const currentX = Number.isFinite(Number(actor.x)) ? Number(actor.x) : 0;
          const currentY = Number.isFinite(Number(actor.y)) ? Number(actor.y) : 0;
          const dx = targetX - currentX;
          const dy = targetY - currentY;
          const distance = Math.hypot(dx, dy);
          const step = speed * deltaSeconds;
          if (distance <= Math.max(step, 0.012)) {
            actor.x = Number(targetX.toFixed(3));
            actor.y = Number(targetY.toFixed(3));
            actor.props.lastMoveTarget = {x: actor.x, y: actor.y, timestamp: Date.now()};
            delete actor.props.moveTarget;
            delete actor.props.moveFrom;
            actor.props.spellState = actor.props.idleSpellState || "casting";
            updateSceneMovementElements(container, scene, actor, options);
            container.__mainComputerMovementFrame = 0;
            if (typeof options.onSceneMovement === "function") {
              options.onSceneMovement({
                phase: "finish",
                scene,
                actor,
                actorId: String(actor.id || ""),
                worldX: actor.x,
                worldY: actor.y,
                movementMode: "left-click"
              });
            }
            return;
          }
          actor.x = Number((currentX + (dx / distance) * step).toFixed(4));
          actor.y = Number((currentY + (dy / distance) * step).toFixed(4));
          actor.props.spellState = "moving";
          updateSceneMovementElements(container, scene, actor, options);
          container.__mainComputerMovementFrame = requestAnimationFrame(tick);
        };
        container.__mainComputerMovementFrame = requestAnimationFrame(tick);
      }

      function renderSceneMovementMarker(container, scene, options = {}) {
        if (options.showMovementMarker === false || sceneProjection(scene) !== "isometric") return;
        updateSceneMovementMarker(container, scene, options);
      }

      function bindSceneClickMovement(container, scene, options = {}) {
        if (container.__mainComputerClickMovementHandler) {
          container.removeEventListener("pointerdown", container.__mainComputerClickMovementHandler);
          container.__mainComputerClickMovementHandler = null;
        }
        if (!options.enableClickMovement || sceneProjection(scene) !== "isometric") {
          stopSceneMovement(container);
          delete container.dataset.clickMovement;
          delete container.dataset.movementMode;
          return;
        }
        container.dataset.clickMovement = "enabled";
        container.dataset.movementMode = "left-click";
        const clickMovementHandler = (event) => {
          if (event.button !== 0 || event.defaultPrevented) return;
          const actor = sceneMovementActor(scene, options);
          if (!actor) return;
          event.preventDefault();
          event.stopPropagation();
          const destination = screenPointToIsoWorld(container, event.clientX, event.clientY, scene);
          const moveFrom = setSceneMovementDestination(actor, destination, scene);
          if (!moveFrom) return;
          updateSceneMovementElements(container, scene, actor, options);
          if (typeof options.onSceneMovement === "function") {
            options.onSceneMovement({
              phase: "start",
              scene,
              actor,
              actorId: String(actor.id || ""),
              worldX: actor.x,
              worldY: actor.y,
              targetX: actor.props.moveTarget.x,
              targetY: actor.props.moveTarget.y,
              moveFrom,
              movementMode: "left-click"
            });
          }
          startSceneMovement(container, scene, actor, options);
        };
        container.__mainComputerClickMovementHandler = clickMovementHandler;
        container.addEventListener("pointerdown", clickMovementHandler);
        const actor = sceneMovementActor(scene, options);
        if (actor?.props?.moveTarget) startSceneMovement(container, scene, actor, options);
      }

      function renderSceneObject(parent, object, scene, options = {}) {
        if (!object || !parent) return;
        const element = document.createElement("div");
        const objectType = String(object.type || "object");
        const projected = projectSceneObject(object, scene);
        element.className = "scene-object";
        element.dataset.sceneObjectId = String(object.id || "");
        element.dataset.sceneObjectType = objectType;
        if (object.parentId || object.props?.parentId) element.dataset.sceneParentId = String(object.parentId || object.props?.parentId || "");
        if (object?.props?.moveTarget) element.dataset.sceneMoving = "true";
        element.style.left = `${projected.left}px`;
        element.style.top = `${projected.top}px`;
        element.style.width = `${Math.max(0, projected.width)}px`;
        element.style.height = `${Math.max(0, projected.height)}px`;
        element.style.zIndex = String(projected.zIndex);
        if (Number.isFinite(projected.pathLength)) element.style.setProperty("--scene-path-length", `${projected.pathLength.toFixed(2)}px`);
        if (Number.isFinite(projected.pathAngle)) element.style.setProperty("--scene-path-angle", `${projected.pathAngle.toFixed(2)}deg`);
        if (projected.sourceLeft !== undefined) element.style.setProperty("--scene-source-left", `${Number(projected.sourceLeft).toFixed(2)}px`);
        if (projected.targetLeft !== undefined) element.style.setProperty("--scene-target-left", `${Number(projected.targetLeft).toFixed(2)}px`);
        const moveFrom = object?.props?.moveFrom;
        if (moveFrom && sceneProjection(scene) === "isometric") {
          const fromPoint = projectWorldPoint(Number(moveFrom.x) || 0, Number(moveFrom.y) || 0, numericSceneProp(object?.props?.z ?? object?.props?.elevation, 0, -256, 512), scene);
          element.style.setProperty("--scene-move-from-x", `${(fromPoint.left - projected.left).toFixed(2)}px`);
          element.style.setProperty("--scene-move-from-y", `${(fromPoint.top - projected.top).toFixed(2)}px`);
          element.style.setProperty("--scene-move-duration", `${numericSceneProp(moveFrom.durationMs, 420, 120, 1200)}ms`);
        }
        if (projected.transform) element.style.transform = projected.transform;
        element.dataset.sceneAnchor = projected.anchor;
        if (objectType === "particle-emitter") {
          if (sceneObjectGpuForgeAtlas(object, options)) {
            renderParticleEmitter(element, object, scene, options);
          } else if (options.particleLayer) {
            renderWebglParticleEmitterMarker(element, object, scene, projected, options.particleLayer);
          } else {
            renderParticleEmitter(element, object, scene, options);
          }
        } else if (objectType === "sprite-actor") {
          renderSpriteActor(element, object);
        } else if (object.props?.label) {
          element.setAttribute("aria-label", String(object.props.label));
        }
        decorateSceneObject(element, object, options);
        appendSceneObjectLabel(element, object, options);
        parent.append(element);
      }

      function sceneState(scene) {
        if (!scene.objects.length) return "empty";
        if (sceneProjection(scene) === "shuttle-3d") return "shuttle3d-lookaround";
        if (sceneProjection(scene) === "isometric") return "isometric-sprite-scene";
        if (scene.objects.every((object) => object?.type === "particle-emitter")) return "particle-field";
        return "objects";
      }

      function renderSceneSurface(container, sceneOrId, options = {}) {
        if (!container) return null;
        if (container.__mainComputerWebglParticleLayer) {
          container.__mainComputerWebglParticleLayer.dispose();
          container.__mainComputerWebglParticleLayer = null;
        }
        disposeShuttle3dLookaround(container);
        const scene = resolveScene(sceneOrId);
        const projection = sceneProjection(scene);
        const metrics = sceneProjectionMetrics(scene);
        const vfx = sceneVfxSettings(scene);
        container.replaceChildren();
        container.dataset.sceneViewer = "true";
        container.dataset.sceneId = scene.id;
        container.dataset.sceneName = scene.name;
        container.dataset.sceneState = sceneState(scene);
        container.dataset.sceneProjection = projection;
        container.dataset.sceneMode = String(options.mode || (options.embedded ? "document-embed" : "surface"));
        if (scene?.metadata?.rolloutPhase) container.dataset.rolloutPhase = String(scene.metadata.rolloutPhase);
        if (scene?.metadata?.characterModel) container.dataset.characterModel = String(scene.metadata.characterModel);
        container.dataset.particleMultiplier = vfx.particleMultiplier.toFixed(2);
        container.dataset.effectMultiplier = vfx.effectMultiplier.toFixed(2);
        container.style.setProperty("--scene-particle-density", vfx.particleMultiplier.toFixed(2));
        container.style.setProperty("--scene-effect-intensity", vfx.effectMultiplier.toFixed(2));
        container.style.setProperty("--scene-tile-width", `${metrics.tileWidth}px`);
        container.style.setProperty("--scene-tile-height", `${metrics.tileHeight}px`);
        container.style.setProperty("--scene-origin-x", `${metrics.originX}px`);
        container.style.setProperty("--scene-origin-y", `${metrics.originY}px`);
        if (options.projectId) container.dataset.projectId = String(options.projectId);
        if (options.selectedObjectId) container.dataset.selectedObjectId = String(options.selectedObjectId);
        container.setAttribute("aria-label", options.label || `Scene: ${scene.name}`);
        if (scene.background) {
          container.style.background = scene.background;
        } else {
          container.style.removeProperty("background");
        }
        if (projection === "shuttle-3d") {
          renderShuttle3dScene(container, scene, options);
          return {
            scene,
            objectCount: scene.objects.length,
            dispose() {
              disposeShuttle3dLookaround(container);
            }
          };
        }
        renderSceneBackdrop(container, scene);
        const particleLayer = options.renderObjects === false ? null : createSceneWebglParticleLayer(container, scene, options);
        container.__mainComputerWebglParticleLayer = particleLayer;
        renderSceneChoreographyOverlay(container, scene);
        renderSceneMovementMarker(container, scene, options);
        if (options.renderObjects !== false) {
          const renderOptions = particleLayer ? {...options, particleLayer} : options;
          scene.objects.forEach((object) => renderSceneObject(container, object, scene, renderOptions));
          particleLayer?.start?.();
        }
        bindSceneClickMovement(container, scene, options);
        return {
          scene,
          objectCount: scene.objects.length,
          dispose() {
            disposeShuttle3dLookaround(container);
            if (container.__mainComputerClickMovementHandler) {
              container.removeEventListener("pointerdown", container.__mainComputerClickMovementHandler);
              container.__mainComputerClickMovementHandler = null;
            }
            stopSceneMovement(container);
            if (container.__mainComputerWebglParticleLayer) {
              container.__mainComputerWebglParticleLayer.dispose();
              container.__mainComputerWebglParticleLayer = null;
            }
            container.replaceChildren();
          }
        };
      }

      function hydrateSceneEmbeds(root = document) {
        root?.querySelectorAll?.("[data-scene-embed], [data-doc-object='scene-embed']").forEach((element) => {
          renderSceneSurface(element, element.dataset.sceneId || undefined, {
            embedded: true,
            mode: "document-embed",
            label: element.getAttribute("aria-label") || "Embedded scene",
            showLabels: false
          });
        });
      }

      window.MainComputerSceneViewer = {
        resolveScene,
        renderSceneSurface,
        hydrateSceneEmbeds,
        screenPointToIsoWorld
      };
    })();
