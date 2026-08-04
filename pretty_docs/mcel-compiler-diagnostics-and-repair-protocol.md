# MCEL Compiler Diagnostics and Repair Protocol
## Stable, Stage-Aware Errors and Safe AI Re-entry for `mcel.dsl.v1`

## Status

This document specifies how MCEL compiler front ends, IR validation, projection, migration comparison, acceptance preparation, observation preparation, and proof reconciliation report failures to an AI author and describe safe ways to resume the application-authoring cycle.

It is a documentation specification. It does not authorize a diagnostics engine, automatic source mutation, DSL compiler, repair executor, generated-file rewrite, evidence reuse implementation, application migration, or retirement of any existing diagnostic path.

Read this with:

- `pretty_docs/mcel-ai-authoring-semantic-boundary.md`;
- `pretty_docs/mcel-application-ir-and-compiler-migration.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`;
- `pretty_docs/mcel-constrained-expression-model.md`;
- `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`;
- `pretty_docs/mcel-official-vanilla-javascript-dsl.md`;
- `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`;
- `pretty_docs/mcel-ai-application-authoring-cycle.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`.

## The short answer

An AI-friendly compiler error must answer seven questions:

```text
What failed?
Where is the authored decision?
Which semantic rule was violated?
Why does it matter?
Which repairs are safe?
Which evidence is no longer usable for the candidate?
At which authoring stage should the AI resume?
```

A useful diagnostic looks like this:

```text
MCEL_COLLECTION_KEY_REQUIRED

stage:
  surface

source:
  mcel_apps/inventory/application.js:84:5

semantic path:
  surface:inventory.items

problem:
  a rendered collection has no declared stable item identity

available compatible fields:
  field:Item.id
  field:Item.sku

safe repairs:
  choose `key: Item.id`
  or choose `key: Item.sku`

choice required:
  yes — the compiler cannot decide application identity

invalidated candidate outputs:
  surface projection
  row-bound intent bindings
  item-keyed provisional ownership
  browser scenarios that address a row

resume at:
  surface
```

It must not look like this:

```text
TypeError: Cannot read properties of undefined (reading 'id')
```

### TL;DR

A diagnostic is a machine-readable repair instruction, not merely an exception message.

## 1. Diagnostics are part of the authoring language

The official DSL is not complete when an AI can produce a correct first draft. It is complete only when the AI can reliably repair an incorrect draft without guessing which semantic decision MCEL rejected.

### Bad cycle

```text
write application
-> compiler crashes
-> inspect stack trace
-> guess at internal implementation
-> edit several files
-> rerun everything
```

### Required cycle

```text
write one semantic decision
-> receive stable diagnostic
-> return to the earliest affected authoring stage
-> apply one safe repair transaction
-> run the narrowest sufficient validation
-> renew only invalidated projections and evidence
-> continue
```

### TL;DR

Error behavior is part of the DSL’s public contract because repair is part of AI application development.

## 2. The authoring stages

Every blocking diagnostic names the earliest stage at which its cause can be repaired.

| Stage ID | Stage | AI is deciding | Typical failures |
| --- | --- | --- | --- |
| `requirements` | Requirements | User-visible behavior, constraints, authority expectations | Material ambiguity, contradictory requirements |
| `model` | Models and state | Schemas, authority, identity, initialization | Missing authority, unstable identity, invalid default |
| `intent` | Intents and transitions | Inputs, reads, writes, refusals, invariants | Undeclared write, missing refusal, invalid input source |
| `effect` | Capabilities and effects | Effect ownership, operation keys, dispositions, cleanup | Unowned effect, missing recovery, ambiguous target |
| `surface` | Semantic surface | Controls, visible values, collections, bindings | Missing collection key, unbound control, hidden authority guess |
| `layout` | Layout | Placement and ownership without semantic redefinition | Duplicate owner, impossible containment |
| `scenario` | Claims and scenarios | Independent expected outcomes and interactions | Self-confirming claim, uncovered intent, missing effect claim |
| `compile` | Source construction | Valid DSL graph and module composition | Invalid helper use, dynamic import, duplicate semantic ID |
| `normalize` | Canonical IR | Deterministic IDs, references, ordering, types | Unresolved reference, nondeterministic value, cycle |
| `project` | Low-level projections | Generated explicit definitions and package contracts | Projection mismatch, generated-file drift |
| `migrate` | Multi-level compatibility | Legacy, explicit, IR, and DSL equivalence | Conflicting semantics, unmapped legacy behavior |
| `acceptance` | Contract evidence | Enforceable semantic outcomes | Failed contract or stale evidence binding |
| `observe` | Browser/runtime evidence | Independent visible and lifecycle observations | Missing scenario, ambiguous locator, runtime divergence |
| `prove` | Reconciliation | Complete truth and effect accounting | Unexplained effect, contradictory evidence, incomplete proof |

A diagnostic raised at `prove` may identify an earlier `repairStage` such as `effect` or `scenario`.

### Example

```text
reported stage:
  prove

root repair stage:
  effect

reason:
  request-quote produced a capability result with no declared cleanup disposition
```

### TL;DR

Report where the failure was detected, but send the AI back to where the missing decision belongs.

## 3. Diagnostic envelope

Every compiler-facing diagnostic normalizes into `mcel.compiler-diagnostic.v1`.

```json
{
  "schema": "mcel.compiler-diagnostic.v1",
  "code": "MCEL_COLLECTION_KEY_REQUIRED",
  "ruleVersion": "mcel.surface.collection-key.v1",
  "severity": "error",
  "blocking": true,
  "stage": "surface",
  "repairStage": "surface",
  "appId": "inventory",
  "semanticPath": "surface:inventory.items",
  "summary": "Rendered collection requires stable item identity.",
  "problem": "No key expression is declared for the item collection.",
  "source": {
    "kind": "authored-source",
    "file": "mcel_apps/inventory/application.js",
    "startLine": 84,
    "startColumn": 5,
    "endLine": 91,
    "endColumn": 6
  },
  "observed": null,
  "expected": {
    "kind": "stable-scalar-field-reference"
  },
  "relatedSemanticIds": [
    "state:items",
    "model:Item"
  ],
  "safeRepairs": [],
  "invalidations": [],
  "rerun": [],
  "migrationImpact": null,
  "diagnosticKey": "sha256:..."
}
```

Required fields are:

```text
schema
code
ruleVersion
severity
blocking
stage
repairStage
appId
semanticPath
summary
problem
source
safeRepairs
invalidations
rerun
diagnosticKey
```

Optional explanatory fields may add observed values, expected forms, related IDs, evidence references, migration impact, and supporting notes.

### TL;DR

Every tool may format diagnostics differently for humans, but all tools must expose one stable machine contract.

## 4. Stable diagnostic identity

A diagnostic code names a rule family. A diagnostic key identifies one current violation.

### Code

```text
MCEL_COLLECTION_KEY_REQUIRED
```

The code remains stable when wording or source line numbers change.

### Diagnostic key

The deterministic key is calculated from:

```text
rule version
application ID
semantic path
normalized violation class
relevant semantic references
```

It excludes:

```text
human wording
source line numbers
timestamps
stack traces
compiler process IDs
output ordering
```

### Why source lines are excluded

Moving a declaration from line 84 to line 88 should not create a new semantic violation.

### Why semantic path is included

Two separate collections missing keys are separate repair obligations even though they share one code.

### TL;DR

Codes classify rules. Deterministic keys let an AI track whether a particular violation was repaired, moved, or duplicated.

## 5. Diagnostic code namespaces

Codes use stable uppercase namespaces.

| Prefix | Meaning |
| --- | --- |
| `MCEL_REQUIREMENT_` | Requirements ambiguity or contradiction |
| `MCEL_MODEL_` | Model, schema, state authority, or identity |
| `MCEL_INTENT_` | Intent, input, refusal, read, write, or invariant |
| `MCEL_EXPR_` | Constrained expression construction or typing |
| `MCEL_EFFECT_` | Effect ownership, lifecycle, evidence, cleanup, or recovery |
| `MCEL_SURFACE_` | Semantic surface and binding |
| `MCEL_LAYOUT_` | Layout ownership and containment |
| `MCEL_SCENARIO_` | Scenario and independent claim definition |
| `MCEL_DSL_` | Official source syntax and module composition |
| `MCEL_IR_` | Canonical IR reference, normalization, determinism, or equivalence |
| `MCEL_PROJECTION_` | Low-level generated representation |
| `MCEL_MIGRATION_` | Legacy/DSL mapping and compatibility |
| `MCEL_ACCEPTANCE_` | Acceptance contract or evidence |
| `MCEL_OBSERVATION_` | Runtime/browser observation |
| `MCEL_PROOF_` | Final reconciliation and truth-gate completeness |
| `MCEL_REPAIR_` | Repair-plan validation and application |

Codes name the violated semantic rule, not the internal function that noticed it.

### Wrong

```text
MCEL_NORMALIZER_VISIT_OBJECT_FAILURE
```

### Required

```text
MCEL_IR_REFERENCE_UNRESOLVED
```

### TL;DR

An AI should reason about application semantics, not compiler implementation details.

## 6. Severity and blocking behavior

Supported severities are:

```text
info
warning
error
fatal
```

Blocking is separate from severity.

### Examples

```text
warning + nonblocking:
  generated label uses deterministic default

warning + blocking:
  legacy opaque callback prevents DSL-v1 equivalence

error + blocking:
  canonical write is outside the intent write set

fatal + blocking:
  compiler cannot establish deterministic application identity
```

`blocking: false` must never mean “safe to ignore forever.” It means the current requested stage may proceed under an explicitly documented rule.

### TL;DR

Severity describes seriousness. Blocking states whether the requested authoring or proof transition is allowed.

## 7. Source provenance

A diagnostic must point to the highest authored source that can repair the problem.

### Official DSL source

```text
source kind:
  authored-source

file:
  mcel_apps/inventory/application.js
```

### Requirements-driven legacy application

```text
source kind:
  requirements-source

file:
  pretty_docs/mcel-git-tools-requirements.md

source binding:
  intent:git.push-governed
```

### Legacy adapter with no precise source span

```text
source kind:
  legacy-source-binding

file:
  main_computer/web/applications/scripts/mcel-git-tools-semantic-adapter.js

symbol:
  preparePush

precision:
  symbol
```

### Generated projection failure

The diagnostic still points to authored source and separately names the generated artifact:

```text
repair source:
  mcel_apps/inventory/application.js

generated conflict:
  mcel_apps/inventory/generated/application.definition.js
```

The AI should not be instructed to repair a generated file.

### TL;DR

Point to the source of authority, not merely the file in which a generated consequence became visible.

## 8. Semantic paths

Every diagnostic includes a stable semantic path.

Examples:

```text
model:Item.field:quantity
state:items
intent:add-item.input:quantity
intent:add-item.transition:0
intent:request-quote.effect:request
surface:inventory.items
scenario:add-item.valid.claim:visible-row
migration:git-tools.intent:push-governed
```

Semantic paths are designed for:

```text
diagnostic grouping
AI source navigation
migration comparison
repair dependency ordering
proof traceability
stable references after formatting changes
```

A semantic path must not depend on an array index when the object has a stable semantic ID.

### Wrong

```text
intents[3].effects[0]
```

### Required

```text
intent:request-quote.effect:request
```

### TL;DR

Semantic paths survive source movement and make diagnostics comparable across compiler front ends.

## 9. Safe repair classes

Every proposed repair has one class.

### `mechanical`

The repair restores a relationship already determined by authored semantics.

Example:

```text
regenerate a missing low-level projection
```

This may eventually be automatically applicable.

### `deterministic-default`

A documented default exists and applying it introduces no new semantic choice.

Example:

```text
insert the default empty `scenarios: []` collection in normalized output
```

This may eventually be automatically applicable when the source policy permits it.

### `semantic-choice`

The repair requires an independent application decision.

Example:

```text
choose Item.id or Item.sku as collection identity
```

MCEL must not choose automatically.

### `semantic-rewrite`

Opaque or invalid behavior must be restated in constrained semantics.

Example:

```text
replace Date.now() ID allocation with dsl.id.next("item")
```

### `migration-mapping`

The current and future authoring levels need an explicit compatibility record.

Example:

```text
map legacy preparePush confirmation behavior to effect:git.push.confirmation
```

### `evidence-renewal`

The application meaning may already be valid, but evidence bound to an earlier fingerprint cannot prove the candidate.

Example:

```text
rerun the request-quote supersession browser scenario
```

### `recovery-decision`

External truth is uncertain and requires an explicit recovery or reconciliation path.

Example:

```text
reconcile whether a Git push reached the remote before retrying
```

### TL;DR

MCEL may automate plumbing. It must not automate an authority, identity, lifecycle, recovery, or proof decision.

## 10. Safe repair record

Each repair option uses `mcel.compiler-repair-option.v1`.

```json
{
  "schema": "mcel.compiler-repair-option.v1",
  "repairId": "choose-item-id-key",
  "class": "semantic-choice",
  "title": "Use Item.id as collection identity",
  "description": "Bind the collection key to the stable Item.id field.",
  "changes": [
    {
      "kind": "authored-source-edit",
      "semanticPath": "surface:inventory.items",
      "preview": "key: Item.id"
    }
  ],
  "preconditions": [
    "field:Item.id is stable",
    "field:Item.id is scalar",
    "field:Item.id is unique in state:items"
  ],
  "semanticConsequences": [
    "row identity uses Item.id",
    "row-bound operations receive Item.id"
  ],
  "invalidations": [
    "projection:surface",
    "evidence:browser:item-addressing"
  ],
  "automatic": false
}
```

A repair option cannot claim to be safe unless its semantic consequences are stated.

### TL;DR

A repair is not just replacement text. It declares why the change is legal and what meaning it introduces.

## 11. Repairs that MCEL must never apply automatically

The following always require an authored decision or approved policy:

```text
canonical versus local authority
stable item identity
intent mutation ownership
capability selection
external target selection
confirmation scope
cancellation policy
concurrency policy
retry policy
recovery after uncertain external mutation
retained artifact policy
proof outcome claims
migration conflict resolution
```

### Example: missing authority

Bad automatic repair:

```javascript
const search = state.local.text("search", "");
```

when the source merely said:

```javascript
const search = state.text("search", "");
```

The compiler cannot know whether search is mount-local, canonical, URL-owned, or user-preference state.

Required diagnostic:

```text
MCEL_MODEL_STATE_AUTHORITY_REQUIRED

safe repairs:
  declare state.local(...)
  declare state.canonical(...)
  declare a supported external authority

automatic:
  false
```

### TL;DR

Missing semantic decisions become visible choices, not compiler guesses.

## 12. Repair dependency ordering

Diagnostics form a dependency graph.

Example:

```text
A: Item.id is not declared stable
B: collection cannot use Item.id as key
C: row action cannot derive itemId
D: browser scenario cannot address the intended row
```

The AI must repair `A` before `B`, then recompile before deciding whether `C` and `D` remain.

The report includes:

```json
{
  "blockedBy": ["diagnostic-key:A"],
  "supersedesWhenRepaired": [
    "diagnostic-key:B",
    "diagnostic-key:C",
    "diagnostic-key:D"
  ]
}
```

Downstream diagnostics may be marked `cascading: true`.

### TL;DR

Repair the earliest semantic cause, then regenerate downstream diagnostics instead of editing every symptom.

## 13. Candidate truth versus last proven truth

A failed candidate does not silently replace the last proven application.

MCEL tracks:

```text
last proven semantic fingerprint
candidate semantic fingerprint
candidate source-binding fingerprint
candidate diagnostic set
```

### Example

```text
last proven:
  semantic fingerprint: sha256:AAA
  truth: semantic-runtime-proven

edited candidate:
  semantic fingerprint: unavailable
  blocking diagnostic: MCEL_EFFECT_OWNER_REQUIRED

result:
  deployed/proven application remains AAA
  candidate is not proof eligible
  evidence for AAA remains evidence for AAA
  evidence for AAA must not be presented as evidence for the candidate
```

A compile failure does not make old evidence false. It makes it inapplicable to the uncompiled candidate.

### TL;DR

Keep the last proven app intact, but never lend its evidence to a broken or semantically different candidate.

## 14. Evidence invalidation

A diagnostic or repair record must state what evidence is invalid for the candidate.

### Semantic change

Adding a new canonical field to `add-item` may invalidate:

```text
normalized IR
low-level application projection
intent contract
surface projection
add-item acceptance evidence
add-item browser evidence
intent-complete proof
repository binding
```

It may leave `remove-item` behavior semantically unaffected, but the repository-bound proof still needs a new binding.

### Source-only change with identical semantics

Reformatting the DSL may preserve the semantic fingerprint but change the source-binding fingerprint.

Possible result:

```text
semantic equivalence:
  exact

runtime behavior evidence:
  potentially reusable only under explicit equivalence policy

source provenance and repository-bound proof:
  renew
```

No evidence is reused merely because the compiler says “probably equivalent.” Reuse requires a documented equivalence class and evidence policy.

### Failed candidate before normalization

```text
semantic fingerprint:
  unavailable

candidate evidence eligibility:
  none
```

### TL;DR

Invalidation follows semantic dependency and evidence binding, not file count or intuition.

## 15. Invalidation record

Each diagnostic and repair option may emit `mcel.evidence-invalidation.v1` entries.

```json
{
  "schema": "mcel.evidence-invalidation.v1",
  "targetKind": "browser-scenario",
  "targetId": "contract-workbench.acceptance.quote-supersession",
  "reasonCode": "SEMANTIC_DEPENDENCY_CHANGED",
  "changedSemanticIds": [
    "intent:request-quote",
    "effect:request-quote.request"
  ],
  "previousSemanticFingerprint": "sha256:AAA",
  "candidateSemanticFingerprint": "sha256:BBB",
  "requiredAction": "rerun",
  "reusable": false
}
```

Supported required actions are:

```text
none
regenerate
revalidate
rerun
reobserve
reconcile
manual-review
```

### TL;DR

The compiler should tell the AI exactly which generated artifacts and evidence must be renewed and why.

## 16. Narrowest sufficient rerun

A diagnostic provides ordered rerun commands or abstract validation actions.

Example:

```text
repair stage:
  surface

rerun:
  1. compile application source
  2. validate canonical IR
  3. regenerate surface projection
  4. run affected surface conformance checks
  5. rerun row-addressing browser scenarios
  6. rerun app proof
```

It should not default to:

```text
run every repository test and hope
```

A final release or milestone gate may still require the broad suite. The repair loop first uses the narrowest check that can confirm the repaired semantic boundary.

### TL;DR

Use focused repair validation first; use broad proof at the promotion boundary.

## 17. The AI repair protocol

An AI repairs an MCEL application through this sequence:

1. **Preserve the candidate and last-proven fingerprints.**
2. **Read the complete normalized diagnostic report.**
3. **Group diagnostics by semantic path and dependency.**
4. **Select the earliest non-cascading blocking diagnostic.**
5. **Reject any repair that silently chooses authority or policy.**
6. **Choose one safe repair or request the missing semantic decision.**
7. **Apply the repair as one scoped project-edit transaction.**
8. **Re-run the narrow validation named by the diagnostic.**
9. **Discard diagnostics that no longer reproduce.**
10. **Record newly invalidated projections and evidence.**
11. **Renew required acceptance, observation, and effect evidence.**
12. **Run final proof before promoting the candidate.**

The AI must not edit generated projections directly, suppress a blocking diagnostic without a versioned waiver, or claim completion while invalidated evidence remains.

### TL;DR

Repair one root semantic cause, regenerate, renew affected evidence, and prove again.

## 18. Repair plan and receipt

A multi-file or multi-stage repair uses a reviewable plan.

```json
{
  "schema": "mcel.compiler-repair-plan.v1",
  "appId": "inventory",
  "candidateSourceBindingFingerprint": "sha256:SOURCE-A",
  "diagnosticKeys": ["sha256:DIAG-A"],
  "selectedRepairs": ["choose-item-id-key"],
  "expectedSemanticChanges": [
    "surface:inventory.items.key -> field:Item.id"
  ],
  "expectedInvalidations": [
    "projection:surface",
    "evidence:browser:item-addressing"
  ],
  "forbiddenPaths": [
    "mcel_apps/inventory/generated/"
  ]
}
```

After application, MCEL records:

```json
{
  "schema": "mcel.compiler-repair-receipt.v1",
  "planFingerprint": "sha256:PLAN",
  "applied": true,
  "changedAuthoredFiles": [
    "mcel_apps/inventory/application.js"
  ],
  "changedGeneratedFiles": [],
  "resolvedDiagnosticKeys": ["sha256:DIAG-A"],
  "newDiagnosticKeys": [],
  "semanticFingerprintBefore": null,
  "semanticFingerprintAfter": "sha256:SEMANTIC-B",
  "requiredEvidenceRenewal": [
    "browser:item-addressing"
  ]
}
```

The receipt proves that a repair was applied. It does not prove the repaired application is correct.

### TL;DR

Repairs are reviewable transactions; proof remains a separate authority.

## 19. Example: unresolved canonical authority

### Source

```javascript
const items = state.list("items", Item, []);
```

### Diagnostic

```text
MCEL_MODEL_STATE_AUTHORITY_REQUIRED

semantic path:
  state:items

problem:
  state authority is not declared

safe repairs:
  state.canonical.list(...)
  state.local.list(...)
  supported external authority

repair class:
  semantic-choice

automatic:
  false

resume at:
  model
```

### Why it blocks

Canonical versus local authority determines:

```text
who may write
whether mounts share state
which receipts are required
which proof authority observes the result
```

### TL;DR

The compiler cannot infer ownership from a variable name or common UI convention.

## 20. Example: collection identity

### Source

```javascript
surface.collection("items", {
  source: visibleItems,
  row: (item) => surface.text(item.name)
});
```

### Diagnostic

```text
MCEL_SURFACE_COLLECTION_KEY_REQUIRED

available compatible fields:
  Item.id
  Item.sku

invalidates:
  row identity
  item-bound actions
  provisional item ownership
  browser row locators

resume at:
  surface
```

### Unsafe automatic repair

```text
use array position
```

Position changes under filtering and sorting and therefore cannot become semantic identity.

### TL;DR

Identity is authored; row-key plumbing is generated.

## 21. Example: canonical write outside the intent

### Source

```javascript
const previewItem = intent.local("preview-item", {
  change: () => [items.append(input.item)]
});
```

### Diagnostic

```text
MCEL_INTENT_CANONICAL_WRITE_NOT_AUTHORIZED

semantic path:
  intent:preview-item.transition:0

observed write:
  state:items

intent authority:
  local

safe repairs:
  convert the operation to an authorized mutation
  or write renderer-local preview state

repair class:
  semantic-choice

resume at:
  intent
```

### TL;DR

The compiler may detect the write set; it may not upgrade an operation’s authority.

## 22. Example: opaque JavaScript callback

### Source

```javascript
change: ({state}) => {
  state.items.push({
    id: Date.now(),
    name: process.env.DEFAULT_ITEM
  });
}
```

### Diagnostics

```text
MCEL_EXPR_AMBIENT_TIME_FORBIDDEN
MCEL_EXPR_AMBIENT_PROCESS_STATE_FORBIDDEN
MCEL_EXPR_OPAQUE_MUTATION_FORBIDDEN
```

### Safe rewrite

```javascript
change: ({input}) => [
  items.append({
    id: dsl.id.next("item"),
    name: input.name
  })
]
```

### Repair class

```text
semantic-rewrite
```

### Migration behavior

A legacy importer may preserve the callback as `legacy.opaque-function`, but the feature remains migration-incomplete and cannot qualify for DSL v1.

### TL;DR

Quarantine old opacity; do not normalize it into false semantic precision.

## 23. Example: unowned capability effect

### Source

```javascript
const push = intent.capability("push", {
  capability: git.push
});
```

### Diagnostic

```text
MCEL_EFFECT_OWNER_OR_TARGET_REQUIRED

missing:
  operation key
  remote target
  confirmation scope
  allowed dispositions
  uncertain-result recovery

resume at:
  effect
```

### Why one automatic repair is impossible

The compiler cannot decide:

```text
which remote
which branch
whether force is allowed
which confirmation authorizes the push
what happens after a timeout with uncertain remote truth
```

### TL;DR

“Call Git” is not a complete effect lifecycle.

## 24. Example: self-confirming proof claim

### Scenario

```javascript
prove.scenario("add item")
  .invoke(addItem)
  .expectReceipt(addItem, "committed");
```

### Diagnostic

```text
MCEL_SCENARIO_INDEPENDENT_CLAIM_REQUIRED

problem:
  scenario proves only that the operation reported its own success

required independent authority:
  canonical-state claim
  visible-surface claim
  external-effect claim when applicable

resume at:
  scenario
```

### Better scenario

```javascript
prove.scenario("add item")
  .invoke(addItem, {name: "Steel", quantity: 12})
  .expectCanonical(items, contains({name: "Steel", quantity: 12}))
  .expectVisibleRow({name: "Steel", quantity: "12"});
```

### TL;DR

A receipt can carry evidence; it cannot be the only witness to its own correctness.

## 25. Example: late async result after supersession

### Observation

```text
request A started for contract-7
request B superseded A
request A result arrived
canonical quote changed from A
```

### Diagnostic

```text
MCEL_EFFECT_LATE_EVENT_COMMITTED

stage:
  prove

repair stage:
  effect

violated rule:
  superseded operation lost commit authority

invalid evidence:
  quote-supersession acceptance
  quote-supersession browser observation
  intent-complete proof

required repair:
  declare and enforce late-result rejection
  then rerun supersession evidence
```

### TL;DR

The final visible quote does not excuse an illegal intermediate commit.

## 26. Example: Git Tools migration conflict

### Legacy meaning

```text
push requires exact confirmation bound to repository, remote, branch, and commit
```

### DSL candidate

```javascript
const push = intent.capability("push", {
  capability: git.push,
  request: ({input}) => ({remote: input.remote})
});
```

### Diagnostic

```text
MCEL_MIGRATION_SEMANTIC_CONFLICT

semantic path:
  migration:git-tools.intent:push

legacy:
  exact confirmation required

candidate DSL:
  no confirmation obligation

compatibility:
  conflicting

blocked:
  DSL-primary promotion
  reuse of legacy push evidence

resume at:
  effect
```

### TL;DR

Migration diagnostics preserve old safety semantics instead of treating absent DSL declarations as harmless simplification.

## 27. Example: Code Editor stale save

### Candidate behavior

```text
save writes draft bytes without checking the canonical source hash
```

### Diagnostic

```text
MCEL_INTENT_STALE_SOURCE_GUARD_REQUIRED

semantic path:
  intent:save-document

missing:
  expected source version/hash
  refusal on mismatch
  draft retention after refusal

related effect:
  effect:filesystem-write

resume at:
  intent
```

### Required proof renewal

```text
stale-save refusal acceptance
retained-draft browser observation
no-filesystem-write effect claim
```

### TL;DR

A failed save must explain both the absent external write and the retained user work.

## 28. Example: Document Editor export residue

### Candidate lifecycle

```text
export capability completed
artifact exists
provisional export state remains indefinitely
```

### Diagnostic

```text
MCEL_EFFECT_UNEXPLAINED_RESIDUE

residue:
  provisional:document-export.progress

allowed repairs:
  declare cleanup after terminal success
  or declare retained export state with owner and retention policy

resume at:
  effect
```

### TL;DR

Something left behind is part of application meaning and must be cleaned up or explicitly retained.

## 29. Front-end diagnostics and canonical diagnostics

Different compiler front ends may detect different syntax-level problems.

### Official DSL front end

```text
MCEL_DSL_DYNAMIC_MODULE_IMPORT_FORBIDDEN
```

### Legacy requirements importer

```text
MCEL_REQUIREMENT_INTENT_BLOCK_MALFORMED
```

After both produce candidate IR, canonical semantic violations must normalize to the same codes.

Example:

```text
legacy importer:
  MCEL_MODEL_STATE_AUTHORITY_REQUIRED

DSL compiler:
  MCEL_MODEL_STATE_AUTHORITY_REQUIRED
```

This is required for migration comparison and common AI repair tooling.

### TL;DR

Syntax diagnostics may be front-end-specific; semantic diagnostics must converge at the IR boundary.

## 30. Generated-file drift

Generated files are inspectable but not normal authoring surfaces.

### Detection

```text
generated/application.definition.js differs from projection of current IR
```

### Diagnostic

```text
MCEL_PROJECTION_GENERATED_FILE_DRIFT

repair source:
  none, unless authored semantics are also wrong

safe repair:
  regenerate projection from canonical IR

forbidden repair:
  preserve the manual generated-file edit as authority

repair class:
  mechanical
```

If the manual edit represents desired behavior, the AI must first express that behavior in requirements and official DSL source.

### TL;DR

Regenerate drift; move intended behavior back into authored semantics.

## 31. Diagnostic suppression and waivers

A blocking diagnostic cannot be hidden with a comment such as:

```javascript
// ignore MCEL_EFFECT_OWNER_REQUIRED
```

A temporary waiver, when allowed by policy, must be a versioned migration record containing:

```text
waiver ID
rule code
semantic path
reason
owner
expiry condition
blocked maturity level
required follow-up
```

A waiver cannot upgrade truth status or qualify a feature for DSL v1.

### TL;DR

Exceptions are visible migration debt, not comments that make proof disappear.

## 32. Diagnostic report

A compiler or proof command emits `mcel.compiler-diagnostic-report.v1`.

```json
{
  "schema": "mcel.compiler-diagnostic-report.v1",
  "appId": "inventory",
  "requestedStage": "compile",
  "status": "blocked",
  "lastProvenSemanticFingerprint": "sha256:AAA",
  "candidateSemanticFingerprint": null,
  "candidateSourceBindingFingerprint": "sha256:SOURCE-B",
  "summary": {
    "fatal": 0,
    "error": 1,
    "warning": 0,
    "blocking": 1,
    "root": 1,
    "cascading": 3
  },
  "diagnostics": [],
  "recommendedRepairOrder": ["sha256:DIAG-A"],
  "candidatePromotionAllowed": false
}
```

The report ordering is deterministic:

```text
repair-stage order
semantic dependency depth
semantic path
code
diagnostic key
```

### TL;DR

One deterministic report should be usable by CLI tools, Code Editor, MCEL Lab, automated agents, and proof reports.

## 33. Human presentation

Human-facing output should be concise first and expandable.

### CLI summary

```text
MCEL compile blocked: inventory

1 root error, 3 cascading errors

[MCEL_COLLECTION_KEY_REQUIRED] surface:inventory.items
Rendered collection requires stable item identity.
Source: mcel_apps/inventory/application.js:84:5
Resume at: surface

Run with --diagnostics-json for the full repair contract.
```

### Code Editor presentation

The editor may show:

```text
source underline
stable code
one-sentence problem
safe repair choices
invalidated evidence badge
resume-stage link
```

It must not cover or replace the primary editing surface merely to display diagnostics.

### TL;DR

Show the root cause immediately; preserve the complete machine record underneath it.

## 34. Diagnostic determinism

Given the same:

```text
source
compiler version
IR schema version
migration mappings
validation policy
```

MCEL must produce the same:

```text
diagnostic codes
diagnostic keys
semantic paths
repair classes
repair dependency order
invalidation classifications
```

Human wording may improve without changing diagnostic identity.

### TL;DR

An AI must not receive a different repair problem merely because diagnostics were emitted in a different order.

## 35. Security and sensitive data

Diagnostics must not leak:

```text
secret values
tokens
private keys
full environment dumps
unredacted capability payloads
private document contents unless explicitly allowed
```

Diagnostics may report:

```text
semantic field name
value type
redacted fingerprint
length
classification
source path when permitted
```

Example:

```text
observed:
  token: <redacted sha256:...>
```

not:

```text
observed:
  token: ghp_actual_secret
```

### TL;DR

Repair information must be precise without turning compiler errors into a secret-exfiltration channel.

## 36. Existing application-definition migration obligations

Every diagnostics implementation pass must check the definition families recorded in `pretty_docs/mcel-existing-application-definition-migration-inventory.md`.

| Family | Required diagnostic behavior |
| --- | --- |
| Requirements registry and semantic adapters | Preserve requirements source binding and normalize semantic violations at the IR boundary |
| Surface-led Document Editor | Distinguish semantic-surface failures from layout and export-effect failures |
| Scaffolded explicit packages | Point repairs to explicit authored contracts until DSL-primary migration occurs |
| Normalized Workbench `application.js` | Preserve callback provenance and report opaque-function migration blockers |
| MCEL Lab blueprints and annotations | Preserve blueprint source identity and distinguish authoring diagnostics from runtime damage/repair diagnostics |
| Legacy surface-only applications | Report unmapped semantics and unavailable proof obligations without inventing missing intent meaning |

### TL;DR

A common diagnostic protocol must illuminate legacy gaps, not erase them by assuming every app already uses the DSL.

## 37. Per-pass review checklist

Every future DSL, IR, projection, migration, or diagnostics pass must answer:

1. Which authoring stage does the new rule belong to?
2. What stable diagnostic code reports its violation?
3. What semantic path identifies the failed decision?
4. Which authored source receives the repair?
5. Is the repair mechanical or a semantic choice?
6. Which repairs are safe to suggest?
7. Which repairs are forbidden to automate?
8. Which generated projections become invalid?
9. Which acceptance, observation, effect, and proof evidence becomes invalid?
10. At which stage does the AI resume?
11. How do legacy and DSL front ends converge on the same semantic diagnostic?
12. Which migration inventory records must change?

### TL;DR

A semantic rule is incomplete until its failure and repair behavior are documented.

## 38. Acceptance criteria for a future implementation

A future diagnostics implementation is not complete until:

1. All blocking semantic rules emit stable codes.
2. Every diagnostic has a semantic path and repair stage.
3. Official DSL source spans survive into canonical diagnostics.
4. Legacy front ends retain the best available source binding.
5. Generated-file failures point back to authored authority.
6. Diagnostic ordering and keys are deterministic.
7. Cascading diagnostics identify root dependencies.
8. Safe repair options expose their semantic consequences.
9. Authority and policy decisions are never automatically selected.
10. Candidate truth is separated from last proven truth.
11. Evidence invalidation is explicit and fingerprint-bound.
12. Narrow rerun guidance is available.
13. Repair plans are reviewable project-edit transactions.
14. Repair receipts do not masquerade as proof.
15. Git Tools, Code Editor, Document Editor, Counter, and Workbench examples all produce useful stage-aware diagnostics.
16. Sensitive values are redacted.
17. Blocking diagnostics prevent DSL-primary or DSL-v1 promotion.

### TL;DR

The implementation succeeds when an AI can repair semantic mistakes without guessing, over-editing, or borrowing stale proof.

## 39. Documentation sequence from here

The scaffolder/compatibility specification, AI application authoring cycle, pattern catalog, semantic change/evidence-impact model, and benchmark contract are now documented. The benchmark in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` includes injected diagnostic and repair cases that test root-cause identification, safe repair, candidate isolation, evidence renewal, and return to the correct stage.

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` completes the final cross-document review. It authorizes no automatic repair or migration behavior by itself; after explicit authorization, diagnostics may first be implemented as part of the structural IR validator and normalizer.

## Final rule

> MCEL must never tell an AI only that an application is wrong. It must identify the authored semantic decision, distinguish safe plumbing from consequential choice, preserve the last proven application, state what evidence the candidate invalidates, and return the AI to the earliest stage where the application can be repaired honestly.
