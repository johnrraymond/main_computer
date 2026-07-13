    (() => {
      "use strict";

      const globalObject = typeof window !== "undefined" ? window : globalThis;
      const CONTRACT_VERSION = "mcel-code-editor-layout.v1";
      const STORAGE_KEY = "main-computer-code-editor-layout-preferences-v1";
      const HISTORY_LIMIT = 20;
      const RAW_GEOMETRY_KEYS = Object.freeze([
        "x", "y", "w", "h", "left", "top", "right", "bottom", "width", "height"
      ]);

      function deepFreeze(value) {
        if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
        Object.freeze(value);
        Object.values(value).forEach(deepFreeze);
        return value;
      }

      const GENERATED_LAYOUT_CONTRACT = deepFreeze({
        version: "mcel-owned-track-containment.v1",
        description: "Generated descendants of an owned remaining track must fit, shrink, and scroll inside that track.",
        rules: [
          {
            id: "runtime-window",
            selector: ".code-studio-runtime-window",
            attributes: {
              "data-mcel-layout-node": "runtime-window",
              "data-mcel-layout": "stack",
              "data-mcel-layout-tracks": "content remaining",
              "data-mcel-layout-fill": "parent",
              "data-mcel-layout-overflow": "contain",
              "data-mcel-layout-containment": "owned-remaining-track",
            },
          },
          {
            id: "runtime-header",
            selector: ".code-studio-runtime-header",
            attributes: {
              "data-mcel-layout-node": "runtime-header",
              "data-mcel-layout-track": "content",
              "data-mcel-layout-overflow": "contain-inline",
            },
          },
          {
            id: "runtime-layout",
            selector: ".code-studio-runtime-layout",
            attributes: {
              "data-mcel-layout-node": "runtime-layout",
              "data-mcel-layout": "split",
              "data-mcel-layout-fill": "remaining",
              "data-mcel-layout-overflow": "contain",
            },
          },
          {
            id: "runtime-files",
            selector: ".code-studio-runtime-files",
            attributes: {
              "data-mcel-layout-node": "runtime-files",
              "data-mcel-layout-fill": "parent",
              "data-mcel-layout-overflow": "scroll",
            },
          },
          {
            id: "runtime-editor",
            selector: ".code-studio-runtime-editor",
            attributes: {
              "data-mcel-layout-node": "runtime-editor",
              "data-mcel-layout": "stack",
              "data-mcel-layout-tracks": "content remaining content",
              "data-mcel-layout-fill": "parent",
              "data-mcel-layout-overflow": "contain",
            },
          },
          {
            id: "runtime-monaco",
            selector: ".code-studio-monaco-host",
            attributes: {
              "data-mcel-layout-node": "runtime-monaco",
              "data-mcel-layout-fill": "remaining",
              "data-mcel-layout-overflow": "contain",
            },
          },
          {
            id: "runtime-fallback",
            selector: ".code-studio-runtime-fallback",
            attributes: {
              "data-mcel-layout-node": "runtime-fallback",
              "data-mcel-layout": "stack",
              "data-mcel-layout-tracks": "content remaining",
              "data-mcel-layout-fill": "remaining",
              "data-mcel-layout-overflow": "contain",
            },
          },
          {
            id: "runtime-draft",
            selector: "#code-studio-runtime-draft",
            attributes: {
              "data-mcel-layout-node": "runtime-draft",
              "data-mcel-layout-fill": "parent",
              "data-mcel-layout-overflow": "scroll",
              "data-mcel-layout-containment": "paint-contained",
            },
          },
          {
            id: "runtime-badges",
            selector: ".code-studio-runtime-badges",
            attributes: {
              "data-mcel-layout-node": "runtime-badges",
              "data-mcel-layout-track": "content",
              "data-mcel-layout-overflow": "scroll-inline",
            },
          },
        ],
      });

      const APPLICATION_CONTRACT = deepFreeze({
        id: "code-editor",
        version: 1,
        root: "code-editor.workbench",
        description: "IDE-grade dock workbench with a required center editor, an operational Aider companion, and semantic user layout operations.",
        generatedLayoutContract: GENERATED_LAYOUT_CONTRACT,
        units: {
          "code-editor.workbench": {
            role: "application",
            selector: ".code-studio-shell",
            required: true,
            accepts: ["top", "left-fixed", "left", "center", "right", "bottom", "tab", "stage", "trigger", "bottom-fixed"],
          },
          "code-editor.titlebar": {
            role: "command-chrome",
            selector: ".code-studio-titlebar",
            required: true,
          },
          "code-editor.activity": {
            role: "navigation-chrome",
            selector: ".code-studio-activitybar",
            required: true,
          },
          "code-editor.explorer": {
            role: "navigation",
            selector: ".code-studio-sidebar",
            required: false,
          },
          "code-editor.explorer.open-editors": {
            role: "workspace-open-editor-list",
            selector: ".code-studio-open-editors",
            required: false,
            layout: {
              track: "content",
              density: "compact",
              overflow: "contain",
            },
          },
          "code-editor.explorer.tree": {
            role: "workspace-file-tree",
            selector: ".code-studio-tree",
            required: true,
            layout: {
              fill: "remaining",
              density: "compact",
              overflow: "scroll",
            },
          },
          "code-editor.editor": {
            role: "primary-work",
            selector: ".code-studio-editor-group",
            required: true,
            layout: {
              fill: "owned-center-slot",
              overflow: "contain",
            },
          },
          "code-editor.runtime-preview": {
            role: "owned-primary-surface",
            selector: "#code-studio-runtime-preview",
            required: false,
            layout: {
              fill: "remaining",
              overflow: "contain",
              containment: "owned-remaining-track",
            },
          },
          "code-editor.file-map": {
            role: "agent-context-selector",
            selector: ".code-studio-aider-file-map",
            required: false,
          },
          "code-editor.inspector": {
            role: "agent-control",
            selector: ".code-studio-inspector",
            required: false,
          },
          "code-editor.proof": {
            role: "evidence-history",
            selector: "#code-studio-bottom-panel",
            required: false,
          },
          "code-editor.status": {
            role: "persistent-status",
            selector: ".code-studio-statusbar",
            required: true,
          },
        },
        relationships: [
          {subject: "code-editor.activity", relation: "selects", object: "code-editor.editor", strength: "strong"},
          {subject: "code-editor.explorer", relation: "owns", object: "code-editor.explorer.open-editors", strength: "hard"},
          {subject: "code-editor.explorer", relation: "owns", object: "code-editor.explorer.tree", strength: "hard"},
          {subject: "code-editor.explorer", relation: "navigates", object: "code-editor.editor", strength: "hard"},
          {subject: "code-editor.explorer", relation: "scopes", object: "code-editor.editor", strength: "hard"},
          {subject: "code-editor.file-map", relation: "selects", object: "workspace.selection", strength: "hard"},
          {subject: "code-editor.file-map", relation: "feeds", object: "code-editor.inspector", strength: "strong"},
          {subject: "code-editor.inspector", relation: "controls", object: "code-editor.editor", strength: "strong"},
          {subject: "code-editor.inspector", relation: "consumes", object: "workspace.selection", strength: "hard"},
          {subject: "code-editor.proof", relation: "proves", object: "editor.operation", strength: "strong"},
          {subject: "code-editor.proof", relation: "records", object: "aider.operation", strength: "strong"},
          {subject: "code-editor.status", relation: "confirms", object: "editor.state", strength: "hard"},
          {subject: "code-editor.status", relation: "confirms", object: "layout.state", strength: "hard"},
        ],
        invariants: [
          "editor-remains-center",
          "editor-never-collapses",
          "active-critical-controls-remain-actionable",
          "owned-remaining-track-descendants-contain-their-paint",
          "no-undeclared-overlap",
          "status-remains-persistent",
          "user-preferences-use-semantic-hints",
          "infeasible-preferences-remediate-without-being-forgotten",
        ],
        operations: {
          dock: {requires: ["placement"], mutableCapability: "placement"},
          "resize-share": {requires: ["share"], mutableCapability: "share"},
          collapse: {requires: ["collapsed"], mutableCapability: "collapsed"},
          "tab-with": {requires: ["targetUserId"], mutableCapability: "tab-group"},
          undo: {requires: []},
          reset: {requires: []},
        },
      });

      const SAFE_DEFAULTS = deepFreeze({
        "code-editor.activity": {
          prefer: "left-fixed",
          allowed: ["left-fixed"],
          fallback: [],
          strength: "required",
          minInline: 44,
          minBlock: 0,
          maxShare: 0.08,
          mutable: [],
        },
        "code-editor.explorer": {
          prefer: "left",
          allowed: ["left", "trigger"],
          fallback: ["trigger"],
          strength: "strong",
          minInline: 220,
          minBlock: 260,
          maxShare: 0.28,
          preferredShare: 0.18,
          mutable: ["share", "collapsed"],
        },
        "code-editor.editor": {
          prefer: "center",
          allowed: ["center", "split-center"],
          fallback: [],
          strength: "required",
          minInline: 520,
          minBlock: 320,
          maxShare: 1,
          preferredShare: 1,
          mutable: [],
        },
        "code-editor.inspector": {
          prefer: "right",
          allowed: ["right", "bottom", "tab", "trigger"],
          fallback: ["bottom", "tab", "trigger"],
          strength: "preferred",
          minInline: 300,
          minBlock: 220,
          maxShare: 0.36,
          preferredShare: 0.24,
          mutable: ["placement", "share", "collapsed", "tab-group"],
        },
        "code-editor.proof": {
          prefer: "bottom",
          allowed: ["bottom", "trigger"],
          fallback: ["trigger"],
          strength: "preferred",
          minInline: 420,
          minBlock: 180,
          maxShare: 0.48,
          preferredShare: 0.30,
          mutable: ["placement", "share", "collapsed"],
        },
        "code-editor.status": {
          prefer: "bottom-fixed",
          allowed: ["bottom-fixed"],
          fallback: [],
          strength: "required",
          minInline: 0,
          minBlock: 22,
          maxShare: 0.06,
          mutable: [],
        },
      });

      const DEFAULT_PREFERENCES = deepFreeze({
        version: 1,
        units: {
          "code-editor.explorer": {
            placement: "left",
            preferredShare: 0.18,
            collapsed: false,
          },
          "code-editor.inspector": {
            placement: "right",
            preferredShare: 0.24,
            collapsed: false,
            tabWith: "code-editor.editor",
          },
          "code-editor.proof": {
            placement: "bottom",
            preferredShare: 0.30,
            collapsed: false,
            tabWith: "code-editor.editor",
          },
        },
      });

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function clamp(value, minimum, maximum) {
        return Math.min(maximum, Math.max(minimum, value));
      }

      function numberOr(value, fallback) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : fallback;
      }

      function tokenList(value) {
        return String(value || "")
          .trim()
          .split(/[\s,]+/)
          .map((entry) => entry.trim())
          .filter(Boolean);
      }

      function unique(values) {
        return [...new Set(values)];
      }

      function elementForUnit(root, unitId) {
        const definition = APPLICATION_CONTRACT.units[unitId];
        if (!root || !definition?.selector) return null;
        return root.querySelector(definition.selector);
      }

      function hintFromElement(element, fallback) {
        const dataset = element?.dataset || {};
        const allowed = tokenList(dataset.mcLayoutAllowed);
        const fallbackPlacements = tokenList(dataset.mcLayoutFallback);
        const mutable = tokenList(dataset.mcLayoutUserMutable);

        return {
          prefer: dataset.mcLayoutPrefer || fallback.prefer,
          allowed: allowed.length ? allowed : [...fallback.allowed],
          fallback: fallbackPlacements.length ? fallbackPlacements : [...fallback.fallback],
          strength: dataset.mcLayoutStrength || fallback.strength,
          policy: dataset.mcLayoutPolicy || "",
          inactive: dataset.mcLayoutInactive || "",
          fill: dataset.mcLayoutFill || fallback.fill || "",
          overflow: dataset.mcLayoutOverflow || fallback.overflow || "",
          containment: dataset.mcLayoutContainment || fallback.containment || "",
          tracks: tokenList(dataset.mcLayoutTracks || fallback.tracks || ""),
          minInline: numberOr(dataset.mcLayoutMinInline, fallback.minInline),
          minBlock: numberOr(dataset.mcLayoutMinBlock, fallback.minBlock),
          maxShare: clamp(numberOr(dataset.mcLayoutMaxShare, fallback.maxShare), 0.05, 1),
          preferredShare: clamp(numberOr(dataset.mcLayoutPreferredShare, fallback.preferredShare || 0.2), 0.05, 1),
          mutable: mutable.length ? mutable : [...fallback.mutable],
          userId: dataset.mcLayoutUserId || "",
          componentId: dataset.mcComponentId || "",
        };
      }

      function extractAuthoredContract(root) {
        const units = {};
        const missing = [];
        const mismatches = [];

        for (const [unitId, definition] of Object.entries(APPLICATION_CONTRACT.units)) {
          const fallback = {
            ...(SAFE_DEFAULTS[unitId] || {
              prefer: "",
              allowed: [],
              fallback: [],
              strength: "preferred",
              minInline: 0,
              minBlock: 0,
              maxShare: 1,
              preferredShare: 0.2,
              mutable: [],
            }),
            ...(definition.layout || {}),
          };
          const element = elementForUnit(root, unitId);
          if (!element && definition.required) missing.push(unitId);
          const hint = hintFromElement(element, fallback);
          if (element && hint.userId && hint.userId !== unitId) {
            mismatches.push(`${unitId} is authored as ${hint.userId}`);
          }
          units[unitId] = {
            id: unitId,
            role: definition.role,
            required: definition.required === true,
            selector: definition.selector,
            present: Boolean(element),
            ...hint,
          };
        }

        const shell = elementForUnit(root, "code-editor.workbench");
        const zones = tokenList(shell?.dataset?.mcLayoutZones);
        const authoredZones = zones.length ? zones : [...APPLICATION_CONTRACT.units["code-editor.workbench"].accepts];

        return {
          id: APPLICATION_CONTRACT.id,
          version: APPLICATION_CONTRACT.version,
          contractVersion: CONTRACT_VERSION,
          root: APPLICATION_CONTRACT.root,
          layout: shell?.dataset?.mcLayout || "dock-workbench",
          policy: shell?.dataset?.mcLayoutPolicy || "editor-centered-workbench",
          zones: authoredZones,
          units,
          relationships: clone(APPLICATION_CONTRACT.relationships),
          invariants: [...APPLICATION_CONTRACT.invariants],
          missing,
          mismatches,
          complete: missing.length === 0 && mismatches.length === 0,
        };
      }

      function rejectRawGeometry(value, path = "preferences") {
        if (!value || typeof value !== "object") return [];
        const violations = [];
        for (const [key, child] of Object.entries(value)) {
          const childPath = `${path}.${key}`;
          if (RAW_GEOMETRY_KEYS.includes(String(key).toLowerCase())) {
            violations.push(childPath);
          }
          violations.push(...rejectRawGeometry(child, childPath));
        }
        return violations;
      }

      function normalizeUnitPreference(unitId, input, authored) {
        const unit = authored.units[unitId] || SAFE_DEFAULTS[unitId];
        const defaults = DEFAULT_PREFERENCES.units[unitId] || {};
        const source = input && typeof input === "object" ? input : {};
        const allowed = unit.allowed || [];
        const fallbackPlacement = defaults.placement || unit.prefer || allowed[0] || "";

        let placement = String(source.placement || fallbackPlacement);
        if (!allowed.includes(placement)) placement = fallbackPlacement;

        const preferredShare = clamp(
          numberOr(source.preferredShare, defaults.preferredShare || unit.preferredShare || 0.2),
          0.08,
          Math.max(0.08, unit.maxShare || 1),
        );

        return {
          placement,
          preferredShare,
          collapsed: source.collapsed === true,
          tabWith: String(source.tabWith || defaults.tabWith || ""),
        };
      }

      function normalizePreferences(input, authored) {
        const source = input && typeof input === "object" ? input : {};
        const rawGeometryViolations = rejectRawGeometry(source);
        const units = {};
        for (const unitId of Object.keys(DEFAULT_PREFERENCES.units)) {
          units[unitId] = normalizeUnitPreference(unitId, source.units?.[unitId], authored);
        }
        return {
          version: 1,
          units,
          rawGeometryViolations,
        };
      }

      function preferencePlacementOrder(preference, unit) {
        const initial = preference.collapsed ? "trigger" : preference.placement;
        return unique([initial, ...(unit.fallback || []), "trigger"])
          .filter((placement) => unit.allowed.includes(placement));
      }

      function dimensionForShare(total, share, minimum, maximumShare, hardMaximum = Infinity) {
        const maximum = Math.min(total * maximumShare, hardMaximum);
        return Math.round(clamp(total * share, Math.min(minimum, maximum), maximum));
      }

      function resolveLayout({
        viewport = {},
        authored,
        preferences,
        proofExpanded = false,
        centerTab = "editor",
      }) {
        const width = Math.max(320, numberOr(viewport.width, 1440));
        const height = Math.max(420, numberOr(viewport.height, 900));
        const units = authored.units;
        const normalized = normalizePreferences(preferences, authored);

        const activityUnit = units["code-editor.activity"];
        const explorerUnit = units["code-editor.explorer"];
        const editorUnit = units["code-editor.editor"];
        const inspectorUnit = units["code-editor.inspector"];
        const proofUnit = units["code-editor.proof"];

        const activityInline = Math.max(40, activityUnit.minInline || 44);
        const titleBlock = 36;
        const statusBlock = 22;
        const compactProofBlock = 34;

        const explorerPreference = normalized.units["code-editor.explorer"];
        const inspectorPreference = normalized.units["code-editor.inspector"];
        const proofPreference = normalized.units["code-editor.proof"];

        const explorerInline = dimensionForShare(
          width,
          explorerPreference.preferredShare,
          explorerUnit.minInline,
          explorerUnit.maxShare,
          360,
        );
        const inspectorInline = dimensionForShare(
          width,
          inspectorPreference.preferredShare,
          inspectorUnit.minInline,
          inspectorUnit.maxShare,
          520,
        );
        const inspectorBottomBlock = dimensionForShare(
          height,
          inspectorPreference.preferredShare,
          inspectorUnit.minBlock,
          Math.min(0.44, inspectorUnit.maxShare),
          360,
        );
        const proofExpandedBlock = dimensionForShare(
          height,
          proofPreference.preferredShare,
          proofUnit.minBlock,
          proofUnit.maxShare,
          420,
        );

        const explanations = [];
        let explorerPlacement = explorerPreference.collapsed ? "trigger" : explorerPreference.placement;
        if (!explorerUnit.allowed.includes(explorerPlacement)) explorerPlacement = explorerUnit.prefer;

        function editorInlineWith(explorer, inspector) {
          return width
            - activityInline
            - (explorer === "left" ? explorerInline : 0)
            - (inspector === "right" ? inspectorInline : 0);
        }

        function editorBlockWith(inspector, proofPlacement) {
          const proofBlock = proofPlacement === "bottom"
            ? (proofExpanded && !proofPreference.collapsed ? proofExpandedBlock : compactProofBlock)
            : 0;
          return height
            - titleBlock
            - statusBlock
            - proofBlock
            - (inspector === "bottom" ? inspectorBottomBlock : 0);
        }

        if (
          explorerPlacement === "left"
          && editorInlineWith("left", "trigger") < editorUnit.minInline
        ) {
          explorerPlacement = "trigger";
          explanations.push("Explorer collapsed to its activity-rail trigger because the center editor minimum could not fit.");
        }

        const inspectorOrder = preferencePlacementOrder(inspectorPreference, inspectorUnit);
        let inspectorPlacement = "trigger";
        for (const placement of inspectorOrder) {
          if (placement === "right") {
            if (editorInlineWith(explorerPlacement, "right") >= editorUnit.minInline) {
              inspectorPlacement = "right";
              break;
            }
            continue;
          }
          if (placement === "bottom") {
            if (
              editorInlineWith(explorerPlacement, "bottom") >= editorUnit.minInline
              && editorBlockWith("bottom", proofPreference.placement) >= editorUnit.minBlock
            ) {
              inspectorPlacement = "bottom";
              break;
            }
            continue;
          }
          if (placement === "tab") {
            if (
              editorInlineWith(explorerPlacement, "tab") >= Math.min(editorUnit.minInline, width - activityInline)
              && editorBlockWith("tab", proofPreference.placement) >= editorUnit.minBlock
            ) {
              inspectorPlacement = "tab";
              break;
            }
            continue;
          }
          if (placement === "trigger") {
            inspectorPlacement = "trigger";
            break;
          }
        }

        if (inspectorPlacement !== (inspectorPreference.collapsed ? "trigger" : inspectorPreference.placement)) {
          explanations.push(
            `Inspector preference ${inspectorPreference.placement} was retained but realized as ${inspectorPlacement} at this capacity.`,
          );
        }

        const proofOrder = preferencePlacementOrder(proofPreference, proofUnit);
        let proofPlacement = "trigger";
        for (const placement of proofOrder) {
          if (placement === "bottom") {
            const expanded = proofExpanded && !proofPreference.collapsed;
            const requiredBlock = expanded ? proofExpandedBlock : compactProofBlock;
            const remaining = height
              - titleBlock
              - statusBlock
              - requiredBlock
              - (inspectorPlacement === "bottom" ? inspectorBottomBlock : 0);
            if (remaining >= editorUnit.minBlock) {
              proofPlacement = "bottom";
              break;
            }
            continue;
          }
          if (placement === "tab") {
            if (height - titleBlock - statusBlock >= editorUnit.minBlock) {
              proofPlacement = "tab";
              break;
            }
            continue;
          }
          if (placement === "trigger") {
            proofPlacement = "trigger";
            break;
          }
        }

        if (proofPlacement !== (proofPreference.collapsed ? "trigger" : proofPreference.placement)) {
          explanations.push(
            `Proof preference ${proofPreference.placement} was retained but realized as ${proofPlacement} at this capacity.`,
          );
        }

        let capacity = "wide";
        if (explorerPlacement !== "left" || inspectorPlacement === "tab") capacity = "narrow";
        else if (inspectorPlacement === "bottom") capacity = "medium";
        if (inspectorPlacement === "trigger" || editorInlineWith(explorerPlacement, inspectorPlacement) < editorUnit.minInline) {
          capacity = "compact";
        }

        const proofCollapsed = proofPreference.collapsed || !proofExpanded || proofPlacement !== "bottom";
        const proofBlock = proofPlacement === "bottom"
          ? (proofCollapsed ? compactProofBlock : proofExpandedBlock)
          : 0;
        const actualCenterTab = inspectorPlacement === "tab" && centerTab === "inspector"
          ? "inspector"
          : "editor";

        const remediated = (
          explorerPlacement !== (explorerPreference.collapsed ? "trigger" : explorerPreference.placement)
          || inspectorPlacement !== (inspectorPreference.collapsed ? "trigger" : inspectorPreference.placement)
          || proofPlacement !== (proofPreference.collapsed ? "trigger" : proofPreference.placement)
        );

        return {
          contractVersion: CONTRACT_VERSION,
          viewport: {width, height},
          capacity,
          complete: authored.complete,
          contractDiagnostics: {
            missing: [...authored.missing],
            mismatches: [...authored.mismatches],
            rawGeometryViolations: [...normalized.rawGeometryViolations],
          },
          preferred: {
            explorer: explorerPreference.placement,
            inspector: inspectorPreference.placement,
            proof: proofPreference.placement,
          },
          actual: {
            explorer: explorerPlacement,
            inspector: inspectorPlacement,
            proof: proofPlacement,
            centerTab: actualCenterTab,
          },
          dimensions: {
            activityInline,
            explorerInline: explorerPlacement === "left" ? explorerInline : 0,
            inspectorInline: inspectorPlacement === "right" ? inspectorInline : 0,
            inspectorBottomBlock: inspectorPlacement === "bottom" ? inspectorBottomBlock : 0,
            proofBlock,
            titleBlock,
            statusBlock,
          },
          collapsed: {
            explorer: explorerPreference.collapsed,
            inspector: inspectorPreference.collapsed,
            proof: proofCollapsed,
          },
          remediated,
          explanations,
          preferences: normalized,
        };
      }

      function setDataset(root, name, value) {
        if (value === undefined || value === null || value === "") {
          delete root.dataset[name];
        } else {
          root.dataset[name] = String(value);
        }
      }

      function applyGeneratedLayoutContract(root) {
        if (!root || typeof root.querySelectorAll !== "function") {
          return {
            version: GENERATED_LAYOUT_CONTRACT.version,
            complete: false,
            applied: [],
            missing: GENERATED_LAYOUT_CONTRACT.rules.map((rule) => rule.id),
          };
        }

        const applied = [];
        const missing = [];
        for (const rule of GENERATED_LAYOUT_CONTRACT.rules) {
          const matches = [];
          if (typeof root.matches === "function" && root.matches(rule.selector)) matches.push(root);
          root.querySelectorAll(rule.selector).forEach((node) => matches.push(node));
          if (!matches.length) {
            missing.push(rule.id);
            continue;
          }
          matches.forEach((node) => {
            Object.entries(rule.attributes).forEach(([name, value]) => node.setAttribute(name, value));
          });
          applied.push(rule.id);
        }

        return {
          version: GENERATED_LAYOUT_CONTRACT.version,
          complete: missing.length === 0,
          applied,
          missing,
        };
      }

      function applyResolvedLayout(root, resolved) {
        if (!root) return resolved;
        const style = root.style;
        setDataset(root, "mcelLayoutLive", "true");
        setDataset(root, "mcelLayoutVersion", resolved.contractVersion);
        setDataset(root, "mcelLayoutContractState", resolved.complete ? "complete" : "incomplete");
        setDataset(root, "mcelLayoutCapacity", resolved.capacity);
        setDataset(root, "mcelExplorerPlacement", resolved.actual.explorer);
        setDataset(root, "mcelInspectorPlacement", resolved.actual.inspector);
        setDataset(root, "mcelProofPlacement", resolved.actual.proof);
        setDataset(root, "mcelCenterTab", resolved.actual.centerTab);
        setDataset(root, "mcelLayoutRemediated", resolved.remediated ? "true" : "false");
        setDataset(root, "mcelExplorerPreferred", resolved.preferred.explorer);
        setDataset(root, "mcelInspectorPreferred", resolved.preferred.inspector);
        setDataset(root, "mcelProofPreferred", resolved.preferred.proof);

        style.setProperty("--mcel-code-editor-activity-inline", `${resolved.dimensions.activityInline}px`);
        style.setProperty("--mcel-code-editor-explorer-inline", `${resolved.dimensions.explorerInline}px`);
        style.setProperty("--mcel-code-editor-inspector-inline", `${resolved.dimensions.inspectorInline}px`);
        style.setProperty("--mcel-code-editor-inspector-bottom-block", `${resolved.dimensions.inspectorBottomBlock}px`);
        style.setProperty("--mcel-code-editor-proof-block", `${resolved.dimensions.proofBlock}px`);
        style.setProperty("--mcel-code-editor-status-block", `${resolved.dimensions.statusBlock}px`);
        style.setProperty("--mcel-code-editor-title-block", `${resolved.dimensions.titleBlock}px`);

        const centerTabs = root.querySelector("#code-editor-layout-center-tabs");
        if (centerTabs) {
          centerTabs.hidden = resolved.actual.inspector !== "tab";
          centerTabs.querySelectorAll("[data-code-editor-center-tab]").forEach((button) => {
            const selected = button.dataset.codeEditorCenterTab === resolved.actual.centerTab;
            button.setAttribute("aria-selected", selected ? "true" : "false");
            button.classList.toggle("active", selected);
          });
        }

        const proof = elementForUnit(root, "code-editor.proof");
        if (proof) {
          proof.dataset.mcelResolvedPlacement = resolved.actual.proof;
          if (resolved.actual.proof !== "bottom") {
            if (proof.dataset.expanded !== "false") proof.dataset.expanded = "false";
            const toggle = root.querySelector("#code-studio-toggle-assistant");
            if (toggle) {
              toggle.setAttribute("aria-expanded", "false");
              toggle.textContent = resolved.actual.proof === "tab" ? "Open proof tab" : "Open proof dock";
            }
          }
        }

        const status = root.querySelector("#code-editor-gridstack-status");
        if (status) {
          const remediation = resolved.remediated ? " · remediated" : "";
          status.textContent = `${resolved.actual.explorer}/${resolved.actual.inspector}/${resolved.actual.proof}${remediation}`;
          status.title = resolved.explanations.join(" ") || "Authored MCEL dock layout is active.";
        }

        const toggle = root.querySelector("#code-editor-gridstack-toggle");
        if (toggle) {
          toggle.textContent = "Layout";
          toggle.setAttribute("aria-label", "Open semantic layout controls");
        }

        if (typeof root.dispatchEvent === "function" && typeof globalObject.CustomEvent === "function") {
          root.dispatchEvent(new globalObject.CustomEvent("mcel:code-editor-layout-resolved", {
            detail: clone(resolved),
          }));
        }
        return resolved;
      }

      function validateOperation(operation, authored) {
        const source = operation && typeof operation === "object" ? operation : {};
        const kind = String(source.kind || "");
        if (!APPLICATION_CONTRACT.operations[kind]) {
          return {accepted: false, reason: `Unsupported layout operation: ${kind || "missing"}.`};
        }
        if (kind === "reset" || kind === "undo") return {accepted: true, operation: {kind}};

        const userId = String(source.userId || "");
        const unit = authored.units[userId];
        if (!unit) return {accepted: false, reason: `Unknown layout unit: ${userId || "missing"}.`};

        const rule = APPLICATION_CONTRACT.operations[kind];
        if (!unit.mutable.includes(rule.mutableCapability)) {
          return {accepted: false, reason: `${userId} does not allow ${rule.mutableCapability} changes.`};
        }

        if (kind === "dock") {
          const placement = String(source.placement || "");
          if (!unit.allowed.includes(placement)) {
            return {accepted: false, reason: `${placement || "Missing placement"} is not allowed for ${userId}.`};
          }
          return {accepted: true, operation: {kind, userId, placement}};
        }

        if (kind === "resize-share") {
          const share = numberOr(source.share, NaN);
          if (!Number.isFinite(share)) return {accepted: false, reason: "Resize share must be numeric."};
          return {
            accepted: true,
            operation: {
              kind,
              userId,
              share: clamp(share, 0.08, unit.maxShare),
            },
          };
        }

        if (kind === "collapse") {
          return {
            accepted: true,
            operation: {
              kind,
              userId,
              collapsed: source.collapsed === true,
            },
          };
        }

        if (kind === "tab-with") {
          if (!unit.allowed.includes("tab")) {
            return {accepted: false, reason: `${userId} cannot be tabbed.`};
          }
          const targetUserId = String(source.targetUserId || "");
          if (!authored.units[targetUserId]) {
            return {accepted: false, reason: `Unknown tab target: ${targetUserId || "missing"}.`};
          }
          return {accepted: true, operation: {kind, userId, targetUserId}};
        }

        return {accepted: false, reason: `Operation ${kind} is not implemented.`};
      }

      function applyOperationToPreferences(preferences, operation, authored) {
        const validation = validateOperation(operation, authored);
        if (!validation.accepted) return {...validation, preferences};

        const next = normalizePreferences(preferences, authored);
        const normalized = validation.operation;
        if (normalized.kind === "reset") {
          return {
            accepted: true,
            operation: normalized,
            preferences: normalizePreferences(DEFAULT_PREFERENCES, authored),
            explanation: "Restored the authored Code Editor layout.",
          };
        }

        const unit = next.units[normalized.userId];
        if (!unit) return {accepted: false, reason: "Operation does not target a user-mutable unit.", preferences};

        if (normalized.kind === "dock") {
          if (normalized.placement === "trigger") {
            // Trigger is a collapsed realization, not the durable wall preference.
            // Keep the last non-trigger placement so the unit can restore when reopened.
            unit.collapsed = true;
          } else {
            unit.placement = normalized.placement;
            unit.collapsed = false;
          }
        } else if (normalized.kind === "resize-share") {
          unit.preferredShare = normalized.share;
        } else if (normalized.kind === "collapse") {
          unit.collapsed = normalized.collapsed;
        } else if (normalized.kind === "tab-with") {
          unit.placement = "tab";
          unit.tabWith = normalized.targetUserId;
          unit.collapsed = false;
        }

        return {
          accepted: true,
          operation: normalized,
          preferences: next,
          explanation: `Applied ${normalized.kind} to ${normalized.userId}.`,
        };
      }

      function storageAdapter(storage) {
        if (storage) return storage;
        try {
          return globalObject.localStorage || null;
        } catch {
          return null;
        }
      }

      function readStoredPreferences(storage, authored) {
        try {
          const raw = storageAdapter(storage)?.getItem(STORAGE_KEY);
          return normalizePreferences(raw ? JSON.parse(raw) : DEFAULT_PREFERENCES, authored);
        } catch {
          return normalizePreferences(DEFAULT_PREFERENCES, authored);
        }
      }

      function writeStoredPreferences(storage, preferences) {
        try {
          storageAdapter(storage)?.setItem(STORAGE_KEY, JSON.stringify(preferences));
          return true;
        } catch {
          return false;
        }
      }

      function createSplitter(root, name, label) {
        let splitter = root.querySelector(`[data-code-editor-layout-splitter="${name}"]`);
        if (splitter) return splitter;
        splitter = root.ownerDocument.createElement("div");
        splitter.className = "code-editor-layout-splitter";
        splitter.dataset.codeEditorLayoutSplitter = name;
        splitter.dataset.mcGenerated = "layout-splitter";
        splitter.setAttribute("role", "separator");
        splitter.setAttribute("aria-label", label);
        splitter.setAttribute("tabindex", "0");
        root.append(splitter);
        return splitter;
      }

      function mount(root, options = {}) {
        if (!root) return null;
        if (root.__mcelCodeEditorLayoutController) return root.__mcelCodeEditorLayoutController;

        const authored = extractAuthoredContract(root);
        const store = storageAdapter(options.storage);
        try {
          store?.removeItem("main-computer-code-editor-gridstack-layout-v1");
          store?.removeItem("main-computer-code-editor-gridstack-enabled-v1");
        } catch {}
        let preferences = readStoredPreferences(options.storage, authored);
        let centerTab = "editor";
        let latest = null;
        let resizeTimer = 0;
        const history = [];

        const body = elementForUnit(root, "code-editor.workbench")?.querySelector(".code-studio-body")
          || root.querySelector(".code-studio-body");
        const shell = elementForUnit(root, "code-editor.workbench");
        const explorerSplitter = body ? createSplitter(body, "explorer", "Resize Explorer") : null;
        const inspectorSplitter = body ? createSplitter(body, "inspector", "Resize Inspector") : null;
        const proofSplitter = shell ? createSplitter(shell, "proof", "Resize Bottom Proof Dock") : null;

        function viewport() {
          const rect = root.getBoundingClientRect?.();
          return {
            width: rect?.width > 0 ? rect.width : numberOr(globalObject.innerWidth, 1440),
            height: rect?.height > 0 ? rect.height : numberOr(globalObject.innerHeight, 900),
          };
        }

        function resolve() {
          const proof = elementForUnit(root, "code-editor.proof");
          latest = resolveLayout({
            viewport: viewport(),
            authored,
            preferences,
            proofExpanded: proof?.dataset?.expanded === "true",
            centerTab,
          });
          applyResolvedLayout(root, latest);
          updateMenuState();
          return latest;
        }

        function persist() {
          return writeStoredPreferences(options.storage, preferences);
        }

        function pushHistory() {
          history.push(clone(preferences));
          if (history.length > HISTORY_LIMIT) history.shift();
        }

        function applyOperation(operation, {save = true} = {}) {
          if (operation?.kind === "undo") {
            if (!history.length) {
              return {accepted: false, reason: "No layout operation is available to undo."};
            }
            preferences = history.pop();
            if (save) persist();
            return {accepted: true, preferences: clone(preferences), resolved: resolve(), explanation: "Undid the previous layout operation."};
          }

          const result = applyOperationToPreferences(preferences, operation, authored);
          if (!result.accepted) return result;
          pushHistory();
          preferences = result.preferences;
          if (operation.kind === "tab-with") centerTab = "inspector";
          if (operation.kind === "dock" && operation.userId === "code-editor.inspector" && operation.placement !== "tab") {
            centerTab = "editor";
          }
          if (save) persist();
          return {...result, resolved: resolve()};
        }

        function setMenuOpen(open) {
          const menu = root.querySelector("#code-editor-layout-menu");
          const toggle = root.querySelector("#code-editor-gridstack-toggle");
          if (menu) menu.hidden = !open;
          if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
          setDataset(root, "mcelLayoutMenuOpen", open ? "true" : "false");
        }

        function updateMenuState() {
          const menu = root.querySelector("#code-editor-layout-menu");
          if (!menu || !latest) return;
          menu.querySelectorAll("[data-code-editor-layout-placement]").forEach((button) => {
            const userId = button.dataset.codeEditorLayoutUserId;
            const placement = button.dataset.codeEditorLayoutPlacement;
            const unitName = userId?.split(".").pop();
            const active = latest.actual[unitName] === placement;
            const preferred = latest.preferred[unitName] === placement;
            button.setAttribute("aria-pressed", active ? "true" : "false");
            button.dataset.preferred = preferred ? "true" : "false";
          });
          const diagnostics = menu.querySelector("[data-code-editor-layout-diagnostics]");
          if (diagnostics) {
            diagnostics.textContent = latest.explanations.join(" ") || "Authored preferences fit this viewport.";
          }
        }

        function toggleExplorer() {
          if (latest?.actual.explorer === "trigger") {
            const open = root.dataset.mcelExplorerOverlayOpen !== "true";
            setDataset(root, "mcelExplorerOverlayOpen", open ? "true" : "false");
            return {accepted: true, overlayOpen: open};
          }
          return applyOperation({
            kind: "collapse",
            userId: "code-editor.explorer",
            collapsed: !preferences.units["code-editor.explorer"].collapsed,
          });
        }

        function activateCenterTab(tab) {
          centerTab = tab === "inspector" ? "inspector" : "editor";

          if (centerTab === "inspector" && preferences.units["code-editor.inspector"]?.collapsed) {
            return applyOperation({
              kind: "collapse",
              userId: "code-editor.inspector",
              collapsed: false,
            });
          }

          if (centerTab === "inspector" && (latest?.actual.inspector === "right" || latest?.actual.inspector === "bottom")) {
            elementForUnit(root, "code-editor.inspector")
              ?.querySelector("button, input, select, textarea, [tabindex]")
              ?.focus?.();
            return latest;
          }

          if (latest?.actual.inspector !== "tab" && centerTab === "inspector") {
            return applyOperation({
              kind: "tab-with",
              userId: "code-editor.inspector",
              targetUserId: "code-editor.editor",
            });
          }
          return resolve();
        }

        function bindOperationButtons() {
          root.querySelector("#code-editor-gridstack-toggle")?.addEventListener("click", () => {
            const open = root.dataset.mcelLayoutMenuOpen !== "true";
            setMenuOpen(open);
          });
          root.querySelector("#code-editor-layout-menu-close")?.addEventListener("click", () => setMenuOpen(false));
          root.querySelector("#code-editor-gridstack-reset")?.addEventListener("click", () => {
            applyOperation({kind: "reset"});
            centerTab = "editor";
            setMenuOpen(false);
          });
          root.querySelector("#code-editor-layout-undo")?.addEventListener("click", () => applyOperation({kind: "undo"}));

          root.querySelectorAll("[data-code-editor-layout-placement]").forEach((button) => {
            button.addEventListener("click", () => {
              const userId = button.dataset.codeEditorLayoutUserId;
              const placement = button.dataset.codeEditorLayoutPlacement;
              if (placement === "tab") {
                applyOperation({
                  kind: "tab-with",
                  userId,
                  targetUserId: "code-editor.editor",
                });
              } else {
                applyOperation({kind: "dock", userId, placement});
              }
            });
          });

          root.querySelectorAll("[data-code-editor-layout-collapse]").forEach((button) => {
            button.addEventListener("click", () => {
              const userId = button.dataset.codeEditorLayoutUserId;
              const current = preferences.units[userId];
              applyOperation({kind: "collapse", userId, collapsed: !current?.collapsed});
            });
          });

          root.querySelectorAll("[data-code-editor-center-tab]").forEach((button) => {
            button.addEventListener("click", () => activateCenterTab(button.dataset.codeEditorCenterTab));
          });

          root.querySelector('[data-code-studio-panel="explorer"]')?.addEventListener("click", toggleExplorer);
          root.querySelectorAll('[data-code-studio-panel="source"], [data-code-studio-panel="runtime"], [data-code-studio-panel="contract"]')
            .forEach((button) => button.addEventListener("click", () => activateCenterTab("editor")));
        }

        function attachSplitter(splitter, userId, axis) {
          if (!splitter) return;
          let start = null;

          function shareFromEvent(event) {
            const rect = root.getBoundingClientRect();
            if (axis === "left") {
              return (event.clientX - rect.left - (latest?.dimensions.activityInline || 44)) / Math.max(1, rect.width);
            }
            if (axis === "inspector") {
              if (latest?.actual.inspector === "bottom") {
                return (rect.bottom - event.clientY - (latest?.dimensions.statusBlock || 22) - (latest?.dimensions.proofBlock || 0))
                  / Math.max(1, rect.height);
              }
              return (rect.right - event.clientX) / Math.max(1, rect.width);
            }
            if (axis === "right") {
              return (rect.right - event.clientX) / Math.max(1, rect.width);
            }
            return (rect.bottom - event.clientY - (latest?.dimensions.statusBlock || 22)) / Math.max(1, rect.height);
          }

          function finish(event) {
            if (!start) return;
            splitter.releasePointerCapture?.(event.pointerId);
            const share = shareFromEvent(event);
            start = null;
            applyOperation({kind: "resize-share", userId, share});
          }

          splitter.addEventListener("pointerdown", (event) => {
            start = {pointerId: event.pointerId};
            splitter.setPointerCapture?.(event.pointerId);
            event.preventDefault();
          });
          splitter.addEventListener("pointerup", finish);
          splitter.addEventListener("pointercancel", () => { start = null; });
          splitter.addEventListener("keydown", (event) => {
            const delta = (event.key === "ArrowRight" || event.key === "ArrowDown") ? 0.01
              : (event.key === "ArrowLeft" || event.key === "ArrowUp") ? -0.01
                : 0;
            if (!delta) return;
            const current = preferences.units[userId]?.preferredShare || 0.2;
            applyOperation({kind: "resize-share", userId, share: current + delta});
            event.preventDefault();
          });
        }

        bindOperationButtons();
        attachSplitter(explorerSplitter, "code-editor.explorer", "left");
        attachSplitter(inspectorSplitter, "code-editor.inspector", "inspector");
        attachSplitter(proofSplitter, "code-editor.proof", "bottom");

        const proof = elementForUnit(root, "code-editor.proof");
        if (proof && typeof globalObject.MutationObserver === "function") {
          const observer = new globalObject.MutationObserver((records) => {
            if (records.some((entry) => entry.attributeName === "data-expanded")) resolve();
          });
          observer.observe(proof, {attributes: true, attributeFilter: ["data-expanded"]});
        }

        globalObject.addEventListener?.("resize", () => {
          globalObject.clearTimeout?.(resizeTimer);
          resizeTimer = globalObject.setTimeout?.(resolve, 60) || 0;
        });

        const controller = {
          contract: authored,
          get preferences() { return clone(preferences); },
          get resolved() { return clone(latest); },
          resolve,
          persist,
          applyOperation,
          activateCenterTab,
          toggleExplorer,
          setMenuOpen,
          reset() { return applyOperation({kind: "reset"}); },
          undo() { return applyOperation({kind: "undo"}); },
          exportPreferences() { return clone(preferences); },
        };

        root.__mcelCodeEditorLayoutController = controller;
        globalObject.MainComputerCodeEditorLayoutController = controller;
        resolve();
        return controller;
      }

      globalObject.MainComputerCodeEditorLayout = Object.freeze({
        CONTRACT_VERSION,
        STORAGE_KEY,
        APPLICATION_CONTRACT,
        GENERATED_LAYOUT_CONTRACT,
        SAFE_DEFAULTS,
        DEFAULT_PREFERENCES,
        extractAuthoredContract,
        normalizePreferences,
        resolveLayout,
        validateOperation,
        applyOperationToPreferences,
        applyGeneratedLayoutContract,
        applyResolvedLayout,
        mount,
      });
    })();
