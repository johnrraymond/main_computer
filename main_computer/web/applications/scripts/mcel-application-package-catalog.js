var McelApplicationPackages = (() => {
  "use strict";

  function clonePlain(value) {
    if (value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(clonePlain);
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, clonePlain(entry)]));
  }

  function deepFreeze(value) {
    if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
    Object.freeze(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key]));
    return value;
  }

  const PAYLOAD = deepFreeze({
  "catalogFingerprint": "sha256:f3f0f0f3cb90bd81b16a1448c5e67037d66b4da784e01184d9008018fb2a1688",
  "catalogFingerprintAlgorithm": "sha256-mcel-package-catalog-v1",
  "format": "mcel-application-package-browser-catalog-v1",
  "packageCount": 1,
  "packages": [
    {
      "appId": "contract-counter",
      "blueprint": "mcel_apps/contract-counter/blueprint.json",
      "conformance": {
        "currentMode": "structural-only",
        "missingBridges": [
          "operation-linked-browser-observation",
          "app-oriented-proof-orchestration"
        ],
        "targetMode": "semantic-runtime-proven"
      },
      "contracts": {
        "acceptance": "mcel_apps/contract-counter/contracts/acceptance.js",
        "adapter": "mcel_apps/contract-counter/contracts/adapter.js",
        "domain": "mcel_apps/contract-counter/contracts/domain.js",
        "intents": "mcel_apps/contract-counter/contracts/intents.js",
        "layout": "mcel_apps/contract-counter/contracts/layout.js",
        "observation": "mcel_apps/contract-counter/contracts/observation.js",
        "surface": "mcel_apps/contract-counter/contracts/surface.js"
      },
      "fileCount": 20,
      "fingerprint": "sha256:fa2971e4891dd066e67d8e2e6854bb01906fea2e5e692535efda3ed6882931e2",
      "fingerprintAlgorithm": "sha256-mcel-package-path-content-v1",
      "manifest": "mcel_apps/contract-counter/mcel.app.json",
      "packageRoot": "mcel_apps/contract-counter",
      "requirements": "mcel_apps/contract-counter/requirements.md",
      "runtime": {
        "document": "mcel_apps/contract-counter/src/index.html",
        "script": "mcel_apps/contract-counter/src/app.js",
        "style": "mcel_apps/contract-counter/src/app.css"
      },
      "runtimeProjection": {
        "documentUrl": "applications/mcel-packages/contract-counter/src/index.html",
        "fileCount": 9,
        "fingerprint": "sha256:4e0fefd5c7b5de98cd12fd5e01dcdff9ec6c3aceeef9bfeb492f308cc7fde3a2",
        "fingerprintAlgorithm": "sha256-mcel-runtime-projection-v1",
        "manifest": "main_computer/web/applications/mcel-packages/contract-counter/mcel.runtime.json",
        "manifestUrl": "applications/mcel-packages/contract-counter/mcel.runtime.json",
        "root": "main_computer/web/applications/mcel-packages/contract-counter",
        "schema": "mcel.application-runtime-projection.v1",
        "scriptUrl": "applications/mcel-packages/contract-counter/src/app.js",
        "styleUrl": "applications/mcel-packages/contract-counter/src/app.css"
      },
      "template": {
        "id": "mcel.canonical-application-template",
        "version": "1.0.0"
      },
      "testsRoot": "mcel_apps/contract-counter/tests",
      "title": "Contract Counter"
    }
  ],
  "schema": "mcel.application-package-browser-catalog.v1",
  "sourceFormat": "mcel-application-packages-v1",
  "sourceSchema": "mcel.application-package-catalog.v1"
});
  const PACKAGES_BY_ID = new Map(PAYLOAD.packages.map((record) => [record.appId, record]));

  function normalizeAppId(value) {
    return String(value || "").trim();
  }

  function getCatalog() {
    return clonePlain(PAYLOAD);
  }

  function listPackages() {
    return PAYLOAD.packages.map(clonePlain);
  }

  function getPackage(appId) {
    const record = PACKAGES_BY_ID.get(normalizeAppId(appId));
    return record ? clonePlain(record) : null;
  }

  function hasPackage(appId) {
    return PACKAGES_BY_ID.has(normalizeAppId(appId));
  }

  return Object.freeze({
    SCHEMA: PAYLOAD.schema,
    FORMAT: PAYLOAD.format,
    catalogFingerprint: PAYLOAD.catalogFingerprint,
    catalogFingerprintAlgorithm: PAYLOAD.catalogFingerprintAlgorithm,
    packageCount: PAYLOAD.packageCount,
    getCatalog,
    listPackages,
    getPackage,
    hasPackage
  });
})();

if (typeof window !== "undefined") {
  window.McelApplicationPackages = McelApplicationPackages;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelApplicationPackages;
}
