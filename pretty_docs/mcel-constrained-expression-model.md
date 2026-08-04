# MCEL Constrained Expression Model

## Status

This document specifies the inspectable expression vocabulary used by `mcel.application-ir.v1` and constructed by the official vanilla-JavaScript source form in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`.

The bounded Wave 2A expression kernel is now implemented in `main_computer/mcel_constrained_expression.py`, with the read-only analyzer `tools/mcel_constrained_expression.py` and tests against the Counter IR. It constructs portable expression records, validates expression contexts and operand/result types, extracts reads and writes, normalizes and fingerprints expression graphs, and records versioned pure domain operators. It does not evaluate application behavior, implement the official JavaScript DSL builders, perform capabilities, generate package projections, migrate applications, or retire any callback-based path.

Read this with:

- `pretty_docs/mcel-ai-authoring-semantic-boundary.md`;
- `pretty_docs/mcel-application-ir-and-compiler-migration.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`;
- `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`;
- `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`;
- `pretty_docs/mcel-ai-application-authoring-cycle.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`.

## The short answer

The official MCEL source is valid vanilla JavaScript, but application meaning cannot be arbitrary JavaScript hidden inside callbacks.

The source constructs a typed expression graph:

```javascript
change: contracts.append(
  Contract.create({
    id: nextId("contract"),
    name: input.name,
    quantity: input.quantity
  })
)
```

The compiler normalizes that declaration into inspectable IR:

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

MCEL can then determine:

```text
what the expression reads
what it may write
what type it returns
whether it is deterministic
whether it is pure
which refusal or invariant it supports
which proof claim depends on it
whether two compiler front ends produced equivalent meaning
```

### TL;DR

JavaScript is the authoring notation. A constrained typed expression graph is the executable application meaning.

## 1. Why arbitrary callbacks are not the final model

### Current explicit behavior

Contract Workbench currently contains callbacks such as:

```javascript
({contracts, filterText, sortMode}) => {
  const normalizedFilter = String(filterText || "").trim().toLowerCase();
  const filtered = contracts.filter((contract) => (
    !normalizedFilter
    || contract.name.toLowerCase().includes(normalizedFilter)
    || contract.category.toLowerCase().includes(normalizedFilter)
  ));
  return [...filtered].sort(...);
}
```

That callback executes correctly, but a function hash cannot by itself explain:

```text
which fields are read
whether hidden state is accessed
whether time or randomness is used
whether the operation is deterministic
whether sorting has a stable tie breaker
whether another compiler emitted equivalent meaning
which part of the expression caused a proof failure
```

### Required constrained meaning

The same behavior should normalize into query expressions:

```json
{
  "kind": "query.sort",
  "source": {
    "kind": "query.filter",
    "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
    "predicate": {
      "kind": "boolean.or",
      "operands": [
        {"kind": "text.is-empty", "value": {"kind": "text.normalize-search", "value": {"kind": "state.read", "state": {"ref": "state:filter-text"}}}},
        {"kind": "text.contains", "value": {"kind": "item.read", "field": {"ref": "field:Contract.name"}}, "search": {"kind": "text.normalize-search", "value": {"kind": "state.read", "state": {"ref": "state:filter-text"}}}},
        {"kind": "text.contains", "value": {"kind": "item.read", "field": {"ref": "field:Contract.category"}}, "search": {"kind": "text.normalize-search", "value": {"kind": "state.read", "state": {"ref": "state:filter-text"}}}}
      ]
    }
  },
  "order": [
    {"kind": "query.dynamic-order", "selector": {"kind": "state.read", "state": {"ref": "state:sort-mode"}}, "allowedFields": [{"ref": "field:Contract.name"}, {"ref": "field:Contract.quantity"}, {"ref": "field:Contract.category"}]},
    {"kind": "query.order-ascending", "value": {"kind": "item.read", "field": {"ref": "field:Contract.id"}}}
  ]
}
```

### TL;DR

A callback is executable code. An expression graph is executable code whose meaning MCEL can inspect, compare, diagnose, and prove.

## 2. Governing constraints

Every v1 expression must satisfy these constraints:

1. It belongs to a registered expression kind.
2. Its inputs and result type are known.
3. Its scope determines which references are legal.
4. Its purity and determinism class are known.
5. Its reads are structurally extractable.
6. Any write is structurally visible and authorized by its owning intent.
7. It cannot directly perform an external effect.
8. It cannot access ambient JavaScript state.
9. It cannot depend on wall-clock time, randomness, process state, network state, DOM state, or filesystem state unless that value enters through an explicit context, input, event, or capability result.
10. It preserves source provenance sufficient for diagnostics.
11. It normalizes deterministically.
12. It has a defined proof interpretation.

Invalid:

```javascript
change: () => {
  fetch("/api/save");
  return Date.now();
}
```

Valid separation:

```javascript
const save = intent.capability("save-document", {
  capability: files.write,
  request: FileWriteRequest.create({
    path: input.path,
    expectedHash: state.sourceHash,
    content: state.draft
  }),
  commit: documentHash.set(result.sha256)
});
```

The expression builds the request and canonical reconciliation. The capability owns the external filesystem effect.

### TL;DR

Expressions compute and describe transitions. Capabilities perform effects. Effect declarations, runtime instances, evidence, terminal dispositions, cleanup, and proof reconciliation are governed by `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`. The DSL must not blur that boundary.

## 3. Expression record shape

Every normalized expression uses this minimum shape:

```json
{
  "kind": "number.add",
  "type": {"ref": "schema:integer"},
  "operands": [
    {"kind": "state.read", "state": {"ref": "state:revision"}},
    {"kind": "constant", "value": 1, "type": {"ref": "schema:integer"}}
  ],
  "source": {
    "file": "mcel_apps/contract-workbench/application.js",
    "start": {"line": 1, "column": 1},
    "end": {"line": 1, "column": 20}
  }
}
```

Required common fields:

| Field | Meaning |
| --- | --- |
| `kind` | Closed expression operation identifier |
| `type` | Normalized result schema reference or inline primitive schema |
| expression-specific fields | Operands, references, branches, fields, or selectors required by the kind |
| `source` | Authored source range or generated provenance |

The compiler may also attach derived analysis outside the semantic fingerprint:

```json
{
  "analysis": {
    "reads": ["state:revision"],
    "writes": [],
    "purity": "pure",
    "determinism": "deterministic",
    "totality": "total"
  }
}
```

The compiler must calculate analysis from the expression. Author-supplied analysis cannot override the graph.

### TL;DR

Expression meaning is authoritative. Read/write and purity summaries are compiler-derived checks, not author-controlled promises.

## 4. Expression contexts

An expression is valid only in a declared context.

| Context | May read | May produce | May write |
| --- | --- | --- | --- |
| Schema default | constants, deterministic constructors | one valid value | nothing |
| Input normalization | raw input, constants, context explicitly bound to the input | normalized input or refusal | nothing |
| Validation | input, state declared by the owner, context | boolean or refusal finding | nothing |
| Invariant | declared canonical state | boolean or invariant finding | nothing |
| Derivation | declared dependencies | derived value | nothing |
| Mutation transition | declared state reads, normalized input, approved context | canonical patch/transition | declared canonical writes only |
| Postcondition | before state, after state, input, receipt | boolean or finding | nothing |
| Provisional receive | current provisional value, capability event, input, operation context | provisional update | owned provisional path only |
| Capability request | normalized input, declared state reads, approved context | request value | nothing |
| Capability reconciliation | result/events, state reads, input, operation context | canonical transition or terminal refusal/failure | declared canonical writes only |
| Surface binding | state, derived state, local state, item scope, presentation context | visible/property value | nothing |
| Scenario setup | constants, fixtures, prior step outputs | next stimulus | scenario fixture only |
| Scenario claim | observed state, visible surface, receipt, effect ledger | boolean/claim result | nothing |

The compiler must reject a legal expression kind used in an illegal context.

Example:

```json
{
  "kind": "capability.result.read",
  "field": "sha256"
}
```

is valid during capability reconciliation but invalid inside an ordinary derived-state expression.

### TL;DR

Expression kinds are not globally legal. Their owning semantic context defines what they may observe and change.

## 5. Value-source expressions

The v1 core must include explicit source expressions.

### Constants

```json
{"kind": "constant", "value": 1, "type": {"ref": "schema:integer"}}
```

Constants must already satisfy their normalized type. `undefined`, functions, symbols, class instances, and cyclic values are invalid.

### State reads

```json
{"kind": "state.read", "state": {"ref": "state:contracts"}}
```

A state read is valid only when the owning node declares or structurally permits that dependency.

### Input reads

```json
{"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}}
```

This reads the normalized semantic input, not the raw DOM value.

### Item reads

```json
{
  "kind": "item.read",
  "itemScope": {"ref": "scope:contracts-row"},
  "field": {"ref": "field:Contract.id"}
}
```

Item scope must be attached to a keyed collection. Array position is not a valid substitute for item identity.

### Context reads

```json
{
  "kind": "context.read",
  "context": {"ref": "context:authenticated-user"},
  "field": "userId"
}
```

The context must be explicitly bound and typed. Ambient globals are forbidden.

### Before and after reads

```json
{"kind": "transition.before.read", "state": {"ref": "state:revision"}}
```

```json
{"kind": "transition.after.read", "state": {"ref": "state:revision"}}
```

These are legal in postconditions and transition proofs, not in ordinary derivations.

### Capability event and result reads

```json
{"kind": "capability.event.read", "field": "report"}
```

```json
{"kind": "capability.result.read", "field": "amount"}
```

Event schemas and result schemas define the available fields.

### Prior scenario output

```json
{
  "kind": "scenario.output.read",
  "step": {"ref": "scenario-step:create-item"},
  "field": "itemId"
}
```

This is limited to scenario construction and cannot become application runtime state.

### TL;DR

Every value enters an expression through a named, typed source. There is no ambient state.

## 6. Record and collection construction

### Record construction

```json
{
  "kind": "record.construct",
  "model": {"ref": "model:Contract"},
  "fields": {
    "id": {"kind": "id.next", "namespace": "contract"},
    "name": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}},
    "category": {"kind": "input.read", "input": {"ref": "input:add-contract.category"}},
    "quantity": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}},
    "quoteStatus": {"kind": "constant", "value": "idle"},
    "quoteAmount": {"kind": "constant", "value": 0}
  }
}
```

Every required model field must be supplied or have an explicit schema default.

### Record field access

```json
{
  "kind": "record.get",
  "record": {"kind": "state.read", "state": {"ref": "state:selected-contract"}},
  "field": {"ref": "field:Contract.quantity"}
}
```

### Record update

```json
{
  "kind": "record.set",
  "record": {"kind": "item.current"},
  "fields": {
    "quantity": {"kind": "input.read", "input": {"ref": "input:update-quantity.quantity"}}
  }
}
```

`record.set` returns a new typed record. It is not an object mutation.

### List construction

```json
{
  "kind": "list.construct",
  "itemType": {"ref": "model:Contract"},
  "items": []
}
```

### Map construction

```json
{
  "kind": "map.construct",
  "keyType": {"ref": "schema:string"},
  "valueType": {"ref": "schema:QuoteProgress"},
  "entries": []
}
```

Map semantic equality is key-based. Serialization order is lexicographic by normalized key unless the map schema explicitly gives order semantic meaning.

### TL;DR

Construction is typed and immutable. JavaScript object mutation is not the semantic model.

## 7. Scalar expression families

The v1 core should support the operations required by existing applications without becoming a general-purpose JavaScript virtual machine.

### Boolean

```text
boolean.not
boolean.and
boolean.or
boolean.all
boolean.any
```

Short-circuit behavior is semantic and must be specified. `boolean.and` and `boolean.or` preserve operand order because later operands may be partial even though expressions remain pure.

### Equality and ordering

```text
compare.equal
compare.not-equal
compare.less-than
compare.less-than-or-equal
compare.greater-than
compare.greater-than-or-equal
compare.in-set
compare.is-null
```

Equality is schema-aware. It must not inherit JavaScript coercion.

### Numbers

```text
number.add
number.subtract
number.multiply
number.divide
number.minimum
number.maximum
number.round
number.absolute
number.is-integer
```

Division by zero and numeric overflow behavior must be explicit. Silent `NaN` and `Infinity` are not valid ordinary values unless a schema explicitly admits them.

### Text

```text
text.trim
text.lowercase
text.uppercase
text.length
text.is-empty
text.contains
text.starts-with
text.ends-with
text.compare
text.concat
text.normalize-search
```

Locale-sensitive behavior requires an explicit locale or a fixed MCEL normalization profile. Host locale is not an implicit dependency.

### Optional values

```text
optional.is-present
optional.unwrap
optional.default
```

`optional.unwrap` must have either a statically proven presence condition or an explicit refusal/error branch.

### Conditional selection

```json
{
  "kind": "conditional",
  "when": {"kind": "compare.greater-than", "left": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}}, "right": {"kind": "constant", "value": 0}},
  "then": {"kind": "constant", "value": "valid"},
  "else": {"kind": "constant", "value": "invalid"}
}
```

Both branches must have compatible result types.

### TL;DR

MCEL defines predictable typed operators. It does not inherit JavaScript coercion, locale, `NaN`, or truthiness rules accidentally.

## 8. Query expressions

Queries describe pure reads over collections.

Minimum v1 query kinds:

```text
query.filter
query.sort
query.map
query.find-by-key
query.find-first
query.any
query.every
query.count
query.sum
query.average
query.group-by
query.distinct-by
query.take
query.skip
```

### Filter

```json
{
  "kind": "query.filter",
  "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
  "itemScope": {"id": "scope:visible-contract-filter", "model": {"ref": "model:Contract"}},
  "predicate": {
    "kind": "compare.greater-than",
    "left": {"kind": "item.read", "field": {"ref": "field:Contract.quantity"}},
    "right": {"kind": "constant", "value": 0}
  }
}
```

### Sort

```json
{
  "kind": "query.sort",
  "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
  "order": [
    {"kind": "query.order-ascending", "value": {"kind": "item.read", "field": {"ref": "field:Contract.name"}}, "collation": "mcel-text-v1"},
    {"kind": "query.order-ascending", "value": {"kind": "item.read", "field": {"ref": "field:Contract.id"}}, "collation": "mcel-text-v1"}
  ]
}
```

A stable tie breaker must be explicit whenever the primary ordering can compare equal and output order is observable.

### Aggregate

Workbench total quantity:

```json
{
  "kind": "query.sum",
  "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
  "itemScope": {"id": "scope:contract-quantity-sum", "model": {"ref": "model:Contract"}},
  "value": {"kind": "item.read", "field": {"ref": "field:Contract.quantity"}},
  "empty": {"kind": "constant", "value": 0}
}
```

Workbench reconciled quote average:

```json
{
  "kind": "number.round",
  "mode": "nearest",
  "value": {
    "kind": "query.average",
    "source": {"kind": "record.get", "record": {"kind": "provisional.current"}, "field": "reports"},
    "itemScope": {"id": "scope:quote-reports", "schema": {"ref": "schema:QuoteReport"}},
    "value": {"kind": "item.read", "field": "amount"},
    "empty": {"kind": "constant", "value": 0}
  }
}
```

### TL;DR

Queries expose dependencies, item scopes, ordering, empty behavior, and aggregates instead of hiding them in array callbacks.

## 9. Predicate, refusal, and invariant expressions

A predicate returns a typed boolean. A refusal expression returns a structured refusal. An invariant produces a named finding when its predicate is false.

### Validation predicate

```json
{
  "kind": "boolean.and",
  "operands": [
    {
      "kind": "compare.greater-than",
      "left": {"kind": "text.length", "value": {"kind": "text.trim", "value": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}}}},
      "right": {"kind": "constant", "value": 0}
    },
    {
      "kind": "number.is-integer",
      "value": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}}
    },
    {
      "kind": "compare.greater-than",
      "left": {"kind": "input.read", "input": {"ref": "input:add-contract.quantity"}},
      "right": {"kind": "constant", "value": 0}
    }
  ]
}
```

### Refusal rule

```json
{
  "kind": "refusal.when",
  "when": {
    "kind": "text.is-empty",
    "value": {"kind": "text.trim", "value": {"kind": "input.read", "input": {"ref": "input:add-contract.name"}}}
  },
  "refusal": {
    "code": "CONTRACT_NAME_REQUIRED",
    "message": "A contract name is required."
  }
}
```

Refusal codes are stable semantic IDs. Display messages are not identity.

### Collection uniqueness invariant

```json
{
  "kind": "invariant.assert",
  "id": "invariant:contract-keys-unique",
  "predicate": {
    "kind": "collection.keys-unique",
    "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
    "key": {"ref": "field:Contract.id"}
  },
  "finding": {"code": "CONTRACT_KEYS_NOT_UNIQUE"}
}
```

Specialized operators such as `collection.keys-unique` are preferable when they carry clearer semantics and diagnostics than a manually constructed count comparison.

### TL;DR

Refusal and invariant meaning must be named, structured, and independently testable—not buried in `if` statements.

## 10. Canonical transition expressions

Canonical transition expressions describe immutable state change.

Minimum v1 transition kinds:

```text
transition.assign
transition.sequence
list.append
list.remove-by-key
list.update-by-key
map.put
map.remove
record.set
number.increment
number.add-to-state
```

### Append and increment

```json
{
  "kind": "transition.sequence",
  "steps": [
    {
      "kind": "list.append",
      "target": {"ref": "state:contracts"},
      "value": {"kind": "record.construct", "model": {"ref": "model:Contract"}, "fields": {}}
    },
    {
      "kind": "number.increment",
      "target": {"ref": "state:next-contract-id"},
      "amount": 1
    },
    {
      "kind": "number.increment",
      "target": {"ref": "state:revision"},
      "amount": 1
    }
  ]
}
```

Sequence order is semantic when later steps read results written by earlier steps. Independent writes may be normalized by target ID only when the IR explicitly marks them as an unordered atomic patch.

### Remove by key

```json
{
  "kind": "list.remove-by-key",
  "target": {"ref": "state:contracts"},
  "keyField": {"ref": "field:Contract.id"},
  "key": {"kind": "input.read", "input": {"ref": "input:remove-contract.contract-id"},
  "cardinality": "exactly-one"
}
```

The cardinality rule determines whether missing or duplicate matches refuse, fail an invariant, or are permitted.

### Update by key

```json
{
  "kind": "list.update-by-key",
  "target": {"ref": "state:contracts"},
  "keyField": {"ref": "field:Contract.id"},
  "key": {"kind": "input.read", "input": {"ref": "input:update-quantity.contract-id"}},
  "update": {
    "kind": "record.set",
    "record": {"kind": "item.current"},
    "fields": {
      "quantity": {"kind": "input.read", "input": {"ref": "input:update-quantity.quantity"}}
    }
  },
  "cardinality": "exactly-one"
}
```

### Authority validation

If an intent declares:

```json
{"writes": [{"ref": "state:contracts"}]}
```

but its transition contains:

```json
{"kind": "number.increment", "target": {"ref": "state:revision"}, "amount": 1}
```

compilation must fail. The expression graph cannot silently broaden authority.

### TL;DR

Canonical writes are visible structural operations. The compiler derives the actual write set and checks it against declared authority.

## 11. Provisional-state reconciliation

Provisional expressions own temporary, operation-scoped state. They do not directly commit canonical application truth.

### Read current provisional entry

```json
{
  "kind": "provisional.get-by-key",
  "state": {"ref": "state:quote-progress"},
  "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "default": {"kind": "record.construct", "model": {"ref": "model:QuoteProgress"}, "fields": {}}
}
```

### Event switch

```json
{
  "kind": "event.switch",
  "eventType": {"kind": "capability.event.type"},
  "cases": {
    "quote.started": {"kind": "record.set", "record": {"kind": "provisional.current"}, "fields": {"expected": {"kind": "capability.event.read", "field": "expected"}}},
    "quote.received": {"kind": "record.set", "record": {"kind": "provisional.current"}, "fields": {"received": {"kind": "number.add", "operands": [{"kind": "provisional.current.read", "field": "received"}, {"kind": "constant", "value": 1}]}}},
    "quote.failed": {"kind": "record.set", "record": {"kind": "provisional.current"}, "fields": {"status": {"kind": "constant", "value": "failed"}}}
  },
  "default": {"kind": "event.ignore", "reason": "unrecognized-event-type"}
}
```

Every event type admitted by the capability stream schema must have an explicit disposition:

```text
accepted and reconciled
ignored by declared rule
terminal failure
invalid event
late event rejected by lifecycle policy
```

### Write provisional entry

```json
{
  "kind": "provisional.put-by-key",
  "target": {"ref": "state:quote-progress"},
  "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "value": {"kind": "event.switch"}
}
```

### Cleanup

```json
{
  "kind": "provisional.remove-by-key",
  "target": {"ref": "state:quote-progress"},
  "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}}
}
```

Cleanup belongs to lifecycle completion and must be included in effect accounting.

### TL;DR

Every streamed event and every provisional entry has an explicit reconciliation or rejection path. Temporary state cannot become unexplained residue.

## 12. Capability request and result expressions

Expressions may construct capability requests and reconcile typed results. They may not execute the capability.

### Request construction

```json
{
  "kind": "record.construct",
  "schema": {"ref": "schema:QuoteRequest"},
  "fields": {
    "contractId": {"kind": "record.get", "record": {"kind": "query.find-by-key", "source": {"kind": "state.read", "state": {"ref": "state:contracts"}}, "keyField": {"ref": "field:Contract.id"}, "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}}}, "field": {"ref": "field:Contract.id"}},
    "category": {"kind": "record.get", "record": {"kind": "query.find-by-key", "source": {"kind": "state.read", "state": {"ref": "state:contracts"}}, "keyField": {"ref": "field:Contract.id"}, "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}}}, "field": {"ref": "field:Contract.category"}},
    "quantity": {"kind": "record.get", "record": {"kind": "query.find-by-key", "source": {"kind": "state.read", "state": {"ref": "state:contracts"}}, "keyField": {"ref": "field:Contract.id"}, "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}}}, "field": {"ref": "field:Contract.quantity"}}
  }
}
```

The official DSL may provide a local binding to avoid repeating the `find-by-key` expression. Normalization may preserve a typed `let` binding:

```json
{
  "kind": "let",
  "bindings": {
    "contract": {"kind": "query.find-by-key", "source": {}, "keyField": {}, "key": {}}
  },
  "in": {"kind": "record.construct", "fields": {"contractId": {"kind": "binding.read", "binding": "contract", "field": "id"}}}
}
```

### Result reconciliation

```json
{
  "kind": "list.update-by-key",
  "target": {"ref": "state:contracts"},
  "keyField": {"ref": "field:Contract.id"},
  "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
  "update": {
    "kind": "record.set",
    "record": {"kind": "item.current"},
    "fields": {
      "quoteStatus": {"kind": "constant", "value": "quoted"},
      "quoteAmount": {"kind": "capability.result.read", "field": "amount"}
    }
  }
}
```

### TL;DR

Expressions describe the data crossing a capability boundary and the state reconciliation after it. The capability runtime remains the effect authority.

## 13. Surface expressions

Surface expressions compute visible values and properties from semantic state.

Minimum v1 surface expression roles:

```text
text content
control value
control enabled/disabled
visibility
semantic status
collection source
item-bound value
validation message
accessible name or description
```

### Visible text

```json
{
  "kind": "surface.text",
  "value": {
    "kind": "text.concat",
    "parts": [
      {"kind": "constant", "value": "Total quantity: "},
      {"kind": "state.read", "state": {"ref": "state:total-quantity"}}
    ]
  }
}
```

### Enabled property

```json
{
  "kind": "surface.property",
  "property": "disabled",
  "value": {
    "kind": "boolean.not",
    "operand": {"kind": "state.read", "state": {"ref": "state:can-submit"}}
  }
}
```

### Item-bound field

```json
{
  "kind": "surface.text",
  "value": {"kind": "item.read", "itemScope": {"ref": "scope:contracts-row"}, "field": {"ref": "field:Contract.name"}}
}
```

Surface expressions are pure. DOM reads, query selectors, element mutation, and event listeners belong to generated runtime projection, not semantic expression meaning.

### TL;DR

The IR states what a surface means. Generated runtime code decides how that meaning reaches the DOM.

## 14. Scenario and proof expressions

Scenario claims use the same semantic vocabulary but read independent evidence authorities.

### Canonical-state claim

```json
{
  "kind": "claim.exists",
  "authority": "canonical-state",
  "source": {"ref": "state:contracts"},
  "where": {
    "kind": "boolean.and",
    "operands": [
      {"kind": "compare.equal", "left": {"kind": "item.read", "field": {"ref": "field:Contract.name"}}, "right": {"kind": "constant", "value": "Steel"}},
      {"kind": "compare.equal", "left": {"kind": "item.read", "field": {"ref": "field:Contract.quantity"}}, "right": {"kind": "constant", "value": 12}}
    ]
  }
}
```

### Visible-surface claim

```json
{
  "kind": "claim.surface-row-exists",
  "surface": {"ref": "surface:contract-list"},
  "fields": {
    "name": {"kind": "constant", "value": "Steel"},
    "quantity": {"kind": "constant", "value": "12"}
  }
}
```

### Receipt claim

```json
{
  "kind": "claim.receipt-disposition",
  "intent": {"ref": "intent:add-contract"},
  "disposition": "committed"
}
```

### Effect-ledger claim

```json
{
  "kind": "claim.effect-accounted",
  "effect": {"ref": "effect:request-quote.capability-request"},
  "allowedFinalDispositions": ["committed", "cancelled", "superseded", "refused", "failed"],
  "requireCleanup": true
}
```

Proof expressions cannot simply invoke the implementation expression and assert its return value. They must bind to an independently produced authority such as canonical state evidence, browser observation, receipt evidence, or effect-ledger evidence.

### TL;DR

The same semantic vocabulary supports proof, but proof reads independent evidence instead of trusting the implementation’s own answer.

## 15. Local bindings and reusable expressions

The official DSL needs local names for readability and to avoid repeated graph fragments.

Illustrative source:

```javascript
const contract = contracts.findByKey(input.contractId);

const request = QuoteRequest.create({
  contractId: contract.id,
  category: contract.category,
  quantity: contract.quantity
});
```

Canonical IR:

```json
{
  "kind": "let",
  "bindings": [
    {
      "name": "contract",
      "value": {
        "kind": "query.find-by-key",
        "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
        "keyField": {"ref": "field:Contract.id"},
        "key": {"kind": "input.read", "input": {"ref": "input:request-quote.contract-id"}},
        "missing": "refuse:CONTRACT_NOT_FOUND"
      }
    }
  ],
  "in": {
    "kind": "record.construct",
    "schema": {"ref": "schema:QuoteRequest"},
    "fields": {
      "contractId": {"kind": "binding.read", "binding": "contract", "field": "id"},
      "category": {"kind": "binding.read", "binding": "contract", "field": "category"},
      "quantity": {"kind": "binding.read", "binding": "contract", "field": "quantity"}
    }
  }
}
```

Bindings are lexical, immutable, typed, and expression-local. They cannot contain runtime closures or mutable captured state.

### Bounded evaluation

The v1 graph is acyclic. User-defined recursion, unbounded loops, generators, and callback-driven iteration are not core expression semantics. Collection operators iterate only over their declared finite input collection, and projection/runtime policy may impose explicit capture or work ceilings where evidence size or hostile input matters.

A later version may add bounded folds or recursive domain operators only with a documented termination rule, cost model, deterministic limit behavior, and proof interpretation. A JavaScript helper that recursively constructs a finite expression graph is authoring machinery; recursion inside the resulting semantic graph is a different feature and is not implicitly authorized.

Reusable named expressions may be declared when they represent stable semantic concepts:

```text
predicate:path-is-inside-project-root
query:visible-contracts
validator:positive-quantity
reconcile:quote-progress-event
```

A named expression must declare parameters and a result type and must normalize independently of its source file location.

### TL;DR

Local names improve authoring. They do not reintroduce hidden closure state.

## 16. Totality, refusal, and failure

Every expression must have an explicit totality classification:

```text
total
may-refuse
may-fail-invalid-evidence
```

Ordinary expressions may not throw arbitrary JavaScript exceptions.

### Missing keyed item

Invalid implicit behavior:

```javascript
contracts.find((item) => item.id === input.contractId).quantity
```

Required explicit behavior:

```json
{
  "kind": "query.find-by-key",
  "source": {"kind": "state.read", "state": {"ref": "state:contracts"}},
  "keyField": {"ref": "field:Contract.id"},
  "key": {"kind": "input.read", "input": {"ref": "input:update-quantity.contract-id"}},
  "missing": {
    "kind": "refusal",
    "code": "CONTRACT_NOT_FOUND"
  },
  "duplicates": {
    "kind": "invariant-failure",
    "code": "CONTRACT_KEYS_NOT_UNIQUE"
  }
}
```

### Invalid parse

Raw-control parsing belongs to the input-binding boundary:

```json
{
  "kind": "input.parse-integer",
  "raw": {"kind": "control.value.read", "control": {"ref": "control:draft-quantity"}},
  "invalid": {"kind": "refusal", "code": "CONTRACT_QUANTITY_INVALID"}
}
```

Once normalized, `input.read` is typed and does not repeatedly parse.

### TL;DR

Missing values, invalid inputs, and impossible states have declared outcomes. They do not escape through arbitrary exceptions.

## 17. Determinism and forbidden ambient behavior

The v1 expression model forbids direct use of:

```text
Date.now()
new Date() without explicit context input
Math.random()
crypto randomness without an authorized capability/context
fetch()
XMLHttpRequest
WebSocket
filesystem APIs
process.env
window globals
DOM queries
mutable module globals
unregistered singleton state
host locale defaults
host timezone defaults
iteration over unordered host objects when order is observable
```

Equivalent needs must use explicit semantic inputs.

### Time

```json
{"kind": "context.read", "context": {"ref": "context:operation-time"}, "field": "instant"}
```

The operation-time context must identify who supplies the instant and how evidence records it.

### Random identifier

Use deterministic allocation when possible:

```json
{"kind": "id.next", "namespace": "contract", "counter": {"ref": "state:next-contract-id"}}
```

When cryptographic randomness is genuinely required, it is a capability or governed context and therefore a consequential effect/input, not a pure expression.

### Locale-sensitive sort

```json
{
  "kind": "query.order-ascending",
  "value": {"kind": "item.read", "field": {"ref": "field:Contact.name"}},
  "collation": "mcel-text-v1"
}
```

or an explicit locale profile:

```json
{"collation": {"kind": "locale-collation", "locale": "en-US", "options": {"sensitivity": "base"}}}
```

### TL;DR

If an environmental value can change the answer, it must enter through an explicit, typed, evidenced boundary.

## 18. Core operators versus domain operators

The core expression set should remain small enough to specify and prove, but real applications need domain semantics.

Examples:

```text
Git Tools:
  git.ref-normalize
  git.branch-name-valid
  git.status-classify
  git.patch-summary

Code Editor:
  path.normalize-relative
  path.is-inside-root
  content.sha256
  text.apply-edits
  project.changed-file-set

Document Editor:
  document.region-by-id
  document.anchor-resolve
  document.selection-normalize
  document.export-plan
```

These should be registered **domain operators**, not arbitrary callbacks.

A domain operator specification must include:

```text
stable operator ID and version
parameter schemas
result schema
purity class
determinism class
totality/refusal behavior
semantic definition
normalization rules
reference implementation or projection requirements
conformance examples
proof interpretation
migration mappings from existing definitions
```

Example:

```json
{
  "kind": "domain.call",
  "operator": {"ref": "operator:path.is-inside-root@v1"},
  "arguments": {
    "root": {"kind": "state.read", "state": {"ref": "state:project-root"}},
    "path": {"kind": "input.read", "input": {"ref": "input:save-file.path"}}
  }
}
```

A domain operator cannot perform an effect. `git.push`, filesystem write, and document export remain capability operations.

Wave 2A represents registries as deterministic `mcel.domain-operator-registry.v1` records. Each entry has a stable base operator ID, explicit version, named parameter schemas, result schema, allowed expression contexts, totality, and fixed `pure`/`deterministic` classifications. Registry order does not affect its fingerprint. A call binds the exact version in a reference such as `operator:path.is-inside-root@v1`.

### TL;DR

Extend MCEL with versioned semantic operators, not opaque app-specific functions. Domain calculation is allowed; domain side effects remain capabilities.

## 19. Git Tools expression mapping

Git Tools tests whether the model handles governed operational applications.

### Preflight predicate

```json
{
  "kind": "boolean.and",
  "operands": [
    {"kind": "compare.equal", "left": {"kind": "state.read", "state": {"ref": "state:repository-status"}}, "right": {"kind": "constant", "value": "ready"}},
    {"kind": "domain.call", "operator": {"ref": "operator:git.ref-name-valid@v1"}, "arguments": {"value": {"kind": "input.read", "input": {"ref": "input:create-branch.name"}}}},
    {"kind": "compare.equal", "left": {"kind": "state.read", "state": {"ref": "state:repository-revision"}}, "right": {"kind": "input.read", "input": {"ref": "input:create-branch.expected-revision"}}}
  ]
}
```

### Confirmation requirement

Confirmation is not a boolean hidden inside the transition. It is an explicit intent/effect policy. An expression may evaluate the confirmation evidence:

```json
{
  "kind": "confirmation.matches",
  "confirmation": {"kind": "context.read", "context": {"ref": "context:review-confirmation"}},
  "intent": {"ref": "intent:create-branch"},
  "operationDigest": {"kind": "input.read", "input": {"ref": "input:create-branch.operation-digest"}}
}
```

### Receipt claim

```json
{
  "kind": "claim.receipt-field",
  "receipt": {"ref": "receipt:create-branch"},
  "field": "resultingRef",
  "expect": {"kind": "input.read", "input": {"ref": "input:create-branch.name"}}
}
```

### Migration requirement

Current Git Tools adapters and requirements may retain app-specific code during migration. Each callback or helper must be classified as:

```text
core expression
registered Git domain operator
capability effect
surface projection
proof claim
legacy opaque gap
```

### TL;DR

Git calculations become typed operators; Git mutations remain governed capabilities with confirmations, receipts, and recovery evidence.

## 20. Code Editor expression mapping

Code Editor tests draft/canonical separation, stale-source protection, and reviewed multi-file changes.

### Stale-source check

```json
{
  "kind": "compare.equal",
  "left": {"kind": "state.read", "state": {"ref": "state:loaded-file-hash"}},
  "right": {"kind": "input.read", "input": {"ref": "input:save-file.expected-hash"}}
}
```

### Path safety

```json
{
  "kind": "domain.call",
  "operator": {"ref": "operator:path.is-inside-root@v1"},
  "arguments": {
    "root": {"kind": "state.read", "state": {"ref": "state:project-root"}},
    "path": {"kind": "input.read", "input": {"ref": "input:save-file.path"}}
  }
}
```

### Project edit request

```json
{
  "kind": "record.construct",
  "schema": {"ref": "schema:ProjectEditTransaction"},
  "fields": {
    "root": {"kind": "state.read", "state": {"ref": "state:project-root"}},
    "operations": {"kind": "input.read", "input": {"ref": "input:apply-reviewed-edit.operations"}},
    "expectedHashes": {"kind": "input.read", "input": {"ref": "input:apply-reviewed-edit.expected-hashes"}}
  }
}
```

The expression constructs the reviewed request. The project-edit capability performs isolated validation, overlay creation, reviewed apply, receipt generation, and rollback handling.

### Draft state

Editor text remains local until a governed save/apply intent commits through the capability path. A surface expression may read local draft state; a canonical file record cannot be mutated by a local keystroke expression.

### TL;DR

The expression model preserves the difference between editing a draft and authorizing a filesystem mutation.

## 21. Document Editor expression mapping

Document Editor tests semantic regions, local selection, layout ownership, persistence, and export planning.

### Region lookup

```json
{
  "kind": "domain.call",
  "operator": {"ref": "operator:document.region-by-id@v1"},
  "arguments": {
    "document": {"kind": "state.read", "state": {"ref": "state:document"}},
    "regionId": {"kind": "state.read", "state": {"ref": "state:selected-region-id"}}
  }
}
```

### Local selection normalization

```json
{
  "kind": "domain.call",
  "operator": {"ref": "operator:document.selection-normalize@v1"},
  "arguments": {
    "selection": {"kind": "input.read", "input": {"ref": "input:update-selection.selection"}},
    "document": {"kind": "state.read", "state": {"ref": "state:document"}}
  }
}
```

### Export plan

```json
{
  "kind": "domain.call",
  "operator": {"ref": "operator:document.export-plan@v1"},
  "arguments": {
    "document": {"kind": "state.read", "state": {"ref": "state:document"}},
    "format": {"kind": "input.read", "input": {"ref": "input:export-document.format"}}
  }
}
```

The export plan is pure. Producing a PDF or writing a file is an export capability effect.

### TL;DR

Document semantics may be calculated in expressions. Persistence and export remain explicit effects with independent evidence.

## 22. Legacy opaque-function migration

Existing applications contain callbacks that cannot be eliminated in one pass. The migration system needs an explicit quarantine form.

Illustrative migration-only record:

```json
{
  "kind": "legacy.opaque-function",
  "language": "javascript",
  "sourceHash": "sha256:...",
  "declaredInputs": ["state:contracts", "state:filter-text", "state:sort-mode"],
  "declaredResult": {"ref": "schema:ContractList"},
  "declaredPurity": "pure",
  "migration": {
    "owner": "frontend:contract-workbench-v0",
    "replacementStatus": "required",
    "targetExpressionKinds": ["query.filter", "query.sort"]
  }
}
```

Rules:

1. It is allowed only in an importer or compatibility projection explicitly marked pre-v1.
2. It is never emitted by the official v1 DSL compiler.
3. It cannot be considered semantically equivalent to a constrained graph solely because declared reads/writes match.
4. Runtime and browser evidence may establish behavioral compatibility for tested scenarios, but not complete expression equivalence.
5. It must keep source hash, source provenance, declared inputs/result, and migration owner.
6. It blocks `dsl-v1` migration status for the affected semantic feature.
7. Retirement requires replacement by core or registered domain expressions plus renewed equivalence and proof evidence.

Possible migration comparison:

```text
legacy opaque callback vs constrained graph:
  source equality: no
  exact IR equality: no
  declared interface compatibility: possible
  scenario-observed behavioral compatibility: possible
  complete semantic equivalence: not yet established
```

### TL;DR

Opaque callbacks may survive temporarily as named migration debt. They cannot masquerade as final inspectable semantics.

## 23. Normalization rules

### 23.1 Kinds and fields

Expression kinds use stable lowercase dotted IDs. Unknown fields are rejected unless an extension schema explicitly permits them.

### 23.2 Types

Result types are normalized to schema references or canonical primitive schemas. Equivalent aliases normalize to one type.

### 23.3 Constants

Object keys are lexicographically ordered. Map entries are key-sorted unless order is semantic. `undefined` is omitted only where the schema defines absence; it is never normalized into `null` silently.

### 23.4 Commutative operators

Operands may be sorted only for kinds whose specification declares semantic commutativity and whose evaluation/partiality behavior remains equivalent.

For example, sorting operands of pure total `boolean.all` may be legal. Sorting short-circuit `boolean.and` operands is not automatically legal when later operands may refuse or be partial.

### 23.5 Associative flattening

Nested operators may be flattened only when the kind declares associativity under the normalized numeric/text semantics.

Floating-point reassociation is forbidden unless the numeric profile proves equivalence.

### 23.6 Local bindings

Binding names are not semantic identity. Normalization may alpha-rename lexical bindings deterministically while preserving source names in provenance.

### 23.7 Source data

Source spans and authored helper names are excluded from the semantic fingerprint and included in the source-binding fingerprint.

### 23.8 Derived analysis

Read/write, purity, determinism, and totality summaries are recomputed and excluded from semantic identity when fully derivable. A mismatch between stored analysis and recomputation is invalid IR.

### 23.9 Domain operators

Operator version is semantic. `operator:path.is-inside-root@v1` and `@v2` are not equivalent without an explicit compatibility rule.

### 23.10 Legacy opaque functions

Opaque source hashes are semantic for the migration record, but no normalization rule may claim two different function hashes are equivalent.

### TL;DR

Normalization removes incidental spelling and ordering differences without rewriting away evaluation order, refusal behavior, numeric meaning, or operator versions.

## 24. Static analysis requirements

Before runtime projection, the compiler must calculate and validate:

```text
result type
legal context
state reads
input reads
context reads
item-scope reads
capability event/result reads
canonical writes
provisional writes
purity
determinism
totality/refusal behavior
unreachable branches
unbound references
unused semantic inputs where policy treats them as errors
write-authority agreement
claimed effect ownership
```

Example diagnostic:

```text
MCEL_EXPR_UNDECLARED_CANONICAL_WRITE

source:
  mcel_apps/inventory/application.js:71:5

expression path:
  intent:rename-item.change.steps[1]

problem:
  expression writes state:revision
  intent declares writes [state:items]

safe repairs:
  declare state:revision as an owned write
  or remove the revision transition
```

Example purity diagnostic:

```text
MCEL_EXPR_AMBIENT_CALL_FORBIDDEN

source:
  mcel_apps/inventory/application.js:88:16

problem:
  Date.now() is not a constrained expression

safe repair:
  bind context:operation-time and read its instant
```

### TL;DR

The compiler should explain the violated semantic rule and the narrowest safe repair, not expose an internal JavaScript exception.

## 25. Expression-equivalence levels

Expression comparison supports these results:

### Exact

Normalized expression graphs and semantic fingerprints match.

### Semantically equivalent

Graphs differ but a versioned, documented rewrite rule proves the same result, refusal behavior, reads, writes, and effect boundary.

Example candidate:

```text
number.increment(target, 1)
≡
transition.assign(target, number.add(state.read(target), constant(1)))
```

This equivalence is legal only if the numeric profile and transition semantics match exactly.

### Intentional versioned delta

Meaning changed under an explicit application or operator version change.

### Incomplete

One side contains an unmapped expression, legacy opaque callback, or missing operator.

### Conflicting

The expressions differ in result, reads, writes, refusal, ordering, determinism, or effect boundary.

Scenario equality alone does not promote an incomplete comparison to complete semantic equivalence.

### TL;DR

Behavioral tests support migration, but inspectable expression equivalence is the authority for replacing one compiler with another.

## 26. Per-pass migration obligations

Every constrained-expression pass must check all three authored levels and current projections.

| Layer | Required question |
| --- | --- |
| Documentation | Is the semantic calculation, refusal, transition, or reconciliation still described correctly? |
| Current definition | Which callback/helper currently implements it? |
| Legacy compiler/importer | How is current behavior imported or quarantined? |
| Candidate DSL | How will the official source express the decision once? |
| IR | Which core/domain expression graph carries the meaning? |
| Low-level projection | Can current application/contracts still be produced? |
| Evidence | Which acceptance, browser, receipt, or effect evidence must be renewed? |
| Migration inventory | Which applications and definition families are affected? |

The pass result for every affected feature must be one of:

```text
exactly mapped
mapped through registered domain operator
legacy opaque gap retained
intentional versioned change
semantic conflict
not affected with reason
```

### TL;DR

Adding an expression kind is not complete until existing applications are mapped and no current meaning disappears in the reorganization.

## 27. Minimum v1 core vocabulary

The first implementation plan should cover at least these core families:

```text
Sources:
  constant
  state.read
  input.read
  item.read
  context.read
  transition.before.read
  transition.after.read
  capability.event.read
  capability.result.read
  scenario.output.read

Structure:
  let
  binding.read
  record.construct
  record.get
  record.set
  list.construct
  map.construct

Logic and scalar:
  conditional
  boolean.not
  boolean.and
  boolean.or
  compare.*
  number.*
  text.*
  optional.*

Queries:
  query.filter
  query.sort
  query.map
  query.find-by-key
  query.any
  query.every
  query.count
  query.sum
  query.average
  query.group-by
  query.distinct-by

Validation:
  refusal.when
  invariant.assert
  collection.keys-unique
  schema.valid

Canonical transitions:
  transition.assign
  transition.sequence
  list.append
  list.remove-by-key
  list.update-by-key
  map.put
  map.remove
  number.increment

Provisional reconciliation:
  provisional.get-by-key
  provisional.put-by-key
  provisional.remove-by-key
  provisional.current
  event.switch
  event.ignore

Surface:
  surface.text
  surface.property
  surface.visibility
  surface.status

Proof:
  claim.exists
  claim.equal
  claim.surface-row-exists
  claim.receipt-disposition
  claim.receipt-field
  claim.effect-accounted

Extension:
  domain.call
  legacy.opaque-function (migration only)
```

This is a minimum semantic capability list, not a commitment to final JavaScript helper names.

### TL;DR

The v1 core must express Counter, Workbench, and the common calculations around real application effects without becoming unrestricted JavaScript.

## 28. Acceptance criteria for this specification

This document is complete enough for the later implementation plan only when documentation can show:

1. Counter increment, reset, and direct-set refusal use constrained expressions without special-case callbacks.
2. Workbench visible filtering, sorting, total quantity, and `canSubmit` derivations are expressible.
3. Workbench add, remove, and update mutations expose reads, writes, refusals, transitions, and postconditions.
4. Workbench request-quote request construction, streamed reconciliation, average calculation, canonical commit, cancellation cleanup, and late-event disposition are expressible.
5. Git Tools calculations are divided into core expressions, registered Git operators, and governed capability effects.
6. Code Editor stale-hash, path-safety, request construction, and canonical reconciliation are expressible without treating filesystem mutation as a pure expression.
7. Document Editor region, selection, and export-plan calculations are expressible without treating persistence/export as pure expressions.
8. Every current opaque callback has a legal migration classification.
9. Static analysis can derive reads, writes, types, purity, determinism, and totality.
10. Normalization and equivalence rules do not erase consequential differences.
11. Proof claims bind to independent evidence authorities.
12. The future official vanilla-JavaScript syntax can construct every required graph without exposing raw IR JSON as the normal authoring surface.

### TL;DR

The model is ready only when it preserves current applications, supports the final DSL, and makes behavior more explainable than today’s callback hashes.

## 29. Completeness-review dispositions

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` fixes the initial v1 boundary:

```text
v1 has no user-defined expression macros
v1 permits core builders, compiler helpers, and versioned registered domain operators
normalized IR always carries the resolved type; diagnostics may derive a display form
`mcel-text-v1` is the deterministic default text profile
locale-sensitive behavior requires an explicit versioned profile
unknown operators and proof-claim kinds are rejected
v1 normalization uses only documented rewrites
```

The following remain future-version or implementation-strategy questions:

```text
additional numeric profiles beyond the initial corpus
additional locale/collation profiles
future bounded macro proposals
additional domain-operator registry packaging
additional proof-operator extension families
runtime evaluator versus generated-code optimization
schema-version evolution and cross-version equivalence
```

These deferred questions do not reopen the fixed constraints:

```text
one official vanilla-JavaScript syntax
one declaration per independent semantic decision
no ambient or hidden consequential behavior
capabilities own effects
opaque functions are migration debt, not DSL v1
all current application-definition families must remain mapped
```

## Semantic change impact

Expression-node changes are compared through the typed dependency and evidence-impact rules in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`. Inspectable expression dependencies permit narrow impact closure; `legacy.opaque-function` regions widen the affected boundary and block narrow reuse unless declared boundaries prove independence.

### TL;DR

Inspectable expressions enable precise impact analysis; opaque callbacks force conservative renewal.

## Benchmark obligation

The expression model is exercised by the invalid-callback, transition-change, derived-query, capability-request, stale-save, Git-domain, and Document Editor export cases in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`. A benchmark success may not rely on application-specific opaque callbacks or hidden JavaScript escape paths.

### TL;DR

The benchmark must show that constrained expressions are both expressive enough and easier for an AI to repair than opaque callbacks.

## Final rule

> Every calculation that can change application truth, visible behavior, refusal, lifecycle reconciliation, or proof must have an inspectable typed expression or a versioned registered operator. Every consequential external action must remain an explicit capability effect. No opaque callback may become the hidden semantic foundation of MCEL DSL v1.
