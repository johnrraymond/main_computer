    (function () {
      const imageAssetKinds = new Set(["image"]);

      function fallbackScene(sceneId = "default-empty-scene") {
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
