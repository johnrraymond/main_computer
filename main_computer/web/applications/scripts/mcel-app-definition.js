var McelAppDefinition = (() => {
  "use strict";

  const CONTRACT_VERSION = "mcel.application-definition.v1";
  const DESCRIPTOR = Symbol("mcel-application-definition-descriptor");
  const APP_ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
  const IDENTIFIER_PATTERN = /^[A-Za-z][A-Za-z0-9_.:-]*$/;
  const STATE_PATH_PATTERN = /^[A-Za-z0-9_$-]+(?:\.[A-Za-z0-9_$-]+)*$/;

  function safeString(value) {
    return String(value == null ? "" : value).trim();
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
      value.forEach((entry, index) => {
        copy[index] = cloneValue(entry, seen);
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
    Reflect.ownKeys(value).forEach((key) => deepFreeze(value[key], seen));
    return Object.freeze(value);
  }

  function fail(code, message, details = {}) {
    const error = new Error(message || code);
    error.name = "McelApplicationDefinitionError";
    error.code = code;
    error.details = deepFreeze(cloneValue(details));
    throw error;
  }

  function descriptor(kind, fields = {}) {
    return deepFreeze({[DESCRIPTOR]: true, kind, ...fields});
  }

  function isDescriptor(value, kind = "") {
    return Boolean(value && value[DESCRIPTOR] === true && (!kind || value.kind === kind));
  }

  function assertIdentifier(value, code, label) {
    const normalized = safeString(value);
    if (!IDENTIFIER_PATTERN.test(normalized)) {
      fail(code, `${label} must be a stable MCEL identifier.`, {value});
    }
    return normalized;
  }

  function normalizeStatePath(value) {
    const raw = safeString(value).replace(/^state\./, "");
    if (!STATE_PATH_PATTERN.test(raw) || raw.includes("__proto__") || raw.includes("constructor") || raw.includes("prototype")) {
      fail("MCEL_APP_STATE_PATH_INVALID", `Invalid application state path ${raw || "<empty>"}.`, {path: value});
    }
    return raw;
  }

  function schemaDescriptor(name, validate, describe = {}) {
    return descriptor("schema", {name, validate, describe: deepFreeze(cloneValue(describe))});
  }

  const schema = Object.freeze({
    any() {
      return schemaDescriptor("any", () => true);
    },
    string(options = {}) {
      const minLength = Number.isSafeInteger(options.minLength) ? options.minLength : 0;
      const maxLength = Number.isSafeInteger(options.maxLength) ? options.maxLength : null;
      return schemaDescriptor("string", (value) => (
        typeof value === "string"
        && value.length >= minLength
        && (maxLength === null || value.length <= maxLength)
      ), {minLength, maxLength});
    },
    integer(options = {}) {
      const minimum = Number.isSafeInteger(options.minimum) ? options.minimum : null;
      const maximum = Number.isSafeInteger(options.maximum) ? options.maximum : null;
      return schemaDescriptor("integer", (value) => (
        Number.isSafeInteger(value)
        && (minimum === null || value >= minimum)
        && (maximum === null || value <= maximum)
      ), {minimum, maximum});
    },
    boolean() {
      return schemaDescriptor("boolean", (value) => typeof value === "boolean");
    },
    oneOf(...values) {
      const allowed = values.flat().map(cloneValue);
      return schemaDescriptor("one-of", (value) => allowed.some((candidate) => Object.is(candidate, value)), {allowed});
    },
    array(itemSchema = null) {
      const item = itemSchema || schema.any();
      if (!isDescriptor(item, "schema")) fail("MCEL_APP_SCHEMA_REQUIRED", "Array item schema is invalid.");
      return schemaDescriptor("array", (value) => Array.isArray(value) && value.every((entry) => item.validate(entry)), {item: item.name});
    },
    object(shape = {}) {
      if (!isPlainObject(shape)) fail("MCEL_APP_OBJECT_SCHEMA_INVALID", "Object schema shape must be an object.");
      Object.entries(shape).forEach(([key, child]) => {
        if (!isDescriptor(child, "schema")) fail("MCEL_APP_SCHEMA_REQUIRED", `Object field ${key} requires a schema.`);
      });
      return schemaDescriptor("object", (value) => (
        isPlainObject(value)
        && Object.entries(shape).every(([key, child]) => child.validate(value[key]))
      ), {fields: Object.keys(shape).sort()});
    }
  });

  function stateDescriptor(authority, initial, options = {}) {
    const validator = options.schema || schema.any();
    if (!isDescriptor(validator, "schema")) fail("MCEL_APP_STATE_SCHEMA_INVALID", "State schema is invalid.");
    if (!validator.validate(initial)) {
      fail("MCEL_APP_STATE_INITIAL_INVALID", `Initial ${authority} state does not satisfy its schema.`, {initial});
    }
    return descriptor("state", {
      authority,
      initial: cloneValue(initial),
      schema: validator,
      description: safeString(options.description)
    });
  }

  const state = Object.freeze({
    canonical(initial, options = {}) {
      return stateDescriptor("canonical", initial, options);
    },
    provisional(initial, options = {}) {
      return stateDescriptor("provisional", initial, options);
    },
    local(initial, options = {}) {
      return stateDescriptor("renderer-local", initial, options);
    },
    derived(reads, compute, options = {}) {
      if (!Array.isArray(reads) || !reads.length || typeof compute !== "function") {
        fail("MCEL_APP_DERIVED_STATE_INVALID", "Derived state requires read paths and a compute function.");
      }
      const validator = options.schema || schema.any();
      if (!isDescriptor(validator, "schema")) fail("MCEL_APP_STATE_SCHEMA_INVALID", "Derived state schema is invalid.");
      return descriptor("state", {
        authority: "derived",
        reads: reads.map(normalizeStatePath),
        compute,
        schema: validator,
        description: safeString(options.description)
      });
    }
  });

  const source = Object.freeze({
    nodeValue(nodeId, options = {}) {
      return descriptor("payload-source", {
        sourceKind: "node-property",
        nodeId: assertIdentifier(nodeId, "MCEL_APP_NODE_ID_INVALID", "Payload source node"),
        property: safeString(options.property || "value"),
        parse: safeString(options.parse),
        normalize: safeString(options.normalize)
      });
    },
    itemKey() {
      return descriptor("payload-source", {sourceKind: "item-key"});
    },
    itemField(path, options = {}) {
      return descriptor("payload-source", {
        sourceKind: "item-field",
        path: normalizeStatePath(path),
        property: safeString(options.property || "value"),
        parse: safeString(options.parse),
        normalize: safeString(options.normalize)
      });
    },
    state(path) {
      return descriptor("payload-source", {sourceKind: "state-path", path: normalizeStatePath(path)});
    },
    literal(value) {
      return descriptor("payload-source", {sourceKind: "literal", value: cloneValue(value)});
    },
    latestReceipt(path) {
      return descriptor("projection-source", {sourceKind: "latest-receipt", path: normalizeStatePath(path)});
    }
  });

  function capability(id, specification = {}) {
    const capabilityId = assertIdentifier(id, "MCEL_APP_CAPABILITY_ID_INVALID", "Capability id");
    const operations = isPlainObject(specification.operations) ? specification.operations : {};
    const normalized = {};
    Object.keys(operations).sort().forEach((name) => {
      const operationId = assertIdentifier(name, "MCEL_APP_CAPABILITY_OPERATION_INVALID", "Capability operation");
      const operation = operations[name] || {};
      normalized[operationId] = deepFreeze({
        request: operation.request || schema.any(),
        response: operation.response || schema.any(),
        stream: operation.stream === true,
        cancellable: operation.cancellable === true
      });
    });
    return descriptor("capability", {
      id: capabilityId,
      risk: safeString(specification.risk || "external-read"),
      operations: normalized,
      description: safeString(specification.description)
    });
  }

  function normalizePayload(payload) {
    if (!isPlainObject(payload)) return {};
    const normalized = {};
    Object.keys(payload).sort().forEach((key) => {
      const entry = payload[key];
      if (!isDescriptor(entry, "payload-source") && !isDescriptor(entry, "schema")) {
        fail("MCEL_APP_PAYLOAD_SOURCE_INVALID", `Payload field ${key} must use a declared source or schema.`);
      }
      normalized[key] = entry;
    });
    return normalized;
  }

  function operationDescriptor(operationKind, specification = {}) {
    if (!isPlainObject(specification)) fail("MCEL_APP_OPERATION_INVALID", "Operation specification must be an object.");
    return descriptor("operation", {
      operationKind,
      risk: safeString(specification.risk || (operationKind === "prohibited" ? "prohibited" : "local-state")),
      reads: Array.isArray(specification.reads) ? specification.reads.map(normalizeStatePath) : [],
      writes: Array.isArray(specification.writes) ? specification.writes.map(normalizeStatePath) : [],
      payload: normalizePayload(specification.payload),
      uses: Array.isArray(specification.uses) ? specification.uses.map(safeString).filter(Boolean) : [],
      provisionalPath: specification.provisionalPath ? normalizeStatePath(specification.provisionalPath) : "",
      concurrency: safeString(specification.concurrency || "serial-per-application"),
      cancellable: specification.cancellable === true,
      cancels: safeString(specification.cancels),
      reason: safeString(specification.reason),
      preflight: specification.preflight,
      transition: specification.transition,
      ensures: specification.ensures,
      run: specification.run,
      receive: specification.receive,
      commit: specification.commit,
      cancel: specification.cancel
    });
  }

  const operation = Object.freeze({
    mutation(specification = {}) {
      return operationDescriptor("mutation", specification);
    },
    async(specification = {}) {
      return operationDescriptor("async", specification);
    },
    cancel(specification = {}) {
      return operationDescriptor("cancel", specification);
    },
    prohibited(specification = {}) {
      return operationDescriptor("prohibited", specification);
    }
  });

  function nodeDescriptor(nodeKind, specification = {}) {
    if (!isPlainObject(specification)) fail("MCEL_APP_NODE_INVALID", "Surface node specification must be an object.");
    return descriptor("surface-node", {
      nodeKind,
      id: assertIdentifier(specification.id, "MCEL_APP_NODE_ID_INVALID", "Surface node id"),
      regionId: assertIdentifier(specification.regionId, "MCEL_APP_REGION_ID_INVALID", "Surface region id"),
      statePath: specification.statePath ? normalizeStatePath(specification.statePath) : "",
      property: safeString(specification.property),
      transform: safeString(specification.transform),
      inputType: safeString(specification.inputType),
      localPath: specification.localPath ? normalizeStatePath(specification.localPath) : "",
      intentId: safeString(specification.intentId),
      payload: normalizePayload(specification.payload),
      source: specification.source || null,
      templateId: safeString(specification.templateId),
      when: cloneValue(specification.when || {}),
      content: cloneValue(specification.content || {}),
      keyPath: specification.keyPath ? normalizeStatePath(specification.keyPath) : "",
      item: cloneValue(specification.item || {}),
      accessibility: cloneValue(specification.accessibility || {})
    });
  }

  const node = Object.freeze({
    input(specification) {
      return nodeDescriptor("input", specification);
    },
    property(specification) {
      return nodeDescriptor("property", specification);
    },
    control(specification) {
      return nodeDescriptor("control", specification);
    },
    conditional(specification) {
      return nodeDescriptor("conditional", specification);
    },
    collection(specification) {
      return nodeDescriptor("collection", specification);
    },
    receipt(specification) {
      return nodeDescriptor("operation-evidence", specification);
    }
  });

  function invariant(id, check, options = {}) {
    if (typeof check !== "function") fail("MCEL_APP_INVARIANT_CHECK_REQUIRED", "Invariant requires a check function.");
    return descriptor("invariant", {
      id: assertIdentifier(id, "MCEL_APP_INVARIANT_ID_INVALID", "Invariant id"),
      reads: Array.isArray(options.reads) ? options.reads.map(normalizeStatePath) : [],
      check,
      description: safeString(options.description)
    });
  }

  function acceptance(id, specification = {}) {
    return descriptor("acceptance", {
      id: assertIdentifier(id, "MCEL_APP_ACCEPTANCE_ID_INVALID", "Acceptance id"),
      acceptanceKind: safeString(specification.kind || "workflow"),
      operationId: safeString(specification.operationId),
      given: cloneValue(specification.given || {}),
      when: cloneValue(specification.when || {}),
      expect: cloneValue(specification.expect || {})
    });
  }

  function observe(id, specification = {}) {
    return descriptor("observation", {
      id: assertIdentifier(id, "MCEL_APP_OBSERVATION_ID_INVALID", "Observation id"),
      observationKind: safeString(specification.kind || "property"),
      source: safeString(specification.source || "browser-dom"),
      nodeId: safeString(specification.nodeId),
      statePath: specification.statePath ? normalizeStatePath(specification.statePath) : "",
      property: safeString(specification.property),
      normalization: safeString(specification.normalization),
      keyPath: specification.keyPath ? normalizeStatePath(specification.keyPath) : "",
      fields: cloneValue(specification.fields || {}),
      requireOrderMatch: specification.requireOrderMatch === true,
      requireItemControls: cloneValue(specification.requireItemControls || []),
      compareToLatestReceiptPath: safeString(specification.compareToLatestReceiptPath),
      compareToStatePredicate: cloneValue(specification.compareToStatePredicate || null),
      compareToProvisionalStatePath: safeString(specification.compareToProvisionalStatePath),
      compareToOperationReceipt: specification.compareToOperationReceipt === true,
      minimumInstances: Number.isInteger(specification.minimumInstances) ? specification.minimumInstances : 0,
      requireIsolated: cloneValue(specification.requireIsolated || []),
      expect: cloneValue(specification.expect)
    });
  }

  function surface(specification = {}) {
    if (!isPlainObject(specification)) fail("MCEL_APP_SURFACE_INVALID", "Surface specification must be an object.");
    const regions = Array.isArray(specification.regions) ? specification.regions.map((region) => deepFreeze({
      id: assertIdentifier(region.id, "MCEL_APP_REGION_ID_INVALID", "Surface region id"),
      role: safeString(region.role)
    })) : [];
    const nodes = Array.isArray(specification.nodes) ? specification.nodes : [];
    if (!regions.length || !nodes.length || !nodes.every((entry) => isDescriptor(entry, "surface-node"))) {
      fail("MCEL_APP_SURFACE_INCOMPLETE", "Surface requires declared regions and descriptor-backed nodes.");
    }
    return descriptor("surface", {
      id: assertIdentifier(specification.id, "MCEL_APP_SURFACE_ID_INVALID", "Surface id"),
      root: safeString(specification.root),
      regions,
      nodes
    });
  }

  function listItemControlIntents(item = {}) {
    const controls = isPlainObject(item.controls) ? item.controls : {};
    return Object.values(controls).map((entry) => safeString(entry && entry.intentId)).filter(Boolean);
  }

  function collectRequiredRuntimeFeatures(application) {
    const features = new Set();
    Object.values(application.state).forEach((entry) => {
      if (entry.authority === "renderer-local") features.add("renderer-local-state");
      if (entry.authority === "provisional") features.add("provisional-state");
      if (entry.authority === "derived") features.add("derived-state");
    });
    Object.values(application.operations).forEach((entry) => {
      if (Object.keys(entry.payload).length) features.add("control-payload-extraction");
      if (entry.operationKind === "async") features.add("capability-operation-runtime");
      if (entry.operationKind === "cancel" || entry.cancellable) features.add("operation-cancellation");
      if (entry.operationKind === "async" && entry.provisionalPath) features.add("provisional-state-runtime");
      if (entry.concurrency !== "serial-per-application") features.add("operation-concurrency-policy");
    });
    application.surface.nodes.forEach((entry) => {
      if (entry.nodeKind === "input") features.add("dynamic-input-binding");
      if (entry.nodeKind === "property") features.add("dynamic-property-projection");
      if (entry.nodeKind === "conditional") features.add("conditional-projection");
      if (entry.nodeKind === "collection") {
        features.add("keyed-collection-reconciliation");
        if (listItemControlIntents(entry.item).length) features.add("dynamic-item-control-binding");
      }
    });
    if (application.multiInstance && application.multiInstance.required === true) {
      features.add("multi-instance-proof");
    }
    if (application.observations.some((entry) => entry.observationKind === "collection" || entry.observationKind === "conditional")) {
      features.add("dynamic-browser-observation");
    }
    return [...features].sort();
  }

  function validateApplicationReferences(application) {
    const stateIds = new Set(Object.keys(application.state));
    const operationIds = new Set(Object.keys(application.operations));
    const regionIds = new Set(application.surface.regions.map((entry) => entry.id));
    const nodeIds = new Set();

    application.surface.nodes.forEach((entry) => {
      if (nodeIds.has(entry.id)) fail("MCEL_APP_NODE_ID_DUPLICATE", `Duplicate surface node ${entry.id}.`);
      nodeIds.add(entry.id);
      if (!regionIds.has(entry.regionId)) fail("MCEL_APP_NODE_REGION_UNKNOWN", `Surface node ${entry.id} references unknown region ${entry.regionId}.`);
      if (entry.intentId && !operationIds.has(entry.intentId)) fail("MCEL_APP_NODE_INTENT_UNKNOWN", `Surface node ${entry.id} references unknown operation ${entry.intentId}.`);
      if (entry.statePath && !stateIds.has(entry.statePath.split(".")[0])) fail("MCEL_APP_NODE_STATE_UNKNOWN", `Surface node ${entry.id} references unknown state ${entry.statePath}.`);
      if (entry.localPath && !stateIds.has(entry.localPath.split(".")[0])) fail("MCEL_APP_NODE_STATE_UNKNOWN", `Surface node ${entry.id} references unknown local state ${entry.localPath}.`);
      listItemControlIntents(entry.item).forEach((intentId) => {
        if (!operationIds.has(intentId)) fail("MCEL_APP_ITEM_INTENT_UNKNOWN", `Collection ${entry.id} references unknown item operation ${intentId}.`);
      });
    });

    Object.entries(application.operations).forEach(([id, entry]) => {
      [...entry.reads, ...entry.writes].forEach((path) => {
        if (!stateIds.has(path.split(".")[0])) fail("MCEL_APP_OPERATION_STATE_UNKNOWN", `Operation ${id} references unknown state ${path}.`);
      });
      entry.uses.forEach((capabilityId) => {
        if (!Object.prototype.hasOwnProperty.call(application.capabilities, capabilityId)) {
          fail("MCEL_APP_OPERATION_CAPABILITY_UNKNOWN", `Operation ${id} requires unknown capability ${capabilityId}.`);
        }
      });
    });

    application.observations.forEach((entry) => {
      if (entry.nodeId && !nodeIds.has(entry.nodeId)) fail("MCEL_APP_OBSERVATION_NODE_UNKNOWN", `Observation ${entry.id} references unknown node ${entry.nodeId}.`);
      if (entry.statePath && !stateIds.has(entry.statePath.split(".")[0])) fail("MCEL_APP_OBSERVATION_STATE_UNKNOWN", `Observation ${entry.id} references unknown state ${entry.statePath}.`);
    });
  }

  function defineApplication(specification = {}) {
    if (!isPlainObject(specification)) fail("MCEL_APP_DEFINITION_INVALID", "Application definition must be an object.");
    const appId = safeString(specification.id);
    if (!APP_ID_PATTERN.test(appId)) fail("MCEL_APP_ID_INVALID", "Application id must be lowercase hyphenated.", {appId});
    const title = safeString(specification.title);
    if (!title) fail("MCEL_APP_TITLE_REQUIRED", "Application title is required.");

    const normalizedState = {};
    Object.keys(specification.state || {}).sort().forEach((key) => {
      const entry = specification.state[key];
      if (!isDescriptor(entry, "state")) fail("MCEL_APP_STATE_DESCRIPTOR_REQUIRED", `State ${key} requires a state descriptor.`);
      normalizedState[key] = entry;
    });

    const normalizedCapabilities = {};
    Object.keys(specification.capabilities || {}).sort().forEach((key) => {
      const entry = specification.capabilities[key];
      if (!isDescriptor(entry, "capability")) fail("MCEL_APP_CAPABILITY_DESCRIPTOR_REQUIRED", `Capability ${key} requires a capability descriptor.`);
      normalizedCapabilities[key] = entry;
    });

    const normalizedOperations = {};
    Object.keys(specification.operations || {}).sort().forEach((key) => {
      const entry = specification.operations[key];
      if (!isDescriptor(entry, "operation")) fail("MCEL_APP_OPERATION_DESCRIPTOR_REQUIRED", `Operation ${key} requires an operation descriptor.`);
      normalizedOperations[key] = deepFreeze({...entry, id: key});
    });

    const normalizedSurface = specification.surface;
    if (!isDescriptor(normalizedSurface, "surface")) fail("MCEL_APP_SURFACE_DESCRIPTOR_REQUIRED", "Application requires a surface descriptor.");
    const normalizedInvariants = Array.isArray(specification.invariants) ? specification.invariants : [];
    if (!normalizedInvariants.every((entry) => isDescriptor(entry, "invariant"))) fail("MCEL_APP_INVARIANT_DESCRIPTOR_REQUIRED", "Invariant entries require descriptors.");
    const normalizedAcceptance = Array.isArray(specification.acceptance) ? specification.acceptance : [];
    if (!normalizedAcceptance.every((entry) => isDescriptor(entry, "acceptance"))) fail("MCEL_APP_ACCEPTANCE_DESCRIPTOR_REQUIRED", "Acceptance entries require descriptors.");
    const normalizedObservations = Array.isArray(specification.observations) ? specification.observations : [];
    if (!normalizedObservations.every((entry) => isDescriptor(entry, "observation"))) fail("MCEL_APP_OBSERVATION_DESCRIPTOR_REQUIRED", "Observation entries require descriptors.");

    const application = {
      schema: CONTRACT_VERSION,
      id: appId,
      title,
      state: normalizedState,
      capabilities: normalizedCapabilities,
      operations: normalizedOperations,
      surface: normalizedSurface,
      layout: deepFreeze(cloneValue(specification.layout || {})),
      invariants: normalizedInvariants,
      acceptance: normalizedAcceptance,
      observations: normalizedObservations,
      multiInstance: deepFreeze(cloneValue(specification.multiInstance || {required: false}))
    };
    validateApplicationReferences(application);
    application.requiredRuntimeFeatures = collectRequiredRuntimeFeatures(application);
    return deepFreeze(application);
  }

  function inspect(application) {
    if (!application || application.schema !== CONTRACT_VERSION) fail("MCEL_APP_DEFINITION_REQUIRED", "inspect requires an MCEL application definition.");
    const stateAuthorityCounts = {};
    Object.values(application.state).forEach((entry) => {
      stateAuthorityCounts[entry.authority] = (stateAuthorityCounts[entry.authority] || 0) + 1;
    });
    const nodeKindCounts = {};
    application.surface.nodes.forEach((entry) => {
      nodeKindCounts[entry.nodeKind] = (nodeKindCounts[entry.nodeKind] || 0) + 1;
    });
    const operationKindCounts = {};
    Object.values(application.operations).forEach((entry) => {
      operationKindCounts[entry.operationKind] = (operationKindCounts[entry.operationKind] || 0) + 1;
    });
    return deepFreeze({
      schema: "mcel.application-definition-inspection.v1",
      appId: application.id,
      title: application.title,
      stateAuthorityCounts,
      operationKindCounts,
      nodeKindCounts,
      capabilityCount: Object.keys(application.capabilities).length,
      invariantCount: application.invariants.length,
      acceptanceCount: application.acceptance.length,
      observationCount: application.observations.length,
      requiredRuntimeFeatures: [...application.requiredRuntimeFeatures]
    });
  }

  return Object.freeze({
    contractVersion: CONTRACT_VERSION,
    schema,
    state,
    source,
    capability,
    operation,
    node,
    surface,
    invariant,
    acceptance,
    observe,
    defineApplication,
    inspect
  });
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = McelAppDefinition;
}
