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
      const saveLiveWorkspaceButton = root.querySelector("#code-studio-save-live-workspace");
      const restoreLiveWorkspaceButton = root.querySelector("#code-studio-restore-live-workspace");
      const clearLiveWorkspaceButton = root.querySelector("#code-studio-clear-live-workspace");
      const liveWorkspacePersistenceStatus = root.querySelector("#code-studio-live-workspace-persistence");
      const proofDock = root.querySelector("#code-studio-bottom-panel");
      const proofDockToggle = root.querySelector("#code-studio-toggle-assistant");
      const proofDockDetailPanel = root.querySelector("#code-studio-proof-detail-panel");
      const status = root.querySelector("#code-studio-status");
      const flagshipInspector = root.querySelector("#code-studio-flagship-inspector");
      const topRouteStatus = root.querySelector("#code-studio-top-route-status");
      const topGateStatus = root.querySelector("#code-studio-top-gate-status");
      const topPersistenceStatus = root.querySelector("#code-studio-top-persistence-status");
      const topRuntimeVersion = root.querySelector("#code-studio-top-runtime-version");
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
      const SCM_AI_REPAIR_PROMPT_VERSION = "1.0.0";
      const SCM_REPLAY_SNAPSHOT_VERSION = "1.0.0";
      const SCM_CONTRACT_AUTHORING_HELPER_VERSION = "1.0.0";
      const LIVE_WORKSPACE_PERSISTENCE_VERSION = "1.0.0";
      const LIVE_WORKSPACE_PERSISTENCE_KEY = "main-computer-code-studio-live-workspace-v1";
      const MCEL_RUNTIME_PACKAGE_VERSION = "mcel-runtime.v0.1.15";
      const SCM_RECEIPT_VECTOR_VERSION = "1.0.0";
      const SCM_LAB_RECEIPT_KIND = "mcel-lab-medium-scm-proven-dev-network-app-receipt";
      const SCM_LAB_RECEIPT_PROOF_KIND = "mcel-code-studio-normalized-lab-receipt-vector";
      const SCM_LAB_RECEIPT_EFFECT_SURFACE = {
        "wallet.connect": {
          label: "wallet.connect",
          category: "wallet-action",
          declaredReads: ["source.devRelease.devNetwork", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletAdapter"],
          declaredWrites: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletAdapter", "runtime.externalOutcome", "runtime.evidenceStrip"],
          passNextAction: "draft tx",
          blockedNextAction: "retry connect",
          exceptionNextAction: "inspect exception"
        },
        "wallet.provider.accountsChanged": {
          label: "wallet.provider.accountsChanged",
          category: "provider-event",
          declaredReads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents"],
          declaredWrites: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.evidenceStrip"],
          passNextAction: "inspect account update",
          blockedNextAction: "retry connect",
          exceptionNextAction: "inspect provider exception"
        },
        "wallet.provider.chainChanged": {
          label: "wallet.provider.chainChanged",
          category: "provider-event",
          declaredReads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents"],
          declaredWrites: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.evidenceStrip"],
          passNextAction: "inspect chain gate",
          blockedNextAction: "switch network",
          exceptionNextAction: "inspect provider exception"
        },
        "wallet.provider.disconnect": {
          label: "wallet.provider.disconnect",
          category: "provider-event",
          declaredReads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents"],
          declaredWrites: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.evidenceStrip"],
          passNextAction: "retry connect",
          blockedNextAction: "retry connect",
          exceptionNextAction: "inspect provider exception"
        },
        "wallet.provider.error": {
          label: "wallet.provider.error",
          category: "provider-event",
          declaredReads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents"],
          declaredWrites: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.walletEvents", "runtime.evidenceStrip"],
          passNextAction: "inspect exception",
          blockedNextAction: "inspect exception",
          exceptionNextAction: "inspect exception"
        },
        "release.draftTx": {
          label: "release.draftTx",
          category: "tx-draft",
          declaredReads: ["source.devRelease.contractAddress", "source.devRelease.requests", "state.selectedRequestId", "runtime.wallet", "runtime.network", "runtime.externalOutcome"],
          declaredWrites: ["runtime.txDraft", "runtime.evidenceStrip"],
          passNextAction: "inspect tx draft",
          blockedNextAction: "retry connect",
          exceptionNextAction: "inspect exception"
        },
        "ai.repairWalletHint": {
          label: "ai.repairWalletHint",
          category: "repair-packet",
          declaredReads: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome", "runtime.proofChip"],
          declaredWrites: ["runtime.proofChip", "runtime.repairPacket", "runtime.assistantRepairPrompt", "runtime.evidenceStrip"],
          forbiddenWrites: ["source.devRelease", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"],
          passNextAction: "inspect bounded repair packet",
          blockedNextAction: "inspect bounded repair packet",
          exceptionNextAction: "inspect bounded repair packet"
        }
      };

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
        lastScmRepairPrompt: "",
        lastScmRepairPromptExport: null,
        lastScmReplaySnapshotComparison: null,
        lastLiveWorkspacePersistence: null,
        lastRouteLoaderPersistenceGate: null,
        lastScmContractAuthoringHelper: null,
        lastScmContractAuthoringHelperText: "",
        lastScmContractAuthoringExport: null,
        lastScmReceiptVector: null,
        persistenceHydrated: false,
        lastSerializationGate: null,
        lastRepairGate: null,
        lastSaveFileEffectGate: null,
        activeScmInspectorTab: "contract",
      };

      let scmInstance = null;
      let scmRouteInstance = null;
      let scmRouteKey = "";

      function prepareFlagshipWorkbenchRegions() {
        root.dataset.workbenchSplit = "flagship-region-split";
        const regionSelectors = {
          shell: ".code-studio-shell",
          body: ".code-studio-body",
          rail: ".code-studio-activitybar",
          sidebar: ".code-studio-sidebar",
          editor: ".code-studio-editor-group",
          inspector: ".code-studio-inspector",
          dock: "#code-studio-bottom-panel",
          statusbar: ".code-studio-statusbar"
        };
        const shell = root.querySelector(regionSelectors.shell);
        const body = root.querySelector(regionSelectors.body);
        const rail = root.querySelector(regionSelectors.rail);
        const sidebar = root.querySelector(regionSelectors.sidebar);
        const editor = root.querySelector(regionSelectors.editor);
        const inspector = root.querySelector(regionSelectors.inspector);
        const dock = root.querySelector(regionSelectors.dock);
        const statusbar = root.querySelector(regionSelectors.statusbar);

        if (body) body.dataset.codeStudioWorkbenchRegion = "main-grid";
        if (rail) rail.dataset.codeStudioWorkbenchRegion = "mode-rail";
        if (sidebar) sidebar.dataset.codeStudioWorkbenchRegion = "workspace-sidebar";
        if (editor) editor.dataset.codeStudioWorkbenchRegion = "editor-workbench";
        if (inspector) inspector.dataset.codeStudioWorkbenchRegion = "scm-ai-inspector";
        if (dock) {
          dock.dataset.codeStudioWorkbenchRegion = "proof-dock";
          if (dock.dataset.expanded !== "true") dock.dataset.expanded = "false";
        }
        if (statusbar) statusbar.dataset.codeStudioWorkbenchRegion = "statusbar";
        if (shell && body && body.parentElement !== shell) shell.append(body);
        if (shell && dock && dock.parentElement !== shell) shell.append(dock);
        if (shell && statusbar && statusbar.parentElement !== shell) shell.append(statusbar);
        return {shell, body, rail, sidebar, editor, inspector, dock, statusbar};
      }


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

      function liveWorkspaceStorage() {
        try {
          return window.localStorage || null;
        } catch {
          return null;
        }
      }

      function summarizeScmGateForPersistence(gate) {
        if (!gate) return null;
        return {
          label: gate.label || "",
          ok: gate.ok !== false,
          skipped: Boolean(gate.skipped),
          code: gate.code || gate.violation?.code || "",
          message: gate.message || gate.violation?.message || "",
          resultKind: gate.result?.kind || gate.kind || ""
        };
      }

      function summarizeRouteLoaderPersistenceGate(loaderGate) {
        if (!loaderGate) return null;
        return {
          label: loaderGate.label || "route-and-loaders",
          ok: loaderGate.ok !== false,
          route: summarizeScmGateForPersistence(loaderGate.route),
          effects: {
            loadWorkspace: summarizeScmGateForPersistence(loaderGate.effects?.loadWorkspace),
            loadFile: summarizeScmGateForPersistence(loaderGate.effects?.loadFile)
          }
        };
      }

      function collectLiveWorkspacePersistenceSummary() {
        const summary = studioState.lastLiveWorkspacePersistence || {
          kind: "mcel-code-studio-live-workspace-persistence-summary",
          persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
          storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
          status: "not-loaded",
          ok: true,
          savedAt: "",
          restoredAt: "",
          clearedAt: "",
          selectedPath: studioState.selectedPath,
          sourceLength: sourceEditor.value.length,
          fileCount: workspaceFields().files.length,
          route: {
            params: routeParamsForScm() || {},
            query: routeQueryForScm()
          },
          saveFileEffect: summarizeScmGateForPersistence(studioState.lastSaveFileEffectGate),
          routeLoaderSync: summarizeRouteLoaderPersistenceGate(studioState.lastRouteLoaderPersistenceGate)
        };
        return jsonSafeClone(summary);
      }

      function renderLiveWorkspacePersistenceStatus(summary = collectLiveWorkspacePersistenceSummary()) {
        if (!liveWorkspacePersistenceStatus) return summary;
        const statusText = summary.status || "unknown";
        const path = summary.selectedPath || studioState.selectedPath || "";
        const savedAt = summary.savedAt ? ` saved ${summary.savedAt}` : "";
        liveWorkspacePersistenceStatus.textContent = `live workspace persistence: ${statusText}${path ? ` · ${path}` : ""}${savedAt}`;
        liveWorkspacePersistenceStatus.dataset.status = statusText;
        liveWorkspacePersistenceStatus.dataset.ok = summary.ok === false ? "false" : "true";
        return summary;
      }

      function buildLiveWorkspacePersistenceRecord(reason = "manual-save") {
        const fields = workspaceFields();
        const selected = selectedFile(fields);
        const routeParams = routeParamsForScm(fields) || {};
        const routeQuery = routeQueryForScm();
        return jsonSafeClone({
          kind: "mcel-code-studio-live-workspace-persistence-record",
          persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
          savedAt: new Date().toISOString(),
          reason,
          source: sourceEditor.value,
          selectedPath: studioState.selectedPath,
          selectedFile: selected ? {
            path: selected.path,
            language: selected.language,
            required: selected.required,
            field: selected.field,
            length: selected.value.length
          } : null,
          fileCount: fields.files.length,
          sourceLength: sourceEditor.value.length,
          route: {
            name: window.McelCodeStudioScm?.routeName || "workspace.file",
            params: routeParams,
            query: routeQuery,
            key: currentScmRouteKey(routeParams, routeQuery)
          },
          dirtyState: collectDirtyStateSummary(fields)
        });
      }

      function persistLiveWorkspaceFromSource(reason = "manual-save", options = {}) {
        const storage = liveWorkspaceStorage();
        if (!storage) {
          const summary = {
            kind: "mcel-code-studio-live-workspace-persistence-summary",
            persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
            storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
            status: "unavailable",
            ok: false,
            message: "localStorage is unavailable.",
            selectedPath: studioState.selectedPath,
            sourceLength: sourceEditor.value.length,
            fileCount: workspaceFields().files.length
          };
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          setStatus("Live workspace persistence is unavailable in this browser context.");
          return summary;
        }

        const {parseError, workspace} = parseSource();
        if (!workspace || parseError) {
          const summary = {
            kind: "mcel-code-studio-live-workspace-persistence-summary",
            persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
            storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
            status: "blocked",
            ok: false,
            message: "Source workspace must parse before it can be persisted.",
            selectedPath: studioState.selectedPath,
            sourceLength: sourceEditor.value.length,
            fileCount: workspaceFields().files.length
          };
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          setStatus("Live workspace persistence blocked: source workspace does not parse.");
          return summary;
        }

        syncScmInstance();
        const saveGate = options.saveGate || runScmGate("effect:saveFile", (mcel, instance) => mcel.runEffect(instance, "saveFile", {
          fileId: selectedScmFileId(),
          selectedPath: studioState.selectedPath,
          reason
        }));
        studioState.lastSaveFileEffectGate = saveGate;

        const loaderGate = options.loaderGate || enterScmRouteAndRunLoaders({forceEnter: true});
        studioState.lastRouteLoaderPersistenceGate = loaderGate;

        const record = buildLiveWorkspacePersistenceRecord(reason);
        const ok = saveGate.ok !== false && loaderGate.ok !== false;
        const summary = {
          kind: "mcel-code-studio-live-workspace-persistence-summary",
          persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
          storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
          status: ok ? "saved" : "blocked",
          ok,
          savedAt: ok ? record.savedAt : "",
          reason,
          selectedPath: record.selectedPath,
          selectedFile: record.selectedFile,
          sourceLength: record.sourceLength,
          fileCount: record.fileCount,
          route: record.route,
          dirtyState: record.dirtyState,
          saveFileEffect: summarizeScmGateForPersistence(saveGate),
          routeLoaderSync: summarizeRouteLoaderPersistenceGate(loaderGate)
        };

        if (ok) {
          try {
            storage.setItem(LIVE_WORKSPACE_PERSISTENCE_KEY, JSON.stringify(record));
          } catch (error) {
            summary.status = "blocked";
            summary.ok = false;
            summary.message = error?.message || "localStorage write failed.";
          }
        }

        studioState.lastLiveWorkspacePersistence = jsonSafeClone(summary);
        renderLiveWorkspacePersistenceStatus(studioState.lastLiveWorkspacePersistence);
        setStatus(summary.ok ? "Live workspace persisted through SCM saveFile effect and route loaders." : `Live workspace persistence blocked: ${summary.message || saveGate.code || loaderGate.route?.code || "SCM gate failed"}.`);
        return studioState.lastLiveWorkspacePersistence;
      }

      function hydratePersistedLiveWorkspace(options = {}) {
        const storage = liveWorkspaceStorage();
        if (!storage) {
          const summary = {
            kind: "mcel-code-studio-live-workspace-persistence-summary",
            persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
            storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
            status: "unavailable",
            ok: false,
            message: "localStorage is unavailable.",
            selectedPath: studioState.selectedPath,
            sourceLength: sourceEditor.value.length,
            fileCount: workspaceFields().files.length
          };
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          return summary;
        }

        const raw = storage.getItem(LIVE_WORKSPACE_PERSISTENCE_KEY);
        if (!raw) {
          const summary = collectLiveWorkspacePersistenceSummary();
          summary.status = "not-saved";
          summary.ok = true;
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          return summary;
        }

        let record = null;
        try {
          record = JSON.parse(raw);
        } catch {
          const summary = {
            kind: "mcel-code-studio-live-workspace-persistence-summary",
            persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
            storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
            status: "blocked",
            ok: false,
            message: "Persisted workspace record is not valid JSON.",
            selectedPath: studioState.selectedPath,
            sourceLength: sourceEditor.value.length,
            fileCount: workspaceFields().files.length
          };
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          return summary;
        }

        if (record?.kind !== "mcel-code-studio-live-workspace-persistence-record" || typeof record.source !== "string") {
          const summary = {
            kind: "mcel-code-studio-live-workspace-persistence-summary",
            persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
            storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
            status: "blocked",
            ok: false,
            message: "Persisted workspace record shape is not recognized.",
            selectedPath: studioState.selectedPath,
            sourceLength: sourceEditor.value.length,
            fileCount: workspaceFields().files.length
          };
          studioState.lastLiveWorkspacePersistence = summary;
          renderLiveWorkspacePersistenceStatus(summary);
          return summary;
        }

        sourceEditor.value = record.source;
        studioState.selectedPath = record.selectedPath || studioState.selectedPath;
        studioState.dirty = false;
        studioState.damaged = false;
        studioState.mounted = false;
        studioState.persistenceHydrated = true;
        syncLineGutter();
        syncScmInstance();
        const loaderGate = enterScmRouteAndRunLoaders({forceEnter: true});
        studioState.lastRouteLoaderPersistenceGate = loaderGate;

        const fields = workspaceFields();
        const summary = {
          kind: "mcel-code-studio-live-workspace-persistence-summary",
          persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
          storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
          status: options.manual ? "restored" : "hydrated",
          ok: loaderGate.ok !== false,
          savedAt: record.savedAt || "",
          restoredAt: new Date().toISOString(),
          reason: record.reason || "",
          selectedPath: studioState.selectedPath,
          selectedFile: selectedFile(fields) ? {
            path: selectedFile(fields).path,
            language: selectedFile(fields).language,
            required: selectedFile(fields).required,
            field: selectedFile(fields).field,
            length: selectedFile(fields).value.length
          } : null,
          sourceLength: sourceEditor.value.length,
          fileCount: fields.files.length,
          route: record.route || {
            params: routeParamsForScm(fields) || {},
            query: routeQueryForScm()
          },
          dirtyState: collectDirtyStateSummary(fields),
          routeLoaderSync: summarizeRouteLoaderPersistenceGate(loaderGate)
        };
        studioState.lastLiveWorkspacePersistence = jsonSafeClone(summary);
        renderLiveWorkspacePersistenceStatus(studioState.lastLiveWorkspacePersistence);
        if (options.manual) {
          renderRuntime();
          validateSource();
          showPane("source");
          setStatus("Persisted live workspace restored and route/effect loaders refreshed.");
        }
        return studioState.lastLiveWorkspacePersistence;
      }

      function clearPersistedLiveWorkspace() {
        const storage = liveWorkspaceStorage();
        if (storage) {
          try {
            storage.removeItem(LIVE_WORKSPACE_PERSISTENCE_KEY);
          } catch {}
        }
        const summary = {
          kind: "mcel-code-studio-live-workspace-persistence-summary",
          persistenceVersion: LIVE_WORKSPACE_PERSISTENCE_VERSION,
          storageKey: LIVE_WORKSPACE_PERSISTENCE_KEY,
          status: "cleared",
          ok: true,
          clearedAt: new Date().toISOString(),
          selectedPath: studioState.selectedPath,
          sourceLength: sourceEditor.value.length,
          fileCount: workspaceFields().files.length,
          route: {
            params: routeParamsForScm() || {},
            query: routeQueryForScm()
          }
        };
        studioState.lastLiveWorkspacePersistence = summary;
        renderLiveWorkspacePersistenceStatus(summary);
        setStatus("Persisted live workspace cleared. Current author source remains loaded.");
        return summary;
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

      function parseScmReceiptJsonCandidate(value) {
        if (typeof value !== "string") return value || null;
        const text = value.trim();
        if (!text) return null;
        try {
          return JSON.parse(text);
        } catch (_error) {
          const firstBrace = text.indexOf("{");
          const lastBrace = text.lastIndexOf("}");
          if (firstBrace >= 0 && lastBrace > firstBrace) {
            try {
              return JSON.parse(text.slice(firstBrace, lastBrace + 1));
            } catch (_nestedError) {
              return null;
            }
          }
          return null;
        }
      }

      function uniqueScmReceiptList(...values) {
        const seen = new Set();
        const items = [];
        values.forEach((value) => {
          normalizeScmSurfaceList(value).forEach((item) => {
            if (!seen.has(item)) {
              seen.add(item);
              items.push(item);
            }
          });
        });
        return items;
      }

      function normalizeReceiptOutcome(value, fallback = "waiting") {
        const text = String(value || fallback || "waiting").trim().toLowerCase();
        if (["pass", "blocked", "exception", "fail", "waiting"].includes(text)) return text;
        if (text === "ok" || text === "success") return "pass";
        if (text === "rejected" || text === "denied" || text === "cancelled" || text === "canceled") return "blocked";
        return fallback;
      }

      function normalizeReceiptExternalOutcome(value = {}) {
        const outcome = value && typeof value === "object" ? value : {};
        const status = normalizeReceiptOutcome(outcome.status || outcome.actionOutcome || outcome.outcome, "waiting");
        return jsonSafeClone({
          kind: outcome.kind || (status === "waiting" ? "" : "mcel-external-outcome"),
          status,
          reason: outcome.reason || outcome.code || "",
          message: outcome.message || "",
          provider: outcome.provider || outcome.providerKind || "",
          method: outcome.method || outcome.rpcMethod || "",
          account: outcome.account || outcome.selectedAddress || "",
          chainId: outcome.chainId || "",
          nextAction: outcome.nextAction || "",
          sequence: outcome.sequence ?? null,
          raw: outcome
        });
      }

      function inferLabReceiptEffect(proof = {}, componentEvidence = [], selectedEvidence = null) {
        const selectedName = selectedEvidence?.effectName || selectedEvidence?.transitionName || proof.selectedEffect || "";
        if (selectedName) return selectedName;
        const effectEntries = [...(componentEvidence || [])]
          .reverse()
          .filter((entry) => entry?.effectName || entry?.transitionName);
        const latestEffect = effectEntries[0]?.effectName || effectEntries[0]?.transitionName || "";
        if (latestEffect) return latestEffect;
        if ((proof.repairPacketCount || 0) > 0 || proof.checks?.repairPacketGenerated) return "ai.repairWalletHint";
        if ((proof.txDraftCount || 0) > 0 || proof.checks?.txDraftRuntimeOnly) return "release.draftTx";
        if ((proof.providerAccountsChangedCount || 0) > 0) return "wallet.provider.accountsChanged";
        if ((proof.providerChainChangedCount || 0) > 0) return "wallet.provider.chainChanged";
        if ((proof.providerDisconnectCount || 0) > 0) return "wallet.provider.disconnect";
        if ((proof.providerErrorCount || 0) > 0) return "wallet.provider.error";
        if ((proof.walletConnectCount || 0) > 0 || proof.externalOutcome?.kind === "mcel-external-outcome") return "wallet.connect";
        return "";
      }

      function labReceiptEffectDeclaration(effectName = "", componentEvidence = [], selectedEvidence = null) {
        const catalog = SCM_LAB_RECEIPT_EFFECT_SURFACE[effectName] || null;
        const evidence = selectedEvidence?.effectName === effectName
          ? selectedEvidence
          : [...(componentEvidence || [])].reverse().find((entry) => entry?.effectName === effectName) || null;
        return {
          label: effectName || catalog?.label || "",
          category: catalog?.category || evidenceEntryScope(evidence || {}, "effect"),
          declaredReads: uniqueScmReceiptList(evidence?.reads, evidence?.declaredReads, catalog?.declaredReads),
          declaredWrites: uniqueScmReceiptList(evidence?.writes, evidence?.declaredWrites, catalog?.declaredWrites),
          forbiddenWrites: uniqueScmReceiptList(evidence?.forbiddenWrites, catalog?.forbiddenWrites),
          passNextAction: catalog?.passNextAction || "",
          blockedNextAction: catalog?.blockedNextAction || "",
          exceptionNextAction: catalog?.exceptionNextAction || ""
        };
      }

      function inferLabReceiptNextAction(effectName, actionOutcome, externalOutcome = {}, declaration = {}) {
        if (externalOutcome.nextAction) return externalOutcome.nextAction;
        if (actionOutcome === "exception") return declaration.exceptionNextAction || "inspect exception";
        if (actionOutcome === "blocked") return declaration.blockedNextAction || "retry blocked action";
        if (effectName === "wallet.connect") return declaration.passNextAction || "draft tx";
        if (effectName === "release.draftTx") return declaration.passNextAction || "inspect tx draft";
        if (effectName === "ai.repairWalletHint") return declaration.passNextAction || "inspect bounded repair packet";
        return declaration.passNextAction || "inspect receipt";
      }

      function normalizeLabReceiptRuntimeConsequences(effectName, proof = {}, actionOutcome = "waiting") {
        const consequences = [];
        const checks = proof.checks || {};
        if (effectName === "wallet.connect") {
          consequences.push("runtime.wallet updated");
          consequences.push("runtime.network updated");
          consequences.push(actionOutcome === "pass" ? "runtime.txDraft preserved until draft effect" : "runtime.txDraft cleared");
        } else if (effectName === "wallet.provider.accountsChanged") {
          consequences.push((proof.providerAccountDisconnectCount || 0) > 0 ? "disconnected wallet" : "committed account update");
          consequences.push("runtime.walletEvents updated");
          consequences.push("runtime.txDraft cleared");
        } else if (effectName === "wallet.provider.chainChanged") {
          consequences.push("runtime.network updated");
          consequences.push("runtime.walletEvents updated");
          consequences.push("runtime.txDraft cleared");
        } else if (effectName === "release.draftTx") {
          consequences.push(checks.txDraftRuntimeOnly ? "runtime.txDraft updated" : "runtime.txDraft not proven");
          consequences.push("source unchanged");
          consequences.push("no transaction send attempted");
        } else if (effectName === "ai.repairWalletHint") {
          consequences.push("runtime.repairPacket updated");
          consequences.push("runtime.assistantRepairPrompt updated");
          consequences.push("source unchanged");
          consequences.push("live AI call false");
        }
        if (checks.sourceSafeAfterExternalOutcome) consequences.push("source unchanged after external outcome");
        return [...new Set(consequences)];
      }

      function normalizeLabReceiptTxDraftBoundary(proof = {}, repairPacket = {}) {
        const evidence = repairPacket?.evidence || {};
        const boundary = proof.txDraftBoundary || evidence.txDraftBoundary || proof.runtimeTxDraft?.boundary || "";
        const nonce = proof.txDraftBoundary?.nonce || proof.runtimeTxDraft?.nonce || {};
        const gasEstimate = proof.txDraftBoundary?.gasEstimate || proof.runtimeTxDraft?.gasEstimate || {};
        const ethCall = proof.txDraftBoundary?.ethCall || proof.runtimeTxDraft?.ethCall || {};
        const noSend = proof.runtimeTxDraft?.noSend === true || boundary === "runtime-only-no-send" || proof.checks?.txDraftRuntimeOnly === true;
        return jsonSafeClone({
          status: proof.runtimeTxDraft?.status || (proof.checks?.txDraftRuntimeOnly ? "observed" : "not-observed"),
          boundary: boundary || (noSend ? "runtime-only-no-send" : ""),
          noSend,
          probeStatus: {
            nonce: nonce.status || proof.nonceStatus || "",
            gasEstimate: gasEstimate.status || proof.gasStatus || "",
            ethCall: ethCall.status || proof.ethCallStatus || ""
          },
          raw: proof.txDraftBoundary || proof.runtimeTxDraft || evidence || null
        });
      }

      function normalizeLabReceiptRepairPacket(proof = {}, declaration = {}) {
        const packet = proof.repairPacket || {};
        const generated = packet.kind === "mcel-repair-packet"
          || packet.status === "ready"
          || (proof.repairPacketCount || 0) > 0
          || proof.checks?.repairPacketGenerated === true;
        const liveAiCall = packet.liveAiCall === false || proof.checks?.repairPacketNoLiveAiCall === true
          ? false
          : (packet.liveAiCall === true ? true : null);
        return jsonSafeClone({
          status: generated ? "generated" : "not required",
          generated,
          liveAiCall,
          allowedWrites: uniqueScmReceiptList(declaration.declaredWrites, packet.allowedWrites),
          forbiddenWrites: uniqueScmReceiptList(packet.forbiddenWrites, declaration.forbiddenWrites),
          boundaryBlocked: proof.checks?.repairBoundaryBlocked === true || (proof.repairBoundaryBlockedCount || 0) > 0,
          packet
        });
      }

      function normalizeLabReceiptLayoutObservation(proof = {}) {
        const observation = proof.layoutObservation || {};
        return jsonSafeClone({
          kind: observation.kind || "",
          source: observation.source || "",
          measured: observation.measured === true,
          regions: observation.regions || {},
          metrics: observation.metrics || {},
          documentHeightRatio: observation.documentHeightRatio ?? null,
          violations: proof.layoutViolations || [],
          styleViolations: proof.styleViolations || []
        });
      }

      function findMcelLabReceiptPayload() {
        const node = document.querySelector("#mcel-tiny-contract-evidence");
        if (!node) return null;
        return parseScmReceiptJsonCandidate(node.textContent || "");
      }

      function normalizeMcelLabReceiptVector(input, options = {}) {
        const envelope = parseScmReceiptJsonCandidate(input);
        if (!envelope || typeof envelope !== "object") return null;
        const proof = envelope.proof && typeof envelope.proof === "object" ? envelope.proof : envelope;
        const looksLikeLabReceipt = envelope.kind === SCM_LAB_RECEIPT_KIND
          || proof.component === "DevNetworkReleaseConsole"
          || proof.externalOutcome?.kind === "mcel-external-outcome"
          || Boolean(proof.actionOutcome || proof.governanceOutcome || proof.safetyOutcome || proof.proofCompleteness);
        if (!looksLikeLabReceipt) return null;

        const componentEvidence = Array.isArray(envelope.componentEvidence) ? envelope.componentEvidence : [];
        const selectedEvidence = options.selectedEvidence || null;
        const selectedEffect = inferLabReceiptEffect(proof, componentEvidence, selectedEvidence);
        const declaration = labReceiptEffectDeclaration(selectedEffect, componentEvidence, selectedEvidence);
        const externalOutcome = normalizeReceiptExternalOutcome(proof.externalOutcome || envelope.externalOutcome || {});
        const actionOutcome = normalizeReceiptOutcome(proof.actionOutcome || externalOutcome.status || proof.status, "waiting");
        const governanceOutcome = normalizeReceiptOutcome(proof.governanceOutcome, "waiting");
        const safetyOutcome = normalizeReceiptOutcome(proof.safetyOutcome, "waiting");
        const repairPacket = normalizeLabReceiptRepairPacket(proof, declaration);
        const txDraftBoundary = normalizeLabReceiptTxDraftBoundary(proof, repairPacket.packet);
        const nextAction = proof.nextAction || inferLabReceiptNextAction(selectedEffect, actionOutcome, externalOutcome, declaration);

        return jsonSafeClone({
          kind: SCM_LAB_RECEIPT_PROOF_KIND,
          vectorVersion: SCM_RECEIPT_VECTOR_VERSION,
          sourceKind: envelope.kind || "mcel-lab-receipt-proof",
          ingestedAt: new Date().toISOString(),
          status: proof.status || actionOutcome,
          mode: proof.mode || "",
          selectedEffect,
          selectedEffectCategory: declaration.category,
          actionOutcome,
          externalOutcome,
          governanceOutcome,
          safetyOutcome,
          proofCompleteness: proof.proofCompleteness || "waiting",
          declaredReads: uniqueScmReceiptList(proof.declaredReads, declaration.declaredReads),
          declaredWrites: uniqueScmReceiptList(proof.declaredWrites, declaration.declaredWrites),
          runtimeConsequences: normalizeLabReceiptRuntimeConsequences(selectedEffect, proof, actionOutcome),
          nextAction,
          repairPacket,
          txDraftBoundary,
          layoutObservation: normalizeLabReceiptLayoutObservation(proof),
          checks: proof.checks || {},
          rawReceipt: envelope
        });
      }

      function normalizeScmReceiptVector(input, options = {}) {
        const direct = parseScmReceiptJsonCandidate(input);
        if (direct?.kind === SCM_LAB_RECEIPT_PROOF_KIND) return jsonSafeClone(direct);
        const labVector = normalizeMcelLabReceiptVector(direct, options);
        if (labVector) return labVector;
        if (direct?.receiptVector) return normalizeScmReceiptVector(direct.receiptVector, options);
        if (direct?.proof) return normalizeScmReceiptVector(direct.proof, options);
        return jsonSafeClone({
          kind: SCM_LAB_RECEIPT_PROOF_KIND,
          vectorVersion: SCM_RECEIPT_VECTOR_VERSION,
          sourceKind: "not-ingested",
          ingestedAt: "",
          status: "waiting",
          mode: "waiting",
          selectedEffect: "",
          selectedEffectCategory: "",
          actionOutcome: "waiting",
          externalOutcome: normalizeReceiptExternalOutcome({}),
          governanceOutcome: "waiting",
          safetyOutcome: "waiting",
          proofCompleteness: "waiting",
          declaredReads: [],
          declaredWrites: [],
          runtimeConsequences: [],
          nextAction: "",
          repairPacket: {
            status: "not required",
            generated: false,
            liveAiCall: null,
            allowedWrites: [],
            forbiddenWrites: [],
            boundaryBlocked: false,
            packet: {}
          },
          txDraftBoundary: {
            status: "not-observed",
            boundary: "",
            noSend: false,
            probeStatus: {nonce: "", gasEstimate: "", ethCall: ""},
            raw: null
          },
          layoutObservation: {
            kind: "",
            source: "",
            measured: false,
            regions: {},
            metrics: {},
            documentHeightRatio: null,
            violations: [],
            styleViolations: []
          },
          checks: {},
          rawReceipt: null
        });
      }

      function ingestScmReceiptVector(input, options = {}) {
        const vector = normalizeScmReceiptVector(input, options);
        studioState.lastScmReceiptVector = vector.sourceKind === "not-ingested" ? null : vector;
        return vector;
      }

      function collectScmReceiptVector(report = studioState.lastReport, summary = null, selectedEvidence = null) {
        const evidenceSummary = summary || collectScmEvidenceSummary(report);
        const candidates = [
          report?.receiptVector,
          report?.labReceipt,
          report?.mcelLabReceipt,
          selectedEvidence?.receiptVector,
          selectedEvidence?.proof,
          selectedEvidence?.rawReceipt,
          evidenceSummary?.componentPacket?.receiptVector,
          evidenceSummary?.componentPacket?.labReceipt,
          evidenceSummary?.routePacket?.receiptVector,
          studioState.lastScmReceiptVector,
          findMcelLabReceiptPayload()
        ];
        for (const candidate of candidates) {
          const vector = normalizeScmReceiptVector(candidate, {selectedEvidence});
          if (vector.sourceKind !== "not-ingested") {
            studioState.lastScmReceiptVector = vector;
            return vector;
          }
        }
        return normalizeScmReceiptVector(null, {selectedEvidence});
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

      function flattenScmGateFlags(gates = {}) {
        const flags = {};
        const visit = (prefix, value) => {
          if (!value || typeof value !== "object") return;
          if ("ok" in value || "skipped" in value || "code" in value) {
            flags[prefix] = {
              ok: value.ok !== false,
              skipped: Boolean(value.skipped),
              code: value.code || ""
            };
            return;
          }
          Object.entries(value).forEach(([key, child]) => {
            visit(prefix ? `${prefix}.${key}` : key, child);
          });
        };
        visit("gates", gates);
        return flags;
      }

      function compareScmGateFlags(before = {}, after = {}) {
        const changes = [];
        const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])].sort();
        keys.forEach((key) => {
          const beforeGate = before[key] || {};
          const afterGate = after[key] || {};
          if (beforeGate.ok !== afterGate.ok || beforeGate.skipped !== afterGate.skipped || beforeGate.code !== afterGate.code) {
            changes.push({
              gate: key.replace(/^gates\.?/, ""),
              before: beforeGate,
              after: afterGate
            });
          }
        });
        return changes;
      }

      function buildScmReplaySnapshot(stage, entry, report = studioState.lastReport) {
        const summary = collectScmEvidenceSummary(report);
        const fields = workspaceFields();
        const routeParams = routeParamsForScm(fields) || {};
        const routeQuery = routeQueryForScm();
        const gates = collectGateStatus(summary.gates || studioState.lastScmGates || null);

        return jsonSafeClone({
          kind: "mcel-code-studio-scm-replay-snapshot",
          snapshotVersion: SCM_REPLAY_SNAPSHOT_VERSION,
          capturedAt: new Date().toISOString(),
          stage,
          evidenceKey: entry?.evidenceKey || "",
          evidenceLabel: evidenceEntryLabel(entry || {}),
          selectedEvidence: formatEvidenceDetail(entry || {}),
          workspace: {
            selectedPath: studioState.selectedPath,
            route: {
              name: window.McelCodeStudioScm?.routeName || "workspace.file",
              params: routeParams,
              query: routeQuery,
              key: currentScmRouteKey(routeParams, routeQuery)
            }
          },
          dirtyState: collectDirtyStateSummary(fields),
          gates,
          evidenceSummary: {
            component: summary.component,
            route: summary.route,
            combined: summary.combined
          },
          recentEvidenceKeys: (summary.recentEvidence || []).map((item) => item.evidenceKey || evidenceEntryLabel(item))
        });
      }

      function compareScmReplaySnapshots(beforeSnapshot, afterSnapshot, replayResult = null) {
        const beforeCombined = beforeSnapshot?.evidenceSummary?.combined || {};
        const afterCombined = afterSnapshot?.evidenceSummary?.combined || {};
        const gateChanges = compareScmGateFlags(
          flattenScmGateFlags(beforeSnapshot?.gates || {}),
          flattenScmGateFlags(afterSnapshot?.gates || {})
        );
        const deltas = {
          total: (afterCombined.total || 0) - (beforeCombined.total || 0),
          ok: (afterCombined.ok || 0) - (beforeCombined.ok || 0),
          violations: (afterCombined.violations || 0) - (beforeCombined.violations || 0),
          blocking: (afterCombined.blocking || 0) - (beforeCombined.blocking || 0),
          gateChanges
        };
        const replayOk = replayResult?.ok !== false;
        const gatesOk = afterSnapshot?.gates?.ok !== false;
        const stable = replayOk && gatesOk && deltas.violations <= 0 && deltas.blocking <= 0 && gateChanges.length === 0;

        return jsonSafeClone({
          kind: "mcel-code-studio-scm-replay-comparison",
          comparisonVersion: SCM_REPLAY_SNAPSHOT_VERSION,
          comparedAt: new Date().toISOString(),
          ok: replayOk && gatesOk,
          stable,
          selectedEvidence: afterSnapshot?.selectedEvidence || beforeSnapshot?.selectedEvidence || null,
          before: beforeSnapshot,
          after: afterSnapshot,
          replayResult,
          deltas
        });
      }

      function formatScmReplayComparisonDetail(comparison = studioState.lastScmReplaySnapshotComparison) {
        if (!comparison) {
          return {
            kind: "mcel-code-studio-scm-replay-comparison",
            comparisonVersion: SCM_REPLAY_SNAPSHOT_VERSION,
            status: "Replay selected gate to capture before/after SCM evidence snapshots."
          };
        }

        return {
          kind: comparison.kind,
          comparisonVersion: comparison.comparisonVersion,
          comparedAt: comparison.comparedAt,
          ok: comparison.ok,
          stable: comparison.stable,
          selectedEvidence: comparison.selectedEvidence,
          deltas: comparison.deltas,
          replayResult: comparison.replayResult,
          before: {
            capturedAt: comparison.before?.capturedAt || "",
            gatesOk: comparison.before?.gates?.ok !== false,
            combined: comparison.before?.evidenceSummary?.combined || null,
            dirtyState: comparison.before?.dirtyState || null
          },
          after: {
            capturedAt: comparison.after?.capturedAt || "",
            gatesOk: comparison.after?.gates?.ok !== false,
            combined: comparison.after?.evidenceSummary?.combined || null,
            dirtyState: comparison.after?.dirtyState || null
          }
        };
      }

      function toContractIdentifier(value, fallback = "GeneratedMcelApp") {
        const words = String(value || fallback)
          .replace(/[^A-Za-z0-9]+/g, " ")
          .trim()
          .split(/\s+/)
          .filter(Boolean);
        const name = words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join("");
        return name || fallback;
      }

      function scmEffectAuthoringTemplate(name) {
        const templates = {
          runValidation: {
            triggers: ["source.workspace.files", "state.dirty"],
            reads: ["source.workspace.files", "state.dirty"],
            writes: ["runtime.validationReport"],
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win"
          },
          loadWorkspace: {
            triggers: ["source.workspace.manifest"],
            reads: ["source.workspace.manifest"],
            writes: ["runtime.validationReport"],
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win"
          },
          loadFile: {
            triggers: ["state.activeFileId"],
            reads: ["source.workspace.files", "state.activeFileId"],
            writes: ["runtime.loadedFile"],
            cancellation: "cancel-previous",
            racePolicy: "latest-inputs-win"
          },
          saveFile: {
            triggers: ["state.drafts", "state.activeFileId"],
            reads: ["source.workspace.files", "state.drafts", "state.activeFileId"],
            writes: ["source.workspace.files", "state.dirty", "runtime.validationReport"],
            cancellation: "explicit-user-action-only",
            racePolicy: "single-writer"
          }
        };
        return templates[name] || {
          triggers: [],
          reads: [],
          writes: [],
          cancellation: "declare-before-use",
          racePolicy: "declare-before-use"
        };
      }

      function buildScmContractAuthoringHelper(options = {}) {
        const packet = options.packet || exportScmEvidenceDebugPacket({refresh: options.refresh !== false});
        const workspace = packet.workspace || {};
        const versions = packet.versions || {};
        const selected = packet.selectedEvidence || {};
        const gates = packet.gates || {};
        const sourceFiles = Array.isArray(workspace.files) ? workspace.files : [];
        const componentName = toContractIdentifier(workspace.title || "Generated MCEL App");
        const effectNames = Object.keys(gates.effect || {});
        const routeName = workspace.route?.name || "workspace.file";
        const selectedReads = Array.isArray(selected.reads) ? selected.reads : [];
        const selectedWrites = Array.isArray(selected.writes) ? selected.writes : [];

        return jsonSafeClone({
          kind: "mcel-code-studio-scm-contract-authoring-helper",
          helperVersion: SCM_CONTRACT_AUTHORING_HELPER_VERSION,
          generatedAt: new Date().toISOString(),
          purpose: "Create strict SCM contracts for generated MCEL apps without copying a React/Vue-style render loop.",
          sourceOfTruth: "current Code Studio SCM evidence packet",
          generatedApp: {
            componentName,
            componentContract: "mcel.scm.generated-app.v1",
            routeName: `${routeName}.generated`,
            routeContract: "mcel.scm.route.generated-app.v1",
            selectedPath: workspace.selectedPath || "",
            fileCount: sourceFiles.length,
            files: sourceFiles.map((file) => ({
              path: file.path,
              language: file.language,
              required: file.required === true,
              field: file.field || "",
              length: file.length || 0
            }))
          },
          authoringPrinciples: [
            "Declare ownership before rendering UI.",
            "Declare reads and writes before loading data or mutating state.",
            "Route params, query, loader reads, and loader writes must be explicit.",
            "Effects need triggers, declared reads/writes, cancellation, and race policy.",
            "Layout and style checks must describe computed behavior, not visual intent only.",
            "Serialization must keep runtime/editor chrome out of author-owned source.",
            "Repair strategies must be guarded and must not mutate source unless explicitly declared."
          ],
          contractSkeleton: {
            component: {
              name: componentName,
              version: "1.0.0",
              contract: "mcel.scm.generated-app.v1",
              owns: {
                source: ["workspace.manifest", "workspace.files"],
                runtime: ["workbench.shell", "loadedFile", "serializedOutput", "validationReport"],
                state: ["activeFileId", "dirty"],
                layout: ["appShell", "content", "statusbar"],
                style: ["generatedAppTheme"],
                effects: effectNames.length ? effectNames : ["loadWorkspace", "loadFile", "saveFile", "runValidation"]
              },
              children: {
                required: "Declare every generated child component, slot, inputs, outputs, mayMutate, and maySerialize."
              },
              outputs: ["fileOpened", "draftEdited", "draftCommitted", "workspaceSerialized"]
            },
            route: {
              name: `${routeName}.generated`,
              version: "1.0.0",
              contract: "mcel.scm.route.generated-app.v1",
              params: Object.keys(workspace.route?.params || {}),
              query: Object.keys(workspace.route?.query || {}),
              loaders: {
                loadWorkspace: {
                  reads: ["route.params.workspaceId"],
                  writes: ["route.data.workspace"],
                  cancellation: "cancel-previous",
                  racePolicy: "latest-route-wins"
                },
                loadFile: {
                  reads: ["route.params.workspaceId", "route.params.fileId"],
                  writes: ["route.data.activeFile"],
                  cancellation: "cancel-previous",
                  racePolicy: "latest-route-wins"
                }
              }
            },
            effects: Object.fromEntries((effectNames.length ? effectNames : ["loadWorkspace", "loadFile", "saveFile", "runValidation"])
              .map((name) => [name, scmEffectAuthoringTemplate(name)])),
            layout: {
              gate: gates.layout?.ok === false ? "must-fix-before-export" : "declare-computed-layout-before-ship",
              examples: ["root overflow", "body display", "dock collapsed height"]
            },
            style: {
              gate: gates.style?.ok === false ? "must-fix-before-export" : "declare-computed-style-before-ship",
              forbiddenGlobalLeakage: ["undeclared button theme", "runtime debug chrome serialized as source"]
            },
            serialization: {
              sourceOwns: ["source.workspace.manifest", "source.workspace.files"],
              runtimeOnly: [
                "runtime.workbench.shell",
                "runtime.editor.chrome",
                "runtime.loadedFile",
                "runtime.serializedOutput",
                "runtime.validationReport",
                "runtime.assistantSession"
              ],
              failIfRuntimeLeaks: true
            },
            repair: {
              allowed: ["runtime.workbench.shell", "runtime.validationReport"],
              forbidden: ["source.workspace.manifest", "source.workspace.files", "state.activeFileId", "state.dirty"],
              replaySafety: "mutating transitions and repair strategies require explicit user action"
            },
            failureBehavior: {
              blocking: ["undeclared writes", "runtime leaks", "layout/style contract violations"],
              warning: ["missing optional generated-app helper metadata"],
              evidenceRequired: true
            }
          },
          selectedEvidenceFocus: {
            scope: selected.scope || "",
            phase: selected.phase || "",
            code: selected.code || "",
            reads: selectedReads,
            writes: selectedWrites
          },
          evidenceContext: {
            gatesOk: gates.ok !== false,
            layoutOk: gates.layout?.ok !== false,
            styleOk: gates.style?.ok !== false,
            routeOk: gates.route?.ok !== false,
            serializationOk: gates.serialization?.ok !== false,
            repairOk: gates.repair?.ok !== false,
            violationCount: packet.evidence?.summary?.combined?.violations || 0,
            blockingCount: packet.evidence?.summary?.combined?.blocking || 0,
            persistenceStatus: packet.persistence?.status || "",
            replaySnapshotStable: packet.lastReplaySnapshotComparison?.stable ?? null
          },
          authoringChecklist: [
            "Name the generated component and route contracts.",
            "Declare source/runtime/state/layout/style/effect ownership.",
            "Declare child slots, inputs, outputs, mayMutate, and maySerialize.",
            "Declare route params/query/loaders and route transition guards.",
            "Declare every effect trigger/read/write/cancellation/race policy.",
            "Declare computed layout and forbidden global style leakage.",
            "Declare clean serialization boundaries and runtime-only fields.",
            "Declare repair strategies, forbidden mutations, and replay safety.",
            "Add tests that assert the contract skeleton and evidence packet shape."
          ],
          versions: {
            codeStudio: versions.codeStudio || "2.9.0",
            component: versions.component || "2.9.0",
            route: versions.route || "1.1.0",
            runtimePackage: versions.runtimePackage || MCEL_RUNTIME_PACKAGE_VERSION
          }
        });
      }

      function formatScmContractAuthoringHelperDetail(helper) {
        if (!helper) {
          return {
            kind: "mcel-code-studio-scm-contract-authoring-helper-detail",
            status: "not-generated",
            message: "Generate a contract helper to create a strict SCM starter for AI-generated apps."
          };
        }
        return {
          kind: "mcel-code-studio-scm-contract-authoring-helper-detail",
          helperVersion: helper.helperVersion,
          componentName: helper.generatedApp?.componentName || "",
          routeName: helper.generatedApp?.routeName || "",
          gatesOk: helper.evidenceContext?.gatesOk !== false,
          violationCount: helper.evidenceContext?.violationCount || 0,
          checklistCount: Array.isArray(helper.authoringChecklist) ? helper.authoringChecklist.length : 0
        };
      }

      function formatScmContractAuthoringHelper(helper) {
        const detail = formatScmContractAuthoringHelperDetail(helper);
        return [
          "MCEL SCM CONTRACT AUTHORING HELPER",
          `helperVersion: ${helper?.helperVersion || SCM_CONTRACT_AUTHORING_HELPER_VERSION}`,
          "",
          "Purpose:",
          "Create a strict SCM contract starter for generated MCEL apps. Do not translate the app into a React/Vue-style render loop.",
          "",
          "Generated target:",
          `component=${detail.componentName || ""}`,
          `route=${detail.routeName || ""}`,
          `gatesOk=${detail.gatesOk} violations=${detail.violationCount}`,
          "",
          "Required contract surfaces:",
          "- component ownership",
          "- child composition",
          "- route params/query/loaders",
          "- effect triggers/reads/writes/cancellation/race policy",
          "- layout/style computed gates",
          "- serialization boundaries",
          "- repair guards and replay safety",
          "",
          "Authoring helper JSON:",
          "```json",
          JSON.stringify(helper, null, 2),
          "```"
        ].join("\n");
      }

      function exportScmContractAuthoringHelper(options = {}) {
        const packet = options.packet || exportScmEvidenceDebugPacket({refresh: options.refresh !== false});
        const helper = buildScmContractAuthoringHelper({packet, refresh: false});
        const text = formatScmContractAuthoringHelper(helper);
        studioState.lastScmContractAuthoringHelper = helper;
        studioState.lastScmContractAuthoringHelperText = text;
        studioState.lastScmContractAuthoringExport = {
          kind: "mcel-code-studio-scm-contract-authoring-helper-export",
          helperVersion: SCM_CONTRACT_AUTHORING_HELPER_VERSION,
          generatedAt: helper.generatedAt,
          byteLength: text.length,
          componentName: helper.generatedApp?.componentName || "",
          routeName: helper.generatedApp?.routeName || "",
          violationCount: helper.evidenceContext?.violationCount || 0
        };
        return {helper, text, packet, export: studioState.lastScmContractAuthoringExport};
      }

      async function copyCurrentScmContractAuthoringHelper() {
        const output = exportScmContractAuthoringHelper({refresh: true});
        try {
          const result = await copyScmText(output.text);
          setStatus(result.ok
            ? `SCM contract authoring helper copied for generated apps (${result.byteLength} bytes).`
            : "SCM contract authoring helper was prepared, but this browser blocked clipboard copy.");
          return {...output, result};
        } catch (error) {
          setStatus(`SCM contract authoring helper was prepared, but clipboard copy failed: ${error?.message || String(error)}.`);
          return {...output, result: {ok: false, mode: "clipboard", message: error?.message || String(error)}};
        }
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
          persistence: collectLiveWorkspacePersistenceSummary(),
          contractAuthoring: studioState.lastScmContractAuthoringExport,
          receiptVector: collectScmReceiptVector(report, summary, selectedEntry),
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
          lastReplaySnapshotComparison: studioState.lastScmReplaySnapshotComparison,
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

      async function copyScmText(text) {
        const value = String(text || "");
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(value);
          return {ok: true, mode: "clipboard", byteLength: value.length};
        }

        const textarea = document.createElement("textarea");
        textarea.value = value;
        textarea.setAttribute("readonly", "readonly");
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        const copied = document.execCommand?.("copy") === true;
        textarea.remove();
        return {ok: copied, mode: "execCommand", byteLength: value.length};
      }

      async function copyScmEvidenceDebugPacket(packet) {
        return copyScmText(JSON.stringify(packet, null, 2));
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

      function buildScmAiRepairPrompt(options = {}) {
        const packet = options.packet || exportScmEvidenceDebugPacket({refresh: options.refresh !== false});
        const visibleEvidence = Array.isArray(packet?.evidence?.visible) ? packet.evidence.visible : [];
        const violations = visibleEvidence
          .filter((entry) => evidenceEntryIsViolation(entry))
          .map((entry) => formatEvidenceDetail(entry));
        const promptInput = jsonSafeClone({
          kind: "mcel-code-studio-scm-ai-repair-prompt-input",
          promptVersion: SCM_AI_REPAIR_PROMPT_VERSION,
          generatedAt: new Date().toISOString(),
          versions: packet.versions,
          workspace: packet.workspace,
          filters: packet.filters,
          dirtyState: packet.dirtyState,
          persistence: packet.persistence,
          contractAuthoring: packet.contractAuthoring,
          gates: packet.gates,
          selectedEvidence: packet.selectedEvidence,
          lastReplayResult: packet.lastReplayResult,
          lastReplaySnapshotComparison: packet.lastReplaySnapshotComparison,
          violationCount: violations.length,
          violations,
          evidencePacket: packet
        });
        const summary = packet?.evidence?.summary?.combined || {};
        const versions = packet?.versions || {};
        const workspace = packet?.workspace || {};
        const selected = packet?.selectedEvidence || {};

        return [
          "MCEL STRICT COMPOSITION MODEL AI REPAIR PROMPT",
          `promptVersion: ${SCM_AI_REPAIR_PROMPT_VERSION}`,
          "",
          "Role:",
          "You are repairing an MCEL Code Studio app as a contract-first UI system for AI-written software.",
          "Do not treat MCEL as a React, Vue, Angular, or Svelte clone. Rendering is secondary to enforceable ownership, reads, writes, routes, effects, layout, style, serialization, repair, and failure behavior.",
          "",
          "Repair objective:",
          "Use the SCM evidence packet to make the smallest safe code change that restores the declared contracts.",
          "Prefer narrow full-file replacement patches and preserve the current component, route, and runtime package versions unless the evidence proves a contract version must change.",
          "",
          "Allowed repair surface:",
          "- Fix only files directly needed to satisfy the reported SCM evidence.",
          "- Preserve declared component ownership, child composition boundaries, route params/query, effect reads/writes, layout/style gates, serialization boundaries, repair guards, replay safety, and live workspace persistence boundaries.",
          "- Use selectedEvidence, violations, gates, dirtyState, persistence, route context, and lastReplayResult as the source of truth.",
          "- Add or update tests that prove the repaired contract shape remains stable.",
          "",
          "Forbidden changes:",
          "- Do not broadly rewrite Code Studio.",
          "- Do not broaden the SCM kernel, route semantics, or runtime package unless the packet evidence explicitly requires it.",
          "- Do not introduce undeclared DOM, source, route, state, or runtime reads/writes.",
          "- Do not serialize runtime-only editor chrome or assistant/debug UI into author-owned source.",
          "- Do not auto-run mutating transitions or repair strategies without explicit user action.",
          "- Do not convert the app to a React/Vue-style state rendering architecture.",
          "",
          "Current target:",
          `component=${versions.component || ""} componentContract=${versions.componentContract || ""}`,
          `route=${workspace.route?.name || ""} routeVersion=${versions.route || ""} routeContract=${versions.routeContract || ""}`,
          `runtimePackage=${versions.runtimePackage || ""}`,
          `selectedPath=${workspace.selectedPath || ""}`,
          `persistenceStatus=${packet.persistence?.status || ""} persistenceSavedAt=${packet.persistence?.savedAt || ""}`,
          `contractAuthoringHelper=${packet.contractAuthoring?.kind || ""} contractAuthoringComponent=${packet.contractAuthoring?.componentName || ""}`,
          `selectedEvidenceScope=${selected.scope || ""} selectedEvidencePhase=${selected.phase || ""} selectedEvidenceCode=${selected.code || ""}`,
          `combinedEvidenceTotal=${summary.total || 0} combinedViolations=${summary.violations || 0} blocking=${summary.blocking || 0}`,
          `replaySnapshotStable=${packet.lastReplaySnapshotComparison?.stable ?? ""} replaySnapshotViolationsDelta=${packet.lastReplaySnapshotComparison?.deltas?.violations ?? ""}`,
          "",
          "SCM evidence packet JSON:",
          "```json",
          JSON.stringify(promptInput, null, 2),
          "```"
        ].join("\n");
      }

      function exportScmAiRepairPrompt(options = {}) {
        const packet = options.packet || exportScmEvidenceDebugPacket({refresh: options.refresh !== false});
        const prompt = buildScmAiRepairPrompt({packet, refresh: false});
        studioState.lastScmRepairPrompt = prompt;
        studioState.lastScmRepairPromptExport = {
          kind: "mcel-code-studio-scm-ai-repair-prompt",
          promptVersion: SCM_AI_REPAIR_PROMPT_VERSION,
          generatedAt: new Date().toISOString(),
          byteLength: prompt.length,
          evidenceCount: packet?.evidence?.summary?.combined?.total || 0,
          violations: packet?.evidence?.summary?.combined?.violations || 0,
          selectedEvidence: packet?.selectedEvidence || null
        };
        return {
          prompt,
          packet,
          export: studioState.lastScmRepairPromptExport
        };
      }

      async function copyCurrentScmAiRepairPrompt() {
        const output = exportScmAiRepairPrompt({refresh: true});
        try {
          const result = await copyScmText(output.prompt);
          setStatus(result.ok
            ? `SCM AI repair prompt copied from evidence packet (${result.byteLength} bytes).`
            : "SCM AI repair prompt was prepared, but this browser blocked clipboard copy.");
          return {...output, result};
        } catch (error) {
          setStatus(`SCM AI repair prompt was prepared, but clipboard copy failed: ${error?.message || String(error)}.`);
          return {...output, result: {ok: false, mode: "clipboard", message: error?.message || String(error)}};
        }
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
        const beforeSnapshot = buildScmReplaySnapshot("before", entry, studioState.lastReport);
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
        const afterSnapshot = buildScmReplaySnapshot("after", studioState.selectedScmEvidenceSnapshot, studioState.lastReport);
        studioState.lastScmReplaySnapshotComparison = compareScmReplaySnapshots(beforeSnapshot, afterSnapshot, result);
        setStatus(`SCM evidence replay ${result?.ok === false ? "blocked" : "completed"} for ${evidenceEntryLabel(entry)}; before/after snapshot comparison ${studioState.lastScmReplaySnapshotComparison.stable ? "stable" : "changed"}.`);
        return result;
      }

      // Flagship inspector mental model: Contract = what must hold, Effects = what may mutate, Runtime = what is current, Repair = what may change next.
      function gateLabel(value) {
        return value === false ? "fail" : "ok";
      }

      function setInspectorPanel(tab = "contract") {
        if (!flagshipInspector) return;
        const selected = ["contract", "evidence", "runtime", "ai"].includes(tab) ? tab : "contract";
        studioState.activeScmInspectorTab = selected;
        flagshipInspector.querySelectorAll("[data-code-studio-scm-ai-tab]").forEach((button) => {
          const active = button.dataset.codeStudioScmAiTab === selected;
          button.classList.toggle("active", active);
          button.setAttribute("aria-selected", active ? "true" : "false");
        });
        flagshipInspector.querySelectorAll("[data-code-studio-scm-ai-panel]").forEach((panel) => {
          const active = panel.dataset.codeStudioScmAiPanel === selected;
          panel.classList.toggle("active", active);
          panel.hidden = !active;
        });
      }

      function renderDefinitionList(node, entries) {
        if (!node) return;
        node.innerHTML = entries.map(([label, value]) => `
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value)}</dd>
        `).join("");
      }

      function updateTopCommandStatus(summary, gates, persistence, fields) {
        if (topRouteStatus) {
          topRouteStatus.textContent = `${window.McelCodeStudioScm?.routeName || "workspace.file"}/${studioState.selectedPath || fields?.files?.[0]?.path || "no-file"}`;
        }
        if (topGateStatus) {
          const ok = gates.ok !== false && summary.combined.violations === 0;
          topGateStatus.textContent = ok ? "gates ok" : `gates fail · ${summary.combined.violations} violation(s)`;
          topGateStatus.dataset.ok = ok ? "true" : "false";
        }
        if (topPersistenceStatus) {
          const statusText = persistence.status || "not saved";
          topPersistenceStatus.textContent = `persistence ${statusText}`;
          topPersistenceStatus.dataset.ok = persistence.ok === false ? "false" : "true";
        }
        if (topRuntimeVersion) {
          topRuntimeVersion.textContent = MCEL_RUNTIME_PACKAGE_VERSION;
        }
      }

      function normalizeScmSurfaceList(value) {
        if (Array.isArray(value)) return value.map((item) => String(item || "").trim()).filter(Boolean);
        return String(value || "")
          .split(/[,\s]+/)
          .map((item) => item.trim())
          .filter(Boolean);
      }

      function formatScmSurfaceList(value, fallback = "none declared") {
        const items = normalizeScmSurfaceList(value);
        return items.length ? items.join(", ") : fallback;
      }

      function compactSourceSnippet(value, limit = 72) {
        const text = String(value || "").replace(/\s+/g, " ").trim();
        if (!text) return "source element";
        return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
      }

      function extractContractEffectsFromHtml(html, filePath = "source.html") {
        const parser = new DOMParser();
        const doc = parser.parseFromString(String(html || ""), "text/html");
        return [...doc.querySelectorAll("[data-mc-effect]")].map((node, index) => {
          const effectName = node.getAttribute("data-mc-effect") || `effect-${index + 1}`;
          return {
            name: effectName,
            kind: "declared-effect",
            status: "declared",
            trigger: node.getAttribute("data-mc-trigger") || node.getAttribute("data-mc-event") || "explicit",
            reads: normalizeScmSurfaceList(node.getAttribute("data-mc-reads") || node.getAttribute("data-mc-read")),
            writes: normalizeScmSurfaceList(node.getAttribute("data-mc-writes") || node.getAttribute("data-mc-write")),
            sourcePath: filePath,
            sourceLabel: compactSourceSnippet(node.textContent || node.getAttribute("aria-label") || effectName)
          };
        });
      }

      function collectContractEffectSurface(fields = workspaceFields()) {
        const byName = new Map();
        const add = (entry) => {
          if (!entry?.name) return;
          const existing = byName.get(entry.name);
          if (!existing) {
            byName.set(entry.name, entry);
            return;
          }
          byName.set(entry.name, {
            ...existing,
            ...entry,
            reads: [...new Set([...(existing.reads || []), ...(entry.reads || [])])],
            writes: [...new Set([...(existing.writes || []), ...(entry.writes || [])])],
            sourcePath: existing.sourcePath || entry.sourcePath,
            sourceLabel: existing.sourceLabel || entry.sourceLabel
          });
        };

        (fields.files || []).forEach((file) => {
          extractContractEffectsFromHtml(file.value, file.path || "source.html").forEach(add);
        });
        extractContractEffectsFromHtml(sourceEditor.value, "workspace-source").forEach(add);

        [
          {name: "runValidation", kind: "component-effect", trigger: "validate", reads: ["source.workspace.files"], writes: ["runtime.validationReport"]},
          {name: "loadWorkspace", kind: "route-effect", trigger: "route loader", reads: ["route.params.workspaceId"], writes: ["runtime.workspace"]},
          {name: "loadFile", kind: "route-effect", trigger: "route loader", reads: ["route.params.fileId"], writes: ["runtime.loadedFile"]},
          {name: "saveFile", kind: "source-effect", trigger: "explicit save", reads: ["runtime.loadedFile"], writes: ["source.workspace.files"]},
          {name: "serialize", kind: "serialization", trigger: "explicit serialize", reads: ["source.workspace.files", "runtime.preview"], writes: ["runtime.serializedOutput"]},
          {name: "repair:rebuildWorkbenchShell", kind: "repair", trigger: "AI repair", reads: ["runtime.validationReport", "runtime.evidence"], writes: ["runtime.repairResult"]}
        ].forEach(add);

        return [...byName.values()];
      }

      function effectNameFromEvidence(entry = {}) {
        return entry.effectName
          || entry.transitionName
          || entry.loaderName
          || entry.strategyName
          || entry.childName
          || "";
      }

      function evidenceStatusLabel(entry = {}) {
        if (!entry || entry.kind === "mcel-code-studio-idle-evidence") return "waiting";
        if (evidenceEntryIsViolation(entry)) return "fail";
        if (entry.ok === false) return "fail";
        if (entry.skipped) return "skipped";
        if (entry.phase || entry.kind || entry.code) return "pass";
        return "waiting";
      }

      function statusRank(status) {
        if (status === "fail") return 4;
        if (status === "pass") return 3;
        if (status === "skipped") return 2;
        if (status === "declared") return 1;
        return 0;
      }

      function mergeEffectStatus(current, next) {
        return statusRank(next) > statusRank(current) ? next : current;
      }

      function buildEffectGraphModel(summary, gates, fields, selectedEvidence) {
        const effectMap = new Map();
        collectContractEffectSurface(fields).forEach((effect) => {
          effectMap.set(effect.name, {
            ...effect,
            status: effect.status || "declared",
            latestEvidence: null
          });
        });

        (summary.allEvidence || []).forEach((entry) => {
          const name = effectNameFromEvidence(entry);
          if (!name) return;
          const existing = effectMap.get(name) || {
            name,
            kind: evidenceEntryScope(entry, "effect"),
            trigger: entry.phase || "evidence",
            reads: [],
            writes: [],
            sourcePath: evidenceEntryScope(entry, "effect"),
            sourceLabel: evidenceEntryLabel(entry)
          };
          const status = evidenceStatusLabel(entry);
          effectMap.set(name, {
            ...existing,
            status: mergeEffectStatus(existing.status || "waiting", status),
            latestEvidence: entry,
            sourceLabel: existing.sourceLabel || evidenceEntryLabel(entry)
          });
        });

        const validationStatus = gates.effect?.runValidation?.ok === false ? "fail" : gates.effect?.runValidation?.skipped ? "skipped" : gates.effect?.runValidation?.ok ? "pass" : "declared";
        const loadWorkspaceStatus = gates.effect?.loadWorkspace?.ok === false ? "fail" : gates.effect?.loadWorkspace?.ok ? "pass" : "declared";
        const loadFileStatus = gates.effect?.loadFile?.ok === false ? "fail" : gates.effect?.loadFile?.ok ? "pass" : "declared";
        const saveStatus = gates.effect?.saveFile?.ok === false ? "fail" : gates.effect?.saveFile?.ok ? "pass" : "declared";
        const serializationStatus = gates.serialization?.ok === false ? "fail" : gates.serialization?.resultKind || gates.serialization?.code ? "pass" : "declared";
        const repairStatus = gates.repair?.ok === false ? "fail" : gates.repair?.resultKind || gates.repair?.code ? "pass" : "declared";

        [
          ["runValidation", validationStatus],
          ["loadWorkspace", loadWorkspaceStatus],
          ["loadFile", loadFileStatus],
          ["saveFile", saveStatus],
          ["serialize", serializationStatus],
          ["repair:rebuildWorkbenchShell", repairStatus]
        ].forEach(([name, status]) => {
          const current = effectMap.get(name);
          if (!current) return;
          effectMap.set(name, {...current, status: mergeEffectStatus(current.status || "waiting", status)});
        });

        let selectedName = effectNameFromEvidence(selectedEvidence);
        if (!selectedName && selectedEvidence?.scope === "serialization") selectedName = "serialize";
        if (!selectedName && selectedEvidence?.scope === "repair") selectedName = "repair:rebuildWorkbenchShell";
        const effects = [...effectMap.values()].sort((left, right) => {
          const statusDelta = statusRank(right.status) - statusRank(left.status);
          return statusDelta || String(left.name).localeCompare(String(right.name));
        });
        const selected = effectMap.get(selectedName)
          || effects.find((effect) => effect.status === "fail")
          || effects.find((effect) => effect.status === "declared")
          || effects[0]
          || null;

        return {effects, selected};
      }

      function buildActionableScmGaps(summary, gates, effectGraph, selectedEvidence) {
        const gaps = [];
        if (gates.available === false) {
          gaps.push("SCM bridge unavailable: load the runtime bridge before trusting proof output.");
        }
        if (gates.ok === false) {
          gaps.push("Run or inspect the failing SCM gate in the proof dock.");
        }
        if (summary.combined.violations > 0) {
          gaps.push(`Open violation detail: ${summary.combined.violations} blocking or suspicious evidence entr${summary.combined.violations === 1 ? "y" : "ies"}.`);
        }
        const declaredOnly = (effectGraph.effects || []).filter((effect) => effect.status === "declared").slice(0, 3);
        declaredOnly.forEach((effect) => {
          gaps.push(`Run or inspect ${effect.name}; it is declared but has no committed receipt in the current evidence.`);
        });
        if (selectedEvidence && evidenceEntryIsViolation(selectedEvidence)) {
          gaps.unshift(`Selected evidence is failing: ${evidenceEntryLabel(selectedEvidence)}.`);
        }
        if (!gaps.length) {
          gaps.push("No current gaps: open the proof dock only if you need raw replay, serialized output, or repair payloads.");
        }
        return gaps.slice(0, 5);
      }

      function buildScmReceiptSurfaceModel(summary, gates, fields, selectedEvidence, persistence, replayComparison, contractAuthoring) {
        const effectGraph = buildEffectGraphModel(summary, gates, fields, selectedEvidence);
        const selectedEffect = effectGraph.selected || {};
        const receiptVector = collectScmReceiptVector(studioState.lastReport, summary, selectedEvidence);
        const vectorEffect = receiptVector?.selectedEffect || "";
        const receiptVectorIngested = receiptVector?.sourceKind !== "not-ingested";
        const receiptMode = receiptVector?.mode && receiptVectorIngested
          ? receiptVector.mode
          : (studioState.lastReport ? "authoring-refresh" : "waiting");
        const receiptOk = receiptVectorIngested
          ? ["pass", "blocked", "exception"].includes(receiptVector.status) && receiptVector.governanceOutcome !== "fail" && receiptVector.safetyOutcome !== "fail" && receiptVector.proofCompleteness !== "incomplete"
          : receiptMode !== "waiting" && gates.ok !== false && summary.combined.violations === 0;
        const receiptLabel = receiptVectorIngested
          ? receiptVector.status
          : (receiptMode === "waiting" ? "not run" : receiptOk ? "pass" : `gap · ${summary.combined.violations} violation(s)`);
        const gaps = buildActionableScmGaps(summary, gates, effectGraph, selectedEvidence);
        const activePane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "source";
        const selected = selectedFile(fields);
        return {
          receiptMode,
          receiptOk,
          receiptVector,
          effectGraph: effectGraph.effects,
          actionableGaps: gaps,
          receiptRows: [
            ["Mode", receiptMode],
            ["Receipt", receiptLabel],
            ["Selected effect", vectorEffect || selectedEffect.name || "none"],
            ["Action outcome", receiptVector?.actionOutcome || "waiting"],
            ["External outcome", receiptVector?.externalOutcome?.status && receiptVector.externalOutcome.status !== "waiting"
              ? `${receiptVector.externalOutcome.status}${receiptVector.externalOutcome.reason ? ` · ${receiptVector.externalOutcome.reason}` : ""}`
              : "not ingested"],
            ["Governance / Safety", `${receiptVector?.governanceOutcome || "waiting"} / ${receiptVector?.safetyOutcome || "waiting"}`],
            ["Proof completeness", receiptVector?.proofCompleteness || "waiting"],
            ["Next action", receiptVector?.nextAction || "none"],
            ["Raw payloads", "Bottom Proof Dock only"]
          ],
          selectedEffectRows: [
            ["Effect", vectorEffect || selectedEffect.name || "none selected"],
            ["Status", receiptVector?.actionOutcome && receiptVector.actionOutcome !== "waiting" ? receiptVector.actionOutcome : (selectedEffect.status || "waiting")],
            ["Trigger", selectedEffect.trigger || selectedEffect.kind || receiptVector?.selectedEffectCategory || "not declared"],
            ["Reads", formatScmSurfaceList(receiptVector?.declaredReads?.length ? receiptVector.declaredReads : selectedEffect.reads)],
            ["Writes", formatScmSurfaceList(receiptVector?.declaredWrites?.length ? receiptVector.declaredWrites : selectedEffect.writes)],
            ["Source", `${selectedEffect.sourcePath || receiptVector?.sourceKind || "evidence"} · ${selectedEffect.sourceLabel || "receipt vector normalized"} `]
          ],
          currentRuntimeRows: [
            ["Active pane", activePane],
            ["Mounted", studioState.mounted ? "mounted" : "not mounted"],
            ["Dirty state", studioState.dirty ? "dirty" : "clean"],
            ["Selected file", selected?.path || studioState.selectedPath || "none"],
            ["Runtime chrome", "runtime preview, editor UI, evidence, assistant output"],
            ["Route key", currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())]
          ],
          proofHistoryRows: [
            ["Replay", replayComparison ? (replayComparison.stable ? "stable" : "changed") : "not run"],
            ["Serialization", gates.serialization?.ok === false ? "fail" : studioState.lastSerializationGate ? "clean source checked" : "not run"],
            ["Repair", gates.repair?.ok === false ? "fail" : studioState.lastRepairGate ? "scoped repair checked" : "not run"],
            ["Persistence", `${persistence.status || "not saved"}${persistence.savedAt ? ` · ${persistence.savedAt}` : ""}`],
            ["Contract helper", contractAuthoring ? "generated" : "not generated"]
          ]
        };
      }

      function renderScmEffectGraph(node, effects = []) {
        if (!node) return;
        node.innerHTML = (effects || []).slice(0, 9).map((effect) => `
          <article class="code-studio-scm-effect-node" data-status="${escapeHtml(effect.status || "waiting")}">
            <div>
              <strong>${escapeHtml(effect.name || "effect")}</strong>
              <span>${escapeHtml(effect.kind || effect.trigger || "governed effect")}</span>
            </div>
            <code>${escapeHtml(effect.status || "waiting")}</code>
          </article>
        `).join("") || '<p class="code-studio-empty-state">No governed effects found in the current contract surface.</p>';
      }

      function renderActionableScmGaps(node, gaps = []) {
        if (!node) return;
        node.innerHTML = (gaps || []).map((gap) => `<li>${escapeHtml(gap)}</li>`).join("");
      }

      function buildFlagshipInspectorModel(report = studioState.lastReport) {
        const fields = workspaceFields();
        const selected = selectedFile(fields);
        const summary = collectScmEvidenceSummary(report);
        const gates = collectGateStatus(report?.scm || studioState.lastScmGates);
        const persistence = collectLiveWorkspacePersistenceSummary();
        const filter = studioState.scmEvidenceFilter || "all";
        const entries = visibleScmEvidenceEntries(summary, filter);
        const selectedEvidence = resolveSelectedScmEvidence(summary, filter, entries);
        const contractAuthoring = studioState.lastScmContractAuthoringExport || studioState.lastScmContractAuthoringHelper || null;
        const replayComparison = studioState.lastScmReplaySnapshotComparison;
        const receiptSurface = buildScmReceiptSurfaceModel(summary, gates, fields, selectedEvidence, persistence, replayComparison, contractAuthoring);

        return {
          fields,
          selected,
          summary,
          gates,
          persistence,
          selectedEvidence,
          contractAuthoring,
          replayComparison,
          ...receiptSurface,
          contractRows: [
            ["Component", `CodeStudio ${window.McelCodeStudioScm?.componentVersion || "2.9.0"}`],
            ["Route", `${window.McelCodeStudioScm?.routeName || "workspace.file"} · ${window.McelCodeStudioScm?.routeVersion || "1.1.0"}`],
            ["Owns", "source.workspace.manifest, source.workspace.files"],
            ["May read", "route params/query, selected source file, dirty runtime state, SCM evidence"],
            ["May write", "runtime.loadedFile, runtime.validationReport, runtime.serializedOutput, guarded evidence UI state"],
            ["Serialization", "clean source only; runtime/editor/assistant chrome omitted"],
            ["Failure behavior", "violations block unsafe repair/serialization and stay replayable"]
          ],
          evidenceRows: [
            ["Gate status", gates.ok === false ? "fail" : "ok"],
            ["Layout", gateLabel(gates.layout?.ok)],
            ["Style", gateLabel(gates.style?.ok)],
            ["Route", gateLabel(gates.route?.ok)],
            ["Component evidence", `${summary.component.total} total`],
            ["Route evidence", `${summary.route.total} total`],
            ["Violations", `${summary.combined.violations}`],
            ["Selected", `${evidenceEntryScope(selectedEvidence)} · ${selectedEvidence.phase || "idle"}`]
          ],
          runtimeRows: [
            ["Selected path", selected?.path || studioState.selectedPath || "none"],
            ["Files", `${fields.files.length}`],
            ["Dirty state", studioState.dirty ? "dirty" : "clean"],
            ["Mounted", studioState.mounted ? "mounted" : "not mounted"],
            ["Persistence", `${persistence.status || "not saved"}${persistence.savedAt ? ` · ${persistence.savedAt}` : ""}`],
            ["Route key", currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())],
            ["Replay", replayComparison ? (replayComparison.stable ? "stable" : "changed") : "not run"]
          ],
          aiRows: [
            ["Repair prompt", studioState.lastScmRepairPrompt ? "generated" : "ready from evidence"],
            ["Contract helper", contractAuthoring ? `${contractAuthoring.componentName || "generated helper"} · ${contractAuthoring.routeName || "route pending"}` : "not generated"],
            ["Evidence packet", studioState.lastScmDebugPacket ? "exported" : "ready"],
            ["Allowed repair", "smallest safe contract-first patch only"],
            ["Forbidden", "no undeclared DOM/source/route/state/runtime reads or writes"]
          ]
        };
      }

      function renderFlagshipInspector(report = studioState.lastReport) {
        if (!flagshipInspector) return null;
        const model = buildFlagshipInspectorModel(report);
        updateTopCommandStatus(model.summary, model.gates, model.persistence, model.fields);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-receipt-summary"), model.receiptRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-contract-summary"), model.contractRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-selected-effect-summary"), model.selectedEffectRows);
        renderScmEffectGraph(flagshipInspector.querySelector("#code-studio-flagship-effect-graph"), model.effectGraph);
        renderActionableScmGaps(flagshipInspector.querySelector("#code-studio-flagship-actionable-gaps"), model.actionableGaps);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-evidence-summary"), model.evidenceRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-current-runtime-summary"), model.currentRuntimeRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-proof-history-summary"), model.proofHistoryRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-runtime-summary"), model.runtimeRows);
        renderDefinitionList(flagshipInspector.querySelector("#code-studio-flagship-ai-summary"), model.aiRows);

        flagshipInspector.querySelectorAll("[data-code-studio-scm-ai-tab]").forEach((button) => {
          button.onclick = () => setInspectorPanel(button.dataset.codeStudioScmAiTab || "contract");
        });
        flagshipInspector.querySelectorAll("[data-code-studio-scm-ai-action]").forEach((button) => {
          button.onclick = async () => {
            const action = button.dataset.codeStudioScmAiAction || "";
            if (action === "copy-prompt") {
              await copyCurrentScmAiRepairPrompt();
              studioState.activeScmInspectorTab = "ai";
            } else if (action === "copy-helper") {
              await copyCurrentScmContractAuthoringHelper();
              studioState.activeScmInspectorTab = "ai";
            } else if (action === "copy-packet") {
              await copyCurrentScmEvidenceDebugPacket();
              studioState.activeScmInspectorTab = "evidence";
            }
            renderFlagshipInspector(studioState.lastReport);
            renderScmEvidencePanel(studioState.lastReport);
          };
        });
        setInspectorPanel(studioState.activeScmInspectorTab || "contract");
        return model;
      }



      function setProofDockExpanded(expanded, label = "") {
        if (!proofDock) return;
        proofDock.dataset.expanded = expanded ? "true" : "false";
        if (proofDockToggle) {
          proofDockToggle.setAttribute("aria-expanded", expanded ? "true" : "false");
          proofDockToggle.textContent = expanded ? "Close proof dock" : "Open proof dock";
        }
        if (!expanded && proofDockDetailPanel) {
          proofDockDetailPanel.hidden = true;
          proofDockDetailPanel.innerHTML = "";
        }
        if (label) setStatus(label);
        ensureCodeStudioScmSurfaceStyles();
      }

      function renderProofDockPayload(title, detail, options = {}) {
        if (!proofDockDetailPanel) return null;
        const kind = options.kind || "proof-detail";
        const action = options.action || "";
        const payload = typeof detail === "string" ? detail : JSON.stringify(jsonSafeClone(detail), null, 2);
        proofDockDetailPanel.hidden = false;
        proofDockDetailPanel.dataset.proofKind = kind;
        proofDockDetailPanel.innerHTML = `
          <div class="code-studio-proof-detail-heading">
            <strong>${escapeHtml(title || "Proof detail")}</strong>
            <span>${escapeHtml(kind)}</span>
            ${action ? `<button type="button" data-code-studio-proof-action="${escapeHtml(action)}">Copy</button>` : ""}
          </div>
          <pre class="code-studio-proof-detail-output" tabindex="0">${escapeHtml(payload || "{}")}</pre>
        `;
        proofDockDetailPanel.querySelector("[data-code-studio-proof-action]")?.addEventListener("click", async () => {
          await copyTextToClipboard(payload || "");
          setStatus(`${title || "Proof detail"} copied from the Bottom Proof Dock.`);
        });
        setProofDockExpanded(true, `${title || "Proof detail"} opened in the Bottom Proof Dock.`);
        return payload;
      }

      function renderSelectedEvidenceInProofDock(summary, filter = studioState.scmEvidenceFilter || "all") {
        const entries = visibleScmEvidenceEntries(summary, filter);
        const selectedEntry = resolveSelectedScmEvidence(summary, filter, entries);
        const selectedSnapshot = studioState.selectedScmEvidenceSnapshot?.evidenceKey === studioState.selectedScmEvidenceKey
          ? studioState.selectedScmEvidenceSnapshot
          : selectedEntry;
        return renderProofDockPayload("Selected SCM evidence detail", formatEvidenceDetail(selectedSnapshot), {
          kind: "selected-evidence",
          action: "copy-selected-evidence"
        });
      }

      function renderReplayComparisonInProofDock() {
        return renderProofDockPayload("Replay snapshot comparison", formatScmReplayComparisonDetail(studioState.lastScmReplaySnapshotComparison), {
          kind: "replay-comparison",
          action: "copy-replay-comparison"
        });
      }

      function renderContractHelperInProofDock() {
        const detail = studioState.lastScmContractAuthoringHelper
          ? formatScmContractAuthoringHelperDetail(studioState.lastScmContractAuthoringHelper)
          : {status: "not-generated", message: "Generate the contract helper first."};
        return renderProofDockPayload("SCM contract authoring helper", detail, {
          kind: "contract-helper",
          action: "copy-contract-helper-detail"
        });
      }

      function renderScmEvidencePanel(report = studioState.lastReport) {
        if (!scmEvidencePanel) return null;
        const summary = collectScmEvidenceSummary(report);
        const gates = summary.gates || {};
        const filter = studioState.scmEvidenceFilter || "all";
        const entries = visibleScmEvidenceEntries(summary, filter);
        const selectedEntry = resolveSelectedScmEvidence(summary, filter, entries);
        studioState.selectedScmEvidenceKey = selectedEntry.evidenceKey || "";
        const previewEntries = entries.slice(0, 8);

        const filterOptions = SCM_EVIDENCE_FILTERS.map((value) => `
          <option value="${value}"${filter === value ? " selected" : ""}>${value}</option>
        `).join("");

        const rows = previewEntries.map((entry) => `
          <button type="button"
            class="code-studio-scm-evidence-entry"
            data-ok="${evidenceEntryIsViolation(entry) ? "false" : "true"}"
            data-selected="${entry.evidenceKey === studioState.selectedScmEvidenceKey ? "true" : "false"}"
            data-scm-evidence-key="${escapeHtml(entry.evidenceKey || "")}">
            <strong>${escapeHtml(evidenceEntryLabel(entry))}</strong>
            <span>${escapeHtml(entry.message || entry.path || entry.target || "SCM operation recorded.")}</span>
          </button>
        `).join("");

        scmEvidencePanel.innerHTML = `
          <div class="code-studio-scm-evidence-heading">
            <strong>SCM proof summary</strong>
            <div class="code-studio-scm-evidence-actions">
              <label>Filter
                <select id="code-studio-scm-evidence-filter">
                  ${filterOptions}
                </select>
              </label>
              <button type="button" id="code-studio-replay-scm-evidence">Replay selected gate</button>
              <button type="button" id="code-studio-open-scm-evidence-detail">Open evidence detail in proof dock</button>
              <button type="button" id="code-studio-open-scm-replay-detail">Open replay in proof dock</button>
              <button type="button" id="code-studio-open-scm-contract-helper-detail">Open helper in proof dock</button>
              <button type="button" id="code-studio-export-scm-evidence-packet">Copy packet</button>
              <button type="button" id="code-studio-generate-scm-repair-prompt">Generate AI repair prompt</button>
              <button type="button" id="code-studio-generate-scm-contract-helper">Generate contract helper</button>
              <button type="button" id="code-studio-download-scm-evidence-packet">Download packet</button>
              <button type="button" id="code-studio-refresh-scm-evidence">Refresh SCM evidence</button>
            </div>
          </div>
          <div class="code-studio-scm-evidence-summary" data-code-studio-proof-routing="compact-summary">
            <span>component <code>${summary.component.total}</code></span>
            <span>route <code>${summary.route.total}</code></span>
            <span>visible <code>${entries.length}</code></span>
            <span>violations <code>${summary.combined.violations}</code></span>
            <span>blocking <code>${summary.combined.blocking}</code></span>
            <span>layout <code>${gates.layout?.ok === false ? "fail" : "ok"}</code></span>
            <span>style <code>${gates.style?.ok === false ? "fail" : "ok"}</code></span>
            <span>route <code>${gates.route?.ok === false ? "fail" : "ok"}</code></span>
          </div>
          <div class="code-studio-scm-proof-summary-card">
            <strong>Center workbench is authoring-first.</strong>
            <p>Long SCM proof payloads are routed to the Bottom Proof Dock. This panel only previews selected evidence and actions.</p>
          </div>
          <div class="code-studio-scm-evidence-preview" role="list" aria-label="Compact SCM evidence preview">
            ${rows || '<p class="code-studio-empty-state">No SCM evidence entries match this filter.</p>'}
            ${entries.length > previewEntries.length ? `<p class="code-studio-evidence-overflow-note">Showing ${previewEntries.length} of ${entries.length}. Open the proof dock for full details.</p>` : ""}
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

        scmEvidencePanel.querySelector("#code-studio-open-scm-evidence-detail")?.addEventListener("click", () => {
          renderSelectedEvidenceInProofDock(summary, filter);
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-replay-detail")?.addEventListener("click", () => {
          renderReplayComparisonInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-contract-helper-detail")?.addEventListener("click", () => {
          renderContractHelperInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-replay-scm-evidence")?.addEventListener("click", () => {
          const entry = entries.find((candidate) => candidate.evidenceKey === studioState.selectedScmEvidenceKey) || selectedEntry;
          replayScmEvidenceEntry(entry);
          renderScmEvidencePanel(studioState.lastReport);
          renderReplayComparisonInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-export-scm-evidence-packet")?.addEventListener("click", () => {
          copyCurrentScmEvidenceDebugPacket();
        });

        scmEvidencePanel.querySelector("#code-studio-generate-scm-repair-prompt")?.addEventListener("click", () => {
          copyCurrentScmAiRepairPrompt();
        });

        scmEvidencePanel.querySelector("#code-studio-generate-scm-contract-helper")?.addEventListener("click", async () => {
          await copyCurrentScmContractAuthoringHelper();
          renderScmEvidencePanel(studioState.lastReport);
          renderContractHelperInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-download-scm-evidence-packet")?.addEventListener("click", () => {
          downloadCurrentScmEvidenceDebugPacket();
        });

        scmEvidencePanel.querySelector("#code-studio-refresh-scm-evidence")?.addEventListener("click", () => {
          runScmRuntimeChecks();
          renderScmEvidencePanel(studioState.lastReport);
          setStatus("SCM evidence refreshed from component, route, effect, layout, style, serialization, and repair gates.");
        });

        renderFlagshipInspector(studioState.lastReport || report);
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
        return scmEvidenceSummary;
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
        studioState.lastRouteLoaderPersistenceGate = enterScmRouteAndRunLoaders({forceEnter: true});
        persistLiveWorkspaceFromSource("commitDraft", {
          saveGate: studioState.lastSaveFileEffectGate,
          loaderGate: studioState.lastRouteLoaderPersistenceGate
        });
        renderRuntime();
        setStatus("Runtime draft committed into author-owned source, persisted through SCM saveFile, and route/effect loaders refreshed.");
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
            showPane("contract");
          }
          if (panel === "source" || panel === "explorer") {
            runScmTransition("selectPanel", {panel: "source"});
            showPane("source");
          }
          if (panel === "assistant") {
            setProofDockExpanded(true, "Bottom Proof Dock opened for assistant and proof detail output.");
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
        prepareFlagshipWorkbenchRegions();
        setProofDockExpanded(!expanded, expanded ? "Bottom Proof Dock collapsed." : "Bottom Proof Dock opened.");
      });

      sourceEditor.addEventListener("input", () => {
        syncLineGutter();
        studioState.mounted = false;
        studioState.damaged = false;
        syncScmInstance();
        scmRouteKey = "";
        setRuntimeLabel();
        renderLiveWorkspacePersistenceStatus({
          ...collectLiveWorkspacePersistenceSummary(),
          status: "source-dirty",
          ok: true,
          selectedPath: studioState.selectedPath,
          sourceLength: sourceEditor.value.length,
          fileCount: workspaceFields().files.length
        });
        setStatus("Source changed. Save live workspace, remount, or validate to refresh the MCEL runtime, route loaders, and SCM evidence.");
      });
      sourceEditor.addEventListener("scroll", () => {
        if (gutter) gutter.scrollTop = sourceEditor.scrollTop;
      });

      saveLiveWorkspaceButton?.addEventListener("click", () => {
        persistLiveWorkspaceFromSource("manual-save");
        renderScmEvidencePanel(studioState.lastReport);
      });
      restoreLiveWorkspaceButton?.addEventListener("click", () => {
        hydratePersistedLiveWorkspace({manual: true});
      });
      clearLiveWorkspaceButton?.addEventListener("click", () => {
        clearPersistedLiveWorkspace();
        renderScmEvidencePanel(studioState.lastReport);
      });

      validateButton?.addEventListener("click", validateSource);
      refreshScmEvidenceButton?.addEventListener("click", () => {
        runScmRuntimeChecks();
        renderScmEvidencePanel(studioState.lastReport);
        setStatus("SCM evidence summary refreshed without leaving the active editor pane.");
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
        normalizeScmReceiptVector,
        ingestScmReceiptVector,
        collectScmReceiptVector,
        buildScmEvidenceDebugPacket,
        exportScmEvidenceDebugPacket,
        copyCurrentScmEvidenceDebugPacket,
        downloadCurrentScmEvidenceDebugPacket,
        buildScmAiRepairPrompt,
        exportScmAiRepairPrompt,
        copyCurrentScmAiRepairPrompt,
        buildScmReplaySnapshot,
        compareScmReplaySnapshots,
        formatScmReplayComparisonDetail,
        persistLiveWorkspaceFromSource,
        hydratePersistedLiveWorkspace,
        clearPersistedLiveWorkspace,
        collectLiveWorkspacePersistenceSummary,
        renderLiveWorkspacePersistenceStatus,
        buildScmContractAuthoringHelper,
        formatScmContractAuthoringHelper,
        exportScmContractAuthoringHelper,
        copyCurrentScmContractAuthoringHelper,
        buildFlagshipInspectorModel,
        renderFlagshipInspector,
        setInspectorPanel,
        renderScmEvidencePanel,
        ensureCodeStudioScmSurfaceStyles,
        prepareFlagshipWorkbenchRegions,
        getScmInstance() {
          return syncScmInstance();
        },
        getScmRouteInstance() {
          return syncScmRouteInstance({enter: false});
        },
      };

      prepareFlagshipWorkbenchRegions();
      ensureCodeStudioScmSurfaceStyles();
      hydratePersistedLiveWorkspace();
      syncLineGutter();
      validateSource();
      renderRuntime();
      serializeCleanSource();
      showPane("source");
      setRuntimeLabel();
      renderFlagshipInspector(studioState.lastReport);
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
