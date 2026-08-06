var McelHostBoundApplicationRuntime = (() => {
  "use strict";

  const SCHEMA = "mcel.host-bound-application-runtime.v1";
  const MOUNT_SCHEMA = "mcel.host-bound-application-mount.v1";
  const HOST_BOUND_MODE = "host-bound";
  const mounts = new Map();

  function safeString(value) {
    return String(value === undefined || value === null ? "" : value).trim();
  }

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

  function absoluteUrl(path) {
    const normalized = safeString(path).replace(/^\/+/, "");
    if (!normalized) throw new Error("Host-bound MCEL asset URL is empty.");
    const rawOrigin = safeString(globalThis.location?.origin);
    const origin = rawOrigin && rawOrigin !== "null" ? rawOrigin : "http://127.0.0.1";
    return new URL(normalized, `${origin}/`).href;
  }

  function packageCatalogAuthority(candidate) {
    const catalog = candidate || globalThis.McelApplicationPackages;
    if (
      !catalog
      || typeof catalog.getPackage !== "function"
      || typeof catalog.listPackages !== "function"
    ) {
      throw new Error("McelApplicationPackages is unavailable.");
    }
    return catalog;
  }

  function setRootStatus(root, status, detail = "") {
    if (!root?.dataset) return;
    root.dataset.mcelHostBoundStatus = safeString(status) || "unknown";
    if (detail) root.dataset.mcelHostBoundDetail = safeString(detail);
    else delete root.dataset.mcelHostBoundDetail;
  }

  function dispatchLifecycle(root, name, detail) {
    if (!root || typeof root.dispatchEvent !== "function") return;
    if (typeof globalThis.CustomEvent !== "function") return;
    root.dispatchEvent(new CustomEvent(name, {bubbles: true, detail}));
  }

  async function readManifest(record, request) {
    if (request.manifest && typeof request.manifest === "object") {
      return clonePlain(request.manifest);
    }
    const fetcher = request.fetcher || globalThis.fetch;
    if (typeof fetcher !== "function") throw new Error("Host-bound MCEL manifest fetch is unavailable.");
    const manifestUrl = absoluteUrl(record.runtimeProjection.manifestUrl);
    const response = await fetcher(manifestUrl, {cache: "no-store"});
    if (!response?.ok) {
      throw new Error(`Could not load host-bound MCEL manifest: HTTP ${response?.status || 0}.`);
    }
    const manifest = await response.json();
    if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
      throw new Error("Host-bound MCEL manifest must be a JSON object.");
    }
    return manifest;
  }

  function verifyManifest(record, catalog, manifest) {
    const projection = record.runtimeProjection || {};
    if (projection.mountMode !== HOST_BOUND_MODE) {
      throw new Error(`MCEL application ${record.appId} is not host-bound.`);
    }
    if (manifest.schema !== "mcel.application-runtime-projection.v1") {
      throw new Error("Unsupported host-bound MCEL runtime manifest schema.");
    }
    if (safeString(manifest.appId) !== safeString(record.appId)) {
      throw new Error("Host-bound MCEL manifest app identity mismatch.");
    }
    if (safeString(manifest.source?.packageFingerprint) !== safeString(record.fingerprint)) {
      throw new Error("Host-bound MCEL package fingerprint mismatch.");
    }
    if (safeString(manifest.source?.catalogFingerprint) !== safeString(catalog.catalogFingerprint)) {
      throw new Error("Host-bound MCEL catalog fingerprint mismatch.");
    }
    if (safeString(manifest.projection?.fingerprint) !== safeString(projection.fingerprint)) {
      throw new Error("Host-bound MCEL projection fingerprint mismatch.");
    }
    if (safeString(manifest.runtime?.mode) !== HOST_BOUND_MODE) {
      throw new Error("Host-bound MCEL manifest runtime mode mismatch.");
    }
    for (const field of ["route", "rootSelector", "facade"]) {
      if (!safeString(manifest.runtime?.[field])) {
        throw new Error(`Host-bound MCEL manifest runtime.${field} is required.`);
      }
    }
    if (safeString(manifest.runtime.route) !== safeString(projection.hostRoute)) {
      throw new Error("Host-bound MCEL route mismatch.");
    }
    if (safeString(manifest.runtime.rootSelector) !== safeString(projection.rootSelector)) {
      throw new Error("Host-bound MCEL root selector mismatch.");
    }
    if (safeString(manifest.runtime.facade) !== safeString(projection.runtimeFacade)) {
      throw new Error("Host-bound MCEL runtime facade mismatch.");
    }
    return manifest;
  }

  async function loadDeclaredModule(manifestUrl, entry, loader, label) {
    if (!entry?.path || !entry?.export) {
      throw new Error(`Host-bound MCEL ${label} module is not declared.`);
    }
    const moduleUrl = new URL(entry.path, manifestUrl).href;
    const namespace = await loader(moduleUrl, entry, label);
    const value = namespace?.[entry.export];
    if (!value) throw new Error(`Host-bound MCEL ${label} export is unavailable: ${entry.export}.`);
    return value;
  }

  function verifyModules(appId, modules) {
    for (const label of ["domain", "adapter", "surface", "layout", "observation", "acceptance"]) {
      if (safeString(modules[label]?.appId) !== appId) {
        throw new Error(`Host-bound MCEL ${label} identity mismatch.`);
      }
    }
    if (typeof modules.adapter.invoke !== "function") {
      throw new Error("Host-bound MCEL adapter must expose invoke().");
    }
    if (!modules.adapter.bindings || typeof modules.adapter.bindings !== "object") {
      throw new Error("Host-bound MCEL adapter bindings are unavailable.");
    }
    return modules;
  }

  function resolveRoot(manifest, request) {
    const root = request.root || globalThis.document?.querySelector?.(manifest.runtime.rootSelector);
    if (!root) {
      throw new Error(`Host-bound MCEL root is unavailable: ${manifest.runtime.rootSelector}.`);
    }
    if (typeof root.matches === "function" && !root.matches(manifest.runtime.rootSelector)) {
      throw new Error("Host-bound MCEL root does not match its declared selector.");
    }
    return root;
  }

  function resolveFacade(manifest) {
    const facade = globalThis[manifest.runtime.facade];
    if (!facade || typeof facade !== "object") {
      throw new Error(`Host-bound MCEL runtime facade is unavailable: ${manifest.runtime.facade}.`);
    }
    return facade;
  }

  async function mountApplication(request = {}) {
    const catalog = packageCatalogAuthority(request.packageCatalog);
    const appId = safeString(request.appId || request.packageRecord?.appId);
    if (!appId) throw new Error("Host-bound MCEL appId is required.");
    if (mounts.has(appId)) return mounts.get(appId);

    const record = request.packageRecord || catalog.getPackage(appId);
    if (!record?.runtimeProjection) {
      throw new Error(`Unknown host-bound MCEL application package: ${appId}.`);
    }

    const manifestUrl = absoluteUrl(record.runtimeProjection.manifestUrl);
    const manifest = verifyManifest(record, catalog, await readManifest(record, request));
    const root = resolveRoot(manifest, request);
    resolveFacade(manifest);
    setRootStatus(root, "loading");
    root.dataset.mcelHostBoundApp = appId;
    root.dataset.mcelHostBoundProjection = safeString(record.runtimeProjection.fingerprint);

    const loader = request.moduleLoader || ((url) => import(url));
    const [domain, intents, adapter, surface, layout, observation, acceptance] = await Promise.all([
      loadDeclaredModule(manifestUrl, manifest.modules.domain, loader, "domain"),
      loadDeclaredModule(manifestUrl, manifest.modules.intents, loader, "intents"),
      loadDeclaredModule(manifestUrl, manifest.modules.adapter, loader, "adapter"),
      loadDeclaredModule(manifestUrl, manifest.modules.surface, loader, "surface"),
      loadDeclaredModule(manifestUrl, manifest.modules.layout, loader, "layout"),
      loadDeclaredModule(manifestUrl, manifest.modules.observation, loader, "observation"),
      loadDeclaredModule(manifestUrl, manifest.modules.acceptance, loader, "acceptance")
    ]);
    const modules = verifyModules(appId, {domain, intents, adapter, surface, layout, observation, acceptance});
    let active = true;

    const mount = Object.freeze({
      schema: MOUNT_SCHEMA,
      kind: "host-bound",
      appId,
      root,
      record: deepFreeze(clonePlain(record)),
      manifest: deepFreeze(clonePlain(manifest)),
      modules: Object.freeze(modules),
      get active() {
        return active;
      },
      invoke(intentName, ...args) {
        if (!active) throw new Error(`Host-bound MCEL application ${appId} is unmounted.`);
        const normalizedIntent = safeString(intentName);
        if (!normalizedIntent) throw new Error("Host-bound MCEL intent name is required.");
        dispatchLifecycle(root, "mcel:host-bound-intent", {
          appId,
          intentName: normalizedIntent,
          phase: "dispatch"
        });
        return modules.adapter.invoke(normalizedIntent, ...args);
      },
      unmount() {
        if (!active) return false;
        active = false;
        mounts.delete(appId);
        setRootStatus(root, "unmounted");
        dispatchLifecycle(root, "mcel:host-bound-unmounted", {appId});
        return true;
      }
    });

    mounts.set(appId, mount);
    setRootStatus(root, "mounted");
    dispatchLifecycle(root, "mcel:host-bound-mounted", {
      appId,
      fingerprint: record.runtimeProjection.fingerprint,
      intentCount: Object.keys(adapter.bindings).length
    });
    return mount;
  }

  function getMount(appId) {
    return mounts.get(safeString(appId)) || null;
  }

  function listMounts() {
    return [...mounts.values()];
  }

  async function autoMount(request = {}) {
    const catalog = packageCatalogAuthority(request.packageCatalog);
    const records = catalog.listPackages()
      .filter((record) => record?.runtimeProjection?.mountMode === HOST_BOUND_MODE)
      .sort((left, right) => safeString(left.appId).localeCompare(safeString(right.appId)));
    const mounted = [];
    for (const record of records) {
      const selector = safeString(record.runtimeProjection.rootSelector);
      const root = selector ? globalThis.document?.querySelector?.(selector) : null;
      if (!root) continue;
      try {
        mounted.push(await mountApplication({...request, appId: record.appId, packageRecord: record, root}));
      } catch (error) {
        setRootStatus(root, "error", error?.message || String(error));
        console.warn(`Host-bound MCEL mount failed for ${record.appId}:`, error);
      }
    }
    return mounted;
  }

  return Object.freeze({
    schema: SCHEMA,
    mountApplication,
    autoMount,
    getMount,
    listMounts
  });
})();

if (typeof window !== "undefined") {
  window.McelHostBoundApplicationRuntime = McelHostBoundApplicationRuntime;
  window.McelHostBoundApplicationsReady = Promise.resolve()
    .then(() => McelHostBoundApplicationRuntime.autoMount())
    .then((mounts) => Object.freeze([...mounts]));
}
