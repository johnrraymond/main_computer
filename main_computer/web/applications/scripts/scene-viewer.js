    (function () {
      const imageAssetKinds = new Set(["image"]);

      function fallbackScene(sceneId = "default-empty-scene") {
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
                                        "lookaroundLayer": "cockpit-controls",
                                        "interaction": "pilot-shuttle",
                                        "interactKey": "E",
                                        "pausesCombat": true,
                                        "pilotStation": "helm-console"
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
                                        "lookaroundLayer": "cockpit-controls",
                                        "interaction": "pilot-shuttle",
                                        "interactKey": "E",
                                        "pausesCombat": true,
                                        "pilotStation": "science-console"
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
                                        "lookaroundLayer": "side-controls",
                                        "interaction": "pilot-shuttle",
                                        "interactKey": "E",
                                        "pausesCombat": true,
                                        "pilotStation": "port-console"
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
                                        "lookaroundLayer": "side-controls",
                                        "interaction": "pilot-shuttle",
                                        "interactKey": "E",
                                        "pausesCombat": true,
                                        "pilotStation": "starboard-console"
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
                              "restart": "r",
                              "interact": "mouse-over-console-e",
                              "pilot": "console-hover-e-flies-to-mother-ship"
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
                              "flight": {
                                        "enabled": true,
                                        "targetLabel": "Mother Ship",
                                        "startDistance": 36.5,
                                        "dockingDistance": 11.5,
                                        "maxForwardSpeed": 12.5,
                                        "maxReverseSpeed": 4.5,
                                        "acceleration": 2.8,
                                        "lateralSpeed": 4.2,
                                        "verticalSpeed": 2.6,
                                        "lateralLimit": 6.5,
                                        "verticalLimit": 2.2,
                                        "targetPosition": [
                                                  0.85,
                                                  1.0,
                                                  -36.5
                                        ]
                              },
                              "playerAnchor": "hero-sprite",
                              "controlsHint": "Mouse over console + E pilot • W/S throttle flies to Mother Ship • docking triggers shuttle-bay cutscene • Click/Space/F fire outside pilot",
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
                              },
                              "pilotStations": [
                                        {
                                                  "id": "helm-console",
                                                  "objectId": "nav-console",
                                                  "label": "Helm Console",
                                                  "role": "helm",
                                                  "bounds": {
                                                            "min": [
                                                                      -2.82,
                                                                      -1.35,
                                                                      -5.72
                                                            ],
                                                            "max": [
                                                                      -0.36,
                                                                      0.82,
                                                                      -3.42
                                                            ]
                                                  },
                                                  "glowBounds": {
                                                            "min": [
                                                                      -2.64,
                                                                      0.59,
                                                                      -5.49
                                                            ],
                                                            "max": [
                                                                      -0.66,
                                                                      0.72,
                                                                      -5.08
                                                            ]
                                                  },
                                                  "camera": {
                                                            "position": [
                                                                      -0.92,
                                                                      0.82,
                                                                      -2.82
                                                            ],
                                                            "yaw": -10,
                                                            "pitch": -7
                                                  },
                                                  "exitPosition": [
                                                            -0.92,
                                                            0.75,
                                                            -2.48
                                                  ],
                                                  "activationRange": 3.35
                                        },
                                        {
                                                  "id": "science-console",
                                                  "objectId": "science-console",
                                                  "label": "Science Console",
                                                  "role": "science",
                                                  "bounds": {
                                                            "min": [
                                                                      0.36,
                                                                      -1.35,
                                                                      -5.72
                                                            ],
                                                            "max": [
                                                                      2.82,
                                                                      0.82,
                                                                      -3.42
                                                            ]
                                                  },
                                                  "glowBounds": {
                                                            "min": [
                                                                      0.66,
                                                                      0.59,
                                                                      -5.49
                                                            ],
                                                            "max": [
                                                                      2.64,
                                                                      0.72,
                                                                      -5.08
                                                            ]
                                                  },
                                                  "camera": {
                                                            "position": [
                                                                      0.92,
                                                                      0.82,
                                                                      -2.82
                                                            ],
                                                            "yaw": 10,
                                                            "pitch": -7
                                                  },
                                                  "exitPosition": [
                                                            0.92,
                                                            0.75,
                                                            -2.48
                                                  ],
                                                  "activationRange": 3.35
                                        },
                                        {
                                                  "id": "port-console",
                                                  "objectId": "port-side-console",
                                                  "label": "Port Systems",
                                                  "role": "port-panel",
                                                  "bounds": {
                                                            "min": [
                                                                      -4.38,
                                                                      -0.72,
                                                                      -4.12
                                                            ],
                                                            "max": [
                                                                      -3.48,
                                                                      1.22,
                                                                      -0.86
                                                            ]
                                                  },
                                                  "glowBounds": {
                                                            "min": [
                                                                      -4.23,
                                                                      0.91,
                                                                      -3.66
                                                            ],
                                                            "max": [
                                                                      -3.68,
                                                                      1.04,
                                                                      -1.24
                                                            ]
                                                  },
                                                  "camera": {
                                                            "position": [
                                                                      -3.08,
                                                                      0.78,
                                                                      -2.18
                                                            ],
                                                            "yaw": -82,
                                                            "pitch": -4
                                                  },
                                                  "exitPosition": [
                                                            -3.08,
                                                            0.75,
                                                            -2.18
                                                  ],
                                                  "activationRange": 3.1
                                        },
                                        {
                                                  "id": "starboard-console",
                                                  "objectId": "starboard-side-console",
                                                  "label": "Starboard Ops",
                                                  "role": "starboard-panel",
                                                  "bounds": {
                                                            "min": [
                                                                      3.48,
                                                                      -0.72,
                                                                      -4.12
                                                            ],
                                                            "max": [
                                                                      4.38,
                                                                      1.22,
                                                                      -0.86
                                                            ]
                                                  },
                                                  "glowBounds": {
                                                            "min": [
                                                                      3.68,
                                                                      0.91,
                                                                      -3.66
                                                            ],
                                                            "max": [
                                                                      4.23,
                                                                      1.04,
                                                                      -1.24
                                                            ]
                                                  },
                                                  "camera": {
                                                            "position": [
                                                                      3.08,
                                                                      0.78,
                                                                      -2.18
                                                            ],
                                                            "yaw": 82,
                                                            "pitch": -4
                                                  },
                                                  "exitPosition": [
                                                            3.08,
                                                            0.75,
                                                            -2.18
                                                  ],
                                                  "activationRange": 3.1
                                        }
                              ]
                    },
                    "combatEnabled": true,
                    "healthHud": true,
                    "playerWeapon": "hand-phaser"
          }
};
        if (sceneId && sceneId !== scene.id) {
          return {...scene, id: sceneId, name: scene.name || "Shuttle Boarding Defense"};
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
          : [0, 0.75, 2.45];
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
          [-4.5, -1.45, -7.2],
          [-4.5, 2.05, -7.2],
          [-3.55, 3.15, -7.2],
          [3.55, 3.15, -7.2],
          [4.5, 2.05, -7.2],
          [4.5, -1.45, -7.2],
          [-4.5, -1.45, 4.8],
          [-4.5, 2.05, 4.8],
          [-3.55, 3.15, 4.8],
          [3.55, 3.15, 4.8],
          [4.5, 2.05, 4.8],
          [4.5, -1.45, 4.8]
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
          minZ: number(suppliedBounds.minZ, -6.12, -40, 40),
          maxZ: number(suppliedBounds.maxZ, 3.72, -40, 40)
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


      function shuttle3dVector3(value, fallback) {
        return Array.isArray(value)
          && value.length === 3
          && value.every((entry) => Number.isFinite(Number(entry)))
            ? value.map(Number)
            : fallback.slice();
      }

      function shuttle3dBoundsConfig(value, fallback) {
        const supplied = value && typeof value === "object" ? value : {};
        const minimum = shuttle3dVector3(supplied.min, fallback.min);
        const maximum = shuttle3dVector3(supplied.max, fallback.max);
        return {
          min: [
            Math.min(minimum[0], maximum[0]),
            Math.min(minimum[1], maximum[1]),
            Math.min(minimum[2], maximum[2])
          ],
          max: [
            Math.max(minimum[0], maximum[0]),
            Math.max(minimum[1], maximum[1]),
            Math.max(minimum[2], maximum[2])
          ]
        };
      }

      function shuttle3dPilotStationsConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.pilotStations;
        const number = (value, fallback, minimum, maximum) => {
          const parsed = Number(value);
          if (!Number.isFinite(parsed)) return fallback;
          return Math.min(maximum, Math.max(minimum, parsed));
        };
        const defaults = [
          {
            id: "helm-console",
            objectId: "nav-console",
            label: "Helm Console",
            role: "helm",
            bounds: {min: [-2.82, -1.35, -5.72], max: [-0.36, 0.82, -3.42]},
            glowBounds: {min: [-2.64, 0.59, -5.49], max: [-0.66, 0.72, -5.08]},
            camera: {position: [-0.92, 0.82, -2.82], yaw: -10, pitch: -7},
            exitPosition: [-0.92, 0.75, -2.48],
            activationRange: 3.35
          },
          {
            id: "science-console",
            objectId: "science-console",
            label: "Science Console",
            role: "science",
            bounds: {min: [0.36, -1.35, -5.72], max: [2.82, 0.82, -3.42]},
            glowBounds: {min: [0.66, 0.59, -5.49], max: [2.64, 0.72, -5.08]},
            camera: {position: [0.92, 0.82, -2.82], yaw: 10, pitch: -7},
            exitPosition: [0.92, 0.75, -2.48],
            activationRange: 3.35
          },
          {
            id: "port-console",
            objectId: "port-side-console",
            label: "Port Systems",
            role: "port-panel",
            bounds: {min: [-4.38, -0.72, -4.12], max: [-3.48, 1.22, -0.86]},
            glowBounds: {min: [-4.23, 0.91, -3.66], max: [-3.68, 1.04, -1.24]},
            camera: {position: [-3.08, 0.78, -2.18], yaw: -82, pitch: -4},
            exitPosition: [-3.08, 0.75, -2.18],
            activationRange: 3.1
          },
          {
            id: "starboard-console",
            objectId: "starboard-side-console",
            label: "Starboard Ops",
            role: "starboard-panel",
            bounds: {min: [3.48, -0.72, -4.12], max: [4.38, 1.22, -0.86]},
            glowBounds: {min: [3.68, 0.91, -3.66], max: [4.23, 1.04, -1.24]},
            camera: {position: [3.08, 0.78, -2.18], yaw: 82, pitch: -4},
            exitPosition: [3.08, 0.75, -2.18],
            activationRange: 3.1
          }
        ];
        const stations = Array.isArray(supplied) && supplied.length ? supplied : defaults;
        return stations
          .filter((station) => station && typeof station === "object")
          .map((station, index) => {
            const fallback = defaults[index] || defaults[0];
            const camera = station.camera && typeof station.camera === "object" ? station.camera : {};
            return {
              id: String(station.id || fallback.id || `pilot-console-${index + 1}`),
              objectId: String(station.objectId || station.object || fallback.objectId || station.id || `pilot-console-${index + 1}`),
              label: String(station.label || fallback.label || `Pilot Console ${index + 1}`),
              role: String(station.role || fallback.role || "pilot-console"),
              bounds: shuttle3dBoundsConfig(station.bounds, fallback.bounds),
              glowBounds: shuttle3dBoundsConfig(station.glowBounds || station.panelBounds, fallback.glowBounds || fallback.bounds),
              camera: {
                position: shuttle3dVector3(camera.position, fallback.camera.position),
                yaw: number(camera.yaw, fallback.camera.yaw, -180, 180),
                pitch: number(camera.pitch, fallback.camera.pitch, -45, 45)
              },
              exitPosition: shuttle3dVector3(station.exitPosition, fallback.exitPosition || fallback.camera.position),
              activationRange: number(station.activationRange, fallback.activationRange || 3.2, 0.5, 12)
            };
          });
      }


      function shuttle3dFlightConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.flight;
        const flight = supplied && typeof supplied === "object" ? supplied : {};
        const shuttle = scene?.metadata?.shuttle3d && typeof scene.metadata.shuttle3d === "object" ? scene.metadata.shuttle3d : {};
        const cutscene = flight.cutscene && typeof flight.cutscene === "object" ? flight.cutscene : {};
        const number = (value, fallback, minimum, maximum) => {
          const parsed = Number(value);
          if (!Number.isFinite(parsed)) return fallback;
          return Math.min(maximum, Math.max(minimum, parsed));
        };
        const startDistance = number(flight.startDistance, 36.5, 12, 240);
        const dockingDistance = Math.min(
          startDistance - 0.75,
          number(flight.dockingDistance, 11.5, 7.5, Math.max(8, startDistance - 0.75))
        );
        const targetPosition = shuttle3dVector3(flight.targetPosition, [0.85, 1.0, -startDistance]);
        return {
          enabled: flight.enabled !== false,
          targetLabel: String(flight.targetLabel || shuttle.motherShipLabel || "Mother Ship"),
          startDistance,
          dockingDistance,
          targetPosition: [targetPosition[0], targetPosition[1], -startDistance],
          maxForwardSpeed: number(flight.maxForwardSpeed, 12.5, 1, 60),
          maxReverseSpeed: number(flight.maxReverseSpeed, 4.5, 0.5, 30),
          acceleration: number(flight.acceleration, 2.8, 0.2, 16),
          lateralSpeed: number(flight.lateralSpeed, 4.2, 0.25, 24),
          verticalSpeed: number(flight.verticalSpeed, 2.6, 0.25, 18),
          lateralLimit: number(flight.lateralLimit, 6.5, 0.5, 28),
          verticalLimit: number(flight.verticalLimit, 2.2, 0.25, 12),
          cutscene: {
            enabled: cutscene.enabled !== false,
            durationMs: number(cutscene.durationMs, 9600, 3000, 30000),
            bayLabel: String(cutscene.bayLabel || "Mother Ship Shuttle Bay")
          }
        };
      }

      function shuttle3dCloneJson(value, fallback = {}) {
        const source = value !== undefined ? value : fallback;
        if (source && typeof source === "object") {
          return JSON.parse(JSON.stringify(source));
        }
        if (fallback && typeof fallback === "object") {
          return JSON.parse(JSON.stringify(fallback));
        }
        return source;
      }

      function shuttle3dObjectValue(value) {
        return value && typeof value === "object" && !Array.isArray(value) ? value : {};
      }

      function shuttle3dStringMap(value, fallback = {}) {
        const supplied = shuttle3dObjectValue(value);
        const source = {...fallback, ...supplied};
        return Object.fromEntries(Object.entries(source).map(([key, entry]) => [String(key), String(entry)]));
      }

      function shuttle3dObjectMap(value, fallback = {}) {
        const supplied = shuttle3dObjectValue(value);
        const source = {...fallback, ...supplied};
        return Object.fromEntries(
          Object.entries(source)
            .filter(([key]) => String(key).trim())
            .map(([key, entry]) => [String(key), shuttle3dCloneJson(entry)])
        );
      }

      function shuttle3dMotherShipInteriorStateDefaults() {
        return {
          schema: "game.motherShipInterior.stateDefaults.v1",
          location: "bay.shuttle",
          objectiveId: "objective.bay-ops",
          power: "emergency",
          security: "quarantine",
          locations: {
            "bay.shuttle": "Mother Ship Shuttle Bay",
            "bay.ops": "Bay Operations",
            "security.checkpoint": "Security Checkpoint",
            "corridor.main": "Main Corridor Hub",
            "engineering.access": "Engineering Access",
            "medbay.stub": "Medbay Triage",
            "science.ops.stub": "Science/Ops Lab",
            "bridge.access": "Bridge Access",
            "bridge.deck": "Bridge Deck"
          },
          objectives: {
            "objective.bay-ops": {
              label: "Use the starboard interior access and bring Bay Operations online.",
              location: "bay.shuttle"
            },
            "objective.enter-corridor": {
              label: "Enter the main corridor.",
              location: "bay.ops"
            },
            "objective.restore-power": {
              label: "Reach Engineering Access and restore main power.",
              location: "corridor.main"
            },
            "objective.survey-departments": {
              label: "Survey medbay and science, then proceed to the bridge.",
              location: "engineering.access"
            },
            "objective.bridge-access": {
              label: "Proceed to the bridge and inspect the main viewscreen.",
              location: "corridor.main"
            },
            "objective.bridge-screen": {
              label: "Use the bridge viewscreen controls to identify the current target.",
              location: "bridge.deck"
            },
            "objective.enemy-track": {
              label: "Enemy raider centered on the bridge viewscreen.",
              location: "bridge.deck"
            },
            "objective.enemy-attack": {
              label: "Fire the bridge tactical console at the enemy raider.",
              location: "bridge.deck"
            },
            "objective.enemy-disabled": {
              label: "Enemy raider destroyed. Open navigation and choose the next system.",
              location: "bridge.deck"
            },
            "objective.planet-view": {
              label: "Center the current system planet on the bridge viewscreen.",
              location: "bridge.deck"
            },
            "objective.planet-scan": {
              label: "Run a planetary scan from the bridge tactical/sensor console.",
              location: "bridge.deck"
            },
            "objective.planet-surveyed": {
              label: "Planetary survey complete. Open navigation and choose the next system.",
              location: "bridge.deck"
            }
          },
          doors: {
            "door.bay-access": {
              label: "Starboard Interior Access",
              from: "bay.shuttle",
              to: "bay.ops",
              state: "open"
            },
            "door.bay-inner": {
              label: "Inner Shuttle Bay Door",
              from: "bay.ops",
              to: "security.checkpoint",
              state: "open"
            },
            "door.security-hub": {
              label: "Security Checkpoint Door",
              from: "security.checkpoint",
              to: "corridor.main",
              state: "open"
            },
            "door.engineering-access": {
              label: "Engineering Access Door",
              from: "corridor.main",
              to: "engineering.access",
              state: "open"
            },
            "door.medbay": {
              label: "Medbay Door",
              from: "corridor.main",
              to: "medbay.stub",
              state: "open"
            },
            "door.science": {
              label: "Science/Ops Door",
              from: "corridor.main",
              to: "science.ops.stub",
              state: "open"
            },
            "door.bridge": {
              label: "Bridge Command Door",
              from: "corridor.main",
              to: "bridge.deck",
              state: "open"
            }
          },
          terminals: {
            "terminal.bay-ops": {
              label: "Bay Operations Terminal",
              location: "bay.ops",
              state: "offline"
            },
            "terminal.engineering-power": {
              label: "Engineering Power Console",
              location: "engineering.access",
              state: "offline"
            },
            "terminal.bridge-viewscreen": {
              label: "Bridge Viewscreen",
              location: "bridge.deck",
              state: "standby"
            },
            "terminal.bridge-tactical": {
              label: "Bridge Tactical Console / Sensor Array",
              location: "bridge.deck",
              state: "ready"
            }
          },
          flags: {
            bayControlActive: true,
            boardersPausedAfterDocking: true,
            bridgeViewscreenTrackingActive: false,
            bridgeTacticalArmed: false,
            bridgeTacticalShotsFired: 0,
            bridgeTacticalLastFireAtMs: 0,
            currentSystemPlanetSurveyed: false,
            lastSurveyedPlanetId: "",
            lastSurveyedSystemId: "",
            planetScansCompleted: 0,
            planetScanLastAtMs: 0,
            enemyShipHullPercent: 100,
            enemyShipDisabled: false
          }
        };
      }

      function shuttle3dNormalizeMotherShipFlags(value) {
        const defaults = shuttle3dMotherShipInteriorStateDefaults().flags;
        const flags = {...defaults, ...shuttle3dObjectValue(value)};
        if (!Number.isFinite(Number(flags.enemyShipHullPercent))) flags.enemyShipHullPercent = defaults.enemyShipHullPercent;
        flags.enemyShipHullPercent = Math.max(0, Math.min(100, Number(flags.enemyShipHullPercent)));
        if (!Number.isFinite(Number(flags.bridgeTacticalShotsFired))) flags.bridgeTacticalShotsFired = defaults.bridgeTacticalShotsFired;
        flags.bridgeTacticalShotsFired = Math.max(0, Number(flags.bridgeTacticalShotsFired));
        if (!Number.isFinite(Number(flags.bridgeTacticalLastFireAtMs))) flags.bridgeTacticalLastFireAtMs = defaults.bridgeTacticalLastFireAtMs;
        flags.bridgeTacticalLastFireAtMs = Math.max(0, Number(flags.bridgeTacticalLastFireAtMs));
        if (!Number.isFinite(Number(flags.planetScansCompleted))) flags.planetScansCompleted = defaults.planetScansCompleted;
        flags.planetScansCompleted = Math.max(0, Number(flags.planetScansCompleted));
        if (!Number.isFinite(Number(flags.planetScanLastAtMs))) flags.planetScanLastAtMs = defaults.planetScanLastAtMs;
        flags.planetScanLastAtMs = Math.max(0, Number(flags.planetScanLastAtMs));
        flags.lastSurveyedPlanetId = String(flags.lastSurveyedPlanetId || "");
        flags.lastSurveyedSystemId = String(flags.lastSurveyedSystemId || "");
        if (typeof flags.currentSystemPlanetSurveyed !== "boolean") flags.currentSystemPlanetSurveyed = Boolean(defaults.currentSystemPlanetSurveyed);
        if (typeof flags.bridgeTacticalArmed !== "boolean") flags.bridgeTacticalArmed = Boolean(defaults.bridgeTacticalArmed);
        if (typeof flags.bridgeViewscreenTrackingActive !== "boolean") flags.bridgeViewscreenTrackingActive = Boolean(defaults.bridgeViewscreenTrackingActive);
        if (typeof flags.bayControlActive !== "boolean") flags.bayControlActive = Boolean(defaults.bayControlActive);
        if (typeof flags.boardersPausedAfterDocking !== "boolean") flags.boardersPausedAfterDocking = Boolean(defaults.boardersPausedAfterDocking);
        if (typeof flags.enemyShipDisabled !== "boolean") flags.enemyShipDisabled = flags.enemyShipHullPercent <= 0;
        return flags;
      }

      function shuttle3dNormalizeMotherShipDoors(value) {
        const doors = shuttle3dObjectMap(value, {});
        Object.values(doors).forEach((door) => {
          if (!door || typeof door !== "object") return;
          // Patch B keeps the no-locked-door rule centralized with the default state factory.
          if (String(door.state || "").toLowerCase() === "locked") door.state = "open";
        });
        return doors;
      }


      function shuttle3dMotherShipInteriorLevelDefaults() {
        return {
          schema: "game.motherShipInterior.level.v1",
          movement: {
            // Patch C keeps the playable movement envelope in level data.
            // It must cover every room, including the bridge deck and viewscreen.
            bounds: {
              minX: -9.8,
              maxX: 9.8,
              minZ: -39.65,
              maxZ: 5.12
            },
            wallCollision: {
              enabled: true,
              thickness: 0.12
            },
            colliders: [
              {
                id: "docked-shuttle-hull",
                minX: -1.36,
                maxX: 1.36,
                minZ: -1.42,
                maxZ: 1.44
              }
            ]
          },
          spawns: {
            "spawn.shuttle-bay": {
              id: "spawn.shuttle-bay",
              room: "bay.shuttle",
              label: "Mother Ship Shuttle Bay arrival",
              position: [0.24, 0.9, 4.3],
              // Face into the mother ship and toward the starboard/right side of the bay.
              // In bay coordinates, shipside is negative Z and bay-right is positive X.
              yaw: 32,
              pitch: -4
            }
          },
          rooms: [
            {
                        "id": "bay.shuttle",
                        "name": "Mother Ship Shuttle Bay",
                        "location": "bay.shuttle",
                        "kind": "shuttle-bay",
                        "priority": 100,
                        "bounds": {
                                    "minX": -4.72,
                                    "maxX": 4.72,
                                    "minZ": -4.62,
                                    "maxZ": 5.12
                        },
                        "visual": {
                                    "color": "#38bdf8",
                                    "edgeColor": "#60a5fa",
                                    "labelColor": "#bfdbfe",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -5.2,
                                                            "maxX": 5.2,
                                                            "minZ": -5.25,
                                                            "maxZ": 6.2
                                                },
                                                "accentColor": "#67e8f9"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -5.2,
                                                            "minZ": -5.25,
                                                            "maxZ": 6.2
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": 5.2,
                                                            "minZ": -5.25,
                                                            "maxZ": 6.2
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": 5.72,
                                                            "minX": -5.25,
                                                            "maxX": -2.75
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": 5.72,
                                                            "minX": 2.75,
                                                            "maxX": 5.25
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": 5.72,
                                                            "minX": -1.65,
                                                            "maxX": 1.65
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -5.05,
                                                            "minX": -5.2,
                                                            "maxX": -0.82
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -5.05,
                                                            "minX": 0.82,
                                                            "maxX": 2.15
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -5.05,
                                                            "minX": 4.45,
                                                            "maxX": 5.2
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.bay-aft-port",
                                                            "edge": "aft",
                                                            "bounds": {
                                                                        "minX": -2.75,
                                                                        "maxX": -1.65,
                                                                        "minZ": 5.55,
                                                                        "maxZ": 5.9
                                                            }
                                                },
                                                {
                                                            "id": "opening.bay-aft-starboard",
                                                            "edge": "aft",
                                                            "bounds": {
                                                                        "minX": 1.65,
                                                                        "maxX": 2.75,
                                                                        "minZ": 5.55,
                                                                        "maxZ": 5.9
                                                            }
                                                },
                                                {
                                                            "id": "opening.bay-access",
                                                            "exit": "exit.bay-access",
                                                            "door": "door.bay-access"
                                                }
                                    ],
                                    "doorPanels": [
                                                {
                                                            "door": "door.bay-access",
                                                            "center": [
                                                                        3.3,
                                                                        -5.04
                                                            ],
                                                            "width": 2.22,
                                                            "vertical": false
                                                }
                                    ]
                        }
            },
            {
                        "id": "bay.ops",
                        "name": "Bay Operations",
                        "location": "bay.ops",
                        "kind": "operations",
                        "priority": 60,
                        "bounds": {
                                    "minX": -2.25,
                                    "maxX": 4.9,
                                    "minZ": -9.45,
                                    "maxZ": -4.2
                        },
                        "visual": {
                                    "color": "#38bdf8",
                                    "edgeColor": "#22d3ee",
                                    "labelColor": "#bae6fd",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -2.35,
                                                            "maxX": 5.05,
                                                            "minZ": -9.65,
                                                            "maxZ": -4.35
                                                },
                                                "accentColor": "#38bdf8"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": 5.05,
                                                            "minZ": -9.65,
                                                            "maxZ": -4.35
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": -2.35,
                                                            "minZ": -9.65,
                                                            "maxZ": -8.1
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -9.65,
                                                            "minX": -2.35,
                                                            "maxX": -1.14
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -9.65,
                                                            "minX": 1.14,
                                                            "maxX": 5.05
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -4.35,
                                                            "minX": -2.35,
                                                            "maxX": 2.12
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -4.35,
                                                            "minX": 4.45,
                                                            "maxX": 5.05
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.bay-inner",
                                                            "exit": "exit.bay-inner",
                                                            "door": "door.bay-inner"
                                                },
                                                {
                                                            "id": "opening.bay-ops-to-bay",
                                                            "exit": "exit.bay-access",
                                                            "door": "door.bay-access"
                                                }
                                    ],
                                    "boxes": [
                                                {
                                                            "min": [
                                                                        -1.18,
                                                                        -1.06,
                                                                        -9.72
                                                            ],
                                                            "max": [
                                                                        1.18,
                                                                        -0.92,
                                                                        -8.62
                                                            ],
                                                            "color": "#243244",
                                                            "emissive": false
                                                },
                                                {
                                                            "min": [
                                                                        -1.42,
                                                                        -1.04,
                                                                        -9.76
                                                            ],
                                                            "max": [
                                                                        -1.16,
                                                                        2.12,
                                                                        -8.58
                                                            ],
                                                            "color": "#475569",
                                                            "emissive": false
                                                },
                                                {
                                                            "min": [
                                                                        1.16,
                                                                        -1.04,
                                                                        -9.76
                                                            ],
                                                            "max": [
                                                                        1.42,
                                                                        2.12,
                                                                        -8.58
                                                            ],
                                                            "color": "#475569",
                                                            "emissive": false
                                                },
                                                {
                                                            "min": [
                                                                        -1.42,
                                                                        2.12,
                                                                        -9.76
                                                            ],
                                                            "max": [
                                                                        1.42,
                                                                        2.48,
                                                                        -8.58
                                                            ],
                                                            "color": "#475569",
                                                            "emissive": false
                                                }
                                    ],
                                    "beams": [
                                                {
                                                            "start": [
                                                                        -1.04,
                                                                        -0.72,
                                                                        -9.42
                                                            ],
                                                            "end": [
                                                                        1.04,
                                                                        -0.72,
                                                                        -9.42
                                                            ],
                                                            "radius": 0.026,
                                                            "color": "#38bdf8",
                                                            "emissive": true
                                                },
                                                {
                                                            "start": [
                                                                        -1.04,
                                                                        -0.72,
                                                                        -8.86
                                                            ],
                                                            "end": [
                                                                        1.04,
                                                                        -0.72,
                                                                        -8.86
                                                            ],
                                                            "radius": 0.026,
                                                            "color": "#38bdf8",
                                                            "emissive": true
                                                },
                                                {
                                                            "start": [
                                                                        -1.08,
                                                                        1.74,
                                                                        -9.54
                                                            ],
                                                            "end": [
                                                                        1.08,
                                                                        1.74,
                                                                        -9.54
                                                            ],
                                                            "radius": 0.028,
                                                            "color": "#67e8f9",
                                                            "emissive": true
                                                },
                                                {
                                                            "start": [
                                                                        -1.08,
                                                                        0.4,
                                                                        -9.55
                                                            ],
                                                            "end": [
                                                                        -1.08,
                                                                        0.4,
                                                                        -8.72
                                                            ],
                                                            "radius": 0.022,
                                                            "color": "#86efac",
                                                            "emissive": true
                                                },
                                                {
                                                            "start": [
                                                                        1.08,
                                                                        0.4,
                                                                        -9.55
                                                            ],
                                                            "end": [
                                                                        1.08,
                                                                        0.4,
                                                                        -8.72
                                                            ],
                                                            "radius": 0.022,
                                                            "color": "#86efac",
                                                            "emissive": true
                                                }
                                    ]
                        }
            },
            {
                        "id": "security.checkpoint",
                        "name": "Security Checkpoint",
                        "location": "security.checkpoint",
                        "kind": "checkpoint",
                        "priority": 75,
                        "bounds": {
                                    "minX": -3.2,
                                    "maxX": 3.2,
                                    "minZ": -13.55,
                                    "maxZ": -8.75
                        },
                        "visual": {
                                    "color": "#f59e0b",
                                    "edgeColor": "#fbbf24",
                                    "labelColor": "#fef3c7",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -3.25,
                                                            "maxX": 3.25,
                                                            "minZ": -13.65,
                                                            "maxZ": -8.75
                                                },
                                                "accentColor": "#fbbf24"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -3.25,
                                                            "minZ": -13.65,
                                                            "maxZ": -8.75
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": 3.25,
                                                            "minZ": -13.65,
                                                            "maxZ": -8.75
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -8.85,
                                                            "minX": -3.25,
                                                            "maxX": -0.82
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -8.85,
                                                            "minX": 0.82,
                                                            "maxX": 3.25
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.security-bay-inner",
                                                            "exit": "exit.bay-inner",
                                                            "door": "door.bay-inner"
                                                },
                                                {
                                                            "id": "opening.security-hub",
                                                            "exit": "exit.security-hub",
                                                            "door": "door.security-hub"
                                                }
                                    ],
                                    "doorPanels": [
                                                {
                                                            "door": "door.bay-inner",
                                                            "center": [
                                                                        0,
                                                                        -8.88
                                                            ],
                                                            "width": 1.64,
                                                            "vertical": false
                                                }
                                    ]
                        }
            },
            {
                        "id": "corridor.main",
                        "name": "Main Corridor Hub",
                        "location": "corridor.main",
                        "kind": "corridor",
                        "priority": 40,
                        "bounds": {
                                    "minX": -6.55,
                                    "maxX": 6.55,
                                    "minZ": -18.75,
                                    "maxZ": -13.25
                        },
                        "visual": {
                                    "color": "#94a3b8",
                                    "edgeColor": "#64748b",
                                    "labelColor": "#e2e8f0",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -6.75,
                                                            "maxX": 6.75,
                                                            "minZ": -18.9,
                                                            "maxZ": -13.25
                                                },
                                                "accentColor": "#67e8f9"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -6.75,
                                                            "minZ": -18.9,
                                                            "maxZ": -13.25
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": 6.75,
                                                            "minZ": -18.9,
                                                            "maxZ": -13.25
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -13.35,
                                                            "minX": -6.75,
                                                            "maxX": -0.8
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -13.35,
                                                            "minX": 0.8,
                                                            "maxX": 6.75
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.corridor-security",
                                                            "exit": "exit.security-hub",
                                                            "door": "door.security-hub"
                                                },
                                                {
                                                            "id": "opening.corridor-engineering",
                                                            "exit": "exit.corridor-engineering",
                                                            "door": "door.engineering-access"
                                                },
                                                {
                                                            "id": "opening.corridor-medbay",
                                                            "exit": "exit.corridor-medbay",
                                                            "door": "door.medbay"
                                                },
                                                {
                                                            "id": "opening.corridor-science",
                                                            "exit": "exit.corridor-science",
                                                            "door": "door.science"
                                                },
                                                {
                                                            "id": "opening.corridor-bridge",
                                                            "exit": "exit.corridor-bridge",
                                                            "door": "door.bridge"
                                                }
                                    ],
                                    "doorPanels": [
                                                {
                                                            "door": "door.security-hub",
                                                            "center": [
                                                                        0,
                                                                        -13.36
                                                            ],
                                                            "width": 1.6,
                                                            "vertical": false
                                                },
                                                {
                                                            "door": "door.engineering-access",
                                                            "center": [
                                                                        3.18,
                                                                        -17.85
                                                            ],
                                                            "width": 2.1,
                                                            "vertical": true
                                                },
                                                {
                                                            "door": "door.medbay",
                                                            "center": [
                                                                        -3.18,
                                                                        -17.85
                                                            ],
                                                            "width": 2.1,
                                                            "vertical": true
                                                }
                                    ],
                                    "beams": [
                                                {
                                                            "start": [
                                                                        -5.65,
                                                                        -0.88,
                                                                        -16.3
                                                            ],
                                                            "end": [
                                                                        5.65,
                                                                        -0.88,
                                                                        -16.3
                                                            ],
                                                            "radius": 0.026,
                                                            "color": "#67e8f9",
                                                            "emissive": true
                                                }
                                    ]
                        }
            },
            {
                        "id": "corridor.trunk",
                        "name": "Main Corridor Trunk",
                        "location": "corridor.main",
                        "kind": "corridor",
                        "priority": 45,
                        "bounds": {
                                    "minX": -2.55,
                                    "maxX": 2.55,
                                    "minZ": -25.85,
                                    "maxZ": -13.25
                        },
                        "visual": {
                                    "color": "#94a3b8",
                                    "edgeColor": "#64748b",
                                    "labelColor": "#e2e8f0",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -2.55,
                                                            "maxX": 2.55,
                                                            "minZ": -25.85,
                                                            "maxZ": -13.25
                                                },
                                                "floor": true,
                                                "ceiling": false,
                                                "accentColor": "#67e8f9"
                                    },
                                    "walls": [],
                                    "boxes": [
                                      {
                                        "min": [
                                          -2.72,
                                          -1.04,
                                          -25.7
                                        ],
                                        "max": [
                                          -2.54,
                                          0.68,
                                          -13.35
                                        ],
                                        "color": "#334155"
                                      },
                                      {
                                        "min": [
                                          2.54,
                                          -1.04,
                                          -25.7
                                        ],
                                        "max": [
                                          2.72,
                                          0.68,
                                          -13.35
                                        ],
                                        "color": "#334155"
                                      },
                                      {
                                        "min": [
                                          -2.42,
                                          -1.052,
                                          -25.45
                                        ],
                                        "max": [
                                          -2.18,
                                          -0.94,
                                          -13.55
                                        ],
                                        "color": "#38bdf8",
                                        "emissive": true
                                      },
                                      {
                                        "min": [
                                          2.18,
                                          -1.052,
                                          -25.45
                                        ],
                                        "max": [
                                          2.42,
                                          -0.94,
                                          -13.55
                                        ],
                                        "color": "#38bdf8",
                                        "emissive": true
                                      }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.trunk-hub",
                                                            "exit": "exit.security-hub"
                                                },
                                                {
                                                            "id": "opening.trunk-bridge",
                                                            "exit": "exit.corridor-bridge",
                                                            "door": "door.bridge"
                                                }
                                    ],
                                    "beams": [
                                                {
                                                            "start": [
                                                                        0,
                                                                        -0.88,
                                                                        -13.7
                                                            ],
                                                            "end": [
                                                                        0,
                                                                        -0.88,
                                                                        -25.6
                                                            ],
                                                            "radius": 0.026,
                                                            "color": "#67e8f9",
                                                            "emissive": true
                                                },
                                                {
                                                  "start": [
                                                    -2.46,
                                                    0.18,
                                                    -25.6
                                                  ],
                                                  "end": [
                                                    -2.46,
                                                    0.18,
                                                    -13.45
                                                  ],
                                                  "radius": 0.022,
                                                  "color": "#38bdf8",
                                                  "emissive": true
                                                },
                                                {
                                                  "start": [
                                                    2.46,
                                                    0.18,
                                                    -25.6
                                                  ],
                                                  "end": [
                                                    2.46,
                                                    0.18,
                                                    -13.45
                                                  ],
                                                  "radius": 0.022,
                                                  "color": "#38bdf8",
                                                  "emissive": true
                                                },
                                                {
                                                  "start": [
                                                    -1.8,
                                                    1.28,
                                                    -25.4
                                                  ],
                                                  "end": [
                                                    1.8,
                                                    1.28,
                                                    -25.4
                                                  ],
                                                  "radius": 0.026,
                                                  "color": "#67e8f9",
                                                  "emissive": true
                                                },
                                                {
                                                  "start": [
                                                    -1.8,
                                                    1.28,
                                                    -19.0
                                                  ],
                                                  "end": [
                                                    1.8,
                                                    1.28,
                                                    -19.0
                                                  ],
                                                  "radius": 0.026,
                                                  "color": "#67e8f9",
                                                  "emissive": true
                                                }
                                    ]
                        }
            },
            {
                        "id": "engineering.access",
                        "name": "Engineering Access",
                        "location": "engineering.access",
                        "kind": "engineering",
                        "priority": 80,
                        "bounds": {
                                    "minX": 2.0,
                                    "maxX": 9.8,
                                    "minZ": -24.25,
                                    "maxZ": -17.15
                        },
                        "visual": {
                                    "color": "#fbbf24",
                                    "edgeColor": "#f59e0b",
                                    "labelColor": "#fde68a",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": 2.0,
                                                            "maxX": 9.9,
                                                            "minZ": -24.35,
                                                            "maxZ": -17.05
                                                },
                                                "accentColor": "#86efac"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": 9.9,
                                                            "minZ": -24.35,
                                                            "maxZ": -17.05
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -24.35,
                                                            "minX": 2.0,
                                                            "maxX": 9.9
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.engineering-corridor",
                                                            "exit": "exit.corridor-engineering",
                                                            "door": "door.engineering-access"
                                                }
                                    ]
                        }
            },
            {
                        "id": "medbay.stub",
                        "name": "Medbay Triage",
                        "location": "medbay.stub",
                        "kind": "medbay",
                        "priority": 80,
                        "bounds": {
                                    "minX": -9.8,
                                    "maxX": -2.0,
                                    "minZ": -24.25,
                                    "maxZ": -17.15
                        },
                        "visual": {
                                    "color": "#fca5a5",
                                    "edgeColor": "#f87171",
                                    "labelColor": "#fee2e2",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -9.9,
                                                            "maxX": -2.0,
                                                            "minZ": -24.35,
                                                            "maxZ": -17.05
                                                },
                                                "accentColor": "#fca5a5"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -9.9,
                                                            "minZ": -24.35,
                                                            "maxZ": -17.05
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -24.35,
                                                            "minX": -9.9,
                                                            "maxX": -2.0
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.medbay-corridor",
                                                            "exit": "exit.corridor-medbay",
                                                            "door": "door.medbay"
                                                }
                                    ]
                        }
            },
            {
                        "id": "science.ops.stub",
                        "name": "Science/Ops Lab",
                        "location": "science.ops.stub",
                        "kind": "science",
                        "priority": 80,
                        "bounds": {
                                    "minX": -9.8,
                                    "maxX": -2.0,
                                    "minZ": -31.5,
                                    "maxZ": -24.0
                        },
                        "visual": {
                                    "color": "#a78bfa",
                                    "edgeColor": "#8b5cf6",
                                    "labelColor": "#ede9fe",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -9.9,
                                                            "maxX": -2.0,
                                                            "minZ": -31.5,
                                                            "maxZ": -24.0
                                                },
                                                "accentColor": "#a78bfa"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -9.9,
                                                            "minZ": -31.5,
                                                            "maxZ": -24.0
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -31.5,
                                                            "minX": -9.9,
                                                            "maxX": -2.0
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.science-corridor",
                                                            "exit": "exit.corridor-science",
                                                            "door": "door.science"
                                                }
                                    ],
                                    "doorPanels": [
                                                {
                                                            "door": "door.science",
                                                            "center": [
                                                                        -3.18,
                                                                        -25.0
                                                            ],
                                                            "width": 2.1,
                                                            "vertical": true
                                                }
                                    ]
                        }
            },
            {
                        "id": "bridge.access",
                        "name": "Bridge Access",
                        "location": "bridge.access",
                        "kind": "bridge-access",
                        "priority": 90,
                        "bounds": {
                                    "minX": -2.9,
                                    "maxX": 2.9,
                                    "minZ": -32.25,
                                    "maxZ": -25.45
                        },
                        "visual": {
                                    "color": "#86efac",
                                    "edgeColor": "#22c55e",
                                    "labelColor": "#dcfce7",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -2.95,
                                                            "maxX": 2.95,
                                                            "minZ": -32.25,
                                                            "maxZ": -25.35
                                                },
                                                "accentColor": "#fbbf24"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -2.95,
                                                            "minZ": -32.25,
                                                            "maxZ": -25.35
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": 2.95,
                                                            "minZ": -32.25,
                                                            "maxZ": -25.35
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -32.25,
                                                            "minX": -2.95,
                                                            "maxX": -1.12
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -32.25,
                                                            "minX": 1.12,
                                                            "maxX": 2.95
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.bridge-door",
                                                            "exit": "exit.corridor-bridge",
                                                            "door": "door.bridge"
                                                },
                                                {
                                                            "id": "opening.bridge-throat",
                                                            "exit": "exit.bridge-deck"
                                                }
                                    ],
                                    "doorPanels": [
                                                {
                                                            "door": "door.bridge",
                                                            "center": [
                                                                        0,
                                                                        -25.72
                                                            ],
                                                            "width": 2.5,
                                                            "vertical": false
                                                }
                                    ]
                        }
            },
            {
                        "id": "bridge.deck",
                        "name": "Bridge Deck",
                        "location": "bridge.deck",
                        "kind": "bridge",
                        "priority": 110,
                        "bounds": {
                                    "minX": -4.65,
                                    "maxX": 4.65,
                                    "minZ": -39.35,
                                    "maxZ": -31.25
                        },
                        "visual": {
                                    "color": "#ef4444",
                                    "edgeColor": "#f97316",
                                    "labelColor": "#fee2e2",
                                    "boundary": true,
                                    "floorBand": true,
                                    "label": true,
                                    "labelHeight": 0.42
                        },
                        "geometry": {
                                    "schema": "game.room.geometry.v1",
                                    "shell": {
                                                "bounds": {
                                                            "minX": -4.8,
                                                            "maxX": 4.8,
                                                            "minZ": -39.5,
                                                            "maxZ": -31.25
                                                },
                                                "accentColor": "#0ea5e9"
                                    },
                                    "walls": [
                                                {
                                                            "axis": "x",
                                                            "x": -4.8,
                                                            "minZ": -39.5,
                                                            "maxZ": -31.25
                                                },
                                                {
                                                            "axis": "x",
                                                            "x": 4.8,
                                                            "minZ": -39.5,
                                                            "maxZ": -31.25
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -31.25,
                                                            "minX": -4.8,
                                                            "maxX": -1.12
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -31.25,
                                                            "minX": 1.12,
                                                            "maxX": 4.8
                                                },
                                                {
                                                            "axis": "z",
                                                            "z": -39.5,
                                                            "minX": -4.8,
                                                            "maxX": 4.8
                                                }
                                    ],
                                    "openings": [
                                                {
                                                            "id": "opening.bridge-deck-throat",
                                                            "exit": "exit.bridge-deck"
                                                }
                                    ],
                                    "beams": [
                                                {
                                                            "start": [
                                                                        -3.7,
                                                                        2.3,
                                                                        -32.0
                                                            ],
                                                            "end": [
                                                                        3.7,
                                                                        2.3,
                                                                        -32.0
                                                            ],
                                                            "radius": 0.018,
                                                            "color": "#67e8f9",
                                                            "emissive": true
                                                },
                                                {
                                                            "start": [
                                                                        -3.7,
                                                                        2.3,
                                                                        -38.9
                                                            ],
                                                            "end": [
                                                                        3.7,
                                                                        2.3,
                                                                        -38.9
                                                            ],
                                                            "radius": 0.018,
                                                            "color": "#67e8f9",
                                                            "emissive": true
                                                }
                                    ]
                        }
            }
],
          exits: [
            {id: "exit.bay-access", from: "bay.shuttle", to: "bay.ops", door: "door.bay-access", bounds: {minX: 2.05, maxX: 4.55, minZ: -5.2, maxZ: -4.2}},
            {id: "exit.bay-inner", from: "bay.ops", to: "security.checkpoint", door: "door.bay-inner", bounds: {minX: -1.2, maxX: 1.2, minZ: -9.65, maxZ: -8.75}},
            {id: "exit.security-hub", from: "security.checkpoint", to: "corridor.main", door: "door.security-hub", bounds: {minX: -1.35, maxX: 1.35, minZ: -13.65, maxZ: -13.25}},
            {id: "exit.corridor-engineering", from: "corridor.main", to: "engineering.access", door: "door.engineering-access", bounds: {minX: 1.95, maxX: 3.85, minZ: -18.8, maxZ: -17.05}},
            {id: "exit.corridor-medbay", from: "corridor.main", to: "medbay.stub", door: "door.medbay", bounds: {minX: -3.85, maxX: -1.95, minZ: -18.8, maxZ: -17.05}},
            {id: "exit.corridor-science", from: "corridor.main", to: "science.ops.stub", door: "door.science", bounds: {minX: -3.85, maxX: -1.95, minZ: -25.2, maxZ: -23.95}},
            {id: "exit.corridor-bridge", from: "corridor.main", to: "bridge.access", door: "door.bridge", bounds: {minX: -1.6, maxX: 1.6, minZ: -25.9, maxZ: -25.35}},
            {id: "exit.bridge-deck", from: "bridge.access", to: "bridge.deck", bounds: {minX: -2.9, maxX: 2.9, minZ: -32.25, maxZ: -31.25}}
          ],
          // Patch H makes ship visual content data-first. Patch I also moves
          // repeated map/console markers into props so room decoration can
          // evolve without adding more one-off renderer calls. Patch M moves
          // visible terminal console bodies into data-defined props. Patch N
          // adds room visual metadata for the data-driven room boundary pass.
          props: [
            {
              id: "prop.route.bridge-marker",
              room: "corridor.trunk",
              kind: "floor-marker",
              position: [0.0, -23.35],
              size: [1.35, 0.5],
              color: "#38bdf8",
              emissive: true,
              label: "Bridge route marker"
            },
            {
              id: "prop.sign.bridge",
              room: "corridor.main",
              kind: "sign",
              position: [0.0, -16.85],
              size: [2.1, 0.62],
              color: "#38bdf8",
              emissive: true,
              facing: "south",
              label: "Bridge"
            },
            {
              id: "prop.sign.medbay",
              room: "corridor.main",
              kind: "sign",
              position: [-4.42, -17.55],
              size: [1.65, 0.54],
              color: "#fca5a5",
              emissive: true,
              facing: "east",
              label: "Medbay"
            },
            {
              id: "prop.sign.engineering",
              room: "corridor.main",
              kind: "sign",
              position: [4.42, -17.55],
              size: [1.65, 0.54],
              color: "#fbbf24",
              emissive: true,
              facing: "west",
              label: "Engineering"
            },
            {
              id: "prop.bridge-access-beacon",
              room: "bridge.access",
              kind: "beacon",
              position: [0.0, -29.15],
              size: [0.42, 1.55],
              color: "#86efac",
              emissive: true,
              label: "Bridge access beacon"
            },
            {
              id: "prop.bridge-tactical-marker",
              room: "bridge.deck",
              kind: "floor-marker",
              position: [2.85, -36.7],
              size: [1.05, 0.52],
              color: "#f97316",
              emissive: true,
              label: "Planetary sensor console marker"
            },
            {
              id: "prop.display.bridge-viewscreen",
              room: "bridge.deck",
              kind: "viewscreen",
              position: [0.0, -39.12],
              size: [6.9, 2.1, 0.08],
              color: "#38bdf8",
              emissive: true,
              facing: "north",
              display: "systemPlanet",
              target: "currentSystemPlanet",
              label: "Bridge current-system planetary viewscreen"
            },
            {
              id: "prop.bridge-viewscreen-status",
              room: "bridge.deck",
              kind: "status-panel",
              position: [-3.68, -36.62],
              size: [0.92, 0.56],
              color: "#38bdf8",
              emissive: true,
              target: "currentSystemPlanet",
              label: "Current planet status panel"
            },
            {
              id: "prop.console.bay-ops-terminal",
              room: "bay.ops",
              kind: "terminal-console",
              position: [3.86, -6.42],
              size: [0.82, 0.48, 0.72],
              color: "#38bdf8",
              emissive: true,
              facing: "west",
              target: "terminal.bay-ops",
              label: "Bay Operations terminal console"
            },
            {
              id: "prop.console.engineering-power",
              room: "engineering.access",
              kind: "terminal-console",
              position: [7.35, -20.75],
              size: [0.9, 0.5, 0.78],
              color: "#86efac",
              emissive: true,
              facing: "west",
              target: "terminal.engineering-power",
              label: "Engineering power console body"
            },
            {
              id: "prop.console.bridge-viewscreen",
              room: "bridge.deck",
              kind: "terminal-console",
              position: [0.0, -37.15],
              size: [1.25, 0.42, 0.74],
              color: "#ef4444",
              emissive: true,
              facing: "south",
              target: "terminal.bridge-viewscreen",
              label: "Bridge viewscreen control console"
            },
            {
              id: "prop.console.bridge-tactical",
              room: "bridge.deck",
              kind: "terminal-console",
              position: [2.85, -36.7],
              size: [0.95, 0.54, 0.82],
              color: "#f97316",
              emissive: true,
              facing: "west",
              target: "terminal.bridge-tactical",
              label: "Bridge planetary sensor console body"
            },
            {
              id: "prop.marker.bay-ops-terminal",
              room: "bay.ops",
              kind: "map-marker",
              position: [3.86, -6.42],
              size: [0.36, 0.66],
              color: "#38bdf8",
              emissive: true,
              target: "terminal.bay-ops",
              label: "Bay Ops terminal marker"
            },
            {
              id: "prop.marker.engineering-power",
              room: "engineering.access",
              kind: "map-marker",
              position: [7.35, -20.75],
              size: [0.36, 0.66],
              color: "#86efac",
              emissive: true,
              target: "terminal.engineering-power",
              label: "Engineering power marker"
            },
            {
              id: "prop.marker.medbay",
              room: "medbay.stub",
              kind: "map-marker",
              position: [-6.3, -20.4],
              size: [0.36, 0.66],
              color: "#fca5a5",
              emissive: true,
              target: "medbay.stub",
              label: "Medbay marker"
            },
            {
              id: "prop.marker.science-ops",
              room: "science.ops.stub",
              kind: "map-marker",
              position: [-6.28, -26.25],
              size: [0.36, 0.66],
              color: "#a78bfa",
              emissive: true,
              target: "science.ops.stub",
              label: "Science/Ops marker"
            },
            {
              id: "prop.marker.bridge-access",
              room: "bridge.access",
              kind: "map-marker",
              position: [0.0, -25.72],
              size: [0.36, 0.66],
              color: "#86efac",
              emissive: true,
              target: "door.bridge",
              label: "Bridge access marker"
            },
            {
              id: "prop.marker.bridge-viewscreen",
              room: "bridge.deck",
              kind: "map-marker",
              position: [0.0, -37.15],
              size: [0.36, 0.66],
              color: "#ef4444",
              emissive: true,
              target: "terminal.bridge-viewscreen",
              label: "Bridge viewscreen marker"
            }
          ],
          // Patch D makes E-key targets data-driven so prompts, ranges, and actions stay together.
          interactables: [
            {
              id: "door.bay-access",
              kind: "access",
              label: "Starboard Interior Access",
              location: "bay.shuttle",
              position: [3.18, -4.62],
              range: 2.05,
              action: "enterBayOpsAccess",
              prompt: "Press E to enter through Starboard Interior Access."
            },
            {
              id: "terminal.bay-ops",
              kind: "terminal",
              label: "Bay Operations Terminal",
              location: "bay.ops",
              position: [3.86, -6.42],
              range: 1.75,
              action: "activateBayOperationsTerminal",
              prompt: "Press E to use Bay Operations Terminal."
            },
            {
              id: "terminal.engineering-power",
              kind: "terminal",
              label: "Engineering Power Console",
              location: "engineering.access",
              position: [7.35, -20.75],
              range: 1.9,
              action: "restoreEngineeringPower",
              prompt: "Press E to use Engineering Power Console."
            },
            {
              id: "door.bay-inner",
              kind: "door",
              label: "Inner Shuttle Bay Door",
              location: "bay.ops",
              position: [0.0, -8.92],
              range: 1.75,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Inner Shuttle Bay Door."
            },
            {
              id: "door.security-hub",
              kind: "door",
              label: "Security Checkpoint Door",
              location: "security.checkpoint",
              position: [0.0, -13.36],
              range: 1.65,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Security Checkpoint Door."
            },
            {
              id: "door.engineering-access",
              kind: "door",
              label: "Engineering Access Door",
              location: "corridor.main",
              position: [3.25, -17.8],
              range: 1.85,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Engineering Access Door."
            },
            {
              id: "door.medbay",
              kind: "door",
              label: "Medbay Door",
              location: "corridor.main",
              position: [-3.25, -17.8],
              range: 1.85,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Medbay Door."
            },
            {
              id: "door.science",
              kind: "door",
              label: "Science/Ops Door",
              location: "corridor.main",
              position: [-3.25, -25.0],
              range: 1.85,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Science/Ops Door."
            },
            {
              id: "door.bridge",
              kind: "door",
              label: "Bridge Command Door",
              location: "corridor.main",
              position: [0.0, -25.72],
              range: 1.95,
              action: "inspectOpenDoorRoute",
              prompt: "Press E to inspect Bridge Command Door."
            },
            {
              id: "terminal.bridge-tactical",
              kind: "terminal",
              label: "Bridge Tactical Console / Sensor Array",
              location: "bridge.deck",
              position: [2.85, -36.7],
              range: 1.85,
              action: "fireBridgeTacticalConsole",
              prompt: "Press E to use the bridge tactical console / sensor array."
            },
            {
              id: "terminal.bridge-viewscreen",
              kind: "terminal",
              label: "Bridge Viewscreen",
              location: "bridge.deck",
              position: [0.0, -37.15],
              range: 2.45,
              action: "trackEnemyShipOnViewscreen",
              prompt: "Press E to use the bridge viewscreen controls."
            }
          ],
          // Patch E formalizes action ids as registry entries instead of embedding E-key behavior in a switch.
          // Patch Q exposes expected interaction effects so data can describe what a successful E-key action changes.
          // See pretty_docs/game-runtime-patch-Q-interaction-effect-metadata.md.
          interactions: {
            enterBayOpsAccess: {
              id: "enterBayOpsAccess",
              label: "Enter Bay Operations access",
              handler: "enterBayOpsAccess",
              changesState: ["location", "objectiveId", "lastInteractionStatus"],
              successStatus: "Entered the lit Bay Operations vestibule. The route ahead is open.",
              nextObjective: "objective.bay-ops"
            },
            activateBayOperationsTerminal: {
              id: "activateBayOperationsTerminal",
              label: "Activate Bay Operations Terminal",
              handler: "activateBayOperationsTerminal",
              status: "Bay Operations online. Route to Security Checkpoint is available.",
              changesState: [
                "terminals[terminal.bay-ops].state",
                "doors[door.bay-inner].state",
                "flags.bayOpsTerminalUsed",
                "objectiveId",
                "lastInteractionStatus"
              ],
              successStatus: "Bay Operations online. Route to Security Checkpoint is available.",
              nextObjective: "objective.enter-corridor"
            },
            restoreEngineeringPower: {
              id: "restoreEngineeringPower",
              label: "Restore Engineering Power",
              handler: "restoreEngineeringPower",
              status: "Engineering restored main power. Bridge route confirmed open.",
              changesState: [
                "terminals[terminal.engineering-power].state",
                "power",
                "security",
                "doors[door.bridge].state",
                "flags.engineeringPowerRestored",
                "objectiveId",
                "lastInteractionStatus"
              ],
              successStatus: "Engineering restored main power. Bridge route confirmed open.",
              nextObjective: "objective.bridge-access"
            },
            inspectOpenDoorRoute: {
              id: "inspectOpenDoorRoute",
              label: "Inspect open route",
              handler: "inspectOpenDoorRoute",
              changesState: ["doors[target.id].state", "objectiveId", "lastInteractionStatus"],
              successStatus: "Route is open. No door lock is required.",
              nextObjective: ["objective.restore-power", "objective.survey-departments", "objective.bridge-screen"]
            },
            trackEnemyShipOnViewscreen: {
              id: "trackEnemyShipOnViewscreen",
              label: "Acquire bridge viewscreen target",
              handler: "trackEnemyShipOnViewscreen",
              status: "Current target centered on the main viewscreen.",
              changesState: [
                "terminals[terminal.bridge-viewscreen].state",
                "flags.bridgeViewscreenTrackingActive",
                "flags.enemyShipOnBridgeViewscreen",
                "flags.lastSurveyedPlanetId",
                "flags.lastSurveyedSystemId",
                "flags.bridgeViewscreenInteractedAtMs",
                "objectiveId",
                "lastInteractionStatus"
              ],
              successStatus: "Current target centered on the main viewscreen.",
              nextObjective: ["objective.enemy-attack", "objective.enemy-disabled", "objective.planet-scan", "objective.planet-surveyed"]
            },
            fireBridgeTacticalConsole: {
              id: "fireBridgeTacticalConsole",
              label: "Fire tactical weapons or scan planet",
              handler: "fireBridgeTacticalConsole",
              changesState: [
                "terminals[terminal.bridge-viewscreen].state",
                "terminals[terminal.bridge-tactical].state",
                "flags.bridgeViewscreenTrackingActive",
                "flags.enemyShipHullPercent",
                "flags.enemyShipDisabled",
                "flags.bridgeTacticalShotsFired",
                "flags.bridgeTacticalLastFireAtMs",
                "flags.currentSystemPlanetSurveyed",
                "flags.lastSurveyedPlanetId",
                "flags.lastSurveyedSystemId",
                "flags.planetScansCompleted",
                "flags.planetScanLastAtMs",
                "objectiveId",
                "lastInteractionStatus"
              ],
              successStatus: "Bridge tactical / sensor action completed.",
              nextObjective: ["objective.enemy-attack", "objective.enemy-disabled", "objective.planet-surveyed"]
            }
          }
        };
      }

      function shuttle3dNumberValue(value, fallback) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : fallback;
      }

      function shuttle3dBoundsValue(value, fallback) {
        const source = shuttle3dObjectValue(value);
        const base = fallback && typeof fallback === "object" ? fallback : {};
        return {
          minX: shuttle3dNumberValue(source.minX, shuttle3dNumberValue(base.minX, -1)),
          maxX: shuttle3dNumberValue(source.maxX, shuttle3dNumberValue(base.maxX, 1)),
          minZ: shuttle3dNumberValue(source.minZ, shuttle3dNumberValue(base.minZ, -1)),
          maxZ: shuttle3dNumberValue(source.maxZ, shuttle3dNumberValue(base.maxZ, 1))
        };
      }

      function shuttle3dBoundsAreUsable(bounds) {
        return (
          bounds
          && Number.isFinite(bounds.minX)
          && Number.isFinite(bounds.maxX)
          && Number.isFinite(bounds.minZ)
          && Number.isFinite(bounds.maxZ)
          && bounds.minX <= bounds.maxX
          && bounds.minZ <= bounds.maxZ
        );
      }

      function shuttle3dRoomVisualDefaults(kind) {
        // Patch N: room visuals are authored in motherShipInterior.rooms[].visual.
        // These defaults let old projects get the same data-driven room boundary pass.
        const normalizedKind = String(kind || "room").toLowerCase();
        const palette = {
          "shuttle-bay": {color: "#38bdf8", edgeColor: "#60a5fa", labelColor: "#bfdbfe"},
          operations: {color: "#38bdf8", edgeColor: "#22d3ee", labelColor: "#bae6fd"},
          checkpoint: {color: "#f59e0b", edgeColor: "#fbbf24", labelColor: "#fef3c7"},
          corridor: {color: "#94a3b8", edgeColor: "#64748b", labelColor: "#e2e8f0"},
          engineering: {color: "#fbbf24", edgeColor: "#f59e0b", labelColor: "#fde68a"},
          medbay: {color: "#fca5a5", edgeColor: "#f87171", labelColor: "#fee2e2"},
          science: {color: "#a78bfa", edgeColor: "#8b5cf6", labelColor: "#ede9fe"},
          "bridge-access": {color: "#86efac", edgeColor: "#22c55e", labelColor: "#dcfce7"},
          bridge: {color: "#ef4444", edgeColor: "#f97316", labelColor: "#fee2e2"}
        };
        const defaults = palette[normalizedKind] || {color: "#38bdf8", edgeColor: "#64748b", labelColor: "#e2e8f0"};
        return {
          color: defaults.color,
          edgeColor: defaults.edgeColor,
          labelColor: defaults.labelColor,
          boundary: true,
          floorBand: true,
          label: true,
          labelHeight: 0.42
        };
      }

      function shuttle3dNormalizeMotherShipRoomVisual(value, fallbackValue, kind) {
        const defaults = shuttle3dRoomVisualDefaults(kind);
        const raw = shuttle3dObjectValue(value);
        const fallback = shuttle3dObjectValue(fallbackValue);
        return {
          color: String(raw.color || fallback.color || defaults.color),
          edgeColor: String(raw.edgeColor || fallback.edgeColor || defaults.edgeColor),
          labelColor: String(raw.labelColor || fallback.labelColor || defaults.labelColor),
          boundary: raw.boundary === false || fallback.boundary === false ? false : defaults.boundary,
          floorBand: raw.floorBand === false || fallback.floorBand === false ? false : defaults.floorBand,
          label: raw.label === false || fallback.label === false ? false : defaults.label,
          labelHeight: Math.max(0.08, Math.min(1.2, shuttle3dNumberValue(raw.labelHeight, shuttle3dNumberValue(fallback.labelHeight, defaults.labelHeight))))
        };
      }

      function shuttle3dNormalizeMotherShipRoomGeometry(value, fallbackValue, roomBounds, roomId) {
        // Patch O keeps mother-ship room shell/wall/opening geometry in rooms[].geometry
        // instead of hardcoding every room frame inside appendShuttleBayScene.
        const raw = shuttle3dObjectValue(value);
        const fallback = shuttle3dObjectValue(fallbackValue);
        const source = Object.keys(raw).length ? raw : fallback;
        const geometry = shuttle3dCloneJson(source, {});
        const bounds = shuttle3dBoundsValue(geometry?.shell?.bounds, roomBounds);
        if (!geometry.schema) geometry.schema = "game.room.geometry.v1";
        if (!geometry.shell || typeof geometry.shell !== "object") {
          geometry.shell = {
            bounds,
            accentColor: geometry.accentColor || "#67e8f9"
          };
        } else {
          geometry.shell.bounds = bounds;
        }
        if (!Array.isArray(geometry.walls)) geometry.walls = [];
        if (!Array.isArray(geometry.openings)) geometry.openings = [];
        if (!Array.isArray(geometry.doorPanels)) geometry.doorPanels = [];
        if (!Array.isArray(geometry.boxes)) geometry.boxes = [];
        if (!Array.isArray(geometry.beams)) geometry.beams = [];
        geometry.room = String(geometry.room || roomId || "");
        return geometry;
      }

      function shuttle3dNormalizeMotherShipRooms(value, fallbackRooms, locations) {
        const source = Array.isArray(value) && value.length ? value : fallbackRooms;
        return source
          .map((room, index) => {
            const raw = shuttle3dObjectValue(room);
            const fallback = shuttle3dObjectValue(fallbackRooms[index]);
            const id = String(raw.id || fallback.id || "").trim();
            if (!id) return null;
            const location = String(raw.location || fallback.location || id).trim();
            const bounds = shuttle3dBoundsValue(raw.bounds, fallback.bounds);
            if (!shuttle3dBoundsAreUsable(bounds)) return null;
            return {
              id,
              name: String(raw.name || fallback.name || locations?.[location] || id),
              location,
              kind: String(raw.kind || fallback.kind || "room"),
              priority: shuttle3dNumberValue(raw.priority, shuttle3dNumberValue(fallback.priority, index)),
              bounds,
              visual: shuttle3dNormalizeMotherShipRoomVisual(raw.visual, fallback.visual, raw.kind || fallback.kind || "room"),
              geometry: shuttle3dNormalizeMotherShipRoomGeometry(raw.geometry, fallback.geometry, bounds, id)
            };
          })
          .filter(Boolean);
      }

      function shuttle3dRoomMap(rooms) {
        return Object.fromEntries(rooms.map((room) => [room.id, shuttle3dCloneJson(room)]));
      }

      function shuttle3dMovementBoundsFromRooms(rooms, fallbackBounds) {
        const fallback = shuttle3dBoundsValue(fallbackBounds, {});
        if (!Array.isArray(rooms) || !rooms.length) return fallback;
        return rooms.reduce((bounds, room) => ({
          minX: Math.min(bounds.minX, room.bounds.minX),
          maxX: Math.max(bounds.maxX, room.bounds.maxX),
          minZ: Math.min(bounds.minZ, room.bounds.minZ),
          maxZ: Math.max(bounds.maxZ, room.bounds.maxZ)
        }), fallback);
      }

      function shuttle3dMotherShipWallColliders(rooms, wallCollision) {
        const config = shuttle3dObjectValue(wallCollision);
        if (config.enabled === false) return [];
        const thickness = Math.max(0.02, Math.min(0.5, shuttle3dNumberValue(config.thickness, 0.12)));
        const halfThickness = thickness / 2;
        const colliders = [];
        (Array.isArray(rooms) ? rooms : []).forEach((room) => {
          const roomId = String(room?.id || room?.location || "room");
          const walls = Array.isArray(room?.geometry?.walls) ? room.geometry.walls : [];
          walls.forEach((wall, index) => {
            if (!wall || typeof wall !== "object" || wall.collision === false) return;
            const axis = String(wall.axis || "").toLowerCase();
            if (axis === "x") {
              const x = Number(wall.x);
              const minZ = Math.min(Number(wall.minZ), Number(wall.maxZ));
              const maxZ = Math.max(Number(wall.minZ), Number(wall.maxZ));
              if (![x, minZ, maxZ].every(Number.isFinite) || maxZ <= minZ) return;
              colliders.push({
                id: String(wall.id || `wall.${roomId}.${index + 1}`),
                kind: "wall",
                room: roomId,
                minX: x - halfThickness,
                maxX: x + halfThickness,
                minZ,
                maxZ
              });
              return;
            }
            if (axis === "z") {
              const z = Number(wall.z);
              const minX = Math.min(Number(wall.minX), Number(wall.maxX));
              const maxX = Math.max(Number(wall.minX), Number(wall.maxX));
              if (![z, minX, maxX].every(Number.isFinite) || maxX <= minX) return;
              colliders.push({
                id: String(wall.id || `wall.${roomId}.${index + 1}`),
                kind: "wall",
                room: roomId,
                minX,
                maxX,
                minZ: z - halfThickness,
                maxZ: z + halfThickness
              });
            }
          });
        });
        return colliders;
      }

      function shuttle3dNormalizeMotherShipProps(value, fallbackProps, rooms) {
        const fallbackById = new Map((Array.isArray(fallbackProps) ? fallbackProps : [])
          .map((prop) => [String(prop?.id || ""), prop]));
        const roomById = new Map((Array.isArray(rooms) ? rooms : [])
          .map((room) => [String(room?.id || ""), room]));
        const source = Array.isArray(value) && value.length ? value : (Array.isArray(fallbackProps) ? fallbackProps : []);
        return source
          .map((prop, index) => {
            const raw = shuttle3dObjectValue(prop);
            const fallback = shuttle3dObjectValue(fallbackById.get(String(raw.id || "")) || (Array.isArray(fallbackProps) ? fallbackProps[index] : null));
            const id = String(raw.id || fallback.id || "").trim();
            if (!id) return null;
            const room = String(raw.room || raw.location || fallback.room || fallback.location || "").trim();
            const roomInfo = roomById.get(room);
            const positionSource = Array.isArray(raw.position) ? raw.position : fallback.position;
            const x = shuttle3dNumberValue(positionSource?.[0], NaN);
            const z = shuttle3dNumberValue(positionSource?.[1], NaN);
            if (!Number.isFinite(x) || !Number.isFinite(z)) return null;
            const sizeSource = Array.isArray(raw.size) ? raw.size : fallback.size;
            const size = [
              Math.max(0.05, Math.abs(shuttle3dNumberValue(sizeSource?.[0], 0.7))),
              Math.max(0.05, Math.abs(shuttle3dNumberValue(sizeSource?.[1], 0.35))),
              Math.max(0.05, Math.abs(shuttle3dNumberValue(sizeSource?.[2], 0.5)))
            ];
            return {
              id,
              room,
              location: String(raw.location || fallback.location || roomInfo?.location || room || "").trim(),
              kind: String(raw.kind || fallback.kind || "floor-marker").trim(),
              position: [x, z],
              size,
              color: String(raw.color || fallback.color || "#38bdf8"),
              emissive: raw.emissive === true || fallback.emissive === true,
              facing: String(raw.facing || fallback.facing || "north"),
              axis: String(raw.axis || fallback.axis || "x"),
              target: String(raw.target || fallback.target || ""),
              display: String(raw.display || fallback.display || ""),
              label: String(raw.label || fallback.label || id)
            };
          })
          .filter(Boolean);
      }

      function shuttle3dNormalizeMotherShipMovement(value, fallbackMovement, rooms) {
        const supplied = shuttle3dObjectValue(value);
        const fallback = shuttle3dObjectValue(fallbackMovement);
        const bounds = shuttle3dBoundsValue(supplied.bounds, shuttle3dMovementBoundsFromRooms(rooms, fallback.bounds));
        const authoredColliders = (
          Array.isArray(supplied.colliders)
            ? supplied.colliders
            : Array.isArray(fallback.colliders)
              ? fallback.colliders
              : []
        )
          .map((collider, index) => {
            const raw = shuttle3dObjectValue(collider);
            const normalized = shuttle3dBoundsValue(raw, {});
            if (!shuttle3dBoundsAreUsable(normalized)) return null;
            return {
              id: String(raw.id || `ship-collider-${index}`),
              kind: String(raw.kind || "fixture"),
              ...normalized
            };
          })
          .filter(Boolean);
        const wallCollision = {
          ...shuttle3dObjectValue(fallback.wallCollision),
          ...shuttle3dObjectValue(supplied.wallCollision)
        };
        const wallColliders = shuttle3dMotherShipWallColliders(rooms, wallCollision);
        return {
          bounds,
          wallCollision: {
            enabled: wallCollision.enabled !== false,
            thickness: Math.max(0.02, Math.min(0.5, shuttle3dNumberValue(wallCollision.thickness, 0.12)))
          },
          colliders: [...authoredColliders, ...wallColliders]
        };
      }

      function shuttle3dNormalizeMotherShipSpawns(value, fallbackSpawns, locations) {
        const supplied = shuttle3dObjectValue(value);
        const source = Object.keys(supplied).length ? supplied : fallbackSpawns;
        return Object.fromEntries(
          Object.entries(source)
            .map(([spawnId, spawn]) => {
              const raw = shuttle3dObjectValue(spawn);
              const id = String(raw.id || spawnId || "").trim();
              const location = String(raw.location || raw.room || "bay.shuttle").trim();
              const fallbackPosition = Array.isArray(raw.position) ? raw.position : [0.24, 0.9, 4.3];
              const position = (
                Array.isArray(fallbackPosition)
                && fallbackPosition.length === 3
                && fallbackPosition.every((entry) => Number.isFinite(Number(entry)))
              )
                ? fallbackPosition.map(Number)
                : [0.24, 0.9, 4.3];
              if (!id) return null;
              return [id, {
                id,
                room: location,
                location,
                label: String(raw.label || locations?.[location] || id),
                position,
                yaw: shuttle3dNumberValue(raw.yaw, 32),
                pitch: shuttle3dNumberValue(raw.pitch, -4)
              }];
            })
            .filter(Boolean)
        );
      }

      function shuttle3dNormalizeMotherShipExits(value, fallbackExits) {
        const source = Array.isArray(value) && value.length ? value : fallbackExits;
        return source
          .map((exit, index) => {
            const raw = shuttle3dObjectValue(exit);
            const fallback = shuttle3dObjectValue(fallbackExits[index]);
            const id = String(raw.id || fallback.id || "").trim();
            if (!id) return null;
            const bounds = shuttle3dBoundsValue(raw.bounds, fallback.bounds);
            if (!shuttle3dBoundsAreUsable(bounds)) return null;
            return {
              id,
              from: String(raw.from || fallback.from || ""),
              to: String(raw.to || fallback.to || ""),
              door: String(raw.door || fallback.door || ""),
              bounds
            };
          })
          .filter(Boolean);
      }

      function shuttle3dInteractableVisualDefaults(kind) {
        // Patch L: in-world E-key affordances can now be styled by content data.
        // See pretty_docs/game-runtime-patch-L-interactable-visual-metadata.md for authoring intent.
        const normalizedKind = String(kind || "").toLowerCase();
        if (normalizedKind === "terminal") {
          return {
            color: "#38bdf8",
            activeColor: "#fef3c7",
            radiusScale: 0.34,
            height: 0.58,
            activeHeight: 0.88,
            baseSize: 0.18,
            terminalPanel: true,
            routeBeam: false
          };
        }
        if (normalizedKind === "access") {
          return {
            color: "#86efac",
            activeColor: "#fef3c7",
            radiusScale: 0.38,
            height: 0.66,
            activeHeight: 0.96,
            baseSize: 0.22,
            terminalPanel: false,
            routeBeam: true
          };
        }
        if (normalizedKind === "door") {
          return {
            color: "#fbbf24",
            activeColor: "#fef3c7",
            radiusScale: 0.31,
            height: 0.52,
            activeHeight: 0.82,
            baseSize: 0.18,
            terminalPanel: false,
            routeBeam: true
          };
        }
        return {
          color: "#a78bfa",
          activeColor: "#fef3c7",
          radiusScale: 0.34,
          height: 0.54,
          activeHeight: 0.84,
          baseSize: 0.18,
          terminalPanel: false,
          routeBeam: false
        };
      }

      function shuttle3dNormalizeMotherShipInteractableVisual(value, fallbackValue, kind) {
        const defaults = shuttle3dInteractableVisualDefaults(kind);
        const fallback = shuttle3dObjectValue(fallbackValue);
        const raw = shuttle3dObjectValue(value);
        const source = {...defaults, ...fallback, ...raw};
        return {
          color: String(source.color || defaults.color),
          activeColor: String(source.activeColor || defaults.activeColor),
          radiusScale: Math.max(0.08, shuttle3dNumberValue(source.radiusScale, defaults.radiusScale)),
          height: Math.max(0.12, shuttle3dNumberValue(source.height, defaults.height)),
          activeHeight: Math.max(0.12, shuttle3dNumberValue(source.activeHeight, defaults.activeHeight)),
          baseSize: Math.max(0.08, shuttle3dNumberValue(source.baseSize, defaults.baseSize)),
          terminalPanel: source.terminalPanel !== false,
          routeBeam: source.routeBeam === true
        };
      }

      function shuttle3dNormalizeMotherShipInteractables(value, fallbackInteractables, terminals, doors) {
        const source = Array.isArray(value) && value.length ? value : fallbackInteractables;
        return source
          .map((interactable, index) => {
            const raw = shuttle3dObjectValue(interactable);
            const fallback = shuttle3dObjectValue(fallbackInteractables[index]);
            const id = String(raw.id || fallback.id || "").trim();
            if (!id) return null;
            const positionSource = Array.isArray(raw.position) ? raw.position : fallback.position;
            if (!Array.isArray(positionSource) || positionSource.length < 2) return null;
            const x = shuttle3dNumberValue(positionSource[0], NaN);
            const z = shuttle3dNumberValue(positionSource[1], NaN);
            if (!Number.isFinite(x) || !Number.isFinite(z)) return null;
            const range = Math.max(0.1, shuttle3dNumberValue(raw.range, shuttle3dNumberValue(raw.radius, shuttle3dNumberValue(fallback.range, 1.5))));
            const terminal = terminals?.[id] || null;
            const door = doors?.[id] || null;
            const kind = String(raw.kind || fallback.kind || (terminal ? "terminal" : door ? "door" : "access"));
            const label = String(raw.label || fallback.label || terminal?.label || door?.label || id);
            return {
              id,
              kind,
              label,
              location: String(raw.location || fallback.location || terminal?.location || door?.from || ""),
              position: [x, z],
              range,
              action: String(raw.action || raw.interaction || fallback.action || fallback.interaction || ""),
              prompt: String(raw.prompt || fallback.prompt || ""),
              visual: shuttle3dNormalizeMotherShipInteractableVisual(raw.visual, fallback.visual, kind)
            };
          })
          .filter(Boolean);
      }


      function shuttle3dInteractionStringList(value, fallback = []) {
        const source = Array.isArray(value) ? value : Array.isArray(fallback) ? fallback : value ? [value] : fallback ? [fallback] : [];
        return source
          .map((entry) => String(entry || "").trim())
          .filter(Boolean);
      }

      function shuttle3dNormalizeInteractionNextObjective(value, fallback = "") {
        const source = value !== undefined ? value : fallback;
        const entries = shuttle3dInteractionStringList(source);
        if (Array.isArray(source)) return entries;
        return entries[0] || "";
      }

      function shuttle3dNormalizeMotherShipInteractions(value, fallbackInteractions) {
        const fallback = shuttle3dObjectValue(fallbackInteractions);
        const supplied = shuttle3dObjectValue(value);
        const source = {...fallback, ...supplied};
        return Object.fromEntries(
          Object.entries(source)
            .map(([key, interaction]) => {
              const raw = shuttle3dObjectValue(interaction);
              const fallbackEntry = shuttle3dObjectValue(fallback[key]);
              const id = String(raw.id || fallbackEntry.id || key).trim();
              if (!id) return null;
              const status = String(raw.status || fallbackEntry.status || "");
              return [
                id,
                {
                  id,
                  label: String(raw.label || fallbackEntry.label || id),
                  handler: String(raw.handler || raw.effect || raw.action || fallbackEntry.handler || fallbackEntry.effect || fallbackEntry.action || id),
                  status,
                  changesState: shuttle3dInteractionStringList(raw.changesState, fallbackEntry.changesState),
                  successStatus: String(raw.successStatus || fallbackEntry.successStatus || status),
                  nextObjective: shuttle3dNormalizeInteractionNextObjective(raw.nextObjective, fallbackEntry.nextObjective),
                  emitsState: raw.emitsState !== false && fallbackEntry.emitsState !== false
                }
              ];
            })
            .filter(Boolean)
        );
      }


      function shuttle3dMotherShipSupportedInteractionHandlers() {
        return new Set([
          "enterBayOpsAccess",
          "activateBayOperationsTerminal",
          "restoreEngineeringPower",
          "trackEnemyShipOnViewscreen",
          "fireBridgeTacticalConsole",
          "openBridgeNavigationConsole",
          "inspectOpenDoorRoute"
        ]);
      }

      function shuttle3dNormalizeMotherShipValidationRules(value) {
        const supplied = shuttle3dObjectValue(value);
        // See pretty_docs/game-runtime-patch-J-prop-target-validation.md for prop target validation intent.
        return {
          requireRoomBoundsInsideMovement: supplied.requireRoomBoundsInsideMovement !== false,
          requireConnectedRooms: supplied.requireConnectedRooms !== false,
          requireReachableInteractables: supplied.requireReachableInteractables !== false,
          requireInteractionHandlers: supplied.requireInteractionHandlers !== false,
          requireInteractionEffects: supplied.requireInteractionEffects !== false,
          requireDefinitionVersion: supplied.requireDefinitionVersion !== false,
          requireObjectiveTargets: supplied.requireObjectiveTargets !== false,
          requireSpawnInsideRoom: supplied.requireSpawnInsideRoom !== false,
          requireRenderableProps: supplied.requireRenderableProps !== false,
          requirePropTargets: supplied.requirePropTargets !== false,
          requireOpenDoors: supplied.requireOpenDoors !== false
        };
      }

      function shuttle3dPointInsideBounds(x, z, bounds) {
        return (
          shuttle3dBoundsAreUsable(bounds)
          && Number.isFinite(Number(x))
          && Number.isFinite(Number(z))
          && Number(x) >= bounds.minX
          && Number(x) <= bounds.maxX
          && Number(z) >= bounds.minZ
          && Number(z) <= bounds.maxZ
        );
      }

      function shuttle3dBoundsContainBounds(outer, inner) {
        return (
          shuttle3dBoundsAreUsable(outer)
          && shuttle3dBoundsAreUsable(inner)
          && inner.minX >= outer.minX
          && inner.maxX <= outer.maxX
          && inner.minZ >= outer.minZ
          && inner.maxZ <= outer.maxZ
        );
      }

      function shuttle3dRoomsForLocation(config, location) {
        const wanted = String(location || "");
        if (!wanted) return [];
        const rooms = Array.isArray(config?.rooms) ? config.rooms : [];
        return rooms.filter((room) => room.id === wanted || room.location === wanted);
      }

      function shuttle3dRoomForLocation(config, location) {
        return shuttle3dRoomsForLocation(config, location)[0] || null;
      }

      function shuttle3dRoomForLocationAtPoint(config, location, x, z) {
        const matches = shuttle3dRoomsForLocation(config, location);
        return matches.find((room) => shuttle3dPointInsideBounds(x, z, room.bounds)) || matches[0] || null;
      }

      function shuttle3dLocationExists(config, location) {
        return shuttle3dRoomsForLocation(config, location).length > 0;
      }

      function shuttle3dPositionFromSpawn(spawn) {
        const position = Array.isArray(spawn?.position) ? spawn.position : [];
        if (position.length >= 3) return [Number(position[0]), Number(position[2])];
        return [Number(position[0]), Number(position[1])];
      }

      function shuttle3dValidateMotherShipInteriorConfig(config, rulesInput) {
        // Patch F validates definition reachability before content patches ship.
        const errors = [];
        const warnings = [];
        const rooms = Array.isArray(config?.rooms) ? config.rooms : [];
        const roomIds = new Set();
        const roomLocations = new Set();
        const movementBounds = config?.movement?.bounds;
        const doors = shuttle3dObjectValue(config?.doors);
        const terminals = shuttle3dObjectValue(config?.terminals);
        const interactions = shuttle3dObjectValue(config?.interactions);
        const objectives = shuttle3dObjectValue(config?.objectives);
        const exits = Array.isArray(config?.exits) ? config.exits : [];
        const exitIds = new Set(exits.map((exit) => String(exit?.id || "")).filter(Boolean));
        const supportedHandlers = shuttle3dMotherShipSupportedInteractionHandlers();
        const rules = shuttle3dNormalizeMotherShipValidationRules(rulesInput || config?.validationRules);
        const propSystemTargets = new Set(["enemyShip", "currentSystemPlanet"]);
        const propDisplayTargets = new Set(["enemyShipTactical", "systemPlanet"]);
        if (rules.requireDefinitionVersion) {
          const definitionVersion = String(config?.definitionVersion || "").trim();
          if (definitionVersion !== SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION) {
            warnings.push(`mother-ship definitionVersion ${definitionVersion || "<missing>"} does not match ${SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION}`);
          }
          const stateVersion = String(config?.stateVersion || config?.stateDefaults?.stateVersion || "").trim();
          if (!stateVersion) warnings.push("mother-ship stateVersion is missing");
        }
        const migrationDefaults = Array.isArray(config?.migration?.defaultsApplied) ? config.migration.defaultsApplied : [];
        if (migrationDefaults.length) {
          warnings.push(`mother-ship migration supplied defaults for ${migrationDefaults.join(", ")}`);
        }
        const propTargetIsKnown = (target) => {
          const targetId = String(target || "").trim();
          if (!targetId) return true;
          if (propSystemTargets.has(targetId)) return true;
          if (roomIds.has(targetId) || roomLocations.has(targetId)) return true;
          if (terminals[targetId] || doors[targetId] || objectives[targetId]) return true;
          return (Array.isArray(config?.interactables) ? config.interactables : [])
            .some((interactable) => String(interactable?.id || "") === targetId);
        };

        if (rules.requireRoomBoundsInsideMovement && !shuttle3dBoundsAreUsable(movementBounds)) {
          errors.push("mother-ship movement bounds are invalid");
        }

        rooms.forEach((room, index) => {
          if (!room?.id) {
            errors.push(`room[${index}] is missing an id`);
            return;
          }
          if (roomIds.has(room.id)) errors.push(`duplicate room id: ${room.id}`);
          roomIds.add(room.id);
          if (room.location) roomLocations.add(room.location);
          if (!shuttle3dBoundsAreUsable(room.bounds)) errors.push(`room ${room.id} has invalid bounds`);
          else if (rules.requireRoomBoundsInsideMovement && !shuttle3dBoundsContainBounds(movementBounds, room.bounds)) {
            errors.push(`room ${room.id} is outside mother-ship movement bounds`);
          }
          const geometry = shuttle3dObjectValue(room.geometry);
          if (Object.keys(geometry).length) {
            // Patch O validates room-local geometry metadata without making doors into traversal locks.
            if (geometry.room && String(geometry.room) !== String(room.id)) {
              errors.push(`room ${room.id} geometry points at mismatched room ${geometry.room}`);
            }
            (Array.isArray(geometry.walls) ? geometry.walls : []).forEach((wall, wallIndex) => {
              const axis = String(wall?.axis || "").toLowerCase();
              if (!["x", "z"].includes(axis)) errors.push(`room ${room.id} geometry wall[${wallIndex}] has invalid axis`);
              if (axis === "x" && !Number.isFinite(Number(wall?.x))) errors.push(`room ${room.id} geometry wall[${wallIndex}] has invalid x`);
              if (axis === "z" && !Number.isFinite(Number(wall?.z))) errors.push(`room ${room.id} geometry wall[${wallIndex}] has invalid z`);
            });
            (Array.isArray(geometry.openings) ? geometry.openings : []).forEach((opening) => {
              const openingId = String(opening?.id || "opening");
              const exitId = String(opening?.exit || "").trim();
              const doorId = String(opening?.door || "").trim();
              if (exitId && !exitIds.has(exitId)) errors.push(`room ${room.id} geometry ${openingId} references missing exit ${exitId}`);
              if (doorId && !doors[doorId]) errors.push(`room ${room.id} geometry ${openingId} references missing door ${doorId}`);
            });
            (Array.isArray(geometry.doorPanels) ? geometry.doorPanels : []).forEach((panel, panelIndex) => {
              const doorId = String(panel?.door || "").trim();
              if (doorId && !doors[doorId]) errors.push(`room ${room.id} geometry doorPanel[${panelIndex}] references missing door ${doorId}`);
            });
          }
        });

        Object.entries(config?.locations || {}).forEach(([location]) => {
          if (!shuttle3dLocationExists(config, location)) {
            warnings.push(`location ${location} has no matching room`);
          }
        });

        Object.entries(objectives).forEach(([objectiveId, objective]) => {
          const location = String(objective?.location || "");
          if (rules.requireObjectiveTargets && location && !shuttle3dLocationExists(config, location)) {
            errors.push(`objective ${objectiveId} points at missing location ${location}`);
          }
        });

        Object.entries(doors).forEach(([doorId, door]) => {
          ["from", "to"].forEach((side) => {
            const location = String(door?.[side] || "");
            if (rules.requireConnectedRooms && location && !shuttle3dLocationExists(config, location)) {
              errors.push(`door ${doorId} ${side} references missing location ${location}`);
            }
          });
          if (rules.requireOpenDoors && String(door?.state || "").toLowerCase() === "locked") {
            errors.push(`door ${doorId} is locked; mother-ship doors must remain traversal-open`);
          }
        });

        exits.forEach((exit) => {
          const id = String(exit?.id || "exit");
          if (rules.requireConnectedRooms && !shuttle3dLocationExists(config, exit?.from)) errors.push(`${id} starts at missing room/location ${exit?.from}`);
          if (rules.requireConnectedRooms && !shuttle3dLocationExists(config, exit?.to)) errors.push(`${id} ends at missing room/location ${exit?.to}`);
          if (rules.requireConnectedRooms && !shuttle3dBoundsAreUsable(exit?.bounds)) errors.push(`${id} has invalid bounds`);
          if (exit?.door && !doors[exit.door]) warnings.push(`${id} references missing door ${exit.door}`);
        });

        Object.entries(terminals).forEach(([terminalId, terminal]) => {
          const location = String(terminal?.location || "");
          if (location && !shuttle3dLocationExists(config, location)) {
            errors.push(`terminal ${terminalId} points at missing location ${location}`);
          }
        });

        Object.entries(interactions).forEach(([interactionId, interaction]) => {
          const handlerId = String(interaction?.handler || interactionId || "");
          if (rules.requireInteractionHandlers && !handlerId) errors.push(`interaction ${interactionId} has no handler`);
          else if (rules.requireInteractionHandlers && !supportedHandlers.has(handlerId)) errors.push(`interaction ${interactionId} uses unsupported handler ${handlerId}`);

          if (rules.requireInteractionEffects) {
            // Patch Q validates authored effect expectations without replacing the safe handler registry.
            const changesState = Array.isArray(interaction?.changesState) ? interaction.changesState : [];
            if (!changesState.length) warnings.push(`interaction ${interactionId} declares no changesState expectations`);
            changesState.forEach((entry, index) => {
              if (!String(entry || "").trim()) errors.push(`interaction ${interactionId} changesState[${index}] is empty`);
            });
            const successStatus = String(interaction?.successStatus || interaction?.status || "").trim();
            if (!successStatus) warnings.push(`interaction ${interactionId} declares no successStatus`);
            const nextObjectives = shuttle3dInteractionStringList(interaction?.nextObjective);
            nextObjectives.forEach((objectiveId) => {
              if (!objectives[objectiveId]) errors.push(`interaction ${interactionId} nextObjective references missing objective ${objectiveId}`);
            });
          }
        });

        (Array.isArray(config?.interactables) ? config.interactables : []).forEach((interactable) => {
          const id = String(interactable?.id || "interactable");
          const location = String(interactable?.location || "");
          const position = Array.isArray(interactable?.position) ? interactable.position : [];
          const x = Number(position[0]);
          const z = Number(position[1]);
          const locationMatches = shuttle3dRoomsForLocation(config, location);
          const room = shuttle3dRoomForLocationAtPoint(config, location, x, z);
          if (rules.requireReachableInteractables && !locationMatches.length) errors.push(`${id} points at missing location ${location}`);
          else if (rules.requireReachableInteractables && !shuttle3dPointInsideBounds(x, z, room?.bounds)) errors.push(`${id} is outside its declared room/location bounds`);
          if (rules.requireReachableInteractables && !shuttle3dPointInsideBounds(x, z, movementBounds)) errors.push(`${id} is outside playable movement bounds`);
          if (rules.requireReachableInteractables && (!Number.isFinite(Number(interactable?.range)) || Number(interactable.range) <= 0)) {
            errors.push(`${id} has an invalid interaction range`);
          }
          const action = String(interactable?.action || "");
          if (rules.requireInteractionHandlers && !action) errors.push(`${id} has no action id`);
          else if (rules.requireInteractionHandlers && !interactions[action]) errors.push(`${id} references missing interaction ${action}`);
          if (id.startsWith("terminal.") && !terminals[id]) warnings.push(`${id} has no matching terminal state entry`);
          if (id.startsWith("door.") && !doors[id]) warnings.push(`${id} has no matching door state entry`);
        });

        (Array.isArray(config?.props) ? config.props : []).forEach((prop) => {
          // Patch J keeps data-defined visual content honest by validating prop targets.
          const id = String(prop?.id || "prop");
          const roomId = String(prop?.room || prop?.location || "");
          const position = Array.isArray(prop?.position) ? prop.position : [];
          const x = Number(position[0]);
          const z = Number(position[1]);
          const exactRoom = config?.roomMap?.[roomId] || null;
          const locationMatches = exactRoom ? [exactRoom] : shuttle3dRoomsForLocation(config, roomId);
          const room = exactRoom || shuttle3dRoomForLocationAtPoint(config, roomId, x, z);
          const target = String(prop?.target || "").trim();
          const display = String(prop?.display || "").trim();
          if (rules.requireRenderableProps && !locationMatches.length) errors.push(`${id} points at missing room ${roomId}`);
          else if (rules.requireRenderableProps && !shuttle3dPointInsideBounds(x, z, room?.bounds)) errors.push(`${id} is outside its declared room/location bounds`);
          if (rules.requireRenderableProps && !shuttle3dPointInsideBounds(x, z, movementBounds)) errors.push(`${id} is outside playable movement bounds`);
          if (rules.requireRenderableProps && !String(prop?.kind || "").trim()) errors.push(`${id} is missing a render kind`);
          if (rules.requirePropTargets && target && !propTargetIsKnown(target)) errors.push(`${id} targets missing content ${target}`);
          if (rules.requirePropTargets && display && !propDisplayTargets.has(display)) errors.push(`${id} references missing display ${display}`);
        });

        Object.entries(config?.spawns || {}).forEach(([spawnId, spawn]) => {
          const [x, z] = shuttle3dPositionFromSpawn(spawn);
          const location = spawn?.room || spawn?.location;
          const locationMatches = shuttle3dRoomsForLocation(config, location);
          const room = shuttle3dRoomForLocationAtPoint(config, location, x, z);
          if (rules.requireSpawnInsideRoom && !locationMatches.length) errors.push(`spawn ${spawnId} points at missing room ${spawn?.room || spawn?.location}`);
          else if (rules.requireSpawnInsideRoom && !shuttle3dPointInsideBounds(x, z, room?.bounds)) errors.push(`spawn ${spawnId} is outside its declared room/location bounds`);
          if (rules.requireSpawnInsideRoom && !shuttle3dPointInsideBounds(x, z, movementBounds)) errors.push(`spawn ${spawnId} is outside playable movement bounds`);
        });

        return {
          schema: "game.motherShipInterior.validation.v1",
          ok: errors.length === 0,
          errors,
          warnings,
          rules
        };
      }


      const SHUTTLE3D_MOTHER_SHIP_INTERIOR_SCHEMA = "game.motherShipInterior.v1";
      const SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION = "game.motherShipInterior.definition.v2";
      const SHUTTLE3D_MOTHER_SHIP_INTERIOR_STATE_VERSION = "game.motherShipInterior.state.v1";

      function shuttle3dMotherShipInteriorVersionText(value, fallback = "") {
        const text = String(value || "").trim();
        return text || fallback;
      }

      function shuttle3dMotherShipInteriorApplyScalarDefault(target, key, fallback, defaultsApplied) {
        if (target[key] === undefined || target[key] === null || String(target[key]).trim() === "") {
          target[key] = shuttle3dCloneJson(fallback);
          defaultsApplied.push(key);
        }
      }

      function shuttle3dMotherShipInteriorApplyObjectDefault(target, key, fallback, defaultsApplied) {
        const supplied = shuttle3dObjectValue(target[key]);
        if (!Object.keys(supplied).length) {
          target[key] = shuttle3dCloneJson(fallback, {});
          defaultsApplied.push(key);
          return;
        }
        target[key] = {...shuttle3dCloneJson(fallback, {}), ...shuttle3dCloneJson(supplied, {})};
      }

      function shuttle3dMotherShipInteriorApplyListDefault(target, key, fallback, defaultsApplied) {
        if (!Array.isArray(target[key]) || !target[key].length) {
          target[key] = shuttle3dCloneJson(Array.isArray(fallback) ? fallback : []);
          defaultsApplied.push(key);
        }
      }

      function shuttle3dMigrateMotherShipInteriorDefinition(value, defaults, levelDefaults) {
        // Patch T centralizes compatibility defaults before renderers and validators read the definition.
        const source = shuttle3dObjectValue(value);
        const migrated = shuttle3dCloneJson(source, {});
        const defaultsApplied = [];
        const migrations = [];
        const sourceDefinitionVersion = shuttle3dMotherShipInteriorVersionText(
          source.definitionVersion || source.definition_version || source.version || source.schema,
          "legacy-unversioned"
        );

        if (!source.definitionVersion || source.definitionVersion !== SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION) {
          migrations.push(`${sourceDefinitionVersion}->${SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION}`);
        }

        if (!source.schema) defaultsApplied.push("schema");
        migrated.schema = SHUTTLE3D_MOTHER_SHIP_INTERIOR_SCHEMA;
        migrated.definitionVersion = SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION;
        migrated.stateVersion = shuttle3dMotherShipInteriorVersionText(
          source.stateVersion || source.state_version,
          SHUTTLE3D_MOTHER_SHIP_INTERIOR_STATE_VERSION
        );

        shuttle3dMotherShipInteriorApplyScalarDefault(migrated, "enabled", true, defaultsApplied);
        shuttle3dMotherShipInteriorApplyScalarDefault(migrated, "initialLocation", defaults.location, defaultsApplied);
        shuttle3dMotherShipInteriorApplyScalarDefault(migrated, "initialObjective", defaults.objectiveId, defaultsApplied);
        shuttle3dMotherShipInteriorApplyScalarDefault(migrated, "power", defaults.power, defaultsApplied);
        shuttle3dMotherShipInteriorApplyScalarDefault(migrated, "security", defaults.security, defaultsApplied);

        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "locations", defaults.locations, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "objectives", defaults.objectives, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "doors", defaults.doors, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "terminals", defaults.terminals, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "flags", defaults.flags, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "spawns", levelDefaults.spawns, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "movement", levelDefaults.movement, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "interactions", levelDefaults.interactions, defaultsApplied);
        shuttle3dMotherShipInteriorApplyObjectDefault(migrated, "validation", {
          requireRoomBoundsInsideMovement: true,
          requireConnectedRooms: true,
          requireReachableInteractables: true,
          requireInteractionHandlers: true,
          requireInteractionEffects: true,
          requireObjectiveTargets: true,
          requireSpawnInsideRoom: true,
          requireRenderableProps: true,
          requirePropTargets: true,
          requireOpenDoors: true,
          requireDefinitionVersion: true
        }, defaultsApplied);

        shuttle3dMotherShipInteriorApplyListDefault(migrated, "rooms", levelDefaults.rooms, defaultsApplied);
        shuttle3dMotherShipInteriorApplyListDefault(migrated, "exits", levelDefaults.exits, defaultsApplied);
        shuttle3dMotherShipInteriorApplyListDefault(migrated, "props", levelDefaults.props, defaultsApplied);
        shuttle3dMotherShipInteriorApplyListDefault(migrated, "interactables", levelDefaults.interactables, defaultsApplied);

        return {
          definition: migrated,
          report: {
            schema: "game.motherShipInterior.migration.v1",
            sourceDefinitionVersion,
            targetDefinitionVersion: SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION,
            stateVersion: migrated.stateVersion,
            migratedAtLoad: migrations.length > 0 || defaultsApplied.length > 0,
            migrations,
            defaultsApplied: Array.from(new Set(defaultsApplied)).sort()
          }
        };
      }

      function shuttle3dMotherShipInteriorConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.motherShipInterior;
        const defaults = shuttle3dMotherShipInteriorStateDefaults();
        const levelDefaults = shuttle3dMotherShipInteriorLevelDefaults();
        const migratedInterior = shuttle3dMigrateMotherShipInteriorDefinition(supplied, defaults, levelDefaults);
        const interior = migratedInterior.definition;
        const suppliedStateDefaults = shuttle3dObjectValue(interior.stateDefaults);

        const locations = shuttle3dStringMap(interior.locations, defaults.locations);
        const objectives = shuttle3dObjectMap(interior.objectives, defaults.objectives);
        const rooms = shuttle3dNormalizeMotherShipRooms(interior.rooms, levelDefaults.rooms, locations);
        const roomMap = shuttle3dRoomMap(rooms);
        const exits = shuttle3dNormalizeMotherShipExits(interior.exits, levelDefaults.exits);
        const movement = shuttle3dNormalizeMotherShipMovement(interior.movement, levelDefaults.movement, rooms);
        const spawns = shuttle3dNormalizeMotherShipSpawns(interior.spawns, levelDefaults.spawns, locations);
        const props = shuttle3dNormalizeMotherShipProps(interior.props, levelDefaults.props, rooms);
        const doors = shuttle3dNormalizeMotherShipDoors({
          ...defaults.doors,
          ...shuttle3dObjectValue(interior.doors),
          ...shuttle3dObjectValue(suppliedStateDefaults.doors)
        });
        const terminals = shuttle3dObjectMap({
          ...defaults.terminals,
          ...shuttle3dObjectValue(interior.terminals),
          ...shuttle3dObjectValue(suppliedStateDefaults.terminals)
        });
        const interactables = shuttle3dNormalizeMotherShipInteractables(
          interior.interactables,
          levelDefaults.interactables,
          terminals,
          doors
        );
        const interactions = shuttle3dNormalizeMotherShipInteractions(
          interior.interactions,
          levelDefaults.interactions
        );
        const flags = shuttle3dNormalizeMotherShipFlags({
          ...defaults.flags,
          ...shuttle3dObjectValue(interior.flags),
          ...shuttle3dObjectValue(suppliedStateDefaults.flags)
        });
        const validationRules = shuttle3dNormalizeMotherShipValidationRules(interior.validation);

        const initialLocation = String(
          suppliedStateDefaults.location
          || interior.initialLocation
          || interior.location
          || defaults.location
        );
        const initialObjective = String(
          suppliedStateDefaults.objectiveId
          || interior.initialObjective
          || interior.objectiveId
          || defaults.objectiveId
        );
        const stateDefaults = {
          schema: defaults.schema,
          stateVersion: interior.stateVersion || SHUTTLE3D_MOTHER_SHIP_INTERIOR_STATE_VERSION,
          location: locations[initialLocation] ? initialLocation : defaults.location,
          objectiveId: objectives[initialObjective] ? initialObjective : defaults.objectiveId,
          power: String(suppliedStateDefaults.power || interior.power || defaults.power),
          security: String(suppliedStateDefaults.security || interior.security || defaults.security),
          doors: shuttle3dCloneJson(doors),
          terminals: shuttle3dCloneJson(terminals),
          flags: shuttle3dCloneJson(flags),
          lastInteractionStatus: ""
        };

        const config = {
          schema: interior.schema || SHUTTLE3D_MOTHER_SHIP_INTERIOR_SCHEMA,
          definitionVersion: interior.definitionVersion || SHUTTLE3D_MOTHER_SHIP_INTERIOR_DEFINITION_VERSION,
          stateVersion: stateDefaults.stateVersion,
          migration: shuttle3dCloneJson(migratedInterior.report),
          enabled: interior.enabled !== false,
          initialLocation: stateDefaults.location,
          initialObjective: stateDefaults.objectiveId,
          power: stateDefaults.power,
          security: stateDefaults.security,
          locations,
          objectives,
          rooms: shuttle3dCloneJson(rooms),
          roomMap: shuttle3dCloneJson(roomMap),
          exits: shuttle3dCloneJson(exits),
          movement: shuttle3dCloneJson(movement),
          spawns: shuttle3dCloneJson(spawns),
          props: shuttle3dCloneJson(props),
          interactables: shuttle3dCloneJson(interactables),
          interactions: shuttle3dCloneJson(interactions),
          doors: shuttle3dCloneJson(stateDefaults.doors),
          terminals: shuttle3dCloneJson(stateDefaults.terminals),
          flags: shuttle3dCloneJson(stateDefaults.flags),
          stateDefaults,
          validationRules: shuttle3dCloneJson(validationRules)
        };
        config.validationReport = shuttle3dValidateMotherShipInteriorConfig(config, validationRules);
        return config;
      }


      function shuttle3dRayIntersectsBounds(origin, direction, bounds) {
        let near = -Infinity;
        let far = Infinity;
        for (let axis = 0; axis < 3; axis += 1) {
          const ray = direction[axis];
          const minimum = bounds.min[axis];
          const maximum = bounds.max[axis];
          if (Math.abs(ray) < 0.00001) {
            if (origin[axis] < minimum || origin[axis] > maximum) return Infinity;
            continue;
          }
          const t0 = (minimum - origin[axis]) / ray;
          const t1 = (maximum - origin[axis]) / ray;
          near = Math.max(near, Math.min(t0, t1));
          far = Math.min(far, Math.max(t0, t1));
          if (far < near) return Infinity;
        }
        if (far < 0) return Infinity;
        return Math.max(0, near);
      }


      function shuttle3dCombatConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.combat;
        const combat = supplied && typeof supplied === "object" ? supplied : {};
        const player = combat.player && typeof combat.player === "object" ? combat.player : {};
        const phaser = combat.phaser && typeof combat.phaser === "object" ? combat.phaser : {};
        const transport = combat.transport && typeof combat.transport === "object" ? combat.transport : {};
        const alien = combat.alien && typeof combat.alien === "object" ? combat.alien : {};
        const alienShip = combat.alienShip && typeof combat.alienShip === "object" ? combat.alienShip : {};
        const number = (value, fallback, minimum, maximum) => {
          const parsed = Number(value);
          if (!Number.isFinite(parsed)) return fallback;
          return Math.min(maximum, Math.max(minimum, parsed));
        };
        const vector = (value, fallback) => (
          Array.isArray(value)
          && value.length === 3
          && value.every((entry) => Number.isFinite(Number(entry)))
            ? value.map(Number)
            : fallback.slice()
        );
        const suppliedSpawnPoints = Array.isArray(transport.spawnPoints) ? transport.spawnPoints : [];
        const spawnPoints = suppliedSpawnPoints
          .filter((point) => point && typeof point === "object")
          .map((point, index) => ({
            id: String(point.id || `transport-pad-${index + 1}`),
            position: vector(point.position, [0, -0.55, 0.3])
          }));
        if (!spawnPoints.length) {
          spawnPoints.push(
            {id: "port-aft-pad", position: [-2.9, -0.55, 2.55]},
            {id: "starboard-aft-pad", position: [2.9, -0.55, 2.55]},
            {id: "center-pad", position: [0, -0.55, 0.3]},
            {id: "forward-pad", position: [0, -0.55, -3.25]}
          );
        }
        const maxHealth = Math.round(number(player.maxHealth, 100, 1, 1000));
        return {
          enabled: combat.enabled !== false,
          player: {
            maxHealth,
            startingHealth: Math.round(number(player.startingHealth, maxHealth, 1, maxHealth))
          },
          phaser: {
            enabled: phaser.enabled !== false,
            damage: number(phaser.damage, 34, 1, 500),
            cooldownMs: number(phaser.cooldownMs, 280, 50, 5000),
            range: number(phaser.range, 28, 2, 200),
            beamDurationMs: number(phaser.beamDurationMs, 130, 30, 1200)
          },
          alienShip: {
            id: String(alienShip.id || "alien-raider"),
            position: vector(alienShip.position, [-6.4, 2.8, -48]),
            scale: vector(alienShip.scale, [3.8, 0.9, 2.5]).map((value) => Math.max(0.25, Math.abs(value)))
          },
          transport: {
            initialDelayMs: number(transport.initialDelayMs, 2200, 0, 60000),
            intervalMs: number(transport.intervalMs, 5000, 500, 60000),
            beamDurationMs: number(transport.beamDurationMs, 900, 100, 5000),
            maxAlive: Math.round(number(transport.maxAlive, 4, 1, 24)),
            spawnPoints
          },
          alien: {
            maxHealth: number(alien.maxHealth, 60, 1, 1000),
            speed: number(alien.speed, 1.05, 0.1, 8),
            radius: number(alien.radius, 0.38, 0.12, 1.4),
            attackRange: number(alien.attackRange, 1.05, 0.3, 5),
            damage: number(alien.damage, 8, 1, 100),
            attackCooldownMs: number(alien.attackCooldownMs, 850, 100, 5000)
          }
        };
      }

      class Shuttle3dGeometryWriter {
        constructor(options = {}) {
          this.values = [];
          this.annotationTargets = Array.isArray(options.annotationTargets) ? options.annotationTargets : null;
          this.annotationSource = String(options.annotationSource || "runtime geometry");
          this.annotationSequence = 0;
        }

        color(value, emissive = false) {
          const rgb = sceneColorRgb(value);
          const color = [rgb.r, rgb.g, rgb.b, emissive ? 1 : 0];
          color.sourceColor = String(value || "");
          color.emissive = Boolean(emissive);
          return color;
        }

        annotationBoundsForPoints(points, padding = 0.02) {
          const usable = (Array.isArray(points) ? points : [])
            .filter((point) => Array.isArray(point) && point.length >= 3)
            .map((point) => point.slice(0, 3).map(Number))
            .filter((point) => point.every(Number.isFinite));
          if (!usable.length) return null;
          const xs = usable.map((point) => point[0]);
          const ys = usable.map((point) => point[1]);
          const zs = usable.map((point) => point[2]);
          const amount = Math.max(0.005, Math.min(0.75, Number(padding) || 0.02));
          return {
            min: [Math.min(...xs) - amount, Math.min(...ys) - amount, Math.min(...zs) - amount],
            max: [Math.max(...xs) + amount, Math.max(...ys) + amount, Math.max(...zs) + amount]
          };
        }

        recordAnnotationPrimitive(kind, points, color, details = {}) {
          // Patch U.1: capture a fallback selectable target for visible rendered primitives.
          // Data-defined targets remain preferred, but this lets P+click hit one-off bars,
          // beams, view-model pieces, and other visible runtime geometry that has not yet
          // been promoted into authored content data.
          if (!this.annotationTargets) return;
          const normalizedKind = String(kind || "primitive").toLowerCase();
          const bounds = this.annotationBoundsForPoints(points, details.padding);
          if (!bounds) return;
          const sequence = this.annotationSequence + 1;
          this.annotationSequence = sequence;
          const sourceId = String(this.annotationSource || "runtime")
            .trim()
            .replace(/[^a-zA-Z0-9_.:-]+/g, "-")
            .replace(/^-+|-+$/g, "") || "runtime";
          const colorName = String(color?.sourceColor || "").trim();
          const targetId = `${sourceId}.${normalizedKind}.${String(sequence).padStart(4, "0")}`;
          const labelBits = [this.annotationSource, colorName, normalizedKind]
            .filter(Boolean)
            .map((value) => String(value).replace(/^scene-viewer\./, ""));
          this.annotationTargets.push({
            targetKind: `rendered-${normalizedKind}`,
            targetId,
            targetKey: `rendered:${targetId}`,
            label: labelBits.length ? labelBits.join(" ") : `Rendered ${normalizedKind}`,
            room: "",
            source: this.annotationSource,
            primitiveKind: normalizedKind,
            color: colorName,
            emissive: Boolean(color?.emissive || color?.[3]),
            bounds
          });
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
          this.recordAnnotationPrimitive("box", [p000, p100, p010, p110, p001, p101, p011, p111], color, {padding: 0.012});
          this.quad(p001, p101, p111, p011, color);
          this.quad(p100, p000, p010, p110, color);
          this.quad(p000, p001, p011, p010, color);
          this.quad(p101, p100, p110, p111, color);
          this.quad(p010, p011, p111, p110, color);
          this.quad(p000, p100, p101, p001, color);
        }


        beam(start, end, radius, color) {
          const axis = shuttle3dNormalizeVector(shuttle3dSubtract(end, start));
          const guide = Math.abs(axis[1]) < 0.9 ? [0, 1, 0] : [1, 0, 0];
          const right = shuttle3dNormalizeVector(shuttle3dCross(axis, guide));
          const up = shuttle3dNormalizeVector(shuttle3dCross(right, axis));
          const corner = (point, rightScale, upScale) => [
            point[0] + right[0] * rightScale + up[0] * upScale,
            point[1] + right[1] * rightScale + up[1] * upScale,
            point[2] + right[2] * rightScale + up[2] * upScale
          ];
          const a = corner(start, -radius, -radius);
          const b = corner(start, radius, -radius);
          const c = corner(start, radius, radius);
          const d = corner(start, -radius, radius);
          const e = corner(end, -radius, -radius);
          const f = corner(end, radius, -radius);
          const g = corner(end, radius, radius);
          const h = corner(end, -radius, radius);
          this.recordAnnotationPrimitive("beam", [a, b, c, d, e, f, g, h], color, {padding: Math.max(0.02, radius * 0.65)});
          this.quad(a, b, c, d, color);
          this.quad(e, h, g, f, color);
          this.quad(a, e, f, b, color);
          this.quad(b, f, g, c, color);
          this.quad(c, g, h, d, color);
          this.quad(d, h, e, a, color);
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
          this.recordAnnotationPrimitive("console-wedge", [a, b, c, d, e, f, g, h], color, {padding: 0.02});
          this.quad(a, b, f, e, color);
          this.quad(b, c, g, f, color);
          this.quad(c, d, h, g, color);
          this.quad(d, a, e, h, color);
          this.quad(e, f, g, h, color);
          this.quad(d, c, b, a, color);
        }

        ellipsoid(center, radii, segments, rings, color) {
          if (Array.isArray(center) && Array.isArray(radii) && center.length >= 3 && radii.length >= 3) {
            this.recordAnnotationPrimitive("ellipsoid", [
              [Number(center[0]) - Math.abs(Number(radii[0]) || 0), Number(center[1]) - Math.abs(Number(radii[1]) || 0), Number(center[2]) - Math.abs(Number(radii[2]) || 0)],
              [Number(center[0]) + Math.abs(Number(radii[0]) || 0), Number(center[1]) + Math.abs(Number(radii[1]) || 0), Number(center[2]) + Math.abs(Number(radii[2]) || 0)]
            ], color, {padding: 0.025});
          }
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
        constructor(canvas, scene, options = {}) {
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

          // Patch G: keep renderer bootstrapping in named seams so future patches can
          // move gameplay, geometry, and lifecycle systems out of this class safely.
          this.initializeRendererFrameState(scene);
          this.compile();
          // Legacy seam contract retained for static consumers: initializeGameplaySubsystems(scene)
          this.initializeGameplaySubsystems(scene, options);
          this.initializeCombatRuntimeState();
          this.initializeGeometryBuffers();
          this.initializeCanvasLifecycle(canvas);
          this.resize();
          this.draw = this.draw.bind(this);
          this.animationFrame = requestAnimationFrame(this.draw);
        }

        initializeRendererFrameState(scene) {
          this.rendererSubsystems = {
            frameState: "scene-viewer.shuttle3d.frame-state.v1",
            gameplay: "scene-viewer.shuttle3d.gameplay-subsystems.v1",
            geometry: "scene-viewer.shuttle3d.geometry-buffers.v1",
            lifecycle: "scene-viewer.shuttle3d.canvas-lifecycle.v1"
          };
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
        }

        initializeGameplaySubsystems(scene, options = {}) {
          this.starfield = shuttle3dStarfieldConfig(scene);
          this.combat = shuttle3dCombatConfig(scene);
          this.flightConfig = shuttle3dFlightConfig(scene);
          this.interiorConfig = shuttle3dMotherShipInteriorConfig(scene);
          this.shipDefinitionValidation = this.interiorConfig.validationReport;
          this.spaceNavigationRuntime = this.createSpaceNavigationRuntime(options);
          this.spaceNavigationError = this.spaceNavigationRuntime ? "" : this.spaceNavigationError || "Space-navigation definition unavailable.";
          this.characterAIRuntime = this.createCharacterAIRuntime(options);
          this.characterAIError = this.characterAIRuntime ? "" : this.characterAIError || "Character AI definition unavailable.";
          this.lastCharacterAIUiAt = -Infinity;
          this.onCharacterAIChanged = null;
          this.lastCharacterAIMessage = "";
          this.navigationConsoleOpen = false;
          this.navigationConsoleAccessTargetId = "";
          this.lastNavigationUiAt = -Infinity;
          this.onNavigationChanged = null;
          this.flight = this.createFlightState();
          this.shipState = this.createShipState();
          this.shipInteractionRegistry = this.createShipInteractionRegistry();
          this.pilotStations = shuttle3dPilotStationsConfig(scene);
          this.hoveredPilotStation = null;
          this.polygonAnnotationKeyHeld = false;
          this.pilot = {
            active: false,
            station: null,
            throttle: 0,
            heading: 0,
            pitch: 0,
            roll: 0,
            impulse: 0
          };
          this.bayControlInputUnlockAtMs = 0;
          this.bayControlSuppressedKeys = new Set();
          this.combatPauseStartedAtMs = null;
          this.lastPilotUiAt = -Infinity;
          this.lastShipUiAt = -Infinity;
          this.onPilotChanged = null;
          this.onBayControlStarted = null;
          this.onShipStateChanged = null;
        }

        initializeCombatRuntimeState() {
          this.playerHealth = this.combat.player.startingHealth;
          this.aliens = [];
          this.transportSequence = 0;
          this.kills = 0;
          this.gameOver = false;
          this.combatClockMs = 0;
          this.nextTransportAtMs = this.combat.transport.initialDelayMs;
          this.lastPhaserShotAt = -Infinity;
          this.phaserBeam = null;
          this.lastCombatUiAt = -Infinity;
          this.onCombatChanged = null;
        }

        initializeGeometryBuffers() {
          this.geometry = this.buildGeometry();
          this.worldVertexCount = this.geometry.length / 10;
          this.starGeometry = this.buildStarfieldGeometry();
          this.starVertexCount = this.starGeometry.length / 10;
          this.dynamicGeometry = this.buildDynamicGeometry(0);
          this.dynamicVertexCount = this.dynamicGeometry.length / 10;
          this.vertexCount = this.worldVertexCount + this.starVertexCount + this.dynamicVertexCount;
          this.buffer = this.gl.createBuffer();
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.buffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.geometry, this.gl.STATIC_DRAW);
          this.starBuffer = this.gl.createBuffer();
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.starBuffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.starGeometry, this.gl.STATIC_DRAW);
          this.dynamicBuffer = this.gl.createBuffer();
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.dynamicBuffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.dynamicGeometry, this.gl.DYNAMIC_DRAW);
        }

        initializeCanvasLifecycle(canvas) {
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
          const annotationTargets = [];
          const builder = new Shuttle3dGeometryWriter({
            annotationTargets,
            annotationSource: "scene-viewer.static"
          });
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

          const hullFrontZ = forward[0][2];
          const hullAftZ = aft[0][2];
          const ribStartZ = hullFrontZ + 0.85;
          const ribEndZ = hullAftZ - 0.85;
          const ribSpacing = (ribEndZ - ribStartZ) / 5;
          Array.from({length: 6}, (_, index) => ribStartZ + ribSpacing * index).forEach((z) => {
            builder.box([-4.44, -1.34, z - 0.08], [-4.25, 2.1, z + 0.08], trim);
            builder.box([4.25, -1.34, z - 0.08], [4.44, 2.1, z + 0.08], trim);
            builder.box([-3.48, 3.0, z - 0.08], [3.48, 3.14, z + 0.08], trim);
          });

          const deckFrontZ = hullFrontZ + 0.65;
          const deckAftZ = hullAftZ - 0.7;
          [-2.55, -1.28, 0, 1.28, 2.55].forEach((x) => {
            builder.box([x - 0.025, -1.405, deckFrontZ], [x + 0.025, -1.365, deckAftZ], builder.color("#52708c"));
          });
          builder.box([-0.42, -1.39, deckFrontZ], [0.42, -1.34, deckAftZ], builder.color("#315875"));

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

          // Exterior ships are appended dynamically so console piloting can fly the shuttle toward the mother ship.

          this.staticAnnotationPrimitiveTargets = annotationTargets;
          this.refreshAnnotationPrimitiveTargets?.();
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

        createFlightState() {
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          return {
            distance: config.startDistance,
            forwardSpeed: 0,
            lateralOffset: 0,
            verticalOffset: 0,
            docked: false,
            dockingCutsceneActive: false,
            dockingCutsceneStartedAtMs: 0,
            dockingCutsceneElapsedMs: 0,
            dockingCutscenePhase: "approach",
            dockingCutsceneComplete: false,
            playerExitedToBay: false,
            bayPlayerControlActive: false
          };
        }

        createSpaceNavigationRuntime(options = {}) {
          const api = globalThis.MainComputerSpaceNavigationRuntime;
          const definition = options.spaceNavigation
            || options.project?.metadata?.spaceNavigation
            || null;
          if (!api?.create || !definition) return null;
          try {
            return api.create(definition, {projectId: options.projectId || options.project?.id || "game-project"});
          } catch (error) {
            this.spaceNavigationError = error instanceof Error ? error.message : String(error || "Space-navigation runtime failed.");
            console.error("Space-navigation runtime initialization failed", error);
            return null;
          }
        }

        createCharacterAIRuntime(options = {}) {
          const api = globalThis.MainComputerCharacterAIRuntime;
          const definition = options.characterAI
            || options.project?.metadata?.characterAI
            || null;
          if ((!api?.ensure && !api?.create) || !definition) {
            api?.clearCurrent?.();
            return null;
          }
          try {
            const projectId = options.projectId || options.project?.id || "game-project";
            return api.ensure
              ? api.ensure(projectId, definition)
              : api.create(definition, {projectId});
          } catch (error) {
            this.characterAIError = error instanceof Error
              ? error.message
              : String(error || "Character AI runtime failed.");
            api?.clearCurrent?.();
            console.error("Character AI runtime initialization failed", error);
            return null;
          }
        }

        characterAIPhase() {
          if (this.isDockingCutsceneActive?.()) return "transition";
          return this.isShuttleBaySceneActive?.() ? "mother-ship" : "shuttle";
        }

        canCharacterOccupy(characterId, x, z) {
          const radius = 0.34;
          const characters = this.characterAIRuntime?.activeCharacters?.() || [];

          if (this.characterAIPhase() === "mother-ship") {
            // Mother-ship boarders share the bridge world, not the shuttle combat deck.
            // Reusing shuttle bounds/colliders here pinned every boarder in place because
            // their bridge coordinates were outside that unrelated movement mesh.
            const camera = Array.isArray(this.camera) ? this.camera : [0, 0, 0];
            const withinBridgeEncounter = (
              Math.abs(x - camera[0]) <= 18
              && Math.abs(z - camera[2]) <= 24
            );
            if (!withinBridgeEncounter) return false;
          } else {
            const {bounds, colliders} = this.movement;
            if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) return false;
            const blockedByFixture = colliders.some((collider) => (
              x > collider.minX - radius
              && x < collider.maxX + radius
              && z > collider.minZ - radius
              && z < collider.maxZ + radius
            ));
            if (blockedByFixture) return false;
          }

          return !characters.some((other) => (
            other.id !== characterId
            && Math.hypot(x - other.position[0], z - other.position[2]) < radius * 1.7
          ));
        }

        characterAIWorld(nowMs = this.lastFrameTime ?? 0) {
          const navigation = this.navigationSnapshot?.(nowMs) || {};
          return {
            phase: this.characterAIPhase(),
            player: {
              alive: !this.gameOver && this.playerHealth > 0,
              health: this.playerHealth,
              position: this.camera.slice()
            },
            ship: {
              power: String(this.shipState?.power || "unknown"),
              security: String(this.shipState?.security || "unknown"),
              currentSystemId: String(navigation.currentSystemId || "")
            },
            scenario: globalThis.MainComputerSystemScenarioRuntime
              ?.current?.()
              ?.activeScenarioContext?.()
              || {id: "", status: "none", stageId: ""},
            canOccupy: (characterId, x, z) => this.canCharacterOccupy(characterId, x, z)
          };
        }

        visibleCharacterAICharacters() {
          if (!this.characterAIRuntime?.activeCharacters) return [];
          if (this.characterAIRuntime.activeCharactersForWorld) {
            return this.characterAIRuntime.activeCharactersForWorld(
              this.characterAIWorld(this.lastFrameTime ?? 0)
            );
          }
          const phase = this.characterAIPhase();
          return this.characterAIRuntime.activeCharacters().filter((character) => {
            const definition = this.characterAIRuntime.characterDefinition?.(character.id);
            const activePhases = Array.isArray(definition?.activePhases)
              ? definition.activePhases
              : [];
            return !activePhases.length || activePhases.includes(phase);
          });
        }

        characterAISnapshot() {
          const summary = this.characterAIRuntime?.summary?.() || null;
          const characters = this.visibleCharacterAICharacters();
          const activeThreats = characters.filter((character) => (
            character.kind === "enemy"
            && character.status === "active"
            && Number(character.health || 0) > 0
          ));
          return {
            enabled: Boolean(summary),
            error: this.characterAIError || "",
            phase: this.characterAIPhase(),
            player: {
              alive: !this.gameOver && this.playerHealth > 0,
              health: this.playerHealth,
              position: this.camera.slice()
            },
            activeThreatCount: activeThreats.length,
            activeThreatIds: activeThreats.map((character) => character.id),
            summary,
            characters
          };
        }

        emitCharacterAIState(force = false) {
          if (typeof this.onCharacterAIChanged !== "function") return;
          const nowMs = Number.isFinite(this.lastFrameTime) ? this.lastFrameTime : 0;
          if (!force && nowMs - this.lastCharacterAIUiAt < 120) return;
          this.lastCharacterAIUiAt = nowMs;
          this.onCharacterAIChanged(this.characterAISnapshot());
        }

        applyCharacterAIEffect(effect, nowMs) {
          const item = effect && typeof effect === "object" ? effect : {};
          if (item.type === "damage-player") {
            const amount = Math.max(0, Number(item.amount) || 0);
            const attacker = String(item.label || item.characterId || "Hostile").trim();
            this.playerHealth = Math.max(0, this.playerHealth - amount);
            this.lastCharacterAIMessage = `HIT BY ${attacker.toUpperCase()} (-${amount}) — BREAK LINE OF SIGHT`;
            this.setShipInteractionStatus?.(this.lastCharacterAIMessage);
            if (this.playerHealth <= 0) {
              this.gameOver = true;
              this.clearMovementKeys();
            }
            return true;
          }
          if (item.type === "threat-warning") {
            const message = String(
              item.message || `${item.label || item.characterId || "Hostile"} is aiming at you — move to cover.`
            ).trim();
            if (message) {
              this.lastCharacterAIMessage = message;
              this.setShipInteractionStatus?.(message);
            }
            return Boolean(message);
          }
          if (item.type === "repair-ship-power") {
            if (this.shipState?.power !== "online") {
              this.restoreEngineeringPower(null, {
                status: "Engineering Officer Mara Venn restored main power. Bridge route confirmed open."
              });
            }
            return true;
          }
          if (item.type === "character-message" || item.type === "support-requested") {
            const message = String(item.message || "").trim();
            if (message && message !== this.lastCharacterAIMessage) {
              this.lastCharacterAIMessage = message;
              this.setShipInteractionStatus?.(message);
            }
            return Boolean(message);
          }
          return false;
        }

        updateCharacterAI(nowMs, deltaSeconds) {
          if (!this.characterAIRuntime?.step) return null;
          if (this.isDockingCutsceneActive?.()) {
            this.emitCharacterAIState();
            return null;
          }
          let result = null;
          try {
            result = this.characterAIRuntime.step(this.characterAIWorld(nowMs), nowMs);
            let effectChanged = false;
            (result.effects || []).forEach((effect) => {
              effectChanged = this.applyCharacterAIEffect(effect, nowMs) || effectChanged;
            });
            if (result.changed || effectChanged) {
              this.emitCharacterAIState(true);
              this.emitCombatState(true);
              this.emitShipState(true);
            } else {
              this.emitCharacterAIState();
            }
          } catch (error) {
            this.characterAIError = error instanceof Error
              ? error.message
              : String(error || "Character AI update failed.");
            console.error("Character AI update failed", error);
            this.emitCharacterAIState(true);
          }
          return result;
        }

        appendCharacterAIGeometry(builder, nowMs) {
          const characters = this.visibleCharacterAICharacters();
          characters.forEach((character) => {
            const [x, y, z] = character.position;
            const ratio = Math.max(0, Math.min(1, character.health / Math.max(1, character.maxHealth)));
            const enemy = character.kind === "enemy";
            const body = builder.color(enemy ? "#991b1b" : "#1d4ed8");
            const armor = builder.color(enemy ? "#450a0a" : "#0f172a");
            const accent = builder.color(enemy ? "#fef2f2" : "#67e8f9", true);
            const threatBeacon = builder.color("#ff2d2d", true);
            const healthBack = builder.color("#111827");
            const healthFill = builder.color(enemy ? "#f87171" : "#38bdf8", true);

            if (enemy) {
              builder.beam([x, y + 1.58, z], [x, y + 2.72, z], 0.06, threatBeacon);
              builder.box([x - 0.72, y + 2.5, z - 0.055], [x + 0.72, y + 2.62, z + 0.055], threatBeacon);
              builder.box([x - 0.055, y + 2.5, z - 0.72], [x + 0.055, y + 2.62, z + 0.72], threatBeacon);
              builder.box([x - 0.86, y - 0.7, z - 0.045], [x + 0.86, y - 0.65, z + 0.045], threatBeacon);
              builder.box([x - 0.045, y - 0.7, z - 0.86], [x + 0.045, y - 0.65, z + 0.86], threatBeacon);
            }

            builder.ellipsoid([x, y + 0.28, z], [0.32, 0.68, 0.28], 10, 6, body);
            builder.ellipsoid([x, y + 1.02, z], [0.29, 0.31, 0.28], 10, 6, body);
            builder.box([x - 0.43, y + 0.18, z - 0.15], [x + 0.43, y + 0.4, z + 0.15], armor);
            builder.box([x - 0.18, y - 0.68, z - 0.14], [x - 0.04, y + 0.06, z + 0.14], armor);
            builder.box([x + 0.04, y - 0.68, z - 0.14], [x + 0.18, y + 0.06, z + 0.14], armor);
            builder.box([x - 0.18, y + 1.03, z - 0.31], [x + 0.18, y + 1.11, z - 0.27], accent);
            builder.box([x - 0.46, y + 1.47, z - 0.06], [x + 0.46, y + 1.55, z + 0.06], healthBack);
            if (ratio > 0) {
              builder.box(
                [x - 0.44, y + 1.48, z - 0.065],
                [x - 0.44 + 0.88 * ratio, y + 1.54, z + 0.065],
                healthFill
              );
            }
          });
        }

        navigationSnapshot(nowMs = null) {
          if (!this.spaceNavigationRuntime) {
            return {
              enabled: false,
              consoleOpen: Boolean(this.navigationConsoleOpen),
              currentSystemId: "",
              currentSystemLabel: "Navigation unavailable",
              currentPlanet: null,
              currentPlanetId: "",
              currentPlanetLabel: "",
              currentPlanetClassification: "",
              currentPlanets: [],
              currentStars: [],
              currentPlanetCount: 0,
              currentStarCount: 0,
              currentHabitablePlanetCount: 0,
              captainScrawl: "",
              plottedRouteId: null,
              destinationSystemId: null,
              destinationSystemLabel: "",
              travelPhase: "unavailable",
              travelling: false,
              travelProgress: 0,
              elapsedWorldTime: 0,
              destinations: [],
              bridgeControlAccess: false,
              error: this.spaceNavigationError || "Space-navigation definition unavailable."
            };
          }
          return {
            ...this.spaceNavigationRuntime.snapshot(nowMs),
            consoleOpen: Boolean(this.navigationConsoleOpen),
            bridgeControlAccess: this.canAccessBridgeNavigationConsole(),
            error: this.spaceNavigationError || ""
          };
        }

        isWarpTravelActive() {
          return Boolean(this.spaceNavigationRuntime?.snapshot?.().travelling);
        }

        bridgeNavigationConsoleZone() {
          return this.shipInteractionZones?.().find((zone) => zone?.id === "terminal.bridge-navigation") || null;
        }

        bridgeNavigationControlTarget() {
          if (!this.isShuttleBaySceneActive() || !this.isShuttleBayPlayerControlActive()) return null;
          const target = this.bridgeNavigationConsoleZone();
          if (!target || !Array.isArray(target.position) || target.position.length < 2) return null;
          const activeLocation = this.shipLocationForPosition(this.camera[0], this.camera[2]);
          if (target.location && target.location !== activeLocation) return null;
          const dx = this.camera[0] - Number(target.position[0]);
          const dz = this.camera[2] - Number(target.position[1]);
          const distance = Math.hypot(dx, dz);
          const range = Math.max(0, Number(target.range) || 0);
          return distance <= range ? {...target, distance} : null;
        }

        canAccessBridgeNavigationConsole() {
          return Boolean(this.bridgeNavigationControlTarget());
        }

        setNavigationConsoleOpen(open = false) {
          if (!open) {
            this.navigationConsoleOpen = false;
            this.navigationConsoleAccessTargetId = "";
            this.emitNavigationState(true);
            return false;
          }
          const target = this.bridgeNavigationControlTarget();
          if (!target) {
            this.navigationConsoleOpen = false;
            this.navigationConsoleAccessTargetId = "";
            this.spaceNavigationError = "Reach the physical Bridge Navigation Console and press E to access navigation.";
            this.emitNavigationState(true);
            return false;
          }
          this.navigationConsoleOpen = true;
          this.navigationConsoleAccessTargetId = target.id;
          this.spaceNavigationError = "";
          this.emitNavigationState(true);
          return true;
        }

        navigationControlSessionActive() {
          return Boolean(
            this.navigationConsoleOpen
            && this.navigationConsoleAccessTargetId === "terminal.bridge-navigation"
            && this.canAccessBridgeNavigationConsole()
          );
        }

        requireBridgeNavigationControls(actionLabel = "use navigation") {
          if (this.navigationControlSessionActive()) return true;
          this.navigationConsoleOpen = false;
          this.navigationConsoleAccessTargetId = "";
          this.spaceNavigationError = `Return to the physical Bridge Navigation Console to ${actionLabel}.`;
          this.emitNavigationState(true);
          return false;
        }

        plotWarpCourse(routeOrDestinationId) {
          if (!this.spaceNavigationRuntime) return false;
          if (!this.requireBridgeNavigationControls("plot a course")) return false;
          try {
            this.spaceNavigationRuntime.plotCourse(routeOrDestinationId);
            this.spaceNavigationError = "";
            this.emitNavigationState(true);
            return true;
          } catch (error) {
            this.spaceNavigationError = error instanceof Error ? error.message : String(error || "Course plotting failed.");
            this.emitNavigationState(true);
            return false;
          }
        }

        clearWarpCourse() {
          if (!this.spaceNavigationRuntime) return false;
          if (!this.requireBridgeNavigationControls("clear the course")) return false;
          try {
            this.spaceNavigationRuntime.clearCourse();
            this.spaceNavigationError = "";
            this.emitNavigationState(true);
            return true;
          } catch (error) {
            this.spaceNavigationError = error instanceof Error ? error.message : String(error || "Course clearing failed.");
            this.emitNavigationState(true);
            return false;
          }
        }

        engageWarp(nowMs = this.lastFrameTime ?? performance.now()) {
          if (!this.spaceNavigationRuntime) return false;
          if (!this.requireBridgeNavigationControls("engage warp")) return false;
          try {
            this.spaceNavigationRuntime.engage(nowMs);
            this.navigationConsoleOpen = false;
            this.navigationConsoleAccessTargetId = "";
            this.spaceNavigationError = "";
            this.emitNavigationState(true);
            return true;
          } catch (error) {
            this.spaceNavigationError = error instanceof Error ? error.message : String(error || "Warp engagement failed.");
            this.emitNavigationState(true);
            return false;
          }
        }

        updateSpaceNavigation(nowMs = this.lastFrameTime ?? performance.now()) {
          if (!this.spaceNavigationRuntime) return null;
          const update = this.spaceNavigationRuntime.update(nowMs);
          const snapshot = update.snapshot || this.spaceNavigationRuntime.snapshot(nowMs);
          if (this.navigationConsoleOpen && !this.canAccessBridgeNavigationConsole()) {
            this.navigationConsoleOpen = false;
            this.navigationConsoleAccessTargetId = "";
            this.spaceNavigationError = "Bridge navigation closed because you moved away from the physical controls.";
          }
          if (update.arrived) {
            if (this.shipState?.flags) {
              this.shipState.flags.currentSystemPlanetSurveyed = false;
              this.shipState.flags.bridgeViewscreenTrackingActive = false;
              this.shipState.flags.enemyShipOnBridgeViewscreen = false;
            }
            this.setShipTerminalState?.("terminal.bridge-viewscreen", "standby");
            this.setShipTerminalState?.("terminal.bridge-tactical", "ready");
            this.setShipObjective?.("objective.planet-view", true);
            const worldSummary = Number(snapshot.currentPlanetCount || 0) > 1
              ? `${snapshot.currentPlanetCount} charted worlds are available in-system`
              : `${snapshot.currentPlanetLabel || "Destination planet"} is now on the bridge viewscreen`;
            this.setShipInteractionStatus?.(`Warp arrival committed at ${snapshot.currentSystemLabel}. ${worldSummary}. World time ${snapshot.elapsedWorldTime}.`);
            this.emitShipState?.(true);
          }
          const clock = Number.isFinite(nowMs) ? nowMs : 0;
          if (update.changed || snapshot.travelling && clock - this.lastNavigationUiAt >= 80) {
            this.emitNavigationState(
              Boolean(update.changed),
              nowMs,
              update.arrived ? "arrival-committed" : ""
            );
          }
          return update;
        }

        emitNavigationState(
          force = false,
          nowMs = this.lastFrameTime ?? performance.now(),
          changeReason = ""
        ) {
          if (typeof this.onNavigationChanged !== "function") return;
          const clock = Number.isFinite(nowMs) ? nowMs : 0;
          if (!force && clock - this.lastNavigationUiAt < 80) return;
          this.lastNavigationUiAt = clock;
          const navigation = this.navigationSnapshot(nowMs);
          if (String(changeReason || "").trim()) {
            navigation.changeReason = String(changeReason).trim();
          }
          this.onNavigationChanged(navigation);
        }

        openBridgeNavigationConsole(target, interaction) {
          if (!this.spaceNavigationRuntime) {
            this.setShipInteractionStatus(this.spaceNavigationError || "Bridge navigation is unavailable.");
            return false;
          }
          const activeTarget = this.bridgeNavigationControlTarget();
          if (target?.id !== "terminal.bridge-navigation" || !activeTarget) {
            this.setShipInteractionStatus("Stand at the physical Bridge Navigation Console to access navigation.");
            return false;
          }
          this.setShipTerminalState("terminal.bridge-navigation", "online");
          this.navigationConsoleOpen = true;
          this.navigationConsoleAccessTargetId = activeTarget.id;
          this.spaceNavigationError = "";
          this.setShipInteractionStatus(interaction?.status || "Bridge navigation online. Plot an adjacent course and engage warp.");
          this.emitNavigationState(true);
          return true;
        }

        createShipStateFromDefaults() {
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const defaults = config.stateDefaults || {
            location: config.initialLocation,
            objectiveId: config.initialObjective,
            power: config.power,
            security: config.security,
            doors: config.doors,
            terminals: config.terminals,
            flags: config.flags,
            lastInteractionStatus: ""
          };
          return {
            enabled: Boolean(config.enabled),
            location: String(defaults.location || config.initialLocation || "bay.shuttle"),
            power: String(defaults.power || config.power || "emergency"),
            security: String(defaults.security || config.security || "quarantine"),
            objectiveId: String(defaults.objectiveId || config.initialObjective || "objective.bay-ops"),
            doors: shuttle3dNormalizeMotherShipDoors(defaults.doors || config.doors || {}),
            terminals: shuttle3dCloneJson(defaults.terminals || config.terminals || {}),
            flags: shuttle3dNormalizeMotherShipFlags(defaults.flags || config.flags || {}),
            lastInteractionStatus: String(defaults.lastInteractionStatus || "")
          };
        }

        createShipState() {
          return this.createShipStateFromDefaults();
        }

        shipLocationLabel(locationId = this.shipState?.location) {
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const id = String(locationId || config.initialLocation || "bay.shuttle");
          return String(config.locations?.[id] || id);
        }

        shipObjectiveLabel(objectiveId = this.shipState?.objectiveId) {
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const id = String(objectiveId || config.initialObjective || "objective.bay-ops");
          const objective = config.objectives?.[id];
          if (objective && typeof objective === "object") return String(objective.label || id);
          return String(objective || id);
        }

        shipStateSnapshot() {
          if (!this.shipState) this.shipState = this.createShipState();
          const state = this.shipState;
          const interaction = this.shipInteractionTarget?.() || null;
          return {
            enabled: Boolean(state.enabled),
            location: state.location,
            locationLabel: this.shipLocationLabel(state.location),
            power: state.power,
            security: state.security,
            objectiveId: state.objectiveId,
            objectiveLabel: this.shipObjectiveLabel(state.objectiveId),
            doors: JSON.parse(JSON.stringify(state.doors || {})),
            terminals: JSON.parse(JSON.stringify(state.terminals || {})),
            flags: JSON.parse(JSON.stringify(state.flags || {})),
            interactionId: interaction?.id || "",
            interactionLabel: interaction?.label || "",
            interactionKind: interaction?.kind || "",
            interactionHint: this.shipInteractionHint?.(interaction) || "",
            interactionStatus: state.lastInteractionStatus || "",
            shuttleBayControlActive: this.isShuttleBayPlayerControlActive()
          };
        }

        emitShipState(force = false) {
          if (typeof this.onShipStateChanged !== "function") return;
          const clock = this.lastFrameTime ?? 0;
          if (!force && clock - this.lastShipUiAt < 120) return;
          this.lastShipUiAt = clock;
          this.onShipStateChanged(this.shipStateSnapshot());
        }

        setShipLocation(locationId, force = false) {
          if (!this.shipState) this.shipState = this.createShipState();
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const nextLocation = String(locationId || config.initialLocation || "bay.shuttle");
          if (!config.locations?.[nextLocation]) return false;
          if (this.shipState.location === nextLocation && !force) return true;
          this.shipState.location = nextLocation;
          this.emitShipState(true);
          return true;
        }

        resetFlightState() {
          this.flight = this.createFlightState();
          this.shipState = this.createShipState();
          if (this.pilot) {
            this.pilot.active = false;
            this.pilot.station = null;
            this.pilot.throttle = 0;
            this.pilot.impulse = 0;
            this.pilot.roll = 0;
          }
          this.bayControlInputUnlockAtMs = 0;
          this.bayControlSuppressedKeys = new Set();
          this.combatPauseStartedAtMs = null;
          this.clearMovementKeys();
          this.emitPilotState?.(true);
          this.emitShipState?.(true);
        }

        isDockingCutsceneActive() {
          return Boolean(this.flight?.dockingCutsceneActive);
        }

        isShuttleBaySceneActive() {
          return Boolean(this.flight?.playerExitedToBay);
        }

        isShuttleBayPlayerControlActive() {
          return Boolean(this.flight?.bayPlayerControlActive);
        }

        shuttleBayPlayerSpawn() {
          const fallback = shuttle3dMotherShipInteriorLevelDefaults().spawns["spawn.shuttle-bay"];
          const spawn = this.interiorConfig?.spawns?.["spawn.shuttle-bay"] || fallback;
          const position = Array.isArray(spawn.position) && spawn.position.length === 3
            ? spawn.position.map(Number)
            : fallback.position.slice();
          return {
            position,
            yaw: shuttle3dNumberValue(spawn.yaw, fallback.yaw),
            pitch: shuttle3dNumberValue(spawn.pitch, fallback.pitch)
          };
        }

        shuttleBayMovementConfig() {
          const fallback = shuttle3dMotherShipInteriorLevelDefaults().movement;
          const movement = this.interiorConfig?.movement || fallback;
          return {
            ...this.movement,
            // Patch C: mother-ship movement bounds now come from the level definition
            // instead of a local hardcoded envelope.
            bounds: shuttle3dBoundsValue(movement.bounds, fallback.bounds),
            colliders: Array.isArray(movement.colliders)
              ? movement.colliders.map((collider) => ({...collider}))
              : fallback.colliders.map((collider) => ({...collider}))
          };
        }

        motherShipWalkableRegions() {
          const fallback = shuttle3dMotherShipInteriorLevelDefaults().rooms;
          const rooms = Array.isArray(this.interiorConfig?.rooms) && this.interiorConfig.rooms.length
            ? this.interiorConfig.rooms
            : fallback;
          return rooms.map((room) => ({
            id: room.id,
            location: room.location || room.id,
            name: room.name || room.id,
            kind: room.kind || "room",
            priority: shuttle3dNumberValue(room.priority, 0),
            ...shuttle3dBoundsValue(room.bounds, {})
          }));
        }

        isInsideMotherShipWalkable(x, z) {
          return this.motherShipWalkableRegions().some((region) => (
            x >= region.minX && x <= region.maxX && z >= region.minZ && z <= region.maxZ
          ));
        }

        shipDoorState(doorId) {
          const rawState = this.shipState?.doors?.[doorId]?.state || this.interiorConfig?.doors?.[doorId]?.state || "open";
          const state = String(rawState || "open").toLowerCase();
          return state === "locked" ? "open" : state;
        }

        shipDoorIsOpen(doorId) {
          return this.shipDoorState(doorId) === "open";
        }

        shipDoorAllowsPosition(x, z) {
          // Door state is informational only: the mother-ship route no longer uses locked-door gating.
          // Geometry bounds still keep the player inside modeled rooms and corridors.
          return true;
        }

        bridgeViewscreenTrackingActive() {
          const state = String(this.shipState?.terminals?.["terminal.bridge-viewscreen"]?.state || "").toLowerCase();
          return state === "tracking" || state === "surveying" || Boolean(this.shipState?.flags?.bridgeViewscreenTrackingActive);
        }

        currentSystemPlanet() {
          return this.navigationSnapshot?.()?.currentPlanet || null;
        }

        openingEnemyEncounterActive(nowMs = this.lastFrameTime ?? performance.now()) {
          const navigation = this.navigationSnapshot?.(nowMs) || {};
          return Boolean(
            navigation.currentSystemId
            && navigation.currentSystemId === navigation.startSystemId
            && !navigation.travelling
            && !navigation.lastCompletedRouteId
            && !navigation.lastArrivalAtMs
            && Number(navigation.elapsedWorldTime || 0) === 0
          );
        }

        enemyShipHullPercent() {
          const value = Number(this.shipState?.flags?.enemyShipHullPercent);
          if (!Number.isFinite(value)) return 100;
          return Math.max(0, Math.min(100, value));
        }

        enemyShipDisabled() {
          return this.enemyShipHullPercent() <= 0 || Boolean(this.shipState?.flags?.enemyShipDisabled);
        }

        bridgeTacticalShotAgeMs(nowMs = this.lastFrameTime ?? performance.now()) {
          const firedAt = Number(this.shipState?.flags?.bridgeTacticalLastFireAtMs || 0);
          if (!firedAt) return Infinity;
          return Math.max(0, (Number.isFinite(nowMs) ? nowMs : performance.now()) - firedAt);
        }

        fireBridgeTacticalConsole() {
          if (!this.shipState) this.shipState = this.createShipState();
          const flags = this.shipState.flags || (this.shipState.flags = {});
          const nowMs = Math.round(this.lastFrameTime || performance.now() || 0);

          if (this.openingEnemyEncounterActive(nowMs)) {
            this.setShipTerminalState("terminal.bridge-viewscreen", "tracking");
            this.setShipTerminalState("terminal.bridge-tactical", "firing");
            flags.bridgeViewscreenTrackingActive = true;
            flags.enemyShipOnBridgeViewscreen = true;
            flags.bridgeTacticalArmed = true;
            flags.bridgeTacticalShotsFired = Math.max(0, Number(flags.bridgeTacticalShotsFired) || 0) + 1;
            flags.bridgeTacticalLastFireAtMs = nowMs;
            const currentHull = this.enemyShipHullPercent();
            if (currentHull <= 0 || flags.enemyShipDisabled) {
              flags.enemyShipHullPercent = 0;
              flags.enemyShipDisabled = true;
              this.setShipTerminalState("terminal.bridge-tactical", "target-destroyed");
              this.setShipObjective("objective.enemy-disabled", true);
              this.setShipInteractionStatus("Bridge tactical console reports the enemy raider has already been destroyed.");
              this.emitShipState(true);
              return true;
            }
            const nextHull = Math.max(0, currentHull - 50);
            flags.enemyShipHullPercent = nextHull;
            if (nextHull <= 0) {
              flags.enemyShipDisabled = true;
              this.setShipTerminalState("terminal.bridge-tactical", "target-destroyed");
              this.setShipObjective("objective.enemy-disabled", true);
              this.setShipInteractionStatus("Bridge tactical console fired. Direct hit. Enemy raider destroyed in an expanding fireball. Navigation is ready.");
            } else {
              flags.enemyShipDisabled = false;
              this.setShipObjective("objective.enemy-attack", true);
              this.setShipInteractionStatus(`Bridge tactical console fired. Direct hit. Enemy raider hull ${Math.round(nextHull)}%. Fire one more volley.`);
            }
            this.emitShipState(true);
            return true;
          }

          const navigation = this.navigationSnapshot?.(nowMs) || {};
          const planet = navigation.currentPlanet || {};
          this.setShipTerminalState("terminal.bridge-viewscreen", "tracking");
          this.setShipTerminalState("terminal.bridge-tactical", "scanning");
          flags.bridgeViewscreenTrackingActive = true;
          flags.enemyShipOnBridgeViewscreen = false;
          flags.currentSystemPlanetSurveyed = true;
          flags.lastSurveyedPlanetId = String(planet.id || navigation.currentPlanetId || "");
          flags.lastSurveyedSystemId = String(navigation.currentSystemId || "");
          flags.planetScansCompleted = Math.max(0, Number(flags.planetScansCompleted) || 0) + 1;
          flags.planetScanLastAtMs = nowMs;
          this.setShipObjective("objective.planet-surveyed", true);
          this.setShipInteractionStatus(
            `Planetary sensor sweep complete: ${String(planet.label || navigation.currentPlanetLabel || "current planet")} in ${String(navigation.currentSystemLabel || "current system")}. Navigation is ready.`
          );
          this.emitShipState(true);
          return true;
        }

        shipLocationForPosition(x, z) {
          const matches = this.motherShipWalkableRegions()
            .filter((region) => (
              x >= region.minX
              && x <= region.maxX
              && z >= region.minZ
              && z <= region.maxZ
            ))
            .sort((left, right) => (
              shuttle3dNumberValue(right.priority, 0) - shuttle3dNumberValue(left.priority, 0)
            ));
          if (matches.length) return matches[0].location || matches[0].id;
          return "corridor.main";
        }

        setShipObjective(objectiveId, force = false) {
          if (!this.shipState) this.shipState = this.createShipState();
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const nextObjective = String(objectiveId || config.initialObjective || "objective.bay-ops");
          if (!config.objectives?.[nextObjective]) return false;
          if (this.shipState.objectiveId === nextObjective && !force) return true;
          this.shipState.objectiveId = nextObjective;
          this.emitShipState(true);
          return true;
        }

        setShipDoorState(doorId, state) {
          if (!this.shipState) this.shipState = this.createShipState();
          const door = this.shipState.doors?.[doorId];
          if (!door) return false;
          const nextState = String(state || "open").toLowerCase();
          door.state = nextState === "locked" ? "open" : nextState;
          this.emitShipState(true);
          return true;
        }

        setShipTerminalState(terminalId, state) {
          if (!this.shipState) this.shipState = this.createShipState();
          const terminal = this.shipState.terminals?.[terminalId];
          if (!terminal) return false;
          terminal.state = String(state || "offline");
          this.emitShipState(true);
          return true;
        }

        setShipInteractionStatus(message) {
          if (!this.shipState) this.shipState = this.createShipState();
          this.shipState.lastInteractionStatus = String(message || "");
          this.emitShipState(true);
        }

        syncShipLocationFromCamera(force = false) {
          if (!this.isShuttleBaySceneActive() || !this.isShuttleBayPlayerControlActive()) return false;
          const nextLocation = this.shipLocationForPosition(this.camera[0], this.camera[2]);
          const changed = this.setShipLocation(nextLocation, force);
          if (nextLocation === "corridor.main") {
            this.setShipObjective("objective.restore-power");
          } else if (nextLocation === "medbay.stub" || nextLocation === "science.ops.stub") {
            this.setShipObjective("objective.survey-departments");
          } else if (nextLocation === "bridge.access") {
            this.setShipObjective("objective.bridge-access");
          } else if (nextLocation === "bridge.deck") {
            if (this.openingEnemyEncounterActive()) {
              this.setShipObjective(
                this.enemyShipDisabled()
                  ? "objective.enemy-disabled"
                  : this.bridgeViewscreenTrackingActive()
                    ? "objective.enemy-attack"
                    : "objective.bridge-screen"
              );
            } else {
              this.setShipObjective(
                Boolean(this.shipState?.flags?.currentSystemPlanetSurveyed)
                  ? "objective.planet-surveyed"
                  : this.bridgeViewscreenTrackingActive()
                    ? "objective.planet-scan"
                    : "objective.planet-view"
              );
            }
          }
          return changed;
        }

        enterBayOpsAccess() {
          if (!this.isShuttleBayPlayerControlActive()) return false;
          this.clearMovementKeys();
          this.camera = [2.55, 0.9, -5.78];
          this.setLook(28, -3);
          this.setShipLocation("bay.ops", true);
          this.setShipObjective("objective.bay-ops", true);
          this.setShipInteractionStatus("Entered the lit Bay Operations vestibule. The route ahead is open.");
          if (typeof this.onCameraMoved === "function") this.onCameraMoved(this.camera.slice());
          this.emitShipState(true);
          return true;
        }

        shipInteractionZones() {
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          // Patch D: prompts, ranges, and E-key action ids come from the interior definition.
          return Array.isArray(config.interactables) ? config.interactables : [];
        }

        shipInteractionTarget() {
          if (!this.isShuttleBaySceneActive() || !this.isShuttleBayPlayerControlActive()) return null;
          if (this.navigationConsoleOpen && this.navigationConsoleAccessTargetId === "terminal.bridge-navigation") {
            const navigationTarget = this.bridgeNavigationControlTarget();
            if (navigationTarget) return navigationTarget;
          }
          const activeLocation = this.shipLocationForPosition(this.camera[0], this.camera[2]);
          let nearest = null;
          let nearestDistance = Infinity;
          this.shipInteractionZones().forEach((zone) => {
            if (zone.location && zone.location !== activeLocation) return;
            const dx = this.camera[0] - zone.position[0];
            const dz = this.camera[2] - zone.position[1];
            const distance = Math.hypot(dx, dz);
            if (distance <= zone.range && distance < nearestDistance) {
              nearest = {...zone, distance};
              nearestDistance = distance;
            }
          });
          return nearest;
        }

        shipInteractionHint(target = this.shipInteractionTarget()) {
          if (!target) return "";
          if (target.id === "terminal.bridge-tactical") {
            if (this.openingEnemyEncounterActive()) {
              return this.enemyShipDisabled()
                ? "Enemy raider destroyed. Press E to review tactical status."
                : this.enemyShipHullPercent() <= 50
                  ? "Press E to fire the final volley at the enemy raider."
                  : "Press E to fire bridge weapons at the enemy raider.";
            }
            return "Press E to scan the current system planet.";
          }
          if (target.id === "terminal.bridge-viewscreen") {
            if (this.openingEnemyEncounterActive()) {
              return this.enemyShipDisabled()
                ? "Press E to inspect the enemy raider debris field."
                : "Press E to acquire the enemy raider on the bridge viewscreen.";
            }
            return "Press E to center the current system planet on the viewscreen.";
          }
          if (target.prompt) return String(target.prompt);
          if (target.kind === "access") return `Press E to enter through ${target.label}.`;
          if (target.kind === "terminal") return `Press E to use ${target.label}.`;
          return `Press E to inspect ${target.label}.`;
        }

        createShipInteractionHandlerMap() {
          return {
            enterBayOpsAccess: (target, interaction) => this.enterBayOpsAccess(target, interaction),
            activateBayOperationsTerminal: (target, interaction) => this.activateBayOperationsTerminal(target, interaction),
            restoreEngineeringPower: (target, interaction) => this.restoreEngineeringPower(target, interaction),
            trackEnemyShipOnViewscreen: (target, interaction) => this.trackEnemyShipOnViewscreen(target, interaction),
            fireBridgeTacticalConsole: (target, interaction) => this.fireBridgeTacticalConsole(target, interaction),
            openBridgeNavigationConsole: (target, interaction) => this.openBridgeNavigationConsole(target, interaction),
            inspectOpenDoorRoute: (target, interaction) => this.inspectOpenDoorRoute(target, interaction)
          };
        }

        createShipInteractionRegistry() {
          const config = this.interiorConfig || shuttle3dMotherShipInteriorConfig(this.scene);
          const definitions = config.interactions || {};
          const handlers = this.createShipInteractionHandlerMap();
          const registry = {};
          Object.entries(definitions).forEach(([actionId, definition]) => {
            const raw = definition && typeof definition === "object" ? definition : {};
            const id = String(raw.id || actionId).trim();
            if (!id) return;
            const handlerId = String(raw.handler || id).trim();
            registry[id] = {
              id,
              label: String(raw.label || id),
              handlerId,
              status: String(raw.status || ""),
              changesState: shuttle3dInteractionStringList(raw.changesState),
              successStatus: String(raw.successStatus || raw.status || ""),
              nextObjective: raw.nextObjective !== undefined ? shuttle3dCloneJson(raw.nextObjective) : "",
              emitsState: raw.emitsState !== false,
              handler: handlers[handlerId] || null
            };
          });
          Object.keys(handlers).forEach((handlerId) => {
            if (registry[handlerId]) return;
            registry[handlerId] = {
              id: handlerId,
              label: handlerId,
              handlerId,
              status: "",
              changesState: [],
              successStatus: "",
              nextObjective: "",
              emitsState: true,
              handler: handlers[handlerId]
            };
          });
          return registry;
        }

        shipInteractionDefinition(actionId) {
          const id = String(actionId || "").trim();
          if (!id) return null;
          if (!this.shipInteractionRegistry) this.shipInteractionRegistry = this.createShipInteractionRegistry();
          return this.shipInteractionRegistry[id] || null;
        }

        activateBayOperationsTerminal(target, interaction) {
          this.setShipTerminalState("terminal.bay-ops", "online");
          this.setShipDoorState("door.bay-inner", "open");
          this.setShipObjective("objective.enter-corridor");
          if (this.shipState?.flags) this.shipState.flags.bayOpsTerminalUsed = true;
          this.setShipInteractionStatus(interaction?.status || "Bay Operations online. Route to Security Checkpoint is available.");
          return true;
        }

        restoreEngineeringPower(target, interaction) {
          this.setShipTerminalState("terminal.engineering-power", "online");
          this.shipState.power = "online";
          this.shipState.security = "yellow-alert";
          this.setShipDoorState("door.bridge", "open");
          this.setShipObjective("objective.bridge-access");
          if (this.shipState?.flags) this.shipState.flags.engineeringPowerRestored = true;
          this.setShipInteractionStatus(interaction?.status || "Engineering restored main power. Bridge route confirmed open.");
          return true;
        }

        trackEnemyShipOnViewscreen(target, interaction) {
          const navigation = this.navigationSnapshot?.() || {};
          this.setShipTerminalState("terminal.bridge-viewscreen", "tracking");

          if (this.openingEnemyEncounterActive()) {
            this.setShipObjective(this.enemyShipDisabled() ? "objective.enemy-disabled" : "objective.enemy-attack", true);
            if (this.shipState?.flags) {
              this.shipState.flags.enemyShipOnBridgeViewscreen = true;
              this.shipState.flags.bridgeViewscreenTrackingActive = true;
              this.shipState.flags.bridgeViewscreenInteractedAtMs = Math.round(this.lastFrameTime || 0);
            }
            this.setShipInteractionStatus(
              this.enemyShipDisabled()
                ? "Enemy raider debris field centered on the main viewscreen."
                : "Bridge tactical lock engaged. Enemy raider is tracked on the main viewscreen. Use the Bridge Tactical Console / Sensor Array to fire."
            );
            this.emitShipState(true);
            return true;
          }

          const planet = navigation.currentPlanet || {};
          this.setShipObjective(
            this.shipState?.flags?.currentSystemPlanetSurveyed
              ? "objective.planet-surveyed"
              : "objective.planet-scan",
            true
          );
          if (this.shipState?.flags) {
            this.shipState.flags.enemyShipOnBridgeViewscreen = false;
            this.shipState.flags.bridgeViewscreenTrackingActive = true;
            this.shipState.flags.lastSurveyedPlanetId = String(planet.id || navigation.currentPlanetId || "");
            this.shipState.flags.lastSurveyedSystemId = String(navigation.currentSystemId || "");
            this.shipState.flags.bridgeViewscreenInteractedAtMs = Math.round(this.lastFrameTime || 0);
          }
          this.setShipInteractionStatus(
            `${String(planet.label || navigation.currentPlanetLabel || "Current planet")} centered on the main viewscreen. Use the Bridge Tactical Console / Sensor Array to complete the survey.`
          );
          this.emitShipState(true);
          return true;
        }

        inspectOpenDoorRoute(target) {
          if (this.shipDoorState(target.id) !== "open") this.setShipDoorState(target.id, "open");
          if (target.id === "door.bay-inner" || target.id === "door.security-hub") this.setShipObjective("objective.restore-power");
          if (target.id === "door.engineering-access" || target.id === "door.medbay" || target.id === "door.science") this.setShipObjective("objective.survey-departments");
          if (target.id === "door.bridge") this.setShipObjective("objective.bridge-screen");
          this.setShipInteractionStatus(`${target.label} route is open. No door lock is required.`);
          return true;
        }

        performShipInteractionAction(target) {
          const actionId = String(target?.action || target?.interaction || "").trim();
          const interaction = this.shipInteractionDefinition(actionId);
          if (!interaction?.handler) {
            if (actionId) this.setShipInteractionStatus(`No interaction handler registered for ${actionId}.`);
            return false;
          }
          const result = interaction.handler(target, interaction);
          if (result && interaction.emitsState !== false) this.emitShipState(true);
          return Boolean(result);
        }

        interactWithShip() {
          const target = this.shipInteractionTarget();
          if (!target) return false;
          return this.performShipInteractionAction(target);
        }

        enterShuttleBayPlayerControl(force = false) {
          const flight = this.flight;
          if (!flight) return false;
          if (!flight.playerExitedToBay && !force) return false;
          if (flight.bayPlayerControlActive && !force) return false;
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const spawn = this.shuttleBayPlayerSpawn();
          flight.docked = true;
          flight.playerExitedToBay = true;
          flight.bayPlayerControlActive = true;
          flight.dockingCutsceneActive = false;
          flight.dockingCutsceneComplete = true;
          flight.dockingCutsceneElapsedMs = Math.max(
            Number(flight.dockingCutsceneElapsedMs) || 0,
            config.cutscene?.durationMs || 9600
          );
          flight.dockingCutscenePhase = "arrived";
          flight.forwardSpeed = 0;
          flight.distance = config.dockingDistance;
          this.hoveredPilotStation = null;
          this.pilot.active = false;
          this.pilot.station = null;
          this.pilot.throttle = 0;
          this.pilot.impulse = 0;
          this.combatPauseStartedAtMs = null;
          const suppressedBayKeys = new Set(this.movementKeys);
          this.clearMovementKeys();
          this.bayControlSuppressedKeys = suppressedBayKeys;
          this.bayControlInputUnlockAtMs = (Number.isFinite(this.lastFrameTime) ? this.lastFrameTime : performance.now()) + 650;
          this.setShipLocation("bay.shuttle", true);
          if (this.shipState?.flags) {
            this.shipState.flags.bayControlActive = true;
            this.shipState.flags.boardersPausedAfterDocking = true;
          }
          this.camera = spawn.position.slice();
          this.setLook(spawn.yaw, spawn.pitch);
          if (typeof this.onCameraMoved === "function") this.onCameraMoved(this.camera.slice());
          if (typeof this.onBayControlStarted === "function") {
            try {
              this.onBayControlStarted({
                position: this.camera.slice(),
                yaw: spawn.yaw,
                pitch: spawn.pitch
              });
            } catch (error) {
              console.error("Shuttle bay control handoff callback failed", error);
            }
          }
          this.emitPilotState(true);
          this.emitCombatState(true);
          this.emitShipState(true);
          return true;
        }

        forceShuttleBayControl() {
          if (!this.flight) this.flight = this.createFlightState();
          return this.enterShuttleBayPlayerControl(true);
        }

        isDockingSceneActive() {
          return this.isDockingCutsceneActive() || this.isShuttleBaySceneActive();
        }

        hasActiveCharacterEnemy() {
          return this.visibleCharacterAICharacters()
            .some((character) => character.kind === "enemy" && character.health > 0);
        }

        isBoardingPaused() {
          return Boolean(
            this.pilot?.active
            || this.isDockingCutsceneActive()
            || this.isWarpTravelActive()
            || (this.isShuttleBaySceneActive() && !this.hasActiveCharacterEnemy())
          );
        }

        isWeaponFirePaused() {
          if (this.characterAIPhase?.() === "mother-ship") {
            return Boolean(
              this.isDockingCutsceneActive()
              || this.isWarpTravelActive()
            );
          }
          return this.isBoardingPaused();
        }

        dockingCutsceneSnapshot(nowMs = this.lastFrameTime ?? performance.now()) {
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const flight = this.flight || this.createFlightState();
          const duration = Math.max(1, config.cutscene?.durationMs || 9600);
          const elapsed = flight.dockingCutsceneActive
            ? Math.max(0, (Number.isFinite(nowMs) ? nowMs : performance.now()) - flight.dockingCutsceneStartedAtMs)
            : flight.dockingCutsceneElapsedMs;
          const progress = Math.max(0, Math.min(1, elapsed / duration));
          let phase = flight.dockingCutscenePhase || "approach";
          if (flight.playerExitedToBay) phase = "arrived";
          else if (progress >= 0.72) phase = "player-exit";
          else if (progress >= 0.42) phase = "bay-landing";
          else phase = "hangar-approach";
          return {
            active: Boolean(flight.dockingCutsceneActive),
            complete: Boolean(flight.dockingCutsceneComplete),
            playerExitedToBay: Boolean(flight.playerExitedToBay),
            progress,
            elapsed,
            duration,
            phase,
            bayLabel: config.cutscene?.bayLabel || "Mother Ship Shuttle Bay"
          };
        }

        startDockingCutscene(nowMs = performance.now()) {
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const flight = this.flight;
          if (!config.cutscene?.enabled || !flight || flight.dockingCutsceneActive || flight.dockingCutsceneComplete) return false;
          flight.docked = true;
          flight.forwardSpeed = 0;
          flight.distance = config.dockingDistance;
          flight.dockingCutsceneActive = true;
          flight.dockingCutsceneStartedAtMs = Number.isFinite(nowMs) ? nowMs : performance.now();
          flight.dockingCutsceneElapsedMs = 0;
          flight.dockingCutscenePhase = "hangar-approach";
          flight.dockingCutsceneComplete = false;
          flight.playerExitedToBay = false;
          flight.bayPlayerControlActive = false;
          this.pilot.throttle = 0;
          this.pilot.impulse = 0;
          this.clearMovementKeys();
          this.emitPilotState(true);
          this.emitCombatState(true);
          return true;
        }

        updateDockingCutscene(deltaSeconds, nowMs = this.lastFrameTime ?? performance.now()) {
          const flight = this.flight;
          if (!flight?.dockingCutsceneActive) return;
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const snapshot = this.dockingCutsceneSnapshot(nowMs);
          flight.dockingCutsceneElapsedMs = snapshot.elapsed;
          flight.dockingCutscenePhase = snapshot.phase;
          if (snapshot.progress >= 1) {
            flight.dockingCutsceneActive = false;
            flight.dockingCutsceneComplete = true;
            flight.playerExitedToBay = true;
            flight.dockingCutsceneElapsedMs = snapshot.duration;
            flight.dockingCutscenePhase = "arrived";
            flight.forwardSpeed = 0;
            flight.distance = config.dockingDistance;
            this.pilot.active = false;
            this.pilot.station = null;
            this.pilot.throttle = 0;
            this.pilot.impulse = 0;
            this.enterShuttleBayPlayerControl(true);
          }
          this.emitPilotState();
          this.emitCombatState();
        }

        dockingCutsceneCamera(nowMs = this.lastFrameTime ?? performance.now()) {
          const snapshot = this.dockingCutsceneSnapshot(nowMs);
          const p = snapshot.progress;
          const ease = (value) => value * value * (3 - 2 * value);
          if (snapshot.playerExitedToBay) {
            return {
              eye: [0, 1.55, 7.15],
              target: [0, 0.08, 1.15]
            };
          }
          const settle = ease(Math.max(0, Math.min(1, (p - 0.42) / 0.3)));
          const exit = ease(Math.max(0, Math.min(1, (p - 0.72) / 0.28)));
          return {
            eye: [0, 2.75 - settle * 0.88, 9.35 - exit * 1.42],
            target: [0, 0.1 + settle * 0.35, 0.85 + exit * 1.25]
          };
        }


        pilotSnapshot() {
          const station = this.pilot.station || this.hoveredPilotStation;
          const flight = this.flight || this.createFlightState();
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const totalApproach = Math.max(0.1, config.startDistance - config.dockingDistance);
          const progress = Math.max(0, Math.min(1, (config.startDistance - flight.distance) / totalApproach));
          const cutscene = this.dockingCutsceneSnapshot();
          return {
            enabled: this.pilotStations.length > 0,
            active: this.pilot.active,
            paused: this.isBoardingPaused(),
            stationId: this.pilot.station?.id || "",
            stationLabel: this.pilot.station?.label || "",
            hoverId: this.hoveredPilotStation?.id || "",
            hoverLabel: this.hoveredPilotStation?.label || "",
            role: station?.role || "",
            throttle: Number(this.pilot.throttle.toFixed(2)),
            heading: Number(this.pilot.heading.toFixed(1)),
            pitch: Number(this.pilot.pitch.toFixed(1)),
            impulse: Number(this.pilot.impulse.toFixed(2)),
            flightEnabled: Boolean(config.enabled),
            targetLabel: config.targetLabel,
            flightDistance: Number(flight.distance.toFixed(1)),
            flightSpeed: Number(flight.forwardSpeed.toFixed(1)),
            flightProgress: Number(progress.toFixed(3)),
            flightDocked: Boolean(flight.docked),
            dockingCutsceneActive: cutscene.active,
            dockingCutsceneComplete: cutscene.complete,
            dockingCutscenePhase: cutscene.phase,
            dockingCutsceneProgress: Number(cutscene.progress.toFixed(3)),
            playerExitedToBay: cutscene.playerExitedToBay,
            shuttleBayControlActive: this.isShuttleBayPlayerControlActive(),
            shuttleBayLabel: cutscene.bayLabel
          };
        }

        emitPilotState(force = false) {
          if (typeof this.onPilotChanged !== "function") return;
          const clock = this.lastFrameTime ?? 0;
          if (!force && clock - this.lastPilotUiAt < 80) return;
          this.lastPilotUiAt = clock;
          this.onPilotChanged(this.pilotSnapshot());
        }

        stationById(stationOrId) {
          if (!stationOrId) return null;
          if (typeof stationOrId === "object" && stationOrId.id) {
            return this.pilotStations.find((station) => station.id === stationOrId.id) || stationOrId;
          }
          const id = String(stationOrId);
          return this.pilotStations.find((station) => station.id === id || station.objectId === id) || null;
        }

        setHoveredPilotStation(stationOrId) {
          const station = this.stationById(stationOrId);
          if ((station?.id || "") === (this.hoveredPilotStation?.id || "")) return station;
          this.hoveredPilotStation = station;
          this.emitPilotState(true);
          return station;
        }

        pickPilotStation(clientX, clientY) {
          if (this.isDockingSceneActive()) return null;
          if (!this.pilotStations.length || this.disposed) return null;
          const rect = this.canvas.getBoundingClientRect?.();
          if (!rect || rect.width <= 0 || rect.height <= 0) return null;
          const x = ((clientX - rect.left) / rect.width) * 2 - 1;
          const y = 1 - ((clientY - rect.top) / rect.height) * 2;
          const {forward, right, up} = this.cameraBasis();
          const fov = 66 * Math.PI / 180;
          const scale = Math.tan(fov / 2);
          const ray = shuttle3dNormalizeVector([
            forward[0] + right[0] * x * scale * (this.aspect || rect.width / Math.max(1, rect.height)) + up[0] * y * scale,
            forward[1] + right[1] * x * scale * (this.aspect || rect.width / Math.max(1, rect.height)) + up[1] * y * scale,
            forward[2] + right[2] * x * scale * (this.aspect || rect.width / Math.max(1, rect.height)) + up[2] * y * scale
          ]);
          let best = null;
          let bestDistance = Infinity;
          this.pilotStations.forEach((station) => {
            const distance = shuttle3dRayIntersectsBounds(this.camera, ray, station.bounds);
            if (!Number.isFinite(distance) || distance > station.activationRange || distance >= bestDistance) return;
            best = station;
            bestDistance = distance;
          });
          return best;
        }

        setPilotMode(active, stationOrId = null, nowMs = performance.now()) {
          if (this.isDockingSceneActive()) return false;
          if (active) {
            const station = this.stationById(stationOrId) || this.hoveredPilotStation;
            if (!station || this.gameOver) return false;
            this.pilot.active = true;
            this.pilot.station = station;
            this.pilot.throttle = 0;
            this.pilot.impulse = 0;
            this.pilot.heading = station.camera.yaw;
            this.pilot.pitch = station.camera.pitch;
            this.pilot.roll = 0;
            this.combatPauseStartedAtMs = Number.isFinite(nowMs) ? nowMs : performance.now();
            this.clearMovementKeys();
            this.camera = station.camera.position.slice();
            this.camera[1] = station.camera.position[1];
            this.setLook(station.camera.yaw, station.camera.pitch);
            if (typeof this.onCameraMoved === "function") this.onCameraMoved(this.camera.slice());
            this.emitPilotState(true);
            this.emitCombatState(true);
            return true;
          }

          if (!this.pilot.active) return false;
          const station = this.pilot.station;
          const pausedAt = Number.isFinite(this.combatPauseStartedAtMs) ? this.combatPauseStartedAtMs : nowMs;
          const pauseDuration = Math.max(0, (Number.isFinite(nowMs) ? nowMs : performance.now()) - pausedAt);
          this.nextTransportAtMs += pauseDuration;
          this.aliens.forEach((alien) => {
            if (Number.isFinite(alien.transportUntilMs)) alien.transportUntilMs += pauseDuration;
            if (Number.isFinite(alien.nextAttackAtMs)) alien.nextAttackAtMs += pauseDuration;
            if (Number.isFinite(alien.hitFlashUntilMs)) alien.hitFlashUntilMs += pauseDuration;
          });
          if (Number.isFinite(this.lastPhaserShotAt)) this.lastPhaserShotAt += pauseDuration;
          if (this.phaserBeam && Number.isFinite(this.phaserBeam.expiresAtMs)) this.phaserBeam.expiresAtMs += pauseDuration;
          this.combatPauseStartedAtMs = null;
          this.pilot.active = false;
          this.pilot.station = null;
          this.pilot.throttle = 0;
          this.pilot.impulse = 0;
          this.pilot.roll = 0;
          this.clearMovementKeys();
          if (station?.exitPosition) {
            this.camera = station.exitPosition.slice();
            this.camera[1] = station.exitPosition[1];
            if (typeof this.onCameraMoved === "function") this.onCameraMoved(this.camera.slice());
          }
          this.emitPilotState(true);
          this.emitCombatState(true);
          return true;
        }

        updatePilot(deltaSeconds) {
          if (this.isDockingCutsceneActive()) {
            this.updateDockingCutscene(deltaSeconds);
            return;
          }
          if (this.isShuttleBaySceneActive()) {
            this.pilot.throttle = 0;
            this.pilot.impulse = 0;
            this.emitPilotState();
            return;
          }
          if (!this.pilot.active || deltaSeconds <= 0) return;
          let throttleInput = 0;
          let yawInput = 0;
          let pitchInput = 0;
          if (this.movementKeys.has("KeyW")) throttleInput += 1;
          if (this.movementKeys.has("KeyS")) throttleInput -= 1;
          if (this.movementKeys.has("KeyA") || this.movementKeys.has("ArrowLeft")) yawInput -= 1;
          if (this.movementKeys.has("KeyD") || this.movementKeys.has("ArrowRight")) yawInput += 1;
          if (this.movementKeys.has("ArrowUp")) pitchInput += 1;
          if (this.movementKeys.has("ArrowDown")) pitchInput -= 1;
          const throttleRate = this.movementKeys.has("ShiftLeft") || this.movementKeys.has("ShiftRight") ? 1.9 : 1.15;
          this.pilot.throttle = Math.max(-0.35, Math.min(1, this.pilot.throttle + throttleInput * throttleRate * Math.min(0.08, deltaSeconds)));
          if (!throttleInput) this.pilot.throttle *= Math.max(0, 1 - deltaSeconds * 0.35);
          this.pilot.heading = normalizeShuttle3dYaw(this.pilot.heading + yawInput * 42 * Math.min(0.08, deltaSeconds));
          this.pilot.pitch = Math.max(-18, Math.min(18, this.pilot.pitch + pitchInput * 24 * Math.min(0.08, deltaSeconds)));
          this.pilot.roll = Math.max(-16, Math.min(16, yawInput * -10 + this.pilot.roll * 0.86));
          this.updatePilotFlight(deltaSeconds, yawInput, pitchInput);
          this.pilot.impulse = Math.max(0, Math.min(1, Math.abs(this.pilot.throttle)));
          this.emitPilotState();
        }

        updatePilotFlight(deltaSeconds, yawInput = 0, pitchInput = 0) {
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const flight = this.flight;
          if (!config.enabled || !flight || deltaSeconds <= 0) return;
          if (flight.docked) {
            flight.forwardSpeed = 0;
            if (flight.dockingCutsceneActive || flight.playerExitedToBay) {
              this.updateDockingCutscene(deltaSeconds);
              return;
            }
            if (this.pilot.throttle < -0.05 && !flight.dockingCutsceneComplete) {
              flight.docked = false;
            } else {
              this.pilot.throttle = 0;
              this.pilot.impulse = 0;
              flight.distance = config.dockingDistance;
              if (!flight.dockingCutsceneComplete) this.startDockingCutscene(this.lastFrameTime ?? performance.now());
              return;
            }
          }

          const desiredSpeed = this.pilot.throttle >= 0
            ? this.pilot.throttle * config.maxForwardSpeed
            : this.pilot.throttle * config.maxReverseSpeed;
          const velocityBlend = Math.min(1, config.acceleration * deltaSeconds);
          flight.forwardSpeed += (desiredSpeed - flight.forwardSpeed) * velocityBlend;
          flight.distance = Math.max(
            config.dockingDistance,
            Math.min(config.startDistance, flight.distance - flight.forwardSpeed * deltaSeconds)
          );

          flight.lateralOffset = Math.max(
            -config.lateralLimit,
            Math.min(config.lateralLimit, flight.lateralOffset + yawInput * config.lateralSpeed * deltaSeconds)
          );
          flight.verticalOffset = Math.max(
            -config.verticalLimit,
            Math.min(config.verticalLimit, flight.verticalOffset + pitchInput * config.verticalSpeed * deltaSeconds)
          );
          const settle = Math.max(0, 1 - deltaSeconds * 0.35);
          if (!yawInput) flight.lateralOffset *= settle;
          if (!pitchInput) flight.verticalOffset *= settle;

          if (flight.distance <= config.dockingDistance + 0.02 && flight.forwardSpeed >= -0.05) {
            flight.distance = config.dockingDistance;
            flight.forwardSpeed = 0;
            flight.docked = true;
            this.pilot.throttle = 0;
            this.startDockingCutscene(this.lastFrameTime ?? performance.now());
          }
        }


        appendCutsceneShuttle(builder, center, scale = 1, rampProgress = 0) {
          const hull = builder.color("#d8e2ea");
          const trim = builder.color("#334155");
          const glass = builder.color("#38bdf8", true);
          const impulse = builder.color("#67e8f9", true);
          const ramp = builder.color("#94a3b8");
          const [x, y, z] = center;
          const sx = scale;
          const box = (min, max, color) => builder.box(
            [x + min[0] * sx, y + min[1] * sx, z + min[2] * sx],
            [x + max[0] * sx, y + max[1] * sx, z + max[2] * sx],
            color
          );
          builder.ellipsoid([x, y + 0.2 * sx, z], [1.2 * sx, 0.38 * sx, 1.65 * sx], 16, 8, hull);
          box([-1.05, -0.18, -1.34], [1.05, 0.18, -0.92], trim);
          box([-0.52, 0.32, -0.72], [0.52, 0.58, -0.18], glass);
          box([-1.12, -0.18, 0.58], [-0.82, 0.14, 1.3], trim);
          box([0.82, -0.18, 0.58], [1.12, 0.14, 1.3], trim);
          box([-0.18, -0.04, 1.42], [0.18, 0.18, 1.72], impulse);
          if (rampProgress > 0.01) {
            const length = 0.72 + rampProgress * 0.9;
            box([-0.42, -0.34 - rampProgress * 0.2, 1.45], [0.42, -0.24, 1.45 + length], ramp);
            builder.beam([x - 0.42 * sx, y - 0.18 * sx, z + 1.36 * sx], [x - 0.42 * sx, y - (0.34 + rampProgress * 0.2) * sx, z + (1.45 + length) * sx], 0.018 * sx, glass);
            builder.beam([x + 0.42 * sx, y - 0.18 * sx, z + 1.36 * sx], [x + 0.42 * sx, y - (0.34 + rampProgress * 0.2) * sx, z + (1.45 + length) * sx], 0.018 * sx, glass);
          }
        }

        appendCutsceneCadet(builder, center, stride = 0) {
          const suit = builder.color("#1e3a8a");
          const suitLight = builder.color("#60a5fa", true);
          const helmet = builder.color("#dbeafe");
          const visor = builder.color("#38bdf8", true);
          const [x, y, z] = center;
          const legSwing = Math.sin(stride * Math.PI * 2) * 0.11;
          builder.ellipsoid([x, y + 0.58, z], [0.19, 0.34, 0.15], 10, 6, suit);
          builder.ellipsoid([x, y + 0.98, z], [0.18, 0.18, 0.16], 10, 6, helmet);
          builder.box([x - 0.1, y + 0.94, z - 0.17], [x + 0.1, y + 1.02, z - 0.13], visor);
          builder.beam([x - 0.14, y + 0.36, z], [x - 0.34, y + 0.13, z + legSwing], 0.055, suit);
          builder.beam([x + 0.14, y + 0.36, z], [x + 0.34, y + 0.13, z - legSwing], 0.055, suit);
          builder.beam([x - 0.2, y + 0.72, z], [x - 0.38, y + 0.5, z - legSwing], 0.045, suit);
          builder.beam([x + 0.2, y + 0.72, z], [x + 0.38, y + 0.5, z + legSwing], 0.045, suit);
          builder.box([x - 0.08, y + 0.55, z - 0.17], [x + 0.08, y + 0.66, z - 0.13], suitLight);
        }

        appendDockingCutscene(builder, nowMs = 0) {
          const snapshot = this.dockingCutsceneSnapshot(nowMs);
          const progress = snapshot.progress;
          const ease = (value) => value * value * (3 - 2 * value);
          const approach = ease(Math.max(0, Math.min(1, progress / 0.42)));
          const land = ease(Math.max(0, Math.min(1, (progress - 0.38) / 0.24)));
          const ramp = ease(Math.max(0, Math.min(1, (progress - 0.58) / 0.18)));
          const exit = ease(Math.max(0, Math.min(1, (progress - 0.66) / 0.3)));
          const deck = builder.color("#1e293b");
          const deckDark = builder.color("#0f172a");
          const wall = builder.color("#334155");
          const rail = builder.color("#64748b");
          const light = builder.color("#67e8f9", true);
          const green = builder.color("#86efac", true);
          const amber = builder.color("#fbbf24", true);
          const door = builder.color("#475569");
          const openAmount = ease(Math.max(0, Math.min(1, progress / 0.18)));
          const bayZ = 0.25;
          builder.box([-5.2, -1.2, -5.25], [5.2, -1.12, 6.2], deck);
          builder.box([-5.35, -1.2, -5.25], [-5.05, 2.85, 6.2], wall);
          builder.box([5.05, -1.2, -5.25], [5.35, 2.85, 6.2], wall);
          builder.box([-5.35, 2.72, -5.25], [5.35, 3.02, 6.2], deckDark);
          builder.box([-5.2, -1.18, -5.25], [5.2, 2.85, -4.95], wall);
          builder.box([-5.25, -1.05, 5.55], [-1.65 - openAmount * 1.1, 2.6, 5.92], door);
          builder.box([1.65 + openAmount * 1.1, -1.05, 5.55], [5.25, 2.6, 5.92], door);
          builder.box([-1.65, 2.35, 5.55], [1.65, 2.6, 5.92], door);
          builder.box([-4.45, -1.08, -2.95], [4.45, -1.0, -2.68], rail);
          builder.box([-4.45, -1.08, 2.92], [4.45, -1.0, 3.18], rail);
          [-3.7, -1.85, 0, 1.85, 3.7].forEach((x) => {
            builder.beam([x, 2.56, -4.75], [x, 2.56, 5.28], 0.018, light);
            builder.box([x - 0.16, 2.5, -4.86], [x + 0.16, 2.66, -4.64], light);
            builder.box([x - 0.16, 2.5, 4.92], [x + 0.16, 2.66, 5.14], light);
          });
          builder.beam([-4.65, 0.18, -2.82], [-1.32, 0.18, -0.42], 0.026, green);
          builder.beam([4.65, 0.18, -2.82], [1.32, 0.18, -0.42], 0.026, green);
          builder.beam([-4.65, 0.18, 3.08], [-1.32, 0.18, 0.88], 0.026, green);
          builder.beam([4.65, 0.18, 3.08], [1.32, 0.18, 0.88], 0.026, green);
          const shuttleZ = 5.75 - approach * 4.85 - land * 0.45;
          const shuttleY = 0.72 - land * 0.58;
          const shuttleScale = 0.82 + approach * 0.12;
          this.appendCutsceneShuttle(builder, [0, shuttleY, shuttleZ], shuttleScale, ramp);
          if (progress < 0.48) {
            builder.beam([-1.7, 0.8, 5.92], [-0.75, shuttleY + 0.26, shuttleZ + 0.78], 0.026, amber);
            builder.beam([1.7, 0.8, 5.92], [0.75, shuttleY + 0.26, shuttleZ + 0.78], 0.026, amber);
          }
          if ((exit > 0.01 || snapshot.playerExitedToBay) && !this.isShuttleBayPlayerControlActive()) {
            const walkZ = shuttleZ + 1.35 + exit * 2.15;
            const walkX = -0.16 + exit * 0.38;
            this.appendCutsceneCadet(builder, [walkX, -1.06, walkZ], progress * 9);
          }
          if (snapshot.playerExitedToBay) {
            builder.box([-1.85, -1.08, 3.62], [1.85, -0.92, 3.84], green);
            builder.beam([-1.9, 0.05, 3.72], [1.9, 0.05, 3.72], 0.032, green);
          }
        }


        appendMotherShipRoomGeometry(builder, nowMs = 0) {
          return globalThis.MainComputerShuttle3DRendererModules?.call(
            "roomGeometry",
            "appendMotherShipRoomGeometry",
            this,
            builder,
            nowMs
          );
        }


        appendShuttleBayScene(builder, nowMs = 0) {
          const deck = builder.color("#1e293b");
          const bulkhead = builder.color("#475569");
          const rail = builder.color("#64748b");
          const light = builder.color("#67e8f9", true);
          const green = builder.color("#86efac", true);
          const amber = builder.color("#fbbf24", true);
          const blue = builder.color("#38bdf8", true);
          const med = builder.color("#fca5a5", true);
          const sci = builder.color("#a78bfa", true);
          const terminal = builder.color("#0f766e");
          const pulse = 0.55 + 0.45 * Math.sin((nowMs || 0) / 320);

          const terminalBlock = (terminalId, centerX, centerZ, color = blue) => {
            const state = String(this.shipState?.terminals?.[terminalId]?.state || "").toLowerCase();
            const online = state === "online" || state === "tracking";
            const glow = online ? green : color;
            builder.consoleWedge(centerX, centerZ, 1.0, 0.72, -1.08, -0.36, 0.18, terminal);
            builder.box([centerX - 0.36, 0.16, centerZ - 0.32], [centerX + 0.36, 0.5, centerZ + 0.1], glow);
            builder.beam([centerX - 0.48, 0.62, centerZ - 0.42], [centerX + 0.48, 0.62, centerZ - 0.42], 0.02 + pulse * 0.01, glow);
          };
          this.appendMotherShipRoomGeometry(builder, nowMs);

          // Mother Ship Shuttle Bay
          builder.box([2.18, -1.06, -5.36], [4.42, -0.93, -4.72], blue);
          builder.beam([2.24, -0.78, -5.28], [4.36, -0.78, -5.28], 0.03, green);
          builder.beam([2.24, 1.42, -5.2], [4.36, 1.42, -5.2], 0.026, light);
          builder.box([2.52, -1.055, -4.42], [3.12, -0.94, -3.82], green);
          builder.box([3.24, -1.055, -4.78], [3.84, -0.94, -4.18], green);
          builder.beam([2.68, 0.28, -4.16], [3.86, 0.28, -4.74], 0.022, green);
          builder.box([-4.45, -1.08, -2.95], [4.45, -1.0, -2.68], rail);
          builder.box([-4.45, -1.08, 2.92], [4.45, -1.0, 3.18], rail);
          [-3.7, -1.85, 0, 1.85, 3.7].forEach((x) => {
            builder.beam([x, 2.56, -4.75], [x, 2.56, 5.28], 0.018, light);
            builder.box([x - 0.16, 2.5, -4.86], [x + 0.16, 2.66, -4.64], light);
            builder.box([x - 0.16, 2.5, 4.92], [x + 0.16, 2.66, 5.14], light);
          });
          builder.beam([-4.65, 0.18, -2.82], [-1.32, 0.18, -0.42], 0.026, green);
          builder.beam([4.65, 0.18, -2.82], [1.32, 0.18, -0.42], 0.026, green);
          builder.beam([-4.65, 0.18, 3.08], [-1.32, 0.18, 0.88], 0.026, green);
          builder.beam([4.65, 0.18, 3.08], [1.32, 0.18, 0.88], 0.026, green);
          this.appendCutsceneShuttle(builder, [0, 0.14, 0.45], 0.94, 1);
          builder.box([-1.85, -1.08, 3.62], [1.85, -0.92, 3.84], green);
          builder.beam([-1.9, 0.05, 3.72], [1.9, 0.05, 3.72], 0.032, green);
          builder.box([-0.78, -1.06, 4.86], [0.78, -0.94, 5.08], green);
          builder.beam([-0.72, 0.02, 4.96], [0.72, 0.02, 4.96], 0.024, light);

          // Bay Operations on the shipside/right side of the bay.
          // Keep the access vestibule, visible corridor, and walkable bounds aligned so players never step into an unrendered void.
          // Segment the forward Bay Ops bulkhead so the central transit spine is a real visible opening.
          builder.box([2.12, -1.055, -6.95], [4.45, -0.96, -5.15], builder.color("#1d4ed8"));
          builder.box([-1.9, -1.055, -9.12], [1.12, -0.96, -5.05], deck);
          builder.box([-0.72, -1.052, -9.38], [0.72, -0.93, -9.2], green);
          builder.box([-0.72, -1.052, -8.98], [0.72, -0.93, -8.8], green);
          builder.beam([2.34, -0.72, -5.42], [4.28, -0.72, -6.82], 0.024, blue);
          builder.beam([4.28, -0.72, -5.42], [2.34, -0.72, -6.82], 0.024, blue);
          builder.beam([-1.55, -0.76, -5.25], [0.0, -0.76, -8.75], 0.024, blue);
          builder.beam([1.55, -0.76, -5.25], [0.0, -0.76, -8.75], 0.024, blue);
          builder.beam([2.14, 0.12, -4.58], [4.42, 0.12, -4.58], 0.03, green);
          terminalBlock("terminal.bay-ops", 3.86, -6.42, blue);
          builder.beam([2.0, 0.1, -5.15], [4.55, 0.1, -5.15], 0.024, blue);
          // Security checkpoint and inner bay door.
          builder.box([-2.62, -1.05, -12.3], [-1.48, 0.2, -11.7], bulkhead);
          builder.box([1.48, -1.05, -12.3], [2.62, 0.2, -11.7], bulkhead);

          // Main corridor hub.

          // Engineering access.
          terminalBlock("terminal.engineering-power", 7.35, -20.75, green);
          builder.ellipsoid([5.4, 0.05, -21.1], [0.74, 1.15, 0.74], 14, 8, builder.color("#115e59"));
          builder.beam([3.0, 0.3, -18.3], [8.8, 0.3, -23.0], 0.02, green);
          // Medbay triage.
          builder.box([-8.65, -1.04, -22.9], [-6.65, -0.62, -21.7], builder.color("#e2e8f0"));
          builder.box([-5.7, -1.04, -22.9], [-3.7, -0.62, -21.7], builder.color("#e2e8f0"));
          builder.beam([-8.45, -0.34, -22.28], [-6.85, -0.34, -22.28], 0.035, med);
          builder.beam([-5.5, -0.34, -22.28], [-3.9, -0.34, -22.28], 0.035, med);
          // Science/Ops lab.
          builder.consoleWedge(-7.8, -28.6, 1.3, 0.82, -1.08, -0.28, 0.18, builder.color("#312e81"));
          builder.consoleWedge(-4.8, -28.6, 1.3, 0.82, -1.08, -0.28, 0.18, builder.color("#312e81"));
          builder.ellipsoid([-6.28, 0.45, -26.25], [0.75, 0.48, 0.75], 14, 8, sci);
          builder.beam([-8.45, 0.62, -28.95], [-3.55, 0.62, -28.95], 0.025, sci);
          // Bridge command door, command vestibule, and bridge deck.
          // Leave the forward bridge throat open so the vestibule visibly connects to the bridge deck.
          builder.consoleWedge(0, -29.25, 1.8, 0.9, -1.08, -0.32, 0.26, builder.color("#1e3a8a"));
          builder.beam([-1.2, 0.62, -29.68], [1.2, 0.62, -29.68], 0.028, green);
          builder.box([-0.78, -1.052, -31.98], [0.78, -0.93, -31.65], green);
          builder.beam([-1.04, 0.1, -31.88], [1.04, 0.1, -31.88], 0.024, light);
          // Bridge deck with a context-sensitive viewscreen: opening raider, warp transit, then destination planets.
          builder.beam([-1.08, 0.35, -31.48], [1.08, 0.35, -31.48], 0.026, green);
          builder.box([-2.35, -1.06, -34.15], [-1.22, -0.5, -33.3], builder.color("#1e3a8a"));
          builder.box([1.22, -1.06, -34.15], [2.35, -0.5, -33.3], builder.color("#1e3a8a"));
          builder.consoleWedge(-2.85, -36.7, 1.25, 0.82, -1.08, -0.32, 0.22, builder.color("#0f766e"));
          // Starboard console fires tactical weapons during the opening encounter and scans planets after warp.
          const tacticalConsoleGlow = builder.color(
            this.openingEnemyEncounterActive(nowMs)
              ? (this.enemyShipDisabled() ? "#86efac" : this.enemyShipHullPercent() <= 50 ? "#f97316" : "#ef4444")
              : (this.shipState?.flags?.currentSystemPlanetSurveyed ? "#86efac" : "#22d3ee"),
            true
          );
          builder.consoleWedge(2.85, -36.7, 1.25, 0.82, -1.08, -0.32, 0.22, builder.color("#164e63"));
          builder.beam([2.24, -0.48, -36.98], [3.46, -0.48, -36.98], 0.026, tacticalConsoleGlow);
          builder.box([2.54, -0.7, -36.92], [3.16, -0.62, -36.66], tacticalConsoleGlow);
          builder.consoleWedge(0, -35.1, 1.65, 0.9, -1.08, -0.32, 0.24, builder.color("#1e3a8a"));
          builder.ellipsoid([0, -0.55, -36.22], [0.42, 0.38, 0.42], 12, 6, builder.color("#475569"));
          builder.box([-0.36, -1.05, -35.9], [0.36, -0.58, -35.48], builder.color("#64748b"));
          builder.beam([-3.95, -0.78, -33.2], [-1.1, -0.78, -37.2], 0.024, blue);
          builder.beam([3.95, -0.78, -33.2], [1.1, -0.78, -37.2], 0.024, blue);
          this.appendMotherShipRoomVisuals(builder, nowMs);
          this.appendMotherShipInteriorProps(builder, nowMs);
          this.appendMotherShipInteractableHotspots(builder, nowMs);
        }

        appendMotherShipRoomVisuals(builder, nowMs = 0) {
          // Patch N renders room boundary/wayfinding affordances from room visual metadata.
          // See pretty_docs/game-runtime-patch-N-room-visual-metadata.md for the content contract.
          // The pass is intentionally additive so it can validate room data without changing collision.
          const rooms = Array.isArray(this.interiorConfig?.rooms) ? this.interiorConfig.rooms : [];
          if (!rooms.length) return;
          const pulse = 0.42 + 0.58 * Math.sin((nowMs || 0) / 520);
          rooms.forEach((room) => {
            const bounds = room?.bounds || {};
            const minX = Number(bounds.minX);
            const maxX = Number(bounds.maxX);
            const minZ = Number(bounds.minZ);
            const maxZ = Number(bounds.maxZ);
            if (![minX, maxX, minZ, maxZ].every(Number.isFinite) || maxX <= minX || maxZ <= minZ) return;
            const visual = room.visual || shuttle3dRoomVisualDefaults(room.kind);
            const edge = builder.color(visual.edgeColor || visual.color || "#64748b", true);
            const fill = builder.color(visual.color || "#38bdf8", true);
            const labelColor = builder.color(visual.labelColor || visual.color || "#e2e8f0", true);
            const centerX = (minX + maxX) / 2;
            const centerZ = (minZ + maxZ) / 2;
            const width = maxX - minX;
            const depth = maxZ - minZ;
            const y = -0.885;
            const beamRadius = 0.007 + pulse * 0.004;

            if (visual.boundary !== false) {
              builder.beam([minX, y, minZ], [maxX, y, minZ], beamRadius, edge);
              builder.beam([maxX, y, minZ], [maxX, y, maxZ], beamRadius, edge);
              builder.beam([maxX, y, maxZ], [minX, y, maxZ], beamRadius, edge);
              builder.beam([minX, y, maxZ], [minX, y, minZ], beamRadius, edge);
            }
            if (visual.floorBand !== false) {
              const longAxisIsZ = depth >= width;
              if (longAxisIsZ) {
                const bandWidth = Math.min(0.32, Math.max(0.12, width * 0.08));
                builder.box(
                  [centerX - bandWidth / 2, -1.051, minZ + Math.min(0.32, depth * 0.08)],
                  [centerX + bandWidth / 2, -0.985, maxZ - Math.min(0.32, depth * 0.08)],
                  fill
                );
              } else {
                const bandDepth = Math.min(0.32, Math.max(0.12, depth * 0.08));
                builder.box(
                  [minX + Math.min(0.32, width * 0.08), -1.051, centerZ - bandDepth / 2],
                  [maxX - Math.min(0.32, width * 0.08), -0.985, centerZ + bandDepth / 2],
                  fill
                );
              }
            }
            if (visual.label !== false) {
              const labelHeight = Math.max(0.08, Math.min(1.2, Number(visual.labelHeight || 0.42)));
              const plateWidth = Math.max(0.55, Math.min(1.75, width * 0.28));
              const z = maxZ - 0.16;
              builder.box([centerX - plateWidth / 2, 0.06, z - 0.035], [centerX + plateWidth / 2, 0.06 + labelHeight, z + 0.035], labelColor);
              builder.beam([centerX - plateWidth / 2, 0.14 + labelHeight, z], [centerX + plateWidth / 2, 0.14 + labelHeight, z], 0.009 + pulse * 0.004, labelColor);
            }
          });
        }


        appendMotherShipInteriorProps(builder, nowMs = 0) {
          // Patch H: render data-defined ship content from motherShipInterior.props.
          // This is the first content-first render pass for future room expansion.
          const props = Array.isArray(this.interiorConfig?.props) ? this.interiorConfig.props : [];
          if (!props.length) return;
          const pulse = 0.48 + 0.52 * Math.sin((nowMs || 0) / 360);
          const materialFor = (prop, fallback = "#38bdf8", forceGlow = false) => builder.color(
            prop.color || fallback,
            forceGlow || prop.emissive === true
          );
          const drawFloorMarker = (prop) => {
            const [x, z] = prop.position;
            const [width, depth] = prop.size;
            const color = materialFor(prop, "#38bdf8", prop.emissive);
            builder.box([x - width / 2, -1.052, z - depth / 2], [x + width / 2, -0.935, z + depth / 2], color);
            if (prop.emissive) builder.beam([x - width / 2, -0.76, z], [x + width / 2, -0.76, z], 0.014 + pulse * 0.008, color);
          };
          const drawMapMarker = (prop) => {
            const [x, z] = prop.position;
            const width = Math.max(0.08, prop.size?.[0] || 0.36);
            const height = Math.max(0.12, prop.size?.[1] || 0.66);
            const color = materialFor(prop, "#38bdf8", true);
            builder.box([x - width / 2, -1.055, z - width / 2], [x + width / 2, -0.94, z + width / 2], color);
            builder.beam([x, -0.82, z], [x, -0.82 + height, z], 0.018 + pulse * 0.006, color);
          };
          const drawSign = (prop) => {
            const [x, z] = prop.position;
            const [width, height] = prop.size;
            const color = materialFor(prop, "#38bdf8", true);
            const facing = String(prop.facing || "north").toLowerCase();
            const y0 = 1.14;
            const y1 = y0 + height;
            if (facing === "east" || facing === "west") {
              const x0 = x + (facing === "east" ? -0.035 : 0.035);
              builder.box([x0 - 0.035, y0, z - width / 2], [x0 + 0.035, y1, z + width / 2], color);
              builder.beam([x0, y1 + 0.08, z - width / 2], [x0, y1 + 0.08, z + width / 2], 0.012 + pulse * 0.006, color);
            } else {
              const z0 = z + (facing === "south" ? -0.035 : 0.035);
              builder.box([x - width / 2, y0, z0 - 0.035], [x + width / 2, y1, z0 + 0.035], color);
              builder.beam([x - width / 2, y1 + 0.08, z0], [x + width / 2, y1 + 0.08, z0], 0.012 + pulse * 0.006, color);
            }
          };
          const drawBeacon = (prop) => {
            const [x, z] = prop.position;
            const [width, height] = prop.size;
            const color = materialFor(prop, "#86efac", true);
            builder.box([x - width / 2, -1.045, z - width / 2], [x + width / 2, -0.9, z + width / 2], color);
            builder.beam([x, -0.82, z], [x, -0.82 + height, z], 0.02 + pulse * 0.014, color);
          };
          const drawLightStrip = (prop) => {
            const [x, z] = prop.position;
            const [length] = prop.size;
            const color = materialFor(prop, "#38bdf8", true);
            const y = 2.44;
            if (String(prop.axis || "x").toLowerCase() === "z") {
              builder.beam([x, y, z - length / 2], [x, y, z + length / 2], 0.018 + pulse * 0.004, color);
            } else {
              builder.beam([x - length / 2, y, z], [x + length / 2, y, z], 0.018 + pulse * 0.004, color);
            }
          };
          const drawStatusPanel = (prop) => {
            const [x, z] = prop.position;
            const [width, height] = prop.size;
            const target = String(prop.target || "");
            let color;
            if (target === "currentSystemPlanet") {
              const planet = this.navigationSnapshot?.(nowMs)?.currentPlanet || {};
              const surveyComplete = Boolean(this.shipState?.flags?.currentSystemPlanetSurveyed);
              color = builder.color(
                surveyComplete ? "#86efac" : (planet.atmosphereColor || prop.color || "#38bdf8"),
                true
              );
            } else {
              const hull = this.enemyShipHullPercent();
              const disabled = this.enemyShipDisabled();
              color = builder.color(disabled ? "#86efac" : hull < 45 ? "#f97316" : (prop.color || "#38bdf8"), true);
            }
            builder.box([x - width / 2, 0.28, z - 0.04], [x + width / 2, 0.28 + height, z + 0.04], color);
            builder.beam([x - width / 2, 0.28 + height + 0.1, z], [x + width / 2, 0.28 + height + 0.1, z], 0.014 + pulse * 0.006, color);
          };
          const drawTerminalConsole = (prop) => {
            // Patch M renders visible terminal console bodies from data-defined props.
            // See pretty_docs/game-runtime-patch-M-terminal-console-props.md for authoring intent.
            const [x, z] = prop.position;
            const width = Math.max(0.24, prop.size?.[0] || 0.82);
            const depth = Math.max(0.18, prop.size?.[1] || 0.48);
            const height = Math.max(0.28, prop.size?.[2] || 0.72);
            const facing = String(prop.facing || "north").toLowerCase();
            const body = builder.color("#0f172a");
            const trim = materialFor(prop, "#38bdf8", true);
            const activeTarget = this.shipInteractionTarget();
            const active = activeTarget && String(activeTarget.id || "") === String(prop.target || "");
            const activeTrim = active ? builder.color("#fef3c7", true) : trim;
            builder.box([x - width / 2, -1.05, z - depth / 2], [x + width / 2, -0.66, z + depth / 2], body);
            builder.box([x - width * 0.42, -0.64, z - depth * 0.42], [x + width * 0.42, -0.54, z + depth * 0.42], activeTrim);
            if (facing === "east" || facing === "west") {
              const sx = x + (facing === "east" ? width / 2 + 0.035 : -width / 2 - 0.035);
              builder.box([sx - 0.035, -0.48, z - width * 0.34], [sx + 0.035, -0.48 + height, z + width * 0.34], activeTrim);
              builder.beam([sx, -0.36 + height, z - width * 0.34], [sx, -0.36 + height, z + width * 0.34], 0.012 + pulse * 0.006, activeTrim);
            } else {
              const sz = z + (facing === "south" ? -depth / 2 - 0.035 : depth / 2 + 0.035);
              builder.box([x - width * 0.34, -0.48, sz - 0.035], [x + width * 0.34, -0.48 + height, sz + 0.035], activeTrim);
              builder.beam([x - width * 0.34, -0.36 + height, sz], [x + width * 0.34, -0.36 + height, sz], 0.012 + pulse * 0.006, activeTrim);
            }
            if (active) builder.beam([x - width / 2, -0.5, z], [x + width / 2, -0.5, z], 0.02 + pulse * 0.01, activeTrim);
          };
          const drawViewscreen = (prop) => {
            // Patch P renders content-defined viewscreens/displays from prop.display metadata.
            // See pretty_docs/game-runtime-patch-P-content-defined-viewscreens.md for the content contract.
            this.appendMotherShipViewscreenDisplay(builder, prop, nowMs);
          };
          props.forEach((prop) => {
            const kind = String(prop.kind || "").toLowerCase();
            if (kind === "sign") drawSign(prop);
            else if (kind === "beacon") drawBeacon(prop);
            else if (kind === "light-strip") drawLightStrip(prop);
            else if (kind === "status-panel") drawStatusPanel(prop);
            else if (kind === "viewscreen") drawViewscreen(prop);
            else if (kind === "terminal-console") drawTerminalConsole(prop);
            else if (kind === "map-marker") drawMapMarker(prop);
            else drawFloorMarker(prop);
          });
        }


        appendMotherShipInteractableHotspots(builder, nowMs = 0) {
          // Patch K renders E-key affordances from motherShipInterior.interactables.
          // Patch L lets content style those hotspots with normalized interactable visual metadata.
          // If a prompt exists, the player should also see a matching in-world hotspot.
          const interactables = Array.isArray(this.interiorConfig?.interactables) ? this.interiorConfig.interactables : [];
          if (!interactables.length) return;
          const activeTarget = this.shipInteractionTarget();
          const activeId = activeTarget?.id || "";
          const pulse = 0.46 + 0.54 * Math.sin((nowMs || 0) / 260);
          const hotspotVisual = (target) => target.visual || shuttle3dInteractableVisualDefaults(target.kind);
          const hotspotColor = (target) => {
            const visual = hotspotVisual(target);
            if (target.id === activeId) return builder.color(visual.activeColor || "#fef3c7", true);
            return builder.color(visual.color || "#a78bfa", true);
          };
          interactables.forEach((target) => {
            if (!Array.isArray(target.position) || target.position.length < 2) return;
            const x = Number(target.position[0]);
            const z = Number(target.position[1]);
            if (!Number.isFinite(x) || !Number.isFinite(z)) return;
            const visual = hotspotVisual(target);
            const color = hotspotColor(target);
            const kind = String(target.kind || "").toLowerCase();
            const radiusScale = Math.max(0.08, Math.min(1.2, Number(visual.radiusScale || 0.34)));
            const radius = Math.max(0.24, Math.min(1.28, Number(target.range || 1.2) * radiusScale));
            const baseSize = Math.max(0.08, Math.min(0.34, Number(visual.baseSize || 0.18)));
            const height = target.id === activeId
              ? Number(visual.activeHeight || visual.height || 0.82)
              : Number(visual.height || 0.52);
            const y = -0.83;

            builder.box([x - baseSize, -1.048, z - baseSize], [x + baseSize, -0.965, z + baseSize], color);
            builder.beam([x - radius, y, z - radius], [x + radius, y, z - radius], 0.012 + pulse * 0.006, color);
            builder.beam([x + radius, y, z - radius], [x + radius, y, z + radius], 0.012 + pulse * 0.006, color);
            builder.beam([x + radius, y, z + radius], [x - radius, y, z + radius], 0.012 + pulse * 0.006, color);
            builder.beam([x - radius, y, z + radius], [x - radius, y, z - radius], 0.012 + pulse * 0.006, color);
            builder.beam([x, -0.78, z], [x, -0.78 + Math.max(0.12, height), z], 0.014 + pulse * 0.008, color);

            if (kind === "terminal" && visual.terminalPanel !== false) {
              builder.box([x - 0.24, -0.44, z - 0.08], [x + 0.24, -0.28, z + 0.08], color);
            } else if ((kind === "access" || kind === "door") && visual.routeBeam !== false) {
              builder.beam([x - 0.36, -0.48, z], [x + 0.36, -0.48, z], 0.016 + pulse * 0.006, color);
            }
          });
        }


        appendPilotStationHighlights(builder, nowMs) {
          if (!this.pilotStations.length) return;
          const activeId = this.pilot.station?.id || "";
          const hoverId = this.hoveredPilotStation?.id || "";
          this.pilotStations.forEach((station) => {
            const isActive = station.id === activeId;
            const isHovered = station.id === hoverId;
            if (!isActive && !isHovered) return;
            const pulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 120);
            const color = isActive
              ? builder.color(pulse > 0.45 ? "#22d3ee" : "#0ea5e9", true)
              : builder.color(pulse > 0.45 ? "#fbbf24" : "#f97316", true);
            builder.box(station.glowBounds.min, station.glowBounds.max, color);
          });
        }

        appendPilotViewModel(builder) {
          if (!this.pilot.active) return;
          const {forward, right, up} = this.cameraBasis();
          const add = (origin, ...terms) => {
            const point = origin.slice();
            terms.forEach(([vector, scale]) => {
              point[0] += vector[0] * scale;
              point[1] += vector[1] * scale;
              point[2] += vector[2] * scale;
            });
            return point;
          };
          const consoleGlow = builder.color("#22d3ee", true);
          const courseGlow = builder.color("#fbbf24", true);
          const origin = add(this.camera, [forward, 0.95], [up, -0.22]);
          const left = add(origin, [right, -0.34]);
          const rightPoint = add(origin, [right, 0.34]);
          builder.beam(left, rightPoint, 0.018, consoleGlow);
          const vectorEnd = add(origin, [forward, 0.42 + this.pilot.impulse * 0.55], [right, Math.sin(this.pilot.heading * Math.PI / 180) * 0.22], [up, Math.sin(this.pilot.pitch * Math.PI / 180) * 0.22]);
          builder.beam(origin, vectorEnd, 0.025, courseGlow);
          builder.box(add(origin, [right, -0.04], [up, -0.04]), add(origin, [right, 0.04], [up, 0.04], [forward, 0.02]), consoleGlow);
        }

        cameraDirection() {
          const yaw = this.look.yaw * Math.PI / 180;
          const pitch = this.look.pitch * Math.PI / 180;
          return shuttle3dNormalizeVector([
            Math.sin(yaw) * Math.cos(pitch),
            Math.sin(pitch),
            -Math.cos(yaw) * Math.cos(pitch)
          ]);
        }

        cameraBasis() {
          const forward = this.cameraDirection();
          const right = shuttle3dNormalizeVector(shuttle3dCross(forward, [0, 1, 0]));
          const up = shuttle3dNormalizeVector(shuttle3dCross(right, forward));
          return {forward, right, up};
        }

        refreshAnnotationPrimitiveTargets() {
          const staticTargets = Array.isArray(this.staticAnnotationPrimitiveTargets) ? this.staticAnnotationPrimitiveTargets : [];
          const dynamicTargets = Array.isArray(this.dynamicAnnotationPrimitiveTargets) ? this.dynamicAnnotationPrimitiveTargets : [];
          this.annotationPrimitiveTargets = staticTargets.concat(dynamicTargets);
          return this.annotationPrimitiveTargets;
        }

        setPolygonAnnotationKeyHeld(active) {
          // Patch U: hold P + click enters a targeted polygon/object annotation flow.
          this.polygonAnnotationKeyHeld = Boolean(active);
          const shell = this.canvas?.closest?.(".scene-shuttle3d");
          if (shell) {
            shell.dataset.polygonAnnotationMode = this.polygonAnnotationKeyHeld ? "held" : "inactive";
            const hint = shell.querySelector?.("[data-shuttle3d-annotation-hint]");
            if (hint) {
              hint.hidden = !this.polygonAnnotationKeyHeld;
              hint.textContent = this.polygonAnnotationKeyHeld
                ? "P held: click a visible polygon or object to annotate it."
                : "Hold P and click a polygon or object to annotate it.";
            }
          }
        }

        isPolygonAnnotationKeyHeld() {
          return Boolean(this.polygonAnnotationKeyHeld);
        }

        shuttle3dScreenRay(clientX, clientY) {
          const rect = this.canvas.getBoundingClientRect?.();
          if (!rect || rect.width <= 0 || rect.height <= 0) return null;
          const x = ((clientX - rect.left) / rect.width) * 2 - 1;
          const y = 1 - ((clientY - rect.top) / rect.height) * 2;
          const {forward, right, up} = this.cameraBasis();
          const fov = 66 * Math.PI / 180;
          const scale = Math.tan(fov / 2);
          const aspect = this.aspect || rect.width / Math.max(1, rect.height);
          const direction = shuttle3dNormalizeVector([
            forward[0] + right[0] * x * scale * aspect + up[0] * y * scale,
            forward[1] + right[1] * x * scale * aspect + up[1] * y * scale,
            forward[2] + right[2] * x * scale * aspect + up[2] * y * scale
          ]);
          return {origin: this.camera.slice(), direction};
        }

        polygonAnnotationBounds(minimum, maximum) {
          if (!Array.isArray(minimum) || !Array.isArray(maximum) || minimum.length < 3 || maximum.length < 3) return null;
          const min = minimum.slice(0, 3).map(Number);
          const max = maximum.slice(0, 3).map(Number);
          if (!min.every(Number.isFinite) || !max.every(Number.isFinite)) return null;
          return {
            min: [
              Math.min(min[0], max[0]),
              Math.min(min[1], max[1]),
              Math.min(min[2], max[2])
            ],
            max: [
              Math.max(min[0], max[0]),
              Math.max(min[1], max[1]),
              Math.max(min[2], max[2])
            ]
          };
        }

        polygonAnnotationInflatedBounds(minimum, maximum, radius = 0.04) {
          const bounds = this.polygonAnnotationBounds(minimum, maximum);
          if (!bounds) return null;
          const amount = Math.max(0.005, Math.min(0.6, Number(radius) || 0.04));
          return {
            min: [bounds.min[0] - amount, bounds.min[1] - amount, bounds.min[2] - amount],
            max: [bounds.max[0] + amount, bounds.max[1] + amount, bounds.max[2] + amount]
          };
        }

        polygonAnnotationPropBounds(prop) {
          if (!Array.isArray(prop?.position) || prop.position.length < 2) return null;
          const x = Number(prop.position[0]);
          const z = Number(prop.position[1]);
          if (!Number.isFinite(x) || !Number.isFinite(z)) return null;
          const kind = String(prop.kind || "prop").toLowerCase();
          const size = Array.isArray(prop.size) ? prop.size.map(Number) : [];
          const width = Math.max(0.12, Number.isFinite(size[0]) ? size[0] : 0.6);
          const depth = Math.max(0.12, Number.isFinite(size[1]) ? size[1] : 0.5);
          const height = Math.max(0.12, Number.isFinite(size[2]) ? size[2] : depth);
          if (kind === "sign") {
            const facing = String(prop.facing || "north").toLowerCase();
            const y0 = 1.1;
            const y1 = y0 + Math.max(0.18, depth);
            if (facing === "east" || facing === "west") return this.polygonAnnotationInflatedBounds([x, y0, z - width / 2], [x, y1, z + width / 2], 0.06);
            return this.polygonAnnotationInflatedBounds([x - width / 2, y0, z], [x + width / 2, y1, z], 0.06);
          }
          if (kind === "viewscreen" || kind === "status-panel") {
            return this.polygonAnnotationInflatedBounds([x - width / 2, 0.16, z - 0.08], [x + width / 2, 0.35 + depth, z + 0.08], 0.05);
          }
          if (kind === "terminal-console") {
            return this.polygonAnnotationInflatedBounds([x - width / 2, -1.06, z - depth / 2], [x + width / 2, -0.42 + height, z + depth / 2], 0.04);
          }
          if (kind === "beacon" || kind === "map-marker") {
            return this.polygonAnnotationInflatedBounds([x - width / 2, -1.06, z - width / 2], [x + width / 2, -0.72 + height, z + width / 2], 0.05);
          }
          if (kind === "light-strip") {
            const length = Math.max(0.2, width);
            if (String(prop.axis || "x").toLowerCase() === "z") return this.polygonAnnotationInflatedBounds([x, 2.38, z - length / 2], [x, 2.5, z + length / 2], 0.08);
            return this.polygonAnnotationInflatedBounds([x - length / 2, 2.38, z], [x + length / 2, 2.5, z], 0.08);
          }
          return this.polygonAnnotationInflatedBounds([x - width / 2, -1.06, z - depth / 2], [x + width / 2, -0.9, z + depth / 2], 0.03);
        }

        polygonAnnotationRoomGeometryTargets() {
          const targets = [];
          const rooms = Array.isArray(this.interiorConfig?.rooms) ? this.interiorConfig.rooms : [];
          const add = (target) => {
            if (!target?.bounds) return;
            targets.push(target);
          };
          rooms.forEach((room) => {
            const roomId = String(room?.id || room?.location || "room");
            const roomLabel = String(room?.name || roomId);
            const geometry = room?.geometry && typeof room.geometry === "object" ? room.geometry : {};
            const shell = geometry.shell && typeof geometry.shell === "object" ? geometry.shell : {};
            const shellBounds = shell.bounds || room?.bounds || {};
            const minX = Number(shellBounds.minX);
            const maxX = Number(shellBounds.maxX);
            const minZ = Number(shellBounds.minZ);
            const maxZ = Number(shellBounds.maxZ);
            if ([minX, maxX, minZ, maxZ].every(Number.isFinite) && maxX > minX && maxZ > minZ) {
              add({
                targetKind: "room-floor",
                targetId: `${roomId}:floor`,
                targetKey: `room-floor:${roomId}`,
                label: `${roomLabel} floor`,
                room: roomId,
                source: "rooms[].geometry.shell",
                bounds: this.polygonAnnotationInflatedBounds([minX, -1.055, minZ], [maxX, -0.985, maxZ], 0.01)
              });
              add({
                targetKind: "room-ceiling",
                targetId: `${roomId}:ceiling`,
                targetKey: `room-ceiling:${roomId}`,
                label: `${roomLabel} ceiling`,
                room: roomId,
                source: "rooms[].geometry.shell",
                bounds: this.polygonAnnotationInflatedBounds([minX, 2.36, minZ], [maxX, 2.48, maxZ], 0.01)
              });
            }
            (Array.isArray(geometry.walls) ? geometry.walls : []).forEach((wall, index) => {
              const axis = String(wall?.axis || "").toLowerCase();
              const wallId = String(wall?.id || `${roomId}:wall.${index + 1}`);
              let bounds = null;
              if (axis === "x") {
                const x = Number(wall.x);
                const z0 = Number(wall.minZ);
                const z1 = Number(wall.maxZ);
                if ([x, z0, z1].every(Number.isFinite)) bounds = this.polygonAnnotationInflatedBounds([x, -1.06, z0], [x, 2.42, z1], 0.09);
              } else if (axis === "z") {
                const z = Number(wall.z);
                const x0 = Number(wall.minX);
                const x1 = Number(wall.maxX);
                if ([z, x0, x1].every(Number.isFinite)) bounds = this.polygonAnnotationInflatedBounds([x0, -1.06, z], [x1, 2.42, z], 0.09);
              }
              add({
                targetKind: "room-wall",
                targetId: wallId,
                targetKey: `room-wall:${wallId}`,
                label: `${roomLabel} wall ${index + 1}`,
                room: roomId,
                source: "rooms[].geometry.walls",
                bounds
              });
            });
            (Array.isArray(geometry.openings) ? geometry.openings : []).forEach((opening, index) => {
              const bounds = opening?.bounds || {};
              const minOX = Number(bounds.minX);
              const maxOX = Number(bounds.maxX);
              const minOZ = Number(bounds.minZ);
              const maxOZ = Number(bounds.maxZ);
              if (![minOX, maxOX, minOZ, maxOZ].every(Number.isFinite)) return;
              const openingId = String(opening?.id || opening?.exit || opening?.door || `${roomId}:opening.${index + 1}`);
              add({
                targetKind: "room-opening",
                targetId: openingId,
                targetKey: `room-opening:${openingId}`,
                label: `${roomLabel} opening ${index + 1}`,
                room: roomId,
                source: "rooms[].geometry.openings",
                bounds: this.polygonAnnotationInflatedBounds([minOX, -1.04, minOZ], [maxOX, 0.45, maxOZ], 0.08)
              });
            });
            (Array.isArray(geometry.doorPanels) ? geometry.doorPanels : []).forEach((panel, index) => {
              const center = Array.isArray(panel?.center) ? panel.center.map(Number) : [];
              if (center.length < 2 || !center.every(Number.isFinite)) return;
              const width = Math.max(0.24, Number(panel.width) || 1.0);
              const vertical = Boolean(panel.vertical);
              const panelId = String(panel?.id || panel?.door || `${roomId}:doorPanel.${index + 1}`);
              const bounds = vertical
                ? this.polygonAnnotationInflatedBounds([center[0] - 0.08, -0.55, center[1] - width / 2], [center[0] + 0.08, 0.62, center[1] + width / 2], 0.04)
                : this.polygonAnnotationInflatedBounds([center[0] - width / 2, -0.55, center[1] - 0.08], [center[0] + width / 2, 0.62, center[1] + 0.08], 0.04);
              add({
                targetKind: "door-panel",
                targetId: panelId,
                targetKey: `door-panel:${panelId}`,
                label: `${roomLabel} door panel`,
                room: roomId,
                source: "rooms[].geometry.doorPanels",
                bounds
              });
            });
            (Array.isArray(geometry.boxes) ? geometry.boxes : []).forEach((box, index) => {
              const bounds = this.polygonAnnotationBounds(box?.min, box?.max);
              const boxId = String(box?.id || `${roomId}:box.${index + 1}`);
              add({
                targetKind: "room-box",
                targetId: boxId,
                targetKey: `room-box:${boxId}`,
                label: `${roomLabel} structural box ${index + 1}`,
                room: roomId,
                source: "rooms[].geometry.boxes",
                bounds
              });
            });
            (Array.isArray(geometry.beams) ? geometry.beams : []).forEach((beam, index) => {
              const radius = Math.max(0.012, Number(beam?.radius) || 0.03);
              const bounds = this.polygonAnnotationInflatedBounds(beam?.start, beam?.end, radius + 0.04);
              const beamId = String(beam?.id || `${roomId}:beam.${index + 1}`);
              add({
                targetKind: "room-beam",
                targetId: beamId,
                targetKey: `room-beam:${beamId}`,
                label: `${roomLabel} beam ${index + 1}`,
                room: roomId,
                source: "rooms[].geometry.beams",
                bounds
              });
            });
          });
          return targets;
        }

        polygonAnnotationTargets() {
          const targets = [];
          const add = (target) => {
            if (!target?.bounds) return;
            targets.push(target);
          };
          this.polygonAnnotationRoomGeometryTargets().forEach(add);
          (Array.isArray(this.interiorConfig?.props) ? this.interiorConfig.props : []).forEach((prop) => {
            const propId = String(prop?.id || "");
            if (!propId) return;
            add({
              targetKind: `prop.${String(prop.kind || "content")}`,
              targetId: propId,
              targetKey: `prop:${propId}`,
              label: String(prop.label || propId),
              room: String(prop.room || ""),
              source: "motherShipInterior.props",
              bounds: this.polygonAnnotationPropBounds(prop)
            });
          });
          (Array.isArray(this.interiorConfig?.interactables) ? this.interiorConfig.interactables : []).forEach((target) => {
            if (!Array.isArray(target?.position) || target.position.length < 2) return;
            const x = Number(target.position[0]);
            const z = Number(target.position[1]);
            if (!Number.isFinite(x) || !Number.isFinite(z)) return;
            const radius = Math.max(0.18, Math.min(1.4, Number(target.range || 1.0) * 0.22));
            add({
              targetKind: `interactable.${String(target.kind || "action")}`,
              targetId: String(target.id || ""),
              targetKey: `interactable:${String(target.id || "")}`,
              label: String(target.label || target.prompt || target.id || "Interactable"),
              room: String(target.location || ""),
              source: "motherShipInterior.interactables",
              bounds: this.polygonAnnotationInflatedBounds([x - radius, -1.06, z - radius], [x + radius, 0.35, z + radius], 0.04)
            });
          });
          (Array.isArray(this.pilotStations) ? this.pilotStations : []).forEach((station) => {
            add({
              targetKind: "pilot-station",
              targetId: String(station.id || ""),
              targetKey: `pilot-station:${String(station.id || "")}`,
              label: String(station.label || station.id || "Pilot station"),
              room: "shuttle.cockpit",
              source: "shuttle3d.pilotStations",
              bounds: station.bounds
            });
          });
          // Patch U.1: append generic rendered primitive fallbacks after stable targets.
          // This makes P+click useful for visible beams/bars and other one-off geometry
          // before every piece has been promoted to a named data definition.
          (Array.isArray(this.annotationPrimitiveTargets) ? this.annotationPrimitiveTargets : []).forEach(add);
          return targets;
        }

        pickPolygonAnnotationTarget(clientX, clientY) {
          const ray = this.shuttle3dScreenRay(clientX, clientY);
          if (!ray) return null;
          let best = null;
          let bestDistance = Infinity;
          this.polygonAnnotationTargets().forEach((target) => {
            const bounds = target?.bounds;
            if (!bounds || !Array.isArray(bounds.min) || !Array.isArray(bounds.max)) return;
            const distance = shuttle3dRayIntersectsBounds(ray.origin, ray.direction, bounds);
            if (!Number.isFinite(distance) || distance >= bestDistance) return;
            bestDistance = distance;
            best = target;
          });
          if (!best) return null;
          const hit = [
            ray.origin[0] + ray.direction[0] * bestDistance,
            ray.origin[1] + ray.direction[1] * bestDistance,
            ray.origin[2] + ray.direction[2] * bestDistance
          ];
          return {
            ...best,
            distance: bestDistance,
            hit,
            camera: {
              position: this.camera.slice(),
              yaw: Number(this.look?.yaw || 0),
              pitch: Number(this.look?.pitch || 0)
            }
          };
        }

        appendMotherShipViewscreenDisplay(builder, prop, nowMs = 0) {
          return globalThis.MainComputerShuttle3DRendererModules?.call(
            "viewscreens",
            "appendMotherShipViewscreenDisplay",
            this,
            builder,
            prop,
            nowMs
          );
        }

        appendSystemPlanetDisplay(builder, prop, nowMs = 0) {
          return globalThis.MainComputerShuttle3DRendererModules?.call(
            "viewscreens",
            "appendSystemPlanetDisplay",
            this,
            builder,
            prop,
            nowMs
          );
        }

        appendWarpTransitDisplay(builder, prop, nowMs = 0, navigationState = null) {
          return globalThis.MainComputerShuttle3DRendererModules?.call(
            "viewscreens",
            "appendWarpTransitDisplay",
            this,
            builder,
            prop,
            nowMs,
            navigationState
          );
        }

        appendEnemyShipTacticalDisplay(builder, prop, nowMs = 0) {
          return globalThis.MainComputerShuttle3DRendererModules?.call(
            "viewscreens",
            "appendEnemyShipTacticalDisplay",
            this,
            builder,
            prop,
            nowMs
          );
        }


        appendMotherShip(builder, center, scale = 1, docked = false) {
          const shipHull = builder.color("#aebdca");
          const shipDark = builder.color("#66798e");
          const shipGlow = builder.color(docked ? "#86efac" : "#4da6ff", true);
          const p = (dx, dy, dz) => [
            center[0] + dx * scale,
            center[1] + dy * scale,
            center[2] + dz * scale
          ];
          const box = (min, max, color) => builder.box(p(min[0], min[1], min[2]), p(max[0], max[1], max[2]), color);

          builder.ellipsoid(p(0, 0, 0), [3.35 * scale, 0.48 * scale, 1.75 * scale], 18, 8, shipHull);
          builder.ellipsoid(p(0, -0.92, 1.3), [1.15 * scale, 0.62 * scale, 1.65 * scale], 14, 7, shipDark);
          box([-0.3, -0.65, 0.5], [0.3, -0.1, 1.8], shipHull);
          box([-3.1, -1.5, 1.3], [-2.63, -1.12, 4.8], shipDark);
          box([2.63, -1.5, 1.3], [3.1, -1.12, 4.8], shipDark);
          box([-3.15, -1.46, 4.5], [-2.58, -1.14, 4.95], shipGlow);
          box([2.58, -1.46, 4.5], [3.15, -1.14, 4.95], shipGlow);
          box([-2.73, -1.34, 1.8], [2.73, -1.22, 2.05], shipHull);
          if (docked) {
            const dockGlow = builder.color("#86efac", true);
            builder.beam([-2.15, 0.82, -7.05], p(-1.45, -0.28, 4.1), 0.028, dockGlow);
            builder.beam([2.15, 0.82, -7.05], p(1.45, -0.28, 4.1), 0.028, dockGlow);
            builder.box([-2.78, 0.12, -7.04], [2.78, 0.2, -6.94], dockGlow);
          }
        }

        appendAlienRaider(builder, center, scaleVector) {
          const alienHull = builder.color("#4d7c0f");
          const alienDark = builder.color("#18240f");
          const alienGlow = builder.color("#ef4444", true);
          const [alienX, alienY, alienZ] = center;
          const [alienScaleX, alienScaleY, alienScaleZ] = scaleVector;
          builder.ellipsoid(
            [alienX, alienY, alienZ],
            [alienScaleX * 0.32, alienScaleY * 0.85, alienScaleZ],
            16,
            8,
            alienDark
          );
          builder.ellipsoid(
            [alienX - alienScaleX * 0.58, alienY, alienZ + alienScaleZ * 0.12],
            [alienScaleX * 0.55, alienScaleY * 0.3, alienScaleZ * 0.58],
            14,
            6,
            alienHull
          );
          builder.ellipsoid(
            [alienX + alienScaleX * 0.58, alienY, alienZ + alienScaleZ * 0.12],
            [alienScaleX * 0.55, alienScaleY * 0.3, alienScaleZ * 0.58],
            14,
            6,
            alienHull
          );
          builder.box(
            [alienX - alienScaleX * 0.12, alienY - alienScaleY * 0.22, alienZ - alienScaleZ * 0.96],
            [alienX + alienScaleX * 0.12, alienY + alienScaleY * 0.22, alienZ - alienScaleZ * 0.72],
            alienGlow
          );
          builder.box(
            [alienX - alienScaleX * 0.85, alienY - alienScaleY * 0.12, alienZ + alienScaleZ * 0.46],
            [alienX - alienScaleX * 0.54, alienY + alienScaleY * 0.12, alienZ + alienScaleZ * 0.66],
            alienGlow
          );
          builder.box(
            [alienX + alienScaleX * 0.54, alienY - alienScaleY * 0.12, alienZ + alienScaleZ * 0.46],
            [alienX + alienScaleX * 0.85, alienY + alienScaleY * 0.12, alienZ + alienScaleZ * 0.66],
            alienGlow
          );
        }

        appendFlightScene(builder, nowMs = 0) {
          if (this.isDockingCutsceneActive()) {
            this.appendDockingCutscene(builder, nowMs);
            return;
          }
          if (this.isShuttleBaySceneActive()) {
            this.appendShuttleBayScene(builder, nowMs);
            return;
          }
          const config = this.flightConfig || shuttle3dFlightConfig(this.scene);
          const flight = this.flight || this.createFlightState();
          const totalApproach = Math.max(0.1, config.startDistance - config.dockingDistance);
          const progress = Math.max(0, Math.min(1, (config.startDistance - flight.distance) / totalApproach));
          const approachScale = 1 + progress * 0.16;
          const impulseJitter = this.pilot.active && this.pilot.impulse > 0.02
            ? Math.sin(nowMs / 220) * this.pilot.impulse * 0.05
            : 0;
          const motherCenter = [
            config.targetPosition[0] + flight.lateralOffset,
            config.targetPosition[1] + flight.verticalOffset + impulseJitter,
            -flight.distance
          ];
          this.appendMotherShip(builder, motherCenter, approachScale, flight.docked);

          const alienShip = this.combat.alienShip;
          const alienDistance = Math.max(flight.distance + 15.5, 31.5);
          this.appendAlienRaider(builder, [
            alienShip.position[0] - flight.lateralOffset * 0.18,
            alienShip.position[1] + flight.verticalOffset * 0.12,
            -alienDistance
          ], alienShip.scale);

          if (this.pilot.active && this.pilot.impulse > 0.04 && !flight.docked) {
            const trailGlow = builder.color("#67e8f9", true);
            const trailLength = 1.6 + this.pilot.impulse * 3.2;
            [-2.25, 0, 2.25].forEach((x, index) => {
              const y = index === 1 ? 0.38 : 0.72;
              builder.beam([x, y, -7.02], [x - flight.lateralOffset * 0.05, y + flight.verticalOffset * 0.03, -7.02 - trailLength], 0.018, trailGlow);
            });
          }
        }

        appendPhaserViewModel(builder) {
          if (this.pilot.active || !this.combat.enabled || !this.combat.phaser.enabled || this.gameOver) return;
          const {forward, right, up} = this.cameraBasis();
          const add = (origin, ...terms) => {
            const point = origin.slice();
            terms.forEach(([vector, scale]) => {
              point[0] += vector[0] * scale;
              point[1] += vector[1] * scale;
              point[2] += vector[2] * scale;
            });
            return point;
          };
          const body = builder.color("#d8e2ea");
          const grip = builder.color("#25364a");
          const accent = builder.color("#f59e0b", true);
          const base = add(this.camera, [forward, 0.48], [right, 0.28], [up, -0.23]);
          const muzzle = add(base, [forward, 0.48]);
          const gripStart = add(base, [forward, 0.08], [up, -0.025]);
          const gripEnd = add(gripStart, [forward, -0.08], [up, -0.25]);
          builder.beam(base, muzzle, 0.055, body);
          builder.beam(gripStart, gripEnd, 0.07, grip);
          builder.beam(add(muzzle, [forward, -0.04]), add(muzzle, [forward, 0.035]), 0.072, accent);
        }

        buildDynamicGeometry(nowMs) {
          const annotationTargets = [];
          const builder = new Shuttle3dGeometryWriter({
            annotationTargets,
            annotationSource: "scene-viewer.dynamic"
          });
          const transportGlow = builder.color("#84cc16", true);
          const alienBody = builder.color("#365314");
          const alienArmor = builder.color("#1a2e05");
          const alienHit = builder.color("#fef08a", true);
          const alienEyes = builder.color("#ef4444", true);
          const healthBack = builder.color("#111827");
          const healthFill = builder.color("#84cc16", true);
          this.appendFlightScene(builder, nowMs);
          if (this.isDockingCutsceneActive()) {
            this.dynamicAnnotationPrimitiveTargets = annotationTargets;
            this.refreshAnnotationPrimitiveTargets?.();
            return builder.toFloat32Array();
          }
          if (this.isShuttleBaySceneActive()) {
            // The mother-ship bridge is implemented as the shuttle-bay scene. Character
            // AI remains active there, so include its geometry before returning from the
            // alternate-scene branch. The previous combined docking guard returned first,
            // leaving live boarders in runtime state without any rendered entity geometry.
            this.appendPhaserViewModel(builder);
            this.appendCharacterAIGeometry(builder, nowMs);
            if (this.phaserBeam && nowMs <= this.phaserBeam.expiresAtMs) {
              builder.beam(this.phaserBeam.start, this.phaserBeam.end, 0.025, builder.color("#f59e0b", true));
              builder.beam(this.phaserBeam.start, this.phaserBeam.end, 0.009, builder.color("#fff7d6", true));
            }
            this.dynamicAnnotationPrimitiveTargets = annotationTargets;
            this.refreshAnnotationPrimitiveTargets?.();
            return builder.toFloat32Array();
          }
          this.appendPilotStationHighlights(builder, nowMs);
          this.appendPilotViewModel(builder);
          this.appendPhaserViewModel(builder);

          this.aliens.forEach((alien) => {
            const [x, y, z] = alien.position;
            const transporting = alien.state === "transporting";
            if (transporting) {
              builder.beam([x, -1.35, z], [x, 2.55, z], 0.12, transportGlow);
              builder.beam([x - 0.23, -1.25, z], [x - 0.23, 2.25, z], 0.035, transportGlow);
              builder.beam([x + 0.23, -1.25, z], [x + 0.23, 2.25, z], 0.035, transportGlow);
              builder.beam([x, -1.25, z - 0.23], [x, 2.25, z - 0.23], 0.035, transportGlow);
              builder.beam([x, -1.25, z + 0.23], [x, 2.25, z + 0.23], 0.035, transportGlow);
            }

            const bodyColor = nowMs < alien.hitFlashUntilMs ? alienHit : alienBody;
            builder.ellipsoid([x, y + 0.22, z], [0.34, 0.72, 0.3], 10, 6, bodyColor);
            builder.ellipsoid([x, y + 1.0, z], [0.32, 0.34, 0.3], 10, 6, bodyColor);
            builder.box([x - 0.46, y + 0.2, z - 0.16], [x + 0.46, y + 0.42, z + 0.16], alienArmor);
            builder.box([x - 0.2, y - 0.72, z - 0.15], [x - 0.05, y + 0.05, z + 0.15], alienArmor);
            builder.box([x + 0.05, y - 0.72, z - 0.15], [x + 0.2, y + 0.05, z + 0.15], alienArmor);
            builder.box([x - 0.18, y + 1.03, z - 0.32], [x - 0.05, y + 1.12, z - 0.27], alienEyes);
            builder.box([x + 0.05, y + 1.03, z - 0.32], [x + 0.18, y + 1.12, z - 0.27], alienEyes);

            if (!transporting) {
              const ratio = Math.max(0, Math.min(1, alien.health / alien.maxHealth));
              builder.box([x - 0.46, y + 1.48, z - 0.06], [x + 0.46, y + 1.56, z + 0.06], healthBack);
              if (ratio > 0) {
                builder.box(
                  [x - 0.44, y + 1.49, z - 0.065],
                  [x - 0.44 + 0.88 * ratio, y + 1.55, z + 0.065],
                  healthFill
                );
              }
            }
          });

          if (this.phaserBeam && nowMs <= this.phaserBeam.expiresAtMs) {
            builder.beam(this.phaserBeam.start, this.phaserBeam.end, 0.025, builder.color("#f59e0b", true));
            builder.beam(this.phaserBeam.start, this.phaserBeam.end, 0.009, builder.color("#fff7d6", true));
          }
          this.dynamicAnnotationPrimitiveTargets = annotationTargets;
          this.refreshAnnotationPrimitiveTargets?.();
          return builder.toFloat32Array();
        }

        combatSnapshot(nowMs = this.combatClockMs) {
          const cooldownRemainingMs = Math.max(
            0,
            this.combat.phaser.cooldownMs - (nowMs - this.lastPhaserShotAt)
          );
          const characterEnemies = this.visibleCharacterAICharacters()
            .filter((character) => character.kind === "enemy");
          return {
            enabled: this.combat.enabled,
            health: Math.max(0, Math.round(this.playerHealth)),
            maxHealth: this.combat.player.maxHealth,
            alive: this.aliens.length + characterEnemies.length,
            active: this.aliens.filter((alien) => alien.state === "active").length + characterEnemies.length,
            transporting: this.aliens.filter((alien) => alien.state === "transporting").length,
            kills: this.kills,
            gameOver: this.gameOver,
            paused: this.isBoardingPaused(),
            pilotStation: this.pilot.station?.label || "",
            phaserReady: !this.gameOver && !this.isBoardingPaused() && cooldownRemainingMs <= 0,
            cooldownRemainingMs: Math.ceil(cooldownRemainingMs)
          };
        }

        emitCombatState(force = false) {
          if (typeof this.onCombatChanged !== "function") return;
          if (!force && this.combatClockMs - this.lastCombatUiAt < 80) return;
          this.lastCombatUiAt = this.combatClockMs;
          this.onCombatChanged(this.combatSnapshot());
        }

        spawnAlien(nowMs) {
          if (!this.combat.enabled || this.gameOver || this.aliens.length >= this.combat.transport.maxAlive) return false;
          const points = this.combat.transport.spawnPoints;
          const point = points[this.transportSequence % points.length];
          this.transportSequence += 1;
          const alien = {
            id: `boarding-alien-${this.transportSequence}`,
            spawnId: point.id,
            position: point.position.slice(),
            health: this.combat.alien.maxHealth,
            maxHealth: this.combat.alien.maxHealth,
            state: "transporting",
            transportUntilMs: nowMs + this.combat.transport.beamDurationMs,
            nextAttackAtMs: nowMs + this.combat.transport.beamDurationMs + 400,
            hitFlashUntilMs: 0
          };
          this.aliens.push(alien);
          this.emitCombatState(true);
          return true;
        }

        canAlienOccupy(alien, x, z) {
          const {bounds, colliders} = this.movement;
          const radius = this.combat.alien.radius;
          if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) return false;
          const blockedByFixture = colliders.some((collider) => (
            x > collider.minX - radius
            && x < collider.maxX + radius
            && z > collider.minZ - radius
            && z < collider.maxZ + radius
          ));
          if (blockedByFixture) return false;
          return !this.aliens.some((other) => (
            other !== alien
            && other.state === "active"
            && Math.hypot(x - other.position[0], z - other.position[2]) < radius * 1.65
          ));
        }

        updateCombat(nowMs, deltaSeconds) {
          if (!this.combat.enabled) return;
          if (this.isBoardingPaused()) {
            this.emitCombatState();
            return;
          }
          this.combatClockMs = nowMs;
          if (this.phaserBeam && nowMs > this.phaserBeam.expiresAtMs) this.phaserBeam = null;
          if (this.gameOver) {
            this.emitCombatState();
            return;
          }

          if (this.characterAIPhase() === "shuttle" && nowMs >= this.nextTransportAtMs) {
            // The shuttle's original boarding encounter is a separate legacy combat
            // system. Character AI may be initialized for later system scenarios, but
            // that must not suppress the shuttle boarders.
            this.spawnAlien(nowMs);
            this.nextTransportAtMs = nowMs + this.combat.transport.intervalMs;
          }

          let healthChanged = false;
          this.aliens.forEach((alien) => {
            if (alien.state === "transporting") {
              if (nowMs >= alien.transportUntilMs) {
                alien.state = "active";
                this.emitCombatState(true);
              } else {
                return;
              }
            }

            const dx = this.camera[0] - alien.position[0];
            const dz = this.camera[2] - alien.position[2];
            const distance = Math.hypot(dx, dz);
            if (distance > this.combat.alien.attackRange) {
              const step = Math.min(distance, this.combat.alien.speed * Math.min(0.05, deltaSeconds));
              const moveX = distance > 0 ? dx / distance * step : 0;
              const moveZ = distance > 0 ? dz / distance * step : 0;
              const candidateX = alien.position[0] + moveX;
              const candidateZ = alien.position[2] + moveZ;
              if (this.canAlienOccupy(alien, candidateX, alien.position[2])) alien.position[0] = candidateX;
              if (this.canAlienOccupy(alien, alien.position[0], candidateZ)) alien.position[2] = candidateZ;
            } else if (nowMs >= alien.nextAttackAtMs) {
              alien.nextAttackAtMs = nowMs + this.combat.alien.attackCooldownMs;
              this.playerHealth = Math.max(0, this.playerHealth - this.combat.alien.damage);
              healthChanged = true;
            }
          });

          if (this.playerHealth <= 0) {
            this.gameOver = true;
            this.clearMovementKeys();
            healthChanged = true;
          }
          this.emitCombatState(healthChanged);
        }

        firePhaser(nowMs = performance.now()) {
          if (this.isWeaponFirePaused() || !this.combat.enabled || !this.combat.phaser.enabled || this.gameOver) return false;
          this.combatClockMs = Math.max(this.combatClockMs, nowMs);
          if (nowMs - this.lastPhaserShotAt < this.combat.phaser.cooldownMs) return false;
          this.lastPhaserShotAt = nowMs;
          const {forward, right, up} = this.cameraBasis();
          const start = [
            this.camera[0] + forward[0] * 0.42 + right[0] * 0.18 - up[0] * 0.1,
            this.camera[1] + forward[1] * 0.42 + right[1] * 0.18 - up[1] * 0.1,
            this.camera[2] + forward[2] * 0.42 + right[2] * 0.18 - up[2] * 0.1
          ];

          let hitAlien = null;
          let hitCharacter = null;
          let hitDistance = this.combat.phaser.range;
          const considerTarget = (target, center, radius, kind) => {
            const toCenter = shuttle3dSubtract(center, this.camera);
            const distanceAlongRay = shuttle3dDot(toCenter, forward);
            if (distanceAlongRay <= 0 || distanceAlongRay >= hitDistance) return;
            const closest = [
              this.camera[0] + forward[0] * distanceAlongRay,
              this.camera[1] + forward[1] * distanceAlongRay,
              this.camera[2] + forward[2] * distanceAlongRay
            ];
            const missDistance = Math.hypot(
              closest[0] - center[0],
              closest[1] - center[1],
              closest[2] - center[2]
            );
            if (missDistance > radius) return;
            hitDistance = distanceAlongRay;
            if (kind === "character") {
              hitCharacter = target;
              hitAlien = null;
            } else {
              hitAlien = target;
              hitCharacter = null;
            }
          };

          this.aliens.forEach((alien) => {
            if (alien.state !== "active") return;
            considerTarget(
              alien,
              [alien.position[0], alien.position[1] + 0.78, alien.position[2]],
              Math.max(0.68, this.combat.alien.radius * 1.7),
              "alien"
            );
          });
          this.visibleCharacterAICharacters()
            .filter((character) => character.kind === "enemy")
            .forEach((character) => {
              considerTarget(
                character,
                [character.position[0], character.position[1] + 0.78, character.position[2]],
                0.7,
                "character"
              );
            });

          const end = [
            this.camera[0] + forward[0] * hitDistance,
            this.camera[1] + forward[1] * hitDistance,
            this.camera[2] + forward[2] * hitDistance
          ];
          this.phaserBeam = {
            start,
            end,
            expiresAtMs: nowMs + this.combat.phaser.beamDurationMs
          };

          const scenarioRuntime = globalThis.MainComputerSystemScenarioRuntime?.current?.();
          const scenarioContext = scenarioRuntime?.activeScenarioContext?.() || {};
          if (scenarioContext.id && scenarioContext.status === "active") {
            scenarioRuntime.recordPlayerAction?.(
              scenarioContext.id,
              "weapon-discharge",
              {
                targetId: hitCharacter?.id || hitAlien?.id || "",
                targetKind: hitCharacter
                  ? "character"
                  : hitAlien
                    ? "legacy-alien"
                    : "none",
                defensive: scenarioContext.stageId === "protect-witness"
              },
              {nowMs}
            );
          }

          if (hitAlien) {
            hitAlien.health -= this.combat.phaser.damage;
            hitAlien.hitFlashUntilMs = nowMs + 120;
            if (hitAlien.health <= 0) {
              this.aliens = this.aliens.filter((alien) => alien !== hitAlien);
              this.kills += 1;
            }
          }
          if (hitCharacter && this.characterAIRuntime?.damageCharacter) {
            const before = this.characterAIRuntime.character(hitCharacter.id);
            const result = this.characterAIRuntime.damageCharacter(
              hitCharacter.id,
              this.combat.phaser.damage,
              {sourceId: "player", nowMs}
            );
            if (before?.health > 0 && result.character?.health <= 0) {
              this.kills += 1;
              this.visibleCharacterAICharacters()
                .filter((character) => character.kind === "npc")
                .forEach((character) => {
                  this.characterAIRuntime.markProtectedByPlayer(
                    character.id,
                    nowMs
                  );
                });
            }
            this.emitCharacterAIState(true);
          }
          this.emitCombatState(true);
          return true;
        }

        resetCombat(nowMs = performance.now()) {
          if (!this.gameOver) return false;
          if (this.pilot.active) this.setPilotMode(false, null, nowMs);
          this.playerHealth = this.combat.player.startingHealth;
          this.aliens = [];
          this.characterAIRuntime?.reset?.({emit: false});
          this.transportSequence = 0;
          this.kills = 0;
          this.gameOver = false;
          this.phaserBeam = null;
          this.lastPhaserShotAt = -Infinity;
          this.combatClockMs = nowMs;
          this.nextTransportAtMs = nowMs + Math.min(900, this.combat.transport.initialDelayMs);
          this.clearMovementKeys();
          this.resetFlightState();
          this.emitCharacterAIState(true);
          this.emitCombatState(true);
          return true;
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

        isMovementControlKey(code) {
          return code === "KeyW"
            || code === "KeyA"
            || code === "KeyS"
            || code === "KeyD"
            || code === "ArrowLeft"
            || code === "ArrowRight"
            || code === "ArrowUp"
            || code === "ArrowDown"
            || code === "ShiftLeft"
            || code === "ShiftRight";
        }

        setMovementKey(code, active) {
          if (!this.movement.enabled) return;
          const isMovementControl = this.isMovementControlKey(code);
          if (this.isShuttleBayPlayerControlActive() && isMovementControl) {
            const suppressed = this.bayControlSuppressedKeys instanceof Set
              ? this.bayControlSuppressedKeys
              : new Set();
            if (this.bayControlSuppressedKeys !== suppressed) this.bayControlSuppressedKeys = suppressed;
            if (!active && suppressed.has(code)) {
              suppressed.delete(code);
              this.movementKeys.delete(code);
              return;
            }
            const nowMs = performance.now();
            if (active && (suppressed.has(code) || nowMs < this.bayControlInputUnlockAtMs)) return;
          }
          if (active) this.movementKeys.add(code);
          else this.movementKeys.delete(code);
        }

        clearMovementKeys() {
          this.movementKeys.clear();
        }

        canOccupy(x, z) {
          const shipSceneActive = this.isShuttleBaySceneActive();
          const movement = shipSceneActive ? this.shuttleBayMovementConfig() : this.movement;
          const {bounds, radius, colliders} = movement;
          if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) return false;
          if (shipSceneActive && (!this.isInsideMotherShipWalkable(x, z) || !this.shipDoorAllowsPosition(x, z))) return false;
          // Authored fixtures and rooms[].geometry.walls share the same collision path.
          // Wall arrays are already split around doors/openings, so the player's radius
          // blocks solid wall spans without sealing intended passages.
          const blockedByShipGeometry = colliders.some((collider) => (
            x > collider.minX - radius
            && x < collider.maxX + radius
            && z > collider.minZ - radius
            && z < collider.maxZ + radius
          ));
          if (blockedByShipGeometry) return false;
          if (shipSceneActive) return true;
          return !this.aliens.some((alien) => (
            alien.state === "active"
            && Math.hypot(x - alien.position[0], z - alien.position[2]) < radius + this.combat.alien.radius
          ));
        }

        moveCamera(deltaX, deltaZ) {
          if (this.isDockingCutsceneActive() || this.pilot.active || !this.movement.enabled || this.gameOver) return;
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
          if (changed) {
            this.syncShipLocationFromCamera?.();
            if (this.isShuttleBayPlayerControlActive()) this.emitShipState?.();
            if (typeof this.onCameraMoved === "function") {
              this.onCameraMoved(this.camera.slice());
            }
          }
        }

        updateMovement(deltaSeconds) {
          if (this.isDockingCutsceneActive()) {
            this.updateDockingCutscene(deltaSeconds);
            return;
          }
          if (this.isShuttleBaySceneActive() && !this.isShuttleBayPlayerControlActive()) return;
          if (this.pilot.active) {
            this.updatePilot(deltaSeconds);
            return;
          }
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
          this.updateSpaceNavigation(frameTime);
          this.updateMovement(deltaSeconds);
          this.updateCharacterAI(frameTime, deltaSeconds);
          this.updateCombat(frameTime, deltaSeconds);
          this.dynamicGeometry = this.buildDynamicGeometry(frameTime);
          this.dynamicVertexCount = this.dynamicGeometry.length / 10;
          this.vertexCount = this.worldVertexCount + this.starVertexCount + this.dynamicVertexCount;
          this.gl.bindBuffer(this.gl.ARRAY_BUFFER, this.dynamicBuffer);
          this.gl.bufferData(this.gl.ARRAY_BUFFER, this.dynamicGeometry, this.gl.DYNAMIC_DRAW);
          this.resize();
          const gl = this.gl;
          const dockingCutsceneActive = this.isDockingCutsceneActive();
          const shuttleBaySceneActive = this.isShuttleBaySceneActive();
          const alternateSceneActive = dockingCutsceneActive || shuttleBaySceneActive;
          const direction = this.cameraDirection();
          const target = [
            this.camera[0] + direction[0],
            this.camera[1] + direction[1],
            this.camera[2] + direction[2]
          ];
          const cameraView = dockingCutsceneActive
            ? this.dockingCutsceneCamera(frameTime)
            : {eye: this.camera, target};
          const farPlane = Math.max(140, this.starfield.radius + this.starfield.maximumSize + 8);
          const projection = shuttle3dPerspectiveMatrix(66 * Math.PI / 180, this.aspect || 16 / 9, 0.08, farPlane);
          const view = shuttle3dLookAtMatrix(cameraView.eye, cameraView.target, [0, 1, 0]);

          gl.clearColor(0.002, 0.006, 0.02, 1);
          gl.clearDepth(1);
          gl.enable(gl.DEPTH_TEST);
          gl.depthFunc(gl.LEQUAL);
          gl.disable(gl.CULL_FACE);
          gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
          gl.useProgram(this.program);
          gl.uniformMatrix4fv(this.locations.projection, false, projection);
          gl.uniformMatrix4fv(this.locations.view, false, view);
          gl.uniform3fv(this.locations.camera, new Float32Array(cameraView.eye));
          gl.uniform1f(this.locations.time, frameTime / 1000);

          if (!alternateSceneActive) {
            this.bindGeometryBuffer(this.buffer);
            gl.uniform3f(this.locations.offset, 0, 0, 0);
            gl.drawArrays(gl.TRIANGLES, 0, this.worldVertexCount);
          }

          if (this.dynamicVertexCount) {
            this.bindGeometryBuffer(this.dynamicBuffer);
            gl.uniform3f(this.locations.offset, 0, 0, 0);
            gl.drawArrays(gl.TRIANGLES, 0, this.dynamicVertexCount);
          }

          if (!alternateSceneActive) {
            this.bindGeometryBuffer(this.starBuffer);
            gl.uniform3fv(this.locations.offset, new Float32Array(this.camera));
            gl.drawArrays(gl.TRIANGLES, 0, this.starVertexCount);
          }
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
          if (this.dynamicBuffer) this.gl.deleteBuffer(this.dynamicBuffer);
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
          container.removeEventListener("pointermove", handler.pointerHover);
          container.removeEventListener("pointerleave", handler.pointerLeave);
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
          delete container.dataset.shuttle3dCombat;
          delete container.dataset.shuttle3dPilot;
          delete container.dataset.shuttle3dDragging;
        }
      }

      function shuttle3dPolygonAnnotationSanitizeId(value, fallback = "annotation") {
        const clean = String(value || fallback)
          .trim()
          .replace(/[^a-zA-Z0-9_.:-]+/g, "-")
          .replace(/^-+|-+$/g, "");
        return clean || fallback;
      }

      function shuttle3dPolygonAnnotationStore(scene) {
        if (!scene || typeof scene !== "object") return [];
        scene.metadata = scene.metadata && typeof scene.metadata === "object" ? scene.metadata : {};
        scene.metadata.shuttle3d = scene.metadata.shuttle3d && typeof scene.metadata.shuttle3d === "object" ? scene.metadata.shuttle3d : {};
        scene.metadata.shuttle3d.polygonAnnotations = Array.isArray(scene.metadata.shuttle3d.polygonAnnotations)
          ? scene.metadata.shuttle3d.polygonAnnotations
          : [];
        return scene.metadata.shuttle3d.polygonAnnotations;
      }

      function shuttle3dPolygonAnnotationForTarget(scene, target) {
        const annotations = shuttle3dPolygonAnnotationStore(scene);
        const key = String(target?.targetKey || "");
        return annotations.find((annotation) => String(annotation?.targetKey || "") === key) || null;
      }

      async function shuttle3dSavePolygonAnnotation(scene, annotation, options = {}) {
        const annotations = shuttle3dPolygonAnnotationStore(scene);
        const key = String(annotation?.targetKey || "");
        const index = annotations.findIndex((candidate) => String(candidate?.targetKey || "") === key);
        if (index >= 0) annotations[index] = annotation;
        else annotations.push(annotation);
        try {
          window.MainComputerSceneStore?.saveScene?.(scene, {
            source: "shuttle3d-polygon-annotation",
            notify: false
          });
        } catch {
          // The editor callback below remains the disk-persistence source of truth.
        }
        const detail = {
          projectId: String(options.projectId || ""),
          sceneId: String(scene?.id || ""),
          annotation,
          source: "shuttle3d-polygon-annotation"
        };
        if (typeof options.onPolygonAnnotationSave === "function") {
          const result = await options.onPolygonAnnotationSave(detail);
          if (result?.persisted !== true) {
            throw new Error("Annotation save callback did not confirm disk persistence.");
          }
          return result;
        }
        try {
          window.dispatchEvent(new CustomEvent("main-computer-shuttle3d-polygon-annotation-save", {detail}));
        } catch {
          // Annotation still exists in the live scene copy when CustomEvent is unavailable.
        }
        return {persisted: false, annotation, transport: "window-event-fallback"};
      }

      function shuttle3dAnnotationDialogOpen(container) {
        const shell = container?.querySelector?.(".scene-shuttle3d") || container;
        return Boolean(shell?.querySelector?.(".scene-shuttle3d-annotation-dialog[open], .scene-shuttle3d-annotation-dialog[data-open='true']"));
      }

      function shuttle3dAnnotationDialogEventTarget(event) {
        return Boolean(event?.target?.closest?.(".scene-shuttle3d-annotation-dialog"));
      }

      function shuttle3dStopGameplayInputForAnnotationDialog(container) {
        const shuttle = container?.__mainComputerShuttle3dRenderer || null;
        try {
          shuttle?.clearMovementKeys?.();
          shuttle?.setPolygonAnnotationKeyHeld?.(false);
        } catch {
          // The annotation modal is editor tooling; never let input cleanup block typing.
        }
        if (container?.dataset) delete container.dataset.shuttle3dDragging;
      }

      function openShuttle3dPolygonAnnotationDialog(container, scene, target, options = {}) {
        const shell = container?.querySelector?.(".scene-shuttle3d") || container;
        if (!shell || !target) return null;
        shuttle3dStopGameplayInputForAnnotationDialog(container);
        const existing = shuttle3dPolygonAnnotationForTarget(scene, target);
        const dialog = document.createElement("dialog");
        dialog.className = "scene-shuttle3d-annotation-dialog";
        dialog.dataset.open = "true";
        dialog.setAttribute("aria-label", "Annotate selected polygon or object");

        const form = document.createElement("form");
        form.method = "dialog";
        const title = document.createElement("h3");
        title.textContent = "Annotate selected element";
        const targetSummary = document.createElement("p");
        targetSummary.className = "scene-shuttle3d-annotation-target";
        const hit = Array.isArray(target.hit) ? target.hit : [];
        const hitText = hit.length >= 3 && hit.every(Number.isFinite)
          ? ` • hit x ${hit[0].toFixed(2)} y ${hit[1].toFixed(2)} z ${hit[2].toFixed(2)}`
          : "";
        targetSummary.textContent = `${target.label || target.targetId} — ${target.targetKind || "polygon"}${target.room ? ` • ${target.room}` : ""}${hitText}`;

        const labelLabel = document.createElement("label");
        labelLabel.textContent = "Annotation label";
        const labelInput = document.createElement("input");
        labelInput.name = "label";
        labelInput.maxLength = 96;
        labelInput.value = String(existing?.label || target.label || target.targetId || "");
        labelLabel.append(labelInput);

        const noteLabel = document.createElement("label");
        noteLabel.textContent = "Notes";
        const noteInput = document.createElement("textarea");
        noteInput.name = "note";
        noteInput.rows = 5;
        noteInput.maxLength = 1200;
        noteInput.placeholder = "Describe what this polygon/object represents or what should change here.";
        noteInput.value = String(existing?.note || "");
        noteLabel.append(noteInput);

        const tagLabel = document.createElement("label");
        tagLabel.textContent = "Tags";
        const tagInput = document.createElement("input");
        tagInput.name = "tags";
        tagInput.maxLength = 160;
        tagInput.placeholder = "optional, comma-separated";
        tagInput.value = Array.isArray(existing?.tags) ? existing.tags.join(", ") : "";
        tagLabel.append(tagInput);

        const sourceLine = document.createElement("p");
        sourceLine.className = "scene-shuttle3d-annotation-source";
        sourceLine.textContent = `Target: ${target.targetKey || target.targetId || "unknown"} • Source: ${target.source || "runtime geometry"}`;

        const actions = document.createElement("div");
        actions.className = "scene-shuttle3d-annotation-actions";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        const save = document.createElement("button");
        save.type = "submit";
        save.textContent = "Save annotation";
        actions.append(cancel, save);

        form.append(title, targetSummary, labelLabel, noteLabel, tagLabel, sourceLine, actions);
        dialog.append(form);
        shell.append(dialog);

        const removeDialog = () => {
          dialog.dataset.open = "false";
          dialog.remove();
          shuttle3dStopGameplayInputForAnnotationDialog(container);
          shell.focus?.({preventScroll: true});
        };
        const closeDialog = () => {
          if (dialog.open && typeof dialog.close === "function") dialog.close();
          else removeDialog();
        };
        cancel.addEventListener("click", closeDialog);
        dialog.addEventListener("close", removeDialog, {once: true});
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          if (save.disabled) return;
          const targetKey = String(target.targetKey || `${target.targetKind || "target"}:${target.targetId || "unknown"}`);
          const cleanKey = shuttle3dPolygonAnnotationSanitizeId(targetKey, "annotation-target");
          const tags = String(tagInput.value || "")
            .split(",")
            .map((tag) => tag.trim())
            .filter(Boolean)
            .slice(0, 12);
          const annotation = {
            schema: "game.shuttle3d.polygonAnnotation.v1",
            id: String(existing?.id || `annotation.${cleanKey}`),
            targetKey,
            targetId: String(target.targetId || ""),
            targetKind: String(target.targetKind || "polygon"),
            source: String(target.source || ""),
            room: String(target.room || ""),
            label: String(labelInput.value || target.label || target.targetId || "").trim(),
            note: String(noteInput.value || "").trim(),
            tags,
            hit: Array.isArray(target.hit) ? target.hit.map((value) => Number(value)).filter(Number.isFinite).slice(0, 3) : [],
            camera: target.camera && typeof target.camera === "object" ? target.camera : {},
            updatedAt: new Date().toISOString()
          };
          const sourceText = `Target: ${target.targetKey || target.targetId || "unknown"} • Source: ${target.source || "runtime geometry"}`;
          save.disabled = true;
          cancel.disabled = true;
          save.textContent = "Saving...";
          sourceLine.dataset.saveState = "saving";
          sourceLine.textContent = "Writing annotation to project.json...";
          try {
            const result = await shuttle3dSavePolygonAnnotation(scene, annotation, options);
            const hint = shell.querySelector?.("[data-shuttle3d-annotation-hint]");
            if (hint) {
              hint.hidden = false;
              const writePath = String(result?.writePath || result?.write_path || "").trim();
              hint.textContent = result?.persisted === true
                ? `Annotation saved and verified on disk for ${annotation.label || annotation.targetId}${writePath ? ` at ${writePath}` : ""}.`
                : `Annotation saved in the live scene for ${annotation.label || annotation.targetId}.`;
              window.clearTimeout(hint.__mainComputerAnnotationSavedTimer);
              hint.__mainComputerAnnotationSavedTimer = window.setTimeout(() => {
                if (hint && shell.dataset.polygonAnnotationMode !== "held") hint.hidden = true;
              }, 2400);
            }
            closeDialog();
          } catch (error) {
            const message = error instanceof Error ? error.message : String(error || "unknown error");
            save.disabled = false;
            cancel.disabled = false;
            save.textContent = "Save annotation";
            sourceLine.dataset.saveState = "error";
            sourceLine.textContent = `Save failed: ${message}`;
            labelInput.focus?.({preventScroll: true});
          }
        });

        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "open");
        labelInput.focus?.({preventScroll: true});
        labelInput.select?.();
        return dialog;
      }

      function bindShuttle3dLookaround(container, scene, options = {}) {
        disposeShuttle3dLookaround(container);
        const config = shuttle3dCameraConfig(scene);
        const movementCodes = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "ShiftRight"]);
        const pilotCodes = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "ShiftRight", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);
        const firingCodes = new Set(["Space", "KeyF"]);
        setShuttle3dLook(container, config.yaw, config.pitch, config);
        container.dataset.shuttle3dLookaround = "enabled";
        container.dataset.shuttle3dMovement = "wasd";
        container.dataset.shuttle3dCombat = "phaser";
        container.dataset.shuttle3dPilot = "console-hover-e-key";
        container.tabIndex = container.tabIndex >= 0 ? container.tabIndex : 0;
        let dragging = false;
        let dragDistance = 0;
        let startX = 0;
        let startY = 0;
        let startYaw = config.yaw;
        let startPitch = config.pitch;
        const renderer = () => container.__mainComputerShuttle3dRenderer;
        const applyDelta = (dx, dy) => {
          dragDistance = Math.max(dragDistance, Math.hypot(dx, dy));
          const nextYaw = startYaw + dx * 0.14;
          const nextPitch = startPitch - dy * 0.11;
          setShuttle3dLook(container, nextYaw, nextPitch, config);
        };
        const updatePilotHover = (event) => {
          const picked = renderer()?.pickPilotStation?.(event.clientX, event.clientY) || null;
          renderer()?.setHoveredPilotStation?.(picked);
        };
        const pointerDown = (event) => {
          if (event.button !== 0 || event.defaultPrevented) return;
          const target = event.target;
          if (target?.closest?.("button, a, input, select, textarea, dialog")) return;
          const shuttle = renderer();
          if (shuttle?.isPolygonAnnotationKeyHeld?.()) {
            event.preventDefault();
            const picked = shuttle.pickPolygonAnnotationTarget?.(event.clientX, event.clientY);
            if (picked) {
              openShuttle3dPolygonAnnotationDialog(container, scene, picked, options);
            } else {
              const shell = container?.querySelector?.(".scene-shuttle3d") || container;
              const hint = shell?.querySelector?.("[data-shuttle3d-annotation-hint]");
              if (hint) {
                hint.hidden = false;
                hint.textContent = "P held: no selectable rendered primitive under cursor. Try clicking the visible face/bar center.";
              }
            }
            return;
          }
          updatePilotHover(event);
          event.preventDefault();
          dragging = true;
          dragDistance = 0;
          startX = event.clientX;
          startY = event.clientY;
          const current = container.__mainComputerShuttle3dLook || {yaw: config.yaw, pitch: config.pitch};
          startYaw = current.yaw;
          startPitch = current.pitch;
          container.dataset.shuttle3dDragging = "true";
          container.focus({preventScroll: true});
        };
        const pointerHover = (event) => {
          if (shuttle3dAnnotationDialogOpen(container)) return;
          updatePilotHover(event);
        };
        const pointerMove = (event) => {
          if (shuttle3dAnnotationDialogOpen(container)) {
            dragging = false;
            dragDistance = 0;
            delete container.dataset.shuttle3dDragging;
            return;
          }
          if (!dragging) return;
          applyDelta(event.clientX - startX, event.clientY - startY);
          updatePilotHover(event);
        };
        const pointerLeave = () => {
          if (dragging) return;
          renderer()?.setHoveredPilotStation?.(null);
        };
        const pointerUp = () => {
          if (shuttle3dAnnotationDialogOpen(container)) {
            dragging = false;
            dragDistance = 0;
            delete container.dataset.shuttle3dDragging;
            renderer()?.clearMovementKeys?.();
            return;
          }
          if (!dragging) return;
          dragging = false;
          delete container.dataset.shuttle3dDragging;
          if (dragDistance < 5 && !renderer()?.pilot?.active) renderer()?.firePhaser?.();
        };
        const keyDown = (event) => {
          const shuttle = renderer();
          if (shuttle3dAnnotationDialogOpen(container)) {
            if (!shuttle3dAnnotationDialogEventTarget(event)) event.preventDefault();
            shuttle?.clearMovementKeys?.();
            shuttle?.setPolygonAnnotationKeyHeld?.(false);
            return;
          }
          if (event.code === "KeyP") {
            event.preventDefault();
            shuttle?.setPolygonAnnotationKeyHeld?.(true);
            return;
          }
          if (event.code === "KeyE") {
            event.preventDefault();
            if (event.repeat || shuttle?.isDockingCutsceneActive?.()) return;
            if (shuttle?.isShuttleBayPlayerControlActive?.()) {
              shuttle.interactWithShip?.();
            } else if (shuttle?.pilot?.active) {
              shuttle.setPilotMode(false, null, performance.now());
              const current = container.__mainComputerShuttle3dLook || {yaw: config.yaw, pitch: config.pitch};
              shuttle.setLook(current.yaw, current.pitch);
            } else {
              const station = shuttle?.hoveredPilotStation;
              if (station && shuttle.setPilotMode(true, station, performance.now())) {
                setShuttle3dLook(container, station.camera.yaw, station.camera.pitch, config);
              }
            }
            return;
          }
          if (event.code === "KeyT") {
            event.preventDefault();
            if (!event.repeat) shuttle?.forceShuttleBayControl?.();
            return;
          }
          if (shuttle?.pilot?.active) {
            if (pilotCodes.has(event.code)) {
              event.preventDefault();
              shuttle.setMovementKey?.(event.code, true);
              return;
            }
            if (firingCodes.has(event.code)) {
              event.preventDefault();
              return;
            }
          }
          if (firingCodes.has(event.code)) {
            event.preventDefault();
            if (!event.repeat) shuttle?.firePhaser?.();
            return;
          }
          if (event.code === "KeyR") {
            event.preventDefault();
            if (!event.repeat && shuttle?.gameOver) shuttle?.resetCombat?.();
            return;
          }
          if (movementCodes.has(event.code)) {
            event.preventDefault();
            shuttle?.setMovementKey?.(event.code, true);
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
          const shuttle = renderer();
          if (shuttle3dAnnotationDialogOpen(container)) {
            if (!shuttle3dAnnotationDialogEventTarget(event)) event.preventDefault();
            shuttle?.clearMovementKeys?.();
            shuttle?.setPolygonAnnotationKeyHeld?.(false);
            return;
          }
          if (event.code === "KeyP") {
            event.preventDefault();
            shuttle?.setPolygonAnnotationKeyHeld?.(false);
            return;
          }
          if (shuttle?.pilot?.active && pilotCodes.has(event.code)) {
            event.preventDefault();
            shuttle.setMovementKey?.(event.code, false);
            return;
          }
          if (!movementCodes.has(event.code)) return;
          event.preventDefault();
          shuttle?.setMovementKey?.(event.code, false);
        };
        const blur = () => {
          dragging = false;
          dragDistance = 0;
          delete container.dataset.shuttle3dDragging;
          renderer()?.clearMovementKeys?.();
          renderer()?.setPolygonAnnotationKeyHeld?.(false);
        };
        const handler = {pointerDown, pointerHover, pointerMove, pointerLeave, pointerUp, keyDown, keyUp, blur};
        container.__mainComputerShuttle3dLookHandler = handler;
        container.addEventListener("pointerdown", pointerDown);
        container.addEventListener("pointermove", pointerHover);
        container.addEventListener("pointerleave", pointerLeave);
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
        container.dataset.sceneState = "shuttle3d-boarding-combat";
        container.dataset.shuttle3d = "webgl-vertex-mesh";
        container.dataset.sceneLookaround = "enabled";
        container.dataset.sceneCombat = "enabled";

        const shell = document.createElement("div");
        shell.className = "scene-shuttle3d";
        shell.setAttribute("role", "application");
        shell.setAttribute(
          "aria-label",
          shuttle.controlsHint || "Vertex-built 3D shuttle defense. Walk with W A S D, look by dragging or using arrows, fire the phaser with click, Space, or F, and mouse over a console then press E to pilot the shuttle."
        );
        shell.tabIndex = 0;

        const canvas = document.createElement("canvas");
        canvas.className = "scene-shuttle3d-canvas";
        canvas.dataset.sceneObjectId = String(shuttle.viewport || "forward-viewer");
        canvas.setAttribute("role", "img");
        canvas.setAttribute(
          "aria-label",
          `${shuttle3dObjectLabel(scene, "shuttle-floor", "Shuttle interior")} rendered from real hull vertices, with stars, ${shuttle3dObjectLabel(scene, String(shuttle.motherShip || "mother-ship"), "the mother ship")}, and ${shuttle3dObjectLabel(scene, String(shuttle.alienShip || "alien-raider"), "an alien raider")} beyond the forward viewport.`
        );

        const hud = document.createElement("div");
        hud.className = "scene-shuttle3d-hud";
        hud.setAttribute("aria-live", "polite");

        const healthPanel = document.createElement("div");
        healthPanel.className = "scene-shuttle3d-health";
        const healthLabel = document.createElement("span");
        healthLabel.className = "scene-shuttle3d-health-label";
        healthLabel.textContent = "HEALTH 100 / 100";
        const healthTrack = document.createElement("span");
        healthTrack.className = "scene-shuttle3d-health-track";
        const healthFill = document.createElement("span");
        healthFill.className = "scene-shuttle3d-health-fill";
        healthTrack.append(healthFill);
        healthPanel.append(healthLabel, healthTrack);

        const combatLine = document.createElement("div");
        combatLine.className = "scene-shuttle3d-combat-line";
        combatLine.textContent = "BOARDERS 0 • KILLS 0";
        const characterLine = document.createElement("div");
        characterLine.className = "scene-shuttle3d-character-line";
        characterLine.textContent = "CHARACTERS INITIALIZING";
        const phaserLine = document.createElement("div");
        phaserLine.className = "scene-shuttle3d-phaser-line";
        phaserLine.textContent = "TYPE-II PHASER READY";
        const pilotLine = document.createElement("div");
        pilotLine.className = "scene-shuttle3d-pilot-line";
        pilotLine.textContent = "CONSOLE PILOT: MOUSE OVER A CONSOLE + E";
        const shipLine = document.createElement("div");
        shipLine.className = "scene-shuttle3d-ship-line";
        shipLine.textContent = "SHIP: SHUTTLE IN FLIGHT";
        shipLine.hidden = true;
        hud.append(healthPanel, combatLine, characterLine, phaserLine, pilotLine, shipLine);

        const crosshair = document.createElement("div");
        crosshair.className = "scene-shuttle3d-crosshair";
        crosshair.setAttribute("aria-hidden", "true");
        crosshair.append(
          document.createElement("span"),
          document.createElement("span"),
          document.createElement("span"),
          document.createElement("span")
        );

        const damageFlash = document.createElement("div");
        damageFlash.className = "scene-shuttle3d-damage-flash";
        damageFlash.setAttribute("aria-hidden", "true");

        const pilotPrompt = document.createElement("div");
        pilotPrompt.className = "scene-shuttle3d-pilot-prompt";
        pilotPrompt.hidden = true;
        pilotPrompt.textContent = "Mouse over a console and press E";

        const gameOver = document.createElement("div");
        gameOver.className = "scene-shuttle3d-game-over";
        gameOver.hidden = true;
        const gameOverTitle = document.createElement("strong");
        gameOverTitle.textContent = "CADET DOWN";
        const gameOverHint = document.createElement("span");
        gameOverHint.textContent = "Press R to restart the boarding defense.";
        gameOver.append(gameOverTitle, gameOverHint);

        const hint = document.createElement("div");
        hint.className = "scene-shuttle3d-look-hint";
        hint.textContent = shuttle.controlsHint || "Bridge console + E navigation • W/A/S/D move during warp • hold P + click geometry to annotate • Click/Space/F fire";

        const annotationHint = document.createElement("div");
        annotationHint.className = "scene-shuttle3d-annotation-hint";
        annotationHint.dataset.shuttle3dAnnotationHint = "true";
        annotationHint.hidden = true;
        annotationHint.textContent = "Hold P and click a polygon or object to annotate it.";

        const status = document.createElement("div");
        status.className = "scene-shuttle3d-mesh-status";
        status.textContent = "Building shuttle combat vertices…";

        const twiddleSystem = document.createElement("div");
        twiddleSystem.className = "scene-shuttle3d-twiddle-system";
        twiddleSystem.setAttribute("role", "region");
        twiddleSystem.setAttribute("aria-label", "Shuttle bay twiddle system");
        const twiddleTitle = document.createElement("strong");
        twiddleTitle.textContent = "Shuttle Bay Twiddle System";
        const twiddleStatus = document.createElement("span");
        twiddleStatus.className = "scene-shuttle3d-twiddle-status";
        twiddleStatus.textContent = "Awaiting docking handoff.";
        const twiddleButton = document.createElement("button");
        twiddleButton.type = "button";
        twiddleButton.className = "scene-shuttle3d-twiddle-button";
        twiddleButton.textContent = "Twiddle: Give Player Control";
        twiddleButton.title = "Force first-person control in the mother ship shuttle bay.";
        twiddleSystem.append(twiddleTitle, twiddleStatus, twiddleButton);

        const navigationPanel = document.createElement("section");
        navigationPanel.className = "scene-shuttle3d-navigation-panel";
        navigationPanel.hidden = true;
        navigationPanel.setAttribute("aria-label", "Bridge warp navigation");
        const navigationHeader = document.createElement("div");
        navigationHeader.className = "scene-shuttle3d-navigation-header";
        const navigationTitle = document.createElement("strong");
        navigationTitle.textContent = "Bridge Navigation";
        const navigationClose = document.createElement("button");
        navigationClose.type = "button";
        navigationClose.className = "scene-shuttle3d-navigation-close";
        navigationClose.textContent = "×";
        navigationClose.setAttribute("aria-label", "Close navigation");
        navigationHeader.append(navigationTitle, navigationClose);
        const navigationCurrent = document.createElement("div");
        navigationCurrent.className = "scene-shuttle3d-navigation-current";
        navigationCurrent.textContent = "CURRENT SYSTEM: LOADING";
        const navigationTime = document.createElement("div");
        navigationTime.className = "scene-shuttle3d-navigation-time";
        navigationTime.textContent = "WORLD TIME 0";
        const navigationScrawl = document.createElement("div");
        navigationScrawl.className = "scene-shuttle3d-navigation-scrawl";
        navigationScrawl.hidden = true;
        const navigationLabel = document.createElement("label");
        navigationLabel.className = "scene-shuttle3d-navigation-label";
        navigationLabel.textContent = "Adjacent destination";
        const navigationSelect = document.createElement("select");
        navigationSelect.className = "scene-shuttle3d-navigation-select";
        navigationLabel.append(navigationSelect);
        const navigationActions = document.createElement("div");
        navigationActions.className = "scene-shuttle3d-navigation-actions";
        const navigationPlot = document.createElement("button");
        navigationPlot.type = "button";
        navigationPlot.textContent = "Plot Course";
        const navigationEngage = document.createElement("button");
        navigationEngage.type = "button";
        navigationEngage.className = "scene-shuttle3d-navigation-engage";
        navigationEngage.textContent = "Engage Warp";
        navigationActions.append(navigationPlot, navigationEngage);
        const navigationProgress = document.createElement("div");
        navigationProgress.className = "scene-shuttle3d-navigation-progress";
        const navigationProgressFill = document.createElement("span");
        navigationProgress.append(navigationProgressFill);
        const navigationStatus = document.createElement("div");
        navigationStatus.className = "scene-shuttle3d-navigation-status";
        navigationStatus.textContent = "Select an adjacent system.";
        navigationPanel.append(
          navigationHeader,
          navigationCurrent,
          navigationTime,
          navigationScrawl,
          navigationLabel,
          navigationActions,
          navigationProgress,
          navigationStatus
        );

        const warpOverlay = document.createElement("div");
        warpOverlay.className = "scene-shuttle3d-warp-overlay";
        warpOverlay.hidden = true;
        warpOverlay.setAttribute("aria-hidden", "true");
        const warpReadout = document.createElement("div");
        warpReadout.className = "scene-shuttle3d-warp-readout";
        warpReadout.textContent = "WARP DRIVE STANDBY";
        warpOverlay.append(warpReadout);

        shell.append(canvas, warpOverlay, hud, crosshair, pilotPrompt, damageFlash, gameOver, hint, annotationHint, status, navigationPanel, twiddleSystem);
        container.append(shell);
        bindShuttle3dLookaround(container, scene, options);

        try {
          const renderer = new Shuttle3dVertexRenderer(canvas, scene, options);
          container.__mainComputerShuttle3dRenderer = renderer;
          const current = container.__mainComputerShuttle3dLook || shuttle3dCameraConfig(scene);
          renderer.setLook(current.yaw, current.pitch);
          canvas.dataset.shuttleVertexCount = String(renderer.vertexCount);
          canvas.dataset.starfieldMode = renderer.starfield.mode;
          canvas.dataset.starfieldCount = String(renderer.starfield.count);
          canvas.dataset.starfieldRadius = String(renderer.starfield.radius);
          canvas.dataset.combatEnabled = String(renderer.combat.enabled);
          canvas.dataset.pilotStations = String(renderer.pilotStations.length);
          canvas.dataset.pilotMode = "inactive";
          canvas.dataset.alienShip = renderer.combat.alienShip.id;
          let lastHealth = renderer.combat.player.startingHealth;
          let damageTimer = 0;
          let navigationDestinationSignature = "";
          navigationClose.addEventListener("click", () => renderer.setNavigationConsoleOpen?.(false));
          navigationPlot.addEventListener("click", () => {
            renderer.plotWarpCourse?.(navigationSelect.value);
            renderer.emitNavigationState?.(true);
          });
          navigationEngage.addEventListener("click", () => {
            renderer.engageWarp?.(performance.now());
            renderer.emitNavigationState?.(true);
          });
          const updateNavigationHud = (navigation = renderer.navigationSnapshot()) => {
            const destinations = Array.isArray(navigation.destinations) ? navigation.destinations : [];
            const signature = destinations.map((destination) => `${destination.routeId}:${destination.systemId}`).join("|");
            if (signature !== navigationDestinationSignature) {
              const selected = navigation.destinationSystemId || navigationSelect.value;
              navigationSelect.replaceChildren();
              destinations.forEach((destination) => {
                const option = document.createElement("option");
                option.value = destination.systemId;
                const worldCount = Math.max(1, Number(destination.planetCount || 1));
                const starCount = Math.max(1, Number(destination.starCount || 1));
                const astronomy = worldCount > 1 || starCount > 1
                  ? ` • ${starCount} stars • ${worldCount} worlds`
                  : ` • ${destination.planetLabel || "unknown planet"}`;
                option.textContent = `${destination.label}${astronomy} • ${destination.worldTimeCost} time`;
                option.dataset.routeId = destination.routeId;
                navigationSelect.append(option);
              });
              if (destinations.some((destination) => destination.systemId === selected)) navigationSelect.value = selected;
              navigationDestinationSignature = signature;
            }
            navigationPanel.hidden = !navigation.consoleOpen;
            const currentWorldCount = Math.max(1, Number(navigation.currentPlanetCount || 1));
            const currentStarCount = Math.max(1, Number(navigation.currentStarCount || 1));
            navigationCurrent.textContent = `CURRENT SYSTEM: ${String(navigation.currentSystemLabel || "UNKNOWN").toUpperCase()} • STARS: ${currentStarCount} • WORLDS: ${currentWorldCount}`;
            navigationTime.textContent = `WORLD TIME ${Number(navigation.elapsedWorldTime || 0)} • PHASE ${String(navigation.travelPhase || "unknown").replace(/-/g, " ").toUpperCase()}`;
            const scrawl = String(navigation.captainScrawl || "").trim();
            const showScrawl = Boolean(scrawl) && (navigation.travelPhase === "course-plotted" || navigation.travelling);
            navigationScrawl.hidden = !showScrawl;
            navigationScrawl.textContent = showScrawl ? `CAPTAIN'S SCRAWL • ${scrawl}` : "";
            navigationSelect.disabled = !navigation.enabled || navigation.travelling || !destinations.length;
            navigationPlot.disabled = navigationSelect.disabled;
            navigationEngage.disabled = !navigation.enabled || navigation.travelPhase !== "course-plotted" || navigation.travelling;
            navigationProgressFill.style.width = `${Math.round(Number(navigation.travelProgress || 0) * 100)}%`;
            const destination = navigation.destinationSystemLabel || "";
            if (navigation.error) navigationStatus.textContent = navigation.error;
            else if (navigation.travelling) navigationStatus.textContent = `${String(navigation.travelPhase).replace(/-/g, " ").toUpperCase()} TO ${destination.toUpperCase()} • ${Math.round(Number(navigation.travelProgress || 0) * 100)}%`;
            else if (navigation.travelPhase === "course-plotted") navigationStatus.textContent = `COURSE LOCKED: ${destination.toUpperCase()} • READY TO ENGAGE`;
            else navigationStatus.textContent = `${destinations.length} ADJACENT ${destinations.length === 1 ? "SYSTEM" : "SYSTEMS"} AVAILABLE`;
            warpOverlay.hidden = !navigation.travelling;
            warpReadout.textContent = navigation.travelling
              ? `${String(navigation.travelPhase).replace(/-/g, " ").toUpperCase()} • ${destination.toUpperCase()} • ${Math.round(Number(navigation.travelProgress || 0) * 100)}%`
              : "WARP DRIVE STANDBY";
            shell.dataset.warpPhase = String(navigation.travelPhase || "unavailable");
            shell.dataset.currentSystem = String(navigation.currentSystemId || "");
            shell.dataset.warpDestination = String(navigation.destinationSystemId || "");
            shell.dataset.worldTime = String(navigation.elapsedWorldTime || 0);
            canvas.dataset.currentSystem = String(navigation.currentSystemId || "");
            canvas.dataset.warpPhase = String(navigation.travelPhase || "unavailable");
            canvas.dataset.worldTime = String(navigation.elapsedWorldTime || 0);
            if (typeof options.onNavigationChanged === "function") {
              try {
                options.onNavigationChanged({...navigation});
              } catch (error) {
                console.error("Game Surface navigation callback failed", error);
              }
            }
          };
          const updateTwiddleSystem = (pilot = renderer.pilotSnapshot()) => {
            const progress = Math.round((pilot.dockingCutsceneProgress || 0) * 100);
            const phase = (pilot.dockingCutscenePhase || "idle").replace(/-/g, " ").toUpperCase();
            twiddleSystem.dataset.cutscene = pilot.dockingCutsceneActive ? "active" : (pilot.playerExitedToBay ? "complete" : "inactive");
            twiddleSystem.dataset.playerControl = pilot.shuttleBayControlActive ? "active" : "inactive";
            twiddleSystem.hidden = !(pilot.dockingCutsceneActive || (!pilot.shuttleBayControlActive && (pilot.playerExitedToBay || pilot.flightDocked)));
            if (pilot.shuttleBayControlActive) {
              twiddleStatus.textContent = `Control restored in ${pilot.shuttleBayLabel}.`;
              twiddleButton.disabled = true;
            } else if (pilot.dockingCutsceneActive) {
              twiddleStatus.textContent = `Docking ${phase} • ${progress}% • press T if handoff sticks.`;
              twiddleButton.disabled = false;
            } else if (pilot.playerExitedToBay || pilot.flightDocked) {
              twiddleStatus.textContent = `Docking handoff pending • press T to restore player control.`;
              twiddleButton.disabled = false;
            } else {
              twiddleStatus.textContent = "Awaiting docking handoff.";
              twiddleButton.disabled = false;
            }
          };
          twiddleButton.addEventListener("click", () => {
            renderer.forceShuttleBayControl?.();
            updateTwiddleSystem(renderer.pilotSnapshot());
          });
          const updateMovementStatus = (camera) => {
            const combat = renderer.combatSnapshot();
            const pilot = renderer.pilotSnapshot();
            const pilotText = pilot.active ? ` • piloting ${pilot.stationLabel}` : "";
            const bayText = pilot.shuttleBayControlActive ? ` • ${pilot.shuttleBayLabel}` : "";
            status.textContent = `${renderer.worldVertexCount.toLocaleString()} fixed vertices • ${renderer.starfield.count} sphere stars • ${combat.alive} boarders${pilotText}${bayText} • x ${camera[0].toFixed(1)} • z ${camera[2].toFixed(1)}`;
            canvas.dataset.cameraX = camera[0].toFixed(3);
            canvas.dataset.cameraZ = camera[2].toFixed(3);
          };
          const updateCombatHud = (combat) => {
            const ratio = combat.maxHealth > 0 ? Math.max(0, Math.min(1, combat.health / combat.maxHealth)) : 0;
            healthLabel.textContent = `HEALTH ${combat.health} / ${combat.maxHealth}`;
            healthFill.style.width = `${(ratio * 100).toFixed(1)}%`;
            healthPanel.dataset.healthState = combat.health <= combat.maxHealth * 0.25
              ? "critical"
              : combat.health <= combat.maxHealth * 0.55
                ? "warning"
                : "nominal";
            const transportText = combat.transporting ? ` • ${combat.transporting} TRANSPORTING` : "";
            combatLine.textContent = `BOARDERS ${combat.active}${transportText} • KILLS ${combat.kills}`;
            phaserLine.textContent = combat.paused
              ? `BOARDERS PAUSED • ${combat.pilotStation || "PILOT CONTROL"}`
              : combat.phaserReady
                ? "TYPE-II PHASER READY"
                : `PHASER RECHARGING ${combat.cooldownRemainingMs}ms`;
            phaserLine.dataset.phaserReady = String(combat.phaserReady);
            phaserLine.dataset.combatPaused = String(combat.paused);
            gameOver.hidden = !combat.gameOver;
            shell.dataset.playerState = combat.gameOver ? "defeated" : "active";
            canvas.dataset.playerHealth = String(combat.health);
            canvas.dataset.alienCount = String(combat.alive);
            canvas.dataset.killCount = String(combat.kills);
            if (combat.health < lastHealth) {
              shell.classList.remove("scene-shuttle3d--damaged");
              void shell.offsetWidth;
              shell.classList.add("scene-shuttle3d--damaged");
              window.clearTimeout(damageTimer);
              damageTimer = window.setTimeout(() => shell.classList.remove("scene-shuttle3d--damaged"), 260);
            }
            lastHealth = combat.health;
            updateMovementStatus(renderer.camera);
          };
          const updateCharacterAIHud = (snapshot) => {
            window.MainComputerPaxScenarioInteraction?.setWorldSnapshot?.(snapshot || null);
            const characters = Array.isArray(snapshot?.characters)
              ? snapshot.characters
              : [];
            characterLine.hidden = !snapshot?.enabled;
            if (!snapshot?.enabled) {
              characterLine.textContent = snapshot?.error
                ? `CHARACTER AI UNAVAILABLE • ${snapshot.error}`
                : "CHARACTER AI UNAVAILABLE";
              return;
            }
            const labels = characters.map((character) => {
              const health = Math.max(0, Math.round(Number(character.health || 0)));
              const maxHealth = Math.max(1, Math.round(Number(character.maxHealth || 1)));
              const action = String(character.currentActionId || character.actionId || "hold_position")
                .replace(/_/g, " ")
                .toUpperCase();
              const prefix = character.kind === "enemy" ? "HOSTILE" : "ALLY";
              return `${prefix}: ${String(character.label || character.id)} ${health}/${maxHealth} • ${action}`;
            });
            const threatCount = Math.max(0, Number(snapshot?.activeThreatCount || 0));
            characterLine.textContent = labels.length
              ? `${threatCount ? `ACTIVE THREATS ${threatCount} • ` : ""}${labels.join(" || ")}`
              : `CHARACTER AI • ${String(snapshot.phase || "inactive").toUpperCase()}`;
            characterLine.dataset.characterCount = String(characters.length);
            characterLine.dataset.characterPhase = String(snapshot.phase || "");
            canvas.dataset.characterCount = String(characters.length);
          };

          const updateShipHud = (ship) => {
            const visible = Boolean(ship?.enabled && renderer.pilotSnapshot().playerExitedToBay);
            shipLine.hidden = !visible;
            if (!visible) return;
            const location = String(ship.locationLabel || ship.location || "Mother Ship");
            const objective = String(ship.objectiveLabel || ship.objectiveId || "Awaiting objective");
            const interaction = ship.interactionHint ? ` • ${ship.interactionHint}` : "";
            const statusText = ship.interactionStatus ? ` • ${ship.interactionStatus}` : "";
            shipLine.textContent = `SHIP ${location} • ${String(ship.power || "unknown").toUpperCase()} POWER • ${String(ship.security || "unknown").toUpperCase()} • OBJECTIVE: ${objective}${interaction}${statusText}`;
            canvas.dataset.shipLocation = String(ship.location || "");
            canvas.dataset.shipObjective = String(ship.objectiveId || "");
            canvas.dataset.shipInteraction = String(ship.interactionId || "");
            shell.dataset.shipLocation = String(ship.location || "");
            shell.dataset.shipObjective = String(ship.objectiveId || "");
            shell.dataset.shipInteraction = String(ship.interactionId || "");
            if (ship.interactionHint) {
              pilotPrompt.hidden = false;
              pilotPrompt.textContent = ship.interactionStatus
                ? `${ship.interactionHint} • ${ship.interactionStatus}`
                : ship.interactionHint;
            }
          };

          const updatePilotHud = (pilot) => {
            canvas.dataset.pilotMode = pilot.active ? "active" : "inactive";
            canvas.dataset.hoveredPilotStation = pilot.hoverId;
            canvas.dataset.activePilotStation = pilot.stationId;
            canvas.dataset.flightDocked = pilot.flightDocked ? "true" : "false";
            canvas.dataset.dockingCutscene = pilot.dockingCutsceneActive ? "active" : (pilot.playerExitedToBay ? "finished" : "inactive");
            canvas.dataset.shuttleBayScene = pilot.playerExitedToBay ? "active" : "inactive";
            canvas.dataset.shuttleBayControl = pilot.shuttleBayControlActive ? "active" : "inactive";
            shell.dataset.pilotMode = pilot.active ? "active" : "inactive";
            shell.dataset.hoveredPilotStation = pilot.hoverId;
            shell.dataset.flightDocked = pilot.flightDocked ? "true" : "false";
            shell.dataset.dockingCutscene = pilot.dockingCutsceneActive ? "active" : (pilot.playerExitedToBay ? "finished" : "inactive");
            shell.dataset.shuttleBayScene = pilot.playerExitedToBay ? "active" : "inactive";
            shell.dataset.shuttleBayControl = pilot.shuttleBayControlActive ? "active" : "inactive";
            updateTwiddleSystem(pilot);
            if (pilot.dockingCutsceneActive) {
              const progress = Math.round(pilot.dockingCutsceneProgress * 100);
              pilotLine.textContent = `DOCKING CUTSCENE • ${pilot.dockingCutscenePhase.replace(/-/g, " ").toUpperCase()} • ${progress}%`;
              pilotPrompt.hidden = false;
              pilotPrompt.textContent = `Autopilot docking with ${pilot.targetLabel}: shuttle entering bay, landing, and cadet exiting`;
            } else if (pilot.playerExitedToBay) {
              pilotLine.textContent = `ARRIVED: ${pilot.shuttleBayLabel} • FIRST-PERSON CONTROL`;
              const shipTarget = renderer.shipInteractionTarget?.();
              if (shipTarget) {
                pilotPrompt.hidden = false;
                pilotPrompt.textContent = renderer.shipInteractionHint(shipTarget);
              } else {
                pilotPrompt.hidden = true;
                pilotPrompt.textContent = `W/A/S/D to walk the ${pilot.shuttleBayLabel} • drag or arrows to look`;
              }
            } else if (pilot.active) {
              const range = pilot.flightDocked ? `DOCKED WITH ${pilot.targetLabel}` : `RANGE ${pilot.flightDistance.toFixed(1)} TO ${pilot.targetLabel}`;
              pilotLine.textContent = `PILOTING ${pilot.stationLabel} • ${range} • SPEED ${pilot.flightSpeed.toFixed(1)} • E EXIT`;
              pilotPrompt.hidden = false;
              pilotPrompt.textContent = pilot.flightDocked
                ? `Docked with ${pilot.targetLabel} — docking cutscene starting`
                : `W/S throttle flies to ${pilot.targetLabel} • A/D steer • E exit`;
            } else if (pilot.hoverId) {
              pilotLine.textContent = `READY: ${pilot.hoverLabel} • PRESS E TO PILOT`;
              pilotPrompt.hidden = false;
              pilotPrompt.textContent = `Press E to take ${pilot.hoverLabel}`;
            } else {
              pilotLine.textContent = "CONSOLE PILOT: MOUSE OVER A CONSOLE + E";
              pilotPrompt.hidden = true;
              pilotPrompt.textContent = "Mouse over a console and press E";
            }
            updateMovementStatus(renderer.camera);
          };
          renderer.onBayControlStarted = (bay) => {
            const bayLookConfig = shuttle3dCameraConfig(scene);
            setShuttle3dLook(container, bay.yaw, bay.pitch, bayLookConfig);
            updateMovementStatus(bay.position || renderer.camera);
          };
          renderer.onCameraMoved = updateMovementStatus;
          renderer.onCombatChanged = updateCombatHud;
          renderer.onCharacterAIChanged = updateCharacterAIHud;
          renderer.onPilotChanged = updatePilotHud;
          renderer.onShipStateChanged = updateShipHud;
          renderer.onNavigationChanged = updateNavigationHud;
          updateMovementStatus(renderer.camera);
          renderer.emitPilotState(true);
          renderer.emitNavigationState(true);
          renderer.emitCombatState(true);
          renderer.emitCharacterAIState(true);
          renderer.emitShipState(true);
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
