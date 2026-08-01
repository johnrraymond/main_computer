const root = document.querySelector("#contract-counter-app");
const status = root?.querySelector("[data-mcel-runtime-status]");

export const requiredMcelApplicationRuntime = Object.freeze({
  packageSchema: "mcel.application-package.v1",
  appId: "contract-counter",
  manifestUrl: "../mcel.runtime.json",
  mountMethod: "MCEL.mountApplicationPackage",
  requiredBehavior: Object.freeze([
    "resolve the validated browser-safe package projection",
    "load declared contracts through the SCM-controlled application runtime",
    "project committed state into semantic nodes",
    "bind semantic controls to declared intents",
    "display only committed or refused operation receipts"
  ])
});

if (!globalThis.MCEL || typeof globalThis.MCEL.mountApplicationPackage !== "function") {
  if (root) root.dataset.mcelRuntimeStatus = "unsupported";
  if (status) {
    status.dataset.mcelRuntimeStatus = "unsupported";
    status.textContent = "MCEL.mountApplicationPackage is unavailable in this browser host.";
  }
} else {
  await globalThis.MCEL.mountApplicationPackage({
    appId: requiredMcelApplicationRuntime.appId,
    manifestUrl: requiredMcelApplicationRuntime.manifestUrl,
    root
  });
}
