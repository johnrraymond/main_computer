const hostRoot = document.querySelector("#mcel-package-host");
const status = hostRoot?.querySelector("[data-mcel-host-status]");
const content = hostRoot?.querySelector("[data-mcel-package-content]");

function setStatus(kind, message) {
  if (!status) return;
  status.dataset.mcelHostStatus = kind;
  status.textContent = message;
  status.hidden = kind === "ready";
}

function requireAuthority(name, method) {
  const authority = globalThis[name];
  if (!authority || (method && typeof authority[method] !== "function")) {
    throw new Error(`${name}${method ? `.${method}` : ""} is unavailable.`);
  }
  return authority;
}

function absoluteUrl(reference) {
  return new URL(String(reference || ""), document.baseURI).href;
}

async function importProjectedDocument(record, manifest) {
  const response = await fetch(absoluteUrl(record.runtimeProjection.documentUrl), {cache: "no-store"});
  if (!response.ok) throw new Error(`Could not load projected document: HTTP ${response.status}.`);
  const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
  const sourceRoot = parsed.querySelector(manifest.surface.rootSelector);
  if (!sourceRoot) throw new Error(`Projected document is missing ${manifest.surface.rootSelector}.`);
  const mountedRoot = document.importNode(sourceRoot, true);
  content.replaceChildren(mountedRoot);
  return mountedRoot;
}

async function loadPackage() {
  const appId = new URLSearchParams(location.search).get("app") || "";
  const catalog = requireAuthority("McelApplicationPackages", "getPackage");
  const record = catalog.getPackage(appId);
  if (!record?.runtimeProjection) throw new Error(`Unknown or unprojected MCEL application package: ${appId || "<empty>"}.`);

  const manifestUrl = absoluteUrl(record.runtimeProjection.manifestUrl);
  const response = await fetch(manifestUrl, {cache: "no-store"});
  if (!response.ok) throw new Error(`Could not load runtime manifest: HTTP ${response.status}.`);
  const manifest = await response.json();

  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = absoluteUrl(record.runtimeProjection.styleUrl);
  stylesheet.dataset.mcelPackageStyle = appId;
  document.head.append(stylesheet);

  const root = await importProjectedDocument(record, manifest);
  await import(absoluteUrl(record.runtimeProjection.scriptUrl));
  const mount = requireAuthority("MCEL", "applicationPackageMount").applicationPackageMount(root);
  if (!mount) throw new Error(`MCEL application ${appId} did not produce an active package mount.`);

  const host = {
    schema: "mcel.application-package-host.v1",
    ready: true,
    appId,
    record,
    manifest,
    root,
    mount,
    dispatch(intentId, payload = {}, options = {}) {
      return mount.dispatch(intentId, payload, options);
    },
    observe(operationResult, request = {}) {
      return requireAuthority("McelApplicationOperationObserver", "observeCommittedOperation")
        .observeCommittedOperation({
          mount,
          operationResult,
          observationContract: mount.observation,
          packageFingerprint: record.fingerprint,
          runtimeProjectionFingerprint: record.runtimeProjection.fingerprint,
          route: `${location.pathname}${location.search}`,
          surfaceLocator: manifest.surface.rootSelector,
          ...request
        });
    },
    dispatchAndObserve(intentId, payload = {}, request = {}) {
      const operationResult = mount.dispatch(intentId, payload, {
        operationId: request.operationId,
        expectedRevision: request.expectedRevision
      });
      const observation = this.observe(operationResult, request);
      return {operationResult, observation};
    }
  };
  globalThis.McelApplicationPackageHost = Object.freeze(host);
  setStatus("ready", `MCEL application ${appId} is ready.`);
  window.dispatchEvent(new CustomEvent("mcel-application-package-host-ready", {detail: {appId}}));
  return host;
}

loadPackage().catch((error) => {
  const detail = error?.code ? `${error.code}: ${error.message}` : String(error?.stack || error);
  setStatus("error", detail);
  globalThis.McelApplicationPackageHost = Object.freeze({
    schema: "mcel.application-package-host.v1",
    ready: false,
    error: detail
  });
});
