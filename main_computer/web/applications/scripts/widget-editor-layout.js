    function ensureWidgetEditorChrome() {
      let paneCreated = false;
      if (!widgetEditorRoot) {
        widgetEditorRoot = document.createElement("div");
        widgetEditorRoot.id = "mc-widget-editor-root";
        document.body.append(widgetEditorRoot);
      }
      if (!widgetEditorSelection) {
        widgetEditorSelection = document.createElement("div");
        widgetEditorSelection.className = "mc-widget-selection";
        widgetEditorSelection.hidden = true;
        widgetEditorRoot.append(widgetEditorSelection);
      }
      if (!widgetEditorDockPreview) {
        widgetEditorDockPreview = document.createElement("div");
        widgetEditorDockPreview.className = "mc-widget-dock-preview";
        widgetEditorDockPreview.hidden = true;
        widgetEditorRoot.append(widgetEditorDockPreview);
      }
      if (!widgetEditorPane) {
        widgetEditorPane = document.createElement("section");
        widgetEditorPane.id = "mc-widget-editor-pane";
        widgetEditorPane.setAttribute("aria-label", "Main Computer widget editor");
        widgetEditorRoot.append(widgetEditorPane);
        paneCreated = true;
      }
      if (paneCreated) {
        renderWidgetEditorPane();
        applyWidgetEditorPanePlacement();
      }
    }

    function getEditableWidgets() {
      return [...document.querySelectorAll("[data-mc-widget-id]")].filter((element) => {
        if (element.closest("#mc-widget-editor-root")) return false;
        if (element.closest("[hidden]")) return false;
        const codeEditorHost = element.closest("#code-editor-app");
        if (codeEditorHost) {
          const hostStyle = getComputedStyle(codeEditorHost);
          if (hostStyle.display === "none" || hostStyle.visibility === "hidden" || codeEditorHost.offsetParent === null) return false;
        }
        const style = getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
        if (element.offsetParent === null && style.position !== "fixed") return false;
        const rect = element.getBoundingClientRect();
        if (rect.width < 4 || rect.height < 4) return false;
        if (rect.bottom < 0 || rect.right < 0) return false;
        if (rect.top > window.innerHeight || rect.left > window.innerWidth) return false;
        const visibleRect = widgetEditorVisibleRect(element);
        if (!visibleRect || visibleRect.width < 4 || visibleRect.height < 4) return false;
        return true;
      });
    }

    function widgetEditorVisibleRect(element) {
      let rect = element.getBoundingClientRect();
      let left = Math.max(0, rect.left);
      let top = Math.max(0, rect.top);
      let right = Math.min(window.innerWidth, rect.right);
      let bottom = Math.min(window.innerHeight, rect.bottom);
      for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        if (parent.closest?.("#mc-widget-editor-root")) continue;
        const style = getComputedStyle(parent);
        const clips = /(auto|scroll|hidden|clip)/.test(`${style.overflow} ${style.overflowX} ${style.overflowY}`);
        if (!clips) continue;
        const parentRect = parent.getBoundingClientRect();
        left = Math.max(left, parentRect.left);
        top = Math.max(top, parentRect.top);
        right = Math.min(right, parentRect.right);
        bottom = Math.min(bottom, parentRect.bottom);
        if (right <= left || bottom <= top) return null;
      }
      return {left, top, right, bottom, width: right - left, height: bottom - top};
    }

    function refreshWidgetEditorChrome() {
      ensureWidgetEditorChrome();
      widgetEditorRoot.querySelectorAll(".mc-widget-handle").forEach((handle) => handle.remove());
      updateWidgetEditorSelection();
    }

    function refreshWidgetEditorHandles() {
      refreshWidgetEditorChrome();
    }

    function selectWidgetEditorTarget(element) {
      selectedWidgetEditorTarget = MainComputerWidgets.ownerWidgetForTarget(element);
      renderWidgetEditorPane();
      updateWidgetEditorSelection();
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh();
    }

    function openWidgetEditorFor(element) {
      selectWidgetEditorTarget(element);
      if (!widgetEditorPane) ensureWidgetEditorChrome();
      widgetEditorPane.classList.add("open");
      applyWidgetEditorPanePlacement();
    }

    function handleWidgetEditorCtrlClick(event) {
      if (!(event.ctrlKey || event.metaKey)) return;
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target || target.closest("#mc-widget-editor-root")) return;
      const widget = MainComputerWidgets.ownerWidgetForTarget(target);
      if (!widget) return;
      event.preventDefault();
      event.stopPropagation();
      if (target.matches("input, textarea, select, button")) target.blur();
      openWidgetEditorFor(widget);
    }

    function closeWidgetEditor() {
      if (widgetEditorPane) widgetEditorPane.classList.remove("open");
    }

    function widgetEditorTargetOverride() {
      if (!selectedWidgetEditorTarget) return {};
      const id = selectedWidgetEditorTarget.dataset.mcWidgetId;
      return sanitizeWidgetEditorOverride(selectedWidgetEditorTarget, widgetEditorOverrides[id] || {});
    }

    function widgetEditorResolvedSettings(element) {
      return MainComputerWidgets.read(element);
    }

    function normalizeWidgetEditorPreset(value, options, fallback) {
      const raw = String(value ?? "").trim();
      return options.some(([option]) => option === raw) ? raw : fallback;
    }

    function sanitizeWidgetEditorOverride(element, override = {}) {
      return MainComputerWidgets.sanitize(element, override);
    }

    function normalizeWidgetEditorOverridePatch(patch) {
      return sanitizeWidgetEditorOverride(selectedWidgetEditorTarget, patch);
    }

    function rememberWidgetEditorFocus() {
      const active = document.activeElement;
      if (!widgetEditorPane || !widgetEditorPane.contains(active)) return null;
      return {
        field: active.dataset?.widgetEditorField || "",
        selectionStart: typeof active.selectionStart === "number" ? active.selectionStart : null,
        selectionEnd: typeof active.selectionEnd === "number" ? active.selectionEnd : null
      };
    }

    function restoreWidgetEditorFocus(snapshot) {
      if (!snapshot?.field || !widgetEditorPane) return;
      const field = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(snapshot.field) : snapshot.field.replace(/"/g, '\\"');
      const control = widgetEditorPane.querySelector(`[data-widget-editor-field="${field}"]`);
      if (!control) return;
      control.focus();
      if (snapshot.selectionStart != null && typeof control.setSelectionRange === "function") {
        control.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd ?? snapshot.selectionStart);
      }
    }

    function widgetEditorSetSelectedOverride(patch) {
      if (!selectedWidgetEditorTarget) return;
      const id = selectedWidgetEditorTarget.dataset.mcWidgetId;
      const normalizedPatch = normalizeWidgetEditorOverridePatch(patch);
      if (!Object.keys(normalizedPatch).length) return;
      widgetEditorOverrides[id] = sanitizeWidgetEditorOverride(selectedWidgetEditorTarget, {...(widgetEditorOverrides[id] || {}), ...normalizedPatch});
      Object.keys(widgetEditorOverrides[id]).forEach((key) => {
        if (widgetEditorOverrides[id][key] === "" || widgetEditorOverrides[id][key] === null) {
          delete widgetEditorOverrides[id][key];
        }
      });
      if (!Object.keys(widgetEditorOverrides[id]).length) delete widgetEditorOverrides[id];
      widgetEditorSaveOverrides();
      applyWidgetOverride(selectedWidgetEditorTarget, widgetEditorOverrides[id] || {});
      updateWidgetEditorSelection();
      if (
        "density" in normalizedPatch ||
        "layoutPreset" in normalizedPatch ||
        "widthPreset" in normalizedPatch ||
        "minHeightPreset" in normalizedPatch ||
        "overflowPreset" in normalizedPatch ||
        "itemDisplayPreset" in normalizedPatch ||
        "visible" in normalizedPatch
      ) {
        if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 120});
      }
    }

    function renderWidgetEditorSelectField(name, label, value, options) {
      const optionHtml = options.map(([optionValue, optionLabel]) => `<option value="${escapeHtml(optionValue)}" ${value === optionValue ? "selected" : ""}>${escapeHtml(optionLabel)}</option>`).join("");
      return `<label class="mc-widget-editor-field">${label}<select data-widget-editor-field="${name}">${optionHtml}</select></label>`;
    }

    function renderWidgetEditorSchemaFields(target, schema, resolved, override) {
      const fieldHtml = [];
      const checked = (key, defaultValue = false) => Boolean(resolved[key] ?? defaultValue) ? "checked" : "";
      const overrideValue = (key) => escapeHtml(override[key] ?? "");
      for (const field of schema.fields) {
        if (field === "label") {
          fieldHtml.push(`<label class="mc-widget-editor-field">label override<input data-widget-editor-field="label" value="${overrideValue("label")}" placeholder="${escapeHtml(target.dataset.mcWidgetLabel || "")}"></label>`);
        } else if (field === "visible") {
          fieldHtml.push(`<label class="mc-widget-editor-field">visible<input data-widget-editor-field="visible" type="checkbox" ${checked("visible", true)}></label>`);
        } else if (field === "disabled" && "disabled" in target) {
          fieldHtml.push(`<label class="mc-widget-editor-field">disabled<input data-widget-editor-field="disabled" type="checkbox" ${checked("disabled")} ></label>`);
        } else if (field === "placeholder" && "placeholder" in target) {
          fieldHtml.push(`<label class="mc-widget-editor-field">placeholder<input data-widget-editor-field="placeholder" value="${escapeHtml(resolved.placeholder || "")}" placeholder="${escapeHtml(target.getAttribute("placeholder") || "")}"></label>`);
        } else if (field === "density") {
          fieldHtml.push(renderWidgetEditorSelectField("density", "density preset", resolved.density, widgetEditorPresetOptions.density));
        } else if (field === "layoutPreset") {
          fieldHtml.push(renderWidgetEditorSelectField("layoutPreset", "layout preset", resolved.layoutPreset, widgetEditorPresetOptions.layoutPreset));
        } else if (field === "widthPreset") {
          fieldHtml.push(renderWidgetEditorSelectField("widthPreset", "width preset", resolved.widthPreset, widgetEditorPresetOptions.widthPreset));
        } else if (field === "minHeightPreset") {
          fieldHtml.push(renderWidgetEditorSelectField("minHeightPreset", "min-height preset", resolved.minHeightPreset, widgetEditorPresetOptions.minHeightPreset));
        } else if (field === "overflowPreset") {
          fieldHtml.push(renderWidgetEditorSelectField("overflowPreset", "overflow preset", resolved.overflowPreset, widgetEditorPresetOptions.overflowPreset));
        } else if (field === "itemDisplayPreset") {
          fieldHtml.push(renderWidgetEditorSelectField("itemDisplayPreset", "item display preset", resolved.itemDisplayPreset, widgetEditorPresetOptions.itemDisplayPreset));
        }
      }
      return fieldHtml.join("");
    }

    function renderWidgetEditorPane() {
      if (!widgetEditorPane) return;
      const focusSnapshot = rememberWidgetEditorFocus();
      const target = selectedWidgetEditorTarget;
      const label = target?.dataset.mcWidgetLabel || "No widget selected";
      const id = target?.dataset.mcWidgetId || "";
      const widgetKind = target ? MainComputerWidgets.getKind(target) : "";
      const schema = resolveWidgetEditorSchema(target);
      const override = widgetEditorTargetOverride();
      const resolved = widgetEditorResolvedSettings(target);
      widgetEditorPane.innerHTML = `
        <div class="mc-widget-editor-header">
          <strong>Widget Editor</strong>
          <button type="button" data-widget-editor-action="close">Close</button>
        </div>
        <div class="mc-widget-editor-body">
          <div class="mc-widget-editor-docks">
            <button type="button" data-widget-editor-dock="left">Dock Left</button>
            <button type="button" data-widget-editor-dock="right">Dock Right</button>
            <button type="button" data-widget-editor-dock="top">Dock Top</button>
            <button type="button" data-widget-editor-dock="bottom">Dock Bottom</button>
            <button type="button" data-widget-editor-action="float">Float</button>
            <button type="button" data-widget-editor-action="reset-pane">Reset Pane</button>
          </div>
          <div class="mc-widget-editor-meta">
            <strong>${escapeHtml(label)}</strong>
            <span>ID: ${escapeHtml(id || "none")}</span>
            <span>Kind: ${escapeHtml(widgetKind || schema.label.toLowerCase())}</span>
          </div>
          ${target ? `
            ${renderWidgetEditorSchemaFields(target, schema, resolved, override)}
            <div class="mc-widget-editor-actions">
              <button type="button" data-widget-editor-action="revert-widget">Revert Selected Widget</button>
              <button type="button" data-widget-editor-action="clear-layout-overrides">Clear Layout Overrides</button>
            </div>
          ` : `<p>Ctrl-click an editable widget to tune safe presets.</p>`}
        </div>`;
      widgetEditorPane.querySelector(".mc-widget-editor-header")?.addEventListener("pointerdown", startWidgetEditorPaneDrag);
      widgetEditorPane.querySelectorAll("[data-widget-editor-field]").forEach((control) => {
        control.addEventListener("input", () => {
          const field = control.dataset.widgetEditorField;
          widgetEditorSetSelectedOverride({[field]: control.type === "checkbox" ? control.checked : control.value});
        });
      });
      widgetEditorPane.querySelectorAll("[data-widget-editor-dock]").forEach((button) => {
        button.addEventListener("click", () => setWidgetEditorDock(button.dataset.widgetEditorDock));
      });
      widgetEditorPane.querySelectorAll("[data-widget-editor-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const action = button.dataset.widgetEditorAction;
          if (action === "close") closeWidgetEditor();
          if (action === "float") setWidgetEditorFloating();
          if (action === "reset-pane") resetWidgetEditorPane();
          if (action === "revert-widget") resetSelectedWidgetOverrides();
          if (action === "clear-layout-overrides") clearWidgetEditorLayoutOverrides();
        });
      });
      applyWidgetEditorPanePlacement();
      restoreWidgetEditorFocus(focusSnapshot);
    }

    function applyWidgetOverrides() {
      ensureWidgetEditorMetadata();
      document.querySelectorAll("[data-mc-widget-id]").forEach((element) => {
        applyWidgetOverride(element, widgetEditorOverrides[element.dataset.mcWidgetId] || {});
      });
    }

    function applyWidgetOverride(element, override = {}) {
      if (!element) return;
      const safeOverride = sanitizeWidgetEditorOverride(element, override);
      MainComputerWidgets.apply(element, safeOverride);
      if (Object.keys(safeOverride).length !== Object.keys(override || {}).length && element.dataset.mcWidgetId && widgetEditorOverrides[element.dataset.mcWidgetId]) {
        widgetEditorOverrides[element.dataset.mcWidgetId] = safeOverride;
        if (!Object.keys(safeOverride).length) delete widgetEditorOverrides[element.dataset.mcWidgetId];
        widgetEditorSaveOverrides();
      }
    }

    function resetSelectedWidgetOverrides() {
      if (!selectedWidgetEditorTarget) return;
      delete widgetEditorOverrides[selectedWidgetEditorTarget.dataset.mcWidgetId];
      widgetEditorSaveOverrides();
      applyWidgetOverride(selectedWidgetEditorTarget, {});
      renderWidgetEditorPane();
      updateWidgetEditorSelection();
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 120});
    }

    function clearWidgetEditorLayoutOverrides() {
      const layoutKeys = new Set(["density", "layoutPreset", "widthPreset", "minHeightPreset", "overflowPreset", "itemDisplayPreset", "width", "height", "minHeight", "maxHeight", "overflow"]);
      let changed = false;
      Object.keys(widgetEditorOverrides).forEach((id) => {
        const next = {...widgetEditorOverrides[id]};
        layoutKeys.forEach((key) => {
          if (key in next) {
            delete next[key];
            changed = true;
          }
        });
        if (Object.keys(next).length) widgetEditorOverrides[id] = next;
        else delete widgetEditorOverrides[id];
      });
      if (!changed) return;
      widgetEditorSaveOverrides();
      applyWidgetOverrides();
      renderWidgetEditorPane();
      updateWidgetEditorSelection();
      if (widgetEditorChromeReady) scheduleWidgetEditorHandleRefresh({delay: 120});
    }

    function resetWidgetEditorPane() {
      widgetEditorPaneState = JSON.parse(JSON.stringify(widgetEditorDefaultPaneState));
      widgetEditorSavePaneState();
      applyWidgetEditorPanePlacement();
    }

    function setWidgetEditorDock(dock) {
      widgetEditorPaneState.placementMode = "docked";
      widgetEditorPaneState.dock = dock;
      widgetEditorSavePaneState();
      applyWidgetEditorPanePlacement();
    }

    function setWidgetEditorFloating() {
      widgetEditorPaneState.placementMode = "floating";
      widgetEditorSavePaneState();
      applyWidgetEditorPanePlacement();
    }

    function applyWidgetEditorPanePlacement() {
      if (!widgetEditorPane) return;
      const rect = widgetEditorPaneState.floatingRect || widgetEditorDefaultPaneState.floatingRect;
      widgetEditorPane.style.left = "";
      widgetEditorPane.style.right = "";
      widgetEditorPane.style.top = "";
      widgetEditorPane.style.bottom = "";
      widgetEditorPane.style.width = "";
      widgetEditorPane.style.height = "";
      if (widgetEditorPaneState.placementMode === "floating") {
        widgetEditorPane.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 80))}px`;
        widgetEditorPane.style.top = `${Math.max(8, Math.min(rect.top, window.innerHeight - 80))}px`;
        widgetEditorPane.style.width = `${Math.max(260, rect.width)}px`;
        widgetEditorPane.style.height = `${Math.max(260, rect.height)}px`;
        return;
      }
      const dock = widgetEditorPaneState.dock || "right";
      if (dock === "left") {
        widgetEditorPane.style.left = "12px";
        widgetEditorPane.style.top = "12px";
        widgetEditorPane.style.bottom = "12px";
        widgetEditorPane.style.width = "380px";
      } else if (dock === "right") {
        widgetEditorPane.style.right = "12px";
        widgetEditorPane.style.top = "12px";
        widgetEditorPane.style.bottom = "12px";
        widgetEditorPane.style.width = "380px";
      } else if (dock === "top") {
        widgetEditorPane.style.left = "12px";
        widgetEditorPane.style.right = "12px";
        widgetEditorPane.style.top = "12px";
        widgetEditorPane.style.height = "320px";
        widgetEditorPane.style.width = "auto";
      } else {
        widgetEditorPane.style.left = "12px";
        widgetEditorPane.style.right = "12px";
        widgetEditorPane.style.bottom = "12px";
        widgetEditorPane.style.height = "320px";
        widgetEditorPane.style.width = "auto";
      }
    }

    function startWidgetEditorPaneDrag(event) {
      if (!widgetEditorPane || event.target.closest("button")) return;
      const rect = widgetEditorPane.getBoundingClientRect();
      widgetEditorPaneDrag = {
        offsetX: event.clientX - rect.left,
        offsetY: event.clientY - rect.top,
        width: rect.width,
        height: rect.height,
        mode: widgetEditorPaneState.placementMode
      };
      widgetEditorPane.setPointerCapture?.(event.pointerId);
      document.addEventListener("pointermove", moveWidgetEditorPaneDrag);
      document.addEventListener("pointerup", finishWidgetEditorPaneDrag, {once: true});
    }

    function moveWidgetEditorPaneDrag(event) {
      if (!widgetEditorPaneDrag || !widgetEditorPane) return;
      const left = event.clientX - widgetEditorPaneDrag.offsetX;
      const top = event.clientY - widgetEditorPaneDrag.offsetY;
      if (widgetEditorPaneDrag.mode === "floating") {
        widgetEditorPane.style.left = `${Math.max(8, left)}px`;
        widgetEditorPane.style.top = `${Math.max(8, top)}px`;
        widgetEditorPane.style.width = `${widgetEditorPaneDrag.width}px`;
        widgetEditorPane.style.height = `${widgetEditorPaneDrag.height}px`;
      } else {
        const dock = nearestWidgetEditorDockFromRect({left, top, width: widgetEditorPaneDrag.width, height: widgetEditorPaneDrag.height});
        showWidgetEditorDockPreview(dock);
      }
    }

    function finishWidgetEditorPaneDrag(event) {
      document.removeEventListener("pointermove", moveWidgetEditorPaneDrag);
      if (!widgetEditorPaneDrag || !widgetEditorPane) return;
      if (widgetEditorPaneDrag.mode === "floating") {
        const rect = widgetEditorPane.getBoundingClientRect();
        widgetEditorPaneState.floatingRect = {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
      } else {
        const left = event.clientX - widgetEditorPaneDrag.offsetX;
        const top = event.clientY - widgetEditorPaneDrag.offsetY;
        widgetEditorPaneState.dock = nearestWidgetEditorDockFromRect({left, top, width: widgetEditorPaneDrag.width, height: widgetEditorPaneDrag.height});
      }
      widgetEditorDockPreview.hidden = true;
      widgetEditorPaneDrag = null;
      widgetEditorSavePaneState();
      applyWidgetEditorPanePlacement();
    }

    function nearestWidgetEditorDockFromRect(rect) {
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      const distances = {
        left: centerX,
        right: window.innerWidth - centerX,
        top: centerY,
        bottom: window.innerHeight - centerY
      };
      return Object.entries(distances).sort((a, b) => a[1] - b[1])[0][0];
    }

    function showWidgetEditorDockPreview(dock) {
      if (!widgetEditorDockPreview) return;
      widgetEditorDockPreview.hidden = false;
      widgetEditorDockPreview.style.left = dock === "left" ? "8px" : dock === "right" ? "calc(100vw - 388px)" : "8px";
      widgetEditorDockPreview.style.top = dock === "top" ? "8px" : dock === "bottom" ? "calc(100vh - 328px)" : "8px";
      widgetEditorDockPreview.style.width = dock === "left" || dock === "right" ? "380px" : "calc(100vw - 16px)";
      widgetEditorDockPreview.style.height = dock === "top" || dock === "bottom" ? "320px" : "calc(100vh - 16px)";
    }

    function updateWidgetEditorSelection() {
      if (!widgetEditorSelection) return;
      if (!selectedWidgetEditorTarget || selectedWidgetEditorTarget.style.display === "none") {
        widgetEditorSelection.hidden = true;
        return;
      }
      const rect = selectedWidgetEditorTarget.getBoundingClientRect();
      widgetEditorSelection.hidden = rect.width <= 0 || rect.height <= 0;
      widgetEditorSelection.style.left = `${rect.left}px`;
      widgetEditorSelection.style.top = `${rect.top}px`;
      widgetEditorSelection.style.width = `${rect.width}px`;
      widgetEditorSelection.style.height = `${rect.height}px`;
    }

    async function toggleApplicationWidgetFullscreen(widget) {
      if (!widget) return;
      try {
        if (document.fullscreenElement === widget) {
          await document.exitFullscreen();
        } else {
          await widget.requestFullscreen();
        }
      } catch (error) {
        if (taskManagerStatus && currentApp === "task-manager") {
          taskManagerStatus.textContent = `Fullscreen failed: ${String(error.message || error)}`;
        }
      }
    }
