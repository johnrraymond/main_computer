"use strict";

/*
 * Restricted construction runtime for mcel.dsl.v1.
 *
 * This process evaluates one CommonJS DSL source inside a Node vm context that
 * exposes only the compiler-provided @mcel/app module. The resulting values are
 * portable semantic records. No application behavior is executed here.
 */

const crypto = require("node:crypto");
const path = require("node:path");
const vm = require("node:vm");

const RUNTIME_VERSION = "mcel-dsl-builder-wave2b";
const FRONTEND_ID = "mcel.dsl.v1";

class DslError extends Error {
  constructor(code, message, semanticPath = "$", details = undefined) {
    super(message);
    this.name = "DslError";
    this.code = code;
    this.semanticPath = semanticPath;
    this.details = details;
  }
}

function assert(condition, code, message, semanticPath = "$", details = undefined) {
  if (!condition) {
    throw new DslError(code, message, semanticPath, details);
  }
}

function semanticId(prefix, value) {
  assert(typeof value === "string" && value.length > 0, "MCEL_DSL_ID_REQUIRED", `A ${prefix} identifier is required.`);
  if (value.startsWith(`${prefix}:`)) return value;
  return `${prefix}:${value}`;
}

function ref(value) {
  const id = typeof value === "string" ? value : value && value.id;
  assert(typeof id === "string", "MCEL_DSL_REFERENCE_REQUIRED", "A semantic handle or semantic ID is required.");
  return {ref: id};
}

function camelCase(value) {
  assert(typeof value === "string" && value.length > 0, "MCEL_DSL_NAME_REQUIRED", "A semantic source name is required.");
  return value.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
}

function titleCaseIdentifier(value) {
  return String(value || "")
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join("");
}

function appScopedId(metadata, kind, name) {
  return semanticId(kind, `${metadata.id}.${name}`);
}

function appScopedRef(metadata, kind, name) {
  return {ref: appScopedId(metadata, kind, name)};
}

function normalizeSchema(schema, semanticPath) {
  if (schema instanceof SchemaBuilder) return schema.toJSON();
  assert(schema && typeof schema === "object" && !Array.isArray(schema), "MCEL_DSL_SCHEMA_REQUIRED", "A field schema is required.", semanticPath);
  return deepClone(schema, semanticPath);
}

function receiptSuccessClaim() {
  return {
    kind: "claim.receipt-disposition",
    authority: "operation-receipt",
    expected: "completed",
  };
}

function deepClone(value, semanticPath = "$") {
  if (value === null || ["string", "boolean", "number"].includes(typeof value)) {
    assert(!(typeof value === "number" && !Number.isFinite(value)), "MCEL_DSL_NONDETERMINISTIC_VALUE", "Non-finite numbers are forbidden.", semanticPath);
    return value;
  }
  if (Array.isArray(value)) return value.map((item, index) => deepClone(item, `${semanticPath}[${index}]`));
  if (typeof value === "object") {
    const result = {};
    for (const key of Object.keys(value).sort()) {
      result[key] = deepClone(value[key], `${semanticPath}.${key}`);
    }
    return result;
  }
  throw new DslError(
    "MCEL_DSL_NONPORTABLE_VALUE",
    `DSL output contains a nonportable ${typeof value} value.`,
    semanticPath,
  );
}

function sourceRecord(sourcePath, lineCount) {
  return {
    kind: "dsl-source-binding",
    frontend: FRONTEND_ID,
    file: sourcePath,
    start: {line: 1, column: 1},
    end: {line: Math.max(1, lineCount), column: 1},
  };
}

class SchemaBuilder {
  constructor(record) {
    this.record = record;
  }
  minimum(value) {
    assert(Number.isInteger(value), "MCEL_DSL_SCHEMA_MINIMUM_INVALID", "Integer minimum must be an integer.");
    return new SchemaBuilder({...this.record, minimum: value});
  }
  minLength(value) {
    assert(Number.isInteger(value) && value >= 0, "MCEL_DSL_SCHEMA_MIN_LENGTH_INVALID", "Minimum length must be a nonnegative integer.");
    return new SchemaBuilder({...this.record, minLength: value});
  }
  oneOf(values) {
    assert(this.record.kind === "string" && Array.isArray(values) && values.length > 0 &&
      values.every(value => typeof value === "string" && value.length > 0) &&
      new Set(values).size === values.length,
      "MCEL_DSL_ENUM_INVALID", "A string choice requires unique, nonempty values.");
    return new SchemaBuilder({...this.record, enum: deepClone(values)});
  }
  toJSON() {
    return deepClone(this.record);
  }
}

class StateHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
    this.name = record.sourceName;
  }
  read() {
    return {kind: "state.read", state: ref(this)};
  }
  set(value) {
    return {kind: "transition.assign", target: ref(this), value: expression(value)};
  }
  increment(amount = 1) {
    assert(Number.isInteger(amount), "MCEL_DSL_INCREMENT_INVALID", "Increment amount must be an integer.");
    return {kind: "number.increment", target: ref(this), amount};
  }
}

class InvariantHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
  }
}

class IntentHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
    this.name = record.sourceName;
  }
}

class CapabilityHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
    this.operations = new Map();
  }
  operation(name, runtimeMethod, options = {}) {
    assert(typeof name === "string" && /^[a-z][a-z0-9-]*$/.test(name),
      "MCEL_DSL_CAPABILITY_OPERATION_INVALID", "Capability operation requires a stable lowercase name.");
    assert(typeof runtimeMethod === "string" && /^[A-Za-z][A-Za-z0-9_.]*$/.test(runtimeMethod),
      "MCEL_DSL_CAPABILITY_METHOD_INVALID", "A capability operation requires a declared runtime method name.");
    assert(!this.operations.has(name), "MCEL_DSL_CAPABILITY_OPERATION_DUPLICATE", `Duplicate operation '${name}'.`);
    knownCapabilityOptions(options);
    const inputs = {};
    for (const [key, schema] of Object.entries(options.inputs || {})) {
      assert(/^[a-z][A-Za-z0-9_]*$/.test(key) && schema instanceof SchemaBuilder,
        "MCEL_DSL_CAPABILITY_INPUT_INVALID", "Capability inputs require named typed schemas.");
      inputs[key] = schema.toJSON();
    }
    const operation = {name, runtimeMethod,
      cancellable: options.cancellable === true,
      risk: options.risk || this.record.risk,
      inputs,
      ...(options.result ? {resultSchema: normalizeSchema(options.result, `capability:${this.id}.${name}.result`)} : {})};
    assert(operation.risk === this.record.risk,
      "MCEL_DSL_CAPABILITY_RISK_MISMATCH", "The operation's risk cannot differ from the declared capability authority.");
    this.record.operations.push(operation);
    this.operations.set(name, operation);
    return this;
  }
}
function knownCapabilityOptions(options) {
  assert(options && typeof options === "object" && !Array.isArray(options) &&
    Object.keys(options).every(key => ["risk", "result", "cancellable", "inputs"].includes(key)),
    "MCEL_DSL_CAPABILITY_OPTION_UNKNOWN", "Unsupported capability operation option.");
}

class SurfaceNodeHandle {
  constructor(localName, record) {
    this.localName = localName;
    this.record = record;
    this.id = record.id;
  }
}

class SurfaceHandle {
  constructor(record, nodesByName) {
    this.record = record;
    this.id = record.id;
    this.nodesByName = nodesByName;
  }
  node(name) {
    const node = this.nodesByName.get(name);
    assert(node, "MCEL_DSL_SURFACE_NODE_UNKNOWN", `Surface node '${name}' is not declared.`, `${this.id}/node:${name}`);
    return node;
  }
}

class LayoutHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
  }
}

class ScenarioHandle {
  constructor(record) {
    this.record = record;
    this.id = record.id;
  }
}

function expression(value) {
  if (value instanceof StateHandle) return value.read();
  if (value && value.__mcelExpression === true) return deepClone(value.record);
  if (value && typeof value === "object" && typeof value.kind === "string") return deepClone(value);
  return {kind: "constant", value: deepClone(value)};
}

function collectWriteTargets(value, result = new Set()) {
  if (Array.isArray(value)) {
    for (const item of value) collectWriteTargets(item, result);
    return result;
  }
  if (!value || typeof value !== "object") return result;
  if ([
    "transition.assign", "list.append", "list.remove-by-key", "list.update-by-key",
    "map.put", "map.remove", "number.increment", "number.add-to-state",
  ].includes(value.kind) && value.target && typeof value.target.ref === "string") {
    result.add(value.target.ref);
  }
  for (const item of Object.values(value)) collectWriteTargets(item, result);
  return result;
}

function transitionRole(step, stateName) {
  if (step && step.kind === "number.increment" && step.amount === 1) return `${stateName}-plus-one`;
  if (step && step.kind === "transition.assign" && step.value && step.value.kind === "constant" && step.value.value === 0) return `${stateName}-zero`;
  return `${stateName}-write`;
}

function findTransitionStepForTarget(transition, targetId) {
  if (!transition || typeof transition !== "object") return undefined;
  if (transition.target && transition.target.ref === targetId) return transition;
  if (Array.isArray(transition.steps)) {
    for (const step of transition.steps) {
      const found = findTransitionStepForTarget(step, targetId);
      if (found) return found;
    }
  }
  return undefined;
}

class ScenarioBuilder {
  constructor(name, options, source) {
    const id = semanticId("scenario", name);
    this.record = {
      id,
      kind: "scenario",
      source,
      steps: [],
    };
    if (options && options.intent) this.record.intent = ref(options.intent);
  }
  forIntent(intent) {
    this.record.intent = ref(intent);
    return this;
  }
  expect(...claims) {
    this.record.steps.push(...claims.flat().map((claim) => deepClone(claim)));
    return this;
  }
  build() {
    assert(this.record.intent, "MCEL_DSL_SCENARIO_INTENT_REQUIRED", "A Counter scenario must identify its intent.", this.record.id);
    return new ScenarioHandle(this.record);
  }
}

function createHostBoundApplicationBuilder(metadata) {
  let presentation = null;
  let proof = null;
  let zones = [];
  let semanticSurface = null;
  let layoutGrammar = null;
  let layoutTree = null;
  const states = [];
  const capabilities = [];
  const intents = [];
  const effects = [];
  const surfaceNodes = [];
  const scenarios = [];
  const proofInvariants = [];

  function stateRecord(name, authority, schema, initial) {
    const record = {
      id: appScopedId(metadata, "state", name),
      kind: "state",
      sourceName: camelCase(name),
      authority,
      schema: normalizeSchema(schema, `state:${name}.schema`),
      initial: deepClone(initial, `state:${name}.initial`),
    };
    states.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  function capabilityRecord(name, options = {}) {
    const operations = [];
    const record = {
      id: appScopedId(metadata, "capability", name),
      kind: "capability",
      sourceName: options.sourceName || camelCase(name),
      risk: options.risk || "external-read",
      description: options.description || "",
      operations,
    };
    capabilities.push(record);
    const handle = {
      id: record.id,
      name,
      record,
      operation(operationName, runtimeMethod, operationOptions = {}) {
        operations.push({
          name: operationName,
          runtimeMethod,
          cancellable: Boolean(operationOptions.cancellable || false),
        });
        return handle;
      },
    };
    return Object.freeze(handle);
  }

  function effectRecord(intent, capability) {
    const record = {
      id: appScopedId(metadata, "effect", `${intent.name}.request`),
      kind: "effect",
      effectKind: "capability-request",
      owner: appScopedRef(metadata, "intent", intent.name),
      risk: intent.risk,
      target: appScopedRef(metadata, "capability", capability.name),
      authority: appScopedRef(metadata, "capability", capability.name),
      cardinality: {minimum: 0, maximum: 1},
      allowedFinalDispositions: ["completed", "refused-before-attempt", "failed", "cancelled"],
      requiredEvidence: ["operation-receipt", "capability-response", "visible-outcome"],
      cleanupObligations: [],
    };
    effects.push(record);
    return record;
  }

  function intentRecord(name, options, capability = null) {
    assert(options && typeof options === "object", "MCEL_DSL_INTENT_OPTIONS_REQUIRED", `Intent '${name}' requires options.`);
    const effect = capability ? effectRecord({name, risk: options.risk || "external-read"}, capability) : null;
    const record = {
      id: appScopedId(metadata, "intent", name),
      kind: "intent",
      sourceName: options.sourceName || camelCase(name),
      label: options.label,
      operationKind: capability ? "capability" : "interaction",
      lane: options.lane,
      runtimeMethod: options.runtimeMethod,
      executionBinding: `${metadata.id}-runtime.${options.binding}`,
      cancellable: Boolean(options.cancellable || false),
      risk: options.risk || (capability ? "external-read" : "read-only"),
      input: [],
      reads: (options.reads || []).map((stateName) => appScopedRef(metadata, "state", stateName)),
      writes: (options.writes || []).map((stateName) => appScopedRef(metadata, "state", stateName)),
      refusals: [],
      invariants: (options.invariants || []).map((invariantName) => appScopedRef(metadata, "invariant", invariantName)),
      effectRefs: effect ? [{ref: effect.id}] : [],
      outcomes: deepClone(options.outcomes || ["completed", "refused", "failed"]),
    };
    if (options.viewState) {
      // A renderer-local transition is deliberately NOT an IR canonical write.
      // The input and target are separately checked by the shared IR validator.
      const config = options.viewState;
      assert(config && typeof config === "object" && typeof config.state === "string" &&
        typeof config.input === "string" && config.schema,
        "MCEL_DSL_VIEW_TRANSITION_INVALID", "View-state selection requires state, input, and input schema.");
      const inputId = appScopedId(metadata, "input", `${name}.${config.input}`);
      const targetId = appScopedId(metadata, "state", config.state);
      record.input.push({id: inputId, kind: "intent-input", sourceName: config.input,
        schema: normalizeSchema(config.schema, `${name}.viewState.schema`)});
      record.viewWrites = [{ref: targetId}];
      record.viewTransition = {kind: "transition.assign", target: {ref: targetId},
        value: {kind: "input.read", input: {ref: inputId}}, invalidInput: "refuse"};
    }
    intents.push(record);
    surfaceNodes.push({
      id: appScopedId(metadata, "surface-node", name),
      kind: "surface-node",
      sourceName: record.sourceName,
      label: record.label,
      nodeKind: "control",
      intent: appScopedRef(metadata, "intent", name),
    });
    scenarios.push({
      id: appScopedId(metadata, "scenario", name),
      kind: "scenario",
      intent: appScopedRef(metadata, "intent", name),
      steps: [receiptSuccessClaim()],
    });
    return Object.freeze({id: record.id, name, record});
  }

  function scenarioRecord(name, options = {}) {
    const record = {
      id: appScopedId(metadata, "scenario", name),
      kind: "scenario",
      sourceName: options.sourceName || camelCase(name),
      label: options.label || name,
      intent: options.intent ? appScopedRef(metadata, "intent", options.intent) : undefined,
      given: deepClone(options.given || {}),
      expect: deepClone(options.expect || {}),
      steps: deepClone(options.steps || []),
    };
    scenarios.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  function invariantRecord(name, options = {}) {
    const record = {
      id: appScopedId(metadata, "invariant", name),
      kind: "invariant",
      sourceName: options.sourceName || camelCase(name),
      label: options.label || name,
      description: options.description || "",
      examples: deepClone(options.examples || []),
    };
    proofInvariants.push(record);
    return Object.freeze({id: record.id, name, record});
  }

  function build() {
    assert(presentation, "MCEL_DSL_HOST_BOUND_PRESENTATION_REQUIRED", "Host-bound application presentation must be declared.");
    assert(proof, "MCEL_DSL_HOST_BOUND_PROOF_REQUIRED", "Host-bound application proof contract must be declared.");

    return {
      __mcelNativeApplicationIr: true,
      document: {
        schema: "mcel.application-ir.v1",
        application: {
          id: semanticId("app", metadata.id),
          kind: "application",
          appId: metadata.id,
          semanticVersion: String(metadata.semanticVersion || "1"),
          title: metadata.title,
          targetTruthStatus: metadata.targetTruthStatus || "semantic-runtime-proven",
          authoringStatus: "dsl-authoritative",
        },
        models: [],
        states,
        derivations: [],
        intents,
        capabilities,
        effects,
        surfaces: [{
          ...presentation,
          ...(semanticSurface ? {semanticSurface: deepClone(semanticSurface, "semanticSurface")} : {}),
          nodes: surfaceNodes,
        }],
        layouts: [
          {
            id: appScopedId(metadata, "layout", "workspace"),
            kind: "layout",
            surface: appScopedRef(metadata, "surface", "workspace"),
            zones,
            ...(layoutGrammar ? {layoutGrammar: deepClone(layoutGrammar, "layoutGrammar")} : {}),
            ...(layoutTree ? {layoutTree: deepClone(layoutTree, "layoutTree")} : {}),
            orderedChildren: surfaceNodes.map((node) => ({ref: node.id})),
          },
        ],
        scenarios,
        proof,
        migration: {
          state: "dsl-authoritative",
          sourceFamily: "official-vanilla-javascript-dsl",
          knownGaps: [],
        },
        provenance: {
          frontend: {id: FRONTEND_ID, version: "1", sourceFiles: []},
          nodeBindings: [],
        },
      },
    };
  }

  return {
    field: {
      integer: () => new SchemaBuilder({kind: "integer"}),
      text: () => new SchemaBuilder({kind: "string"}),
      string: () => new SchemaBuilder({kind: "string"}),
      boolean: () => new SchemaBuilder({kind: "boolean"}),
      record: () => new SchemaBuilder({kind: "record"}),
    },
    presentation: {
      hostBound(name, options) {
        presentation = {
          id: appScopedId(metadata, "surface", name),
          kind: "surface",
          sourceName: options.sourceName || `${titleCaseIdentifier(metadata.title)}Surface`,
          route: options.route,
          root: options.root,
          presentationAuthority: options.presentationAuthority,
        };
        if (typeof options.runtimeFacade === "string" && options.runtimeFacade.trim()) {
          presentation.runtimeFacade = options.runtimeFacade.trim();
        }
      },
      semanticSurface(options) {
        semanticSurface = deepClone(options || {}, "semanticSurface");
      },
    },
    state: {
      rendererLocal(name, schema, options = {}) {
        return stateRecord(name, "renderer-local", schema, options.initial);
      },
      derived(name, schema, options = {}) {
        return stateRecord(name, "derived", schema, options.initial);
      },
      canonical(name, schema, options = {}) {
        return stateRecord(name, "canonical", schema, options.initial);
      },
    },
    capability: {
      external: capabilityRecord,
    },
    intent: {
      interaction: intentRecord,
      capabilityRequest(name, capability, options) {
        return intentRecord(name, options, capability);
      },
    },
    scenario: {
      example: scenarioRecord,
    },
    invariant: {
      semantic: invariantRecord,
    },
    layout: {
      zones(zoneNames) {
        zones = deepClone(zoneNames || []);
      },
      grammar(options) {
        layoutGrammar = deepClone(options || {}, "layoutGrammar");
      },
      tree(options) {
        layoutTree = deepClone(options || {}, "layoutTree");
      },
    },
    proof: {
      semanticRuntimeProven(options = {}) {
        proof = {
          invariants: proofInvariants,
          requiredAuthorities: deepClone(options.requiredAuthorities || [
            "visible-surface",
            "operation-receipt",
            "capability-response",
          ]),
          targetTruthStatus: options.targetTruthStatus || metadata.targetTruthStatus || "semantic-runtime-proven",
        };
      },
    },
    build,
  };
}


function createDsl(metadata, source) {
  const states = [];
  const invariants = [];
  const intents = [];
  const effects = [];
  const surfaces = [];
  const layouts = [];
  const scenarios = [];
  const stateById = new Map();
  const capabilities = [];
  const capabilityById = new Map();

  const field = {
    integer: () => new SchemaBuilder({kind: "integer"}),
    number: () => new SchemaBuilder({kind: "number"}),
    text: () => new SchemaBuilder({kind: "string"}),
    boolean: () => new SchemaBuilder({kind: "boolean"}),
    record(fields) {
      assert(fields && typeof fields === "object" && !Array.isArray(fields) && Object.keys(fields).length > 0,
        "MCEL_DSL_RECORD_FIELDS_REQUIRED", "A record requires named fields.");
      const declared = {};
      for (const [name, schema] of Object.entries(fields)) {
        assert(/^[a-z][A-Za-z0-9_]*$/.test(name), "MCEL_DSL_RECORD_FIELD_INVALID", `Invalid record field '${name}'.`);
        assert(schema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `Record field '${name}' needs a field schema.`);
        declared[name] = schema.toJSON();
      }
      return new SchemaBuilder({kind: "record", fields: declared});
    },
    list(item, options = {}) {
      assert(item instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", "A list requires an item schema.");
      const schema = item.toJSON();
      const key = options.key;
      assert(typeof key === "string" && schema.kind === "record" && schema.fields &&
        schema.fields[key] && schema.fields[key].kind === "string",
        "MCEL_DSL_LIST_KEY_INVALID", "Keyed lists require a declared string identity field.");
      return new SchemaBuilder({kind: "list", items: schema, key});
    },
  };

  function stateDeclaration(authority, name, schema, options = {}) {
    assert(schema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `State '${name}' requires a field schema.`, `state:${name}`);
    assert(options && typeof options === "object" && !Array.isArray(options) &&
      Object.keys(options).every(key => key === "initial"),
      "MCEL_DSL_STATE_OPTION_UNKNOWN", `Unknown state option for '${name}'.`);
    const id = semanticId("state", name);
    assert(!stateById.has(id), "MCEL_DSL_STATE_DUPLICATE", `State '${name}' is already declared.`);
    assert(authority !== "canonical" || Object.hasOwn(options, "initial"),
      "MCEL_DSL_CANONICAL_INITIAL_REQUIRED", "Canonical state requires an initial value.");
    const record = {id, kind: "state", sourceName: name, authority,
      schema: schema.toJSON(),
      ...(Object.hasOwn(options, "initial") ? {initial: deepClone(options.initial)} : {}),
      source};
    const handle = new StateHandle(record);
    states.push(handle);
    stateById.set(handle.id, handle);
    return handle;
  }
  const state = {
    canonical(name, schema, options = {}) {return stateDeclaration("canonical", name, schema, options);},
    rendererLocal(name, schema, options = {}) {return stateDeclaration("renderer-local", name, schema, options);},
    derived(name, schema, options = {}) {return stateDeclaration("derived", name, schema, options);},
  };
  const capability = {
    external(name, options = {}) {
      assert(options && typeof options === "object" && !Array.isArray(options) &&
        Object.keys(options).every(key => ["risk", "description"].includes(key)),
        "MCEL_DSL_CAPABILITY_OPTION_UNKNOWN", `Unsupported capability option for '${name}'.`);
      const risk = options.risk;
      assert(["external-read", "source-write", "consequential"].includes(risk),
        "MCEL_DSL_CAPABILITY_RISK_REQUIRED", "External capability requires explicit external-read, source-write or consequential risk.");
      const id = semanticId("capability", name);
      assert(!capabilityById.has(id), "MCEL_DSL_CAPABILITY_DUPLICATE", `Capability '${name}' already exists.`);
      const record = {id, kind: "capability", sourceName: name, risk,
        description: options.description || "", operations: [], source};
      const handle = new CapabilityHandle(record);
      capabilities.push(handle);
      capabilityById.set(id, handle);
      return handle;
    },
  };

  const expr = {
    constant: (value) => expression(value),
    add: (left, right) => ({kind: "number.add", left: expression(left), right: expression(right)}),
    greaterThanOrEqual: (left, right) => ({kind: "compare.greater-than-or-equal", left: expression(left), right: expression(right)}),
  };

  function read(handle) {
    assert(handle instanceof StateHandle, "MCEL_DSL_STATE_HANDLE_REQUIRED", "read(...) requires a state handle.");
    return handle.read();
  }

  function invariant(name, options) {
    assert(options && typeof options.check === "function", "MCEL_DSL_INVARIANT_CHECK_REQUIRED", `Invariant '${name}' requires a constrained check callback.`);
    const check = options.check({read, expr});
    const record = {
      id: semanticId("invariant", name),
      kind: "invariant",
      check: expression(check),
      source,
    };
    const handle = new InvariantHandle(record);
    invariants.push(handle);
    return handle;
  }

  function buildMutation(name, options = {}) {
    assert(typeof options.change === "function", "MCEL_DSL_MUTATION_CHANGE_REQUIRED", `Mutation '${name}' requires a constrained change callback.`, `intent:${name}`);
    const inputHandles = {};
    const inputRecords = [];
    for (const [inputName, inputSchema] of Object.entries(options.inputs || {})) {
      assert(inputSchema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `Intent input '${inputName}' requires schema.`);
      const id = semanticId("input", `${name}.${inputName}`);
      inputRecords.push({id, kind: "intent-input", sourceName: inputName, schema: inputSchema.toJSON()});
      inputHandles[inputName] = {kind: "input.read", input: {ref: id}};
    }
    const rawSteps = options.change({read, expr, input: inputHandles});
    assert(Array.isArray(rawSteps) && rawSteps.length > 0, "MCEL_DSL_MUTATION_EMPTY", `Mutation '${name}' must construct at least one transition step.`, `intent:${name}`);
    const transition = {kind: "transition.sequence", steps: rawSteps.map((step) => deepClone(step))};
    const writeIds = [...collectWriteTargets(transition)].sort();
    const readIds = (options.reads || []).map((item) => ref(item).ref).sort();
    const invariantRefs = (options.invariants || []).map(ref).sort((a, b) => a.ref.localeCompare(b.ref));
    const intentId = semanticId("intent", name);
    const effectRecords = writeIds.map((stateId) => {
      const stateHandle = stateById.get(stateId);
      assert(stateHandle, "MCEL_DSL_WRITE_TARGET_UNKNOWN", `Mutation '${name}' writes unknown state '${stateId}'.`, intentId);
      const step = findTransitionStepForTarget(transition, stateId);
      return {
        id: semanticId("effect", `${name}.${stateHandle.name}-write`),
        kind: "effect",
        effectKind: "canonical-write",
        owner: {ref: intentId},
        risk: options.risk || "local-state",
        target: stateHandle.read(),
        authority: ref(stateHandle),
        cardinality: {minimum: 1, maximum: 1},
        allowedFinalDispositions: deepClone((options.effect && options.effect.allowedFinalDispositions) || ["completed", "refused-before-attempt", "failed"]),
        requiredEvidence: deepClone((options.effect && options.effect.requiredEvidence) || ["operation-receipt", "canonical-reconciliation", "visible-outcome"]),
        cleanupObligations: deepClone((options.effect && options.effect.cleanupObligations) || []),
        role: transitionRole(step, stateHandle.name),
        source,
      };
    });
    effects.push(...effectRecords);
    const record = {
      id: intentId,
      kind: "intent",
      sourceName: name,
      operationKind: "mutation",
      cancellable: false,
      risk: options.risk || "local-state",
      input: inputRecords,
      reads: readIds.map((id) => ({ref: id})),
      writes: writeIds.map((id) => ({ref: id})),
      refusals: [],
      invariants: invariantRefs,
      effectRefs: effectRecords.map((item) => ref(item.id)),
      outcomes: deepClone(options.outcomes || ["committed", "refused"]),
      transition,
      source,
    };
    const handle = new IntentHandle(record);
    intents.push(handle);
    return handle;
  }

  function buildCapabilityRequest(name, authority, options = {}) {
    assert(authority instanceof CapabilityHandle && capabilityById.get(authority.id) === authority,
      "MCEL_DSL_CAPABILITY_HANDLE_REQUIRED", "Capability request requires a compiler-issued declared capability.");
    const operation = authority.operations.get(options.operation);
    assert(operation, "MCEL_DSL_CAPABILITY_OPERATION_UNKNOWN", `Unknown capability operation '${options.operation}'.`);
    assert(options && typeof options === "object" && !Array.isArray(options) &&
      Object.keys(options).every(key => ["operation", "inputs", "arguments", "reads", "resultTo", "outcomes"].includes(key)),
      "MCEL_DSL_CAPABILITY_REQUEST_OPTION_UNKNOWN", "Unsupported capability request option.");
    const inputs = [];
    for (const [fieldName, fieldSchema] of Object.entries(options.inputs || {})) {
      assert(fieldSchema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `Capability input '${fieldName}' requires schema.`);
      inputs.push({id: semanticId("input", `${name}.${fieldName}`), kind: "intent-input", sourceName: fieldName,
        schema: fieldSchema.toJSON()});
    }
    const requestArguments = [];
    for (const [key, value] of Object.entries(options.arguments || {})) {
      assert(value instanceof StateHandle && stateById.get(value.id) === value,
        "MCEL_DSL_CAPABILITY_ARGUMENT_INVALID", "Bound arguments require declared state handles.");
      requestArguments.push({name: key, state: ref(value)});
    }
    const supplied = new Set([...Object.keys(options.inputs || {}), ...requestArguments.map(x => x.name)]);
    assert(supplied.size === Object.keys(operation.inputs).length &&
      [...supplied].every(key => Object.hasOwn(operation.inputs, key)),
      "MCEL_DSL_CAPABILITY_INPUT_MISMATCH", "Request must bind every registered operation input exactly once.");
    for (const [key, schema] of Object.entries(options.inputs || {})) {
      assert(JSON.stringify(schema.toJSON()) === JSON.stringify(operation.inputs[key]),
        "MCEL_DSL_CAPABILITY_INPUT_SCHEMA_MISMATCH", `Input '${key}' differs from the registered operation schema.`);
    }
    for (const item of requestArguments) {
      assert(JSON.stringify(stateById.get(item.state.ref).record.schema) === JSON.stringify(operation.inputs[item.name]),
        "MCEL_DSL_CAPABILITY_ARGUMENT_SCHEMA_MISMATCH", `State argument '${item.name}' differs from the registered operation schema.`);
    }
    const id = semanticId("intent", name);
    const effectId = semanticId("effect", `${name}.request`);
    const reads = (options.reads || []).map(item => {
      assert(item instanceof StateHandle && stateById.get(item.id) === item,
        "MCEL_DSL_READ_STATE_REQUIRED", "Capability read dependencies require declared state handles.");
      return ref(item);
    });
    for (const item of requestArguments) {
      if (!reads.some(x => x.ref === item.state.ref)) reads.push(item.state);
    }
    reads.sort((a, b) => a.ref.localeCompare(b.ref));
    let resultBindings = [];
    if (options.resultTo !== undefined) {
      assert(options.resultTo instanceof StateHandle && stateById.get(options.resultTo.id) === options.resultTo,
        "MCEL_DSL_RESULT_STATE_REQUIRED", "Result target must be a declared state.");
      assert(options.resultTo.record.authority === "derived",
        "MCEL_DSL_RESULT_AUTHORITY_INVALID", "Capability results may update only derived state; never canonical or renderer-local state.");
      assert(operation.resultSchema && JSON.stringify(operation.resultSchema) === JSON.stringify(options.resultTo.record.schema),
        "MCEL_DSL_RESULT_SCHEMA_MISMATCH", "Declared capability result must exactly match derived target schema.");
      resultBindings = [{target: ref(options.resultTo), source: "capability-response", disposition: "completed"}];
    }
    const record = {id, kind: "intent", sourceName: name, operationKind: "capability", operation: options.operation,
      runtimeMethod: operation.runtimeMethod, risk: authority.record.risk, cancellable: operation.cancellable,
      input: inputs, reads, writes: [], refusals: [], invariants: [],
      requestArguments, effectRefs: [{ref: effectId}], resultBindings,
      outcomes: deepClone(options.outcomes || ["completed", "refused", "failed"]), source};
    intents.push(new IntentHandle(record));
    effects.push({id: effectId, kind: "effect", effectKind: "capability-request",
      owner: {ref: id}, risk: authority.record.risk, target: ref(authority), authority: ref(authority),
      cardinality: {minimum: 0, maximum: 1},
      allowedFinalDispositions: ["completed", "refused-before-attempt", "failed", "cancelled"],
      requiredEvidence: ["operation-receipt", "capability-response", "visible-outcome"],
      cleanupObligations: [], source});
    return intents[intents.length - 1];
  }
  function buildCollectionSelection(name, collectionState, selectedState) {
    assert(collectionState instanceof StateHandle && stateById.get(collectionState.id) === collectionState &&
      collectionState.record.schema.kind === "list" && collectionState.record.schema.key,
      "MCEL_DSL_SELECTION_COLLECTION_INVALID", "Dynamic selection requires a declared keyed-list state.");
    const keyField = collectionState.record.schema.key;
    const keySchema = collectionState.record.schema.items?.fields?.[keyField];
    assert(keySchema?.kind === "string", "MCEL_DSL_SELECTION_KEY_INVALID", "Selection identity must be a declared string key.");
    assert(selectedState instanceof StateHandle && stateById.get(selectedState.id) === selectedState &&
      selectedState.record.authority === "renderer-local" && selectedState.record.schema.kind === "string" &&
      !selectedState.record.schema.enum,
      "MCEL_DSL_SELECTION_STATE_INVALID", "Selection target must be a dynamic string renderer-local state.");
    assert(selectedState.record.initial === "", "MCEL_DSL_SELECTION_EMPTY_INVALID", "Unselected state must begin as an empty string.");
    const inputId = semanticId("input", `${name}.key`);
    const record = {id: semanticId("intent", name), kind: "intent", sourceName: name,
      operationKind: "interaction", risk: "local-state", lane: "local-view-state",
      input: [{id: inputId, kind: "intent-input", sourceName: "key", schema: {kind: "string"}}],
      reads: [ref(collectionState)], writes: [], viewWrites: [ref(selectedState)], effectRefs: [],
      refusals: [], invariants: [], outcomes: ["selected", "refused"],
      collectionSelection: {source: ref(collectionState), target: ref(selectedState),
        input: {ref: inputId}, key: keyField, membership: "required", missing: "refuse", empty: "clear"},
      source};
    const handle = new IntentHandle(record);
    intents.push(handle);
    return handle;
  }
  function buildViewSelect(name, selectedState) {
    assert(selectedState instanceof StateHandle && stateById.get(selectedState.id) === selectedState &&
      selectedState.record.authority === "renderer-local" &&
      selectedState.record.schema.kind === "string" &&
      Array.isArray(selectedState.record.schema.enum) && selectedState.record.schema.enum.length > 0 &&
      selectedState.record.schema.enum.includes(selectedState.record.initial),
      "MCEL_DSL_VIEW_SELECTION_INVALID", "A view selector requires a bounded renderer-local string state with a valid initial choice.");
    const inputId = semanticId("input", `${name}.choice`);
    const record = {id: semanticId("intent", name), kind: "intent", sourceName: name,
      operationKind: "interaction", risk: "local-state", lane: "local-view-state",
      input: [{id: inputId, kind: "intent-input", sourceName: "choice", schema: deepClone(selectedState.record.schema)}],
      reads: [], writes: [], viewWrites: [ref(selectedState)], effectRefs: [],
      refusals: [], invariants: [], outcomes: ["completed", "refused"],
      viewTransition: {kind: "transition.assign", target: ref(selectedState),
        value: {kind: "input.read", input: {ref: inputId}}, invalidInput: "refuse"}, source};
    const handle = new IntentHandle(record);
    intents.push(handle);
    return handle;
  }
  const intent = {
    mutation: buildMutation,
    capabilityRequest: buildCapabilityRequest,
    selectFromCollection: buildCollectionSelection,
    selectView: buildViewSelect,
    keyedCollection(name, collectionState) {
      assert(collectionState instanceof StateHandle && collectionState.record.schema.kind === "list" &&
        collectionState.record.schema.key, "MCEL_DSL_COLLECTION_STATE_REQUIRED", "Keyed collection intents require keyed list state.");
      const schema = collectionState.record.schema;
      const recordSchema = new SchemaBuilder(schema.items);
      const keySchema = new SchemaBuilder(schema.items.fields[schema.key]);
      const mk = (operation, inputs, step) => buildMutation(`${name}-${operation}`, {
        inputs, reads: [collectionState],
        change: ({input}) => [step(input)],
      });
      return Object.freeze({
        add: mk("add", {item: recordSchema}, input => ({kind: "list.append", target: ref(collectionState), value: input.item})),
        update: mk("update", {key: keySchema, item: recordSchema}, input =>
          ({kind: "list.update-by-key", target: ref(collectionState), key: input.key, keyField: schema.key, update: input.item})),
        remove: mk("remove", {key: keySchema}, input =>
          ({kind: "list.remove-by-key", target: ref(collectionState), key: input.key, keyField: schema.key})),
      });
    },
    prohibited(name, options = {}) {
      const record = {
        id: semanticId("intent", name),
        kind: "intent",
        sourceName: options.sourceName || name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()),
        operationKind: "prohibited",
        risk: "prohibited",
        input: [], reads: [], writes: [], refusals: [], invariants: [], effectRefs: [],
        outcomes: ["refused"],
        reasonCode: options.reasonCode || "MCEL_CANONICAL_ASSIGNMENT_BYPASSES_OPERATION_AUTHORITY",
        source,
      };
      const handle = new IntentHandle(record);
      intents.push(handle);
      return handle;
    },
  };

  function nodeId(localName, options = {}) {
    if (options.id) return semanticId("surface-node", options.id);
    return semanticId("surface-node", `${metadata.id}.${localName}`);
  }

  // Interface intent is author meaning, never an executable layout. Keep the
  // public DSL deliberately closed so a typo cannot silently discard intent.
  const INTERFACE_ROLES = new Set([
    "main-workarea", "navigation", "collection", "document", "inspector",
    "commands", "results", "status", "auxiliary-workarea", "mode-selector",
  ]);
  const INTERFACE_PURPOSES = new Set(["monitor", "compare", "inspect", "navigate", "manage"]);
  const INTERFACE_RELATIONSHIPS = new Set([
    "selects", "contextual-to", "supports", "reports-for", "switches-mode-of",
    "acts-on", "informs", "accompanies",
  ]);
  function knownOptions(options, keys, where) {
    assert(options && typeof options === "object" && !Array.isArray(options),
      "MCEL_DSL_OPTIONS_INVALID", `${where} requires an options record.`);
    const unknown = Object.keys(options).filter(key => !keys.includes(key));
    assert(!unknown.length, "MCEL_DSL_UNKNOWN_OPTION",
      `${where} has unsupported option(s): ${unknown.join(", ")}`, where);
  }
  function intentRegionId(name) {
    return semanticId("interface-region", `${metadata.id}.${name}`);
  }
  // A mode condition is an author-owned semantic state relationship, not a
  // placement or layout declaration. Never convert it into operational authority.
  function modeCondition(options, where) {
    if (options.when === undefined) return {};
    knownOptions(options.when, ["state", "equals"], where + ".when");
    const mode=options.when.state, value=options.when.equals;
    assert(mode instanceof StateHandle && stateById.get(mode.id) === mode &&
      mode.record.authority === "renderer-local" && mode.record.schema.kind === "string" &&
      Array.isArray(mode.record.schema.enum) && mode.record.schema.enum.includes(value),
      "MCEL_DSL_VIEW_CONDITION_INVALID", "A view condition must reference a declared renderer-local enum state and one of its values.");
    return {visibleWhen:{state:ref(mode),equals:value}};
  }
  const issuedSurfaceNodes = [];
  function surfaceNode(name, record) {
    const handle = new SurfaceNodeHandle(name, record);
    issuedSurfaceNodes.push(handle);
    return handle;
  }
  const surface = {
    form(name, options = {}) {
      knownOptions(options, ["id", "fields", "intent", "defaults", "when"], `surface.form:${name}`);
      assert(options.fields && typeof options.fields === "object", "MCEL_DSL_FORM_FIELDS_REQUIRED", "Form requires fields.");
      const fields = {};
      for (const [fieldName, schema] of Object.entries(options.fields)) {
        assert(schema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `Form field '${fieldName}' requires schema.`);
        fields[fieldName] = schema.toJSON();
      }
      const defaults={};
      for (const [k,v] of Object.entries(options.defaults || {})) {
        assert(Object.prototype.hasOwnProperty.call(fields,k), "MCEL_DSL_FORM_DEFAULT_UNKNOWN", "Form default refers to an undeclared field.");
        const schema=fields[k];
        assert((schema.kind==="number" && typeof v==="number" && Number.isFinite(v)) ||
               (schema.kind==="integer" && Number.isSafeInteger(v)) ||
               (schema.kind==="string" && typeof v==="string" && (!schema.enum || schema.enum.includes(v))) ||
               (schema.kind==="boolean" && typeof v==="boolean"),
          "MCEL_DSL_FORM_DEFAULT_INVALID", "Form default must match its typed field.");
        defaults[k]=v;
      }
      if (options.intent !== undefined) assert(options.intent instanceof IntentHandle,
        "MCEL_DSL_FORM_INTENT_REQUIRED", "Bound form requires intent handle.");
      const record = {id: nodeId(name, options), kind: "surface-node", nodeKind: "form",
        sourceName: name, fields, ...(Object.keys(defaults).length ? {defaults} : {}), ...(options.intent ? {intent: ref(options.intent)} : {}), ...modeCondition(options, `surface.form:${name}`)};
      return surfaceNode(name, record);
    },
    collection(name, options = {}) {
      knownOptions(options, ["id", "key", "fields", "state", "actions", "select", "when"], `surface.collection:${name}`);
      assert(typeof options.key === "string" && options.key, "MCEL_DSL_COLLECTION_KEY_REQUIRED", "Collection requires key.");
      assert(Array.isArray(options.fields) && options.fields.length > 0 && options.fields.every(x => typeof x === "string"),
        "MCEL_DSL_COLLECTION_FIELDS_REQUIRED", "Collection requires visible fields.");
      if (options.state !== undefined) assert(options.state instanceof StateHandle &&
        options.state.record.schema.kind === "list" && options.state.record.schema.key === options.key,
        "MCEL_DSL_COLLECTION_STATE_REQUIRED", "Surface collection state must match key.");
      if (options.select !== undefined) {
        assert(options.select instanceof IntentHandle && options.state instanceof StateHandle &&
          options.select.record.collectionSelection?.source?.ref === options.state.id,
          "MCEL_DSL_COLLECTION_SELECTION_BINDING_INVALID",
          "Collection selection intent must select from this collection's declared state.");
      }
      const actions = options.actions || {};
      for (const [key, value] of Object.entries(actions)) assert(["add", "update", "remove"].includes(key) && value instanceof IntentHandle,
        "MCEL_DSL_COLLECTION_ACTION_INVALID", "Collection actions must be declared intent handles.");
      const record = {id: nodeId(name, options), kind: "surface-node", nodeKind: "collection", sourceName: name,
        key: options.key, fields: [...options.fields],
        ...(options.state ? {state: ref(options.state)} : {}),
        ...(options.select ? {selectionIntent: ref(options.select)} : {}),
        actions: Object.fromEntries(Object.entries(actions).map(([key, value]) => [key, ref(value)])),
        ...modeCondition(options, `surface.collection:${name}`)};
      return surfaceNode(name, record);
    },
    text(name, options = {}) {
      knownOptions(options, ["id", "value", "when"], `surface.text:${name}`);
      const record = {
        id: nodeId(name, options), kind: "surface-node", nodeKind: "state-value",
        value: expression(options.value),
        ...modeCondition(options, `surface.text:${name}`),
      };
      return surfaceNode(name, record);
    },
    action(name, options = {}) {
      knownOptions(options, ["id", "intent"], `surface.action:${name}`);
      assert(options.intent instanceof IntentHandle, "MCEL_DSL_ACTION_INTENT_REQUIRED", `Surface action '${name}' requires an intent handle.`);
      const record = {
        id: nodeId(name, options), kind: "surface-node", nodeKind: "control", intent: ref(options.intent),
      };
      return surfaceNode(name, record);
    },
    plot(name, options = {}) {
      knownOptions(options, ["id", "state", "x", "y"], `surface.plot:${name}`);
      assert(options.state instanceof StateHandle && stateById.get(options.state.id) === options.state,
        "MCEL_DSL_PLOT_STATE_INVALID", "Plot needs a declared state.");
      const schema = options.state.record.schema;
      assert(schema.kind === "list" && schema.items?.kind === "record" &&
        schema.items.fields?.[options.x]?.kind === "number" &&
        schema.items.fields?.[options.y]?.kind === "number",
        "MCEL_DSL_PLOT_FIELDS_INVALID", "Plot needs a keyed list with numeric x and y fields.");
      assert(options.x !== options.y, "MCEL_DSL_PLOT_AXES_INVALID", "Plot axes must be distinct.");
      return surfaceNode(name, {id: nodeId(name, options), kind: "surface-node", nodeKind: "plot",
        sourceName: name, state: ref(options.state), x: options.x, y: options.y});
    },
    documentEditor(name, options = {}) {
      knownOptions(options, ["id", "value", "path", "draft", "loadIntent", "saveIntent"], `surface.documentEditor:${name}`);
      for (const key of ["value", "path", "draft"]) {
        const item=options[key];
        assert(item instanceof StateHandle && stateById.get(item.id)===item && item.record.schema.kind==="string",
          "MCEL_DSL_DOCUMENT_STATE_INVALID", `Document editor ${key} requires a declared string state.`);
      }
      assert(options.value.record.authority==="derived" && options.path.record.authority==="renderer-local" &&
        options.draft.record.authority==="renderer-local", "MCEL_DSL_DOCUMENT_AUTHORITY_INVALID",
        "Loaded source is derived; selected path and unsaved draft are renderer-local.");
      assert(options.loadIntent instanceof IntentHandle && options.loadIntent.record.operationKind==="capability" &&
        options.loadIntent.record.resultBindings?.some(x=>x.target.ref===options.value.id),
        "MCEL_DSL_DOCUMENT_LOAD_INVALID", "Document editor load intent must populate the source state.");
      if (options.saveIntent!==undefined) assert(options.saveIntent instanceof IntentHandle &&
        options.saveIntent.record.operationKind==="capability" && options.saveIntent.record.risk==="source-write" &&
        options.saveIntent.record.requestArguments?.some(x=>x.state.ref===options.draft.id) &&
        options.saveIntent.record.requestArguments?.some(x=>x.state.ref===options.path.id),
        "MCEL_DSL_DOCUMENT_SAVE_INVALID", "Document save must be a governed source-write of selected path and draft.");
      return surfaceNode(name,{id:nodeId(name,options),kind:"surface-node",nodeKind:"document-editor",sourceName:name,
        contentState:ref(options.value),path:ref(options.path),draft:ref(options.draft),loadIntent:ref(options.loadIntent),
        ...(options.saveIntent ? {saveIntent:ref(options.saveIntent)}:{})});
    },
    receipt(name, options = {}) {
      knownOptions(options, ["id"], `surface.receipt:${name}`);
      const record = {id: nodeId(name, options), kind: "surface-node", nodeKind: "operation-evidence"};
      return surfaceNode(name, record);
    },
    region(name, options = {}) {
      knownOptions(options, ["role", "priority", "prefer", "children"], `surface.region:${name}`);
      assert(typeof name === "string" && /^[a-z][a-z0-9-]*$/.test(name),
        "MCEL_DSL_REGION_ID_INVALID", "Region names must be stable lowercase IDs.");
      assert(Array.isArray(options.children || []), "MCEL_DSL_REGION_CHILDREN_INVALID", "Region children must be an array.");
      if (options.role !== undefined) {
        assert(INTERFACE_ROLES.has(options.role), "MCEL_DSL_REGION_ROLE_INVALID", `Unknown interface role '${options.role}'.`);
      }
      if (options.priority !== undefined) {
        assert(["primary", "secondary", "supporting"].includes(options.priority),
          "MCEL_DSL_REGION_PRIORITY_INVALID", "Priority must be primary, secondary or supporting.");
      }
      if (options.prefer !== undefined) {
        knownOptions(options.prefer, ["side"], `surface.region:${name}.prefer`);
        assert(["start", "end", "top", "bottom"].includes(options.prefer.side),
          "MCEL_DSL_REGION_PREFERENCE_INVALID", "Preferred side must be start, end, top or bottom.");
      }
      assert(options.role !== undefined || (options.priority === undefined && options.prefer === undefined),
        "MCEL_DSL_REGION_ROLE_REQUIRED", "Region preferences or priority require an interface role.");
      return {kind: "dsl.surface-region", name, id: intentRegionId(name),
        ...(options.role ? {role: options.role} : {}),
        ...(options.priority ? {priority: options.priority} : {}),
        ...(options.prefer ? {prefer: deepClone(options.prefer)} : {}),
        children: (options.children || []).flat()};
    },
    relate(from, kind, to) {
      const fromId = typeof from === "string" ? intentRegionId(from) : from && from.kind === "dsl.surface-region" && from.id;
      const toId = typeof to === "string" ? intentRegionId(to) : to && to.kind === "dsl.surface-region" && to.id;
      assert(fromId && toId, "MCEL_DSL_RELATION_ENDPOINT_INVALID", "A semantic relationship joins two declared region IDs or handles.");
      assert(INTERFACE_RELATIONSHIPS.has(kind), "MCEL_DSL_RELATION_KIND_INVALID", `Unknown relationship '${kind}'.`);
      assert(fromId !== toId, "MCEL_DSL_RELATION_SELF_INVALID", "A region cannot relate to itself.");
      return {kind: "dsl.interface-relation", from: {ref: fromId}, to: {ref: toId}, relation: kind};
    },
    bindHtml(name, options = {}) {
      // The HTML file owns hierarchy and region meaning; JS binds real typed
      // surface nodes by stable region ID, without duplicating the HTML tree.
      knownOptions(options, ["id", "sourceName", "bindings", "relations"], `surface.bindHtml:${name}`);
      assert(options.bindings && typeof options.bindings === "object" && !Array.isArray(options.bindings),
        "MCEL_DSL_HTML_BINDINGS_REQUIRED", "HTML-bound surface requires a bindings map.");
      const nodes = [], regions = [];
      const nodesByName = new Map();
      const seenNodes = new Set();
      for (const [regionName, children] of Object.entries(options.bindings)) {
        assert(/^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/.test(regionName),
          "MCEL_DSL_HTML_REGION_ID_INVALID", `Invalid HTML region ID '${regionName}'.`);
        assert(Array.isArray(children), "MCEL_DSL_HTML_BINDING_INVALID", `Region '${regionName}' bindings must be an array.`);
        const members = [];
        for (const child of children) {
          assert(child instanceof SurfaceNodeHandle, "MCEL_DSL_HTML_BINDING_INVALID", "HTML bindings must contain DSL surface node handles.");
          assert(!seenNodes.has(child.id), "MCEL_DSL_SURFACE_NODE_DUPLICATE", `Node '${child.id}' belongs to multiple regions.`);
          seenNodes.add(child.id);
          nodes.push(child.record);
          nodesByName.set(child.localName, child);
          members.push({ref: child.id});
        }
        regions.push({id: intentRegionId(regionName), members});
      }
      const relations = options.relations || [];
      assert(Array.isArray(relations), "MCEL_DSL_RELATIONS_INVALID", "HTML relationships must be an array.");
      const keys = new Set();
      for (const relation of relations) {
        assert(relation && relation.kind === "dsl.interface-relation", "MCEL_DSL_RELATION_INVALID", "Use dsl.surface.relate to declare HTML relationships.");
        const k = `${relation.from.ref}:${relation.relation}:${relation.to.ref}`;
        assert(!keys.has(k), "MCEL_DSL_RELATION_DUPLICATE", "Duplicate HTML semantic relationship.");
        keys.add(k);
      }
      const record = {
        id: semanticId("surface", options.id || `${metadata.id}.${name}`),
        kind: "surface", sourceName: options.sourceName || `${metadata.title.replace(/\s+/g, "")}Surface`,
        nodes,
        htmlBindings: {
          schema: "mcel.html-bindings.v1", regions,
          relations: relations.map(x => ({from: x.from, kind: x.relation, to: x.to})),
        },
        source,
      };
      const handle = new SurfaceHandle(record, nodesByName);
      surfaces.push(handle);
      return handle;
    },
    define(name, options = {}) {
      knownOptions(options, ["id", "sourceName", "root", "relations", "purpose"], `surface.define:${name}`);
      if (options.purpose !== undefined) assert(INTERFACE_PURPOSES.has(options.purpose),
        "MCEL_DSL_SURFACE_PURPOSE_INVALID", "Surface purpose must be a registered semantic purpose.");
      assert(options.root && options.root.kind === "dsl.surface-region", "MCEL_DSL_SURFACE_ROOT_REQUIRED", `Surface '${name}' requires a structural root region.`);
      const nodes = [];
      const nodesByName = new Map();
      const regions = [];
      const seenRegions = new Set();
      const seenNodes = new Set();
      function visit(region) {
        assert(!seenRegions.has(region.id), "MCEL_DSL_REGION_DUPLICATE", `Duplicate interface region '${region.id}'.`);
        seenRegions.add(region.id);
        const members = [];
        for (const child of region.children) {
          if (child && child.kind === "dsl.surface-region") {
            visit(child);
          } else {
            assert(child instanceof SurfaceNodeHandle, "MCEL_DSL_SURFACE_CHILD_INVALID", `Surface '${name}' contains an unsupported child.`);
            assert(!seenNodes.has(child.id), "MCEL_DSL_SURFACE_NODE_DUPLICATE", `Node '${child.id}' belongs to multiple regions.`);
            seenNodes.add(child.id);
            nodes.push(child.record);
            nodesByName.set(child.localName, child);
            members.push({ref: child.id});
          }
        }
        if (region.role) {
          assert(members.length > 0, "MCEL_DSL_REGION_EMPTY", `Semantic region '${region.name}' must own at least one node.`);
          regions.push({id: region.id, kind: "interface-region", role: region.role, source,
            ...(region.priority ? {priority: region.priority} : {}),
            ...(region.prefer ? {prefer: region.prefer} : {}),
            members: members.sort((a,b) => a.ref.localeCompare(b.ref))});
        } else {
          assert(!members.length || !regions.length,
            "MCEL_DSL_REGION_UNGROUPED", "An untyped root cannot mix direct nodes with semantic regions.");
        }
      }
      visit(options.root);
      const relations = options.relations || [];
      assert(Array.isArray(relations), "MCEL_DSL_RELATIONS_INVALID", "Relations must be an array.");
      const relKeys = new Set();
      for (const relation of relations) {
        assert(relation && relation.kind === "dsl.interface-relation" &&
          seenRegions.has(relation.from.ref) && seenRegions.has(relation.to.ref),
          "MCEL_DSL_RELATION_UNRESOLVED", "Relationships must reference regions on this surface.");
        const key = `${relation.from.ref}:${relation.relation}:${relation.to.ref}`;
        assert(!relKeys.has(key), "MCEL_DSL_RELATION_DUPLICATE", "Duplicate semantic relationship.");
        relKeys.add(key);
      }
      const record = {
        id: semanticId("surface", options.id || `${metadata.id}.${name}`),
        kind: "surface",
        sourceName: options.sourceName || `${metadata.title.replace(/\s+/g, "")}Surface`,
        nodes,
        ...(options.purpose ? {purpose: options.purpose} : {}),
        ...(regions.length ? {interfaceIntent: {
          schema: "mcel.interface-intent.v1",
          regions: regions.sort((a,b) => a.id.localeCompare(b.id)),
          relations: relations.map(x => ({from: x.from, kind: x.relation, to: x.to})).sort((a,b) =>
            `${a.from.ref}:${a.kind}:${a.to.ref}`.localeCompare(`${b.from.ref}:${b.kind}:${b.to.ref}`)),
        }} : {}),
        source,
      };
      const handle = new SurfaceHandle(record, nodesByName);
      surfaces.push(handle);
      return handle;
    },
  };

  const layout = {
    define(name, options = {}) {
      assert(options.surface instanceof SurfaceHandle, "MCEL_DSL_LAYOUT_SURFACE_REQUIRED", `Layout '${name}' requires a surface handle.`);
      const orderedChildren = (options.orderedChildren || []).map(ref);
      const record = {
        id: semanticId("layout", options.id || `${metadata.id}.${name}`),
        kind: "layout",
        surface: ref(options.surface),
        orderedChildren,
        ...(options.tree ? {layoutTree: deepClone(options.tree, `layout:${name}.tree`)} : {}),
        source,
      };
      const handle = new LayoutHandle(record);
      layouts.push(handle);
      return handle;
    },
  };

  const prove = {
    scenario(name, options = {}) {
      return new ScenarioBuilder(name, options, source);
    },
    canonical(stateHandle) {
      return {
        equals(value) {
          return {kind: "claim.equal", authority: "canonical-state", actual: stateHandle.read(), expected: expression(value)};
        },
      };
    },
    visible(nodeHandle) {
      return {
        exists() {
          return {kind: "claim.exists", authority: "visible-surface", target: ref(nodeHandle)};
        },
      };
    },
    receiptDisposition(expected, code) {
      const record = {kind: "claim.receipt-disposition", authority: "operation-receipt", expected};
      if (code) record.code = code;
      return record;
    },
    config(options = {}) {
      return {
        invariants: (options.invariants || []).map((item) => item.record),
        requiredAuthorities: deepClone(options.requiredAuthorities || []),
        targetTruthStatus: options.targetTruthStatus || metadata.targetTruthStatus || "semantic-runtime-proven",
      };
    },
  };

  const migration = {
    importApplicationIr(document) {
      assert(document && typeof document === "object" && !Array.isArray(document), "MCEL_DSL_MIGRATION_IR_REQUIRED", "Migration IR import requires one portable Application IR object.");
      return {__mcelImportedApplicationIr: true, document: deepClone(document)};
    },
  };

  const ir = {
    application(document) {
      assert(document && typeof document === "object" && !Array.isArray(document), "MCEL_DSL_NATIVE_IR_REQUIRED", "Native IR construction requires one portable Application IR object.");
      return {__mcelNativeApplicationIr: true, document: deepClone(document)};
    },
  };

  const application = {
    hostBound(declaration) {
      assert(typeof declaration === "function", "MCEL_DSL_HOST_BOUND_DECLARATION_REQUIRED", "Host-bound application requires one declaration callback.");
      const builder = createHostBoundApplicationBuilder(metadata);
      declaration(builder);
      return builder.build();
    },
  };

  return {
    field, state, capability, intent, invariant, surface, layout, prove, expr, ir, migration, application,
    __collections: {states, capabilities, invariants, intents, effects, surfaces, layouts, scenarios, issuedSurfaceNodes},
  };
}

function bindImportedSource(document, metadata, source, sourceHash, sourcePath, native = false) {
  const imported = deepClone(document);
  assert(imported.schema === "mcel.application-ir.v1", "MCEL_DSL_MIGRATION_IR_SCHEMA_INVALID", "Imported migration document must be mcel.application-ir.v1.");
  assert(imported.application && imported.application.appId === metadata.id, "MCEL_DSL_MIGRATION_APP_ID_CONFLICT", "Imported migration IR application identity must match defineApp metadata.");
  const bind = (record) => {
    if (record && typeof record === "object" && typeof record.id === "string") record.source = source;
  };
  bind(imported.application);
  for (const key of ["models", "states", "derivations", "intents", "capabilities", "effects", "surfaces", "layouts", "scenarios"]) {
    for (const record of imported[key] || []) {
      bind(record);
      if (key === "models") for (const child of record.fields || []) bind(child);
      if (key === "intents") for (const child of record.input || []) bind(child);
      if (key === "surfaces") for (const child of record.nodes || []) bind(child);
    }
  }
  if (imported.proof && Array.isArray(imported.proof.invariants)) {
    for (const invariant of imported.proof.invariants) bind(invariant);
  }
  const semanticIds = [];
  const collect = (record) => { if (record && typeof record.id === "string") semanticIds.push(record.id); };
  collect(imported.application);
  for (const key of ["models", "states", "derivations", "intents", "capabilities", "effects", "surfaces", "layouts", "scenarios"]) {
    for (const record of imported[key] || []) {
      collect(record);
      if (key === "models") for (const child of record.fields || []) collect(child);
      if (key === "intents") for (const child of record.input || []) collect(child);
      if (key === "surfaces") for (const child of record.nodes || []) collect(child);
    }
  }
  if (imported.proof && Array.isArray(imported.proof.invariants)) {
    for (const invariant of imported.proof.invariants) collect(invariant);
  }
  const nativeAuthoritative = native && imported.application.authoringStatus === "dsl-authoritative";
  imported.application.authoringStatus = nativeAuthoritative ? "dsl-authoritative" : "dual-authored";
  const inheritedGaps = [...(imported.migration?.knownGaps || [])].filter((value) => value !== "migration-ir-bridge-not-final-authoring-surface" && value !== "opaque-callbacks-require-constrained-expression-replacement");
  imported.migration = {
    ...(imported.migration || {}),
    state: nativeAuthoritative ? (imported.migration?.state || "dsl-authoritative") : "dual-authored",
    sourceFamily: native ? "official-vanilla-javascript-dsl" : "official-vanilla-javascript-dsl-migration-bridge",
    knownGaps: nativeAuthoritative
      ? inheritedGaps
      : [...new Set([...inheritedGaps, ...(native ? [] : ["migration-ir-bridge-not-final-authoring-surface"]), "candidate-not-promoted", "legacy-package-remains-live"])].sort(),
  };
  imported.provenance = {
    ...(imported.provenance || {}),
    compiler: {id: "mcel.dsl.compiler", version: RUNTIME_VERSION},
    frontend: {id: FRONTEND_ID, version: "1", sourceFiles: [{path: sourcePath, sha256: sourceHash}]},
    ...(native ? {nativeIrConstruction: {kind: "application-ir-construction", status: "native"}} : {migrationBridge: {kind: "application-ir-import", status: "explicit-migration-debt"}}),
    nodeBindings: [...new Set(semanticIds)].sort().map((nodeId) => ({id: semanticId("binding", nodeId), semanticId: nodeId, source})),
  };
  delete imported.fingerprints;
  delete imported.normalization;
  return imported;
}

function finalizeApplication(metadata, built, dsl, source, sourceHash, sourcePath) {
  assert(built && typeof built === "object" && !Array.isArray(built), "MCEL_DSL_ROOT_RESULT_INVALID", "defineApp builder must return one declaration object.");
  if (built.__mcelImportedApplicationIr === true) {
    return bindImportedSource(built.document, metadata, source, sourceHash, sourcePath, false);
  }
  if (built.__mcelNativeApplicationIr === true) {
    return bindImportedSource(built.document, metadata, source, sourceHash, sourcePath, true);
  }
  const unwrap = (items, ClassType, label) => (items || []).map((item) => {
    assert(item instanceof ClassType, "MCEL_DSL_DECLARATION_HANDLE_REQUIRED", `${label} must contain compiler-issued semantic handles.`);
    return item.record;
  });
  const scenarioRecords = (built.scenarios || []).map((item) => {
    if (item instanceof ScenarioBuilder) item = item.build();
    assert(item instanceof ScenarioHandle, "MCEL_DSL_DECLARATION_HANDLE_REQUIRED", "scenarios must contain scenario handles.");
    return item.record;
  });
  const stateRecords = unwrap(built.states, StateHandle, "states");
  const capabilityRecords = unwrap(built.capabilities, CapabilityHandle, "capabilities");
  const intentRecords = unwrap(built.intents, IntentHandle, "intents");
  const surfaceRecords = unwrap(built.surfaces, SurfaceHandle, "surfaces");
  if (surfaceRecords.some(surface => surface.htmlBindings)) {
    const bound = surfaceRecords.flatMap(surface => surface.nodes || []).map(node => node.id);
    const issued = dsl.__collections.issuedSurfaceNodes.map(node => node.id);
    assert(bound.length === issued.length && new Set(bound).size === bound.length &&
      bound.every(id => issued.includes(id)),
      "MCEL_DSL_UNBOUND_SURFACE_NODE", "All declared surface nodes must be bound exactly once to HTML regions.");
  }
  const layoutRecords = unwrap(built.layouts, LayoutHandle, "layouts");
  const invariantRecords = built.proof && Array.isArray(built.proof.invariants)
    ? deepClone(built.proof.invariants)
    : unwrap(built.invariants || [], InvariantHandle, "invariants");

  const allRecords = [
    {id: semanticId("app", metadata.id), source},
    ...surfaceRecords.flatMap(surface => surface.interfaceIntent ? surface.interfaceIntent.regions : []),
    ...stateRecords, ...capabilityRecords, ...intentRecords, ...dsl.__collections.effects,
    ...surfaceRecords, ...layoutRecords, ...scenarioRecords, ...invariantRecords,
  ];
  const nodeBindings = allRecords.map((record) => ({
    id: semanticId("binding", record.id), semanticId: record.id, source,
  }));

  return {
    schema: "mcel.application-ir.v1",
    application: {
      id: semanticId("app", metadata.id),
      kind: "application",
      appId: metadata.id,
      semanticVersion: String(metadata.semanticVersion || "1"),
      title: metadata.title,
      targetTruthStatus: metadata.targetTruthStatus || "semantic-runtime-proven",
      authoringStatus: "dual-authored",
      source,
    },
    models: deepClone(built.models || []),
    states: stateRecords,
    derivations: deepClone(built.derivations || []),
    intents: intentRecords,
    capabilities: capabilityRecords,
    effects: deepClone(dsl.__collections.effects),
    surfaces: surfaceRecords,
    layouts: layoutRecords,
    scenarios: scenarioRecords,
    proof: built.proof || {
      invariants: invariantRecords,
      requiredAuthorities: [],
      targetTruthStatus: metadata.targetTruthStatus || "semantic-runtime-proven",
    },
    migration: {
      state: "dual-authored",
      sourceFamily: "official-vanilla-javascript-dsl",
      knownGaps: ["candidate-not-promoted", "legacy-package-remains-live"],
    },
    provenance: {
      compiler: {id: "mcel.dsl.compiler", version: RUNTIME_VERSION},
      frontend: {
        id: FRONTEND_ID,
        version: "1",
        sourceFiles: [{path: sourcePath, sha256: sourceHash}],
      },
      nodeBindings,
    },
  };
}

function createMcelModule(sourcePath, sourceHash, sourceText) {
  const lineCount = sourceText.split(/\r?\n/).length;
  const source = sourceRecord(sourcePath, lineCount);
  return {
    defineApp(metadata, builder) {
      assert(metadata && typeof metadata === "object" && !Array.isArray(metadata), "MCEL_DSL_APP_METADATA_REQUIRED", "defineApp requires literal application metadata.");
      assert(typeof metadata.id === "string", "MCEL_DSL_APP_ID_REQUIRED", "Application metadata requires id.");
      assert(typeof metadata.title === "string", "MCEL_DSL_APP_TITLE_REQUIRED", "Application metadata requires title.");
      assert(typeof builder === "function", "MCEL_DSL_APP_BUILDER_REQUIRED", "defineApp requires one builder callback.");
      const dsl = createDsl(metadata, source);
      const built = builder(dsl);
      return finalizeApplication(metadata, built, dsl, source, sourceHash, sourcePath);
    },
  };
}

function compile(request) {
  assert(request && typeof request === "object", "MCEL_DSL_REQUEST_INVALID", "Compiler request must be an object.");
  const sourcePath = String(request.sourcePath || "application.js").replaceAll("\\", "/");
  const sourceText = String(request.sourceText || "");
  const sourceHash = `sha256:${crypto.createHash("sha256").update(Buffer.from(sourceText, "utf8")).digest("hex")}`;
  assert(/^\s*["']use strict["'];/.test(sourceText), "MCEL_DSL_STRICT_MODE_REQUIRED", "Official mcel.dsl.v1 source must begin with a strict-mode directive.");
  assert(sourceText.includes('require("@mcel/app")') || sourceText.includes("require('@mcel/app')"), "MCEL_DSL_MODULE_IMPORT_REQUIRED", "Official mcel.dsl.v1 source must require only @mcel/app.");

  const mcel = createMcelModule(sourcePath, sourceHash, sourceText);
  const sandbox = Object.create(null);
  sandbox.module = {exports: {}};
  sandbox.exports = sandbox.module.exports;
  sandbox.require = (moduleId) => {
    if (moduleId === "@mcel/app") return mcel;
    throw new DslError("MCEL_DSL_REQUIRE_DENIED", `Module '${moduleId}' is not available to mcel.dsl.v1 source.`);
  };
  const forbiddenObject = (name) => new Proxy(Object.create(null), {
    get() {
      throw new DslError("MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN", `Ambient global '${name}' is not available to mcel.dsl.v1 source.`);
    },
    set() {
      throw new DslError("MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN", `Ambient global '${name}' is not writable in mcel.dsl.v1 source.`);
    },
  });
  const forbiddenFunction = (name) => new Proxy(function forbiddenAmbientGlobal() {}, {
    apply() {
      throw new DslError("MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN", `Ambient global '${name}' is not available to mcel.dsl.v1 source.`);
    },
    construct() {
      throw new DslError("MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN", `Ambient global '${name}' is not available to mcel.dsl.v1 source.`);
    },
    get() {
      throw new DslError("MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN", `Ambient global '${name}' is not available to mcel.dsl.v1 source.`);
    },
  });
  sandbox.process = forbiddenObject("process");
  sandbox.fetch = forbiddenFunction("fetch");
  sandbox.XMLHttpRequest = forbiddenFunction("XMLHttpRequest");
  sandbox.WebSocket = forbiddenFunction("WebSocket");
  sandbox.Date = forbiddenFunction("Date");
  sandbox.Math = forbiddenObject("Math");
  sandbox.setTimeout = forbiddenFunction("setTimeout");
  sandbox.setInterval = forbiddenFunction("setInterval");
  sandbox.queueMicrotask = forbiddenFunction("queueMicrotask");
  sandbox.Function = forbiddenFunction("Function");
  sandbox.eval = forbiddenFunction("eval");
  sandbox.console = Object.freeze({log() {}, warn() {}, error() {}});

  const context = vm.createContext(sandbox, {
    name: `mcel-dsl:${sourcePath}`,
    codeGeneration: {strings: false, wasm: false},
  });
  const script = new vm.Script(sourceText, {filename: sourcePath, displayErrors: true});
  script.runInContext(context, {timeout: Number(request.timeoutMs || 1000), displayErrors: true});
  const result = sandbox.module.exports;
  assert(result && result.schema === "mcel.application-ir.v1", "MCEL_DSL_EXPORT_INVALID", "DSL source must export the result of mcel.defineApp(...).", "$exports");
  return result;
}

function diagnosticFromError(error) {
  if (error instanceof DslError) {
    return {
      code: error.code,
      semanticPath: error.semanticPath || "$",
      summary: error.message,
      problem: error.message,
      observed: error.details,
      expected: null,
      blocking: true,
      severity: "error",
      repairStage: "compile",
    };
  }
  const message = error && error.message ? String(error.message) : String(error);
  const ambient = /\b(process|fetch|Date|Math|setTimeout|setInterval|XMLHttpRequest|WebSocket)\b/.exec(message);
  const timeout = /timed out/i.test(message);
  return {
    code: timeout ? "MCEL_DSL_EVALUATION_TIMEOUT" : (ambient ? "MCEL_DSL_AMBIENT_GLOBAL_FORBIDDEN" : "MCEL_DSL_EVALUATION_FAILED"),
    semanticPath: "$source",
    summary: message,
    problem: message,
    observed: error && error.name ? error.name : "Error",
    expected: {kind: "portable-semantic-construction"},
    blocking: true,
    severity: "error",
    repairStage: "compile",
  };
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { input += chunk; });
process.stdin.on("end", () => {
  try {
    const request = JSON.parse(input);
    const ir = compile(request);
    process.stdout.write(JSON.stringify({schema: "mcel.dsl-runtime-result.v1", valid: true, ir}));
  } catch (error) {
    process.stdout.write(JSON.stringify({schema: "mcel.dsl-runtime-result.v1", valid: false, diagnostics: [diagnosticFromError(error)]}));
  }
});
