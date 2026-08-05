# MCEL AI Authoring Documentation Completeness Review

## Status

This is the final documentation review for the proposed `mcel.application-ir.v1` and `mcel.dsl.v1` authoring program.

It reviews the documentation set. It does **not** claim that the compiler, DSL, importers, compatibility validator, projection system, benchmark runner, application migrations, or DSL v1 have been implemented or proven.

## Verdict

The documentation program is complete enough to begin a **bounded implementation of the MCEL Application IR kernel** after explicit implementation authorization.

The first implementation may define and test:

```text
mcel.application-ir.v1 records
stable semantic IDs and references
schema validation
reference resolution
deterministic normalization
semantic and source-binding fingerprints
canonical diagnostics
fixture-based Counter equivalence
```

The review does **not** authorize these later steps as part of the first wave:

```text
changing the live application scaffolder
promoting DSL source to production authority
retiring a legacy compiler
migrating Git Tools, Code Editor, or Document Editor
changing the MCEL runtime
reusing existing evidence for a candidate
promoting generated candidates
claiming benchmark success
claiming DSL v1 release status
```

### TL;DR

The design is documented well enough to start with the stable IR kernel. The DSL is not yet implemented, benchmarked, or proven.

# Part I: What was reviewed?

## 1. Documentation chain

The review covers the following specifications as one system:

| Document | Question answered | Review result |
|---|---|---|
| Executive overview | What code-level authoring problem are we solving? | Complete |
| Semantic boundary | What must the AI declare, what may MCEL generate, and what must be rejected? | Complete |
| IR and compiler migration | What stable target joins existing and future compilers? | Complete |
| IR schema and normalization | What canonical records, references, ordering, and fingerprints exist? | Complete for v1 kernel planning |
| Existing-definition migration inventory | Which current apps and definition families must survive? | Complete as an initial repository inventory |
| Constrained expression model | How is behavior inspectable without arbitrary JavaScript? | Complete for the documented v1 core |
| Consequential effects and proof accounting | How are effects owned, evidenced, terminated, and reconciled? | Complete for implementation planning |
| Official vanilla-JavaScript DSL | What one official source shape will an AI write? | Complete as a proposed v1 source contract |
| Diagnostics and repair | How does an AI identify and repair semantic failures? | Complete |
| Scaffolder, projection, and compatibility | Who owns each file and how are candidates staged and promoted? | Complete for later implementation planning |
| AI application authoring cycle | How does an AI advance and re-enter after failure? | Complete |
| Pattern catalog | What recurring application moves have complete examples? | Complete as the initial pattern corpus |
| Semantic change and evidence impact | What must be renewed after a change? | Complete for implementation planning |
| AI authoring and migration benchmark | How will the proposed system be judged? | Complete as a benchmark contract |

### TL;DR

The set now covers meaning, source syntax, canonical representation, migration, compilation, repair, execution projections, evidence, proof, modification, and evaluation.

## 2. What “documentation complete” means

Documentation completeness means that an implementation team can answer these questions before writing code:

```text
What is the authoritative semantic target?
What may the DSL infer?
What must remain explicit?
How are effects separated from expressions?
How are existing applications preserved?
How are generated files owned?
How is a failed candidate isolated?
How is compatibility classified?
How is evidence invalidated or reused?
What must the benchmark prove?
```

It does not mean every future extension has already been designed.

### TL;DR

The v1 foundation is specified; future-version and optimization questions may remain deferred.

# Part II: Requirement traceability

## 3. One official vanilla-JavaScript syntax

### Requirement

MCEL must have one official application-authoring syntax, and that syntax must be valid vanilla JavaScript.

### Documented resolution

The official source contract is strict CommonJS JavaScript:

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
    // Semantic declarations.
  }
);
```

Alternative YAML, custom `.mcel`, competing fluent roots, and direct raw-IR authoring are not official v1 syntaxes.

### Review result

Complete.

### TL;DR

One source language: constrained vanilla JavaScript through `@mcel/app`.

## 4. One declaration per independent semantic decision

### Requirement

Mechanical repetition may be generated. Authority, identity, mutation ownership, capability policy, concurrency, recovery, and claimed outcomes may not be guessed.

### Example

The author declares stable collection identity once:

```javascript
const contracts = dsl.state.canonical.list({
  id: "contracts",
  model: Contract,
  initial: []
});

const contractList = dsl.surface.collection({
  id: "contract-list",
  source: visibleContracts,
  key: Contract.id
});
```

MCEL may generate current-row key plumbing, DOM identity, reconciliation bindings, and observation selectors.

MCEL may not infer `Contract.id` from array position.

### Review result

Complete.

### TL;DR

The author decides identity; MCEL generates identity plumbing.

## 5. Stable MCEL Application IR

### Requirement

Requirements-driven definitions, explicit packages, the current normalized Workbench definition, and the future DSL must converge on one source-independent semantic representation.

### Documented pipeline

```text
legacy/current front ends ─┐
                          ├─> mcel.application-ir.v1
mcel.dsl.v1 compiler ─────┘
                                  |
                                  v
                        generated projections
                                  |
                  runtime + independent evidence
```

### Review result

Complete enough for the first IR-kernel implementation wave.

### TL;DR

Source forms may change; normalized application meaning is the stable center.

## 6. Existing applications cannot be lost

### Requirement

Git Tools, Code Editor, Document Editor, Counter, Workbench, blueprint apps, requirements-registry apps, scaffolded packages, and legacy surfaces must remain visible during migration.

### Example: Git Tools

The DSL is incomplete if it models only:

```text
push succeeded
push failed
```

but loses:

```text
preflight
confirmation scope
remote effect ownership
indeterminate remote outcome
receipt evidence
recovery path
```

### Documented protection

Each application family has:

```text
current meaning authority
current executable/compiler path
target IR mapping
known gaps
migration state
retirement gate
```

### Review result

Complete as an initial inventory. Repository inspection must refresh the inventory during every migration pass.

### TL;DR

No compiler or app definition retires because the new model forgot how to represent it.

## 7. Proof must explain side effects

### Requirement

A visible final state cannot hide unexplained operations.

### Example: latest-per-item quote request

The final row showing quote `B` is insufficient. Proof must also establish:

```text
request A started
request B superseded A
A lost commit authority
late A events were rejected
B committed
provisional state closed
no unexplained operation residue remained
```

### Review result

Complete for implementation planning through the effect declaration, instance, evidence, disposition, cleanup, uncertainty, recovery, and reconciliation rules.

### TL;DR

Proof must close the lifecycle, not merely inspect the last screen.

## 8. AI repair must be a first-class protocol

### Requirement

Diagnostics must tell the AI where it is in the authoring cycle and how to recover safely.

### Example

```text
MCEL_COLLECTION_KEY_REQUIRED
stage: model
semanticPath: surface:inventory-items
source: mcel_apps/inventory/application.js:52:5
availableKeys: Item.id, Item.sku
invalidates: surface, observation, item-effect-ownership
resumeAt: model
```

### Review result

Complete.

### TL;DR

A diagnostic is a typed re-entry instruction, not an internal stack trace.

## 9. Candidate and last-proven truth remain separate

### Requirement

A failed compile or migration candidate must not overwrite the application that is currently proven.

### Documented boundary

```text
promoted package:
  last proven application

runtime/state/mcel/compiler-candidates/...:
  candidate source
  candidate IR
  diagnostics
  generated candidate projections
  candidate evidence
```

### Review result

Complete.

### TL;DR

A candidate earns promotion; it never borrows the live application’s truth.

# Part III: Cross-document consistency

## 10. Source, IR, projection, evidence, and proof have distinct authorities

| Layer | Authority |
|---|---|
| Requirements documents | Intended user-visible behavior and constraints |
| Official DSL source | Authored executable semantic decisions |
| Canonical IR | Normalized machine-comparable application meaning |
| Generated low-level definition/contracts | Replaceable execution projections |
| Runtime/SCM/capability/browser records | Observed execution evidence |
| Proof report | Reconciliation verdict |

No reviewed document assigns the same artifact contradictory primary authority.

### TL;DR

The compiler states meaning; independent evidence shows what happened; proof reconciles the two.

## 11. Expressions and effects do not overlap

### Expressions may

```text
read declared values
calculate predicates
construct records
filter and sort
calculate transitions
construct capability requests
reconcile declared results
state proof predicates
```

### Expressions may not

```text
call Git
write files
perform exports
access the network
read ambient secrets
use implicit wall-clock time
use implicit randomness
mutate the DOM
```

Those operations belong to capabilities and effect accounting.

### Review result

Consistent across the semantic boundary, IR, expression, effect, DSL, pattern, and proof documents.

### TL;DR

Expressions explain calculations; capabilities own consequential work.

## 12. Migration equivalence does not replace runtime proof

Two front ends may produce equal semantic fingerprints and still require renewed execution evidence when compiler, projection, runtime, capability, or evidence-policy bindings change.

```text
legacy IR == DSL IR
```

proves semantic convergence. It does not independently prove that the new compiler projection executed correctly.

### Review result

Consistent across migration, compatibility, change-impact, authoring-cycle, and benchmark documents.

### TL;DR

Equal meaning is necessary for migration, but runtime evidence is still independent.

# Part IV: Final v1 dispositions

## 13. Questions closed by this review

The following choices are fixed for the initial v1 implementation.

### No user-defined expression macros in v1

V1 permits:

```text
core expression builders
compiler-provided helpers
versioned registered domain operators
static app-local modules that compose those forms
```

V1 does not permit an application to define a new opaque or user-executed expression macro.

### Layout order is semantic only when the node kind declares ordered children

Examples:

```text
column children: ordered
row children: ordered
scenario steps: ordered
unordered registry entries: normalized by stable ID
```

A layout or surface kind must declare whether child order is semantic.

### V1 canonical rewrites are closed

Only rewrites explicitly specified by the IR and constrained-expression normalization documents are allowed in v1 semantic-equivalence comparison. Additional rewrites require a versioned specification change.

### Opaque callbacks use one migration-only node kind

The v1 migration record is the IR expression node:

```text
kind: legacy.opaque-function
```

It inherits the `mcel.application-ir.v1` node envelope and must carry language, source hash, provenance, declared inputs, declared result, declared purity, migration owner, replacement status, and target expression kinds.

It may be imported and compared. It may not be emitted by the official DSL compiler or qualify the affected feature for `dsl-v1` status.

### Effect extensions are registered and versioned

The core effect taxonomy is closed for a compiler version. Application-specific effect kinds require a registered, versioned domain effect contract with authority, lifecycle, evidence, cleanup, and proof rules.

Unknown effect kinds are rejected.

### Default text behavior is explicit

`mcel-text-v1` is the deterministic default text normalization/collation profile for v1. Locale-sensitive behavior requires an explicit versioned locale profile in the semantic graph.

### Diagnostic display types may be derived

The authored DSL does not need to repeat resolved types solely for diagnostic display. The compiler may derive display forms, but normalized IR always carries or references the resolved result type.

### TL;DR

V1 closes the escape hatches: no user macros, no hidden rewrites, no unknown effects, and no implicit locale behavior.

## 14. Questions deferred without blocking the first implementation wave

These remain intentionally deferred:

```text
IR v2 evolution and cross-version migration
additional numeric profiles beyond the initial application corpus
additional locale/collation profiles
future bounded macro proposals
performance optimization and incremental compilation
runtime evaluator versus generated-code optimization
final installed CLI command names
additional domain-operator registry packaging formats
additional proof-operator extension families
```

They do not block implementing structural IR records, references, validation, normalization, fingerprints, and diagnostics.

They must be resolved before implementing the specific feature that depends on them.

### TL;DR

Deferred extension questions cannot silently become implementation defaults.

# Part V: Existing-app migration readiness

## 15. Counter readiness

Counter supplies the first bounded implementation slice:

```text
canonical integer state
increment mutation
reset mutation
prohibited direct-set
visible count
acceptance/browser evidence
legacy-evidence proof classification
```

The first IR implementation may use fixture IR and a read-only importer. It must not change Counter’s current execution authority.

### TL;DR

Counter tests the kernel without prematurely replacing the canary.

## 16. Workbench readiness

Workbench supplies the semantic-completeness target:

```text
canonical/local/derived/provisional state
seven intents
fourteen browser scenarios
keyed collections
async progress
cancellation
supersession
parallel item operations
multi-instance isolation
intent-complete proof
```

Workbench migration is not the first implementation wave. It follows after the IR, expression, DSL, and compatibility kernels can represent Counter exactly.

### TL;DR

Workbench is the full convergence test, not the place to improvise the core compiler.

## 17. Git Tools, Code Editor, and Document Editor readiness

These applications have documented target homes for their important semantics, but each remains migration work:

| Application | Required preservation |
|---|---|
| Git Tools | preflight, confirmation, governed Git effects, uncertainty, receipts, recovery |
| Code Editor | local drafts, canonical file state, stale-source checks, path safety, filesystem effects, retained drafts |
| Document Editor | semantic regions, local selection/scroll ownership, persistence, export artifacts, residue |

Their legacy/current compilers remain authoritative until dual-authored compatibility and renewed proof pass.

### TL;DR

The real apps are mapped, not migrated.

# Part VI: Implementation gate

## 18. What may be implemented first

After explicit authorization, the first code wave should be limited to:

```text
mcel.application-ir.v1 data records or schemas
stable ID/reference validation
reference resolution
normalization of unordered and ordered semantic structures
semantic fingerprint calculation
source-binding fingerprint calculation
canonical diagnostic envelope
fixture-based Counter IR validation
read-only reporting
```

Required properties:

```text
no runtime behavior change
no application-file migration
no live scaffolder default change
no generated-file promotion
no evidence reuse
no legacy compiler retirement
```

### TL;DR

Build the semantic measuring instrument before changing what applications run.

## 19. What follows the IR kernel

A safe implementation sequence is:

```text
Wave 1: IR schema, validator, normalizer, fingerprints, diagnostics
Wave 2A: constrained expression graph, type/context analysis, and domain-operator registry
Wave 2B: Counter-bounded official vanilla-JavaScript builders and candidate compiler (implemented)
Wave 3: repository-derived Counter legacy importer and feature-level compatibility report (implemented)
Wave 4: candidate projection in isolated runtime state (implemented)
Wave 5: Counter dual-authored evidence and proof
Wave 6: Workbench expression replacement and seven-intent convergence
Wave 7: Git Tools, Code Editor, and Document Editor migration slices
Wave 8: controlled benchmark and DSL-v1 qualification
```

Each wave must update the migration inventory and affected documentation when implementation reveals a missing or incorrect assumption.

### TL;DR

Implementation proceeds from comparison to compilation to projection to migration—not directly from syntax to production.

## 20. Documentation change control after implementation begins

The documentation is complete, not immutable.

An implementation discovery must be classified as:

```text
clarification with no semantic change
v1 specification correction
intentional v1 semantic change
future-version proposal
implementation defect
```

A compiler must not silently define missing semantics. Any v1 semantic change updates the affected specification, examples, compatibility rules, migration inventory, benchmark cases, and change-impact rules before promotion.

### TL;DR

Code may test the design; it may not secretly rewrite the design.

# Final decision

## 21. Completeness result

```text
documentation set: complete for bounded implementation planning
internal authority model: consistent
existing-definition preservation: represented
IR foundation: Wave 1 structural kernel implemented
expression foundation: Wave 2A constrained-expression kernel implemented
DSL source contract: documented; Counter-bounded Wave 2B builders/compiler implemented; complete DSL surface not implemented
proof/effect model: documented, not implemented
benchmark contract: documented, not executed
application migrations: Counter dual-authoring compatibility and isolated candidate projection implemented; promotion not started
DSL v1 release claim: not earned
```

## 22. Authorization boundary

This review closed the documentation prerequisite. The first bounded code wave has now been explicitly authorized and implemented as the standalone structural IR kernel in `main_computer/mcel_application_ir.py`.

The implementation remains inside the staged boundary: Wave 1 validates and normalizes candidate IR, computes semantic/source-binding fingerprints, and emits canonical diagnostics against a Counter fixture. Wave 2A constructs and analyzes constrained expression records, checks typed contexts and write authority, and records versioned pure domain operators. Counter-bounded Wave 2B evaluates strict CommonJS construction source in a restricted Node context, emits candidate IR, and proves exact semantic equivalence with the legacy Counter fixture. Wave 3 now imports the checked-in explicit Counter package directly from its requirements and contract exports, compares the live-derived IR, repository-bound fixture IR, and DSL IR feature by feature, and emits a compatibility report only when all three semantic fingerprints and the fixture source hashes agree. None of these waves executes application behavior, changes runtime behavior, alters live application authority, generates or promotes package projections, reuses evidence, or retires legacy compilers.

### TL;DR

Wave 1 provides the semantic measuring instrument. Wave 2A provides the typed, inspectable expression graph. Counter-bounded Wave 2B provides the first official vanilla-JavaScript builder/compiler front end. Wave 3 removes the fixture-only trust gap by deriving Counter IR from the live explicit package and requiring exact three-way semantic and feature compatibility. The complete DSL surface remains gated by Workbench and broader migration coverage.

# Governing rule

> MCEL may begin implementing the AI-authoring architecture only from the stable IR outward, while preserving every current application authority, keeping candidates separate from the last proven application, and refusing to claim DSL success until compatibility, independent evidence, effect accounting, and the benchmark all pass.

## Wave 5 bounded implementation result

The Counter migration now includes isolated candidate evidence. The Wave 5 implementation is limited to a disposable candidate workspace, existing MCEL authority execution, Counter effect accounting, and a candidate-scoped proof report. It does not change the live package, promote the DSL source, reuse evidence, alter scaffolder defaults, or retire the legacy compiler path.

The next implementation decision remains separate: whether the exact, independently proven Counter candidate is ready for an explicit authority-transition design. Wave 5 itself keeps `promotionEligible: false`.

**TL;DR:** Independent candidate proof is implemented; authority transition is still blocked.

## Wave 6 bounded implementation result

The Counter migration now includes an explicit promotion and rollback rehearsal. The Wave 6 implementation binds the exact generated candidate to independently earned candidate evidence, generates a file-by-file authority-transition plan with old and new hashes, stages machine-readable generated ownership, applies the plan in a disposable repository copy, reruns semantic/runtime proof, and restores the original package exactly.

The rehearsal may classify Counter as promotion-eligible only when post-promotion compatibility, acceptance, Chromium observation, effect accounting, application proof, repository binding, and rollback restoration all pass. It does not modify the live package, execute promotion, reuse live evidence as candidate evidence, retire the legacy importer, or migrate another application.

The next implementation decision remains separate: an explicitly authorized Wave 7 may execute the already rehearsed Counter authority transition with stale-plan checks and rollback availability. Wave 6 itself leaves `promotionExecuted: false`.

## Wave 7 bounded implementation result

The Counter migration now includes the explicitly authorized live authority transition. Wave 7 does not reinterpret a passing rehearsal as permission to copy files blindly: it reruns the rehearsal, verifies the exact candidate/evidence binding and all file hashes, creates durable promotion and rollback material, and then performs a guarded multi-file transaction against the live repository.

After applying the rehearsed package shape, Wave 7 regenerates and checks the live package/runtime/browser projections, executes fresh acceptance and Chromium observation, closes the six Counter effect instances, verifies exact repository binding and semantic round-trip, and requires `semantic-runtime-proven` application proof. Failure at any point restores the protected Counter/shared-MCEL source boundary automatically.

A successful transaction makes `application.js` the live `mcel.dsl.v1` authority, marks the explicit contracts as derived projection artifacts, retires legacy explicit-package authority, and records both the candidate source-binding fingerprint and the promoted live-source binding. Rollback remains available only while the protected post-promotion snapshot is unchanged; later protected-source drift blocks rollback rather than being overwritten.

**TL;DR:** Counter can now cross the authority boundary transactionally, retain its exact semantic identity and proof status, and return to the pre-promotion package exactly when rollback remains safe.

## Wave 8 bounded implementation result

The promoted Counter now has a native DSL/IR intent-complete proof authority. Wave 8 compiles the live authoritative `application.js`, validates the generated ownership manifest and all seven derived contract hashes, executes fresh Node and Chromium operation probes, closes the declared effect ledger, and reconciles each IR scenario claim against canonical state, receipts, and visible outcomes.

The final Counter proof no longer borrows the legacy explicit-package classification. It reports three declared and covered intents, four declared and evidenced scenarios, exact generated ownership, closed effect accounting, `legacyEvidenceRequired: false`, exact repository binding, and the unchanged `semantic-runtime-proven` truth status.

The implementation is deliberately bounded to Contract Counter. Broader application migration still requires per-family import, projection, evidence, and native-proof adapters before any legacy authority can be retired.

**TL;DR:** Counter’s promoted DSL is now not only the source authority; it is also the direct source of its numerical intent-complete proof.

## Wave 9 bounded implementation result

The Counter proving sequence is now exposed through generic application commands and a generic IR-native authority. The reusable boundary covers application discovery, authoritative DSL compilation, candidate projection dispatch, promotion/rollback dispatch, evidence-bound IR-native proof, and integration with the final application proof runner.

Counter-specific code remains only where the current portable IR cannot yet supply mechanics: explicit legacy projection formatting and runtime scenario driving. Those mechanics are registered as an application profile and cannot independently assert truth, promotion eligibility, repository binding, or evidence validity.

A passing generic Counter run reports `genericPipeline: true`, `counterSpecificExecutionPathRequired: false`, exact generated ownership, IR-native intent completeness, no legacy evidence requirement, and the unchanged semantic fingerprint.

**TL;DR:** The Counter experiment has become the first registered instance of the standard MCEL authoring system rather than a separate Counter authority path.

## Wave 10 and Wave 11 Workbench portability result

Wave 10 established the second-application generic pipeline and candidate-scoped runtime proof while truthfully exposing 26 opaque callback regions. Wave 11 closes that complete-language gate: all 26 active regions are now versioned pure domain operators, the official candidate frontend is `mcel.dsl.v1`, migration warnings are zero, and the checked-in portable projection profile is complete.

The semantic fingerprint remains unchanged because the v1 fingerprint preserves each former callback hash as a compatibility identity, while the native expression graph and operator-registry fingerprint establish the new constrained representation. At the end of Wave 11, the live Workbench package remained `legacy-explicit-package` authority and promotion execution remained disabled. The next permissible step at that boundary was a separately authorized promotion rehearsal, not an implicit authority transition.

## Wave 12 Workbench promotion-rehearsal result

The separately authorized Workbench rehearsal is now implemented through the generic application promotion authority. It binds the exact zero-warning native DSL candidate and portable projection to fresh isolated `semantic-runtime-proven` evidence, stages machine-readable promotion and rollback material, applies the proposed authority shape only in a disposable repository copy, and exercises the full post-promotion proof chain.

The rehearsal adds a Workbench IR-native proof mechanic required by the generic final proof runner once the isolated manifest becomes `dsl-authoritative`. That proof numerically covers seven intents, fourteen browser scenarios, eighteen declared effects, one streamed and cancellable capability operation, and all seven observation contracts. It does not fall back to the legacy normalized-definition intent classification.

The live Workbench package remains `legacy-explicit-package` authority. Successful rehearsal may set `promotionEligible: true`, but `promotionExecuted` remains false and no live package source is changed.

**TL;DR:** Workbench can now rehearse the same reversible authority boundary already proven by Counter, through the generic pipeline, without yet crossing it live.

## Wave 13 Workbench authority-transition result

The generic application promotion authority now executes the live Workbench transition transactionally rather than stopping at rehearsal. It reuses only the exact Wave 12 plan after rerunning that rehearsal and verifying fresh candidate evidence. The live repository is changed only after the transaction has persisted a complete protected-source backup and verified all precondition hashes.

The committed authority shape is numerically complete: seven intents, fourteen scenarios, eighteen effects, one streamed and cancellable capability, eight generated artifacts, exact repository binding, and `semantic-runtime-proven` final truth. The semantic fingerprint remains unchanged across the transition.

Automatic rollback is mandatory on any post-apply failure. Later explicit rollback is available only while the protected post-promotion MCEL source snapshot remains exact, preventing an old transaction from erasing subsequent authoritative work.

**TL;DR:** Workbench can now cross the same live, reversible DSL-authority boundary proven by Counter, but through the generic application promotion command and with its larger effect, scenario, and capability surface fully accounted for.
