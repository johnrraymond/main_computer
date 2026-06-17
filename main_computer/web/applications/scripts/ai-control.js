    let aiControlLoaded = false;
    let aiControlCatalog = null;
    let aiControlProfiles = null;
    let aiControlSelectedSurfaceId = "";
    let aiControlSelectedProfileId = "";
    let aiControlSelectedComposableId = "";

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

    function aiControlSetStatus(message, isError = false) {
      if (!aiControlStatus) return;
      aiControlStatus.textContent = message;
      aiControlStatus.classList.toggle("error", Boolean(isError));
    }

    function aiControlPromptById(promptId) {
      const prompts = aiControlCatalog?.prompts || [];
      return prompts.find((prompt) => prompt.id === promptId) || null;
    }

    function aiControlStructureById(surfaceId) {
      const structures = aiControlCatalog?.message_structures || [];
      return structures.find((structure) => structure.id === surfaceId) || null;
    }

    function aiControlProfileById(profileId) {
      const profiles = aiControlProfiles?.profiles || [];
      return profiles.find((profile) => profile.id === profileId) || null;
    }

    function aiControlComposableById(composableId) {
      const composables = aiControlProfiles?.composables || [];
      return composables.find((item) => item.id === composableId) || null;
    }

    function aiControlShortText(value, limit = 110) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      if (!text || text.length <= limit) return text;
      return `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
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
      if (!aiControlDetail) return null;
      return aiControlDetail.querySelector(`[data-ai-control-prompt-editor="${aiControlCssEscape(promptId)}"]`);
    }

    function aiControlUpdatePromptButtons(promptId) {
      const prompt = aiControlPromptById(promptId);
      const textarea = aiControlTextareaFor(promptId);
      const saveButton = aiControlDetail?.querySelector(`[data-ai-control-save-prompt="${aiControlCssEscape(promptId)}"]`);
      const resetButton = aiControlDetail?.querySelector(`[data-ai-control-reset-prompt="${aiControlCssEscape(promptId)}"]`);
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

    function aiControlSurfaceOptionText(structure) {
      const promptIds = aiControlSurfacePromptIds(structure);
      const overrideCount = promptIds.filter((promptId) => aiControlPromptById(promptId)?.has_override).length;
      const title = structure?.title || structure?.id || "Unknown AI surface";
      const description = aiControlShortText(structure?.description || "No description available.", 96);
      const promptText = `${promptIds.length} prompt${promptIds.length === 1 ? "" : "s"}`;
      const overrideText = overrideCount
        ? `, ${overrideCount} override${overrideCount === 1 ? "" : "s"}`
        : "";
      return `${title} — ${description} (${promptText}${overrideText})`;
    }

    function aiControlRenderSelectedSurfaceDescription(structure) {
      if (!aiControlSurfaceDescription) return;
      if (!structure) {
        aiControlSurfaceDescription.textContent = "Pick an AI surface to see what call path it represents and which static prompts prepare it.";
        return;
      }
      const promptIds = aiControlSurfacePromptIds(structure);
      const promptNames = promptIds.map((promptId) => aiControlPromptLabel(promptId)).join(", ") || "no static prompt slots";
      aiControlSurfaceDescription.innerHTML = `
        <strong>${aiControlEscape(structure.title || structure.id)}</strong>
        <span>${aiControlEscape(structure.description || "No description available.")}</span>
        <small>${aiControlEscape(structure.source_file || "")} :: ${aiControlEscape(structure.function || "")} · ${aiControlEscape(promptNames)}</small>
      `;
    }

    function renderAiControlSurfacePicker() {
      const structures = aiControlCatalog?.message_structures || [];
      if (aiControlSurfaceListSummary) {
        const promptCount = aiControlCatalog?.prompt_count || 0;
        const overrideCount = aiControlCatalog?.override_count || 0;
        aiControlSurfaceListSummary.textContent = `${structures.length} surfaces · ${promptCount} prompts · ${overrideCount} overrides`;
      }
      if (!aiControlSurfaceSelect) return;
      if (!structures.length) {
        aiControlSurfaceSelect.innerHTML = '<option value="">No AI surfaces registered</option>';
        aiControlSurfaceSelect.disabled = true;
        aiControlRenderSelectedSurfaceDescription(null);
        return;
      }
      aiControlSurfaceSelect.disabled = false;
      aiControlSurfaceSelect.innerHTML = structures.map((structure) => {
        const optionText = aiControlSurfaceOptionText(structure);
        const optionTitle = [
          structure.title || structure.id,
          structure.id,
          structure.description || "",
          `${structure.source_file || ""} :: ${structure.function || ""}`,
        ].filter(Boolean).join(" — ");
        return `<option value="${aiControlEscape(structure.id || "")}" title="${aiControlEscape(optionTitle)}">${aiControlEscape(optionText)}</option>`;
      }).join("");
      aiControlSurfaceSelect.value = aiControlSelectedSurfaceId || structures[0]?.id || "";
      aiControlSelectedSurfaceId = aiControlSurfaceSelect.value;
      aiControlRenderSelectedSurfaceDescription(aiControlStructureById(aiControlSelectedSurfaceId));
    }

    function aiControlRenderSlots(structure) {
      const slots = structure?.slots || [];
      if (!slots.length) {
        return '<div class="ai-control-empty">No message slots registered for this surface.</div>';
      }
      return `
        <ol class="ai-control-slot-list">
          ${slots.map((slot, index) => {
            const promptLabel = slot.prompt_id
              ? `<button type="button" class="ai-control-inline-prompt-link" data-ai-control-jump-prompt="${aiControlEscape(slot.prompt_id)}">${aiControlEscape(aiControlPromptLabel(slot.prompt_id))}${aiControlPromptBadge(slot.prompt_id)}</button>`
              : "";
            return `
              <li>
                <span class="ai-control-slot-index">${index + 1}</span>
                <span class="ai-control-slot-role">${aiControlEscape(slot.role || "")}</span>
                <span class="ai-control-slot-kind ${slot.optional ? "optional" : ""}">${aiControlEscape(slot.kind || "")}${slot.optional ? " optional" : ""}</span>
                <span>${aiControlEscape(slot.label || "")}${promptLabel}</span>
              </li>
            `;
          }).join("")}
        </ol>
      `;
    }

    function aiControlRenderPromptEditor(promptId) {
      const prompt = aiControlPromptById(promptId);
      if (!prompt) {
        return `<div class="ai-control-warning">Unknown prompt id: ${aiControlEscape(promptId)}</div>`;
      }
      const surfaces = (prompt.surfaces || []).map((surface) => `<code>${aiControlEscape(surface)}</code>`).join(" ");
      return `
        <article class="ai-control-prompt-card" data-ai-control-prompt-card="${aiControlEscape(prompt.id)}">
          <div class="ai-control-prompt-card-head">
            <div>
              <h4>${aiControlEscape(prompt.title || prompt.id)} ${aiControlPromptBadge(prompt.id)}</h4>
              <p class="ai-control-muted">${aiControlEscape(prompt.description || "")}</p>
            </div>
            <div class="ai-control-prompt-actions">
              <button type="button" data-ai-control-save-prompt="${aiControlEscape(prompt.id)}" disabled>Save Override</button>
              <button type="button" data-ai-control-reset-prompt="${aiControlEscape(prompt.id)}" ${prompt.has_override ? "" : "disabled"}>Reset</button>
            </div>
          </div>
          <div class="ai-control-prompt-meta">
            <div class="ai-control-prompt-meta-row"><strong>Prompt id</strong><code>${aiControlEscape(prompt.id)}</code></div>
            <div class="ai-control-prompt-meta-row"><strong>Source</strong><code>${aiControlEscape(prompt.source_file || "")} :: ${aiControlEscape(prompt.source_symbol || "")}</code></div>
            <div class="ai-control-prompt-meta-row"><strong>Surfaces</strong><span class="ai-control-surfaces">${surfaces || "none"}</span></div>
          </div>
          ${prompt.source_error ? `<div class="ai-control-warning">${aiControlEscape(prompt.source_error)}</div>` : ""}
          <label class="ai-control-editor-label" for="ai-control-editor-${aiControlCssEscape(prompt.id)}">Effective static prompt text</label>
          <textarea
            class="ai-control-editor"
            id="ai-control-editor-${aiControlEscape(prompt.id)}"
            data-ai-control-prompt-editor="${aiControlEscape(prompt.id)}"
            spellcheck="false"
          >${aiControlEscape(prompt.effective_content || "")}</textarea>
          <details class="ai-control-default-details">
            <summary>Source default</summary>
            <pre>${aiControlEscape(prompt.default_content || "")}</pre>
          </details>
        </article>
      `;
    }

    function renderAiControlSurfaceDetail() {
      if (!aiControlDetail) return;
      const structure = aiControlStructureById(aiControlSelectedSurfaceId);
      if (!structure) {
        aiControlDetail.innerHTML = '<div class="ai-control-empty">Select an AI surface to inspect how that call is prepared.</div>';
        if (aiControlDetailTitle) aiControlDetailTitle.textContent = "Select an AI surface";
        if (aiControlDetailMeta) aiControlDetailMeta.textContent = "Message order and editable static prompts";
        aiControlRenderSelectedSurfaceDescription(null);
        return;
      }
      const promptIds = aiControlSurfacePromptIds(structure);
      if (aiControlDetailTitle) aiControlDetailTitle.textContent = structure.title || structure.id;
      if (aiControlDetailMeta) aiControlDetailMeta.textContent = `${structure.source_file || ""} :: ${structure.function || ""}`;
      aiControlRenderSelectedSurfaceDescription(structure);
      aiControlDetail.innerHTML = `
        <article class="ai-control-surface-card">
          <h4>Message preparation</h4>
          <p>${aiControlEscape(structure.description || "")}</p>
          <dl>
            <div><dt>Surface id</dt><dd><code>${aiControlEscape(structure.id || "")}</code></dd></div>
            <div><dt>Provider call</dt><dd><code>${aiControlEscape(structure.provider_call || "")}</code></dd></div>
            <div><dt>Function</dt><dd><code>${aiControlEscape(structure.source_file || "")} :: ${aiControlEscape(structure.function || "")}</code></dd></div>
          </dl>
          <h4>Message slot order</h4>
          ${aiControlRenderSlots(structure)}
        </article>

        <div class="ai-control-prompt-editors">
          <div class="ai-control-section-head">
            <div>
              <h4>Static prompts used by this surface</h4>
              <p>Runtime overrides are explicit and reversible. They do not edit the source file.</p>
            </div>
          </div>
          ${promptIds.length ? promptIds.map((promptId) => aiControlRenderPromptEditor(promptId)).join("") : '<div class="ai-control-empty">This surface has no static prompt slots.</div>'}
        </div>
      `;
    }

    function aiControlRenderProfilePicker() {
      const profiles = aiControlProfiles?.profiles || [];
      if (aiControlProfileSummary) {
        aiControlProfileSummary.textContent = `${profiles.length} profiles · ${aiControlProfiles?.composable_count || 0} choices`;
      }
      if (aiControlActiveProfileLabel) {
        const active = aiControlProfiles?.active_profile;
        aiControlActiveProfileLabel.textContent = active ? `active: ${active.name || active.id}` : "no active profile";
      }
      if (!aiControlProfileSelect) return;
      if (!profiles.length) {
        aiControlProfileSelect.innerHTML = '<option value="">No profiles available</option>';
        aiControlProfileSelect.disabled = true;
        return;
      }
      aiControlProfileSelect.disabled = false;
      if (!aiControlSelectedProfileId || !aiControlProfileById(aiControlSelectedProfileId)) {
        aiControlSelectedProfileId = aiControlProfiles?.active_profile_id || profiles[0].id;
      }
      aiControlProfileSelect.innerHTML = profiles.map((profile) => {
        const activeMark = profile.is_active ? "active · " : "";
        const sourceText = profile.is_factory ? "factory" : "user";
        const changedText = profile.has_override || profile.has_profile_choice_overrides ? ", edited" : "";
        const title = [
          profile.name || profile.id,
          profile.description || "",
          `${sourceText}${changedText}`,
        ].filter(Boolean).join(" — ");
        return `<option value="${aiControlEscape(profile.id)}" title="${aiControlEscape(title)}">${aiControlEscape(profile.name || profile.id)} — ${activeMark}${sourceText}${changedText} (${(profile.enabled_composable_ids || []).length} enabled)</option>`;
      }).join("");
      aiControlProfileSelect.value = aiControlSelectedProfileId;
      aiControlRenderProfileEditor();
    }

    function aiControlProfileFormEnabledIds() {
      if (!aiControlComposableList) return [];
      return Array.from(aiControlComposableList.querySelectorAll("[data-ai-control-profile-choice]:checked"))
        .map((checkbox) => checkbox.getAttribute("data-ai-control-profile-choice") || "")
        .filter(Boolean);
    }

    function aiControlProfileFormChoiceOverrides() {
      const overrides = {};
      if (!aiControlComposableList) return overrides;
      aiControlComposableList.querySelectorAll("[data-ai-control-profile-choice-card]").forEach((card) => {
        const id = card.getAttribute("data-ai-control-profile-choice-card") || "";
        if (!id) return;
        const payload = {};
        for (const field of ["label", "kind", "description", "prompt_text"]) {
          const input = card.querySelector(`[data-ai-control-choice-field="${field}"]`);
          if (!input) continue;
          const base = input.getAttribute("data-ai-control-base-value") || "";
          const value = input.value || "";
          if (value !== base) {
            payload[field] = value;
          }
        }
        if (Object.keys(payload).length) {
          overrides[id] = payload;
        }
      });
      return overrides;
    }

    function aiControlProfileChoicePrompt(id) {
      const card = aiControlComposableList?.querySelector(`[data-ai-control-profile-choice-card="${aiControlCssEscape(id)}"]`);
      if (!card) return "";
      const prompt = card.querySelector('[data-ai-control-choice-field="prompt_text"]');
      return prompt?.value || "";
    }

    function aiControlCompilePreviewFromForm() {
      const profile = aiControlProfileById(aiControlSelectedProfileId);
      if (!profile) return "";
      const name = aiControlProfileNameInput?.value || profile.name || profile.id;
      const description = aiControlProfileDescriptionInput?.value || "";
      const ids = aiControlProfileFormEnabledIds();
      const lines = [`User treatment profile: ${name}`];
      if (description.trim()) lines.push("", description.trim());
      if (ids.length) {
        lines.push("", "Enabled profile choices:");
        for (const id of ids) {
          const promptText = aiControlProfileChoicePrompt(id).trim();
          if (promptText) lines.push(`- ${promptText}`);
        }
      } else {
        lines.push("", "No profile choices are enabled yet.");
      }
      return lines.join("\n");
    }

    function aiControlRefreshProfilePreview() {
      if (aiControlProfilePreview) {
        aiControlProfilePreview.value = aiControlCompilePreviewFromForm();
      }
    }

    function aiControlRenderProfileDescription(profile) {
      if (!aiControlProfileDescription) return;
      if (!profile) {
        aiControlProfileDescription.textContent = "Pick a profile to see and edit its full set of choices.";
        return;
      }
      const sourceText = profile.is_factory ? "Factory profile" : "User profile";
      const changedText = profile.has_override || profile.has_profile_choice_overrides ? " · edited" : "";
      aiControlProfileDescription.innerHTML = `
        <strong>${aiControlEscape(profile.name || profile.id)}</strong>
        <span>${aiControlEscape(profile.description || "No description available.")}</span>
        <small>${aiControlEscape(sourceText + changedText)} · ${aiControlEscape(profile.id)}</small>
      `;
    }

    function aiControlChoiceCard(choice) {
      const sourceText = choice.is_factory ? "factory" : "user";
      const globalEditMark = choice.has_override ? " · global edit" : "";
      const profileEditMark = choice.profile_has_override ? " · profile overlay" : "";
      const checked = choice.enabled ? "checked" : "";
      return `
        <article class="ai-control-choice-card" data-ai-control-profile-choice-card="${aiControlEscape(choice.id)}">
          <div class="ai-control-choice-card-head">
            <label class="ai-control-choice-check">
              <input type="checkbox" data-ai-control-profile-choice="${aiControlEscape(choice.id)}" ${checked}>
              <span>
                <strong>${aiControlEscape(choice.label || choice.id)}</strong>
                <small>${aiControlEscape(choice.kind || "choice")} · ${sourceText}${globalEditMark}${profileEditMark}</small>
              </span>
            </label>
            <button type="button" data-ai-control-reset-profile-choice="${aiControlEscape(choice.id)}" ${choice.profile_has_override ? "" : "disabled"}>Reset choice text</button>
          </div>
          <div class="ai-control-choice-fields">
            <label>
              <span>Label</span>
              <input type="text" data-ai-control-choice-field="label" data-ai-control-base-value="${aiControlEscape(choice.base_label ?? choice.label ?? "")}" value="${aiControlEscape(choice.label || "")}">
            </label>
            <label>
              <span>Kind</span>
              <input type="text" data-ai-control-choice-field="kind" data-ai-control-base-value="${aiControlEscape(choice.base_kind ?? choice.kind ?? "")}" value="${aiControlEscape(choice.kind || "")}">
            </label>
            <label>
              <span>Description</span>
              <textarea rows="2" data-ai-control-choice-field="description" data-ai-control-base-value="${aiControlEscape(choice.base_description ?? choice.description ?? "")}">${aiControlEscape(choice.description || "")}</textarea>
            </label>
            <label>
              <span>Prompt text for this profile</span>
              <textarea rows="4" data-ai-control-choice-field="prompt_text" data-ai-control-base-value="${aiControlEscape(choice.base_prompt_text ?? choice.prompt_text ?? "")}">${aiControlEscape(choice.prompt_text || "")}</textarea>
            </label>
          </div>
        </article>
      `;
    }

    function aiControlRenderComposableList(profile) {
      if (!aiControlComposableList) return;
      if (!profile) {
        aiControlComposableList.innerHTML = '<div class="ai-control-empty">Select a profile before editing choices.</div>';
        return;
      }
      const choices = profile.choices || [];
      aiControlComposableList.innerHTML = choices.map(aiControlChoiceCard).join("") || '<div class="ai-control-empty">No profile choices exist yet.</div>';
    }

    function aiControlRenderProfileEditor() {
      const profile = aiControlProfileById(aiControlSelectedProfileId);
      aiControlRenderProfileDescription(profile);
      if (aiControlProfileNameInput) aiControlProfileNameInput.value = profile?.name || "";
      if (aiControlProfileDescriptionInput) aiControlProfileDescriptionInput.value = profile?.description || "";
      aiControlRenderComposableList(profile);
      aiControlRefreshProfilePreview();

      if (aiControlSetActiveProfile) aiControlSetActiveProfile.disabled = !profile || profile.is_active;
      if (aiControlSaveProfile) aiControlSaveProfile.disabled = !profile && !aiControlProfileNameInput?.value;
      if (aiControlDuplicateProfile) aiControlDuplicateProfile.disabled = !profile;
      if (aiControlResetProfile) aiControlResetProfile.disabled = !(profile && profile.is_factory && (profile.has_override || profile.has_profile_choice_overrides));
      if (aiControlDeleteProfile) aiControlDeleteProfile.disabled = !(profile && profile.can_delete);
    }

    function aiControlClearComposableForm() {
      aiControlSelectedComposableId = "";
      if (aiControlComposableIdInput) aiControlComposableIdInput.value = "";
      if (aiControlComposableLabelInput) aiControlComposableLabelInput.value = "";
      if (aiControlComposableKindInput) aiControlComposableKindInput.value = "framing";
      if (aiControlComposableDescriptionInput) aiControlComposableDescriptionInput.value = "";
      if (aiControlComposablePromptInput) aiControlComposablePromptInput.value = "";
      if (aiControlResetComposable) aiControlResetComposable.disabled = true;
      if (aiControlDeleteComposable) aiControlDeleteComposable.disabled = true;
    }

    function renderAiControlProfiles() {
      aiControlRenderProfilePicker();
      if (!aiControlSelectedComposableId) aiControlClearComposableForm();
    }

    function renderAiControlCatalog() {
      renderAiControlProfiles();
      renderAiControlSurfacePicker();
      renderAiControlSurfaceDetail();
    }

    async function aiControlLoadJson(url) {
      const response = await fetch(url, {cache: "no-store"});
      const data = await response.json();
      if (!response.ok || data.ok === false) {
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      return data;
    }

    async function loadAiControlCatalog() {
      if (!aiControlApp) return;
      aiControlSetStatus("Loading AI Control profile and prompt structure...");
      try {
        const [promptData, profileData] = await Promise.all([
          aiControlLoadJson("/api/applications/ai-control/prompts"),
          aiControlLoadJson("/api/applications/ai-control/profiles"),
        ]);
        aiControlCatalog = promptData;
        aiControlProfiles = profileData;
        aiControlLoaded = true;
        renderAiControlCatalog();
        aiControlSetStatus(`Loaded ${profileData.profile_count || 0} profiles, ${profileData.composable_count || 0} choices, ${promptData.message_structures?.length || 0} AI surfaces, and ${promptData.prompt_count || 0} static prompts.`);
      } catch (error) {
        aiControlSetStatus(`Failed to load AI Control: ${error.message || error}`, true);
      }
    }

    async function aiControlPostProfileAction(payload, successMessage) {
      aiControlSetStatus("Saving AI Control profile state...");
      try {
        const response = await fetch("/api/applications/ai-control/profiles/action", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        aiControlProfiles = data;
        renderAiControlCatalog();
        aiControlSetStatus(successMessage || "Saved AI Control profile state.");
      } catch (error) {
        aiControlSetStatus(`Failed to save profile state: ${error.message || error}`, true);
      }
    }

    function aiControlCurrentProfilePayload(action) {
      return {
        action,
        profile_id: aiControlSelectedProfileId,
        name: aiControlProfileNameInput?.value || "",
        description: aiControlProfileDescriptionInput?.value || "",
        enabled_composable_ids: aiControlProfileFormEnabledIds(),
        composable_overrides: aiControlProfileFormChoiceOverrides(),
        set_active: !aiControlSelectedProfileId,
      };
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
    if (aiControlSurfaceSelect) {
      aiControlSurfaceSelect.addEventListener("change", () => {
        aiControlSelectedSurfaceId = aiControlSurfaceSelect.value || "";
        renderAiControlCatalog();
      });
    }
    if (aiControlProfileSelect) {
      aiControlProfileSelect.addEventListener("change", () => {
        aiControlSelectedProfileId = aiControlProfileSelect.value || "";
        renderAiControlProfiles();
      });
    }
    for (const input of [aiControlProfileNameInput, aiControlProfileDescriptionInput]) {
      if (input) input.addEventListener("input", () => aiControlRefreshProfilePreview());
    }
    if (aiControlComposableList) {
      aiControlComposableList.addEventListener("change", (event) => {
        if (event.target.closest("[data-ai-control-profile-choice], [data-ai-control-choice-field]")) {
          aiControlRefreshProfilePreview();
        }
      });
      aiControlComposableList.addEventListener("input", (event) => {
        if (event.target.closest("[data-ai-control-choice-field]")) {
          const card = event.target.closest("[data-ai-control-profile-choice-card]");
          const resetButton = card?.querySelector("[data-ai-control-reset-profile-choice]");
          if (resetButton) {
            resetButton.disabled = !Object.keys(aiControlProfileFormChoiceOverrides()[card.getAttribute("data-ai-control-profile-choice-card") || ""] || {}).length;
          }
          aiControlRefreshProfilePreview();
        }
      });
      aiControlComposableList.addEventListener("click", (event) => {
        const resetButton = event.target.closest("[data-ai-control-reset-profile-choice]");
        if (resetButton) {
          const card = resetButton.closest("[data-ai-control-profile-choice-card]");
          if (!card) return;
          card.querySelectorAll("[data-ai-control-choice-field]").forEach((input) => {
            input.value = input.getAttribute("data-ai-control-base-value") || "";
          });
          resetButton.disabled = true;
          aiControlRefreshProfilePreview();
        }
      });
    }
    if (aiControlNewProfile) {
      aiControlNewProfile.addEventListener("click", () => {
        aiControlSelectedProfileId = "";
        if (aiControlProfileNameInput) aiControlProfileNameInput.value = "New Profile";
        if (aiControlProfileDescriptionInput) aiControlProfileDescriptionInput.value = "";
        if (aiControlComposableList) {
          aiControlComposableList.querySelectorAll("[data-ai-control-profile-choice]").forEach((box) => { box.checked = false; });
          aiControlComposableList.querySelectorAll("[data-ai-control-choice-field]").forEach((input) => {
            input.value = input.getAttribute("data-ai-control-base-value") || "";
          });
        }
        aiControlRefreshProfilePreview();
        aiControlSetStatus("Editing a new profile. Choose composables, then Save Profile.");
      });
    }
    if (aiControlDuplicateProfile) {
      aiControlDuplicateProfile.addEventListener("click", () => {
        if (!aiControlSelectedProfileId) return;
        aiControlPostProfileAction({action: "duplicate_profile", profile_id: aiControlSelectedProfileId}, "Duplicated profile and made the copy active in AI Control state.");
      });
    }
    if (aiControlSetActiveProfile) {
      aiControlSetActiveProfile.addEventListener("click", () => {
        if (!aiControlSelectedProfileId) return;
        aiControlPostProfileAction({action: "set_active_profile", profile_id: aiControlSelectedProfileId}, "Set active system profile in AI Control state.");
      });
    }
    if (aiControlSaveProfile) {
      aiControlSaveProfile.addEventListener("click", () => {
        const payload = aiControlCurrentProfilePayload("save_profile");
        if (!payload.name.trim()) {
          aiControlSetStatus("Profile name is required.", true);
          return;
        }
        aiControlPostProfileAction(payload, "Saved profile.");
      });
    }
    if (aiControlResetProfile) {
      aiControlResetProfile.addEventListener("click", () => {
        if (!aiControlSelectedProfileId) return;
        aiControlPostProfileAction({action: "reset_profile", profile_id: aiControlSelectedProfileId}, "Reset factory profile to its built-in settings.");
      });
    }
    if (aiControlDeleteProfile) {
      aiControlDeleteProfile.addEventListener("click", () => {
        if (!aiControlSelectedProfileId) return;
        aiControlPostProfileAction({action: "delete_profile", profile_id: aiControlSelectedProfileId}, "Deleted user profile.");
      });
    }
    if (aiControlSaveComposable) {
      aiControlSaveComposable.addEventListener("click", () => {
        const label = aiControlComposableLabelInput?.value || "";
        if (!label.trim()) {
          aiControlSetStatus("Choice label is required.", true);
          return;
        }
        aiControlPostProfileAction({
          action: "save_composable",
          composable_id: aiControlComposableIdInput?.value || "",
          label,
          kind: aiControlComposableKindInput?.value || "user_defined",
          description: aiControlComposableDescriptionInput?.value || "",
          prompt_text: aiControlComposablePromptInput?.value || "",
        }, "Saved composable choice.");
      });
    }
    if (aiControlResetComposable) {
      aiControlResetComposable.addEventListener("click", () => {
        const id = aiControlComposableIdInput?.value || "";
        if (!id) return;
        aiControlPostProfileAction({action: "reset_composable", composable_id: id}, "Reset factory choice to its built-in text.");
      });
    }
    if (aiControlDeleteComposable) {
      aiControlDeleteComposable.addEventListener("click", () => {
        const id = aiControlComposableIdInput?.value || "";
        if (!id) return;
        aiControlPostProfileAction({action: "delete_composable", composable_id: id}, "Deleted user-defined choice.");
      });
    }
    if (aiControlClearComposable) {
      aiControlClearComposable.addEventListener("click", () => aiControlClearComposableForm());
    }
    if (aiControlDetail) {
      aiControlDetail.addEventListener("input", (event) => {
        const textarea = event.target.closest("[data-ai-control-prompt-editor]");
        if (textarea) {
          aiControlUpdatePromptButtons(textarea.getAttribute("data-ai-control-prompt-editor") || "");
        }
      });
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
          const card = aiControlDetail.querySelector(`[data-ai-control-prompt-card="${aiControlCssEscape(promptId)}"]`);
          if (card) card.scrollIntoView({behavior: "smooth", block: "start"});
        }
      });
    }

    if (aiControlApp) {
      loadAiControlCatalog();
    }
