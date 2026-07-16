(() => {
  (function createMcelDomainAdapterRegistry(global) {
    if (!global) return;

    const REGISTRY_VERSION = "mcel-domain-adapter-registry-v4";
    const AUTHORITY = "mcel-domain-adapter-registry";

    const REQUIRED_METHODS_BY_READINESS_FIELD = Object.freeze({
      adapterExecutable: Object.freeze(["executeIntent", "buildReceipt"]),
      stateMachineReady: Object.freeze(["getState"]),
      actionPlannerReady: Object.freeze(["listIntents", "preflightIntent"]),
      capabilityProviderReady: Object.freeze(["listObjects", "mapEvidence"]),
      recoveryReady: Object.freeze(["classifyFailure", "buildRecoveryOptions"])
    });

    const READINESS_FIELDS = Object.freeze(Object.keys(REQUIRED_METHODS_BY_READINESS_FIELD));
    const adapters = new Map();

    function normalizeId(value) {
      return String(value || "").trim();
    }

    function clonePlain(value) {
      if (value == null || typeof value !== "object") return value;
      if (Array.isArray(value)) return value.map(clonePlain);
      return Object.fromEntries(
        Object.entries(value)
          .filter(([, entry]) => typeof entry !== "function")
          .map(([key, entry]) => [key, clonePlain(entry)])
      );
    }

    function callable(adapter, methodName) {
      return Boolean(adapter && typeof adapter[methodName] === "function");
    }

    function missingMethodsFor(adapter, field) {
      return (REQUIRED_METHODS_BY_READINESS_FIELD[field] || []).filter((methodName) => !callable(adapter, methodName));
    }

    function fieldReady(adapter, field) {
      return missingMethodsFor(adapter, field).length === 0;
    }

    function normalizedStringList(value) {
      if (!Array.isArray(value)) return [];
      return Array.from(new Set(
        value.map((entry) => String(entry || "").trim()).filter(Boolean)
      )).sort();
    }

    function validateRecoveryCoverage(coverage = {}) {
      const requiredFailureClasses = normalizedStringList(
        coverage.requiredFailureClasses
      );
      const coveredFailureClasses = normalizedStringList(
        coverage.coveredFailureClasses
      );
      const unverifiedFailureClasses = normalizedStringList(
        coverage.unverifiedFailureClasses
      );
      const missingFailureClasses = requiredFailureClasses.filter(
        (failureClass) => !coveredFailureClasses.includes(failureClass)
      );
      const unexpectedFailureClasses = coveredFailureClasses.filter(
        (failureClass) => !requiredFailureClasses.includes(failureClass)
      );
      const checks = {
        adapterClaimsReady: coverage.coverageReady === true,
        classificationReady: coverage.classificationReady === true,
        guidanceReady: coverage.guidanceReady === true,
        derivedAudit:
          coverage.verificationMode === "derived-runtime-audit" &&
          coverage.verification?.passed === true,
        requiredClassesDeclared: requiredFailureClasses.length > 0,
        requiredClassesCovered: missingFailureClasses.length === 0,
        noUnexpectedClasses: unexpectedFailureClasses.length === 0,
        noUnverifiedClasses: unverifiedFailureClasses.length === 0
      };
      return {
        passed: Object.values(checks).every(Boolean),
        checks,
        requiredFailureClasses,
        coveredFailureClasses,
        unverifiedFailureClasses,
        missingFailureClasses,
        unexpectedFailureClasses
      };
    }

    function recoveryCoverageFor(adapter) {
      const classifierPresent = Boolean(adapter && fieldReady(adapter, "recoveryReady"));
      if (!classifierPresent) {
        return {
          classifierPresent: false,
          coverageReady: false,
          source: "missing-recovery-classifier",
          coverage: null,
          validation: null,
          error: null
        };
      }
      if (!callable(adapter, "getRecoveryCoverage")) {
        return {
          classifierPresent: true,
          coverageReady: false,
          source: "missing-recovery-coverage-proof",
          coverage: null,
          validation: null,
          error: null
        };
      }
      try {
        const coverage = clonePlain(adapter.getRecoveryCoverage()) || {};
        const validation = validateRecoveryCoverage(coverage);
        return {
          classifierPresent: true,
          coverageReady: validation.passed,
          source: String(coverage.source || "adapter-recovery-coverage"),
          coverage,
          validation,
          error: null
        };
      } catch (error) {
        return {
          classifierPresent: true,
          coverageReady: false,
          source: "recovery-coverage-error",
          coverage: null,
          validation: null,
          error: {
            name: error?.name || "Error",
            message: error?.message || String(error)
          }
        };
      }
    }

    const INTENT_COVERAGE_STATUSES = Object.freeze([
      "executable",
      "preflight-only",
      "declared-only",
      "prohibited"
    ]);

    function validateIntentCoverage(coverage = {}) {
      const requiredIntentIds = normalizedStringList(coverage.requiredIntentIds);
      const entries = Array.isArray(coverage.entries)
        ? coverage.entries.map(clonePlain)
        : [];
      const classifiedIntentIds = normalizedStringList(
        entries.map((entry) => entry?.intentId)
      );
      const missingIntentIds = requiredIntentIds.filter(
        (intentId) => !classifiedIntentIds.includes(intentId)
      );
      const unexpectedIntentIds = classifiedIntentIds.filter(
        (intentId) => !requiredIntentIds.includes(intentId)
      );
      const duplicateIntentIds = classifiedIntentIds.filter(
        (intentId) => entries.filter((entry) => entry?.intentId === intentId).length > 1
      );
      const invalidEntries = entries
        .filter((entry) => (
          !entry ||
          !requiredIntentIds.includes(String(entry.intentId || "")) ||
          !INTENT_COVERAGE_STATUSES.includes(String(entry.status || "")) ||
          typeof entry.label !== "string" ||
          !entry.label.trim() ||
          typeof entry.risk !== "string" ||
          !entry.risk.trim() ||
          typeof entry.executionBinding !== "string" ||
          !entry.executionBinding.trim()
        ))
        .map((entry) => String(entry?.intentId || "unknown"));
      const statusCounts = Object.fromEntries(
        INTENT_COVERAGE_STATUSES.map((status) => [
          status,
          entries.filter((entry) => entry?.status === status).length
        ])
      );
      const incompleteIntentIds = entries
        .filter((entry) => ["declared-only", "preflight-only"].includes(entry?.status))
        .map((entry) => String(entry.intentId))
        .sort();
      const derivedFullApplicationReady = Boolean(
        requiredIntentIds.length > 0 &&
        missingIntentIds.length === 0 &&
        unexpectedIntentIds.length === 0 &&
        duplicateIntentIds.length === 0 &&
        invalidEntries.length === 0 &&
        incompleteIntentIds.length === 0
      );
      const checks = {
        derivedAudit:
          coverage.verificationMode === "derived-intent-coverage-audit" &&
          coverage.verification?.passed === true,
        requiredIntentsDeclared: requiredIntentIds.length > 0,
        allIntentsClassified: missingIntentIds.length === 0,
        noUnexpectedIntents: unexpectedIntentIds.length === 0,
        noDuplicateIntents: duplicateIntentIds.length === 0,
        validEntries: invalidEntries.length === 0,
        scopeDeclared: typeof coverage.semanticRuntimeScope === "string" &&
          Boolean(coverage.semanticRuntimeScope.trim()),
        fullReadinessClaimMatchesDerivation:
          coverage.fullApplicationSemanticReady === derivedFullApplicationReady
      };
      const auditPassed = Object.values(checks).every(Boolean);
      return {
        passed: auditPassed,
        coverageReady: Boolean(auditPassed && derivedFullApplicationReady),
        fullApplicationSemanticReady: Boolean(auditPassed && derivedFullApplicationReady),
        checks,
        requiredIntentIds,
        classifiedIntentIds,
        missingIntentIds,
        unexpectedIntentIds,
        duplicateIntentIds,
        invalidEntries,
        incompleteIntentIds,
        statusCounts
      };
    }

    function intentCoverageFor(adapter) {
      if (!adapter || !callable(adapter, "getIntentCoverage")) {
        return {
          available: false,
          auditReady: false,
          coverageReady: false,
          semanticRuntimeScope: "unclassified",
          coverage: null,
          validation: null,
          error: null
        };
      }
      try {
        const coverage = clonePlain(adapter.getIntentCoverage()) || {};
        const validation = validateIntentCoverage(coverage);
        return {
          available: true,
          auditReady: validation.passed,
          coverageReady: validation.coverageReady,
          semanticRuntimeScope: String(coverage.semanticRuntimeScope || "unclassified"),
          coverage,
          validation,
          error: null
        };
      } catch (error) {
        return {
          available: true,
          auditReady: false,
          coverageReady: false,
          semanticRuntimeScope: "coverage-error",
          coverage: null,
          validation: null,
          error: {
            name: error?.name || "Error",
            message: error?.message || String(error)
          }
        };
      }
    }

    function appIdFor(appOrPlan, maybePlan) {
      if (typeof appOrPlan === "object" && appOrPlan) {
        return normalizeId(appOrPlan.app || appOrPlan.appId || appOrPlan.id);
      }
      if (typeof maybePlan === "object" && maybePlan) {
        return normalizeId(appOrPlan || maybePlan.app || maybePlan.appId || maybePlan.id);
      }
      return normalizeId(appOrPlan);
    }

    function adapterSummary(adapter) {
      if (!adapter) return null;
      return {
        id: normalizeId(adapter.id || adapter.adapterId || adapter.name),
        appId: normalizeId(adapter.appId || adapter.app || adapter.targetAppId),
        version: normalizeId(adapter.version || "unversioned"),
        kind: normalizeId(adapter.kind || adapter.adapterKind || "domain-adapter")
      };
    }

    function registerAdapter(adapter = {}) {
      if (!adapter || typeof adapter !== "object") {
        throw new TypeError("MCEL domain adapter must be an object.");
      }
      const appId = normalizeId(adapter.appId || adapter.app || adapter.targetAppId);
      const id = normalizeId(adapter.id || adapter.adapterId || adapter.name || appId);
      if (!appId) {
        throw new Error("MCEL domain adapter registration requires appId.");
      }
      if (!id) {
        throw new Error("MCEL domain adapter registration requires id.");
      }
      const registered = {
        ...adapter,
        id,
        appId
      };
      adapters.set(appId, registered);
      return evaluateAdapterReadiness(appId);
    }

    function unregisterAdapter(appId) {
      return adapters.delete(normalizeId(appId));
    }

    function clearAdapters() {
      adapters.clear();
    }

    function getAdapter(appId) {
      return adapters.get(normalizeId(appId)) || null;
    }

    function listAdapters() {
      return Array.from(adapters.values()).map(adapterSummary);
    }

    function evaluateAdapterReadiness(appOrPlan, maybePlan = {}) {
      const appId = appIdFor(appOrPlan, maybePlan);
      const adapter = getAdapter(appId);
      const missingMethods = {};
      const readiness = {};

      READINESS_FIELDS.forEach((field) => {
        const missing = missingMethodsFor(adapter, field);
        missingMethods[field] = missing;
        readiness[field] = Boolean(adapter && missing.length === 0);
      });

      const recoveryCoverage = recoveryCoverageFor(adapter);
      readiness.recoveryReady = Boolean(
        recoveryCoverage.classifierPresent &&
        recoveryCoverage.coverageReady
      );

      const missingCoreSemantics = READINESS_FIELDS.filter((field) => !readiness[field]);
      const runtimeCoreReady = Boolean(adapter && missingCoreSemantics.length === 0);
      const intentCoverage = intentCoverageFor(adapter);
      const fullApplicationSemanticReady = Boolean(
        runtimeCoreReady &&
        intentCoverage.coverageReady
      );
      const semanticRuntimeReady = fullApplicationSemanticReady;
      const coverageEntries = intentCoverage.coverage?.entries || [];
      const statusCounts = intentCoverage.validation?.statusCounts || {};
      const incompleteIntentIds =
        intentCoverage.validation?.incompleteIntentIds || [];
      const summary = adapterSummary(adapter);
      const adapterKind = fullApplicationSemanticReady
        ? "executable-semantic-workbench"
        : (
          runtimeCoreReady
            ? "scope-limited-executable-semantic-workbench"
            : (summary?.kind || "missing-domain-adapter")
        );

      return {
        version: REGISTRY_VERSION,
        authority: AUTHORITY,
        source: AUTHORITY,
        appId,
        adapter: summary?.id || "",
        adapterId: summary?.id || "",
        adapterKind,
        adapterVersion: summary?.version || "",
        registryAdapterPresent: Boolean(adapter),
        semanticRuntimeReady,
        runtimeCoreReady,
        fullApplicationSemanticReady,
        semanticRuntimeScope: intentCoverage.semanticRuntimeScope,
        executableIntentCount: Number(statusCounts.executable || 0),
        preflightOnlyIntentCount: Number(statusCounts["preflight-only"] || 0),
        declaredOnlyIntentCount: Number(statusCounts["declared-only"] || 0),
        prohibitedIntentCount: Number(statusCounts.prohibited || 0),
        blockedIntentCount: Number(
          (statusCounts["declared-only"] || 0) +
          (statusCounts.prohibited || 0)
        ),
        totalIntentCount: coverageEntries.length,
        intentCoverageAuditReady: intentCoverage.auditReady,
        intentCoverageReady: intentCoverage.coverageReady,
        intentCoverage: clonePlain(intentCoverage.coverage),
        intentCoverageValidation: clonePlain(intentCoverage.validation),
        intentCoverageError: clonePlain(intentCoverage.error),
        missingApplicationSemantics: clonePlain(incompleteIntentIds),
        adapterExecutable: readiness.adapterExecutable,
        stateMachineReady: readiness.stateMachineReady,
        actionPlannerReady: readiness.actionPlannerReady,
        capabilityProviderReady: readiness.capabilityProviderReady,
        recoveryReady: readiness.recoveryReady,
        recoveryClassifierPresent: recoveryCoverage.classifierPresent,
        recoveryCoverageReady: recoveryCoverage.coverageReady,
        recoveryCoverageSource: recoveryCoverage.source,
        recoveryCoverage: clonePlain(recoveryCoverage.coverage),
        recoveryCoverageValidation: clonePlain(recoveryCoverage.validation),
        recoveryCoverageError: clonePlain(recoveryCoverage.error),
        missingSemantics: missingCoreSemantics,
        requiredMethods: clonePlain(REQUIRED_METHODS_BY_READINESS_FIELD),
        missingMethods,
        claim: fullApplicationSemanticReady
          ? "The registered MCEL domain adapter proves runtime-core and full intent-level semantic coverage."
          : (
            runtimeCoreReady
              ? `Runtime core is ready, but application semantic coverage is partial (${intentCoverage.semanticRuntimeScope}).`
              : "No registered MCEL domain adapter currently proves executable semantic runtime-core readiness."
          )
      };
    }

    function semanticReadinessForPlan(plan = {}) {
      return evaluateAdapterReadiness(plan.app || plan.appId || plan.id || "", plan);
    }

    function safeCall(adapter, methodName, ...args) {
      if (!callable(adapter, methodName)) {
        return {available: false, value: null, error: null};
      }
      try {
        return {available: true, value: adapter[methodName](...args), error: null};
      } catch (error) {
        return {
          available: true,
          value: null,
          error: {
            name: error?.name || "Error",
            message: error?.message || String(error)
          }
        };
      }
    }

    function snapshotSemanticRuntime(appOrPlan, maybePlan = {}) {
      const appId = appIdFor(appOrPlan, maybePlan);
      const plan = typeof appOrPlan === "object" ? appOrPlan : maybePlan;
      const adapter = getAdapter(appId);
      const readiness = evaluateAdapterReadiness(appId, plan);
      const stateResult = safeCall(adapter, "getState", {plan});
      const intentsResult = safeCall(adapter, "listIntents", stateResult.value, {plan});

      return {
        ...readiness,
        snapshotVersion: `${REGISTRY_VERSION}-snapshot-v1`,
        stateSnapshotAvailable: Boolean(stateResult.available && !stateResult.error),
        intentListAvailable: Boolean(intentsResult.available && !intentsResult.error),
        state: clonePlain(stateResult.value),
        intents: clonePlain(intentsResult.value),
        errors: [stateResult.error, intentsResult.error].filter(Boolean)
      };
    }

    const api = Object.freeze({
      REGISTRY_VERSION,
      AUTHORITY,
      REQUIRED_METHODS_BY_READINESS_FIELD,
      READINESS_FIELDS,
      INTENT_COVERAGE_STATUSES,
      validateRecoveryCoverage,
      validateIntentCoverage,
      registerAdapter,
      unregisterAdapter,
      clearAdapters,
      getAdapter,
      listAdapters,
      evaluateAdapterReadiness,
      semanticReadinessForPlan,
      snapshotSemanticRuntime
    });

    global.McelDomainAdapterRegistry = api;

    if (typeof module !== "undefined" && module.exports) {
      module.exports = api;
    }
  })(typeof window !== "undefined" ? window : globalThis);
})();
