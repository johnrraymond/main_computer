# MCEL Application IR and Compiler Migration Model

This document defines the stable semantic target for MCEL applications and the migration model that allows existing application definitions, scaffolded packages, and the future official vanilla-JavaScript DSL to coexist without becoming competing truths.

It is a documentation specification. It does not authorize an IR implementation, a DSL compiler, or the retirement of any current application path.

## The short answer

MCEL currently has several ways to describe or assemble an application:

```text
documentation-first requirements and high-level contracts
current application-specific adapters, extractors, and bridges
scaffolded explicit application packages
normalized high-level application.js definitions
future official vanilla-JavaScript DSL
```

These must become compiler front ends for one stable semantic target:

```text
legacy requirements/application front ends ─┐
scaffolded explicit package front end ───────┼─> MCEL Application IR
current high-level application.js ───────────┤
future official vanilla-JavaScript DSL ──────┘
                                                    |
                                                    v
                                      replaceable generated projections
                                                    |
                           runtime + acceptance + browser observation + proof
```

The IR is the stable application meaning. Source forms and compilers may change. Generated package layouts may change. The IR must remain deterministic, comparable, and complete enough to explain the application and its consequential effects. `pretty_docs/mcel-ai-application-authoring-cycle.md` defines how an AI advances each authored level toward this center, `pretty_docs/mcel-ai-authoring-pattern-catalog.md` supplies recurring semantic slices used to test front-end equivalence, and `pretty_docs/mcel-semantic-change-and-evidence-impact.md` defines how a later semantic delta propagates across those levels and their evidence.

### TL;DR

MCEL should migrate compilers, not repeatedly redefine application meaning.

## 1. The three authoring levels that must remain visible

The migration tracks three distinct authoring levels.

### Level A: documentation-defined meaning

Examples include:

```text
pretty_docs/mcel-git-tools-requirements.md
pretty_docs/mcel-code-editor-requirements.md
pretty_docs/mcel-document-editor-surface.md
pretty_docs/mcel-requirements-language.md
```

These documents state:

```text
what the user needs
which operations exist
which risks and authorities apply
which behavior is prohibited
which receipts and recovery paths are required
which visible and proof outcomes are expected
```

They are the authority for intended product behavior and constraints. They are not, by themselves, runtime proof.

### Level B: current executable or explicit MCEL form

This level currently has more than one repository shape.

Requirements-driven existing applications reach MCEL through combinations of:

```text
requirements registry records
blueprints
semantic adapters
surface extractors
layout builders
runtime checks
app-local controllers and bridges
acceptance bindings
```

Scaffolded applications use explicit package contracts such as:

```text
contracts/domain.js
contracts/intents.js
contracts/adapter.js
contracts/surface.js
contracts/layout.js
contracts/acceptance.js
contracts/observation.js
```

Contract Workbench has a high-level human-owned `application.js` that the current normalizer exports to `mcel.application-definition.normalized.v1` and lowers into those explicit contracts.

There is not yet one uniform legacy compiler. The phrase **legacy compiler front end** in this document includes the existing parser, registry, adapter, extractor, normalizer, and generator chains that turn an older source form into executable MCEL structures.

### Level C: emerging official DSL

The future official authoring source is one constrained, valid-vanilla-JavaScript DSL.

Conceptually:

```javascript
export default defineApp("inventory", ({state, intent, view, prove}) => {
  const items = state.canonical.list("items", Item, []);

  const addItem = intent.mutation("add-item", {
    input: {
      name: field.text().required()
    },
    change: items.append({
      id: nextId("item"),
      name: input.name
    })
  });

  view.page(
    view.form(addItem),
    view.collection(items, {key: Item.id})
  );
});
```

During migration, this is a candidate front end. It does not become the sole authoring authority merely because syntax exists.

### TL;DR

Documentation says what the app must mean. Current explicit forms say what MCEL can execute today. The DSL is the future source. The IR is where their executable meanings must meet.

## 2. The stable compiler architecture

The target compiler architecture is:

```text
source front end
  -> source validation
  -> MCEL Application IR
  -> IR validation
  -> generated projections
  -> runtime execution
  -> independent evidence
  -> truth reconciliation
```

### Front ends

A front end translates one source representation into the IR.

Examples:

```text
requirements/adapter migration front end
scaffolded explicit-package import front end
current application.js normalization front end
official DSL front end
```

### IR

The IR carries the complete executable semantic meaning in a source-independent form.

### Back ends and projections

Back ends derive replaceable artifacts from the IR:

```text
low-level explicit application definition
package contracts
runtime projection
browser package catalog
acceptance obligations
observation scenarios
proof-coverage bindings
diagnostic and inspection views
```

### Evidence authorities

Acceptance and browser observation do not become compiler output claims that automatically prove themselves. They remain independently executed evidence that must agree with the IR-bound application.

### TL;DR

Front ends explain source. The IR records meaning. Back ends generate machinery. Evidence checks reality.

## 3. What the MCEL Application IR is

The IR is a canonical, deterministic graph of application semantics.

It must be:

```text
syntax-independent
serializable
schema-validatable
source-traceable
deterministically ordered
stable-ID based
semantically diffable
complete enough for runtime and proof generation
free of unexplained consequential effects
```

It is not merely:

```text
a copy of the source AST
a DOM tree
a package-file manifest
a list of test cases
a compiled JavaScript bundle
the current normalized Workbench JSON renamed as v1
```

The current `mcel.application-definition.normalized.v1` is valuable implementation evidence. It proves that a high-level definition can be deterministically exported and lowered. The final IR may reuse parts of it, but the IR specification must not inherit opaque function bodies or current package-layout assumptions without review.

### TL;DR

The IR is the stable meaning of an application, not a frozen snapshot of today’s compiler internals.

## 4. Minimum IR domains

The IR must represent at least these domains.

| Domain | Required meaning |
| --- | --- |
| Application | Stable app identity, title, version, source provenance |
| Models | Records, scalar schemas, collections, defaults, invariants |
| State | Canonical, renderer-local, derived, and provisional authorities |
| Intents | Inputs, value sources, reads, writes, refusals, transitions, outcomes |
| Capabilities | External authority, request/response/event schemas, risk |
| Lifecycles | Operation identity, progress, cancellation, concurrency, cleanup |
| Surface | Semantic regions, nodes, controls, bindings, conditions, collections |
| Layout | Semantic ownership and constraints, not accidental pixels |
| Effects | Owner, target, authority, evidence requirements, allowed dispositions |
| Scenarios | Stimuli and explicit claims against independent authorities |
| Proof | Coverage obligations and effect-accounting requirements |
| Provenance | Source locations and compiler origin for every meaningful node |

A front end is incomplete when it cannot produce one of the IR domains required by the source application.

### TL;DR

The IR must contain the whole app: behavior, authority, surface, effects, and proof—not only state and UI.

## 5. Stable identity and source provenance

Every semantically meaningful IR node must have a stable ID.

Example:

```json
{
  "id": "git-tools.intent.push-current-branch",
  "kind": "intent",
  "sourceName": "pushCurrentBranch",
  "source": {
    "frontend": "requirements-registry",
    "file": "pretty_docs/mcel-git-tools-requirements.md",
    "blockId": "git-tools.intent.push-current-branch"
  }
}
```

A DSL front end may point to JavaScript source coordinates:

```json
{
  "id": "inventory.intent.add-item",
  "source": {
    "frontend": "mcel-dsl-v1",
    "file": "mcel_apps/inventory/application.js",
    "startLine": 18,
    "startColumn": 3,
    "endLine": 31,
    "endColumn": 5
  }
}
```

Generated descendant IDs may be derived mechanically from stable parents. Mutable labels must not define semantic identity.

### TL;DR

The IR ID says what the node is. Provenance says where a human or AI must repair it.

## 6. Counter example: the smallest equivalence slice

### Documentation meaning

```text
count is canonical
increment exclusively adds one
reset writes zero
direct-set is prohibited
the visible count reflects committed canonical state
```

### Current explicit form

Counter currently carries explicit domain, intent, adapter, surface, acceptance, and observation contracts. Its proof remains correctly classified as legacy evidence because it does not yet have normalized intent-complete source convergence.

### Candidate DSL

```javascript
const count = state.canonical.integer("count", 0);

const increment = intent.mutation("increment", {
  change: count.add(1)
});

const reset = intent.mutation("reset", {
  change: count.set(0)
});

const directSet = intent.prohibited("direct-set");
```

### Canonical IR slice

```json
{
  "state": [
    {
      "id": "contract-counter.state.count",
      "authority": "canonical",
      "schema": {"kind": "integer"},
      "initial": 0
    }
  ],
  "intents": [
    {
      "id": "contract-counter.intent.increment",
      "kind": "mutation",
      "reads": ["contract-counter.state.count"],
      "writes": ["contract-counter.state.count"],
      "transition": {
        "kind": "number.add",
        "target": "contract-counter.state.count",
        "value": 1
      }
    },
    {
      "id": "contract-counter.intent.direct-set",
      "kind": "prohibited",
      "writes": []
    }
  ]
}
```

### Required comparison

The legacy import and DSL front end must agree on:

```text
canonical authority
initial value
intent identities
write set
transition meaning
prohibition
visible binding
acceptance claims
```

### TL;DR

Counter proves that the IR can express a complete simple app without preserving package-file ceremony.

## 7. Git Tools example: requirements-driven migration

Git Tools currently declares high-level intents in its requirements document. For example, `push-current-branch` requires fresh state, a selected remote, explicit confirmation, and a successful preflight; it has `remote-mutation` risk and must produce execution and recovery evidence.

### Documentation source

```text
intent: pushCurrentBranch
risk: remote-mutation
requires:
  successful preflight
  explicit confirmation
  current branch
  selected remote target
  non-stale repository state
produces:
  push execution receipt
  updated repository evidence
  recovery classification on failure
```

### Current executable path

The current application meaning is distributed across:

```text
requirements registry
Git Tools semantic adapter
backend operations
surface and layout bindings
runtime checks
acceptance and truth evidence
```

This distributed chain is the legacy compiler front end for migration purposes.

### Future DSL concept

```javascript
const pushCurrentBranch = intent.effect("push-current-branch", {
  risk: risk.remoteMutation,
  requires: [
    repositoryState.fresh,
    selectedRemote.present,
    confirmation.explicit
  ],
  capability: git.pushCurrentBranch,
  effects: [
    effect.remoteMutation(selectedRemote),
    effect.receipt("git-tools-push-current-branch-receipt")
  ],
  outcomes: {
    committed: repositoryState.refresh(),
    failed: recovery.classify()
  }
});
```

### IR requirement

The IR must preserve:

```text
risk classification
preflight dependency
confirmation requirement
remote target authority
remote mutation effect
freshness guard
receipt requirement
success reconciliation
failure and recovery disposition
```

A DSL migration that merely calls `git.push()` is not equivalent.

### TL;DR

The IR must retain the safety and evidence meaning currently carried by requirements and adapters, not only the backend action name.

## 8. Code Editor example: side-effect completeness

The Code Editor requirements distinguish local draft editing from saving a file.

### Documentation meaning

```text
edit-draft:
  local-state risk
  produces a dirty draft and visible dirty state

save-file:
  local-file-mutation risk
  requires active file, dirty draft, stale-source check, and write policy
  produces saved source, updated dirty state, and a write receipt
```

### Candidate IR effect record

```json
{
  "id": "code-editor.effect.save-file-write",
  "kind": "local-file-mutation",
  "owner": "code-editor.intent.save-file",
  "target": {"kind": "input-reference", "path": "activeFile.path"},
  "guards": [
    "code-editor.guard.active-file-present",
    "code-editor.guard.dirty-draft-present",
    "code-editor.guard.source-not-stale",
    "code-editor.guard.write-policy-allows"
  ],
  "allowedDispositions": [
    "committed",
    "refused",
    "stale",
    "failed"
  ],
  "requiredEvidence": [
    "source-before-hash",
    "source-after-hash",
    "write-receipt",
    "dirty-state-reconciliation"
  ]
}
```

The final visible editor state does not explain whether a stale write was attempted, refused, partially completed, or committed. The IR and proof obligations must account for the effect disposition.

### TL;DR

Side effects are part of application meaning. They cannot remain hidden in backend code while the IR records only the final screen.

## 9. Document Editor example: semantic surface compatibility

Document Editor has important surface meaning even where a complete semantic adapter is not yet live.

The requirements include independent scroll owners and anchored regions:

```text
outline scrolls independently
document pages own document scrolling
companion remains anchored and scrolls internally
file picker owns modal scrolling
```

A compatible IR must express the semantic ownership:

```json
{
  "regions": [
    {
      "id": "document-editor.region.navigation",
      "role": "document-outline-navigation",
      "scrollOwner": "document-editor.node.document-outline"
    },
    {
      "id": "document-editor.region.primary",
      "role": "document-authoring-lane",
      "scrollOwner": "document-editor.node.document-pages"
    },
    {
      "id": "document-editor.region.companion",
      "role": "ai-companion",
      "anchored": true,
      "scrollOwner": "document-editor.node.companion-content"
    }
  ]
}
```

The IR should not freeze browser pixel positions. It must preserve ownership and behavior that a layout projection must satisfy.

### TL;DR

Application IR includes semantic layout obligations, not merely operations and data.

## 10. Compatibility is semantic, not textual

Two compiler outputs do not need identical source text or identical generated file layouts.

They must agree at defined semantic comparison layers.

| Comparison layer | Examples |
| --- | --- |
| Identity | App, model, state, intent, capability, surface, scenario IDs |
| Authority | Canonical/local/provisional ownership, capability and mutation authority |
| Schema | Inputs, state, events, responses, receipts |
| Behavior | Reads, writes, transitions, refusals, invariants |
| Effects | Risk, target, lifecycle, dispositions, cleanup, evidence |
| Surface | Regions, nodes, bindings, item identity, semantic layout obligations |
| Proof | Scenario claims, coverage, independent authorities, effect accounting |

Allowed comparison results are:

```text
exact
semantically-equivalent
intentional-versioned-delta
incomplete
conflicting
```

`semantically-equivalent` requires a named equivalence rule. It must not mean “the screens looked similar.”

`intentional-versioned-delta` requires an approved documentation change and renewed evidence.

`incomplete` and `conflicting` block migration promotion.

### TL;DR

Compare application meaning layer by layer. Do not compare only files, snapshots, or happy-path output.

## 11. Per-feature migration ledger

Migration must be tracked per semantic feature, not only per application.

Example:

| Feature | Documentation | Legacy/current form | DSL form | IR comparison | Evidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Counter canonical count | present | explicit contract | drafted | exact | acceptance + browser | dual-authored |
| Counter direct-set refusal | present | explicit prohibited intent | missing | incomplete | legacy acceptance | blocked |
| Git Tools push preflight | present | adapter/backend chain | drafted | semantically-equivalent | runtime + receipt | dual-authored |
| Git Tools remote failure recovery | present | current recovery path | missing | incomplete | partial | blocked |
| Document Editor scroll ownership | specified | app-local layout bridge | drafted | unresolved | browser fit evidence | blocked |

The ledger must identify omissions. An application cannot be declared migrated because its easiest intents compile.

### TL;DR

Migration completeness is the sum of feature-level equivalence, not the existence of a DSL file.

## 12. Migration states

Each application and each feature may have one of these states.

### `documentation-only`

Meaning is specified, but no complete executable semantic form exists.

### `legacy-compiled`

Current registries, adapters, bridges, explicit contracts, or normalizers produce executable behavior. No DSL representation exists.

### `dual-authored`

Legacy/current and DSL front ends both produce IR. Their compatibility is checked. Neither may silently drift.

### `dsl-primary-explicit-shadow`

The DSL is edited. The IR and explicit application/package forms are generated. The legacy form remains as an independently compared shadow during the migration window.

### `dsl-v1`

The official DSL is the code-authoring authority. The IR is the machine semantic authority. Explicit package forms are deterministic generated projections. Legacy front ends may be retired for the covered application version.

Promotion requires feature-level completeness and proof; it is not a manual label.

### TL;DR

Applications move through explicit migration states. “Has DSL source” is not the final state.

## 13. Required work in every migration pass

Each DSL or IR design pass must inspect all three authoring levels.

For the feature being changed, the pass must answer:

1. **Documentation:** What behavior, authority, risk, and claimed outcome are specified?
2. **Current form:** Where is that meaning executable today?
3. **DSL:** How is each independent semantic decision expressed once?
4. **IR:** What canonical nodes and edges represent the meaning?
5. **Compatibility:** Are current and DSL-produced IR exact or semantically equivalent?
6. **Projections:** Which generated contracts, runtime artifacts, and scaffolder outputs change?
7. **Evidence:** Which acceptance, browser, receipt, and effect-accounting evidence must be renewed?
8. **Migration ledger:** Which feature and app states change?

A pass need not modify every physical file. It must explicitly determine whether every level is affected.

### Example pass: add collection identity

```text
Documentation:
  contracts use stable contract.id identity

Current form:
  surface collection keyPath = id
  row controls receive item key

DSL:
  view.collection(contracts, {key: Contract.id})

IR:
  collection identity edge -> Contract.id

Generated:
  row-key binding and item-action payload source

Evidence:
  sorting preserves row identity
  item-local async work remains attached to the correct contract
```

### TL;DR

Every pass checks meaning, current execution, DSL expression, IR, projections, and proof.

## 14. Generated versus authored boundaries during migration

During the transition, file ownership must be explicit.

A target package may eventually look like:

```text
mcel_apps/inventory/
  requirements.md                       human/AI-authored product meaning
  application.js                        official DSL source
  generated/
    mcel.application.ir.json             generated canonical IR
    application.definition.js            generated low-level explicit projection
    contracts/                            generated package contracts
  tests/
    authored-scenarios.js                 authored claimed outcomes when not colocated
    generated-bindings/                   generated proof plumbing
```

`pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` now settles the target paths, live-versus-candidate boundary, and machine-readable ownership rules. The governing ownership classes are:

```text
requirements and approved DSL source: authored
IR and low-level projections: generated
acceptance/browser evidence: observed
proof report: reconciled
```

Manual edits to generated projections must be detected. They cannot become an undocumented fourth authoring level.

### TL;DR

Authored meaning, generated machinery, observed evidence, and reconciled truth must remain visibly separate.

## 15. Relationship to the current scaffolder

The current scaffolder creates a complete explicit package skeleton and golden fixture. That remains useful during migration.

It is not the final DSL compiler.

The scaffolder should evolve in stages:

```text
current:
  create explicit package files and tests

migration:
  create requirements + DSL source + explicit generated targets
  preserve legacy fixture comparison where required

v1:
  create official DSL workspace
  compile to IR
  project deterministic explicit package artifacts
  install proof and observation entry points
```

Scaffolding and compilation remain distinct responsibilities:

```text
scaffolder creates a safe workspace
front end compiles authored semantics
IR back ends generate executable projections
proof tools verify the result
```

### TL;DR

The app-creation script should eventually scaffold the DSL pipeline, not remain an alternative authoring system.

## 16. Relationship to the current Workbench normalizer

Contract Workbench proves this current path:

```text
human-owned application.js
  -> Node exporter
  -> normalized definition
  -> seven explicit package contracts
  -> runtime projection
  -> acceptance + Chromium observation
  -> intent-complete proof
```

This is the closest live prototype of the future compiler architecture.

But the current definition permits JavaScript functions that the normalizer serializes and later emits. `pretty_docs/mcel-constrained-expression-model.md` now requires final v1 behavior to become typed core expressions or registered pure domain operators; current opaque callbacks may survive only as migration-quarantined records and block DSL-v1 completion for the affected feature.

Therefore:

```text
current normalized definition = migration input and evidence
future MCEL Application IR = stable specified target
```

They must not be declared identical before the expression and effect models are documented.

### TL;DR

Workbench proves the pipeline shape. It does not automatically define the final IR vocabulary.

## 17. Compatibility validator responsibilities

A future compatibility validator must be able to compare two IR documents and report differences at semantic paths.

Example:

```text
MCEL_IR_COMPATIBILITY_CONFLICT

app: git-tools
feature: git-tools.intent.push-current-branch
path: intents.push-current-branch.effects.remote-mutation.confirmation

legacy IR:
  explicit-confirmation required

DSL IR:
  no confirmation guard

result:
  conflicting

migration impact:
  DSL promotion blocked
  remote-mutation acceptance invalid
  push browser and receipt evidence must not be reused
```

The validator should also identify harmless generated differences:

```text
MCEL_IR_COMPATIBILITY_EQUIVALENT

path: surface.git-status.generated-node-id
legacy: git-tools-node-17
DSL: git-tools.surface.status.summary
rule: generated-descendant-id-renaming-v1
result: semantically-equivalent
```

### TL;DR

Compatibility errors must tell an AI exactly which semantic decision was lost or changed.

## 18. Evidence renewal and change impact

A compiler comparison alone does not prove runtime equivalence.

When IR changes, MCEL must identify affected evidence.

Example:

```text
change:
  request-quote concurrency changes from latest-per-item to parallel-per-item

IR impact:
  lifecycle policy changed
  supersession effect removed
  operation registry behavior changed

renew:
  capability acceptance
  parallel item browser scenario
  cancellation scenario
  late-event handling
  effect-accounting proof

may retain:
  add-contract proof
  remove-contract proof
  static surface proof
```

Evidence must be bound to the IR fingerprint and relevant semantic subgraph where practical.

### TL;DR

Equivalent IR permits evidence comparison. Changed IR requires targeted re-proof.

## 19. Conditions for retiring a legacy compiler path

A legacy front end may be retired for an application only when:

1. every in-scope documented feature has a DSL representation;
2. the DSL compiles to schema-valid canonical IR;
3. the legacy/current path can be imported or projected to comparable IR;
4. all comparisons are `exact`, `semantically-equivalent`, or approved `intentional-versioned-delta`;
5. no feature remains `incomplete` or `conflicting`;
6. consequential effects have complete disposition and evidence obligations;
7. generated explicit contracts remain inspectable and deterministic;
8. application acceptance passes;
9. browser observation passes;
10. repository binding is exact;
11. the truth gate reaches the required status;
12. rollback to the last proven compiler path remains possible for the migration release.

Retirement is application-version-specific. Retiring one Git Tools legacy path does not retire all requirements-driven application paths.

### TL;DR

A compiler is retired after semantic and runtime equivalence, not after the DSL can produce a page.

## 20. Conditions for MCEL DSL v1

DSL v1 requires more than a stable syntax.

At minimum:

```text
one official vanilla-JavaScript syntax is documented
canonical IR schema and normalization rules are documented
constrained expressions are documented
consequential effects and proof accounting are documented
compiler diagnostics and repair protocol are documented in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`
scaffolder and generated-file ownership are documented
compatibility and migration ledger formats are documented
```

The implementation must then prove representative migrations:

```text
Contract Counter
  minimum ceremony and prohibition

Contract Workbench
  complete dynamic and asynchronous semantics

at least one requirements-driven operational app
  Git Tools or Code Editor

at least one surface-heavy existing app
  Document Editor or equivalent
```

Each representative app must pass the required IR comparison and runtime proof for its claimed scope.

### TL;DR

DSL v1 is a proven migration system, not merely a parser and helper library.

## 21. Documentation sequence from here

This document fixes the compiler and migration architecture. The concrete schema is now specified in `pretty_docs/mcel-application-ir-schema-and-normalization.md`, and the repository-grounded definition-family ledger is `pretty_docs/mcel-existing-application-definition-migration-inventory.md`. The remaining specifications should be completed in this order:

1. **Constrained expression model** — specified in `pretty_docs/mcel-constrained-expression-model.md`: typed inspectable transitions, derivations, validation, reconciliation, bounded domain operators, and migration-only opaque callbacks.
2. **Consequential effects and proof accounting** — specified in `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`: effect classes, owners, runtime instances, evidence, dispositions, cleanup, retained residue, uncertainty, recovery, and completeness.
3. **Official vanilla-JavaScript DSL syntax** — specified in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`: one strict CommonJS source form, deterministic app/module composition, stable semantic handles, constrained builder callbacks, effect/lifecycle declarations, and proof scenarios that construct the IR.
4. **Compiler diagnostics and repair protocol** — specified in `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`: stable codes and keys, semantic paths, source provenance, safe repair classes, dependency ordering, candidate truth, evidence invalidation, narrow reruns, and reviewable repair transactions.
5. **Scaffolder, low-level projection, and compatibility details** — specified in `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md`.
6. **AI application authoring cycle and pattern catalog** — specified in `pretty_docs/mcel-ai-application-authoring-cycle.md` and `pretty_docs/mcel-ai-authoring-pattern-catalog.md`.
7. **Semantic change and impact model** — specified in `pretty_docs/mcel-semantic-change-and-evidence-impact.md`: deterministic semantic deltas, dependency closure, projection/evidence invalidation, evidence reuse records, and conservative renewal.
8. **AI authoring and migration benchmark** — specified in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`: controlled creation, migration, modification, repair, equivalence, proof, reliability, and economy measurements.
9. **Documentation completeness review** — completed in `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md`; it confirms that the migration architecture is ready for an explicitly authorized structural IR-kernel implementation.

No IR or DSL compiler implementation is authorized by this document.

## Final rule

> Requirements, existing application definitions, and the official DSL may be authored at different levels, but every executable path must converge on one comparable MCEL Application IR. Every migration pass must preserve or explicitly version the application’s authority, behavior, surface, consequential effects, and proof obligations across all three levels.

## Documentation completeness result

`pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` confirms that the three-level migration model is represented across the IR, DSL, importers, projections, evidence, change-impact, and benchmark contracts. It permits an explicitly authorized IR-kernel implementation while leaving every current compiler and promoted application authoritative until dual-authored compatibility and renewed proof pass.

### TL;DR

The migration architecture is ready to measure current meaning before it attempts to replace anything.

## Counter projection implementation checkpoint

The Counter migration now reaches the isolated-projection checkpoint. The official DSL, live explicit importer, and fixture IR converge on one semantic fingerprint; the Wave 4 projector then emits the explicit Counter contracts into candidate runtime state and proves that the generated package imports back to the same IR. The candidate package and runtime fingerprints are exact with the live package, but the legacy explicit package remains the execution authority.

No other application family has reached this checkpoint, and no compiler path has been retired.

### TL;DR

Counter now proves IR-to-explicit-package round-trip equivalence without promotion.

# Wave 5 implementation note

Counter-bounded Wave 5 adds independent evidence for the isolated generated package. `main_computer/mcel_counter_candidate_evidence.py` creates a disposable repository workspace under the candidate runtime-state directory, overlays the Wave 4 package shadow, and runs the existing package catalog, runtime projection, acceptance, Chromium observation, and application-proof authorities against that workspace. It never swaps candidate files into `mcel_apps/contract-counter`.

The candidate evidence root is source-binding specific:

```text
runtime/reports/mcel-compiler-candidates/
  contract-counter/
    <dsl-source-binding-fingerprint>/
      acceptance/
      observation/
      effects/
      proof/
      mcel-candidate-evidence-report.json
      mcel-candidate-evidence-report.md
```

Wave 5 also reconciles the four declared Counter canonical-write effects through independent Node and Chromium probes. Increment and reset must close their count and revision effects as `completed`; stale increment must close the same declared effects as `refused-before-attempt`; prohibited direct-set must produce no canonical effect. The final candidate report requires fresh evidence, exact repository binding inside the isolated workspace, `semantic-runtime-proven`, no live-package change, no evidence reuse, and no promotion.

**TL;DR:** Wave 5 lets the generated Counter candidate earn proof independently while the legacy package remains live authority.

# Wave 6 implementation note

Counter-bounded Wave 6 adds a non-mutating authority-transition rehearsal in `main_computer/mcel_counter_promotion_rehearsal.py` and `tools/mcel_counter_promotion_rehearsal.py`. It requires the exact Wave 4 candidate and fresh Wave 5 `semantic-runtime-proven` evidence bound to the same semantic and source-binding fingerprints.

The proposed promotion shape is explicit and reviewable:

```text
mcel_apps/contract-counter/application.js
  becomes the authoritative mcel.dsl.v1 source

mcel_apps/contract-counter/mcel.generated.json
  records generated-file ownership and exact hashes

mcel_apps/contract-counter/mcel.app.json
  declares dsl-authoritative authoring

contracts/*.js
  are deterministically rewritten from canonical IR as derived artifacts
```

The plan is applied only in a disposable repository workspace. The rehearsal reruns three-way semantic compatibility, package validation, runtime projection, package-local acceptance, Chromium observation, effect accounting, application proof, and repository binding. It then applies rollback material and requires the original package, catalog, runtime-projection, and semantic fingerprints to be restored exactly.

A passing result means `promotionEligible: true`, not that promotion occurred. The live authority remains `legacy-explicit-package`, `promotionExecuted` remains false, and the live repository is source-fingerprint checked before and after.

**TL;DR:** Wave 6 proves that the Counter authority transition and rollback are executable and reversible without performing either operation on the live repository.

# Wave 7 implementation note

Counter-bounded Wave 7 adds the live transaction executor in `main_computer/mcel_counter_promotion.py` and `tools/mcel_counter_promotion.py`. The executor accepts no ad hoc file set. It requires the current Wave 6 plan, exact candidate evidence binding, exact before-hashes, exact staged payload hashes, and a repository-wide promotion lock.

The transaction captures the protected Counter and shared MCEL authority sources before mutation, stages all target bytes as sibling temporary files, and replaces them only after every temporary hash passes. The post-apply proof chain runs against the live repository and must preserve the canonical Counter semantic fingerprint while establishing DSL source authority and generated contract ownership.

Automatic rollback restores the pre-transaction protected snapshot when execution fails. Committed transactions retain durable rollback material and a post-promotion protected snapshot. Explicit rollback requires the current protected source tree to equal that post-promotion snapshot, then restores both source files and package/catalog/runtime/semantic fingerprints exactly.

**TL;DR:** Wave 7 turns the rehearsed Counter migration into a guarded, auditable, reversible live authority transition rather than a manual source-file replacement.

# Wave 8 implementation note

Counter-bounded Wave 8 adds `mcel_counter_ir_native_proof.py` as the first proof authority that begins at a promoted `mcel.dsl.v1` source rather than a normalized legacy definition or a legacy evidence fallback. It compiles the live package source into canonical IR, validates exact generated ownership, and binds runtime evidence to the IR-declared intents, effects, invariants, and scenarios.

The proof uses fresh Node and Chromium probes to establish committed increment and reset behavior, stale-revision refusal, prohibited direct-set refusal, canonical-state preservation on refusal, and visible-surface agreement. The four declared effects close across six effect instances, while direct-set is proven to produce no canonical write. Every IR scenario claim is reconciled, giving `3 / 3` intent coverage and `4 / 4` scenario evidence.

`mcel_app_prove.py` selects this path only when the package manifest declares `dsl-authoritative`. Legacy and normalized-definition applications retain their existing proof classifications. The Counter semantic fingerprint remains unchanged and the final truth gate remains `semantic-runtime-proven`.

**TL;DR:** Wave 8 removes Counter’s last legacy proof dependency by making authoritative DSL, canonical IR, generated ownership, and fresh runtime evidence converge in one native intent-complete report.

# Wave 9 implementation note

Wave 9 separates application authority from application-specific mechanics. `main_computer/mcel_app_authoring_profiles.py` is a registry of bounded mechanics profiles. The generic compile, project, promote, and IR-native proof authorities resolve one profile by `appId`; no profile may decide truth status or bypass package, evidence, ownership, or repository bindings.

For Contract Counter, the existing deterministic projector and runtime probes become mechanics behind `mcel.contract-counter.authoring-profile.v1`. The live proof path is now:

```text
mcel_app_prove.py
  -> mcel_app_ir_native_proof.py
  -> registered application profile mechanics
  -> generic IR-native proof report
```

The old Counter IR-native CLI delegates to the generic CLI. Its continued existence is command compatibility, not a second execution authority. Compilation, projection inspection, promoted-authority inspection, and IR-native proof can all be invoked through `tools/mcel_app_*.py --app contract-counter`.

**TL;DR:** Counter is no longer special at the command or proof-authority layer. It is the first application profile executed by the standard MCEL authoring pipeline.
