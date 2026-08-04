# MCEL AI Authoring Language
## Semantic Authoring Boundary

This document defines what the official MCEL AI-authoring language must require an author to say, what the compiler may generate, and what the compiler must reject. `pretty_docs/mcel-ai-application-authoring-cycle.md` applies this boundary stage by stage, and `pretty_docs/mcel-ai-authoring-pattern-catalog.md` demonstrates it through recurring code-level tasks.

It is a design specification, not an implemented API. Code examples show the intended authoring level and may change when the canonical IR is specified.

## Fixed decisions

The authoring-language program currently has these fixed decisions:

1. The official authoring language is valid vanilla JavaScript.
2. MCEL has one official authoring syntax, not several competing syntaxes.
3. The stable compilation target is a source-independent MCEL Application IR. The current explicit or normalized `application.js` forms are migration inputs and provisional low-level projections, not the final semantic base. See `pretty_docs/mcel-application-ir-and-compiler-migration.md`.
4. Proof must account for every consequential side effect. A successful final screen is not enough when the operation also started, cancelled, superseded, rejected, or committed work.
5. The governing authoring rule is:

> Require one declaration for each independent semantic decision. Generate mechanical repetition.

### TL;DR

The DSL is JavaScript, but it is not unrestricted application code. It is one constrained language for constructing a complete MCEL application meaning.

## Boundary test

For every proposed shortcut, ask four questions:

1. Does this choice change authority, identity, behavior, lifecycle, or a claimed outcome?
2. Could two reasonable applications make different choices here?
3. Would hiding the choice make a consequential side effect harder to explain?
4. Can the compiler derive the answer without guessing application meaning?

If the answer to the first three questions is yes, the AI must normally declare the choice. If the fourth answer is yes and the derivation is mechanical, the compiler may generate it.

## Boundary summary

| Concept | AI must declare | Compiler may infer or generate | Compiler must reject |
| --- | --- | --- | --- |
| State | Authority, schema, initial meaning | Storage plumbing, subscriptions, revision wiring | State with ambiguous authority |
| Input | Semantic field and value source | Local draft state, control binding, parsing | Input with no unambiguous source |
| Derived state | Dependencies and derivation meaning | Dependency ordering and recomputation wiring | Hidden dependencies or nondeterminism |
| Mutation | Input, canonical change, refusal meaning | Read/write extraction when structurally provable | Undeclared or out-of-authority writes |
| Collection | Source and stable item identity | Current-row key payload plumbing | Position used as semantic identity |
| Capability work | Capability, operation identity, lifecycle policy, commit rule | Abort plumbing, active-operation registry, cleanup | Unclassified external work or missing lifecycle identity |
| Surface | Semantic structure and meaningful presentation choices | Node IDs, ordinary bindings, generated controls | Surface behavior with no semantic owner |
| Scenario | Stimulus and claimed outcome | Acceptance/browser adapters and coverage links | Self-confirming or consequence-incomplete proof |
| Identifier | Stable semantic identity | Mechanical descendant IDs | Identity inferred from mutable labels |
| Side effect | Effect class, authority, expected disposition | Evidence recording and reconciliation plumbing | Consequential effect with no declared explanation path |

## 1. State authority

### Application idea

```text
Inventory items are shared application truth.
Search text belongs only to one mounted interface.
The visible item list is computed from both.
```

### AI must declare

```javascript
const items = state.canonical.list("items", Item, []);
const search = state.local.text("search", "");

const visibleItems = state.derived.list("visible-items", {
  from: [items, search],
  query: items.where(textSearch(search, [Item.name, Item.sku]))
});
```

The important decisions are:

```text
items are canonical
search is renderer-local
visibleItems is derived
visibleItems depends on items and search
```

### Compiler may generate

```text
renderer-local storage
canonical subscriptions
revision checks
recomputation ordering
surface refresh wiring
multi-instance isolation plumbing
```

### Compiler must reject

```javascript
const search = state.text("search", "");
```

when `state.text` does not identify whether the value is canonical, local, provisional, URL-owned, or otherwise governed.

It must also reject a derived value that writes canonical state:

```javascript
state.derived("visible-items", {
  from: [items, search],
  compute() {
    items.clear();
    return [];
  }
});
```

### Proof must explain

```text
which authority owned each value
which mounted instance changed local state
whether canonical state changed
which dependencies caused recomputation
what became visible afterward
```

### TL;DR

The compiler may generate state machinery. It may not guess who owns the state.

## 2. Models and schemas

### Application idea

```text
An item has a stable ID, name, quantity, and priority.
```

### AI must declare

```javascript
const Item = model("item", {
  id: field.id(),
  name: field.text().minLength(1),
  quantity: field.integer().minimum(1),
  priority: field.enum("low", "normal", "high").default("normal")
});
```

### Compiler may generate

```text
runtime validators
input parsers
canonical-state validation
surface formatting defaults
acceptance value generators
serialized schema in the canonical IR
```

### Compiler must reject

A model field whose meaning changes depending on runtime JavaScript type coercion:

```javascript
quantity: field.any()
```

when the field participates in canonical mutation, sorting, arithmetic, or proof.

It must also reject conflicting declarations:

```javascript
quantity: field.integer().minimum(1)
```

paired with a control that supplies an arbitrary object.

### Proof must explain

```text
which schema accepted or rejected the value
which normalization occurred
whether the canonical value matches the declared type
whether the visible representation corresponds to that value
```

### TL;DR

Declare semantic data once. Generate its validators and parsers, but do not erase type meaning.

## 3. Intent input and value sources

### Application idea

```text
Add Item receives a name and quantity from controls.
The current user ID comes from governed context.
```

A schema alone does not say where values come from.

### AI must declare

```javascript
const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(field.text().minLength(1)),
    quantity: input.control(field.integer().minimum(1)),
    createdBy: input.context(currentUser.id)
  },

  change: items.append({
    id: id.next("item"),
    name: value.name,
    quantity: value.quantity,
    createdBy: value.createdBy
  })
});
```

### Compiler may generate

For `input.control(...)`:

```text
renderer-local draft state
control value binding
payload extraction
parsing
validation display
browser locator
acceptance input binding
```

For `input.context(...)`:

```text
context lookup
payload placement
context-availability preflight
provenance recording
```

### Compiler must reject

```javascript
input: {
  createdBy: field.id()
}
```

when the source is unspecified.

It must also reject two simultaneous sources for one field unless the language explicitly defines precedence:

```javascript
createdBy: input.from(
  input.control(field.id()),
  input.context(currentUser.id)
)
```

### Proof must explain

```text
where each consequential input value came from
which normalization was applied
which validation accepted or refused it
which input values entered the canonical transition
```

### TL;DR

A field schema says what a value is. An input binding says where it comes from. MCEL needs both.

## 4. Derived state and queries

### Application idea

```text
Filter items by search text and sort them by name.
```

### Weak shape

```javascript
const visibleItems = state.derived(() =>
  items.filter(item => item.name.includes(search)).sort(compareAnything)
);
```

This is valid JavaScript, but it hides dependencies and permits arbitrary behavior.

### AI should declare

```javascript
const visibleItems = state.derived.list("visible-items", {
  from: [items, search],
  query: items
    .where(textSearch(search, [Item.name, Item.sku]))
    .orderBy(ascending(Item.name), ascending(Item.id))
});
```

### Compiler may generate

```text
dependency graph
cycle checks
stable recomputation order
query evaluation
collection refresh
proof-readable query description
```

### Compiler must reject

```javascript
query: () => {
  fetch("/items");
  return Math.random() > 0.5 ? items : [];
}
```

It must also reject undeclared dependencies:

```javascript
from: [items],
query: items.where(textSearch(search, [Item.name]))
```

unless `search` can be structurally extracted and added deterministically.

### Proof must explain

```text
which source values were read
which query rules were applied
which order was produced
why an item was included or excluded
what collection the browser displayed
```

### TL;DR

Derived state should be an inspectable semantic query, not an opaque callback.

## 5. Canonical mutations

### Application idea

```text
Add one item and advance the revision.
```

### AI must declare

```javascript
const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(Item.name.required()),
    quantity: input.control(Item.quantity)
  },

  change: transaction(
    items.append({
      id: id.next("item"),
      name: value.name,
      quantity: value.quantity,
      priority: "normal"
    }),
    revision.increment()
  )
});
```

This declaration structurally reveals the canonical writes.

### Compiler may generate

```text
read set
write set
SCM dispatch contract
revision protection
rollback-safe transaction envelope
operation receipt
low-level transition function
ordinary success postconditions implied by the expression
```

### Compiler must reject

A mutation that claims one write set but performs another:

```javascript
intent.mutation("add-item", {
  writes: [items],
  change: transaction(items.append(newItem), preferences.clear())
});
```

It must reject canonical writes from a non-mutation intent:

```javascript
intent.local("preview-item", {
  change: items.append(newItem)
});
```

### Proof must explain

```text
pre-mutation canonical revision
input values
refusal or authorization result
all canonical paths written
post-mutation revision
operation receipt
visible consequence
```

### TL;DR

Keep the canonical change explicit. Generate the transaction machinery around it.

## 6. Refusals, invariants, and postconditions

### Application idea

```text
An item name is required.
Item IDs stay unique.
A successful Add increases item count by one.
```

These are related but different claims.

### AI must declare independent rules

```javascript
const Item = model("item", {
  id: field.id(),
  name: field.text().minLength(1)
});

invariant("item-ids-unique", items.uniqueBy(Item.id));

const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(Item.name)
  },

  refuse: [
    when(value.name.trim().equals(""), "ITEM_NAME_REQUIRED")
  ],

  change: items.append({
    id: id.next("item"),
    name: value.name
  }),

  ensures: [
    items.count().increasedBy(1)
  ]
});
```

### Compiler may generate

```text
schema-derived preflight checks
stable refusal payload shape
invariant execution points
standard append postconditions
acceptance branches for declared refusals
```

### Compiler must reject

A refusal that mutates state:

```javascript
refuse: when(nameMissing, items.clear())
```

An invariant that depends on an external capability:

```javascript
invariant("remote-valid", quoteService.check(items))
```

A postcondition that cannot be connected to the operation's before/after state.

### Proof must explain

```text
which condition refused the operation
whether canonical state remained unchanged on refusal
which invariants held before and after commit
which postconditions were independently checked
```

### TL;DR

Validation, refusal, invariant, and postcondition are different semantic decisions. Do not collapse them into one vague “valid” flag.

## 7. Collections and stable identity

### Application idea

```text
Render filtered items. Remove the item whose row button was clicked.
```

### AI must declare

```javascript
view.collection(visibleItems, {
  id: "inventory.items",
  key: Item.id,

  row(item) {
    return view.row(
      view.text(item.name),
      view.text(item.quantity),
      view.action(removeItem, {
        itemId: item.id
      })
    );
  }
});
```

The function-like row form may be syntax sugar, but `item` must remain a constrained collection-item reference rather than an unrestricted runtime object.

### Compiler may generate

```text
keyed reconciliation
current-item context
item-key payload extraction
stable row IDs
row-level locator bindings
preservation of focus and provisional state by key
```

When the intent has exactly one key-compatible input, the official syntax may eventually allow:

```javascript
view.action(removeItem)
```

but only after the collection key and intent input establish one unambiguous mapping.

### Compiler must reject

```javascript
view.collection(visibleItems, {
  key: item.position
});
```

when position changes under filtering or sorting.

It must reject a row action whose key type does not match the intent input.

### Proof must explain

```text
which stable item key owned the row
which row emitted the operation
which canonical item changed
whether sorting or filtering preserved identity
which row remained visible afterward
```

### TL;DR

Position is presentation. Key is identity. Only key plumbing is mechanical.

## 8. Capabilities and asynchronous lifecycles

### Application idea

```text
Request a quote for one item.
Show progress.
Only the latest request for that item may commit.
Allow cancellation.
```

### AI must declare

```javascript
const quoteService = capability("quote-service", {
  risk: "external-read-stream",
  request: {
    input: schema.object({
      itemId: schema.id(),
      quantity: schema.integer().minimum(1)
    }),
    output: schema.object({
      amount: schema.integer().minimum(0)
    }),
    stream: true,
    cancellable: true
  }
});

const requestQuote = intent.capability("request-quote", {
  input: {
    itemId: input.itemKey(items)
  },

  use: quoteService.request,
  operationKey: value.itemId,
  provisional: quoteProgress.by(value.itemId),
  concurrency: concurrency.latestPerKey(),
  cancellableBy: cancelQuote,

  receive: quoteProgress.reduce(quoteEvent),

  commit: items
    .byKey(value.itemId)
    .set({
      quoteAmount: result.amount,
      quoteStatus: "quoted"
    })
});
```

### Compiler may generate

```text
AbortSignal plumbing
active-operation registry
operation tokens
late-event suppression
provisional initialization
standard cancellation receipt
provisional cleanup
capability adapter invocation
```

### Compiler must reject

```javascript
intent.capability("request-quote", {
  use: quoteService.request,
  commit: applyQuote
});
```

when operation identity, provisional ownership, concurrency, or cancellation behavior is required but unspecified.

It must reject a capability result that writes canonical state outside SCM reconciliation.

### Proof must explain

```text
which request began
which operation key owned it
which progress events were accepted
which events were ignored and why
whether another request superseded it
whether cancellation reached the capability
which result was eligible to commit
which canonical write occurred
which provisional state was removed
```

### TL;DR

An async intent is a lifecycle, not a function call.

## 9. Consequential side effects

A consequential side effect is any effect whose occurrence, nonoccurrence, ordering, failure, or cleanup can change canonical truth, visible behavior, external systems, recovery, or a later operation.

Examples include:

```text
canonical writes
provisional writes
renderer-local changes that affect behavior
capability requests
file, network, Git, shell, publish, or message actions
cancellation
supersession
late-event rejection
resource acquisition and release
receipts and durable audit records
```

### AI must declare the semantic effect

```javascript
const publishSite = intent.capability("publish-site", {
  use: publisher.publish,
  risk: "external-write",
  operationKey: value.siteId,
  commit: deployments.record(result.deploymentId),
  recovery: publisher.rollback(result.deploymentId)
});
```

The declaration need not repeat every transport detail. It must reveal that the operation performs an external write, what identifies it, what canonical fact records completion, and what recovery relationship exists.

### Compiler may generate an effect ledger

Conceptually:

```json
{
  "effectId": "publish-site/external-write",
  "operationId": "publish-site",
  "authority": "publisher.publish",
  "risk": "external-write",
  "expectedDispositions": [
    "committed",
    "refused",
    "failed",
    "cancelled",
    "recovered"
  ],
  "evidenceRequired": [
    "request",
    "result-or-failure",
    "canonical-reconciliation",
    "visible-outcome"
  ]
}
```

### Compiler and proof must reject unexplained effects

An operation must not pass proof when an observed effect has no declared explanation path:

```text
an undeclared network request occurred
canonical state changed outside the operation write set
an external write succeeded but no canonical receipt exists
an operation was cancelled but provisional state remained
an older superseded result committed
an acquired resource was never released or deliberately retained
```

### Proof must reconcile each effect

Each declared effect should end in one explainable disposition:

```text
not attempted
refused before attempt
started
progressing
committed
failed
cancelled
superseded
ignored as late
recovered
intentionally retained under a declared rule
```

“Nothing visibly broke” is not a disposition.

### TL;DR

Every consequential effect needs an owner, evidence, and an explainable final disposition.

## 10. Surface declarations and bindings

### Application idea

```text
Show the Add form, search control, item list, and row actions.
```

### AI must declare semantic structure

```javascript
view.page("inventory.main",
  view.form(addItem, {
    fields: {
      name: view.textInput({label: "Item name"}),
      quantity: view.numberInput({label: "Quantity", step: 1})
    }
  }),

  view.input(search, {
    label: "Search inventory"
  }),

  view.collection(visibleItems, {
    key: Item.id,
    row: item => view.row(
      view.text(item.name),
      view.text(item.quantity),
      view.action(removeItem, {itemId: item.id})
    )
  })
);
```

### Compiler may generate

```text
stable descendant node IDs
local-state input binding
property projection
conditional projection
payload extraction
ordinary labels from semantic field names when allowed
browser locators
surface-contract nodes
```

### Compiler must reject

A control with no semantic owner:

```javascript
view.button("Do it", () => mutateAnything())
```

A surface binding that changes canonical state directly:

```javascript
view.textInput({onInput: items.replaceAll})
```

A generated node ID used as semantic identity when the underlying semantic owner has no stable ID.

### Proof must explain

```text
which semantic state produced each consequential property
which control invoked which intent
which payload was extracted
which visible result followed the committed or refused operation
```

### TL;DR

Declare the semantic surface. Generate ordinary DOM and binding machinery.

## 11. Proof scenarios and claimed outcomes

### Application idea

```text
Adding Steel commits one canonical item and shows one Steel row.
```

### AI must declare the claim

```javascript
prove.scenario("add Steel")
  .enter(addItem, {
    name: "Steel",
    quantity: 12
  })
  .invoke(addItem)
  .expectCommitted()
  .expectCanonical(items, contains({
    name: "Steel",
    quantity: 12
  }))
  .expectVisibleRow("inventory.items", {
    name: "Steel",
    quantity: "12"
  });
```

### Compiler may generate

```text
package-local acceptance adapter
browser interaction script
state assertion adapter
receipt assertion
scenario-to-intent coverage map
stable scenario ID
proof-report binding
```

### Compiler must reject self-confirming proof

This is insufficient:

```javascript
scenario("add Steel")
  .invoke(addItem)
  .expect(addItem.returnedSuccess());
```

The implementation cannot be its only witness.

It must also reject a scenario that claims async completion but omits consequential lifecycle outcomes:

```javascript
scenario("quote")
  .invoke(requestQuote)
  .expectText("$42");
```

when cancellation, provisional cleanup, supersession, or canonical reconciliation are relevant to the declared behavior.

### Cross-cutting scenarios remain top-level

```javascript
prove.scenario("parallel quote requests remain independent")
  .invoke(requestQuote, {itemId: "item-a"})
  .invoke(requestQuote, {itemId: "item-b"})
  .expectProvisional("item-a")
  .expectProvisional("item-b")
  .complete("item-b")
  .expectCanonicalQuote("item-b")
  .expectStillPending("item-a");
```

### Proof must explain

```text
stimulus
operation disposition
canonical consequence
noncanonical lifecycle consequence
visible consequence
absence of forbidden or unexpected effects
```

### TL;DR

Generate proof machinery, not proof meaning. The author must state the claimed outcome.

## 12. Stable identifiers and labels

### Application idea

```text
The add-item operation is shown as “Create Item.”
```

### AI must declare stable semantic identity

```javascript
const addItem = intent.mutation("add-item", {
  label: "Add Item",
  change: items.append(newItem)
});

view.action(addItem, {
  label: "Create Item"
});
```

### Compiler may generate

```text
surface node IDs derived from semantic paths
scenario IDs derived from explicit scenario identities
payload-binding IDs
proof-obligation IDs
```

Generated IDs must be deterministic and visible in normalized output.

### Compiler must reject

Inferring semantic identity from mutable presentation text:

```javascript
view.button("Create Item")
```

when the label is the only operation identity.

It should also diagnose a rename that would accidentally create a second semantic entity rather than migrate the existing one.

### Proof must explain

```text
which stable intent, state path, item key, and scenario produced the evidence
```

### TL;DR

Labels may change. Semantic identity must survive the change.

## 13. Vanilla JavaScript without unrestricted behavior

The official DSL must be valid vanilla JavaScript. This gives MCEL ordinary files, modules, formatting, editors, source maps, and JavaScript tooling.

It does not authorize arbitrary execution inside semantic declarations.

### Acceptable shape

```javascript
change: items.append({
  id: id.next("item"),
  name: value.name
})
```

Each helper constructs an inspectable MCEL expression. The complete type, scope, purity, operator, normalization, static-analysis, and migration rules are specified in `pretty_docs/mcel-constrained-expression-model.md`.

Conceptually, the compiler receives:

```json
{
  "kind": "list.append",
  "target": "state.items",
  "value": {
    "id": {
      "kind": "id.next",
      "namespace": "item"
    },
    "name": {
      "kind": "input.field",
      "path": "name"
    }
  }
}
```

### Compiler must reject or isolate unrestricted behavior

```javascript
change() {
  const id = Date.now();
  fetch("https://example.com/side-effect");
  return {...state, id};
}
```

Problems:

```text
nondeterministic ID
undeclared network effect
opaque read/write set
uninspectable transition
unprovable side effect
```

If MCEL eventually permits bounded custom JavaScript functions, the function class, inputs, outputs, authority, determinism requirements, and proof obligations must be specified separately. Arbitrary functions are not the default escape hatch.

### TL;DR

Vanilla JavaScript is the source format. MCEL expressions remain the semantic language.

## 14. Official syntax, canonical IR, and low-level target

MCEL will have one official AI-facing syntax. That syntax may combine several JavaScript forms without becoming several languages:

```text
structured declarations for applications, state, intents, capabilities, and views
expression builders for transitions, queries, validation, and bindings
fluent chains for ordered proof scenarios
```

The intended pipeline is:

```text
official vanilla-JavaScript DSL
→ canonical MCEL application IR
→ explicit low-level application definition
→ generated application package
→ runtime, acceptance, browser observation, and proof
```

The current Workbench `application.js` is the closest live high-level compiler-front-end prototype because it already exposes:

```text
schemas
state authorities
operations
capabilities
surface
layout
acceptance
observation
proof relationships
```

It currently normalizes into an explicit JSON model and seven generated package contracts. It is therefore migration evidence, not the stable base. `pretty_docs/mcel-application-ir-and-compiler-migration.md` requires the final source-independent IR to sit between every front end and the replaceable projections. The exact future role of `application.js` may be:

```text
the official DSL source for migrated apps
a legacy high-level compatibility source
a generated explicit inspection artifact
or a path retired after IR-backed projection is proven
```

The explicit package contracts remain current executable projections during migration.

### Required equivalence test

For Workbench, the official DSL must eventually preserve:

```text
7 declared intents
14 declared browser scenarios
all state authorities
all capability lifecycles
all cancellation and concurrency policy
all generated contracts
intent-complete proof
semantic-runtime-proven truth
```

Equivalent meaning is established through canonical IR and proof, not by similar-looking source.

### TL;DR

There is one authoring language, one canonical meaning, and one explicit generation path.

## 15. AI-repairable diagnostics

Diagnostics are part of the official authoring interface.

### Required diagnostic shape

```text
MCEL_DSL_COLLECTION_KEY_REQUIRED

source:
  inventory.application.js:74:3

semantic path:
  view.inventory.items

problem:
  collection has no stable item key

available compatible fields:
  id
  sku

repair:
  add `key: Item.id`
```

Another example:

```text
MCEL_DSL_UNEXPLAINED_EFFECT

source:
  inventory.application.js:118:5

semantic path:
  intents.publish-site

problem:
  observed external write `publisher.publish` has no declared
  reconciliation or recovery rule

required decision:
  declare the canonical completion record and one of:
  `recovery: ...`
  `irreversible: true`
```

### Compiler must avoid

```text
TypeError: Cannot read properties of undefined
invalid app
schema mismatch
proof failed
```

without a semantic path and actionable explanation.

### Diagnostics should distinguish

```text
syntax failure
invalid semantic combination
missing independent decision
ambiguous inference
unsupported expression
unexplained side effect
proof-coverage gap
generated-artifact drift
```

### TL;DR

An AI should know what decision is missing and how to repair it without reverse-engineering the compiler.

## 16. Feature-edit test

The language must be judged by later edits, not only initial application creation.

### Change request

```text
Add optional priority to items and display it in each row.
```

### Independent semantic decisions

The AI may correctly need to make three edits:

```javascript
// Model decision
priority: field.enum("low", "normal", "high").default("normal")
```

```javascript
// Operation-input and canonical-write decision
priority: input.control(Item.priority)
```

```javascript
// Presentation decision
view.text(item.priority)
```

### Mechanical edits that must be generated

```text
local draft state
select-control options
payload extraction
parsing and validation
canonical schema projection
acceptance input adapter
browser locator
proof coverage mapping
generated contract updates
```

One edit would be too few if it hides independent model, behavior, and presentation choices. Ten edits would be too many if seven are only plumbing.

### TL;DR

The target is one declaration per independent decision, not one declaration per feature at any cost.

## 17. Decision checklist for every DSL construct

A proposed construct is acceptable only when its documentation answers:

1. What independent semantic decision does the construct express?
2. Which authority owns the result?
3. Which stable identities does it create or reference?
4. Which inputs does it read?
5. Which state or external systems may it affect?
6. Which mechanical structures does the compiler generate?
7. Which invalid combinations are rejected?
8. Which consequential side effects can occur?
9. How is each effect evidenced and reconciled?
10. Which acceptance and browser claims become required?
11. What deterministic canonical IR does it produce?
12. What diagnostic tells an AI how to repair misuse?

A construct that cannot answer these questions is not ready for the official DSL.

## Documentation consequences

This boundary determines the next documents. The compiler and migration architecture is specified in `pretty_docs/mcel-application-ir-and-compiler-migration.md`. The concrete IR schema and normalization rules are now specified in `pretty_docs/mcel-application-ir-schema-and-normalization.md`, and the current application-definition preservation ledger is `pretty_docs/mcel-existing-application-definition-migration-inventory.md`. The remaining specifications are:

1. **Constrained expression specification** — completed in `pretty_docs/mcel-constrained-expression-model.md`; it defines transitions, queries, validation, bindings, lifecycle reconciliation, domain operators, and opaque-callback migration without unrestricted execution.
2. **Consequential side-effect and proof specification** — completed in `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`; it defines effect ownership, instances, evidence classes, terminal dispositions, cleanup and residue, uncertainty, recovery, and proof completeness.
3. **Official vanilla-JavaScript syntax specification** — completed in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`; it fixes the strict CommonJS source form, `@mcel/app` import, root and module declarations, stable IDs, semantic handles, constrained callbacks, surfaces, layouts, effects, and ordered scenarios that map into the IR.
4. **Compiler diagnostic and repair specification** — completed in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`; it defines stable codes and keys, source provenance, semantic paths, safe repair classes, dependency ordering, candidate truth, evidence invalidation, narrow reruns, and authoring-cycle re-entry.
5. **Scaffolder, projection, and compatibility details** — specified in `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`, including exact ownership, candidate staging, importers, projections, compatibility reports, promotion, and rollback.
6. **AI application authoring cycle and pattern catalog** — completed in `pretty_docs/mcel-ai-application-authoring-cycle.md` and `pretty_docs/mcel-ai-authoring-pattern-catalog.md`; they define stage transitions, re-entry, and complete task-oriented examples.
7. **Semantic change and impact model** — completed in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`; it defines semantic deltas, typed dependency closure, earliest-stage re-entry, projection invalidation, evidence renewal, audited reuse, and conservative fallback.
8. **AI-authoring and migration benchmark** — completed in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`; it defines controlled creation, migration, modification, repair, proof, reliability, and economy trials with hard semantic gates before efficiency scoring.
9. **Documentation completeness review** — completed in `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md`; it confirms cross-document consistency, closes the initial v1 escape hatches, and defines the bounded first implementation scope.

No compiler or DSL implementation is authorized by this document.

## Final rule

> The AI declares every independent decision that changes authority, identity, behavior, lifecycle, effect, or claimed outcome. MCEL generates the repeated machinery required to execute, observe, and prove that decision.

## Documentation completeness review

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` verifies that the later IR, expression, effect, DSL, diagnostics, compatibility, authoring-cycle, change-impact, and benchmark specifications preserve this boundary. It also closes the initial v1 escape hatches: no user-defined expression macros, no unknown effect kinds, no implicit locale semantics, and no undocumented canonical rewrites.

### TL;DR

The semantic boundary is now the implementation gate, not merely design guidance.
