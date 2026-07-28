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
              label: "Proceed to the bridge and identify the enemy ship on the viewscreen.",
              location: "corridor.main"
            },
            "objective.bridge-screen": {
              label: "Use the bridge viewscreen to track the enemy ship.",
              location: "bridge.deck"
            },
            "objective.enemy-track": {
              label: "Enemy ship tracked on the bridge tactical display.",
              location: "bridge.deck"
            },
            "objective.enemy-attack": {
              label: "Use the bridge tactical console to fire on the enemy ship.",
              location: "bridge.deck"
            },
            "objective.enemy-disabled": {
              label: "Enemy raider disabled. Hold the bridge and await next orders.",
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
              label: "Bridge Tactical Console",
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
              id: "bay.shuttle",
              name: "Mother Ship Shuttle Bay",
              location: "bay.shuttle",
              kind: "shuttle-bay",
              priority: 100,
              bounds: {minX: -4.72, maxX: 4.72, minZ: -4.62, maxZ: 5.12}
            },
            {
              id: "bay.ops",
              name: "Bay Operations",
              location: "bay.ops",
              kind: "operations",
              priority: 60,
              bounds: {minX: -2.25, maxX: 4.9, minZ: -9.45, maxZ: -4.2}
            },
            {
              id: "security.checkpoint",
              name: "Security Checkpoint",
              location: "security.checkpoint",
              kind: "checkpoint",
              priority: 75,
              bounds: {minX: -3.2, maxX: 3.2, minZ: -13.55, maxZ: -8.75}
            },
            {
              id: "corridor.main",
              name: "Main Corridor Hub",
              location: "corridor.main",
              kind: "corridor",
              priority: 40,
              bounds: {minX: -6.55, maxX: 6.55, minZ: -18.75, maxZ: -13.25}
            },
            {
              id: "corridor.trunk",
              name: "Main Corridor Trunk",
              location: "corridor.main",
              kind: "corridor",
              priority: 45,
              bounds: {minX: -2.55, maxX: 2.55, minZ: -25.85, maxZ: -13.25}
            },
            {
              id: "engineering.access",
              name: "Engineering Access",
              location: "engineering.access",
              kind: "engineering",
              priority: 80,
              bounds: {minX: 2.0, maxX: 9.8, minZ: -24.25, maxZ: -17.15}
            },
            {
              id: "medbay.stub",
              name: "Medbay Triage",
              location: "medbay.stub",
              kind: "medbay",
              priority: 80,
              bounds: {minX: -9.8, maxX: -2.0, minZ: -24.25, maxZ: -17.15}
            },
            {
              id: "science.ops.stub",
              name: "Science/Ops Lab",
              location: "science.ops.stub",
              kind: "science",
              priority: 80,
              bounds: {minX: -9.8, maxX: -2.0, minZ: -31.5, maxZ: -24.0}
            },
            {
              id: "bridge.access",
              name: "Bridge Access",
              location: "bridge.access",
              kind: "bridge-access",
              priority: 90,
              bounds: {minX: -2.9, maxX: 2.9, minZ: -32.25, maxZ: -25.45}
            },
            {
              id: "bridge.deck",
              name: "Bridge Deck",
              location: "bridge.deck",
              kind: "bridge",
              priority: 110,
              bounds: {minX: -4.65, maxX: 4.65, minZ: -39.35, maxZ: -31.25}
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
              label: "Bridge Tactical Console",
              location: "bridge.deck",
              position: [2.85, -36.7],
              range: 1.85,
              action: "fireBridgeTacticalConsole",
              prompt: "Press E to fire Bridge Tactical Console."
            },
            {
              id: "terminal.bridge-viewscreen",
              kind: "terminal",
              label: "Bridge Viewscreen",
              location: "bridge.deck",
              position: [0.0, -37.15],
              range: 2.45,
              action: "trackEnemyShipOnViewscreen",
              prompt: "Press E to use Bridge Viewscreen."
            }
          ]
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
              bounds
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

      function shuttle3dNormalizeMotherShipMovement(value, fallbackMovement, rooms) {
        const supplied = shuttle3dObjectValue(value);
        const fallback = shuttle3dObjectValue(fallbackMovement);
        const bounds = shuttle3dBoundsValue(supplied.bounds, shuttle3dMovementBoundsFromRooms(rooms, fallback.bounds));
        const colliders = (
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
              ...normalized
            };
          })
          .filter(Boolean);
        return {bounds, colliders};
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
              prompt: String(raw.prompt || fallback.prompt || "")
            };
          })
          .filter(Boolean);
      }


      function shuttle3dMotherShipInteriorConfig(scene) {
        const supplied = scene?.metadata?.shuttle3d?.motherShipInterior;
        const interior = supplied && typeof supplied === "object" ? supplied : {};
        const defaults = shuttle3dMotherShipInteriorStateDefaults();
        const levelDefaults = shuttle3dMotherShipInteriorLevelDefaults();
        const suppliedStateDefaults = shuttle3dObjectValue(interior.stateDefaults);

        const locations = shuttle3dStringMap(interior.locations, defaults.locations);
        const objectives = shuttle3dObjectMap(interior.objectives, defaults.objectives);
        const rooms = shuttle3dNormalizeMotherShipRooms(interior.rooms, levelDefaults.rooms, locations);
        const roomMap = shuttle3dRoomMap(rooms);
        const exits = shuttle3dNormalizeMotherShipExits(interior.exits, levelDefaults.exits);
        const movement = shuttle3dNormalizeMotherShipMovement(interior.movement, levelDefaults.movement, rooms);
        const spawns = shuttle3dNormalizeMotherShipSpawns(interior.spawns, levelDefaults.spawns, locations);
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
        const flags = shuttle3dNormalizeMotherShipFlags({
          ...defaults.flags,
          ...shuttle3dObjectValue(interior.flags),
          ...shuttle3dObjectValue(suppliedStateDefaults.flags)
        });

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
          location: locations[initialLocation] ? initialLocation : defaults.location,
          objectiveId: objectives[initialObjective] ? initialObjective : defaults.objectiveId,
          power: String(suppliedStateDefaults.power || interior.power || defaults.power),
          security: String(suppliedStateDefaults.security || interior.security || defaults.security),
          doors: shuttle3dCloneJson(doors),
          terminals: shuttle3dCloneJson(terminals),
          flags: shuttle3dCloneJson(flags),
          lastInteractionStatus: ""
        };

        return {
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
          interactables: shuttle3dCloneJson(interactables),
          doors: shuttle3dCloneJson(stateDefaults.doors),
          terminals: shuttle3dCloneJson(stateDefaults.terminals),
          flags: shuttle3dCloneJson(stateDefaults.flags),
          stateDefaults
        };
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
          this.combat = shuttle3dCombatConfig(scene);
          this.flightConfig = shuttle3dFlightConfig(scene);
          this.interiorConfig = shuttle3dMotherShipInteriorConfig(scene);
          this.flight = this.createFlightState();
          this.shipState = this.createShipState();
          this.pilotStations = shuttle3dPilotStationsConfig(scene);
          this.hoveredPilotStation = null;
          this.pilot = {
            active: false,
            station: null,
            throttle: 0,
            heading: 0,
            pitch: 0,
            roll: 0,
            impulse: 0
          };
          this.combatPauseStartedAtMs = null;
          this.lastPilotUiAt = -Infinity;
          this.lastShipUiAt = -Infinity;
          this.onPilotChanged = null;
          this.onBayControlStarted = null;
          this.onShipStateChanged = null;
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
          return state === "tracking" || Boolean(this.shipState?.flags?.bridgeViewscreenTrackingActive);
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
            this.setShipTerminalState("terminal.bridge-tactical", "disabled-target");
            this.setShipObjective("objective.enemy-disabled", true);
            this.setShipInteractionStatus("Bridge tactical console reports the enemy raider is already disabled.");
            this.emitShipState(true);
            return true;
          }
          const nextHull = Math.max(0, currentHull - 35);
          flags.enemyShipHullPercent = nextHull;
          if (nextHull <= 0) {
            flags.enemyShipDisabled = true;
            this.setShipTerminalState("terminal.bridge-tactical", "disabled-target");
            this.setShipObjective("objective.enemy-disabled", true);
            this.setShipInteractionStatus("Bridge tactical console fired final volley. Enemy raider disabled.");
          } else {
            flags.enemyShipDisabled = false;
            this.setShipObjective("objective.enemy-attack", true);
            this.setShipInteractionStatus(`Bridge tactical console fired. Enemy raider hull ${Math.round(nextHull)}%.`);
          }
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
            this.setShipObjective(
              this.enemyShipDisabled()
                ? "objective.enemy-disabled"
                : this.bridgeViewscreenTrackingActive()
                  ? "objective.enemy-attack"
                  : "objective.bridge-screen"
            );
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
          if (target.prompt) return String(target.prompt);
          if (target.kind === "access") return `Press E to enter through ${target.label}.`;
          if (target.kind === "terminal") return `Press E to use ${target.label}.`;
          return `Press E to inspect ${target.label}.`;
        }

        performShipInteractionAction(target) {
          const action = String(target?.action || target?.interaction || "");
          switch (action) {
            case "enterBayOpsAccess":
              return this.enterBayOpsAccess();
            case "activateBayOperationsTerminal":
              this.setShipTerminalState("terminal.bay-ops", "online");
              this.setShipDoorState("door.bay-inner", "open");
              this.setShipObjective("objective.enter-corridor");
              if (this.shipState?.flags) this.shipState.flags.bayOpsTerminalUsed = true;
              this.setShipInteractionStatus("Bay Operations online. Route to Security Checkpoint is available.");
              return true;
            case "restoreEngineeringPower":
              this.setShipTerminalState("terminal.engineering-power", "online");
              this.shipState.power = "online";
              this.shipState.security = "yellow-alert";
              this.setShipDoorState("door.bridge", "open");
              this.setShipObjective("objective.bridge-access");
              if (this.shipState?.flags) this.shipState.flags.engineeringPowerRestored = true;
              this.setShipInteractionStatus("Engineering restored main power. Bridge route confirmed open.");
              return true;
            case "trackEnemyShipOnViewscreen":
              this.setShipTerminalState("terminal.bridge-viewscreen", "tracking");
              this.setShipObjective(this.enemyShipDisabled() ? "objective.enemy-disabled" : "objective.enemy-attack", true);
              if (this.shipState?.flags) {
                this.shipState.flags.enemyShipOnBridgeViewscreen = true;
                this.shipState.flags.bridgeViewscreenTrackingActive = true;
                this.shipState.flags.bridgeViewscreenInteractedAtMs = Math.round(this.lastFrameTime || 0);
              }
              this.setShipInteractionStatus("Bridge tactical lock engaged. Enemy raider is tracked on the main viewscreen. Use the Bridge Tactical Console to fire.");
              this.emitShipState(true);
              return true;
            case "fireBridgeTacticalConsole":
              return this.fireBridgeTacticalConsole();
            case "inspectOpenDoorRoute":
              if (this.shipDoorState(target.id) !== "open") this.setShipDoorState(target.id, "open");
              if (target.id === "door.bay-inner" || target.id === "door.security-hub") this.setShipObjective("objective.restore-power");
              if (target.id === "door.engineering-access" || target.id === "door.medbay" || target.id === "door.science") this.setShipObjective("objective.survey-departments");
              if (target.id === "door.bridge") this.setShipObjective("objective.bridge-screen");
              this.setShipInteractionStatus(`${target.label} route is open. No door lock is required.`);
              return true;
            default:
              return false;
          }
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
          this.clearMovementKeys();
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

        isBoardingPaused() {
          return Boolean(this.pilot?.active || this.isDockingSceneActive());
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


        appendShuttleBayScene(builder, nowMs = 0) {
          const deck = builder.color("#1e293b");
          const deckDark = builder.color("#0f172a");
          const wall = builder.color("#334155");
          const bulkhead = builder.color("#475569");
          const rail = builder.color("#64748b");
          const light = builder.color("#67e8f9", true);
          const green = builder.color("#86efac", true);
          const amber = builder.color("#fbbf24", true);
          const red = builder.color("#ef4444", true);
          const blue = builder.color("#38bdf8", true);
          const screenGlow = builder.color("#0ea5e9", true);
          const med = builder.color("#fca5a5", true);
          const sci = builder.color("#a78bfa", true);
          const terminal = builder.color("#0f766e");
          const doorMaterial = builder.color("#475569");
          const openDoor = builder.color("#22c55e", true);
          const closedDoor = builder.color("#f59e0b", true);
          const attentionDoor = builder.color("#f59e0b", true);
          const pulse = 0.55 + 0.45 * Math.sin((nowMs || 0) / 320);

          const roomShell = (minX, maxX, minZ, maxZ, accent = light) => {
            builder.box([minX, -1.2, minZ], [maxX, -1.12, maxZ], deck);
            builder.box([minX, 2.72, minZ], [maxX, 3.02, maxZ], deckDark);
            builder.beam([minX + 0.45, 2.56, minZ + 0.42], [maxX - 0.45, 2.56, minZ + 0.42], 0.018, accent);
            builder.beam([minX + 0.45, 2.56, maxZ - 0.42], [maxX - 0.45, 2.56, maxZ - 0.42], 0.018, accent);
          };

          const wallX = (x, minZ, maxZ) => builder.box([x - 0.15, -1.2, minZ], [x + 0.15, 2.85, maxZ], wall);
          const wallZ = (z, minX, maxX) => builder.box([minX, -1.2, z - 0.15], [maxX, 2.85, z + 0.15], wall);
          const doorStateColor = (doorId) => {
            const state = this.shipDoorState(doorId);
            if (state === "open") return openDoor;
            if (state === "closed") return attentionDoor;
            return closedDoor;
          };
          const doorPanel = (doorId, centerX, centerZ, width, vertical = false) => {
            const color = doorStateColor(doorId);
            const state = this.shipDoorState(doorId);
            if (vertical) {
              if (state !== "open") builder.box([centerX - 0.12, -1.05, centerZ - width / 2], [centerX + 0.12, 2.42, centerZ + width / 2], doorMaterial);
              builder.beam([centerX, 0.35, centerZ - width / 2], [centerX, 0.35, centerZ + width / 2], 0.03, color);
              builder.beam([centerX, 2.02, centerZ - width / 2], [centerX, 2.02, centerZ + width / 2], 0.03, color);
            } else {
              if (state !== "open") builder.box([centerX - width / 2, -1.05, centerZ - 0.12], [centerX + width / 2, 2.42, centerZ + 0.12], doorMaterial);
              builder.beam([centerX - width / 2, 0.35, centerZ], [centerX + width / 2, 0.35, centerZ], 0.03, color);
              builder.beam([centerX - width / 2, 2.02, centerZ], [centerX + width / 2, 2.02, centerZ], 0.03, color);
            }
          };
          const terminalBlock = (terminalId, centerX, centerZ, color = blue) => {
            const state = String(this.shipState?.terminals?.[terminalId]?.state || "").toLowerCase();
            const online = state === "online" || state === "tracking";
            const glow = online ? green : color;
            builder.consoleWedge(centerX, centerZ, 1.0, 0.72, -1.08, -0.36, 0.18, terminal);
            builder.box([centerX - 0.36, 0.16, centerZ - 0.32], [centerX + 0.36, 0.5, centerZ + 0.1], glow);
            builder.beam([centerX - 0.48, 0.62, centerZ - 0.42], [centerX + 0.48, 0.62, centerZ - 0.42], 0.02 + pulse * 0.01, glow);
          };
          const mapMarker = (x, z, color) => {
            builder.box([x - 0.18, -1.055, z - 0.18], [x + 0.18, -0.94, z + 0.18], color);
            builder.beam([x, -0.82, z], [x, -0.16, z], 0.018, color);
          };

          // Mother Ship Shuttle Bay
          roomShell(-5.2, 5.2, -5.25, 6.2, light);
          wallX(-5.2, -5.25, 6.2);
          wallX(5.2, -5.25, 6.2);
          wallZ(5.72, -5.25, -2.75);
          wallZ(5.72, 2.75, 5.25);
          wallZ(5.72, -1.65, 1.65);
          wallZ(-5.05, -5.2, -0.82);
          wallZ(-5.05, 0.82, 2.15);
          wallZ(-5.05, 4.45, 5.2);
          doorPanel("door.bay-access", 3.3, -5.04, 2.22, false);
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
          roomShell(-2.35, 5.05, -9.65, -4.35, blue);
          wallX(5.05, -9.65, -4.35);
          wallX(-2.35, -9.65, -8.1);
          // Segment the forward Bay Ops bulkhead so the central transit spine is a real visible opening.
          wallZ(-9.65, -2.35, -1.14);
          wallZ(-9.65, 1.14, 5.05);
          wallZ(-4.35, -2.35, 2.12);
          wallZ(-4.35, 4.45, 5.05);
          builder.box([2.12, -1.055, -6.95], [4.45, -0.96, -5.15], builder.color("#1d4ed8"));
          builder.box([-1.9, -1.055, -9.12], [1.12, -0.96, -5.05], deck);
          // Bay Ops interior transit spine: this replaces the previous black void with a modeled corridor throat.
          builder.box([-1.18, -1.06, -9.72], [1.18, -0.92, -8.62], builder.color("#243244"));
          builder.box([-1.42, -1.04, -9.76], [-1.16, 2.12, -8.58], bulkhead);
          builder.box([1.16, -1.04, -9.76], [1.42, 2.12, -8.58], bulkhead);
          builder.box([-1.42, 2.12, -9.76], [1.42, 2.48, -8.58], bulkhead);
          builder.beam([-1.04, -0.72, -9.42], [1.04, -0.72, -9.42], 0.026, blue);
          builder.beam([-1.04, -0.72, -8.86], [1.04, -0.72, -8.86], 0.026, blue);
          builder.beam([-1.08, 1.74, -9.54], [1.08, 1.74, -9.54], 0.028, light);
          builder.beam([-1.08, 0.4, -9.55], [-1.08, 0.4, -8.72], 0.022, green);
          builder.beam([1.08, 0.4, -9.55], [1.08, 0.4, -8.72], 0.022, green);
          builder.box([-0.72, -1.052, -9.38], [0.72, -0.93, -9.2], green);
          builder.box([-0.72, -1.052, -8.98], [0.72, -0.93, -8.8], green);
          builder.beam([2.34, -0.72, -5.42], [4.28, -0.72, -6.82], 0.024, blue);
          builder.beam([4.28, -0.72, -5.42], [2.34, -0.72, -6.82], 0.024, blue);
          builder.beam([-1.55, -0.76, -5.25], [0.0, -0.76, -8.75], 0.024, blue);
          builder.beam([1.55, -0.76, -5.25], [0.0, -0.76, -8.75], 0.024, blue);
          builder.beam([2.14, 0.12, -4.58], [4.42, 0.12, -4.58], 0.03, green);
          terminalBlock("terminal.bay-ops", 3.86, -6.42, blue);
          builder.beam([2.0, 0.1, -5.15], [4.55, 0.1, -5.15], 0.024, blue);
          builder.beam([-1.12, 0.14, -8.82], [1.12, 0.14, -8.82], 0.024, amber);
          mapMarker(3.86, -6.42, blue);

          // Security checkpoint and inner bay door.
          roomShell(-3.25, 3.25, -13.65, -8.75, amber);
          wallX(-3.25, -13.65, -8.75);
          wallX(3.25, -13.65, -8.75);
          wallZ(-8.85, -3.25, -0.82);
          wallZ(-8.85, 0.82, 3.25);
          doorPanel("door.bay-inner", 0, -8.88, 1.64, false);
          builder.box([-2.62, -1.05, -12.3], [-1.48, 0.2, -11.7], bulkhead);
          builder.box([1.48, -1.05, -12.3], [2.62, 0.2, -11.7], bulkhead);
          builder.beam([-2.55, 0.55, -12.0], [2.55, 0.55, -12.0], 0.03, amber);

          // Main corridor hub.
          roomShell(-6.75, 6.75, -18.9, -13.25, light);
          wallX(-6.75, -18.9, -13.25);
          wallX(6.75, -18.9, -13.25);
          wallZ(-13.35, -6.75, -0.8);
          wallZ(-13.35, 0.8, 6.75);
          doorPanel("door.security-hub", 0, -13.36, 1.6, false);
          builder.beam([-5.65, -0.88, -16.3], [5.65, -0.88, -16.3], 0.026, light);
          builder.beam([0, -0.88, -13.7], [0, -0.88, -25.6], 0.026, light);
          doorPanel("door.engineering-access", 3.18, -17.85, 2.1, true);
          doorPanel("door.medbay", -3.18, -17.85, 2.1, true);

          // Engineering access.
          roomShell(2.0, 9.9, -24.35, -17.05, green);
          wallX(9.9, -24.35, -17.05);
          wallZ(-24.35, 2.0, 9.9);
          terminalBlock("terminal.engineering-power", 7.35, -20.75, green);
          builder.ellipsoid([5.4, 0.05, -21.1], [0.74, 1.15, 0.74], 14, 8, builder.color("#115e59"));
          builder.beam([5.4, 1.15, -21.1], [5.4, 2.45, -21.1], 0.06, this.shipState?.power === "online" ? green : amber);
          builder.beam([3.0, 0.3, -18.3], [8.8, 0.3, -23.0], 0.02, green);
          mapMarker(7.35, -20.75, green);

          // Medbay triage.
          roomShell(-9.9, -2.0, -24.35, -17.05, med);
          wallX(-9.9, -24.35, -17.05);
          wallZ(-24.35, -9.9, -2.0);
          builder.box([-8.65, -1.04, -22.9], [-6.65, -0.62, -21.7], builder.color("#e2e8f0"));
          builder.box([-5.7, -1.04, -22.9], [-3.7, -0.62, -21.7], builder.color("#e2e8f0"));
          builder.beam([-8.45, -0.34, -22.28], [-6.85, -0.34, -22.28], 0.035, med);
          builder.beam([-5.5, -0.34, -22.28], [-3.9, -0.34, -22.28], 0.035, med);
          mapMarker(-6.3, -20.4, med);

          // Science/Ops lab.
          roomShell(-9.9, -2.0, -31.5, -24.0, sci);
          wallX(-9.9, -31.5, -24.0);
          wallZ(-31.5, -9.9, -2.0);
          doorPanel("door.science", -3.18, -25.0, 2.1, true);
          builder.consoleWedge(-7.8, -28.6, 1.3, 0.82, -1.08, -0.28, 0.18, builder.color("#312e81"));
          builder.consoleWedge(-4.8, -28.6, 1.3, 0.82, -1.08, -0.28, 0.18, builder.color("#312e81"));
          builder.ellipsoid([-6.28, 0.45, -26.25], [0.75, 0.48, 0.75], 14, 8, sci);
          builder.beam([-8.45, 0.62, -28.95], [-3.55, 0.62, -28.95], 0.025, sci);
          mapMarker(-6.28, -26.25, sci);

          // Bridge command door, command vestibule, and bridge deck.
          roomShell(-2.95, 2.95, -32.25, -25.35, amber);
          wallX(-2.95, -32.25, -25.35);
          wallX(2.95, -32.25, -25.35);
          // Leave the forward bridge throat open so the vestibule visibly connects to the bridge deck.
          wallZ(-32.25, -2.95, -1.12);
          wallZ(-32.25, 1.12, 2.95);
          doorPanel("door.bridge", 0, -25.72, 2.5, false);
          builder.consoleWedge(0, -29.25, 1.8, 0.9, -1.08, -0.32, 0.26, builder.color("#1e3a8a"));
          builder.beam([-1.2, 0.62, -29.68], [1.2, 0.62, -29.68], 0.028, green);
          builder.box([-0.78, -1.052, -31.98], [0.78, -0.93, -31.65], green);
          builder.beam([-1.04, 0.1, -31.88], [1.04, 0.1, -31.88], 0.024, light);
          mapMarker(0, -25.72, green);

          // Bridge deck with forward viewscreen showing the enemy ship.
          roomShell(-4.8, 4.8, -39.5, -31.25, screenGlow);
          wallX(-4.8, -39.5, -31.25);
          wallX(4.8, -39.5, -31.25);
          wallZ(-31.25, -4.8, -1.12);
          wallZ(-31.25, 1.12, 4.8);
          wallZ(-39.5, -4.8, 4.8);
          builder.beam([-1.08, 0.35, -31.48], [1.08, 0.35, -31.48], 0.026, green);
          builder.box([-2.35, -1.06, -34.15], [-1.22, -0.5, -33.3], builder.color("#1e3a8a"));
          builder.box([1.22, -1.06, -34.15], [2.35, -0.5, -33.3], builder.color("#1e3a8a"));
          builder.consoleWedge(-2.85, -36.7, 1.25, 0.82, -1.08, -0.32, 0.22, builder.color("#0f766e"));
          // Starboard bridge tactical console: E-key fires on the enemy ship shown on the viewscreen.
          const tacticalConsoleGlow = builder.color(this.enemyShipDisabled() ? "#86efac" : "#f97316", true);
          builder.consoleWedge(2.85, -36.7, 1.25, 0.82, -1.08, -0.32, 0.22, builder.color("#7c2d12"));
          builder.beam([2.24, -0.48, -36.98], [3.46, -0.48, -36.98], 0.026, tacticalConsoleGlow);
          builder.box([2.54, -0.7, -36.92], [3.16, -0.62, -36.66], tacticalConsoleGlow);
          builder.consoleWedge(0, -35.1, 1.65, 0.9, -1.08, -0.32, 0.24, builder.color("#1e3a8a"));
          builder.ellipsoid([0, -0.55, -36.22], [0.42, 0.38, 0.42], 12, 6, builder.color("#475569"));
          builder.box([-0.36, -1.05, -35.9], [0.36, -0.58, -35.48], builder.color("#64748b"));
          builder.beam([-3.95, -0.78, -33.2], [-1.1, -0.78, -37.2], 0.024, blue);
          builder.beam([3.95, -0.78, -33.2], [1.1, -0.78, -37.2], 0.024, blue);
          builder.beam([-3.7, 2.3, -32.0], [3.7, 2.3, -32.0], 0.018, light);
          builder.beam([-3.7, 2.3, -38.9], [3.7, 2.3, -38.9], 0.018, light);
          this.appendBridgeViewscreenEnemy(builder, nowMs);
          mapMarker(0, -37.15, red);
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

        appendBridgeViewscreenEnemy(builder, nowMs = 0) {
          const screenGlass = builder.color("#06111f");
          const screenGlow = builder.color("#38bdf8", true);
          const tacticalGrid = builder.color("#0ea5e9", true);
          const hullPercent = this.enemyShipHullPercent();
          const disabled = this.enemyShipDisabled();
          const shotAgeMs = this.bridgeTacticalShotAgeMs(nowMs);
          const tacticalFiring = shotAgeMs < 850;
          const hostileHull = builder.color(disabled ? "#334155" : hullPercent < 45 ? "#854d0e" : "#365314");
          const hostileDark = builder.color(disabled ? "#0f172a" : "#111827");
          const hostileAlert = builder.color(disabled ? "#64748b" : "#ef4444", true);
          const signal = builder.color(disabled ? "#86efac" : "#fbbf24", true);
          const weaponGlow = builder.color("#f97316", true);
          const hitGlow = builder.color("#fef3c7", true);
          const tracking = this.bridgeViewscreenTrackingActive();
          const lockGlow = builder.color(disabled ? "#86efac" : tracking ? "#86efac" : "#fbbf24", true);
          const scanPulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 460);
          const lockPulse = 0.5 + 0.5 * Math.sin((nowMs || 0) / 170);
          const z = -39.12;

          // Bridge viewscreen surface mounted on the forward bulkhead.
          builder.box([-3.45, 0.18, z - 0.04], [3.45, 2.28, z + 0.04], screenGlass);
          builder.beam([-3.3, 0.34, z + 0.08], [3.3, 0.34, z + 0.08], 0.022, tacticalGrid);
          builder.beam([-3.3, 2.1, z + 0.08], [3.3, 2.1, z + 0.08], 0.022, tacticalGrid);
          builder.beam([-3.24, 0.42, z + 0.08], [-3.24, 2.0, z + 0.08], 0.018, screenGlow);
          builder.beam([3.24, 0.42, z + 0.08], [3.24, 2.0, z + 0.08], 0.018, screenGlow);
          [-2.1, 0, 2.1].forEach((x) => {
            builder.beam([x, 0.42, z + 0.07], [x, 2.02, z + 0.07], 0.006, tacticalGrid);
          });
          [0.82, 1.28, 1.74].forEach((y) => {
            builder.beam([-3.1, y, z + 0.07], [3.1, y, z + 0.07], 0.006, tacticalGrid);
          });

          // Enemy raider tactical image on the viewscreen.
          builder.ellipsoid([0, 1.28, z + 0.16], [0.2, 0.42, 0.05], 14, 6, hostileDark);
          builder.ellipsoid([-0.48, 1.28, z + 0.17], [0.5, 0.16, 0.05], 14, 5, hostileHull);
          builder.ellipsoid([0.48, 1.28, z + 0.17], [0.5, 0.16, 0.05], 14, 5, hostileHull);
          builder.box([-0.12, 1.12, z + 0.23], [0.12, 1.44, z + 0.29], hostileAlert);
          builder.box([-0.88, 1.2, z + 0.21], [-0.58, 1.36, z + 0.28], hostileAlert);
          builder.box([0.58, 1.2, z + 0.21], [0.88, 1.36, z + 0.28], hostileAlert);
          builder.beam([-1.46, 0.68, z + 0.12], [1.46, 1.88, z + 0.12], 0.014 + scanPulse * 0.008, signal);
          builder.beam([-1.46, 1.88, z + 0.12], [1.46, 0.68, z + 0.12], 0.014 + scanPulse * 0.008, signal);
          builder.beam([-2.75, 0.6, z + 0.13], [-1.38, 0.6, z + 0.13], 0.016, hostileAlert);
          builder.beam([1.38, 0.6, z + 0.13], [2.75, 0.6, z + 0.13], 0.016, hostileAlert);
          builder.beam([-2.75, 1.96, z + 0.13], [-1.38, 1.96, z + 0.13], 0.016, hostileAlert);
          builder.beam([1.38, 1.96, z + 0.13], [2.75, 1.96, z + 0.13], 0.016, hostileAlert);
          if (tacticalFiring) {
            const impact = 1 - Math.min(1, shotAgeMs / 850);
            builder.beam([2.86, 0.58, z + 0.22], [0.42, 1.2, z + 0.34], 0.018 + impact * 0.028, weaponGlow);
            builder.beam([2.66, 0.72, z + 0.22], [-0.34, 1.36, z + 0.34], 0.014 + impact * 0.022, weaponGlow);
            builder.box([-0.26, 1.1, z + 0.33], [0.26, 1.48, z + 0.42], hitGlow);
          }
          const hullBarWidth = 2.4 * Math.max(0, Math.min(1, hullPercent / 100));
          builder.box([-1.2, 0.18, z + 0.18], [-1.2 + hullBarWidth, 0.25, z + 0.25], disabled ? lockGlow : hostileAlert);
          if (disabled) {
            builder.beam([-1.6, 1.28, z + 0.34], [1.6, 1.28, z + 0.34], 0.028, lockGlow);
            builder.beam([0, 0.7, z + 0.34], [0, 1.86, z + 0.34], 0.028, lockGlow);
          }

          if (tracking) {
            const thickness = 0.018 + lockPulse * 0.014;
            builder.beam([-1.12, 0.86, z + 0.18], [-0.54, 0.86, z + 0.18], thickness, lockGlow);
            builder.beam([0.54, 0.86, z + 0.18], [1.12, 0.86, z + 0.18], thickness, lockGlow);
            builder.beam([-1.12, 1.7, z + 0.18], [-0.54, 1.7, z + 0.18], thickness, lockGlow);
            builder.beam([0.54, 1.7, z + 0.18], [1.12, 1.7, z + 0.18], thickness, lockGlow);
            builder.beam([-1.12, 0.86, z + 0.18], [-1.12, 1.24, z + 0.18], thickness, lockGlow);
            builder.beam([1.12, 0.86, z + 0.18], [1.12, 1.24, z + 0.18], thickness, lockGlow);
            builder.beam([-1.12, 1.34, z + 0.18], [-1.12, 1.7, z + 0.18], thickness, lockGlow);
            builder.beam([1.12, 1.34, z + 0.18], [1.12, 1.7, z + 0.18], thickness, lockGlow);
            builder.beam([-2.95, 0.48, z + 0.16], [2.95, 0.48, z + 0.16], 0.014, lockGlow);
            builder.beam([-2.95, 2.08, z + 0.16], [2.95, 2.08, z + 0.16], 0.014, lockGlow);
          } else {
            builder.beam([-2.85 + scanPulse * 5.7, 0.48, z + 0.16], [-2.85 + scanPulse * 5.7, 2.08, z + 0.16], 0.012, signal);
          }
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
          const builder = new Shuttle3dGeometryWriter();
          const transportGlow = builder.color("#84cc16", true);
          const alienBody = builder.color("#365314");
          const alienArmor = builder.color("#1a2e05");
          const alienHit = builder.color("#fef08a", true);
          const alienEyes = builder.color("#ef4444", true);
          const healthBack = builder.color("#111827");
          const healthFill = builder.color("#84cc16", true);
          this.appendFlightScene(builder, nowMs);
          if (this.isDockingSceneActive()) {
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
          return builder.toFloat32Array();
        }

        combatSnapshot(nowMs = this.combatClockMs) {
          const cooldownRemainingMs = Math.max(
            0,
            this.combat.phaser.cooldownMs - (nowMs - this.lastPhaserShotAt)
          );
          return {
            enabled: this.combat.enabled,
            health: Math.max(0, Math.round(this.playerHealth)),
            maxHealth: this.combat.player.maxHealth,
            alive: this.aliens.length,
            active: this.aliens.filter((alien) => alien.state === "active").length,
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

          if (nowMs >= this.nextTransportAtMs) {
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
          if (this.isBoardingPaused() || !this.combat.enabled || !this.combat.phaser.enabled || this.gameOver) return false;
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
          let hitDistance = this.combat.phaser.range;
          this.aliens.forEach((alien) => {
            if (alien.state !== "active") return;
            const center = [alien.position[0], alien.position[1] + 0.78, alien.position[2]];
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
            if (missDistance <= Math.max(0.68, this.combat.alien.radius * 1.7)) {
              hitAlien = alien;
              hitDistance = distanceAlongRay;
            }
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

          if (hitAlien) {
            hitAlien.health -= this.combat.phaser.damage;
            hitAlien.hitFlashUntilMs = nowMs + 120;
            if (hitAlien.health <= 0) {
              this.aliens = this.aliens.filter((alien) => alien !== hitAlien);
              this.kills += 1;
            }
          }
          this.emitCombatState(true);
          return true;
        }

        resetCombat(nowMs = performance.now()) {
          if (this.pilot.active) this.setPilotMode(false, null, nowMs);
          this.playerHealth = this.combat.player.startingHealth;
          this.aliens = [];
          this.transportSequence = 0;
          this.kills = 0;
          this.gameOver = false;
          this.phaserBeam = null;
          this.lastPhaserShotAt = -Infinity;
          this.combatClockMs = nowMs;
          this.nextTransportAtMs = nowMs + Math.min(900, this.combat.transport.initialDelayMs);
          this.clearMovementKeys();
          this.resetFlightState();
          this.emitCombatState(true);
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
          const shipSceneActive = this.isShuttleBaySceneActive();
          const movement = shipSceneActive ? this.shuttleBayMovementConfig() : this.movement;
          const {bounds, radius, colliders} = movement;
          if (x < bounds.minX || x > bounds.maxX || z < bounds.minZ || z > bounds.maxZ) return false;
          if (shipSceneActive && (!this.isInsideMotherShipWalkable(x, z) || !this.shipDoorAllowsPosition(x, z))) return false;
          const blockedByFixture = colliders.some((collider) => (
            x > collider.minX - radius
            && x < collider.maxX + radius
            && z > collider.minZ - radius
            && z < collider.maxZ + radius
          ));
          if (blockedByFixture) return false;
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
          this.updateMovement(deltaSeconds);
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

      function bindShuttle3dLookaround(container, scene) {
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
          if (target?.closest?.("button, a, input, select, textarea")) return;
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
          updatePilotHover(event);
        };
        const pointerMove = (event) => {
          if (!dragging) return;
          applyDelta(event.clientX - startX, event.clientY - startY);
          updatePilotHover(event);
        };
        const pointerLeave = () => {
          if (dragging) return;
          renderer()?.setHoveredPilotStation?.(null);
        };
        const pointerUp = () => {
          if (!dragging) return;
          dragging = false;
          delete container.dataset.shuttle3dDragging;
          if (dragDistance < 5 && !renderer()?.pilot?.active) renderer()?.firePhaser?.();
        };
        const keyDown = (event) => {
          const shuttle = renderer();
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
            if (!event.repeat) shuttle?.resetCombat?.();
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
        hud.append(healthPanel, combatLine, phaserLine, pilotLine, shipLine);

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
        hint.textContent = shuttle.controlsHint || "Mouse over console + E pilot • W/S throttle flies to Mother Ship • docking triggers shuttle-bay cutscene • Click/Space/F fire outside pilot";

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

        shell.append(canvas, hud, crosshair, pilotPrompt, damageFlash, gameOver, hint, status, twiddleSystem);
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
          canvas.dataset.combatEnabled = String(renderer.combat.enabled);
          canvas.dataset.pilotStations = String(renderer.pilotStations.length);
          canvas.dataset.pilotMode = "inactive";
          canvas.dataset.alienShip = renderer.combat.alienShip.id;
          let lastHealth = renderer.combat.player.startingHealth;
          let damageTimer = 0;
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
          renderer.onPilotChanged = updatePilotHud;
          renderer.onShipStateChanged = updateShipHud;
          updateMovementStatus(renderer.camera);
          renderer.emitPilotState(true);
          renderer.emitCombatState(true);
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
