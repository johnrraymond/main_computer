(() => {
  (function createFileExplorerSemanticAdapter(global) {
    "use strict";

    if (!global) return;

    const VERSION = "file-explorer-semantic-adapter-read-only-v1";
    const APP_ID = "file-explorer";
    const ADAPTER_ID = "file-explorer-domain-adapter";
    const KIND = "bounded-read-only-file-navigation-domain-adapter";
    const STATE_SCHEMA_VERSION = "file-explorer-semantic-state-v1";
    const PREFLIGHT_SCHEMA_VERSION = "file-explorer-preflight-v1";
    const RECEIPT_SCHEMA_VERSION = "mcel-semantic-receipt-v1";
    const RECOVERY_CLASSIFICATION_SCHEMA_VERSION = "file-explorer-recovery-classification-v1";
    const RECOVERY_PLAN_SCHEMA_VERSION = "file-explorer-recovery-plan-v1";
    const RECOVERY_COVERAGE_VERSION = "file-explorer-recovery-coverage-v1";
    const INTENT_COVERAGE_SCHEMA_VERSION = "file-explorer-intent-coverage-v1";
    const SEMANTIC_RUNTIME_SCOPE = "bounded-read-only-file-explorer-v1";
    const MAX_RECEIPTS = 100;
    const ADAPTER_TOOLKIT = global.McelSemanticAdapterToolkit || (
      typeof require === "function" ? require("./mcel-semantic-adapter-toolkit.js") : null
    );

    if (!ADAPTER_TOOLKIT) {
      throw new Error("McelSemanticAdapterToolkit must be loaded before FileExplorerSemanticAdapter.");
    }

    const ENDPOINTS = Object.freeze({
      inspectRoots: "/api/applications/file-explorer/roots",
      listDirectory: "/api/applications/file-explorer/list",
      searchCurrentFolder: "/api/applications/file-explorer/search",
      previewEntry: "/api/applications/file-explorer/read"
    });

    const CURRENT_INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "inspectRoots",
        label: "Inspect available roots",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-api.roots",
        mutates: false
      }),
      Object.freeze({
        id: "selectRoot",
        label: "Select a trusted root",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-adapter.select-root-then-list",
        mutates: false
      }),
      Object.freeze({
        id: "listDirectory",
        label: "List the current directory",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-api.list",
        mutates: false
      }),
      Object.freeze({
        id: "navigateUp",
        label: "Navigate to the parent directory",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-adapter.parent-then-list",
        mutates: false
      }),
      Object.freeze({
        id: "searchCurrentFolder",
        label: "Search within the current folder",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-api.search",
        mutates: false
      }),
      Object.freeze({
        id: "previewEntry",
        label: "Preview a bounded entry",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-api.read",
        mutates: false
      }),
      Object.freeze({
        id: "classifyEntry",
        label: "Classify an entry for an owning app",
        risk: "safe-read",
        status: "executable",
        executionBinding: "file-explorer-adapter.classify-entry",
        mutates: false
      }),
      Object.freeze({
        id: "deleteFile",
        label: "Delete a file",
        risk: "prohibited-mutation",
        status: "prohibited",
        executionBinding: "policy-prohibited",
        mutates: true
      }),
      Object.freeze({
        id: "moveOrRename",
        label: "Move or rename an entry",
        risk: "prohibited-mutation",
        status: "prohibited",
        executionBinding: "policy-prohibited",
        mutates: true
      }),
      Object.freeze({
        id: "runFileCommand",
        label: "Run a command against a file",
        risk: "prohibited-command-execution",
        status: "prohibited",
        executionBinding: "policy-prohibited",
        mutates: "potential"
      })
    ]);

    const PLANNED_INTENT_DEFINITIONS = Object.freeze([
      Object.freeze({
        id: "openInOwningApp",
        label: "Open the selected entry in an owning app",
        risk: "cross-app-handoff",
        status: "planned",
        semanticStatus: "preflight-only",
        executionBinding: "handoff-contract-not-implemented",
        mutates: false
      })
    ]);

    const FAILURE_DEFINITIONS = Object.freeze({
      "transport-unavailable": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: false,
        message: "The File Explorer API transport is unavailable.",
        nextStep: "Restore the viewport API transport, then retry the read-only intent."
      }),
      "request-failed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        message: "The File Explorer API request failed.",
        nextStep: "Refresh roots or the current directory, then retry."
      }),
      "state-not-observed": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        message: "File Explorer state has not been observed.",
        nextStep: "Inspect available roots before executing a scoped browsing intent."
      }),
      "unknown-root": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        message: "The requested root is not in the trusted root inventory.",
        nextStep: "Refresh roots and choose one of the returned root identifiers."
      }),
      "path-invalid": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        message: "The requested relative path is invalid or escapes the selected root.",
        nextStep: "Use a normalized root-relative path without '..' or an absolute prefix."
      }),
      "path-not-found": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: true,
        message: "The requested entry or directory was not found.",
        nextStep: "Refresh the current directory and select an existing entry."
      }),
      "query-required": Object.freeze({
        severity: "blocking",
        retrySafe: true,
        refreshRequired: false,
        message: "A non-empty search query is required.",
        nextStep: "Enter a search term and retry the bounded search."
      }),
      "preview-unavailable": Object.freeze({
        severity: "informational",
        retrySafe: false,
        refreshRequired: false,
        message: "Content preview is unavailable for this entry.",
        nextStep: "Use the returned metadata and preview-denied reason."
      }),
      "prohibited-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        message: "The requested mutation is prohibited in the read-only File Explorer.",
        nextStep: "Use a separate governed mutation workflow; File Explorer will not perform the action."
      }),
      "handoff-not-implemented": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        message: "Cross-app handoff is planned but not part of the current semantic runtime scope.",
        nextStep: "Choose the target app manually until a governed handoff contract is implemented."
      }),
      "unsupported-intent": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: false,
        message: "The requested intent is not registered by the File Explorer adapter.",
        nextStep: "Choose a declared read-only intent."
      }),
      "unknown-failure": Object.freeze({
        severity: "blocking",
        retrySafe: false,
        refreshRequired: true,
        message: "The File Explorer adapter could not classify the failure.",
        nextStep: "Refresh roots and current directory while keeping all mutation intents disabled."
      })
    });

    function clonePlain(value) {
      return ADAPTER_TOOLKIT.clonePlain(value);
    }

    function nowIso(options = {}) {
      return ADAPTER_TOOLKIT.nowIso(options, {literalOptionsNow: true});
    }

    function safeString(value) {
      return ADAPTER_TOOLKIT.safeString(value, {trim: false});
    }

    function normalizeIntentId(intentOrId) {
      const raw = typeof intentOrId === "object"
        ? intentOrId?.id || intentOrId?.intentId || intentOrId?.intent
        : intentOrId;
      const value = safeString(raw).trim();
      return value.startsWith("file-explorer.intent.")
        ? value.slice("file-explorer.intent.".length)
        : value;
    }

    function normalizeRelativePath(value) {
      const raw = safeString(value).replace(/\\/g, "/").trim();
      if (!raw || raw === ".") return "";
      if (/^[a-zA-Z]:\//.test(raw) || raw.startsWith("/") || raw.startsWith("//")) {
        throw Object.assign(new Error("Absolute paths are not allowed."), {code: "path-invalid"});
      }
      const parts = raw.split("/").filter((part) => part && part !== ".");
      if (parts.some((part) => part === "..")) {
        throw Object.assign(new Error("Path traversal is not allowed."), {code: "path-invalid"});
      }
      return parts.join("/");
    }

    function initialState() {
      return {
        schemaVersion: STATE_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        source: "uninitialized",
        observedAt: "",
        phase: "uninitialized",
        readOnly: true,
        roots: [],
        selectedRootId: "",
        relativePath: "",
        entries: [],
        selectedEntry: null,
        preview: null,
        search: {
          query: "",
          results: [],
          count: 0
        },
        lastIntentId: "",
        lastReceiptId: "",
        error: null
      };
    }

    let currentState = initialState();
    let receiptSequence = 0;
    let receiptLedger = [];
    let transportOverride = null;

    function getState() {
      return clonePlain(currentState);
    }

    function setState(patch = {}) {
      currentState = {
        ...currentState,
        ...clonePlain(patch),
        readOnly: true
      };
      return getState();
    }

    function resetState() {
      currentState = initialState();
      receiptSequence = 0;
      receiptLedger = [];
      return getState();
    }

    function defaultTransport(path, payload = {}) {
      if (typeof global.fetch !== "function") {
        throw Object.assign(new Error("File Explorer API transport is unavailable."), {
          code: "transport-unavailable"
        });
      }
      return global.fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload || {})
      }).then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
          const error = new Error(
            data.message || data.error || `file explorer API returned ${response.status}`
          );
          error.code = safeString(data.code || "request-failed");
          error.status = response.status;
          error.payload = data;
          throw error;
        }
        return data;
      });
    }

    function setTransport(transport) {
      if (transport !== null && typeof transport !== "function") {
        throw new TypeError("File Explorer semantic transport must be a function or null.");
      }
      transportOverride = transport;
      return Boolean(transportOverride);
    }

    function activeTransport(options = {}) {
      if (typeof options.transport === "function") return options.transport;
      if (typeof transportOverride === "function") return transportOverride;
      return defaultTransport;
    }

    function definitionFor(intentOrId) {
      return ADAPTER_TOOLKIT.intentDefinitionFor(
        [...CURRENT_INTENT_DEFINITIONS, ...PLANNED_INTENT_DEFINITIONS],
        intentOrId,
        {normalizeIntentId}
      );
    }

    function rootKnown(rootId, state = currentState) {
      const candidate = safeString(rootId).trim();
      return Boolean(candidate && (state.roots || []).some((root) => root?.id === candidate));
    }

    function selectedRoot(parameters = {}, state = currentState) {
      return safeString(
        parameters.rootId ??
        parameters.root_id ??
        state.selectedRootId
      ).trim();
    }

    function requestedPath(parameters = {}, state = currentState) {
      return normalizeRelativePath(
        parameters.relativePath ??
        parameters.relative_path ??
        state.relativePath
      );
    }

    function preflightIntent(intentOrId, state = getState(), options = {}) {
      const intentId = normalizeIntentId(intentOrId);
      const definition = definitionFor(intentId);
      const parameters = clonePlain(options.parameters || options.payload || {});
      const blockers = [];
      const warnings = [];
      const safeState = state && typeof state === "object" ? state : getState();

      function block(code, message) {
        blockers.push({code, message});
      }

      if (!definition) {
        block("unsupported-intent", `Unsupported File Explorer intent: ${intentId || "unknown"}.`);
      } else if (definition.status === "prohibited") {
        block("prohibited-intent", `${definition.label} is prohibited in the read-only File Explorer.`);
      } else if (definition.status === "planned") {
        block("handoff-not-implemented", `${definition.label} is not implemented in the current adapter scope.`);
      }

      let rootId = "";
      let relativePath = "";
      try {
        rootId = selectedRoot(parameters, safeState);
        relativePath = requestedPath(parameters, safeState);
      } catch (error) {
        block(error.code || "path-invalid", error.message || "Invalid relative path.");
      }

      if (definition && ["selectRoot", "listDirectory", "navigateUp", "searchCurrentFolder", "previewEntry"].includes(intentId)) {
        if (!rootId) {
          block("state-not-observed", "A trusted root must be selected first.");
        } else if ((safeState.roots || []).length > 0 && !rootKnown(rootId, safeState)) {
          block("unknown-root", `Unknown File Explorer root: ${rootId}.`);
        }
      }

      if (intentId === "selectRoot" && (safeState.roots || []).length === 0) {
        block("state-not-observed", "Inspect roots before selecting one.");
      }

      if (intentId === "searchCurrentFolder") {
        const query = safeString(parameters.query).trim();
        if (!query) block("query-required", "A non-empty search query is required.");
      }

      if (intentId === "previewEntry") {
        const entry = parameters.entry || null;
        const entryPath = safeString(
          parameters.relativePath ??
          parameters.relative_path ??
          entry?.relative_path ??
          entry?.relativePath
        ).trim();
        if (!entryPath) block("path-not-found", "A selected entry path is required for preview.");
      }

      if (intentId === "classifyEntry" && !parameters.entry) {
        block("path-not-found", "An entry is required for classification.");
      }

      const decision = blockers.length ? "block" : "allow";
      return {
        schemaVersion: PREFLIGHT_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        intentId,
        decision,
        allowed: decision === "allow",
        readOnly: true,
        rootId,
        relativePath,
        blockers,
        warnings,
        parameters,
        checkedAt: nowIso(options)
      };
    }

    function classifyEntry(entry = {}) {
      const path = safeString(entry.relative_path ?? entry.relativePath ?? entry.name).toLowerCase();
      const kind = safeString(entry.kind).toLowerCase();
      const suffixMatch = path.match(/(\.[a-z0-9]+)$/);
      const suffix = safeString(entry.extension || suffixMatch?.[1]).toLowerCase();
      if (entry.category && entry.category !== "other") {
        return {
          category: safeString(entry.category),
          suggestedApp: safeString(entry.suggested_app ?? entry.suggestedApp),
          source: "backend-entry-evidence"
        };
      }
      if (kind === "directory" && /(^|\/)(game_projects|assets|scripts|data|builds)(\/|$)/.test(path)) {
        return {category: "game", suggestedApp: "game-editor", source: "adapter-path-classifier"};
      }
      if ([".csv", ".tsv", ".xlsx", ".xls"].includes(suffix)) {
        return {category: "spreadsheet", suggestedApp: "spreadsheet", source: "adapter-extension-classifier"};
      }
      if ([".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".wav", ".ogg", ".glb", ".gltf", ".obj"].includes(suffix)) {
        return {category: "asset", suggestedApp: "game-editor", source: "adapter-extension-classifier"};
      }
      if ([".txt", ".md", ".rst", ".log"].includes(suffix)) {
        return {category: "text", suggestedApp: "document", source: "adapter-extension-classifier"};
      }
      if ([".py", ".js", ".ts", ".html", ".css", ".json", ".toml", ".yaml", ".yml", ".ps1", ".sh"].includes(suffix)) {
        return {category: "code", suggestedApp: "code-editor", source: "adapter-extension-classifier"};
      }
      return {category: "other", suggestedApp: "", source: "adapter-extension-classifier"};
    }

    function listIntents(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      return ADAPTER_TOOLKIT.listIntentDefinitions(CURRENT_INTENT_DEFINITIONS, {
        plannedDefinitions: PLANNED_INTENT_DEFINITIONS,
        mapDefinition(definition) {
          const parameters = {};
          if (["listDirectory", "navigateUp", "searchCurrentFolder", "previewEntry"].includes(definition.id)) {
            parameters.rootId = safeState.selectedRootId;
            parameters.relativePath = safeState.relativePath;
          }
          const preflight = preflightIntent(definition.id, safeState, {parameters});
          return {
            semanticStatus: ADAPTER_TOOLKIT.semanticStatusFor(definition),
            executable: definition.status === "executable",
            prohibited: definition.status === "prohibited",
            planned: definition.status === "planned",
            available: definition.id === "inspectRoots" || preflight.allowed,
            blockedReason: preflight.blockers.map((item) => item.message).join(" ")
          };
        }
      });
    }

    function listObjects(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      const objects = [
        {
          id: "file-explorer-state",
          kind: "application-state",
          label: "File Explorer state",
          readOnly: true,
          phase: safeState.phase
        },
        {
          id: "trusted-roots",
          kind: "root-inventory",
          label: "Trusted roots",
          readOnly: true,
          count: (safeState.roots || []).length,
          items: clonePlain(safeState.roots || [])
        },
        {
          id: "current-directory",
          kind: "directory-scope",
          label: "Current directory",
          readOnly: true,
          rootId: safeState.selectedRootId,
          relativePath: safeState.relativePath
        },
        {
          id: "directory-entries",
          kind: "entry-collection",
          label: "Directory entries",
          readOnly: true,
          count: (safeState.entries || []).length,
          items: clonePlain(safeState.entries || [])
        }
      ];
      if (safeState.selectedEntry) {
        objects.push({
          id: "selected-entry",
          kind: "file-entry",
          label: safeString(safeState.selectedEntry.name || "Selected entry"),
          readOnly: true,
          entry: clonePlain(safeState.selectedEntry)
        });
      }
      if (safeState.preview) {
        objects.push({
          id: "entry-preview",
          kind: "read-only-preview",
          label: "Entry preview",
          readOnly: true,
          preview: clonePlain(safeState.preview)
        });
      }
      return objects;
    }

    function nextReceiptId(intentId, createdAt) {
      receiptSequence += 1;
      const stamp = Date.parse(createdAt) || Date.now();
      return `${APP_ID}-${intentId || "intent"}-${stamp}-${receiptSequence}`;
    }

    function storeReceipt(receipt) {
      const storedReceipt = ADAPTER_TOOLKIT.appendBoundedReceipt(receiptLedger, clonePlain(receipt), {
        maxReceipts: MAX_RECEIPTS
      });
      currentState = {
        ...currentState,
        lastReceiptId: receipt.receiptId || currentState.lastReceiptId
      };
      return storedReceipt;
    }

    function buildReceipt(intentOrId, preflight = {}, result = {}, options = {}) {
      const intentId = normalizeIntentId(intentOrId);
      const createdAt = nowIso(options);
      const status = safeString(result.status || (
        preflight.allowed === false ? "blocked" : "pass"
      ));
      return {
        schemaVersion: RECEIPT_SCHEMA_VERSION,
        receiptId: nextReceiptId(intentId, createdAt),
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        intentId,
        kind: "read-only-semantic-execution-receipt",
        status,
        decision: preflight.decision || (status === "blocked" ? "block" : "allow"),
        readOnly: true,
        mutationAttempted: false,
        executionBinding: definitionFor(intentId)?.executionBinding || "not-registered",
        parameters: clonePlain(preflight.parameters || options.parameters || {}),
        blockers: clonePlain(preflight.blockers || []),
        warnings: clonePlain(preflight.warnings || []),
        result: clonePlain(result),
        createdAt
      };
    }

    function failureCodeFrom(error) {
      const explicit = safeString(error?.code).trim();
      if (FAILURE_DEFINITIONS[explicit]) return explicit;
      const message = safeString(error?.message || error).toLowerCase();
      if (message.includes("transport") || message.includes("fetch")) return "transport-unavailable";
      if (message.includes("traversal") || message.includes("escapes") || message.includes("absolute path")) return "path-invalid";
      if (message.includes("unknown file explorer root") || message.includes("unknown root")) return "unknown-root";
      if (message.includes("not found")) return "path-not-found";
      if (message.includes("query") && message.includes("required")) return "query-required";
      return "request-failed";
    }

    function classifyFailure(input, state = getState(), options = {}) {
      const sourceCode = failureCodeFrom(input);
      const definition = FAILURE_DEFINITIONS[sourceCode] || FAILURE_DEFINITIONS["unknown-failure"];
      return {
        schemaVersion: RECOVERY_CLASSIFICATION_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        failureClass: sourceCode,
        sourceCode,
        sourceMessage: safeString(input?.message || input),
        severity: definition.severity,
        retrySafe: definition.retrySafe,
        refreshRequired: definition.refreshRequired,
        mutationAllowed: false,
        message: definition.message,
        recommendedNextStep: definition.nextStep,
        prohibitedActions: ["deleteFile", "moveOrRename", "runFileCommand"],
        classifiedAt: nowIso(options),
        statePhase: safeString(state?.phase)
      };
    }

    function recoveryOptionsFor(failureClass) {
      const commonRefresh = {
        intentId: "inspectRoots",
        label: "Refresh trusted roots",
        kind: "governed-read",
        executable: true,
        safe: true
      };
      const listRefresh = {
        intentId: "listDirectory",
        label: "Refresh the current directory",
        kind: "governed-read",
        executable: true,
        safe: true
      };
      const options = {
        "transport-unavailable": [commonRefresh],
        "request-failed": [commonRefresh, listRefresh],
        "state-not-observed": [commonRefresh],
        "unknown-root": [commonRefresh],
        "path-invalid": [
          {intentId: "chooseBoundedPath", label: "Choose a normalized root-relative path", kind: "human-action", executable: false, safe: true}
        ],
        "path-not-found": [listRefresh],
        "query-required": [
          {intentId: "enterSearchQuery", label: "Enter a non-empty search query", kind: "human-action", executable: false, safe: true}
        ],
        "preview-unavailable": [
          {intentId: "inspectMetadata", label: "Use metadata-only preview evidence", kind: "inspection", executable: false, safe: true}
        ],
        "prohibited-intent": [
          {intentId: "useGovernedMutationApp", label: "Use a separate governed mutation workflow", kind: "human-action", executable: false, safe: true}
        ],
        "handoff-not-implemented": [
          {intentId: "openTargetAppManually", label: "Open the target app manually", kind: "human-action", executable: false, safe: true}
        ],
        "unsupported-intent": [
          {intentId: "inspectIntentCatalog", label: "Inspect registered File Explorer intents", kind: "inspection", executable: false, safe: true}
        ],
        "unknown-failure": [commonRefresh, listRefresh]
      };
      return clonePlain(options[failureClass] || options["unknown-failure"]);
    }

    function buildRecoveryOptions(failureOrInput, state = getState(), options = {}) {
      const failure = failureOrInput?.schemaVersion === RECOVERY_CLASSIFICATION_SCHEMA_VERSION
        ? clonePlain(failureOrInput)
        : classifyFailure(failureOrInput, state, options);
      return {
        schemaVersion: RECOVERY_PLAN_SCHEMA_VERSION,
        appId: APP_ID,
        adapterId: ADAPTER_ID,
        adapterVersion: VERSION,
        failureClass: failure.failureClass,
        severity: failure.severity,
        retrySafe: failure.retrySafe,
        refreshRequired: failure.refreshRequired,
        mutationAllowed: false,
        recommendedNextStep: failure.recommendedNextStep,
        options: recoveryOptionsFor(failure.failureClass),
        prohibitedActions: ["deleteFile", "moveOrRename", "runFileCommand"],
        generatedAt: nowIso(options)
      };
    }

    function getRecoveryCoverage() {
      const audit = ADAPTER_TOOLKIT.recoveryCoverageAudit({
        failureDefinitions: FAILURE_DEFINITIONS,
        isCovered: (failureClass) => recoveryOptionsFor(failureClass).length > 0,
        checks({requiredFailureClasses, unverifiedFailureClasses}) {
          return {
            definitionsComplete: requiredFailureClasses.every((failureClass) => {
              const definition = FAILURE_DEFINITIONS[failureClass];
              return Boolean(
                definition &&
                safeString(definition.severity) &&
                typeof definition.retrySafe === "boolean" &&
                typeof definition.refreshRequired === "boolean" &&
                safeString(definition.message) &&
                safeString(definition.nextStep)
              );
            }),
            guidanceComplete: unverifiedFailureClasses.length === 0,
            prohibitedMutationFallbackDeclared: true
          };
        }
      });
      return {
        version: RECOVERY_COVERAGE_VERSION,
        source: "file-explorer-recovery-coverage-audit-v1",
        verificationMode: "derived-runtime-audit",
        classificationReady: audit.checks.definitionsComplete,
        guidanceReady: audit.checks.guidanceComplete,
        coverageReady: audit.passed,
        requiredFailureClasses: audit.requiredFailureClasses,
        coveredFailureClasses: audit.coveredFailureClasses,
        unverifiedFailureClasses: audit.unverifiedFailureClasses,
        verification: {
          passed: audit.passed,
          checks: audit.checks
        }
      };
    }

    function getIntentCoverage() {
      const entries = CURRENT_INTENT_DEFINITIONS.map((definition) => ({
        intentId: definition.id,
        label: definition.label,
        risk: definition.risk,
        mutates: definition.mutates,
        status: definition.status,
        executable: definition.status === "executable",
        preflightAvailable: definition.status === "executable",
        prohibited: definition.status === "prohibited",
        executionBinding: definition.executionBinding,
        complete: ["executable", "prohibited"].includes(definition.status)
      }));
      const requiredIntentIds = entries.map((entry) => entry.intentId);
      const executableIntentIds = entries.filter((entry) => entry.status === "executable").map((entry) => entry.intentId);
      const prohibitedIntentIds = entries.filter((entry) => entry.status === "prohibited").map((entry) => entry.intentId);
      const fullApplicationSemanticReady = entries.every((entry) => entry.complete === true);
      const checks = {
        currentScopeDeclared: SEMANTIC_RUNTIME_SCOPE === "bounded-read-only-file-explorer-v1",
        allCurrentIntentsClassified: entries.length === CURRENT_INTENT_DEFINITIONS.length,
        uniqueIntentIds: new Set(requiredIntentIds).size === requiredIntentIds.length,
        executionBindingsDeclared: entries.every((entry) => Boolean(entry.executionBinding)),
        readOnlyExecutables: entries
          .filter((entry) => entry.status === "executable")
          .every((entry) => entry.mutates === false),
        mutationIntentsProhibited: ["deleteFile", "moveOrRename", "runFileCommand"].every(
          (intentId) => entries.some((entry) => entry.intentId === intentId && entry.status === "prohibited")
        ),
        plannedHandoffExcludedFromCurrentScope: PLANNED_INTENT_DEFINITIONS.every(
          (entry) => entry.status === "planned"
        )
      };
      const passed = Object.values(checks).every(Boolean);
      return {
        schemaVersion: INTENT_COVERAGE_SCHEMA_VERSION,
        source: "file-explorer-intent-coverage-audit-v1",
        verificationMode: "derived-intent-coverage-audit",
        semanticRuntimeScope: SEMANTIC_RUNTIME_SCOPE,
        fullApplicationSemanticReady: Boolean(passed && fullApplicationSemanticReady),
        requiredIntentIds,
        classifiedIntentIds: requiredIntentIds.slice(),
        executableIntentIds,
        preflightOnlyIntentIds: [],
        declaredOnlyIntentIds: [],
        prohibitedIntentIds,
        excludedPlannedIntentIds: PLANNED_INTENT_DEFINITIONS.map((entry) => entry.id),
        incompleteIntentIds: [],
        entries,
        verification: {
          passed,
          checks
        }
      };
    }

    function parentPath(path) {
      const parts = normalizeRelativePath(path).split("/").filter(Boolean);
      parts.pop();
      return parts.join("/");
    }

    function updateStateForResult(intentId, parameters, raw, observedAt) {
      const base = {
        source: definitionFor(intentId)?.executionBinding || "file-explorer-semantic-adapter",
        observedAt,
        phase: "ready",
        lastIntentId: intentId,
        error: null
      };
      if (intentId === "inspectRoots") {
        const roots = clonePlain(raw.roots || []);
        const selectedRootId = currentState.selectedRootId && roots.some((root) => root.id === currentState.selectedRootId)
          ? currentState.selectedRootId
          : "";
        return setState({...base, roots, selectedRootId});
      }
      if (["selectRoot", "listDirectory", "navigateUp"].includes(intentId)) {
        return setState({
          ...base,
          selectedRootId: safeString(raw.root_id || parameters.rootId || parameters.root_id),
          relativePath: normalizeRelativePath(raw.relative_path || ""),
          entries: clonePlain(raw.entries || []),
          selectedEntry: null,
          preview: null,
          search: {query: "", results: [], count: 0}
        });
      }
      if (intentId === "searchCurrentFolder") {
        return setState({
          ...base,
          selectedRootId: safeString(raw.root_id || parameters.rootId || parameters.root_id || currentState.selectedRootId),
          search: {
            query: safeString(raw.query || parameters.query),
            results: clonePlain(raw.results || []),
            count: Number(raw.count || 0)
          }
        });
      }
      if (intentId === "previewEntry") {
        return setState({
          ...base,
          selectedEntry: clonePlain(raw.entry || parameters.entry || null),
          preview: {
            readable: raw.readable === true,
            reason: safeString(raw.reason),
            encoding: safeString(raw.encoding),
            content: raw.readable === true ? safeString(raw.content) : "",
            entry: clonePlain(raw.entry || parameters.entry || null)
          }
        });
      }
      if (intentId === "classifyEntry") {
        return setState({
          ...base,
          selectedEntry: {
            ...clonePlain(parameters.entry || {}),
            category: raw.category,
            suggested_app: raw.suggestedApp
          }
        });
      }
      return setState(base);
    }

    async function executeIntent(intentOrId, parameters = {}, options = {}) {
      const intentId = normalizeIntentId(intentOrId);
      const state = getState();
      const preflight = preflightIntent(intentId, state, {
        ...options,
        parameters
      });
      if (!preflight.allowed) {
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "blocked",
          failureClass: preflight.blockers[0]?.code || "unsupported-intent"
        }, options));
        return {
          status: "blocked",
          ok: false,
          intentId,
          preflight,
          receipt,
          state: getState()
        };
      }

      const observedAt = nowIso(options);
      try {
        let raw;
        const transport = activeTransport(options);
        if (intentId === "inspectRoots") {
          raw = await transport(ENDPOINTS.inspectRoots, {});
        } else if (intentId === "selectRoot") {
          const rootId = selectedRoot(parameters, state);
          raw = await transport(ENDPOINTS.listDirectory, {
            root_id: rootId,
            relative_path: normalizeRelativePath(parameters.relativePath ?? parameters.relative_path ?? "")
          });
        } else if (intentId === "listDirectory") {
          raw = await transport(ENDPOINTS.listDirectory, {
            root_id: selectedRoot(parameters, state),
            relative_path: requestedPath(parameters, state)
          });
        } else if (intentId === "navigateUp") {
          raw = await transport(ENDPOINTS.listDirectory, {
            root_id: selectedRoot(parameters, state),
            relative_path: parentPath(parameters.relativePath ?? parameters.relative_path ?? state.relativePath)
          });
        } else if (intentId === "searchCurrentFolder") {
          raw = await transport(ENDPOINTS.searchCurrentFolder, {
            root_id: selectedRoot(parameters, state),
            relative_path: requestedPath(parameters, state),
            query: safeString(parameters.query).trim(),
            limit: Math.max(1, Math.min(Number(parameters.limit || 80), 200))
          });
        } else if (intentId === "previewEntry") {
          const entry = parameters.entry || {};
          raw = await transport(ENDPOINTS.previewEntry, {
            root_id: selectedRoot(parameters, state),
            relative_path: normalizeRelativePath(
              parameters.relativePath ??
              parameters.relative_path ??
              entry.relative_path ??
              entry.relativePath
            )
          });
        } else if (intentId === "classifyEntry") {
          raw = classifyEntry(parameters.entry || {});
        } else {
          throw Object.assign(new Error(`Unsupported File Explorer intent: ${intentId}.`), {
            code: "unsupported-intent"
          });
        }

        const nextState = updateStateForResult(intentId, parameters, raw || {}, observedAt);
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "pass",
          ok: true,
          raw: clonePlain(raw || {}),
          readOnly: true
        }, options));
        return {
          status: "pass",
          ok: true,
          intentId,
          preflight,
          receipt,
          result: clonePlain(raw || {}),
          state: nextState
        };
      } catch (error) {
        const failure = classifyFailure(error, state, options);
        const recovery = buildRecoveryOptions(failure, state, options);
        currentState = {
          ...currentState,
          observedAt,
          phase: "error",
          lastIntentId: intentId,
          error: {
            code: failure.failureClass,
            message: safeString(error?.message || error)
          }
        };
        const receipt = storeReceipt(buildReceipt(intentId, preflight, {
          status: "fail",
          ok: false,
          failure,
          recovery
        }, options));
        return {
          status: "fail",
          ok: false,
          intentId,
          preflight,
          receipt,
          failure,
          recovery,
          state: getState()
        };
      }
    }

    async function requestEndpoint(path, payload = {}, options = {}) {
      const mapping = {
        [ENDPOINTS.inspectRoots]: "inspectRoots",
        [ENDPOINTS.listDirectory]: "listDirectory",
        [ENDPOINTS.searchCurrentFolder]: "searchCurrentFolder",
        [ENDPOINTS.previewEntry]: "previewEntry"
      };
      const intentId = mapping[path];
      if (!intentId) {
        throw new Error(`Unsupported File Explorer endpoint: ${path}`);
      }
      const execution = await executeIntent(intentId, {
        rootId: payload.root_id,
        relativePath: payload.relative_path,
        query: payload.query,
        limit: payload.limit,
        entry: payload.entry
      }, options);
      if (!execution.ok) {
        const error = new Error(
          execution.failure?.message ||
          execution.preflight?.blockers?.[0]?.message ||
          "File Explorer semantic execution failed."
        );
        error.code = execution.failure?.failureClass ||
          execution.preflight?.blockers?.[0]?.code ||
          "request-failed";
        error.semanticExecution = execution;
        throw error;
      }
      return clonePlain(execution.result);
    }

    function listReceipts() {
      return ADAPTER_TOOLKIT.listBoundedReceipts(receiptLedger);
    }

    function mapEvidence(state = getState()) {
      const safeState = state && typeof state === "object" ? state : getState();
      const evidence = [{
        evidenceId: "file-explorer-state",
        kind: "state-snapshot",
        source: safeState.source,
        observedAt: safeState.observedAt,
        authoritative: safeState.source !== "uninitialized",
        receiptBacked: false,
        claims: {
          phase: safeState.phase,
          readOnly: true,
          rootCount: (safeState.roots || []).length,
          selectedRootId: safeState.selectedRootId,
          relativePath: safeState.relativePath,
          entryCount: (safeState.entries || []).length,
          searchResultCount: Number(safeState.search?.count || 0),
          previewReadable: safeState.preview?.readable === true
        }
      }];
      listReceipts().forEach((receipt) => {
        evidence.push({
          evidenceId: receipt.receiptId,
          kind: receipt.kind,
          source: ADAPTER_ID,
          observedAt: receipt.createdAt,
          authoritative: true,
          receiptBacked: true,
          receiptId: receipt.receiptId,
          claims: {
            intentId: receipt.intentId,
            status: receipt.status,
            decision: receipt.decision,
            readOnly: receipt.readOnly === true,
            mutationAttempted: receipt.mutationAttempted === true,
            executionBinding: receipt.executionBinding,
            failureClass: receipt.result?.failure?.failureClass || ""
          }
        });
      });
      return evidence;
    }

    const adapter = Object.freeze({
      id: ADAPTER_ID,
      appId: APP_ID,
      version: VERSION,
      kind: KIND,
      getState,
      listObjects,
      listIntents,
      preflightIntent,
      executeIntent,
      buildReceipt,
      mapEvidence,
      classifyFailure,
      buildRecoveryOptions,
      getRecoveryCoverage,
      getIntentCoverage,
      requestEndpoint,
      classifyEntry,
      setTransport,
      listReceipts,
      resetState
    });

    let registrationReadiness = null;
    if (
      global.McelDomainAdapterRegistry &&
      typeof global.McelDomainAdapterRegistry.registerAdapter === "function"
    ) {
      registrationReadiness = global.McelDomainAdapterRegistry.registerAdapter(adapter);
    }

    global.FileExplorerSemanticAdapter = Object.freeze({
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
      ENDPOINTS,
      CURRENT_INTENT_DEFINITIONS,
      PLANNED_INTENT_DEFINITIONS,
      FAILURE_DEFINITIONS,
      registrationReadiness: clonePlain(registrationReadiness)
    });

    if (typeof module !== "undefined" && module.exports) {
      module.exports = global.FileExplorerSemanticAdapter;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
