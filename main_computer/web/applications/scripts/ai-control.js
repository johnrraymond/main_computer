    let aiControlLoaded = false;
    let aiControlCatalog = null;
    let aiControlSelectedSurfaceId = "";

    function aiControlEscape(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "\"": "&quot;",
        "'": "&#39;"
      }[char]));
    }

    function aiControlCssEscape(value) {
      const text = String(value ?? "");
      if (window.CSS && typeof window.CSS.escape === "function") {
        return window.CSS.escape(text);
      }
      return text.replace(/[^a-zA-Z0-9_-]/g, (char) => `\\${char}`);
    }

    function aiControlPromptById(promptId) {
      const prompts = aiControlCatalog?.prompts || [];
      return prompts.find((prompt) => prompt.id === promptId) || null;
    }

    function aiControlStructureById(surfaceId) {
      const structures = aiControlCatalog?.message_structures || [];
      return structures.find((structure) => structure.id === surfaceId) || null;
    }

    function aiControlSetStatus(message, isError = false) {
      if (!aiControlStatus) return;
      aiControlStatus.textContent = message;
      aiControlStatus.classList.toggle("error", Boolean(isError));
    }

    function aiControlPromptLabel(promptId) {
      const prompt = aiControlPromptById(promptId);
      if (!prompt) return promptId || "unknown prompt";
      return prompt.title || prompt.id || promptId;
    }

    function aiControlPromptBadge(promptId) {
      const prompt = aiControlPromptById(promptId);
      if (!prompt) return "";
      return prompt.has_override ? '<span class="ai-control-badge">override</span>' : "";
    }

    function aiControlTextareaFor(promptId) {
      return document.querySelector(`[data-ai-control-prompt-editor="${aiControlCssEscape(promptId)}"]`);
    }

    function aiControlUpdatePromptButtons(promptId) {
      const prompt = aiControlPromptById(promptId);
      const textarea = aiControlTextareaFor(promptId);
      const saveButton = document.querySelector(`[data-ai-control-save-prompt="${aiControlCssEscape(promptId)}"]`);
      const resetButton = document.querySelector(`[data-ai-control-reset-prompt="${aiControlCssEscape(promptId)}"]`);
      const changed = Boolean(prompt && textarea && textarea.value !== String(prompt.effective_content || ""));
      if (saveButton) saveButton.disabled = !changed;
      if (resetButton) resetButton.disabled = !(prompt && prompt.has_override);
    }

    function aiControlSurfacePromptIds(structure) {
      const ids = [];
      for (const slot of structure?.slots || []) {
        if (slot.prompt_id && !ids.includes(slot.prompt_id)) ids.push(slot.prompt_id);
      }
      return ids;
    }

    function renderAiControlSurfaceList() {
      if (!aiControlSurfaceList) return;
      const structures = aiControlCatalog?.message_structures || [];
      if (!structures.length) {
        aiControlSurfaceList.innerHTML = '<div class="ai-control-empty">No AI surfaces are registered.</div>';
        return;
      }
      aiControlSurfaceList.innerHTML = structures.map((structure) => {
        const id = aiControlEscape(structure.id || "");
        const title = aiControlEscape(structure.title || structure.id);
        const source = aiControlEscape(`${structure.source_file || "unknown"} :: ${structure.function || "unknown"}`);
        const active = structure.id === aiControlSelectedSurfaceId ? " active" : "";
        const promptIds = aiControlSurfacePromptIds(structure);
        const promptCount = promptIds.length;
        const changedCount = promptIds.filter((promptId) => aiControlPromptById(promptId)?.has_override).length;
        const changed = changedCount ? `<span class="ai-control-badge">${changedCount} override${changedCount === 1 ? "" : "s"}</span>` : "";
        return `<button type="button" class="ai-control-surface-button${active}" data-ai-control-surface-id="${id}">
          <strong>${title}</strong>
          <span class="ai-control-surface-id">${id}</span>
          <span class="ai-control-surface-meta">${source}</span>
          <span class="ai-control-surface-meta">${promptCount} static prompt${promptCount === 1 ? "" : "s"} ${changed}</span>
        </button>`;
      }).join("");
    }

    function renderAiControlSlot(slot, index) {
      const role = aiControlEscape(slot.role || "unknown");
      const kind = aiControlEscape(slot.kind || "unknown");
      const label = aiControlEscape(slot.label || "");
      const optional = slot.optional ? " · optional" : "";
      const promptLink = slot.prompt_id
        ? `<button type="button" class="ai-control-inline-prompt-link" data-ai-control-jump-prompt="${aiControlEscape(slot.prompt_id)}">${aiControlEscape(aiControlPromptLabel(slot.prompt_id))}${aiControlPromptBadge(slot.prompt_id)}</button>`
        : "";
      return `<li>
        <span class="ai-control-slot-index">${index + 1}</span>
        <span class="ai-control-slot-role">${role}</span>
        <span class="ai-control-slot-kind${slot.optional ? " optional" : ""}">${kind}${optional}</span>
        <span class="ai-control-slot-label">${label}${promptLink}</span>
      </li>`;
    }

    function renderAiControlPromptEditor(promptId) {
      const prompt = aiControlPromptById(promptId);
      if (!prompt) {
        return `<article class="ai-control-prompt-card ai-control-warning">
          Unknown static prompt referenced by this surface: <code>${aiControlEscape(promptId)}</code>
        </article>`;
      }
      const surfaces = (prompt.surfaces || []).map((surface) => `<code>${aiControlEscape(surface)}</code>`).join(" ");
      const status = prompt.has_override
        ? '<span class="ai-control-badge">runtime override active</span>'
        : '<span class="ai-control-muted">Using source default.</span>';
      const sourceError = prompt.source_error
        ? `<div class="ai-control-warning">Source read error: ${aiControlEscape(prompt.source_error)}</div>`
        : "";
      return `<article class="ai-control-prompt-card" data-ai-control-prompt-card="${aiControlEscape(prompt.id)}">
        <div class="ai-control-prompt-card-head">
          <div>
            <h4>${aiControlEscape(prompt.title || prompt.id)}</h4>
            <p><code>${aiControlEscape(prompt.id)}</code> · ${aiControlEscape(prompt.source_file || "")} :: ${aiControlEscape(prompt.source_symbol || "")}</p>
          </div>
          <div class="ai-control-prompt-actions">
            ${status}
            <button type="button" data-ai-control-reset-prompt="${aiControlEscape(prompt.id)}"${prompt.has_override ? "" : " disabled"}>Reset</button>
            <button type="button" data-ai-control-save-prompt="${aiControlEscape(prompt.id)}" disabled>Save</button>
          </div>
        </div>
        ${sourceError}
        <p class="ai-control-description">${aiControlEscape(prompt.description || "")}</p>
        <div class="ai-control-prompt-meta-row"><strong>Used by</strong><span class="ai-control-surfaces">${surfaces || "none"}</span></div>
        <label class="ai-control-editor-label">Effective prompt text</label>
        <textarea class="ai-control-editor" spellcheck="false" data-ai-control-prompt-editor="${aiControlEscape(prompt.id)}">${aiControlEscape(prompt.effective_content || "")}</textarea>
        <details class="ai-control-default-details">
          <summary>Source default</summary>
          <pre>${aiControlEscape(prompt.default_content || "")}</pre>
        </details>
      </article>`;
    }

    function bindAiControlPromptEditors() {
      if (!aiControlDetail) return;
      aiControlDetail.querySelectorAll("[data-ai-control-prompt-editor]").forEach((textarea) => {
        const promptId = textarea.getAttribute("data-ai-control-prompt-editor") || "";
        textarea.addEventListener("input", () => aiControlUpdatePromptButtons(promptId));
        aiControlUpdatePromptButtons(promptId);
      });
    }

    function renderAiControlSurfaceDetail() {
      const structure = aiControlStructureById(aiControlSelectedSurfaceId);
      if (!aiControlDetail) return;
      if (!structure) {
        if (aiControlDetailTitle) aiControlDetailTitle.textContent = "Select an AI surface";
        if (aiControlDetailMeta) aiControlDetailMeta.textContent = "Message order and static prompt editors";
        aiControlDetail.innerHTML = '<div class="ai-control-empty">Select an AI surface to inspect how that call is prepared.</div>';
        return;
      }

      if (aiControlDetailTitle) aiControlDetailTitle.textContent = structure.title || structure.id;
      if (aiControlDetailMeta) aiControlDetailMeta.textContent = `${structure.id} · ${structure.source_file} :: ${structure.function}`;

      const slots = (structure.slots || []).map((slot, index) => renderAiControlSlot(slot, index)).join("");
      const promptIds = aiControlSurfacePromptIds(structure);
      const promptEditors = promptIds.length
        ? promptIds.map((promptId) => renderAiControlPromptEditor(promptId)).join("")
        : '<div class="ai-control-empty">This surface has no editable static prompt slots.</div>';

      aiControlDetail.innerHTML = `
        <section class="ai-control-surface-card">
          <h4>Message preparation</h4>
          <p>${aiControlEscape(structure.description || "")}</p>
          <dl>
            <div><dt>Surface</dt><dd><code>${aiControlEscape(structure.id || "")}</code></dd></div>
            <div><dt>Source</dt><dd>${aiControlEscape(structure.source_file || "")} :: ${aiControlEscape(structure.function || "")}</dd></div>
            <div><dt>Provider call</dt><dd><code>${aiControlEscape(structure.provider_call || "")}</code></dd></div>
          </dl>
          <ol class="ai-control-slot-list">${slots}</ol>
        </section>

        <section class="ai-control-prompt-editors">
          <div class="ai-control-section-head">
            <h4>Static prompts used by this surface</h4>
            <p>Edit the effective runtime text for this surface's named prompt slots. Defaults remain in source.</p>
          </div>
          ${promptEditors}
        </section>`;
      bindAiControlPromptEditors();
    }

    function renderAiControlCatalog() {
      const prompts = aiControlCatalog?.prompts || [];
      const structures = aiControlCatalog?.message_structures || [];
      if (aiControlSurfaceCount) aiControlSurfaceCount.textContent = String(structures.length);
      if (aiControlPromptCount) aiControlPromptCount.textContent = String(prompts.length);
      if (aiControlOverrideCount) aiControlOverrideCount.textContent = String(aiControlCatalog?.override_count || 0);
      if (aiControlSurfaceListSummary) {
        aiControlSurfaceListSummary.textContent = `${structures.length} surface${structures.length === 1 ? "" : "s"} registered`;
      }
      if (!aiControlSelectedSurfaceId && structures.length) {
        aiControlSelectedSurfaceId = structures[0].id;
      }
      if (aiControlSelectedSurfaceId && !aiControlStructureById(aiControlSelectedSurfaceId) && structures.length) {
        aiControlSelectedSurfaceId = structures[0].id;
      }
      renderAiControlSurfaceList();
      renderAiControlSurfaceDetail();
    }

    async function loadAiControlCatalog() {
      if (!aiControlApp) return;
      aiControlSetStatus("Loading AI prompt structure...");
      try {
        const response = await fetch("/api/applications/ai-control/prompts", {cache: "no-store"});
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        aiControlCatalog = data;
        aiControlLoaded = true;
        renderAiControlCatalog();
        aiControlSetStatus(`Loaded ${data.message_structures?.length || 0} AI surfaces, ${data.prompt_count || 0} static prompts, and ${data.override_count || 0} overrides.`);
      } catch (error) {
        aiControlSetStatus(`Failed to load AI prompt structure: ${error.message || error}`, true);
      }
    }

    async function saveAiControlPrompt(promptId) {
      const prompt = aiControlPromptById(promptId);
      const textarea = aiControlTextareaFor(promptId);
      if (!prompt || !textarea) return;
      aiControlSetStatus(`Saving override for ${prompt.id}...`);
      try {
        const response = await fetch("/api/applications/ai-control/prompts/override", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({id: prompt.id, content: textarea.value})
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        aiControlCatalog = data;
        renderAiControlCatalog();
        aiControlSetStatus(`Saved runtime override for ${prompt.id}.`);
      } catch (error) {
        aiControlSetStatus(`Failed to save override: ${error.message || error}`, true);
      }
    }

    async function resetAiControlPrompt(promptId) {
      const prompt = aiControlPromptById(promptId);
      if (!prompt) return;
      aiControlSetStatus(`Resetting override for ${prompt.id}...`);
      try {
        const response = await fetch("/api/applications/ai-control/prompts/override", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({id: prompt.id, reset: true})
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        aiControlCatalog = data;
        renderAiControlCatalog();
        aiControlSetStatus(`Reset ${prompt.id} to its source default.`);
      } catch (error) {
        aiControlSetStatus(`Failed to reset override: ${error.message || error}`, true);
      }
    }

    if (aiControlRefresh) {
      aiControlRefresh.addEventListener("click", () => loadAiControlCatalog());
    }
    if (aiControlSurfaceList) {
      aiControlSurfaceList.addEventListener("click", (event) => {
        const button = event.target.closest("[data-ai-control-surface-id]");
        if (!button) return;
        aiControlSelectedSurfaceId = button.getAttribute("data-ai-control-surface-id") || "";
        renderAiControlSurfaceList();
        renderAiControlSurfaceDetail();
      });
    }
    if (aiControlDetail) {
      aiControlDetail.addEventListener("click", (event) => {
        const saveButton = event.target.closest("[data-ai-control-save-prompt]");
        if (saveButton) {
          saveAiControlPrompt(saveButton.getAttribute("data-ai-control-save-prompt") || "");
          return;
        }
        const resetButton = event.target.closest("[data-ai-control-reset-prompt]");
        if (resetButton) {
          resetAiControlPrompt(resetButton.getAttribute("data-ai-control-reset-prompt") || "");
          return;
        }
        const jumpButton = event.target.closest("[data-ai-control-jump-prompt]");
        if (jumpButton) {
          const promptId = jumpButton.getAttribute("data-ai-control-jump-prompt") || "";
          const card = document.querySelector(`[data-ai-control-prompt-card="${aiControlCssEscape(promptId)}"]`);
          if (card) card.scrollIntoView({behavior: "smooth", block: "start"});
        }
      });
    }

    if (aiControlApp) {
      loadAiControlCatalog();
    }
