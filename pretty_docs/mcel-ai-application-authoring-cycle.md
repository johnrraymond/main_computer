# MCEL AI Application Authoring Cycle
## A Stage-Gated Path from User Request to Proven and Migratable Application

## Status

This document specifies the application-authoring cycle an AI follows while MCEL moves from documentation-driven and explicit application definitions toward the official `mcel.dsl.v1` source language.

It is a documentation specification. It does not authorize implementation of the DSL compiler, canonical IR compiler, legacy importers, candidate workspace, repair executor, compatibility validator, generated projection promoter, migration of an existing application, or retirement of any current application-definition path.

Read this with:

- `pretty_docs/mcel-ai-authoring-language-executive-overview.md`;
- `pretty_docs/mcel-ai-authoring-semantic-boundary.md`;
- `pretty_docs/mcel-application-ir-and-compiler-migration.md`;
- `pretty_docs/mcel-application-ir-schema-and-normalization.md`;
- `pretty_docs/mcel-existing-application-definition-migration-inventory.md`;
- `pretty_docs/mcel-constrained-expression-model.md`;
- `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`;
- `pretty_docs/mcel-official-vanilla-javascript-dsl.md`;
- `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`;
- `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`;
- `pretty_docs/mcel-ai-authoring-pattern-catalog.md`;
- `pretty_docs/mcel-semantic-change-and-evidence-impact.md`.

## The short answer

The AI does not jump from a user request directly into files. It advances through explicit semantic gates:

```text
understand the request
-> settle requirements
-> inventory the current application path when one exists
-> create or select a safe candidate workspace
-> declare models, authority, and identity
-> declare intents, refusals, transitions, capabilities, and effects
-> declare semantic surfaces and layout
-> declare independent proof scenarios
-> compile a candidate IR
-> diagnose and repair the earliest failed decision
-> compare legacy/current and DSL meaning
-> generate candidate projections
-> run acceptance, browser observation, and effect accounting
-> reconcile proof
-> promote atomically
-> modify through the earliest affected stage
-> renew only invalidated evidence
```

At every gate, the AI must know:

```text
what it is deciding
which artifact carries that decision
what MCEL may generate
what check proves the stage complete
where to return if the check fails
```

### TL;DR

The authoring cycle is a semantic state machine. Each stage closes one class of decisions before the AI advances.

## 1. One cycle, three authored levels

During migration, MCEL must keep three levels visible:

| Level | Purpose | Transitional authority |
| --- | --- | --- |
| Requirements and application documentation | User-visible behavior, constraints, risks, and proof expectations | Intended meaning |
| Current explicit or legacy definition path | What the repository can currently execute and prove | Promoted executable meaning |
| Official DSL candidate | Future compact authoring source | Candidate meaning until compatibility and proof pass |

The canonical IR is not a fourth authored level. It is the normalized machine representation used to compare and compile the authored levels.

### Example: Git push

Documentation may state:

```text
A push requires clean-worktree preflight, valid ref names, explicit confirmation,
a remote-mutation receipt, and recovery when the remote result is indeterminate.
```

The current Git Tools adapter and operation path express the live executable version.

The future DSL candidate may express:

```javascript
const push = intent.capability("push", {
  input: {
    repositoryId: input.control(field.id()),
    branch: input.control(field.text().minLength(1)),
    confirmation: input.confirmation("git-push")
  },
  use: GitService.push,
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

The migration is not complete until all three levels agree through the IR and renewed evidence.

### TL;DR

Documentation says what should mean; the promoted explicit path says what currently runs; the DSL candidate must prove it means the same thing before it replaces anything.

## 2. The cycle state record

Every authoring or migration run should be describable by a machine-readable state record.

Proposed shape:

```json
{
  "schema": "mcel.application-authoring-cycle-state.v1",
  "appId": "inventory",
  "mode": "greenfield-dsl-candidate",
  "currentStage": "intent",
  "lastCompletedStage": "model",
  "promotedSemanticFingerprint": "sha256:...",
  "candidateSourceBindingFingerprint": "sha256:...",
  "candidateSemanticFingerprint": null,
  "blockingDiagnostics": [],
  "compatibilityStatus": "not-run",
  "evidenceStatus": {
    "acceptance": "not-run",
    "browserObservation": "not-run",
    "effectAccounting": "not-run",
    "proof": "not-run"
  },
  "nextRequiredDecision": "Declare add-item canonical transition."
}
```

This record must distinguish:

```text
last proven application
candidate application
completed authoring stages
blocking semantic decisions
invalidated generated projections
invalidated evidence
next safe action
```

### TL;DR

An AI should be able to ask “where am I?” and receive a semantic stage, not infer progress from which files happen to exist.

## 3. Cycle modes

The same stage model supports four common modes.

| Mode | Starting point | Primary comparison |
| --- | --- | --- |
| `greenfield-explicit` | New current-format scaffold | Requirements versus explicit package |
| `greenfield-dsl-candidate` | Future DSL scaffold | Requirements versus DSL/IR candidate |
| `legacy-migration` | Existing requirements, adapter, surface, blueprint, or explicit package | Imported legacy IR versus DSL IR |
| `application-change` | Existing promoted application plus requested change | Prior proven IR versus candidate IR |

The cycle does not assume that all applications already use the DSL.

### Example

Contract Counter currently follows the explicit package path. Contract Workbench follows the normalized high-level definition path. Git Tools and Code Editor use requirements and application-specific adapters. Document Editor is surface-led. Each can enter the cycle without pretending it already has a DSL source.

### TL;DR

One cycle governs greenfield work, legacy migration, and later feature changes; only the entry artifact differs.

# The stages

## 4. Stage `requirements`: settle the user-visible contract

### AI receives

A request such as:

```text
Build an inventory app where a user can add items, search them, remove them,
and request a supplier quote for each row.
```

### AI must declare

```text
user-visible operations
material constraints
state that must persist
mount-local behavior
external systems
risk and confirmation expectations
success, refusal, cancellation, failure, and recovery outcomes
claims that must be independently proven
```

### Example requirements decisions

```text
items are shared canonical state
search text is renderer-local
item.id is stable identity
quantity must be at least one
quote requests are cancellable
only the newest quote request for one item may commit
parallel quote requests for different items may proceed
```

### MCEL may generate

Nothing that changes meaning. It may validate documentation grammar, register requirement identifiers, and identify unresolved terms.

### Completion gate

The stage passes when no material ambiguity remains about:

```text
authority
identity
mutation ownership
external effects
confirmation
concurrency
cancellation
recovery
claimed outcomes
```

### Failure example

```text
“Save the document automatically.”
```

Unresolved questions include whether save is local or remote, what happens on stale source, whether drafts are retained, and what proves persistence. The AI must remain at `requirements`.

### TL;DR

Do not code around an unresolved authority, identity, lifecycle, or proof decision.

## 5. Stage `inventory`: locate current meaning before migration

This stage is required for an existing application and skipped only for a genuinely new app.

### AI must locate

```text
requirements authority
current source definition
current compiler, registry, extractor, adapter, or surface path
promoted explicit contracts
runtime projection
acceptance evidence
browser observation
effect or receipt evidence
truth status
```

### Example: Code Editor

The AI records where the current system represents:

```text
active file identity
local draft
loaded content hash
project-root containment
stale-source refusal
filesystem write
retained draft after refusal or failure
visible save receipt
```

### MCEL may generate

A legacy-source descriptor and an initial migration inventory entry. It may not silently infer missing semantics from the rendered UI.

### Completion gate

Every existing semantic feature receives one status:

```text
mapped
opaque but retained
known gap
conflicting
out of scope through an explicit versioned decision
```

### Failure example

A save button exists, but no current authority explains stale-source behavior. The inventory must record the gap; the DSL must not invent a silent answer.

### TL;DR

Before replacing an application compiler, locate every place where its meaning currently lives.

## 6. Stage `workspace`: create a safe candidate boundary

### AI chooses

```text
existing promoted package
candidate workspace
scaffold mode
ownership manifest
legacy-source descriptor when needed
```

### Current live greenfield command

The repository-local explicit scaffold is currently created with:

```text
python tools/mcel_create_app.py <app-id> --title "<Application title>"
```

The future DSL scaffold mode is specified but not implemented.

### Required boundary

```text
promoted package:
  remains live and unchanged

candidate workspace:
  receives DSL source, candidate IR, generated projections, diagnostics,
  compatibility output, and candidate-bound evidence
```

### Completion gate

Every file has one writer through the ownership contract, and candidate generation cannot overwrite the last proven application.

### Failure example

Both a human and the compiler edit `contracts/intents.js`. The AI must return to `workspace` and resolve ownership before semantic work continues.

### TL;DR

Create beside the live app. Do not use compilation as permission to rewrite proven files.

## 7. Stage `model`: declare types, authority, identity, and initialization

### AI must declare

```text
models and fields
canonical state
renderer-local state
derived state
provisional state
stable collection keys
initial values
invariants that belong to the model
```

### Example

```javascript
const Item = dsl.model("item", {
  id: field.id(),
  name: field.text().minLength(1),
  quantity: field.integer().minimum(1)
});

const items = state.canonical(
  "items",
  field.list(Item),
  {initial: []}
);

const search = state.local(
  "search",
  field.text(),
  {initial: ""}
);
```

### MCEL may generate

```text
schema records
state registry entries
revision plumbing
local mount storage
source provenance
stable internal references
```

### Completion gate

Every state value has:

```text
schema
authority
owner
initialization rule
stable identity where identity matters
```

### Failure example

```javascript
const search = state.value("search", "");
```

The declaration does not say whether search is canonical, local, derived, or provisional.

### TL;DR

The model stage answers what exists, who owns it, and how its identity survives change.

## 8. Stage `intent`: declare governed application changes

### AI must declare

```text
intent identity
input schemas
input source classes
refusals and preconditions
canonical transitions
postconditions
prohibited operations
```

### Example

```javascript
const addItem = intent.mutation("add-item", {
  input: {
    name: input.control(field.text().minLength(1)),
    quantity: input.control(field.integer().minimum(1))
  },

  refuses: ({input, refuse, expr}) => [
    refuse.when(expr.isBlank(input.name), "ITEM_NAME_REQUIRED"),
    refuse.when(expr.lessThan(input.quantity, 1), "ITEM_QUANTITY_INVALID")
  ],

  change: ({input, id}) => [
    items.append({
      id: id.next("item"),
      name: input.name,
      quantity: input.quantity
    })
  ]
});
```

### MCEL may generate

```text
read/write extraction
payload plumbing
revision checks
SCM operation contracts
receipt structures
intent coverage obligations
```

### Completion gate

Every canonical change is owned by one declared intent, and every input says both what it means and where it comes from.

### Failure example

A row removal intent accepts `itemId` from a free text control even though the requirement says it acts on the current row. Return to `intent` and declare `input.itemKey(...)`.

### TL;DR

An intent owns a semantic change; controls and handlers do not own canonical state.

## 9. Stage `effect`: declare external and asynchronous lifecycles

### AI must declare

```text
capability interface
risk
operation identity
request expression
provisional reconciliation
concurrency
cancellation
commit authority
allowed terminal dispositions
cleanup, retention, recovery, or compensation
```

### Example

```javascript
const requestQuote = intent.capability("request-quote", {
  input: {
    itemId: input.itemKey(items, Item.id)
  },
  use: QuoteService.requestQuote,
  operationKey: ({input}) => input.itemId,
  concurrency: dsl.concurrency.latestPerKey(),
  cancellation: dsl.cancellation.allowed(),
  effect: {
    allowedDispositions: ["committed", "cancelled", "superseded", "failed"],
    cleanup: dsl.cleanup.removeKey(quoteProgress, ({input}) => input.itemId)
  }
});
```

### MCEL may generate

```text
operation registry
AbortSignal plumbing
late-event rejection
runtime effect IDs
effect ledger entries
cleanup checks
receipt/evidence bindings
```

### Completion gate

Every consequential effect has an owner, target or operation key when required, legal dispositions, required evidence, and an explained terminal state.

### Failure example

A Git push allows `indeterminate` but declares no recovery path. Return to `effect`; do not paper over the uncertainty in a browser scenario.

### TL;DR

Declare lifecycle policy. Generate lifecycle machinery.

## 10. Stage `surface`: bind meaning to visible and interactive nodes

### AI must declare

```text
semantic regions
visible values
controls and their intents
collections and stable keys
receipts and status surfaces
conditional meaning
accessibility-relevant labels
```

### Example

```javascript
const primary = surface.define("primary", {
  root: surface.region("shell", {
    role: "application",
    children: [
      surface.form("add-item-form", {intent: addItem}),
      surface.input("search", {
        bind: search,
        control: "search",
        label: "Search items"
      }),
      surface.collection("items", {
        source: visibleItems,
        key: Item.id,
        row: (item) => [
          surface.text("name", {value: item.name}),
          surface.action("remove", {intent: removeItem})
        ]
      })
    ]
  })
});
```

### MCEL may generate

```text
control-local draft plumbing
parsers
payload binding
current-row key binding
semantic locators
observation bindings
```

### Completion gate

Every control and visible claim maps to declared semantics, every collection has stable identity, and the surface does not redefine authority.

### Failure example

A list uses array position as row identity. Return to `model` if identity is missing or `surface` if the declared key was simply omitted.

### TL;DR

The surface exposes semantic state and intents; it does not invent them.

## 11. Stage `layout`: place semantic regions without changing meaning

### AI must declare

```text
region placement
responsive modes
ordering constraints
scroll ownership
overflow behavior
minimum control sizes
```

### Completion gate

Layout constraints preserve the semantic surface, have one owner per region, and do not create hidden state or private control paths.

### Failure example

Document Editor selection and document scroll are both assigned ownership of the same scroll container. Return to `layout` and declare one owner.

### TL;DR

Layout controls space, not application authority.

## 12. Stage `scenario`: declare independent expected outcomes

### AI must declare

```text
initial conditions
ordered stimuli
canonical claims
visible claims
receipt claims
effect claims
refusal and nonoccurrence claims
cross-intent and multi-instance interactions
```

### Example

```javascript
const addSteel = prove.scenario("add-steel")
  .step("add", prove.invoke(addItem, {
    name: "Steel",
    quantity: 12
  }, {through: primary}))
  .expect(
    prove.receipt("add").disposition("committed"),
    prove.canonical(items).contains({name: "Steel", quantity: 12}),
    prove.visible(primary.node("items")).containsText("Steel"),
    prove.effects("add").allClosed()
  );
```

### MCEL may generate

```text
acceptance adapters
browser steps
semantic locators
intent-coverage bindings
effect-ledger assertions
```

### Completion gate

Every intent and consequential effect has coverage, and claimed outcomes are checked against authorities independent of the implementation expression.

### Failure example

The scenario asserts only that `add-item` returned success. It does not prove canonical state or visible output. Return to `scenario`.

### TL;DR

Declare the externally meaningful outcome once; generate the proof plumbing around it.

## 13. Stage `compile`: construct and validate the candidate semantic graph

### AI action

Compile the official source or import the current definition into candidate IR.

### MCEL must produce

```text
source-bound candidate IR
semantic fingerprint
source-binding fingerprint
diagnostics
normalization report
no promoted-file mutation
```

### Completion gate

The candidate IR is deterministic, fully referenced, acyclic and bounded, contains no illegal opaque behavior, and has no blocking compile or normalize diagnostics.

### Failure handling

Use `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`. Return to the diagnostic’s `repairStage`, not automatically to the top of the cycle.

### TL;DR

Compilation produces a candidate meaning and precise diagnostics; it does not promote an application.

## 14. Stage `repair`: fix the earliest independent semantic decision

### Required loop

```text
read root diagnostic
-> identify repairStage
-> choose only a safe or reviewed semantic repair
-> edit the authored source
-> rerun the narrowest required validation
-> regenerate the candidate fingerprint
-> continue from the earliest invalidated stage
```

### Example diagnostic

```text
MCEL_COLLECTION_KEY_REQUIRED
repairStage: surface
safe choices: Item.id or Item.sku
choice required: yes
```

The AI may not choose `Item.id` merely because it is first in the list unless requirements or an explicit deterministic policy authorizes that identity.

### Completion gate

No blocking root diagnostics remain. Cascading diagnostics disappear after their causes are repaired.

### TL;DR

Repair semantic causes in dependency order; do not edit generated files or chase secondary failures.

## 15. Stage `compatibility`: compare current and candidate meaning

Required for migration and dual-authored applications.

### MCEL compares

```text
requirements obligations
models and state authority
intents and refusals
capability/effect lifecycles
surfaces and layouts
scenarios and claims
opaque or unmapped legacy behavior
```

### Possible feature results

```text
exact
semantically-equivalent
intentional-versioned-delta
incomplete
conflicting
```

### Completion gate

Every in-scope feature is exact or semantically equivalent, or has an explicitly reviewed versioned delta with renewed requirements and proof obligations.

### Failure example

The DSL represents Git push success and failure but omits the legacy `indeterminate` and recovery path. Compatibility is `incomplete`; the candidate cannot promote.

### TL;DR

A visually similar candidate is not compatible when it loses a refusal, effect, lifecycle, recovery rule, or proof claim.

## 16. Stage `project`: generate explicit candidate artifacts

### MCEL produces

```text
generated low-level application definition
explicit package contracts
browser-safe runtime projection
blueprint or semantic document projection when required
acceptance and observation bindings
generation manifest and fingerprints
```

### Completion gate

Generated artifacts are deterministic, match the candidate IR, obey ownership rules, and remain in the candidate boundary.

### Failure example

A generated intent contract contains a write absent from the IR. Return to `project`; do not “fix” the generated file manually.

### TL;DR

Projection is a deterministic view of the IR. Drift is a compiler failure, not an authoring invitation.

## 17. Stage `acceptance`: prove semantic contracts

### Current repository authority

For a current package, the app-scoped acceptance command is:

```text
python main_computer/mcel_acceptance_runner.py --app <app-id> --check
```

### Completion gate

All enforceable in-scope scenarios pass and evidence fingerprints bind to the candidate package and semantic source.

### Failure return

The acceptance report must identify the earliest relevant stage, commonly `intent`, `effect`, or `scenario`.

### TL;DR

Acceptance proves declared semantic outcomes; it does not substitute for browser observation or external-effect accounting.

## 18. Stage `observe`: obtain independent browser/runtime evidence

### Current repository authority

```text
python main_computer/mcel_application_observation_runner.py --app <app-id> --check
```

### Completion gate

Declared surface, operation receipt, state, lifecycle, and isolation claims agree with independent Chromium observation.

### Failure example

Canonical state updates, but the mounted row does not. Return to `surface` or `project`, depending on whether the binding is absent from the IR or lost during projection.

### TL;DR

The browser must independently see what the semantic model claims became visible.

## 19. Stage `account`: reconcile consequential effects

### MCEL checks

```text
required effects occurred
prohibited effects did not occur
every observed effect has an owner
every runtime effect reached one legal disposition
cleanup or declared retention closed
uncertainty has recovery or remains explicitly unresolved
no unexplained residue remains
```

### Completion gate

The effect ledger is closed for every scenario and operation instance.

### Failure example

A cancelled quote request leaves provisional progress behind. Return to `effect` if cleanup policy is missing or `project/runtime` if the declared cleanup failed to execute.

### TL;DR

A final screen is not enough; every consequential side effect must be explained.

## 20. Stage `prove`: reconcile all independent authorities

### Current repository authority

```text
python main_computer/mcel_app_prove.py --app <app-id> --check
```

### Required agreement

```text
package validity
application discovery
generated projection identity
operation conformance
intent-complete coverage when applicable
surface conformance
acceptance evidence
browser observation
effect accounting
repository/source binding
truth gate
```

### Completion gate

The candidate reaches its declared truth target, normally `semantic-runtime-proven`, without borrowing stale evidence from the previous fingerprint.

### TL;DR

Proof is the reconciliation verdict over independent authorities, not another compiler success message.

## 21. Stage `promote`: replace the live application atomically

### Preconditions

```text
candidate compile and normalization pass
compatibility pass
candidate projections pass
fresh acceptance and browser evidence
closed effect accounting
proof pass
reviewed migration and ownership records
rollback artifact available
```

### Promotion result

```text
candidate generated files become promoted files atomically
promoted fingerprints update
old promoted files remain recoverable
candidate evidence becomes promoted evidence
legacy authority changes only as the migration state permits
```

### Failure behavior

The old proven application remains live. A failed promotion cannot leave half of the generated package replaced.

### TL;DR

Promotion is a reviewed state transition, not the side effect of compiling.

## 22. Stage `modify`: re-enter at the earliest affected decision

A feature change should not restart blindly at scaffolding, nor skip directly to source editing.

### Example request

```text
Add item priority, display it in each row, and allow filtering by priority.
```

Independent decisions affected:

```text
model: Item.priority schema and default
intent: add-item receives and writes priority
surface: form control and row field
derivation: visibleItems filtering
scenario: creation and filtering claims
```

Likely unaffected:

```text
quote cancellation policy
Git capability boundaries
unrelated remove-item semantics
```

The semantic change-impact specification will define exact invalidation rules. Until then, the AI must conservatively record affected stages and evidence rather than assume full reuse.

### TL;DR

One edit per independent decision; renew only evidence whose semantic basis changed.

# Cycle control rules

## 23. Do not advance on file presence

The existence of `application.js`, `contracts/`, or a browser projection does not prove a stage is complete.

```text
file exists != semantic decision complete
compiler passes != compatibility passes
compatibility passes != browser observation passes
browser observation passes != effect accounting closes
```

### TL;DR

Stages close through semantic gates and evidence, not directory shape.

## 24. Do not borrow proof from the last promoted fingerprint

If a candidate changes semantic meaning, old acceptance or observation evidence remains valid only for the old fingerprint.

```text
promoted semantic fingerprint: sha256:OLD
candidate semantic fingerprint: sha256:NEW

OLD evidence cannot prove NEW
```

A narrow change may renew only affected evidence when the future change-impact model proves the remaining evidence is still bound to unchanged semantics.

### TL;DR

The old app remains true; its evidence does not automatically transfer to the candidate.

## 25. Stop only for consequential ambiguity

The AI may apply deterministic presentation defaults when the specification authorizes them.

It must stop when ambiguity changes:

```text
authority
identity
canonical writes
input source
capability selection
confirmation
concurrency
cancellation
recovery
retention
claimed outcome
```

### TL;DR

Defaults may fill plumbing and presentation; they may not invent application policy.

## 26. Current and future command boundaries

The current repository provides live commands for explicit scaffolding, package discovery/projection, acceptance, observation, and proof.

```text
python tools/mcel_create_app.py <app-id> --title "<title>"
python tools/mcel_application_packages.py
python tools/mcel_application_runtime_projection.py --check
python tools/mcel_application_package_browser_catalog.py --check
python main_computer/mcel_acceptance_runner.py --app <app-id> --check
python main_computer/mcel_application_observation_runner.py --app <app-id> --check
python main_computer/mcel_app_prove.py --app <app-id> --check
```

A bounded Counter-only DSL candidate compiler is now live:

```text
python tools/mcel_dsl_compile.py --input tests/fixtures/mcel_dsl/contract-counter.application.js --compare-ir tests/fixtures/mcel_application_ir/contract-counter.ir.json
```

It may stage candidate IR with `--write-candidate`. It does not generate contracts, run candidate evidence, promote files, or replace the compatibility, repair, evidence, and atomic-promotion systems, which remain proposed.

### TL;DR

Use current repository commands for current packages. Use the Wave 2B command only for the Counter candidate IR lane; treat projection, candidate evidence, promotion, and broad application migration commands as contracts to be implemented later.

## 27. Cycle completion criteria

An application-authoring cycle is complete only when:

```text
requirements are settled
all current semantic sources are inventoried when migration is involved
one candidate source owns each new semantic decision
candidate IR is valid and deterministic
compatibility is complete
projections match the IR
acceptance passes
browser observation passes
effects reconcile
proof reaches the target truth status
promotion is atomic and reviewable
migration records are updated
```

For DSL v1, the cycle must also be repeatable by an AI using the official syntax, diagnostics, pattern catalog, and change-impact rules without manual edits to generated artifacts.

### TL;DR

The cycle ends when meaning, execution, observation, proof, and migration state agree.

## Benchmark use

`pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` measures this cycle end to end. It records stage entry and re-entry, authored decisions, compiler diagnostics, compatibility, evidence renewal, proof, promotion safety, and later modification rather than measuring source generation alone.

### TL;DR

The benchmark treats the complete authoring cycle as the unit of work.

## Documentation completeness result

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` confirms that every cycle stage has an authored input, generated output, completion gate, failure return point, authority boundary, and migration consequence. The cycle remains a specification until the IR/compiler and evidence tooling exist.

### TL;DR

The AI now has a documented route; implementation must make the route executable without skipping its gates.
