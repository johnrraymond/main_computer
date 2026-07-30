var McelObservationBundle = (() => {
  "use strict";

  const CONTRACT_VERSION = "mcel.observation-bundle.v1";
  const MODE = "read-only";
  const LENS_IDS = Object.freeze([
    "dom",
    "accessibility",
    "layout",
    "visual",
    "source",
    "transition",
    "ridges"
  ]);
  const LENS_STATUSES = Object.freeze(["captured", "missing", "unavailable"]);

  function globalApi(name) {
    if (typeof window !== "undefined" && window[name]) return window[name];
    if (typeof globalThis !== "undefined" && globalThis[name]) return globalThis[name];
    return null;
  }

  function epistemicApi(options) {
    return options?.epistemicApi || globalApi("McelEpistemicStatus");
  }

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value).trim();
  }

  function clonePlain(value) {
    if (value == null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(clonePlain);
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => typeof entry !== "function" && entry !== undefined)
        .map(([key, entry]) => [key, clonePlain(entry)])
    );
  }

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }

  function canonicalValue(value) {
    if (Array.isArray(value)) return value.map(canonicalValue);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .filter((key) => typeof value[key] !== "function" && value[key] !== undefined)
        .map((key) => [key, canonicalValue(value[key])])
    );
  }

  function canonicalJson(value) {
    return JSON.stringify(canonicalValue(value));
  }

  function stableHash(value) {
    const text = typeof value === "string" ? value : canonicalJson(value);
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }

  function uniqueStrings(values) {
    return [...new Set((Array.isArray(values) ? values : [])
      .map((value) => safeString(value))
      .filter(Boolean))].sort();
  }

  function normalizeFact(lensId, fact, index, capturedAt) {
    const input = fact && typeof fact === "object" ? fact : {};
    const kind = safeString(input.kind);
    const locator = safeString(input.locator);
    const fingerprint = safeString(input.fingerprint);
    return deepFreeze({
      id: safeString(input.id) || `fact.${lensId}.${stableHash({kind, locator, fingerprint, index})}`,
      lens: lensId,
      kind,
      locator,
      value: input.value === undefined ? null : canonicalValue(clonePlain(input.value)),
      observedAt: safeString(input.observedAt) || capturedAt,
      fingerprint,
      detail: canonicalValue(clonePlain(input.detail || {}))
    });
  }

  function normalizeLens(lensId, input, capturedAt) {
    if (input === undefined || input === null) {
      return deepFreeze({
        id: lensId,
        status: "missing",
        reason: "not-collected",
        facts: []
      });
    }
    const subject = Array.isArray(input) ? {status: "captured", facts: input} : input;
    const status = safeString(subject.status || "captured").toLowerCase();
    const facts = (Array.isArray(subject.facts) ? subject.facts : [])
      .map((fact, index) => normalizeFact(lensId, fact, index, capturedAt))
      .sort((left, right) => left.id.localeCompare(right.id));
    return deepFreeze({
      id: lensId,
      status,
      reason: safeString(subject.reason),
      facts
    });
  }

  function resolveClaims(claims, api) {
    const groups = new Map();
    claims.forEach((claim) => {
      const key = api.claimKey(claim);
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(claim);
    });
    return [...groups.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, candidates]) => api.resolveClaimCandidates(candidates));
  }

  function canonicalizeObservationBundle(bundle) {
    const subject = bundle && typeof bundle === "object" ? bundle : {};
    const lenses = {};
    LENS_IDS.forEach((lensId) => {
      const lens = subject.lenses?.[lensId] || {};
      lenses[lensId] = {
        id: lensId,
        status: safeString(lens.status),
        reason: safeString(lens.reason),
        facts: [...(lens.facts || [])]
          .map((fact) => canonicalValue(clonePlain(fact)))
          .sort((left, right) => safeString(left.id).localeCompare(safeString(right.id)))
      };
    });
    return canonicalValue({
      contractVersion: CONTRACT_VERSION,
      observationId: safeString(subject.observationId),
      appId: safeString(subject.appId),
      route: safeString(subject.route),
      mode: safeString(subject.mode),
      capturedAt: safeString(subject.capturedAt),
      repositoryFingerprint: safeString(subject.repositoryFingerprint),
      viewport: canonicalValue(clonePlain(subject.viewport || {})),
      stateMarkers: uniqueStrings(subject.stateMarkers),
      provenance: canonicalValue(clonePlain(subject.provenance || {})),
      lenses,
      claims: [...(subject.claims || [])]
        .map((claim) => canonicalValue(clonePlain(claim)))
        .sort((left, right) => safeString(left.claimId).localeCompare(safeString(right.claimId))),
      resolvedClaims: [...(subject.resolvedClaims || [])]
        .map((claim) => canonicalValue(clonePlain(claim)))
        .sort((left, right) => safeString(left.claimId).localeCompare(safeString(right.claimId)))
    });
  }

  function observationBundleFingerprint(bundle) {
    return `mcel-observation.${stableHash(canonicalizeObservationBundle(bundle))}`;
  }

  function diagnostic(code, message, detail = {}) {
    return deepFreeze({
      code: safeString(code),
      message: safeString(message),
      detail: canonicalValue(clonePlain(detail))
    });
  }

  function validateObservationBundle(bundle, options = {}) {
    const subject = bundle && typeof bundle === "object" ? bundle : {};
    const api = epistemicApi(options);
    const diagnostics = [];

    if (subject.contractVersion !== CONTRACT_VERSION) {
      diagnostics.push(diagnostic(
        "observation-contract-version-invalid",
        "Observation bundle contractVersion is invalid.",
        {actual: subject.contractVersion, expected: CONTRACT_VERSION}
      ));
    }
    if (!safeString(subject.observationId)) {
      diagnostics.push(diagnostic("observation-id-missing", "Observation bundle requires observationId."));
    }
    if (!safeString(subject.appId)) {
      diagnostics.push(diagnostic("observation-app-id-missing", "Observation bundle requires appId."));
    }
    if (safeString(subject.mode) !== MODE) {
      diagnostics.push(diagnostic(
        "observation-mode-not-read-only",
        "Observation bundle v1 permits read-only collection only.",
        {mode: safeString(subject.mode)}
      ));
    }
    if (!safeString(subject.capturedAt)) {
      diagnostics.push(diagnostic("observation-captured-at-missing", "Observation bundle requires capturedAt."));
    }
    if (!safeString(subject.repositoryFingerprint)) {
      diagnostics.push(diagnostic(
        "observation-repository-fingerprint-missing",
        "Observation bundle requires repositoryFingerprint."
      ));
    }
    if (
      !safeString(subject.provenance?.producer) ||
      !safeString(subject.provenance?.collectorVersion) ||
      !safeString(subject.provenance?.codeFingerprint)
    ) {
      diagnostics.push(diagnostic(
        "observation-provenance-incomplete",
        "Observation provenance requires producer, collectorVersion, and codeFingerprint."
      ));
    }

    LENS_IDS.forEach((lensId) => {
      const lens = subject.lenses?.[lensId];
      if (!lens) {
        diagnostics.push(diagnostic(
          "observation-lens-missing",
          "Every observation lens must be present, even when evidence is missing.",
          {lensId}
        ));
        return;
      }
      if (!LENS_STATUSES.includes(safeString(lens.status))) {
        diagnostics.push(diagnostic(
          "observation-lens-status-invalid",
          "Observation lens status is invalid.",
          {lensId, status: safeString(lens.status)}
        ));
      }
      if (lens.status !== "captured" && !safeString(lens.reason)) {
        diagnostics.push(diagnostic(
          "observation-lens-missing-reason",
          "A missing or unavailable observation lens requires a reason.",
          {lensId}
        ));
      }
      if (lens.status !== "captured" && (lens.facts || []).length > 0) {
        diagnostics.push(diagnostic(
          "observation-lens-status-fact-conflict",
          "A missing or unavailable observation lens cannot contain captured facts.",
          {lensId, status: lens.status}
        ));
      }
      (lens.facts || []).forEach((fact) => {
        if (
          !safeString(fact.id) ||
          !safeString(fact.kind) ||
          !safeString(fact.locator) ||
          !safeString(fact.observedAt) ||
          !safeString(fact.fingerprint)
        ) {
          diagnostics.push(diagnostic(
            "observation-fact-incomplete",
            "Every observation fact requires id, kind, exact locator, observation time, and fingerprint.",
            {lensId, factId: safeString(fact.id)}
          ));
        }
        if (safeString(fact.lens) !== lensId) {
          diagnostics.push(diagnostic(
            "observation-fact-lens-mismatch",
            "Observation fact lens does not match its containing lens.",
            {lensId, factId: safeString(fact.id), factLens: safeString(fact.lens)}
          ));
        }
      });
    });

    if (!api || typeof api.validateClaim !== "function") {
      diagnostics.push(diagnostic(
        "epistemic-api-unavailable",
        "Observation bundle validation requires McelEpistemicStatus."
      ));
    } else {
      [...(subject.claims || []), ...(subject.resolvedClaims || [])].forEach((claim) => {
        const report = api.validateClaim(claim);
        report.diagnostics.forEach((item) => {
          diagnostics.push(diagnostic(
            `observation-${item.code}`,
            item.message,
            {claimId: safeString(claim.claimId), ...item.detail}
          ));
        });
      });
      try {
        const normalizedClaims = (subject.claims || []).map((claim) => api.createClaim(claim));
        const expectedResolvedClaims = resolveClaims(normalizedClaims, api);
        if (api.canonicalJson(expectedResolvedClaims) !== api.canonicalJson(subject.resolvedClaims || [])) {
          diagnostics.push(diagnostic(
            "observation-claim-resolution-invalid",
            "Observation resolvedClaims do not match deterministic conflict resolution."
          ));
        }
      } catch (error) {
        diagnostics.push(diagnostic(
          "observation-claim-resolution-failed",
          "Observation claims could not be resolved deterministically.",
          {
            code: safeString(error?.code),
            message: safeString(error?.message || error)
          }
        ));
      }
    }

    const expectedFingerprint = observationBundleFingerprint(subject);
    if (safeString(subject.bundleFingerprint) !== expectedFingerprint) {
      diagnostics.push(diagnostic(
        "observation-fingerprint-invalid",
        "Observation bundle fingerprint does not match its canonical contents.",
        {actual: safeString(subject.bundleFingerprint), expected: expectedFingerprint}
      ));
    }

    return deepFreeze({
      contractVersion: CONTRACT_VERSION,
      valid: diagnostics.length === 0,
      diagnostics,
      diagnosticCodes: diagnostics.map((item) => item.code)
    });
  }

  function createObservationBundle(input, options = {}) {
    const source = input && typeof input === "object" ? input : {};
    const api = epistemicApi(options);
    if (!api || typeof api.createClaim !== "function" || typeof api.resolveClaimCandidates !== "function") {
      throw new Error("McelObservationBundle requires McelEpistemicStatus.");
    }
    const mode = safeString(source.mode || MODE);
    if (mode !== MODE) {
      const error = new Error("MCEL observation bundle v1 is read-only and cannot record active mutation.");
      error.code = "MCEL_OBSERVATION_MODE_FORBIDDEN";
      throw error;
    }

    const capturedAt = safeString(source.capturedAt);
    const repositoryFingerprint = safeString(source.repositoryFingerprint);
    const claims = (Array.isArray(source.claims) ? source.claims : [])
      .map((claim) => api.createClaim({
        ...claim,
        observedAt: safeString(claim.observedAt) || capturedAt,
        repositoryFingerprint: safeString(claim.repositoryFingerprint) || repositoryFingerprint
      }))
      .sort((left, right) => left.claimId.localeCompare(right.claimId));
    const lenses = {};
    LENS_IDS.forEach((lensId) => {
      lenses[lensId] = normalizeLens(lensId, source.lenses?.[lensId], capturedAt);
    });

    const partial = {
      contractVersion: CONTRACT_VERSION,
      observationId: safeString(source.observationId),
      appId: safeString(source.appId),
      route: safeString(source.route),
      mode,
      capturedAt,
      repositoryFingerprint,
      viewport: canonicalValue(clonePlain(source.viewport || {})),
      stateMarkers: uniqueStrings(source.stateMarkers),
      provenance: canonicalValue(clonePlain({
        producer: safeString(source.provenance?.producer),
        collectorVersion: safeString(source.provenance?.collectorVersion),
        codeFingerprint: safeString(source.provenance?.codeFingerprint),
        browser: source.provenance?.browser || null,
        sources: source.provenance?.sources || []
      })),
      lenses,
      claims,
      resolvedClaims: resolveClaims(claims, api)
    };
    const bundle = deepFreeze({
      ...partial,
      bundleFingerprint: observationBundleFingerprint(partial)
    });
    const report = validateObservationBundle(bundle, {epistemicApi: api});
    if (!report.valid) {
      const error = new Error(`Invalid ${CONTRACT_VERSION}: ${report.diagnosticCodes.join(", ")}`);
      error.code = "MCEL_OBSERVATION_BUNDLE_INVALID";
      error.report = report;
      throw error;
    }
    return bundle;
  }

  return deepFreeze({
    CONTRACT_VERSION,
    MODE,
    LENS_IDS,
    LENS_STATUSES,
    createObservationBundle,
    validateObservationBundle,
    canonicalizeObservationBundle,
    observationBundleFingerprint
  });
})();

if (typeof window !== "undefined") {
  window.McelObservationBundle = McelObservationBundle;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelObservationBundle;
}
