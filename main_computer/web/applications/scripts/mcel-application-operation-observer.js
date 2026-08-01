var McelApplicationOperationObserver = (() => {
  "use strict";

  const CONTRACT_VERSION = "mcel.application-operation-observation.v1";

  function safeString(value) {
    return value === undefined || value === null ? "" : String(value).trim();
  }

  function clonePlain(value) {
    if (value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(clonePlain);
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, entry]) => typeof entry !== "function" && entry !== undefined)
        .map(([key, entry]) => [key, clonePlain(entry)])
    );
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

  function stableHash(value) {
    const text = JSON.stringify(canonicalValue(value));
    let hash = 0x811c9dc5;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, "0")}`;
  }

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }

  function fail(code, message, detail = {}) {
    const error = new Error(message);
    error.name = "McelApplicationOperationObservationError";
    error.code = code;
    error.detail = deepFreeze(canonicalValue(clonePlain(detail)));
    throw error;
  }

  function requireString(source, key, code) {
    const value = safeString(source?.[key]);
    if (!value) fail(code, `Operation observation requires ${key}.`, {key});
    return value;
  }

  function now() {
    try {
      return new Date().toISOString();
    } catch (_error) {
      return "unknown-time";
    }
  }

  function readStatePath(state, path) {
    const normalized = safeString(path).replace(/^state\./, "");
    if (!normalized || normalized.includes("__proto__") || normalized.includes("constructor")) {
      fail("MCEL_APPLICATION_OBSERVATION_STATE_PATH_INVALID", "Observation state path is invalid.", {path});
    }
    let cursor = state;
    for (const part of normalized.split(".")) {
      if (cursor === null || cursor === undefined) return undefined;
      cursor = cursor[part];
    }
    return clonePlain(cursor);
  }

  function exactSemanticNode(root, semanticNodeId) {
    const matches = Array.from(root.querySelectorAll("[data-mcel-node-id]"))
      .filter((element) => safeString(element.getAttribute?.("data-mcel-node-id")) === semanticNodeId);
    if (root.getAttribute?.("data-mcel-node-id") === semanticNodeId) matches.unshift(root);
    if (matches.length !== 1) {
      fail(
        matches.length ? "MCEL_APPLICATION_OBSERVATION_NODE_AMBIGUOUS" : "MCEL_APPLICATION_OBSERVATION_NODE_MISSING",
        `Semantic node ${semanticNodeId} must resolve exactly once.`,
        {semanticNodeId, matchCount: matches.length}
      );
    }
    return matches[0];
  }

  function visible(element) {
    if (!element || element.hidden === true) return false;
    const style = typeof getComputedStyle === "function" ? getComputedStyle(element) : null;
    if (style && (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0)) {
      return false;
    }
    if (typeof element.getBoundingClientRect === "function") {
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }
    return true;
  }

  function observedProperty(element, property) {
    if (property === "textContent") return safeString(element.textContent);
    if (property === "visible") return visible(element);
    fail("MCEL_APPLICATION_OBSERVATION_PROPERTY_UNSUPPORTED", `Unsupported observation property ${property}.`, {property});
  }

  function normalizeExpected(value, mode) {
    if (mode === "string") return String(value);
    if (mode === "number") return Number(value);
    if (mode === "boolean") return Boolean(value);
    return clonePlain(value);
  }

  function requireObservationContract(contract, appId) {
    if (!contract || contract.schema !== "mcel.observation-contract.v1") {
      fail("MCEL_APPLICATION_OBSERVATION_CONTRACT_INVALID", "A valid package observation contract is required.");
    }
    if (safeString(contract.appId) !== appId) {
      fail("MCEL_APPLICATION_OBSERVATION_APP_ID_MISMATCH", "Observation contract appId does not match the mounted app.", {
        expected: appId,
        actual: safeString(contract.appId)
      });
    }
    if (contract.currentStatus !== "operation-linked") {
      fail("MCEL_APPLICATION_OBSERVATION_CONTRACT_NOT_EXECUTABLE", "Observation contract is not operation-linked.", {
        currentStatus: safeString(contract.currentStatus)
      });
    }
    if (!Array.isArray(contract.observations) || !contract.observations.length) {
      fail("MCEL_APPLICATION_OBSERVATION_CONTRACT_EMPTY", "Observation contract has no observations.");
    }
    return contract;
  }

  function observationProducer(candidate) {
    const producer = candidate
      || (typeof McelBrowserObservationProducer !== "undefined" ? McelBrowserObservationProducer : null)
      || globalThis.McelBrowserObservationProducer;
    if (!producer || typeof producer.captureReadOnlyObservation !== "function") {
      fail("MCEL_APPLICATION_OBSERVATION_PRODUCER_UNAVAILABLE", "McelBrowserObservationProducer is required.");
    }
    return producer;
  }

  function observeCommittedOperation(request = {}, options = {}) {
    const mount = request.mount;
    const result = request.operationResult;
    if (!mount || mount.kind !== "mcel-application-package-mount" || typeof mount.readState !== "function") {
      fail("MCEL_APPLICATION_OBSERVATION_MOUNT_INVALID", "A live MCEL application package mount is required.");
    }
    if (!result || result.ok !== true || result.status !== "committed" || !result.receipt) {
      fail("MCEL_APPLICATION_OBSERVATION_COMMIT_REQUIRED", "Only a committed application operation may be observed.", {
        status: safeString(result?.status),
        code: safeString(result?.code)
      });
    }

    const appId = safeString(mount.appId);
    const contract = requireObservationContract(request.observationContract || mount.observation, appId);
    const root = mount.root;
    const surfaceId = safeString(mount.surface?.surfaceId);
    const rootSurfaceId = safeString(root?.getAttribute?.("data-mcel-surface-id"));
    if (!surfaceId || rootSurfaceId !== surfaceId) {
      fail("MCEL_APPLICATION_OBSERVATION_SURFACE_MISMATCH", "Mounted and observed surface identities do not agree.", {
        mountedSurfaceId: surfaceId,
        observedSurfaceId: rootSurfaceId
      });
    }

    const packageFingerprint = requireString(mount.packageRecord, "fingerprint", "MCEL_APPLICATION_OBSERVATION_PACKAGE_FINGERPRINT_MISSING");
    const projectionFingerprint = requireString(mount.manifest?.projection, "fingerprint", "MCEL_APPLICATION_OBSERVATION_PROJECTION_FINGERPRINT_MISSING");
    if (safeString(request.packageFingerprint) && request.packageFingerprint !== packageFingerprint) {
      fail("MCEL_APPLICATION_OBSERVATION_PACKAGE_FINGERPRINT_MISMATCH", "Observed package fingerprint is stale.", {
        expected: packageFingerprint,
        actual: safeString(request.packageFingerprint)
      });
    }
    if (safeString(request.runtimeProjectionFingerprint) && request.runtimeProjectionFingerprint !== projectionFingerprint) {
      fail("MCEL_APPLICATION_OBSERVATION_PROJECTION_FINGERPRINT_MISMATCH", "Observed runtime projection fingerprint is stale.", {
        expected: projectionFingerprint,
        actual: safeString(request.runtimeProjectionFingerprint)
      });
    }

    const canonicalState = mount.readState();
    if (Number(canonicalState?.revision) !== Number(result.revision) || Number(result.receipt?.after?.revision) !== Number(result.revision)) {
      fail("MCEL_APPLICATION_OBSERVATION_REVISION_MISMATCH", "Operation receipt and canonical revision do not agree.", {
        canonicalRevision: canonicalState?.revision,
        resultRevision: result.revision,
        receiptRevision: result.receipt?.after?.revision
      });
    }

    const observedNodes = {};
    const comparisons = [];
    let observedReceipt = null;
    contract.observations.forEach((declaration) => {
      const semanticNodeId = requireString(declaration, "semanticNodeId", "MCEL_APPLICATION_OBSERVATION_NODE_ID_MISSING");
      const property = requireString(declaration, "property", "MCEL_APPLICATION_OBSERVATION_PROPERTY_MISSING");
      const element = exactSemanticNode(root, semanticNodeId);
      const actual = observedProperty(element, property);
      observedNodes[semanticNodeId] = observedNodes[semanticNodeId] || {};
      observedNodes[semanticNodeId][property] = clonePlain(actual);

      if (declaration.compareToStatePath) {
        const expected = normalizeExpected(
          readStatePath(canonicalState, declaration.compareToStatePath),
          safeString(declaration.normalization || "string")
        );
        const passed = actual === expected;
        comparisons.push({id: declaration.id, kind: "state", semanticNodeId, property, actual, expected, passed});
      } else if (declaration.compareToOperationReceipt === true) {
        try {
          observedReceipt = JSON.parse(String(actual));
        } catch (_error) {
          fail("MCEL_APPLICATION_OBSERVATION_RECEIPT_INVALID", "Visible operation receipt is not valid JSON.", {semanticNodeId});
        }
        const passed = observedReceipt.operationId === result.operationId
          && observedReceipt.status === "committed"
          && Number(observedReceipt.after?.revision) === Number(result.revision);
        comparisons.push({
          id: declaration.id,
          kind: "receipt",
          semanticNodeId,
          property,
          actual: {
            operationId: observedReceipt.operationId,
            status: observedReceipt.status,
            revision: observedReceipt.after?.revision
          },
          expected: {operationId: result.operationId, status: "committed", revision: result.revision},
          passed
        });
      } else if (Object.prototype.hasOwnProperty.call(declaration, "expected")) {
        const expected = normalizeExpected(declaration.expected, safeString(declaration.normalization));
        comparisons.push({id: declaration.id, kind: "literal", semanticNodeId, property, actual, expected, passed: actual === expected});
      }
    });

    const failedComparisons = comparisons.filter((entry) => entry.passed !== true);
    if (failedComparisons.length) {
      fail("MCEL_APPLICATION_OBSERVATION_COMPARISON_FAILED", "Browser observation does not agree with the committed operation.", {
        failedComparisons
      });
    }

    const capturedAt = safeString(request.capturedAt) || now();
    const repositoryFingerprint = requireString(request, "repositoryFingerprint", "MCEL_APPLICATION_OBSERVATION_REPOSITORY_FINGERPRINT_MISSING");
    const route = safeString(request.route || globalThis.location?.pathname || "/");
    const surfaceLocator = safeString(request.surfaceLocator || mount.manifest?.surface?.rootSelector);
    const bundle = observationProducer(options.observationProducer).captureReadOnlyObservation({
      observationId: safeString(request.observationId || `${appId}:${result.operationId}`),
      appId,
      route,
      root,
      surfaceId,
      surfaceLocator,
      surfaceDescriptor: {appId, route, surfaceId, locator: surfaceLocator},
      repositoryFingerprint,
      capturedAt,
      codeFingerprint: safeString(request.codeFingerprint || projectionFingerprint),
      browser: clonePlain(request.browser || null),
      viewport: clonePlain(request.viewport || null),
      stateMarkers: [
        `operation:${result.operationId}`,
        `revision:${result.revision}`,
        `package:${packageFingerprint}`,
        `projection:${projectionFingerprint}`
      ]
    });

    const report = {
      schema: CONTRACT_VERSION,
      status: "pass",
      ok: true,
      capturedAt,
      appId,
      operationId: result.operationId,
      intentId: result.intentId,
      repositoryFingerprint,
      packageFingerprint,
      runtimeProjectionFingerprint: projectionFingerprint,
      catalogFingerprint: safeString(mount.manifest?.source?.catalogFingerprint),
      surfaceId,
      beforeRevision: result.receipt.before.revision,
      afterRevision: result.receipt.after.revision,
      canonicalState: clonePlain(canonicalState),
      canonicalStateFingerprint: stableHash(canonicalState),
      operationReceiptFingerprint: stableHash(result.receipt),
      observedNodes: canonicalValue(observedNodes),
      receiptObservation: observedReceipt ? {
        operationId: observedReceipt.operationId,
        intentId: observedReceipt.intentId,
        status: observedReceipt.status,
        revision: observedReceipt.after?.revision
      } : null,
      comparison: {
        stateMatches: comparisons.filter((entry) => entry.kind === "state").every((entry) => entry.passed),
        receiptMatches: comparisons.filter((entry) => entry.kind === "receipt").every((entry) => entry.passed),
        surfaceMatches: true,
        checks: comparisons.map(clonePlain)
      },
      observationBundle: bundle
    };
    return deepFreeze({...report, observationFingerprint: stableHash(report)});
  }

  return deepFreeze({
    CONTRACT_VERSION,
    observeCommittedOperation
  });
})();

if (typeof window !== "undefined") {
  window.McelApplicationOperationObserver = McelApplicationOperationObserver;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelApplicationOperationObserver;
}
