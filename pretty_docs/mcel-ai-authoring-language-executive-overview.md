# MCEL AI Authoring Language
## Executive Overview by Example

This document explains why MCEL needs a higher-level AI authoring language. It uses code examples rather than abstract promises.

The governing rule is:

> Remove mechanical repetition, but keep authority decisions explicit.

## 1. One form field currently creates several declarations

### Application idea

```text
Quantity is an integer.
It starts at 1.
The Add operation receives it.
```

### Current explicit form

The same idea may be repeated as renderer-local state:

```javascript
state: {
  draftQuantity: {
    authority: "renderer-local",
    initial: "1",
    schema: string()
  }
}
```

An input binding:

```javascript
mcel.node.input({
  id: "contract-workbench.draft-quantity",
  localPath: "draftQuantity",
  inputType: "number"
});
```

A payload extraction rule:

```javascript
payload: {
  quantity: {
    fromNode: "contract-workbench.draft-quantity",
    property: "value",
    parse: "integer"
  }
}
```

And an operation schema:

```javascript
input: {
  quantity: integer({min: 1})
}
```

An AI can miss one declaration, use inconsistent names, forget the parser, or change the schema without changing the interface.

### Desired authoring level

```javascript
quantity: numberField({
  initial: 1,
  min: 1,
  for: addContract.input.quantity
})
```

The compiler derives:

```text
renderer-local draft state
number input binding
integer parser
payload construction
validation
observation binding
```

### TL;DR

Declare the field once. Generate its mechanical relationships.

## 2. Collection actions repeat item-key plumbing

### Application idea

```text
Click Remove on a contract row.
Remove that contract.
```

### Current explicit form

The collection declares its key:

```javascript
collection({
  statePath: "visibleContracts",
  keyPath: "id"
});
```

The row declares its control:

```javascript
{
  selector: "[data-mcel-item-intent='remove-contract']",
  intentId: "remove-contract"
}
```

The payload repeats the current item key:

```javascript
payload: {
  contractId: {
    fromItemKey: true
  }
}
```

The operation separately declares:

```javascript
input: {
  contractId: string()
}
```

### Desired authoring level

```javascript
rowAction(removeContract, {
  item: contract.id
})
```

Or, when the operation input and collection key establish the relationship unambiguously:

```javascript
rowAction(removeContract)
```

The compiler already knows the collection key, operation input, and row context.

### Constraint

The compiler may connect an explicitly declared item key to an operation. It may not invent the item key.

This remains invalid:

```javascript
collection(contracts, {
  row: ContractRow
});
```

when the application has not identified stable item identity.

### TL;DR

Infer key plumbing only after identity is explicit.

## 3. A mutation is distributed across several structures

### Application idea

```text
Add a contract to canonical state.
```

### Current explicit form

The author coordinates an intent declaration:

```javascript
intent({
  id: "add-contract",
  kind: "mutation"
});
```

Preflight validation:

```javascript
preflight({state, payload}) {
  if (!payload.name.trim()) {
    return refusal("CONTRACT_NAME_REQUIRED");
  }
}
```

The canonical transition:

```javascript
transition({state, payload}) {
  return {
    ...state,
    contracts: [
      ...state.contracts,
      {
        id: `contract-${state.nextContractId}`,
        ...payload
      }
    ]
  };
}
```

A postcondition:

```javascript
ensures({before, after}) {
  return after.contracts.length === before.contracts.length + 1;
}
```

And a proof scenario:

```javascript
scenario("add contract", {
  intentId: "add-contract"
});
```

These pieces are meaningful, but the application intention is fragmented.

### Desired authoring level

```javascript
const addContract = mutation({
  input: {
    name: text.required(),
    quantity: integer.min(1),
    category: enumOf("materials", "services", "transport")
  },

  change({state, input}) {
    state.contracts.append({
      id: nextId(state.nextContractId),
      ...input
    });
  },

  proves: scenario()
    .enter({
      name: "Steel",
      quantity: 12,
      category: "materials"
    })
    .expectCommitted()
    .expectRow({
      name: "Steel",
      quantity: 12
    })
});
```

The transition and claimed outcome remain explicit. The compiler generates the surrounding contract machinery.

### TL;DR

Keep the consequential transition explicit. Generate its repeated contract scaffolding.

## 4. The language must not guess state authority

### Application idea

```text
There is a search field.
```

That statement is incomplete. Search might be:

```text
local to one mounted interface
shared canonical application state
stored user preference
URL state
```

### Correct explicit declarations

```javascript
search: local.string("")
```

```javascript
contracts: canonical.list(Contract)
```

```javascript
visibleContracts: derived({
  from: [contracts, search],
  compute: ({contracts, search}) =>
    contracts.filter(matches(search))
})
```

### Dangerous authoring behavior

This is too ambiguous:

```javascript
search: string("")
```

The compiler must not silently decide that `search` is local merely because search fields are commonly local. That decision changes authority, persistence, and multi-instance behavior.

### TL;DR

Reduce the syntax used to declare authority. Never invent authority.

## 5. Async operations require coordinated lifecycle declarations

### Application idea

```text
Request quotes.
Show progress.
Commit the final result.
```

### Current coordination burden

The author may need to coordinate:

```text
capability contract
async intent
request schema
stream event schema
provisional state
receive function
final reconciliation
cancellation target
concurrency policy
row progress projection
acceptance scenarios
browser scenarios
```

### Desired authoring level

```javascript
const requestQuote = capabilityOperation({
  input: {
    contractId: itemKey()
  },

  capability: quotes.requestQuote,

  progress: provisional.by("contractId", {
    initial: {
      status: "running",
      received: 0,
      expected: 0
    },

    receive(progress, event) {
      return applyQuoteEvent(progress, event);
    }
  }),

  concurrency: latestPerItem("contractId"),
  cancellableBy: cancelQuote,

  commit({state, input, result}) {
    state.contracts
      .byId(input.contractId)
      .setQuote(result);
  }
});
```

The author still chooses:

```text
capability
provisional key
event reconciliation
concurrency policy
cancellation relationship
final canonical commit
```

The compiler supplies:

```text
active-operation registry wiring
AbortSignal plumbing
late-event suppression
provisional cleanup
receipt structures
scenario bindings
proof obligations
```

### TL;DR

Hide lifecycle plumbing. Preserve lifecycle policy.

## 6. Proof is another representation of the same feature

### Application idea

```text
Adding Steel creates a visible Steel row.
```

### Current drift risk

The AI can be asked to maintain separate versions of the same claim:

```text
operation test
state assertion
browser script
DOM assertion
receipt assertion
intent-coverage mapping
```

Those versions can disagree.

### Desired declaration

```javascript
scenario("add Steel")
  .enter({
    name: "Steel",
    quantity: 12,
    category: "materials"
  })
  .invoke(addContract)
  .expectCommitted()
  .expectState({
    contracts: contains({
      name: "Steel",
      quantity: 12
    })
  })
  .expectRow({
    name: "Steel",
    quantity: "12"
  });
```

The compiler generates:

```text
acceptance obligation
Chromium scenario
canonical-state expectation
surface expectation
receipt expectation
intent-complete proof binding
```

### TL;DR

Write the claimed behavior once. Generate every required proof view.

## 7. Concrete syntax cannot become canonical meaning

Two concrete syntaxes may describe the same operation.

### Object syntax

```javascript
mutation({
  id: "clear-all",
  change: clearContracts
});
```

### Dedicated syntax

```text
intent clear-all mutation:
  set contracts = []
```

Both should compile into the same canonical representation:

```json
{
  "id": "clear-all",
  "operationKind": "mutation",
  "writes": [
    "canonical.contracts"
  ],
  "transition": {
    "kind": "set",
    "path": "contracts",
    "value": []
  }
}
```

Without a canonical application IR, each syntax can develop different defaults, identifiers, or proof behavior.

### TL;DR

Concrete syntax is replaceable. Canonical meaning is not.

## 8. Diagnostics must support AI repair

### Bad diagnostic

```text
TypeError: Cannot read property 'id' of undefined
```

This does not identify the semantic problem or the safe repair.

### Required diagnostic

```text
MCEL_DSL_COLLECTION_KEY_REQUIRED

source:
  inventory.mcel:42:3

path:
  view.inventory.items

problem:
  collection has no stable key

available scalar fields:
  id
  sku
  name

repair:
  add `key: id`
```

Another example:

```text
MCEL_DSL_CANONICAL_WRITE_OUTSIDE_MUTATION

source:
  inventory.mcel:71:5

path:
  intent.preview.change

problem:
  local operation attempts to write canonical state `items`

allowed repairs:
  change the intent kind to `mutation`
  or write renderer-local state instead
```

### TL;DR

Compiler diagnostics are part of the AI programming interface.

## 9. Simple applications must remain simple

Workbench proves semantic completeness. Counter must prove authoring economy.

### Desired Counter source

```javascript
export default app("counter", {
  state: {
    count: canonical.integer(0)
  },

  intents: {
    increment: mutation(({state}) => {
      state.count += 1;
    }),

    reset: mutation(({state}) => {
      state.count = 0;
    }),

    directSet: prohibited()
  },

  view: column(
    text(state.count),
    button("Increment", intents.increment),
    button("Reset", intents.reset)
  ),

  prove: [
    scenario("increment")
      .invoke(intents.increment)
      .expectText("1")
  ]
});
```

The authoring language has failed if Counter still requires:

```text
seven hand-maintained contracts
separate payload declarations
separate DOM bindings
separate browser scripts
manual intent-coverage maps
```

### TL;DR

Workbench proves completeness. Counter proves economy.

## 10. The language must be judged by feature edits

Creating an application once is not enough. The language must make later changes local and safe.

### Example change

Add a `priority` field.

### Poor authoring system

The AI manually updates:

```text
state schema
form state
input node
payload extraction
operation input
canonical model
collection field
acceptance test
browser scenario
proof mapping
```

### Desired authoring system

```javascript
priority: selectField({
  values: ["low", "normal", "high"],
  default: "normal",
  for: addContract.input.priority
})
```

And one display declaration:

```javascript
field(contract.priority)
```

The compiler identifies and regenerates the affected contracts and proof obligations.

### TL;DR

The important measure is the number of coordinated edits required by a feature change.

## What are we trying to build?

An AI should be able to author concepts such as:

```javascript
canonical.list(Contract)
local.string("")
mutation(...)
collection(..., {key: contract.id})
capabilityOperation(...)
scenario(...)
```

MCEL should deterministically generate:

```text
canonical application IR
domain contracts
intent contracts
adapter contracts
surface contracts
layout
acceptance
browser observation
runtime package
intent-complete proof bindings
```

The language should remove repeated plumbing while keeping these decisions explicit:

```text
authority
identity
mutation
capability use
commit timing
cancellation
concurrency
claimed behavior
```

## Governing documentation question

For every repeated declaration, ask:

> Is this repetition mechanical and safe to generate, or does it contain an authority decision that must remain explicit?

That question governs the MCEL AI Authoring Language documentation program.

The settled answer is developed in `pretty_docs/mcel-ai-authoring-semantic-boundary.md`. That specification fixes one official valid-vanilla-JavaScript syntax and applies the boundary concept by concept: what the AI must declare, what MCEL may generate, what the compiler must reject, and what proof must explain. It also requires every consequential side effect to have an owner, evidence, and an explainable final disposition.

`pretty_docs/mcel-application-ir-and-compiler-migration.md` defines where those decisions must converge. Requirements-driven applications, current explicit or normalized application definitions, scaffolded packages, and the future DSL are treated as compiler front ends for one stable MCEL Application IR. `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` then makes that convergence operational at the documentation level: one writer per artifact, candidate outputs staged outside the last proven package, versioned scaffold modes, legacy importers, deterministic projections, feature-level compatibility, evidence-gated promotion, and rollback. Each migration pass must compare all authoring levels, regenerate affected projections, and renew the evidence invalidated by semantic change.

`pretty_docs/mcel-official-vanilla-javascript-dsl.md` now fixes the proposed source form that an AI will actually write: strict CommonJS vanilla JavaScript using `@mcel/app`, one `defineApp` root, stable semantic handles, constrained builder callbacks, static app-local modules, capability lifecycles, semantic surfaces/layouts, and ordered cross-authority proof scenarios. `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md` defines what happens when that source is wrong: stable semantic errors, authored-source locations, safe repair choices, invalidated evidence, preservation of the last proven application, and the exact stage where the AI resumes. `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` defines the authored/generated workspace, candidate staging, legacy importers, compatibility, promotion, and rollback. `pretty_docs/mcel-ai-application-authoring-cycle.md` now defines the complete migration-aware path an AI follows, and `pretty_docs/mcel-ai-authoring-pattern-catalog.md` grounds that path in reusable code-level examples. The semantic change and evidence-impact model is specified in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`. `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` now fixes the controlled task corpus, hard semantic gates, repeated-session protocol, migration preservation checks, repair cases, evidence metrics, and economy thresholds. `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` now completes the final cross-document review and identifies the bounded IR-kernel implementation as the next permissible step after explicit authorization.

## Documentation completeness result

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` audits the complete source, IR, expression, effect, DSL, diagnostics, migration, authoring-cycle, change-impact, and benchmark chain. It concludes that the next permissible code step, after explicit authorization, is the bounded MCEL Application IR kernel—not an immediate app migration or DSL-v1 claim.

### TL;DR

The problem and target are now documented; implementation must begin from the stable IR outward.
