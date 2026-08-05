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

function createDsl(metadata, source) {
  const states = [];
  const invariants = [];
  const intents = [];
  const effects = [];
  const surfaces = [];
  const layouts = [];
  const scenarios = [];
  const stateById = new Map();

  const field = {
    integer: () => new SchemaBuilder({kind: "integer"}),
    text: () => new SchemaBuilder({kind: "string"}),
    boolean: () => new SchemaBuilder({kind: "boolean"}),
  };

  const state = {
    canonical(name, schema, options = {}) {
      assert(schema instanceof SchemaBuilder, "MCEL_DSL_SCHEMA_REQUIRED", `Canonical state '${name}' requires a field schema.`, `state:${name}`);
      const record = {
        id: semanticId("state", name),
        kind: "state",
        sourceName: name,
        authority: "canonical",
        schema: schema.toJSON(),
        initial: deepClone(options.initial),
        source,
      };
      const handle = new StateHandle(record);
      states.push(handle);
      stateById.set(handle.id, handle);
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
    const rawSteps = options.change({read, expr});
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
      input: [],
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

  const intent = {
    mutation: buildMutation,
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

  const surface = {
    text(name, options = {}) {
      const record = {
        id: nodeId(name, options), kind: "surface-node", nodeKind: "state-value",
        value: expression(options.value),
      };
      return new SurfaceNodeHandle(name, record);
    },
    action(name, options = {}) {
      assert(options.intent instanceof IntentHandle, "MCEL_DSL_ACTION_INTENT_REQUIRED", `Surface action '${name}' requires an intent handle.`);
      const record = {
        id: nodeId(name, options), kind: "surface-node", nodeKind: "control", intent: ref(options.intent),
      };
      return new SurfaceNodeHandle(name, record);
    },
    receipt(name, options = {}) {
      const record = {id: nodeId(name, options), kind: "surface-node", nodeKind: "operation-evidence"};
      return new SurfaceNodeHandle(name, record);
    },
    region(name, options = {}) {
      return {kind: "dsl.surface-region", name, children: (options.children || []).flat()};
    },
    define(name, options = {}) {
      assert(options.root && options.root.kind === "dsl.surface-region", "MCEL_DSL_SURFACE_ROOT_REQUIRED", `Surface '${name}' requires a structural root region.`);
      const nodes = [];
      const nodesByName = new Map();
      for (const child of options.root.children) {
        assert(child instanceof SurfaceNodeHandle, "MCEL_DSL_SURFACE_CHILD_INVALID", `Surface '${name}' contains an unsupported structural child.`);
        nodes.push(child.record);
        nodesByName.set(child.localName, child);
      }
      const record = {
        id: semanticId("surface", options.id || `${metadata.id}.${name}`),
        kind: "surface",
        sourceName: options.sourceName || `${metadata.title.replace(/\s+/g, "")}Surface`,
        nodes,
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

  return {
    field, state, intent, invariant, surface, layout, prove, expr, ir, migration,
    __collections: {states, invariants, intents, effects, surfaces, layouts, scenarios},
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
  imported.application.authoringStatus = "dual-authored";
  const inheritedGaps = [...(imported.migration?.knownGaps || [])].filter((value) => value !== "migration-ir-bridge-not-final-authoring-surface" && value !== "opaque-callbacks-require-constrained-expression-replacement");
  imported.migration = {
    ...(imported.migration || {}),
    state: "dual-authored",
    sourceFamily: native ? "official-vanilla-javascript-dsl" : "official-vanilla-javascript-dsl-migration-bridge",
    knownGaps: [...new Set([...inheritedGaps, ...(native ? [] : ["migration-ir-bridge-not-final-authoring-surface"]), "candidate-not-promoted", "legacy-package-remains-live"])].sort(),
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
  const intentRecords = unwrap(built.intents, IntentHandle, "intents");
  const surfaceRecords = unwrap(built.surfaces, SurfaceHandle, "surfaces");
  const layoutRecords = unwrap(built.layouts, LayoutHandle, "layouts");
  const invariantRecords = built.proof && Array.isArray(built.proof.invariants)
    ? deepClone(built.proof.invariants)
    : unwrap(built.invariants || [], InvariantHandle, "invariants");

  const allRecords = [
    {id: semanticId("app", metadata.id), source},
    ...stateRecords, ...intentRecords, ...dsl.__collections.effects,
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
    capabilities: deepClone(built.capabilities || []),
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
