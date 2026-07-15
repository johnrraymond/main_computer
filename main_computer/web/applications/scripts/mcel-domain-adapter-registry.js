(() => {
  (function createMcelDomainAdapterRegistry(global) {
    if (!global) return;

    const REGISTRY_VERSION = "mcel-domain-adapter-registry-v1";
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

      const missingSemantics = READINESS_FIELDS.filter((field) => !readiness[field]);
      const semanticRuntimeReady = Boolean(adapter && missingSemantics.length === 0);
      const summary = adapterSummary(adapter);

      return {
        version: REGISTRY_VERSION,
        authority: AUTHORITY,
        source: AUTHORITY,
        appId,
        adapter: summary?.id || "",
        adapterId: summary?.id || "",
        adapterKind: semanticRuntimeReady ? "executable-semantic-workbench" : (summary?.kind || "missing-domain-adapter"),
        adapterVersion: summary?.version || "",
        registryAdapterPresent: Boolean(adapter),
        semanticRuntimeReady,
        adapterExecutable: readiness.adapterExecutable,
        stateMachineReady: readiness.stateMachineReady,
        actionPlannerReady: readiness.actionPlannerReady,
        capabilityProviderReady: readiness.capabilityProviderReady,
        recoveryReady: readiness.recoveryReady,
        missingSemantics,
        requiredMethods: clonePlain(REQUIRED_METHODS_BY_READINESS_FIELD),
        missingMethods,
        claim: semanticRuntimeReady
          ? "A registered MCEL domain adapter implements the executable semantic runtime interface."
          : "No registered MCEL domain adapter currently proves executable semantic readiness."
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
