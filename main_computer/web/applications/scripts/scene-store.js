    (function () {
      const sceneStorageKey = "main-computer-scenes-v2";
      const selectedSceneStorageKey = "main-computer-selected-scene-v2";
      const sceneChangeEvent = "main-computer-scene-change";
      const selectedSceneChangeEvent = "main-computer-selected-scene-change";
      const defaultSceneId = "default-empty-scene";
      const playerSpriteObjectId = "hero-sprite";

      function cloneScene(scene) {
        return JSON.parse(JSON.stringify(scene));
      }

      function defaultPlayerSprite() {
        const objects = defaultSceneObjects();
        const player = objects.find((object) => object?.id === playerSpriteObjectId) || objects[0] || null;
        return player ? cloneScene(player) : null;
      }

      function defaultSceneObjects() {
        return [
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
                              "spellState": "looking-around",
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
                              "twinkle": true
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
                              "label": "Look-around Camera",
                              "role": "camera",
                              "yaw": 0,
                              "pitch": -2,
                              "yawLimit": 34,
                              "pitchLimit": 18,
                              "instructions": "Drag or use arrow keys to look around the shuttle interior."
                    }
          }
];
      }

      function defaultScene() {
        return {
          "id": "default-empty-scene",
          "name": "Shuttlecraft Lookaround",
          "version": 7,
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
                                        "spellState": "looking-around",
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
                                        "twinkle": true
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
                                        "label": "Look-around Camera",
                                        "role": "camera",
                                        "yaw": 0,
                                        "pitch": -2,
                                        "yawLimit": 34,
                                        "pitchLimit": 18,
                                        "instructions": "Drag or use arrow keys to look around the shuttle interior."
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
                    "rolloutPhase": "phase-2-shuttle-3d-lookaround",
                    "setting": "federation-like shuttle craft interior with stars and mother ship visible through the forward viewport",
                    "starterScene": "shuttlecraft-lookaround-spawn",
                    "characterModel": "first-person-cadet-presence",
                    "meshActorsEnabled": false,
                    "parentedParticles": true,
                    "linkedSpellProjectiles": false,
                    "linkedSensorPulses": true,
                    "targetedParticles": true,
                    "shuttleInterior": true,
                    "choreography": {
                              "title": "Shuttle Look-Around Boot",
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
                                                  "label": "Ready to look",
                                                  "timeMs": 6200,
                                                  "cue": "lookaround-camera"
                                        }
                              ]
                    },
                    "controls": {
                              "mode": "lookaround",
                              "pointerDrag": true,
                              "keyboard": "arrow-keys",
                              "movement": "stationary-inside-shuttle"
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
                              "mode": "lookaround",
                              "yaw": 0,
                              "pitch": -2,
                              "yawLimit": 34,
                              "pitchLimit": 18,
                              "hint": "Drag inside the Game Surface or use arrow keys to look around."
                    },
                    "shuttle3d": {
                              "mode": "simple-css-3d",
                              "lookAround": true,
                              "viewport": "forward-viewer",
                              "starfield": "viewport-starfield",
                              "motherShip": "mother-ship",
                              "motherShipLabel": "Mother Ship",
                              "playerAnchor": "hero-sprite",
                              "controlsHint": "Drag to look around the shuttle. The forward viewport shows stars and the mother ship."
                    }
          }
};
      }

      function normalizeSceneId(value, fallback = defaultSceneId) {
        const clean = String(value || "").trim();
        return clean || fallback;
      }

      function hasStoredScenes() {
        try {
          const parsed = JSON.parse(localStorage.getItem(sceneStorageKey) || "[]");
          return Array.isArray(parsed) && parsed.length > 0;
        } catch {
          return false;
        }
      }

      function dispatchSceneStoreEvent(eventName, detail) {
        try {
          window.dispatchEvent(new CustomEvent(eventName, {detail}));
        } catch {
          // Scene mirroring is best-effort; storage remains the source of truth.
        }
      }

      function normalizeSceneObject(object, index = 0) {
        const source = object && typeof object === "object" ? object : {};
        return {
          id: normalizeSceneId(source.id, `object-${index + 1}`),
          type: normalizeSceneId(source.type, "empty"),
          parentId: source.parentId ? normalizeSceneId(source.parentId, "") : undefined,
          x: Number.isFinite(Number(source.x)) ? Number(source.x) : 0,
          y: Number.isFinite(Number(source.y)) ? Number(source.y) : 0,
          width: Number.isFinite(Number(source.width)) ? Math.max(0, Number(source.width)) : 0,
          height: Number.isFinite(Number(source.height)) ? Math.max(0, Number(source.height)) : 0,
          props: source.props && typeof source.props === "object" ? cloneScene(source.props) : {}
        };
      }

      function normalizeSceneObjects(objects) {
        const normalized = Array.isArray(objects)
          ? objects.filter((object) => object && typeof object === "object").map(normalizeSceneObject)
          : [];
        return normalized.length ? normalized : defaultSceneObjects();
      }

      function normalizeScene(scene, fallbackId = defaultSceneId) {
        const source = scene && typeof scene === "object" ? scene : {};
        const objects = normalizeSceneObjects(source.objects);
        return {
          id: normalizeSceneId(source.id, fallbackId),
          name: String(source.name || "Untitled Scene"),
          version: Number.isFinite(Number(source.version)) ? Math.max(1, Number(source.version)) : 1,
          background: source.background ? String(source.background) : null,
          objects,
          metadata: source.metadata && typeof source.metadata === "object" ? cloneScene(source.metadata) : {}
        };
      }

      function readStoredScenes() {
        try {
          const parsed = JSON.parse(localStorage.getItem(sceneStorageKey) || "[]");
          if (Array.isArray(parsed)) {
            const normalized = parsed.map((scene, index) => normalizeScene(scene, index === 0 ? defaultSceneId : `scene-${index + 1}`));
            if (normalized.length) return normalized;
          }
        } catch {
          // Local scene storage is optional; fall back to the default player scene.
        }
        return [defaultScene()];
      }

      function writeStoredScenes(scenes) {
        const normalized = (Array.isArray(scenes) && scenes.length ? scenes : [defaultScene()])
          .map((scene, index) => normalizeScene(scene, index === 0 ? defaultSceneId : `scene-${index + 1}`));
        localStorage.setItem(sceneStorageKey, JSON.stringify(normalized));
        return normalized;
      }

      function listScenes() {
        return readStoredScenes().map(cloneScene);
      }

      function getScene(sceneId = selectedSceneId()) {
        const cleanId = normalizeSceneId(sceneId);
        const scenes = readStoredScenes();
        return cloneScene(scenes.find((scene) => scene.id === cleanId) || scenes[0] || defaultScene());
      }

      function saveScene(scene, options = {}) {
        const normalized = normalizeScene(scene);
        const scenes = readStoredScenes();
        const index = scenes.findIndex((candidate) => candidate.id === normalized.id);
        if (index >= 0) scenes[index] = normalized;
        else scenes.push(normalized);
        writeStoredScenes(scenes);
        const saved = cloneScene(normalized);
        if (options.notify !== false) {
          dispatchSceneStoreEvent(sceneChangeEvent, {
            scene: saved,
            sceneId: saved.id,
            source: String(options.source || "scene-store")
          });
        }
        return saved;
      }

      function createScene(name = "Untitled Scene") {
        const base = String(name || "Untitled Scene").trim() || "Untitled Scene";
        const slug = base.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "scene";
        const existing = new Set(readStoredScenes().map((scene) => scene.id));
        let id = slug;
        let suffix = 2;
        while (existing.has(id)) {
          id = `${slug}-${suffix}`;
          suffix += 1;
        }
        const scene = normalizeScene({
          id,
          name: base,
          objects: defaultSceneObjects(),
          metadata: {
                      "starter": true,
                      "projection": "shuttle-3d",
                      "tileWidth": 92,
                      "tileHeight": 46,
                      "originX": 480,
                      "originY": 118,
                      "particleOnly": false,
                      "includesDefaultPlayer": true,
                      "isometric": false,
                      "rolloutPhase": "phase-2-shuttle-3d-lookaround",
                      "setting": "federation-like shuttle craft interior with stars and mother ship visible through the forward viewport",
                      "starterScene": "shuttlecraft-lookaround-spawn",
                      "characterModel": "first-person-cadet-presence",
                      "meshActorsEnabled": false,
                      "parentedParticles": true,
                      "linkedSpellProjectiles": false,
                      "linkedSensorPulses": true,
                      "targetedParticles": true,
                      "shuttleInterior": true,
                      "choreography": {
                                  "title": "Shuttle Look-Around Boot",
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
                                                          "label": "Ready to look",
                                                          "timeMs": 6200,
                                                          "cue": "lookaround-camera"
                                              }
                                  ]
                      },
                      "controls": {
                                  "mode": "lookaround",
                                  "pointerDrag": true,
                                  "keyboard": "arrow-keys",
                                  "movement": "stationary-inside-shuttle"
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
                                  "mode": "lookaround",
                                  "yaw": 0,
                                  "pitch": -2,
                                  "yawLimit": 34,
                                  "pitchLimit": 18,
                                  "hint": "Drag inside the Game Surface or use arrow keys to look around."
                      },
                      "shuttle3d": {
                                  "mode": "simple-css-3d",
                                  "lookAround": true,
                                  "viewport": "forward-viewer",
                                  "starfield": "viewport-starfield",
                                  "motherShip": "mother-ship",
                                  "motherShipLabel": "Mother Ship",
                                  "playerAnchor": "hero-sprite",
                                  "controlsHint": "Drag to look around the shuttle. The forward viewport shows stars and the mother ship."
                      }
          }
        }, id);
        saveScene(scene);
        return scene;
      }

      function selectedSceneId() {
        try {
          return normalizeSceneId(localStorage.getItem(selectedSceneStorageKey), defaultSceneId);
        } catch {
          return defaultSceneId;
        }
      }

      function setSelectedSceneId(sceneId, options = {}) {
        const cleanId = normalizeSceneId(sceneId);
        try {
          localStorage.setItem(selectedSceneStorageKey, cleanId);
        } catch {
          // Selection persistence is best-effort only.
        }
        if (options.notify !== false) {
          dispatchSceneStoreEvent(selectedSceneChangeEvent, {
            sceneId: cleanId,
            source: String(options.source || "scene-store")
          });
        }
        return cleanId;
      }

      window.MainComputerSceneStore = {
        defaultSceneId,
        sceneStorageKey,
        selectedSceneStorageKey,
        sceneChangeEvent,
        selectedSceneChangeEvent,
        playerSpriteObjectId,
        defaultPlayerSprite,
        defaultSceneObjects,
        defaultScene,
        normalizeScene,
        hasStoredScenes,
        listScenes,
        getScene,
        saveScene,
        createScene,
        selectedSceneId,
        setSelectedSceneId
      };
    })();
