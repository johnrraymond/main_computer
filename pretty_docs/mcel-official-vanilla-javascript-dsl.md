# MCEL Official Vanilla-JavaScript DSL
## Syntax and Authoring Rules for `mcel.dsl.v1`

## Status

This document specifies the one official source syntax intended to construct `mcel.application-ir.v1`.

It is the source specification for the official DSL. A bounded Counter-only Wave 2B builder/compiler front end is now implemented in `main_computer/mcel_dsl_runtime.js`, `main_computer/mcel_dsl_compiler.py`, and `tools/mcel_dsl_compile.py`. That implementation constructs candidate IR and checks exact Counter semantic equivalence only. The complete DSL surface, Workbench coverage, app-local modules, generated contract projections, runtime changes, promotion, and retirement of existing application-definition paths remain unimplemented and unauthorized.

The governing companion documents are:

- `pretty_docs/mcel-ai-authoring-semantic-boundary.md`;
- `pretty_docs/mcel-application-ir-and-compiler-migration.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-constrained-expression-model.md`;
- `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`;
- `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`;
- `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`;
- `pretty_docs/mcel-ai-application-authoring-cycle.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`.

## The short answer

An MCEL v1 application is authored as ordinary strict-mode JavaScript that imports one compiler-provided module and exports one application declaration:

```javascript
"use strict";

const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {
    id: "inventory",
    title: "Inventory",
    requirements: "requirements.md"
  },
  (dsl) => {
    const {field, model, state, input, intent, surface, layout, prove} = dsl;

    // Stable semantic declarations.

    return {
      models: [],
      states: [],
      capabilities: [],
      invariants: [],
      intents: [],
      surfaces: [],
      layouts: [],
      scenarios: []
    };
  }
);
```

The JavaScript executes only to construct an inspectable semantic graph. It is not the application runtime.

### TL;DR

The author writes vanilla JavaScript. MCEL receives semantic declarations, not arbitrary runtime callbacks.

## 1. Why this is the official shape

The syntax uses four forms, each where it is strongest:

```text
plain objects
  application metadata and named declaration options

semantic handles
  stable references between models, state, intents, surfaces, and scenarios

constrained builder callbacks
  scoped construction of typed expression graphs

fluent chains
  ordered proof scenarios where order is part of the claim
```

The DSL does not offer alternative object-only, fluent-only, YAML, TypeScript, or custom-language forms.

### Wrong direction

```javascript
app("inventory")
  .state("items")
  .canonical()
  .list()
  .intent("add-item")
  .mutation()
  .view()
  .collection();
```

A whole-application fluent chain creates ambiguous ownership and order-sensitive edits.

### Also wrong

```javascript
module.exports = {
  state: {
    items: {authority: "canonical"}
  },
  intent: "add-item",
  source: "items"
};
```

A string-heavy object format repeats references and permits plausible-looking mismatches.

### Required direction

```javascript
const items = state.canonical("items", field.list(Item), {initial: []});

const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(field.text().minLength(1))
  },
  change: ({input}) => [
    items.append({
      id: dsl.id.next("item"),
      name: input.name
    })
  ]
});
```

### TL;DR

Use structured declarations for the application, semantic handles for references, expression builders for behavior, and fluent syntax only for ordered scenarios.

## 2. Source location and file ownership

The official root source is:

```text
mcel_apps/<app-id>/application.js
```

The likely generated destinations are:

```text
mcel_apps/<app-id>/generated/mcel.application.ir.json
mcel_apps/<app-id>/generated/application.definition.js
mcel_apps/<app-id>/contracts/
```

The root `application.js` is human- and AI-owned.

The `generated/` directory and generated contract files are compiler-owned.

During migration, legacy definitions may remain under an explicitly recorded compatibility path. They do not silently share ownership with the DSL source.

### Transitional manifest shape

```json
{
  "authoring": {
    "mode": "dual-authored",
    "dsl": {
      "language": "mcel.dsl.v1",
      "source": "application.js"
    },
    "legacy": {
      "source": "legacy/application.definition.js"
    },
    "ir": "generated/mcel.application.ir.json",
    "definitionProjection": "generated/application.definition.js"
  }
}
```

The exact manifest, ownership, candidate staging, projection, and promotion rules are specified in `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`.

### TL;DR

The official DSL owns `application.js`; generated IR and low-level definitions live behind an explicit generated boundary.

## 3. Module and export form

The official v1 module format is CommonJS:

```javascript
"use strict";

const mcel = require("@mcel/app");

module.exports = mcel.defineApp(...);
```

This is a compiler source contract, not a claim that `@mcel/app` is an external npm dependency. The compiler provides and resolves the module.

The following alternatives are not official v1 source forms:

```javascript
export default ...
```

```html
<script type="module">
```

```typescript
const app: McelApplication = ...;
```

A later version may add another module transport only through a versioned language change. Equivalent transports are not maintained in v1.

### TL;DR

One module form removes unnecessary choices from AI authoring and compiler diagnostics.

## 4. Root application declaration

`mcel.defineApp(metadata, builder)` is the only root constructor.

```javascript
module.exports = mcel.defineApp(
  {
    id: "contract-workbench",
    title: "Contract Operations Workbench",
    version: "1.0.0",
    requirements: "requirements.md"
  },
  (dsl) => {
    // declarations
    return {
      models,
      states,
      capabilities,
      invariants,
      intents,
      surfaces,
      layouts,
      scenarios
    };
  }
);
```

Required metadata:

| Field | Meaning |
| --- | --- |
| `id` | Stable application identity |
| `title` | Human-facing title |
| `requirements` | Repository-relative requirements authority |

Optional metadata includes a version and explicitly documented migration identifiers.

The metadata object must be literal and deterministic. It may not read environment variables, wall-clock time, random values, network results, or mutable files.

### Invalid

```javascript
mcel.defineApp({
  id: process.env.APP_ID,
  title: Date.now().toString(),
  requirements: chooseRequirementsAtRuntime()
}, ...);
```

### TL;DR

Application identity and requirements binding are explicit source facts, not runtime discoveries.

## 5. The builder callback is structural, not runtime behavior

The callback passed to `defineApp` executes at compile time to construct semantic handles.

```javascript
(dsl) => {
  const items = dsl.state.canonical(...);
  const addItem = dsl.intent.mutation(...);

  return {
    states: [items],
    intents: [addItem]
  };
}
```

This callback may:

- call DSL constructors;
- bind returned semantic handles to local constants;
- assemble literal arrays and objects;
- call approved app-local declaration modules;
- return the application root record.

It may not:

- perform I/O;
- inspect the DOM;
- mutate Git or files;
- read ambient process state;
- use nondeterministic values;
- perform application runtime work;
- hide semantic declarations behind dynamic discovery.

The compiler must reject semantic handles that are constructed but not reachable from the returned application root unless they are explicitly marked as migration-only diagnostics.

### TL;DR

The root callback builds the application graph. It does not run the application.

## 6. Stable IDs and JavaScript variable names

Every meaningful declaration receives an explicit stable semantic ID:

```javascript
const contracts = state.canonical("contracts", ...);
const addContract = intent.mutation("add-contract", ...);
const primary = surface.define("primary", ...);
```

The compiler derives canonical IR IDs by node kind:

```text
state:contracts
intent:add-contract
surface:primary
```

The JavaScript variable name is diagnostic and local:

```javascript
const createContract = intent.mutation("add-contract", ...);
```

Changing `addContract` to `createContract` does not change the semantic ID.

Labels are also not identities:

```javascript
surface.action("add-button", {
  intent: addContract,
  label: "Create Contract"
});
```

Changing the label does not rename `intent:add-contract`.

### Invalid

```javascript
intent.mutation(buttonLabel.toLowerCase().replaceAll(" ", "-"), ...);
```

### TL;DR

IDs are explicit and stable. Variable names and labels may change without silently changing application identity.

## 7. Models and fields

Models use `model(name, fields)` and typed field builders:

```javascript
const Contract = model("contract", {
  id: field.id(),
  name: field.text().minLength(1),
  category: field.enum("materials", "services", "transport"),
  quantity: field.integer().minimum(1),
  quoteStatus: field.enum(
    "idle",
    "running",
    "quoted",
    "partial",
    "failed",
    "cancelled"
  ),
  quoteAmount: field.integer().minimum(0)
});
```

Field modifiers construct immutable schema nodes. Modifier order is normalized when it has no semantic meaning:

```javascript
field.text().optional().minLength(1)
field.text().minLength(1).optional()
```

These normalize equivalently unless a modifier explicitly states order-sensitive semantics.

The official v1 field vocabulary includes at least:

```text
boolean
integer
number
text
id
enum
record
list
map
optional
union
literal
```

The complete schema mapping remains part of the IR schema authority.

### Invalid

```javascript
quantity: field.any()
```

when the field participates in canonical writes, arithmetic, sorting, capability requests, or proof claims.

### TL;DR

Declare semantic data once; validators, parsers, and IR schemas are generated from the same field declaration.

## 8. State declarations

State constructors require authority in the constructor name:

```javascript
const contracts = state.canonical(
  "contracts",
  field.list(Contract),
  {initial: []}
);

const draftName = state.local(
  "draft-name",
  field.text(),
  {initial: ""}
);

const QuoteProgress = field.record({
  status: field.enum("running", "partial", "complete", "failed", "cancelled"),
  received: field.integer().minimum(0),
  expected: field.integer().minimum(0),
  reports: field.list(field.record({
    amount: field.integer().minimum(0),
    source: field.text().minLength(1)
  })),
  failures: field.list(field.record({
    code: field.text().minLength(1)
  }))
});

const quoteProgress = state.provisional(
  "quote-progress",
  field.map(field.id(), QuoteProgress),
  {initial: {}}
);
```

Derived state declares dependencies and one constrained calculation:

```javascript
const visibleContracts = state.derived(
  "visible-contracts",
  field.list(Contract),
  {
    from: [contracts, filterText, sortMode],
    compute: ({read, query, expr}) => query
      .from(read(contracts))
      .where((contract) => expr.or(
        expr.isBlank(read(filterText)),
        expr.contains(contract.name, read(filterText), {case: "insensitive"}),
        expr.contains(contract.category, read(filterText), {case: "insensitive"})
      ))
      .orderBy(
        query.dynamicOrder(read(sortMode), {
          name: query.asc(Contract.name),
          quantity: query.asc(Contract.quantity),
          category: query.asc(Contract.category)
        }),
        query.asc(Contract.id)
      )
  }
);
```

`read(handle)` constructs an expression reference. It does not read live runtime state during compilation.

### Invalid

```javascript
state.value("search", "");
```

because authority is absent.

### Invalid

```javascript
compute: () => {
  contracts.push({name: "hidden write"});
  return contracts;
}
```

because a derivation may not mutate canonical state or use ordinary JavaScript collection methods on semantic handles.

### TL;DR

Authority is visible in the source. Derived state is an inspectable expression graph with declared dependencies.

## 9. Input declarations and value sources

Intent input fields combine a schema with a governed source class:

```javascript
input: {
  name: input.control(field.text().minLength(1)),
  quantity: input.control(field.integer().minimum(1)),
  createdBy: input.context(dsl.context.currentUserId),
  contractId: input.itemKey(contracts, Contract.id),
  revisedQuantity: input.itemField(Contract.quantity)
}
```

Supported source classes include:

```text
control
item-key
item-field
local-state
context
confirmation
literal
prior-step
capability-result
```

The source class is an independent semantic decision.

The surface may customize how a control is presented, but it does not redefine the input schema or authority.

### Wrong

```javascript
input: {
  createdBy: input.control(field.id())
}
```

when the value is actually supplied by governed user context.

### Wrong

```javascript
input: {
  contractId: input.control(field.id())
}
```

when the intended identity is the current collection row.

### TL;DR

An input declaration says both what a value means and where it comes from.

## 10. Builder callbacks and expression callbacks

The DSL permits callbacks only in documented builder positions.

Example:

```javascript
change: ({read, input, id}) => [
  contracts.append({
    id: id.next("contract"),
    name: input.name,
    quantity: input.quantity
  })
]
```

The callback receives symbolic handles. Its result must be a DSL node or a literal structure containing DSL nodes.

The callback is executed during compilation. It is not serialized as a JavaScript function and is not invoked by the application runtime.

The compiler must reject:

```javascript
change: async ({input}) => {
  await fetch("https://example.com");
  return input;
}
```

```javascript
change: function* () {
  yield Date.now();
}
```

```javascript
change: ({input}) => {
  while (true) {}
}
```

```javascript
change: ({input}) => customOpaqueFunction(input)
```

unless `customOpaqueFunction` is a registered pure domain operator with versioned semantics.

### TL;DR

Callbacks provide lexical scope for symbolic expressions. They are not an escape hatch for arbitrary runtime JavaScript.

## 11. Synchronous mutations

A mutation declares inputs, refusals, constrained canonical changes, and consequential postconditions:

```javascript
const addContract = intent.mutation("add-contract", {
  input: {
    name: input.control(field.text().minLength(1)),
    quantity: input.control(field.integer().minimum(1)),
    category: input.control(
      field.enum("materials", "services", "transport")
    )
  },

  refuses: ({input, refuse, expr}) => [
    refuse.when(
      expr.isBlank(input.name),
      "CONTRACT_NAME_REQUIRED",
      "A contract name is required."
    ),
    refuse.when(
      expr.lessThan(input.quantity, 1),
      "CONTRACT_QUANTITY_INVALID",
      "Quantity must be a positive integer."
    )
  ],

  change: ({input, id}) => [
    contracts.append({
      id: id.next("contract"),
      name: input.name,
      category: input.category,
      quantity: input.quantity,
      quoteStatus: "idle",
      quoteAmount: 0
    }),
    nextContractId.increment(1),
    revision.increment(1)
  ],

  ensures: ({before, after, claim, expr}) => [
    claim.equal(
      after(contracts).length(),
      expr.add(before(contracts).length(), 1)
    ),
    claim.equal(
      after(revision),
      expr.add(before(revision), 1)
    )
  ]
});
```

The compiler extracts and validates the read/write set from the expression graph. Authors may add an explicit `authority` declaration when needed, but they do not manually repeat mechanically provable lists.

### Explicit authority narrowing

```javascript
const clearAll = intent.mutation("clear-all", {
  authority: {
    writes: [contracts, revision]
  },
  change: () => [
    contracts.set([]),
    revision.increment(1)
  ]
});
```

If an expression writes outside the declared set, compilation fails.

### TL;DR

The author declares the canonical change once; MCEL derives and verifies read/write plumbing.

## 12. Prohibited intents

A prohibited intent is a real semantic declaration:

```javascript
const directSet = intent.prohibited("direct-set", {
  reason: "Arbitrary canonical assignment bypasses declared MCEL operations."
});
```

The compiler generates refusal, acceptance, observation, and effect-nonoccurrence obligations.

The author does not implement a handler that throws.

### Wrong

```javascript
const directSet = intent.mutation("direct-set", {
  change: () => {
    throw new Error("not allowed");
  }
});
```

That models an implementation failure, not a prohibited semantic operation.

### TL;DR

Prohibition is part of the application contract and proof surface, not an accidental exception path.

## 13. Invariants

Invariants use constrained predicates:

```javascript
const uniqueContractIds = dsl.invariant(
  "contract-keys-unique",
  {
    reads: [contracts],
    check: ({read, expr}) => expr.uniqueBy(
      read(contracts),
      Contract.id
    )
  }
);
```

An invariant may not call arbitrary JavaScript or external systems.

Domain-heavy invariants use registered pure operators:

```javascript
const validRefNames = dsl.invariant("git-ref-names-valid", {
  reads: [branches],
  check: ({read, domain}) => domain.git.allRefNamesValid(read(branches))
});
```

### TL;DR

Invariants are inspectable predicates over declared state, not hidden executable callbacks.

## 14. Capabilities

Capabilities declare governed external boundaries:

```javascript
const QuoteReport = field.record({
  amount: field.integer().minimum(0),
  source: field.text().minLength(1)
});

const QuoteResult = field.record({
  amount: field.integer().minimum(0),
  sourceCount: field.integer().minimum(0)
});

const QuoteService = dsl.capability("quote-service", {
  risk: "external-read-stream",
  operations: {
    requestQuote: dsl.capability.operation({
      request: field.record({
        contractId: field.id(),
        category: field.text().minLength(1),
        quantity: field.integer().minimum(1)
      }),
      event: field.union(
        field.record({type: field.literal("quote.started"), expected: field.integer()}),
        field.record({type: field.literal("quote.received"), report: QuoteReport}),
        field.record({type: field.literal("quote.failed"), code: field.text()})
      ),
      result: QuoteResult,
      stream: true,
      cancellable: true
    })
  }
});
```

Capability declarations describe interfaces and risk. They do not contain network, Git, filesystem, or export implementations.

The implementation belongs to a governed capability adapter outside the application DSL.

### TL;DR

The DSL declares what external work is allowed and evidenced; capability adapters perform it.

## 15. Asynchronous and effectful intents

An external operation is declared as one lifecycle:

```javascript
const requestQuote = intent.capability("request-quote", {
  input: {
    contractId: input.itemKey(contracts, Contract.id)
  },

  use: QuoteService.requestQuote,

  operationKey: ({input}) => input.contractId,

  request: ({read, input}) => dsl.request({
    contractId: input.contractId,
    category: read(contracts).byKey(input.contractId).field(Contract.category),
    quantity: read(contracts).byKey(input.contractId).field(Contract.quantity)
  }),

  provisional: {
    state: quoteProgress,
    initial: ({input}) => ({
      key: input.contractId,
      status: "running",
      received: 0,
      expected: 0,
      reports: [],
      failures: []
    }),
    receive: ({current, event, expr}) => expr.match(event.type, {
      "quote.started": current.merge({expected: event.expected}),
      "quote.received": current.merge({
        received: expr.add(current.received, 1),
        reports: expr.append(current.reports, event.report)
      }),
      "quote.failed": current.merge({
        failures: expr.append(current.failures, event)
      })
    })
  },

  concurrency: dsl.concurrency.latestPerKey(),
  cancellation: dsl.cancellation.allowed(),

  commit: ({input, provisional, expr}) => [
    contracts.updateByKey(input.contractId, (contract) => contract.merge({
      quoteStatus: expr.ifElse(
        provisional.failures.length().greaterThan(0),
        "partial",
        "quoted"
      ),
      quoteAmount: expr.averageInteger(
        provisional.reports.map(QuoteReport.amount)
      )
    })),
    revision.increment(1)
  ],

  effect: {
    allowedDispositions: [
      "committed",
      "cancelled",
      "superseded",
      "failed"
    ],
    cleanup: dsl.cleanup.removeKey(quoteProgress, ({input}) => input.contractId)
  }
});
```

The compiler generates the effect declaration, operation registry wiring, cancellation propagation, late-event authority checks, evidence binding, and cleanup accounting.

The author must still declare:

```text
capability operation
operation identity
request meaning
provisional reconciliation
concurrency
cancellation
canonical commit
allowed dispositions
cleanup or retention
```

### TL;DR

The DSL hides lifecycle machinery, not lifecycle policy.

## 16. Cancellation intents

Cancellation is separately addressable and targets one declared lifecycle:

```javascript
const cancelQuote = intent.cancel("cancel-quote", {
  target: requestQuote,
  key: input.itemKey(contracts, Contract.id),
  reason: "Cancel the active quote request for the selected contract."
});
```

The target intent must allow cancellation and use a compatible operation key.

The compiler rejects:

```javascript
intent.cancel("cancel-quote", {
  target: addContract
});
```

because `add-contract` is not an active cancellable lifecycle.

### TL;DR

Cancellation names the operation family and identity it may close.

## 17. Consequential effect policy

The DSL does not require authors to manually duplicate every derived effect record.

A capability intent mechanically implies a capability-request effect owned by that intent. A canonical transition mechanically implies an SCM-governed canonical-write effect.

The author must declare effect decisions that are not mechanically determined:

```javascript
effect: {
  allowedDispositions: ["committed", "cancelled", "superseded", "failed"],
  cleanup: dsl.cleanup.removeKey(quoteProgress, ({input}) => input.contractId),
  retain: []
}
```

Git Tools may declare an indeterminate external state and recovery path:

```javascript
effect: {
  risk: "remote-mutation",
  allowedDispositions: [
    "committed",
    "refused",
    "failed",
    "indeterminate"
  ],
  recovery: dsl.recovery.required({
    when: "indeterminate",
    capability: GitService.inspectRemote,
    closesWith: ["committed", "failed"]
  })
}
```

Document Editor export may explicitly retain an artifact:

```javascript
effect: {
  allowedDispositions: ["committed", "failed"],
  retain: [
    dsl.retention.artifact({
      kind: "document-export",
      owner: "user",
      evidence: "artifact-receipt"
    })
  ]
}
```

### TL;DR

Derived effect records are generated; independent disposition, recovery, cleanup, and retention decisions remain explicit.

## 18. Surface declarations

A surface is a semantic projection, not arbitrary DOM construction:

```javascript
const primary = surface.define("primary", {
  root: surface.region("shell", {
    role: "application",
    children: [
      surface.form("add-contract-form", {
        intent: addContract,
        fields: {
          name: surface.textInput({label: "Contract name"}),
          quantity: surface.numberInput({label: "Quantity", step: 1}),
          category: surface.select({label: "Category"})
        },
        submit: surface.submit({label: "Add contract"})
      }),

      surface.input("filter", {
        bind: filterText,
        control: "search",
        label: "Filter contracts"
      }),

      surface.input("sort", {
        bind: sortMode,
        control: "select",
        label: "Sort contracts"
      }),

      surface.collection("contracts", {
        source: visibleContracts,
        key: Contract.id,
        row: (item) => [
          surface.text("name", {value: item.name}),
          surface.text("category", {value: item.category}),
          surface.numberInput("quantity", {value: item.quantity}),
          surface.text("quote-status", {
            value: item.quoteStatus,
            provisional: quoteProgress.at(item.id).field("status")
          }),
          surface.action("update", {intent: updateQuantity}),
          surface.action("remove", {intent: removeContract}),
          surface.action("quote", {intent: requestQuote}),
          surface.action("cancel", {intent: cancelQuote})
        ]
      }),

      surface.receipt("latest-receipt")
    ]
  })
});
```

The row callback is structural. `item` is a symbolic item handle.

The compiler uses the intent input sources to derive row-key and row-field payload plumbing. It rejects a row action when required input sources cannot be unambiguously supplied.

### TL;DR

The surface declares semantic structure and meaningful presentation choices; MCEL generates ordinary binding plumbing.

## 19. Forms and control inference

A form bound to an intent may generate controls for `input.control(...)` fields:

```javascript
surface.form("add-contract-form", {
  intent: addContract
});
```

This is valid only when every control-sourced field has one deterministic default control and no presentation decision is missing.

The explicit form is required when the author needs labels, order, grouping, alternative controls, help text, or layout decisions:

```javascript
surface.form("add-contract-form", {
  intent: addContract,
  fields: {
    category: surface.radioGroup({
      label: "Category",
      order: ["materials", "services", "transport"]
    })
  }
});
```

Context-, item-, result-, and literal-sourced inputs are not rendered as form fields.

### TL;DR

The compiler may generate ordinary controls from semantic input fields, but it may not turn every input source into a visible form control.

## 20. Collections and stable identity

Collections require a stable key:

```javascript
surface.collection("contracts", {
  source: visibleContracts,
  key: Contract.id,
  row: (contract) => [
    surface.text("name", {value: contract.name})
  ]
});
```

Position is never an implicit key.

### Invalid

```javascript
surface.collection("contracts", {
  source: visibleContracts,
  row: (contract) => [...]
});
```

The compiler reports available stable scalar fields but does not choose one.

### TL;DR

Order is presentation. Key is identity. The author declares identity once.

## 21. Conditional and derived presentation

Conditional presentation uses expression claims:

```javascript
surface.when("empty-state", {
  if: ({read, expr}) => expr.isEmpty(read(visibleContracts)),
  then: surface.text("empty-message", {
    literal: "No contracts match the current view."
  })
});
```

Property projection uses semantic values:

```javascript
surface.text("total-quantity", {
  value: totalQuantity,
  format: "integer"
});
```

A surface declaration may not query the DOM to decide what to render.

### TL;DR

Visible conditions are projections of declared meaning, not DOM-driven hidden state.

## 22. Layout declarations

Layout is separate from surface semantics:

```javascript
const primaryLayout = layout.define("primary", {
  surface: primary,
  modes: ["compact", "wide"],
  regions: [
    layout.region(primary.region("shell"), {
      direction: "column",
      gap: "medium",
      padding: "large",
      minInlineSize: 320,
      maxInlineSize: 1120
    })
  ],
  constraints: [
    layout.minimumControlSize(primary.region("shell"), {
      inline: 44,
      block: 44
    }),
    layout.noHorizontalOverflow(primary.region("shell"))
  ]
});
```

The surface owns semantic roles. The layout owns spatial rules, responsive modes, ordering constraints, overflow, and scroll ownership.

### TL;DR

Surface says what the interface means. Layout says how semantic regions occupy space.

## 23. Proof scenarios

Scenarios use fluent syntax because ordered stimuli are part of the claim:

```javascript
const addSteel = prove.scenario("add-steel")
  .given(
    prove.canonical(contracts).equals([])
  )
  .step(
    "submit",
    prove.invoke(addContract, {
      name: "Steel",
      quantity: 12,
      category: "materials"
    }, {
      through: primary.form("add-contract-form")
    })
  )
  .expect(
    prove.receipt("submit").disposition("committed"),
    prove.canonical(contracts).contains({
      name: "Steel",
      quantity: 12,
      category: "materials"
    }),
    prove.visible(primary.collection("contracts")).containsRow({
      name: "Steel",
      quantity: "12"
    }),
    prove.effects("submit").allClosed()
  );
```

The scenario compiler generates acceptance and browser adapters from the same declared stimulus and claims.

The claims remain independent:

```text
receipt claim
canonical-state claim
visible-surface claim
effect-accounting claim
```

### Invalid self-confirming proof

```javascript
prove.scenario("add-steel")
  .step("submit", prove.invoke(addContract, {...}))
  .expect(prove.receipt("submit").disposition("committed"));
```

when the documented application claim also requires canonical and visible results.

### TL;DR

Write the expected behavior once, but claim it against distinct authorities.

## 24. Async proof scenarios

Named steps identify runtime effect instances:

```javascript
const quoteSupersession = prove.scenario("quote-supersession")
  .given(
    prove.operation(addContract, {
      name: "Steel",
      quantity: 12,
      category: "materials"
    })
  )
  .step("old", prove.invoke(requestQuote, {contractId: "contract-1"}))
  .step("latest", prove.invoke(requestQuote, {contractId: "contract-1"}))
  .expect(
    prove.effect("old").disposition("superseded"),
    prove.effect("old").cannotCommit(),
    prove.effect("latest").disposition("committed"),
    prove.canonical(contracts).item("contract-1").matches({
      quoteStatus: "quoted"
    }),
    prove.cleanup(quoteProgress).key("contract-1").closed(),
    prove.effects().noUnexplainedResidue()
  );
```

Cancellation is equally explicit:

```javascript
const cancelQuoteScenario = prove.scenario("cancel-quote")
  .given(prove.operation(addContract, {...}))
  .step("request", prove.invoke(requestQuote, {contractId: "contract-1"}))
  .step("cancel", prove.invoke(cancelQuote, {contractId: "contract-1"}))
  .expect(
    prove.effect("request").disposition("cancelled"),
    prove.canonical(contracts).unchangedSince("request"),
    prove.cleanup(quoteProgress).key("contract-1").closed()
  );
```

### TL;DR

Async proof names each operation instance and proves authority loss, disposition, cleanup, and visible/canonical consequences.

## 25. Multi-instance proof

Multi-instance behavior is a top-level scenario because it crosses state authorities and mounted roots:

```javascript
const isolation = prove.scenario("multi-instance-isolation")
  .mount("left", primary)
  .mount("right", primary)
  .step("left-add", prove.invoke(addContract, {
    name: "Steel",
    quantity: 12,
    category: "materials"
  }, {
    instance: "left"
  }))
  .expect(
    prove.instance("left").canonical(contracts).itemCount(1),
    prove.instance("right").canonical(contracts).itemCount(0),
    prove.instances("left", "right").isolated([
      "canonical-state",
      "local-state",
      "provisional-state",
      "operation-ledger",
      "receipts",
      "roots"
    ])
  );
```

The exact isolation expectation depends on the application’s instance ownership model. The scenario must not assume isolation where the application deliberately shares canonical authority.

### TL;DR

Instance ownership is proven explicitly rather than inferred from two similar-looking DOM trees.

## 26. App-local modules

Large applications may split declarations across relative JavaScript modules while retaining one official DSL.

Root:

```javascript
"use strict";

const mcel = require("@mcel/app");
const repositoryModelModule = require("./dsl/repository-model.js");
const gitIntentModule = require("./dsl/git-intents.js");

module.exports = mcel.defineApp(metadata, (dsl) => {
  const repositoryModel = dsl.use(repositoryModelModule);
  const gitIntents = dsl.use(gitIntentModule, {
    Repository: repositoryModel.Repository,
    repositories: repositoryModel.repositories
  });

  return {
    models: repositoryModel.models,
    states: repositoryModel.states,
    capabilities: gitIntents.capabilities,
    intents: gitIntents.intents,
    surfaces: [],
    layouts: [],
    scenarios: []
  };
});
```

Module:

```javascript
"use strict";

const mcel = require("@mcel/app");

module.exports = mcel.defineModule(
  "git-tools.repository-model",
  (dsl) => {
    const Repository = dsl.model("repository", {...});
    const repositories = dsl.state.canonical(...);

    return {
      exports: {Repository, repositories},
      models: [Repository],
      states: [repositories]
    };
  }
);
```

Rules:

- imports must be static relative paths or `@mcel/app`;
- module IDs are explicit;
- module dependencies are explicit through `dsl.use` bindings;
- modules return semantic declarations and exports;
- modules do not self-register globally;
- dynamic `require`, dynamic `import`, package discovery, and environment-based module choice are rejected.

### TL;DR

Large apps may be modular, but module composition remains explicit, static, and part of one semantic graph.

## 27. Compile-time constants

The DSL permits literal compile-time constants:

```javascript
const categories = ["materials", "services", "transport"];

const Contract = model("contract", {
  category: field.enum(...categories)
});
```

Constants must be transitively composed from literals and approved pure compile-time helpers.

### Invalid

```javascript
const categories = JSON.parse(
  require("fs").readFileSync("categories.json", "utf8")
);
```

### Invalid

```javascript
const categories = process.env.CONTRACT_CATEGORIES.split(",");
```

Application configuration that changes semantic meaning must be modeled as explicit input, context, versioned source data, or a separate compiled application variant.

### TL;DR

Compile-time constants are deterministic source data, not hidden environment configuration.

## 28. Declaration order

Declaration order is not semantic unless the node kind explicitly says order matters.

These may normalize equivalently:

```javascript
const Item = model(...);
const User = model(...);
```

```javascript
const User = model(...);
const Item = model(...);
```

These remain order-sensitive:

```text
scenario steps
layout-before constraints
surface child order where presentation order is claimed
transition sequences when one step depends on another
```

The compiler normalizes unordered collections by stable semantic ID.

### TL;DR

Source order helps readers; only declared semantic order affects the IR fingerprint.

## 29. Unsupported JavaScript in v1

The official compiler must reject or quarantine at least:

```text
eval and Function constructors
dynamic require or import
network, filesystem, Git, DOM, timer, and process access
Date.now, new Date without governed input, Math.random, crypto randomness
async or generator builder callbacks
unbounded loops or recursion
prototype mutation
reflection used to discover semantic declarations
ambient global reads
runtime-dependent declaration branching
opaque function values in the application graph
semantic IDs generated from mutable labels or iteration positions
```

Ordinary JavaScript syntax is not automatically valid MCEL DSL syntax merely because Node can execute it.

### TL;DR

Vanilla JavaScript is the transport language. MCEL remains a closed semantic language.

## 30. Required compiler diagnostics

The syntax must permit precise errors such as:

```text
MCEL_DSL_UNREACHABLE_DECLARATION
source: application.js:42:3
semantic id: intent:add-item
problem: declaration is not reachable from the returned application root
repair: add `addItem` to `intents`, or remove the declaration
```

```text
MCEL_DSL_AMBIGUOUS_CONTROL_SOURCE
source: application.js:118:7
semantic path: surface:primary/form:add-contract-form
problem: intent:add-contract input `name` has two candidate controls
repair: bind the field explicitly in `fields.name`
```

```text
MCEL_DSL_OPAQUE_CALLBACK
source: application.js:73:11
semantic path: intent:add-contract/change
problem: callback returned an ordinary JavaScript value instead of an MCEL expression node
repair: use `contracts.append(...)` or a registered domain operator
```

```text
MCEL_DSL_EFFECT_DISPOSITION_INCOMPLETE
source: application.js:201:5
semantic path: intent:git-push/effect
problem: remote mutation may become indeterminate but no recovery path is declared
repair: add `indeterminate` and a recovery declaration, or prove the capability cannot become indeterminate
```

The later diagnostic specification governs the full schema and authoring-cycle re-entry behavior.

### TL;DR

The syntax is designed so failures can identify the missing semantic decision, not merely a JavaScript exception.

## 31. Complete Counter example

```javascript
"use strict";

const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {
    id: "contract-counter",
    title: "Contract Counter",
    requirements: "requirements.md"
  },
  (dsl) => {
    const {field, state, intent, surface, layout, prove} = dsl;

    const count = state.canonical(
      "count",
      field.integer().minimum(0),
      {initial: 0}
    );

    const increment = intent.mutation("increment", {
      change: () => [count.increment(1)],
      ensures: ({before, after, claim, expr}) => [
        claim.equal(after(count), expr.add(before(count), 1))
      ]
    });

    const reset = intent.mutation("reset", {
      change: () => [count.set(0)],
      ensures: ({after, claim}) => [
        claim.equal(after(count), 0)
      ]
    });

    const directSet = intent.prohibited("direct-set", {
      reason: "Direct canonical assignment bypasses declared intents."
    });

    const primary = surface.define("primary", {
      root: surface.region("shell", {
        role: "application",
        children: [
          surface.text("count", {value: count, format: "integer"}),
          surface.action("increment", {
            intent: increment,
            label: "Increment"
          }),
          surface.action("reset", {
            intent: reset,
            label: "Reset"
          }),
          surface.receipt("latest-receipt")
        ]
      })
    });

    const primaryLayout = layout.define("primary", {
      surface: primary,
      regions: [
        layout.region(primary.region("shell"), {
          direction: "column",
          gap: "small"
        })
      ]
    });

    const incrementScenario = prove.scenario("increment")
      .step("increment", prove.invoke(increment, {}, {through: primary}))
      .expect(
        prove.receipt("increment").disposition("committed"),
        prove.canonical(count).equals(1),
        prove.visible(primary.node("count")).textEquals("1"),
        prove.effects("increment").allClosed()
      );

    const resetScenario = prove.scenario("reset")
      .given(prove.operation(increment))
      .step("reset", prove.invoke(reset, {}, {through: primary}))
      .expect(
        prove.canonical(count).equals(0),
        prove.visible(primary.node("count")).textEquals("0")
      );

    const prohibitedScenario = prove.scenario("direct-set-prohibited")
      .step("direct", prove.invoke(directSet, {count: 100}))
      .expect(
        prove.receipt("direct").code("INTENT_PROHIBITED"),
        prove.canonical(count).unchanged(),
        prove.effects("direct").noneOccurred()
      );

    return {
      models: [],
      states: [count],
      capabilities: [],
      invariants: [],
      intents: [increment, reset, directSet],
      surfaces: [primary],
      layouts: [primaryLayout],
      scenarios: [incrementScenario, resetScenario, prohibitedScenario]
    };
  }
);
```

This example is intentionally small. The compiler must not require seven manually coordinated contract files to express it.

### TL;DR

Counter proves that the official syntax can preserve canonical authority, mutation, prohibition, surface projection, and proof without Workbench-scale ceremony.

## 32. Git Tools migration slice

A governed push should look conceptually like:

```javascript
const push = intent.capability("push", {
  input: {
    repositoryId: input.control(field.id()),
    remote: input.control(field.text().minLength(1)),
    branch: input.control(field.text().minLength(1)),
    confirmation: input.confirmation("git-push")
  },

  use: GitService.push,

  preflight: ({read, input, domain, refuse}) => [
    refuse.unless(
      domain.git.repositoryClean(read(repositoryState), input.repositoryId),
      "GIT_WORKTREE_NOT_CLEAN"
    ),
    refuse.unless(
      domain.git.refNameValid(input.branch),
      "GIT_BRANCH_INVALID"
    )
  ],

  request: ({input}) => dsl.request({
    repositoryId: input.repositoryId,
    remote: input.remote,
    branch: input.branch,
    confirmation: input.confirmation
  }),

  operationKey: ({input}) => input.repositoryId,

  effect: {
    risk: "remote-mutation",
    allowedDispositions: [
      "committed",
      "refused",
      "failed",
      "indeterminate"
    ],
    recovery: dsl.recovery.required({
      when: "indeterminate",
      capability: GitService.inspectRemote,
      closesWith: ["committed", "failed"]
    })
  }
});
```

The legacy Git Tools requirements, adapters, confirmation policy, receipts, and recovery behavior must map into the same IR facts before the DSL becomes primary.

### TL;DR

The DSL does not reduce Git push to a button callback; it preserves preflight, confirmation, remote effect, uncertainty, recovery, and receipt obligations.

## 33. Code Editor migration slice

A save intent should preserve draft ownership and stale-source protection:

```javascript
const saveFile = intent.capability("save-file", {
  input: {
    fileId: input.context(EditorContext.activeFileId),
    expectedHash: input.context(EditorContext.loadedContentHash),
    content: input.local(draftContent)
  },

  use: FileService.write,

  preflight: ({input, domain, refuse}) => [
    refuse.unless(
      domain.path.isInsideRoot(input.fileId, ProjectContext.root),
      "FILE_OUTSIDE_PROJECT_ROOT"
    ),
    refuse.unless(
      domain.content.hashMatches(input.fileId, input.expectedHash),
      "FILE_STALE_SOURCE"
    )
  ],

  request: ({input}) => dsl.request({
    fileId: input.fileId,
    expectedHash: input.expectedHash,
    content: input.content
  }),

  operationKey: ({input}) => input.fileId,

  effect: {
    risk: "filesystem-mutation",
    allowedDispositions: [
      "committed",
      "refused",
      "failed",
      "indeterminate"
    ],
    retain: [
      dsl.retention.localState(draftContent, {
        when: ["refused", "failed", "indeterminate"]
      })
    ]
  }
});
```

The explicit retention declaration explains why a failed or stale save leaves the draft visible instead of treating that state as unexplained residue.

### TL;DR

The DSL must preserve the editor’s draft, stale-source, filesystem-effect, and retention semantics—not merely emit a write request.

## 34. Document Editor migration slice

An export intent should separate pure export planning from the artifact effect:

```javascript
const exportDocument = intent.capability("export-document", {
  input: {
    documentId: input.context(DocumentContext.activeDocumentId),
    format: input.control(field.enum("pdf", "docx", "html")),
    selection: input.local(activeSelection)
  },

  use: DocumentService.export,

  request: ({read, input, domain}) => domain.document.exportPlan({
    document: read(documentState),
    documentId: input.documentId,
    selection: input.selection,
    format: input.format
  }),

  operationKey: ({input}) => input.documentId,

  effect: {
    risk: "artifact-creation",
    allowedDispositions: ["committed", "failed"],
    retain: [
      dsl.retention.artifact({
        kind: "document-export",
        owner: "user",
        evidence: "artifact-receipt"
      })
    ]
  }
});
```

The Document Editor’s semantic regions, selection authority, scroll ownership, export planning, artifact ownership, and browser-visible receipt remain separate facts in the IR.

### TL;DR

The DSL preserves editor semantics and artifact ownership instead of flattening export into opaque JavaScript.

## 35. Mapping to the IR

Every constructor maps deterministically:

| DSL construct | Primary IR node |
| --- | --- |
| `defineApp` | `application` |
| `model` / `field` | `models` and schema nodes |
| `state.canonical/local/provisional` | `states` |
| `state.derived` | `derivations` plus expression graph |
| `intent.mutation` | mutation intent plus transition/refusal/claim nodes |
| `intent.prohibited` | prohibited intent and nonoccurrence obligations |
| `capability` | capability interface |
| `intent.capability` | intent, capability use, effect declaration, lifecycle policy |
| `intent.cancel` | cancellation intent and target relation |
| `surface.define` | surface, region, node, and binding records |
| `layout.define` | layout records and constraints |
| `prove.scenario` | scenario steps and independent claims |
| `defineModule` / `use` | provenance and module composition records |

Raw DSL source formatting, variable names, and declaration order are excluded from the semantic fingerprint unless they carry declared meaning.

Source locations and module paths remain in the source-binding fingerprint.

### TL;DR

The syntax is a readable constructor for the IR, not a second semantic system.

## 36. Migration requirements per DSL pass

Every syntax addition or change must update the migration ledger for all affected application families.

Required questions:

```text
Which independent decision does the construct express?
Which IR nodes does it create?
Which current definition families already express that decision?
Can their current form be imported without loss?
Which low-level projections change?
Which acceptance, browser, receipt, or effect evidence must be renewed?
Does the construct reduce or increase opaque migration debt?
```

At minimum, each pass checks:

```text
Contract Counter
Contract Workbench
Git Tools
Code Editor
Document Editor
other affected requirements-registry apps
legacy surface-only apps when the construct affects surfaces or layout
```

A pass may record `unchanged and compatible`; it may not silently ignore a layer.

### TL;DR

The syntax advances only when current apps and compilers remain mapped, comparable, and recoverable.

## 37. DSL-v1 completion criteria for syntax

The syntax portion of DSL v1 is complete only when:

1. the root module and app-local module forms are fixed;
2. every required IR node family has an authoring construct;
3. every constrained-expression context has a valid source form;
4. every effect policy and proof claim has a valid source form;
5. Counter can be expressed with low ceremony;
6. Workbench can be expressed without opaque callbacks;
7. Git Tools can express confirmation, remote mutation, uncertainty, and recovery;
8. Code Editor can express drafts, stale-source checks, filesystem effects, and retained state;
9. Document Editor can express semantic regions, persistence/export effects, and retained artifacts;
10. source-to-IR normalization is deterministic;
11. invalid JavaScript and invalid semantic combinations have stable diagnostics;
12. the scaffolder and generated ownership rules are documented;
13. dual-authored equivalence can be checked before any legacy compiler is retired.

### TL;DR

The syntax is complete when it can author and migrate the real MCEL application families—not when it can make Counter look elegant.

## 38. Compiler diagnostics and repair boundary

The official DSL does not expose raw builder exceptions as its public failure contract. Source construction, IR validation, migration comparison, projection, evidence preparation, and proof failures normalize through `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`.

A diagnostic points to the highest authored source, names the stable semantic path, classifies each repair as mechanical or consequential, preserves the last proven application, and states which generated artifacts and evidence the candidate invalidates. Generated files are never the normal repair target.

### TL;DR

The DSL is only AI-authorable when its failures return the AI to the correct semantic decision instead of exposing compiler internals.

## 39. Documentation sequence from here

Completed foundations:

```text
AI authoring executive overview
semantic authoring boundary
IR and compiler migration model
IR schema and normalization
existing-definition migration inventory
constrained expression model
consequential effects and proof accounting
official vanilla-JavaScript DSL syntax
compiler diagnostics and repair protocol
scaffolder, generated projection, and compatibility specification
AI application authoring cycle
AI authoring pattern catalog
semantic change and evidence impact
```

The benchmark contract is now specified in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`, including hard semantic gates, controlled repeated sessions, migration cases, repair injections, proof-independence cases, and economy thresholds.

The final documentation review is complete in `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md`. It confirms that this source contract agrees with the IR, expression, effect, diagnostics, compatibility, authoring-cycle, change-impact, and benchmark specifications.

The bounded Counter compiler now exists, but this document still does not claim that the complete DSL surface exists or that DSL v1 has passed its benchmark. Wave 2B proves only that one official strict CommonJS source can construct a valid Counter IR candidate with the exact legacy semantic fingerprint and a distinct DSL source-binding fingerprint.

## 40. Implemented Wave 2B Counter boundary

The executable source fixture is:

```text
tests/fixtures/mcel_dsl/contract-counter.application.js
```

Compile and compare it without changing the live application:

```text
python tools/mcel_dsl_compile.py \
  --input tests/fixtures/mcel_dsl/contract-counter.application.js \
  --compare-ir tests/fixtures/mcel_application_ir/contract-counter.ir.json
```

Stage the normalized candidate under runtime state only:

```text
python tools/mcel_dsl_compile.py \
  --input tests/fixtures/mcel_dsl/contract-counter.application.js \
  --compare-ir tests/fixtures/mcel_application_ir/contract-counter.ir.json \
  --write-candidate
```

The Counter fixture includes the current revision state, stale-revision scenario, invariants, prohibited direct-set, four canonical-write effects, surface, layout, and proof claims. Its semantic fingerprint must equal the legacy Counter IR fingerprint. Its source-binding fingerprint must differ because the authored source and compiler front end differ.

The restricted Node construction context exposes only `@mcel/app`, denies other modules and ambient effect sources, disables string/wasm code generation, and enforces a timeout. This is a compiler construction boundary, not a general-purpose security sandbox for hostile code.

### TL;DR

Wave 2B proves one exact Counter source-to-IR path; it does not yet compile Workbench or generate live package contracts.

## Final rule

> The official MCEL DSL is one deterministic vanilla-JavaScript language for declaring stable semantic identity, authority, behavior, lifecycle policy, surface meaning, consequential effects, and proof claims. It generates mechanical plumbing, but it never hides an independent decision or replaces independent runtime evidence.

## 41. Implemented Wave 10 Workbench portability boundary

Wave 10 adds a second strict CommonJS `@mcel/app` candidate:

```text
tests/fixtures/mcel_dsl/contract-workbench.application.js
```

The candidate uses an explicit `migration.importApplicationIr(...)` bridge. The bridge is allowed only for repository-bound migration candidates; it is not the normal final authoring surface. It replaces source bindings deterministically, preserves semantic records, and emits explicit migration lineage. It cannot erase `legacy.opaque-function` warnings or claim complete DSL-v1 expression coverage.

The corresponding repository-derived fixture is:

```text
tests/fixtures/mcel_application_ir/contract-workbench.ir.json
```

The generic compile and projection commands require exact semantic equality between the live normalized definition, the fixture, and the strict CommonJS candidate. The Workbench projection profile then regenerates the normalized definition and seven explicit contracts in an isolated candidate package and requires exact package and semantic round-trip results.

A passing Wave 10 result therefore proves:

```text
second application discovered generically
strict @mcel/app candidate compiled
live, fixture, and candidate semantics exact
isolated generated package exact
fresh acceptance and Chromium evidence pass
candidate truth status semantic-runtime-proven
live authority remains legacy-explicit-package
promotion executed false
```

It does not yet prove:

```text
all Workbench callbacks replaced by constrained expressions
portable IR alone can generate every Workbench contract
Workbench is eligible for promotion
```

### TL;DR

Wave 10 proved that the generic pipeline supports a materially larger second application and made the remaining callback/projection debt explicit.

## 42. Implemented Wave 11 Workbench constrained-expression boundary

Wave 11 converts every active Workbench callback region into one registered native constrained expression. The strict CommonJS candidate now uses the official low-level IR construction surface:

```javascript
module.exports = mcel.defineApp(metadata, ({ir}) =>
  ir.application(portableApplication)
);
```

`portableApplication` contains 26 `domain.call` roots and zero active `legacy.opaque-function` roots. Each operator is versioned, pure, deterministic, context-limited, argument-complete, and result-typed. The former callback record remains beneath `compatibility.legacyOpaqueFunction` only to preserve the existing v1 semantic identity; it is not traversed as an executable expression and does not emit migration debt.

The Workbench operator registry is selected by the stable application identity and fails closed for unknown or unversioned operators. The portable projection profile is separately hash-bound and contains the deterministic low-level contract mechanics needed to reproduce the live package. Candidate generation does not execute the former callbacks or invoke the live normalized-definition compiler.

A passing Wave 11 result proves:

```text
26 registered native domain calls
0 active opaque callbacks
0 migration warnings
semantic fingerprint unchanged
portable IR projection complete
isolated candidate truth semantic-runtime-proven
live Workbench authority unchanged
promotion not executed
```

### TL;DR

Workbench now satisfies the documented “expressed without opaque callbacks” gate. Promotion remains a separate transaction and is not authorized by Wave 11.
