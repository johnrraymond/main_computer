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

  function normalizeApplicationStatePath(value) {
    const path = safeString(value).replace(/^(?:state|local|derived|provisional)\./, "");
    if (!/^[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*$/.test(path)) return "";
    if (path.includes("__proto__") || path.includes(".prototype") || path.includes(".constructor")) return "";
    return path;
  }

  function applicationPathParts(path) {
    return normalizeApplicationStatePath(path).split(".").filter(Boolean);
  }

  function readApplicationPath(object, path) {
    const parts = applicationPathParts(path);
    let cursor = object;
    for (const part of parts) {
      if (cursor == null) return undefined;
      cursor = cursor[part];
    }
    return cloneValue(cursor);
  }

  function writeApplicationPath(object, path, value) {
    const parts = applicationPathParts(path);
    let cursor = object;
    for (let index = 0; index < parts.length - 1; index += 1) {
      const part = parts[index];
      if (!isPlainObject(cursor[part])) cursor[part] = {};
      cursor = cursor[part];
    }
    if (parts.length) cursor[parts[parts.length - 1]] = cloneValue(value);
  }

  function schemaAccepts(schema, value) {
    const name = safeString(schema?.name || "any");
    const describe = isPlainObject(schema?.describe) ? schema.describe : {};
    if (name === "any") return true;
    if (name === "string") {
      const minimum = Number.isSafeInteger(describe.minLength) ? describe.minLength : 0;
      const maximum = Number.isSafeInteger(describe.maxLength) ? describe.maxLength : null;
      return typeof value === "string" && value.length >= minimum && (maximum === null || value.length <= maximum);
    }
    if (name === "integer") {
      const minimum = Number.isSafeInteger(describe.minimum) ? describe.minimum : null;
      const maximum = Number.isSafeInteger(describe.maximum) ? describe.maximum : null;
      return Number.isSafeInteger(value) && (minimum === null || value >= minimum) && (maximum === null || value <= maximum);
    }
    if (name === "boolean") return typeof value === "boolean";
    if (name === "one-of") {
      const allowed = Array.isArray(describe.allowed) ? describe.allowed : [];
      return allowed.some((candidate) => valuesEqual(candidate, value));
    }
    if (name === "array") return Array.isArray(value);
    if (name === "object") return isPlainObject(value);
    return false;
  }

  function normalizeViewStateDefinition(domain, canonicalStateKeys) {
    const canonical = new Set(canonicalStateKeys);
    const provisionalInitial = cloneValue(isPlainObject(domain?.provisionalState) ? domain.provisionalState : {});
    const provisional = new Set(Object.keys(provisionalInitial));
    const provisionalDefinitions = Array.isArray(domain?.provisionalStateDefinitions)
      ? domain.provisionalStateDefinitions.slice()
      : Object.keys(provisionalInitial).sort().map((id) => ({id, initial: provisionalInitial[id], schema: {name: "any", describe: {}}}));
    const provisionalSchemas = new Map();
    provisionalDefinitions.forEach((entry, index) => {
      const id = normalizeApplicationStatePath(entry?.id);
      if (!id || id.includes(".") || provisionalSchemas.has(id)) {
        throwViolation("APPLICATION_PROVISIONAL_STATE_INVALID", {
          phase: "define-application",
          provisionalStateIndex: index,
          message: `Provisional state at index ${index} requires a unique top-level identity.`
        });
      }
      const initial = Object.prototype.hasOwnProperty.call(provisionalInitial, id) ? provisionalInitial[id] : entry.initial;
      if (!schemaAccepts(entry.schema, initial)) {
        throwViolation("APPLICATION_PROVISIONAL_STATE_INITIAL_INVALID", {
          phase: "define-application",
          stateId: id,
          message: `Provisional initial state ${id} does not satisfy its schema.`
        });
      }
      provisionalInitial[id] = cloneValue(initial);
      provisionalSchemas.set(id, cloneValue(entry.schema || {name: "any", describe: {}}));
    });
    const localInitial = cloneValue(isPlainObject(domain?.rendererLocalState) ? domain.rendererLocalState : {});
    const localDefinitions = Array.isArray(domain?.rendererLocalStateDefinitions)
      ? domain.rendererLocalStateDefinitions.slice()
      : Object.keys(localInitial).sort().map((id) => ({id, initial: localInitial[id], schema: {name: "any", describe: {}}}));
    const localSchemas = new Map();

    localDefinitions.forEach((entry, index) => {
      const id = normalizeApplicationStatePath(entry?.id);
      if (!id || id.includes(".")) {
        throwViolation("APPLICATION_LOCAL_STATE_INVALID", {
          phase: "define-application",
          localStateIndex: index,
          message: `Renderer-local state at index ${index} requires a top-level stable identity.`
        });
      }
      if (canonical.has(id) || provisional.has(id) || localSchemas.has(id)) {
        throwViolation("APPLICATION_STATE_AUTHORITY_COLLISION", {
          phase: "define-application",
          stateId: id,
          message: `Application state identity ${id} is declared by more than one authority.`
        });
      }
      const initial = Object.prototype.hasOwnProperty.call(localInitial, id) ? localInitial[id] : entry.initial;
      if (!schemaAccepts(entry.schema, initial)) {
        throwViolation("APPLICATION_LOCAL_STATE_INITIAL_INVALID", {
          phase: "define-application",
          stateId: id,
          message: `Renderer-local initial state ${id} does not satisfy its schema.`
        });
      }
      localInitial[id] = cloneValue(initial);
      localSchemas.set(id, cloneValue(entry.schema || {name: "any", describe: {}}));
    });

    const derivedEntries = Array.isArray(domain?.derivedState) ? domain.derivedState.slice() : [];
    const derivedById = new Map();
    derivedEntries.forEach((entry, index) => {
      const id = normalizeApplicationStatePath(entry?.id);
      if (!id || id.includes(".") || typeof entry?.compute !== "function") {
        throwViolation("APPLICATION_DERIVED_STATE_INVALID", {
          phase: "define-application",
          derivedStateIndex: index,
          message: `Derived state at index ${index} requires a top-level identity and compute function.`
        });
      }
      if (canonical.has(id) || provisional.has(id) || localSchemas.has(id) || derivedById.has(id)) {
        throwViolation("APPLICATION_STATE_AUTHORITY_COLLISION", {
          phase: "define-application",
          stateId: id,
          message: `Application state identity ${id} is declared by more than one authority.`
        });
      }
      const reads = Array.isArray(entry.reads) ? entry.reads.map(normalizeApplicationStatePath).filter(Boolean) : [];
      if (!reads.length) {
        throwViolation("APPLICATION_DERIVED_STATE_READS_REQUIRED", {
          phase: "define-application",
          stateId: id,
          message: `Derived state ${id} requires declared read paths.`
        });
      }
      derivedById.set(id, {
        id,
        reads,
        compute: entry.compute,
        schema: cloneValue(entry.schema || {name: "any", describe: {}}),
        computeFingerprint: safeString(entry.computeFingerprint)
      });
    });

    const knownRoots = new Set([...canonical, ...localSchemas.keys(), ...provisional, ...derivedById.keys()]);
    derivedById.forEach((entry) => {
      entry.reads.forEach((path) => {
        const root = applicationPathParts(path)[0];
        if (!knownRoots.has(root)) {
          throwViolation("APPLICATION_DERIVED_STATE_DEPENDENCY_UNKNOWN", {
            phase: "define-application",
            stateId: entry.id,
            dependency: path,
            message: `Derived state ${entry.id} references unknown state ${path}.`
          });
        }
        if (provisional.has(root)) {
          throwViolation("APPLICATION_DERIVED_PROVISIONAL_DEPENDENCY_UNSUPPORTED", {
            phase: "define-application",
            stateId: entry.id,
            dependency: path,
            message: `Derived state ${entry.id} cannot read provisional state until provisional runtime support is implemented.`
          });
        }
      });
    });

    const ordered = [];
    const temporary = new Set();
    const permanent = new Set();
    function visit(id, trail = []) {
      if (permanent.has(id)) return;
      if (temporary.has(id)) {
        throwViolation("APPLICATION_DERIVED_STATE_CYCLE", {
          phase: "define-application",
          stateId: id,
          cycle: [...trail, id],
          message: `Derived state dependency cycle detected: ${[...trail, id].join(" -> ")}.`
        });
      }
      temporary.add(id);
      const entry = derivedById.get(id);
      entry.reads.forEach((path) => {
        const dependency = applicationPathParts(path)[0];
        if (derivedById.has(dependency)) visit(dependency, [...trail, id]);
      });
      temporary.delete(id);
      permanent.add(id);
      ordered.push(entry);
    }
    [...derivedById.keys()].sort().forEach((id) => visit(id));

    return {
      provisionalInitial: deepFreeze(cloneValue(provisionalInitial)),
      provisionalSchemas,
      provisionalStateIds: Object.freeze([...provisionalSchemas.keys()].sort()),
      localInitial: deepFreeze(cloneValue(localInitial)),
      localSchemas,
      derivedById,
      derivedOrder: Object.freeze(ordered.slice()),
      localStateIds: Object.freeze([...localSchemas.keys()].sort()),
      derivedStateIds: Object.freeze([...derivedById.keys()].sort())
    };
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

  function normalizeAuthorityPath(value) {
    const path = safeString(value);
    const match = path.match(/^(state|local|derived|provisional)\.(.+)$/);
    if (!match) return "";
    const suffix = normalizeApplicationStatePath(match[2]);
    return suffix ? `${match[1]}.${suffix}` : "";
  }

  function normalizeCapabilityDefinitions(domain) {
    const capabilities = isPlainObject(domain?.capabilities) ? domain.capabilities : {};
    const result = new Map();
    Object.keys(capabilities).sort().forEach((alias) => {
      const entry = capabilities[alias];
      if (!safeString(alias) || !isPlainObject(entry) || !isPlainObject(entry.operations)) {
        throwViolation("APPLICATION_CAPABILITY_CONTRACT_INVALID", {
          phase: "define-application",
          capability: alias,
          message: `Application capability ${alias || "<empty>"} is invalid.`
        });
      }
      const operations = new Map();
      Object.keys(entry.operations).sort().forEach((name) => {
        const operation = entry.operations[name];
        if (!safeString(name) || !isPlainObject(operation)) {
          throwViolation("APPLICATION_CAPABILITY_OPERATION_CONTRACT_INVALID", {
            phase: "define-application",
            capability: alias,
            operation: name,
            message: `Application capability operation ${alias}.${name} is invalid.`
          });
        }
        operations.set(name, deepFreeze({
          name,
          stream: operation.stream === true,
          cancellable: operation.cancellable === true,
          request: cloneValue(operation.request || {name: "any", describe: {}}),
          response: cloneValue(operation.response || {name: "any", describe: {}})
        }));
      });
      result.set(alias, deepFreeze({
        alias,
        id: safeString(entry.id || alias),
        risk: safeString(entry.risk),
        description: safeString(entry.description),
        operations
      }));
    });
    return result;
  }

  function validateCapabilityProviders(capabilityDefinitions, providers, phase, requiredAliases = []) {
    const normalized = isPlainObject(providers) ? providers : {};
    const result = {};
    const required = new Set(requiredAliases.map(safeString).filter(Boolean));
    required.forEach((alias) => {
      const definition = capabilityDefinitions.get(alias);
      const provider = normalized[alias];
      if (!definition || !isPlainObject(provider)) {
        throwViolation("APPLICATION_CAPABILITY_PROVIDER_MISSING", {
          phase,
          capability: alias,
          capabilityId: definition?.id || "",
          message: `Application capability provider ${alias} is required.`
        });
      }
      definition.operations.forEach((_operation, name) => {
        if (typeof provider[name] !== "function") {
          throwViolation("APPLICATION_CAPABILITY_PROVIDER_OPERATION_MISSING", {
            phase,
            capability: alias,
            capabilityId: definition.id,
            operation: name,
            message: `Application capability provider ${alias} is missing operation ${name}.`
          });
        }
      });
      result[alias] = provider;
    });
    Object.keys(normalized).forEach((alias) => {
      if (!capabilityDefinitions.has(alias)) {
        throwViolation("APPLICATION_CAPABILITY_PROVIDER_UNDECLARED", {
          phase,
          capability: alias,
          message: `Application capability provider ${alias} is not declared.`
        });
      }
    });
    return Object.freeze({...result});
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

      const kind = safeString(intent.kind || "mutation");
      const reads = Array.isArray(intent.reads)
        ? intent.reads.map(normalizeAuthorityPath).filter(Boolean)
        : [];
      const writes = Array.isArray(intent.writes)
        ? intent.writes.map(normalizeAuthorityPath).filter(Boolean)
        : [];
      const canonicalReads = reads.filter((path) => path.startsWith("state."));
      const canonicalWrites = writes.filter((path) => path.startsWith("state."));
      if (kind === "mutation" && (!canonicalReads.length || !canonicalWrites.length)) {
        throwViolation("APPLICATION_INTENT_PATHS_REQUIRED", {
          phase: "define-application",
          intentId,
          message: `Mutation intent ${intentId} requires declared canonical state reads and writes.`
        });
      }
      if (!new Set(["mutation", "prohibited", "async-capability", "cancel-operation"]).has(kind)) {
        throwViolation("APPLICATION_INTENT_KIND_UNSUPPORTED", {
          phase: "define-application",
          intentId,
          kind,
          message: `Application intent ${intentId} declares unsupported kind ${kind || "<empty>"}.`
        });
      }

      const entry = deepFreeze({
        key,
        id: intentId,
        kind,
        risk: safeString(intent.risk),
        reason: safeString(intent.reason),
        reads,
        writes,
        canonicalReads,
        canonicalWrites,
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

    const intents = normalizeIntentEntries(spec.intents);
    const viewState = normalizeViewStateDefinition(spec.domain, stateKeys);
    const capabilities = normalizeCapabilityDefinitions(spec.domain);
    [...intents.byId.values()].forEach((intent) => {
      const uses = Array.isArray(intent.contract?.uses) ? intent.contract.uses.map(safeString).filter(Boolean) : [];
      uses.forEach((alias) => {
        if (!capabilities.has(alias)) {
          throwViolation("APPLICATION_INTENT_CAPABILITY_UNKNOWN", {
            phase: "define-application",
            appId,
            intentId: intent.id,
            capability: alias,
            message: `Application intent ${intent.id} references undeclared capability ${alias}.`
          });
        }
      });
      const provisionalPath = normalizeApplicationStatePath(intent.contract?.provisionalPath);
      if (intent.kind === "async-capability") {
        if (!provisionalPath || !viewState.provisionalSchemas.has(provisionalPath)) {
          throwViolation("APPLICATION_INTENT_PROVISIONAL_PATH_UNKNOWN", {
            phase: "define-application",
            appId,
            intentId: intent.id,
            provisionalPath,
            message: `Async application intent ${intent.id} requires a declared provisional state path.`
          });
        }
        const concurrency = safeString(intent.contract?.concurrency || "serial-per-application");
        if (!new Set(["serial-per-application", "latest-per-item-key"]).has(concurrency)) {
          throwViolation("APPLICATION_INTENT_CONCURRENCY_POLICY_UNSUPPORTED", {
            phase: "define-application",
            appId,
            intentId: intent.id,
            concurrency,
            message: `Async application intent ${intent.id} declares unsupported concurrency policy ${concurrency || "<empty>"}.`
          });
        }
        if (concurrency === "latest-per-item-key") {
          const payloadContract = isPlainObject(intent.contract?.payload) ? intent.contract.payload : {};
          const itemKeyFields = Object.entries(payloadContract).filter(([, source]) => source?.fromItemKey === true);
          if (itemKeyFields.length !== 1) {
            throwViolation("APPLICATION_INTENT_CONCURRENCY_KEY_REQUIRED", {
              phase: "define-application",
              appId,
              intentId: intent.id,
              itemKeyFieldCount: itemKeyFields.length,
              message: `Async application intent ${intent.id} requires exactly one item-key payload source.`
            });
          }
        }
      }
    });
    [...intents.byId.values()].filter((intent) => intent.kind === "cancel-operation").forEach((intent) => {
      const targetId = safeString(intent.contract?.cancels);
      const target = intents.byId.get(targetId);
      if (!target || target.kind !== "async-capability" || target.contract?.cancellable !== true) {
        throwViolation("APPLICATION_INTENT_CANCEL_TARGET_INVALID", {
          phase: "define-application",
          appId,
          intentId: intent.id,
          targetIntentId: targetId,
          message: `Cancellation intent ${intent.id} must target a declared cancellable async operation.`
        });
      }
    });

    return {
      appId,
      stateKeys,
      invariantReads,
      invariants,
      intents,
      viewState,
      capabilities
    };
  }

  function resolveIntent(definition, intentId) {
    const id = safeString(intentId);
    return definition.intentById.get(id) || definition.intentByKey.get(id) || null;
  }

  function buildScmTransition(definitionDraft, intent) {
    const readPaths = [...new Set([...intent.canonicalReads, ...definitionDraft.invariantReads])].sort();
    const writePaths = [...new Set(intent.canonicalWrites)].sort();

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

  function buildCapabilityCommitTransition(definitionDraft, intent) {
    const readPaths = [...new Set([...intent.canonicalReads, ...definitionDraft.invariantReads])].sort();
    const writePaths = [...new Set(intent.canonicalWrites)].sort();
    return {
      reads: readPaths,
      writes: writePaths,
      pre() {
        return true;
      },
      apply(ctx, input) {
        const before = stateFromContext(ctx, readPaths);
        const payload = cloneValue(input?.payload || {});
        const provisional = cloneValue(input?.provisional || {});
        const proposed = definitionDraft.adapter.commitCapabilityOperation({
          intentId: intent.id,
          input: cloneValue(input || {}),
          payload,
          provisional,
          state: cloneValue(before)
        });
        if (!isPlainObject(proposed)) {
          throw new Error(`Application capability commit ${intent.id} must return a state object.`);
        }
        const undeclared = changedPaths(before, proposed).filter((path) => !pathCovered(path, writePaths));
        if (undeclared.length) {
          throw new Error(
            `Application capability commit ${intent.id} changed undeclared state paths: ${undeclared.join(", ")}.`
          );
        }
        writePaths.forEach((path) => ctx.set(path, readObjectPath(proposed, path)));
        return {before, proposed: cloneValue(proposed), provisional, payload};
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
      viewState: checked.viewState,
      capabilities: checked.capabilities,
      intentByKey: checked.intents.byKey,
      intentById: checked.intents.byId
    };
    const transitions = {};
    [...checked.intents.byId.values()].forEach((intent) => {
      if (intent.kind === "mutation") {
        transitions[intent.id] = buildScmTransition(definitionDraft, intent);
      } else if (intent.kind === "async-capability") {
        transitions[intent.id] = buildCapabilityCommitTransition(definitionDraft, intent);
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
      intentIds: Object.freeze([...checked.intents.byId.keys()].sort()),
      localStateIds: checked.viewState.localStateIds,
      derivedStateIds: checked.viewState.derivedStateIds,
      provisionalStateIds: checked.viewState.provisionalStateIds,
      capabilityIds: Object.freeze([...checked.capabilities.keys()].sort())
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

  function validateLocalStateCandidate(viewState, candidate, phase) {
    if (!isPlainObject(candidate)) {
      throwViolation("APPLICATION_LOCAL_STATE_PATCH_INVALID", {
        phase,
        message: "Renderer-local state updates require a plain object."
      });
    }
    Object.keys(candidate).forEach((id) => {
      if (!viewState.localSchemas.has(id)) {
        throwViolation("APPLICATION_LOCAL_STATE_UNKNOWN", {
          phase,
          stateId: id,
          message: `Renderer-local state ${id} is not declared.`
        });
      }
      if (!schemaAccepts(viewState.localSchemas.get(id), candidate[id])) {
        throwViolation("APPLICATION_LOCAL_STATE_SCHEMA_FAILED", {
          phase,
          stateId: id,
          value: cloneValue(candidate[id]),
          message: `Renderer-local state ${id} does not satisfy its schema.`
        });
      }
    });
  }

  function computeDerivedState(definitionDraft, canonicalState, localState, phase) {
    const values = {};
    const view = {...cloneValue(canonicalState), ...cloneValue(localState)};
    for (const entry of definitionDraft.viewState.derivedOrder) {
      const input = {};
      entry.reads.forEach((path) => writeApplicationPath(input, path, readApplicationPath(view, path)));
      const frozenInput = deepFreeze(cloneValue(input));
      const inputSnapshot = stableJson(frozenInput);
      let result;
      try {
        result = entry.compute(frozenInput);
      } catch (error) {
        throwViolation("APPLICATION_DERIVED_STATE_COMPUTE_FAILED", {
          phase,
          stateId: entry.id,
          computeFingerprint: entry.computeFingerprint,
          message: safeString(error?.message || error) || `Derived state ${entry.id} failed.`
        });
      }
      if (stableJson(frozenInput) !== inputSnapshot) {
        throwViolation("APPLICATION_DERIVED_STATE_MUTATED_INPUT", {
          phase,
          stateId: entry.id,
          message: `Derived state ${entry.id} mutated its declared inputs.`
        });
      }
      if (!schemaAccepts(entry.schema, result)) {
        throwViolation("APPLICATION_DERIVED_STATE_SCHEMA_FAILED", {
          phase,
          stateId: entry.id,
          value: cloneValue(result),
          message: `Derived state ${entry.id} does not satisfy its schema.`
        });
      }
      values[entry.id] = cloneValue(result);
      view[entry.id] = cloneValue(result);
    }
    return deepFreeze(values);
  }

  function composeApplicationViewState(instance, record) {
    if (record.derivedViolation) {
      const error = new Error(record.derivedViolation.message);
      error.name = "McelApplicationRuntimeError";
      error.violation = record.derivedViolation;
      throw error;
    }
    return deepFreeze({
      ...cloneValue(instance.state),
      ...cloneValue(record.localState),
      ...cloneValue(record.derivedState)
    });
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
    const localState = {
      ...cloneValue(stored.draft.viewState.localInitial),
      ...cloneValue(options.localState || {})
    };
    validateLocalStateCandidate(stored.draft.viewState, localState, "create-instance");
    const provisionalState = {
      ...cloneValue(stored.draft.viewState.provisionalInitial),
      ...cloneValue(options.provisionalState || {})
    };
    Object.keys(provisionalState).forEach((id) => {
      if (!stored.draft.viewState.provisionalSchemas.has(id)) {
        throwViolation("APPLICATION_PROVISIONAL_STATE_UNKNOWN", {
          phase: "create-instance",
          stateId: id,
          message: `Provisional state ${id} is not declared.`
        });
      }
      if (!schemaAccepts(stored.draft.viewState.provisionalSchemas.get(id), provisionalState[id])) {
        throwViolation("APPLICATION_PROVISIONAL_STATE_SCHEMA_FAILED", {
          phase: "create-instance",
          stateId: id,
          message: `Provisional state ${id} does not satisfy its schema.`
        });
      }
    });
    const providedCapabilities = isPlainObject(options.capabilities) ? options.capabilities : {};
    validateCapabilityProviders(stored.draft.capabilities, providedCapabilities, "create-instance", []);
    const initialDerivedState = computeDerivedState(stored.draft, scmInstance.state, localState, "create-instance");
    const instance = {
      kind: "mcel-application-instance",
      contractVersion: CONTRACT_VERSION,
      id: scmInstance.id,
      appId,
      definition,
      readState() {
        return readApplicationState(instance);
      },
      readLocalState() {
        return readApplicationLocalState(instance);
      },
      readDerivedState() {
        return readApplicationDerivedState(instance);
      },
      readProvisionalState() {
        return readApplicationProvisionalState(instance);
      },
      readViewState() {
        return readApplicationViewState(instance);
      },
      updateLocalState(patch = {}) {
        return updateApplicationLocalState(instance, patch);
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
      },
      localRevision: {
        enumerable: true,
        get() {
          return instanceRecords.get(instance)?.localRevision || 0;
        }
      },
      provisionalRevision: {
        enumerable: true,
        get() {
          return instanceRecords.get(instance)?.provisionalRevision || 0;
        }
      }
    });
    instanceRecords.set(instance, {
      scmInstance,
      receipts,
      stored,
      localState: deepFreeze(cloneValue(localState)),
      derivedState: initialDerivedState,
      provisionalState: deepFreeze(cloneValue(provisionalState)),
      capabilities: Object.freeze({...providedCapabilities}),
      activeOperations: new Map(),
      activeOperationKeys: new Map(),
      localRevision: 0,
      provisionalRevision: 0,
      derivedViolation: null
    });
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

  function readApplicationLocalState(instance) {
    const record = assertApplicationInstance(instance, "read-local-state");
    return deepFreeze(cloneValue(record.localState));
  }

  function readApplicationDerivedState(instance) {
    const record = assertApplicationInstance(instance, "read-derived-state");
    if (record.derivedViolation) composeApplicationViewState(instance, record);
    return deepFreeze(cloneValue(record.derivedState));
  }

  function readApplicationProvisionalState(instance) {
    const record = assertApplicationInstance(instance, "read-provisional-state");
    return deepFreeze(cloneValue(record.provisionalState));
  }

  function replaceApplicationProvisionalState(instance, stateId, value, phase = "update-provisional-state") {
    const record = assertApplicationInstance(instance, phase);
    const id = normalizeApplicationStatePath(stateId);
    if (!id || id.includes(".") || !record.stored.draft.viewState.provisionalSchemas.has(id)) {
      throwViolation("APPLICATION_PROVISIONAL_STATE_UNKNOWN", {
        phase,
        stateId: id,
        message: `Provisional state ${id || "<empty>"} is not declared.`
      });
    }
    const schema = record.stored.draft.viewState.provisionalSchemas.get(id);
    if (!schemaAccepts(schema, value)) {
      throwViolation("APPLICATION_PROVISIONAL_STATE_SCHEMA_FAILED", {
        phase,
        stateId: id,
        value: cloneValue(value),
        message: `Provisional state ${id} does not satisfy its schema.`
      });
    }
    const before = cloneValue(record.provisionalState);
    const candidate = {...before, [id]: cloneValue(value)};
    const changed = !valuesEqual(before, candidate);
    if (changed) {
      record.provisionalState = deepFreeze(candidate);
      record.provisionalRevision += 1;
    }
    return deepFreeze({
      kind: "mcel-application-provisional-state-result",
      contractVersion: CONTRACT_VERSION,
      appId: instance.appId,
      applicationInstanceId: instance.id,
      changed,
      provisionalRevision: record.provisionalRevision,
      before,
      after: cloneValue(record.provisionalState)
    });
  }

  function readApplicationViewState(instance) {
    const record = assertApplicationInstance(instance, "read-view-state");
    return composeApplicationViewState(instance, record);
  }

  function updateApplicationLocalState(instance, patch = {}) {
    const record = assertApplicationInstance(instance, "update-local-state");
    if (!isPlainObject(patch)) {
      throwViolation("APPLICATION_LOCAL_STATE_PATCH_INVALID", {
        phase: "update-local-state",
        appId: instance.appId,
        instanceId: instance.id,
        message: "Renderer-local state updates require a plain object patch."
      });
    }
    validateLocalStateCandidate(record.stored.draft.viewState, patch, "update-local-state");
    const beforeLocalState = cloneValue(record.localState);
    const beforeDerivedState = cloneValue(record.derivedState);
    const candidateLocalState = {...beforeLocalState, ...cloneValue(patch)};
    validateLocalStateCandidate(record.stored.draft.viewState, candidateLocalState, "update-local-state");
    const candidateDerivedState = computeDerivedState(
      record.stored.draft,
      instance.state,
      candidateLocalState,
      "update-local-state"
    );
    const changed = !valuesEqual(beforeLocalState, candidateLocalState);
    if (changed) {
      record.localState = deepFreeze(cloneValue(candidateLocalState));
      record.derivedState = candidateDerivedState;
      record.localRevision += 1;
      record.derivedViolation = null;
    }
    return deepFreeze({
      kind: "mcel-application-local-state-result",
      contractVersion: CONTRACT_VERSION,
      appId: instance.appId,
      applicationInstanceId: instance.id,
      changed,
      localRevision: record.localRevision,
      before: {
        localState: beforeLocalState,
        derivedState: beforeDerivedState
      },
      after: {
        localState: cloneValue(record.localState),
        derivedState: cloneValue(record.derivedState),
        viewState: cloneValue(composeApplicationViewState(instance, record))
      }
    });
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
      message: safeString(
        details.message
        || details?.adapter?.preflight?.message
        || details?.violation?.message
        || ""
      ),
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
      message: details.message,
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

  function capabilityItemKey(intent, payload, phase = "dispatch-capability") {
    const concurrency = safeString(intent?.contract?.concurrency);
    if (concurrency !== "latest-per-item-key") return "";
    const payloadContract = isPlainObject(intent?.contract?.payload) ? intent.contract.payload : {};
    const keyFields = Object.entries(payloadContract)
      .filter(([, source]) => source?.fromItemKey === true)
      .map(([field]) => field);
    if (keyFields.length !== 1) {
      throwViolation("APPLICATION_ASYNC_OPERATION_CONCURRENCY_KEY_INVALID", {
        phase,
        intentId: safeString(intent?.id),
        keyFields,
        message: `Async intent ${safeString(intent?.id) || "<empty>"} requires exactly one item-key payload source.`
      });
    }
    const value = payload?.[keyFields[0]];
    if (value === undefined || value === null || (typeof value !== "string" && typeof value !== "number")) {
      throwViolation("APPLICATION_ASYNC_OPERATION_CONCURRENCY_KEY_INVALID", {
        phase,
        intentId: safeString(intent?.id),
        field: keyFields[0],
        value: cloneValue(value),
        message: `Async intent ${safeString(intent?.id)} produced an invalid item concurrency key.`
      });
    }
    const itemKey = String(value);
    if (!itemKey) {
      throwViolation("APPLICATION_ASYNC_OPERATION_CONCURRENCY_KEY_INVALID", {
        phase,
        intentId: safeString(intent?.id),
        field: keyFields[0],
        message: `Async intent ${safeString(intent?.id)} produced an empty item concurrency key.`
      });
    }
    return itemKey;
  }

  function capabilityOperationKey(intentId, itemKey) {
    return itemKey ? `${safeString(intentId)}:${itemKey}` : safeString(intentId);
  }

  function operationAbortReason(operation) {
    if (operation.status === "superseded") return "superseded";
    if (operation.status === "cancelled") return "cancelled";
    return "";
  }

  function isAuthoritativeCapabilityOperation(record, operation) {
    if (!operation || operation.status !== "running") return false;
    if (record.activeOperations.get(operation.operationId) !== operation) return false;
    if (operation.concurrencyKey && record.activeOperationKeys.get(operation.concurrencyKey) !== operation) return false;
    return true;
  }

  function clearCapabilityProvisional(instance, operation, phase) {
    const record = assertApplicationInstance(instance, phase);
    const provisionalPath = operation.provisionalPath;
    const initial = cloneValue(record.stored.draft.viewState.provisionalInitial[provisionalPath]);
    if (operation.itemKey && isPlainObject(record.provisionalState[provisionalPath])) {
      const next = cloneValue(record.provisionalState[provisionalPath]);
      delete next[operation.itemKey];
      return replaceApplicationProvisionalState(instance, provisionalPath, next, phase);
    }
    return replaceApplicationProvisionalState(instance, provisionalPath, initial, phase);
  }

  function terminalCapabilityResult(instance, request, operation, status, code, message = "") {
    const record = assertApplicationInstance(instance, `capability-${status}`);
    const receipt = buildReceipt(instance, request, {
      status,
      code,
      message,
      beforeRevision: operation.beforeRevision,
      beforeState: operation.beforeState,
      afterRevision: instance.revision,
      afterState: instance.state,
      adapter: {
        adapterId: safeString(record.stored.draft.adapter.adapterId),
        capability: {
          eventCount: operation.eventCount,
          provisionalPath: operation.provisionalPath,
          itemKey: operation.itemKey,
          concurrencyKey: operation.concurrencyKey
        }
      }
    });
    return deepFreeze({
      kind: "mcel-application-operation-result",
      contractVersion: CONTRACT_VERSION,
      ok: false,
      status,
      code,
      appId: instance.appId,
      operationId: operation.operationId,
      intentId: operation.intentId,
      revision: instance.revision,
      state: cloneValue(instance.state),
      provisionalRevision: record.provisionalRevision,
      provisionalState: cloneValue(record.provisionalState),
      receipt
    });
  }

  function endCapabilityOperation(record, operation) {
    if (record.activeOperations.get(operation.operationId) === operation) {
      record.activeOperations.delete(operation.operationId);
    }
    if (operation.concurrencyKey && record.activeOperationKeys.get(operation.concurrencyKey) === operation) {
      record.activeOperationKeys.delete(operation.concurrencyKey);
    }
  }

  function stopCapabilityOperation(instance, operation, status, reason) {
    const record = assertApplicationInstance(instance, `capability-${status}`);
    if (!operation || operation.status !== "running") return false;
    operation.status = status;
    operation.reason = safeString(reason || status);
    try {
      operation.abortController?.abort(operation.reason);
    } catch (_error) {
      // Runtime authority still suppresses late events and commits even if abort signalling fails.
    }
    try {
      clearCapabilityProvisional(instance, operation, `capability-${status}`);
    } catch (_error) {
      // Preserve terminal operation status even if provisional cleanup is already complete.
    }
    if (operation.concurrencyKey && record.activeOperationKeys.get(operation.concurrencyKey) === operation) {
      record.activeOperationKeys.delete(operation.concurrencyKey);
    }
    return true;
  }

  function abortApplicationOperations(instance, reason = "unmounted") {
    const record = assertApplicationInstance(instance, "abort-application-operations");
    const stopped = [];
    [...record.activeOperations.values()].forEach((operation) => {
      if (stopCapabilityOperation(instance, operation, "cancelled", reason)) stopped.push(operation.operationId);
    });
    return deepFreeze({
      kind: "mcel-application-operation-abort-result",
      contractVersion: CONTRACT_VERSION,
      appId: instance.appId,
      applicationInstanceId: instance.id,
      reason: safeString(reason),
      operationIds: stopped
    });
  }

  function runningCapabilityResult(instance, request, details = {}) {
    return deepFreeze({
      kind: "mcel-application-operation-result",
      contractVersion: CONTRACT_VERSION,
      ok: true,
      status: "running",
      code: "APPLICATION_CAPABILITY_OPERATION_RUNNING",
      appId: instance.appId,
      operationId: safeString(request.operationId),
      intentId: safeString(request.intentId),
      revision: instance.revision,
      state: cloneValue(instance.state),
      provisionalRevision: instance.provisionalRevision,
      provisionalState: cloneValue(details.provisionalState || {}),
      event: cloneValue(details.event || null),
      eventCount: details.eventCount || 0,
      receipt: null
    });
  }

  async function dispatchCapabilityIntent(instance, request, intent, baseDetails) {
    const record = assertApplicationInstance(instance, "dispatch-capability");
    const operationId = safeString(request.operationId);
    const expectedRevision = request.expectedRevision;
    const payload = cloneValue(request.payload || {});
    const input = {...payload, payload: cloneValue(payload), expectedRevision};
    const preflight = record.stored.draft.adapter.preflight({
      intentId: intent.id,
      input: cloneValue(input),
      state: cloneValue(baseDetails.beforeState)
    });
    if (!preflight || preflight.ok !== true) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(preflight?.code || "APPLICATION_PREFLIGHT_REFUSED"),
        message: safeString(preflight?.message),
        adapter: {preflight: cloneValue(preflight || {})}
      });
    }
    if (
      instance.appliedOperationIds.includes(operationId)
      || record.activeOperations.has(operationId)
      || record.receipts.some((receipt) => receipt.operationId === operationId)
    ) {
      return refusalResult(instance, request, {...baseDetails, code: "SCM_DUPLICATE_OPERATION"});
    }
    if (expectedRevision !== instance.revision) {
      return refusalResult(instance, request, {...baseDetails, code: "SCM_STALE_REVISION"});
    }

    const concurrency = safeString(intent.contract?.concurrency || "serial-per-application");
    if (!new Set(["serial-per-application", "latest-per-item-key"]).has(concurrency)) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_ASYNC_OPERATION_CONCURRENCY_POLICY_UNSUPPORTED",
        message: `Async concurrency policy ${concurrency || "<empty>"} is unsupported.`
      });
    }
    let itemKey = "";
    try {
      itemKey = capabilityItemKey(intent, payload);
    } catch (error) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(error?.violation?.code || "APPLICATION_ASYNC_OPERATION_CONCURRENCY_KEY_INVALID"),
        message: safeString(error?.violation?.message || error?.message),
        violation: error?.violation || null,
        adapter: {preflight: cloneValue(preflight)}
      });
    }
    const concurrencyKey = capabilityOperationKey(intent.id, itemKey);
    if (concurrency === "serial-per-application" && record.activeOperations.size) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_ASYNC_OPERATION_ALREADY_ACTIVE",
        message: "A serial capability operation is already active for this application instance."
      });
    }
    if (concurrency === "latest-per-item-key") {
      const previous = record.activeOperationKeys.get(concurrencyKey);
      if (previous) stopCapabilityOperation(instance, previous, "superseded", `Superseded by ${operationId}.`);
    }

    const uses = Array.isArray(intent.contract?.uses) ? intent.contract.uses.map(safeString).filter(Boolean) : [];
    let capabilities;
    try {
      capabilities = validateCapabilityProviders(
        record.stored.draft.capabilities,
        record.capabilities,
        "dispatch-capability",
        uses
      );
    } catch (error) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(error?.violation?.code || "APPLICATION_CAPABILITY_PROVIDER_MISSING"),
        message: safeString(error?.violation?.message || error?.message),
        violation: error?.violation || null,
        adapter: {preflight: cloneValue(preflight)}
      });
    }

    const provisionalPath = normalizeApplicationStatePath(intent.contract?.provisionalPath);
    const abortController = typeof AbortController === "function"
      ? new AbortController()
      : {signal: {aborted: false, reason: undefined}, abort(reason) { this.signal.aborted = true; this.signal.reason = reason; }};
    const operation = {
      operationId,
      intentId: intent.id,
      provisionalPath,
      startedAt: now(),
      expectedRevision,
      beforeRevision: baseDetails.beforeRevision,
      beforeState: cloneValue(baseDetails.beforeState),
      payload: cloneValue(payload),
      itemKey,
      concurrency,
      concurrencyKey,
      eventCount: 0,
      ignoredEventCount: 0,
      status: "running",
      reason: "",
      abortController
    };
    record.activeOperations.set(operationId, operation);
    if (concurrencyKey) record.activeOperationKeys.set(concurrencyKey, operation);
    const notify = typeof request.onProgress === "function" ? request.onProgress : null;

    try {
      const iterable = record.stored.draft.adapter.runCapabilityOperation({
        intentId: intent.id,
        input: cloneValue(input),
        payload: cloneValue(payload),
        state: cloneValue(baseDetails.beforeState),
        capabilities,
        signal: abortController.signal
      });
      if (!iterable || typeof iterable[Symbol.asyncIterator] !== "function") {
        throw Object.assign(new Error("Capability operation must return an async iterable."), {
          code: "APPLICATION_CAPABILITY_STREAM_REQUIRED"
        });
      }

      for await (const event of iterable) {
        if (!isAuthoritativeCapabilityOperation(record, operation)) {
          operation.ignoredEventCount += 1;
          throw Object.assign(new Error(`Capability operation ${operationId} is no longer authoritative.`), {
            terminalStatus: operationAbortReason(operation) || "superseded"
          });
        }
        operation.eventCount += 1;
        const current = cloneValue(record.provisionalState[provisionalPath]);
        const next = record.stored.draft.adapter.receiveProvisional({
          intentId: intent.id,
          input: cloneValue(input),
          payload: cloneValue(payload),
          state: cloneValue(instance.state),
          provisional: current,
          event: cloneValue(event),
          capabilities
        });
        if (!isAuthoritativeCapabilityOperation(record, operation)) {
          operation.ignoredEventCount += 1;
          throw Object.assign(new Error(`Capability operation ${operationId} was stopped during event reconciliation.`), {
            terminalStatus: operationAbortReason(operation) || "superseded"
          });
        }
        replaceApplicationProvisionalState(instance, provisionalPath, next, "capability-event");
        if (notify) {
          await notify(runningCapabilityResult(instance, request, {
            provisionalState: record.provisionalState,
            event,
            eventCount: operation.eventCount
          }));
        }
      }

      if (!isAuthoritativeCapabilityOperation(record, operation)) {
        throw Object.assign(new Error(`Capability operation ${operationId} cannot commit because it is no longer authoritative.`), {
          terminalStatus: operationAbortReason(operation) || "superseded"
        });
      }
      const finalProvisional = cloneValue(record.provisionalState[provisionalPath]);
      const commitExpectedRevision = instance.revision;
      const scmResult = scmAuthority().transition(
        record.scmInstance,
        intent.id,
        {...cloneValue(input), expectedRevision: commitExpectedRevision, provisional: finalProvisional},
        {operationId, expectedRevision: commitExpectedRevision}
      );
      operation.status = "committed";
      let derivedViolation = null;
      try {
        record.derivedState = computeDerivedState(
          record.stored.draft,
          instance.state,
          record.localState,
          "capability-commit"
        );
        record.derivedViolation = null;
      } catch (error) {
        derivedViolation = error?.violation || violation("APPLICATION_DERIVED_STATE_COMPUTE_FAILED", {
          phase: "capability-commit",
          appId: instance.appId,
          instanceId: instance.id,
          message: safeString(error?.message || error)
        });
        record.derivedViolation = derivedViolation;
      }
      clearCapabilityProvisional(instance, operation, "capability-complete");
      const receipt = buildReceipt(instance, request, {
        status: "committed",
        code: "APPLICATION_CAPABILITY_OPERATION_COMMITTED",
        beforeRevision: baseDetails.beforeRevision,
        beforeState: baseDetails.beforeState,
        afterRevision: instance.revision,
        afterState: instance.state,
        adapter: {
          adapterId: safeString(record.stored.draft.adapter.adapterId),
          preflight: cloneValue(preflight),
          effectsValidated: true,
          capability: {
            uses,
            eventCount: operation.eventCount,
            ignoredEventCount: operation.ignoredEventCount,
            provisionalPath,
            provisionalRevision: record.provisionalRevision,
            itemKey,
            concurrency,
            concurrencyKey
          },
          derivedState: {ok: derivedViolation === null, violation: cloneValue(derivedViolation)}
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
        provisionalRevision: record.provisionalRevision,
        provisionalState: cloneValue(record.provisionalState),
        receipt
      });
    } catch (error) {
      const terminalStatus = safeString(error?.terminalStatus || operationAbortReason(operation));
      if (terminalStatus === "cancelled") {
        return terminalCapabilityResult(
          instance,
          request,
          operation,
          "cancelled",
          "APPLICATION_ASYNC_OPERATION_CANCELLED",
          operation.reason || "The capability operation was cancelled."
        );
      }
      if (terminalStatus === "superseded") {
        return terminalCapabilityResult(
          instance,
          request,
          operation,
          "superseded",
          "APPLICATION_ASYNC_OPERATION_SUPERSEDED",
          operation.reason || "The capability operation was superseded by a newer item-key operation."
        );
      }
      operation.status = "failed";
      try {
        clearCapabilityProvisional(instance, operation, "capability-failed");
      } catch (_clearError) {
        // Preserve the primary capability failure.
      }
      const runtimeViolation = error?.violation || null;
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(runtimeViolation?.code || error?.code || error?.applicationCode || "APPLICATION_CAPABILITY_OPERATION_FAILED"),
        message: safeString(runtimeViolation?.message || error?.message || error),
        adapter: {
          preflight: cloneValue(preflight),
          capability: {uses, eventCount: operation.eventCount, ignoredEventCount: operation.ignoredEventCount, provisionalPath, itemKey, concurrencyKey}
        },
        violation: runtimeViolation || {
          name: safeString(error?.name || "Error"),
          message: safeString(error?.message || String(error))
        }
      });
    } finally {
      endCapabilityOperation(record, operation);
    }
  }

  function dispatchCancellationIntent(instance, request, intent, baseDetails) {
    const record = assertApplicationInstance(instance, "dispatch-cancellation");
    const operationId = safeString(request.operationId);
    if (
      instance.appliedOperationIds.includes(operationId)
      || record.activeOperations.has(operationId)
      || record.receipts.some((receipt) => receipt.operationId === operationId)
    ) {
      return refusalResult(instance, request, {...baseDetails, code: "SCM_DUPLICATE_OPERATION"});
    }
    if (request.expectedRevision !== instance.revision) {
      return refusalResult(instance, request, {...baseDetails, code: "SCM_STALE_REVISION"});
    }
    const targetIntentId = safeString(intent.contract?.cancels);
    const targetIntent = resolveIntent(record.stored.draft, targetIntentId);
    if (!targetIntent || targetIntent.kind !== "async-capability") {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_ASYNC_OPERATION_CANCEL_TARGET_INVALID",
        message: `Cancellation intent ${intent.id} references invalid async target ${targetIntentId || "<empty>"}.`
      });
    }
    const payload = cloneValue(request.payload || {});
    let itemKey;
    try {
      itemKey = capabilityItemKey(targetIntent, payload, "dispatch-cancellation");
    } catch (error) {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(error?.violation?.code || "APPLICATION_ASYNC_OPERATION_CONCURRENCY_KEY_INVALID"),
        message: safeString(error?.violation?.message || error?.message),
        violation: error?.violation || null
      });
    }
    const concurrencyKey = capabilityOperationKey(targetIntent.id, itemKey);
    const target = record.activeOperationKeys.get(concurrencyKey);
    if (!target || target.status !== "running") {
      return refusalResult(instance, request, {
        ...baseDetails,
        code: "APPLICATION_ASYNC_OPERATION_NOT_ACTIVE",
        message: `No active ${targetIntent.id} operation exists for item ${itemKey}.`
      });
    }
    stopCapabilityOperation(instance, target, "cancelled", `Cancelled by ${operationId}.`);
    const receipt = buildReceipt(instance, request, {
      status: "cancelled",
      code: "APPLICATION_ASYNC_OPERATION_CANCELLED",
      message: `Cancelled ${target.operationId} for item ${itemKey}.`,
      beforeRevision: baseDetails.beforeRevision,
      beforeState: baseDetails.beforeState,
      afterRevision: instance.revision,
      afterState: instance.state,
      adapter: {
        cancellation: {
          targetIntentId,
          targetOperationId: target.operationId,
          itemKey,
          concurrencyKey,
          canonicalStateUnchanged: true,
          provisionalStateClosed: true
        }
      }
    });
    return deepFreeze({
      kind: "mcel-application-operation-result",
      contractVersion: CONTRACT_VERSION,
      ok: false,
      status: "cancelled",
      code: receipt.code,
      appId: instance.appId,
      operationId,
      intentId: intent.id,
      revision: instance.revision,
      state: cloneValue(instance.state),
      provisionalRevision: record.provisionalRevision,
      provisionalState: cloneValue(record.provisionalState),
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
      const payload = cloneValue(request.payload || {});
      const preflight = record.stored.draft.adapter.preflight({
        intentId: intent.id,
        input: {...payload, payload: cloneValue(payload), expectedRevision},
        state: cloneValue(beforeState)
      });
      return refusalResult(instance, request, {
        ...baseDetails,
        code: safeString(preflight?.code || "APPLICATION_INTENT_PROHIBITED"),
        adapter: {preflight: cloneValue(preflight || {})}
      });
    }
    if (intent.kind === "async-capability") {
      return dispatchCapabilityIntent(instance, request, intent, baseDetails);
    }
    if (intent.kind === "cancel-operation") {
      return dispatchCancellationIntent(instance, request, intent, baseDetails);
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

    const payload = cloneValue(request.payload || {});
    const input = {...payload, payload: cloneValue(payload), expectedRevision};
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
      let derivedViolation = null;
      try {
        record.derivedState = computeDerivedState(
          record.stored.draft,
          instance.state,
          record.localState,
          "canonical-commit"
        );
        record.derivedViolation = null;
      } catch (error) {
        derivedViolation = error?.violation || violation("APPLICATION_DERIVED_STATE_COMPUTE_FAILED", {
          phase: "canonical-commit",
          appId: instance.appId,
          instanceId: instance.id,
          message: safeString(error?.message || error)
        });
        record.derivedViolation = derivedViolation;
      }
      const receipt = buildReceipt(instance, request, {
        status: "committed",
        beforeRevision,
        beforeState,
        afterRevision: instance.revision,
        afterState: instance.state,
        adapter: {
          adapterId: safeString(record.stored.draft.adapter.adapterId),
          preflight: cloneValue(preflight),
          effectsValidated: true,
          derivedState: {ok: derivedViolation === null, violation: cloneValue(derivedViolation)}
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
      stateAuthorities: {
        canonical: cloneValue(instance.state),
        rendererLocal: cloneValue(record.localState),
        derived: cloneValue(record.derivedState),
        provisional: cloneValue(record.provisionalState)
      },
      localRevision: record.localRevision,
      provisionalRevision: record.provisionalRevision,
      activeOperations: [...record.activeOperations.values()].map((operation) => ({
        operationId: operation.operationId,
        intentId: operation.intentId,
        provisionalPath: operation.provisionalPath,
        startedAt: operation.startedAt,
        expectedRevision: operation.expectedRevision,
        payload: cloneValue(operation.payload),
        itemKey: operation.itemKey,
        concurrency: operation.concurrency,
        concurrencyKey: operation.concurrencyKey,
        eventCount: operation.eventCount,
        ignoredEventCount: operation.ignoredEventCount,
        status: operation.status,
        reason: operation.reason,
        signalAborted: operation.abortController?.signal?.aborted === true
      })),
      derivedViolation: cloneValue(record.derivedViolation),
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

  const SUPPORTED_PROJECTION_PROPERTIES = new Set(["textContent", "disabled"]);
  const SUPPORTED_PROJECTION_TRANSFORMS = new Set(["", "string", "not"]);
  const SUPPORTED_CONDITIONAL_PREDICATES = new Set(["empty", "nonempty", "truthy", "falsy", "equals", "not-equals"]);
  const SUPPORTED_COLLECTION_FIELD_PROPERTIES = new Set(["textContent", "value"]);
  const SUPPORTED_COLLECTION_FIELD_TRANSFORMS = new Set(["", "string", "currency-integer"]);

  function knownViewStateRoots(definition) {
    return new Set([
      ...Object.keys(isPlainObject(definition?.domain?.initialState) ? definition.domain.initialState : {}),
      ...(Array.isArray(definition?.localStateIds) ? definition.localStateIds : []),
      ...(Array.isArray(definition?.derivedStateIds) ? definition.derivedStateIds : []),
      ...(Array.isArray(definition?.provisionalStateIds) ? definition.provisionalStateIds : [])
    ]);
  }

  function normalizeProjectionBinding(appId, nodeId, binding, knownStateRoots) {
    if (!isPlainObject(binding)) {
      throwViolation("APPLICATION_PROPERTY_BINDING_INVALID", {
        phase: "mount-package",
        appId,
        nodeId,
        message: `Application ${appId} property binding for ${nodeId} must be an object.`
      });
    }
    const statePath = normalizeApplicationStatePath(binding.statePath);
    const stateRoot = applicationPathParts(statePath)[0];
    const property = safeString(binding.property);
    const transform = safeString(binding.transform);
    if (!statePath || !stateRoot) {
      throwViolation("APPLICATION_PROPERTY_STATE_PATH_REQUIRED", {
        phase: "mount-package",
        appId,
        nodeId,
        message: `Application ${appId} property binding for ${nodeId} requires a state path.`
      });
    }
    if (!knownStateRoots.has(stateRoot)) {
      throwViolation("APPLICATION_PROPERTY_STATE_PATH_UNKNOWN", {
        phase: "mount-package",
        appId,
        nodeId,
        statePath,
        message: `Application ${appId} property binding for ${nodeId} references unknown state ${statePath}.`
      });
    }
    if (!SUPPORTED_PROJECTION_PROPERTIES.has(property)) {
      throwViolation("APPLICATION_PROPERTY_UNSUPPORTED", {
        phase: "mount-package",
        appId,
        nodeId,
        property,
        message: `Application ${appId} property binding for ${nodeId} declares unsupported property ${property || "<empty>"}.`
      });
    }
    if (!SUPPORTED_PROJECTION_TRANSFORMS.has(transform)) {
      throwViolation("APPLICATION_PROPERTY_TRANSFORM_UNSUPPORTED", {
        phase: "mount-package",
        appId,
        nodeId,
        transform,
        message: `Application ${appId} property binding for ${nodeId} declares unsupported transform ${transform || "<empty>"}.`
      });
    }
    if (property === "disabled" && transform === "string") {
      throwViolation("APPLICATION_PROPERTY_TRANSFORM_INVALID", {
        phase: "mount-package",
        appId,
        nodeId,
        property,
        transform,
        message: `Application ${appId} disabled projection for ${nodeId} cannot use string transform.`
      });
    }
    return Object.freeze({statePath, property, transform});
  }

  function projectionValue(value, binding, appId, nodeId) {
    let projected = cloneValue(value);
    if (binding.transform === "string") projected = projected === undefined || projected === null ? "" : String(projected);
    if (binding.transform === "not") projected = !Boolean(projected);
    if (binding.property === "textContent") return projected === undefined || projected === null ? "" : String(projected);
    if (binding.property === "disabled") {
      if (typeof projected !== "boolean") {
        throwViolation("APPLICATION_PROPERTY_VALUE_INVALID", {
          phase: "render-application",
          appId,
          nodeId,
          property: binding.property,
          message: `Application ${appId} disabled projection for ${nodeId} requires a boolean value.`
        });
      }
      return projected;
    }
    return projected;
  }

  function writeProjectionProperty(element, binding, value, appId, nodeId) {
    const projected = projectionValue(value, binding, appId, nodeId);
    if (binding.property === "textContent") element.textContent = projected;
    if (binding.property === "disabled") {
      element.disabled = projected;
      if (typeof element.setAttribute === "function" && typeof element.removeAttribute === "function") {
        if (projected) element.setAttribute("disabled", "");
        else element.removeAttribute("disabled");
      }
    }
  }

  function isProjectionEmpty(value) {
    if (value === undefined || value === null) return true;
    if (typeof value === "string" || Array.isArray(value)) return value.length === 0;
    if (isPlainObject(value)) return Object.keys(value).length === 0;
    return false;
  }

  function conditionalPredicateMatches(value, when) {
    const predicate = safeString(when?.predicate);
    if (predicate === "empty") return isProjectionEmpty(value);
    if (predicate === "nonempty") return !isProjectionEmpty(value);
    if (predicate === "truthy") return Boolean(value);
    if (predicate === "falsy") return !Boolean(value);
    if (predicate === "equals") return valuesEqual(value, when?.value);
    if (predicate === "not-equals") return !valuesEqual(value, when?.value);
    return false;
  }

  function clearConditionalHost(host) {
    if (typeof host.replaceChildren === "function") {
      host.replaceChildren();
      return;
    }
    if (typeof host.removeChild === "function") {
      while (host.firstChild) host.removeChild(host.firstChild);
      return;
    }
    if (Array.isArray(host.children)) host.children.splice(0, host.children.length);
  }

  function instantiateConditionalTemplate(projected, appId) {
    const {element: host, conditional, contract} = projected;
    const template = conditional.template;
    if (!template?.content || typeof template.content.cloneNode !== "function" || typeof host.appendChild !== "function") {
      throwViolation("APPLICATION_CONDITIONAL_TEMPLATE_NOT_CLONEABLE", {
        phase: "render-application",
        appId,
        nodeId: contract.id,
        templateId: contract.templateId,
        message: `Application ${appId} conditional template ${contract.templateId} cannot be cloned.`
      });
    }
    clearConditionalHost(host);
    host.appendChild(template.content.cloneNode(true));
    conditional.instanceRoot = host.firstElementChild || host.firstChild || (Array.isArray(host.children) ? host.children[0] : null);
    if (!conditional.instanceRoot) {
      throwViolation("APPLICATION_CONDITIONAL_TEMPLATE_EMPTY", {
        phase: "render-application",
        appId,
        nodeId: contract.id,
        templateId: contract.templateId,
        message: `Application ${appId} conditional template ${contract.templateId} produced no content.`
      });
    }
    conditional.active = true;
  }

  function conditionalSource(contract, state, result) {
    if (safeString(contract.statePath)) return readApplicationPath(state, contract.statePath);
    const receiptPath = safeString(contract?.source?.fromLatestReceipt);
    if (receiptPath) return readApplicationPath(result?.receipt || {}, receiptPath);
    return undefined;
  }

  function renderConditional(projected, state, result, appId) {
    const {contract, element: host, conditional} = projected;
    const sourceValue = conditionalSource(contract, state, result);
    const active = conditionalPredicateMatches(sourceValue, contract.when);
    if (!active) {
      if (conditional.active) clearConditionalHost(host);
      conditional.active = false;
      conditional.instanceRoot = null;
      return;
    }
    if (!conditional.active) instantiateConditionalTemplate(projected, appId);
    const content = isPlainObject(contract.content) ? contract.content : {};
    if (Object.prototype.hasOwnProperty.call(content, "literal")) {
      conditional.instanceRoot.textContent = String(content.literal ?? "");
    } else if (safeString(content.property || "textContent") === "textContent") {
      conditional.instanceRoot.textContent = sourceValue === undefined || sourceValue === null ? "" : String(sourceValue);
    }
  }

  function querySelectorMatches(root, selector, code, details = {}) {
    if (!root || typeof root.querySelectorAll !== "function" || !safeString(selector)) {
      throwViolation(code, {
        ...details,
        selector: safeString(selector),
        message: details.message || `MCEL collection selector ${safeString(selector) || "<empty>"} is invalid.`
      });
    }
    try {
      return Array.from(root.querySelectorAll(selector));
    } catch (error) {
      throwViolation(code, {
        ...details,
        selector: safeString(selector),
        cause: safeString(error?.message || error),
        message: details.message || `MCEL collection selector ${safeString(selector)} is invalid.`
      });
    }
  }

  function collectionTemplateRoot(template, appId, nodeId, templateId, phase) {
    if (!template?.content || typeof template.content.cloneNode !== "function") {
      throwViolation("APPLICATION_COLLECTION_TEMPLATE_NOT_CLONEABLE", {
        phase,
        appId,
        nodeId,
        templateId,
        message: `Application ${appId} collection template ${templateId} cannot be cloned.`
      });
    }
    const fragment = template.content.cloneNode(true);
    let roots = [];
    if (fragment?.children) roots = Array.from(fragment.children);
    else if (fragment?.childNodes) roots = Array.from(fragment.childNodes).filter((entry) => entry?.nodeType === 1);
    else {
      const root = fragment?.firstElementChild || fragment?.firstChild || null;
      if (root) roots = [root];
    }
    if (roots.length !== 1) {
      throwViolation("APPLICATION_COLLECTION_TEMPLATE_ROOT_INVALID", {
        phase,
        appId,
        nodeId,
        templateId,
        rootCount: roots.length,
        message: `Application ${appId} collection template ${templateId} must produce exactly one root element.`
      });
    }
    return roots[0];
  }

  function normalizeCollectionProjection(appId, node, element, knownStateRoots, intentIds, root) {
    if (element.getAttribute("data-mcel-collection-host") === null) {
      throwViolation("APPLICATION_COLLECTION_HOST_REQUIRED", {
        phase: "mount-package",
        appId,
        nodeId: node.id,
        message: `Application ${appId} collection ${node.id} requires data-mcel-collection-host.`
      });
    }
    const statePath = normalizeApplicationStatePath(node.statePath);
    const keyPath = normalizeApplicationStatePath(node.keyPath);
    if (!statePath || !knownStateRoots.has(applicationPathParts(statePath)[0])) {
      throwViolation("APPLICATION_COLLECTION_STATE_PATH_UNKNOWN", {
        phase: "mount-package",
        appId,
        nodeId: node.id,
        statePath,
        message: `Application ${appId} collection ${node.id} references unknown state ${statePath || "<empty>"}.`
      });
    }
    if (!keyPath) {
      throwViolation("APPLICATION_COLLECTION_KEY_PATH_REQUIRED", {
        phase: "mount-package",
        appId,
        nodeId: node.id,
        message: `Application ${appId} collection ${node.id} requires a stable item key path.`
      });
    }
    const templateId = safeString(node.templateId);
    const templates = byDeclaredIdentity(root, "data-mcel-template-id", templateId);
    if (!templateId || templates.length !== 1) {
      throwViolation("APPLICATION_COLLECTION_TEMPLATE_MISSING", {
        phase: "mount-package",
        appId,
        nodeId: node.id,
        templateId,
        message: `Application ${appId} collection ${node.id} requires one declared template ${templateId || "<empty>"}.`
      });
    }
    const sampleRoot = collectionTemplateRoot(templates[0], appId, node.id, templateId, "mount-package");
    const item = isPlainObject(node.item) ? node.item : {};
    const fieldEntries = Object.entries(isPlainObject(item.fields) ? item.fields : {});
    const controlEntries = Object.entries(isPlainObject(item.controls) ? item.controls : {});
    const fields = new Map();
    const controls = new Map();
    const usedSampleElements = new Set();

    fieldEntries.forEach(([name, declaration]) => {
      const selector = safeString(declaration?.selector);
      const itemPath = normalizeApplicationStatePath(declaration?.itemPath);
      const property = safeString(declaration?.property || "textContent");
      const transform = safeString(declaration?.transform);
      if (!safeString(name) || !isPlainObject(declaration) || !selector || !itemPath) {
        throwViolation("APPLICATION_COLLECTION_ITEM_FIELD_INVALID", {
          phase: "mount-package", appId, nodeId: node.id, field: name,
          message: `Application ${appId} collection ${node.id} has an invalid item field ${name || "<empty>"}.`
        });
      }
      if (!SUPPORTED_COLLECTION_FIELD_PROPERTIES.has(property)) {
        throwViolation("APPLICATION_COLLECTION_ITEM_PROPERTY_UNSUPPORTED", {
          phase: "mount-package", appId, nodeId: node.id, field: name, property,
          message: `Application ${appId} collection field ${name} declares unsupported property ${property || "<empty>"}.`
        });
      }
      if (!SUPPORTED_COLLECTION_FIELD_TRANSFORMS.has(transform)) {
        throwViolation("APPLICATION_COLLECTION_ITEM_TRANSFORM_UNSUPPORTED", {
          phase: "mount-package", appId, nodeId: node.id, field: name, transform,
          message: `Application ${appId} collection field ${name} declares unsupported transform ${transform || "<empty>"}.`
        });
      }
      if (property === "value" && transform === "currency-integer") {
        throwViolation("APPLICATION_COLLECTION_ITEM_TRANSFORM_INVALID", {
          phase: "mount-package", appId, nodeId: node.id, field: name, property, transform,
          message: `Application ${appId} collection field ${name} cannot apply currency formatting to an input value.`
        });
      }
      const matches = querySelectorMatches(sampleRoot, selector, "APPLICATION_COLLECTION_ITEM_FIELD_MISMATCH", {
        phase: "mount-package", appId, nodeId: node.id, field: name,
        message: `Application ${appId} collection field ${name} must match exactly one template element.`
      });
      if (matches.length !== 1 || usedSampleElements.has(matches[0])) {
        throwViolation("APPLICATION_COLLECTION_ITEM_FIELD_MISMATCH", {
          phase: "mount-package", appId, nodeId: node.id, field: name, selector, matchCount: matches.length,
          message: `Application ${appId} collection field ${name} must match one unique template element.`
        });
      }
      usedSampleElements.add(matches[0]);
      let provisional = null;
      if (declaration.provisional !== undefined) {
        const provisionalDeclaration = declaration.provisional;
        const provisionalPath = normalizeApplicationStatePath(provisionalDeclaration?.statePath);
        const valuePath = normalizeApplicationStatePath(provisionalDeclaration?.valuePath);
        const provisionalTransform = safeString(provisionalDeclaration?.transform);
        const fallback = safeString(provisionalDeclaration?.fallback || "item");
        if (
          !isPlainObject(provisionalDeclaration)
          || !provisionalPath
          || !knownStateRoots.has(applicationPathParts(provisionalPath)[0])
          || provisionalDeclaration.keyFromItem !== true
          || !valuePath
          || provisionalTransform !== "quote-progress"
          || fallback !== "item"
        ) {
          throwViolation("APPLICATION_COLLECTION_ITEM_PROVISIONAL_INVALID", {
            phase: "mount-package", appId, nodeId: node.id, field: name,
            message: `Application ${appId} collection field ${name} has an invalid provisional overlay.`
          });
        }
        provisional = Object.freeze({
          statePath: provisionalPath,
          keyFromItem: true,
          valuePath,
          transform: provisionalTransform,
          fallback
        });
      }
      fields.set(name, Object.freeze({name, selector, itemPath, property, transform, provisional}));
    });

    controlEntries.forEach(([name, declaration]) => {
      const selector = safeString(declaration?.selector);
      const intentId = safeString(declaration?.intentId);
      if (!safeString(name) || !isPlainObject(declaration) || !selector || !intentIds.has(intentId)) {
        throwViolation("APPLICATION_COLLECTION_ITEM_CONTROL_INVALID", {
          phase: "mount-package", appId, nodeId: node.id, control: name, intentId,
          message: `Application ${appId} collection ${node.id} has an invalid item control ${name || "<empty>"}.`
        });
      }
      const matches = querySelectorMatches(sampleRoot, selector, "APPLICATION_COLLECTION_ITEM_CONTROL_MISMATCH", {
        phase: "mount-package", appId, nodeId: node.id, control: name,
        message: `Application ${appId} collection control ${name} must match exactly one template element.`
      });
      if (matches.length !== 1 || usedSampleElements.has(matches[0])) {
        throwViolation("APPLICATION_COLLECTION_ITEM_CONTROL_MISMATCH", {
          phase: "mount-package", appId, nodeId: node.id, control: name, selector, matchCount: matches.length,
          message: `Application ${appId} collection control ${name} must match one unique template element.`
        });
      }
      usedSampleElements.add(matches[0]);
      const payload = isPlainObject(declaration.payload) ? declaration.payload : {};
      Object.entries(payload).forEach(([field, source]) => {
        const fromKey = source?.fromItemKey === true;
        const fromField = safeString(source?.fromItemField);
        if (!safeString(field) || !isPlainObject(source) || (fromKey ? 1 : 0) + (fromField ? 1 : 0) !== 1) {
          throwViolation("APPLICATION_COLLECTION_ITEM_PAYLOAD_SOURCE_INVALID", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field,
            message: `Application ${appId} collection control ${name} has an invalid payload source for ${field || "<empty>"}.`
          });
        }
        if (fromField && !fields.has(fromField)) {
          throwViolation("APPLICATION_COLLECTION_ITEM_PAYLOAD_FIELD_UNKNOWN", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field, itemField: fromField,
            message: `Application ${appId} collection control ${name} references unknown item field ${fromField}.`
          });
        }
        if (fromField && fields.get(fromField)?.property !== "value") {
          throwViolation("APPLICATION_COLLECTION_ITEM_PAYLOAD_FIELD_NOT_READABLE", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field, itemField: fromField,
            message: `Application ${appId} collection control ${name} cannot read non-value item field ${fromField}.`
          });
        }
        if (fromField && safeString(source.property || "value") !== "value") {
          throwViolation("APPLICATION_COLLECTION_ITEM_PAYLOAD_PROPERTY_UNSUPPORTED", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field, property: safeString(source.property),
            message: `Application ${appId} collection control ${name} may currently read only item-field value.`
          });
        }
        if (source.normalize !== undefined && safeString(source.normalize) !== "trim") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_NORMALIZER_UNKNOWN", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field, normalizer: safeString(source.normalize),
            message: `Application ${appId} collection control ${name} declares an unknown payload normalizer.`
          });
        }
        if (source.parse !== undefined && safeString(source.parse) !== "integer") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSER_UNKNOWN", {
            phase: "mount-package", appId, nodeId: node.id, control: name, field, parser: safeString(source.parse),
            message: `Application ${appId} collection control ${name} declares an unknown payload parser.`
          });
        }
      });
      controls.set(name, Object.freeze({name, selector, intentId, payload: cloneValue(payload)}));
    });

    return {
      template: templates[0], templateId, statePath, keyPath, fields, controls,
      instances: new Map(), controlBindings: new WeakMap(), initialized: false
    };
  }

  function collectionItemKey(item, collection, appId, nodeId) {
    const raw = readApplicationPath(item, collection.keyPath);
    if (raw === undefined || raw === null || (typeof raw !== "string" && typeof raw !== "number")) {
      throwViolation("APPLICATION_COLLECTION_ITEM_KEY_INVALID", {
        phase: "render-application", appId, nodeId, keyPath: collection.keyPath,
        message: `Application ${appId} collection ${nodeId} produced an invalid item key.`
      });
    }
    const key = String(raw);
    if (!key) {
      throwViolation("APPLICATION_COLLECTION_ITEM_KEY_INVALID", {
        phase: "render-application", appId, nodeId, keyPath: collection.keyPath,
        message: `Application ${appId} collection ${nodeId} produced an empty item key.`
      });
    }
    return {raw: cloneValue(raw), key};
  }

  function renderCollectionField(element, field, item, itemKey, provisionalState, appId, nodeId) {
    let value = readApplicationPath(item, field.itemPath);
    if (field.provisional) {
      const provisionalMap = readApplicationPath(provisionalState, field.provisional.statePath);
      const provisionalEntry = isPlainObject(provisionalMap) ? provisionalMap[itemKey] : null;
      if (isPlainObject(provisionalEntry)) {
        const provisionalValue = readApplicationPath(provisionalEntry, field.provisional.valuePath);
        if (provisionalValue !== undefined) {
          if (field.provisional.transform === "quote-progress") {
            const received = Number.isSafeInteger(provisionalEntry.received) ? provisionalEntry.received : 0;
            const expected = Number.isSafeInteger(provisionalEntry.expected) ? provisionalEntry.expected : 0;
            value = expected > 0 ? `${provisionalValue} ${received}/${expected}` : String(provisionalValue);
          } else {
            value = provisionalValue;
          }
        }
      }
    }
    if (field.transform === "string") value = value === undefined || value === null ? "" : String(value);
    if (field.transform === "currency-integer") {
      if (!Number.isSafeInteger(value)) {
        throwViolation("APPLICATION_COLLECTION_ITEM_VALUE_INVALID", {
          phase: "render-application", appId, nodeId, field: field.name,
          message: `Application ${appId} collection field ${field.name} requires a safe integer currency value.`
        });
      }
      value = `$${value}`;
    }
    if (field.property === "textContent") element.textContent = value === undefined || value === null ? "" : String(value);
    if (field.property === "value") element.value = value === undefined || value === null ? "" : String(value);
  }

  function instantiateCollectionItem(projected, item, keyInfo, appId) {
    const {contract, element: host, collection} = projected;
    const root = collectionTemplateRoot(collection.template, appId, contract.id, collection.templateId, "render-application");
    if (typeof root.setAttribute === "function") {
      root.setAttribute("data-mcel-collection-key", keyInfo.key);
      root.setAttribute("data-mcel-runtime-node-id", `${contract.id}:${keyInfo.key}`);
    }
    const fields = new Map();
    const controls = new Map();
    collection.fields.forEach((field, name) => {
      const matches = querySelectorMatches(root, field.selector, "APPLICATION_COLLECTION_ITEM_FIELD_MISMATCH", {
        phase: "render-application", appId, nodeId: contract.id, field: name,
        message: `Application ${appId} collection item field ${name} is missing or duplicated.`
      });
      if (matches.length !== 1) {
        throwViolation("APPLICATION_COLLECTION_ITEM_FIELD_MISMATCH", {
          phase: "render-application", appId, nodeId: contract.id, field: name, matchCount: matches.length,
          message: `Application ${appId} collection item field ${name} is missing or duplicated.`
        });
      }
      fields.set(name, matches[0]);
    });
    const instance = {key: keyInfo.key, rawKey: keyInfo.raw, root, fields, controls, item: cloneValue(item)};
    collection.controls.forEach((control, name) => {
      const matches = querySelectorMatches(root, control.selector, "APPLICATION_COLLECTION_ITEM_CONTROL_MISMATCH", {
        phase: "render-application", appId, nodeId: contract.id, control: name,
        message: `Application ${appId} collection item control ${name} is missing or duplicated.`
      });
      if (matches.length !== 1) {
        throwViolation("APPLICATION_COLLECTION_ITEM_CONTROL_MISMATCH", {
          phase: "render-application", appId, nodeId: contract.id, control: name, matchCount: matches.length,
          message: `Application ${appId} collection item control ${name} is missing or duplicated.`
        });
      }
      controls.set(name, matches[0]);
      collection.controlBindings.set(matches[0], {projected, instance, control, element: matches[0]});
    });
    if (typeof host.appendChild !== "function") {
      throwViolation("APPLICATION_COLLECTION_HOST_NOT_MUTABLE", {
        phase: "render-application", appId, nodeId: contract.id,
        message: `Application ${appId} collection host ${contract.id} cannot accept runtime items.`
      });
    }
    host.appendChild(root);
    return instance;
  }

  function removeCollectionRoot(host, root) {
    if (!root) return;
    if (typeof host.removeChild === "function") {
      try { host.removeChild(root); return; } catch (_error) { /* fall through */ }
    }
    if (typeof root.remove === "function") root.remove();
    else if (Array.isArray(host.children)) {
      const index = host.children.indexOf(root);
      if (index >= 0) host.children.splice(index, 1);
    }
  }

  function clearCollectionHost(projected) {
    const {element: host, collection} = projected;
    [...collection.instances.values()].forEach((instance) => removeCollectionRoot(host, instance.root));
    collection.instances.clear();
    if (!collection.initialized) {
      if (typeof host.replaceChildren === "function") host.replaceChildren();
      else if (Array.isArray(host.children)) host.children.splice(0, host.children.length);
    }
    collection.initialized = true;
  }

  function renderCollection(projected, state, provisionalState, appId) {
    const {contract, element: host, collection} = projected;
    const items = readApplicationPath(state, collection.statePath);
    if (!Array.isArray(items)) {
      throwViolation("APPLICATION_COLLECTION_STATE_NOT_ARRAY", {
        phase: "render-application", appId, nodeId: contract.id, statePath: collection.statePath,
        message: `Application ${appId} collection ${contract.id} requires array state at ${collection.statePath}.`
      });
    }
    if (!collection.initialized) clearCollectionHost(projected);
    const required = [];
    const seen = new Set();
    const keyedItems = items.map((item) => {
      const keyInfo = collectionItemKey(item, collection, appId, contract.id);
      if (seen.has(keyInfo.key)) {
        throwViolation("APPLICATION_COLLECTION_ITEM_KEY_DUPLICATE", {
          phase: "render-application", appId, nodeId: contract.id, itemKey: keyInfo.key,
          message: `Application ${appId} collection ${contract.id} produced duplicate key ${keyInfo.key}.`
        });
      }
      seen.add(keyInfo.key);
      return {item, keyInfo};
    });
    keyedItems.forEach(({item, keyInfo}) => {
      let instance = collection.instances.get(keyInfo.key);
      if (!instance) instance = instantiateCollectionItem(projected, item, keyInfo, appId);
      instance.item = cloneValue(item);
      instance.rawKey = cloneValue(keyInfo.raw);
      instance.fields.forEach((element, name) => renderCollectionField(
        element,
        collection.fields.get(name),
        item,
        keyInfo.key,
        provisionalState,
        appId,
        contract.id
      ));
      required.push(instance);
    });
    [...collection.instances.entries()].forEach(([key, instance]) => {
      if (!seen.has(key)) removeCollectionRoot(host, instance.root);
    });
    collection.instances.clear();
    required.forEach((instance) => {
      collection.instances.set(instance.key, instance);
      if (typeof host.appendChild === "function") host.appendChild(instance.root);
    });
  }

  function collectionControlBinding(collection, target, host) {
    let cursor = target || null;
    while (cursor && cursor !== host) {
      const binding = collection.controlBindings.get(cursor);
      if (binding) return binding;
      cursor = cursor.parentElement || cursor.parentNode || null;
    }
    return null;
  }

  function normalizeSurfaceContract(appId, surface, layout, intents, root, definition) {
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
    const localStateIds = new Set(Array.isArray(definition?.localStateIds) ? definition.localStateIds : []);
    const knownStateRoots = knownViewStateRoots(definition);
    const nodes = [];
    const nodeIds = new Set();
    const nodeById = new Map();
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
      const element = matches[0];
      if (kind === "control") {
        const intentId = safeString(node.intentId);
        if (!intentIds.has(intentId) || safeString(element.getAttribute("data-mcel-intent-id")) !== intentId) {
          throwViolation("APPLICATION_SURFACE_CONTROL_MISMATCH", {
            phase: "mount-package",
            appId,
            nodeId: id,
            intentId,
            message: `Application ${appId} control ${id} does not bind the declared intent.`
          });
        }
      } else if (kind === "input") {
        const localPath = normalizeApplicationStatePath(node.localPath);
        const inputType = safeString(node.inputType).toLowerCase();
        if (!localPath || localPath.includes(".")) {
          throwViolation("APPLICATION_INPUT_LOCAL_PATH_REQUIRED", {
            phase: "mount-package",
            appId,
            nodeId: id,
            message: `Application ${appId} input ${id} requires a top-level renderer-local path.`
          });
        }
        if (!localStateIds.has(localPath)) {
          throwViolation("APPLICATION_INPUT_LOCAL_PATH_UNKNOWN", {
            phase: "mount-package",
            appId,
            nodeId: id,
            localPath,
            message: `Application ${appId} input ${id} references unknown renderer-local state ${localPath}.`
          });
        }
        if (!new Set(["text", "search", "number", "select"]).has(inputType)) {
          throwViolation("APPLICATION_INPUT_TYPE_UNSUPPORTED", {
            phase: "mount-package",
            appId,
            nodeId: id,
            inputType,
            message: `Application ${appId} input ${id} declares unsupported input type ${inputType || "<empty>"}.`
          });
        }
        const tagName = safeString(element.tagName).toLowerCase();
        const actualType = safeString(element.getAttribute("type") || "text").toLowerCase();
        const compatible = !tagName
          || (inputType === "select" ? tagName === "select" : tagName === "input" && actualType === inputType);
        if (!compatible) {
          throwViolation("APPLICATION_INPUT_ELEMENT_MISMATCH", {
            phase: "mount-package",
            appId,
            nodeId: id,
            inputType,
            tagName,
            actualType,
            message: `Application ${appId} input ${id} does not match its declared input type ${inputType}.`
          });
        }
      } else if (kind === "state-value" && !safeString(node.statePath)) {
        throwViolation("APPLICATION_SURFACE_STATE_PATH_REQUIRED", {
          phase: "mount-package",
          appId,
          nodeId: id,
          message: `Application ${appId} state node ${id} requires statePath.`
        });
      } else if (kind === "property") {
        // Property bindings are normalized below.
      } else if (kind === "conditional") {
        if (element.getAttribute("data-mcel-conditional-host") === null) {
          throwViolation("APPLICATION_CONDITIONAL_HOST_REQUIRED", {
            phase: "mount-package",
            appId,
            nodeId: id,
            message: `Application ${appId} conditional ${id} requires data-mcel-conditional-host.`
          });
        }
        const statePath = normalizeApplicationStatePath(node.statePath);
        const receiptPath = safeString(node?.source?.fromLatestReceipt);
        if ((statePath ? 1 : 0) + (receiptPath ? 1 : 0) !== 1) {
          throwViolation("APPLICATION_CONDITIONAL_SOURCE_INVALID", {
            phase: "mount-package",
            appId,
            nodeId: id,
            message: `Application ${appId} conditional ${id} requires exactly one state or latest-receipt source.`
          });
        }
        if (statePath && !knownStateRoots.has(applicationPathParts(statePath)[0])) {
          throwViolation("APPLICATION_CONDITIONAL_STATE_PATH_UNKNOWN", {
            phase: "mount-package",
            appId,
            nodeId: id,
            statePath,
            message: `Application ${appId} conditional ${id} references unknown state ${statePath}.`
          });
        }
        const predicate = safeString(node?.when?.predicate);
        if (!SUPPORTED_CONDITIONAL_PREDICATES.has(predicate)) {
          throwViolation("APPLICATION_CONDITIONAL_PREDICATE_UNSUPPORTED", {
            phase: "mount-package",
            appId,
            nodeId: id,
            predicate,
            message: `Application ${appId} conditional ${id} declares unsupported predicate ${predicate || "<empty>"}.`
          });
        }
        const content = isPlainObject(node.content) ? node.content : {};
        if (content.property !== undefined && safeString(content.property) !== "textContent") {
          throwViolation("APPLICATION_CONDITIONAL_CONTENT_UNSUPPORTED", {
            phase: "mount-package",
            appId,
            nodeId: id,
            property: safeString(content.property),
            message: `Application ${appId} conditional ${id} may currently project only textContent.`
          });
        }
      } else if (kind === "collection") {
        // Collection contracts are normalized below after the shared node checks.
      } else if (!new Set(["state-value", "operation-evidence"]).has(kind)) {
        throwViolation("APPLICATION_SURFACE_NODE_KIND_UNSUPPORTED", {
          phase: "mount-package",
          appId,
          nodeId: id,
          kind,
          message: `Application ${appId} surface node kind ${kind || "<empty>"} is not supported by the generic projection.`
        });
      }

      let propertyBindings = [];
      if (kind === "property") {
        propertyBindings = [normalizeProjectionBinding(appId, id, node, knownStateRoots)];
      } else if (node.properties !== undefined) {
        if (!Array.isArray(node.properties)) {
          throwViolation("APPLICATION_PROPERTY_BINDINGS_INVALID", {
            phase: "mount-package",
            appId,
            nodeId: id,
            message: `Application ${appId} node ${id} properties must be an array.`
          });
        }
        propertyBindings = node.properties.map((entry) => normalizeProjectionBinding(appId, id, entry, knownStateRoots));
      }

      let conditional = null;
      if (kind === "conditional") {
        const templateId = safeString(node.templateId);
        const templates = byDeclaredIdentity(root, "data-mcel-template-id", templateId);
        if (!templateId || templates.length !== 1) {
          throwViolation("APPLICATION_CONDITIONAL_TEMPLATE_MISSING", {
            phase: "mount-package",
            appId,
            nodeId: id,
            templateId,
            message: `Application ${appId} conditional ${id} requires one declared template ${templateId || "<empty>"}.`
          });
        }
        conditional = {template: templates[0], active: false, instanceRoot: null};
      }
      const collection = kind === "collection"
        ? normalizeCollectionProjection(appId, node, element, knownStateRoots, intentIds, root)
        : null;

      nodeIds.add(id);
      const projected = {contract: node, element, propertyBindings, conditional, collection};
      nodes.push(projected);
      nodeById.set(id, projected);
    });

    nodes.filter(({contract}) => contract.kind === "control").forEach(({contract}) => {
      const payload = contract.payload === undefined ? {} : contract.payload;
      if (!isPlainObject(payload)) {
        throwViolation("APPLICATION_CONTROL_PAYLOAD_INVALID", {
          phase: "mount-package",
          appId,
          nodeId: contract.id,
          message: `Application ${appId} control ${contract.id} payload declaration must be an object.`
        });
      }
      Object.entries(payload).forEach(([field, source]) => {
        if (!safeString(field) || !isPlainObject(source)) {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_INVALID", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            message: `Application ${appId} control ${contract.id} has an invalid payload field declaration.`
          });
        }
        if (source.fromItemKey || source.fromItemField) {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_DYNAMIC_SOURCE_UNSUPPORTED", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            message: `Application ${appId} static control ${contract.id} cannot use dynamic item payload sources.`
          });
        }
        const sourceId = safeString(source.fromNode);
        const sourceNode = nodeById.get(sourceId);
        if (!sourceId || !sourceNode || sourceNode.contract.kind !== "input") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_SOURCE_MISSING", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            sourceNodeId: sourceId,
            message: `Application ${appId} control ${contract.id} payload field ${field} references a missing input node.`
          });
        }
        if (safeString(source.property || "value") !== "value") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_PROPERTY_UNSUPPORTED", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            property: safeString(source.property),
            message: `Application ${appId} control ${contract.id} payload field ${field} may currently read only input value.`
          });
        }
        if (source.normalize !== undefined && safeString(source.normalize) !== "trim") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_NORMALIZER_UNKNOWN", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            normalizer: safeString(source.normalize),
            message: `Application ${appId} control ${contract.id} payload field ${field} declares an unknown normalizer.`
          });
        }
        if (source.parse !== undefined && safeString(source.parse) !== "integer") {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSER_UNKNOWN", {
            phase: "mount-package",
            appId,
            nodeId: contract.id,
            field,
            parser: safeString(source.parse),
            message: `Application ${appId} control ${contract.id} payload field ${field} declares an unknown parser.`
          });
        }
      });
    });
    return {surface, layout, nodes, nodeById};
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

  function inputElementValue(element) {
    if (element && "value" in Object(element)) return String(element.value ?? "");
    return safeString(element?.getAttribute?.("value"));
  }

  function writeInputElementValue(element, value) {
    if (!element) return;
    element.value = value === undefined || value === null ? "" : String(value);
  }

  function inputEventName(contract) {
    return safeString(contract?.inputType).toLowerCase() === "select" ? "change" : "input";
  }

  function extractControlPayload(appId, projection, contract) {
    const payload = {};
    Object.entries(isPlainObject(contract?.payload) ? contract.payload : {}).forEach(([field, source]) => {
      const sourceNodeId = safeString(source.fromNode);
      const sourceNode = projection.nodeById.get(sourceNodeId);
      if (!sourceNode || sourceNode.contract.kind !== "input") {
        throwViolation("APPLICATION_CONTROL_PAYLOAD_SOURCE_MISSING", {
          phase: "extract-control-payload",
          appId,
          nodeId: contract.id,
          field,
          sourceNodeId,
          message: `Application ${appId} control ${contract.id} payload field ${field} references a missing input node.`
        });
      }
      let value = inputElementValue(sourceNode.element);
      if (safeString(source.normalize) === "trim") value = value.trim();
      if (safeString(source.parse) === "integer") {
        if (!/^[+-]?\d+$/.test(value)) {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED", {
            phase: "extract-control-payload",
            appId,
            nodeId: contract.id,
            field,
            parser: "integer",
            value,
            message: `Application ${appId} control ${contract.id} payload field ${field} is not a valid integer.`
          });
        }
        const parsed = Number(value);
        if (!Number.isSafeInteger(parsed)) {
          throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED", {
            phase: "extract-control-payload",
            appId,
            nodeId: contract.id,
            field,
            parser: "integer",
            value,
            message: `Application ${appId} control ${contract.id} payload field ${field} is outside the safe integer range.`
          });
        }
        value = parsed;
      }
      payload[field] = cloneValue(value);
    });
    return deepFreeze(payload);
  }

  function normalizePayloadValue(appId, nodeId, field, source, value, phase) {
    let normalized = value;
    if (safeString(source?.normalize) === "trim") normalized = String(normalized ?? "").trim();
    if (safeString(source?.parse) === "integer") {
      const text = String(normalized ?? "");
      if (!/^[+-]?\d+$/.test(text)) {
        throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED", {
          phase, appId, nodeId, field, parser: "integer", value: text,
          message: `Application ${appId} control ${nodeId} payload field ${field} is not a valid integer.`
        });
      }
      const parsed = Number(text);
      if (!Number.isSafeInteger(parsed)) {
        throwViolation("APPLICATION_CONTROL_PAYLOAD_PARSE_FAILED", {
          phase, appId, nodeId, field, parser: "integer", value: text,
          message: `Application ${appId} control ${nodeId} payload field ${field} is outside the safe integer range.`
        });
      }
      normalized = parsed;
    }
    return cloneValue(normalized);
  }

  function extractItemControlPayload(appId, application, binding) {
    const {projected, instance, control} = binding;
    const {contract, collection} = projected;
    const currentItems = readApplicationPath(application.readViewState(), collection.statePath);
    const stillCurrent = Array.isArray(currentItems) && currentItems.some((item) => {
      try { return collectionItemKey(item, collection, appId, contract.id).key === instance.key; }
      catch (_error) { return false; }
    });
    if (!stillCurrent) {
      throwViolation("APPLICATION_COLLECTION_ITEM_KEY_STALE", {
        phase: "extract-item-control-payload", appId, nodeId: contract.id, itemKey: instance.key,
        message: `Application ${appId} collection item ${instance.key} is stale.`
      });
    }
    const payload = {};
    Object.entries(isPlainObject(control.payload) ? control.payload : {}).forEach(([field, source]) => {
      if (source.fromItemKey === true) {
        payload[field] = cloneValue(instance.rawKey);
        return;
      }
      const itemField = safeString(source.fromItemField);
      const fieldElement = instance.fields.get(itemField);
      if (!fieldElement) {
        throwViolation("APPLICATION_COLLECTION_ITEM_PAYLOAD_FIELD_MISSING", {
          phase: "extract-item-control-payload", appId, nodeId: contract.id, itemKey: instance.key, field, itemField,
          message: `Application ${appId} collection item ${instance.key} is missing payload field ${itemField}.`
        });
      }
      const raw = safeString(source.property || "value") === "value"
        ? inputElementValue(fieldElement)
        : undefined;
      payload[field] = normalizePayloadValue(appId, `${contract.id}:${control.name}`, field, source, raw, "extract-item-control-payload");
    });
    return deepFreeze(payload);
  }

  function bindingRefusal(application, intentId, error) {
    const violationEntry = error?.violation || violation("APPLICATION_BINDING_FAILED", {
      phase: "application-binding",
      appId: application.appId,
      message: safeString(error?.message || error) || "MCEL application binding failed."
    });
    return deepFreeze({
      kind: "mcel-application-binding-result",
      contractVersion: CONTRACT_VERSION,
      ok: false,
      status: "refused",
      code: safeString(violationEntry.code || "APPLICATION_BINDING_FAILED"),
      appId: application.appId,
      intentId: safeString(intentId),
      revision: application.revision,
      state: cloneValue(application.state),
      receipt: null,
      violation: cloneValue(violationEntry)
    });
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
    const [domain, intents, adapter, surface, layout, observation] = await Promise.all([
      loadDeclaredModule(manifestUrl, manifest.modules.domain, loader, "domain"),
      loadDeclaredModule(manifestUrl, manifest.modules.intents, loader, "intents"),
      loadDeclaredModule(manifestUrl, manifest.modules.adapter, loader, "adapter"),
      loadDeclaredModule(manifestUrl, manifest.modules.surface, loader, "surface"),
      loadDeclaredModule(manifestUrl, manifest.modules.layout, loader, "layout"),
      manifest.modules.observation
        ? loadDeclaredModule(manifestUrl, manifest.modules.observation, loader, "observation")
        : Promise.resolve(null)
    ]);
    const appId = packageRecord.appId;
    if (
      safeString(domain?.appId) !== appId
      || safeString(adapter?.appId) !== appId
      || safeString(surface?.appId) !== appId
      || (observation && safeString(observation?.appId) !== appId)
    ) {
      throwViolation("APPLICATION_RUNTIME_MODULE_IDENTITY_MISMATCH", {
        phase: "mount-package",
        appId,
        message: `Application ${appId} runtime module identities do not agree.`
      });
    }
    const root = resolveMountRoot(request, manifest);
    let definition = applicationDefinition(appId);
    if (!definition || request.replaceDefinition === true) {
      definition = defineApplication({appId, domain, intents, adapter}, {replace: request.replaceDefinition === true});
    }
    const projection = normalizeSurfaceContract(appId, surface, layout, intents, root, definition);
    const application = createApplicationInstance(definition, {
      id: request.instanceId,
      state: cloneValue(request.state || {}),
      localState: cloneValue(request.localState || {}),
      provisionalState: cloneValue(request.provisionalState || {}),
      capabilities: request.capabilities || {}
    });
    const listeners = [];
    let active = true;
    let lastResult = null;
    const operationIdFactory = typeof request.operationIdFactory === "function"
      ? request.operationIdFactory
      : ({intentId}) => `${appId}:${intentId}:${nextMountOperationId++}`;

    function render(result = lastResult) {
      const state = application.readViewState();
      projection.nodes.forEach((projected) => {
        const {contract, element, propertyBindings} = projected;
        if (contract.kind === "state-value") {
          const value = readStateProjection(state, contract.statePath);
          element.textContent = value === undefined ? "" : String(value);
        } else if (contract.kind === "input") {
          writeInputElementValue(element, readApplicationPath(state, contract.localPath));
        } else if (contract.kind === "conditional") {
          renderConditional(projected, state, result, appId);
        } else if (contract.kind === "collection") {
          renderCollection(projected, state, application.readProvisionalState(), appId);
        } else if (contract.kind === "operation-evidence" && result?.receipt) {
          element.textContent = JSON.stringify(result.receipt, null, 2);
        }
        propertyBindings.forEach((binding) => {
          writeProjectionProperty(
            element,
            binding,
            readApplicationPath(state, binding.statePath),
            appId,
            contract.id
          );
        });
      });
      if (!result) {
        setMountStatus(root, "mounted", `MCEL application ${appId} mounted at revision ${application.revision}.`);
      } else if (result.status === "running") {
        setMountStatus(root, "running", `${result.intentId} is running at revision ${result.revision}.`);
      } else if (result.ok) {
        setMountStatus(root, "committed", `${result.intentId} committed at revision ${result.revision}.`);
      } else if (result.status === "cancelled" || result.status === "superseded") {
        setMountStatus(root, result.status, `${result.intentId || "operation"} ${result.status}: ${result.code}.`);
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
      const dispatched = application.dispatch({
        operationId,
        expectedRevision,
        intentId,
        payload: {...cloneValue(payload || {}), expectedRevision},
        onProgress(progress) {
          if (!active) return;
          lastResult = progress;
          render(lastResult);
        }
      });
      if (dispatched && typeof dispatched.then === "function") {
        return dispatched.then((result) => {
          if (active) {
            lastResult = result;
            render(lastResult);
          }
          return result;
        });
      }
      lastResult = dispatched;
      render(lastResult);
      return lastResult;
    }

    const inputs = projection.nodes.filter(({contract}) => contract.kind === "input");
    const controls = projection.nodes.filter(({contract}) => contract.kind === "control");
    const collections = projection.nodes.filter(({contract}) => contract.kind === "collection");
    [...inputs, ...controls, ...collections].forEach(({contract, element}) => {
      if (typeof element.addEventListener !== "function" || typeof element.removeEventListener !== "function") {
        const code = contract.kind === "input"
          ? "APPLICATION_INPUT_NOT_INTERACTIVE"
          : contract.kind === "collection"
            ? "APPLICATION_COLLECTION_HOST_NOT_INTERACTIVE"
            : "APPLICATION_SURFACE_CONTROL_NOT_INTERACTIVE";
        throwViolation(code, {
          phase: "mount-package",
          appId,
          nodeId: contract.id,
          message: `Application ${appId} ${contract.kind} ${contract.id} is not interactive.`
        });
      }
    });
    inputs.forEach(({contract, element}) => {
      const eventName = inputEventName(contract);
      const listener = () => {
        try {
          application.updateLocalState({[contract.localPath]: inputElementValue(element)});
          render(lastResult);
        } catch (error) {
          writeInputElementValue(element, readApplicationPath(application.readLocalState(), contract.localPath));
          lastResult = bindingRefusal(application, "", error);
          render(lastResult);
        }
      };
      element.addEventListener(eventName, listener);
      listeners.push({element, eventName, listener});
    });
    controls.forEach(({contract, element}) => {
      const listener = () => {
        if (element.disabled === true) return null;
        let payload;
        try {
          payload = extractControlPayload(appId, projection, contract);
        } catch (error) {
          lastResult = bindingRefusal(application, contract.intentId, error);
          render(lastResult);
          return lastResult;
        }
        return dispatch(contract.intentId, payload);
      };
      element.addEventListener("click", listener);
      listeners.push({element, eventName: "click", listener});
    });
    collections.forEach((projected) => {
      const {contract, element: host, collection} = projected;
      const listener = (event = {}) => {
        const binding = collectionControlBinding(collection, event.target, host);
        if (!binding || binding.element?.disabled === true) return null;
        let payload;
        try {
          payload = extractItemControlPayload(appId, application, binding);
        } catch (error) {
          lastResult = bindingRefusal(application, binding.control.intentId, error);
          render(lastResult);
          return lastResult;
        }
        return dispatch(binding.control.intentId, payload);
      };
      host.addEventListener("click", listener);
      listeners.push({element: host, eventName: "click", listener});
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
      observation: observation ? deepFreeze(cloneValue(observation)) : null,
      dispatch,
      render,
      readState() {
        return application.readState();
      },
      readLocalState() {
        return application.readLocalState();
      },
      readDerivedState() {
        return application.readDerivedState();
      },
      readProvisionalState() {
        return application.readProvisionalState();
      },
      readViewState() {
        return application.readViewState();
      },
      readLastResult() {
        return deepFreeze(cloneValue(lastResult));
      },
      updateLocalState(patch = {}) {
        const result = application.updateLocalState(patch);
        render(lastResult);
        return result;
      },
      unmount() {
        if (!active) return false;
        abortApplicationOperations(application, "unmounted");
        listeners.forEach(({element, eventName, listener}) => element.removeEventListener(eventName, listener));
        projection.nodes.filter(({contract}) => contract.kind === "conditional").forEach((projected) => {
          clearConditionalHost(projected.element);
          projected.conditional.active = false;
          projected.conditional.instanceRoot = null;
        });
        projection.nodes.filter(({contract}) => contract.kind === "collection").forEach((projected) => {
          [...projected.collection.instances.values()].forEach((instance) => removeCollectionRoot(projected.element, instance.root));
          projected.collection.instances.clear();
        });
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
    readApplicationLocalState,
    readApplicationDerivedState,
    readApplicationProvisionalState,
    readApplicationViewState,
    updateApplicationLocalState,
    createApplicationOperation,
    dispatchApplicationIntent,
    abortApplicationOperations,
    exportApplicationEvidence,
    mountApplicationPackage,
    applicationPackageMount
  });
})();

if (typeof window !== "undefined") {
  window.McelApplicationRuntime = McelApplicationRuntime;
}
