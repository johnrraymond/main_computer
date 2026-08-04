# MCEL AI Authoring Pattern Catalog
## Example-First Patterns for `mcel.dsl.v1`, Migration, and Proof

## Status

This catalog gives an AI task-oriented examples for recurring MCEL application decisions. Every pattern connects:

```text
user request
-> independent semantic decisions
-> official DSL shape
-> compiler-generated plumbing
-> proof obligations
-> common diagnostic
-> migration impact
```

It is a documentation specification. The examples use the official DSL from `pretty_docs/mcel-official-vanilla-javascript-dsl.md`. A Counter-only Wave 2B compiler now exists, but the broader patterns in this catalog remain target contracts until their builders, importers, projections, and evidence paths are implemented and proven.

Use this catalog with `pretty_docs/mcel-ai-application-authoring-cycle.md`. The cycle says where the AI is. This catalog shows how common decisions are expressed at that stage.

## How to use a pattern

1. Match the user request to the nearest pattern.
2. Copy the semantic shape, not application-specific names.
3. Identify every independent decision listed by the pattern.
4. Check whether an existing application already represents the feature through a legacy or explicit path.
5. Declare the DSL candidate without deleting the current authority.
6. Compare through the IR.
7. Run the required acceptance, observation, effect, and proof obligations.

### TL;DR

Patterns accelerate authoring; they do not authorize copying hidden policy or skipping migration checks.

# Pattern 1: canonical scalar with governed mutation

## User request

```text
Show a count. Increment it. Reset it. Do not allow arbitrary direct assignment.
```

## Independent decisions

```text
count is canonical
count is a nonnegative integer
increment owns +1
reset owns assignment to zero
direct-set is prohibited
visible count must agree with canonical count
```

## DSL shape

```javascript
const count = state.canonical(
  "count",
  field.integer().minimum(0),
  {initial: 0}
);

const increment = intent.mutation("increment", {
  change: () => [count.increment(1)]
});

const reset = intent.mutation("reset", {
  change: () => [count.set(0)]
});

const directSet = intent.prohibited("direct-set", {
  reason: "Direct assignment bypasses declared intents."
});
```

## Compiler may generate

```text
SCM operation contracts
revision protection
receipts
surface action bindings
prohibited-effect nonoccurrence checks
intent coverage mapping
```

## Proof obligations

```text
increment commits count = prior + 1
reset commits count = 0
direct-set is refused
no effect occurs for direct-set
visible count agrees with canonical count
```

## Common diagnostic

```text
MCEL_CANONICAL_WRITE_OUTSIDE_INTENT
```

## Migration note

This is the Contract Counter baseline. A DSL migration must preserve increment, reset, prohibition, visible projection, and proof—not merely the final number.

### TL;DR

Canonical state changes only through named intents; prohibition is a semantic feature.

# Pattern 2: renderer-local form draft feeding a mutation

## User request

```text
Let the user type a name and quantity, then add an item.
```

## Independent decisions

```text
form drafts are mount-local
name is required text
quantity is an integer >= 1
add-item owns the canonical append
successful commit may clear the drafts
refusal may retain the drafts
```

## DSL shape

```javascript
const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(field.text().minLength(1)),
    quantity: input.control(field.integer().minimum(1))
  },
  change: ({input, id}) => [
    items.append({
      id: id.next("item"),
      name: input.name,
      quantity: input.quantity
    })
  ]
});

const addForm = surface.form("add-item", {
  intent: addItem,
  fields: {
    name: surface.textInput({label: "Item name"}),
    quantity: surface.numberInput({label: "Quantity", step: 1})
  }
});
```

## Compiler may generate

```text
local draft state
input property bindings
integer parser
payload construction
validation messages
submit binding
browser locators
```

## Proof obligations

```text
valid input commits one canonical item
invalid quantity is refused
refusal does not change canonical items
retained or cleared drafts follow the declared policy
visible row matches committed state
```

## Common diagnostic

```text
MCEL_INTENT_INPUT_SOURCE_AMBIGUOUS
```

### TL;DR

Declare schema, source, transition, and draft disposition; generate ordinary form plumbing.

# Pattern 3: keyed CRUD collection

## User request

```text
Show all items. Remove the item whose row button is clicked.
```

## Independent decisions

```text
Item.id is stable identity
row order is presentation, not identity
remove-item receives the current row key
remove-item owns canonical removal
```

## DSL shape

```javascript
const removeItem = intent.mutation("remove-item", {
  input: {
    itemId: input.itemKey(items, Item.id)
  },
  change: ({input}) => [
    items.removeByKey(input.itemId)
  ]
});

const itemList = surface.collection("items", {
  source: items,
  key: Item.id,
  row: (item) => [
    surface.text("name", {value: item.name}),
    surface.action("remove", {intent: removeItem})
  ]
});
```

## Compiler may generate

```text
current-row key extraction
payload binding
keyed reconciliation
row-local semantic locators
row-bound browser actions
```

## Proof obligations

```text
selected key is removed
other keys remain
reordering does not retarget row actions
visible row disappears
```

## Common diagnostic

```text
MCEL_COLLECTION_KEY_REQUIRED
```

### TL;DR

Declare stable identity once; generate row-key plumbing everywhere else.

# Pattern 4: derived search, filter, and sort

## User request

```text
Search items by name or SKU and sort matching items by name.
```

## Independent decisions

```text
search text is local
items remain canonical
visibleItems is derived
search fields are Item.name and Item.sku
sort order is ascending Item.name
```

## DSL shape

```javascript
const search = state.local("search", field.text(), {initial: ""});

const visibleItems = state.derived(
  "visible-items",
  field.list(Item),
  {
    from: [items, search],
    compute: ({read, query, expr}) => query
      .from(read(items))
      .where((item) => expr.or(
        expr.isBlank(read(search)),
        expr.contains(item.name, read(search), {case: "insensitive"}),
        expr.contains(item.sku, read(search), {case: "insensitive"})
      ))
      .orderBy(query.asc(Item.name), query.asc(Item.id))
  }
);
```

## Compiler may generate

```text
dependency graph
recomputation scheduling
surface updates
filter/sort observation steps
```

## Proof obligations

```text
canonical items remain unchanged
search changes only this mounted instance
matching set is correct
sort order is correct
clearing search restores all rows
```

## Common diagnostic

```text
MCEL_DERIVATION_DEPENDENCY_UNDECLARED
```

### TL;DR

Derived state describes a pure inspectable query over declared dependencies.

# Pattern 5: structured refusal with canonical nonchange

## User request

```text
Do not add an item with a blank name or invalid quantity.
```

## Independent decisions

```text
refusal codes
refusal predicates
canonical nonchange
visible validation or receipt behavior
whether local drafts are retained
```

## DSL shape

```javascript
refuses: ({input, refuse, expr}) => [
  refuse.when(
    expr.isBlank(input.name),
    "ITEM_NAME_REQUIRED",
    "An item name is required."
  ),
  refuse.when(
    expr.lessThan(input.quantity, 1),
    "ITEM_QUANTITY_INVALID",
    "Quantity must be at least one."
  )
]
```

## Proof obligations

```text
receipt contains the declared refusal code
canonical state is unchanged
no prohibited external effect occurs
draft retention or cleanup matches policy
visible message is associated with the correct control or receipt
```

## Common diagnostic

```text
MCEL_REFUSAL_WITHOUT_NONCHANGE_CLAIM
```

### TL;DR

A refusal is an expected semantic outcome, not an exception.

# Pattern 6: clear-all or other multi-record mutation

## User request

```text
Remove all items after the user invokes Clear All.
```

## Independent decisions

```text
which canonical collections are cleared
whether confirmation is required
which revision changes
what happens to item-keyed provisional operations
```

## DSL shape

```javascript
const clearAll = intent.mutation("clear-all", {
  authority: {writes: [items, revision]},
  change: () => [
    items.set([]),
    revision.increment(1)
  ]
});
```

When active item-keyed operations exist, effect policy must also state whether they are cancelled, allowed to finish without commit authority, or block the mutation.

## Proof obligations

```text
all canonical items are removed
revision changes once
visible collection is empty
active effect dispositions are explained
```

## Common diagnostic

```text
MCEL_MUTATION_ORPHANS_ACTIVE_EFFECT
```

### TL;DR

Bulk state changes must account for the lifecycles attached to the records they remove.

# Pattern 7: async capability with provisional progress

## User request

```text
Request supplier quotes, show progress, then commit the final average quote.
```

## Independent decisions

```text
capability operation
operation key
request fields
provisional state schema
stream event reconciliation
final result calculation
canonical commit
allowed dispositions
cleanup
```

## DSL shape

```javascript
const requestQuote = intent.capability("request-quote", {
  input: {
    itemId: input.itemKey(items, Item.id)
  },
  use: QuoteService.requestQuote,
  operationKey: ({input}) => input.itemId,
  request: ({read, input}) => dsl.request({
    itemId: input.itemId,
    quantity: read(items).byKey(input.itemId).field(Item.quantity)
  }),
  provisional: {
    state: quoteProgress,
    initial: ({input}) => ({key: input.itemId, reports: [], failures: []}),
    receive: ({current, event, expr}) => expr.match(event.type, {
      "quote.received": current.merge({
        reports: expr.append(current.reports, event.report)
      }),
      "quote.failed": current.merge({
        failures: expr.append(current.failures, event)
      })
    })
  },
  commit: ({input, provisional, expr}) => [
    items.updateByKey(input.itemId, (item) => item.merge({
      quote: expr.averageInteger(provisional.reports.map(QuoteReport.amount))
    }))
  ],
  effect: {
    allowedDispositions: ["committed", "cancelled", "superseded", "failed"],
    cleanup: dsl.cleanup.removeKey(quoteProgress, ({input}) => input.itemId)
  }
});
```

## Compiler may generate

```text
active operation registry
stream subscription
provisional ownership
effect IDs
receipt/evidence correlation
cleanup checks
```

## Proof obligations

```text
progress appears before commit
canonical quote changes only at commit
every event belongs to the correct operation
final value is calculated correctly
provisional state closes
```

## Common diagnostic

```text
MCEL_EFFECT_OPERATION_KEY_REQUIRED
```

### TL;DR

An async intent is one declared lifecycle, not a hidden promise callback.

# Pattern 8: cancellation and latest-per-key supersession

## User request

```text
Let the user cancel a quote. If they request again for the same item, only the newest request may commit.
```

## Independent decisions

```text
cancellation is allowed
cancel intent targets requestQuote
operation identity is itemId
same-key concurrency is latest-per-key
older result loses commit authority
late events are rejected
cleanup closes both cancelled and superseded operations
```

## DSL shape

```javascript
requestQuote = intent.capability("request-quote", {
  // ...
  operationKey: ({input}) => input.itemId,
  concurrency: dsl.concurrency.latestPerKey(),
  cancellation: dsl.cancellation.allowed(),
  effect: {
    allowedDispositions: ["committed", "cancelled", "superseded", "failed"],
    cleanup: dsl.cleanup.removeKey(quoteProgress, ({input}) => input.itemId)
  }
});

const cancelQuote = intent.cancel("cancel-quote", {
  target: requestQuote,
  key: input.itemKey(items, Item.id)
});
```

## Proof obligations

```text
cancel closes the targeted active operation
canonical item remains unchanged after cancellation
new same-key operation supersedes the old one
old completion cannot commit
late events are observed as rejected or ignored by declared policy
provisional state is clean
```

## Common diagnostic

```text
MCEL_LATE_RESULT_COMMIT_AUTHORITY_UNEXPLAINED
```

### TL;DR

Supersession proves loss of authority, not merely that the newer result appeared last.

# Pattern 9: parallel per-item operations

## User request

```text
Request quotes for two different rows at the same time.
```

## Independent decisions

```text
operation key is row identity
concurrency restriction applies per key, not globally
provisional state is keyed
completion order may differ from start order
```

## Proof obligations

```text
both operations become active
each provisional record belongs to one item
completing B does not close A
canonical commits target the correct rows
all effect ledgers close independently
```

## Common diagnostic

```text
MCEL_EFFECT_TARGET_KEY_COLLISION
```

### TL;DR

Per-key concurrency keeps independent rows independent.

# Pattern 10: governed remote mutation with uncertainty and recovery

## User request

```text
Push a branch after preflight and confirmation, and recover if the network fails after the remote may have changed.
```

## Independent decisions

```text
repository identity
preflight predicates
confirmation scope
Git capability
remote-mutation risk
idempotency or correlation key
committed/refused/failed/indeterminate dispositions
recovery capability and closure conditions
receipt claims
```

## DSL shape

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
    refuse.unless(domain.git.refNameValid(input.branch), "GIT_BRANCH_INVALID")
  ],
  operationKey: ({input}) => input.repositoryId,
  effect: {
    risk: "remote-mutation",
    allowedDispositions: ["committed", "refused", "failed", "indeterminate"],
    recovery: dsl.recovery.required({
      when: "indeterminate",
      capability: GitService.inspectRemote,
      closesWith: ["committed", "failed"]
    })
  }
});
```

## Migration note

Git Tools migration must preserve requirements, semantic adapter rules, confirmation policy, receipts, and recovery—not only button behavior.

## Common diagnostic

```text
MCEL_INDETERMINATE_EFFECT_WITHOUT_RECOVERY
```

### TL;DR

Remote mutation remains incomplete until uncertainty is reconciled.

# Pattern 11: stale-safe file save with retained draft

## User request

```text
Save the editor draft only if the source file has not changed; keep the draft when save is refused or fails.
```

## Independent decisions

```text
draft is local
file identity and project root
loaded source hash
stale-source refusal
filesystem capability
operation key
retained draft dispositions
visible receipt
```

## DSL shape

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
  operationKey: ({input}) => input.fileId,
  effect: {
    risk: "filesystem-mutation",
    allowedDispositions: ["committed", "refused", "failed", "indeterminate"],
    retain: [
      dsl.retention.localState(draftContent, {
        when: ["refused", "failed", "indeterminate"]
      })
    ]
  }
});
```

## Proof obligations

```text
stale source refuses before write
canonical/external file does not change on refusal
draft remains visible
successful write updates the source binding
receipt and effect evidence agree
```

## Common diagnostic

```text
MCEL_RETAINED_STATE_WITHOUT_DISPOSITION
```

### TL;DR

Retained draft is an explained outcome, not leftover residue.

# Pattern 12: document export with retained artifact

## User request

```text
Export the active document as PDF and give the user the resulting artifact.
```

## Independent decisions

```text
active document identity
format
selection or export scope
pure export plan
export capability
artifact ownership
artifact receipt
retention policy
```

## DSL shape

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

## Proof obligations

```text
export request matches the declared document and format
artifact exists and has an evidence identity
artifact ownership transfers or remains as declared
visible receipt references the same artifact
failed export leaves no unexplained partial artifact
```

## Common diagnostic

```text
MCEL_ARTIFACT_RETENTION_UNOWNED
```

### TL;DR

Artifact creation is an effect with ownership and retention, not just a returned path.

# Pattern 13: multi-instance isolation

## User request

```text
Two mounted copies of the application must not leak canonical, local,
provisional, operation-ledger, receipt, or root ownership into each other.
```

## Independent decisions

```text
which authorities are instance-local
whether any canonical state is deliberately shared
mount identities are distinct
operation and receipt ledgers are instance-bound
surface roots do not cross-bind
```

## DSL proof shape

```javascript
const isolation = prove.scenario("multi-instance-isolation")
  .mount("left", primary)
  .mount("right", primary)
  .step("left-add", prove.invoke(addItem, {
    name: "Steel",
    quantity: 12
  }, {
    instance: "left"
  }))
  .expect(
    prove.instance("left").canonical(items).itemCount(1),
    prove.instance("right").canonical(items).itemCount(0),
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

When an application deliberately shares one authority, the scenario must declare that ownership model rather than copying this isolation list unchanged.

## Common diagnostic

```text
MCEL_INSTANCE_AUTHORITY_SCOPE_CONFLICT
```

### TL;DR

Instance ownership is explicit and browser-proven; two similar DOM trees are not proof of isolation.

# Pattern 14: cross-intent scenario

## User request

```text
Add two items, remove one, then clear the remaining collection.
```

This behavior belongs in a top-level scenario because it crosses several intents and cumulative state transitions.

## Proof shape

```javascript
const addRemoveClear = prove.scenario("add-remove-clear")
  .given(
    prove.operation(addItem, {name: "Steel", quantity: 12}),
    prove.operation(addItem, {name: "Copper", quantity: 8})
  )
  .step(
    "remove-steel",
    prove.invoke(removeItem, {itemId: "item-steel"}, {through: primary})
  )
  .step(
    "clear-rest",
    prove.invoke(clearAll, {}, {through: primary})
  )
  .expect(
    prove.canonical(items).equals([]),
    prove.visible(primary.collection("items")).rowCount(0),
    prove.effects().noUnexplainedResidue()
  );
```

## Common diagnostic

```text
MCEL_SCENARIO_CLAIM_AUTHORITY_MISSING
```

### TL;DR

Keep intent-local examples close to their intent; use top-level scenarios for interactions across intents, authorities, effects, or mounts.

# Pattern 15: adding a field to an existing application

## User request

```text
Add optional priority, display it in rows, and support filtering by priority.
```

## Independent decisions

```text
model schema and default
intent input source
canonical transition
surface control
row projection
derived filter behavior
proof claims
legacy/DSL compatibility mapping when migrating
```

## Correct edit shape

```javascript
const Item = dsl.model("item", {
  // existing fields
  priority: field.enum("low", "normal", "high").default("normal")
});
```

```javascript
input: {
  // existing fields
  priority: input.control(Item.priority)
}
```

```javascript
change: ({input, id}) => [
  items.append({
    // existing fields
    priority: input.priority
  })
]
```

```javascript
surface.text("priority", {value: item.priority})
```

## What should not be edited manually

```text
generated input parser
generated payload adapter
generated package contract
generated browser locator
generated intent coverage map
```

## Proof impact

At minimum, renew creation, row rendering, filtering, generated projection identity, and compatibility evidence. Exact reuse and renewal rules are defined in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`.

### TL;DR

A feature may require several edits because it changes several independent decisions; it should not require repeated plumbing edits.

# Pattern selection guide

| User wording | Start with pattern |
| --- | --- |
| “increment,” “reset,” “toggle a shared value” | Canonical scalar mutation |
| “enter fields and submit” | Local form draft feeding mutation |
| “remove/edit this row” | Keyed CRUD collection |
| “search/filter/sort” | Derived query |
| “reject invalid input” | Structured refusal |
| “delete everything” | Bulk mutation and active-effect accounting |
| “show progress while waiting” | Async capability with provisional progress |
| “cancel” or “only latest result wins” | Cancellation and supersession |
| “run on two rows at once” | Parallel per-item operations |
| “push/publish/deploy” | Governed remote mutation |
| “save this file safely” | Stale-safe file save |
| “export/download artifact” | Document export and artifact retention |
| “two windows should differ” | Multi-instance isolation |
| “perform a workflow of several actions” | Cross-intent scenario |
| “add a field to an existing app” | Semantic feature modification |

## Pattern completeness rule

A new catalog pattern is complete only when it includes:

```text
user request
independent semantic decisions
official DSL shape
compiler-generated mechanics
proof obligations
common diagnostic
migration consequences for affected existing apps
TL;DR
```

A pattern that shows only syntax is a snippet, not an MCEL authoring pattern.

### TL;DR

The catalog teaches meaning, generation, repair, migration, and proof together.

## Benchmark use

The creation, modification, repair, and migration tasks in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` draw from these patterns but include held-out variants so an implementation cannot pass by memorizing the examples.

### TL;DR

Patterns teach the semantic move; the benchmark tests whether the AI can generalize it.

