# MCEL Scaffolder, Generated Projection, and Compatibility Specification

## Status

This document specifies how MCEL creates an application workspace, assigns file ownership, compiles authored application meaning into generated projections, compares legacy and DSL compiler paths, protects the last proven application while a candidate is incomplete, and promotes a compatible candidate without losing existing Git Tools, Code Editor, Document Editor, Workbench, Counter, or other application definitions.

It is a documentation specification. It does not authorize implementation of the DSL compiler, IR compiler, legacy importers, compatibility validator, candidate workspace, projection promoter, scaffolder upgrade, generated-file rewrite, application migration, or retirement of any current compiler path.

The live repository behavior remains:

```text
tools/mcel_create_app.py
main_computer.mcel_scaffolding
mcel.canonical-application-template 1.0.0
mcel.application-package.v1
```

The v1 DSL behavior described here is a target contract that must be implemented only after the remaining documentation program is complete and reviewed. `pretty_docs/mcel-ai-application-authoring-cycle.md` defines how an AI enters and advances through this workspace, and `pretty_docs/mcel-ai-authoring-pattern-catalog.md` shows the recurring semantic features the scaffold and compiler must support without forcing generated-file edits.

### TL;DR

This document says which files an AI edits, which files MCEL generates, how old and new compilers are compared, and how a candidate becomes live without destroying the last proven app.

## Current bounded implementation status

Counter-bounded Wave 4 now implements the isolated projection path described here. `main_computer/mcel_counter_candidate_projection.py` consumes exact canonical Counter IR, applies the versioned `mcel.counter.explicit-projection.v1` profile, writes only under `runtime/state/mcel/compiler-candidates`, stages a full package shadow, and compares the generated contracts, package fingerprint, catalog fingerprint, runtime projection, and imported candidate semantics with the live package.

This implementation does not change the scaffolder default, write into `mcel_apps/contract-counter`, promote the candidate, or reuse evidence. Other applications and the general-purpose projection compiler remain unimplemented.

### TL;DR

The first isolated projection exists for Counter; it proves the boundary without changing live authority.

## 1. What problem does this specification solve?

MCEL currently has several real application-definition paths:

```text
requirements and semantic adapters
surface-led application definitions
scaffolded explicit packages
normalized Workbench application.js
MCEL Lab blueprints and annotations
legacy surface-only applications
```

The official DSL adds another front end. Without an exact ownership and compatibility contract, a migration pass could create contradictory authorities:

```text
requirements say one thing
legacy adapter executes another thing
DSL declares a third thing
generated contracts contain a fourth thing
browser evidence still belongs to the old package
```

The scaffolder and compiler boundary must prevent that split.

### Bad migration

```text
1. Create application.js.
2. Generate new contracts directly over the existing contracts.
3. Discover afterward that request-quote cancellation was not represented.
4. The last proven Workbench files are gone.
```

### Required migration

```text
1. Preserve the last proven package.
2. Compile the DSL into a candidate IR and candidate projections.
3. Import the legacy application into a comparable IR.
4. Compare every migrated feature.
5. Run renewed evidence against the candidate.
6. Promote only after compatibility and proof pass.
```

### TL;DR

A new compiler must first produce a comparable candidate, not overwrite the application it is trying to replace.

## 2. The stable pipeline

The target pipeline is:

```text
requirements documentation
        |
        v
official application.js DSL --------------------------┐
app-local DSL modules ---------------------------------┤
legacy requirements/adapter importer ------------------┤
legacy surface-definition importer --------------------┤
current explicit-package importer ---------------------┤
current Workbench-definition importer -----------------┤
MCEL Lab blueprint importer ---------------------------┘
        |
        v
mcel.application-ir.v1
        |
        +--> readable low-level application definition
        +--> explicit application contracts
        +--> blueprint projection
        +--> semantic document and mount bootstrap
        +--> acceptance/observation bindings
        +--> package manifest fingerprints
        |
        v
browser-safe runtime projection
        |
        v
acceptance + browser observation + effect accounting + proof
```

The front end may change. The generated file layout may evolve through versioned projections. The IR meaning and evidence reconciliation remain comparable.

### TL;DR

Many front ends may import meaning; one canonical IR feeds replaceable generated projections and independent proof.

## 3. Live package versus candidate package

MCEL must distinguish two application states.

### Last proven package

The checked-in application package and its current runtime projection remain the execution authority until a candidate is promoted.

```text
mcel_apps/<app-id>/
main_computer/web/applications/mcel-packages/<app-id>/
runtime/reports/... evidence bound to the promoted fingerprints
```

### Candidate package

A compiler candidate is staged outside the live source package:

```text
runtime/state/mcel/compiler-candidates/
  <app-id>/
    <source-binding-fingerprint>/
      mcel.application.ir.json
      application.definition.js
      package/
      compiler-report.json
```

The exact candidate directory name uses the complete source-binding fingerprint or a collision-safe directory encoding of it. A shortened display form may appear in human output; the stored identity remains complete.

Candidate output is disposable compiler state. It is not application authority, repository evidence, or proof merely because compilation succeeded.

### Example

```text
last proven semantic fingerprint:
  sha256:AAA

candidate source-binding fingerprint:
  sha256:SOURCE-BBB

candidate semantic fingerprint:
  sha256:CCC

result:
  live package AAA remains mounted
  candidate CCC may be inspected and tested
  CCC cannot borrow evidence from AAA
```

### TL;DR

Compile beside the live app. Promote later. Never use compilation as permission to erase the last proven package.

### Wave 2B implementation note

`tools/mcel_dsl_compile.py --write-candidate` implements the first Counter-only candidate IR lane. Wave 3 now adds `tools/mcel_counter_legacy_import.py` and `tools/mcel_counter_compatibility.py`: the former derives IR directly from the live explicit package, while the latter compares live-derived, fixture, and DSL IR and may write the first feature-level compatibility report. These tools do not create `application.definition.js`, a candidate package, runtime projection, or promotion transaction.


## 4. Authored, generated, legacy, and evidence ownership

Every application artifact belongs to exactly one primary ownership class for a given migration state.

| Ownership | Meaning | Normal editor |
| --- | --- | --- |
| `authored` | Contains an independent semantic or presentation decision | Human or AI through reviewed project edit |
| `generated` | Deterministic projection of authored meaning | Compiler or scaffolder only |
| `legacy-authored` | Current semantic authority retained for compatibility | Existing compiler path or human/AI using that path |
| `legacy-generated` | Current projection produced by a legacy path | Legacy generator only |
| `dual-authored-shadow` | A second authored representation used only for comparison | Named front end; never silently live |
| `evidence` | Observation, acceptance, effect, compatibility, or proof result | Evidence producer only |
| `runtime-state` | Disposable candidate or operational state | Compiler/runtime only |
| `presentation-authored` | Styling or content that cannot redefine application semantics | Human or AI within constrained boundary |

An artifact must not be both authored and generated in the same migration state.

### Bad ownership

```text
contracts/intents.js:
  AI edits the transition manually
  DSL compiler also regenerates the transition
```

### Correct ownership during dual authoring

```text
legacy/contracts/intents.js:
  legacy-authored execution authority

application.js:
  dual-authored-shadow candidate authority

candidate package/contracts/intents.js:
  generated from candidate DSL
```

### Correct ownership after promotion

```text
application.js:
  authored

contracts/intents.js:
  generated
```

### TL;DR

A file has one writer. Dual authoring means two explicitly separate sources compared through IR, not two writers fighting over one file.

## 5. The ownership contract artifact

A DSL-capable package reserves:

```text
mcel_apps/<app-id>/mcel.ownership.json
```

Its proposed schema is:

```text
mcel.application-artifact-ownership.v1
```

Example:

```json
{
  "schema": "mcel.application-artifact-ownership.v1",
  "appId": "inventory",
  "migrationState": "dsl-primary-explicit-shadow",
  "rules": [
    {
      "path": "requirements.md",
      "ownership": "authored",
      "role": "requirements"
    },
    {
      "path": "application.js",
      "ownership": "authored",
      "role": "official-dsl-source"
    },
    {
      "path": "modules/**",
      "ownership": "authored",
      "role": "official-dsl-module"
    },
    {
      "path": "generated/**",
      "ownership": "generated",
      "role": "compiler-projection"
    },
    {
      "path": "contracts/**",
      "ownership": "generated",
      "role": "explicit-contract-projection"
    },
    {
      "path": "src/index.html",
      "ownership": "generated",
      "role": "semantic-document-projection"
    },
    {
      "path": "src/app.js",
      "ownership": "generated",
      "role": "runtime-mount-bootstrap"
    },
    {
      "path": "src/app.css",
      "ownership": "presentation-authored",
      "role": "presentation-style"
    },
    {
      "path": "tests/custom/**",
      "ownership": "authored",
      "role": "additional-integration-test"
    }
  ]
}
```

The scaffolder creates this file. A migration transaction changes its state and rules. The compiler validates it but does not silently change ownership because it found a file at a familiar path.

### TL;DR

Ownership is machine-readable, versioned, and reviewed; it is not inferred from file names after a conflict occurs.

## 6. Current live scaffold remains valid

The existing template remains the live explicit-package template:

```text
mcel.canonical-application-template
version 1.0.0
```

Its current ownership metadata uses:

```text
generator-owned
user-owned
mixed
derived
```

The implementation must not reinterpret those historical labels retroactively. A future template version maps them deliberately into the new ownership contract.

### Current explicit package

```text
mcel.app.json                  generator-owned
requirements.md               user-owned
blueprint.json                user-owned
contracts/**                  user-owned
src/**                        user-owned
tests/**                      user-owned
```

This remains correct for packages still in `legacy-compiled` or `legacy-explicit` migration states.

### TL;DR

The DSL reorganization does not rewrite history. Existing explicit packages remain valid until a reviewed migration changes their ownership.

## 7. The future DSL scaffold

The proposed DSL scaffold is a new versioned template, not a silent change to template `1.0.0`.

Proposed identity:

```text
mcel.dsl-application-template
version 1.0.0
```

Proposed package shape:

```text
mcel_apps/<app-id>/
├── mcel.app.json
├── mcel.ownership.json
├── requirements.md
├── application.js
├── modules/
│   └── .gitkeep
├── generated/
│   ├── mcel.application.ir.json
│   ├── application.definition.js
│   └── mcel.generation.json
├── blueprint.json
├── contracts/
│   ├── domain.js
│   ├── intents.js
│   ├── adapter.js
│   ├── surface.js
│   ├── layout.js
│   ├── observation.js
│   └── acceptance.js
├── src/
│   ├── index.html
│   ├── app.js
│   └── app.css
├── tests/
│   ├── mcel_acceptance_bindings.json
│   ├── test_acceptance.py
│   ├── test_package.py
│   ├── test_operations.py
│   ├── test_surface.py
│   ├── test_browser.py
│   ├── test_truth.py
│   └── custom/
└── migration/
    └── legacy-sources.json       optional
```

A new DSL scaffold begins at:

```text
currentMode: forward-specification
```

or the versioned successor status selected by the later implementation. It does not begin `semantic-runtime-proven` merely because the template itself is proven.

### TL;DR

A proven template can create a structurally valid candidate; it cannot transfer its proof to a new application identity.

## 8. File-by-file v1 ownership

### Authored semantic files

```text
requirements.md
application.js
modules/**/*.js
migration/legacy-sources.json
```

`migration/legacy-sources.json` is reviewed migration configuration. It may be generated initially from repository inspection, but any semantic mapping or retirement status is authored/reviewed rather than inferred authority.

### Generated semantic projections

```text
generated/mcel.application.ir.json
generated/application.definition.js
generated/mcel.generation.json
blueprint.json
contracts/**/*.js
src/index.html
src/app.js
tests/mcel_acceptance_bindings.json
tests/test_acceptance.py
tests/test_package.py
tests/test_operations.py
tests/test_surface.py
tests/test_browser.py
tests/test_truth.py
```

### Presentation-authored files

```text
src/app.css
```

CSS may style the generated semantic structure. It may not create hidden controls, encode canonical state, substitute visual order for collection identity, or redefine an intent.

### Additional authored tests

```text
tests/custom/**
```

Custom tests may add external integration evidence. They do not replace generated acceptance, observation, effect, or intent-complete obligations.

### TL;DR

The DSL owns application meaning. The compiler owns semantic projections. CSS owns presentation. Custom tests add evidence but cannot erase generated obligations.

## 9. The package manifest remains the package entry point

The package continues to use:

```text
mcel.app.json
schema: mcel.application-package.v1
```

until a separate versioned package-schema migration is justified. The DSL migration does not require an immediate package-schema break.

The transitional `authoring` group remains flat enough for current repository tooling to inspect safely:

```json
{
  "authoring": {
    "schema": "mcel.dsl.v1",
    "status": "dual-authored",
    "definition": "application.js",
    "ir": "generated/mcel.application.ir.json",
    "definitionProjection": "generated/application.definition.js",
    "ownership": "mcel.ownership.json",
    "legacySources": "migration/legacy-sources.json"
  }
}
```

The manifest also records promoted fingerprints in a versioned `generation` group:

```json
{
  "generation": {
    "schema": "mcel.application-generation.v1",
    "compiler": "mcel-dsl-compiler-v1",
    "semanticFingerprint": "sha256:...",
    "sourceBindingFingerprint": "sha256:...",
    "projectionFingerprint": "sha256:..."
  }
}
```

A candidate compiler does not edit the live manifest. Promotion updates it atomically with the generated projection set.

### TL;DR

The manifest identifies the promoted source and projections. Candidate fingerprints stay in candidate reports until promotion.

## 10. The generation record

The generated file:

```text
generated/mcel.generation.json
```

uses:

```text
mcel.application-generation.v1
```

It records:

```text
compiler identity and version
DSL language version
IR schema version
source-binding fingerprint
semantic fingerprint
projection fingerprint
generated file list and content hashes
normalization policy
legacy compatibility report identity when applicable
```

Example:

```json
{
  "schema": "mcel.application-generation.v1",
  "appId": "contract-workbench",
  "compiler": {
    "id": "mcel-dsl-compiler",
    "version": "1.0.0"
  },
  "language": "mcel.dsl.v1",
  "irSchema": "mcel.application-ir.v1",
  "sourceBindingFingerprint": "sha256:SOURCE",
  "semanticFingerprint": "sha256:SEMANTIC",
  "projectionFingerprint": "sha256:PROJECTION",
  "files": [
    {
      "path": "contracts/intents.js",
      "sha256": "sha256:..."
    }
  ]
}
```

Timestamps are excluded from semantic and projection fingerprints. A human-readable generation time may appear as incidental metadata only when it cannot affect equality.

### TL;DR

Generated files carry a reproducible receipt that proves what source and compiler produced them.

## 11. The legacy-source descriptor

A package entering migration may include:

```text
migration/legacy-sources.json
```

Schema:

```text
mcel.application-legacy-sources.v1
```

Example for Git Tools:

```json
{
  "schema": "mcel.application-legacy-sources.v1",
  "appId": "git-tools",
  "sources": [
    {
      "id": "git-tools.requirements",
      "kind": "requirements-document",
      "path": "pretty_docs/mcel-git-tools-requirements.md",
      "importer": "mcel-requirements-registry-importer-v1"
    },
    {
      "id": "git-tools.semantic-adapter",
      "kind": "semantic-adapter",
      "path": "main_computer/web/applications/scripts/git-tools-semantic-adapter.js",
      "importer": "mcel-semantic-adapter-importer-v1"
    }
  ],
  "executionAuthority": "legacy",
  "comparisonPolicy": "exact-or-approved-semantic-equivalence",
  "retirementStatus": "blocked"
}
```

The descriptor does not copy legacy meaning into the package. It binds repository sources to named importers and compatibility obligations.

### TL;DR

Legacy files stay where they are until migration says otherwise; the descriptor makes their role explicit and comparable.

## 12. Importers never mutate legacy sources

A legacy importer:

```text
reads one named legacy definition family
preserves source provenance
emits candidate mcel.application-ir.v1
reports unmapped or opaque semantics
does not rewrite the legacy source
does not improve semantics by guessing
```

Examples:

```text
requirements registry importer
semantic adapter importer
surface-led Document Editor importer
explicit package importer
Workbench normalized-definition importer
MCEL Lab blueprint importer
legacy surface-only importer
```

### Wrong importer

```text
The legacy surface has a Save button.
Importer invents a save mutation, filesystem capability, stale-source policy, and proof scenario.
```

### Correct importer

```text
The legacy surface has a Save button.
Importer records the surface control and reports missing intent/effect mapping.
Compatibility status: incomplete.
```

### TL;DR

Import what is present. Diagnose what is missing. Never turn appearance or convention into authority.

## 13. Exporters and projections never become authoring languages

The IR back end may generate:

```text
application.definition.js
contracts/*.js
blueprint.json
src/index.html
src/app.js
acceptance bindings
test harnesses
browser runtime projection
```

These are one-way projections. MCEL does not require an IR-to-DSL decompiler or round-trip source formatter for v1.

### Bad workflow

```text
1. AI edits generated/application.definition.js.
2. Tool reverse-engineers the change into application.js.
3. Ambiguous round-trip silently changes source meaning.
```

### Correct workflow

```text
1. AI edits application.js.
2. Compiler regenerates projection.
3. Manual generated-file edit is reported as drift.
```

### TL;DR

Generated output is readable for inspection, not editable as a second DSL.

## 14. Low-level application definition projection

The target file is:

```text
generated/application.definition.js
```

It is the readable low-level projection corresponding to the current explicit Workbench-style definition boundary.

It must:

```text
preserve stable semantic IDs
show normalized schemas and authorities
show resolved expression and effect structures
show source-provenance references
avoid opaque source callbacks
remain deterministic
remain executable only as a compiler/runtime input, not as authored authority
```

The current Workbench `application.js` remains a legacy/current high-level source during migration. It does not move to `generated/application.definition.js` until a compatibility pass proves that the official DSL and IR preserve its complete meaning.

### TL;DR

The existing explicit definition shape survives as an inspectable target; it stops being the normal source only after equivalence is proven.

## 15. Explicit contract projection

The IR compiler produces:

```text
contracts/domain.js
contracts/intents.js
contracts/adapter.js
contracts/surface.js
contracts/layout.js
contracts/observation.js
contracts/acceptance.js
```

Each contract identifies:

```text
projection schema
app ID
semantic fingerprint
projection fingerprint
generator identity
source IR path
```

The projected contracts may use runtime-specific module forms. They may not add application semantics absent from the IR.

### Example

If the IR declares:

```text
intent:update-quantity writes state:contracts
```

`contracts/intents.js` may generate the SCM transition plumbing. It may not also create an undeclared analytics network request.

### TL;DR

Back ends translate meaning; they do not embellish it.

## 16. Blueprint projection

For DSL-primary applications:

```text
blueprint.json
```

is generated from IR surface, layout, and application metadata.

During migration, an existing authored blueprint may remain `legacy-authored` and compare against a candidate generated blueprint.

A generated blueprint may omit runtime-only facts. It must preserve the semantic surface and layout ownership required by current blueprint consumers.

### TL;DR

Blueprints remain supported, but their long-term role is a projection of application meaning rather than a competing source of it.

## 17. Semantic document and mount bootstrap

For DSL-primary applications:

```text
src/index.html
src/app.js
```

are generated.

`src/index.html` projects semantic surface nodes, regions, templates, controls, accessibility labels, and stable bindings.

`src/app.js` is a generic package mount bootstrap. It may report runtime failure and provide host options, but it must not contain application transitions, hidden capability calls, or semantic state.

### Presentation boundary

```text
src/app.css
```

remains presentation-authored. Browser conformance proves that its styling does not break semantic ownership, visibility, fit, or interaction.

### TL;DR

HTML and mount wiring are semantic projections; CSS may style them but may not become a hidden application runtime.

## 18. Acceptance and observation projection

Official DSL scenarios generate:

```text
contracts/acceptance.js
contracts/observation.js
tests/mcel_acceptance_bindings.json
generated test harnesses
```

They remain separate authorities at execution time:

```text
acceptance executes contractual operation claims
browser observation independently inspects visible behavior
intent-complete proof reconciles declared coverage
```

The generator may create adapters and selectors. It may not weaken a claim or replace a required independent visible outcome with an internal assertion.

### TL;DR

Scenarios generate proof plumbing, not proof results.

## 19. Browser runtime projection remains separate

The current live browser projection remains:

```text
main_computer/web/applications/mcel-packages/<app-id>/
```

`tools/mcel_application_runtime_projection.py` currently projects validated explicit package contracts and runtime files and binds them to package/catalog fingerprints.

The future DSL compiler does not write this tree directly. The sequence remains:

```text
authored DSL
-> promoted package projections
-> validated repository package
-> browser-safe runtime projection
```

This preserves the current security and provenance boundary.

### TL;DR

The DSL compiler builds the package. The existing runtime-projection authority publishes the browser-safe subset.

## 20. Compatibility comparison artifact

Dual-authored applications produce:

```text
runtime/reports/mcel-application-compatibility/apps/<app-id>/
  mcel-application-compatibility-report.json
  mcel-application-compatibility-report.md
```

Schema:

```text
mcel.application-compatibility-report.v1
```

The report contains:

```text
application ID
legacy front-end identities
DSL source identity
legacy IR semantic fingerprint
DSL IR semantic fingerprint
source-binding fingerprints
per-feature comparison results
unmapped legacy semantics
new DSL-only semantics
opaque-function and opaque-effect debt
projection comparison
required evidence renewal
promotion recommendation
retirement recommendation
```

### TL;DR

Compatibility is a first-class report, not a sentence in a migration note.

## 21. Compatibility classifications

The existing classifications remain authoritative:

```text
exact
semantically-equivalent
intentional-versioned-delta
incomplete
conflicting
```

### `exact`

Canonical IR is byte-identical after normalization.

### `semantically-equivalent`

IR differs only in approved nonsemantic organization or a versioned equivalence rule.

### `intentional-versioned-delta`

The DSL application intentionally changes behavior, with requirements, version, impact, and renewed evidence.

### `incomplete`

One front end cannot yet represent or import required meaning.

### `conflicting`

Both representations make incompatible claims about the same semantic decision.

Neither `incomplete` nor `conflicting` permits promotion.

### TL;DR

“Close enough” is not a status. Every difference belongs to a defined equivalence class.

## 22. Feature-level comparison

Compatibility is evaluated per semantic feature, not only by whole-file fingerprint.

Example:

| Feature | Legacy IR | DSL IR | Result | Promotion effect |
| --- | --- | --- | --- | --- |
| canonical count | present | present | exact | clear |
| increment | present | present | exact | clear |
| reset | present | present | exact | clear |
| direct-set refusal | present | missing | incomplete | blocked |
| visible count | present | present | exact | clear |
| browser scenario | legacy evidence | generated obligation | evidence renewal | blocked until run |

A whole-app semantic fingerprint mismatch is explained through these feature records.

### TL;DR

One missing refusal can block migration even when most of the application matches.

## 23. Migration states and execution authority

### `legacy-compiled`

```text
legacy source: authored authority
legacy projections: live execution authority
DSL source: absent
```

### `dual-authored`

```text
legacy source: live execution authority
DSL source: candidate shadow
legacy IR and DSL IR: compared
live package: unchanged
```

### `dsl-primary-explicit-shadow`

```text
DSL source: authored execution authority
DSL-generated package: live
legacy source/importer: compatibility shadow
legacy compiler: retained for rollback/comparison
```

### `dsl-v1`

```text
DSL source: sole code-authoring authority
IR: machine semantic authority
generated package: live projection
legacy compiler: retired for covered version
```

### TL;DR

The migration state says which source may change live behavior. Presence of a DSL file alone does not make it authoritative.

## 24. Candidate compilation protocol

A candidate compile performs:

1. Parse and evaluate the constrained DSL builder.
2. Emit source-bound candidate IR.
3. Validate IR schema, references, authority, expression, effect, surface, layout, and scenario rules.
4. Generate candidate low-level definition and package projections.
5. Validate candidate package structure.
6. Compare candidate outputs with any legacy-imported IR.
7. Emit diagnostics and compatibility report.
8. Leave the live package untouched.

Candidate compilation may run in check-only mode without writing the candidate tree when the implementation can retain all results in memory. A written candidate tree remains runtime state, not a checked-in source change.

### TL;DR

Compilation proves that a candidate can be represented; it does not promote or prove the candidate.

## 25. Promotion protocol

Promotion is an explicit project-edit transaction.

Required sequence:

1. Candidate DSL compiles without blocking diagnostics.
2. Candidate IR validates.
3. Compatibility is `exact`, `semantically-equivalent`, or approved `intentional-versioned-delta`.
4. Candidate package projections validate.
5. Required acceptance passes.
6. Required Chromium observation passes.
7. Consequential effect accounting closes.
8. Intent-complete proof passes when applicable.
9. Repository/source binding is exact for the candidate.
10. Generated output hashes match the candidate generation record.
11. The previous promoted projection and manifest are retained as rollback evidence.
12. Compiler-owned files and manifest fingerprints are replaced atomically.
13. Migration state and compatibility inventory are updated.
14. Final app proof runs against the promoted package.

A failed step leaves the last proven package unchanged.

### TL;DR

Promotion is a reviewed, atomic, evidence-gated replacement—not the side effect of running the compiler.

## 26. Rollback protocol

Every promotion retains enough information to restore:

```text
previous manifest
previous ownership state
previous generated file hashes
previous semantic and source-binding fingerprints
previous compatibility state
previous proof report identity
```

Rollback restores the prior promoted projection. It does not pretend the failed candidate never existed; candidate diagnostics and failed evidence remain available as development records according to retention policy.

### TL;DR

A migration can move forward without making the old proven compiler path unrecoverable.

## 27. Generated-file drift

MCEL detects drift when a promoted generated file no longer matches `mcel.generation.json`.

Example:

```text
contracts/intents.js changed manually
application.js unchanged
semantic fingerprint unchanged
projection hash mismatch
```

Required diagnostic:

```text
MCEL_PROJECTION_GENERATED_FILE_DRIFT
repair source: application.js or named legacy source
safe mechanical repair: regenerate from promoted source
forbidden repair: bless manual generated edit as semantic authority
```

If the manual change reflects a real missing semantic decision, the AI must express that decision in authored source and regenerate.

### TL;DR

Generated drift is either discarded plumbing or evidence of a missing authored decision; it is never silently accepted as a new source language.

## 28. Scaffold creation modes

The eventual scaffolder supports versioned modes.

### Current live mode

```text
explicit-v1
```

Backed by:

```text
mcel.canonical-application-template 1.0.0
```

### Future mode

```text
dsl-v1
```

Backed by:

```text
mcel.dsl-application-template 1.0.0
```

The default must not change from `explicit-v1` to `dsl-v1` until the DSL-v1 documentation completeness gate, implementation, migration benchmark, and release decision pass.

A template mode is always explicit in machine-readable scaffold results, even when a CLI default selected it.

### TL;DR

Changing the default authoring language is a versioned release decision, not an unnoticed template edit.

## 29. Migration workspace operation

The future scaffolder provides a preparation operation conceptually equivalent to:

```text
mcel app migrate <app-id> --prepare --to dsl-v1
```

The exact installed CLI remains deferred. The operation must:

```text
create or validate the package workspace
create application.js without claiming equivalence
create mcel.ownership.json in dual-authored state
inventory legacy sources into migration/legacy-sources.json
run named legacy importers
emit an initial compatibility report
preserve the live legacy package and compiler path
report unmapped features and opaque debt
```

It must not:

```text
move legacy files automatically
rewrite legacy sources
replace live contracts
claim semantic-runtime proof
retire a compiler
choose missing authority or lifecycle policy
```

### TL;DR

Migration preparation creates a comparison workspace, not a fake completed migration.

## 30. Template upgrade operation

A future template upgrade must distinguish:

```text
generator-owned file update
compiler-owned projection regeneration
authored file migration
presentation file migration
legacy-source mapping change
```

It may automatically update a generator-owned manifest field or generic mount bootstrap when semantics are unchanged.

It may not overwrite:

```text
requirements.md
application.js
modules/**
src/app.css
tests/custom/**
legacy semantic sources
```

without a reviewed project-edit plan.

### TL;DR

Template upgrades may refresh owned machinery; they may not rewrite application meaning under the name of scaffolding.

## 31. Contract Counter migration

### Wave 3 compatibility command

```powershell
python tools/mcel_counter_compatibility.py --write-report
```

The report is written beneath:

```text
runtime/reports/mcel-application-compatibility/apps/contract-counter/
  mcel-application-compatibility-report.json
  mcel-application-compatibility-report.md
```

An `exact` result requires:

- live explicit package import succeeds;
- fixture IR validation succeeds;
- DSL compilation succeeds;
- live, fixture, and DSL semantic fingerprints are identical;
- every indexed application, state, intent, effect, invariant, surface node, layout, and scenario is exact;
- fixture source hashes match the checked-in live package;
- no blocking diagnostic remains.

The report still records `liveAuthority: legacy-explicit-package`, `candidateAuthority: none`, and `promotionEligible: false`.

### TL;DR

Wave 3 proves three-way Counter compatibility without changing which application is live.


### Current state

```text
explicit scaffolded package
hand-authored explicit contracts
legacy intent-complete status: legacy-evidence
```

### Migration path

```text
explicit package importer -> legacy IR
new Counter application.js -> DSL IR
compare canonical count, increment, reset, direct-set refusal, surface, and scenarios
run fresh acceptance and browser observation
generate root contracts only after promotion
```

### Critical blocker

A DSL migration that expresses increment and reset but omits prohibited direct-set remains `incomplete` even if the visible counter works.

### TL;DR

Counter proves that the DSL scaffold is economical without dropping refusal or proof semantics.

## 32. Contract Workbench migration

### Current state

```text
current high-level application.js
mcel.application-definition.v1
normalized definition
generated explicit contracts
semantic-runtime-proven evidence
```

### Migration path

```text
current definition importer -> legacy IR
mcel.dsl.v1 source -> DSL IR
compare seven intents, four state authorities, fourteen scenarios, dynamic surface, cancellation, concurrency, and effect accounting
preserve current contracts until candidate proof passes
promote official DSL and generated definition together
```

### File conflict rule

The current Workbench `application.js` cannot simultaneously become the official DSL file. During dual authoring, one source must move behind an explicitly reviewed legacy path or the official DSL must use a temporary migration path recorded by the ownership contract. The migration transaction—not an ad hoc rename—settles the final location.

### TL;DR

Workbench tests the complete compiler boundary and forces us to handle the `application.js` name collision honestly.

## 33. Git Tools migration

### Current authorities

```text
mcel-git-tools-requirements.md
requirements registry
semantic adapter
backend operation paths
acceptance/runtime/receipt evidence
```

### Candidate workspace

```text
mcel_apps/git-tools/application.js
migration/legacy-sources.json
candidate IR from requirements/adapter importer
candidate IR from DSL compiler
```

### Required comparison

```text
repository identity
read-only inspection
preflight
confirmation scope and consumption
governed push capability
remote-mutation uncertainty
receipts
recovery
project-level publishing distinctions
```

Git effects remain capabilities. A generated adapter may bind them, but the DSL compiler cannot absorb Git mutation into a pure expression.

### TL;DR

Git Tools migration is complete only when remote effects, confirmation, uncertainty, and recovery survive—not when its buttons render.

## 34. Code Editor migration

### Current authorities

```text
mcel-code-editor-requirements.md
requirements registry
semantic adapter
project-edit transaction authority
editor/file runtime code
browser and receipt evidence
```

### Required comparison

```text
resource and document identity
canonical file state
local draft state
stale-source hash
path containment
save/project-edit intent
filesystem capability
refusal and retained draft
partial-write uncertainty
visible conflict and receipt claims
```

The compiler may generate project-edit request plumbing. It may not hide filesystem mutation in an expression or discard retained drafts during a failed candidate compile.

### TL;DR

Code Editor migration proves that generated plumbing can preserve local work and filesystem safety at the same time.

## 35. Document Editor migration

### Current authorities

```text
surface-led Document Editor helpers
semantic-region definitions
layout and scroll ownership
edit/reload/export operation paths
browser-fit and surface evidence
```

### Required comparison

```text
document model
semantic region identity
selection and scroll ownership
canonical content
local editing state
persistence capability
export capability and retained artifact
reload/reconciliation behavior
anchored surface and layout obligations
```

The blueprint and semantic document projections must preserve region identity and scroll ownership. Generated HTML similarity alone is insufficient.

### TL;DR

Document Editor migration tests whether projections preserve ownership and retained artifacts, not merely content text.

## 36. Other existing definition families

Every compiler/scaffolder pass also checks:

```text
Calculator
File Explorer
Website Builder
MCEL Lab
legacy surface-only applications
```

A pass may record:

```text
unchanged and compatible
mapping added
known gap
not affected by this semantic feature
blocked from migration
ready for retirement
```

It may not omit an application family because the new compiler has no importer for it yet.

### TL;DR

The migration inventory is part of compiler correctness; unsupported old definitions remain visible obligations.

## 37. Compatibility and evidence are separate

Two front ends may compile to exact IR and still lack proof for the candidate runtime.

```text
legacy IR == DSL IR
```

establishes semantic compatibility.

It does not establish:

```text
generated contracts are correct
browser projection is correct
capability behavior is correct
effects closed correctly
CSS preserves surface fit
repository package is bound to the candidate source
```

Those require renewed evidence according to the change-impact model.

### TL;DR

IR equivalence permits testing and promotion work; it does not replace acceptance, observation, effects, or proof.

## 38. Last-proven evidence reuse

Evidence may be reused only when its authority, inputs, semantic fingerprint, projection fingerprint, runtime version, and freshness policy remain valid.

Typical migration behavior:

```text
requirements evidence:
  may remain applicable if requirements did not change

legacy runtime evidence:
  does not prove newly generated runtime projection

DSL compiler unit evidence:
  does not prove application behavior

browser observation:
  must be renewed when semantic document, runtime projection, or visible claims change
```

`pretty_docs/mcel-semantic-change-and-evidence-impact.md` now defines the exact dependency and evidence-impact model. Migration may reuse evidence only through an explicit, fingerprint-bound reuse record proving exact equivalence or dependency independence. When current reports or legacy definitions lack the required granularity, promotion retains the conservative rule and renews the broader application-scoped authority.

### TL;DR

Do not lend old runtime proof to new generated code merely because the intended meaning matched.

## 39. Application package discovery compatibility

Current package discovery and validation remain the gate before runtime projection.

The future compiler must emit packages that continue to satisfy:

```text
safe repository-relative manifest paths
no symlinked package entries
unique app identity
manifest/directory/blueprint agreement
valid acceptance bindings
valid conformance declaration
deterministic package fingerprint
```

New authoring and ownership fields must be added in a backward-compatible way or through an explicit package-schema version and validator migration. They cannot make current tools silently ignore unsafe paths.

### TL;DR

The new source language must fit through the current package safety boundary or change that boundary explicitly and versionedly.

## 40. Runtime projection compatibility

The current runtime projection copies only browser-required contracts and runtime assets. Future generated packages must preserve that separation.

The DSL compiler may not place secrets, migration descriptors, requirements documents, source maps, candidate IR, or server-only capability implementations into the browser projection merely because they exist inside the package workspace.

The runtime manifest continues to bind:

```text
source package fingerprint
catalog fingerprint
projection fingerprint
module identities
runtime asset paths
conformance mode
```

### TL;DR

The browser receives executable projections, not the authoring workspace or migration history.

## 41. Security boundary for app-local modules

Official DSL modules under:

```text
modules/**/*.js
```

are compiler inputs. They are not copied into the browser runtime as arbitrary JavaScript.

The compiler evaluates them only through the constrained `@mcel/app` authoring environment and converts their declarations into IR.

A module that imports:

```text
fs
child_process
network clients
ambient environment access
browser DOM APIs
```

is rejected unless the import is a separately documented static authoring dependency that cannot perform effects. V1 should prefer no such dependencies.

### TL;DR

Vanilla JavaScript is the source notation, not a permission slip to smuggle arbitrary JavaScript into generated applications.

## 42. Deterministic scaffold and projection requirements

Given identical:

```text
template version
app ID and title
authored source bytes
legacy source bytes
compiler/importer versions
normalization policy
```

MCEL must produce identical:

```text
ownership file
candidate IR
low-level definition projection
explicit contracts
blueprint
semantic document
mount bootstrap
generated harnesses
generation record
compatibility classifications
semantic and projection fingerprints
```

Filesystem traversal order, timestamps, temporary paths, host OS separators, locale, and process identity cannot affect those outputs.

### TL;DR

A migration must be reproducible on another machine before it can be trusted as a compiler replacement.

## 43. Scaffolder diagnostics

Scaffold, migration, projection, and compatibility failures use the common protocol in `mcel-compiler-diagnostics-and-repair-protocol.md`.

Required examples include:

```text
MCEL_SCAFFOLD_UNSAFE_DESTINATION
MCEL_SCAFFOLD_OWNERSHIP_CONFLICT
MCEL_SCAFFOLD_TEMPLATE_VERSION_UNSUPPORTED
MCEL_PROJECTION_GENERATED_FILE_DRIFT
MCEL_PROJECTION_UNDECLARED_SEMANTIC_OUTPUT
MCEL_COMPATIBILITY_FEATURE_MISSING
MCEL_COMPATIBILITY_SEMANTIC_CONFLICT
MCEL_COMPATIBILITY_LEGACY_IMPORT_OPAQUE
MCEL_PROMOTION_EVIDENCE_INCOMPLETE
MCEL_PROMOTION_LIVE_FINGERPRINT_CHANGED
```

The final implementation may refine exact codes, but every rule must retain a stable stage-aware diagnostic.

### TL;DR

Compiler migration failures must return the AI to source, mapping, projection, evidence, or promotion—not to a random generated file.

## 44. Per-pass review checklist

Every scaffolder, compiler, importer, projection, or compatibility pass answers:

1. Which application-definition families are affected?
2. Which files are authored, generated, legacy-owned, evidence, or runtime state?
3. Does any file gain two writers?
4. Which live scaffold/template version changes?
5. Which candidate paths are written?
6. Which manifest or ownership fields change?
7. Which IR nodes and expression/effect rules are projected?
8. Which legacy importer mappings change?
9. Which feature-level comparisons change?
10. Which generated files become stale?
11. Which application-scoped evidence must be renewed?
12. Can the last proven application remain live throughout failure?
13. What rollback artifact is retained?
14. Which migration inventory rows change?
15. Is any compiler path being retired, and has its retirement gate passed?

### TL;DR

A pass is incomplete if it updates the new DSL path without checking the current apps, live projections, evidence, and rollback boundary.

## 45. Acceptance criteria for future implementation

The scaffolder/projection/compatibility implementation is not complete until:

1. Current explicit template behavior remains reproducible.
2. The DSL template is separately versioned.
3. Every package has machine-readable ownership.
4. Candidate compilation cannot overwrite the promoted package.
5. Candidate outputs are fingerprint-bound.
6. Official source, IR, definition, contracts, blueprint, document, bootstrap, and tests have fixed paths and owners.
7. Generated drift is detected.
8. Legacy importers preserve provenance and report gaps without guessing.
9. Compatibility comparison works per feature and whole application.
10. Counter reaches dual-authored equivalence.
11. Workbench reaches seven-intent and fourteen-scenario equivalence without opaque callbacks.
12. Git Tools preserves confirmation, effects, uncertainty, and recovery.
13. Code Editor preserves draft, stale-source, filesystem, and retained-state semantics.
14. Document Editor preserves regions, layout ownership, persistence, export, and residue.
15. Candidate evidence is separate from last-proven evidence.
16. Promotion is atomic and rollback-capable.
17. Package discovery and runtime projection remain safe and deterministic.
18. Generated browser output excludes authoring and migration-only material.
19. Diagnostics point to the highest authored source.
20. No legacy compiler retires before equivalence, renewed proof, and reviewed inventory status.

### TL;DR

The implementation succeeds when an AI can create or migrate an app without guessing ownership, overwriting proof, or losing existing application meaning.

## 46. Documentation sequence from here

Completed foundations now include:

```text
AI authoring executive overview
semantic authoring boundary
IR and compiler migration model
IR schema and normalization
existing-definition migration inventory
constrained expression model
consequential effects and proof accounting
official vanilla-JavaScript DSL syntax
compiler diagnostics and repair protocol
scaffolder, generated projection, and compatibility specification
AI application authoring cycle
AI authoring pattern catalog
semantic change and evidence impact
```

The controlled scaffold, candidate, migration, promotion, and rollback trials are now specified in `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md`. `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` confirms that these paths agree with the IR, source, diagnostics, evidence, and migration authorities.

Remaining documentation before implementation:

1. **Documentation completeness review** — the final implementation authorization gate.

No DSL, IR, scaffolder, importer, compatibility, projection, promotion, or migration implementation is authorized by this document.

## Final rule

> MCEL may change how applications are authored and generated only by preserving the last proven application, assigning one writer to every artifact, compiling all front ends through comparable IR, staging candidates outside the live package, renewing independent evidence, and promoting generated projections through an explicit reversible transaction.

## Counter Wave 5 isolated evidence checkpoint

`tools/mcel_counter_candidate_evidence.py` implements the candidate-evidence boundary for Counter. It rebuilds a disposable source workspace, overlays only the generated candidate package, regenerates browser/runtime projections there, and writes evidence only beneath `runtime/reports/mcel-compiler-candidates`. Existing live reports are not accepted as candidate proof.

The workspace and report are bound to the DSL source-binding fingerprint. A failed candidate cannot change the live package, cannot borrow live acceptance or browser evidence, and cannot become promotion eligible. The report is intentionally a pre-promotion authority: it proves the candidate can independently earn the current truth status, not that its source should replace the legacy package.

**TL;DR:** Candidate proof is isolated, fresh, fingerprint-bound, and non-promoting.
