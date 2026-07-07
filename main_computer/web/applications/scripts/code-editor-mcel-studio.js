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
        "monaco",
        "layout",
        "style",
        "serialization",
        "repair"
      ];
      const SCM_EVIDENCE_PACKET_VERSION = "1.0.0";
      const SCM_AI_REPAIR_PROMPT_VERSION = "1.0.0";
      const SCM_REPLAY_SNAPSHOT_VERSION = "1.0.0";
      const SCM_REGRESSION_HARNESS_VERSION = "1.0.0";
      const SCM_REPLAY_FIXTURE_HARNESS_VERSION = "1.2.0";
      const SCM_DRAFT_PROVENANCE_VERSION = "1.0.0";
      const SCM_CONTRACT_AUTHORING_HELPER_VERSION = "1.0.0";
      const LIVE_WORKSPACE_PERSISTENCE_VERSION = "1.0.0";
      const MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION = "18N-MCEL-j";
      const MCEL_PROOF_DOCK_UNIFICATION_VERSION = "18N-MCEL-j";
      const MONACO_RUNTIME_EFFECTS = [
        "editor.monaco.load",
        "editor.monaco.mount",
        "editor.monaco.change",
        "editor.monaco.layoutObserved",
        "editor.monaco.dispose"
      ];
      const EDITOR_DRAFT_PROVENANCE_EFFECTS = [
        "editorDraft.created",
        "editorDraft.changed",
        "editorDraft.restored",
        "editorDraft.committed",
        "editorDraft.discarded"
      ];
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
        lastScmRegressionHarness: null,
        lastScmRegressionHarnessExport: null,
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
        monacoMounted: false,
        monacoRuntimeReceipts: [],
        lastMonacoRuntimeReceipt: null,
        lastMonacoRuntimeEffectGate: null,
        editorDraftProvenance: {
          currentDraftId: "",
          currentDraftKey: "",
          sequence: 0,
          events: []
        },
        lastEditorDraftProvenanceReceipt: null,
        lastEditorDraftProvenanceEffectGate: null,
        lastCodeStudioCommitBoundary: null,
        codeStudioCommitBoundaryReceipts: [],
        codeStudioCommitBoundarySequence: 0,
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
        const beforePersistenceSourceHash = hashRegressionString(sourceEditor.value || "");
        const persistenceBoundary = buildMcelCodeStudioCommitBoundary({
          action: "codeStudio.persistLiveWorkspace",
          draftText: sourceEditor.value || "",
          reason,
          gates: {saveGate, loaderGate},
          phase: "persistence-preflight",
          intendedWrites: [
            `localStorage.${LIVE_WORKSPACE_PERSISTENCE_KEY}`,
            "runtime.liveWorkspacePersistence",
            "runtime.evidenceStrip"
          ],
          beforeSourceHash: beforePersistenceSourceHash,
          blockers: []
        });
        recordMcelCodeStudioCommitBoundary(persistenceBoundary);
        const ok = saveGate.ok !== false && loaderGate.ok !== false && persistenceBoundary.canCommit === true;
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
          routeLoaderSync: summarizeRouteLoaderPersistenceGate(loaderGate),
          commitBoundary: {
            kind: persistenceBoundary.kind,
            boundaryVersion: persistenceBoundary.boundaryVersion,
            action: persistenceBoundary.action,
            status: persistenceBoundary.status,
            canCommit: persistenceBoundary.canCommit === true,
            receipt: persistenceBoundary.mcelCommitReceipt
          }
        };

        if (ok) {
          try {
            storage.setItem(LIVE_WORKSPACE_PERSISTENCE_KEY, JSON.stringify(record));
            const committedPersistenceBoundary = jsonSafeClone(persistenceBoundary);
            committedPersistenceBoundary.status = "committed";
            committedPersistenceBoundary.mcelCommitReceipt = mcelCodeStudioCommitReceipt({
              draft: persistenceBoundary.mcelCommitDraft,
              provenance: persistenceBoundary.mcelCommitProvenance,
              freshness: persistenceBoundary.mcelCommitFreshness,
              consumerGate: persistenceBoundary.mcelCommitConsumerGate,
              preflight: persistenceBoundary.mcelCommitPreflight,
              mutationExecuted: true,
              beforeSourceHash: beforePersistenceSourceHash,
              afterSourceHash: hashRegressionString(sourceEditor.value || ""),
              reason: `${reason}:localStorage-write`
            });
            summary.commitBoundary.status = committedPersistenceBoundary.status;
            summary.commitBoundary.receipt = committedPersistenceBoundary.mcelCommitReceipt;
            recordMcelCodeStudioCommitBoundary(committedPersistenceBoundary);
          } catch (error) {
            summary.status = "blocked";
            summary.ok = false;
            summary.message = error?.message || "localStorage write failed.";
          }
        }

        studioState.lastLiveWorkspacePersistence = jsonSafeClone(summary);
        renderLiveWorkspacePersistenceStatus(studioState.lastLiveWorkspacePersistence);
        setStatus(summary.ok ? "Live workspace persisted through SCM saveFile effect, route loaders, and MCEL 18N commit boundary." : `Live workspace persistence blocked: ${summary.message || persistenceBoundary.mcelCommitPreflight?.blockers?.join(", ") || saveGate.code || loaderGate.route?.code || "SCM/18N gate failed"}.`);
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
        const rootRect = root.getBoundingClientRect?.() || {height: 0, width: 0};
        const rootComputed = typeof window.getComputedStyle === "function" ? window.getComputedStyle(root) : null;
        const rootHeight = rootRect.height || root.offsetHeight || root.clientHeight || window.innerHeight || 1;
        const rootWidth = rootRect.width || root.offsetWidth || root.clientWidth || window.innerWidth || 0;
        const pageHeight = Math.max(
          document.documentElement?.scrollHeight || 0,
          document.body?.scrollHeight || 0,
          rootHeight
        );
        const rootIsBoundedViewport = rootComputed?.position === "fixed"
          || rootComputed?.overflow === "hidden"
          || root.style?.position === "fixed"
          || root.style?.overflow === "hidden";
        const ownedDocumentHeight = rootIsBoundedViewport
          ? rootHeight
          : Math.max(root.scrollHeight || 0, rootHeight);
        const documentHeight = rootIsBoundedViewport
          ? ownedDocumentHeight
          : Math.max(pageHeight, ownedDocumentHeight);

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
          metrics: {
            rootHeight,
            rootWidth,
            pageHeight,
            ownedDocumentHeight,
            rootPosition: rootComputed?.position || root.style?.position || "",
            rootOverflow: rootComputed?.overflow || root.style?.overflow || "",
            rootIsBoundedViewport
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
        if (String(entry.effectName || "").startsWith("editor.monaco.") || String(entry.effect || "").startsWith("editor.monaco.")) return "monaco";
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

      function summarizeLayoutGateViolations(gates = studioState.lastScmGates || null) {
        const violations = gates?.layout?.violations || gates?.layout?.result?.violations || [];
        return violations.map((violation) => ({
          code: violation.code || "",
          severity: violation.severity || "",
          selector: violation.selector || "",
          property: violation.property || "",
          stateName: violation.stateName || "",
          actual: violation.actual ?? "",
          expected: violation.expected ?? violation.expectedMax ?? "",
          message: violation.message || ""
        }));
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
        const rawTxDraft = proof.txDraftBoundary || proof.runtimeTxDraft || evidence || {};
        const invalidatedBy = Array.isArray(rawTxDraft.invalidatedBy)
          ? rawTxDraft.invalidatedBy
          : (Array.isArray(proof.txDraftProvenance?.invalidatedBy) ? proof.txDraftProvenance.invalidatedBy : []);
        return jsonSafeClone({
          status: proof.runtimeTxDraft?.status || rawTxDraft.status || (proof.checks?.txDraftRuntimeOnly ? "observed" : "not-observed"),
          boundary: boundary || (noSend ? "runtime-only-no-send" : ""),
          noSend,
          probeStatus: {
            nonce: nonce.status || proof.nonceStatus || "",
            gasEstimate: gasEstimate.status || proof.gasStatus || "",
            ethCall: ethCall.status || proof.ethCallStatus || ""
          },
          provenance: {
            provenanceVersion: rawTxDraft.provenanceVersion || proof.txDraftProvenance?.provenanceVersion || "",
            sourceRequestHash: rawTxDraft.sourceRequestHash || proof.txDraftProvenance?.sourceRequestHash || "",
            selectedRequestSnapshot: rawTxDraft.selectedRequestSnapshot || proof.txDraftProvenance?.selectedRequestSnapshot || null,
            walletAccountHash: rawTxDraft.walletAccountHash || proof.txDraftProvenance?.walletAccountHash || "",
            chainProof: rawTxDraft.chainProof || proof.txDraftProvenance?.chainProof || {},
            externalOutcomeSequence: rawTxDraft.externalOutcomeSequence || proof.txDraftProvenance?.externalOutcomeSequence || [],
            networkGateSequence: rawTxDraft.networkGateSequence || proof.txDraftProvenance?.networkGateSequence || [],
            calldataSource: rawTxDraft.calldataSource || proof.txDraftProvenance?.calldataSource || "",
            abiEncodingStatus: rawTxDraft.abiEncodingStatus || proof.txDraftProvenance?.abiEncodingStatus || "",
            probeEnvelopeIds: rawTxDraft.probeEnvelopeIds || proof.txDraftProvenance?.probeEnvelopeIds || [],
            invalidatedBy,
            freshnessStatus: rawTxDraft.freshnessStatus || proof.txDraftProvenance?.freshnessStatus || "",
            freshnessAction: rawTxDraft.freshnessAction || proof.txDraftProvenance?.freshnessAction || "",
            noSendBoundaryPreserved: rawTxDraft.noSendBoundaryPreserved === true || proof.txDraftProvenance?.noSendBoundaryPreserved === true,
            provenanceEnforced: rawTxDraft.provenanceEnforced === true || proof.txDraftProvenance?.provenanceEnforced === true,
            provenanceFreshness: rawTxDraft.provenanceFreshness || proof.txDraftProvenance?.provenanceFreshness || null,
            consumerGate: rawTxDraft.consumerGate || proof.txDraftConsumerGate || proof.txDraftProvenance?.consumerGate || {},
            endgamePreflight: rawTxDraft.endgamePreflight || proof.txDraftEndgamePreflight || proof.txDraftConsumerGate?.endgamePreflight || {},
            valid: rawTxDraft.valid === true || proof.txDraftProvenance?.valid === true
          },
          consumerGate: rawTxDraft.consumerGate || proof.txDraftConsumerGate || proof.txDraftProvenance?.consumerGate || {},
          endgamePreflight: rawTxDraft.endgamePreflight || proof.txDraftEndgamePreflight || proof.txDraftConsumerGate?.endgamePreflight || {},
          raw: rawTxDraft || null
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

      function normalizeMcelCommitBoundaryForReceipt(boundary = null, txDraftBoundary = {}) {
        const source = boundary && typeof boundary === "object" ? boundary : {};
        const draft = source.mcelCommitDraft || source.commitDraft || {};
        const provenance = source.mcelCommitProvenance || source.provenance || {};
        const freshness = source.mcelCommitFreshness || source.freshness || {};
        const consumerGate = source.mcelCommitConsumerGate || source.consumerGate || {};
        const preflight = source.mcelCommitPreflight || source.preflight || txDraftBoundary.endgamePreflight || {};
        const receipt = source.mcelCommitReceipt || source.commitReceipt || {};
        const blockers = uniqueScmReceiptList(preflight.blockers, consumerGate.blockers, freshness.invalidatedBy?.map?.((entry) => entry?.reason || entry));
        const observed = Boolean(
          source.kind
          || draft.kind
          || provenance.kind
          || freshness.kind
          || consumerGate.kind
          || preflight.kind
          || receipt.kind
          || txDraftBoundary.endgamePreflight
        );
        const canSend = source.canSend === true || preflight.canSend === true;
        const canSign = source.canSign === true || preflight.canSign === true;
        const canBroadcast = source.canBroadcast === true || preflight.canBroadcast === true;
        const locked = source.locked !== false && (canSend !== true && canSign !== true && canBroadcast !== true);
        return jsonSafeClone({
          kind: "mcel-code-studio-18n-commit-boundary-summary",
          boundaryKind: source.kind || (observed ? "mcelWalletToolCommitBoundary.v1" : ""),
          boundaryVersion: source.boundaryVersion || receipt.receiptVersion || "",
          action: source.action || draft.action || "wallet.send-sign",
          status: source.status || preflight.status || consumerGate.status || (observed ? "locked" : "not-observed"),
          observed,
          mcelOnly: source.mcelOnly !== false,
          seriousAction: source.seriousAction !== false,
          locked,
          canCommit: preflight.canCommit === true,
          canSend,
          canSign,
          canBroadcast,
          draftKind: draft.kind || "",
          draftId: draft.draftId || "",
          provenanceKind: provenance.kind || "",
          freshnessKind: freshness.kind || "",
          freshnessStatus: freshness.status || "",
          consumerGateKind: consumerGate.kind || "",
          consumerGateStatus: consumerGate.status || "",
          preflightKind: preflight.kind || "",
          preflightStatus: preflight.status || "",
          receiptKind: receipt.kind || "",
          receiptStatus: receipt.status || "",
          mutationExecuted: receipt.mutationExecuted === true,
          blockers,
          allowedActions: uniqueScmReceiptList(source.allowedActions, consumerGate.allowedActions, preflight.allowedActions),
          blockedActions: uniqueScmReceiptList(source.blockedActions, preflight.blockedActions, blockers),
          proofDockSpecimens: source.mcelProofDockSpecimens || source.proofDockSpecimens || null,
          nextAction: source.nextAction || preflight.summary || consumerGate.reason || "",
          invariant: source.invariant || [],
          raw: source
        });
      }

      function summarizeMcelCommitBoundaryForWorkbench(receiptVector = studioState.lastScmReceiptVector) {
        const boundary = receiptVector?.commitBoundary || receiptVector?.mcelCommitBoundary || receiptVector?.walletCommitBoundary || {};
        const observed = boundary.observed === true || Boolean(boundary.boundaryKind || boundary.draftKind || boundary.receiptKind);
        const locked = boundary.locked !== false && boundary.canSend !== true && boundary.canSign !== true && boundary.canBroadcast !== true;
        const status = observed
          ? (boundary.status || (locked ? "locked" : "needs inspection"))
          : "not observed";
        const label = observed
          ? `${status} · ${boundary.action || "wallet.send-sign"} · canSend=${boundary.canSend === true} canSign=${boundary.canSign === true} canBroadcast=${boundary.canBroadcast === true}`
          : "not observed";
        return jsonSafeClone({
          kind: "mcel-code-studio-18n-commit-boundary-workbench-summary",
          observed,
          status,
          label,
          action: boundary.action || "wallet.send-sign",
          locked,
          canCommit: boundary.canCommit === true,
          canSend: boundary.canSend === true,
          canSign: boundary.canSign === true,
          canBroadcast: boundary.canBroadcast === true,
          draftKind: boundary.draftKind || "",
          provenanceKind: boundary.provenanceKind || "",
          freshnessKind: boundary.freshnessKind || "",
          freshnessStatus: boundary.freshnessStatus || "",
          consumerGateStatus: boundary.consumerGateStatus || "",
          preflightStatus: boundary.preflightStatus || "",
          receiptStatus: boundary.receiptStatus || "",
          mutationExecuted: boundary.mutationExecuted === true,
          blockers: boundary.blockers || [],
          allowedActions: boundary.allowedActions || [],
          blockedActions: boundary.blockedActions || [],
          proofDockSpecimens: boundary.proofDockSpecimens || null,
          nextAction: boundary.nextAction || (observed ? "inspect MCEL 18N preflight/receipt" : "run MCEL Lab wallet proof"),
          raw: boundary
        });
      }

      function mcelProofDockBoundaryParts(boundary = {}) {
        const draft = boundary.mcelCommitDraft || boundary.commitDraft || {};
        const provenance = boundary.mcelCommitProvenance || boundary.provenance || {};
        const freshness = boundary.mcelCommitFreshness || boundary.freshness || {};
        const consumerGate = boundary.mcelCommitConsumerGate || boundary.consumerGate || {};
        const preflight = boundary.mcelCommitPreflight || boundary.preflight || {};
        const receipt = boundary.mcelCommitReceipt || boundary.commitReceipt || boundary.walletBlockedAttemptReceipt || {};
        const blockers = uniqueScmReceiptList(
          boundary.blockers,
          preflight.blockers,
          consumerGate.blockers,
          freshness.blockers,
          (freshness.invalidatedBy || []).map((entry) => entry?.reason || entry)
        );
        const allowedActions = uniqueScmReceiptList(boundary.allowedActions, consumerGate.allowedActions, preflight.allowedActions);
        const blockedActions = uniqueScmReceiptList(boundary.blockedActions, preflight.blockedActions, blockers);
        return {draft, provenance, freshness, consumerGate, preflight, receipt, blockers, allowedActions, blockedActions};
      }

      function mcelProofDockCommitBoundarySpecimen({
        specimen = "unknown",
        action = "unknown",
        label = "",
        boundary = {},
        receipt = null,
        source = "code-studio",
        locked = null,
        fallbackStatus = "not-observed",
        blockedActions = []
      } = {}) {
        const parts = mcelProofDockBoundaryParts(boundary);
        const draft = parts.draft;
        const provenance = parts.provenance;
        const freshness = parts.freshness;
        const consumerGate = parts.consumerGate;
        const preflight = parts.preflight;
        const commitReceipt = receipt || parts.receipt || {};
        const observed = Boolean(boundary.kind || draft.kind || commitReceipt.kind || receipt);
        const mergedBlockers = uniqueScmReceiptList(parts.blockers, blockedActions, commitReceipt.blockers);
        const mergedAllowedActions = uniqueScmReceiptList(parts.allowedActions, commitReceipt.allowedActions);
        const mergedBlockedActions = uniqueScmReceiptList(blockedActions, parts.blockedActions, mergedBlockers);
        const computedLocked = locked === null
          ? (boundary.locked === true || (action.startsWith("wallet.") && boundary.canSend !== true && boundary.canSign !== true && boundary.canBroadcast !== true))
          : locked === true;
        return jsonSafeClone({
          kind: "mcelProofDockCommitBoundarySpecimen.v1",
          proofDockVersion: MCEL_PROOF_DOCK_UNIFICATION_VERSION,
          source,
          specimen,
          action,
          label: label || specimen,
          observed,
          mcelOnly: true,
          seriousAction: true,
          locked: computedLocked,
          status: boundary.status || preflight.status || consumerGate.status || commitReceipt.status || fallbackStatus,
          draft: {
            kind: draft.kind || boundary.draftKind || "",
            id: draft.draftId || boundary.draftId || commitReceipt.draftId || "",
            status: draft.status || boundary.status || ""
          },
          provenance: {
            kind: provenance.kind || boundary.provenanceKind || "",
            status: provenance.provenanceEnforced === false ? "missing" : (provenance.kind || boundary.provenanceKind ? "recorded" : "not-observed"),
            sourceHash: provenance.sourceHash || commitReceipt.sourceHash || "",
            targetHash: provenance.targetHash || ""
          },
          freshness: {
            kind: freshness.kind || boundary.freshnessKind || "",
            status: freshness.status || boundary.freshnessStatus || commitReceipt.freshnessStatus || "not-observed",
            invalidatedBy: freshness.invalidatedBy || []
          },
          consumerGate: {
            kind: consumerGate.kind || boundary.consumerGateKind || "",
            status: consumerGate.status || boundary.consumerGateStatus || commitReceipt.consumerGateStatus || "not-observed",
            allowedActions: mergedAllowedActions
          },
          preflight: {
            kind: preflight.kind || boundary.preflightKind || "",
            status: preflight.status || boundary.preflightStatus || commitReceipt.preflightStatus || "not-observed",
            canCommit: preflight.canCommit === true || boundary.canCommit === true,
            canSend: boundary.canSend === true || preflight.canSend === true,
            canSign: boundary.canSign === true || preflight.canSign === true,
            canBroadcast: boundary.canBroadcast === true || preflight.canBroadcast === true
          },
          receipt: {
            kind: commitReceipt.kind || boundary.receiptKind || "",
            status: commitReceipt.status || boundary.receiptStatus || "not-observed",
            receiptId: commitReceipt.receiptId || "",
            mutationExecuted: commitReceipt.mutationExecuted === true || boundary.mutationExecuted === true
          },
          unlockRequirements: {
            kind: boundary.walletUnlockRequirements?.kind || "",
            status: boundary.walletUnlockRequirements?.status || "",
            readyForProviderExecution: boundary.walletUnlockRequirements?.readyForProviderExecution === true,
            missing: boundary.walletUnlockRequirements?.missing || []
          },
          finalLockedSpecimen: {
            kind: boundary.walletFinalLockedSpecimen?.kind || "",
            status: boundary.walletFinalLockedSpecimen?.status || "",
            mutationExecuted: boundary.walletFinalLockedSpecimen?.mutationExecuted === true
          },
          allowedActions: mergedAllowedActions,
          blockedActions: mergedBlockedActions,
          blockers: mergedBlockers,
          nextAction: boundary.nextAction || preflight.summary || consumerGate.reason || "inspect MCEL 18N proof dock specimen",
          invariant: [
            "Unified MCEL proof dock specimens expose draft, provenance, freshness, consumer gate, preflight, and receipt.",
            "Code Studio and wallet specimens share one proof-dock shape.",
            "Wallet send/sign/broadcast remain locked.",
            "Wallet unlock requirements remain incomplete until a separate explicit unlock design patch."

          ]
        });
      }

      function latestCodeStudioCommitBoundaryForAction(action) {
        if (studioState.lastCodeStudioCommitBoundary?.action === action) return studioState.lastCodeStudioCommitBoundary;
        const receipt = [...(studioState.codeStudioCommitBoundaryReceipts || [])].reverse().find((entry) => entry.action === action);
        if (!receipt) return null;
        return {
          kind: "mcelCodeStudioCommitBoundary.v1",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          action,
          status: receipt.status || "not-observed",
          locked: false,
          canCommit: receipt.committed === true || receipt.status === "committed",
          canSend: false,
          canSign: false,
          canBroadcast: false,
          mcelCommitReceipt: receipt,
          mcelCommitPreflight: {
            kind: "mcelCommitPreflight.v1",
            status: receipt.preflightStatus || "not-observed",
            canCommit: receipt.committed === true,
            canSend: false,
            canSign: false,
            canBroadcast: false,
            blockers: receipt.blockers || []
          },
          mcelCommitConsumerGate: {
            kind: "mcelCommitConsumerGate.v1",
            status: receipt.consumerGateStatus || "not-observed",
            allowedActions: receipt.committed === true ? [`${action}-with-receipt`] : ["inspect-18n-preflight"],
            blockers: receipt.blockers || []
          },
          mcelCommitFreshness: {
            kind: "mcelCommitFreshness.v1",
            status: receipt.freshnessStatus || "not-observed",
            invalidatedBy: []
          },
          mcelCommitDraft: {
            kind: "mcelCommitDraft.v1",
            action,
            draftId: receipt.draftId || ""
          },
          mcelCommitProvenance: {
            kind: "mcelCommitProvenance.v1",
            sourceHash: receipt.sourceHash || "",
            provenanceEnforced: Boolean(receipt.sourceHash || receipt.draftId)
          }
        };
      }

      function collectMcelProofDockUnifiedSpecimens(receiptVector = studioState.lastScmReceiptVector) {
        const vector = receiptVector || normalizeScmReceiptVector(null);
        const walletBoundary = vector?.commitBoundary || vector?.mcelCommitBoundary || vector?.walletCommitBoundary || {};
        const walletSourceSpecimens = vector?.mcelProofDockSpecimens || vector?.proofDockSpecimens || walletBoundary.proofDockSpecimens || null;
        const walletBlockedActions = ["wallet.send", "wallet.sign", "wallet.broadcast"];
        const walletSpecimens = [
          mcelProofDockCommitBoundarySpecimen({
            specimen: "wallet.txDraft",
            action: "wallet.txDraft",
            label: "Wallet txDraft",
            boundary: walletBoundary.raw || walletBoundary,
            source: "mcel-lab.wallet-tool.txDraft",
            locked: true,
            fallbackStatus: walletBoundary.status || "locked"
          }),
          mcelProofDockCommitBoundarySpecimen({
            specimen: "wallet.blockedSend",
            action: "wallet.blockedSend",
            label: "Blocked wallet send",
            boundary: walletBoundary.raw || walletBoundary,
            source: "mcel-lab.wallet-tool.blockedSend",
            locked: true,
            blockedActions: walletBlockedActions,
            fallbackStatus: walletBoundary.status || "locked"
          }),
          mcelProofDockCommitBoundarySpecimen({
            specimen: "wallet.blockedSign",
            action: "wallet.blockedSign",
            label: "Blocked wallet sign",
            boundary: walletBoundary.raw || walletBoundary,
            source: "mcel-lab.wallet-tool.blockedSign",
            locked: true,
            blockedActions: walletBlockedActions,
            fallbackStatus: walletBoundary.status || "locked"
          }),
          mcelProofDockCommitBoundarySpecimen({
            specimen: "wallet.blockedBroadcast",
            action: "wallet.blockedBroadcast",
            label: "Blocked wallet broadcast",
            boundary: walletBoundary.raw || walletBoundary,
            source: "mcel-lab.wallet-tool.blockedBroadcast",
            locked: true,
            blockedActions: walletBlockedActions,
            fallbackStatus: walletBoundary.status || "locked"
          })
        ];
        const codeStudioActions = [
          ["codeStudio.runtimeMount", "codeStudio.mountRuntimeDraft", "Code Studio runtime mount"],
          ["codeStudio.editorDraftCommit", "codeStudio.commitRuntimeDraft", "Code Studio editor draft commit"],
          ["codeStudio.workspacePersist", "codeStudio.persistLiveWorkspace", "Code Studio workspace persistence"]
        ];
        const codeStudioSpecimens = codeStudioActions.map(([specimen, action, label]) => {
          const boundary = latestCodeStudioCommitBoundaryForAction(action) || {
            kind: "mcelCodeStudioCommitBoundary.v1",
            boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
            action,
            status: "not-observed",
            locked: false,
            canCommit: false,
            canSend: false,
            canSign: false,
            canBroadcast: false
          };
          return mcelProofDockCommitBoundarySpecimen({
            specimen,
            action,
            label,
            boundary,
            source: "code-studio.bottom-proof-dock",
            locked: false,
            fallbackStatus: boundary.status || "not-observed"
          });
        });
        const specimens = [...codeStudioSpecimens, ...walletSpecimens];
        const blockers = uniqueScmReceiptList(...specimens.map((entry) => entry.blockers || []));
        return jsonSafeClone({
          kind: "mcelProofDockUnifiedSpecimens.v1",
          proofDockVersion: MCEL_PROOF_DOCK_UNIFICATION_VERSION,
          source: "code-studio.bottom-proof-dock",
          sourceVectorKind: vector?.sourceKind || "not-ingested",
          specimenCount: specimens.length,
          codeStudioSpecimenCount: codeStudioSpecimens.length,
          walletSpecimenCount: walletSpecimens.length,
          walletLocked: walletSpecimens.every((entry) => entry.locked === true && entry.preflight.canSend !== true && entry.preflight.canSign !== true && entry.preflight.canBroadcast !== true),
          walletUnlockStatus: walletBoundary.walletUnlockRequirements?.status || "incomplete",
          walletFinalLockedSpecimenStatus: walletBoundary.walletFinalLockedSpecimen?.finalStatus || "locked",
          mutationExecutedCount: specimens.filter((entry) => entry.receipt.mutationExecuted === true).length,
          blockedCount: specimens.filter((entry) => (entry.blockers || []).length || String(entry.status || "").includes("blocked") || entry.locked === true).length,
          blockers,
          sourceWalletProofDockSpecimens: walletSourceSpecimens,
          codeStudioSpecimens,
          walletSpecimens,
          specimens,
          invariant: [
            "codeStudio.runtimeMount, codeStudio.editorDraftCommit, codeStudio.workspacePersist, wallet.txDraft, wallet.blockedSend, wallet.blockedSign, and wallet.blockedBroadcast share one proof dock model.",
            "Every unified specimen reports draft, provenance, freshness, consumer gate, preflight, receipt, allowed actions, and blocked actions.",
            "Wallet send/sign/broadcast remain locked while Code Studio runtime-only mutations may be receipted."
          ]
        });
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
        const commitBoundary = normalizeMcelCommitBoundaryForReceipt(
          proof.mcelCommitBoundary || proof.walletCommitBoundary || proof.commitBoundary,
          txDraftBoundary
        );
        const mcelProofDockSpecimens = proof.mcelProofDockSpecimens
          || proof.proofDockSpecimens
          || commitBoundary.proofDockSpecimens
          || proof.walletCommitBoundary?.mcelProofDockSpecimens
          || null;
        const nextAction = proof.nextAction || commitBoundary.nextAction || inferLabReceiptNextAction(selectedEffect, actionOutcome, externalOutcome, declaration);

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
          commitBoundary,
          mcelCommitBoundary: commitBoundary,
          walletCommitBoundary: commitBoundary,
          mcelProofDockSpecimens,
          proofDockSpecimens: mcelProofDockSpecimens,
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
            provenance: {
              provenanceVersion: "",
              sourceRequestHash: "",
              selectedRequestSnapshot: null,
              walletAccountHash: "",
              chainProof: {},
              externalOutcomeSequence: [],
              networkGateSequence: [],
              calldataSource: "",
              abiEncodingStatus: "",
              probeEnvelopeIds: [],
              invalidatedBy: [],
              valid: false
            },
            raw: null
          },
          commitBoundary: normalizeMcelCommitBoundaryForReceipt(null, {}),
          mcelCommitBoundary: normalizeMcelCommitBoundaryForReceipt(null, {}),
          walletCommitBoundary: normalizeMcelCommitBoundaryForReceipt(null, {}),
          mcelProofDockSpecimens: {
            kind: "mcelProofDockUnifiedSpecimens.v1",
            proofDockVersion: MCEL_PROOF_DOCK_UNIFICATION_VERSION,
            source: "not-ingested",
            specimenCount: 0,
            specimens: []
          },
          proofDockSpecimens: {
            kind: "mcelProofDockUnifiedSpecimens.v1",
            proofDockVersion: MCEL_PROOF_DOCK_UNIFICATION_VERSION,
            source: "not-ingested",
            specimenCount: 0,
            specimens: []
          },
          draftProvenance: {
            status: "not-observed",
            activeDraftId: "",
            eventType: "",
            sourceMutationGate: "commitDraft",
            runtimeOnlyUntilCommit: true,
            sourceMutationsOnlyByCommitDraft: true,
            events: []
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

      function scmReceiptSelectedEvidenceKey(selectedEvidence = null) {
        if (!selectedEvidence || selectedEvidence.phase === "idle") return "";
        return String(
          selectedEvidence.evidenceKey
          || selectedEvidence.effectName
          || selectedEvidence.effect
          || selectedEvidence.phase
          || ""
        ).trim();
      }

      function scmReceiptSelectedEffectName(selectedEvidence = null) {
        return String(
          selectedEvidence?.effectName
          || selectedEvidence?.effect
          || selectedEvidence?.selectedEffect
          || ""
        ).trim();
      }

      function labelScmReceiptSourceAuthority(authority = "not-ingested") {
        const labels = {
          "selected-evidence": "selected SCM evidence",
          "live-report": "live validation report",
          "component-packet": "component evidence packet",
          "route-packet": "route evidence packet",
          "lab-dom": "Lab DOM receipt",
          "imported": "imported receipt vector",
          "cached": "cached previous vector",
          "not-ingested": "not ingested"
        };
        return labels[authority] || authority || "not ingested";
      }

      function buildScmReceiptSourceAuthority(vector = null, descriptor = {}, selectedEvidence = null) {
        const sourceKind = vector?.sourceKind || "not-ingested";
        const authority = sourceKind === "not-ingested"
          ? "not-ingested"
          : (descriptor.authority || "unknown");
        const selectedEvidenceKey = scmReceiptSelectedEvidenceKey(selectedEvidence);
        const selectedEvidenceEffect = scmReceiptSelectedEffectName(selectedEvidence);
        const vectorEffect = String(vector?.selectedEffect || "").trim();
        const reasons = [];

        let freshness = descriptor.freshness || "current";
        let current = sourceKind !== "not-ingested";
        if (sourceKind === "not-ingested") {
          freshness = "not ingested";
          current = false;
          reasons.push("No Lab/SCM receipt vector has been ingested for the current workbench selection.");
        } else if (authority === "cached") {
          freshness = "stale";
          current = false;
          reasons.push("Using the previous normalized receipt vector because no current receipt source was found.");
        } else if (selectedEvidenceEffect && vectorEffect && selectedEvidenceEffect !== vectorEffect) {
          freshness = "stale";
          current = false;
          reasons.push(`Selected evidence effect ${selectedEvidenceEffect} does not match receipt vector effect ${vectorEffect}.`);
        } else if (authority === "lab-dom") {
          freshness = "external";
          current = false;
          reasons.push("Receipt came from Lab DOM evidence, not the selected Code Studio SCM evidence.");
        } else if (descriptor.ambiguous === true) {
          freshness = "needs verification";
          current = false;
          reasons.push("Receipt source was inferred from a fallback candidate.");
        }

        const label = sourceKind === "not-ingested"
          ? "not ingested"
          : `${labelScmReceiptSourceAuthority(authority)} · ${freshness}`;

        return jsonSafeClone({
          kind: "mcel-code-studio-receipt-source-authority",
          authority,
          sourceKind,
          label,
          freshness,
          current,
          stale: current === false && sourceKind !== "not-ingested",
          staleReason: reasons,
          selectedEvidenceKey,
          selectedEvidenceEffect,
          selectedEvidencePhase: selectedEvidence?.phase || "",
          selectedEvidenceScope: selectedEvidence ? evidenceEntryScope(selectedEvidence) : "",
          vectorEffect,
          candidateRank: descriptor.rank ?? null,
          candidateField: descriptor.field || "",
          guidance: sourceKind === "not-ingested"
            ? "Run or import a Lab/SCM receipt before trusting receipt-vector proof data."
            : (current
              ? "Receipt vector is tied to the current explicit workbench source."
              : "Refresh evidence, select the matching SCM evidence entry, or open a current Lab receipt before trusting this vector.")
        });
      }

      function attachScmReceiptSourceAuthority(vector = null, descriptor = {}, selectedEvidence = null) {
        const safeVector = jsonSafeClone(vector || normalizeScmReceiptVector(null));
        safeVector.receiptSource = buildScmReceiptSourceAuthority(safeVector, descriptor, selectedEvidence);
        return safeVector;
      }

      function summarizeScmReceiptSourceForWorkbench(receiptVector = studioState.lastScmReceiptVector) {
        const vector = receiptVector || normalizeScmReceiptVector(null);
        const source = vector.receiptSource || buildScmReceiptSourceAuthority(vector, {}, null);
        const reason = (source.staleReason || []).join(" ");
        return jsonSafeClone({
          kind: "mcel-code-studio-receipt-source-workbench-summary",
          authority: source.authority || "not-ingested",
          sourceKind: source.sourceKind || vector.sourceKind || "not-ingested",
          label: source.label || "not ingested",
          freshness: source.freshness || "not ingested",
          current: source.current === true,
          stale: source.stale === true,
          staleReason: source.staleReason || [],
          reason,
          selectedEvidenceKey: source.selectedEvidenceKey || "",
          selectedEvidenceEffect: source.selectedEvidenceEffect || "",
          vectorEffect: source.vectorEffect || vector.selectedEffect || "",
          guidance: source.guidance || ""
        });
      }

      function ingestScmReceiptVector(input, options = {}) {
        const vector = normalizeScmReceiptVector(input, options);
        const sourced = attachScmReceiptSourceAuthority(vector, {
          authority: options.authority || "imported",
          field: options.field || "ingestScmReceiptVector",
          freshness: options.freshness || "current"
        }, options.selectedEvidence || null);
        studioState.lastScmReceiptVector = sourced.sourceKind === "not-ingested" ? null : sourced;
        return sourced;
      }

      function collectScmReceiptVector(report = studioState.lastReport, summary = null, selectedEvidence = null) {
        const evidenceSummary = summary || collectScmEvidenceSummary(report);
        const candidates = [
          {authority: "selected-evidence", field: "selectedEvidence.receiptVector", value: selectedEvidence?.receiptVector},
          {authority: "selected-evidence", field: "selectedEvidence.proof", value: selectedEvidence?.proof},
          {authority: "selected-evidence", field: "selectedEvidence.rawReceipt", value: selectedEvidence?.rawReceipt},
          {authority: "live-report", field: "report.receiptVector", value: report?.receiptVector},
          {authority: "live-report", field: "report.labReceipt", value: report?.labReceipt},
          {authority: "live-report", field: "report.mcelLabReceipt", value: report?.mcelLabReceipt},
          {authority: "component-packet", field: "componentPacket.receiptVector", value: evidenceSummary?.componentPacket?.receiptVector},
          {authority: "component-packet", field: "componentPacket.labReceipt", value: evidenceSummary?.componentPacket?.labReceipt},
          {authority: "route-packet", field: "routePacket.receiptVector", value: evidenceSummary?.routePacket?.receiptVector},
          {authority: "lab-dom", field: "#mcel-tiny-contract-evidence", value: findMcelLabReceiptPayload()},
          {authority: "cached", field: "studioState.lastScmReceiptVector", value: studioState.lastScmReceiptVector, freshness: "stale"}
        ];
        for (const [index, candidate] of candidates.entries()) {
          const vector = normalizeScmReceiptVector(candidate.value, {selectedEvidence});
          if (vector.sourceKind !== "not-ingested") {
            const sourced = attachScmReceiptSourceAuthority(vector, {
              authority: candidate.authority,
              field: candidate.field,
              rank: index + 1,
              freshness: candidate.freshness || "current"
            }, selectedEvidence);
            if (candidate.authority !== "cached") {
              studioState.lastScmReceiptVector = sourced;
            }
            return sourced;
          }
        }
        return attachScmReceiptSourceAuthority(normalizeScmReceiptVector(null, {selectedEvidence}), {
          authority: "not-ingested",
          field: "none",
          freshness: "not ingested"
        }, selectedEvidence);
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

      function formatScmDispositionCounts(counts = {}) {
        const order = ["PASS", "BLOCKED", "EXCEPTION", "FAIL", "MISMATCH"];
        return order
          .map((key) => `${key} ${counts?.[key] || 0}`)
          .join(" · ");
      }

      function summarizeScmReplayComparisonForWorkbench(comparison = studioState.lastScmReplaySnapshotComparison) {
        if (!comparison) {
          return jsonSafeClone({
            kind: "mcel-code-studio-replay-workbench-summary",
            state: "not run",
            label: "not run",
            selectedEvidenceLabel: "none",
            deltaSummary: "no replay captured",
            issueRows: ["Replay selected gate to capture before/after SCM evidence snapshots."]
          });
        }

        const deltas = comparison.deltas || {};
        const gateChanges = Array.isArray(deltas.gateChanges) ? deltas.gateChanges : [];
        const issueRows = [];
        if (comparison.ok === false) issueRows.push("Replay gate result failed or SCM gates were not ok after replay.");
        if ((deltas.violations || 0) > 0) issueRows.push(`violations increased by ${deltas.violations}`);
        if ((deltas.blocking || 0) > 0) issueRows.push(`blocking evidence increased by ${deltas.blocking}`);
        if (gateChanges.length) issueRows.push(`${gateChanges.length} gate flag change(s) after replay`);
        if (!issueRows.length && comparison.stable) issueRows.push("Replay snapshot stayed stable; no violation, blocking, or gate deltas increased.");

        const state = comparison.stable ? "stable" : (comparison.ok === false ? "failed" : "changed");
        const selectedEvidenceLabel = evidenceEntryLabel(comparison.selectedEvidence || {});
        const deltaSummary = `total ${deltas.total || 0} · violations ${deltas.violations || 0} · blocking ${deltas.blocking || 0} · gate changes ${gateChanges.length}`;
        return jsonSafeClone({
          kind: "mcel-code-studio-replay-workbench-summary",
          state,
          label: comparison.stable ? "PASS stable replay" : `${state.toUpperCase()} replay needs inspection`,
          selectedEvidenceLabel: selectedEvidenceLabel || "selected evidence",
          ok: comparison.ok !== false,
          stable: comparison.stable === true,
          deltaSummary,
          issueRows,
          deltas
        });
      }

      function hashRegressionString(value = "") {
        const text = String(value || "");
        let hash = 2166136261;
        for (let index = 0; index < text.length; index += 1) {
          hash ^= text.charCodeAt(index);
          hash = Math.imul(hash, 16777619);
        }
        return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
      }

      function buildScmRegressionSourceSnapshot(label = "snapshot") {
        const source = sourceEditor.value || "";
        const fields = workspaceFields();
        const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
        const host = runtimePreview.querySelector("#code-studio-runtime-monaco");
        const activePane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "";
        return jsonSafeClone({
          kind: "mcel-code-studio-scm-regression-source-snapshot",
          harnessVersion: SCM_REGRESSION_HARNESS_VERSION,
          label,
          capturedAt: new Date().toISOString(),
          selectedPath: studioState.selectedPath,
          selectedFileId: selectedScmFileId(fields),
          sourceLength: source.length,
          sourceHash: hashRegressionString(source),
          sourceContainsRuntimeChrome: source.includes('data-mc-generated="runtime"') || source.includes('data-mc-serialize="omit"'),
          runtimeDraftMounted: Boolean(draft),
          runtimeDraftLength: draft?.value?.length || 0,
          monacoHostMounted: Boolean(host),
          monacoOutcome: host?.dataset?.monacoOutcome || runtimePreview.querySelector(".code-studio-runtime-editor")?.dataset?.monacoOutcome || "",
          dirty: studioState.dirty,
          damaged: studioState.damaged,
          activePane
        });
      }

      function compareScmRegressionSourceSnapshots(before, after) {
        return {
          sourceUnchanged: before?.sourceHash === after?.sourceHash && before?.sourceLength === after?.sourceLength,
          runtimeChromeStayedOutOfSource: after?.sourceContainsRuntimeChrome === false,
          selectedPathStable: before?.selectedPath === after?.selectedPath,
          beforeHash: before?.sourceHash || "",
          afterHash: after?.sourceHash || ""
        };
      }

      function editorDraftProvenanceEffectName(eventType = "changed") {
        const normalized = String(eventType || "changed").trim();
        const effectName = `editorDraft.${normalized}`;
        return EDITOR_DRAFT_PROVENANCE_EFFECTS.includes(effectName) ? effectName : "editorDraft.changed";
      }

      function selectedEditorDraftSourceSnapshot(fields = workspaceFields()) {
        const file = selectedFile(fields);
        const sourceText = file?.value || "";
        return {
          selectedPath: file?.path || studioState.selectedPath || "",
          selectedFileId: selectedScmFileId(fields),
          language: file?.language || "plaintext",
          sourceHash: hashRegressionString(sourceText),
          sourceLength: sourceText.length
        };
      }

      function runEditorDraftProvenanceScmEffect(receipt) {
        const effectName = receipt?.effect || editorDraftProvenanceEffectName(receipt?.eventType);
        return runScmGate(`effect:${effectName}`, (mcel, instance) => mcel.runEffect(instance, effectName, receipt));
      }

      function recordEditorDraftProvenance(eventType = "changed", context = {}) {
        const fields = workspaceFields();
        const sourceSnapshot = selectedEditorDraftSourceSnapshot(fields);
        const text = String(context.text ?? runtimePreview.querySelector("#code-studio-runtime-draft")?.value ?? "");
        const effect = editorDraftProvenanceEffectName(eventType);
        const sequence = (studioState.editorDraftProvenance.sequence || 0) + 1;
        const baseSourceHash = context.baseSourceHash || sourceSnapshot.sourceHash;
        const draftKey = context.draftKey || `${sourceSnapshot.selectedPath || "unknown"}@${baseSourceHash}`;
        const draftId = context.draftId
          || studioState.editorDraftProvenance.currentDraftId
          || `editor-draft:${draftKey}`;
        const sourceChanged = context.sourceChanged === true;
        const receipt = jsonSafeClone({
          kind: "mcel-code-studio-editor-draft-provenance-receipt",
          provenanceVersion: SCM_DRAFT_PROVENANCE_VERSION,
          eventId: `editor-draft-provenance-${String(sequence).padStart(4, "0")}`,
          eventType: String(eventType || "changed"),
          effect,
          actionOutcome: "pass",
          governanceOutcome: "pass",
          safetyOutcome: "pass",
          origin: context.origin || "runtime-editor",
          draftId,
          draftKey,
          selectedPath: sourceSnapshot.selectedPath,
          selectedFileId: sourceSnapshot.selectedFileId,
          language: sourceSnapshot.language,
          sourceSnapshotHash: baseSourceHash,
          sourceSnapshotLength: sourceSnapshot.sourceLength,
          beforeSourceHash: context.beforeSourceHash || baseSourceHash,
          afterSourceHash: context.afterSourceHash || (sourceChanged ? hashRegressionString(sourceEditor.value || "") : baseSourceHash),
          draftHash: hashRegressionString(text),
          textLength: text.length,
          sourceChanged,
          sourceMutationGate: "commitDraft",
          runtimeOnlyUntilCommit: effect !== "editorDraft.committed",
          serializationExcludedUntilCommit: effect !== "editorDraft.committed",
          declaredReads: context.declaredReads || [
            "source.workspace.files",
            "state.activeFileId",
            "runtime.editorDraft",
            "runtime.evidenceStrip"
          ],
          declaredWrites: context.declaredWrites || (effect === "editorDraft.committed"
            ? ["source.workspace.files", "state.drafts", "state.dirty", "runtime.editorDraftProvenance", "runtime.evidenceStrip"]
            : ["runtime.editorDraft", "runtime.editorDraftProvenance", "state.drafts", "state.dirty", "runtime.evidenceStrip"]),
          forbiddenWrites: effect === "editorDraft.committed" ? [] : ["source.workspace.files"],
          commitBoundaryReceipt: context.commitBoundaryReceipt || null,
          nextAction: context.nextAction || (effect === "editorDraft.committed" ? "render committed source" : "commit draft or discard draft")
        });

        studioState.editorDraftProvenance.sequence = sequence;
        studioState.editorDraftProvenance.currentDraftId = effect === "editorDraft.committed" || effect === "editorDraft.discarded" ? "" : draftId;
        studioState.editorDraftProvenance.currentDraftKey = effect === "editorDraft.committed" || effect === "editorDraft.discarded" ? "" : draftKey;
        studioState.editorDraftProvenance.events = [
          ...studioState.editorDraftProvenance.events.slice(-31),
          receipt
        ];
        studioState.lastEditorDraftProvenanceReceipt = receipt;
        studioState.lastEditorDraftProvenanceEffectGate = runEditorDraftProvenanceScmEffect(receipt);
        return receipt;
      }

      function ensureEditorDraftProvenanceCreated(file, draft, context = {}) {
        if (!file || !draft) return null;
        const sourceHash = hashRegressionString(file.value || "");
        const draftKey = `${file.path || studioState.selectedPath || "unknown"}@${sourceHash}`;
        if (studioState.editorDraftProvenance.currentDraftKey === draftKey && studioState.lastEditorDraftProvenanceReceipt?.eventType !== "discarded") {
          return studioState.lastEditorDraftProvenanceReceipt;
        }
        return recordEditorDraftProvenance("created", {
          origin: context.origin || "runtime-render",
          text: draft.value,
          baseSourceHash: sourceHash,
          draftKey,
          declaredWrites: ["runtime.editorDraft", "runtime.editorDraftProvenance", "runtime.evidenceStrip"],
          nextAction: "edit runtime draft"
        });
      }

      function collectEditorDraftProvenanceSummary() {
        const events = studioState.editorDraftProvenance.events || [];
        const sourceMutationsOnlyByCommitDraft = events.every((event) => !event.sourceChanged || event.effect === "editorDraft.committed" && event.sourceMutationGate === "commitDraft");
        const uncommittedDraftsRuntimeOnly = events.every((event) => event.effect === "editorDraft.committed" || event.runtimeOnlyUntilCommit !== false);
        return jsonSafeClone({
          kind: "mcel-code-studio-editor-draft-provenance-summary",
          provenanceVersion: SCM_DRAFT_PROVENANCE_VERSION,
          activeDraftId: studioState.editorDraftProvenance.currentDraftId || "",
          activeDraftKey: studioState.editorDraftProvenance.currentDraftKey || "",
          totalEvents: events.length,
          lastEvent: studioState.lastEditorDraftProvenanceReceipt,
          recentEvents: events.slice(-8),
          declaredEffects: [...EDITOR_DRAFT_PROVENANCE_EFFECTS],
          sourceMutationGate: "commitDraft",
          invariants: {
            sourceMutationsOnlyByCommitDraft,
            uncommittedDraftsRuntimeOnly,
            serializationExcludedUntilCommit: events.every((event) => event.effect === "editorDraft.committed" || event.serializationExcludedUntilCommit !== false)
          }
        });
      }


      function currentCodeStudioCommitSourceSnapshot(fields = workspaceFields(), draftText = "") {
        const file = selectedFile(fields);
        const routeParams = routeParamsForScm(fields) || {};
        const routeQuery = routeQueryForScm();
        const activePane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "";
        return jsonSafeClone({
          kind: "mcelCodeStudioCommitSourceSnapshot.v1",
          selectedPath: studioState.selectedPath,
          selectedFileId: selectedScmFileId(fields),
          selectedFileHash: hashRegressionString(file?.value || ""),
          selectedFileLength: file?.value?.length || 0,
          sourceHash: hashRegressionString(sourceEditor.value || ""),
          sourceLength: sourceEditor.value.length,
          draftHash: hashRegressionString(draftText),
          draftLength: String(draftText || "").length,
          route: {
            name: window.McelCodeStudioScm?.routeName || "workspace.file",
            params: routeParams,
            query: routeQuery,
            key: currentScmRouteKey(routeParams, routeQuery)
          },
          activePane,
          dirtyState: collectDirtyStateSummary(fields),
          sourceContainsRuntimeChrome: String(sourceEditor.value || "").includes('data-mc-generated="runtime"') || String(sourceEditor.value || "").includes('data-mc-serialize="omit"')
        });
      }

      function mcelCodeStudioCommitDraft({
        action = "codeStudio.commitRuntimeDraft",
        draftText = "",
        reason = "commit-draft",
        intendedWrites = [],
        sourceSnapshot = null,
        selectedFile = null
      } = {}) {
        const fields = workspaceFields();
        const snapshot = sourceSnapshot || currentCodeStudioCommitSourceSnapshot(fields, draftText);
        const file = selectedFile || selectedFileFromFieldsForBoundary(fields);
        const draftPayload = {
          action,
          selectedPath: snapshot.selectedPath,
          selectedFileId: snapshot.selectedFileId,
          sourceHash: snapshot.sourceHash,
          selectedFileHash: snapshot.selectedFileHash,
          draftHash: snapshot.draftHash,
          routeKey: snapshot.route?.key || ""
        };
        return jsonSafeClone({
          kind: "mcelCommitDraft.v1",
          boundarySpecimen: "mcel.code-studio",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          draftId: `mcelCodeStudioCommitDraft:${hashRegressionString(JSON.stringify(draftPayload))}`,
          action,
          seriousAction: true,
          locked: false,
          mcelOnly: true,
          selectedPath: snapshot.selectedPath,
          selectedFileId: snapshot.selectedFileId,
          sourceSnapshotHash: snapshot.sourceHash,
          selectedFileHash: snapshot.selectedFileHash,
          draftHash: snapshot.draftHash,
          draftLength: snapshot.draftLength,
          sourceLength: snapshot.sourceLength,
          route: snapshot.route,
          target: {
            path: file?.path || snapshot.selectedPath || "",
            field: file?.field || "",
            language: file?.language || ""
          },
          intendedWrites,
          proofRefs: [
            studioState.lastEditorDraftProvenanceReceipt?.eventId || "editor-draft-provenance.not-observed",
            studioState.lastScmReceiptVector?.vectorVersion || "receipt-vector.not-ingested",
            studioState.lastSaveFileEffectGate?.resultKind || studioState.lastSaveFileEffectGate?.kind || "saveFile.not-run",
            studioState.lastRouteLoaderPersistenceGate?.label || "route-loaders.not-run"
          ],
          createdFor: reason,
          invariant: [
            "runtime draft intent is explicit before source mutation",
            "selected file, route, source hash, and draft hash are captured",
            "commitRuntimeDraft cannot write source until freshness and SCM gates pass",
            "wallet send/sign/broadcast remains out of scope"
          ]
        });
      }

      function selectedFileFromFieldsForBoundary(fields = workspaceFields()) {
        return fields.files.find((file) => file.path === studioState.selectedPath) || fields.files[0] || null;
      }

      function mcelCodeStudioCommitProvenance({draft = {}, sourceSnapshot = null, currentSnapshot = null} = {}) {
        const snapshot = sourceSnapshot || currentSnapshot || currentCodeStudioCommitSourceSnapshot(workspaceFields(), "");
        return jsonSafeClone({
          kind: "mcelCommitProvenance.v1",
          boundarySpecimen: "mcel.code-studio",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          draftHash: draft.draftId || "",
          sourceHash: snapshot.sourceHash || "",
          targetHash: hashRegressionString(JSON.stringify({
            selectedPath: snapshot.selectedPath || "",
            selectedFileId: snapshot.selectedFileId || "",
            routeKey: snapshot.route?.key || ""
          })),
          sourceSnapshot: snapshot,
          targetSnapshot: {
            selectedPath: snapshot.selectedPath || "",
            selectedFileId: snapshot.selectedFileId || "",
            route: snapshot.route || {}
          },
          provenanceEnforced: Boolean(draft.draftId && snapshot.sourceHash && snapshot.selectedFileId),
          proofRefs: draft.proofRefs || [],
          invariant: [
            "reviewed source snapshot is identified",
            "selected target file is identified",
            "route/key context is identified before mutation"
          ]
        });
      }

      function mcelCodeStudioCommitFreshness({
        draft = {},
        provenance = {},
        currentSnapshot = null,
        expectedDraftHash = "",
        extraInvalidations = []
      } = {}) {
        const current = currentSnapshot || currentCodeStudioCommitSourceSnapshot(workspaceFields(), "");
        const invalidatedBy = [...extraInvalidations.filter(Boolean)];
        if (provenance.sourceHash && current.sourceHash && provenance.sourceHash !== current.sourceHash) {
          invalidatedBy.push({
            reason: "source-file-changed",
            previousSourceHash: provenance.sourceHash,
            currentSourceHash: current.sourceHash
          });
        }
        if (draft.selectedPath && current.selectedPath && draft.selectedPath !== current.selectedPath) {
          invalidatedBy.push({
            reason: "selected-file-changed",
            previousSelectedPath: draft.selectedPath,
            currentSelectedPath: current.selectedPath
          });
        }
        if (draft.selectedFileHash && current.selectedFileHash && draft.selectedFileHash !== current.selectedFileHash) {
          invalidatedBy.push({
            reason: "selected-file-content-changed",
            previousSelectedFileHash: draft.selectedFileHash,
            currentSelectedFileHash: current.selectedFileHash
          });
        }
        if (expectedDraftHash && draft.draftHash && expectedDraftHash !== draft.draftHash) {
          invalidatedBy.push({
            reason: "runtime-draft-changed",
            previousDraftHash: draft.draftHash,
            currentDraftHash: expectedDraftHash
          });
        }
        if (current.sourceContainsRuntimeChrome === true) {
          invalidatedBy.push({
            reason: "runtime-chrome-in-source",
            message: "Generated runtime chrome must not be persisted into author-owned source."
          });
        }
        const unique = [];
        const seen = new Set();
        invalidatedBy.forEach((entry) => {
          const key = `${entry.reason || "unknown"}:${JSON.stringify(entry)}`;
          if (seen.has(key)) return;
          seen.add(key);
          unique.push(entry);
        });
        const valid = unique.length === 0 && provenance.provenanceEnforced === true;
        return jsonSafeClone({
          kind: "mcelCommitFreshness.v1",
          boundarySpecimen: "mcel.code-studio",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          status: valid ? "valid" : (unique.length ? "invalidated" : "stale"),
          valid,
          sourceHash: current.sourceHash || "",
          selectedFileHash: current.selectedFileHash || "",
          draftHash: expectedDraftHash || current.draftHash || "",
          invalidatedBy: unique,
          action: valid ? "continue to MCEL Code Studio consumer gate" : "rebuild runtime draft from the current author source",
          invariant: [
            "source hash must match the reviewed draft source",
            "selected file must match the reviewed draft target",
            "runtime draft hash must match the reviewed draft text"
          ]
        });
      }

      function mcelCodeStudioCommitConsumerGate({
        draft = {},
        provenance = {},
        freshness = {},
        gates = {},
        phase = "preflight",
        blockers = []
      } = {}) {
        const allBlockers = [...blockers.filter(Boolean)];
        const editGate = gates.editGate || null;
        const commitGate = gates.commitGate || null;
        const saveGate = gates.saveGate || null;
        const loaderGate = gates.loaderGate || null;
        if (freshness.valid !== true) allBlockers.push(...(freshness.invalidatedBy || []).map((entry) => entry.reason || "freshness-invalid"));
        if (provenance.provenanceEnforced !== true) allBlockers.push("provenance-not-enforced");
        if (editGate && editGate.ok === false) allBlockers.push(editGate.code || "editDraft-gate-blocked");
        if (commitGate && commitGate.ok === false) allBlockers.push(commitGate.code || "commitDraft-gate-blocked");
        if (saveGate && saveGate.ok === false) allBlockers.push(saveGate.code || "saveFile-gate-blocked");
        if (loaderGate && loaderGate.ok === false) allBlockers.push(loaderGate.route?.code || "route-loader-gate-blocked");
        if (!draft.draftId) allBlockers.push("commit-draft-missing");
        const uniqueBlockers = [...new Set(allBlockers.filter(Boolean))];
        const valid = provenance.provenanceEnforced === true && freshness.valid === true && uniqueBlockers.length === 0;
        const actionName = draft.action || "codeStudio.commitRuntimeDraft";
        const allowedActions = valid
          ? (actionName === "codeStudio.mountRuntimeDraft"
            ? ["mountRuntimeDraft-with-receipt", "mountMonaco-runtime-only", "inspect-runtime-mount-receipt"]
            : actionName === "codeStudio.persistLiveWorkspace"
              ? ["persistLiveWorkspace-with-receipt", "refresh-route-loader-receipt", "inspect-persistence-receipt"]
              : ["commitRuntimeDraft-with-receipt", "persistLiveWorkspace-with-receipt", "inspect-source-mutation-receipt"])
          : ["rebuild-draft", "refresh-scm-gates", "inspect-18n-preflight"];
        return jsonSafeClone({
          kind: "mcelCommitConsumerGate.v1",
          boundarySpecimen: "mcel.code-studio",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          consumer: `mcel.code-studio.${actionName}`,
          phase,
          status: valid ? "pass" : "blocked",
          valid,
          blockers: uniqueBlockers,
          allowedActions,
          reason: valid
            ? `MCEL Code Studio 18N consumer gate accepted ${actionName}.`
            : `MCEL Code Studio 18N consumer gate blocked commit: ${uniqueBlockers.join(", ") || "not proven"}`,
          invariant: [
            "Code Studio cannot commit stale runtime intent",
            "Code Studio cannot mount runtime chrome without a fresh source snapshot",
            "Code Studio cannot persist without SCM save/load proof",
            "consumer gate must run before source/localStorage mutation"
          ]
        });
      }

      function mcelCodeStudioCommitPreflight({draft = {}, freshness = {}, consumerGate = {}} = {}) {
        const blockers = [...new Set([...(consumerGate.blockers || []), ...((freshness.invalidatedBy || []).map((entry) => entry.reason || "freshness-invalid"))].filter(Boolean))];
        const allowed = consumerGate.valid === true && blockers.length === 0;
        return jsonSafeClone({
          kind: "mcelCommitPreflight.v1",
          boundarySpecimen: "mcel.code-studio",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          status: allowed ? "allowed" : "blocked",
          allowed,
          canCommit: allowed,
          canSend: false,
          canSign: false,
          canBroadcast: false,
          blockers,
          summary: allowed
            ? `${draft.action || "Code Studio commit"} may proceed through a receipted MCEL 18N boundary.`
            : `${draft.action || "Code Studio commit"} is blocked until draft, provenance, freshness, and SCM gates agree.`,
          invariant: [
            "preflight result is explicit",
            "source mutation is not inferred from a button click",
            "wallet execution remains locked and unrelated"
          ]
        });
      }

      function mcelCodeStudioCommitReceipt({
        draft = {},
        provenance = {},
        freshness = {},
        consumerGate = {},
        preflight = {},
        mutationExecuted = false,
        beforeSourceHash = "",
        afterSourceHash = "",
        reason = "mcel-code-studio-commit"
      } = {}) {
        const sequence = (studioState.codeStudioCommitBoundarySequence || 0) + 1;
        studioState.codeStudioCommitBoundarySequence = sequence;
        const committed = mutationExecuted === true && preflight.allowed === true && consumerGate.valid === true;
        return jsonSafeClone({
          kind: "mcelCommitReceipt.v1",
          receiptVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          receiptId: `mcel-code-studio-commit-receipt-${String(sequence).padStart(4, "0")}`,
          boundarySpecimen: "mcel.code-studio",
          action: draft.action || "codeStudio.commitRuntimeDraft",
          status: committed ? "committed" : (preflight.allowed ? "preflight-allowed" : "blocked"),
          committed,
          mutationExecuted,
          beforeSourceHash,
          afterSourceHash,
          draftId: draft.draftId || "",
          sourceHash: provenance.sourceHash || "",
          freshnessStatus: freshness.status || "not-observed",
          consumerGateStatus: consumerGate.status || "not-observed",
          preflightStatus: preflight.status || "not-observed",
          blockers: preflight.blockers || consumerGate.blockers || [],
          reason,
          invariant: [
            "receipt records the exact commit decision",
            "receipt records whether mutation actually executed",
            "receipt keeps wallet send/sign/broadcast false"
          ]
        });
      }

      function buildMcelCodeStudioCommitBoundary({
        action = "codeStudio.commitRuntimeDraft",
        draftText = "",
        reason = "preflight",
        gates = {},
        phase = "preflight",
        intendedWrites = [],
        mutationExecuted = false,
        beforeSourceHash = "",
        afterSourceHash = "",
        blockers = []
      } = {}) {
        const fields = workspaceFields();
        const file = selectedFileFromFieldsForBoundary(fields);
        const sourceSnapshot = currentCodeStudioCommitSourceSnapshot(fields, draftText);
        const draft = mcelCodeStudioCommitDraft({action, draftText, reason, intendedWrites, sourceSnapshot, selectedFile: file});
        const provenance = mcelCodeStudioCommitProvenance({draft, sourceSnapshot, currentSnapshot: sourceSnapshot});
        const freshness = mcelCodeStudioCommitFreshness({
          draft,
          provenance,
          currentSnapshot: sourceSnapshot,
          expectedDraftHash: hashRegressionString(draftText),
          extraInvalidations: blockers.map((reason) => ({reason}))
        });
        const consumerGate = mcelCodeStudioCommitConsumerGate({draft, provenance, freshness, gates, phase, blockers});
        const preflight = mcelCodeStudioCommitPreflight({draft, freshness, consumerGate});
        const receipt = mcelCodeStudioCommitReceipt({
          draft,
          provenance,
          freshness,
          consumerGate,
          preflight,
          mutationExecuted,
          beforeSourceHash,
          afterSourceHash,
          reason
        });
        return jsonSafeClone({
          kind: "mcelCodeStudioCommitBoundary.v1",
          boundaryVersion: MCEL_CODE_STUDIO_COMMIT_BOUNDARY_VERSION,
          action,
          status: preflight.status,
          seriousAction: true,
          mcelOnly: true,
          locked: false,
          canCommit: preflight.canCommit === true,
          canSend: false,
          canSign: false,
          canBroadcast: false,
          mcelCommitDraft: draft,
          mcelCommitProvenance: provenance,
          mcelCommitFreshness: freshness,
          mcelCommitConsumerGate: consumerGate,
          mcelCommitPreflight: preflight,
          mcelCommitReceipt: receipt,
          nextAction: preflight.allowed
            ? (action === "codeStudio.mountRuntimeDraft"
              ? "mount runtime preview through receipt and keep generated chrome out of source"
              : "commit through receipt and record after-source hash")
            : "rebuild runtime draft and rerun SCM gates",
          invariant: [
            "Code Studio runtime mount is now an 18N boundary specimen",
            "Code Studio runtime commit is now an 18N boundary specimen",
            "source mutation only follows allowed preflight",
            "local workspace persistence carries its own boundary receipt",
            "wallet execution remains locked"
          ]
        });
      }

      function recordMcelCodeStudioCommitBoundary(boundary = null) {
        if (!boundary) return null;
        studioState.lastCodeStudioCommitBoundary = jsonSafeClone(boundary);
        if (boundary.mcelCommitReceipt) {
          studioState.codeStudioCommitBoundaryReceipts = [
            ...(studioState.codeStudioCommitBoundaryReceipts || []).slice(-15),
            boundary.mcelCommitReceipt
          ];
        }
        renderMcelCodeStudioCommitBoundarySurface(boundary);
        return boundary;
      }

      function renderMcelCodeStudioCommitBoundarySurface(boundary = studioState.lastCodeStudioCommitBoundary) {
        const target = root.querySelector("#code-studio-18n-commit-boundary-status");
        if (!target || !boundary) return null;
        const preflight = boundary.mcelCommitPreflight || {};
        const receipt = boundary.mcelCommitReceipt || {};
        target.dataset.status = boundary.status || "unknown";
        target.dataset.canCommit = boundary.canCommit === true ? "true" : "false";
        target.textContent = [
          `18N Code Studio boundary: ${boundary.status || "unknown"}`,
          `action: ${boundary.action || "codeStudio.commitRuntimeDraft"}`,
          `canCommit=${boundary.canCommit === true} canSend=${boundary.canSend === true} canSign=${boundary.canSign === true} canBroadcast=${boundary.canBroadcast === true}`,
          `receipt=${receipt.status || "not-recorded"} mutationExecuted=${receipt.mutationExecuted === true}`,
          `blockers=${(preflight.blockers || []).join(", ") || "none"}`
        ].join("\n");
        return target;
      }

      function renderMcelCodeStudioCommitBoundaryInProofDock(boundary = studioState.lastCodeStudioCommitBoundary) {
        const payload = boundary || buildMcelCodeStudioCommitBoundary({
          action: "codeStudio.commitRuntimeDraft",
          reason: "proof-dock-empty-boundary",
          blockers: ["commit-boundary-not-run"]
        });
        return renderProofDockPayload("MCEL 18N Code Studio commit boundary", payload, {
          kind: "mcel-code-studio-18n-commit-boundary-detail",
          action: "copy-code-studio-18n-boundary",
          summaryRows: [
            ["Boundary", payload.kind || "mcelCodeStudioCommitBoundary.v1"],
            ["Status", payload.status || "unknown"],
            ["Action", payload.action || "codeStudio.commitRuntimeDraft"],
            ["Can commit", String(payload.canCommit === true)],
            ["Allowed actions", (payload.mcelCommitConsumerGate?.allowedActions || []).join(", ") || "none"],
            ["Wallet execution", `send=${payload.canSend === true} sign=${payload.canSign === true} broadcast=${payload.canBroadcast === true}`]
          ],
          issueRows: payload.mcelCommitPreflight?.blockers || []
        });
      }

      function formatEditorDraftProvenanceDetail(summary = collectEditorDraftProvenanceSummary()) {
        return {
          kind: summary.kind,
          provenanceVersion: summary.provenanceVersion,
          sourceMutationGate: summary.sourceMutationGate,
          activeDraftId: summary.activeDraftId,
          totalEvents: summary.totalEvents,
          invariants: summary.invariants,
          declaredEffects: summary.declaredEffects,
          lastEvent: summary.lastEvent,
          recentEvents: summary.recentEvents
        };
      }

      function buildGenericScmReplayFixtureVector(fields = {}) {
        const actionOutcome = normalizeReceiptOutcome(fields.actionOutcome, "waiting");
        const externalOutcome = normalizeReceiptExternalOutcome(fields.externalOutcome || {status: actionOutcome});
        return jsonSafeClone({
          kind: SCM_LAB_RECEIPT_PROOF_KIND,
          vectorVersion: SCM_RECEIPT_VECTOR_VERSION,
          sourceKind: fields.sourceKind || "mcel-code-studio-generic-replay-fixture",
          ingestedAt: fields.ingestedAt || "",
          status: fields.status || actionOutcome,
          mode: fields.mode || "deterministic-fixture",
          selectedEffect: fields.selectedEffect || "",
          selectedEffectCategory: fields.selectedEffectCategory || "runtime-action",
          actionOutcome,
          externalOutcome,
          governanceOutcome: normalizeReceiptOutcome(fields.governanceOutcome, "pass"),
          safetyOutcome: normalizeReceiptOutcome(fields.safetyOutcome, "pass"),
          proofCompleteness: fields.proofCompleteness || "complete",
          declaredReads: uniqueScmReceiptList(fields.declaredReads),
          declaredWrites: uniqueScmReceiptList(fields.declaredWrites),
          runtimeConsequences: uniqueScmReceiptList(fields.runtimeConsequences),
          nextAction: fields.nextAction || "inspect fixture receipt",
          repairPacket: fields.repairPacket || {
            status: "not required",
            generated: false,
            liveAiCall: null,
            allowedWrites: [],
            forbiddenWrites: [],
            boundaryBlocked: false,
            packet: {}
          },
          txDraftBoundary: fields.txDraftBoundary || {
            status: "not-observed",
            boundary: "",
            noSend: false,
            probeStatus: {nonce: "", gasEstimate: "", ethCall: ""},
            provenance: {
              provenanceVersion: "",
              sourceRequestHash: "",
              selectedRequestSnapshot: null,
              walletAccountHash: "",
              chainProof: {},
              externalOutcomeSequence: [],
              networkGateSequence: [],
              calldataSource: "",
              abiEncodingStatus: "",
              probeEnvelopeIds: [],
              invalidatedBy: [],
              valid: false
            },
            raw: null
          },
          commitBoundary: normalizeMcelCommitBoundaryForReceipt(fields.commitBoundary || fields.mcelCommitBoundary || fields.walletCommitBoundary, fields.txDraftBoundary || {}),
          mcelCommitBoundary: normalizeMcelCommitBoundaryForReceipt(fields.commitBoundary || fields.mcelCommitBoundary || fields.walletCommitBoundary, fields.txDraftBoundary || {}),
          walletCommitBoundary: normalizeMcelCommitBoundaryForReceipt(fields.commitBoundary || fields.mcelCommitBoundary || fields.walletCommitBoundary, fields.txDraftBoundary || {}),
          draftProvenance: fields.draftProvenance || {
            status: "not-observed",
            activeDraftId: "",
            eventType: "",
            sourceMutationGate: "commitDraft",
            runtimeOnlyUntilCommit: true,
            sourceMutationsOnlyByCommitDraft: true,
            events: []
          },
          layoutObservation: fields.layoutObservation || {
            kind: "",
            source: "",
            measured: false,
            regions: {},
            metrics: {},
            documentHeightRatio: null,
            violations: [],
            styleViolations: []
          },
          checks: fields.checks || {},
          rawReceipt: fields.rawReceipt || null
        });
      }

      function buildWalletScmReplayFixtureReceipt(effectName, actionOutcome, externalOutcome = {}, proof = {}) {
        const proofChecks = proof.checks || {};
        const proofPayload = {...proof};
        delete proofPayload.checks;
        return jsonSafeClone({
          kind: SCM_LAB_RECEIPT_KIND,
          proof: {
            component: "DevNetworkReleaseConsole",
            mode: "deterministic-fixture",
            selectedEffect: effectName,
            status: actionOutcome,
            actionOutcome,
            externalOutcome: {
              kind: "mcel-external-outcome",
              status: actionOutcome,
              provider: "fixture.ethereum",
              method: effectName === "wallet.connect" ? "eth_requestAccounts" : "",
              ...externalOutcome
            },
            governanceOutcome: "pass",
            safetyOutcome: "pass",
            proofCompleteness: "complete",
            ...proofPayload,
            checks: {
              sourceSafeAfterExternalOutcome: true,
              ...proofChecks
            }
          }
        });
      }

      function buildWalletScmReplayFixtures() {
        const draftTxProvenance = {
          provenanceVersion: "txDraft.provenance.v1",
          sourceRequestHash: "srcReq:fixture-allowance-view",
          selectedRequestSnapshot: {
            id: "rel-allowance-view",
            status: "needs-wallet",
            risk: "medium",
            contractMethod: "allowance(address,address)"
          },
          walletAccountHash: "account:fixture",
          chainProof: {expectedChainId: "0x28757b2", chainId: "0x28757b2", ok: true, status: "matched"},
          externalOutcomeSequence: [{sequence: 1, operation: "wallet.connect", status: "pass", reason: "account-granted"}],
          networkGateSequence: [{status: "dev-network-ready", ok: true, expectedChainId: "0x28757b2", chainId: "0x28757b2"}],
          calldataSource: "abi-encoding",
          abiEncodingStatus: "encoded",
          probeEnvelopeIds: ["eth_getTransactionCount:pass", "eth_estimateGas:pass", "eth_call:pass"],
          invalidatedBy: [],
          valid: true
        };
        const draftTxBoundary = {
          status: "observed",
          boundary: "runtime-only-no-send",
          noSend: true,
          probeStatus: {nonce: "pass", gasEstimate: "pass", ethCall: "pass"},
          provenance: draftTxProvenance,
          raw: {boundary: "runtime-only-no-send", ...draftTxProvenance}
        };
        const accountInvalidatedDraftBoundary = {
          status: "empty",
          boundary: "runtime-only-no-send",
          noSend: true,
          probeStatus: {nonce: "skipped", gasEstimate: "skipped", ethCall: "skipped"},
          provenance: {
            ...draftTxProvenance,
            valid: false,
            invalidatedBy: [{reason: "account-changed"}],
            abiEncodingStatus: "invalidated-by-account-event"
          },
          raw: {boundary: "runtime-only-no-send", valid: false, invalidatedBy: [{reason: "account-changed"}]}
        };
        const chainInvalidatedDraftBoundary = {
          status: "empty",
          boundary: "runtime-only-no-send",
          noSend: true,
          probeStatus: {nonce: "skipped", gasEstimate: "skipped", ethCall: "skipped"},
          provenance: {
            ...draftTxProvenance,
            valid: false,
            chainProof: {expectedChainId: "0x28757b2", chainId: "0x1", ok: false, status: "mismatch"},
            invalidatedBy: [{reason: "chain-changed"}],
            abiEncodingStatus: "invalidated-by-chain-event"
          },
          raw: {boundary: "runtime-only-no-send", valid: false, invalidatedBy: [{reason: "chain-changed"}]}
        };
        return [
          {
            id: "wallet.connect.pass",
            family: "wallet",
            label: "wallet.connect pass normalizes account-granted external outcome",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.connect", "pass", {
              reason: "account-granted",
              account: "0xfixture",
              chainId: "0x539",
              nextAction: "draft tx"
            }, {walletConnectCount: 1}),
            expected: {
              selectedEffect: "wallet.connect",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              externalStatus: "pass",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              declaredWritesContain: ["runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.evidenceStrip"],
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "draft tx"
            }
          },
          {
            id: "wallet.connect.blocked",
            family: "wallet",
            label: "wallet.connect blocked preserves source and clears unsafe tx draft",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.connect", "blocked", {
              reason: "account-request-rejected",
              message: "User rejected account request",
              nextAction: "retry connect"
            }, {walletConnectCount: 1}),
            expected: {
              selectedEffect: "wallet.connect",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "retry connect"
            }
          },
          {
            id: "wallet.connect.exception",
            family: "wallet",
            label: "wallet.connect exception is captured without source mutation",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.connect", "exception", {
              reason: "provider-exception",
              message: "Injected provider threw",
              nextAction: "inspect exception"
            }, {walletConnectCount: 1}),
            expected: {
              selectedEffect: "wallet.connect",
              actionOutcome: "exception",
              expectedDisposition: "EXCEPTION",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "exception",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "inspect exception"
            }
          },
          {
            id: "wallet.accountsChanged.switch",
            family: "wallet",
            label: "accountsChanged account switch commits runtime account update and clears draft",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.provider.accountsChanged", "pass", {
              reason: "account-switch",
              account: "0xswitched"
            }, {providerAccountsChangedCount: 1, runtimeTxDraft: accountInvalidatedDraftBoundary}),
            expected: {
              selectedEffect: "wallet.provider.accountsChanged",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              consequencesContain: ["committed account update", "runtime.txDraft cleared"],
              txDraftInvalidatedByContain: ["account-changed"]
            }
          },
          {
            id: "wallet.accountsChanged.disconnect",
            family: "wallet",
            label: "accountsChanged empty account list disconnects wallet and clears draft",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.provider.accountsChanged", "pass", {
              reason: "account-list-empty"
            }, {providerAccountsChangedCount: 1, providerAccountDisconnectCount: 1}),
            expected: {
              selectedEffect: "wallet.provider.accountsChanged",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              consequencesContain: ["disconnected wallet", "runtime.txDraft cleared"]
            }
          },
          {
            id: "wallet.chainChanged.wrong-chain",
            family: "wallet",
            label: "chainChanged wrong chain blocks next tx draft and clears stale draft",
            receipt: buildWalletScmReplayFixtureReceipt("wallet.provider.chainChanged", "blocked", {
              reason: "wrong-chain",
              chainId: "0x1",
              nextAction: "switch network"
            }, {providerChainChangedCount: 1, runtimeTxDraft: chainInvalidatedDraftBoundary, checks: {networkGatePass: false}}),
            expected: {
              selectedEffect: "wallet.provider.chainChanged",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "switch network",
              consequencesContain: ["runtime.network updated", "runtime.txDraft cleared"],
              txDraftInvalidatedByContain: ["chain-changed"]
            }
          },
          {
            id: "wallet.draftTx.pass",
            family: "wallet",
            label: "release.draftTx pass creates runtime-only no-send tx draft",
            receipt: buildWalletScmReplayFixtureReceipt("release.draftTx", "pass", {
              reason: "probe-pass",
              nextAction: "inspect tx draft"
            }, {
              txDraftCount: 1,
              checks: {txDraftRuntimeOnly: true},
              runtimeTxDraft: draftTxBoundary
            }),
            expected: {
              selectedEffect: "release.draftTx",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              txDraftNoSend: true,
              txDraftProvenanceRecorded: true,
              consequencesContain: ["runtime.txDraft updated", "source unchanged", "no transaction send attempted"]
            }
          },
          {
            id: "wallet.draftTx.blocked-wallet",
            family: "wallet",
            label: "release.draftTx blocked after wallet block does not create a durable source change",
            receipt: buildWalletScmReplayFixtureReceipt("release.draftTx", "blocked", {
              reason: "wallet-blocked",
              nextAction: "retry connect"
            }, {checks: {txDraftRuntimeOnly: false}}),
            expected: {
              selectedEffect: "release.draftTx",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "retry connect"
            }
          },
          {
            id: "wallet.draftTx.blocked-chain",
            family: "wallet",
            label: "release.draftTx blocked after wrong chain remains no-send and source-safe",
            receipt: buildWalletScmReplayFixtureReceipt("release.draftTx", "blocked", {
              reason: "wrong-chain",
              chainId: "0x1",
              nextAction: "retry connect"
            }, {checks: {networkGatePass: false, txDraftRuntimeOnly: false}}),
            expected: {
              selectedEffect: "release.draftTx",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "retry connect"
            }
          },
          {
            id: "wallet.repairPacket.generated",
            family: "wallet",
            label: "bounded repair packet is generated without a live AI call",
            receipt: buildWalletScmReplayFixtureReceipt("ai.repairWalletHint", "pass", {
              reason: "repair-packet-generated",
              nextAction: "inspect bounded repair packet"
            }, {
              repairPacketCount: 1,
              checks: {repairPacketGenerated: true, repairPacketNoLiveAiCall: true},
              repairPacket: {
                kind: "mcel-repair-packet",
                status: "ready",
                liveAiCall: false,
                allowedWrites: ["runtime.proofChip", "runtime.repairPacket", "runtime.assistantRepairPrompt", "runtime.evidenceStrip"],
                forbiddenWrites: ["source.devRelease", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"]
              }
            }),
            expected: {
              selectedEffect: "ai.repairWalletHint",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              repairPacketGenerated: true,
              liveAiCall: false,
              forbiddenWritesContain: ["source.devRelease", "runtime.txDraft"]
            }
          },
          {
            id: "wallet.repairPacket.forbidden-write-blocked",
            family: "wallet",
            label: "repair packet fixture proves forbidden wallet/source writes stay blocked",
            receipt: buildWalletScmReplayFixtureReceipt("ai.repairWalletHint", "pass", {
              reason: "forbidden-write-blocked",
              nextAction: "inspect bounded repair packet"
            }, {
              repairPacketCount: 1,
              repairBoundaryBlockedCount: 1,
              checks: {repairPacketGenerated: true, repairPacketNoLiveAiCall: true, repairBoundaryBlocked: true},
              repairPacket: {
                kind: "mcel-repair-packet",
                status: "ready",
                liveAiCall: false,
                allowedWrites: ["runtime.proofChip", "runtime.repairPacket", "runtime.assistantRepairPrompt", "runtime.evidenceStrip"],
                forbiddenWrites: ["source.devRelease", "runtime.wallet", "runtime.network", "runtime.txDraft", "runtime.externalOutcome"]
              }
            }),
            expected: {
              selectedEffect: "ai.repairWalletHint",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              repairPacketGenerated: true,
              repairBoundaryBlocked: true,
              liveAiCall: false,
              forbiddenWritesContain: ["source.devRelease", "runtime.wallet", "runtime.txDraft"]
            }
          }
        ];
      }

      function buildCodeEditorScmReplayFixtures() {
        const codeStudioSource = "mcel-code-studio-generic-replay-fixture";
        const vector = (fields) => buildGenericScmReplayFixtureVector({
          sourceKind: codeStudioSource,
          governanceOutcome: "pass",
          safetyOutcome: "pass",
          proofCompleteness: "complete",
          ...fields
        });
        return [
          {
            id: "code-editor.monaco.mount.pass",
            family: "code-editor",
            label: "Monaco mount pass stays runtime-only and points to commitDraft as the source gate",
            receipt: vector({
              selectedEffect: "editor.monaco.mount",
              selectedEffectCategory: "editor-runtime",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "model-mounted", provider: "monaco-editor"},
              declaredReads: ["source.workspace.files", "state.selectedPath"],
              declaredWrites: ["runtime.editor.monacoInstance", "runtime.editor.monacoModel", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.monaco mounted", "source unchanged"],
              nextAction: "edit runtime draft"
            }),
            expected: {
              selectedEffect: "editor.monaco.mount",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              declaredReadsContain: ["source.workspace.files"],
              declaredWritesContain: ["runtime.editor.monacoInstance", "runtime.evidenceStrip"]
            }
          },
          {
            id: "code-editor.monaco.mount.blocked",
            family: "code-editor",
            label: "Monaco mount blocked falls back to textarea without source mutation",
            receipt: vector({
              selectedEffect: "editor.monaco.mount",
              selectedEffectCategory: "editor-runtime",
              actionOutcome: "blocked",
              externalOutcome: {status: "blocked", reason: "mobile-browser-unsupported", provider: "monaco-editor"},
              declaredReads: ["source.workspace.files", "state.selectedPath"],
              declaredWrites: ["runtime.editor.fallback", "runtime.evidenceStrip"],
              runtimeConsequences: ["fallback textarea active", "source unchanged"],
              nextAction: "use fallback editor"
            }),
            expected: {
              selectedEffect: "editor.monaco.mount",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "use fallback editor"
            }
          },
          {
            id: "code-editor.monaco.mount.exception",
            family: "code-editor",
            label: "Monaco mount exception is captured as external runtime evidence",
            receipt: vector({
              selectedEffect: "editor.monaco.mount",
              selectedEffectCategory: "editor-runtime",
              actionOutcome: "exception",
              externalOutcome: {status: "exception", reason: "loader-exception", provider: "monaco-editor", message: "loader failed"},
              declaredReads: ["source.workspace.files", "state.selectedPath"],
              declaredWrites: ["runtime.editor.fallback", "runtime.evidenceStrip"],
              runtimeConsequences: ["fallback textarea active", "source unchanged"],
              nextAction: "inspect exception"
            }),
            expected: {
              selectedEffect: "editor.monaco.mount",
              actionOutcome: "exception",
              expectedDisposition: "EXCEPTION",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "exception",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              nextAction: "inspect exception"
            }
          },
          {
            id: "code-editor.monaco.change.draft-only",
            family: "code-editor",
            label: "Monaco change writes editor draft only until commitDraft",
            receipt: vector({
              selectedEffect: "editor.monaco.change",
              selectedEffectCategory: "editor-runtime",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "model-updated", provider: "monaco-editor"},
              declaredReads: ["runtime.editor.monacoModel", "state.selectedPath"],
              declaredWrites: ["state.drafts", "state.dirty", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft updated", "state.dirty updated", "source unchanged"],
              nextAction: "commit draft"
            }),
            expected: {
              selectedEffect: "editor.monaco.change",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              declaredWritesContain: ["state.drafts", "state.dirty"],
              nextAction: "commit draft"
            }
          },
          {
            id: "code-editor.editorDraft.created.provenance",
            family: "code-editor",
            label: "editorDraft created provenance records source snapshot hash and remains runtime-only",
            receipt: vector({
              selectedEffect: "editorDraft.created",
              selectedEffectCategory: "draft-provenance",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "runtime-draft-created", provider: "code-studio"},
              declaredReads: ["source.workspace.files", "state.activeFileId"],
              declaredWrites: ["runtime.editorDraft", "runtime.editorDraftProvenance", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft provenance created", "source unchanged"],
              nextAction: "edit runtime draft",
              draftProvenance: {
                status: "created",
                activeDraftId: "editor-draft:src/app.js@fnv1a-fixture",
                eventType: "created",
                sourceMutationGate: "commitDraft",
                runtimeOnlyUntilCommit: true,
                sourceMutationsOnlyByCommitDraft: true,
                events: ["created"]
              }
            }),
            expected: {
              selectedEffect: "editorDraft.created",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              draftProvenanceEventType: "created",
              draftRuntimeOnlyUntilCommit: true,
              sourceMutationsOnlyByCommitDraft: true,
              declaredWritesContain: ["runtime.editorDraftProvenance", "runtime.evidenceStrip"]
            }
          },
          {
            id: "code-editor.editorDraft.changed.provenance",
            family: "code-editor",
            label: "editorDraft changed provenance proves Monaco/fallback edits remain runtime-only",
            receipt: vector({
              selectedEffect: "editorDraft.changed",
              selectedEffectCategory: "draft-provenance",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "runtime-draft-changed", provider: "code-studio"},
              declaredReads: ["runtime.editorDraft", "state.activeFileId"],
              declaredWrites: ["runtime.editorDraft", "runtime.editorDraftProvenance", "state.drafts", "state.dirty", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft updated", "state.dirty updated", "source unchanged"],
              nextAction: "commit draft",
              draftProvenance: {
                status: "changed",
                activeDraftId: "editor-draft:src/app.js@fnv1a-fixture",
                eventType: "changed",
                sourceMutationGate: "commitDraft",
                runtimeOnlyUntilCommit: true,
                sourceMutationsOnlyByCommitDraft: true,
                events: ["created", "changed"]
              }
            }),
            expected: {
              selectedEffect: "editorDraft.changed",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              draftProvenanceEventType: "changed",
              draftRuntimeOnlyUntilCommit: true,
              sourceMutationsOnlyByCommitDraft: true,
              declaredWritesContain: ["runtime.editorDraftProvenance", "state.dirty"]
            }
          },
          {
            id: "code-editor.editorDraft.committed.provenance",
            family: "code-editor",
            label: "editorDraft committed provenance allows the only source mutation through commitDraft",
            receipt: vector({
              selectedEffect: "editorDraft.committed",
              selectedEffectCategory: "draft-provenance",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "commit-draft-source-gate", provider: "code-studio"},
              declaredReads: ["source.workspace.files", "state.drafts", "state.activeFileId", "runtime.editorDraftProvenance"],
              declaredWrites: ["source.workspace.files", "state.drafts", "state.dirty", "runtime.editorDraftProvenance", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft committed", "source changed through commitDraft"],
              nextAction: "render committed source",
              draftProvenance: {
                status: "committed",
                activeDraftId: "",
                eventType: "committed",
                sourceMutationGate: "commitDraft",
                runtimeOnlyUntilCommit: false,
                sourceMutationsOnlyByCommitDraft: true,
                events: ["created", "changed", "committed"]
              },
              checks: {sourceMutationGate: "commitDraft"}
            }),
            expected: {
              selectedEffect: "editorDraft.committed",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              sourceWriteEffect: true,
              sourceMutationGate: "commitDraft",
              draftProvenanceEventType: "committed",
              sourceMutationsOnlyByCommitDraft: true,
              declaredWritesContain: ["source.workspace.files", "runtime.editorDraftProvenance"]
            }
          },
          {
            id: "code-editor.editorDraft.discarded.provenance",
            family: "code-editor",
            label: "editorDraft discarded provenance closes the runtime draft without mutating source",
            receipt: vector({
              selectedEffect: "editorDraft.discarded",
              selectedEffectCategory: "draft-provenance",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "draft-discarded", provider: "code-studio"},
              declaredReads: ["runtime.editorDraft", "runtime.editorDraftProvenance"],
              declaredWrites: ["runtime.editorDraft", "runtime.editorDraftProvenance", "state.drafts", "state.dirty", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft discarded", "state.dirty cleared", "source unchanged"],
              nextAction: "render source draft",
              draftProvenance: {
                status: "discarded",
                activeDraftId: "",
                eventType: "discarded",
                sourceMutationGate: "commitDraft",
                runtimeOnlyUntilCommit: true,
                sourceMutationsOnlyByCommitDraft: true,
                events: ["created", "changed", "discarded"]
              }
            }),
            expected: {
              selectedEffect: "editorDraft.discarded",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              draftProvenanceEventType: "discarded",
              sourceMutationsOnlyByCommitDraft: true
            }
          },
          {
            id: "code-editor.editorDraft.restored.provenance",
            family: "code-editor",
            label: "editorDraft restored provenance marks replay/restore origin without making source durable",
            receipt: vector({
              selectedEffect: "editorDraft.restored",
              selectedEffectCategory: "draft-provenance",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "draft-restored-from-replay", provider: "code-studio"},
              declaredReads: ["runtime.editorDraftProvenance", "runtime.replaySnapshot"],
              declaredWrites: ["runtime.editorDraft", "runtime.editorDraftProvenance", "state.drafts", "state.dirty", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.editorDraft restored", "source unchanged"],
              nextAction: "inspect restored draft",
              draftProvenance: {
                status: "restored",
                activeDraftId: "editor-draft:src/app.js@fnv1a-fixture",
                eventType: "restored",
                sourceMutationGate: "commitDraft",
                runtimeOnlyUntilCommit: true,
                sourceMutationsOnlyByCommitDraft: true,
                events: ["restored"]
              }
            }),
            expected: {
              selectedEffect: "editorDraft.restored",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              draftProvenanceEventType: "restored",
              draftRuntimeOnlyUntilCommit: true,
              sourceMutationsOnlyByCommitDraft: true
            }
          },
          {
            id: "code-editor.commitDraft.source-gate",
            family: "code-editor",
            label: "commitDraft is the only fixture that may write source.workspace.files",
            receipt: vector({
              selectedEffect: "commitDraft",
              selectedEffectCategory: "source-gate",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "explicit-user-commit"},
              declaredReads: ["source.workspace.files", "state.drafts", "state.selectedPath"],
              declaredWrites: ["source.workspace.files", "state.dirty", "runtime.validationReport", "runtime.evidenceStrip"],
              runtimeConsequences: ["source.workspace.files updated by commitDraft", "runtime.editorDraft committed"],
              nextAction: "serialize clean source",
              checks: {sourceMutationGate: "commitDraft"}
            }),
            expected: {
              selectedEffect: "commitDraft",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "pass",
              sourceWriteEffect: true,
              sourceMutationGate: "commitDraft",
              declaredWritesContain: ["source.workspace.files"],
              nextAction: "serialize clean source"
            }
          },
          {
            id: "code-editor.serialization.clean-source",
            family: "code-editor",
            label: "clean serialization excludes runtime and Monaco chrome",
            receipt: vector({
              selectedEffect: "serializeCleanSource",
              selectedEffectCategory: "serialization",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "clean-source"},
              declaredReads: ["source.workspace.files", "source.workspace.manifest"],
              declaredWrites: ["runtime.serializedSource", "runtime.evidenceStrip"],
              runtimeConsequences: ["serialized source generated", "runtime chrome omitted", "source unchanged"],
              nextAction: "inspect serialized output",
              checks: {runtimeChromeOmitted: true, sourceSerializationClean: true}
            }),
            expected: {
              selectedEffect: "serializeCleanSource",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              checks: {runtimeChromeOmitted: true, sourceSerializationClean: true}
            }
          },
          {
            id: "code-editor.layout.observe.pass",
            family: "code-editor",
            label: "layout observation pass records measured component-owned viewport",
            receipt: vector({
              selectedEffect: "layout.observe",
              selectedEffectCategory: "layout",
              actionOutcome: "pass",
              externalOutcome: {status: "pass", reason: "layout-measured"},
              declaredReads: ["layout.editorGroup", "runtime.editor.monacoInstance"],
              declaredWrites: ["runtime.layoutObservation", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.layoutObservation updated", "source unchanged"],
              nextAction: "keep authoring",
              layoutObservation: {
                kind: "mcel-layout-observation",
                source: "code-studio-component-owned-viewport",
                measured: true,
                regions: {editorGroup: true, bottomDock: true},
                metrics: {ownedDocumentHeight: 720},
                documentHeightRatio: 1,
                violations: [],
                styleViolations: []
              }
            }),
            expected: {
              selectedEffect: "layout.observe",
              actionOutcome: "pass",
              expectedDisposition: "PASS",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              layoutMeasured: true,
              layoutViolationCount: 0
            }
          },
          {
            id: "code-editor.layout.observe.fail",
            family: "code-editor",
            label: "layout observation fail is represented as a blocked receipt, not source mutation",
            receipt: vector({
              selectedEffect: "layout.observe",
              selectedEffectCategory: "layout",
              actionOutcome: "blocked",
              externalOutcome: {status: "blocked", reason: "layout-overflow"},
              declaredReads: ["layout.editorGroup", "runtime.editor.monacoInstance"],
              declaredWrites: ["runtime.layoutObservation", "runtime.evidenceStrip"],
              runtimeConsequences: ["runtime.layoutObservation updated", "source unchanged"],
              nextAction: "inspect layout violation",
              layoutObservation: {
                kind: "mcel-layout-observation",
                source: "code-studio-component-owned-viewport",
                measured: true,
                regions: {editorGroup: true, bottomDock: true},
                metrics: {ownedDocumentHeight: 1440},
                documentHeightRatio: 1.4,
                violations: ["editor-workbench-overflow"],
                styleViolations: []
              }
            }),
            expected: {
              selectedEffect: "layout.observe",
              actionOutcome: "blocked",
              expectedDisposition: "BLOCKED",
              governanceOutcome: "pass",
              safetyOutcome: "pass",
              externalStatus: "blocked",
              runtimeOnlyWrites: true,
              sourceUnchanged: true,
              layoutMeasured: true,
              layoutViolationCount: 1,
              nextAction: "inspect layout violation"
            }
          }
        ];
      }

      function buildGenericScmReplayFixtures(options = {}) {
        const allFixtures = [
          ...buildWalletScmReplayFixtures(),
          ...buildCodeEditorScmReplayFixtures()
        ];
        if (!options.family || options.family === "all") return allFixtures;
        return allFixtures.filter((fixture) => fixture.family === options.family);
      }

      function listContainsAllScmFixtureValues(actual = [], expected = []) {
        const values = Array.isArray(actual) ? actual : [];
        const needles = Array.isArray(expected) ? expected : [];
        return needles.every((needle) => values.includes(needle));
      }

      function classifyScmReplayFixtureDisposition(vector = {}) {
        const governanceOutcome = normalizeReceiptOutcome(vector.governanceOutcome, "pass");
        const safetyOutcome = normalizeReceiptOutcome(vector.safetyOutcome, "pass");
        const actionOutcome = normalizeReceiptOutcome(vector.actionOutcome || vector.externalOutcome?.status, "waiting");
        if (governanceOutcome !== "pass" || safetyOutcome !== "pass") return "FAIL";
        if (actionOutcome === "exception") return "EXCEPTION";
        if (actionOutcome === "blocked") return "BLOCKED";
        if (actionOutcome === "pass") return "PASS";
        return "FAIL";
      }

      function buildScmReplayFixtureExpectationComparison(expected = {}, vector = {}, failures = []) {
        const observedDisposition = classifyScmReplayFixtureDisposition(vector);
        const expectedDisposition = expected.expectedDisposition || observedDisposition;
        const mismatched = failures.length > 0 || expectedDisposition !== observedDisposition;
        const disposition = mismatched ? "MISMATCH" : observedDisposition;
        const mismatchReasons = [...failures];
        if (expectedDisposition !== observedDisposition) {
          mismatchReasons.push(`expectedDisposition expected ${expectedDisposition} but saw ${observedDisposition}`);
        }
        return jsonSafeClone({
          expectedDisposition,
          observedDisposition,
          disposition,
          ok: !mismatched,
          mismatch: mismatched,
          mismatchReasons,
          expectedFields: {
            selectedEffect: expected.selectedEffect || "",
            actionOutcome: expected.actionOutcome || "",
            externalStatus: expected.externalStatus || "",
            governanceOutcome: expected.governanceOutcome || "",
            safetyOutcome: expected.safetyOutcome || "",
            nextAction: expected.nextAction || ""
          },
          observedFields: {
            selectedEffect: vector.selectedEffect || "",
            actionOutcome: vector.actionOutcome || "",
            externalStatus: vector.externalOutcome?.status || "",
            governanceOutcome: vector.governanceOutcome || "",
            safetyOutcome: vector.safetyOutcome || "",
            nextAction: vector.nextAction || ""
          }
        });
      }

      function summarizeScmReplayFixtureVector(vector = {}) {
        return jsonSafeClone({
          sourceKind: vector.sourceKind || "",
          selectedEffect: vector.selectedEffect || "",
          selectedEffectCategory: vector.selectedEffectCategory || "",
          actionOutcome: vector.actionOutcome || "",
          externalStatus: vector.externalOutcome?.status || "",
          governanceOutcome: vector.governanceOutcome || "",
          safetyOutcome: vector.safetyOutcome || "",
          proofCompleteness: vector.proofCompleteness || "",
          declaredReads: vector.declaredReads || [],
          declaredWrites: vector.declaredWrites || [],
          runtimeConsequences: vector.runtimeConsequences || [],
          nextAction: vector.nextAction || "",
          repairPacket: {
            status: vector.repairPacket?.status || "",
            generated: vector.repairPacket?.generated === true,
            liveAiCall: vector.repairPacket?.liveAiCall ?? null,
            forbiddenWrites: vector.repairPacket?.forbiddenWrites || [],
            boundaryBlocked: vector.repairPacket?.boundaryBlocked === true
          },
          txDraftBoundary: {
            status: vector.txDraftBoundary?.status || "",
            boundary: vector.txDraftBoundary?.boundary || "",
            noSend: vector.txDraftBoundary?.noSend === true
          },
          draftProvenance: {
            status: vector.draftProvenance?.status || "",
            eventType: vector.draftProvenance?.eventType || "",
            sourceMutationGate: vector.draftProvenance?.sourceMutationGate || "",
            runtimeOnlyUntilCommit: vector.draftProvenance?.runtimeOnlyUntilCommit !== false,
            sourceMutationsOnlyByCommitDraft: vector.draftProvenance?.sourceMutationsOnlyByCommitDraft !== false,
            events: vector.draftProvenance?.events || []
          },
          layoutObservation: {
            measured: vector.layoutObservation?.measured === true,
            violations: vector.layoutObservation?.violations || [],
            styleViolations: vector.layoutObservation?.styleViolations || []
          },
          checks: vector.checks || {}
        });
      }

      function assertGenericScmReplayFixtureVector(fixture = {}) {
        const vector = normalizeScmReceiptVector(fixture.receipt, {selectedEvidence: fixture.selectedEvidence || null});
        const expected = fixture.expected || {};
        const failures = [];
        const declaredWrites = vector.declaredWrites || [];
        const declaredReads = vector.declaredReads || [];
        const runtimeConsequences = vector.runtimeConsequences || [];
        const writesSource = declaredWrites.some((write) => String(write).startsWith("source."));

        if (vector.sourceKind === "not-ingested") failures.push("fixture receipt was not ingested");
        if (expected.selectedEffect && vector.selectedEffect !== expected.selectedEffect) {
          failures.push(`selectedEffect expected ${expected.selectedEffect} but saw ${vector.selectedEffect || "(empty)"}`);
        }
        if (expected.actionOutcome && vector.actionOutcome !== expected.actionOutcome) {
          failures.push(`actionOutcome expected ${expected.actionOutcome} but saw ${vector.actionOutcome || "(empty)"}`);
        }
        if (expected.externalStatus && vector.externalOutcome?.status !== expected.externalStatus) {
          failures.push(`externalOutcome.status expected ${expected.externalStatus} but saw ${vector.externalOutcome?.status || "(empty)"}`);
        }
        if (expected.governanceOutcome && vector.governanceOutcome !== expected.governanceOutcome) {
          failures.push(`governanceOutcome expected ${expected.governanceOutcome} but saw ${vector.governanceOutcome || "(empty)"}`);
        }
        if (expected.safetyOutcome && vector.safetyOutcome !== expected.safetyOutcome) {
          failures.push(`safetyOutcome expected ${expected.safetyOutcome} but saw ${vector.safetyOutcome || "(empty)"}`);
        }
        if (expected.nextAction && vector.nextAction !== expected.nextAction) {
          failures.push(`nextAction expected ${expected.nextAction} but saw ${vector.nextAction || "(empty)"}`);
        }
        if (expected.declaredReadsContain && !listContainsAllScmFixtureValues(declaredReads, expected.declaredReadsContain)) {
          failures.push(`declaredReads missing ${expected.declaredReadsContain.filter((item) => !declaredReads.includes(item)).join(", ")}`);
        }
        if (expected.declaredWritesContain && !listContainsAllScmFixtureValues(declaredWrites, expected.declaredWritesContain)) {
          failures.push(`declaredWrites missing ${expected.declaredWritesContain.filter((item) => !declaredWrites.includes(item)).join(", ")}`);
        }
        if (expected.forbiddenWritesContain && !listContainsAllScmFixtureValues(vector.repairPacket?.forbiddenWrites || [], expected.forbiddenWritesContain)) {
          failures.push(`forbiddenWrites missing ${expected.forbiddenWritesContain.filter((item) => !(vector.repairPacket?.forbiddenWrites || []).includes(item)).join(", ")}`);
        }
        if (expected.consequencesContain && !listContainsAllScmFixtureValues(runtimeConsequences, expected.consequencesContain)) {
          failures.push(`runtimeConsequences missing ${expected.consequencesContain.filter((item) => !runtimeConsequences.includes(item)).join(", ")}`);
        }
        if (expected.runtimeOnlyWrites === true && writesSource) {
          failures.push("runtime-only fixture declared a source write");
        }
        if (expected.sourceWriteEffect === true && !writesSource) {
          failures.push("source-write fixture did not declare a source write");
        }
        if (writesSource && expected.sourceMutationGate && vector.checks?.sourceMutationGate !== expected.sourceMutationGate) {
          failures.push(`source write was not gated by ${expected.sourceMutationGate}`);
        }
        if (expected.sourceUnchanged === true) {
          const sourceSafe = runtimeConsequences.includes("source unchanged")
            || runtimeConsequences.includes("source unchanged after external outcome")
            || vector.checks?.sourceSafeAfterExternalOutcome === true
            || vector.checks?.sourceSerializationClean === true;
          if (!sourceSafe) failures.push("fixture did not prove source unchanged");
        }
        if (expected.txDraftNoSend === true && vector.txDraftBoundary?.noSend !== true) {
          failures.push("txDraftBoundary.noSend was not true");
        }
        if (expected.txDraftProvenanceRecorded === true) {
          const provenance = vector.txDraftBoundary?.provenance || {};
          if (!provenance.sourceRequestHash || !provenance.walletAccountHash || !provenance.chainProof?.status) {
            failures.push("txDraftBoundary provenance was not recorded");
          }
        }
        if (expected.txDraftInvalidatedByContain) {
          const invalidatedBy = (vector.txDraftBoundary?.provenance?.invalidatedBy || [])
            .map((entry) => entry.reason || entry)
            .filter(Boolean);
          if (!listContainsAllScmFixtureValues(invalidatedBy, expected.txDraftInvalidatedByContain)) {
            failures.push(`txDraft invalidation missing ${expected.txDraftInvalidatedByContain.filter((item) => !invalidatedBy.includes(item)).join(", ")}`);
          }
        }
        if (expected.draftProvenanceEventType && vector.draftProvenance?.eventType !== expected.draftProvenanceEventType) {
          failures.push(`draftProvenance.eventType expected ${expected.draftProvenanceEventType} but saw ${vector.draftProvenance?.eventType || "(empty)"}`);
        }
        if (expected.draftRuntimeOnlyUntilCommit === true && vector.draftProvenance?.runtimeOnlyUntilCommit === false) {
          failures.push("draft provenance was not runtime-only until commit");
        }
        if (expected.sourceMutationsOnlyByCommitDraft === true && vector.draftProvenance?.sourceMutationsOnlyByCommitDraft === false) {
          failures.push("draft provenance allowed a source mutation outside commitDraft");
        }
        if (expected.repairPacketGenerated === true && vector.repairPacket?.generated !== true) {
          failures.push("repair packet was not marked generated");
        }
        if (expected.repairBoundaryBlocked === true && vector.repairPacket?.boundaryBlocked !== true) {
          failures.push("repair boundary was not marked blocked");
        }
        if (Object.prototype.hasOwnProperty.call(expected, "liveAiCall") && vector.repairPacket?.liveAiCall !== expected.liveAiCall) {
          failures.push(`liveAiCall expected ${expected.liveAiCall} but saw ${vector.repairPacket?.liveAiCall}`);
        }
        if (expected.layoutMeasured === true && vector.layoutObservation?.measured !== true) {
          failures.push("layout observation was not measured");
        }
        if (typeof expected.layoutViolationCount === "number" && (vector.layoutObservation?.violations || []).length !== expected.layoutViolationCount) {
          failures.push(`layout violation count expected ${expected.layoutViolationCount} but saw ${(vector.layoutObservation?.violations || []).length}`);
        }
        if (expected.checks) {
          Object.entries(expected.checks).forEach(([key, value]) => {
            if (vector.checks?.[key] !== value) failures.push(`check ${key} expected ${value} but saw ${vector.checks?.[key]}`);
          });
        }

        const comparison = buildScmReplayFixtureExpectationComparison(expected, vector, failures);
        return jsonSafeClone({
          id: fixture.id || "",
          family: fixture.family || "",
          label: fixture.label || "",
          ok: comparison.ok,
          disposition: comparison.disposition,
          expectedDisposition: comparison.expectedDisposition,
          observedDisposition: comparison.observedDisposition,
          mismatch: comparison.mismatch,
          mismatchReasons: comparison.mismatchReasons,
          failures: comparison.mismatchReasons,
          expected,
          comparison,
          vector: summarizeScmReplayFixtureVector(vector)
        });
      }

      function runGenericScmReplayFixturePack(options = {}) {
        const fixtures = buildGenericScmReplayFixtures(options);
        const results = fixtures.map((fixture) => assertGenericScmReplayFixtureVector(fixture));
        const failed = results.filter((result) => !result.ok);
        const dispositionCounts = results.reduce((counts, result) => {
          const disposition = result.disposition || "FAIL";
          counts[disposition] = (counts[disposition] || 0) + 1;
          return counts;
        }, {PASS: 0, BLOCKED: 0, EXCEPTION: 0, FAIL: 0, MISMATCH: 0});
        const mismatches = results
          .filter((result) => result.disposition === "MISMATCH" || result.mismatch === true)
          .map((result) => ({
            id: result.id,
            expectedDisposition: result.expectedDisposition,
            observedDisposition: result.observedDisposition,
            reasons: result.mismatchReasons || result.failures || []
          }));
        const familyCounts = results.reduce((counts, result) => {
          const family = result.family || "unknown";
          counts[family] = counts[family] || {total: 0, passed: 0, failed: 0, mismatched: 0};
          counts[family].total += 1;
          if (result.ok) counts[family].passed += 1;
          else counts[family].failed += 1;
          if (result.disposition === "MISMATCH") counts[family].mismatched += 1;
          return counts;
        }, {});
        return jsonSafeClone({
          kind: "mcel-code-studio-generic-scm-replay-fixture-pack",
          fixtureHarnessVersion: SCM_REPLAY_FIXTURE_HARNESS_VERSION,
          family: options.family || "all",
          ok: failed.length === 0,
          disposition: failed.length === 0 ? "PASS" : "MISMATCH",
          total: results.length,
          passed: results.length - failed.length,
          failed: failed.map((result) => result.id),
          mismatchCount: mismatches.length,
          mismatches,
          dispositionCounts,
          familyCounts,
          results
        });
      }

      function runScmRegressionScenario(id, label, action) {
        const before = buildScmRegressionSourceSnapshot(`${id}:before`);
        let detail = null;
        let exception = null;
        try {
          detail = action(before) || {};
        } catch (error) {
          exception = {
            name: error?.name || "Error",
            message: error?.message || String(error)
          };
        }
        const after = buildScmRegressionSourceSnapshot(`${id}:after`);
        const sourceSafety = compareScmRegressionSourceSnapshots(before, after);
        const safetyOk = sourceSafety.sourceUnchanged && sourceSafety.runtimeChromeStayedOutOfSource;
        const actionOk = !exception && detail?.ok !== false;
        const ok = actionOk && safetyOk;
        let disposition = "FAIL";
        if (safetyOk && detail?.disposition === "MISMATCH") {
          disposition = "MISMATCH";
        } else if (safetyOk && exception) {
          disposition = "EXCEPTION";
        } else if (safetyOk && actionOk === false) {
          disposition = "BLOCKED";
        } else if (safetyOk && actionOk) {
          disposition = "PASS";
        }
        return jsonSafeClone({
          id,
          label,
          ok,
          disposition,
          actionOk,
          safetyOk,
          sourceSafety,
          before,
          after,
          detail,
          exception
        });
      }

      function runScmRegressionHarness(options = {}) {
        const startedAt = new Date().toISOString();
        const previousPane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "source";
        const results = [];
        const add = (id, label, action) => {
          results.push(runScmRegressionScenario(id, label, action));
        };

        add("source.validation", "Validate source without mutating author-owned MCEL", () => {
          const report = validateSource();
          return {
            ok: report?.ok !== false,
            failed: report?.failed || [],
            checks: (report?.checks || []).map((check) => ({id: check.id, ok: check.ok}))
          };
        });

        add("runtime.mount-source-safe", "Mount runtime and verify Monaco/fallback chrome stays generated", () => {
          renderRuntime();
          const editor = runtimePreview.querySelector(".code-studio-runtime-editor");
          const host = runtimePreview.querySelector("#code-studio-runtime-monaco");
          const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
          return {
            ok: Boolean(editor && host && draft)
              && host.getAttribute("data-mc-generated") === "runtime"
              && host.getAttribute("data-mc-serialize") === "omit"
              && draft.getAttribute("data-code-studio-monaco-fallback") === "textarea",
            editorGenerated: editor?.getAttribute("data-mc-generated") || "",
            hostSerialize: host?.getAttribute("data-mc-serialize") || "",
            fallback: draft?.getAttribute("data-code-studio-monaco-fallback") || ""
          };
        });

        add("monaco.runtime-boundary", "Assert Monaco effects are runtime-only and commitDraft remains the source gate", () => {
          const manifest = window.McelCodeStudioScm?.manifest || window.McelCodeStudioScm?.surface || null;
          const declaredEffects = MONACO_RUNTIME_EFFECTS.map((effectName) => ({
            effectName,
            known: Boolean((manifest?.effects || {})[effectName] || (manifest?.effectSurface || {})[effectName])
          }));
          const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
          const host = runtimePreview.querySelector("#code-studio-runtime-monaco");
          return {
            ok: declaredEffects.length === 5 && Boolean(draft && host),
            declaredEffects,
            lastMonacoReceipt: studioState.lastMonacoRuntimeReceipt,
            recentMonacoReceipts: studioState.monacoRuntimeReceipts.slice(-5),
            sourceMutationGate: "commitRuntimeDraft"
          };
        });

        add("generic.wallet-fixtures", "Replay deterministic wallet receipt fixtures through the generic vector contract", () => {
          const fixturePack = runGenericScmReplayFixturePack({family: "wallet"});
          return {
            ok: fixturePack.ok,
            disposition: fixturePack.disposition,
            mismatchCount: fixturePack.mismatchCount,
            dispositionCounts: fixturePack.dispositionCounts,
            fixturePack
          };
        });

        add("generic.code-editor-fixtures", "Replay deterministic Code Studio, Monaco, and editorDraft provenance fixtures through the generic vector contract", () => {
          const fixturePack = runGenericScmReplayFixturePack({family: "code-editor"});
          return {
            ok: fixturePack.ok,
            disposition: fixturePack.disposition,
            mismatchCount: fixturePack.mismatchCount,
            dispositionCounts: fixturePack.dispositionCounts,
            fixturePack
          };
        });

        add("editorDraft.provenance-boundary", "Record draft provenance without allowing an uncommitted draft to mutate source", () => {
          const fields = workspaceFields();
          const file = selectedFile(fields);
          renderRuntime();
          const draft = runtimePreview.querySelector("#code-studio-runtime-draft");
          const baseline = collectEditorDraftProvenanceSummary();
          const changed = recordEditorDraftProvenance("changed", {
            origin: "regression-harness",
            text: `${draft?.value || file?.value || ""}\n// draft provenance probe`,
            nextAction: "discard probe draft"
          });
          const discarded = recordEditorDraftProvenance("discarded", {
            origin: "regression-harness",
            text: `${draft?.value || file?.value || ""}\n// draft provenance probe`,
            nextAction: "restore clean source draft"
          });
          const summary = collectEditorDraftProvenanceSummary();
          return {
            ok: summary.invariants.sourceMutationsOnlyByCommitDraft
              && summary.invariants.uncommittedDraftsRuntimeOnly
              && changed.effect === "editorDraft.changed"
              && discarded.effect === "editorDraft.discarded",
            beforeEvents: baseline.totalEvents,
            afterEvents: summary.totalEvents,
            summary
          };
        });

        add("replay.snapshot-comparison", "Build replay before/after evidence snapshots and compare violation deltas", () => {
          const summary = collectScmEvidenceSummary(studioState.lastReport);
          const entry = resolveSelectedScmEvidence(summary, studioState.scmEvidenceFilter || "all", visibleScmEvidenceEntries(summary, studioState.scmEvidenceFilter || "all"));
          const beforeReplay = buildScmReplaySnapshot("regression-before", entry, studioState.lastReport);
          const replayResult = runScmRuntimeChecks();
          const afterReplay = buildScmReplaySnapshot("regression-after", entry, studioState.lastReport);
          const comparison = compareScmReplaySnapshots(beforeReplay, afterReplay, replayResult);
          studioState.lastScmReplayResult = {
            evidenceKey: entry?.evidenceKey || "",
            label: evidenceEntryLabel(entry),
            scope: evidenceEntryScope(entry),
            ok: replayResult?.ok !== false,
            replayedAt: new Date().toISOString(),
            result: replayResult
          };
          studioState.lastScmReplaySnapshotComparison = comparison;
          return {
            ok: comparison.ok !== false,
            stable: comparison.stable,
            deltas: comparison.deltas
          };
        });

        add("serialization.clean-source", "Serialize clean source and prove runtime/Monaco chrome is omitted", () => {
          const clean = serializeCleanSource();
          return {
            ok: Boolean(clean)
              && !clean.includes('data-mc-generated="runtime"')
              && !clean.includes('data-mc-serialize="omit"')
              && !clean.includes("code-studio-runtime-monaco"),
            serializedLength: clean.length,
            omittedRuntimeChrome: !clean.includes("code-studio-runtime-monaco")
          };
        });

        if (options.restorePane !== false) {
          showPane(previousPane);
        }

        const failed = results.filter((scenario) => !scenario.ok);
        const dispositionCounts = results.reduce((counts, scenario) => {
          const disposition = scenario.disposition || "FAIL";
          counts[disposition] = (counts[disposition] || 0) + 1;
          return counts;
        }, {PASS: 0, BLOCKED: 0, EXCEPTION: 0, FAIL: 0, MISMATCH: 0});
        const fixturePacks = results
          .map((scenario) => scenario.detail?.fixturePack)
          .filter(Boolean);
        const fixtureMismatches = fixturePacks.flatMap((pack) => (pack.mismatches || [])
          .map((mismatch) => ({
            family: pack.family || "all",
            id: mismatch.id,
            expectedDisposition: mismatch.expectedDisposition,
            observedDisposition: mismatch.observedDisposition,
            reasons: mismatch.reasons || []
          })));
        const harness = jsonSafeClone({
          kind: "mcel-code-studio-scm-regression-harness",
          harnessVersion: SCM_REGRESSION_HARNESS_VERSION,
          ranAt: startedAt,
          completedAt: new Date().toISOString(),
          ok: failed.length === 0,
          total: results.length,
          passed: results.length - failed.length,
          failed: failed.map((scenario) => scenario.id),
          dispositionCounts,
          mismatchCount: fixtureMismatches.length,
          fixtureMismatches,
          sourceSafety: compareScmRegressionSourceSnapshots(results[0]?.before, buildScmRegressionSourceSnapshot("final")),
          scenarios: results,
          fixturePacks,
          monaco: {
            mounted: studioState.monacoMounted,
            lastReceipt: studioState.lastMonacoRuntimeReceipt,
            recentReceipts: studioState.monacoRuntimeReceipts.slice(-5),
            declaredEffects: [...MONACO_RUNTIME_EFFECTS]
          },
          draftProvenance: collectEditorDraftProvenanceSummary()
        });

        studioState.lastScmRegressionHarness = harness;
        studioState.lastScmRegressionHarnessExport = {
          ranAt: harness.ranAt,
          ok: harness.ok,
          total: harness.total,
          passed: harness.passed,
          failed: [...harness.failed],
          dispositionCounts: harness.dispositionCounts,
          mismatchCount: harness.mismatchCount,
          fixtureMismatches: harness.fixtureMismatches
        };
        setStatus(harness.ok
          ? `SCM regression harness passed ${harness.passed}/${harness.total}; fixture dispositions matched expected semantics.`
          : `SCM regression harness found ${harness.failed.length} failing scenario(s), ${harness.mismatchCount || 0} fixture mismatch(es): ${harness.failed.join(", ")}.`);
        renderScmEvidencePanel(studioState.lastReport);
        return harness;
      }

      function formatScmRegressionHarnessDetail(harness = studioState.lastScmRegressionHarness) {
        if (!harness) {
          return {
            kind: "mcel-code-studio-scm-regression-harness",
            harnessVersion: SCM_REGRESSION_HARNESS_VERSION,
            workbenchSummary: summarizeScmReplayExpectationsForWorkbench(null),
            status: "Run the SCM regression harness to replay source-safe validation, runtime mount, Monaco boundary, generic receipt fixtures, replay, and serialization scenarios."
          };
        }
        const workbenchSummary = summarizeScmReplayExpectationsForWorkbench(harness);
        return {
          kind: harness.kind,
          harnessVersion: harness.harnessVersion,
          workbenchSummary,
          replayExpectationFailures: {
            mismatchCount: workbenchSummary.mismatchCount || 0,
            mismatchDetails: workbenchSummary.mismatchDetails || [],
            issueRows: workbenchSummary.issueRows || []
          },
          fixturePackStatus: workbenchSummary.fixturePackStatus || [],
          ranAt: harness.ranAt,
          completedAt: harness.completedAt,
          ok: harness.ok,
          total: harness.total,
          passed: harness.passed,
          failed: harness.failed,
          dispositionCounts: harness.dispositionCounts || {},
          dispositionSummary: workbenchSummary.dispositionSummary,
          mismatchCount: harness.mismatchCount || 0,
          fixtureMismatches: harness.fixtureMismatches || [],
          sourceSafety: harness.sourceSafety,
          monaco: harness.monaco,
          draftProvenance: harness.draftProvenance,
          fixturePacks: harness.fixturePacks || [],
          scenarios: harness.scenarios.map((scenario) => ({
            id: scenario.id,
            disposition: scenario.disposition || (scenario.exception ? "EXCEPTION" : (scenario.ok ? "PASS" : (scenario.actionOk === false ? "BLOCKED" : "FAIL"))),
            ok: scenario.ok,
            actionOk: scenario.actionOk,
            sourceSafety: scenario.sourceSafety,
            detail: scenario.detail,
            exception: scenario.exception
          }))
        };
      }

      function formatScmReplayExpectationFailuresDetail(harness = studioState.lastScmRegressionHarness) {
        const summary = summarizeScmReplayExpectationsForWorkbench(harness);
        return jsonSafeClone({
          kind: "mcel-code-studio-replay-expectation-failures-workbench-detail",
          status: summary.state,
          label: summary.label,
          mismatchFirst: summary.mismatchDetails || [],
          fixturePackStatus: summary.fixturePackStatus || [],
          dispositionSummary: summary.dispositionSummary,
          nextAction: summary.mismatchCount
            ? "Inspect expectedDisposition versus observedDisposition before trusting replay regression output."
            : "No replay fixture expectation mismatches recorded. Inspect fixture pack status for blocked or exception scenarios.",
          rawRegressionHarness: harness || null
        });
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
            runtimePackage: MCEL_RUNTIME_PACKAGE_VERSION,
            regressionHarness: SCM_REGRESSION_HARNESS_VERSION
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
          lastRegressionHarness: studioState.lastScmRegressionHarness,
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

      function normalizeTxDraftInvalidationReasons(invalidatedBy = []) {
        return (Array.isArray(invalidatedBy) ? invalidatedBy : [])
          .map((entry) => entry?.reason || entry?.kind || entry?.event || entry)
          .map((entry) => String(entry || "").trim())
          .filter(Boolean);
      }

      function summarizeTxDraftProvenanceForWorkbench(receiptVector = studioState.lastScmReceiptVector) {
        const vector = receiptVector || null;
        const boundary = vector?.txDraftBoundary || {};
        const provenance = boundary?.provenance || vector?.txDraftProvenance || {};
        const invalidatedBy = normalizeTxDraftInvalidationReasons(provenance.invalidatedBy);
        const externalOutcomeSequence = Array.isArray(provenance.externalOutcomeSequence) ? provenance.externalOutcomeSequence : [];
        const networkGateSequence = Array.isArray(provenance.networkGateSequence) ? provenance.networkGateSequence : [];
        const probeEnvelopeIds = Array.isArray(provenance.probeEnvelopeIds) ? provenance.probeEnvelopeIds : [];
        const chainProofStatus = provenance.chainProof?.status || provenance.chainProof?.chainId || "";
        const freshnessStatus = provenance.freshnessStatus || provenance.provenanceFreshness?.status || "";
        const freshnessAction = provenance.freshnessAction || provenance.provenanceFreshness?.action || "";
        const consumerGate = boundary.consumerGate || provenance.consumerGate || {};
        const consumerGateStatus = consumerGate.status || "";
        const consumerGateAction = consumerGate.action || "";
        const endgamePreflight = boundary.endgamePreflight || provenance.endgamePreflight || consumerGate.endgamePreflight || {};
        const sendSignPreflightStatus = endgamePreflight.status || "locked-no-draft";
        const sendSignPreflightLabel = `${sendSignPreflightStatus} · canSend=${endgamePreflight.canSend === true} canSign=${endgamePreflight.canSign === true} canBroadcast=${endgamePreflight.canBroadcast === true}`;
        const consumerGateReasons = Array.isArray(consumerGate.invalidationReasons)
          ? consumerGate.invalidationReasons
          : normalizeTxDraftInvalidationReasons(consumerGate.invalidatedBy || []);
        const hasProvenance = Boolean(
          provenance.provenanceVersion
          || provenance.sourceRequestHash
          || provenance.walletAccountHash
          || chainProofStatus
          || externalOutcomeSequence.length
          || networkGateSequence.length
          || probeEnvelopeIds.length
        );
        const observed = Boolean(vector && vector.sourceKind !== "not-ingested" && (
          boundary.status
          || boundary.boundary
          || boundary.noSend === true
          || hasProvenance
        ));
        let state = "not observed";
        if (!observed) {
          state = "not observed";
        } else if (invalidatedBy.length || freshnessStatus === "invalidated") {
          state = "invalidated";
        } else if (consumerGateStatus === "blocked" && observed) {
          state = "consumer blocked";
        } else if (boundary.noSend !== true && provenance.noSendBoundaryPreserved !== true) {
          state = "needs inspection";
        } else if (provenance.valid === true || freshnessStatus === "valid") {
          state = "valid";
        } else if (freshnessStatus === "stale") {
          state = "stale";
        } else if (hasProvenance) {
          state = "provenance present";
        } else {
          state = "missing provenance";
        }
        const label = state === "valid"
          ? `valid · no-send · ${chainProofStatus || "chain proof pending"}`
          : (state === "provenance present"
            ? `provenance present · no-send · ${chainProofStatus || "chain proof pending"}`
            : (state === "invalidated"
              ? `invalidated · ${invalidatedBy.join(", ")}`
              : (state === "consumer blocked"
                ? `consumer blocked · ${consumerGateReasons.join(", ") || consumerGateAction || "freshness not proven"}`
                : (state === "needs inspection"
                  ? `needs inspection · ${boundary.boundary || "boundary unclear"}`
                  : (state === "stale"
                    ? `stale · ${freshnessAction || "rebuild draft to prove freshness"}`
                    : state)))));
        return jsonSafeClone({
          kind: "mcel-code-studio-tx-draft-provenance-workbench-summary",
          state,
          label,
          boundaryStatus: boundary.status || "not-observed",
          boundary: boundary.boundary || "",
          noSend: boundary.noSend === true,
          provenanceVersion: provenance.provenanceVersion || "",
          sourceRequestHash: provenance.sourceRequestHash || "",
          selectedRequestSnapshot: provenance.selectedRequestSnapshot || null,
          walletAccountHash: provenance.walletAccountHash || "",
          chainProofStatus,
          calldataSource: provenance.calldataSource || "",
          abiEncodingStatus: provenance.abiEncodingStatus || "",
          externalOutcomeCount: externalOutcomeSequence.length,
          networkGateCount: networkGateSequence.length,
          probeEnvelopeCount: probeEnvelopeIds.length,
          invalidatedBy,
          freshnessStatus,
          freshnessAction,
          noSendBoundaryPreserved: provenance.noSendBoundaryPreserved === true,
          provenanceEnforced: provenance.provenanceEnforced === true,
          consumerGate,
          consumerGateStatus,
          consumerGateReasons,
          consumerGateAction,
          endgamePreflight,
          sendSignPreflightStatus,
          sendSignPreflightLabel,
          nextAction: consumerGateAction || freshnessAction || (state === "invalidated" || state === "consumer blocked" ? "rebuild draft from current receipt" : ""),
          valid: state === "valid",
          provenancePresent: hasProvenance,
          raw: {
            boundary,
            provenance,
            consumerGate
          }
        });
      }

      function summarizeScmReplayFixturePackForWorkbench(pack = {}) {
        const counts = pack.dispositionCounts || {};
        const mismatchCount = pack.mismatchCount || (pack.mismatches || []).length || 0;
        const state = mismatchCount > 0 || pack.disposition === "MISMATCH"
          ? "mismatches"
          : ((counts.FAIL || 0) > 0
            ? "failures"
            : ((counts.EXCEPTION || 0) > 0
              ? "exceptions"
              : ((counts.BLOCKED || 0) > 0 ? "blocked" : "all matched")));
        return jsonSafeClone({
          family: pack.family || "all",
          state,
          label: `${pack.family || "all"}: ${state} · ${pack.passed || 0}/${pack.total || 0} matched`,
          ok: pack.ok !== false && mismatchCount === 0,
          total: pack.total || 0,
          passed: pack.passed || 0,
          failed: pack.failed || [],
          mismatchCount,
          dispositionCounts: counts,
          dispositionSummary: formatScmDispositionCounts(counts),
          mismatches: pack.mismatches || []
        });
      }

      function summarizeScmReplayExpectationsForWorkbench(harness = studioState.lastScmRegressionHarness) {
        if (!harness) {
          return jsonSafeClone({
            kind: "mcel-code-studio-replay-expectation-workbench-summary",
            state: "not run",
            label: "not run",
            mismatchCount: 0,
            dispositionSummary: formatScmDispositionCounts({PASS: 0, BLOCKED: 0, EXCEPTION: 0, FAIL: 0, MISMATCH: 0}),
            fixturePackStatus: [],
            mismatchDetails: [],
            issueRows: ["Run the SCM regression harness to compare replay fixtures against expected dispositions."]
          });
        }

        const fixturePackStatus = (harness.fixturePacks || []).map((pack) => summarizeScmReplayFixturePackForWorkbench(pack));
        const mismatchDetails = (harness.fixtureMismatches || []).map((mismatch) => ({
          family: mismatch.family || "all",
          id: mismatch.id || "",
          expectedDisposition: mismatch.expectedDisposition || "",
          observedDisposition: mismatch.observedDisposition || "",
          reasons: mismatch.reasons || []
        }));
        const mismatchCount = harness.mismatchCount || mismatchDetails.length || fixturePackStatus.reduce((count, pack) => count + (pack.mismatchCount || 0), 0);
        const counts = harness.dispositionCounts || {};
        const blockedCount = counts.BLOCKED || 0;
        const exceptionCount = counts.EXCEPTION || 0;
        const failCount = counts.FAIL || 0;
        const state = mismatchCount > 0
          ? "mismatches"
          : (failCount > 0
            ? "failures"
            : (exceptionCount > 0
              ? "exceptions"
              : (blockedCount > 0 ? "blocked" : "all matched")));
        const issueRows = mismatchDetails.length
          ? mismatchDetails.slice(0, 6).map((mismatch) => `${mismatch.family}/${mismatch.id}: expected ${mismatch.expectedDisposition || "?"} but observed ${mismatch.observedDisposition || "?"}${mismatch.reasons?.length ? ` · ${mismatch.reasons.join("; ")}` : ""}`)
          : [`Fixture pack status: ${state}. ${formatScmDispositionCounts(counts)}`];

        return jsonSafeClone({
          kind: "mcel-code-studio-replay-expectation-workbench-summary",
          state,
          label: mismatchCount > 0
            ? `MISMATCH ${mismatchCount} replay expectation mismatch(es)`
            : `${state} · ${harness.passed || 0}/${harness.total || 0} scenarios`,
          total: harness.total || 0,
          passed: harness.passed || 0,
          failed: harness.failed || [],
          mismatchCount,
          dispositionCounts: counts,
          dispositionSummary: formatScmDispositionCounts(counts),
          fixturePackStatus,
          mismatchDetails,
          issueRows
        });
      }

      function summarizeScmRegressionStatusForWorkbench(harness = studioState.lastScmRegressionHarness) {
        if (!harness) {
          return {
            label: "not run",
            total: 0,
            passed: 0,
            failed: [],
            blocked: [],
            exceptions: [],
            mismatches: [],
            mismatchCount: 0,
            dispositionCounts: {},
            dispositionSummary: formatScmDispositionCounts({PASS: 0, BLOCKED: 0, EXCEPTION: 0, FAIL: 0, MISMATCH: 0}),
            fixturePacks: [],
            fixturePackStatus: [],
            replayExpectations: summarizeScmReplayExpectationsForWorkbench(null)
          };
        }
        const scenarios = Array.isArray(harness.scenarios) ? harness.scenarios : [];
        const blocked = scenarios
          .filter((scenario) => scenario.disposition === "BLOCKED")
          .map((scenario) => scenario.id);
        const exceptions = scenarios
          .filter((scenario) => scenario.disposition === "EXCEPTION")
          .map((scenario) => scenario.id);
        const mismatches = scenarios
          .filter((scenario) => scenario.disposition === "MISMATCH")
          .map((scenario) => scenario.id);
        const replayExpectations = summarizeScmReplayExpectationsForWorkbench(harness);
        const label = replayExpectations.mismatchCount > 0
          ? replayExpectations.label
          : (harness.ok ? `PASS ${harness.passed}/${harness.total}` : `FAIL ${(harness.failed || []).length}/${harness.total}`);
        return jsonSafeClone({
          label,
          total: harness.total || scenarios.length,
          passed: harness.passed || 0,
          failed: harness.failed || [],
          blocked,
          exceptions,
          mismatches,
          mismatchCount: replayExpectations.mismatchCount || 0,
          dispositionCounts: harness.dispositionCounts || {},
          dispositionSummary: formatScmDispositionCounts(harness.dispositionCounts || {}),
          fixturePacks: harness.fixturePacks || [],
          fixturePackStatus: replayExpectations.fixturePackStatus || [],
          replayExpectations
        });
      }

      function formatNormalizedScmReceiptVectorDetail(receiptVector = collectScmReceiptVector(studioState.lastReport)) {
        const vector = receiptVector || normalizeScmReceiptVector(null);
        const receiptSource = summarizeScmReceiptSourceForWorkbench(vector);
        const txDraftProvenance = summarizeTxDraftProvenanceForWorkbench(vector);
        const mcelCommitBoundary = summarizeMcelCommitBoundaryForWorkbench(vector);
        const mcelProofDockSpecimens = collectMcelProofDockUnifiedSpecimens(vector);
        const regression = summarizeScmRegressionStatusForWorkbench();
        const replayWorkbench = summarizeScmReplayComparisonForWorkbench();
        const replayExpectations = summarizeScmReplayExpectationsForWorkbench();
        return jsonSafeClone({
          kind: "mcel-code-studio-normalized-scm-receipt-workbench-detail",
          replayWorkbench,
          replayExpectations,
          receiptVectorVersion: vector.vectorVersion || SCM_RECEIPT_VECTOR_VERSION,
          sourceKind: vector.sourceKind || "not-ingested",
          receiptSource,
          receiptSourceAuthority: vector.receiptSource || {},
          status: vector.status || "waiting",
          selectedEffect: vector.selectedEffect || "",
          actionOutcome: vector.actionOutcome || "waiting",
          externalOutcome: vector.externalOutcome || {},
          governanceOutcome: vector.governanceOutcome || "waiting",
          safetyOutcome: vector.safetyOutcome || "waiting",
          proofCompleteness: vector.proofCompleteness || "waiting",
          declaredReads: vector.declaredReads || [],
          declaredWrites: vector.declaredWrites || [],
          runtimeConsequences: vector.runtimeConsequences || [],
          nextAction: vector.nextAction || "",
          repairPacket: vector.repairPacket || {},
          txDraftProvenance,
          mcelCommitBoundary,
          mcelProofDockSpecimens,
          commitBoundary: vector.commitBoundary || {},
          layoutObservation: vector.layoutObservation || {},
          replay: {
            lastReplaySnapshotComparison: studioState.lastScmReplaySnapshotComparison || null,
            lastReplayResult: studioState.lastScmReplayResult || null
          },
          regression,
          actionableGaps: buildActionableScmGaps(
            collectScmEvidenceSummary(studioState.lastReport),
            collectGateStatus(studioState.lastReport?.scm || studioState.lastScmGates),
            buildEffectGraphModel(
              collectScmEvidenceSummary(studioState.lastReport),
              collectGateStatus(studioState.lastReport?.scm || studioState.lastScmGates),
              workspaceFields(),
              null
            ),
            null
          ),
          rawReceiptVector: vector
        });
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
        const receiptSource = summarizeScmReceiptSourceForWorkbench(receiptVector);
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
        const txDraftProvenance = summarizeTxDraftProvenanceForWorkbench(receiptVector);
        const mcelCommitBoundary = summarizeMcelCommitBoundaryForWorkbench(receiptVector);
        const mcelProofDockSpecimens = collectMcelProofDockUnifiedSpecimens(receiptVector);
        const regressionStatus = summarizeScmRegressionStatusForWorkbench();
        const replayWorkbench = summarizeScmReplayComparisonForWorkbench(replayComparison);
        const replayExpectations = summarizeScmReplayExpectationsForWorkbench();
        const gaps = buildActionableScmGaps(summary, gates, effectGraph, selectedEvidence);
        if (receiptVectorIngested && receiptSource.current === false) {
          gaps.unshift(`Receipt vector source is ${receiptSource.freshness}: ${receiptSource.reason || receiptSource.guidance || "refresh or select a current receipt source."}`);
        }
        if (receiptVectorIngested && mcelCommitBoundary.observed && mcelCommitBoundary.locked !== true) {
          gaps.unshift("MCEL 18N commit boundary needs inspection: a serious action boundary is not locked.");
        }
        if (receiptVectorIngested && txDraftProvenance.state === "invalidated") {
          gaps.unshift(`txDraft provenance invalidated: ${txDraftProvenance.invalidatedBy.join(", ")}. Action: ${txDraftProvenance.nextAction || "rebuild draft from current receipt"}.`);
        } else if (receiptVectorIngested && txDraftProvenance.state === "consumer blocked") {
          gaps.unshift(`txDraft consumer gate blocked: ${txDraftProvenance.consumerGateReasons.join(", ") || txDraftProvenance.consumerGateAction || "freshness not proven"}. Action: ${txDraftProvenance.nextAction || "rebuild draft from current receipt"}.`);
        } else if (receiptVectorIngested && txDraftProvenance.state === "stale") {
          gaps.unshift(`txDraft provenance is stale: ${txDraftProvenance.nextAction || "rebuild draft to prove freshness"}.`);
        } else if (receiptVectorIngested && txDraftProvenance.state === "needs inspection") {
          gaps.unshift("txDraft boundary needs inspection before any future send/sign work.");
        } else if (receiptVectorIngested && txDraftProvenance.state === "missing provenance" && receiptVector?.selectedEffect === "release.draftTx") {
          gaps.unshift("txDraft receipt is missing auditable provenance fields.");
        }
        const activePane = root.querySelector("[data-code-studio-pane].active")?.dataset.codeStudioPane || "source";
        const selected = selectedFile(fields);
        return {
          receiptMode,
          receiptOk,
          receiptVector,
          mcelProofDockSpecimens,
          effectGraph: effectGraph.effects,
          actionableGaps: gaps,
          receiptRows: [
            ["Mode", receiptMode],
            ["Receipt", receiptLabel],
            ["Receipt source", receiptSource.label],
            ["Receipt freshness", receiptSource.current ? "current" : receiptSource.freshness],
            ["Selected effect", vectorEffect || selectedEffect.name || "none"],
            ["Action outcome", receiptVector?.actionOutcome || "waiting"],
            ["External outcome", receiptVector?.externalOutcome?.status && receiptVector.externalOutcome.status !== "waiting"
              ? `${receiptVector.externalOutcome.status}${receiptVector.externalOutcome.reason ? ` · ${receiptVector.externalOutcome.reason}` : ""}`
              : "not ingested"],
            ["Governance / Safety", `${receiptVector?.governanceOutcome || "waiting"} / ${receiptVector?.safetyOutcome || "waiting"}`],
            ["Proof completeness", receiptVector?.proofCompleteness || "waiting"],
            ["Tx draft boundary", receiptVector?.txDraftBoundary?.boundary || "not observed"],
            ["Tx draft provenance", txDraftProvenance.label],
            ["Tx draft consumer gate", txDraftProvenance.consumerGateStatus || "not observed"],
            ["Tx draft action", txDraftProvenance.nextAction || txDraftProvenance.freshnessAction || "none"],
            ["MCEL 18N boundary", mcelCommitBoundary.label],
            ["MCEL 18N receipt", `${mcelCommitBoundary.receiptStatus || "not observed"} · mutationExecuted=${mcelCommitBoundary.mutationExecuted === true}`],
            ["MCEL 18N specimens", `${mcelProofDockSpecimens.specimenCount || 0} · wallet locked=${mcelProofDockSpecimens.walletLocked === true}`],
            ["Next action", receiptVector?.nextAction || "none"],
            ["Raw payloads", "Receipt Vector in Bottom Proof Dock"]
          ],
          selectedEffectRows: [
            ["Effect", vectorEffect || selectedEffect.name || "none selected"],
            ["Status", receiptVector?.actionOutcome && receiptVector.actionOutcome !== "waiting" ? receiptVector.actionOutcome : (selectedEffect.status || "waiting")],
            ["Trigger", selectedEffect.trigger || selectedEffect.kind || receiptVector?.selectedEffectCategory || "not declared"],
            ["Reads", formatScmSurfaceList(receiptVector?.declaredReads?.length ? receiptVector.declaredReads : selectedEffect.reads)],
            ["Writes", formatScmSurfaceList(receiptVector?.declaredWrites?.length ? receiptVector.declaredWrites : selectedEffect.writes)],
            ["Runtime consequences", formatScmSurfaceList(receiptVector?.runtimeConsequences, "none recorded")],
            ["Source", `${selectedEffect.sourcePath || receiptSource.authority || receiptVector?.sourceKind || "evidence"} · ${selectedEffect.sourceLabel || receiptSource.freshness || "receipt vector normalized"} `]
          ],
          currentRuntimeRows: [
            ["Active pane", activePane],
            ["Mounted", studioState.mounted ? "mounted" : "not mounted"],
            ["Dirty state", studioState.dirty ? "dirty" : "clean"],
            ["Selected file", selected?.path || studioState.selectedPath || "none"],
            ["Tx draft provenance", txDraftProvenance.label],
            ["Send/sign preflight", txDraftProvenance.sendSignPreflightLabel || "locked-no-draft · canSend=false canSign=false canBroadcast=false"],
            ["Receipt source", receiptSource.label],
            ["Runtime chrome", "runtime preview, editor UI, evidence, assistant output"],
            ["Route key", currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())]
          ],
          proofHistoryRows: [
            ["Receipt vector", receiptVectorIngested ? `${receiptVector.sourceKind || "ingested"} · ${receiptVector.vectorVersion || SCM_RECEIPT_VECTOR_VERSION}` : "not ingested"],
            ["Receipt authority", `${receiptSource.authority} · ${receiptSource.current ? "current" : receiptSource.freshness}`],
            ["Unified 18N specimens", `${mcelProofDockSpecimens.specimenCount || 0} specimen(s)`],
            ["Replay", replayWorkbench.label],
            ["Replay expectations", replayExpectations.label],
            ["Regression harness", regressionStatus.label],
            ["Serialization", gates.serialization?.ok === false ? "fail" : studioState.lastSerializationGate ? "clean source checked" : "not run"],
            ["Repair", gates.repair?.ok === false ? "fail" : studioState.lastRepairGate ? "scoped repair checked" : "not run"],
            ["Persistence", `${persistence.status || "not saved"}${persistence.savedAt ? ` · ${persistence.savedAt}` : ""}`],
            ["Contract helper", contractAuthoring ? "generated" : "not generated"]
          ],
          txDraftProvenance,
          receiptSource,
          regressionStatus,
          replayWorkbench,
          replayExpectations
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
        const draftProvenance = collectEditorDraftProvenanceSummary();
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
            ["Editor draft provenance", `${draftProvenance.totalEvents} event(s) · ${draftProvenance.invariants.sourceMutationsOnlyByCommitDraft ? "commit-gated" : "needs inspection"}`],
            ["txDraft provenance", receiptSurface.txDraftProvenance?.label || "not observed"],
            ["Send/sign preflight", receiptSurface.txDraftProvenance?.sendSignPreflightLabel || "locked-no-draft · canSend=false canSign=false canBroadcast=false"],
            ["Mounted", studioState.mounted ? "mounted" : "not mounted"],
            ["Persistence", `${persistence.status || "not saved"}${persistence.savedAt ? ` · ${persistence.savedAt}` : ""}`],
            ["Route key", currentScmRouteKey(routeParamsForScm(fields), routeQueryForScm())],
            ["Replay", receiptSurface.replayWorkbench?.label || "not run"]
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
        const summaryRows = Array.isArray(options.summaryRows) ? options.summaryRows.filter((row) => row && row.length) : [];
        const issueRows = Array.isArray(options.issueRows) ? options.issueRows.filter(Boolean) : [];
        const payload = typeof detail === "string" ? detail : JSON.stringify(jsonSafeClone(detail), null, 2);
        const summaryMarkup = summaryRows.length || issueRows.length
          ? `<section class="code-studio-proof-detail-summary" aria-label="Proof detail summary">
              ${summaryRows.length ? `<dl>${summaryRows.map((row) => `<div><dt>${escapeHtml(row[0])}</dt><dd>${escapeHtml(row[1])}</dd></div>`).join("")}</dl>` : ""}
              ${issueRows.length ? `<div class="code-studio-proof-detail-issues"><strong>Mismatch-first findings</strong><ul>${issueRows.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : ""}
            </section>`
          : "";
        proofDockDetailPanel.hidden = false;
        proofDockDetailPanel.dataset.proofKind = kind;
        proofDockDetailPanel.innerHTML = `
          <div class="code-studio-proof-detail-heading">
            <strong>${escapeHtml(title || "Proof detail")}</strong>
            <span>${escapeHtml(kind)}</span>
            ${action ? `<button type="button" data-code-studio-proof-action="${escapeHtml(action)}">Copy</button>` : ""}
          </div>
          ${summaryMarkup}
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
        const workbench = summarizeScmReplayComparisonForWorkbench(studioState.lastScmReplaySnapshotComparison);
        return renderProofDockPayload("Replay snapshot comparison", formatScmReplayComparisonDetail(studioState.lastScmReplaySnapshotComparison), {
          kind: "replay-comparison",
          action: "copy-replay-comparison",
          summaryRows: [
            ["Replay status", workbench.label],
            ["Selected evidence", workbench.selectedEvidenceLabel],
            ["Delta summary", workbench.deltaSummary]
          ],
          issueRows: workbench.issueRows
        });
      }

      function renderScmRegressionHarnessInProofDock() {
        const workbench = summarizeScmReplayExpectationsForWorkbench(studioState.lastScmRegressionHarness);
        return renderProofDockPayload("SCM regression harness", formatScmRegressionHarnessDetail(studioState.lastScmRegressionHarness), {
          kind: "regression-harness",
          action: "copy-regression-harness",
          summaryRows: [
            ["Fixture pack status", workbench.label],
            ["Disposition counts", workbench.dispositionSummary],
            ["Mismatch count", String(workbench.mismatchCount || 0)]
          ],
          issueRows: workbench.issueRows
        });
      }

      function renderScmReplayExpectationFailuresInProofDock() {
        const workbench = summarizeScmReplayExpectationsForWorkbench(studioState.lastScmRegressionHarness);
        return renderProofDockPayload("Replay expectation failures", formatScmReplayExpectationFailuresDetail(studioState.lastScmRegressionHarness), {
          kind: "replay-expectation-failures",
          action: "copy-replay-expectation-failures",
          summaryRows: [
            ["Replay expectations", workbench.label],
            ["Fixture packs", workbench.fixturePackStatus?.map((pack) => pack.label).join(" | ") || "none"],
            ["Disposition counts", workbench.dispositionSummary]
          ],
          issueRows: workbench.issueRows
        });
      }

      function renderScmReceiptVectorInProofDock() {
        const summary = collectScmEvidenceSummary(studioState.lastReport);
        const selectedEntry = resolveSelectedScmEvidence(summary, studioState.scmEvidenceFilter || "all", visibleScmEvidenceEntries(summary, studioState.scmEvidenceFilter || "all"));
        const vector = collectScmReceiptVector(studioState.lastReport, summary, selectedEntry);
        return renderProofDockPayload("Normalized SCM receipt vector", formatNormalizedScmReceiptVectorDetail(vector), {
          kind: "normalized-receipt-vector",
          action: "copy-normalized-receipt-vector"
        });
      }

      function renderMcelUnifiedProofDockSpecimensInProofDock() {
        const summary = collectScmEvidenceSummary(studioState.lastReport);
        const selectedEntry = resolveSelectedScmEvidence(summary, studioState.scmEvidenceFilter || "all", visibleScmEvidenceEntries(summary, studioState.scmEvidenceFilter || "all"));
        const vector = collectScmReceiptVector(studioState.lastReport, summary, selectedEntry);
        const specimens = collectMcelProofDockUnifiedSpecimens(vector);
        return renderProofDockPayload("Unified MCEL 18N proof dock specimens", specimens, {
          kind: "mcel-proof-dock-unified-commit-boundary-specimens",
          action: "copy-mcel-proof-dock-unified-specimens",
          summaryRows: [
            ["Specimens", String(specimens.specimenCount || 0)],
            ["Code Studio", String(specimens.codeStudioSpecimenCount || 0)],
            ["Wallet", String(specimens.walletSpecimenCount || 0)],
            ["Wallet locked", String(specimens.walletLocked === true)],
            ["Mutation receipts", String(specimens.mutationExecutedCount || 0)]
          ],
          issueRows: specimens.blockers || []
        });
      }

      function renderEditorDraftProvenanceInProofDock() {
        return renderProofDockPayload("Draft provenance", formatEditorDraftProvenanceDetail(), {
          kind: "draft-provenance",
          action: "copy-draft-provenance"
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
        const receiptVector = collectScmReceiptVector(report, summary, selectedEntry);
        const txDraftProvenance = summarizeTxDraftProvenanceForWorkbench(receiptVector);
        const mcelCommitBoundary = summarizeMcelCommitBoundaryForWorkbench(receiptVector);
        const replayWorkbench = summarizeScmReplayComparisonForWorkbench(studioState.lastScmReplaySnapshotComparison);
        const replayExpectations = summarizeScmReplayExpectationsForWorkbench(studioState.lastScmRegressionHarness);
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
              <button type="button" id="code-studio-run-scm-regression-harness">Run regression harness</button>
              <button type="button" id="code-studio-open-scm-evidence-detail">Open evidence detail in proof dock</button>
              <button type="button" id="code-studio-open-scm-receipt-vector-detail">Open receipt vector in proof dock</button>
              <button type="button" id="code-studio-open-mcel-18n-specimens-detail">Open 18N specimens in proof dock</button>
              <button type="button" id="code-studio-open-scm-replay-detail">Open replay in proof dock</button>
              <button type="button" id="code-studio-open-scm-regression-detail">Open regression in proof dock</button>
              <button type="button" id="code-studio-open-scm-replay-expectation-failures">Open replay expectation failures in proof dock</button>
              <button type="button" id="code-studio-open-draft-provenance-detail">Open editor draft provenance in proof dock</button>
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
            <span>receipt vector <code>${receiptVector.sourceKind === "not-ingested" ? "idle" : "ingested"}</code></span>
            <span>replay <code>${replayWorkbench.state}</code></span>
            <span>fixture packs <code>${replayExpectations.state}</code></span>
            <span>regression <code>${studioState.lastScmRegressionHarness?.mismatchCount ? "mismatch" : (studioState.lastScmRegressionHarness?.ok === false ? "fail" : studioState.lastScmRegressionHarness ? "ok" : "idle")}</code></span>
            <span>txDraft provenance <code>${txDraftProvenance.state}</code></span>
            <span>18N boundary <code>${mcelCommitBoundary.status}</code></span>
            <span>18N specimens <code>${collectMcelProofDockUnifiedSpecimens(receiptVector).specimenCount}</code></span>
            <span>editor draft provenance <code>${collectEditorDraftProvenanceSummary().invariants.sourceMutationsOnlyByCommitDraft ? "ok" : "fail"}</code></span>
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

        scmEvidencePanel.querySelector("#code-studio-open-scm-receipt-vector-detail")?.addEventListener("click", () => {
          renderScmReceiptVectorInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-mcel-18n-specimens-detail")?.addEventListener("click", () => {
          renderMcelUnifiedProofDockSpecimensInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-replay-detail")?.addEventListener("click", () => {
          renderReplayComparisonInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-regression-detail")?.addEventListener("click", () => {
          renderScmRegressionHarnessInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-replay-expectation-failures")?.addEventListener("click", () => {
          renderScmReplayExpectationFailuresInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-scm-contract-helper-detail")?.addEventListener("click", () => {
          renderContractHelperInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-open-draft-provenance-detail")?.addEventListener("click", () => {
          renderEditorDraftProvenanceInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-replay-scm-evidence")?.addEventListener("click", () => {
          const entry = entries.find((candidate) => candidate.evidenceKey === studioState.selectedScmEvidenceKey) || selectedEntry;
          replayScmEvidenceEntry(entry);
          renderScmEvidencePanel(studioState.lastReport);
          renderReplayComparisonInProofDock();
        });

        scmEvidencePanel.querySelector("#code-studio-run-scm-regression-harness")?.addEventListener("click", () => {
          runScmRegressionHarness();
          renderScmRegressionHarnessInProofDock();
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

      function resolveMonacoAdapter() {
        if (typeof window === "undefined") return null;
        return window.MainComputerMonacoAdapter || null;
      }

      function monacoReceiptSummary(receipt = {}) {
        return {
          kind: receipt.kind || "mcel-code-studio-monaco-runtime-receipt",
          effect: receipt.effect || "editor.monaco.mount",
          actionOutcome: receipt.actionOutcome || (receipt.ok === false ? "blocked" : "pass"),
          externalOutcome: receipt.externalOutcome || "unknown",
          governanceOutcome: receipt.governanceOutcome || "pass",
          safetyOutcome: receipt.safetyOutcome || "pass",
          nextAction: receipt.nextAction || "inspect Monaco receipt",
          source: receipt.source || "",
          path: receipt.path || studioState.selectedPath || "",
          language: receipt.language || "",
          message: receipt.message || ""
        };
      }

      function runMonacoScmEffect(effectName, payload = {}) {
        return runScmGate(`effect:${effectName}`, (mcel, instance) => {
          if (typeof mcel.runEffect !== "function") {
            return {
              ok: true,
              skipped: true,
              effectName,
              message: "SCM effect runner is unavailable."
            };
          }
          return mcel.runEffect(instance, effectName, payload);
        });
      }

      function recordMonacoRuntimeReceipt(receipt = {}, context = {}) {
        const normalized = monacoReceiptSummary({
          ...receipt,
          ...context,
          path: receipt.path || context.path || studioState.selectedPath || "",
          language: receipt.language || context.language || ""
        });
        studioState.lastMonacoRuntimeReceipt = normalized;
        studioState.monacoRuntimeReceipts = [...studioState.monacoRuntimeReceipts.slice(-11), normalized];
        studioState.lastMonacoRuntimeEffectGate = runMonacoScmEffect(normalized.effect, normalized);
        if (normalized.effect === "editor.monaco.mount") {
          studioState.monacoMounted = normalized.actionOutcome === "pass";
        }
        return normalized;
      }

      function disposeRuntimeMonaco(reason = "renderRuntime") {
        const adapter = resolveMonacoAdapter();
        if (!adapter || typeof adapter.dispose !== "function") return null;
        const outcome = adapter.dispose(reason);
        if (outcome?.actionOutcome === "blocked" && outcome?.externalOutcome === "not-mounted") return outcome;
        recordMonacoRuntimeReceipt({
          ...outcome,
          effect: "editor.monaco.dispose",
          nextAction: "remount editor"
        });
        return outcome;
      }

      function updateRuntimeDraftFromEditor(draft, text, context = {}) {
        if (!draft) return;
        draft.value = String(text ?? "");
        studioState.dirty = true;
        studioState.damaged = false;
        runScmTransition("editDraft", {text: draft.value});
        recordEditorDraftProvenance("changed", {
          origin: context.origin === "monaco" ? "monaco" : "fallback",
          text: draft.value,
          nextAction: "commit draft"
        });
        if (context.origin === "monaco") {
          recordMonacoRuntimeReceipt({
            effect: "editor.monaco.change",
            actionOutcome: "pass",
            externalOutcome: "model-updated",
            path: context.path || studioState.selectedPath || "",
            language: context.language || "",
            nextAction: "commit draft"
          });
        }
        setRuntimeLabel();
        setStatus(context.origin === "monaco"
          ? "Monaco draft changed through SCM editDraft and draft provenance. Source is still unchanged until Commit editor draft."
          : "Runtime draft changed through SCM editDraft and draft provenance. Source is still unchanged until Commit editor draft.");
      }

      function mountRuntimeMonaco(file, draft) {
        const host = runtimePreview.querySelector("#code-studio-runtime-monaco");
        const editor = runtimePreview.querySelector(".code-studio-runtime-editor");
        if (!host || !draft || !file) return;
        const adapter = resolveMonacoAdapter();
        if (!adapter || typeof adapter.mount !== "function") {
          host.dataset.monacoOutcome = "blocked";
          host.textContent = "Monaco adapter is unavailable. Fallback textarea remains active.";
          recordMonacoRuntimeReceipt({
            effect: "editor.monaco.mount",
            actionOutcome: "blocked",
            externalOutcome: "adapter-missing",
            path: file.path,
            language: file.language,
            nextAction: "load Monaco adapter"
          });
          return;
        }

        adapter.mount({
          host,
          path: file.path,
          language: file.language,
          value: draft.value,
          allowCdn: true,
          onChange: (text) => updateRuntimeDraftFromEditor(draft, text, {
            origin: "monaco",
            path: file.path,
            language: file.language
          }),
          onReceipt: (receipt) => {
            const normalized = recordMonacoRuntimeReceipt(receipt, {
              path: file.path,
              language: file.language
            });
            if (editor) {
              editor.dataset.monacoOutcome = normalized.actionOutcome;
              editor.dataset.monacoMounted = normalized.effect === "editor.monaco.mount" && normalized.actionOutcome === "pass" ? "true" : editor.dataset.monacoMounted || "false";
            }
          }
        }).then((receipt) => {
          const normalized = recordMonacoRuntimeReceipt(receipt, {
            path: file.path,
            language: file.language
          });
          if (editor) {
            editor.dataset.monacoOutcome = normalized.actionOutcome;
            editor.dataset.monacoMounted = normalized.actionOutcome === "pass" ? "true" : "false";
          }
          if (normalized.actionOutcome === "pass") {
            setStatus("Monaco mounted as a runtime-only draft editor. Commit editor draft remains the source mutation gate.");
          } else if (normalized.actionOutcome === "blocked") {
            setStatus("Monaco was blocked; the fallback runtime textarea remains active and source-safe.");
          } else {
            setStatus("Monaco raised a runtime exception; the fallback runtime textarea remains active.");
          }
          renderScmEvidencePanel();
          renderFlagshipInspector();
        }).catch((error) => {
          const normalized = recordMonacoRuntimeReceipt({
            effect: "editor.monaco.mount",
            actionOutcome: "exception",
            externalOutcome: "mount-promise-exception",
            path: file.path,
            language: file.language,
            message: error?.message || String(error),
            nextAction: "inspect exception"
          });
          host.dataset.monacoOutcome = "exception";
          host.textContent = "Monaco raised an exception. Fallback textarea remains active.";
          if (editor) {
            editor.dataset.monacoOutcome = normalized.actionOutcome;
            editor.dataset.monacoMounted = "false";
          }
          setStatus("Monaco raised a runtime exception; the fallback runtime textarea remains active.");
          renderScmEvidencePanel();
          renderFlagshipInspector();
        });
      }

      function renderRuntime() {
        disposeRuntimeMonaco("renderRuntime");
        const fields = workspaceFields();
        const file = selectedFile(fields);
        const parsed = parseSource();
        const mountBlockers = [
          ...(!parsed.workspace || parsed.parseError ? ["source-workspace-missing-or-invalid"] : []),
          ...(!file ? ["selected-file-missing"] : []),
          ...(String(sourceEditor.value || "").includes('data-mc-generated="runtime"') ? ["runtime-chrome-in-source"] : [])
        ];
        const mountBoundary = buildMcelCodeStudioCommitBoundary({
          action: "codeStudio.mountRuntimeDraft",
          draftText: file?.value || "",
          reason: "mount-runtime-draft",
          phase: "runtime-mount-preflight",
          intendedWrites: ["runtime.preview", "runtime.editorDraftProvenance", "runtime.monacoAdapter"],
          beforeSourceHash: hashRegressionString(sourceEditor.value || ""),
          blockers: mountBlockers
        });
        recordMcelCodeStudioCommitBoundary(mountBoundary);
        if (mountBoundary.canCommit !== true) {
          runtimePreview.innerHTML = `
            <section class="code-studio-runtime-window" ${generatedAttrs("runtime-envelope", "blocked-18n-boundary")}>
              <header class="code-studio-runtime-header" ${generatedAttrs("runtime-header", "blocked-18n-boundary")}>
                <strong>Runtime mount blocked by MCEL 18N boundary</strong>
                <span>${escapeHtml(mountBoundary.mcelCommitPreflight?.blockers?.join(", ") || "source is not current/proven")}</span>
              </header>
              <article class="code-studio-runtime-editor" ${generatedAttrs("runtime-editor", "blocked-18n-boundary")}>
                <p>Rebuild the runtime draft from current source before mounting generated chrome.</p>
              </article>
            </section>
          `;
          studioState.mounted = false;
          setRuntimeLabel();
          renderMcelCodeStudioCommitBoundaryInProofDock(mountBoundary);
          setStatus(`18N runtime mount blocked: ${mountBoundary.mcelCommitPreflight?.blockers?.join(", ") || "not proven"}.`);
          return;
        }
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
              <article class="code-studio-runtime-editor" data-monaco-mounted="false" data-monaco-outcome="not-started" ${generatedAttrs("runtime-editor", file?.path || "empty")}>
                <div
                  id="code-studio-runtime-monaco"
                  class="code-studio-monaco-host"
                  data-mc-generated="runtime"
                  data-mc-serialize="omit"
                  data-mc-runtime-kind="runtime-monaco-editor"
                  data-mc-runtime-key="${escapeHtml(file?.path || "empty")}"
                  data-code-studio-monaco-runtime="host"
                >Monaco editor runtime will mount here when the adapter is available.</div>
                <label class="code-studio-runtime-fallback">
                  <span>${escapeHtml(file?.path || "No source file")} · fallback runtime draft</span>
                  <textarea id="code-studio-runtime-draft" spellcheck="false" data-code-studio-monaco-fallback="textarea">${escapeHtml(file?.value || "")}</textarea>
                </label>
                <div class="code-studio-runtime-badges" ${generatedAttrs("runtime-badges", "proof-badges")}>
                  <span>Monaco runtime adapter</span>
                  <span>runtime-only dirty state</span>
                  <span>serialize=omit</span>
                  <span>commitDraft is source gate</span>
                  <span>fallback textarea preserved</span>
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
          ensureEditorDraftProvenanceCreated(file, draft, {origin: "runtime-render"});
          draft.addEventListener("input", () => updateRuntimeDraftFromEditor(draft, draft.value, {
            origin: "fallback",
            path: file?.path || studioState.selectedPath || "",
            language: file?.language || ""
          }));
        }
        studioState.mounted = true;
        studioState.damaged = false;
        mountRuntimeMonaco(file, draft);
        const mountedBoundary = jsonSafeClone(mountBoundary);
        mountedBoundary.status = "mounted";
        mountedBoundary.mcelCommitReceipt = mcelCodeStudioCommitReceipt({
          draft: mountBoundary.mcelCommitDraft,
          provenance: mountBoundary.mcelCommitProvenance,
          freshness: mountBoundary.mcelCommitFreshness,
          consumerGate: mountBoundary.mcelCommitConsumerGate,
          preflight: mountBoundary.mcelCommitPreflight,
          mutationExecuted: true,
          beforeSourceHash: mountBoundary.mcelCommitProvenance?.sourceHash || "",
          afterSourceHash: hashRegressionString(runtimePreview.innerHTML || ""),
          reason: "mount-runtime-draft-runtime-only-mutation"
        });
        recordMcelCodeStudioCommitBoundary(mountedBoundary);
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
              layoutViolations: summarizeLayoutGateViolations(report.scm),
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
        const adapter = resolveMonacoAdapter();
        const monacoValue = adapter && typeof adapter.getValue === "function" ? adapter.getValue() : null;
        if (typeof monacoValue === "string") draft.value = monacoValue;
        const beforeSourceHash = hashRegressionString(sourceEditor.value || "");
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
          const blockedBoundary = buildMcelCodeStudioCommitBoundary({
            action: "codeStudio.commitRuntimeDraft",
            draftText: draft.value,
            reason: "commitDraft-transition-blocked",
            gates: {editGate, commitGate},
            phase: "source-mutation-preflight",
            intendedWrites: ["source.workspace.files", "state.dirty", "runtime.editorDraftProvenance"],
            beforeSourceHash
          });
          recordMcelCodeStudioCommitBoundary(blockedBoundary);
          setStatus(`SCM commitDraft transition blocked commit: ${commitGate.code || commitGate.message || "contract violation"}.`);
          return;
        }

        const commitBoundary = buildMcelCodeStudioCommitBoundary({
          action: "codeStudio.commitRuntimeDraft",
          draftText: draft.value,
          reason: "commit-runtime-draft",
          gates: {editGate, commitGate},
          phase: "source-mutation-preflight",
          intendedWrites: ["source.workspace.files", "state.dirty", "runtime.editorDraftProvenance"],
          beforeSourceHash
        });
        recordMcelCodeStudioCommitBoundary(commitBoundary);
        if (commitBoundary.canCommit !== true) {
          renderMcelCodeStudioCommitBoundaryInProofDock(commitBoundary);
          setStatus(`18N commit boundary blocked runtime draft commit: ${commitBoundary.mcelCommitPreflight?.blockers?.join(", ") || "not proven"}.`);
          return;
        }

        const target = [...workspace.querySelectorAll('[data-mc-component="code-file"]')]
          .find((node) => node.getAttribute("data-mc-file-path") === file.path);
        if (!target) {
          const missingTargetBoundary = buildMcelCodeStudioCommitBoundary({
            action: "codeStudio.commitRuntimeDraft",
            draftText: draft.value,
            reason: "selected-target-missing",
            gates: {editGate, commitGate},
            phase: "source-mutation-preflight",
            intendedWrites: ["source.workspace.files", "state.dirty", "runtime.editorDraftProvenance"],
            beforeSourceHash,
            blockers: ["selected-target-missing"]
          });
          recordMcelCodeStudioCommitBoundary(missingTargetBoundary);
          setStatus("Cannot commit: selected file path is no longer in source.");
          return;
        }
        target.textContent = draft.value;
        sourceEditor.value = workspace.outerHTML.trim();
        const afterSourceHash = hashRegressionString(sourceEditor.value || "");
        const committedBoundary = jsonSafeClone(commitBoundary);
        committedBoundary.status = "committed";
        committedBoundary.mcelCommitReceipt = mcelCodeStudioCommitReceipt({
          draft: commitBoundary.mcelCommitDraft,
          provenance: commitBoundary.mcelCommitProvenance,
          freshness: commitBoundary.mcelCommitFreshness,
          consumerGate: commitBoundary.mcelCommitConsumerGate,
          preflight: commitBoundary.mcelCommitPreflight,
          mutationExecuted: true,
          beforeSourceHash,
          afterSourceHash,
          reason: "commit-runtime-draft-source-mutation"
        });
        recordMcelCodeStudioCommitBoundary(committedBoundary);
        recordEditorDraftProvenance("committed", {
          origin: "commitDraft",
          text: draft.value,
          sourceChanged: true,
          beforeSourceHash,
          afterSourceHash,
          commitBoundaryReceipt: committedBoundary.mcelCommitReceipt,
          nextAction: "render committed source"
        });
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
        renderMcelCodeStudioCommitBoundaryInProofDock(studioState.lastCodeStudioCommitBoundary);
        setStatus("Runtime draft committed through MCEL 18N boundary, persisted through SCM saveFile, and route/effect loaders refreshed.");
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
      root.querySelector("#code-studio-show-18n-boundary")?.addEventListener("click", () => {
        renderMcelCodeStudioCommitBoundaryInProofDock(studioState.lastCodeStudioCommitBoundary);
      });

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
        buildMcelCodeStudioCommitBoundary,
        renderMcelCodeStudioCommitBoundaryInProofDock,
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
        summarizeScmReceiptSourceForWorkbench,
        summarizeTxDraftProvenanceForWorkbench,
        formatNormalizedScmReceiptVectorDetail,
        renderScmReceiptVectorInProofDock,
        collectMcelProofDockUnifiedSpecimens,
        renderMcelUnifiedProofDockSpecimensInProofDock,
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
        summarizeScmReplayComparisonForWorkbench,
        summarizeScmReplayExpectationsForWorkbench,
        runScmRegressionHarness,
        formatScmRegressionHarnessDetail,
        formatScmReplayExpectationFailuresDetail,
        renderScmRegressionHarnessInProofDock,
        renderScmReplayExpectationFailuresInProofDock,
        buildGenericScmReplayFixtures,
        runGenericScmReplayFixturePack,
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
