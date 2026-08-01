var McelApplicationRuntime = (() => {
  const CONTRACT_VERSION = "mcel.application-runtime.v1";
  const RECEIPT_SCHEMA = "mcel.application-operation-receipt.v1";
  const definitions = new Map();
  const instanceRecords = new WeakMap();
  let nextInstanceId = 1;

  function scmAuthority() {
    if (typeof McelLabScm !== "undefined") return McelLabScm;
    if (typeof globalThis !== "undefined" && globalThis.McelLabScm) return globalThis.McelLabScm;
    throw new Error("MCEL application runtime requires McelLabScm.");
  }

  function safeString(value) {
    return String(value || "").trim();
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function cloneValue(value, seen = new Map()) {
    if (!value || typeof value !== "object") return value;
    if (seen.has(value)) return seen.get(value);
    if (Array.isArray(value)) {
      const copy = [];
      seen.set(value, copy);
      value.forEach((item, index) => {
        copy[index] = cloneValue(item, seen);
      });
      return copy;
    }
    const copy = {};
    seen.set(value, copy);
    Object.keys(value).forEach((key) => {
      copy[key] = cloneValue(value[key], seen);
    });
    return copy;
  }

  function deepFreeze(value, seen = new Set()) {
    if (!value || (typeof value !== "object" && typeof value !== "function")) return value;
    if (seen.has(value)) return value;
    seen.add(value);
    Object.keys(value).forEach((key) => deepFreeze(value[key], seen));
    return Object.freeze(value);
  }

  function stableJson(value) {
    if (value === undefined) return "undefined";
    if (!value || typeof value !== "object") return JSON.stringify(value);
    if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }

  function valuesEqual(left, right) {
    return stableJson(left) === stableJson(right);
  }

  function now() {
    try {
      return new Date().toISOString();
    } catch (_error) {
      return "unknown-time";
    }
  }

  function violation(code, details = {}) {
    return deepFreeze({
      kind: "mcel-application-runtime-violation",
      contractVersion: CONTRACT_VERSION,
      generatedAt: now(),
      ok: false,
      code,
      message: details.message || code,
      ...cloneValue(details)
    });
  }

  function throwViolation(code, details = {}) {
    const entry = violation(code, details);
    const error = new Error(entry.message);
    error.name = "McelApplicationRuntimeError";
    error.violation = entry;
    throw error;
  }

  function validAppId(value) {
    return /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(safeString(value));
  }

  function normalizeStatePath(value) {
    const path = safeString(value);
    if (!/^state\.[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*$/.test(path)) return "";
    if (path.includes("__proto__") || path.includes(".prototype") || path.includes(".constructor")) return "";
    return path;
  }

  function pathParts(path) {
    return normalizeStatePath(path).split(".").slice(1);
  }

  function readObjectPath(object, path) {
    const parts = pathParts(path);
    let cursor = object;
    for (const part of parts) {
      if (cursor == null) return undefined;
      cursor = cursor[part];
    }
    return cloneValue(cursor);
  }

  function writeObjectPath(object, path, value) {
    const parts = pathParts(path);
    let cursor = object;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const part = parts[index];
      if (!isPlainObject(cursor[part])) cursor[part] = {};
      cursor = cursor[part];
    }
    cursor[parts[parts.length - 1]] = cloneValue(value);
  }

  function pathCovered(path, declarations) {
    return declarations.some((declared) => path === declared || path.startsWith(`${declared}.`));
  }

  function changedPaths(before, after, prefix = "state") {
    if (valuesEqual(before, after)) return [];
    if (
      before === null
      || after === null
      || typeof before !== "object"
      || typeof after !== "object"
      || Array.isArray(before)
      || Array.isArray(after)
    ) {
      return [prefix];
    }
    const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
    const changed = [];
    [...keys].sort().forEach((key) => {
      changed.push(...changedPaths(before[key], after[key], `${prefix}.${key}`));
    });
    return changed;
  }

  function stateFromContext(ctx, statePaths) {
    const state = {};
    statePaths.forEach((path) => writeObjectPath(state, path, ctx.get(path)));
    return state;
  }

  function normalizeIntentEntries(intents) {
    if (!isPlainObject(intents)) {
      throwViolation("APPLICATION_INTENTS_REQUIRED", {
        phase: "define-application",
        message: "MCEL application definitions require an intents object."
      });
    }

    const byKey = new Map();
    const byId = new Map();
    Object.keys(intents).sort().forEach((key) => {
      const intent = intents[key];
      const intentId = safeString(intent?.id || key);
      if (!intentId || !/^[A-Za-z][A-Za-z0-9_.:-]*$/.test(intentId) || !isPlainObject(intent)) {
        throwViolation("APPLICATION_INTENT_INVALID", {
          phase: "define-application",
          intentKey: key,
          intentId,
          message: `Application intent ${key} is invalid.`
        });
      }
      if (byId.has(intentId)) {
        throwViolation("APPLICATION_INTENT_DUPLICATE", {
          phase: "define-application",
          intentId,
          message: `Application intent id ${intentId} is duplicated.`
        });
      }

      const reads = Array.isArray(intent.reads)
        ? intent.reads.map(normalizeStatePath).filter(Boolean)
        : [];
      const writes = Array.isArray(intent.writes)
        ? intent.writes.map(normalizeStatePath).filter(Boolean)
        : [];
      if (intent.kind !== "prohibited" && (!reads.length || !writes.length)) {
        throwViolation("APPLICATION_INTENT_PATHS_REQUIRED", {
          phase: "define-application",
          intentId,
          message: `Executable application intent ${intentId} requires declared state reads and writes.`
        });
      }

      const entry = deepFreeze({
        key,
        id: intentId,
        kind: safeString(intent.kind || "mutation"),
        risk: safeString(intent.risk),
        reason: safeString(intent.reason),
        reads,
        writes,
        contract: intent
      });
      byKey.set(key, entry);
      byId.set(intentId, entry);
    });
    return {byKey, byId};
  }

  function validateDefinition(spec) {
    const appId = safeString(spec?.appId || spec?.domain?.appId || spec?.adapter?.appId);
    if (!validAppId(appId)) {
      throwViolation("APPLICATION_ID_INVALID", {
        phase: "define-application",
        appId,
        message: `Invalid MCEL application id ${appId || "<empty>"}.`
      });
    }
    if (!isPlainObject(spec?.domain) || !isPlainObject(spec.domain.initialState)) {
      throwViolation("APPLICATION_DOMAIN_REQUIRED", {
        phase: "define-application",
        appId,
        message: `Application ${appId} requires a domain with initialState.`
      });
    }
    if (safeString(spec.domain.appId) !== appId || safeString(spec?.adapter?.appId) !== appId) {
      throwViolation("APPLICATION_IDENTITY_MISMATCH", {
        phase: "define-application",
        appId,
        domainAppId: safeString(spec.domain.appId),
        adapterAppId: safeString(spec?.adapter?.appId),
        message: `Application ${appId} domain and adapter identities must agree.`
      });
    }
    if (
      !isPlainObject(spec.adapter)
      || typeof spec.adapter.preflight !== "function"
      || typeof spec.adapter.transition !== "function"
      || typeof spec.adapter.validateEffects !== "function"
    ) {
      throwViolation("APPLICATION_ADAPTER_INVALID", {
        phase: "define-application",
        appId,
        message: `Application ${appId} requires preflight, transition, and validateEffects adapter functions.`
      });
    }
    const stateKeys = Object.keys(spec.domain.initialState).sort();
    if (!stateKeys.length) {
      throwViolation("APPLICATION_STATE_EMPTY", {
        phase: "define-application",
        appId,
        message: `Application ${appId} must declare canonical state.`
      });
    }
    const invariantReads = Array.isArray(spec.domain.invariantReads)
      ? spec.domain.invariantReads.map(normalizeStatePath).filter(Boolean)
      : stateKeys.map((key) => `state.${key}`);
    const invariants = Array.isArray(spec.domain.invariants) ? spec.domain.invariants.slice() : [];
    invariants.forEach((invariant, index) => {
      if (!safeString(invariant?.id) || typeof invariant?.check !== "function") {
        throwViolation("APPLICATION_INVARIANT_INVALID", {
          phase: "define-application",
          appId,
          invariantIndex: index,
          message: `Application ${appId} has an invalid invariant at index ${index}.`
        });
      }
    });

    return {
      appId,
      stateKeys,
      invariantReads,
      invariants,
      intents: normalizeIntentEntries(spec.intents)
    };
  }

  function resolveIntent(definition, intentId) {
    const id = safeString(intentId);
    return definition.intentById.get(id) || definition.intentByKey.get(id) || null;
  }

  function buildScmTransition(definitionDraft, intent) {
    const readPaths = [...new Set([...intent.reads, ...definitionDraft.invariantReads])].sort();
    const writePaths = [...new Set(intent.writes)].sort();

    return {
      reads: readPaths,
      writes: writePaths,

      pre(ctx, input) {
        const state = stateFromContext(ctx, readPaths);
        const result = definitionDraft.adapter.preflight({
          intentId: intent.id,
          input: cloneValue(input || {}),
          state: cloneValue(state)
        });
        if (!result || result.ok !== true) {
          const code = safeString(result?.code || "APPLICATION_PREFLIGHT_REFUSED");
          const error = new Error(`Application adapter preflight refused ${intent.id}: ${code}`);
          error.applicationCode = code;
          throw error;
        }
        return true;
      },

      apply(ctx, input) {
        const before = stateFromContext(ctx, readPaths);
        const proposed = definitionDraft.adapter.transition({
          intentId: intent.id,
          input: cloneValue(input || {}),
          state: cloneValue(before)
        });
        if (!isPlainObject(proposed)) {
          throw new Error(`Application adapter transition ${intent.id} must return a state object.`);
        }

        const undeclared = changedPaths(before, proposed).filter((path) => !pathCovered(path, writePaths));
        if (undeclared.length) {
          throw new Error(
            `Application adapter transition ${intent.id} changed undeclared state paths: ${undeclared.join(", ")}.`
          );
        }

        writePaths.forEach((path) => ctx.set(path, readObjectPath(proposed, path)));
        return {
          before,
          proposed: cloneValue(proposed)
        };
      },

      post(ctx, input, result) {
        const after = stateFromContext(ctx, readPaths);
        for (const invariant of definitionDraft.invariants) {
          if (invariant.check(cloneValue(after)) !== true) return false;
        }
        return definitionDraft.adapter.validateEffects({
          intentId: intent.id,
          input: cloneValue(input || {}),
          before: cloneValue(result?.before || {}),
          after: cloneValue(after)
        }) === true;
      }
    };
  }

  function defineApplication(spec, options = {}) {
    const checked = validateDefinition(spec);
    if (definitions.has(checked.appId) && options?.replace !== true) {
      throwViolation("APPLICATION_DUPLICATE_DEFINITION", {
        phase: "define-application",
        appId: checked.appId,
        message: `MCEL application ${checked.appId} is already defined.`
      });
    }

    const definitionDraft = {
      appId: checked.appId,
      schema: safeString(spec.schema || "mcel.application-definition.v1"),
      version: safeString(spec.version || "1.0.0"),
      domain: spec.domain,
      adapter: spec.adapter,
      intents: spec.intents,
      invariantReads: checked.invariantReads,
      invariants: checked.invariants,
      intentByKey: checked.intents.byKey,
      intentById: checked.intents.byId
    };
    const transitions = {};
    [...checked.intents.byId.values()].forEach((intent) => {
      if (intent.kind !== "prohibited") {
        transitions[intent.id] = buildScmTransition(definitionDraft, intent);
      }
    });

    const componentName = `application.${checked.appId}`;
    const scm = scmAuthority();
    const scmDefinition = scm.defineComponent(
      componentName,
      {
        version: definitionDraft.version,
        contract: `mcel.application.${checked.appId}.v1`,
        owns: {
          state: checked.stateKeys
        },
        state: cloneValue(spec.domain.initialState),
        transitions
      },
      {replace: options?.replace === true}
    );

    const definition = Object.freeze({
      kind: "mcel-application-definition",
      contractVersion: CONTRACT_VERSION,
      appId: checked.appId,
      schema: definitionDraft.schema,
      version: definitionDraft.version,
      componentName,
      domain: spec.domain,
      intents: spec.intents,
      adapter: spec.adapter,
      scmDefinition,
      intentIds: Object.freeze([...checked.intents.byId.keys()].sort())
    });
    definitionDraft.publicDefinition = definition;
    definitions.set(checked.appId, {definition, draft: definitionDraft});
    return definition;
  }

  function applicationDefinition(appId) {
    return definitions.get(safeString(appId))?.definition || null;
  }

  function listApplicationDefinitions() {
    return [...definitions.values()]
      .map(({definition}) => ({
        kind: "mcel-application-definition-summary",
        appId: definition.appId,
        version: definition.version,
        componentName: definition.componentName,
        intentIds: definition.intentIds.slice()
      }))
      .sort((left, right) => left.appId.localeCompare(right.appId));
  }

  function createApplicationInstance(definitionOrAppId, options = {}) {
    const definition = typeof definitionOrAppId === "string"
      ? applicationDefinition(definitionOrAppId)
      : definitionOrAppId;
    const appId = safeString(definition?.appId);
    const stored = definitions.get(appId);
    if (!definition || stored?.definition !== definition) {
      throwViolation("APPLICATION_DEFINITION_UNKNOWN", {
        phase: "create-instance",
        appId,
        message: `Unknown MCEL application definition ${appId || "<empty>"}.`
      });
    }

    const scm = scmAuthority();
    const scmInstance = scm.createComponentInstance(definition.componentName, {
      id: options.id || `mcel-app-${appId}-${nextInstanceId++}`,
      state: cloneValue(options.state || {})
    });
    const receipts = [];
    const instance = {
      kind: "mcel-application-instance",
      contractVersion: CONTRACT_VERSION,
      id: scmInstance.id,
      appId,
      definition,
      readState() {
        return readApplicationState(instance);
      },
      createOperation(scope = "application-operation") {
        return createApplicationOperation(instance, scope);
      },
      dispatch(request = {}) {
        return dispatchApplicationIntent(instance, request);
      },
      exportEvidence() {
        return exportApplicationEvidence(instance);
      }
    };
    Object.defineProperties(instance, {
      state: {
        enumerable: true,
        get() {
          return scmInstance.state;
        }
      },
      revision: {
        enumerable: true,
        get() {
          return scmInstance.revision;
        }
      },
      appliedOperationIds: {
        enumerable: true,
        get() {
          return scmInstance.appliedOperationIds;
        }
      },
      receipts: {
        enumerable: true,
        get() {
          return deepFreeze(cloneValue(receipts));
        }
      }
    });
    instanceRecords.set(instance, {scmInstance, receipts, stored});
    return Object.freeze(instance);
  }

  function assertApplicationInstance(instance, phase) {
    const record = instanceRecords.get(instance);
    if (!record) {
      throwViolation("APPLICATION_INSTANCE_INVALID", {
        phase,
        appId: safeString(instance?.appId),
        instanceId: safeString(instance?.id),
        message: "MCEL application operation requires a canonical application instance."
      });
    }
    return record;
  }

  function readApplicationState(instance) {
    assertApplicationInstance(instance, "read-state");
    return instance.state;
  }

  function createApplicationOperation(instance, scope = "application-operation") {
    const record = assertApplicationInstance(instance, "create-operation");
    return scmAuthority().createOperation(record.scmInstance, scope);
  }

  function buildReceipt(instance, request, details) {
    const receipt = deepFreeze({
      schema: RECEIPT_SCHEMA,
      contractVersion: CONTRACT_VERSION,
      generatedAt: now(),
      appId: instance.appId,
      applicationInstanceId: instance.id,
      operationId: safeString(request.operationId),
      intentId: safeString(request.intentId),
      status: details.status,
      ok: details.status === "committed",
      code: safeString(details.code || (details.status === "committed" ? "APPLICATION_OPERATION_COMMITTED" : "APPLICATION_OPERATION_REFUSED")),
      before: {
        revision: details.beforeRevision,
        state: cloneValue(details.beforeState)
      },
      after: {
        revision: details.afterRevision,
        state: cloneValue(details.afterState)
      },
      adapter: cloneValue(details.adapter || {}),
      scm: cloneValue(details.scm || {}),
      violation: cloneValue(details.violation || null)
    });
    const record = assertApplicationInstance(instance, "record-receipt");
    record.receipts.push(receipt);
    return receipt;
  }

  function refusalResult(instance, request, details) {
    const receipt = buildReceipt(instance, request, {
      status: "refused",
      code: details.code,
      beforeRevision: details.beforeRevision,
      beforeState: details.beforeState,
      afterRevision: instance.revision,
      afterState: instance.state,
      adapter: details.adapter,
      scm: details.scm,
      violation: details.violation
    });
    return deepFreeze({
      kind: "mcel-application-operation-result",
      contractVersion: CONTRACT_VERSION,
      ok: false,
      status: "refused",
      code: receipt.code,
      appId: instance.appId,
      operationId: receipt.operationId,
      intentId: receipt.intentId,
      revision: instance.revision,
      state: cloneValue(instance.state),
      receipt
    });
  }

  function dispatchApplicationIntent(instance, request = {}) {
    const record = assertApplicationInstance(instance, "dispatch");
    const operationId = safeString(request.operationId);
    const intentId = safeString(request.intentId);
    const expectedRevision = request.expectedRevision;
    const beforeRevision = instance.revision;
    const beforeState = cloneValue(instance.state);
    const baseDetails = {beforeRevision, beforeState};

    if (
      !operationId
      || operationId.length > 200
      || !Number.isSafeInteger(expectedRevision)
      || expectedRevision < 0
      || !intentId
    ) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_OPERATION_ENVELOPE_REQUIRED"
      });
    }

    const intent = resolveIntent(record.stored.draft, intentId);
    if (!intent) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_INTENT_UNKNOWN"
      });
    }
    if (intent.kind === "prohibited") {
      const preflight = record.stored.draft.adapter.preflight({
        intentId: intent.id,
        input: {...cloneValue(request.payload || {}), expectedRevision},
        state: cloneValue(beforeState)
      });
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(preflight?.code || "APPLICATION_INTENT_PROHIBITED"),
        adapter: {preflight: cloneValue(preflight || {})}
      });
    }
    if (instance.appliedOperationIds.includes(operationId)) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "SCM_DUPLICATE_OPERATION"
      });
    }
    if (expectedRevision !== instance.revision) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "SCM_STALE_REVISION"
      });
    }

    const input = {...cloneValue(request.payload || {}), expectedRevision};
    const preflight = record.stored.draft.adapter.preflight({
      intentId: intent.id,
      input: cloneValue(input),
      state: cloneValue(beforeState)
    });
    if (!preflight || preflight.ok !== true) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(preflight?.code || "APPLICATION_PREFLIGHT_REFUSED"),
        adapter: {preflight: cloneValue(preflight || {})}
      });
    }

    try {
      const scm = scmAuthority();
      const scmResult = scm.transition(
        record.scmInstance,
        intent.id,
        input,
        {operationId, expectedRevision}
      );
      const receipt = buildReceipt(instance, request, {
        status: "committed",
        beforeRevision,
        beforeState,
        afterRevision: instance.revision,
        afterState: instance.state,
        adapter: {
          adapterId: safeString(record.stored.draft.adapter.adapterId),
          preflight: cloneValue(preflight),
          effectsValidated: true
        },
        scm: {
          componentName: scmResult.componentName,
          transitionName: scmResult.transitionName,
          previousRevision: scmResult.previousRevision,
          revision: scmResult.revision,
          evidence: cloneValue(scmResult.evidence)
        }
      });
      return deepFreeze({
        kind: "mcel-application-operation-result",
        contractVersion: CONTRACT_VERSION,
        ok: true,
        status: "committed",
        code: receipt.code,
        appId: instance.appId,
        operationId,
        intentId: intent.id,
        revision: instance.revision,
        state: cloneValue(instance.state),
        receipt
      });
    } catch (error) {
      const scmViolation = error?.violation || null;
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(scmViolation?.code || error?.applicationCode || "APPLICATION_OPERATION_FAILED"),
        adapter: {preflight: cloneValue(preflight)},
        scm: scmViolation ? {violation: cloneValue(scmViolation)} : {},
        violation: scmViolation || {
          name: safeString(error?.name || "Error"),
          message: safeString(error?.message || String(error))
        }
      });
    }
  }

  function exportApplicationEvidence(instance) {
    const record = assertApplicationInstance(instance, "export-evidence");
    return deepFreeze({
      kind: "mcel-application-evidence-packet",
      contractVersion: CONTRACT_VERSION,
      generatedAt: now(),
      appId: instance.appId,
      applicationInstanceId: instance.id,
      revision: instance.revision,
      state: cloneValue(instance.state),
      receipts: cloneValue(record.receipts),
      scm: scmAuthority().exportEvidence(record.scmInstance)
    });
  }

  const activeMounts = new WeakMap();
  let nextMountOperationId = 1;

  function packageCatalogAuthority(candidate) {
    const catalog = candidate
      || (typeof McelApplicationPackages !== "undefined" ? McelApplicationPackages : null)
      || (typeof globalThis !== "undefined" ? globalThis.McelApplicationPackages : null);
    if (!catalog || typeof catalog.getPackage !== "function") {
      throwViolation("APPLICATION_PACKAGE_CATALOG_REQUIRED", {
        phase: "mount-package",
        message: "MCEL package mounting requires the browser application-package catalog."
      });
    }
    return catalog;
  }

  function baseUrl() {
    const value = typeof document !== "undefined" && document.baseURI
      ? document.baseURI
      : "http://mcel.invalid/";
    return String(value);
  }

  function resolveBrowserUrl(reference, parent) {
    try {
      return new URL(String(reference || ""), parent || baseUrl()).href;
    } catch (_error) {
      throwViolation("APPLICATION_PACKAGE_URL_INVALID", {
        phase: "mount-package",
        reference: safeString(reference),
        message: `Invalid MCEL application package URL: ${safeString(reference) || "<empty>"}.`
      });
    }
  }

  async function readRuntimeManifest(url, options) {
    if (isPlainObject(options?.manifest)) return cloneValue(options.manifest);
    const fetcher = options?.fetcher
      || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
    if (!fetcher) {
      throwViolation("APPLICATION_PACKAGE_FETCH_REQUIRED", {
        phase: "mount-package",
        manifestUrl: url,
        message: "MCEL package mounting requires fetch or an injected manifest."
      });
    }
    let response;
    try {
      response = await fetcher(url);
    } catch (error) {
      throwViolation("APPLICATION_PACKAGE_MANIFEST_FETCH_FAILED", {
        phase: "mount-package",
        manifestUrl: url,
        message: safeString(error?.message || error)
      });
    }
    if (!response || response.ok === false) {
      throwViolation("APPLICATION_PACKAGE_MANIFEST_FETCH_FAILED", {
        phase: "mount-package",
        manifestUrl: url,
        status: response?.status,
        message: `Could not fetch MCEL application runtime manifest: ${url}.`
      });
    }
    try {
      return typeof response.json === "function"
        ? await response.json()
        : JSON.parse(await response.text());
    } catch (error) {
      throwViolation("APPLICATION_PACKAGE_MANIFEST_INVALID", {
        phase: "mount-package",
        manifestUrl: url,
        message: safeString(error?.message || error)
      });
    }
  }

  function verifyRuntimeManifest(packageRecord, catalog, manifest) {
    const projection = packageRecord?.runtimeProjection;
    if (!isPlainObject(manifest) || manifest.schema !== "mcel.application-runtime-projection.v1") {
      throwViolation("APPLICATION_RUNTIME_PROJECTION_SCHEMA_INVALID", {
        phase: "mount-package",
        appId: safeString(packageRecord?.appId),
        message: "MCEL runtime projection manifest schema is invalid."
      });
    }
    const checks = [
      [manifest.appId, packageRecord.appId, "APPLICATION_PACKAGE_IDENTITY_MISMATCH"],
      [manifest?.source?.packageFingerprint, packageRecord.fingerprint, "APPLICATION_PACKAGE_FINGERPRINT_MISMATCH"],
      [manifest?.source?.catalogFingerprint, catalog.catalogFingerprint, "APPLICATION_CATALOG_FINGERPRINT_MISMATCH"],
      [manifest?.projection?.fingerprint, projection?.fingerprint, "APPLICATION_RUNTIME_PROJECTION_FINGERPRINT_MISMATCH"]
    ];
    for (const [actual, expected, code] of checks) {
      if (!actual || actual !== expected) {
        throwViolation(code, {
          phase: "mount-package",
          appId: safeString(packageRecord?.appId),
          expected: safeString(expected),
          actual: safeString(actual),
          message: `${code}: expected ${safeString(expected)}, received ${safeString(actual)}.`
        });
      }
    }
    if (!isPlainObject(manifest.modules) || !safeString(manifest?.surface?.rootSelector)) {
      throwViolation("APPLICATION_RUNTIME_PROJECTION_INCOMPLETE", {
        phase: "mount-package",
        appId: packageRecord.appId,
        message: "MCEL runtime projection requires module and surface declarations."
      });
    }
    return manifest;
  }

  async function loadDeclaredModule(manifestUrl, entry, loader, label) {
    if (!isPlainObject(entry) || !safeString(entry.path) || !safeString(entry.export)) {
      throwViolation("APPLICATION_RUNTIME_MODULE_DECLARATION_INVALID", {
        phase: "mount-package",
        module: label,
        message: `MCEL runtime module ${label} is not declared correctly.`
      });
    }
    const url = resolveBrowserUrl(entry.path, manifestUrl);
    let namespace;
    try {
      namespace = await loader(url, cloneValue(entry));
    } catch (error) {
      throwViolation("APPLICATION_RUNTIME_MODULE_LOAD_FAILED", {
        phase: "mount-package",
        module: label,
        url,
        message: safeString(error?.message || error)
      });
    }
    const value = namespace?.[entry.export];
    if (!value) {
      throwViolation("APPLICATION_RUNTIME_MODULE_EXPORT_MISSING", {
        phase: "mount-package",
        module: label,
        exportName: entry.export,
        url,
        message: `MCEL runtime module ${label} does not export ${entry.export}.`
      });
    }
    return value;
  }

  function defaultModuleLoader(url) {
    return import(url);
  }

  function resolveMountRoot(request, manifest) {
    const root = request?.root
      || (typeof document !== "undefined" && document.querySelector
        ? document.querySelector(manifest.surface.rootSelector)
        : null);
    if (!root || typeof root.querySelectorAll !== "function" || typeof root.getAttribute !== "function") {
      throwViolation("APPLICATION_SURFACE_ROOT_REQUIRED", {
        phase: "mount-package",
        selector: safeString(manifest?.surface?.rootSelector),
        message: "MCEL package mounting requires the declared application surface root."
      });
    }
    if (activeMounts.has(root)) {
      throwViolation("APPLICATION_SURFACE_ALREADY_MOUNTED", {
        phase: "mount-package",
        message: "The MCEL application surface root is already mounted."
      });
    }
    return root;
  }

  function descendantsWithAttribute(root, name) {
    const values = [];
    if (root.getAttribute(name) !== null) values.push(root);
    Array.from(root.querySelectorAll(`[${name}]`) || []).forEach((node) => values.push(node));
    return values;
  }

  function byDeclaredIdentity(root, attribute, identity) {
    return descendantsWithAttribute(root, attribute)
      .filter((node) => safeString(node.getAttribute(attribute)) === identity);
  }

  function normalizeSurfaceContract(appId, surface, layout, intents, root) {
    if (
      !isPlainObject(surface)
      || surface.schema !== "mcel.semantic-surface-ir.v1"
      || safeString(surface.appId) !== appId
      || !safeString(surface.surfaceId)
      || !Array.isArray(surface.regions)
      || !Array.isArray(surface.nodes)
    ) {
      throwViolation("APPLICATION_SURFACE_CONTRACT_INVALID", {
        phase: "mount-package",
        appId,
        message: `Application ${appId} semantic surface contract is invalid.`
      });
    }
    if (
      !isPlainObject(layout)
      || layout.schema !== "mcel.layout-grammar.v1"
      || safeString(layout.surfaceId) !== surface.surfaceId
    ) {
      throwViolation("APPLICATION_LAYOUT_CONTRACT_INVALID", {
        phase: "mount-package",
        appId,
        message: `Application ${appId} layout contract does not match its semantic surface.`
      });
    }
    if (safeString(root.getAttribute("data-mcel-surface-id")) !== surface.surfaceId) {
      throwViolation("APPLICATION_SURFACE_IDENTITY_MISMATCH", {
        phase: "mount-package",
        appId,
        expected: surface.surfaceId,
        actual: safeString(root.getAttribute("data-mcel-surface-id")),
        message: `Application ${appId} root does not expose the declared semantic surface id.`
      });
    }

    const regionIds = new Set();
    surface.regions.forEach((region) => {
      const id = safeString(region?.id);
      if (!id || regionIds.has(id) || byDeclaredIdentity(root, "data-mcel-region-id", id).length !== 1) {
        throwViolation("APPLICATION_SURFACE_REGION_MISMATCH", {
          phase: "mount-package",
          appId,
          regionId: id,
          message: `Application ${appId} region ${id || "<empty>"} is missing, duplicated, or undeclared.`
        });
      }
      regionIds.add(id);
      if (!Object.prototype.hasOwnProperty.call(layout.regions || {}, id)) {
        throwViolation("APPLICATION_LAYOUT_REGION_MISSING", {
          phase: "mount-package",
          appId,
          regionId: id,
          message: `Application ${appId} layout does not declare region ${id}.`
        });
      }
    });

    const intentIds = new Set();
    Object.keys(intents || {}).forEach((key) => {
      intentIds.add(safeString(intents[key]?.id || key));
    });
    const nodes = [];
    const nodeIds = new Set();
    surface.nodes.forEach((node) => {
      const id = safeString(node?.id);
      const kind = safeString(node?.kind);
      const matches = byDeclaredIdentity(root, "data-mcel-node-id", id);
      if (!id || nodeIds.has(id) || matches.length !== 1 || !regionIds.has(safeString(node?.regionId))) {
        throwViolation("APPLICATION_SURFACE_NODE_MISMATCH", {
          phase: "mount-package",
          appId,
          nodeId: id,
          message: `Application ${appId} semantic node ${id || "<empty>"} is missing, duplicated, or invalid.`
        });
      }
      if (kind === "control") {
        const intentId = safeString(node.intentId);
        if (!intentIds.has(intentId) || safeString(matches[0].getAttribute("data-mcel-intent-id")) !== intentId) {
          throwViolation("APPLICATION_SURFACE_CONTROL_MISMATCH", {
            phase: "mount-package",
            appId,
            nodeId: id,
            intentId,
            message: `Application ${appId} control ${id} does not bind the declared intent.`
          });
        }
      } else if (kind === "state-value" && !safeString(node.statePath)) {
        throwViolation("APPLICATION_SURFACE_STATE_PATH_REQUIRED", {
          phase: "mount-package",
          appId,
          nodeId: id,
          message: `Application ${appId} state node ${id} requires statePath.`
        });
      } else if (!new Set(["state-value", "operation-evidence"]).has(kind)) {
        throwViolation("APPLICATION_SURFACE_NODE_KIND_UNSUPPORTED", {
          phase: "mount-package",
          appId,
          nodeId: id,
          kind,
          message: `Application ${appId} surface node kind ${kind || "<empty>"} is not supported by the generic projection.`
        });
      }
      nodeIds.add(id);
      nodes.push({contract: node, element: matches[0]});
    });
    return {surface, layout, nodes};
  }

  function readStateProjection(state, path) {
    const parts = safeString(path).replace(/^state\./, "").split(".").filter(Boolean);
    let cursor = state;
    for (const part of parts) {
      if (cursor == null) return undefined;
      cursor = cursor[part];
    }
    return cursor;
  }

  function setMountStatus(root, state, text) {
    if (root.dataset) root.dataset.mcelRuntimeStatus = state;
    const status = root.querySelector ? root.querySelector("[data-mcel-runtime-status]") : null;
    if (status) {
      if (status.dataset) status.dataset.mcelRuntimeStatus = state;
      status.textContent = text;
    }
  }

  async function mountApplicationPackage(request = {}) {
    const catalog = packageCatalogAuthority(request.packageCatalog);
    let packageRecord = request.packageRecord || null;
    const requestedAppId = safeString(request.appId || packageRecord?.appId);
    if (!packageRecord && requestedAppId) packageRecord = catalog.getPackage(requestedAppId);
    if (!packageRecord && safeString(request.manifestUrl)) {
      packageRecord = catalog.listPackages().find(
        (entry) => safeString(entry?.runtimeProjection?.manifestUrl) === safeString(request.manifestUrl)
      ) || null;
    }
    if (!packageRecord || !packageRecord.runtimeProjection) {
      throwViolation("APPLICATION_PACKAGE_UNKNOWN", {
        phase: "mount-package",
        appId: requestedAppId,
        message: `Unknown or unprojected MCEL application package ${requestedAppId || "<empty>"}.`
      });
    }

    const manifestUrl = resolveBrowserUrl(
      request.manifestUrl || packageRecord.runtimeProjection.manifestUrl,
      request.baseUrl || baseUrl()
    );
    const manifest = verifyRuntimeManifest(
      packageRecord,
      catalog,
      await readRuntimeManifest(manifestUrl, request)
    );
    const loader = request.moduleLoader || defaultModuleLoader;
    const [domain, intents, adapter, surface, layout] = await Promise.all([
      loadDeclaredModule(manifestUrl, manifest.modules.domain, loader, "domain"),
      loadDeclaredModule(manifestUrl, manifest.modules.intents, loader, "intents"),
      loadDeclaredModule(manifestUrl, manifest.modules.adapter, loader, "adapter"),
      loadDeclaredModule(manifestUrl, manifest.modules.surface, loader, "surface"),
      loadDeclaredModule(manifestUrl, manifest.modules.layout, loader, "layout")
    ]);
    const appId = packageRecord.appId;
    if (safeString(domain?.appId) !== appId || safeString(adapter?.appId) !== appId || safeString(surface?.appId) !== appId) {
      throwViolation("APPLICATION_RUNTIME_MODULE_IDENTITY_MISMATCH", {
        phase: "mount-package",
        appId,
        message: `Application ${appId} runtime module identities do not agree.`
      });
    }
    const root = resolveMountRoot(request, manifest);
    const projection = normalizeSurfaceContract(appId, surface, layout, intents, root);

    let definition = applicationDefinition(appId);
    if (!definition || request.replaceDefinition === true) {
      definition = defineApplication({appId, domain, intents, adapter}, {replace: request.replaceDefinition === true});
    }
    const application = createApplicationInstance(definition, {
      id: request.instanceId,
      state: cloneValue(request.state || {})
    });
    const listeners = [];
    let active = true;
    let lastResult = null;
    const operationIdFactory = typeof request.operationIdFactory === "function"
      ? request.operationIdFactory
      : ({intentId}) => `${appId}:${intentId}:${nextMountOperationId++}`;

    function render(result = lastResult) {
      const state = application.readState();
      projection.nodes.forEach(({contract, element}) => {
        if (contract.kind === "state-value") {
          const value = readStateProjection(state, contract.statePath);
          element.textContent = value === undefined ? "" : String(value);
        } else if (contract.kind === "operation-evidence" && result?.receipt) {
          element.textContent = JSON.stringify(result.receipt, null, 2);
        }
      });
      if (!result) {
        setMountStatus(root, "mounted", `MCEL application ${appId} mounted at revision ${application.revision}.`);
      } else if (result.ok) {
        setMountStatus(root, "committed", `${result.intentId} committed at revision ${result.revision}.`);
      } else {
        setMountStatus(root, "refused", `${result.intentId || "operation"} refused: ${result.code}.`);
      }
      return deepFreeze({state: cloneValue(state), result: cloneValue(result)});
    }

    function dispatch(intentId, payload = {}, options = {}) {
      if (!active) {
        throwViolation("APPLICATION_SURFACE_UNMOUNTED", {
          phase: "dispatch-mounted-application",
          appId,
          message: `Application ${appId} surface is unmounted.`
        });
      }
      const expectedRevision = Number.isSafeInteger(options.expectedRevision)
        ? options.expectedRevision
        : application.revision;
      const operationId = safeString(options.operationId || operationIdFactory({
        appId,
        intentId,
        expectedRevision,
        revision: application.revision
      }));
      lastResult = application.dispatch({
        operationId,
        expectedRevision,
        intentId,
        payload: {...cloneValue(payload || {}), expectedRevision}
      });
      render(lastResult);
      return lastResult;
    }

    const controls = projection.nodes.filter(({contract}) => contract.kind === "control");
    controls.forEach(({contract, element}) => {
      if (typeof element.addEventListener !== "function" || typeof element.removeEventListener !== "function") {
        throwViolation("APPLICATION_SURFACE_CONTROL_NOT_INTERACTIVE", {
          phase: "mount-package",
          appId,
          nodeId: contract.id,
          message: `Application ${appId} control ${contract.id} is not interactive.`
        });
      }
    });
    controls.forEach(({contract, element}) => {
      const listener = () => dispatch(contract.intentId);
      element.addEventListener("click", listener);
      listeners.push({element, listener});
    });

    const mount = Object.freeze({
      kind: "mcel-application-package-mount",
      contractVersion: CONTRACT_VERSION,
      appId,
      packageRecord: deepFreeze(cloneValue(packageRecord)),
      manifest: deepFreeze(cloneValue(manifest)),
      definition,
      application,
      root,
      surface: deepFreeze(cloneValue(surface)),
      layout: deepFreeze(cloneValue(layout)),
      dispatch,
      render,
      readState() {
        return application.readState();
      },
      unmount() {
        if (!active) return false;
        listeners.forEach(({element, listener}) => element.removeEventListener("click", listener));
        active = false;
        activeMounts.delete(root);
        setMountStatus(root, "unmounted", `MCEL application ${appId} unmounted.`);
        return true;
      }
    });
    activeMounts.set(root, mount);
    render();
    return mount;
  }

  function applicationPackageMount(root) {
    return activeMounts.get(root) || null;
  }

  return Object.freeze({
    contractVersion: CONTRACT_VERSION,
    receiptSchema: RECEIPT_SCHEMA,
    defineApplication,
    applicationDefinition,
    listApplicationDefinitions,
    createApplicationInstance,
    readApplicationState,
    createApplicationOperation,
    dispatchApplicationIntent,
    exportApplicationEvidence,
    mountApplicationPackage,
    applicationPackageMount
  });
})();

if (typeof window !== "undefined") {
  window.McelApplicationRuntime = McelApplicationRuntime;
}
