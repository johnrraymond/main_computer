    (() => {
      const root = document.querySelector("#code-editor-app");
      if (!root) return;

      const sourceEditor = root.querySelector("#code-studio-source-editor");
      const gutter = root.querySelector("#code-studio-line-gutter");
      const runtimePreview = root.querySelector("#code-studio-runtime-preview");
      const serializedOutput = root.querySelector("#code-studio-serialized-output");
      const contractReport = root.querySelector("#code-studio-contract-report");
      const contractEnvelope = root.querySelector("#code-studio-contract-envelope");
      const scmEvidencePanel = root.querySelector("#code-studio-scm-evidence-panel");
      const refreshScmEvidenceButton = root.querySelector("#code-studio-refresh-scm-evidence");
      const status = root.querySelector("#code-studio-status");
      const runtimeState = root.querySelector("#code-studio-runtime-state");
      const validateButton = root.querySelector("#code-studio-validate");
      const serializeButton = root.querySelector("#code-studio-serialize");
      const mountButton = root.querySelector("#code-studio-mount-runtime");
      const damageButton = root.querySelector("#code-studio-damage-runtime");
      const repairButton = root.querySelector("#code-studio-repair-runtime");
      const commitButton = root.querySelector("#code-studio-commit-runtime");
      const panes = [...root.querySelectorAll("[data-code-studio-pane]")];
      const tabButtons = [...root.querySelectorAll("[data-code-studio-tab]")];
      const SCM_EVIDENCE_FILTERS = [
        "all",
        "violations",
        "component",
        "route",
        "effect",
        "layout",
        "style",
        "serialization",
        "repair"
      ];
      const SCM_EVIDENCE_PACKET_VERSION = "1.0.0";
      const MCEL_RUNTIME_PACKAGE_VERSION = "mcel-runtime.v0.1.15";

      if (!sourceEditor || !runtimePreview) return;

      const studioState = {
        mounted: false,
        dirty: false,
        damaged: false,
        selectedPath: "src/app.js",
        lastReport: null,
        lastScmGates: null,
        scmEvidenceFilter: "all",
        selectedScmEvidenceKey: "",
        selectedScmEvidenceSnapshot: null,
        lastScmReplayResult: null,
        lastScmDebugPacket: null,
        lastScmDebugPacketJson: "",
        lastScmDebugPacketExport: null,
        lastSerializationGate: null,
        lastRepairGate: null,
        lastSaveFileEffectGate: null,
      };

      let scmInstance = null;
      let scmRouteInstance = null;
      let scmRouteKey = "";

      function resolveScmBridge() {
        const mcel = window.MCEL || null;
        const studio = window.McelCodeStudioScm || null;
        if (!mcel || !studio || typeof studio.createDefaultInstance !== "function") return null;
        return {mcel, studio};
      }

      function normalizeScmFileId(value, fallback = "file") {
        const normalized = String(value || fallback || "file")
          .trim()
          .replace(/[^A-Za-z0-9_-]+/g, "-")
          .replace(/^-+|-+$/g, "");
        return normalized || "file";
      }

      function sourceForScm(fields = workspaceFields()) {
        return {
          workspace: {
            manifest: {
              id: "workspace-main",
              title: fields.title || "MCEL Code Studio",
              summary: fields.summary || "Source-safe code editor example."
            },
            files: fields.files.map((entry, index) => ({
              id: normalizeScmFileId(entry.field || entry.path, `file-${index + 1}`),
              path: entry.path,
              language: entry.language,
              text: entry.value
            }))
          }
        };
      }

      function selectedScmFileId(fields = workspaceFields()) {
        const file = selectedFile(fields);
        return file ? normalizeScmFileId(file.field || file.path, `file-${file.index + 1}`) : null;
      }

      function syncScmInstance() {
        const bridge = resolveScmBridge();
        if (!bridge) return null;

        const fields = workspaceFields();
        const source = sourceForScm(fields);
        const activeFileId = selectedScmFileId(fields);
        const selectedPanel = root.querySelector("[data-code-studio-tab].active")?.dataset.codeStudioTab || "source";
        const bottomDockExpanded = root.querySelector("#code-studio-bottom-panel")?.dataset.expanded === "true";

        if (!scmInstance) {
          scmInstance = bridge.studio.createDefaultInstance({
            id: "code-studio-live-scm-instance",
            source,
            state: {
              activeFileId,
              openTabs: activeFileId ? [activeFileId] : [],
              selectedPanel,
              bottomDockExpanded,
              dirty: studioState.dirty
            }
          });
        }

        scmInstance.source = source;
        scmInstance.state.activeFileId = activeFileId;
        scmInstance.state.selectedPanel = selectedPanel;
        scmInstance.state.bottomDockExpanded = bottomDockExpanded;
        scmInstance.state.dirty = Boolean(studioState.dirty);
        scmInstance.state.openTabs = Array.from(new Set([...(scmInstance.state.openTabs || []), activeFileId].filter(Boolean)));
        scmInstance.runtime.loadedFile = source.workspace.files.find((file) => file.id === activeFileId) || null;
        scmInstance.runtime.workbench = scmInstance.runtime.workbench || {};
        scmInstance.runtime.workbench.shell = {
          ...(scmInstance.runtime.workbench.shell || {}),
          mounted: Boolean(studioState.mounted),
          damaged: Boolean(studioState.damaged),
          selectedPath: studioState.selectedPath
        };

        return scmInstance;
      }


      function routeParamsForScm(fields = workspaceFields()) {
        const source = sourceForScm(fields);
        const activeFileId = selectedScmFileId(fields);
        if (!activeFileId) return null;
        return {
          workspaceId: normalizeScmFileId(source.workspace.manifest.id || "workspace-main", "workspace-main"),
          fileId: activeFileId
        };
      }

      function routeQueryForScm() {
        const selectedPanel = root.querySelector("[data-code-studio-tab].active")?.dataset.codeStudioTab || "source";
        return {
          panel: selectedPanel
        };
      }

      function currentScmRouteKey(params, query) {
        return JSON.stringify({
          workspaceId: params?.workspaceId || "",
          fileId: params?.fileId || "",
          panel: query?.panel || "source"
        });
      }

      function syncScmRouteInstance(options = {}) {
        const bridge = resolveScmBridge();
        const componentInstance = syncScmInstance();
        if (!bridge || !componentInstance || typeof bridge.studio.createDefaultRouteInstance !== "function") return null;

        const fields = workspaceFields();
        const params = routeParamsForScm(fields);
        if (!params) return null;
        const query = routeQueryForScm();

        if (!scmRouteInstance) {
          scmRouteInstance = bridge.studio.createDefaultRouteInstance({
            mcel: bridge.mcel,
            id: "code-studio-live-scm-route",
            componentInstance,
            data: {}
          });
        }

        scmRouteInstance.componentInstance = componentInstance;

        if (options.enter === false) return scmRouteInstance;

        const routeKey = currentScmRouteKey(params, query);
        if (options.forceEnter === true || scmRouteKey !== routeKey || scmRouteInstance.status !== "entered") {
          bridge.mcel.enterRoute(scmRouteInstance, {params, query});
          scmRouteKey = routeKey;
        }

        return scmRouteInstance;
      }

      function exportScmRouteEvidence() {
        const bridge = resolveScmBridge();
        const routeInstance = syncScmRouteInstance({enter: false});
        if (!bridge || !routeInstance || typeof bridge.mcel.exportRouteEvidence !== "function") {
          return {
            kind: "mcel-scm-route-evidence-packet",
            routeName: "",
            instanceId: "",
            evidence: [],
            unavailable: true
          };
        }
        return bridge.mcel.exportRouteEvidence(routeInstance);
      }

      function runScmRouteGate(label, callback) {
        const bridge = resolveScmBridge();
        if (!bridge) {
          return {
            label,
            ok: true,
            skipped: true,
            message: "SCM route runtime is not available in this page load."
          };
        }

        try {
          const result = callback(bridge.mcel);
          return {
            label,
            ok: result?.ok !== false,
            kind: result?.kind || "mcel-scm-route-operation-result",
            code: "",
            result
          };
        } catch (error) {
          const violation = error?.violation || null;
          return {
            label,
            ok: false,
            kind: violation?.kind || "mcel-scm-violation",
            code: violation?.code || "SCM_ROUTE_OPERATION_EXCEPTION",
            message: violation?.message || error?.message || String(error),
            violation
          };
        }
      }

      function enterScmRouteAndRunLoaders(options = {}) {
        const routeGate = runScmRouteGate("route:enter", (mcel) => {
          const routeInstance = syncScmRouteInstance({forceEnter: options.forceEnter === true});
          if (!routeInstance) {
            return {
              kind: "mcel-scm-route-enter-result",
              ok: true,
              skipped: true
            };
          }

          const loadWorkspace = mcel.runRouteLoader(routeInstance, "loadWorkspace");
          const loadFile = mcel.runRouteLoader(routeInstance, "loadFile");

          return {
            kind: "mcel-scm-live-route-sync-result",
            ok: loadWorkspace?.ok !== false && loadFile?.ok !== false,
            routeName: routeInstance.routeName,
            params: {...routeInstance.params},
            query: {...routeInstance.query},
            data: JSON.parse(JSON.stringify(routeInstance.data || {})),
            loaders: {
              loadWorkspace,
              loadFile
            }
          };
        });

        const workspaceEffect = runScmGate("effect:loadWorkspace", (mcel, instance) => {
          const params = routeParamsForScm();
          return mcel.runEffect(instance, "loadWorkspace", {
            workspaceId: params?.workspaceId || "workspace-main"
          });
        });
        const fileEffect = runScmGate("effect:loadFile", (mcel, instance) => {
          const params = routeParamsForScm();
          return mcel.runEffect(instance, "loadFile", {
            fileId: params?.fileId || selectedScmFileId()
          });
        });

        return {
          label: "route-and-loaders",
          ok: routeGate.ok && workspaceEffect.ok && fileEffect.ok,
          route: routeGate,
          effects: {
            loadWorkspace: workspaceEffect,
            loadFile: fileEffect
          }
        };
      }

      function requestScmRouteLeave(options = {}) {
        return runScmRouteGate("route:leave", (mcel) => {
          const routeInstance = syncScmRouteInstance({enter: true});
          if (!routeInstance) {
            return {
              kind: "mcel-scm-route-leave-result",
              ok: true,
              skipped: true
            };
          }
          return mcel.leaveRoute(routeInstance, options.resolution ? {resolution: options.resolution} : {});
        });
      }

      function canNavigateScmRoute(nextPath) {
        if (nextPath === studioState.selectedPath) return true;
        const leaveGate = requestScmRouteLeave();
        if (!leaveGate.ok || leaveGate.result?.blocked) {
          const blockers = leaveGate.result?.blockers || leaveGate.violation?.blockers || [];
          setStatus(`SCM route blocked navigation away from ${studioState.selectedPath}: ${blockers.join(", ") || "dirty state"}. Commit the draft or discard it before switching files.`);
          showPane("contract");
          return false;
        }
        return true;
      }

      function exportScmEvidence() {
        const bridge = resolveScmBridge();
        const instance = syncScmInstance();
        if (!bridge || !instance || typeof bridge.mcel.exportScmEvidence !== "function") {
          return {
            kind: "mcel-scm-evidence-packet",
            componentName: "CodeStudio",
            instanceId: "",
            evidence: [],
            unavailable: true
          };
        }
        return bridge.mcel.exportScmEvidence(instance);
      }

      function runScmGate(label, callback) {
        const bridge = resolveScmBridge();
        const instance = syncScmInstance();
        if (!bridge || !instance) {
          return {
            label,
            ok: true,
            skipped: true,
            message: "SCM runtime is not available in this page load."
          };
        }

        try {
          const result = callback(bridge.mcel, instance);
          return {
            label,
            ok: result?.ok !== false,
            kind: result?.kind || "mcel-scm-operation-result",
            code: "",
            result
          };
        } catch (error) {
          const violation = error?.violation || null;
          return {
            label,
            ok: false,
            kind: violation?.kind || "mcel-scm-violation",
            code: violation?.code || "SCM_OPERATION_EXCEPTION",
            message: violation?.message || error?.message || String(error),
            violation
          };
        }
      }

      function runScmTransition(name, payload = {}) {
        return runScmGate(`transition:${name}`, (mcel, instance) => mcel.transition(instance, name, payload));
      }

      function scopedNodes(selector) {
        const nodes = [];
        if (root.matches?.(selector)) nodes.push(root);
        root.querySelectorAll?.(selector).forEach((node) => nodes.push(node));
        return nodes;
      }

      function scopedNode(selector) {
        return scopedNodes(selector)[0] || null;
      }

      function applyScmSurfaceStyles(selector, styles) {
        scopedNodes(selector).forEach((node) => {
          Object.entries(styles).forEach(([property, value]) => {
            node.style[property] = value;
          });
        });
      }

      function ensureCodeStudioScmSurfaceStyles() {
        const bottomDock = scopedNode("#code-studio-bottom-panel");
        applyScmSurfaceStyles("#code-editor-app", {
          backgroundColor: "#1e1e1e",
          overflow: "hidden"
        });
        applyScmSurfaceStyles(".code-studio-shell", {
          display: "grid",
          overflow: "hidden"
        });
        applyScmSurfaceStyles(".code-studio-body", {
          display: "grid"
        });
        applyScmSurfaceStyles(".code-studio-titlebar button", {
          backgroundColor: "#2d2d30",
          color: "#dcdcdc"
        });
        if (bottomDock?.dataset.expanded === "true") {
          bottomDock.style.height = "min(360px, 45%)";
          bottomDock.style.maxHeight = "";
        } else if (bottomDock) {
          bottomDock.style.height = "38px";
          bottomDock.style.maxHeight = "80px";
          bottomDock.style.overflow = "hidden";
        }
      }

      function readComputed(selector, properties) {
        const node = scopedNode(selector);
        if (!node || typeof window.getComputedStyle !== "function") return {};
        const computed = window.getComputedStyle(node);
        return properties.reduce((values, property) => {
          values[property] = computed[property] || computed.getPropertyValue(property) || "";
          return values;
        }, {});
      }

      function collectLayoutObservation() {
        ensureCodeStudioScmSurfaceStyles();
        const bottomDock = scopedNode("#code-studio-bottom-panel");
        const rootRect = root.getBoundingClientRect?.() || {height: 0};
        const documentHeight = document.documentElement?.scrollHeight || rootRect.height || 0;
        const rootHeight = rootRect.height || root.offsetHeight || 1;

        return {
          computed: {
            ".code-studio-shell": readComputed(".code-studio-shell", ["display", "overflow"]),
            ".code-studio-body": readComputed(".code-studio-body", ["display"])
          },
          regions: {
            activitybar: Boolean(root.querySelector(".code-studio-activitybar")),
            sidebar: Boolean(root.querySelector(".code-studio-sidebar")),
            editorGroup: Boolean(root.querySelector(".code-studio-editor-group")),
            inspector: Boolean(root.querySelector(".code-studio-inspector")),
            bottomDock: Boolean(bottomDock),
            statusbar: Boolean(root.querySelector(".code-studio-statusbar"))
          },
          rects: {
            "#code-studio-bottom-panel": {
              height: bottomDock?.getBoundingClientRect?.().height || bottomDock?.offsetHeight || 0
            }
          },
          documentHeightRatio: rootHeight ? documentHeight / rootHeight : 1
        };
      }

      function collectStyleObservation() {
        ensureCodeStudioScmSurfaceStyles();
        return {
          computed: {
            "#code-editor-app": readComputed("#code-editor-app", ["backgroundColor"]),
            ".code-studio-body": readComputed(".code-studio-body", ["display"]),
            ".code-studio-titlebar button": readComputed(".code-studio-titlebar button", ["backgroundColor", "color"]),
            "button": readComputed("button", ["backgroundColor"])
          },
          globalLeakage: []
        };
      }

      function runScmRuntimeChecks() {
        const bridge = resolveScmBridge();
        const available = Boolean(bridge);
        const layout = runScmGate("layout", (mcel, instance) => mcel.checkLayoutContract(instance, collectLayoutObservation()));
        const style = runScmGate("style", (mcel, instance) => mcel.checkStyleContract(instance, collectStyleObservation()));
        const validation = runScmGate("effect:runValidation", (mcel, instance) => mcel.runEffect(instance, "runValidation", {
          selectedPath: studioState.selectedPath
        }));
        const route = enterScmRouteAndRunLoaders();
        const evidence = exportScmEvidence();
        const routeEvidence = exportScmRouteEvidence();
        const gates = {
          available,
          ok: [layout, style, validation, route].every((entry) => entry.ok),
          layout: {
            ok: layout.ok,
            code: layout.code || "",
            violations: layout.result?.violations || []
          },
          style: {
            ok: style.ok,
            code: style.code || "",
            violations: style.result?.violations || []
          },
          validation: {
            ok: validation.ok,
            code: validation.code || ""
          },
          route: {
            ok: route.ok,
            code: route.route?.code || "",
            params: route.route?.result?.params || {},
            query: route.route?.result?.query || {},
            data: route.route?.result?.data || {},
            effectLoadWorkspaceOk: Boolean(route.effects?.loadWorkspace?.ok),
            effectLoadFileOk: Boolean(route.effects?.loadFile?.ok)
          },
          evidenceCount: evidence.evidence.length,
          routeEvidenceCount: routeEvidence.evidence.length,
          recentEvidence: evidence.evidence.slice(-8),
          recentRouteEvidence: routeEvidence.evidence.slice(-8)
        };
        studioState.lastScmGates = gates;
        return gates;
      }

      function escapeHtml(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function evidenceEntryLabel(entry) {
        const parts = [
          entry.phase || entry.kind || "evidence",
          entry.componentName || entry.routeName || "",
          entry.transitionName || entry.effectName || entry.loaderName || entry.strategyName || entry.childName || "",
          entry.code || ""
        ].filter(Boolean);
        return parts.join(" · ");
      }

      function evidenceEntryScope(entry, fallback = "component") {
        if (entry.scope) return entry.scope;
        if (entry.routeName || entry.loaderName || String(entry.phase || "").startsWith("route")) return "route";
        if (entry.effectName || String(entry.phase || "").includes("effect")) return "effect";
        if (entry.strategyName || String(entry.phase || "").includes("repair")) return "repair";
        if (String(entry.phase || "").includes("serialize") || String(entry.code || "").includes("SERIAL")) return "serialization";
        if (String(entry.phase || "").includes("layout") || String(entry.code || "").includes("LAYOUT")) return "layout";
        if (String(entry.phase || "").includes("style") || String(entry.code || "").includes("STYLE")) return "style";
        return fallback;
      }

      function evidenceEntryIsViolation(entry) {
        return entry.ok === false
          || entry.kind === "mcel-scm-violation"
          || String(entry.code || "").startsWith("SCM_")
          || entry.severity === "blocking";
      }

      function evidenceEntryKey(entry, index) {
        return [
          evidenceEntryScope(entry),
          entry.phase || "",
          entry.componentName || entry.routeName || "",
          entry.transitionName || entry.effectName || entry.loaderName || entry.strategyName || entry.childName || "",
          entry.code || "",
          entry.path || entry.target || "",
          index
        ].join("|");
      }

      function evidenceFilterMatches(entry, filter) {
        if (!filter || filter === "all") return true;
        if (filter === "violations") return evidenceEntryIsViolation(entry);
        return evidenceEntryScope(entry) === filter;
      }

      function normalizeEvidenceEntries(componentEvidence = [], routeEvidence = []) {
        return [
          ...componentEvidence.map((entry, index) => ({...entry, scope: evidenceEntryScope(entry, "component"), sourceIndex: index})),
          ...routeEvidence.map((entry, index) => ({...entry, scope: evidenceEntryScope(entry, "route"), sourceIndex: index}))
        ].map((entry, index) => ({...entry, evidenceKey: evidenceEntryKey(entry, index)}));
      }

      function summarizeEvidence(entries = []) {
        const summary = {
          total: entries.length,
          ok: 0,
          violations: 0,
          blocking: 0,
          phases: {},
          scopes: {}
        };

        entries.forEach((entry) => {
          const phase = entry.phase || entry.kind || "unknown";
          const scope = evidenceEntryScope(entry);
          summary.phases[phase] = (summary.phases[phase] || 0) + 1;
          summary.scopes[scope] = (summary.scopes[scope] || 0) + 1;
          if (evidenceEntryIsViolation(entry)) {
            summary.violations += 1;
          } else {
            summary.ok += 1;
          }
          if (entry.severity === "blocking" || String(entry.code || "").includes("BLOCK")) {
            summary.blocking += 1;
          }
        });

        return summary;
      }

      function collectScmEvidenceSummary(report = studioState.lastReport) {
        const componentPacket = exportScmEvidence();
        const routePacket = exportScmRouteEvidence();
        const componentEvidence = componentPacket.evidence || [];
        const routeEvidence = routePacket.evidence || [];
        const combined = normalizeEvidenceEntries(componentEvidence, routeEvidence);

        return {
          available: !componentPacket.unavailable || !routePacket.unavailable,
          componentPacket,
          routePacket,
          component: summarizeEvidence(componentEvidence),
          route: summarizeEvidence(routeEvidence),
          combined: summarizeEvidence(combined),
          gates: report?.scm || studioState.lastScmGates || null,
          allEvidence: combined,
          recentComponentEvidence: componentEvidence.slice(-10),
          recentRouteEvidence: routeEvidence.slice(-10),
          recentEvidence: combined.slice(-24)
        };
      }

      function idleScmEvidenceEntry(filter = "all") {
        return {
          kind: "mcel-scm-evidence",
          phase: "idle",
          ok: true,
          scope: filter === "all" ? "component" : filter,
          evidenceKey: `idle|${filter}`,
          message: filter === "all"
            ? "No SCM evidence has been recorded yet. Validate, mount, edit, serialize, repair, or switch files."
            : `No recent SCM evidence entries match the ${filter} filter.`
        };
      }

      function visibleScmEvidenceEntries(summary, filter = studioState.scmEvidenceFilter || "all") {
        const entries = (summary?.recentEvidence || []).filter((entry) => evidenceFilterMatches(entry, filter));
        return entries.length ? entries : [idleScmEvidenceEntry(filter)];
      }

      function resolveSelectedScmEvidence(summary, filter = studioState.scmEvidenceFilter || "all", entries = null) {
        const candidates = entries || visibleScmEvidenceEntries(summary, filter);
        return candidates.find((entry) => entry.evidenceKey === studioState.selectedScmEvidenceKey)
          || candidates.find((entry) => evidenceEntryIsViolation(entry))
          || candidates[candidates.length - 1]
          || idleScmEvidenceEntry(filter);
      }

      function jsonSafeClone(value) {
        try {
          return JSON.parse(JSON.stringify(value, (_key, item) => {
            if (typeof item === "function" || typeof item === "symbol" || typeof item === "undefined") {
              return undefined;
            }
            if (item instanceof Error) {
              return {
                name: item.name,
                message: item.message,
                stack: item.stack || ""
              };
            }
            if (item && typeof Node !== "undefined" && item instanceof Node) {
              return {
                nodeType: item.nodeType,
                nodeName: item.nodeName || "",
                id: item.id || "",
                className: item.className || ""
              };
            }
            return item;
          }));
        } catch (error) {
          return {
            kind: "mcel-json-safe-clone-error",
            message: error?.message || String(error)
          };
        }
      }

      function gateStatusFrom(value, fallbackLabel = "") {
        const gate = value || {};
        return jsonSafeClone({
          label: gate.label || fallbackLabel,
          ok: gate.ok !== false,
          skipped: Boolean(gate.skipped),
          code: gate.code || gate.result?.code || "",
          message: gate.message || gate.result?.message || "",
          violations: gate.violations || gate.result?.violations || (gate.violation ? [gate.violation] : []),
          resultKind: gate.result?.kind || gate.kind || "",
          result: gate.result || null
        });
      }

      function collectDirtyStateSummary(fields = workspaceFields()) {
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        const activePane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "";
        const bottomDock = root.querySelector("#code-studio-bottom-panel");
        return {
          mounted: studioState.mounted,
          dirty: studioState.dirty,
          damaged: studioState.damaged,
          selectedPath: studioState.selectedPath,
          selectedFileId: selectedScmFileId(fields),
          sourceLength: sourceEditor.value.length,
          runtimeHtmlLength: runtimePreview.innerHTML.length,
          runtimeDraftMounted: Boolean(draft),
          runtimeDraftLength: draft?.value?.length || 0,
          activePane,
          bottomDockExpanded: bottomDock?.dataset.expanded === "true"
        };
      }

      function collectGateStatus(gates = studioState.lastScmGates || null) {
        return {
          available: Boolean(gates?.available),
          ok: gates?.ok !== false,
          layout: gateStatusFrom(gates?.layout, "layout"),
          style: gateStatusFrom(gates?.style, "style"),
          effect: {
            runValidation: gateStatusFrom(gates?.validation, "effect:runValidation"),
            loadWorkspace: gateStatusFrom({ok: Boolean(gates?.route?.effectLoadWorkspaceOk)}, "effect:loadWorkspace"),
            loadFile: gateStatusFrom({ok: Boolean(gates?.route?.effectLoadFileOk)}, "effect:loadFile"),
            saveFile: gateStatusFrom(studioState.lastSaveFileEffectGate, "effect:saveFile")
          },
          route: gateStatusFrom(gates?.route, "route"),
          serialization: gateStatusFrom(studioState.lastSerializationGate, "serialize"),
          repair: gateStatusFrom(studioState.lastRepairGate, "repair:rebuildWorkbenchShell")
        };
      }

      function buildScmEvidenceDebugPacket(options = {}) {
        const report = options.report || studioState.lastReport || null;
        const summary = collectScmEvidenceSummary(report);
        const filter = studioState.scmEvidenceFilter || "all";
        const visibleEntries = visibleScmEvidenceEntries(summary, filter);
        const selectedEntry = resolveSelectedScmEvidence(summary, filter, visibleEntries);
        const fields = workspaceFields();
        const selected = selectedFile(fields);
        const routeParams = routeParamsForScm(fields) || {};
        const routeQuery = routeQueryForScm();
        const studio = window.McelCodeStudioScm || {};
        const gates = summary.gates || studioState.lastScmGates || null;

        return jsonSafeClone({
          kind: "mcel-code-studio-scm-debug-packet",
          packetVersion: SCM_EVIDENCE_PACKET_VERSION,
          exportedAt: new Date().toISOString(),
          versions: {
            codeStudio: studio.version || "2.9.0",
            component: studio.version || "2.9.0",
            componentContract: studio.contract || "mcel.scm.code-studio.v1",
            route: studio.routeVersion || "1.1.0",
            routeContract: studio.routeContract || "mcel.scm.route.workspace-file.v1",
            runtimePackage: MCEL_RUNTIME_PACKAGE_VERSION
          },
          workspace: {
            title: fields.title,
            summary: fields.summary,
            selectedPath: studioState.selectedPath,
            selectedFile: selected ? {
              path: selected.path,
              language: selected.language,
              required: selected.required,
              field: selected.field,
              length: selected.value.length
            } : null,
            files: fields.files.map((file) => ({
              path: file.path,
              language: file.language,
              required: file.required,
              field: file.field,
              length: file.value.length
            })),
            route: {
              name: studio.routeName || "workspace.file",
              params: routeParams,
              query: routeQuery,
              key: currentScmRouteKey(routeParams, routeQuery)
            }
          },
          filters: {
            active: filter,
            available: [...SCM_EVIDENCE_FILTERS],
            visibleEvidenceCount: visibleEntries.length
          },
          dirtyState: collectDirtyStateSummary(fields),
          gates: collectGateStatus(gates),
          evidence: {
            component: summary.componentPacket,
            route: summary.routePacket,
            summary: {
              available: summary.available,
              component: summary.component,
              route: summary.route,
              combined: summary.combined
            },
            recent: summary.recentEvidence,
            visible: visibleEntries
          },
          selectedEvidence: formatEvidenceDetail(selectedEntry),
          lastReplayResult: studioState.lastScmReplayResult,
          lastReport: report ? {
            ok: report.ok,
            selectedPath: report.selectedPath,
            failed: report.failed || [],
            checks: (report.checks || []).map((check) => ({
              id: check.id,
              ok: check.ok,
              text: check.text
            }))
          } : null
        });
      }

      function exportScmEvidenceDebugPacket(options = {}) {
        if (options.refresh !== false) {
          runScmRuntimeChecks();
        }
        const packet = buildScmEvidenceDebugPacket({report: studioState.lastReport});
        const json = JSON.stringify(packet, null, 2);
        studioState.lastScmDebugPacket = packet;
        studioState.lastScmDebugPacketJson = json;
        studioState.lastScmDebugPacketExport = {
          exportedAt: packet.exportedAt,
          byteLength: json.length,
          evidenceCount: packet.evidence.summary.combined.total,
          violations: packet.evidence.summary.combined.violations
        };
        return packet;
      }

      async function copyScmEvidenceDebugPacket(packet) {
        const json = JSON.stringify(packet, null, 2);
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(json);
          return {ok: true, mode: "clipboard", byteLength: json.length};
        }

        const textarea = document.createElement("textarea");
        textarea.value = json;
        textarea.setAttribute("readonly", "readonly");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand?.("copy") === true;
        textarea.remove();
        return {ok: copied, mode: "execCommand", byteLength: json.length};
      }

      function downloadScmEvidenceDebugPacket(packet) {
        const json = JSON.stringify(packet, null, 2);
        const blob = new Blob([json], {type: "application/json"});
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        const stamp = (packet.exportedAt || new Date().toISOString()).replace(/[:.]/g, "-");
        anchor.href = url;
        anchor.download = `mcel-code-studio-scm-debug-packet-${stamp}.json`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(url);
        return {ok: true, mode: "download", byteLength: json.length, filename: anchor.download};
      }

      async function copyCurrentScmEvidenceDebugPacket() {
        const packet = exportScmEvidenceDebugPacket({refresh: true});
        try {
          const result = await copyScmEvidenceDebugPacket(packet);
          setStatus(result.ok
            ? `SCM evidence debug packet exported to clipboard (${result.byteLength} bytes).`
            : "SCM evidence debug packet was prepared, but this browser blocked clipboard copy.");
          return {packet, result};
        } catch (error) {
          setStatus(`SCM evidence debug packet was prepared, but clipboard export failed: ${error?.message || String(error)}.`);
          return {packet, result: {ok: false, mode: "clipboard", message: error?.message || String(error)}};
        }
      }

      function downloadCurrentScmEvidenceDebugPacket() {
        const packet = studioState.lastScmDebugPacket || exportScmEvidenceDebugPacket({refresh: true});
        const result = downloadScmEvidenceDebugPacket(packet);
        setStatus(`SCM evidence debug packet downloaded as ${result.filename}.`);
        return {packet, result};
      }

      function formatEvidenceDetail(entry) {
        const safeEntry = entry || {};
        return {
          scope: evidenceEntryScope(safeEntry),
          ok: safeEntry.ok !== false,
          phase: safeEntry.phase || "",
          code: safeEntry.code || "",
          severity: safeEntry.severity || "",
          componentName: safeEntry.componentName || "",
          routeName: safeEntry.routeName || "",
          transitionName: safeEntry.transitionName || "",
          effectName: safeEntry.effectName || "",
          loaderName: safeEntry.loaderName || "",
          strategyName: safeEntry.strategyName || "",
          path: safeEntry.path || "",
          target: safeEntry.target || "",
          message: safeEntry.message || "",
          reads: safeEntry.reads || safeEntry.declaredReads || [],
          writes: safeEntry.writes || safeEntry.declaredWrites || [],
          violation: safeEntry.violation || null,
          replayResult: safeEntry.replayResult || null
        };
      }

      function replayScmEvidenceEntry(entry) {
        const scope = evidenceEntryScope(entry);
        let result = null;

        if (scope === "layout") {
          result = runScmGate("replay:layout", (mcel, instance) => mcel.checkLayoutContract(instance, collectLayoutObservation()));
        } else if (scope === "style") {
          result = runScmGate("replay:style", (mcel, instance) => mcel.checkStyleContract(instance, collectStyleObservation()));
        } else if (entry?.effectName) {
          result = runScmGate(`replay:effect:${entry.effectName}`, (mcel, instance) => mcel.runEffect(instance, entry.effectName, {
            selectedPath: studioState.selectedPath
          }));
        } else if (entry?.loaderName) {
          const routeInstance = syncScmRouteInstance({enter: true});
          result = runScmRouteGate(`replay:loader:${entry.loaderName}`, (mcel) => mcel.runRouteLoader(routeInstance, entry.loaderName));
        } else if (scope === "route") {
          result = enterScmRouteAndRunLoaders();
        } else if (scope === "serialization") {
          result = runScmGate("replay:serialization", (mcel, instance) => mcel.serializeComponent(instance, {
            sourceHtml: sourceEditor.value || "",
            runtimeHtml: runtimePreview.innerHTML || ""
          }));
        } else if (scope === "repair" || entry?.strategyName) {
          result = {
            ok: false,
            code: "SCM_REPLAY_REPAIR_REQUIRES_USER_ACTION",
            message: "Repair replay is intentionally gated by the Repair runtime action because it mutates runtime-owned UI."
          };
        } else if (entry?.transitionName) {
          result = {
            ok: false,
            code: "SCM_REPLAY_TRANSITION_REQUIRES_PAYLOAD",
            message: "Transition replay requires the original payload and is not automatically re-run from the evidence panel."
          };
        } else {
          result = {
            ok: true,
            code: "SCM_REPLAY_RUNTIME_CHECKS",
            result: runScmRuntimeChecks()
          };
        }

        studioState.selectedScmEvidenceSnapshot = {
          ...entry,
          replayedAt: new Date().toISOString(),
          replayResult: result
        };
        studioState.lastScmReplayResult = jsonSafeClone({
          evidenceKey: entry?.evidenceKey || "",
          label: evidenceEntryLabel(entry),
          scope,
          ok: result?.ok !== false,
          replayedAt: studioState.selectedScmEvidenceSnapshot.replayedAt,
          result
        });
        setStatus(`SCM evidence replay ${result?.ok === false ? "blocked" : "completed"} for ${evidenceEntryLabel(entry)}.`);
        return result;
      }

      function renderScmEvidencePanel(report = studioState.lastReport) {
        if (!scmEvidencePanel) return null;
        const summary = collectScmEvidenceSummary(report);
        const gates = summary.gates || {};
        const filter = studioState.scmEvidenceFilter || "all";
        const entries = visibleScmEvidenceEntries(summary, filter);
        const selectedEntry = resolveSelectedScmEvidence(summary, filter, entries);
        studioState.selectedScmEvidenceKey = selectedEntry.evidenceKey || "";
        const selectedSnapshot = studioState.selectedScmEvidenceSnapshot?.evidenceKey === studioState.selectedScmEvidenceKey
          ? studioState.selectedScmEvidenceSnapshot
          : selectedEntry;
        const selectedDetail = formatEvidenceDetail(selectedSnapshot);

        const filterOptions = SCM_EVIDENCE_FILTERS.map((value) => `
          <option value="${value}"${filter === value ? " selected" : ""}>${value}</option>
        `).join("");

        const rows = entries.map((entry) => `
          <button type="button"
            class="code-studio-scm-evidence-entry"
            data-ok="${evidenceEntryIsViolation(entry) ? "false" : "true"}"
            data-selected="${entry.evidenceKey === studioState.selectedScmEvidenceKey ? "true" : "false"}"
            data-scm-evidence-key="${escapeHtml(entry.evidenceKey || "")}">
            <strong>${escapeHtml(evidenceEntryLabel(entry))}</strong>
            <span>${escapeHtml(entry.message || entry.path || entry.target || "SCM operation recorded.")}</span>
            <code>${escapeHtml(JSON.stringify({
              scope: evidenceEntryScope(entry),
              ok: !evidenceEntryIsViolation(entry),
              phase: entry.phase || "",
              code: entry.code || "",
              path: entry.path || "",
              reads: entry.reads || entry.declaredReads || undefined,
              writes: entry.writes || entry.declaredWrites || undefined
            }))}</code>
          </button>
        `).join("");

        scmEvidencePanel.innerHTML = `
          <div class="code-studio-scm-evidence-heading">
            <strong>SCM evidence timeline</strong>
            <div class="code-studio-scm-evidence-actions">
              <label>Filter
                <select id="code-studio-scm-evidence-filter">
                  ${filterOptions}
                </select>
              </label>
              <button type="button" id="code-studio-replay-scm-evidence">Replay selected gate</button>
              <button type="button" id="code-studio-export-scm-evidence-packet">Export SCM Evidence Packet</button>
              <button type="button" id="code-studio-download-scm-evidence-packet">Download packet</button>
              <button type="button" id="code-studio-refresh-scm-evidence">Refresh SCM evidence</button>
            </div>
          </div>
          <div class="code-studio-scm-evidence-summary">
            <span>component <code>${summary.component.total}</code></span>
            <span>route <code>${summary.route.total}</code></span>
            <span>violations <code>${summary.combined.violations}</code></span>
            <span>layout <code>${gates.layout?.ok === false ? "fail" : "ok"}</code></span>
            <span>style <code>${gates.style?.ok === false ? "fail" : "ok"}</code></span>
            <span>route <code>${gates.route?.ok === false ? "fail" : "ok"}</code></span>
          </div>
          <div class="code-studio-scm-evidence-drilldown">
            <div class="code-studio-scm-evidence-list" role="list">
              ${rows}
            </div>
            <div class="code-studio-scm-evidence-detail" id="code-studio-scm-evidence-detail">
              <strong>Selected evidence detail</strong>
              <code>${escapeHtml(JSON.stringify(selectedDetail, null, 2))}</code>
            </div>
          </div>
        `;

        scmEvidencePanel.querySelector("#code-studio-scm-evidence-filter")?.addEventListener("change", (event) => {
          studioState.scmEvidenceFilter = event.target.value || "all";
          studioState.selectedScmEvidenceKey = "";
          studioState.selectedScmEvidenceSnapshot = null;
          renderScmEvidencePanel(studioState.lastReport);
        });

        scmEvidencePanel.querySelectorAll("[data-scm-evidence-key]").forEach((button) => {
          button.addEventListener("click", () => {
            studioState.selectedScmEvidenceKey = button.dataset.scmEvidenceKey || "";
            studioState.selectedScmEvidenceSnapshot = null;
            renderScmEvidencePanel(studioState.lastReport);
          });
        });

        scmEvidencePanel.querySelector("#code-studio-replay-scm-evidence")?.addEventListener("click", () => {
          const entry = entries.find((candidate) => candidate.evidenceKey === studioState.selectedScmEvidenceKey) || selectedEntry;
          replayScmEvidenceEntry(entry);
          renderScmEvidencePanel(studioState.lastReport);
        });

        scmEvidencePanel.querySelector("#code-studio-export-scm-evidence-packet")?.addEventListener("click", () => {
          copyCurrentScmEvidenceDebugPacket();
        });

        scmEvidencePanel.querySelector("#code-studio-download-scm-evidence-packet")?.addEventListener("click", () => {
          downloadCurrentScmEvidenceDebugPacket();
        });

        scmEvidencePanel.querySelector("#code-studio-refresh-scm-evidence")?.addEventListener("click", () => {
          runScmRuntimeChecks();
          renderScmEvidencePanel(studioState.lastReport);
          setStatus("SCM evidence refreshed from component, route, effect, layout, style, serialization, and repair gates.");
        });

        return summary;
      }

      function parseSource() {
        const parser = new DOMParser();
        const doc = parser.parseFromString(sourceEditor.value || "", "text/html");
        const parseError = doc.querySelector("parsererror");
        const workspace = doc.querySelector('[data-mc-component="code-workspace"]');
        return {doc, parseError, workspace};
      }

      function workspaceFields() {
        const {workspace} = parseSource();
        if (!workspace) return {files: [], title: "", summary: ""};
        const title = workspace.querySelector('[data-mc-field="workspace-title"]')?.textContent?.trim() || "";
        const summary = workspace.querySelector('[data-mc-field="workspace-summary"]')?.textContent?.trim() || "";
        const files = [...workspace.querySelectorAll('[data-mc-component="code-file"]')].map((node, index) => ({
          index,
          path: node.getAttribute("data-mc-file-path") || `untitled-${index + 1}.txt`,
          language: node.getAttribute("data-mc-language") || "plaintext",
          field: node.getAttribute("data-mc-field") || `file-${index + 1}`,
          required: node.hasAttribute("data-mc-required"),
          value: node.textContent.replace(/^\n+|\s+$/g, ""),
        }));
        return {files, title, summary};
      }

      function selectedFile(fields = workspaceFields()) {
        return fields.files.find((file) => file.path === studioState.selectedPath) || fields.files[0] || null;
      }

      function setStatus(message) {
        if (status) status.textContent = message;
      }

      function setRuntimeLabel() {
        if (!runtimeState) return;
        const bits = [
          studioState.mounted ? "mounted" : "not mounted",
          studioState.dirty ? "dirty" : "clean",
          studioState.damaged ? "damaged" : "healthy",
        ];
        runtimeState.textContent = `runtime: ${bits.join(" / ")}`;
      }

      function syncLineGutter() {
        if (!gutter) return;
        const lineCount = Math.max(1, String(sourceEditor.value || "").split(/\r\n|\r|\n/).length);
        gutter.textContent = Array.from({length: lineCount}, (_, index) => index + 1).join("\n");
      }

      function showPane(name) {
        panes.forEach((pane) => pane.classList.toggle("active", pane.dataset.codeStudioPane === name));
        tabButtons.forEach((button) => button.classList.toggle("active", button.dataset.codeStudioTab === name));
      }

      function generatedAttrs(kind, key) {
        return `data-mc-generated="runtime" data-mc-serialize="omit" data-mc-runtime-kind="${escapeHtml(kind)}" data-mc-runtime-key="${escapeHtml(key)}"`;
      }

      function renderRuntime() {
        const fields = workspaceFields();
        const file = selectedFile(fields);
        const fileButtons = fields.files.map((entry) => `
          <button type="button" data-code-studio-runtime-file="${escapeHtml(entry.path)}" ${entry.path === (file?.path || "") ? 'aria-current="true"' : ""}>
            ${escapeHtml(entry.path)}
          </button>
        `).join("");

        runtimePreview.innerHTML = `
          <section class="code-studio-runtime-window" ${generatedAttrs("runtime-envelope", "code-studio")}>
            <header class="code-studio-runtime-header" ${generatedAttrs("runtime-header", "workbench-header")}>
              <strong>${escapeHtml(fields.title || "Untitled MCEL workspace")}</strong>
              <span>${escapeHtml(fields.summary || "Runtime generated from author source.")}</span>
            </header>
            <div class="code-studio-runtime-layout" ${generatedAttrs("runtime-layout", "workbench-layout")}>
              <aside class="code-studio-runtime-files" ${generatedAttrs("runtime-file-list", "open-files")}>
                <strong>Generated file explorer</strong>
                ${fileButtons || "<p>No files found in source.</p>"}
              </aside>
              <article class="code-studio-runtime-editor" ${generatedAttrs("runtime-editor", file?.path || "empty")}>
                <label>
                  <span>${escapeHtml(file?.path || "No source file")}</span>
                  <textarea id="code-studio-runtime-draft" spellcheck="false">${escapeHtml(file?.value || "")}</textarea>
                </label>
                <div class="code-studio-runtime-badges" ${generatedAttrs("runtime-badges", "proof-badges")}>
                  <span>generated editor chrome</span>
                  <span>runtime-only dirty state</span>
                  <span>serialize=omit</span>
                  <span>repairable from source</span>
                </div>
              </article>
            </div>
          </section>
        `;
        runtimePreview.querySelectorAll("[data-code-studio-runtime-file]").forEach((button) => {
          button.addEventListener("click", () => {
            const nextPath = button.dataset.codeStudioRuntimeFile || "";
            if (!canNavigateScmRoute(nextPath)) return;
            studioState.selectedPath = nextPath;
            const fields = workspaceFields();
            runScmTransition("openFile", {fileId: selectedScmFileId(fields)});
            enterScmRouteAndRunLoaders({forceEnter: true});
            renderRuntime();
          });
        });
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        if (draft) {
          draft.addEventListener("input", () => {
            studioState.dirty = true;
            studioState.damaged = false;
            runScmTransition("editDraft", {text: draft.value});
            setRuntimeLabel();
            setStatus("Runtime draft changed through SCM editDraft. Source is still unchanged until Commit editor draft.");
          });
        }
        studioState.mounted = true;
        studioState.damaged = false;
        setRuntimeLabel();
      }

      function validateSource() {
        const {parseError, workspace} = parseSource();
        const fields = workspaceFields();
        const file = selectedFile(fields);
        const checks = [
          {
            id: "mcel-code-editor-source-root",
            ok: Boolean(workspace) && !parseError,
            text: "Source has a code-workspace root and parses as HTML.",
          },
          {
            id: "mcel-code-editor-use-case",
            ok: workspace?.getAttribute("data-mc-use-case") === "source-safe-code-editor",
            text: "Source declares the source-safe-code-editor use case.",
          },
          {
            id: "mcel-code-editor-required-title",
            ok: Boolean(fields.title),
            text: "Workspace title is author-owned and required.",
          },
          {
            id: "mcel-code-editor-file-paths",
            ok: fields.files.length > 0 && fields.files.every((entry) => Boolean(entry.path)),
            text: "Each code file has an author-owned file path.",
          },
          {
            id: "mcel-code-editor-runtime-firewall",
            ok: !sourceEditor.value.includes('data-mc-generated="runtime"'),
            text: "Author source does not contain generated runtime chrome.",
          },
          {
            id: "mcel-code-editor-repair-base",
            ok: Boolean(file && file.value.trim()),
            text: "Selected file has enough source content to regenerate the runtime editor.",
          },
        ];
        const scmGates = runScmRuntimeChecks();
        checks.push({
          id: "mcel-code-editor-scm-runtime-gates",
          ok: scmGates.available,
          text: "Live Code Studio called SCM layout, style, and validation-effect gates and captured evidence.",
        });
        const failed = checks.filter((check) => !check.ok);
        studioState.lastReport = {
          ok: failed.length === 0,
          useCase: "source-safe-code-editor",
          selectedPath: file?.path || "",
          checks,
          failed: failed.map((check) => check.id),
          scm: scmGates,
        };
        renderContractReport(studioState.lastReport);
        setStatus(studioState.lastReport.ok ? "Validation passed: source can mount, repair, serialize, and emit SCM evidence." : `Validation blocked: ${failed.length} contract check(s) failed.`);
        return studioState.lastReport;
      }

      function renderContractReport(report = validateSource()) {
        const rows = report.checks.map((check) => `
          <div class="code-studio-contract-row ${check.ok ? "pass" : "fail"}">
            <strong>${check.ok ? "PASS" : "FAIL"} ${escapeHtml(check.id)}</strong>
            <span>${escapeHtml(check.text)}</span>
          </div>
        `).join("");
        contractReport.innerHTML = rows;
        const scmEvidenceSummary = renderScmEvidencePanel(report);
        const mcelEnvelope = typeof window.MCEL?.buildUserSpaceContract === "function"
          ? window.MCEL.buildUserSpaceContract({useCase: "source-safe-code-editor", surface: "code-editor"})
          : null;
        if (contractEnvelope) {
          contractEnvelope.textContent = JSON.stringify({
            useCase: "source-safe-code-editor",
            selectedPath: report.selectedPath,
            ok: report.ok,
            failed: report.failed,
            mcelClauses: mcelEnvelope?.clauses?.length || 0,
            scm: {
              available: Boolean(report.scm?.available),
              ok: Boolean(report.scm?.ok),
              layoutOk: Boolean(report.scm?.layout?.ok),
              styleOk: Boolean(report.scm?.style?.ok),
              validationEffectOk: Boolean(report.scm?.validation?.ok),
              routeOk: Boolean(report.scm?.route?.ok),
              routeParams: report.scm?.route?.params || {},
              routeData: report.scm?.route?.data || {},
              routeEffectLoadWorkspaceOk: Boolean(report.scm?.route?.effectLoadWorkspaceOk),
              routeEffectLoadFileOk: Boolean(report.scm?.route?.effectLoadFileOk),
              evidenceCount: report.scm?.evidenceCount || 0,
              routeEvidenceCount: report.scm?.routeEvidenceCount || 0,
              recentEvidence: report.scm?.recentEvidence || [],
              recentRouteEvidence: report.scm?.recentRouteEvidence || [],
              evidenceSummary: scmEvidenceSummary ? {
                componentTotal: scmEvidenceSummary.component.total,
                routeTotal: scmEvidenceSummary.route.total,
                violations: scmEvidenceSummary.combined.violations,
                blocking: scmEvidenceSummary.combined.blocking,
                phases: scmEvidenceSummary.combined.phases
              } : null
            },
            userPlanningModel: [
              "author source is canonical",
              "runtime editor chrome is generated",
              "dirty state is runtime-only until commit",
              "serialization strips generated nodes",
              "repair regenerates from source"
            ],
          }, null, 2);
        }
        showPane("contract");
      }

      function serializeCleanSource() {
        const {doc, workspace} = parseSource();
        if (!workspace) {
          serializedOutput.textContent = "Cannot serialize: source-safe-code-editor root is missing.";
          showPane("serialized");
          return "";
        }

        const scmGate = runScmGate("serialize", (mcel, instance) => mcel.serializeComponent(instance));
        studioState.lastSerializationGate = scmGate;
        if (!scmGate.ok) {
          serializedOutput.textContent = `SCM serialization blocked: ${scmGate.code || scmGate.message || "contract violation"}`;
          showPane("serialized");
          setStatus("SCM serialization gate blocked export. Commit or repair the runtime state first.");
          return "";
        }

        doc.querySelectorAll('[data-mc-generated="runtime"], [data-mc-serialize="omit"]').forEach((node) => node.remove());
        const clean = workspace.outerHTML.trim();
        serializedOutput.textContent = clean;
        showPane("serialized");
        setStatus("Serialized clean source. SCM serialization gate passed and runtime chrome was excluded.");
        return clean;
      }

      function damageRuntime() {
        if (!studioState.mounted) renderRuntime();
        const generated = [...runtimePreview.querySelectorAll('[data-mc-generated="runtime"]')];
        generated.slice(0, Math.max(1, Math.ceil(generated.length / 2))).forEach((node) => node.remove());
        studioState.damaged = true;
        setRuntimeLabel();
        setStatus("Runtime chrome was intentionally damaged. Author source was not changed.");
        showPane("runtime");
      }

      function repairRuntime() {
        const report = validateSource();
        if (!report.ok) {
          showPane("contract");
          return;
        }

        const scmGate = runScmGate("repair:rebuildWorkbenchShell", (mcel, instance) => mcel.repairComponent(instance, "rebuildWorkbenchShell"));
        studioState.lastRepairGate = scmGate;
        if (!scmGate.ok) {
          showPane("contract");
          setStatus(`SCM repair gate blocked runtime repair: ${scmGate.code || scmGate.message || "contract violation"}.`);
          return;
        }

        renderRuntime();
        showPane("runtime");
        setStatus("Runtime repaired from author-owned source intent through the SCM repair gate.");
      }

      function commitRuntimeDraft() {
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        if (!draft) {
          setStatus("No runtime draft is mounted.");
          return;
        }
        const {doc, workspace} = parseSource();
        const file = selectedFile();
        if (!workspace || !file) {
          setStatus("Cannot commit: source workspace or selected file is missing.");
          return;
        }

        const editGate = runScmTransition("editDraft", {text: draft.value});
        if (!editGate.ok) {
          setStatus(`SCM editDraft transition blocked commit: ${editGate.code || editGate.message || "contract violation"}.`);
          return;
        }
        const commitGate = runScmTransition("commitDraft");
        if (!commitGate.ok) {
          setStatus(`SCM commitDraft transition blocked commit: ${commitGate.code || commitGate.message || "contract violation"}.`);
          return;
        }

        const target = [...workspace.querySelectorAll('[data-mc-component="code-file"]')]
          .find((node) => node.getAttribute("data-mc-file-path") === file.path);
        if (!target) {
          setStatus("Cannot commit: selected file path is no longer in source.");
          return;
        }
        target.textContent = draft.value;
        sourceEditor.value = workspace.outerHTML.trim();
        studioState.dirty = false;
        syncLineGutter();
        syncScmInstance();
        studioState.lastSaveFileEffectGate = runScmGate("effect:saveFile", (mcel, instance) => mcel.runEffect(instance, "saveFile", {
          fileId: selectedScmFileId(),
          selectedPath: studioState.selectedPath
        }));
        enterScmRouteAndRunLoaders({forceEnter: true});
        renderRuntime();
        setStatus("Runtime draft committed into author-owned source through SCM editDraft/commitDraft transitions and saveFile effect.");
      }

      tabButtons.forEach((button) => {
        button.addEventListener("click", () => {
          const panel = button.dataset.codeStudioTab || "source";
          runScmTransition("selectPanel", {panel});
          showPane(panel);
        });
      });
      root.querySelectorAll("[data-code-studio-panel]").forEach((button) => {
        button.addEventListener("click", () => {
          root.querySelectorAll("[data-code-studio-panel]").forEach((entry) => entry.classList.remove("active"));
          button.classList.add("active");
          const panel = button.dataset.codeStudioPanel;
          if (panel === "runtime") {
            runScmTransition("selectPanel", {panel: "runtime"});
            renderRuntime();
          }
          if (panel === "contract") {
            runScmTransition("selectPanel", {panel: "contract"});
            validateSource();
          }
          if (panel === "source" || panel === "explorer") {
            runScmTransition("selectPanel", {panel: "source"});
            showPane("source");
          }
          if (panel === "assistant") {
            const dock = root.querySelector("#code-studio-bottom-panel");
            const dockToggle = root.querySelector("#code-studio-toggle-assistant");
            if (dock) dock.dataset.expanded = "true";
            if (dockToggle) {
              dockToggle.setAttribute("aria-expanded", "true");
              dockToggle.textContent = "Close assistant dock";
            }
            ensureCodeStudioScmSurfaceStyles();
          }
        });
      });
      root.querySelectorAll("[data-code-studio-file]").forEach((button) => {
        button.addEventListener("click", () => {
          const nextPath = button.dataset.codeStudioFile || studioState.selectedPath;
          if (!canNavigateScmRoute(nextPath)) return;
          studioState.selectedPath = nextPath;
          root.querySelectorAll("[data-code-studio-file]").forEach((entry) => entry.classList.toggle("active", entry === button));
          const fields = workspaceFields();
          runScmTransition("openFile", {fileId: selectedScmFileId(fields)});
          enterScmRouteAndRunLoaders({forceEnter: true});
          renderRuntime();
          showPane("runtime");
        });
      });


      const assistantDock = root.querySelector("#code-studio-bottom-panel");
      const assistantToggle = root.querySelector("#code-studio-toggle-assistant");
      assistantToggle?.addEventListener("click", () => {
        const expanded = assistantDock?.dataset.expanded === "true";
        runScmTransition("toggleBottomDock");
        if (assistantDock) assistantDock.dataset.expanded = expanded ? "false" : "true";
        assistantToggle.setAttribute("aria-expanded", expanded ? "false" : "true");
        assistantToggle.textContent = expanded ? "Open assistant dock" : "Close assistant dock";
        ensureCodeStudioScmSurfaceStyles();
      });

      sourceEditor.addEventListener("input", () => {
        syncLineGutter();
        studioState.mounted = false;
        studioState.damaged = false;
        syncScmInstance();
        scmRouteKey = "";
        setRuntimeLabel();
        setStatus("Source changed. Remount or validate to refresh the MCEL runtime, route loaders, and SCM evidence.");
      });
      sourceEditor.addEventListener("scroll", () => {
        if (gutter) gutter.scrollTop = sourceEditor.scrollTop;
      });

      validateButton?.addEventListener("click", validateSource);
      refreshScmEvidenceButton?.addEventListener("click", () => {
        runScmRuntimeChecks();
        renderScmEvidencePanel(studioState.lastReport);
        showPane("contract");
        setStatus("SCM evidence refreshed from component, route, effect, layout, style, serialization, and repair gates.");
      });
      serializeButton?.addEventListener("click", serializeCleanSource);
      mountButton?.addEventListener("click", () => { renderRuntime(); showPane("runtime"); setStatus("Runtime mounted from author source."); });
      damageButton?.addEventListener("click", damageRuntime);
      repairButton?.addEventListener("click", repairRuntime);
      commitButton?.addEventListener("click", commitRuntimeDraft);

      window.MainComputerCodeStudio = {
        getState() {
          return {...studioState, sourceLength: sourceEditor.value.length};
        },
        validate: validateSource,
        mountRuntime: renderRuntime,
        damageRuntime,
        repairRuntime,
        serialize: serializeCleanSource,
        commitRuntimeDraft,
        syncScmInstance,
        checkScmContracts: runScmRuntimeChecks,
        syncScmRouteInstance,
        enterScmRouteAndRunLoaders,
        requestScmRouteLeave,
        exportScmEvidence,
        exportScmRouteEvidence,
        collectScmEvidenceSummary,
        buildScmEvidenceDebugPacket,
        exportScmEvidenceDebugPacket,
        copyCurrentScmEvidenceDebugPacket,
        downloadCurrentScmEvidenceDebugPacket,
        renderScmEvidencePanel,
        ensureCodeStudioScmSurfaceStyles,
        getScmInstance() {
          return syncScmInstance();
        },
        getScmRouteInstance() {
          return syncScmRouteInstance({enter: false});
        },
      };

      ensureCodeStudioScmSurfaceStyles();
      syncLineGutter();
      validateSource();
      renderRuntime();
      serializeCleanSource();
      showPane("source");
      setRuntimeLabel();
    })();

    (() => {
      const root = document.querySelector("#code-editor-app");
      if (!root) return;
      const toggle = root.querySelector("#code-editor-gridstack-toggle");
      const reset = root.querySelector("#code-editor-gridstack-reset");
      const status = root.querySelector("#code-editor-gridstack-status");
      const layoutKey = "main-computer-code-editor-gridstack-layout-v1";
      const enabledKey = "main-computer-code-editor-gridstack-enabled-v1";
      let grid = null;

      function setGridStatus(message) {
        if (status) status.textContent = message;
      }

      function saveCodeEditorGridStackLayout() {
        if (!grid) return;
        try {
          localStorage.setItem(layoutKey, JSON.stringify(grid.save(false)));
        } catch {
          setGridStatus("GridStack layout could not be saved.");
        }
      }

      function disableCodeEditorGridStackTest() {
        try {
          if (grid) grid.destroy(false);
        } catch {}
        grid = null;
        root.dataset.gridstackEnabled = "false";
        try { localStorage.setItem(enabledKey, "false"); } catch {}
        setGridStatus("Layout locked");
      }

      function enableCodeEditorGridStackTest() {
        if (!window.GridStack) {
          setGridStatus("GridStack library unavailable");
          return null;
        }
        const container = root.querySelector(".code-studio-body");
        if (!container) {
          setGridStatus("GridStack container unavailable");
          return null;
        }
        try {
          grid = GridStack.init({
            cellHeight: 80,
            float: true,
            margin: 4,
            resizable: {handles: "e, se, s, sw, w"},
          }, container);
          root.dataset.gridstackEnabled = "true";
          localStorage.setItem(enabledKey, "true");
          setGridStatus("Layout unlocked");
          grid.on("change", saveCodeEditorGridStackLayout);
          return grid;
        } catch {
          setGridStatus("GridStack could not attach to this shell.");
          return null;
        }
      }

      toggle?.addEventListener("click", () => {
        if (grid) {
          disableCodeEditorGridStackTest();
        } else {
          enableCodeEditorGridStackTest();
        }
      });
      reset?.addEventListener("click", () => {
        try { localStorage.removeItem(layoutKey); } catch {}
        if (grid) {
          disableCodeEditorGridStackTest();
          enableCodeEditorGridStackTest();
        }
        setGridStatus("Layout reset");
      });

      window.enableCodeEditorGridStackTest = enableCodeEditorGridStackTest;
      window.disableCodeEditorGridStackTest = disableCodeEditorGridStackTest;
      window.saveCodeEditorGridStackLayout = saveCodeEditorGridStackLayout;
    })();
