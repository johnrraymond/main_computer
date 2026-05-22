    (function () {
      function sceneEmbedId(prefix = "scene-embed") {
        if (window.crypto?.randomUUID) return `${prefix}-${window.crypto.randomUUID()}`;
        return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      }

      function normalizeSceneEmbedMode(mode) {
        return mode === "snapshot" ? "snapshot" : "live";
      }

      function selectedSceneIdForEmbed(sceneId = "") {
        return String(sceneId || window.MainComputerSceneStore?.selectedSceneId?.() || window.MainComputerSceneStore?.defaultSceneId || "default-empty-scene").trim() || "default-empty-scene";
      }

      function hydrateSceneEmbed(element) {
        if (!element) return;
        const sceneId = selectedSceneIdForEmbed(element.dataset.sceneId);
        const mode = normalizeSceneEmbedMode(element.dataset.sceneEmbedMode);
        element.dataset.docObject = "scene-embed";
        element.dataset.docObjectId = element.dataset.docObjectId || sceneEmbedId();
        element.dataset.docObjectLayout = "paragraph";
        element.dataset.sceneEmbed = "true";
        element.dataset.sceneId = sceneId;
        element.dataset.sceneEmbedMode = mode;
        element.contentEditable = "false";
        element.draggable = true;
        element.className = "document-object document-scene-embed scene-surface";
        element.setAttribute("role", "group");
        element.setAttribute("tabindex", "0");
        element.setAttribute("aria-label", `Embedded scene ${sceneId}`);
        window.MainComputerSceneViewer?.renderSceneSurface?.(element, sceneId, {
          embedded: true,
          mode: "document-embed",
          label: `Embedded scene ${sceneId}`
        });
      }

      function serializeSceneEmbed(element) {
        const sceneId = selectedSceneIdForEmbed(element.dataset.sceneId);
        const mode = normalizeSceneEmbedMode(element.dataset.sceneEmbedMode);
        element.replaceChildren();
        element.dataset.docObject = "scene-embed";
        element.dataset.docObjectId = element.dataset.docObjectId || sceneEmbedId();
        element.dataset.docObjectLayout = "paragraph";
        element.dataset.sceneEmbed = "true";
        element.dataset.sceneId = sceneId;
        element.dataset.sceneEmbedMode = mode;
        element.contentEditable = "false";
        element.draggable = true;
        element.className = "document-object document-scene-embed scene-surface";
        element.removeAttribute("role");
        element.removeAttribute("tabindex");
        element.removeAttribute("aria-label");
        element.removeAttribute("title");
      }

      function createSceneEmbed(sceneId = "") {
        const element = document.createElement("figure");
        element.dataset.sceneId = selectedSceneIdForEmbed(sceneId);
        hydrateSceneEmbed(element);
        return element;
      }

      function insertDocumentSceneEmbed(sceneId = "") {
        const element = createSceneEmbed(sceneId);
        const selection = window.getSelection?.();
        const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
        const editor = getActiveDocumentEditor?.();
        if (!range || !editor || !editor.contains(range.startContainer)) {
          documentEditor?.append(element);
        } else {
          if (!range.collapsed) range.deleteContents();
          const block = documentBlockForRange?.(range, editor);
          if (block?.parentNode === editor) {
            block.after(element);
          } else {
            range.insertNode(element);
          }
        }
        const paragraph = document.createElement("p");
        paragraph.innerHTML = "<br>";
        element.after(paragraph);
        hydrateDocumentObjects?.(documentCanvas);
        saveDocumentDraft?.();
        scheduleDocumentRepagination?.();
        if (documentStatus) documentStatus.textContent = "scene embed inserted";
        return element;
      }

      function promptAndInsertDocumentScene() {
        const scenes = window.MainComputerSceneStore?.listScenes?.() || [];
        const defaultId = window.MainComputerSceneStore?.selectedSceneId?.() || scenes[0]?.id || "default-empty-scene";
        const requested = window.prompt?.("Scene ID to embed", defaultId);
        if (requested === null) return null;
        const sceneId = selectedSceneIdForEmbed(requested);
        return insertDocumentSceneEmbed(sceneId);
      }

      documentObjectRuntime?.registerObjectType?.("scene-embed", {
        label: "Scene Embed",
        layout: ["paragraph"],
        capabilities: ["render:block", "scene:live-reference"],
        hydrate: hydrateSceneEmbed,
        serialize: serializeSceneEmbed
      });

      window.createDocumentSceneEmbed = createSceneEmbed;
      window.insertDocumentSceneEmbed = insertDocumentSceneEmbed;
      window.promptAndInsertDocumentScene = promptAndInsertDocumentScene;
    })();
