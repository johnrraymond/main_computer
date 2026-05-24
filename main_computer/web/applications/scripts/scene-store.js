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
];
      }

      function defaultScene() {
        return {
          id: defaultSceneId,
          name: "Arcstorm Finale Showcase",
          version: 5,
          background: "radial-gradient(circle at 50% 18%, rgba(56, 189, 248, 0.22), rgba(15, 23, 42, 0.95) 58%, #020617 100%)",
          objects: defaultSceneObjects(),
          metadata: {
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
            },
            "createdBy": "Main Computer Scene Store"
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
            starter: true,
            projection: "isometric",
            tileWidth: 92,
            tileHeight: 46,
            originX: 480,
            originY: 124,
            particleOnly: false,
            includesDefaultPlayer: true,
            isometric: true,
            rolloutPhase: "phase-4-finale-showcase",
            characterModel: "sprite-particle-rig",
            meshActorsEnabled: false,
            parentedParticles: true,
            linkedSpellProjectiles: true,
            targetedParticles: true
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
