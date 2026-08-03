const hostRoot = document.querySelector("#mcel-package-host");
const status = hostRoot?.querySelector("[data-mcel-host-status]");
const content = hostRoot?.querySelector("[data-mcel-package-content]");
const query = new URLSearchParams(location.search);
const observationMode = query.get("observation") === "1";

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

function clonePlain(value) {
  if (value === undefined) return undefined;
  return JSON.parse(JSON.stringify(value));
}

function abortError(reason = "aborted") {
  if (typeof DOMException === "function") return new DOMException(String(reason), "AbortError");
  const error = new Error(String(reason));
  error.name = "AbortError";
  return error;
}

function delay(milliseconds, signal, ignoreAbort = false) {
  const duration = Math.max(0, Number(milliseconds) || 0);
  if (!duration) {
    if (signal?.aborted && !ignoreAbort) return Promise.reject(abortError(signal.reason));
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback) => {
      if (settled) return;
      settled = true;
      if (signal && onAbort) signal.removeEventListener("abort", onAbort);
      callback();
    };
    const timer = setTimeout(() => finish(resolve), duration);
    const onAbort = ignoreAbort ? null : () => {
      clearTimeout(timer);
      finish(() => reject(abortError(signal?.reason)));
    };
    if (signal && onAbort) {
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, {once: true});
    }
  });
}

function createObservationCapabilityHarness() {
  const quotePlans = new Map();
  const requests = [];

  function enqueueQuotePlan(contractId, plan) {
    const key = String(contractId || "");
    if (!key || !Array.isArray(plan)) throw new Error("A quote plan requires a contract id and event steps.");
    const queue = quotePlans.get(key) || [];
    queue.push(clonePlain(plan));
    quotePlans.set(key, queue);
  }

  function enqueueQuotePlans(plans = {}) {
    Object.entries(plans || {}).forEach(([contractId, queue]) => {
      if (!Array.isArray(queue)) throw new Error(`Quote plan queue for ${contractId} must be an array.`);
      queue.forEach((plan) => enqueueQuotePlan(contractId, plan));
    });
  }

  function reset() {
    quotePlans.clear();
    requests.splice(0, requests.length);
  }

  const provider = Object.freeze({
    async *requestQuote(request = {}, context = {}) {
      const contractId = String(request.contractId || "");
      const queue = quotePlans.get(contractId) || [];
      const plan = queue.shift() || [
        {delayMs: 0, event: {type: "quote.started", expected: 1}},
        {delayMs: 10, event: {type: "quote.received", report: {amount: 100, source: "default-observation-provider"}}}
      ];
      if (queue.length) quotePlans.set(contractId, queue);
      else quotePlans.delete(contractId);
      requests.push({contractId, request: clonePlain(request), startedAt: new Date().toISOString()});
      for (const step of plan) {
        const ignoreAbort = step?.ignoreAbort === true;
        await delay(step?.delayMs, context?.signal, ignoreAbort);
        if (context?.signal?.aborted && !ignoreAbort) throw abortError(context.signal.reason);
        yield clonePlain(step?.event);
      }
    }
  });

  return Object.freeze({
    provider,
    enqueueQuotePlan,
    enqueueQuotePlans,
    reset,
    requests() {
      return clonePlain(requests);
    }
  });
}

async function importProjectedContract(manifestUrl, entry, label) {
  if (!entry?.path || !entry?.export) throw new Error(`Projected ${label} contract is not declared.`);
  const namespace = await import(new URL(entry.path, manifestUrl).href);
  const value = namespace?.[entry.export];
  if (!value) throw new Error(`Projected ${label} contract does not export ${entry.export}.`);
  return value;
}

async function readProjectedRoot(record, manifest) {
  const response = await fetch(absoluteUrl(record.runtimeProjection.documentUrl), {cache: "no-store"});
  if (!response.ok) throw new Error(`Could not load projected document: HTTP ${response.status}.`);
  const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
  const sourceRoot = parsed.querySelector(manifest.surface.rootSelector);
  if (!sourceRoot) throw new Error(`Projected document is missing ${manifest.surface.rootSelector}.`);
  return document.importNode(sourceRoot, true);
}

function appendProjectedRoot(root, replace = false) {
  if (!content) throw new Error("MCEL package host content root is unavailable.");
  if (replace) content.replaceChildren(root);
  else content.appendChild(root);
  return root;
}

async function loadPackage() {
  const appId = query.get("app") || "";
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

  const acceptance = manifest.modules.acceptance
    ? await importProjectedContract(manifestUrl, manifest.modules.acceptance, "acceptance")
    : null;

  const observationHarness = observationMode && appId === "contract-workbench"
    ? createObservationCapabilityHarness()
    : null;
  const mountOptions = observationHarness
    ? {capabilities: {quotes: observationHarness.provider}}
    : {};
  globalThis.__MCEL_APPLICATION_PACKAGE_MOUNT_OPTIONS__ = Object.freeze({
    [appId]: Object.freeze({...mountOptions})
  });

  const root = appendProjectedRoot(await readProjectedRoot(record, manifest), true);
  root.dataset.mcelInstanceRoot = "primary";
  await import(absoluteUrl(record.runtimeProjection.scriptUrl));
  const mount = requireAuthority("MCEL", "applicationPackageMount").applicationPackageMount(root);
  if (!mount) throw new Error(`MCEL application ${appId} did not produce an active package mount.`);

  const isolatedMounts = new Set();
  let nextInstance = 1;

  async function createIsolatedMount(options = {}) {
    const instanceId = String(options.instanceId || `${appId}-browser-instance-${nextInstance++}`);
    const isolatedRoot = await readProjectedRoot(record, manifest);
    isolatedRoot.dataset.mcelInstanceRoot = instanceId;
    if (isolatedRoot.id) isolatedRoot.id = `${isolatedRoot.id}-${instanceId}`;
    appendProjectedRoot(isolatedRoot, false);
    const isolated = await requireAuthority("MCEL", "mountApplicationPackage").mountApplicationPackage({
      appId,
      manifestUrl,
      root: isolatedRoot,
      instanceId,
      state: clonePlain(options.state || {}),
      localState: clonePlain(options.localState || {}),
      provisionalState: clonePlain(options.provisionalState || {}),
      capabilities: options.capabilities || mountOptions.capabilities || {}
    });
    isolatedMounts.add(isolated);
    return isolated;
  }

  function disposeIsolatedMount(isolated) {
    if (!isolated || !isolatedMounts.has(isolated)) return false;
    isolated.unmount();
    isolated.root?.remove?.();
    isolatedMounts.delete(isolated);
    return true;
  }

  const host = {
    schema: "mcel.application-package-host.v2",
    ready: true,
    observationMode,
    appId,
    record,
    manifest,
    root,
    mount,
    acceptance,
    observation: mount.observation,
    observationHarness,
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
      if (operationResult && typeof operationResult.then === "function") {
        return operationResult.then((resolved) => ({operationResult: resolved, observation: this.observe(resolved, request)}));
      }
      return {operationResult, observation: this.observe(operationResult, request)};
    },
    createIsolatedMount,
    disposeIsolatedMount,
    async runBrowserScenarios(request = {}) {
      return requireAuthority("McelApplicationBrowserScenarioRunner", "run").run(this, request);
    },
    dispose() {
      [...isolatedMounts].forEach(disposeIsolatedMount);
      mount.unmount();
      return true;
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
    schema: "mcel.application-package-host.v2",
    ready: false,
    error: detail
  });
});
