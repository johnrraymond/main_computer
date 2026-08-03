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
  "catalogFingerprint": "sha256:cbbd194dfdbde7ab8a1aa1154c4bdd72110d6a92e43af14e6522657c26acc318",
  "catalogFingerprintAlgorithm": "sha256-mcel-package-catalog-v1",
  "format": "mcel-application-package-browser-catalog-v1",
  "packageCount": 2,
  "packages": [
    {
      "appId": "contract-counter",
      "authoring": {},
      "blueprint": "mcel_apps/contract-counter/blueprint.json",
      "conformance": {
        "currentMode": "semantic-runtime-proven",
        "missingBridges": [],
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
      "fingerprint": "sha256:1ab625e72e54349c8abae27ca30cf75ccca1d7895fd172a81d93f6863e6f92b6",
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
        "fileCount": 11,
        "fingerprint": "sha256:8641d80e0cf1caede1b8402c37c37206a941ff48bc0394d27ccabf93ed4c5037",
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
    },
    {
      "appId": "contract-workbench",
      "authoring": {
        "definition": "mcel_apps/contract-workbench/application.js",
        "featureMatrix": "mcel_apps/contract-workbench/forward-specification.json",
        "normalizedDefinition": "mcel_apps/contract-workbench/generated/mcel.application.normalized.json"
      },
      "blueprint": "mcel_apps/contract-workbench/blueprint.json",
      "conformance": {
        "currentMode": "semantic-runtime-proven",
        "missingBridges": [],
        "targetMode": "semantic-runtime-proven"
      },
      "contracts": {
        "acceptance": "mcel_apps/contract-workbench/contracts/acceptance.js",
        "adapter": "mcel_apps/contract-workbench/contracts/adapter.js",
        "domain": "mcel_apps/contract-workbench/contracts/domain.js",
        "intents": "mcel_apps/contract-workbench/contracts/intents.js",
        "layout": "mcel_apps/contract-workbench/contracts/layout.js",
        "observation": "mcel_apps/contract-workbench/contracts/observation.js",
        "surface": "mcel_apps/contract-workbench/contracts/surface.js"
      },
      "fileCount": 24,
      "fingerprint": "sha256:d02fecc4fedd92a1c435a62f25deea63ed8d040bff00764843f6d9f7eb157be3",
      "fingerprintAlgorithm": "sha256-mcel-package-path-content-v1",
      "manifest": "mcel_apps/contract-workbench/mcel.app.json",
      "packageRoot": "mcel_apps/contract-workbench",
      "requirements": "mcel_apps/contract-workbench/requirements.md",
      "runtime": {
        "document": "mcel_apps/contract-workbench/src/index.html",
        "script": "mcel_apps/contract-workbench/src/app.js",
        "style": "mcel_apps/contract-workbench/src/app.css"
      },
      "runtimeProjection": {
        "documentUrl": "applications/mcel-packages/contract-workbench/src/index.html",
        "fileCount": 11,
        "fingerprint": "sha256:b5e519b24578db457fc3672b012a9dc32b0d15b06921442eb4b89ff0ac622c8a",
        "fingerprintAlgorithm": "sha256-mcel-runtime-projection-v1",
        "manifest": "main_computer/web/applications/mcel-packages/contract-workbench/mcel.runtime.json",
        "manifestUrl": "applications/mcel-packages/contract-workbench/mcel.runtime.json",
        "root": "main_computer/web/applications/mcel-packages/contract-workbench",
        "schema": "mcel.application-runtime-projection.v1",
        "scriptUrl": "applications/mcel-packages/contract-workbench/src/app.js",
        "styleUrl": "applications/mcel-packages/contract-workbench/src/app.css"
      },
      "template": {
        "id": "mcel.forward-specification-acid-application",
        "version": "0.1.0"
      },
      "testsRoot": "mcel_apps/contract-workbench/tests",
      "title": "Contract Operations Workbench"
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
