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

    const api = Object.freeze({
      VERSION,
      INTENT_STATUSES,
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
      dispatchAction
    });

    global.McelSemanticAdapterToolkit = api;

    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
