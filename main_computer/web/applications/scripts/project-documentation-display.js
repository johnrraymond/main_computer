    const projectDocDisplayRoot = document.querySelector("#project-doc-display");
    const projectDocDisplayTarget = document.querySelector("#project-doc-display-target");
    const projectDocDisplayStatus = document.querySelector("#project-doc-display-status");
    const projectDocDisplayFrame = document.querySelector("#project-doc-display-frame");
    const projectDocDisplayClose = document.querySelector("#project-doc-display-close");
    const projectDocDisplayManifestLookup = new Map();
    const projectDocDisplayState = {
      targetId: "",
      docStatus: "idle",
      docPath: "",
      contentType: "text/html",
      title: "Project Documentation",
      metadata: {},
    };

    function escapeProjectDocHtml(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function safeProjectDocHtml(content, title = "Project documentation") {
      return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeProjectDocHtml(title)}</title><style>body{margin:0;padding:18px;background:#f8f7f2;color:#171717;font:15px/1.5 Arial,sans-serif}code,pre{font-family:Consolas,monospace}article{max-width:980px;margin:auto}section{margin:0 0 22px}pre{overflow:auto;background:#111;color:#f4f4f4;padding:12px;border-radius:6px}</style></head><body>${content}</body></html>`;
    }

    function updateProjectDocDisplayStatus() {
      if (!projectDocDisplayRoot) return;
      projectDocDisplayRoot.dataset.status = projectDocDisplayState.docStatus;
      if (projectDocDisplayTarget) {
        projectDocDisplayTarget.textContent = projectDocDisplayState.targetId || "Alt+click any documented widget";
      }
      if (projectDocDisplayStatus) {
        const path = projectDocDisplayState.docPath ? ` · ${projectDocDisplayState.docPath}` : "";
        projectDocDisplayStatus.textContent = `${projectDocDisplayState.docStatus}${path}`;
      }
    }

    function openProjectDocDisplay() {
      if (!projectDocDisplayRoot) return;
      projectDocDisplayRoot.hidden = false;
      updateProjectDocDisplayStatus();
    }

    function closeProjectDocDisplay() {
      if (projectDocDisplayRoot) projectDocDisplayRoot.hidden = true;
    }

    async function loadProjectDocDisplayManifest() {
      const response = await fetch("/api/applications/component-docs/manifest", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: "{}",
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
      projectDocDisplayManifestLookup.clear();
      if (Array.isArray(data.entries)) {
        data.entries.forEach((entry) => {
          if (!entry?.id) return;
          projectDocDisplayManifestLookup.set(String(entry.id), String(entry.id));
          (Array.isArray(entry.aliases) ? entry.aliases : []).forEach((alias) => {
            if (alias) projectDocDisplayManifestLookup.set(String(alias), String(entry.id));
          });
        });
      }
      return data;
    }

    async function loadProjectDocDisplay(targetId = projectDocDisplayState.targetId) {
      const cleanTarget = String(targetId || "").trim();
      if (!cleanTarget) return;
      projectDocDisplayState.targetId = cleanTarget;
      projectDocDisplayState.docStatus = "loading";
      projectDocDisplayState.docPath = "";
      updateProjectDocDisplayStatus();
      openProjectDocDisplay();
      try {
        const response = await fetch("/api/applications/component-docs/read", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({id: projectDocDisplayState.targetId}),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.error || `HTTP ${response.status}`);
        projectDocDisplayState.targetId = data.id || projectDocDisplayState.targetId;
        projectDocDisplayState.docStatus = data.exists ? "loaded" : "missing";
        projectDocDisplayState.docPath = data.path || "";
        projectDocDisplayState.contentType = data.content_type || "text/html";
        projectDocDisplayState.title = data.title || projectDocDisplayState.targetId;
        projectDocDisplayState.metadata = data.metadata || {};
        if (projectDocDisplayFrame) {
          const empty = "<article><h1>No documentation found</h1><p>No generated documentation exists for this target yet.</p></article>";
          projectDocDisplayFrame.srcdoc = safeProjectDocHtml(data.content || empty, projectDocDisplayState.title);
        }
      } catch (error) {
        projectDocDisplayState.docStatus = "error";
        projectDocDisplayState.metadata = {error: error.message || String(error)};
        if (projectDocDisplayFrame) {
          projectDocDisplayFrame.srcdoc = safeProjectDocHtml(`<article><h1>Documentation unavailable</h1><p>${escapeProjectDocHtml(error.message || error)}</p></article>`, "Documentation unavailable");
        }
      }
      updateProjectDocDisplayStatus();
    }

    function projectDocClickCandidates(target) {
      const element = target instanceof Element ? target : target?.parentElement;
      if (!element) return [];
      if (element.closest("#project-doc-display") || element.closest("#mc-widget-editor-root")) return [];
      const generatedItem = element.closest("[data-mc-generated-item]");
      const candidates = [
        generatedItem?.dataset.mcComponentOwner,
        generatedItem?.dataset.mcFeatureId,
        element.closest("[data-mc-doc-id]")?.dataset.mcDocId,
        element.closest("[data-mc-component-id]")?.dataset.mcComponentId,
        element.closest("[data-mc-widget-id]")?.dataset.mcWidgetId,
        element.closest("[data-mc-component-owner]")?.dataset.mcComponentOwner,
        element.closest("[data-mc-feature-id]")?.dataset.mcFeatureId,
        element.id,
      ].filter(Boolean).map(String);
      return [...new Set(candidates)];
    }

    function resolveProjectDocClickTarget(target) {
      const candidates = projectDocClickCandidates(target);
      for (const candidate of candidates) {
        const resolved = projectDocDisplayManifestLookup.get(String(candidate));
        if (resolved) return resolved;
      }
      return candidates[0] || "";
    }

    function handleProjectDocAltClick(event) {
      if (!event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      const resolvedId = resolveProjectDocClickTarget(target);
      if (!resolvedId) return;
      event.preventDefault();
      event.stopPropagation();
      if (target?.matches?.("input, textarea, select, button")) target.blur();
      window.MainComputerProjectDocumentation.loadDoc(resolvedId);
    }

    window.MainComputerProjectDocumentation = {
      getState() { return JSON.parse(JSON.stringify(projectDocDisplayState)); },
      loadDoc: loadProjectDocDisplay,
      open: openProjectDocDisplay,
      close: closeProjectDocDisplay,
      reloadManifest: loadProjectDocDisplayManifest,
      resolveClickTarget: resolveProjectDocClickTarget,
      documentTarget() {
        return {
          id: "project.documentation-display",
          kind: "panel",
          label: "Project Documentation Display",
          owner: "main-computer.applications",
          feature: "project.feature.documentation-display",
        };
      },
    };

    document.addEventListener("click", handleProjectDocAltClick, true);
    projectDocDisplayClose?.addEventListener("click", closeProjectDocDisplay);
    loadProjectDocDisplayManifest().catch(() => {});
    updateProjectDocDisplayStatus();
