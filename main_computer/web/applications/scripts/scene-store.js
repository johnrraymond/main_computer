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
        return {
          id: playerSpriteObjectId,
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

      function defaultSpriteSupportAura() {
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

      function defaultEnemySprite() {
        return {
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
        };
      }

      function defaultSceneObjects() {
        return [defaultPlayerSprite(), defaultSpriteSupportAura(), defaultEnemySprite()];
      }

      function defaultScene() {
        return {
          id: defaultSceneId,
          name: "Isometric Battle Floor",
          version: 2,
          background: "radial-gradient(circle at 50% 24%, rgba(56, 189, 248, 0.16), rgba(15, 23, 42, 0.92) 55%, #020617 100%)",
          objects: defaultSceneObjects(),
          metadata: {
            createdBy: "Main Computer Scene Store",
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
            isometric: true
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
