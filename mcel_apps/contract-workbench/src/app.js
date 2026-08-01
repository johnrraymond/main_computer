const root = document.querySelector("#contract-workbench-app");
const status = root?.querySelector("[data-mcel-runtime-status]");

export const requiredMcelApplicationRuntime = Object.freeze({
  packageSchema: "mcel.application-package.v1",
  appId: "contract-workbench",
  conformanceMode: "forward-specification",
  manifestUrl: new URL("../mcel.runtime.json", import.meta.url).href,
  mountMethod: "MCEL.mountApplicationPackage",
  requiredFeatures: Object.freeze([
    "renderer-local-state",
    "provisional-state",
    "derived-state",
    "dynamic-input-binding",
    "control-payload-extraction",
    "dynamic-property-projection",
    "conditional-projection",
    "keyed-collection-reconciliation",
    "dynamic-item-control-binding",
    "capability-operation-runtime",
    "provisional-state-runtime",
    "operation-cancellation",
    "operation-concurrency-policy",
    "dynamic-browser-observation",
    "intent-complete-proof",
    "multi-instance-proof"
  ])
});

function reportBlocked(error) {
  const code = String(error?.code || "MCEL_FORWARD_SPECIFICATION_BLOCKED");
  if (root) {
    root.dataset.mcelRuntimeStatus = "forward-specification-blocked";
    root.dataset.mcelRuntimeBlocker = code;
  }
  if (status) {
    status.dataset.mcelRuntimeStatus = "forward-specification-blocked";
    status.textContent = `Forward specification blocked by current MCEL runtime: ${code}.`;
  }
  globalThis.__MCEL_CONTRACT_WORKBENCH_BLOCKER__ = Object.freeze({code, message: String(error?.message || "")});
}

if (!globalThis.MCEL || typeof globalThis.MCEL.mountApplicationPackage !== "function") {
  reportBlocked(Object.assign(new Error("MCEL.mountApplicationPackage is unavailable."), {
    code: "MCEL_APPLICATION_RUNTIME_UNAVAILABLE"
  }));
} else {
  try {
    await globalThis.MCEL.mountApplicationPackage({
      appId: requiredMcelApplicationRuntime.appId,
      manifestUrl: requiredMcelApplicationRuntime.manifestUrl,
      root
    });
  } catch (error) {
    reportBlocked(error);
  }
}
