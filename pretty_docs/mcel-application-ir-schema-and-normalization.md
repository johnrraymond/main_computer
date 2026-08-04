# MCEL Application IR Schema and Normalization Rules

## Status

Wave 1 implements the structural IR validator and normalizer. Wave 2A now integrates `main_computer/mcel_constrained_expression.py` into IR validation so expression roots are checked for legal context, reference kind, operand/result compatibility, canonical write authority, and deterministic normalization without changing the IR semantic or source-binding fingerprints.

This document specifies the stable semantic representation targeted by current and future MCEL application compilers.

The bounded Wave 1 structural kernel is now implemented in `main_computer/mcel_application_ir.py`, with the versioned JSON Schema in `main_computer/schemas/mcel.application-ir.v1.schema.json`, the read-only validator/normalizer command in `tools/mcel_application_ir.py`, and the repository-bound Counter fixture in `tests/fixtures/mcel_application_ir/contract-counter.ir.json`.

That implementation covers structural records, stable semantic IDs, reference resolution, deterministic normalization, semantic and source-binding fingerprints, canonical diagnostic records, basic write-set authority checking, and Counter fixture validation. It does **not** implement the official DSL, generated runtime projections, application promotion, evidence reuse, legacy-compiler retirement, or complete expression/effect static analysis.

The companion migration inventory is `pretty_docs/mcel-existing-application-definition-migration-inventory.md`. The effect lifecycle, evidence, cleanup, recovery, and proof-completeness rules are specified in `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`. The one official source notation that constructs this IR is specified in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`. Stable semantic failures, authored-source repair paths, invalidated evidence, and stage-aware re-entry are specified in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`. `pretty_docs/mcel-ai-application-authoring-cycle.md` defines the stages that create and validate this IR, while `pretty_docs/mcel-ai-authoring-pattern-catalog.md` provides the recurring task slices the IR must represent.

## Wave 1 kernel command

Validate and normalize a candidate IR without changing any application package:

```powershell
python tools/mcel_application_ir.py `
  --input tests/fixtures/mcel_application_ir/contract-counter.ir.json `
  --write-normalized runtime/state/mcel/ir-kernel/contract-counter.normalized.json
```

The command exits nonzero on blocking diagnostics. `--json` emits `mcel.application-ir-validation-report.v1` with canonical `mcel.compiler-diagnostic.v1` records. The output path is caller-selected and is not a promotion boundary.

### TL;DR

Wave 1 can measure, normalize, and fingerprint candidate meaning; it cannot yet compile or promote an application.

## The short answer

Every MCEL application front end must eventually produce one canonical object:

```text
requirements-driven definition ─┐
scaffolded explicit package ─────┤
normalized application.js ───────┼─> mcel.application-ir.v1
future official vanilla-JS DSL ──┘
```

The IR carries the application meaning that must survive compiler replacement:

```text
identity
models and schemas
state authority
intents and refusals
constrained transitions and derivations
capabilities and lifecycles
surface and layout semantics
consequential effects
scenarios and proof claims
source provenance
migration bindings
```

### TL;DR

Compilers may change. The normalized application meaning must remain stable and comparable.

## 1. Top-level shape

The canonical document has this shape:

```json
{
  "schema": "mcel.application-ir.v1",
  "application": {},
  "models": [],
  "states": [],
  "derivations": [],
  "intents": [],
  "capabilities": [],
  "effects": [],
  "surfaces": [],
  "layouts": [],
  "scenarios": [],
  "proof": {},
  "migration": {},
  "provenance": {},
  "normalization": {},
  "fingerprints": {}
}
```

All semantic collections are arrays of records with stable IDs. References use explicit reference objects:

```json
{"ref": "state:contracts"}
```

A plain string such as `"state:contracts"` remains application data unless the schema says the field is an ID field. This prevents accidental string-path interpretation.

### TL;DR

The IR is a typed semantic graph serialized as deterministic JSON.

## 2. Common semantic-node envelope

Every meaningful node uses the same minimum envelope:

```json
{
  "id": "intent:add-contract",
  "kind": "intent",
  "sourceName": "addContract",
  "source": {
    "frontend": "mcel.dsl.v1",
    "file": "mcel_apps/inventory/application.js",
    "start": {"line": 42, "column": 3},
    "end": {"line": 56, "column": 4}
  }
}
```

Required fields:

| Field | Meaning |
| --- | --- |
| `id` | Stable semantic identity used by references and equivalence checks |
| `kind` | Closed IR node category |
| `sourceName` | Human-readable authored name when one exists |
| `source` | Trace to the source front end and authored location |

Generated subordinate nodes must identify their semantic parent and role:

```json
{
  "id": "binding:add-contract.quantity.control",
  "kind": "input-binding",
  "generatedFrom": {"ref": "intent:add-contract"},
  "role": "input.quantity.control"
}
```

Generated IDs must not depend on display labels, array positions, timestamps, random values, or filesystem traversal order.

### TL;DR

Labels can change. Semantic IDs and parent-role derivation must remain stable.

## 3. Application record

```json
{
  "id": "app:contract-workbench",
  "kind": "application",
  "appId": "contract-workbench",
  "title": "Contract Operations Workbench",
  "semanticVersion": "1",
  "authoringStatus": "dual-authored",
  "targetTruthStatus": "semantic-runtime-proven"
}
```

The application record identifies the semantic application, not a package directory or browser route.

Package roots, generated paths, routes, and projection fingerprints belong to projection or evidence records rather than application identity.

### TL;DR

Application identity must survive file moves and projection-layout changes.

## 4. Models and schemas

A model defines stable domain structure:

```json
{
  "id": "model:Contract",
  "kind": "model",
  "fields": [
    {
      "id": "field:Contract.id",
      "name": "id",
      "schema": {"kind": "text", "required": true},
      "identityRole": "stable-key"
    },
    {
      "id": "field:Contract.quantity",
      "name": "quantity",
      "schema": {"kind": "integer", "minimum": 1, "required": true}
    }
  ]
}
```

The IR must distinguish:

```text
schema meaning
initial/default value
domain identity
validation/refusal behavior
presentation labels
```

A field label is not its identity. A default is not a validation rule. A collection key is not inferred from the first field.

### TL;DR

The model records domain meaning; presentation and input plumbing are separate projections.

## 5. State authority

Every state record declares authority explicitly:

```json
{
  "id": "state:contracts",
  "kind": "state",
  "authority": "canonical",
  "schema": {
    "kind": "list",
    "item": {"ref": "model:Contract"}
  },
  "initial": []
}
```

Allowed v1 authorities:

```text
canonical
renderer-local
provisional
derived
```

Derived state additionally names dependencies and a constrained expression:

```json
{
  "id": "state:visible-contracts",
  "kind": "state",
  "authority": "derived",
  "dependsOn": [
    {"ref": "state:contracts"},
    {"ref": "state:search"}
  ],
  "derive": {
    "kind": "query.filter-text",
    "source": {"ref": "state:contracts"},
    "query": {"kind": "state.read", "state": {"ref": "state:search"}},
    "fields": [
      {"ref": "field:Contract.name"},
      {"ref": "field:Contract.category"}
    ]
  }
}
```

The compiler must reject authority-free state declarations.

### TL;DR

The DSL may shorten authority syntax. The IR may never omit authority.

## 6. Intent record

A mutation intent has this minimum form:

```json
{
  "id": "intent:add-contract",
  "kind": "intent",
  "operationKind": "mutation",
  "risk": "local-state",
  "input": [],
  "reads": [],
  "writes": [],
  "refusals": [],
  "transition": {},
  "invariants": [],
  "effectRefs": [],
  "outcomes": []
}
```

A prohibited intent remains a first-class node:

```json
{
  "id": "intent:direct-set",
  "kind": "intent",
  "operationKind": "prohibited",
  "risk": "prohibited",
  "reasonCode": "MCEL_CANONICAL_ASSIGNMENT_BYPASSES_OPERATION_AUTHORITY"
}
```

Reads and writes are normalized from constrained expressions and checked against any authored declarations. A mismatch is a compiler error, not a warning.

### TL;DR

The IR must expose what an intent receives, reads, changes, refuses, causes, and claims.

## 7. Input fields and value sources

Intent schemas and value sources are different facts:

```json
{
  "id": "input:add-contract.quantity",
  "kind": "intent-input",
  "name": "quantity",
  "schema": {"kind": "integer", "minimum": 1},
  "source": {
    "kind": "control-property",
    "control": {"ref": "surface-node:draft-quantity"},
    "property": "value",
    "parse": "integer"
  }
}
```

Other allowed sources include:

```text
item-key
application-context
authenticated-context
constant
prior-operation-output
capability-event
```

The compiler may generate control storage and parsing after the semantic binding is known. It must reject ambiguous or missing sources when more than one source is possible.

### TL;DR

Schema answers “what value is valid.” Binding answers “where the value comes from.”

## 8. Constrained expression nodes

Core application behavior must normalize into inspectable expression nodes rather than opaque function hashes. The complete context, type, purity, normalization, operator-extension, migration, and static-analysis rules are specified in `pretty_docs/mcel-constrained-expression-model.md`.

Minimum v1 expression families:

```text
constant
state.read
input.read
context.read
record.construct
record.get
record.set
list.append
list.remove-by-key
list.update-by-key
number.add
number.compare
text.normalize
text.compare
boolean.and
boolean.or
conditional
query.filter
query.sort
refusal
sequence
```

Example:

```json
{
  "kind": "list.append",
  "target": {"ref": "state:contracts"},
  "value": {
    "kind": "record.construct",
    "model": {"ref": "model:Contract"},
    "fields": {
      "id": {"kind": "id.next", "namespace": "contract"},
      "name": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}},
      "quantity": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}}
    }
  }
}
```

Vanilla JavaScript is the official future source language, but source callbacks are not automatically acceptable semantic expressions. The DSL must construct this constrained vocabulary.

### TL;DR

Valid JavaScript is the source format; constrained MCEL expressions are the executable meaning.

## 9. Capabilities and asynchronous lifecycle

A capability declares external authority independently of the intent that uses it:

```json
{
  "id": "capability:quotes.request",
  "kind": "capability",
  "authority": "external-read-stream",
  "requestSchema": {"ref": "schema:quote-request"},
  "eventSchema": {"ref": "schema:quote-progress-event"},
  "resultSchema": {"ref": "schema:quote-result"}
}
```

An asynchronous intent must declare lifecycle policy:

```json
{
  "id": "lifecycle:request-quote",
  "kind": "operation-lifecycle",
  "intent": {"ref": "intent:request-quote"},
  "operationKey": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "provisionalState": {"ref": "state:quote-progress"},
  "concurrency": "latest-per-item-key",
  "cancellable": true,
  "receive": {},
  "commit": {},
  "cleanupRequired": true,
  "lateEventPolicy": "reject"
}
```

### TL;DR

An async operation is a lifecycle with identity and disposition—not merely a function call.

## 10. Consequential effect records

Every consequential effect needs an owner, target, evidence requirements, and allowed final dispositions.

```json
{
  "id": "effect:request-quote.capability-request",
  "kind": "effect",
  "effectKind": "capability-request",
  "owner": {"ref": "intent:request-quote"},
  "authority": {"ref": "capability:quotes.request"},
  "targetKey": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "allowedDispositions": [
    "committed",
    "cancelled",
    "superseded",
    "refused",
    "failed"
  ],
  "evidenceRequired": [
    "operation-receipt",
    "lifecycle-events",
    "canonical-reconciliation",
    "provisional-cleanup"
  ]
}
```

Initial effect kinds include:

```text
canonical-write
renderer-local-write
provisional-write
capability-request
external-mutation
confirmation
cancellation
supersession
cleanup
receipt-emission
surface-publication
```

A proof may not pass while a consequential effect has no terminal disposition or required evidence. The complete effect taxonomy, runtime-instance identity, terminal-disposition rules, minimum evidence matrix, residue accounting, uncertainty/recovery model, and reconciliation algorithm are governed by `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`.

### TL;DR

Visible success does not excuse unexplained requests, writes, cancellation, stale events, or cleanup.

## 11. Surface and binding records

The IR records semantic surface structure, not incidental DOM implementation.

```json
{
  "id": "surface-node:add-contract-control",
  "kind": "surface-node",
  "nodeKind": "control",
  "region": {"ref": "surface-region:editor"},
  "invokes": {"ref": "intent:add-contract"},
  "bindings": [
    {"ref": "binding:add-contract.name.control"},
    {"ref": "binding:add-contract.quantity.control"}
  ]
}
```

A keyed collection declares identity separately from presentation order:

```json
{
  "id": "surface-node:contract-list",
  "kind": "surface-node",
  "nodeKind": "collection",
  "source": {"ref": "state:visible-contracts"},
  "itemModel": {"ref": "model:Contract"},
  "key": {"ref": "field:Contract.id"},
  "template": {"ref": "surface-template:contract-row"}
}
```

Selectors, HTML attributes, generated node IDs, and browser package details are projections unless they carry stable semantic identity required by evidence.

### TL;DR

The IR owns semantic regions, bindings, controls, and keys; HTML is a replaceable projection.

## 12. Scenarios and proof claims

A scenario records stimuli and explicit claims against independent authorities:

```json
{
  "id": "scenario:add-contract.valid",
  "kind": "scenario",
  "steps": [
    {"kind": "control.enter", "binding": {"ref": "binding:add-contract.name.control"}, "value": "Steel"},
    {"kind": "intent.invoke", "intent": {"ref": "intent:add-contract"}},
    {"kind": "claim", "claim": {"ref": "claim:add-contract.receipt-committed"}},
    {"kind": "claim", "claim": {"ref": "claim:add-contract.canonical-row"}},
    {"kind": "claim", "claim": {"ref": "claim:add-contract.visible-row"}}
  ]
}
```

Claims identify their authority:

```json
{
  "id": "claim:add-contract.visible-row",
  "kind": "claim",
  "authority": "browser-observation",
  "subject": {"ref": "surface-node:contract-list"},
  "expect": {
    "kind": "collection.contains",
    "value": {"name": "Steel", "quantity": "12"}
  }
}
```

Required authority categories include:

```text
canonical-state
renderer-local-state
provisional-state
operation-receipt
capability-lifecycle
browser-observation
repository-binding
```

### TL;DR

The compiler may generate adapters and selectors; the author must still state consequential expected outcomes.

## 13. Provenance and compiler bindings

The IR preserves both authored origin and compiler origin:

```json
{
  "provenance": {
    "frontend": {
      "id": "legacy.requirements-registry.git-tools",
      "version": "mcel-requirements-registry-v1",
      "sourceFiles": [
        {
          "path": "pretty_docs/mcel-git-tools-requirements.md",
          "sha256": "..."
        }
      ]
    },
    "compiler": {
      "id": "mcel-requirements-importer",
      "version": "documentation-specification-only"
    }
  }
}
```

A source span may differ between legacy and DSL front ends without changing semantic equivalence. Provenance is therefore bound separately from semantic meaning.

### TL;DR

The IR must prove where meaning came from without confusing source location with meaning itself.

## 14. Normalization rules

Normalization is deterministic and fail-closed.

### 14.1 Defaults are expanded

Equivalent shorthand must normalize to the same explicit value.

```text
cancellable omitted on mutation -> false
cleanupRequired on async lifecycle -> explicit true or explicit false
```

A default is permitted only when the semantic-boundary document classifies it as mechanical rather than consequential.

### 14.2 IDs are resolved before ordering

All references resolve to stable IDs. Missing, duplicate, or wrong-kind references fail normalization.

### 14.3 Unordered semantic collections are sorted by ID

Examples:

```text
models
states
intents
capabilities
effects
surfaces
claims
```

### 14.4 Semantic sequences preserve order

Examples:

```text
transition steps
scenario steps
layout child order when declared meaningful
fallback choices when first-match semantics apply
```

### 14.5 Object keys are lexicographically ordered

The canonical serializer uses UTF-8 JSON, fixed escaping, no insignificant whitespace, and lexicographically ordered object keys.

### 14.6 Undefined values do not exist

`undefined`, functions, symbols, dates, class instances, cyclic values, and non-finite numbers are invalid IR values.

`null` is accepted only where the schema assigns it explicit meaning.

### 14.7 Opaque function hashes are migration evidence, not final behavior

The current Workbench normalizer records function hashes. An importer may retain them under a compatibility extension while migration is incomplete, but `mcel.application-ir.v1` semantic completeness requires constrained expressions for behavior that affects runtime or proof.

### 14.8 Incidental metadata is excluded from semantic normalization

Excluded examples:

```text
generatedAt
absolute repository path
compiler process ID
temporary route port
source line movement
formatting-only comments
```

### TL;DR

Equivalent meaning must produce identical canonical semantics even when source text, ordering, or formatting differs.

## 15. Fingerprints

The IR uses at least two distinct fingerprints.

### Semantic fingerprint

```text
sha256-mcel-application-ir-semantics-v1
```

Includes normalized semantic nodes and semantic versioning data.

Excludes source spans, compiler identity, timestamps, generated paths, evidence timestamps, and projection locations.

Use it to compare legacy and DSL compilers.

### Source-binding fingerprint

```text
sha256-mcel-application-ir-source-binding-v1
```

Includes:

```text
semantic fingerprint
front-end ID and version
source file paths and hashes
source-to-node bindings
```

Use it to prove which authored sources produced a semantic IR instance.

Projection and evidence systems retain their own fingerprints rather than being folded into the semantic fingerprint.

### TL;DR

Same meaning may come from different sources. We need to prove both facts separately.

## 16. Equivalence results

Two compiler outputs compare as one of:

| Result | Meaning |
| --- | --- |
| `exact` | Semantic fingerprints and normalized nodes match exactly |
| `semantically-equivalent` | Approved canonical rewrite proves equal meaning despite versioned representation differences |
| `intentional-versioned-delta` | A reviewed semantic change is explicitly versioned and documented |
| `incomplete` | One front end cannot yet represent required nodes |
| `conflicting` | Both represent the feature but disagree semantically |

Textual similarity, matching file counts, matching labels, or matching screenshots are not equivalence criteria.

### TL;DR

Compatibility is a claim about application meaning, not source resemblance.

## 17. Worked slice: Counter increment

### Current explicit representation

`mcel_apps/contract-counter/contracts/intents.js` declares:

```javascript
increment: Object.freeze({
  id: "increment",
  kind: "mutation",
  risk: "local-state",
  reads: Object.freeze(["state.count", "state.revision"]),
  writes: Object.freeze(["state.count", "state.revision"]),
  effects: Object.freeze(["count plus one", "revision plus one"])
})
```

### Candidate canonical IR

```json
{
  "id": "intent:increment",
  "kind": "intent",
  "operationKind": "mutation",
  "risk": "local-state",
  "reads": [
    {"ref": "state:count"},
    {"ref": "state:revision"}
  ],
  "writes": [
    {"ref": "state:count"},
    {"ref": "state:revision"}
  ],
  "transition": {
    "kind": "transition.sequence",
    "steps": [
      {
        "kind": "number.increment",
        "target": {"ref": "state:count"},
        "amount": 1
      },
      {
        "kind": "number.increment",
        "target": {"ref": "state:revision"},
        "amount": 1
      }
    ]
  },
  "effectRefs": [
    {"ref": "effect:increment.count-write"},
    {"ref": "effect:increment.revision-write"}
  ]
}
```

### Migration issue exposed

The current explicit contract describes effects in prose. The IR must preserve the meaning as structured transitions and effect obligations.

### Required equivalence

```text
same canonical reads
same canonical writes
same count delta
same revision delta
same visible count claim
same prohibition against direct-set
```

### TL;DR

Counter proves that the IR is economical without discarding explicit mutation authority.

## 18. Worked slice: Workbench add-contract

### Current normalized representation

The current normalizer already records:

```text
operationKind: mutation
payload sources for name, quantity, and category
reads: contracts, nextContractId, revision
writes: contracts, nextContractId, revision
serial-per-application concurrency
```

It currently records `preflight`, `transition`, and `ensures` as function hashes.

### Candidate canonical IR behavior

```json
{
  "id": "intent:add-contract",
  "kind": "intent",
  "operationKind": "mutation",
  "input": [
    {"ref": "input:add-contract.name"},
    {"ref": "input:add-contract.quantity"},
    {"ref": "input:add-contract.category"}
  ],
  "refusals": [
    {
      "id": "refusal:add-contract.name-required",
      "when": {
        "kind": "text.empty",
        "value": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}}
      },
      "code": "CONTRACT_NAME_REQUIRED"
    },
    {
      "id": "refusal:add-contract.quantity-minimum",
      "when": {
        "kind": "number.less-than",
        "left": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}},
        "right": {"kind": "constant", "value": 1}
      },
      "code": "CONTRACT_QUANTITY_MINIMUM"
    }
  ],
  "transition": {
    "kind": "transition.sequence",
    "steps": [
      {
        "kind": "list.append",
        "target": {"ref": "state:contracts"},
        "value": {
          "kind": "record.construct",
          "model": {"ref": "model:Contract"},
          "fields": {
            "id": {"kind": "id.from-counter", "counter": {"ref": "state:next-contract-id"}, "prefix": "contract-"},
            "name": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}},
            "quantity": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}},
            "category": {"kind": "input.read", "input": {"ref": "input:add-contract.category"}}
          }
        }
      },
      {"kind": "number.increment", "target": {"ref": "state:next-contract-id"}, "amount": 1},
      {"kind": "number.increment", "target": {"ref": "state:revision"}, "amount": 1}
    ]
  }
}
```

### Migration issue exposed

Function hashes prove deterministic source normalization but do not explain behavior. The final IR must lower those functions into constrained expressions or mark the migration incomplete.

### Required equivalence

```text
same payload sources and normalization
same refusal codes and canonical non-mutation on refusal
same ID allocation
same appended record
same revision behavior
same receipt
same visible row
```

### TL;DR

Add-contract proves that form plumbing may be generated while validation and canonical change remain explicit.

## 19. Worked slice: Workbench request-quote

### Current normalized representation

The current definition records:

```text
operationKind: async
uses: quotes
payload source: item-key
provisionalPath: quoteProgress
concurrency: latest-per-item-key
cancellable: true
reads: contracts, revision
writes: contracts, revision
hashed run, receive, commit, and ensures functions
```

### Candidate lifecycle IR

```json
{
  "id": "intent:request-quote",
  "kind": "intent",
  "operationKind": "async",
  "input": [
    {"ref": "input:request-quote.contract-id"}
  ],
  "uses": [
    {"ref": "capability:quotes.request"}
  ],
  "lifecycle": {
    "ref": "lifecycle:request-quote"
  },
  "effectRefs": [
    {"ref": "effect:request-quote.capability-request"},
    {"ref": "effect:request-quote.provisional-progress"},
    {"ref": "effect:request-quote.canonical-commit"},
    {"ref": "effect:request-quote.cleanup"}
  ]
}
```

```json
{
  "id": "lifecycle:request-quote",
  "kind": "operation-lifecycle",
  "operationKey": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "concurrency": "latest-per-item-key",
  "cancellable": true,
  "provisionalState": {"ref": "state:quote-progress"},
  "lateEventPolicy": "reject",
  "receive": {
    "kind": "provisional.reduce-event",
    "target": {"ref": "state:quote-progress"},
    "key": {"kind": "operation.key"},
    "event": {"kind": "capability.event"}
  },
  "commit": {
    "kind": "list.update-by-key",
    "target": {"ref": "state:contracts"},
    "keyField": {"ref": "field:Contract.id"},
    "key": {"kind": "operation.key"},
    "changes": {
      "quote": {"kind": "capability.result-field", "field": "quote"}
    }
  },
  "cleanup": {
    "kind": "provisional.remove-by-key",
    "target": {"ref": "state:quote-progress"},
    "key": {"kind": "operation.key"}
  }
}
```

### Required proof accounting

```text
request started under the correct contract key
provisional events were accepted only for the active operation
newer same-key work superseded older work
parallel different-key work remained independent
cancellation prevented canonical commit
late events were rejected
successful result committed once
provisional state was cleaned up
visible quote agreed with canonical state
```

### TL;DR

Request-quote proves that the IR can explain a complete asynchronous lifecycle, not merely its final UI.

## 20. Projection boundary

The first likely back end is:

```text
mcel.application-ir.v1
  -> generated low-level application definition
  -> explicit package contracts
  -> runtime projection
  -> acceptance/observation adapters
```

The current human-owned Workbench `application.js` is a front-end prototype. The current explicit contract package is a projection prototype. Their exact file names are not IR law.

A likely v1 package boundary is:

```text
mcel_apps/<app>/application.js                       official DSL source
mcel_apps/<app>/generated/mcel.application.ir.json  canonical IR
mcel_apps/<app>/generated/application.definition.js low-level readable projection
mcel_apps/<app>/contracts/                           generated explicit contracts
```

`pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` now fixes this as the promoted package layout while keeping compiler candidates outside the live package under a fingerprint-bound runtime-state workspace. The file layout remains versioned and replaceable; the IR meaning remains the stable center.

### TL;DR

The IR is stable; promoted and candidate projection locations are now explicit and separately owned.

## 21. Required validation failures

The future validator must reject at least:

```text
duplicate semantic ID
unresolved reference
reference to wrong node kind
state with no authority
canonical write outside an authorized mutation
write-set disagreement
collection with no stable key
async operation with no operation identity
latest-per-item concurrency with no item key
cancellable operation with no cancellation lifecycle
capability use with no authority declaration
consequential effect with no allowed terminal disposition
scenario claim with no independent authority
opaque behavior in a DSL-v1-complete application
nondeterministic IR value
semantic fingerprint input containing incidental metadata
```

Each failure maps to the stable diagnostic envelope, code namespaces, semantic paths, safe repair classes, and source repair behavior specified in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`.

### TL;DR

Invalid semantic combinations must fail before runtime and point back to authored source.

## 22. Acceptance criteria for this specification

The specification is ready for implementation planning only when documentation can show that:

1. Counter increment, reset, direct-set prohibition, surface, and proof fit without special cases.
2. Workbench add-contract fits without opaque behavior.
3. Workbench request-quote fits with complete lifecycle and effect accounting.
4. Git Tools governed mutation, receipts, confirmation, and recovery have IR homes.
5. Code Editor draft/canonical boundaries, stale-source checks, filesystem effects, and project-edit transactions have IR homes.
6. Document Editor semantic regions, layout ownership, local editor state, persistence, and export effects have IR homes.
7. Every current application-definition family in the migration inventory has a stated import path or a recorded gap.
8. Semantic and source-binding fingerprint boundaries are unambiguous.
9. No rule depends on the current package file layout unless that dependency is explicitly versioned.
10. The separate constrained-expression, effects/proof, DSL syntax, diagnostics/repair, projection/compatibility, authoring-cycle, change-impact, and benchmark documents agree with this schema; the completeness review must verify that agreement before implementation authorization.

### TL;DR

The IR is not complete until it preserves both the acid apps and the real application-definition families.

## 23. Completeness-review dispositions

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` closes the v1-level questions as follows:

```text
core expression and effect kinds are closed per compiler/schema version;
domain extensions require versioned registration
layout child order is semantic only when the node kind declares ordered children
generated paths and ownership follow the scaffolder/projection specification
legacy opaque callbacks use the migration-only `legacy.opaque-function` expression node
v1 semantic equivalence permits only documented canonical rewrites
unknown expression/effect kinds and undocumented rewrites are rejected
```

The following remain future-version or feature-local work rather than IR-kernel blockers:

```text
IR v2 evolution and cross-version migration
additional numeric and locale profiles
additional domain-registration packaging
runtime evaluator versus generated-code optimization
```

### TL;DR

The v1 IR kernel has a closed semantic boundary; future extensions must be versioned rather than inferred by implementation.

## Semantic change and evidence impact

Semantic differences between two normalized IR documents, their typed dependency closure, and the resulting projection and evidence-renewal policy are specified in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`. The IR schema supplies the stable IDs and references required for that analysis.

### TL;DR

Stable IR identity makes semantic impact calculable across compiler front ends.

## Final rule

> Every compiler front end must produce enough normalized meaning for MCEL to execute the application, compare it with other front ends, trace it to source, and explain every consequential effect through independent evidence.

## Repository-owned structural validation

The Wave 1 IR kernel must run in the repository's standard Python environment without requiring an optional `jsonschema` installation. The checked-in Draft 2020-12 schema remains the public structural contract, while the kernel implements and self-checks the exact schema subset used by that file.

A schema change that introduces an unsupported keyword or unresolved local reference is a blocking schema-definition error until the repository validator is deliberately extended. This prevents an undeclared package dependency and prevents schema edits from silently outrunning the validator.

**TL;DR:** The schema remains authoritative, but the first IR tool is standard-library-only and fails closed when the schema uses constructs it does not understand.

# Wave 4 implementation note

The Counter-bounded projector now consumes normalized `mcel.application-ir.v1` and emits an isolated explicit-package candidate through the versioned `mcel.counter.explicit-projection.v1` profile. The profile supplies deterministic low-level mechanics that are not semantic authority, while the generated package must import back to the same semantic fingerprint. Exact byte, package, catalog, and runtime-projection fingerprints are checked against the live Counter package.

### TL;DR

The IR now has one proven round trip: DSL to IR to isolated Counter package to the same IR.
