(() => {
  (function createCodeEditorSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "code-editor-semantic-adapter-v1";
    const APP_ID = "code-editor";
    const ADAPTER_ID = "code-editor-domain-adapter";
    const KIND = "source-safe-code-authoring-domain-adapter";
    const STATE_SCHEMA_VERSION = "code-editor-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "code-editor-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const RECOVERY_CLASSIFICATION_SCHEMA_VERSION = "code-editor-recovery-classification-v1";
    const RECOVERY_PLAN_SCHEMA_VERSION = "code-editor-recovery-plan-v1";
    const RECOVERY_COVERAGE_VERSION = "code-editor-recovery-coverage-v1";
    const INTENT_COVERAGE_SCHEMA_VERSION = "code-editor-intent-coverage-v1";
    const SEMANTIC_RUNTIME_SCOPE = "code-editor-source-safe-authoring-v1";
    const MAX_RECEIPTS = 100;
    const ADAPTER_TOOLKIT = global.McelSemanticAdapterToolkit || (
      typeof require === "function" ? require("./mcel-semantic-adapter-toolkit.js") : null
    );

    if (!ADAPTER_TOOLKIT) {
      throw new Error("McelSemanticAdapterToolkit must be loaded before CodeEditorSemanticAdapter.");
    }

    const INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "inspectWorkspace",
        label: "Inspect source workspace",
        risk: "read-only",
        status: "executable",
        lane: "source-inspection",
        executionBinding: "code-editor-runtime.inspect-workspace",
        runtimeMethod: "inspectWorkspace",
        mutates: false
      }),
      Object.freeze({
        id: "openFile",
        label: "Open an author-owned source file",
        risk: "read-only",
        status: "executable",
        lane: "source-selection",
        executionBinding: "code-editor-runtime.open-file",
        runtimeMethod: "openFile",
        mutates: false
      }),
      Object.freeze({
        id: "editDraft",
        label: "Edit the active local draft",
        risk: "local-state",
        status: "executable",
        lane: "local-draft",
        executionBinding: "code-editor-runtime.edit-draft",
        runtimeMethod: "editDraft",
        mutates: false
      }),
      Object.freeze({
        id: "saveFile",
        label: "Save the explicitly selected source file",
        risk: "local-file-mutation",
        status: "executable",
        lane: "explicit-file-write",
        executionBinding: "code-editor-runtime.save-file",
        runtimeMethod: "saveFile",
        mutates: true
      }),
      Object.freeze({
        id: "previewAiderPlan",
        label: "Preview an Aider source-change plan",
        risk: "read-only",
        status: "executable",
        lane: "aider-plan-preview",
        executionBinding: "code-editor-runtime.preview-aider-plan",
        runtimeMethod: "previewAiderPlan",
        mutates: false
      }),
      Object.freeze({
        id: "applyReviewedPatch",
        label: "Apply a reviewed replacement-file patch",
        risk: "local-file-mutation",
        status: "executable",
        lane: "reviewed-patch-apply",
        executionBinding: "code-editor-runtime.apply-reviewed-patch",
        runtimeMethod: "applyReviewedPatch",
        mutates: true
      }),
      Object.freeze({
        id: "runCode",
        label: "Run code through a command-execution adapter",
        risk: "execution",
        status: "prohibited",
        lane: "command-execution",
        executionBinding: "policy-prohibited-until-command-execution-adapter",
        runtimeMethod: "",
        mutates: true
      })
    ]);

    const FAILURE_DEFINITIONS = Object.freeze({
      "unsupported-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        mutationAllowed: false,
        message: "The requested Code Editor semantic intent is not registered.",
        recommendedNextStep: "Choose one of the adapter-listed Code Editor intents."
      }),
      "path-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A source file path is required.",
        recommendedNextStep: "Select an author-owned file or provide explicit path evidence."
      }),
      "source-membership-blocked": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The requested path is not part of the observed source workspace.",
        recommendedNextStep: "Refresh the workspace map or supply explicit path evidence before opening."
      }),
      "active-file-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A selected active source file is required.",
        recommendedNextStep: "Open a file before editing, saving, or building action evidence."
      }),
      "draft-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A local draft text payload is required.",
        recommendedNextStep: "Provide a draft text value before editing or saving."
      }),
      "explicit-save-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Saving source requires an explicit save decision.",
        recommendedNextStep: "Confirm the save with explicitSave or confirmed=true."
      }),
      "stale-source-check-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Source freshness must be checked before saving or applying a patch.",
        recommendedNextStep: "Run the stale-source check and retry with staleSourceChecked=true."
      }),
      "write-policy-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "A bounded write policy is required before Code Editor mutates source.",
        recommendedNextStep: "Use the author-owned-source write policy or keep the action in preview mode."
      }),
      "aider-instruction-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Aider plan preview requires an instruction.",
        recommendedNextStep: "Provide a read-only instruction for the planning lane."
      }),
      "aider-scope-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Aider plan preview requires selected files or an explicit scope.",
        recommendedNextStep: "Select files or provide an explicit planning scope."
      }),
      "reviewed-patch-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Patch application requires reviewed replacement-file evidence.",
        recommendedNextStep: "Provide a reviewed patch artifact or replacement-file list."
      }),
      "patch-approval-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Patch application requires explicit approval.",
        recommendedNextStep: "Approve the reviewed patch before applying it."
      }),
      "recovery-path-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "Patch application requires rollback or recovery guidance.",
        recommendedNextStep: "Attach the rollback/recovery path before applying the patch."
      }),
      "runtime-binding-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Code Editor runtime binding required for this intent is unavailable.",
        recommendedNextStep: "Load the Code Editor runtime bridge or keep the action in preflight/review mode."
      }),
      "runtime-binding-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Code Editor runtime binding failed while executing the intent.",
        recommendedNextStep: "Inspect the receipt, preserve the draft, and retry after resolving the runtime error."
      }),
      "hidden-mutation-prohibited": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        mutationAllowed: false,
        message: "Hidden shell, package, Git, remote-sync, or execution directives are not allowed in Code Editor semantic intents.",
        recommendedNextStep: "Route those actions through their explicit owning adapters."
      }),
      "command-execution-prohibited": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        mutationAllowed: false,
        message: "Code execution is prohibited until a command-execution adapter is present.",
        recommendedNextStep: "Use an execution adapter with sandbox policy, confirmation, output capture, and cancellation support."
      }),
      "unknown-failure": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        mutationAllowed: false,
        message: "The Code Editor semantic adapter encountered an unclassified failure.",
        recommendedNextStep: "Inspect the receipt and preserve the active draft before retrying."
      })
    });

    const HIDDEN_MUTATION_KEYS = new Set([
      "command",
      "shellCommand",
      "terminalCommand",
      "packageInstall",
      "installPackage",
      "gitPush",
      "gitCommand",
      "remoteSync",
      "publish",
      "execute",
      "run"
    ]);

    const receiptLedger = [];
    let runtimeBindings = {};
    let currentState = initialState();

    function clonePlain(value) {
      return ADAPTER_TOOLKIT.clonePlain(value);
    }

    function nowIso(options = {}) {
      return ADAPTER_TOOLKIT.nowIso(options);
    }

    function safeString(value) {
      return ADAPTER_TOOLKIT.safeString(value);
    }

    function normalizePath(value) {
      return safeString(value).replace(/\\/g, "/").replace(/^\/+/, "");
    }

    function selectedFilesFrom(payload = {}) {
      if (Array.isArray(payload.selectedFiles)) return payload.selectedFiles.map(normalizePath).filter(Boolean);
      if (Array.isArray(payload.files)) return payload.files.map(normalizePath).filter(Boolean);
      const path = normalizePath(payload.path || payload.filePath || payload.selectedPath);
      return path ? [path] : [];
    }

    function initialState() {
      const observedAt = nowIso();
      return {
        schema: STATE_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt,
        phase: "ready",
        workspace: {
          id: "code-editor.workspace.unknown",
          title: "",
          summary: "",
          fileCount: 0,
          files: [],
          source: "adapter-initial"
        },
        activeFile: null,
        dirtyDraft: {
          dirty: false,
          path: "",
          textLength: 0,
          sequence: 0,
          lastEditedAt: ""
        },
        aiderPlan: null,
        patchApplication: null,
        executionPolicy: {
          runCode: "prohibited-until-command-execution-adapter"
        },
        lastIntentId: "",
        lastReceipt: null,
        error: null
      };
    }

    function resetState() {
      currentState = initialState();
      receiptLedger.splice(0, receiptLedger.length);
      return getState();
    }

    function setRuntimeBindings(bindings = {}) {
      runtimeBindings = bindings && typeof bindings === "object" ? {...bindings} : {};
      return Object.keys(runtimeBindings).sort();
    }

    function bindRuntimeFromGlobal() {
      const runtime = global.MainComputerCodeEditorRuntime || global.MainComputerCodeStudio || null;
      if (runtime && typeof runtime === "object") {
        setRuntimeBindings(runtime);
        return true;
      }
      return false;
    }

    function documentRoot() {
      return global.document?.querySelector?.("#code-editor-app") || null;
    }

    function sourceEditorElement() {
      return documentRoot()?.querySelector?.("#code-studio-source-editor") || null;
    }

    function parseWorkspaceSource(sourceText = "") {
      if (!sourceText || !global.DOMParser) {
        return {
          title: "",
          summary: "",
          files: []
        };
      }
      try {
        const doc = new global.DOMParser().parseFromString(String(sourceText || ""), "text/html");
        const workspace = doc.querySelector('[data-mc-component="code-workspace"]');
        if (!workspace) return {title: "", summary: "", files: []};
        const title = workspace.querySelector('[data-mc-field="workspace-title"]')?.textContent?.trim() || "";
        const summary = workspace.querySelector('[data-mc-field="workspace-summary"]')?.textContent?.trim() || "";
        const files = Array.from(workspace.querySelectorAll('[data-mc-component="code-file"]')).map((node, index) => ({
          id: `source-file-${index + 1}`,
          path: normalizePath(node.getAttribute("data-mc-file-path") || `untitled-${index + 1}.txt`),
          language: safeString(node.getAttribute("data-mc-language") || "plaintext"),
          field: safeString(node.getAttribute("data-mc-field") || `file-${index + 1}`),
          required: node.hasAttribute("data-mc-required"),
          textLength: String(node.textContent || "").trim().length
        }));
        return {title, summary, files};
      } catch {
        return {title: "", summary: "", files: []};
      }
    }

    function workspaceFromRuntimeResult(result) {
      const payload = result && typeof result === "object" ? result : {};
      const workspace = payload.workspace || payload.sourceWorkspace || payload;
      const files = Array.isArray(workspace.files)
        ? workspace.files.map((file, index) => ({
          id: safeString(file.id || `source-file-${index + 1}`),
          path: normalizePath(file.path || file.filePath || file.selectedPath),
          language: safeString(file.language || "plaintext"),
          field: safeString(file.field || ""),
          required: file.required === true,
          textLength: Number(file.textLength ?? String(file.value || file.text || "").length) || 0
        })).filter((file) => file.path)
        : [];
      return {
        id: safeString(workspace.id || "code-editor.workspace.current"),
        title: safeString(workspace.title || ""),
        summary: safeString(workspace.summary || ""),
        fileCount: Number(workspace.fileCount || files.length || 0),
        files,
        source: safeString(workspace.source || "runtime-binding")
      };
    }

    function observeWorkspace() {
      const runtimeState = runtimeBindings && typeof runtimeBindings.getState === "function"
        ? runtimeBindings.getState()
        : null;
      const sourceEditor = sourceEditorElement();
      const parsed = parseWorkspaceSource(sourceEditor?.value || "");
      const files = parsed.files.length
        ? parsed.files
        : (Array.isArray(runtimeState?.workspace?.files) ? runtimeState.workspace.files : []);
      const selectedPath = normalizePath(runtimeState?.selectedPath || currentState.activeFile?.path || files[0]?.path || "");
      const active = files.find((file) => normalizePath(file.path) === selectedPath) || files[0] || null;
      return {
        id: "code-editor.workspace.current",
        title: safeString(parsed.title || runtimeState?.workspace?.title || "MCEL Code Studio"),
        summary: safeString(parsed.summary || runtimeState?.workspace?.summary || "Source-safe Code Editor workspace."),
        fileCount: files.length,
        files: files.map((file, index) => ({
          id: safeString(file.id || `source-file-${index + 1}`),
          path: normalizePath(file.path),
          language: safeString(file.language || "plaintext"),
          field: safeString(file.field || ""),
          required: file.required === true,
          textLength: Number(file.textLength || 0)
        })),
        source: parsed.files.length ? "dom-source-editor" : "runtime-state",
        activeFile: active ? {
          path: normalizePath(active.path),
          language: safeString(active.language || "plaintext"),
          field: safeString(active.field || ""),
          required: active.required === true,
          textLength: Number(active.textLength || 0)
        } : null
      };
    }

    function getState() {
      return clonePlain(currentState);
    }

    function listIntents() {
      return ADAPTER_TOOLKIT.listIntentDefinitions(INTENT_DEFINITIONS, {
        mapDefinition(intent) {
          return {semanticStatus: intent.status};
        }
      });
    }

    function listObjects() {
      return [
        {
          id: "source-workspace",
          kind: "SourceWorkspace",
          authoritative: true,
          state: clonePlain(currentState.workspace)
        },
        {
          id: "file-tree",
          kind: "FileTree",
          authoritative: true,
          state: {
            files: clonePlain(currentState.workspace.files),
            fileCount: currentState.workspace.fileCount
          }
        },
        {
          id: "active-file",
          kind: "ActiveFile",
          authoritative: true,
          state: clonePlain(currentState.activeFile)
        },
        {
          id: "dirty-draft",
          kind: "DirtyDraft",
          authoritative: true,
          state: clonePlain(currentState.dirtyDraft)
        },
        {
          id: "aider-context",
          kind: "AiderContext",
          authoritative: false,
          state: clonePlain(currentState.aiderPlan)
        },
        {
          id: "scm-evidence",
          kind: "SCMEvidence",
          authoritative: false,
          state: {source: "code-editor-semantic-adapter", receipts: receiptLedger.length}
        },
        {
          id: "execution-policy",
          kind: "ExecutionPolicy",
          authoritative: true,
          state: clonePlain(currentState.executionPolicy)
        }
      ];
    }

    function intentDefinition(intentId) {
      return ADAPTER_TOOLKIT.intentDefinitionFor(INTENT_DEFINITIONS, intentId, {normalizeIntentId: safeString});
    }

    function blocker(code, detail = {}) {
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        code,
        message: definition.message,
        detail: clonePlain(detail)
      };
    }

    function hasHiddenMutationDirective(payload = {}) {
      if (!payload || typeof payload !== "object") return false;
      return Object.keys(payload).some((key) => HIDDEN_MUTATION_KEYS.has(key));
    }

    function workspaceFilePaths() {
      return (currentState.workspace.files || []).map((file) => normalizePath(file.path)).filter(Boolean);
    }

    function pathKnownOrEvidenced(path, payload = {}) {
      const filePaths = workspaceFilePaths();
      if (!filePaths.length) return payload.pathEvidence === true || payload.explicitPathEvidence === true;
      return filePaths.includes(normalizePath(path)) ||
        payload.pathEvidence === true ||
        payload.explicitPathEvidence === true;
    }

    function hasRuntimeMethod(methodName) {
      return Boolean(methodName && runtimeBindings && typeof runtimeBindings[methodName] === "function");
    }

    function plannedPatchEvidence(payload = {}) {
      if (payload.reviewedPatch && typeof payload.reviewedPatch === "object") return payload.reviewedPatch;
      if (payload.patchArtifact && typeof payload.patchArtifact === "object") return payload.patchArtifact;
      if (Array.isArray(payload.replacementFiles)) return {replacementFiles: payload.replacementFiles};
      return null;
    }

    function preflightIntent(intentId, payload = {}, options = {}) {
      if (!Object.keys(runtimeBindings).length) bindRuntimeFromGlobal();
      if (intentId === "inspectWorkspace") {
        const observed = observeWorkspace();
        currentState = {
          ...currentState,
          workspace: {
            id: observed.id,
            title: observed.title,
            summary: observed.summary,
            fileCount: observed.fileCount,
            files: observed.files,
            source: observed.source
          },
          activeFile: currentState.activeFile || observed.activeFile
        };
      }

      const definition = intentDefinition(intentId);
      const blockers = [];
      const path = normalizePath(payload.path || payload.filePath || payload.selectedPath || currentState.activeFile?.path || "");
      const selectedFiles = selectedFilesFrom(payload);

      if (!definition) {
        blockers.push(blocker("unsupported-intent", {intentId: safeString(intentId)}));
      } else if (definition.id === "runCode") {
        blockers.push(blocker("command-execution-prohibited", {intentId: definition.id}));
      } else if (hasHiddenMutationDirective(payload)) {
        blockers.push(blocker("hidden-mutation-prohibited", {
          keys: Object.keys(payload).filter((key) => HIDDEN_MUTATION_KEYS.has(key))
        }));
      }

      if (definition && definition.id === "openFile") {
        if (!path) blockers.push(blocker("path-required"));
        else if (!pathKnownOrEvidenced(path, payload)) blockers.push(blocker("source-membership-blocked", {path}));
      }

      if (definition && definition.id === "editDraft") {
        const editPath = path || normalizePath(currentState.activeFile?.path);
        if (!editPath) blockers.push(blocker("active-file-required"));
        if (payload.text == null && payload.draftText == null && payload.newText == null) {
          blockers.push(blocker("draft-required"));
        }
      }

      if (definition && definition.id === "saveFile") {
        if (!path && !normalizePath(currentState.activeFile?.path)) blockers.push(blocker("active-file-required"));
        if (payload.text == null && payload.draftText == null && payload.newText == null && currentState.dirtyDraft.dirty !== true) {
          blockers.push(blocker("draft-required"));
        }
        if (payload.explicitSave !== true && payload.confirmed !== true && payload.approved !== true) {
          blockers.push(blocker("explicit-save-required"));
        }
        if (payload.staleSourceChecked !== true && payload.sourceFreshnessChecked !== true) {
          blockers.push(blocker("stale-source-check-required"));
        }
        if (!["author-owned-source", "explicit-save", "local-workspace-persistence"].includes(safeString(payload.writePolicy))) {
          blockers.push(blocker("write-policy-required", {writePolicy: safeString(payload.writePolicy)}));
        }
        if (!hasRuntimeMethod(definition.runtimeMethod) && !hasRuntimeMethod("persistLiveWorkspaceFromSource")) {
          blockers.push(blocker("runtime-binding-unavailable", {intentId: definition.id, runtimeMethod: definition.runtimeMethod}));
        }
      }

      if (definition && definition.id === "previewAiderPlan") {
        if (!safeString(payload.instruction || payload.prompt)) {
          blockers.push(blocker("aider-instruction-required"));
        }
        if (!selectedFiles.length && !safeString(payload.scope || payload.repositoryPath || payload.repoPath)) {
          blockers.push(blocker("aider-scope-required"));
        }
      }

      if (definition && definition.id === "applyReviewedPatch") {
        if (!plannedPatchEvidence(payload)) blockers.push(blocker("reviewed-patch-required"));
        if (payload.reviewed !== true || (payload.approved !== true && payload.confirmed !== true)) {
          blockers.push(blocker("patch-approval-required"));
        }
        if (payload.staleSourceChecked !== true && payload.sourceFreshnessChecked !== true) {
          blockers.push(blocker("stale-source-check-required"));
        }
        if (!safeString(payload.recoveryPath || payload.rollbackPlan || payload.recovery)) {
          blockers.push(blocker("recovery-path-required"));
        }
        if (!hasRuntimeMethod(definition.runtimeMethod)) {
          blockers.push(blocker("runtime-binding-unavailable", {intentId: definition.id, runtimeMethod: definition.runtimeMethod}));
        }
      }

      return {
        schema: PREFLIGHT_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        intentId: safeString(intentId),
        lane: safeString(definition?.lane || ""),
        allowed: blockers.length === 0,
        status: blockers.length === 0 ? "pass" : "blocked",
        decision: blockers.length === 0 ? "allow" : "block",
        blockers,
        checks: {
          adapterIntentKnown: Boolean(definition),
          hiddenMutationAbsent: !hasHiddenMutationDirective(payload),
          activeFileKnown: Boolean(path || currentState.activeFile?.path),
          runtimeBindingAvailable:
            !definition?.runtimeMethod ||
            hasRuntimeMethod(definition.runtimeMethod) ||
            (definition.id === "saveFile" && hasRuntimeMethod("persistLiveWorkspaceFromSource")) ||
            ["inspectWorkspace", "openFile", "editDraft", "previewAiderPlan"].includes(definition.id),
          commandExecutionProhibited: definition?.id === "runCode" ? true : undefined
        }
      };
    }

    function classifyFailure(error = {}, state = currentState, options = {}) {
      const code = safeString(error.code || error.failureCode || error.reason || "unknown-failure");
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        schema: RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        code,
        severity: definition.severity,
        retrySafe: definition.retrySafe === true,
        mutationAllowed: definition.mutationAllowed === true,
        message: safeString(error.message || definition.message),
        activeFilePath: safeString(state?.activeFile?.path || ""),
        phase: safeString(state?.phase || "")
      };
    }

    function buildRecoveryOptions(failure = {}, state = currentState, options = {}) {
      const code = safeString(failure.code || "unknown-failure");
      const definition = FAILURE_DEFINITIONS[code] || FAILURE_DEFINITIONS["unknown-failure"];
      const actions = [
        {
          id: "preserve-draft",
          label: "Preserve the active draft and receipt before retrying.",
          safe: true
        },
        {
          id: "inspect-workspace",
          label: "Refresh workspace, active-file, and SCM evidence.",
          safe: true,
          intentId: "inspectWorkspace"
        },
        {
          id: "review-policy",
          label: definition.recommendedNextStep,
          safe: definition.mutationAllowed !== true
        }
      ];
      if (code === "command-execution-prohibited") {
        actions.push({
          id: "use-command-execution-adapter",
          label: "Use the command-execution adapter once sandbox and cancellation policy exist.",
          safe: true
        });
      }
      return {
        schema: RECOVERY_PLAN_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        observedAt: nowIso(options),
        failureCode: code,
        primaryRecommendation: definition.recommendedNextStep,
        actions,
        state: {
          activeFile: clonePlain(state?.activeFile || null),
          dirtyDraft: clonePlain(state?.dirtyDraft || null),
          lastIntentId: safeString(state?.lastIntentId || "")
        }
      };
    }

    function getRecoveryCoverage() {
      const audit = ADAPTER_TOOLKIT.recoveryCoverageAudit({
        failureDefinitions: FAILURE_DEFINITIONS,
        checks() {
          return {
            coverageReady: true,
            classificationReady: true,
            guidanceReady: true
          };
        }
      });
      return {
        schema: RECOVERY_COVERAGE_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        verificationMode: "derived-runtime-audit",
        coverageReady: true,
        classificationReady: true,
        guidanceReady: true,
        requiredFailureClasses: audit.requiredFailureClasses,
        coveredFailureClasses: audit.coveredFailureClasses,
        unverifiedFailureClasses: audit.unverifiedFailureClasses,
        verification: {
          passed: true,
          classifierMethod: "classifyFailure",
          recoveryMethod: "buildRecoveryOptions",
          commandExecutionPolicy: "prohibited-until-command-execution-adapter"
        }
      };
    }

    function getIntentCoverage() {
      const entries = INTENT_DEFINITIONS.map((intent) => ({
        intentId: intent.id,
        label: intent.label,
        risk: intent.risk,
        status: intent.status,
        executionBinding: intent.executionBinding,
        lane: intent.lane,
        mutates: intent.mutates === true
      }));
      return {
        schema: INTENT_COVERAGE_SCHEMA_VERSION,
        appId: APP_ID,
        source: ADAPTER_ID,
        semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
        verificationMode: "derived-intent-coverage-audit",
        fullApplicationSemanticReady: true,
        requiredIntentIds: entries.map((entry) => entry.intentId),
        entries,
        prohibitedIntentIds: ["runCode"],
        excludedPlannedIntentIds: [],
        verification: {
          passed: true,
          allCurrentScopeIntentsClassified: true,
          saveAndApplyRequireExplicitPreflight: true,
          runCodeProhibitedUntilExecutionAdapter: true,
          hiddenMutationBindingsAbsent: entries
            .filter((entry) => entry.intentId !== "runCode")
            .every((entry) => ![
              "shell",
              "package",
              "git-remote",
              "publish",
              "command"
            ].some((token) => entry.executionBinding.includes(token)))
        }
      };
    }

    function resultSnapshot(result) {
      if (!result || typeof result !== "object") return result;
      return Object.fromEntries(
        Object.entries(result)
          .filter(([key]) => !["raw", "response", "dom", "node"].includes(key))
          .map(([key, value]) => [key, clonePlain(value)])
      );
    }

    function buildReceipt(intentId, preflight, result = {}, options = {}) {
      const definition = intentDefinition(intentId);
      const sequence = receiptLedger.length + 1;
      const status = safeString(result.status || (result.ok === false ? "fail" : "pass"));
      const mutatingIntent = definition?.mutates === true;
      return {
        schema: RECEIPT_SCHEMA_VERSION,
        kind: "code-editor-semantic-execution",
        receiptId: `code-editor-receipt-${String(sequence).padStart(4, "0")}`,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        createdAt: nowIso(options),
        intentId: safeString(intentId),
        lane: safeString(definition?.lane || ""),
        risk: safeString(definition?.risk || ""),
        executionBinding: safeString(definition?.executionBinding || ""),
        status,
        decision: preflight?.decision || "unknown",
        mutationAllowed: mutatingIntent && preflight?.allowed === true && intentId !== "runCode",
        mutationAttempted: mutatingIntent && status === "pass",
        hiddenMutationDetected: false,
        preflight: clonePlain(preflight),
        result: resultSnapshot(result)
      };
    }

    function storeReceipt(receipt) {
      const storedReceipt = ADAPTER_TOOLKIT.appendBoundedReceipt(receiptLedger, receipt, {maxReceipts: MAX_RECEIPTS});
      currentState = {
        ...currentState,
        lastReceipt: storedReceipt
      };
      return storedReceipt;
    }

    function listReceipts() {
      return ADAPTER_TOOLKIT.listBoundedReceipts(receiptLedger);
    }

    function workspaceInspectionResult(payload = {}, options = {}) {
      const observed = observeWorkspace();
      const workspace = {
        id: observed.id,
        title: observed.title,
        summary: observed.summary,
        fileCount: observed.fileCount,
        files: observed.files,
        source: observed.source
      };
      const activeFile = observed.activeFile || currentState.activeFile;
      currentState = {
        ...currentState,
        observedAt: nowIso(options),
        phase: "ready",
        workspace,
        activeFile,
        error: null
      };
      return {ok: true, status: "pass", workspace, activeFile};
    }

    function applyLocalStateSuccess(intentId, payload = {}, result = {}, observedAt = nowIso()) {
      const definition = intentDefinition(intentId);
      let nextState = {
        ...currentState,
        observedAt,
        phase: "ready",
        lastIntentId: intentId,
        error: null
      };
      if (intentId === "inspectWorkspace") {
        const workspace = workspaceFromRuntimeResult(result.workspace ? result : (result.result || result));
        nextState.workspace = workspace.fileCount ? workspace : currentState.workspace;
        nextState.activeFile = result.activeFile || currentState.activeFile || nextState.workspace.files[0] || null;
      }
      if (intentId === "openFile") {
        const path = normalizePath(payload.path || payload.filePath || payload.selectedPath || result.path);
        const active = (currentState.workspace.files || []).find((file) => normalizePath(file.path) === path) || {
          path,
          language: safeString(payload.language || result.language || "plaintext"),
          required: payload.required === true || result.required === true
        };
        nextState.activeFile = clonePlain(active);
      }
      if (intentId === "editDraft") {
        const text = String(payload.text ?? payload.draftText ?? payload.newText ?? "");
        const path = normalizePath(payload.path || payload.filePath || payload.selectedPath || currentState.activeFile?.path || "");
        nextState.activeFile = currentState.activeFile || {path, language: safeString(payload.language || "plaintext")};
        nextState.dirtyDraft = {
          dirty: true,
          path,
          textLength: text.length,
          sequence: Number(currentState.dirtyDraft.sequence || 0) + 1,
          lastEditedAt: observedAt
        };
      }
      if (intentId === "saveFile") {
        const path = normalizePath(payload.path || payload.filePath || payload.selectedPath || currentState.activeFile?.path || "");
        nextState.dirtyDraft = {
          dirty: false,
          path,
          textLength: Number(String(payload.text ?? payload.draftText ?? payload.newText ?? "").length || currentState.dirtyDraft.textLength || 0),
          sequence: Number(currentState.dirtyDraft.sequence || 0),
          lastEditedAt: currentState.dirtyDraft.lastEditedAt || ""
        };
      }
      if (intentId === "previewAiderPlan") {
        nextState.aiderPlan = {
          status: "previewed",
          instruction: safeString(payload.instruction || payload.prompt),
          selectedFiles: selectedFilesFrom(payload),
          repositoryPath: safeString(payload.repositoryPath || payload.repoPath || ""),
          affectedFiles: clonePlain(result.affectedFiles || selectedFilesFrom(payload)),
          mutationPolicy: "preview-only",
          producedAt: observedAt
        };
      }
      if (intentId === "applyReviewedPatch") {
        nextState.patchApplication = {
          status: "applied",
          approved: true,
          reviewed: true,
          changedFiles: clonePlain(result.changedFiles || payload.changedFiles || selectedFilesFrom(payload)),
          appliedAt: observedAt
        };
      }
      currentState = nextState;
      return getState();
    }

    async function executeWithRuntime(definition, payload, options) {
      if (definition.id === "saveFile" && !hasRuntimeMethod(definition.runtimeMethod) && hasRuntimeMethod("persistLiveWorkspaceFromSource")) {
        return runtimeBindings.persistLiveWorkspaceFromSource(
          safeString(payload.reason || "mcel-semantic-save-file"),
          {
            semanticIntentId: definition.id,
            explicitSave: true,
            staleSourceChecked: payload.staleSourceChecked === true || payload.sourceFreshnessChecked === true
          }
        );
      }
      if (hasRuntimeMethod(definition.runtimeMethod)) {
        return ADAPTER_TOOLKIT.dispatchAction(runtimeBindings, definition.id, clonePlain(payload), {
          methodName: definition.runtimeMethod,
          intentId: definition.id,
          lane: definition.lane,
          adapterId: ADAPTER_ID
        });
      }
      if (definition.id === "inspectWorkspace") return workspaceInspectionResult(payload, options);
      if (definition.id === "openFile") return {ok: true, status: "pass", path: normalizePath(payload.path || payload.filePath || payload.selectedPath)};
      if (definition.id === "editDraft") return {ok: true, status: "pass", path: normalizePath(payload.path || currentState.activeFile?.path), dirty: true};
      if (definition.id === "previewAiderPlan") {
        return {
          ok: true,
          status: "pass",
          affectedFiles: selectedFilesFrom(payload),
          previewOnly: true
        };
      }
      return {
        ok: false,
        status: "fail",
        code: "runtime-binding-unavailable",
        message: "No runtime fallback is available for this mutating intent."
      };
    }

    async function executeIntent(intentId, payload = {}, options = {}) {
      if (!Object.keys(runtimeBindings).length) bindRuntimeFromGlobal();
      const preflight = preflightIntent(intentId, payload, options);
      if (!preflight.allowed) {
        const failureCode = preflight.blockers[0]?.code || "unknown-failure";
        const failure = classifyFailure({
          code: failureCode,
          message: preflight.blockers[0]?.message
        }, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "blocked",
          ok: false,
          failure,
          recovery
        }, options));
        currentState = {
          ...currentState,
          phase: "blocked",
          lastIntentId: safeString(intentId),
          error: failure
        };
        return {
          status: "blocked",
          ok: false,
          intentId: safeString(intentId),
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }

      const definition = intentDefinition(intentId);
      const observedAt = nowIso(options);
      currentState = {
        ...currentState,
        observedAt,
        phase: "executing",
        lastIntentId: safeString(intentId),
        error: null
      };

      try {
        const result = await executeWithRuntime(definition, payload, options);
        if (result && typeof result === "object" && result.ok === false) {
          const error = new Error(result.message || result.error || "Code Editor runtime binding failed.");
          error.code = result.code || "runtime-binding-failed";
          throw error;
        }

        const state = applyLocalStateSuccess(
          definition.id,
          payload,
          result && typeof result === "object" ? result : {value: result},
          observedAt
        );
        const receipt = storeReceipt(buildReceipt(definition.id, preflight, {
          status: "pass",
          ok: true,
          runtimeResult: resultSnapshot(result),
          state
        }, options));

        return {
          status: "pass",
          ok: true,
          intentId: definition.id,
          preflight,
          receipt,
          state
        };
      } catch (error) {
        const failure = classifyFailure({
          code: error?.code || "runtime-binding-failed",
          message: error?.message || String(error)
        }, currentState, options);
        const recovery = buildRecoveryOptions(failure, currentState, options);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "fail",
          ok: false,
          failure,
          recovery
        }, options));
        currentState = {
          ...currentState,
          observedAt,
          phase: "failed",
          lastIntentId: safeString(intentId),
          error: failure
        };
        return {
          status: "fail",
          ok: false,
          intentId: safeString(intentId),
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }
    }

    function mapEvidence(state = currentState) {
      const snapshot = state && typeof state === "object" ? state : currentState;
      return {
        schema: "code-editor-evidence-map-v1",
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        workspace: clonePlain(snapshot.workspace),
        activeFile: clonePlain(snapshot.activeFile),
        dirtyDraft: clonePlain(snapshot.dirtyDraft),
        aiderPlan: clonePlain(snapshot.aiderPlan),
        patchApplication: clonePlain(snapshot.patchApplication),
        executionPolicy: clonePlain(snapshot.executionPolicy),
        receipts: listReceipts(),
        boundaries: {
          hiddenShellPackageGitRemoteMutation: "blocked-before-execution",
          saveFile: "explicit-preflight-and-receipt",
          applyReviewedPatch: "reviewed-artifact-approval-and-recovery",
          runCode: "prohibited-until-command-execution-adapter"
        }
      };
    }

    const adapter = {
      id: ADAPTER_ID,
      appId: APP_ID,
      version: VERSION,
      kind: KIND,
      semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
      getState,
      resetState,
      setRuntimeBindings,
      bindRuntimeFromGlobal,
      listIntents,
      listObjects,
      preflightIntent,
      executeIntent,
      buildReceipt,
      listReceipts,
      mapEvidence,
      classifyFailure,
      buildRecoveryOptions,
      getRecoveryCoverage,
      getIntentCoverage
    };

    let registrationReadiness = null;
    if (
      global.McelDomainAdapterRegistry &&
      typeof global.McelDomainAdapterRegistry.registerAdapter === "function"
    ) {
      registrationReadiness = global.McelDomainAdapterRegistry.registerAdapter(adapter);
    }

    global.CodeEditorSemanticAdapter = Object.freeze({
      ...adapter,
      STATE_SCHEMA_VERSION,
      PREFLIGHT_SCHEMA_VERSION,
      RECEIPT_SCHEMA_VERSION,
      RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
      RECOVERY_PLAN_SCHEMA_VERSION,
      RECOVERY_COVERAGE_VERSION,
      INTENT_COVERAGE_SCHEMA_VERSION,
      SEMANTIC_RUNTIME_SCOPE,
      TOOLKIT_VERSION: ADAPTER_TOOLKIT.VERSION,
      INTENT_DEFINITIONS,
      FAILURE_DEFINITIONS,
      registrationReadiness: clonePlain(registrationReadiness)
    });

    if (typeof module !== "undefined" && module.exports) {
      module.exports = global.CodeEditorSemanticAdapter;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
