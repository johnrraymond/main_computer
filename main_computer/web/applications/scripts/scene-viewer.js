    (function () {
      const imageAssetKinds = new Set(["image"]);

      function fallbackSpriteActor() {
        return {
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
        };
      }

      function fallbackParticleEmitter() {
        return {
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
        };
      }

      function fallbackScene(sceneId = "default-empty-scene") {
        return {
          id: String(sceneId || "default-empty-scene"),
          name: "Isometric Battle Floor",
          version: 2,
          background: "radial-gradient(circle at 50% 24%, rgba(56, 189, 248, 0.16), rgba(15, 23, 42, 0.92) 55%, #020617 100%)",
          objects: [
            fallbackSpriteActor(),
            fallbackParticleEmitter(),
            {
              id: "ruin-scout",
              type: "sprite-actor",
              x: 7,
              y: 3,
              width: 96,
              height: 128,
              props: {
                label: "Ruin Scout",
                color: "#c084fc",
                z: 12,
                bob: 6,
                motion: "glide",
                spriteSeries: ["watch", "lean", "ready", "dash"]
              }
            }
          ],
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

      function projectSceneObject(object, scene) {
        const projection = sceneProjection(scene);
        if (projection !== "isometric") {
          return {
            left: Number(object.x) || 0,
            top: Number(object.y) || 0,
            width: Math.max(0, Number(object.width) || 0),
            height: Math.max(0, Number(object.height) || 0),
            zIndex: 10,
            transform: "",
            anchor: "top-left"
          };
        }
        const metrics = sceneProjectionMetrics(scene);
        const worldX = numericSceneProp(object.x, 0, -256, 256);
        const worldY = numericSceneProp(object.y, 0, -256, 256);
        const worldZ = numericSceneProp(object?.props?.z ?? object?.props?.elevation, 0, -256, 512);
        const left = metrics.originX + ((worldX - worldY) * metrics.tileWidth) / 2;
        const top = metrics.originY + ((worldX + worldY) * metrics.tileHeight) / 2 - worldZ;
        const width = Math.max(48, Number(object.width) || metrics.tileWidth);
        const height = Math.max(56, Number(object.height) || metrics.tileWidth * 1.25);
        return {
          left,
          top,
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

      function renderPlayerCapsule(element, object) {
        element.classList.add("scene-object--player-capsule");
        element.dataset.scenePlayer = "true";
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Player Capsule"));
        const core = document.createElement("span");
        core.className = "scene-player-capsule-core";
        core.setAttribute("aria-hidden", "true");
        element.append(core);
      }

      function renderParticleEmitter(element, object, scene) {
        element.classList.add("scene-object--particle-emitter");
        element.dataset.sceneParticleEmitter = "true";
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Particle Emitter"));
        const color = normalizeSceneColor(object.props?.color, "#7dd3fc");
        const count = Math.round(numericSceneProp(object.props?.particleCount, 32, 4, 96));
        const size = numericSceneProp(object.props?.particleSize, 5, 2, 18);
        const spread = numericSceneProp(object.props?.spread, 1, 0.2, 2);
        const width = Math.max(1, Number(object.width) || 1);
        const height = Math.max(1, Number(object.height) || 1);
        const seed = Math.abs(particleHash(object.id || object.props?.label || "particle-emitter"));
        const projection = sceneProjection(scene);
        const field = document.createElement("span");
        field.className = "scene-particle-field";
        field.setAttribute("aria-hidden", "true");
        field.style.setProperty("--particle-color", color);
        element.style.setProperty("--mint", color);
        element.style.color = color;
        if (projection === "isometric") {
          element.style.transform = "translate(-50%, -82%)";
        }
        for (let index = 0; index < count; index += 1) {
          const particle = document.createElement("span");
          particle.className = "scene-particle";
          const angle = (index * 137.508 + seed) * (Math.PI / 180);
          const radius = Math.sqrt((index + 1) / count) * spread;
          const x = Math.cos(angle) * width * 0.42 * radius;
          const y = Math.sin(angle) * height * 0.42 * radius;
          const particleSize = Math.max(2, size * (0.72 + ((seed + index * 17) % 7) / 18));
          const duration = 1800 + ((seed + index * 113) % 1800);
          const delay = -((seed + index * 89) % duration);
          const alpha = 0.42 + (((seed + index * 31) % 46) / 100);
          particle.style.setProperty("--particle-x", `${x.toFixed(2)}px`);
          particle.style.setProperty("--particle-y", `${y.toFixed(2)}px`);
          particle.style.setProperty("--particle-size", `${particleSize.toFixed(2)}px`);
          particle.style.setProperty("--particle-duration", `${duration}ms`);
          particle.style.setProperty("--particle-delay", `${delay}ms`);
          particle.style.setProperty("--particle-alpha", alpha.toFixed(2));
          field.append(particle);
        }
        element.append(field);
      }

      function spriteSeries(object) {
        const frames = Array.isArray(object?.props?.spriteSeries) ? object.props.spriteSeries : [];
        return frames.length ? frames : ["idle", "step-left", "step-right", "cast"];
      }

      function renderSpriteActor(element, object) {
        element.classList.add("scene-object--sprite-actor");
        if (object?.props?.role === "player") element.dataset.scenePlayer = "true";
        element.dataset.spriteSeries = "true";
        element.setAttribute("role", "img");
        element.setAttribute("aria-label", String(object.props?.label || "Sprite Actor"));
        element.style.setProperty("--scene-bob-height", `${numericSceneProp(object?.props?.bob, 8, 0, 24)}px`);

        const shadow = document.createElement("span");
        shadow.className = "scene-sprite-shadow";
        shadow.setAttribute("aria-hidden", "true");

        const body = document.createElement("span");
        body.className = "scene-sprite-body";
        body.setAttribute("aria-hidden", "true");

        const illustration = document.createElement("span");
        illustration.className = "scene-sprite-illustration";
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

      function renderSceneObject(parent, object, scene, options = {}) {
        if (!object || !parent) return;
        const element = document.createElement("div");
        const objectType = String(object.type || "object");
        const projected = projectSceneObject(object, scene);
        element.className = "scene-object";
        element.dataset.sceneObjectId = String(object.id || "");
        element.dataset.sceneObjectType = objectType;
        element.style.left = `${projected.left}px`;
        element.style.top = `${projected.top}px`;
        element.style.width = `${Math.max(0, projected.width)}px`;
        element.style.height = `${Math.max(0, projected.height)}px`;
        element.style.zIndex = String(projected.zIndex);
        if (projected.transform) element.style.transform = projected.transform;
        element.dataset.sceneAnchor = projected.anchor;
        if (objectType === "particle-emitter") {
          renderParticleEmitter(element, object, scene);
        } else if (objectType === "player-capsule") {
          renderPlayerCapsule(element, object);
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
        if (scene.objects.some((object) => object?.type === "player-capsule")) return "player-ready";
        return "objects";
      }

      function renderSceneSurface(container, sceneOrId, options = {}) {
        if (!container) return null;
        const scene = resolveScene(sceneOrId);
        const projection = sceneProjection(scene);
        const metrics = sceneProjectionMetrics(scene);
        container.replaceChildren();
        container.dataset.sceneViewer = "true";
        container.dataset.sceneId = scene.id;
        container.dataset.sceneName = scene.name;
        container.dataset.sceneState = sceneState(scene);
        container.dataset.sceneProjection = projection;
        container.dataset.sceneMode = String(options.mode || (options.embedded ? "document-embed" : "surface"));
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
        if (options.renderObjects !== false) {
          scene.objects.forEach((object) => renderSceneObject(container, object, scene, options));
        }
        return {
          scene,
          objectCount: scene.objects.length,
          dispose() {
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
        hydrateSceneEmbeds
      };
    })();
