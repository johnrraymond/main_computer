# MCEL Semantic Change and Evidence Impact
## Dependency-Aware Modification, Invalidation, Evidence Reuse, and Renewed Proof

## Status

This document specifies how MCEL determines what a proposed application change means, which generated projections and evidence it invalidates, where an AI must re-enter the authoring cycle, and when prior evidence may be reused honestly.

It is a documentation specification. It does not authorize implementation of the semantic differ, dependency graph, impact planner, evidence-reuse engine, candidate compiler, projection promoter, DSL compiler, legacy importer, application migration, or proof changes.

Read this with:

- `pretty_docs/mcel-ai-application-authoring-cycle.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`;
- `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`;
- `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`.

## The short answer

A file change does not determine proof impact. A semantic dependency change does.

MCEL compares the last proven application with the candidate at several independent layers:

```text
requirements meaning
source binding
canonical IR meaning
generated projection
runtime and capability versions
acceptance obligations
browser-observation obligations
effect-accounting obligations
proof reconciliation
```

It then computes an impact closure:

```text
changed semantic decisions
-> directly dependent semantic nodes
-> generated projections that encode those nodes
-> evidence claims bound to those projections or decisions
-> proof conclusions that depend on those claims
```

The AI resumes at the earliest stage containing an unresolved changed decision. MCEL renews the smallest evidence set whose authority remains honest. When evidence is not granular enough to support narrow reuse, MCEL renews the whole application-scoped authority rather than guessing.

### TL;DR

Invalidate by semantic dependency and evidence binding, not by file count, visual similarity, or intuition.

# Part I: What is being compared?

## 1. The last proven application and the candidate are separate

A modification begins with two application identities:

```text
last proven application:
  semantic fingerprint AAA
  projection fingerprint BBB
  runtime fingerprint CCC
  evidence set E1

candidate application:
  source-binding fingerprint DDD
  semantic fingerprint unknown until compile
  projection fingerprint unknown until projection
  evidence set not yet established
```

A failed candidate does not make application `AAA` false. It means the candidate has not earned the right to replace it.

Prior evidence remains evidence for the exact authorities to which it was bound. It must not be silently relabeled as evidence for the candidate.

### Example

The user adds `priority` to Contract Workbench. The candidate fails because the collection row references a field absent from the canonical model.

Correct result:

```text
promoted Workbench remains semantic-runtime-proven
candidate is blocked at model/surface compatibility
promoted evidence remains valid for promoted fingerprint
candidate has no proof claim
```

Wrong result:

```text
Workbench is now broken
or
old browser evidence proves the candidate because most of the page is unchanged
```

### TL;DR

A candidate may fail without damaging the last proven application; old evidence stays attached to the old truth.

## 2. Six independent comparison layers

MCEL must not collapse all change into one fingerprint.

| Layer | What changed? | Example |
| --- | --- | --- |
| Requirements | Intended user contract | Quote cancellation is now required |
| Source binding | Authored file, span, compiler, or source identity | DSL source moved into a module |
| Semantic IR | Application meaning | Collection key changed from `id` to `sku` |
| Projection | Generated package/runtime shape | Surface node IDs changed while meaning stayed constant |
| Execution environment | Runtime, capability, or adapter implementation | Git capability version changed |
| Evidence/proof | Scenario, observation, effect, or reconciliation rules | Browser scenario now checks cancellation residue |

Each layer has its own fingerprint and invalidation policy.

### TL;DR

A source edit, semantic edit, generated-output edit, runtime edit, and proof edit are not interchangeable.

## 3. Proposed semantic change-set record

A compiler comparison should emit a machine-readable record:

```json
{
  "schema": "mcel.semantic-change-set.v1",
  "appId": "inventory",
  "base": {
    "semanticFingerprint": "sha256:AAA",
    "sourceBindingFingerprint": "sha256:BBB",
    "projectionFingerprint": "sha256:CCC"
  },
  "candidate": {
    "semanticFingerprint": "sha256:DDD",
    "sourceBindingFingerprint": "sha256:EEE",
    "projectionFingerprint": "sha256:FFF"
  },
  "changes": [
    {
      "changeId": "change:item.priority",
      "kind": "model-field-added",
      "semanticPath": "model:Item.field:priority",
      "classification": "semantic-compatible-extension",
      "before": null,
      "after": {
        "schema": "enum(low,normal,high)",
        "default": "normal"
      },
      "directDependencies": [
        "intent:add-item.input:priority",
        "surface:item-row.binding:priority"
      ]
    }
  ]
}
```

The record explains the change. It does not itself authorize evidence reuse or promotion.

### TL;DR

The semantic differ must say exactly what changed before an impact planner decides what to renew.

# Part II: The semantic dependency graph

## 4. Nodes in the impact graph

The dependency graph includes at least:

```text
requirement
model and schema
state authority
stable identity
intent input
input source
refusal
invariant
read set
write set
transition
capability
operation identity
concurrency policy
cancellation policy
recovery policy
effect declaration
surface node
semantic binding
layout region
scenario
claim
generated projection
runtime/capability version
evidence record
proof conclusion
```

Every node has a stable semantic ID. Dependencies use those IDs, not incidental file paths.

### TL;DR

Impact analysis needs a graph of semantic decisions and evidence authorities, not a graph of files alone.

## 5. Edge kinds

Different dependency edges mean different things.

| Edge | Meaning |
| --- | --- |
| `reads` | Derivation, intent, surface, or claim reads a value |
| `writes` | Intent transition changes canonical or local state |
| `binds` | Surface or input source binds to a semantic field |
| `identifies-by` | Collection, effect, or operation uses a stable key |
| `invokes` | Intent invokes a capability |
| `owns-effect` | Intent owns a consequential effect |
| `governs` | Policy controls concurrency, cancellation, confirmation, or recovery |
| `claims` | Scenario asserts an independently observed outcome |
| `projects-to` | IR node contributes to a generated artifact |
| `observed-by` | Semantic claim is witnessed by an evidence record |
| `reconciled-by` | Proof conclusion depends on evidence and declarations |

The impact planner follows only edge kinds relevant to the change.

### Example

Changing the label on a button follows:

```text
surface presentation label
-> browser projection
-> visual/accessibility observation when label is claimed
```

It does not automatically follow:

```text
button label
-> mutation transition
-> canonical-state acceptance
```

### TL;DR

Typed dependency edges let MCEL invalidate what actually depends on the change instead of everything nearby.

## 6. Direct impact versus transitive impact

A changed node has direct dependents. Those dependents may have their own dependents.

```text
Item.priority added
-> add-item input schema
-> add-item form binding
-> add-item transition
-> item-row projection
-> add-item acceptance scenario
-> item-row browser scenario
-> intent-complete proof
```

The complete set is the transitive impact closure.

An unaffected branch remains outside the closure:

```text
remove-item by Item.id
quote cancellation by Item.id
search by Item.name and Item.sku
```

unless the new field changes their declared behavior.

### TL;DR

Start with changed decisions, then follow only declared dependencies to the evidence and proof conclusions that rely on them.

## 7. Earliest authoring-stage re-entry

Every change maps to the earliest stage where a semantic decision changed.

| Change | Earliest stage |
| --- | --- |
| Requirement outcome changed | `requirements` |
| State authority or model changed | `model` |
| Input, refusal, transition, or policy changed | `intent` or `effect` |
| Semantic binding changed | `surface` |
| Placement only changed | `layout` |
| Claimed outcome changed | `scenario` |
| Source moved but semantics unchanged | `compile`/`compatibility` |
| Runtime or capability changed | `acceptance`/`observe`/`effect-accounting` |
| Evidence policy changed | corresponding evidence stage |

The AI does not restart earlier than necessary, but it may not skip the earliest affected decision.

### TL;DR

Return to the first changed semantic decision, not to the first changed file and not automatically to requirements.

# Part III: Change classifications

## 8. Source-only change

A source-only change alters provenance without altering canonical meaning.

### Example

```text
before:
  mcel_apps/inventory/application.js lines 40-55

after:
  mcel_apps/inventory/modules/inventory-intents.js lines 10-25
```

If both compile to exact normalized IR and exact generated projections:

```text
semantic fingerprint: unchanged
projection fingerprint: unchanged
source-binding fingerprint: changed
```

Required renewal:

```text
source binding
repository binding
compiler/source provenance
compatibility record
```

Behavioral evidence may be reused only through an explicit reuse record proving that its semantic, projection, runtime, capability, scenario, and freshness bindings remain exact.

### TL;DR

Moving source may require renewed provenance without requiring pretend browser changes.

## 9. Presentation-only change

A presentation-only change alters labels, styling, or layout without changing semantic operation, state, identity, or effect policy.

### Example

```javascript
surface.action("add-item", {
  intent: addItem,
  label: "Create item" // was "Add item"
});
```

Possible impact:

```text
surface projection
accessibility name
browser locator if label-based
visual/layout evidence
scenario claim if exact label is claimed
```

Normally unaffected:

```text
add-item transition
canonical-state acceptance
capability effects
write-set proof
```

If browser scenarios use stable semantic IDs rather than labels, locator behavior may remain exact while accessibility evidence still changes.

### TL;DR

A changed label is not a changed mutation, but it may still invalidate the evidence that claims the label.

## 10. Model/schema change

A model change may be compatible, restrictive, or breaking.

### Additive field with deterministic default

```javascript
priority: field.enum(["low", "normal", "high"]).default("normal")
```

This can preserve old records if the default is part of normalization.

### Restrictive validation

```text
quantity minimum changes from 1 to 5
```

This changes refusal behavior and valid-input scenarios.

### Breaking type change

```text
quantity changes from integer to decimal string
```

This changes parsing, canonical representation, derived expressions, projections, and proof claims.

### TL;DR

“Schema changed” is not one impact class; defaults, restrictions, and representation changes propagate differently.

## 11. State-authority change

Changing authority is always consequential.

```text
search: renderer-local
-> search: canonical
```

This changes:

```text
ownership
multi-instance behavior
mutation authority
serialization
revision handling
surface synchronization
isolation proof
```

It cannot be classified as a mechanical migration even when the visible single-instance page appears identical.

### TL;DR

Authority changes invalidate every assumption about ownership, synchronization, mutation, and isolation.

## 12. Stable-identity change

Changing a collection or operation key is a high-impact semantic change.

```text
Item.id
-> Item.sku
```

Potential impact:

```text
row reconciliation
focus and local row state
item-bound controls
async operation ownership
cancellation targets
latest-per-key supersession
browser locators
receipts
migration of existing canonical records
```

A visual comparison cannot prove identity equivalence.

### TL;DR

Changing a key changes what the system believes is the same thing, not merely how it finds a row.

## 13. Intent-input or input-source change

These are separate changes.

### Schema change

```text
add-item.quantity: integer >= 1
-> integer >= 5
```

### Source change

```text
createdBy: authenticated context
-> visible form control
```

The second changes authority and trust even if the resulting string is identical.

Impact may include:

```text
validation
surface controls
payload construction
security boundary
acceptance inputs
browser actions
receipts
```

### TL;DR

What an intent receives and where the value comes from are independent semantic decisions.

## 14. Transition, refusal, or invariant change

Changing a canonical transition invalidates evidence that proves its state result.

Changing a refusal invalidates evidence that proves nonoccurrence of the forbidden effect.

Changing an invariant may invalidate every intent that can reach the affected state.

### Example

```text
clear-all previously refused while quote operations were active
now it cancels active quotes and then clears
```

This affects:

```text
clear-all transition
quote cancellation effects
cleanup obligations
active-operation registry
parallel operation scenarios
canonical result
browser result
intent-complete proof
```

### TL;DR

Refusals and invariants are executable behavior, not documentation ornaments.

## 15. Capability or effect-policy change

A capability change may alter external authority even when the intent name is unchanged.

### Example

```text
Git push capability v1
-> Git push capability v2 with force-with-lease support
```

Impact depends on:

```text
request schema
confirmation scope
remote mutation semantics
idempotency
receipts
indeterminate-result recovery
compensation
capability version
```

External-effect evidence must be renewed when the capability implementation or its declared contract changes in a way that can alter the effect.

### TL;DR

An unchanged button does not preserve proof when the external capability behind it changes.

## 16. Concurrency, cancellation, or recovery change

Lifecycle policy changes invalidate more than the final result.

### Example

```text
request-quote:
  latest-per-item
-> parallel-per-item
```

Potentially invalidated:

```text
operation identity
supersession rules
late-event rejection
provisional ownership
commit authority
cleanup
cancellation
parallel scenario expectations
effect-accounting cardinality
```

The final visible quote may still look correct in one run. That does not prove the new lifecycle.

### TL;DR

Concurrency policy is semantic authority over which operation may commit; it always requires lifecycle evidence.

## 17. Surface semantic-binding change

A surface change is semantic when a control or visible value points to a different operation or authority.

```text
Remove button:
  invokes remove-item(current Item.id)
-> invokes clear-all
```

Even if the label and location stay unchanged, this is a major intent-binding change.

Conversely, moving the correct Remove button to another layout region may be layout-only.

### TL;DR

What a control means matters more than where it is drawn.

## 18. Scenario or proof-claim change

A scenario change may reveal a previously unclaimed obligation rather than change runtime behavior.

### Example

Add this claim:

```text
After quote cancellation, no provisional row status remains.
```

The semantic application may remain unchanged, while proof completeness changes.

Required work:

```text
new scenario/claim binding
browser or runtime observation
cleanup evidence
effect-accounting reconciliation
proof renewal
```

Prior evidence may remain valid for its old claims but cannot establish the new claim.

### TL;DR

Adding a stronger claim does not falsify old evidence; it exposes proof work that old evidence never performed.

## 19. Compiler or projection change with exact IR

A compiler may change while producing exact canonical meaning.

There are two subcases.

### Exact projection

```text
semantic fingerprint unchanged
projection fingerprint unchanged
runtime versions unchanged
```

Behavior evidence may be eligible for explicit reuse. Source/repository/compiler binding and compatibility must be renewed.

### Changed projection

```text
semantic fingerprint unchanged
projection fingerprint changed
```

Browser/runtime evidence bound to generated structure must be renewed for affected surfaces or operations. Pure semantic acceptance evidence may be reusable only when its runner does not depend on the changed projection and all other bindings remain exact.

### TL;DR

Exact meaning does not automatically mean exact generated runtime, and exact generated runtime does not automatically mean exact provenance.

# Part IV: Evidence impact

## 20. Evidence is bound by authority, not by filename

Every evidence record should declare enough binding information to decide whether it still applies:

```text
app ID
semantic feature IDs
scenario or claim IDs
semantic fingerprint or feature fingerprints
projection fingerprint when relevant
runtime version
capability/adapter version when relevant
observation policy version
runner version
repository/source binding
freshness window
```

An evidence file with the same path is not necessarily reusable. A moved evidence file is not necessarily invalid if its declared bindings remain exact.

### TL;DR

Evidence validity comes from explicit bindings, not storage location.

## 21. Evidence units

Evidence may be scoped at several levels:

```text
repository
application
feature
intent
effect
surface
scenario
claim
```

Narrow renewal is possible only when the existing evidence authority records a sufficiently narrow binding.

### Example

If one report contains only:

```text
app: contract-workbench
status: pass
```

then MCEL cannot honestly reuse “the unaffected half” of that report.

If it contains feature records:

```text
intent:add-contract: pass
intent:remove-contract: pass
intent:request-quote: pass
```

then the impact planner can reason per intent, subject to shared dependencies.

### TL;DR

Granular reuse requires granular evidence; otherwise renew conservatively.

## 22. Evidence impact classifications

Each evidence unit receives one of these statuses:

| Status | Meaning |
| --- | --- |
| `still-valid` | All declared bindings remain exact |
| `reusable-by-exact-equivalence` | Source/compiler changed, but semantic and execution bindings are exact |
| `reusable-by-proven-independence` | Changed feature is outside the evidence unit’s dependency closure |
| `must-renew` | A bound semantic, projection, runtime, capability, scenario, or policy dependency changed |
| `incompatible` | Evidence asserts behavior contradicted by the candidate |
| `insufficient-granularity` | The report cannot isolate affected from unaffected claims |
| `stale` | Freshness policy expired |
| `missing` | Candidate creates a new obligation with no evidence |

### TL;DR

Reuse must be a positive conclusion with a reason, not the absence of a reason to rerun.

## 23. Proposed evidence impact plan

```json
{
  "schema": "mcel.evidence-impact-plan.v1",
  "appId": "inventory",
  "baseSemanticFingerprint": "sha256:AAA",
  "candidateSemanticFingerprint": "sha256:DDD",
  "earliestReentryStage": "model",
  "affectedSemanticIds": [
    "model:Item.field:priority",
    "intent:add-item",
    "surface:item-row",
    "scenario:add-item.valid"
  ],
  "evidence": [
    {
      "evidenceId": "acceptance:intent:add-item",
      "authority": "acceptance",
      "impact": "must-renew",
      "reasonCodes": ["BOUND_INPUT_SCHEMA_CHANGED", "BOUND_TRANSITION_CHANGED"]
    },
    {
      "evidenceId": "browser:intent:remove-item",
      "authority": "browser-observation",
      "impact": "reusable-by-proven-independence",
      "reasonCodes": ["DEPENDENCY_CLOSURE_DISJOINT"]
    }
  ],
  "requiredRuns": [
    "acceptance:intent:add-item",
    "browser:scenario:add-item.valid",
    "browser:scenario:item-row.priority",
    "proof:intent:add-item"
  ]
}
```

The plan is reviewable and fingerprint-bound.

### TL;DR

Before rerunning tests, MCEL should produce an explicit impact plan saying what is renewed, what is reused, and why.

## 24. Evidence reuse record

Reuse must create its own auditable record:

```json
{
  "schema": "mcel.evidence-reuse-record.v1",
  "evidenceId": "acceptance:intent:remove-item",
  "fromSemanticFingerprint": "sha256:AAA",
  "toSemanticFingerprint": "sha256:DDD",
  "classification": "reusable-by-proven-independence",
  "unchangedBindings": {
    "featureId": "intent:remove-item",
    "projectionFingerprint": "sha256:CCC",
    "runtimeVersion": "mcel-runtime-v1",
    "runnerVersion": "mcel-acceptance-v1"
  },
  "dependencyCheck": {
    "changedClosure": ["model:Item.field:priority", "intent:add-item"],
    "evidenceDependencies": ["model:Item.field:id", "intent:remove-item"],
    "intersection": []
  }
}
```

No reuse record may claim that a changed obligation was tested when it was merely inferred unchanged.

### TL;DR

Evidence reuse is itself a proof artifact.

## 25. Acceptance evidence impact

Acceptance usually depends on:

```text
intent input schema
refusals
preconditions
transition
postconditions
canonical state model
receipts
runner/runtime semantics
```

It may not depend on layout or presentation unless the acceptance contract explicitly includes them.

### Examples

```text
button label changed:
  canonical add-item acceptance may remain valid

quantity minimum changed:
  add-item acceptance must renew

runtime SCM revision behavior changed:
  mutation acceptance must renew
```

### TL;DR

Acceptance renews when the contractual operation or its execution authority changes, not merely when CSS changes.

## 26. Browser-observation evidence impact

Browser evidence depends on:

```text
surface semantics
bindings
locators
projection
layout when geometry is claimed
accessibility names when claimed
runtime behavior
scenario steps
visible outcomes
```

### Examples

```text
stable semantic locator, label changed:
  action locator may remain valid
  label/accessibility claim must renew

row key changed:
  item-addressing and async-row observations must renew

canonical transition changed but same final text in one fixture:
  browser evidence must renew because the scenario’s semantic result changed
```

### TL;DR

Browser evidence proves observed behavior under a specific projection and scenario, not generic visual resemblance.

## 27. Effect-accounting evidence impact

Effect evidence depends on:

```text
effect owner
effect kind
operation identity
target identity
capability version
concurrency/cancellation/recovery policy
allowed dispositions
cleanup and retained residue
receipt/evidence schema
```

Any change to those dependencies normally renews effect accounting for the affected effect family.

### Example

Adding cancellation to `request-quote` creates new possible dispositions and cleanup obligations. Successful-request evidence cannot prove cancellation merely because the same capability is used.

### TL;DR

New effect dispositions create new evidence obligations.

## 28. Proof impact

Proof reconciles declarations and independent evidence. It must renew whenever:

```text
a declared obligation changes
an evidence unit used by the proof changes or becomes stale
a new claim is introduced
a prior claim becomes incompatible
effect accounting changes
source/repository binding changes
truth-gate policy changes
```

A proof runner may reuse unaffected subordinate evidence, but it must issue a new candidate-level reconciliation verdict.

### TL;DR

Even when some evidence is reusable, the candidate still needs a new proof verdict binding the complete candidate truth.

## 29. Conservative fallback rule

When MCEL cannot establish narrow independence, it renews the broader authority.

```text
feature-level dependency known:
  renew affected feature evidence

only app-level report available:
  renew app-level evidence

runtime/capability dependency unknown:
  renew all evidence that could depend on it

legacy opaque callback changed:
  renew affected application evidence and retain migration gap
```

This is not inefficiency. It is refusal to invent evidence granularity that does not exist.

### TL;DR

Uncertainty widens renewal; it never justifies silent reuse.

# Part V: Worked change examples

## 30. Example: add `priority` to an existing item application

### User request

```text
Add low, normal, and high priority to items. Default to normal. Show it in each row.
```

### Independent changed decisions

```text
Item model gains priority enum
old records normalize to normal
add-item accepts priority from a control
add-item transition stores priority
item row displays priority
proof claims canonical and visible priority
```

### Generated impact

```text
model schema
input schema and draft binding
transition expression
surface projection
browser package
acceptance fixture
browser scenario
intent-complete proof
```

### Potentially reusable

```text
remove-item behavior when it depends only on Item.id
quote lifecycle when it depends only on Item.id
search when priority is not declared searchable
```

### Must renew

```text
add-item acceptance
add-item browser scenario
item-row visible claim
candidate proof
```

### TL;DR

One declaration per changed decision; renew only evidence that depends on those decisions when granularity proves independence.

## 31. Example: rename “Add contract” to “Create contract”

### Change classification

```text
presentation/accessibility change
semantic intent ID unchanged
transition unchanged
```

### Impact

```text
surface projection changes
exact-label accessibility claim changes
label-based locator changes if one exists
```

### Potentially reusable

```text
canonical add-contract acceptance
state-transition proof subordinate evidence
capability/effect evidence
```

### Must renew

```text
browser label/accessibility evidence
candidate projection binding
candidate proof reconciliation
```

### TL;DR

A label rename should not force a fake mutation retest, but it must renew what actually claims the label.

## 32. Example: change quote concurrency from latest-per-item to queue-per-item

### Changed decisions

```text
concurrency policy
operation commit ordering
supersession disposition removed or altered
queue lifecycle introduced
provisional ownership
cleanup timing
```

### Must renew

```text
request-quote acceptance
parallel and same-key browser scenarios
effect accounting
late-event behavior
cancellation interaction
intent-complete proof
```

### Cannot reuse as proof of new policy

```text
old latest-per-item supersession evidence
```

It may remain historical evidence for the prior application version.

### TL;DR

Lifecycle-policy changes require lifecycle evidence even when single-request behavior is unchanged.

## 33. Example: Git Tools adds force-with-lease

### Changed decisions

```text
new intent or explicit mode
new confirmation scope
new preflight checks
remote effect semantics
recovery and receipt interpretation
```

### Impact

```text
requirements
Git domain operator expressions
capability request schema
confirmation evidence
remote mutation effect accounting
failure/indeterminate recovery
browser and acceptance scenarios
```

Existing ordinary-push evidence may be reusable only for the unchanged ordinary-push feature when reports and dependencies are feature-scoped.

### TL;DR

A new Git mutation mode is not a checkbox on old proof; it is a new governed effect path.

## 34. Example: Code Editor changes stale-source fingerprint algorithm

### Changed decisions

```text
content identity operator
stale-source precondition
save request binding
receipt provenance
```

### Required renewal

```text
hash/domain-operator conformance
stale-save refusal acceptance
filesystem nonoccurrence evidence on refusal
successful-save evidence
retained-draft behavior
candidate proof
```

An unchanged visible editor does not preserve stale-write safety proof.

### TL;DR

Changing how stale state is detected changes save authority even when the editor UI is untouched.

## 35. Example: Document Editor adds PDF export

### Changed decisions

```text
new export intent
new export capability
artifact type and ownership
file target policy
confirmation or overwrite behavior
success/failure/cleanup dispositions
visible completion claim
```

### New obligations

```text
capability request construction
artifact effect accounting
partial-artifact cleanup or declared retention
acceptance
browser observation
receipt interpretation
proof
```

Existing document-save evidence remains separate; it does not prove export.

### TL;DR

A new external artifact is a new consequential effect with its own lifecycle and proof.

## 36. Example: legacy compiler replaced by DSL compiler with exact output

### Comparison

```text
requirements meaning: unchanged
semantic IR: exact
low-level projection: exact
runtime/capability versions: exact
source binding: changed
compiler identity: changed
```

### Required work

```text
legacy/DSL compatibility report
source-binding and repository-binding renewal
compiler conformance evidence
new candidate proof reconciliation
```

### Behavioral evidence

Acceptance, browser, and effect evidence may be reused only if:

```text
the exact projection was executed
the evidence bindings remain exact
the evidence is fresh
the runner/runtime/capability versions remain exact
an evidence-reuse record is produced
```

If current reports cannot establish those facts, renew them conservatively.

### TL;DR

Exact compiler output can permit audited evidence reuse; it never permits unrecorded evidence borrowing.

# Part VI: Migration and multi-level compatibility

## 37. Every pass checks all three authored levels

For each semantic change, record:

| Layer | Required question |
| --- | --- |
| Documentation | Did intended behavior or proof expectations change? |
| Current explicit/legacy path | How is the changed feature represented and executed now? |
| DSL candidate | How is the changed feature declared in `mcel.dsl.v1`? |
| Canonical IR | Do both paths normalize to the intended change? |
| Projection | Which generated files and browser artifacts change? |
| Evidence | Which obligations are new, changed, reusable, or stale? |
| Migration inventory | Does the application or definition-family record need updating? |

A pass may mark a layer `unchanged-and-compatible`, but it may not omit the layer from review.

### TL;DR

Every change pass follows documentation, legacy/current execution, DSL, IR, projection, and evidence all the way through.

## 38. Per-feature migration ledger impact

A migration ledger entry should retain both compatibility and evidence impact:

| Feature | Legacy IR | DSL IR | Semantic comparison | Evidence impact | Migration status |
| --- | --- | --- | --- | --- | --- |
| `increment` | Present | Present | Exact | Source binding only | Dual-authored |
| `direct-set` refusal | Present | Missing | Incomplete | Candidate proof blocked | DSL gap |
| `request-quote` cancellation | Present | Changed | Conflicting | Full lifecycle renewal | Review required |

### TL;DR

Semantic equivalence and evidence sufficiency are different columns; both must pass.

## 39. Legacy opaque behavior

When a changed feature still contains `legacy.opaque-function`, MCEL cannot perform complete semantic dependency analysis inside that function.

Required behavior:

```text
retain source hash and declared boundary
mark the affected dependency region opaque
widen impact to every declared read, write, effect, surface, and claim boundary
renew affected evidence conservatively
retain migration blocker for DSL-v1
```

Changing an opaque callback may never be classified as harmless merely because its hash changed only slightly or its fixture still passes.

### TL;DR

Opaque behavior widens impact and remains migration debt.

## 40. Updating existing definition families

The change-impact implementation must support:

```text
requirements-registry applications
semantic-adapter applications
surface-led Document Editor
scaffolded explicit packages
normalized Workbench definitions
MCEL Lab blueprints
legacy surface-only applications
```

Each importer must emit the best dependency graph its source supports and declare unknown or opaque edges honestly.

Applications with weak source semantics may require broader evidence renewal until their definitions are migrated to the IR/DSL model.

### TL;DR

Narrow renewal is earned by explicit semantics; legacy definitions are preserved but may remain conservatively expensive to change.

# Part VII: AI-facing workflow

## 41. The modification loop

For an existing application change, the AI follows:

```text
1. Read the request and last proven application record.
2. Identify independent changed decisions.
3. Update requirements when intended behavior changes.
4. Inventory current/legacy representations of affected features.
5. Edit only authored candidate sources.
6. Compile candidate IR.
7. Produce semantic change set.
8. Compute dependency closure.
9. Produce evidence impact plan.
10. Repair blocking diagnostics at the earliest affected stage.
11. Generate candidate projections.
12. Run required renewed evidence.
13. Record any approved evidence reuse.
14. Reconcile candidate proof.
15. Promote atomically only after all invalidations close.
16. Update migration inventory and feature ledger.
```

### TL;DR

A feature edit is complete only when its semantic delta, generated impact, evidence impact, renewed proof, and migration record all close.

## 42. What the AI should see

A concise status might be:

```text
Change: add Item.priority
Earliest stage: model
Affected features: Item, add-item, item-row, add-item.valid
Generated projections: 4 changed
Evidence:
  renew 3
  reuse 5 by proven independence
  missing 1 new visible-priority claim
Blocking diagnostics: 0
Next action: run add-item acceptance and item-row browser scenarios
```

The AI should not have to infer this from a Git diff or a directory full of stale reports.

### TL;DR

Show the AI the semantic change, impact closure, evidence plan, and next honest action.

## 43. Diagnostics

Representative diagnostics include:

```text
MCEL_CHANGE_DEPENDENCY_UNKNOWN
MCEL_CHANGE_AUTHORITY_ESCALATION
MCEL_CHANGE_IDENTITY_BREAKING
MCEL_EVIDENCE_REUSE_BINDING_MISMATCH
MCEL_EVIDENCE_REUSE_INSUFFICIENT_GRANULARITY
MCEL_EVIDENCE_RENEWAL_REQUIRED
MCEL_EVIDENCE_NEW_OBLIGATION_MISSING
MCEL_PROJECTION_CHANGE_REQUIRES_OBSERVATION
MCEL_RUNTIME_VERSION_REQUIRES_RENEWAL
MCEL_MIGRATION_LEDGER_STALE
```

Each diagnostic follows `mcel.compiler-diagnostic.v1` and identifies the earliest authoring or evidence stage for repair.

### TL;DR

Impact failures must return the AI to a named semantic or evidence stage, not tell it vaguely to rerun tests.

# Part VIII: Normalization and determinism

## 44. Change-set determinism

Given the same base IR, candidate IR, dependency schema, and policy versions, MCEL must emit the same:

```text
change IDs
change classifications
affected semantic IDs
impact closure
evidence classifications
required-run ordering
reuse-record inputs
change-set fingerprint
impact-plan fingerprint
```

Incidental timestamps and filesystem traversal order are excluded from semantic equality.

### TL;DR

Impact planning must be reproducible enough to review, cache, compare, and prove.

## 45. Policy versioning

Impact depends on versioned policies:

```text
IR schema version
dependency-edge schema
evidence-binding schema
freshness policy
runtime compatibility policy
capability compatibility policy
projection compatibility policy
proof policy
```

A policy change may itself invalidate prior reuse decisions even when the application is unchanged.

### TL;DR

Evidence reuse is valid under a declared policy version, not forever.

## 46. Fingerprints

The system should distinguish:

```text
semantic-change-set fingerprint
dependency-graph fingerprint
evidence-impact-plan fingerprint
evidence-reuse-record fingerprint
candidate proof fingerprint
```

These records bind the reasoning used to promote a candidate.

### TL;DR

Promotion should preserve not only what changed, but the exact impact reasoning accepted for that change.

# Part IX: Acceptance criteria for later implementation

## 47. Required implementation outcomes

A future implementation is incomplete until it can:

1. Compare two canonical IR documents deterministically.
2. Classify source-only, projection-only, semantic, runtime, capability, evidence-policy, and proof changes.
3. Build a typed dependency graph.
4. Calculate transitive impact closure.
5. Identify the earliest authoring-cycle re-entry stage.
6. Emit `mcel.semantic-change-set.v1`.
7. Emit `mcel.evidence-impact-plan.v1`.
8. Distinguish reusable, renewable, incompatible, stale, missing, and insufficiently granular evidence.
9. Emit auditable evidence-reuse records.
10. Fall back conservatively when dependency or evidence granularity is missing.
11. Preserve the last proven application while the candidate is incomplete.
12. Integrate feature-level migration ledgers.
13. Cover Counter, Workbench, Git Tools, Code Editor, and Document Editor examples.
14. Refuse promotion while any candidate invalidation remains unresolved.

### TL;DR

The implementation must calculate and prove impact; a changed-files list is not enough.

## 48. Required benchmark cases

The AI-authoring benchmark specified in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` includes at least:

```text
source-only module extraction
button-label rename
additive field with default
restrictive validation change
collection-key migration
state-authority change
new refusal
transition change
async concurrency-policy change
new capability effect
new proof claim
runtime version change
legacy-to-DSL exact compilation
opaque legacy callback change
```

For each case, measure:

```text
correct earliest stage
correct affected semantic closure
unnecessary evidence renewal
incorrect evidence reuse
repair iterations
final proof result
```

### TL;DR

A good DSL is not enough; the system must also modify applications without either missing impact or needlessly restarting everything.

## 49. Documentation completeness result

The benchmark contract in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` applies these impact cases. `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` now confirms that the change-impact rules agree with the source, IR, projection, evidence, proof, and migration authorities.

No semantic differ, impact planner, evidence-reuse engine, projection promoter, or application migration is implemented by that review. The first permissible code wave remains the explicitly authorized structural IR kernel.

### TL;DR

The modification model is documented and cross-reviewed; implementation must still earn every narrow evidence-reuse claim.

# Governing rule

> MCEL may reuse prior evidence only when explicit semantic dependencies, execution bindings, policy versions, and evidence granularity prove that the evidence still witnesses the candidate claim. Otherwise, MCEL renews the affected authority and preserves the last proven application until the candidate earns a new proof verdict.
