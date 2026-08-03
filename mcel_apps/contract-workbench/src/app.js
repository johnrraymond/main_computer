const root = document.querySelector("#contract-workbench-app");
const status = root?.querySelector("[data-mcel-runtime-status]");

export const requiredMcelApplicationRuntime = Object.freeze({
  packageSchema: "mcel.application-package.v1",
  appId: "contract-workbench",
  conformanceMode: "semantic-runtime-proven",
  manifestUrl: new URL("../mcel.runtime.json", import.meta.url).href,
  mountMethod: "MCEL.mountApplicationPackage",
  requiredFeatures: Object.freeze([])
});

function reportFailure(error) {
  const code = String(error?.code || "MCEL_APPLICATION_RUNTIME_FAILED");
  if (root) {
    root.dataset.mcelRuntimeStatus = "runtime-failed";
    root.dataset.mcelRuntimeBlocker = code;
  }
  if (status) {
    status.dataset.mcelRuntimeStatus = "runtime-failed";
    status.textContent = `Contract Workbench runtime failed: ${code}.`;
  }
  globalThis.__MCEL_CONTRACT_WORKBENCH_BLOCKER__ = Object.freeze({code, message: String(error?.message || "")});
}

if (!globalThis.MCEL || typeof globalThis.MCEL.mountApplicationPackage !== "function") {
  reportFailure(Object.assign(new Error("MCEL.mountApplicationPackage is unavailable."), {
    code: "MCEL_APPLICATION_RUNTIME_UNAVAILABLE"
  }));
} else {
  try {
    const injected = globalThis.__MCEL_APPLICATION_PACKAGE_MOUNT_OPTIONS__?.[requiredMcelApplicationRuntime.appId] || {};
    await globalThis.MCEL.mountApplicationPackage({
      ...injected,
      appId: requiredMcelApplicationRuntime.appId,
      manifestUrl: requiredMcelApplicationRuntime.manifestUrl,
      root
    });
  } catch (error) {
    reportFailure(error);
  }
}
