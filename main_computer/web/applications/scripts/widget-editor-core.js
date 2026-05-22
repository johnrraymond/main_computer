    function applicationWidgetLabel(widget) {
      if (!widget) return "Widget";
      const explicit = widget.dataset.widgetLabel?.trim();
      if (explicit) return explicit;
      const aria = widget.getAttribute("aria-label")?.trim();
      if (aria) return aria;
      const heading = widget.querySelector(":scope > strong, :scope h3, strong");
      return heading?.textContent?.trim() || "Widget";
    }

    function setApplicationWidgetTicker(widget, text) {
      const ticker = widget?.querySelector(":scope > .widget-ticker span");
      if (ticker) ticker.textContent = text;
    }

    function ensureApplicationWidgets() {
      document.querySelectorAll(".app-widget").forEach((widget) => {
        widget.classList.add("app-fullscreen-widget", "fullscreen-widget");
        if (!widget.querySelector(":scope > .fullscreen-control")) {
          const button = document.createElement("button");
          button.className = "fullscreen-control";
          button.type = "button";
          button.dataset.fullscreenTarget = "closest";
          button.textContent = "Full Screen";
          widget.prepend(button);
        }
        if (!widget.querySelector(":scope > .widget-ticker")) {
          const ticker = document.createElement("div");
          ticker.className = "widget-ticker";
          ticker.innerHTML = `<span>${applicationWidgetLabel(widget)} | embedded ticker view | full screen switches this widget into focus mode</span>`;
          const control = widget.querySelector(":scope > .fullscreen-control");
          if (control) {
            control.insertAdjacentElement("afterend", ticker);
          } else {
            widget.prepend(ticker);
          }
        }
      });
      ensureWidgetEditorMetadata();
    }

    const widgetEditorPaneStorageKey = "main-computer-widget-editor-pane-v1";
    const widgetEditorOverridesStorageKey = "main-computer-widget-overrides-v1";
    const widgetEditorPresetOptions = {
      density: [["compact", "Compact"], ["normal", "Normal"], ["roomy", "Roomy"]],
      layoutPreset: [["auto", "Auto"], ["split", "Split"], ["stacked", "Stacked"]],
      widthPreset: [["auto", "Auto"], ["narrow", "Narrow"], ["normal", "Normal"], ["wide", "Wide"], ["full", "Full"]],
      minHeightPreset: [["none", "None"], ["short", "Short"], ["medium", "Medium"], ["tall", "Tall"]],
      overflowPreset: [["auto", "Auto"], ["visible", "Visible"], ["hidden", "Hidden"]],
      itemDisplayPreset: [["compact", "Compact"], ["normal", "Normal"], ["detailed", "Detailed"]]
    };

    const MainComputerWidgets = {
      definitions: {},
      register(definition) {
        if (!definition?.kind) return;
        this.definitions[definition.kind] = {
          defaults: {},
          fields: [],
          capabilities: [],
          ...definition,
          defaults: {...(definition.defaults || {})},
          fields: [...(definition.fields || [])],
          capabilities: [...(definition.capabilities || [])]
        };
      },
      getKind(element) {
        if (!element) return "panel";
        return element.dataset.mcWidgetKind || inferWidgetEditorKind(element);
      },
      getDefinition(element) {
        return this.definitions[this.getKind(element)] || this.definitions.panel;
      },
      getSchema(element) {
        return this.getDefinition(element);
      },
      hydrate(root = document) {
        const scope = root instanceof Element || root === document ? root : document;
        const widgets = [
          ...(scope.matches?.("[data-mc-widget-id]") ? [scope] : []),
          ...scope.querySelectorAll("[data-mc-widget-id]")
        ];
        widgets.forEach((element) => {
          if (element.closest("[data-mc-generated-item]")) return;
          if (!element.dataset.mcWidgetKind) element.dataset.mcWidgetKind = inferWidgetEditorKind(element);
          const definition = this.getDefinition(element);
          if (definition?.capabilities?.length && !element.dataset.mcCapabilities) {
            element.dataset.mcCapabilities = definition.capabilities.join(" ");
          }
          element.dataset.mcHydrated = "true";
        });
      },
      read(element) {
        if (!element) return {};
        const schema = this.getSchema(element);
        const override = this.sanitize(element, widgetEditorOverrides[element.dataset.mcWidgetId] || {});
        const originalPlaceholder = element.dataset.mcWidgetOriginalPlaceholder ?? element.getAttribute("placeholder") ?? "";
        return {
          label: override.label ?? "",
          visible: override.visible ?? element.style.display !== "none",
          disabled: override.disabled ?? ("disabled" in element ? element.disabled : false),
          placeholder: override.placeholder ?? ("placeholder" in element ? originalPlaceholder : ""),
          density: override.density ?? element.dataset.mcDensity ?? schema.defaults.density ?? "normal",
          layoutPreset: override.layoutPreset ?? element.dataset.mcLayout ?? schema.defaults.layoutPreset ?? "auto",
          widthPreset: override.widthPreset ?? element.dataset.mcWidthPreset ?? schema.defaults.widthPreset ?? "auto",
          minHeightPreset: override.minHeightPreset ?? element.dataset.mcMinHeightPreset ?? schema.defaults.minHeightPreset ?? "none",
          overflowPreset: override.overflowPreset ?? element.dataset.mcOverflowPreset ?? schema.defaults.overflowPreset ?? "auto",
          itemDisplayPreset: override.itemDisplayPreset ?? element.dataset.mcItemDisplayPreset ?? schema.defaults.itemDisplayPreset ?? "normal"
        };
      },
      sanitize(element, settings = {}) {
        if (!element || element.closest("[data-mc-generated-item]")) return {};
        const schema = this.getSchema(element);
        const allowed = new Set(schema.fields || []);
        const normalized = {};
        if (allowed.has("label") && "label" in settings) normalized.label = String(settings.label ?? "").trim();
        if (allowed.has("visible") && "visible" in settings) normalized.visible = Boolean(settings.visible);
        if (allowed.has("disabled") && "disabled" in settings) normalized.disabled = Boolean(settings.disabled);
        if (allowed.has("placeholder") && "placeholder" in settings && "placeholder" in element) normalized.placeholder = String(settings.placeholder ?? "");
        if (allowed.has("density") && "density" in settings) normalized.density = normalizeWidgetEditorPreset(settings.density, widgetEditorPresetOptions.density, schema.defaults.density || "normal");
        if (allowed.has("layoutPreset") && "layoutPreset" in settings) normalized.layoutPreset = normalizeWidgetEditorPreset(settings.layoutPreset, widgetEditorPresetOptions.layoutPreset, schema.defaults.layoutPreset || "auto");
        if (allowed.has("widthPreset") && "widthPreset" in settings) normalized.widthPreset = normalizeWidgetEditorPreset(settings.widthPreset, widgetEditorPresetOptions.widthPreset, schema.defaults.widthPreset || "auto");
        if (allowed.has("minHeightPreset") && "minHeightPreset" in settings) normalized.minHeightPreset = normalizeWidgetEditorPreset(settings.minHeightPreset, widgetEditorPresetOptions.minHeightPreset, schema.defaults.minHeightPreset || "none");
        if (allowed.has("overflowPreset") && "overflowPreset" in settings) normalized.overflowPreset = normalizeWidgetEditorPreset(settings.overflowPreset, widgetEditorPresetOptions.overflowPreset, schema.defaults.overflowPreset || "auto");
        if (allowed.has("itemDisplayPreset") && "itemDisplayPreset" in settings) normalized.itemDisplayPreset = normalizeWidgetEditorPreset(settings.itemDisplayPreset, widgetEditorPresetOptions.itemDisplayPreset, schema.defaults.itemDisplayPreset || "normal");
        return normalized;
      },
      apply(element, settings = {}) {
        if (!element || element.closest("[data-mc-generated-item]")) return;
        const safeOverride = this.sanitize(element, settings);
        if (!element.dataset.mcWidgetOriginalText && element.matches("button")) element.dataset.mcWidgetOriginalText = element.textContent;
        if (!element.dataset.mcWidgetOriginalPlaceholder && "placeholder" in element) element.dataset.mcWidgetOriginalPlaceholder = element.getAttribute("placeholder") || "";
        if (!element.dataset.mcWidgetOriginalDisabled && "disabled" in element) element.dataset.mcWidgetOriginalDisabled = element.disabled ? "true" : "false";
        const setAttr = (name, value) => {
          const normalized = String(value ?? "").trim();
          if (normalized) element.setAttribute(name, normalized);
          else element.removeAttribute(name);
        };
        ["--mc-widget-width", "--mc-widget-height", "--mc-widget-min-height", "--mc-widget-max-height", "--mc-widget-overflow"].forEach((property) => element.style.removeProperty(property));
        setAttr("data-mc-density", safeOverride.density);
        setAttr("data-mc-layout", safeOverride.layoutPreset);
        setAttr("data-mc-width-preset", safeOverride.widthPreset);
        setAttr("data-mc-min-height-preset", safeOverride.minHeightPreset === "none" ? "" : safeOverride.minHeightPreset);
        setAttr("data-mc-overflow-preset", safeOverride.overflowPreset);
        setAttr("data-mc-item-display-preset", safeOverride.itemDisplayPreset);
        if (safeOverride.visible === false) {
          if (!element.dataset.mcWidgetHiddenByEditor) {
            element.dataset.mcWidgetHiddenByEditor = "true";
            element.dataset.mcWidgetPreviousDisplay = element.style.display || "";
          }
          element.style.display = "none";
        } else if (element.dataset.mcWidgetHiddenByEditor) {
          element.style.display = element.dataset.mcWidgetPreviousDisplay || "";
          delete element.dataset.mcWidgetHiddenByEditor;
          delete element.dataset.mcWidgetPreviousDisplay;
        }
        if ("disabled" in element) element.disabled = "disabled" in safeOverride ? Boolean(safeOverride.disabled) : element.dataset.mcWidgetOriginalDisabled === "true";
        if ("placeholder" in element) element.setAttribute("placeholder", safeOverride.placeholder || element.dataset.mcWidgetOriginalPlaceholder || "");
        if (element.matches("button")) element.textContent = safeOverride.label || element.dataset.mcWidgetOriginalText || element.dataset.mcWidgetLabel || "";
        if (safeOverride.label) element.setAttribute("aria-label", safeOverride.label);
        else if (element.dataset.mcWidgetLabel) element.setAttribute("aria-label", element.dataset.mcWidgetLabel);
        if (element.classList.contains("app-widget")) setApplicationWidgetTicker(element, `${safeOverride.label || applicationWidgetLabel(element)} | embedded ticker view | full screen switches this widget into focus mode`);
      },
      closestWidget(target) {
        const element = target instanceof Element ? target : target?.parentElement;
        return element?.closest?.("[data-mc-widget-id]") || null;
      },
      closestGeneratedItem(target) {
        const element = target instanceof Element ? target : target?.parentElement;
        return element?.closest?.("[data-mc-generated-item]") || null;
      },
      ownerWidgetForTarget(target) {
        const item = this.closestGeneratedItem(target);
        if (item) return item.closest("[data-mc-widget-id]");
        return this.closestWidget(target);
      }
    };

    [
      {kind: "container", label: "Container", capabilities: ["selectable", "configurable", "layout"], defaults: {density: "normal", layoutPreset: "auto", overflowPreset: "auto", minHeightPreset: "none"}, fields: ["label", "visible", "density", "layoutPreset", "overflowPreset", "minHeightPreset"]},
      {kind: "panel", label: "Panel", capabilities: ["selectable", "configurable"], defaults: {density: "normal", overflowPreset: "auto", minHeightPreset: "none"}, fields: ["label", "visible", "density", "overflowPreset", "minHeightPreset"]},
      {kind: "field", label: "Field", capabilities: ["selectable", "configurable"], defaults: {widthPreset: "auto"}, fields: ["label", "visible", "widthPreset"]},
      {kind: "input", label: "Input", capabilities: ["selectable", "configurable"], defaults: {widthPreset: "auto"}, fields: ["label", "placeholder", "disabled", "widthPreset"]},
      {kind: "action", label: "Action", capabilities: ["selectable", "configurable"], defaults: {}, fields: ["label", "disabled"]},
      {kind: "output", label: "Output", capabilities: ["selectable", "configurable", "scroll_region"], defaults: {density: "normal", overflowPreset: "auto", minHeightPreset: "none"}, fields: ["label", "visible", "density", "overflowPreset", "minHeightPreset"]},
      {kind: "repeater", label: "Repeater/List", capabilities: ["selectable", "configurable", "generated_items", "scroll_region"], defaults: {density: "normal", itemDisplayPreset: "normal", overflowPreset: "auto", minHeightPreset: "none"}, fields: ["label", "visible", "density", "itemDisplayPreset", "overflowPreset", "minHeightPreset"]}
    ].forEach((definition) => MainComputerWidgets.register(definition));

    const widgetEditorSchemas = MainComputerWidgets.definitions;

    function inferWidgetEditorKind(element) {
      if (!element) return "panel";
      const id = element.dataset.mcWidgetId || "";
      const widgetClass = element.dataset.mcWidgetClass || "";
      if (element.matches("button") || widgetClass === "action") return "action";
      if (element.matches('input[type="checkbox"]') || element.closest(".aider-dry-run")) return "field";
      if (["code-editor.root", "code-editor.aider-workspace"].includes(id) || element.classList.contains("aider-shell") || element.classList.contains("aider-workspace")) return "container";
      if (widgetClass === "output") return "output";
      if (widgetClass === "list" || element.matches("select[size]")) return "repeater";
      if (element.classList.contains("file-map-panel") || element.classList.contains("aider-archive-panel") || id.includes("file-map") || id.includes("history-list") || id.includes("archive")) return "panel";
      if (id === "code-editor.aider-files" || element.matches("textarea[readonly]")) return "panel";
      if (element.matches("input, select, textarea") || widgetClass === "input") return "input";
      if (widgetClass === "box") return "panel";
      return "panel";
    }

    function ensureWidgetEditorMetadata() {
      MainComputerWidgets.hydrate();
    }

    function resolveWidgetEditorSchema(element) {
      return MainComputerWidgets.getSchema(element);
    }
    const widgetEditorDefaultPaneState = {
      placementMode: "docked",
      dock: "right",
      floatingRect: {left: 520, top: 80, width: 380, height: 520}
    };
    let widgetEditorPaneState = widgetEditorLoadPaneState();
    let widgetEditorOverrides = widgetEditorLoadOverrides();
    let widgetEditorRoot = null;
    let widgetEditorPane = null;
    let widgetEditorSelection = null;
    let widgetEditorDockPreview = null;
    let selectedWidgetEditorTarget = null;
    let widgetEditorPaneDrag = null;
    let widgetEditorHandleRefreshFrame = 0;
    let widgetEditorHandleRefreshTimer = 0;
    let widgetEditorChromeReady = false;

    function widgetEditorLoadPaneState() {
      try {
        const stored = JSON.parse(localStorage.getItem(widgetEditorPaneStorageKey) || "null");
        return {
          placementMode: stored?.placementMode === "floating" ? "floating" : "docked",
          dock: ["left", "right", "top", "bottom"].includes(stored?.dock) ? stored.dock : "right",
          floatingRect: {
            left: Number(stored?.floatingRect?.left ?? widgetEditorDefaultPaneState.floatingRect.left),
            top: Number(stored?.floatingRect?.top ?? widgetEditorDefaultPaneState.floatingRect.top),
            width: Number(stored?.floatingRect?.width ?? widgetEditorDefaultPaneState.floatingRect.width),
            height: Number(stored?.floatingRect?.height ?? widgetEditorDefaultPaneState.floatingRect.height)
          }
        };
      } catch {
        return JSON.parse(JSON.stringify(widgetEditorDefaultPaneState));
      }
    }

    function widgetEditorSavePaneState() {
      localStorage.setItem(widgetEditorPaneStorageKey, JSON.stringify(widgetEditorPaneState));
    }

    function widgetEditorLoadOverrides() {
      try {
        const stored = JSON.parse(localStorage.getItem(widgetEditorOverridesStorageKey) || "{}");
        return stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
      } catch {
        return {};
      }
    }

    function widgetEditorSaveOverrides() {
      localStorage.setItem(widgetEditorOverridesStorageKey, JSON.stringify(widgetEditorOverrides));
    }

    function scheduleWidgetEditorHandleRefresh(options = {}) {
      const delay = Number(options.delay ?? 0);
      if (widgetEditorHandleRefreshFrame) {
        cancelAnimationFrame(widgetEditorHandleRefreshFrame);
        widgetEditorHandleRefreshFrame = 0;
      }
      if (widgetEditorHandleRefreshTimer) {
        clearTimeout(widgetEditorHandleRefreshTimer);
        widgetEditorHandleRefreshTimer = 0;
      }

      const run = () => {
        widgetEditorHandleRefreshFrame = requestAnimationFrame(() => {
          widgetEditorHandleRefreshFrame = requestAnimationFrame(() => {
            widgetEditorHandleRefreshFrame = 0;
            refreshWidgetEditorChrome();
          });
        });
      };

      if (delay > 0) {
        widgetEditorHandleRefreshTimer = setTimeout(() => {
          widgetEditorHandleRefreshTimer = 0;
          run();
        }, delay);
      } else {
        run();
      }
    }
