(() => {
  (function createMcelSemanticAdapterToolkit(global) {
    "use strict";

    if (!global) return;

    const VERSION = "mcel-semantic-adapter-toolkit-v1";
    const INTENT_STATUSES = Object.freeze([
      "executable",
      "preflight-only",
      "declared-only",
      "prohibited",
      "planned"
    ]);
    const CONTRACT_ID = "mcel.semantic-adapter-toolkit.conformance.v1";
    const CONTRACT_VERSION = "mcel-semantic-adapter-toolkit-conformance-v1";
    const REQUIRED_PUBLIC_API = Object.freeze([
      "VERSION",
      "INTENT_STATUSES",
      "CONTRACT_ID",
      "CONTRACT_VERSION",
      "clonePlain",
      "nowIso",
      "safeString",
      "normalizedId",
      "semanticStatusFor",
      "cloneIntentDeclaration",
      "intentDefinitionFor",
      "listIntentDefinitions",
      "recoveryCoverageAudit",
      "appendBoundedReceipt",
      "listBoundedReceipts",
      "preflightResult",
      "dispatchAction",
      "listConformanceClauses",
      "buildConformanceContract",
      "validateToolkitConformance"
    ]);
    const CONFORMANCE_CLAUSES = Object.freeze([
      Object.freeze({
        id: "mcel.semantic-adapter-toolkit.clone-plain.v1",
        category: "state-snapshot",
        requires: Object.freeze(["clonePlain"]),
        guarantee: "Shared helpers must clone plain adapter data without leaking callable runtime bindings.",
        evidence: Object.freeze(["tests/test_mcel_semantic_adapter_toolkit.py"])
      }),
      Object.freeze({
        id: "mcel.semantic-adapter-toolkit.intent-declaration.v1",
        category: "intent-declaration",
        requires: Object.freeze([
          "semanticStatusFor",
          "cloneIntentDeclaration",
          "intentDefinitionFor",
          "listIntentDefinitions"
        ]),
        guarantee: "Adapters classify current, planned, prohibited, and declared intents with stable semantic statuses.",
        evidence: Object.freeze(["tests/test_mcel_semantic_adapter_toolkit.py"])
      }),
      Object.freeze({
        id: "mcel.semantic-adapter-toolkit.preflight-receipt.v1",
        category: "preflight-and-receipts",
        requires: Object.freeze([
          "preflightResult",
          "appendBoundedReceipt",
          "listBoundedReceipts"
        ]),
        guarantee: "Shared preflight and receipt helpers preserve explicit allow/block decisions and bounded evidence ledgers.",
        evidence: Object.freeze(["tests/test_mcel_semantic_adapter_toolkit.py"])
      }),
      Object.freeze({
        id: "mcel.semantic-adapter-toolkit.dispatch.v1",
        category: "execution-dispatch",
        requires: Object.freeze(["dispatchAction"]),
        guarantee: "Dispatch reports unavailable or failing runtime bindings as explicit failed semantic receipts instead of throwing.",
        evidence: Object.freeze(["tests/test_mcel_semantic_adapter_toolkit.py"])
      }),
      Object.freeze({
        id: "mcel.semantic-adapter-toolkit.recovery-coverage.v1",
        category: "recovery-coverage",
        requires: Object.freeze(["recoveryCoverageAudit"]),
        guarantee: "Recovery coverage remains derived from declared failure classes and cannot pass with uncovered required classes.",
        evidence: Object.freeze(["tests/test_mcel_semantic_adapter_toolkit.py"])
      })
    ]);

    function clonePlain(value) {
      if (value == null || typeof value !== "object") return value;
      if (Array.isArray(value)) return value.map(clonePlain);
      return Object.fromEntries(
        Object.entries(value)
          .filter(([, entry]) => typeof entry !== "function")
          .map(([key, entry]) => [key, clonePlain(entry)])
      );
    }

    function nowIso(options = {}, config = {}) {
      if (config.literalOptionsNow === true) {
        return String(options.now || new Date().toISOString());
      }
      const value = typeof options.now === "function" ? options.now() : options.now;
      if (value instanceof Date) return value.toISOString();
      if (typeof value === "string" && value.trim()) return value.trim();
      if (value != null && value !== "") return String(value);
      return new Date().toISOString();
    }

    function safeString(value, config = {}) {
      const text = String(value == null ? "" : value);
      return config.trim === false ? text : text.trim();
    }

    function normalizedId(value) {
      return safeString(value);
    }

    function semanticStatusFor(intent = {}) {
      if (intent.semanticStatus) return safeString(intent.semanticStatus);
      if (intent.status === "planned") return "preflight-only";
      return safeString(intent.status || "declared-only");
    }

    function cloneIntentDeclaration(intent = {}, extra = {}) {
      return {
        ...clonePlain(intent),
        semanticStatus: semanticStatusFor(intent),
        ...clonePlain(extra)
      };
    }

    function intentDefinitionFor(intentDefinitions = [], intentOrId, config = {}) {
      const normalizer = typeof config.normalizeIntentId === "function"
        ? config.normalizeIntentId
        : normalizedId;
      const intentId = normalizer(intentOrId);
      return intentDefinitions.find((entry) => entry.id === intentId) || null;
    }

    function listIntentDefinitions(currentDefinitions = [], config = {}) {
      const allDefinitions = [
        ...currentDefinitions,
        ...(Array.isArray(config.plannedDefinitions) ? config.plannedDefinitions : [])
      ];
      return allDefinitions.map((definition) => {
        const extra = typeof config.mapDefinition === "function"
          ? config.mapDefinition(definition)
          : {};
        return cloneIntentDeclaration(definition, extra);
      });
    }

    function sortedKeys(value = {}) {
      return Object.keys(value || {}).sort();
    }

    function recoveryCoverageAudit(config = {}) {
      const requiredFailureClasses = sortedKeys(config.failureDefinitions);
      const coveredFailureClasses = requiredFailureClasses.filter((failureClass) => {
        if (typeof config.isCovered === "function") {
          return config.isCovered(failureClass) === true;
        }
        return true;
      });
      const unverifiedFailureClasses = requiredFailureClasses.filter(
        (failureClass) => !coveredFailureClasses.includes(failureClass)
      );
      const checks = typeof config.checks === "function"
        ? clonePlain(config.checks({requiredFailureClasses, coveredFailureClasses, unverifiedFailureClasses}))
        : clonePlain(config.checks || {});
      return {
        requiredFailureClasses,
        coveredFailureClasses,
        unverifiedFailureClasses,
        checks,
        passed: Object.values(checks).every(Boolean) && unverifiedFailureClasses.length === 0
      };
    }

    function appendBoundedReceipt(receiptLedger, receipt, config = {}) {
      if (!Array.isArray(receiptLedger)) {
        throw new TypeError("receiptLedger must be an array");
      }
      receiptLedger.push(receipt);
      const maxReceipts = Number(config.maxReceipts || 0);
      if (maxReceipts > 0 && receiptLedger.length > maxReceipts) {
        receiptLedger.splice(0, receiptLedger.length - maxReceipts);
      }
      return clonePlain(receipt);
    }

    function listBoundedReceipts(receiptLedger) {
      if (!Array.isArray(receiptLedger)) return [];
      return clonePlain(receiptLedger);
    }

    function preflightResult(config = {}) {
      const blockers = Array.isArray(config.blockers) ? clonePlain(config.blockers) : [];
      const allowed = config.allowed === true && blockers.length === 0;
      return {
        schema: safeString(config.schema),
        appId: safeString(config.appId),
        observedAt: nowIso(config.options || {}),
        intentId: safeString(config.intentId),
        allowed,
        decision: allowed ? "allow" : "block",
        blockers,
        checks: clonePlain(config.checks || {})
      };
    }

    function dispatchAction(binding, intentId, payload = {}, options = {}) {
      if (!binding || typeof binding !== "object") {
        return Promise.resolve({
          status: "fail",
          ok: false,
          code: "runtime-binding-unavailable",
          intentId: safeString(intentId)
        });
      }
      const methodName = safeString(options.methodName || options.runtimeMethod);
      const method = methodName ? binding[methodName] : null;
      if (typeof method !== "function") {
        return Promise.resolve({
          status: "fail",
          ok: false,
          code: "runtime-binding-unavailable",
          intentId: safeString(intentId),
          methodName
        });
      }
      try {
        return Promise.resolve(method.call(binding, payload, options));
      } catch (error) {
        return Promise.resolve({
          status: "fail",
          ok: false,
          code: safeString(error?.code || "runtime-binding-failed"),
          message: safeString(error?.message || error),
          intentId: safeString(intentId),
          methodName
        });
      }
    }

    function listConformanceClauses() {
      return clonePlain(CONFORMANCE_CLAUSES);
    }

    function buildConformanceContract() {
      return {
        id: CONTRACT_ID,
        version: CONTRACT_VERSION,
        toolkitVersion: VERSION,
        requiredPublicApi: clonePlain(REQUIRED_PUBLIC_API),
        intentStatuses: clonePlain(INTENT_STATUSES),
        clauses: listConformanceClauses(),
        nonGoals: [
          "does-not-define-application-domain-vocabulary",
          "does-not-authorize-new-semantic-runtime-scope",
          "does-not-execute-hidden-file-git-shell-package-or-publish-actions",
          "does-not-replace-runtime-or-acceptance-evidence"
        ]
      };
    }

    function validateToolkitConformance(candidateApi) {
      const target = candidateApi || api;
      const requiredApi = REQUIRED_PUBLIC_API.map((methodName) => ({
        name: methodName,
        present: Object.prototype.hasOwnProperty.call(target, methodName),
        callable:
          methodName === "VERSION" ||
          methodName === "INTENT_STATUSES" ||
          methodName === "CONTRACT_ID" ||
          methodName === "CONTRACT_VERSION"
            ? true
            : typeof target[methodName] === "function"
      }));
      const missingPublicApi = requiredApi
        .filter((entry) => entry.present !== true || entry.callable !== true)
        .map((entry) => entry.name);
      const requiredStatusVocabulary = [
        "executable",
        "preflight-only",
        "declared-only",
        "prohibited",
        "planned"
      ];
      const statusVocabulary = requiredStatusVocabulary.map((status) => ({
        status,
        present: Array.isArray(target.INTENT_STATUSES) && target.INTENT_STATUSES.includes(status)
      }));
      const missingIntentStatuses = statusVocabulary
        .filter((entry) => entry.present !== true)
        .map((entry) => entry.status);
      const clauses = CONFORMANCE_CLAUSES.map((clause) => {
        const missingRequirements = clause.requires.filter(
          (methodName) => typeof target[methodName] !== "function"
        );
        return {
          ...clonePlain(clause),
          missingRequirements,
          status: missingRequirements.length === 0 ? "pass" : "fail"
        };
      });
      const failedClauseIds = clauses
        .filter((clause) => clause.status !== "pass")
        .map((clause) => clause.id);
      return {
        schema: "mcel-semantic-adapter-toolkit-conformance-report-v1",
        contractId: CONTRACT_ID,
        contractVersion: CONTRACT_VERSION,
        toolkitVersion: safeString(target.VERSION),
        passed:
          safeString(target.VERSION) === VERSION &&
          missingPublicApi.length === 0 &&
          missingIntentStatuses.length === 0 &&
          failedClauseIds.length === 0,
        requiredApi,
        missingPublicApi,
        statusVocabulary,
        missingIntentStatuses,
        clauses,
        failedClauseIds
      };
    }

    const api = Object.freeze({
      VERSION,
      INTENT_STATUSES,
      CONTRACT_ID,
      CONTRACT_VERSION,
      clonePlain,
      nowIso,
      safeString,
      normalizedId,
      semanticStatusFor,
      cloneIntentDeclaration,
      intentDefinitionFor,
      listIntentDefinitions,
      recoveryCoverageAudit,
      appendBoundedReceipt,
      listBoundedReceipts,
      preflightResult,
      dispatchAction,
      listConformanceClauses,
      buildConformanceContract,
      validateToolkitConformance
    });

    global.McelSemanticAdapterToolkit = api;

    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
