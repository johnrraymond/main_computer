var McelEpistemicStatus = (() => {
  "use strict";

  const CONTRACT_VERSION = "mcel.epistemic-status.v1";
  const STATUSES = Object.freeze([
    "declared",
    "observed",
    "inferred",
    "verified",
    "rejected",
    "ambiguous"
  ]);
  const TRUTH_GATE_ELIGIBLE_STATUSES = Object.freeze(["verified"]);
  const AUTHORED_SOURCE_KINDS = Object.freeze(["authored-contract", "authored-ridge"]);
  const VALIDATOR_STATUSES = Object.freeze(["pass", "fail", "inconclusive", "error"]);

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

  function uniqueObjects(values) {
    const byCanonicalValue = new Map();
    (Array.isArray(values) ? values : []).forEach((value) => {
      const plain = clonePlain(value);
      byCanonicalValue.set(canonicalJson(plain), plain);
    });
    return [...byCanonicalValue.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([, value]) => value);
  }

  function normalizeConfidence(value) {
    if (value === undefined || value === null || value === "") return null;
    const confidence = Number(value);
    return Number.isFinite(confidence) ? confidence : null;
  }

  function normalizeSource(source, index = 0) {
    const input = source && typeof source === "object" ? source : {};
    const kind = safeString(input.kind);
    const locator = safeString(input.locator);
    const fingerprint = safeString(input.fingerprint);
    return deepFreeze({
      id: safeString(input.id) || `source.${stableHash({kind, locator, fingerprint, index})}`,
      kind,
      locator,
      fingerprint,
      observedAt: safeString(input.observedAt),
      authority: safeString(input.authority),
      explicit: input.explicit === true,
      declaredFields: uniqueStrings(input.declaredFields),
      detail: canonicalValue(clonePlain(input.detail || {}))
    });
  }

  function normalizeValidatorResult(result, index = 0) {
    const input = result && typeof result === "object" ? result : {};
    const validatorId = safeString(input.validatorId || input.validator);
    const status = safeString(input.status).toLowerCase();
    return deepFreeze({
      id: safeString(input.id) || `validator-result.${stableHash({validatorId, status, index})}`,
      validatorId,
      validatorVersion: safeString(input.validatorVersion),
      status,
      code: safeString(input.code),
      message: safeString(input.message),
      evidenceFingerprint: safeString(input.evidenceFingerprint),
      validatedAt: safeString(input.validatedAt)
    });
  }

  function normalizeContradiction(contradiction, index = 0) {
    const input = contradiction && typeof contradiction === "object" ? contradiction : {};
    return deepFreeze({
      id: safeString(input.id) || `contradiction.${stableHash({input, index})}`,
      claimId: safeString(input.claimId),
      status: safeString(input.status),
      value: canonicalValue(clonePlain(input.value)),
      sourceIds: uniqueStrings(input.sourceIds),
      reason: safeString(input.reason)
    });
  }

  function diagnostic(code, message, detail = {}) {
    return deepFreeze({
      code: safeString(code),
      message: safeString(message),
      detail: canonicalValue(clonePlain(detail))
    });
  }

  function claimKey(claim) {
    return `${safeString(claim?.subject)}::${safeString(claim?.predicate)}`;
  }

  function validateClaim(claim) {
    const subject = claim && typeof claim === "object" ? claim : {};
    const diagnostics = [];
    const status = safeString(subject.status).toLowerCase();
    const sources = Array.isArray(subject.sources) ? subject.sources : [];
    const contradictions = Array.isArray(subject.contradictions) ? subject.contradictions : [];
    const validatorResults = Array.isArray(subject.validatorResults) ? subject.validatorResults : [];

    if (!safeString(subject.claimId)) {
      diagnostics.push(diagnostic("claim-id-missing", "An epistemic claim requires a stable claimId."));
    }
    if (!safeString(subject.subject)) {
      diagnostics.push(diagnostic("claim-subject-missing", "An epistemic claim requires a subject."));
    }
    if (!safeString(subject.predicate)) {
      diagnostics.push(diagnostic("claim-predicate-missing", "An epistemic claim requires a predicate."));
    }
    if (!STATUSES.includes(status)) {
      diagnostics.push(diagnostic(
        "claim-status-invalid",
        "An epistemic claim uses an unsupported status.",
        {status, allowedStatuses: STATUSES}
      ));
    }
    if (!safeString(subject.observedAt)) {
      diagnostics.push(diagnostic("claim-observed-at-missing", "An epistemic claim requires observedAt."));
    }
    if (!safeString(subject.repositoryFingerprint)) {
      diagnostics.push(diagnostic(
        "claim-repository-fingerprint-missing",
        "An epistemic claim requires the repository fingerprint it was derived against."
      ));
    }
    if (sources.length === 0) {
      diagnostics.push(diagnostic("claim-sources-missing", "An epistemic claim requires at least one source."));
    }
    sources.forEach((source) => {
      if (
        !safeString(source.id) ||
        !safeString(source.kind) ||
        !safeString(source.locator) ||
        !safeString(source.fingerprint)
      ) {
        diagnostics.push(diagnostic(
          "claim-source-incomplete",
          "Every claim source requires an id, kind, exact locator, and evidence fingerprint.",
          {sourceId: safeString(source.id)}
        ));
      }
    });

    const confidence = subject.confidence;
    if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
      diagnostics.push(diagnostic(
        "claim-confidence-invalid",
        "Claim confidence must be null or a number from zero through one.",
        {confidence}
      ));
    }
    if (status === "inferred" && confidence === null) {
      diagnostics.push(diagnostic(
        "inferred-claim-confidence-missing",
        "An inferred claim requires an explicit confidence."
      ));
    }

    if (status === "declared") {
      const explicitAuthoredSource = sources.some((source) =>
        AUTHORED_SOURCE_KINDS.includes(safeString(source.kind)) &&
        source.explicit === true &&
        safeString(source.locator) &&
        Array.isArray(source.declaredFields) &&
        source.declaredFields.length > 0
      );
      if (!explicitAuthoredSource) {
        diagnostics.push(diagnostic(
          "declared-claim-explicit-source-missing",
          "A declared claim requires an exact authored ridge or contract source and the fields it explicitly declares."
        ));
      }
    }

    if (status === "verified") {
      const passingValidators = validatorResults.filter((result) => result.status === "pass");
      const failingValidators = validatorResults.filter((result) => result.status === "fail");
      if (passingValidators.length === 0 || failingValidators.length > 0) {
        diagnostics.push(diagnostic(
          "verified-claim-validator-proof-missing",
          "A verified claim requires at least one passing deterministic validator and no failing validator.",
          {
            passingValidatorCount: passingValidators.length,
            failingValidatorCount: failingValidators.length
          }
        ));
      }
      if (contradictions.length > 0) {
        diagnostics.push(diagnostic(
          "verified-claim-has-contradictions",
          "A claim with unresolved contradictions cannot be verified."
        ));
      }
      passingValidators.forEach((result) => {
        if (
          !safeString(result.validatorId) ||
          !safeString(result.validatorVersion) ||
          !safeString(result.evidenceFingerprint) ||
          !safeString(result.validatedAt)
        ) {
          diagnostics.push(diagnostic(
            "verified-claim-validator-result-incomplete",
            "A passing validator result requires validator identity, version, evidence fingerprint, and validation time.",
            {validatorResultId: safeString(result.id)}
          ));
        }
      });
    }

    validatorResults.forEach((result) => {
      if (!safeString(result.validatorId) || !VALIDATOR_STATUSES.includes(result.status)) {
        diagnostics.push(diagnostic(
          "claim-validator-result-invalid",
          "Every validator result requires a validatorId and supported status.",
          {validatorResultId: safeString(result.id), status: safeString(result.status)}
        ));
      }
    });

    if (
      status === "rejected" &&
      validatorResults.every((result) => result.status !== "fail") &&
      contradictions.length === 0
    ) {
      diagnostics.push(diagnostic(
        "rejected-claim-counterevidence-missing",
        "A rejected claim requires a failing validator result or preserved counterevidence."
      ));
    }

    if (status === "ambiguous" && contradictions.length === 0) {
      diagnostics.push(diagnostic(
        "ambiguous-claim-contradictions-missing",
        "An ambiguous claim must preserve the competing claims as contradictions."
      ));
    }
    if (
      subject.requiredForTruthGate === true &&
      (!Array.isArray(subject.truthGateRequirementIds) || subject.truthGateRequirementIds.length === 0)
    ) {
      diagnostics.push(diagnostic(
        "truth-gate-requirement-id-missing",
        "A truth-gate-required claim must identify the requirement or requirements it is intended to satisfy."
      ));
    }

    return deepFreeze({
      contractVersion: CONTRACT_VERSION,
      valid: diagnostics.length === 0,
      diagnostics,
      diagnosticCodes: diagnostics.map((item) => item.code)
    });
  }

  function normalizeClaim(input) {
    const source = input && typeof input === "object" ? input : {};
    const status = safeString(source.status).toLowerCase();
    const claim = {
      contractVersion: CONTRACT_VERSION,
      claimId: safeString(source.claimId || source.id),
      subject: safeString(source.subject),
      predicate: safeString(source.predicate),
      value: source.value === undefined ? null : canonicalValue(clonePlain(source.value)),
      status,
      sources: uniqueObjects((source.sources || []).map(normalizeSource)),
      confidence: normalizeConfidence(source.confidence),
      contradictions: uniqueObjects((source.contradictions || []).map(normalizeContradiction)),
      observedAt: safeString(source.observedAt),
      repositoryFingerprint: safeString(source.repositoryFingerprint),
      validatorResults: uniqueObjects((source.validatorResults || []).map(normalizeValidatorResult)),
      requiredForTruthGate: source.requiredForTruthGate === true,
      truthGateRequirementIds: uniqueStrings(source.truthGateRequirementIds)
    };
    return deepFreeze(claim);
  }

  function createClaim(input) {
    const claim = normalizeClaim(input);
    const report = validateClaim(claim);
    if (!report.valid) {
      const error = new Error(`Invalid ${CONTRACT_VERSION} claim: ${report.diagnosticCodes.join(", ")}`);
      error.code = "MCEL_EPISTEMIC_CLAIM_INVALID";
      error.report = report;
      throw error;
    }
    return claim;
  }

  function conflictClaimId(subject, predicate, candidates) {
    return `claim.ambiguous.${stableHash({
      subject,
      predicate,
      candidateIds: candidates.map((claim) => claim.claimId).sort()
    })}`;
  }

  function ambiguousResolution(candidates, reason) {
    const subject = candidates[0].subject;
    const predicate = candidates[0].predicate;
    return createClaim({
      claimId: conflictClaimId(subject, predicate, candidates),
      subject,
      predicate,
      value: null,
      status: "ambiguous",
      sources: candidates.flatMap((claim) => claim.sources),
      confidence: null,
      contradictions: candidates.map((claim) => ({
        claimId: claim.claimId,
        status: claim.status,
        value: claim.value,
        sourceIds: claim.sources.map((source) => source.id),
        reason
      })),
      observedAt: [...candidates.map((claim) => claim.observedAt)].sort().at(-1),
      repositoryFingerprint: candidates[0].repositoryFingerprint,
      validatorResults: candidates.flatMap((claim) => claim.validatorResults),
      requiredForTruthGate: candidates.some((claim) => claim.requiredForTruthGate),
      truthGateRequirementIds: candidates.flatMap((claim) => claim.truthGateRequirementIds)
    });
  }

  function resolveClaimCandidates(inputs) {
    const candidates = (Array.isArray(inputs) ? inputs : []).map(createClaim);
    if (candidates.length === 0) {
      throw new Error("Claim resolution requires at least one epistemic claim.");
    }

    const key = claimKey(candidates[0]);
    if (candidates.some((claim) => claimKey(claim) !== key)) {
      throw new Error("Claim resolution candidates must share one subject and predicate.");
    }
    const repositoryFingerprints = uniqueStrings(candidates.map((claim) => claim.repositoryFingerprint));
    if (repositoryFingerprints.length !== 1) {
      throw new Error("Claim resolution cannot combine observations from different repository fingerprints.");
    }

    const distinctValues = new Map();
    candidates.forEach((claim) => {
      distinctValues.set(canonicalJson(claim.value), claim.value);
    });
    if (distinctValues.size === 1) {
      if (candidates.length === 1) return candidates[0];
      if (candidates.some((claim) => ["rejected", "ambiguous"].includes(claim.status))) {
        return ambiguousResolution(candidates, "epistemic-status-conflict");
      }
      const statusOrder = ["verified", "observed", "declared", "inferred"];
      const status = statusOrder.find((candidateStatus) =>
        candidates.some((claim) => claim.status === candidateStatus)
      ) || "ambiguous";
      const representative = candidates.find((claim) => claim.status === status) || candidates[0];
      return createClaim({
        claimId: `claim.resolved.${stableHash({
          key,
          candidateIds: candidates.map((claim) => claim.claimId).sort()
        })}`,
        subject: representative.subject,
        predicate: representative.predicate,
        value: representative.value,
        status,
        sources: candidates.flatMap((claim) => claim.sources),
        confidence: status === "inferred"
          ? Math.min(...candidates.map((claim) => claim.confidence).filter((value) => value !== null))
          : representative.confidence,
        contradictions: [],
        observedAt: [...candidates.map((claim) => claim.observedAt)].sort().at(-1),
        repositoryFingerprint: repositoryFingerprints[0],
        validatorResults: candidates.flatMap((claim) => claim.validatorResults),
        requiredForTruthGate: candidates.some((claim) => claim.requiredForTruthGate),
        truthGateRequirementIds: candidates.flatMap((claim) => claim.truthGateRequirementIds)
      });
    }

    return ambiguousResolution(candidates, "conflicting-value");
  }

  function assessTruthGate(input = {}) {
    const subject = Array.isArray(input) ? {claims: input} : (input || {});
    const claims = (Array.isArray(subject.claims) ? subject.claims : []).map(createClaim);
    const seenClaimIds = new Set();
    const duplicateClaimIds = [];
    claims.forEach((claim) => {
      if (seenClaimIds.has(claim.claimId)) duplicateClaimIds.push(claim.claimId);
      seenClaimIds.add(claim.claimId);
    });
    if (duplicateClaimIds.length > 0) {
      const error = new Error(`Epistemic claim set contains duplicate claim IDs: ${uniqueStrings(duplicateClaimIds).join(", ")}`);
      error.code = "MCEL_EPISTEMIC_CLAIM_SET_INVALID";
      error.report = deepFreeze({
        contractVersion: CONTRACT_VERSION,
        valid: false,
        diagnosticCodes: ["duplicate-claim-id"],
        duplicateClaimIds: uniqueStrings(duplicateClaimIds)
      });
      throw error;
    }
    const explicitlyRequiredIds = uniqueStrings(subject.requiredClaimIds);
    const requiredIds = uniqueStrings([
      ...explicitlyRequiredIds,
      ...claims.filter((claim) => claim.requiredForTruthGate).map((claim) => claim.claimId)
    ]);
    const byId = new Map(claims.map((claim) => [claim.claimId, claim]));
    const missingClaimIds = requiredIds.filter((claimId) => !byId.has(claimId));
    const blockedClaims = requiredIds
      .map((claimId) => byId.get(claimId))
      .filter(Boolean)
      .filter((claim) => !TRUTH_GATE_ELIGIBLE_STATUSES.includes(claim.status))
      .map((claim) => ({
        claimId: claim.claimId,
        status: claim.status,
        subject: claim.subject,
        predicate: claim.predicate
      }));

    const statusCounts = {};
    claims.forEach((claim) => {
      statusCounts[claim.status] = (statusCounts[claim.status] || 0) + 1;
    });

    return deepFreeze({
      contractVersion: CONTRACT_VERSION,
      claimCount: claims.length,
      requiredClaimIds: requiredIds,
      missingClaimIds,
      blockedClaims,
      statusCounts,
      truthGateEligible: missingClaimIds.length === 0 && blockedClaims.length === 0
    });
  }

  return deepFreeze({
    CONTRACT_VERSION,
    STATUSES,
    TRUTH_GATE_ELIGIBLE_STATUSES,
    AUTHORED_SOURCE_KINDS,
    VALIDATOR_STATUSES,
    claimKey,
    createClaim,
    validateClaim,
    resolveClaimCandidates,
    assessTruthGate,
    canonicalJson
  });
})();

if (typeof window !== "undefined") {
  window.McelEpistemicStatus = McelEpistemicStatus;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelEpistemicStatus;
}
