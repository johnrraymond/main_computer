var McelAppTruthGate = (() => {
  "use strict";

  const CONTRACT_VERSION = "mcel.app-truth-gate.v1";
  const SNAPSHOT_SCHEMA = "mcel-app-truth-snapshot-v1";
  const DEFAULT_MAX_EVIDENCE_AGE_MS = 7 * 24 * 60 * 60 * 1000;

  const FINDING_SEVERITY_ORDER = Object.freeze({
    critical: 0,
    error: 1,
    warning: 2,
    info: 3
  });

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
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

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }

  function uniqueStrings(values) {
    return [...new Set((Array.isArray(values) ? values : [])
      .map((value) => safeString(value))
      .filter(Boolean))].sort();
  }

  function asObject(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function finding(code, severity, message, detail = {}, blocking = false) {
    return {
      code: safeString(code),
      severity: safeString(severity || "warning"),
      blocking: blocking === true,
      message: safeString(message),
      detail: clonePlain(detail)
    };
  }

  function sortFindings(findings) {
    return [...findings].sort((left, right) => {
      const severityDelta =
        (FINDING_SEVERITY_ORDER[left.severity] ?? 99) -
        (FINDING_SEVERITY_ORDER[right.severity] ?? 99);
      if (severityDelta) return severityDelta;
      return left.code.localeCompare(right.code);
    });
  }

  function globalApi(name) {
    if (typeof window !== "undefined" && window[name]) return window[name];
    if (typeof globalThis !== "undefined" && globalThis[name]) return globalThis[name];
    return null;
  }

  function resolveApi(options, optionName, globalName) {
    if (options && options[optionName]) return options[optionName];
    return globalApi(globalName);
  }

  function safeCall(fn, fallback = null) {
    try {
      return typeof fn === "function" ? fn() : fallback;
    } catch (error) {
      return {
        __truthGateError: {
          name: safeString(error?.name || "Error"),
          message: safeString(error?.message || error)
        }
      };
    }
  }

  function requirementsState(appId, requirementsRegistry) {
    const summary = requirementsRegistry && typeof requirementsRegistry.getSummary === "function"
      ? safeCall(() => requirementsRegistry.getSummary(), {})
      : {};
    const contract = requirementsRegistry && typeof requirementsRegistry.getAppContract === "function"
      ? safeCall(() => requirementsRegistry.getAppContract(appId), null)
      : null;
    const contractObject = contract && !contract.__truthGateError ? contract : null;
    const blockCounts = asObject(contractObject?.block_type_counts);
    const schemaValid = Boolean(
      requirementsRegistry &&
      requirementsRegistry.strictSchemaReady === true &&
      summary?.valid !== false &&
      Number(summary?.error_count || 0) === 0
    );

    return {
      present: Boolean(contractObject),
      schemaValid,
      contractComplete: contractObject?.contract_complete === true,
      appId,
      title: safeString(contractObject?.title || contractObject?.app || appId),
      currentRuntimeStatus: safeString(contractObject?.current_runtime_status),
      targetRuntimeStatus: safeString(contractObject?.target_runtime_status),
      intentCount: Number(contractObject?.intent_count || 0),
      mutationIntentCount: Number(contractObject?.mutation_intent_count || 0),
      prohibitedIntentCount: Number(contractObject?.prohibited_intent_count || 0),
      acceptanceContractCount: Number(blockCounts["mcel-acceptance"] || 0),
      testBindingCount: Number(blockCounts["mcel-test-binding"] || 0),
      runtimeCheckCount: Number(contractObject?.runtime_check_count || 0),
      source: clonePlain(contractObject?.source || null),
      registryVersion: safeString(requirementsRegistry?.REGISTRY_VERSION || summary?.registry_version),
      error: clonePlain(contract?.__truthGateError || null)
    };
  }

  function adapterState(appId, domainAdapterRegistry) {
    const readiness = domainAdapterRegistry && typeof domainAdapterRegistry.evaluateAdapterReadiness === "function"
      ? safeCall(() => domainAdapterRegistry.evaluateAdapterReadiness(appId), null)
      : null;
    const state = readiness && !readiness.__truthGateError ? readiness : {};

    return {
      registered: state.registryAdapterPresent === true || Boolean(state.adapter || state.adapterId),
      appId,
      adapterId: safeString(state.adapterId || state.adapter),
      adapterKind: safeString(state.adapterKind || (state.registryAdapterPresent ? "registered-domain-adapter" : "missing-domain-adapter")),
      adapterVersion: safeString(state.adapterVersion),
      runtimeCoreReady: state.runtimeCoreReady === true,
      intentCoverageReady: state.intentCoverageReady === true,
      intentCoverageAuditReady: state.intentCoverageAuditReady === true,
      fullApplicationSemanticReady: state.fullApplicationSemanticReady === true,
      semanticRuntimeReady: state.semanticRuntimeReady === true,
      semanticRuntimeScope: safeString(state.semanticRuntimeScope || "unclassified"),
      executableIntentCount: Number(state.executableIntentCount || 0),
      preflightOnlyIntentCount: Number(state.preflightOnlyIntentCount || 0),
      declaredOnlyIntentCount: Number(state.declaredOnlyIntentCount || 0),
      prohibitedIntentCount: Number(state.prohibitedIntentCount || 0),
      blockedIntentCount: Number(state.blockedIntentCount || 0),
      totalIntentCount: Number(state.totalIntentCount || 0),
      excludedPlannedIntentIds: uniqueStrings(state.intentCoverage?.excludedPlannedIntentIds),
      incompleteIntentIds: uniqueStrings(
        state.intentCoverageValidation?.incompleteIntentIds ||
        state.missingApplicationSemantics
      ),
      recoveryReady: state.recoveryReady === true,
      recoveryCoverageReady: state.recoveryCoverageReady === true,
      missingSemantics: uniqueStrings(state.missingSemantics),
      missingApplicationSemantics: uniqueStrings(state.missingApplicationSemantics),
      registryVersion: safeString(domainAdapterRegistry?.REGISTRY_VERSION || state.version),
      authority: safeString(state.authority || domainAdapterRegistry?.AUTHORITY),
      error: clonePlain(readiness?.__truthGateError || null)
    };
  }

  function surfaceState(appId, appSurfaceRegistry) {
    const policy = appSurfaceRegistry && typeof appSurfaceRegistry.getAppPolicy === "function"
      ? safeCall(() => appSurfaceRegistry.getAppPolicy(appId), null)
      : null;
    const state = policy && !policy.__truthGateError ? policy : {};
    const registered = Boolean(
      state.appId &&
      state.state !== "unregistered" &&
      (state.conformanceRequired === true || state.state === "legacy" || state.state === "surface-aware")
    );

    return {
      registered,
      appId,
      label: safeString(state.label || appId),
      registryState: safeString(state.state || "unregistered"),
      conformanceRequired: state.conformanceRequired === true,
      maturity: safeString(state.maturity || (registered ? state.state : "unregistered")),
      surfaceId: safeString(state.surfaceId),
      contractId: safeString(state.contractId),
      requiredLayerIds: uniqueStrings(state.requiredLayerIds),
      notes: safeString(state.notes),
      registryVersion: safeString(appSurfaceRegistry?.registryVersion),
      error: clonePlain(policy?.__truthGateError || null)
    };
  }

  function evidenceAppId(entry) {
    return safeString(
      entry?.appId ||
      entry?.app ||
      entry?.appSurfacePolicy?.appId ||
      entry?.appSurfaceConformance?.appId ||
      entry?.widgetPayload?.appId ||
      entry?.diagnosis?.appId
    );
  }

  function evidenceEntries(input) {
    if (!input) return [];
    if (Array.isArray(input)) return input;
    if (Array.isArray(input.results)) {
      return input.results.map((entry) => ({
        reportSchema: safeString(entry?.reportSchema || input.schema),
        reportVersion: safeString(entry?.reportVersion || input.version),
        generatedAt: safeString(entry?.generatedAt || input.generatedAt),
        ...entry
      }));
    }
    if (Array.isArray(input.scenarioResults)) {
      return input.scenarioResults.map((entry) => ({
        reportSchema: safeString(entry?.reportSchema || input.schema),
        reportVersion: safeString(entry?.reportVersion || input.version),
        generatedAt: safeString(entry?.generatedAt || input.generatedAt),
        ...entry
      }));
    }
    if (evidenceAppId(input)) return [input];
    return Object.entries(asObject(input))
      .map(([appId, value]) => {
        if (value === true || value === false) return {appId, passed: value};
        if (!value || typeof value !== "object") return null;
        return evidenceAppId(value) ? value : {...value, appId};
      })
      .filter(Boolean);
  }

  function evidenceForApp(input, appId) {
    const entries = evidenceEntries(input);
    const matching = entries.filter((entry) => evidenceAppId(entry) === appId);
    if (!matching.length) return null;
    return matching[matching.length - 1];
  }

  function conformanceFromEvidence(entry) {
    if (!entry) return null;
    return (
      entry.appSurfaceConformance ||
      entry.appSurfacePolicyScope ||
      entry.widgetPayload?.appSurfaceConformance ||
      entry.diagnosis?.appSurfaceConformance ||
      entry.diagnosis?.summary?.appSurfaceConformance ||
      null
    );
  }

  function timestampFromEvidence(entry) {
    return safeString(
      entry?.finishedAt ||
      entry?.timestamp ||
      entry?.widgetPayload?.timestamp ||
      entry?.diagnosis?.timestamp ||
      entry?.generatedAt ||
      entry?.startedAt
    );
  }

  function timestampMillis(value) {
    const text = safeString(value);
    if (!text) return null;
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function layerStatusesFromConformance(conformance) {
    const statuses = {};
    const direct = asObject(conformance?.requiredLayerStatuses);
    Object.entries(direct).forEach(([id, status]) => {
      statuses[safeString(id)] = safeString(status);
    });
    (Array.isArray(conformance?.layers) ? conformance.layers : []).forEach((layer) => {
      const id = safeString(layer?.id);
      if (id) statuses[id] = safeString(layer?.status);
    });
    return statuses;
  }

  function normalizeRuntimeEvidence(appId, input, surface, nowMs, maxEvidenceAgeMs) {
    const entry = evidenceForApp(input, appId);
    if (!entry) {
      return {
        present: false,
        appId,
        sourceSchema: "",
        status: "missing",
        timestamp: "",
        ageMs: null,
        freshness: "missing",
        fresh: false,
        diagnosisCompleted: false,
        policyPassed: false,
        failedLayerIds: [],
        unavailableLayerIds: [],
        requiredLayerStatuses: {},
        claimedSemanticRuntimeReady: false,
        scenarioId: "",
        route: ""
      };
    }

    const conformance = conformanceFromEvidence(entry) || {};
    const timestamp = timestampFromEvidence(entry);
    const timestampMs = timestampMillis(timestamp);
    const ageMs = timestampMs == null ? null : Math.max(0, nowMs - timestampMs);
    const fresh = ageMs != null && ageMs <= maxEvidenceAgeMs;
    const status = safeString(
      entry.status ||
      entry.verdict ||
      entry.appSurfacePolicyScope?.status ||
      conformance.status ||
      entry.widgetPayload?.verdict
    ).toLowerCase();
    const failedLayerIds = uniqueStrings(
      entry.appSurfacePolicyScope?.failedLayerIds ||
      conformance.policyFailedLayerIds ||
      conformance.failedLayerIds
    );
    const unavailableLayerIds = uniqueStrings(
      entry.appSurfacePolicyScope?.unavailableLayerIds ||
      conformance.policyUnavailableLayerIds ||
      conformance.unavailableLayerIds
    );
    const requiredLayerStatuses = layerStatusesFromConformance(
      entry.appSurfacePolicyScope || conformance
    );
    const diagnosisThrew = Boolean(
      entry.diagnosisThrew ||
      entry.failures?.some?.((item) =>
        safeString(item?.code || item).includes("diagnosis-threw")
      ) ||
      conformance.layers?.some?.((layer) =>
        safeString(layer?.id) === "diagnostic-no-throw" &&
        safeString(layer?.status) === "fail"
      )
    );
    const hasConformance = Boolean(
      conformance &&
      (safeString(conformance.status) || Array.isArray(conformance.layers) || Object.keys(requiredLayerStatuses).length)
    );
    const requiredStatusesPass = surface.requiredLayerIds.every(
      (layerId) => requiredLayerStatuses[layerId] === "pass" ||
        (!Object.keys(requiredLayerStatuses).length && status === "pass")
    );
    const policyPassed = status === "pass" &&
      failedLayerIds.length === 0 &&
      unavailableLayerIds.length === 0 &&
      (!surface.conformanceRequired || (hasConformance && requiredStatusesPass));

    return {
      present: true,
      appId,
      sourceSchema: safeString(
        entry.schema ||
        entry.reportSchema ||
        entry.widgetPayload?.schema ||
        entry.diagnosis?.schema
      ),
      status: status || "unknown",
      timestamp,
      ageMs,
      freshness: timestampMs == null ? "unknown" : (fresh ? "fresh" : "stale"),
      fresh,
      diagnosisCompleted: !diagnosisThrew && (
        status === "pass" ||
        status === "fail" ||
        Boolean(entry.diagnosis || entry.widgetPayload || hasConformance)
      ),
      policyPassed,
      failedLayerIds,
      unavailableLayerIds,
      requiredLayerStatuses,
      claimedSemanticRuntimeReady: Boolean(
        entry.claimedSemanticRuntimeReady === true ||
        entry.semanticRuntimeReady === true ||
        entry.fullApplicationSemanticReady === true ||
        entry.claims?.semanticRuntimeProven === true
      ),
      scenarioId: safeString(entry.scenarioId || entry.id),
      route: safeString(entry.route || entry.url)
    };
  }

  function normalizeAcceptanceEvidence(appId, input) {
    if (input === true || input === false) {
      return {
        present: true,
        appId,
        passed: input === true,
        status: input === true ? "pass" : "fail",
        testCount: 0,
        timestamp: "",
        sourceSchema: ""
      };
    }
    const entry = evidenceForApp(input, appId);
    if (!entry) {
      return {
        present: false,
        appId,
        passed: false,
        status: "missing",
        testCount: 0,
        timestamp: "",
        sourceSchema: ""
      };
    }
    const status = safeString(entry.status || entry.verdict || (entry.passed === true ? "pass" : "")).toLowerCase();
    const passed = entry.passed === true || status === "pass";
    return {
      present: true,
      appId,
      passed,
      status: status || (passed ? "pass" : "unknown"),
      testCount: Number(entry.testCount || entry.count || entry.passedCount || 0),
      timestamp: timestampFromEvidence(entry),
      sourceSchema: safeString(entry.schema || entry.reportSchema)
    };
  }

  function buildFindings(requirements, adapter, surface, runtime, acceptance) {
    const findings = [];

    if (!requirements.present) {
      findings.push(finding(
        "requirements-contract-missing",
        "warning",
        "No MCEL requirements contract is registered for this app.",
        {appId: requirements.appId}
      ));
    } else {
      if (!requirements.schemaValid) {
        findings.push(finding(
          "requirements-schema-invalid",
          "error",
          "The MCEL requirements registry is not strict-schema clean.",
          {appId: requirements.appId},
          true
        ));
      }
      if (!requirements.contractComplete) {
        findings.push(finding(
          "requirements-contract-incomplete",
          "error",
          "The MCEL requirements contract is present but incomplete.",
          {appId: requirements.appId},
          true
        ));
      }
    }

    if (!adapter.registered) {
      findings.push(finding(
        "missing-domain-adapter",
        "warning",
        "No registered MCEL domain adapter proves executable semantic behavior for this app.",
        {appId: adapter.appId}
      ));
    } else if (
      requirements.intentCount > 0 &&
      (!adapter.intentCoverageReady ||
        !adapter.fullApplicationSemanticReady ||
        adapter.executableIntentCount === 0)
    ) {
      findings.push(finding(
        "required-intent-not-executable",
        "warning",
        "One or more intents required by the adapter's current semantic scope are not proven complete.",
        {
          appId: adapter.appId,
          declaredContractIntentCount: requirements.intentCount,
          currentScopeIntentCount: adapter.totalIntentCount,
          executableIntentCount: adapter.executableIntentCount,
          prohibitedIntentCount: adapter.prohibitedIntentCount,
          declaredOnlyIntentCount: adapter.declaredOnlyIntentCount,
          blockedIntentCount: adapter.blockedIntentCount,
          excludedPlannedIntentIds: adapter.excludedPlannedIntentIds,
          incompleteIntentIds: adapter.incompleteIntentIds,
          semanticRuntimeScope: adapter.semanticRuntimeScope,
          missingApplicationSemantics: adapter.missingApplicationSemantics
        }
      ));
    }

    if (!surface.registered || !surface.conformanceRequired) {
      findings.push(finding(
        "app-not-enrolled",
        "warning",
        "The app is not enrolled in required MCEL app-surface conformance.",
        {
          appId: surface.appId,
          registryState: surface.registryState,
          maturity: surface.maturity
        }
      ));
    }

    if (surface.conformanceRequired) {
      if (!runtime.present) {
        findings.push(finding(
          "runtime-evidence-missing",
          "warning",
          "No runtime conformance evidence was supplied for this required app surface.",
          {appId: runtime.appId, requiredLayerIds: surface.requiredLayerIds}
        ));
      } else {
        if (runtime.freshness === "unknown") {
          findings.push(finding(
            "runtime-evidence-timestamp-missing",
            "warning",
            "Runtime evidence has no parseable timestamp and cannot be treated as fresh.",
            {appId: runtime.appId}
          ));
        } else if (!runtime.fresh) {
          findings.push(finding(
            "runtime-evidence-stale",
            "warning",
            "Runtime conformance evidence is older than the configured freshness window.",
            {appId: runtime.appId, timestamp: runtime.timestamp, ageMs: runtime.ageMs}
          ));
        }

        if (!runtime.diagnosisCompleted) {
          findings.push(finding(
            "runtime-diagnosis-incomplete",
            "error",
            "Runtime evidence did not prove that diagnosis completed without throwing.",
            {appId: runtime.appId},
            true
          ));
        }

        if (!runtime.policyPassed) {
          findings.push(finding(
            "surface-policy-failed",
            "error",
            "Runtime evidence does not pass the app-surface registry policy.",
            {
              appId: runtime.appId,
              status: runtime.status,
              failedLayerIds: runtime.failedLayerIds,
              unavailableLayerIds: runtime.unavailableLayerIds,
              requiredLayerStatuses: runtime.requiredLayerStatuses
            },
            true
          ));
        }
      }
    }

    if (requirements.acceptanceContractCount > 0) {
      if (!acceptance.present) {
        findings.push(finding(
          "acceptance-test-missing",
          "warning",
          "Requirements declare acceptance contracts but no acceptance-test evidence was supplied.",
          {
            appId: acceptance.appId,
            declaredAcceptanceCount: requirements.acceptanceContractCount
          }
        ));
      } else if (!acceptance.passed) {
        findings.push(finding(
          "acceptance-test-failed",
          "error",
          "Supplied acceptance-test evidence did not pass.",
          {
            appId: acceptance.appId,
            status: acceptance.status,
            testCount: acceptance.testCount
          },
          true
        ));
      }
    }

    if (runtime.claimedSemanticRuntimeReady && !adapter.fullApplicationSemanticReady) {
      findings.push(finding(
        "semantic-readiness-overclaimed",
        "error",
        "Runtime evidence claims semantic readiness that the domain-adapter registry does not prove.",
        {
          appId: adapter.appId,
          runtimeCoreReady: adapter.runtimeCoreReady,
          fullApplicationSemanticReady: adapter.fullApplicationSemanticReady,
          semanticRuntimeScope: adapter.semanticRuntimeScope
        },
        true
      ));
    }

    return sortFindings(findings);
  }

  function deriveClaims(requirements, adapter, surface, runtime, acceptance) {
    const specified =
      requirements.present &&
      requirements.schemaValid &&
      requirements.contractComplete;
    const implementationPresent = adapter.registered || surface.registered;
    const runtimeSurfaceProven =
      surface.conformanceRequired &&
      runtime.present &&
      runtime.fresh &&
      runtime.diagnosisCompleted &&
      runtime.policyPassed;
    const acceptanceRequired = requirements.acceptanceContractCount > 0;
    const acceptanceProven = !acceptanceRequired || (acceptance.present && acceptance.passed);
    const semanticRuntimeProven =
      specified &&
      adapter.fullApplicationSemanticReady &&
      runtimeSurfaceProven &&
      acceptanceProven;

    return {
      specified,
      implementationPresent,
      partiallyImplemented: implementationPresent && !adapter.fullApplicationSemanticReady,
      runtimeSurfaceProven,
      acceptanceProven,
      semanticRuntimeProven,
      verificationComplete: runtimeSurfaceProven && acceptanceProven
    };
  }

  function overallStatus(claims, findings) {
    if (findings.some((item) => item.blocking)) return "blocked";
    if (claims.semanticRuntimeProven) return "semantic-runtime-proven";
    if (claims.verificationComplete) return "runtime-proven";
    if (claims.implementationPresent && (
      findings.some((item) => [
        "runtime-evidence-missing",
        "runtime-evidence-stale",
        "runtime-evidence-timestamp-missing",
        "acceptance-test-missing"
      ].includes(item.code))
    )) return "verification-incomplete";
    if (claims.implementationPresent) return "partially-implemented";
    if (claims.specified) return "specified";
    return "untracked";
  }

  function evaluateAppTruth(appId, options = {}) {
    const id = safeString(appId);
    if (!id) throw new Error("MCEL app truth evaluation requires a non-empty appId.");

    const requirementsRegistry = resolveApi(options, "requirementsRegistry", "McelRequirementsRegistry");
    const domainAdapterRegistry = resolveApi(options, "domainAdapterRegistry", "McelDomainAdapterRegistry");
    const appSurfaceRegistry = resolveApi(options, "appSurfaceRegistry", "McelAppSurfaceRegistry");
    const nowValue = options.now ?? Date.now();
    const nowMs = typeof nowValue === "number" ? nowValue : Date.parse(nowValue);
    if (!Number.isFinite(nowMs)) throw new Error("MCEL app truth evaluation requires a valid 'now' value.");
    const maxEvidenceAgeMs = Number.isFinite(Number(options.maxEvidenceAgeMs))
      ? Math.max(0, Number(options.maxEvidenceAgeMs))
      : DEFAULT_MAX_EVIDENCE_AGE_MS;

    const requirements = requirementsState(id, requirementsRegistry);
    const adapter = adapterState(id, domainAdapterRegistry);
    const surface = surfaceState(id, appSurfaceRegistry);
    const runtimeEvidence = normalizeRuntimeEvidence(
      id,
      options.runtimeEvidence,
      surface,
      nowMs,
      maxEvidenceAgeMs
    );
    const acceptanceEvidence = normalizeAcceptanceEvidence(id, options.acceptanceEvidence);
    const findings = buildFindings(
      requirements,
      adapter,
      surface,
      runtimeEvidence,
      acceptanceEvidence
    );
    const claims = deriveClaims(
      requirements,
      adapter,
      surface,
      runtimeEvidence,
      acceptanceEvidence
    );

    return deepFreeze({
      schema: SNAPSHOT_SCHEMA,
      contractVersion: CONTRACT_VERSION,
      appId: id,
      generatedAt: new Date(nowMs).toISOString(),
      overallStatus: overallStatus(claims, findings),
      requirements,
      adapter,
      surface,
      evidence: {
        runtime: runtimeEvidence,
        acceptance: acceptanceEvidence
      },
      claims,
      findings,
      findingCodes: findings.map((item) => item.code)
    });
  }

  function appIdsFromRuntimeEvidence(input) {
    return uniqueStrings(evidenceEntries(input).map(evidenceAppId));
  }

  function listKnownAppIds(options = {}) {
    const requirementsRegistry = resolveApi(options, "requirementsRegistry", "McelRequirementsRegistry");
    const domainAdapterRegistry = resolveApi(options, "domainAdapterRegistry", "McelDomainAdapterRegistry");
    const appSurfaceRegistry = resolveApi(options, "appSurfaceRegistry", "McelAppSurfaceRegistry");
    const ids = [];

    if (requirementsRegistry && typeof requirementsRegistry.listAppContracts === "function") {
      const contracts = safeCall(() => requirementsRegistry.listAppContracts(), []);
      if (Array.isArray(contracts)) ids.push(...contracts.map((item) => item?.app || item?.id));
    }
    if (domainAdapterRegistry && typeof domainAdapterRegistry.listAdapters === "function") {
      const adapters = safeCall(() => domainAdapterRegistry.listAdapters(), []);
      if (Array.isArray(adapters)) ids.push(...adapters.map((item) => item?.appId || item?.app || item?.id));
    }
    if (appSurfaceRegistry && typeof appSurfaceRegistry.listPolicies === "function") {
      const policies = safeCall(() => appSurfaceRegistry.listPolicies(), []);
      if (Array.isArray(policies)) ids.push(...policies.map((item) => item?.appId));
    }
    ids.push(...appIdsFromRuntimeEvidence(options.runtimeEvidence));
    ids.push(...appIdsFromRuntimeEvidence(options.acceptanceEvidence));
    ids.push(...(Array.isArray(options.appIds) ? options.appIds : []));

    return uniqueStrings(ids);
  }

  function buildTruthSnapshot(options = {}) {
    const appIds = listKnownAppIds(options);
    const apps = appIds.map((appId) => evaluateAppTruth(appId, options));
    const statusCounts = {};
    const findingCounts = {};
    apps.forEach((app) => {
      statusCounts[app.overallStatus] = (statusCounts[app.overallStatus] || 0) + 1;
      app.findingCodes.forEach((code) => {
        findingCounts[code] = (findingCounts[code] || 0) + 1;
      });
    });

    const generatedAt = apps[0]?.generatedAt || new Date(
      typeof options.now === "number" ? options.now : (Date.parse(options.now) || Date.now())
    ).toISOString();

    return deepFreeze({
      schema: SNAPSHOT_SCHEMA,
      contractVersion: CONTRACT_VERSION,
      generatedAt,
      appCount: apps.length,
      appIds,
      statusCounts,
      findingCounts,
      apps
    });
  }

  return deepFreeze({
    CONTRACT_VERSION,
    SNAPSHOT_SCHEMA,
    DEFAULT_MAX_EVIDENCE_AGE_MS,
    evaluateAppTruth,
    buildTruthSnapshot,
    listKnownAppIds
  });
})();

if (typeof window !== "undefined") {
  window.McelAppTruthGate = McelAppTruthGate;
  window.MCEL = Object.assign({}, window.MCEL || {}, {appTruthGate: McelAppTruthGate});
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelAppTruthGate;
}
