var McelAppSurfaceConformance = (() => {
  "use strict";

  const contractVersion = "mcel.app-surface-conformance.v1";

  const BASELINE_LAYERS = Object.freeze([
    "semantic-surface",
    "layout-grammar",
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw"
  ]);

  const extractorApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

  const registryApi = (() => {
    if (typeof McelAppSurfaceRegistry !== "undefined") return McelAppSurfaceRegistry;
    if (typeof window !== "undefined" && window.McelAppSurfaceRegistry) return window.McelAppSurfaceRegistry;
    if (typeof window !== "undefined" && window.MCEL?.appSurfaceRegistry) return window.MCEL.appSurfaceRegistry;
    return null;
  })();

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function freezeArray(items) {
    return Object.freeze([...(items || [])]);
  }

  function diagnostic(code, severity, finding, detail) {
    return Object.freeze({
      code,
      severity,
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function compactDiagnostic(item) {
    return diagnostic(
      safeString(item?.code || "app-surface-conformance-diagnostic"),
      safeString(item?.severity || "warning"),
      safeString(item?.finding || "MCEL app surface conformance diagnostic."),
      item?.detail || {}
    );
  }

  function layer(id, status, finding, detail) {
    return Object.freeze({
      id,
      status,
      valid: status === "pass",
      finding,
      detail: Object.freeze(detail || {})
    });
  }

  function statusFromValidation(valid, unavailable) {
    if (unavailable) return "unavailable";
    return valid ? "pass" : "fail";
  }

  function hasCriticalFailure(layers) {
    return layers.some((item) => item.status === "fail");
  }

  function severityCounts(findings) {
    return freezeArray(findings).reduce(
      (counts, finding) => {
        if (finding?.severity === "critical" || finding?.severity === "error") counts.errors += 1;
        else if (finding?.severity === "warning") counts.warnings += 1;
        else counts.info += 1;
        return counts;
      },
      {errors: 0, warnings: 0, info: 0}
    );
  }

  function uniqueStrings(values) {
    return freezeArray([...(new Set((values || []).map((value) => safeString(value).trim()).filter(Boolean)))]);
  }

  function fallbackPolicy(appId) {
    const safeAppId = safeString(appId);
    return Object.freeze({
      appId: safeAppId,
      label: safeAppId,
      state: "unregistered",
      conformanceRequired: false,
      maturity: "unregistered",
      surfaceId: "",
      contractId: "",
      requiredLayerIds: freezeArray([]),
      notes: "No MCEL app-surface registry policy was available for this app."
    });
  }

  function normalizePolicy(policy, appId = "") {
    const input = policy && typeof policy === "object" ? policy : fallbackPolicy(appId);
    const required = !!input.conformanceRequired;
    return Object.freeze({
      appId: safeString(input.appId || appId),
      label: safeString(input.label || input.appId || appId),
      state: safeString(input.state || (required ? "surface-aware" : "legacy")),
      conformanceRequired: required,
      maturity: safeString(input.maturity || (required ? "runtime-baseline" : input.state || "legacy")),
      surfaceId: safeString(input.surfaceId || ""),
      contractId: safeString(input.contractId || ""),
      requiredLayerIds: uniqueStrings(input.requiredLayerIds || (required ? BASELINE_LAYERS : [])),
      notes: safeString(input.notes || "")
    });
  }

  function registryPolicyFor(appId, options = {}) {
    if (options.registryPolicy) return normalizePolicy(options.registryPolicy, appId);
    if (registryApi && typeof registryApi.getAppPolicy === "function") {
      try {
        return normalizePolicy(registryApi.getAppPolicy(appId), appId);
      } catch {}
    }
    return fallbackPolicy(appId);
  }

  function surfaceBundleDiagnostics(bundle) {
    const diagnostics = [];
    if (Array.isArray(bundle?.diagnostics)) diagnostics.push(...bundle.diagnostics);
    if (Array.isArray(bundle?.validation?.surface?.diagnostics)) diagnostics.push(...bundle.validation.surface.diagnostics);
    if (Array.isArray(bundle?.validation?.layout?.diagnostics)) diagnostics.push(...bundle.validation.layout.diagnostics);
    return freezeArray(diagnostics.map(compactDiagnostic));
  }

  function evaluateSurfaceBundle(surfaceBundle, options = {}) {
    const bundle = surfaceBundle || null;
    const diagnostics = [];
    const missing = !bundle;
    const surfaceValid = !missing && !!(bundle.valid && bundle.surfaceIR && bundle.validation?.surface?.valid);
    const layoutValid = !missing && !!(bundle.valid && bundle.layoutGrammar && bundle.validation?.layout?.valid);

    if (missing) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-surface-bundle-unavailable",
        "info",
        "No extracted MCEL surface bundle was supplied for static semantic/layout conformance.",
        {appId: options.appId || ""}
      ));
    } else {
      diagnostics.push(...surfaceBundleDiagnostics(bundle));
    }

    const layers = [
      layer(
        "semantic-surface",
        statusFromValidation(surfaceValid, missing),
        missing
          ? "Static MCEL semantic extraction was not available for this report."
          : surfaceValid
            ? "MCEL semantic surface extracted and validated."
            : "MCEL semantic surface extraction failed.",
        {
          surfaceId: bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
          nodeCount: bundle?.surfaceIR?.graph?.nodes?.length || 0,
          edgeCount: bundle?.surfaceIR?.graph?.edges?.length || 0,
          regionCount: bundle?.surfaceIR?.graph?.regions?.length || 0,
          controlCount: bundle?.surfaceIR?.graph?.controls?.length || 0
        }
      ),
      layer(
        "layout-grammar",
        statusFromValidation(layoutValid, missing),
        missing
          ? "Static MCEL layout grammar extraction was not available for this report."
          : layoutValid
            ? "MCEL shared layout grammar extracted and validated."
            : "MCEL shared layout grammar extraction failed.",
        {
          surfaceId: bundle?.layoutGrammar?.surfaceId || bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
          regionCount: bundle?.layoutGrammar?.regions?.length || 0,
          nodeCount: bundle?.layoutGrammar?.nodes?.length || 0,
          routeCount: bundle?.layoutGrammar?.routes?.length || 0,
          controlCount: bundle?.layoutGrammar?.controls?.length || 0
        }
      )
    ];

    return Object.freeze({
      contractVersion,
      status: hasCriticalFailure(layers) ? "fail" : missing ? "unavailable" : "pass",
      valid: !hasCriticalFailure(layers) && !missing,
      surfaceId: bundle?.surfaceIR?.surface?.id || options.surfaceId || "",
      layers: freezeArray(layers),
      diagnostics: freezeArray(diagnostics)
    });
  }

  function extractSurfaceBundleFromHtml(surfaceHtml, options = {}) {
    const html = safeString(surfaceHtml);
    if (!html.trim()) return null;
    if (!extractorApi || typeof extractorApi.extractSurfaceBundleFromHtml !== "function") {
      return {
        valid: false,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-extractor-api-missing",
            "error",
            "McelSurfaceExtractors is required to extract a static app surface bundle.",
            {appId: options.appId || ""}
          )
        ],
        validation: {
          surface: {valid: false, diagnostics: []},
          layout: {valid: false, diagnostics: []}
        }
      };
    }
    try {
      return extractorApi.extractSurfaceBundleFromHtml(html, {
        surfaceId: options.surfaceId || options.expectedSurfaceId || ""
      });
    } catch (error) {
      return {
        valid: false,
        diagnostics: [
          diagnostic(
            "app-surface-conformance-extraction-threw",
            "error",
            safeString(error?.message || error || "Surface extraction failed."),
            {appId: options.appId || ""}
          )
        ],
        validation: {
          surface: {valid: false, diagnostics: []},
          layout: {valid: false, diagnostics: []}
        }
      };
    }
  }

  function findings(report) {
    return Array.isArray(report?.findings) ? report.findings : [];
  }

  function hasFinding(report, code) {
    return findings(report).some((finding) => safeString(finding?.code) === code);
  }

  function hasFindingContaining(report, token) {
    const needle = safeString(token);
    return findings(report).some((finding) => safeString(finding?.code).includes(needle));
  }

  function nonEmptyObject(value) {
    return Boolean(value && typeof value === "object" && Object.keys(value).length > 0);
  }

  function isVisibleUsefulBox(box, minWidth = 1, minHeight = 1) {
    return Boolean(
      box &&
      box.exists &&
      box.visible &&
      Number(box.width || 0) >= minWidth &&
      Number(box.height || 0) >= minHeight
    );
  }

  function primarySurface(report) {
    return report?.summary?.primarySurface || report?.primarySurface || {};
  }

  function primaryHostBox(report) {
    const summary = primarySurface(report);
    return report?.measurements?.surfaces?.primaryHost ||
      report?.measurements?.surfaces?.monacoHost ||
      summary.host ||
      {};
  }

  function contractMin(report, key) {
    const value = Number(report?.contract?.primarySurface?.[key] || 1);
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function visualViolationCount(report) {
    const measurements = report?.measurements || {};
    return [
      measurements.visualIntegrityViolations,
      measurements.contentFitViolations,
      measurements.layoutCollisions
    ].reduce((count, entries) => count + (Array.isArray(entries) ? entries.length : 0), 0);
  }

  function evaluateRuntimeReport(report, options = {}) {
    const runtimeReport = report || {};
    const measurementObject = runtimeReport.measurements || {};
    const primary = primarySurface(runtimeReport);
    const host = primaryHostBox(runtimeReport);
    const noDiagnosisThrow = !hasFinding(runtimeReport, "diagnosis-threw") &&
      safeString(runtimeReport?.verdict).toLowerCase() !== "unsupported";
    const hasMeasurements = nonEmptyObject(measurementObject);
    const minWidth = contractMin(runtimeReport, "minWidth");
    const minHeight = contractMin(runtimeReport, "minHeight");
    const hostUseful = isVisibleUsefulBox(host, minWidth, minHeight);
    const primaryUsable = !!primary.usable || hostUseful;
    const uniqueAuthoritative = primary.exactlyOneAuthoritativeSurface !== false;
    const visualClean = visualViolationCount(runtimeReport) === 0 &&
      !hasFinding(runtimeReport, "visual-integrity-violation") &&
      !hasFinding(runtimeReport, "semantic-content-fit-violation") &&
      !hasFindingContaining(runtimeReport, "layout");

    const layers = [
      layer(
        "runtime-ownership",
        primaryUsable && uniqueAuthoritative ? "pass" : "fail",
        primaryUsable && uniqueAuthoritative
          ? "Runtime primary surface ownership is usable."
          : "Runtime primary surface ownership is missing, unusable, or ambiguous.",
        {
          primaryUsable,
          exactlyOneAuthoritativeSurface: uniqueAuthoritative,
          hostSelector: host.selector || primary.host?.selector || "",
          width: Number(host.width || 0),
          height: Number(host.height || 0)
        }
      ),
      layer(
        "runtime-visual-fit",
        visualClean ? "pass" : "fail",
        visualClean
          ? "Runtime layout/readability probes found no visual-fit violations."
          : "Runtime layout/readability probes found visual-fit or layout violations.",
        {
          visualViolationCount: visualViolationCount(runtimeReport),
          fitContractVersion: safeString(runtimeReport?.measurements?.fitContract?.contractVersion || "")
        }
      ),
      layer(
        "diagnostic-no-throw",
        noDiagnosisThrow && hasMeasurements ? "pass" : "fail",
        noDiagnosisThrow && hasMeasurements
          ? "Runtime diagnostics completed and retained measurements."
          : "Runtime diagnostics threw or returned without useful measurements.",
        {
          diagnosisThrew: hasFinding(runtimeReport, "diagnosis-threw"),
          hasMeasurements
        }
      )
    ];

    const diagnostics = [];
    if (!noDiagnosisThrow) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-diagnosis-threw",
        "error",
        "The app surface conformance baseline cannot trust a diagnostic report that threw.",
        {appId: runtimeReport.appId || options.appId || ""}
      ));
    }
    if (!hasMeasurements) {
      diagnostics.push(diagnostic(
        "app-surface-conformance-measurements-missing",
        "error",
        "The app surface conformance baseline requires populated runtime measurements.",
        {appId: runtimeReport.appId || options.appId || ""}
      ));
    }

    return Object.freeze({
      contractVersion,
      status: hasCriticalFailure(layers) ? "fail" : "pass",
      valid: !hasCriticalFailure(layers),
      appId: runtimeReport.appId || options.appId || "",
      layers: freezeArray(layers),
      diagnostics: freezeArray(diagnostics)
    });
  }

  function mergeLayerSets(staticResult, runtimeResult) {
    const layers = [];
    const byId = new Map();
    [...(staticResult?.layers || []), ...(runtimeResult?.layers || [])].forEach((item) => {
      if (!item || byId.has(item.id)) return;
      byId.set(item.id, item);
      layers.push(item);
    });
    for (const id of BASELINE_LAYERS) {
      if (byId.has(id)) continue;
      layers.push(layer(id, "unavailable", "This conformance layer was not exercised by the supplied inputs.", {}));
    }
    return freezeArray(layers);
  }

  function evaluateAppSurfaceConformance(input = {}, options = {}) {
    const appId = input.appId || input.report?.appId || options.appId || "";
    const policy = registryPolicyFor(appId, {
      ...options,
      registryPolicy: input.registryPolicy || options.registryPolicy
    });
    const surfaceId = input.surfaceId ||
      input.expectedSurfaceId ||
      options.surfaceId ||
      options.expectedSurfaceId ||
      policy.surfaceId ||
      "";
    const surfaceBundle = input.surfaceBundle || extractSurfaceBundleFromHtml(input.surfaceHtml || input.html || "", {appId, surfaceId});
    const staticResult = evaluateSurfaceBundle(surfaceBundle, {appId, surfaceId});
    const runtimeResult = input.report ? evaluateRuntimeReport(input.report, {appId}) : null;
    const layers = mergeLayerSets(staticResult, runtimeResult);
    const failed = layers.filter((item) => item.status === "fail");
    const unavailable = layers.filter((item) => item.status === "unavailable");
    const requiredLayerIds = uniqueStrings(input.requiredLayerIds || options.requiredLayerIds || policy.requiredLayerIds || []);
    const requiredLayerSet = new Set(requiredLayerIds);
    const policyFailed = layers.filter((item) => requiredLayerSet.has(item.id) && item.status === "fail");
    const policyUnavailable = layers.filter((item) => requiredLayerSet.has(item.id) && item.status === "unavailable");
    const diagnostics = [
      ...(staticResult?.diagnostics || []),
      ...(runtimeResult?.diagnostics || [])
    ];
    const counts = severityCounts(diagnostics);
    const policyRequiresConformance = !!policy.conformanceRequired;
    const policyFailure = policyRequiresConformance && (policyFailed.length > 0 || policyUnavailable.length > 0);
    const status = policyRequiresConformance
      ? (policyFailure ? "fail" : "pass")
      : failed.length
        ? "fail"
        : policy.state === "legacy"
          ? "not-required"
          : unavailable.length
            ? "partial"
            : "pass";

    return Object.freeze({
      contractVersion,
      appId,
      surfaceId: surfaceId || staticResult.surfaceId,
      status,
      valid: status === "pass" || status === "not-required",
      conformanceRequired: policyRequiresConformance,
      registryState: policy.state || "unregistered",
      registryPolicy: policy,
      requiredLayerIds,
      layers,
      failedLayerIds: freezeArray(failed.map((item) => item.id)),
      unavailableLayerIds: freezeArray(unavailable.map((item) => item.id)),
      policyFailedLayerIds: freezeArray(policyFailed.map((item) => item.id)),
      policyUnavailableLayerIds: freezeArray(policyUnavailable.map((item) => item.id)),
      diagnosticCodes: freezeArray(diagnostics.map((item) => item.code).sort()),
      counts,
      diagnostics: freezeArray(diagnostics)
    });
  }


  return Object.freeze({
    contractVersion,
    BASELINE_LAYERS,
    registryPolicyFor,
    extractSurfaceBundleFromHtml,
    evaluateSurfaceBundle,
    evaluateRuntimeReport,
    evaluateAppSurfaceConformance
  });
})();

if (typeof window !== "undefined") {
  window.McelAppSurfaceConformance = McelAppSurfaceConformance;
}
