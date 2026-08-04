# MCEL Existing Application Definition Migration Inventory

## Status

This document inventories the current MCEL application-definition families that must survive the move to `mcel.application-ir.v1` and the final official vanilla-JavaScript DSL.

It is a living migration ledger. It does not authorize code changes or compiler retirement.

The schema target is specified in `pretty_docs/mcel-application-ir-schema-and-normalization.md`. Effect migration and proof completeness are governed by `pretty_docs/mcel-consequential-effects-and-proof-accounting.md`. The official DSL source forms that each family must eventually map into are specified in `pretty_docs/mcel-official-vanilla-javascript-dsl.md`. Cross-front-end semantic failures, legacy source bindings, migration conflict diagnostics, safe repairs, and evidence invalidation are governed by `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md`. Use `pretty_docs/mcel-ai-application-authoring-cycle.md` to advance one inventory entry through migration stages, `pretty_docs/mcel-ai-authoring-pattern-catalog.md` to map recurring features without erasing application-specific policy, and `pretty_docs/mcel-semantic-change-and-evidence-impact.md` to update feature ledgers and renew only evidence whose declared dependencies changed.

## The short answer

MCEL does not currently have one application compiler. It has several definition families:

```text
documentation requirements -> generated requirements registry -> semantic adapter
surface-led definitions -> DOM/runtime surface helper -> surface registry
scaffolded explicit package -> package contracts -> generic runtime
normalized high-level application.js -> normalized definition -> generated package
MCEL Lab blueprint/annotation definitions -> blueprint runtime and semantic adapter
legacy application pages -> surface registry legacy records and app-local runtime
```

The migration must preserve each family long enough to map its meaning into the IR, compare it with the DSL, and renew runtime evidence.

### TL;DR

Nothing is retired because the reorganization has no place for it. The IR must first prove that it can carry the meaning.

## 1. Inventory method

For each application or definition family, this inventory records:

```text
current documentation authority
current authored source
current compiler/extractor path
current executable projection
current evidence path
target IR domains
known gaps
migration state
retirement condition
```

Every migration pass must update this inventory when it changes any recorded fact.

Allowed migration states:

```text
documentation-only
legacy-surface
legacy-compiled
explicit-package
dual-authored
dsl-primary-explicit-shadow
dsl-v1
blocked
retired
```

### TL;DR

The inventory tracks both meaning and machinery so a definition cannot disappear unnoticed.

## 2. Definition-family inventory

| Family | Current authored authority | Current compiler/extractor | Current projection | Initial migration state |
| --- | --- | --- | --- | --- |
| Requirements-registry applications | `mcel-*requirements.md` or MCEL Lab blueprint documentation | `tools/mcel_requirements_registry.py`, generated requirements registry, app semantic adapter | Domain-adapter registry, surface registry, truth-gate inputs | `legacy-compiled` |
| Surface-led Document Editor | Document Editor surface/layout docs plus runtime DOM | `mcel-document-editor-surface.js` and surface extraction/runtime helpers | Surface registry and browser-observed semantic carriers | `legacy-surface` |
| Scaffolded explicit package | Package-local requirements and explicit contracts | `tools/mcel_create_app.py`, package validator and discovery | Generic package runtime, acceptance and observation | `explicit-package` |
| Normalized high-level definition | Human-owned Workbench `application.js` | `tools/mcel_application_definition.py` and definition normalizer | Normalized JSON, generated contracts, runtime package | `dual-authored` precursor |
| MCEL Lab blueprint/annotation | Blueprint-studio documentation, blueprint records, annotations | Blueprint core, specimen planner, semantic adapter | MCEL Lab runtime and scope-limited proof | `legacy-compiled` hybrid |
| Legacy application pages | App-local HTML/JS/CSS and legacy surface policy | App-local scripts and shell routing | Browser application page; no required semantic conformance | `legacy-surface` |

### TL;DR

The future DSL is one new front end. The IR must accept imports from all current front-end families during migration.

## 3. Requirements-registry applications

The generated requirements registry currently contains these complete application contracts:

| App | Documentation source | Dominant object | Intents | Mutations | Prohibited | Regions | Acceptance bindings |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Calculator | `pretty_docs/mcel-calculator-requirements.md` | `CalculationSession` | 11 | 1 | 0 | 11 | 1 |
| Code Editor | `pretty_docs/mcel-code-editor-requirements.md` | `SourceWorkspace` | 7 | 4 | 0 | 7 | 1 |
| File Explorer | `pretty_docs/mcel-file-explorer-requirements.md` | `FileEntry` | 11 | 3 | 3 | 7 | 3 |
| Git Tools | `pretty_docs/mcel-git-tools-requirements.md` | `RepositoryProject` | 10 | 4 | 1 | 8 | 5 |
| MCEL Lab | `pretty_docs/mcel-lab-blueprint-studio.md` | `AppBlueprint` | 7 | 4 | 0 | 7 | 1 |
| Website Builder | `pretty_docs/mcel-website-builder-requirements.md` | `WebsiteProject` | 12 | 8 | 0 | 10 | 5 |

Current common path:

```text
requirements document
  -> tools/mcel_requirements_registry.py
  -> main_computer/web/applications/scripts/mcel-requirements-registry.js
  -> <app>-semantic-adapter.js
  -> mcel-domain-adapter-registry.js
  -> mcel-app-truth-gate.js
```

Additional current authorities include:

```text
mcel-app-surface-registry.js
main_computer/mcel_acceptance_bindings.json
app-local runtime scripts
browser observation evidence
```

The requirements registry is structured and source-bound, but it does not yet produce complete executable IR. Semantic adapters currently contain application-specific knowledge that the migration must make explicit rather than discard.

### TL;DR

Requirements and adapters together act as legacy compilers. The IR importer must preserve both halves.

## 4. Detailed migration record: Git Tools

### Current meaning authority

```text
pretty_docs/mcel-git-tools-requirements.md
pretty_docs/git-tools-backend-decomposition.md
pretty_docs/git-tools-project-level-publishing.md
```

### Current compiler and runtime path

```text
tools/mcel_requirements_registry.py
main_computer/web/applications/scripts/mcel-requirements-registry.js
main_computer/web/applications/scripts/git-tools-semantic-adapter.js
main_computer/web/applications/scripts/mcel-domain-adapter-registry.js
main_computer/web/applications/scripts/git-tools-mcel.js
main_computer/web/applications/scripts/git-tools*.js
main_computer/git_tools.py
main_computer/web/applications/apps/git-tools.html
```

### Current evidence path

```text
main_computer/mcel_acceptance_bindings.json
  git-tools.acceptance.semantic-readiness
  git-tools.acceptance.governed-push
  git-tools.acceptance.project-publishing
  git-tools.acceptance.file-triage
  git-tools.acceptance.recovery

surface registry policy
browser runtime evidence
truth-gate reconciliation
```

### Meaning that must map into the IR

```text
RepositoryProject identity and repository evidence
read-only status and inspection intents
file selection and staging plan
commit-plan preflight
branch-switch safety
Local Gitea target preparation
governed push confirmation
remote Git effect authority
operation receipts
failure classification and recovery
prohibited hidden mutation
project publishing handoff
```

### Known migration hazards

```text
requirements and adapter may encode different portions of one intent
confirmation policy may live outside the main intent record
remote mutation effects and receipts must not collapse into a generic capability call
recovery classification must remain linked to the originating effect
app-local UI bridges must not become semantic authority merely because they execute today
```

### Required target mapping

| Current concept | Target IR domain |
| --- | --- |
| Repository project | model and canonical/context state |
| Status/preflight | read-only intents, derivations, claims |
| File selection | renderer-local state and semantic bindings |
| Commit/push plan | operation input and preconditions |
| Confirmation | confirmation effect obligation |
| Git/Gitea execution | external-mutation capability and effect |
| Receipt | operation-receipt claim and effect disposition |
| Recovery | lifecycle disposition and recovery claim |

### Initial migration result

```text
state: legacy-compiled
IR coverage: partial by documentation mapping
DSL coverage: not authored
retirement: blocked until full intent/effect/evidence equivalence
```

### TL;DR

Git Tools is the decisive test for governed external mutation, receipts, and recovery.

## 5. Detailed migration record: Code Editor

### Current meaning authority

```text
pretty_docs/mcel-code-editor-requirements.md
pretty_docs/mcel-code-editor-authoring-status.md
pretty_docs/mcel-code-editor-preview-host.md
pretty_docs/mcel-code-editor-surface-status.md
pretty_docs/mcel-project-edit-transaction.md
```

### Current compiler and runtime path

```text
tools/mcel_requirements_registry.py
main_computer/web/applications/scripts/mcel-requirements-registry.js
main_computer/web/applications/scripts/code-editor-semantic-adapter.js
main_computer/web/applications/scripts/mcel-domain-adapter-registry.js
main_computer/web/applications/scripts/code-editor-layout-contract.js
main_computer/web/applications/scripts/code-editor-scm-manifest.js
main_computer/web/applications/scripts/code-editor-mcel-studio.js
main_computer/web/applications/scripts/widget-editor-core.js
main_computer/viewport_routes_editor.py
main_computer/web/applications/apps/code-editor.html
```

### Current evidence path

```text
code-editor.acceptance.full-semantic-runtime
surface registry policy
runtime-observable layout and authoring checks
browser evidence
truth-gate reconciliation
```

### Meaning that must map into the IR

```text
SourceWorkspace identity
selected source identity and version
renderer-local draft state
canonical file state
Monaco authoring work surface
stale-source precondition
edit and save intents
AI-assisted change review
project-edit transaction
filesystem mutation authority
SCM manifest and receipts
conflict, refusal, rollback, and recovery
preview and diagnostic projections
```

### Known migration hazards

```text
DOM/editor widget state must not be confused with canonical file state
save semantics may span editor, backend route, and project-edit transaction
source hashes and stale checks must remain semantic preconditions
filesystem effects require receipts and exact target identity
layout and inspector projections must remain subordinate to the selected source work surface
```

### Required target mapping

| Current concept | Target IR domain |
| --- | --- |
| Source workspace and selected file | model, context, stable resource identity |
| Monaco draft | renderer-local state and control binding |
| Disk content/version | canonical/external resource state |
| Stale check | precondition and refusal |
| Save/apply | mutation intent plus filesystem capability |
| Project-edit transaction | external-mutation lifecycle and effect group |
| SCM manifest | receipt/provenance evidence |
| Preview and inspector | derived surface projections |

### Initial migration result

```text
state: legacy-compiled
IR coverage: partial by documentation mapping
DSL coverage: not authored
retirement: blocked until project-edit transaction and filesystem effects reconcile
```

### TL;DR

Code Editor tests whether local drafts, versioned external resources, and reviewed multi-file effects remain explainable.

## 6. Detailed migration record: Document Editor

### Current meaning authority

```text
pretty_docs/mcel-document-editor-surface.md
pretty_docs/mcel-document-editor-layout-fit.md
```

### Current compiler and runtime path

```text
main_computer/web/applications/scripts/mcel-document-editor-surface.js
main_computer/web/applications/scripts/widget-editor-core.js
main_computer/web/applications/scripts/widget-editor-layout.js
main_computer/web/applications/scripts/mcel-app-surface-registry.js
main_computer/viewport_routes_docs.py
main_computer/web/applications/apps/document.html
main_computer/web/applications/styles/document.css
```

### Current evidence path

```text
document-editor.surface.primary surface policy
browser-observed semantic carrier records
layout-fit and surface documentation
runtime page behavior
```

The Document Editor is not currently present in the generated requirements registry and has no dedicated registered semantic adapter. Its current MCEL definition is therefore surface-led rather than full-application semantic.

### Meaning that must map into the IR

```text
document session identity
selected document identity
canonical document content
renderer-local selection, draft, scroll, and companion state
semantic regions and region ownership
document page, content, block, and embedded-object identity
format, insert, reload, discard, AI-apply, and export intents
persistence and export effects
independent scroll ownership
anchored layout and companion behavior
visible status and recovery
```

### Known migration hazards

```text
current semantic carriers describe surface structure but not complete operation semantics
static layout coordinates must not become permanent application meaning
formatting and editing controls need explicit state/effect ownership
reload and discard need canonical/local reconciliation rules
export and AI-apply need capability, receipt, and side-effect accounting
```

### Required target mapping

| Current concept | Target IR domain |
| --- | --- |
| Static surface records | surface regions, nodes, controls and relationships |
| Layout ownership | layout semantic constraints |
| Selected document | stable resource/context identity |
| Editable content and selection | canonical plus renderer-local state |
| Reload/discard | reconciliation intents and refusals |
| AI apply | reviewed mutation lifecycle |
| Export PDF | external capability/effect and receipt |

### Initial migration result

```text
state: legacy-surface
IR coverage: surface partial; behavior incomplete
DSL coverage: not authored
retirement: blocked until requirements and semantic-adapter gaps are filled
```

### TL;DR

Document Editor prevents an IR designed only around domain adapters from losing rich semantic surfaces and editor ownership rules.

## 7. Scaffolded explicit package: Contract Counter

### Current source

```text
mcel_apps/contract-counter/requirements.md
mcel_apps/contract-counter/mcel.app.json
mcel_apps/contract-counter/contracts/*.js
mcel_apps/contract-counter/src/*
```

### Current tooling

```text
tools/mcel_create_app.py
main_computer/mcel_scaffolding/package_validator.py
main_computer/mcel_application_packages.py
runtime projection, acceptance, observation, and proof tools
```

### Migration role

Counter is the minimum-ceremony import test.

The explicit package importer must recover:

```text
canonical count and revision
increment and reset mutations
direct-set prohibition
surface bindings
acceptance behavior
browser observation relationship
legacy evidence classification
```

### Initial migration result

```text
state: dual-authored
IR coverage: live package importer implemented and exact
DSL coverage: Counter-bounded official DSL implemented and exact
special rule: the legacy explicit package remains live; promotion remains blocked
```

### TL;DR

Counter proves that migration machinery does not make a trivial application ceremonial.

### Wave 3 live compatibility status

The repository now has a Counter-specific read-only legacy importer and three-way compatibility checker:

```text
main_computer/mcel_counter_legacy_importer.py
tools/mcel_counter_legacy_import.py
main_computer/mcel_counter_compatibility.py
tools/mcel_counter_compatibility.py
```

The importer reads the live requirements and JavaScript contract exports, derives `mcel.application-ir.v1`, and does not read the IR fixture as its semantic source. The compatibility checker then compares:

```text
live explicit package -> imported IR
checked-in repository-bound IR fixture
official vanilla-JavaScript DSL -> candidate IR
```

Counter is classified `dual-authored` only when all three semantic fingerprints are exact, every feature-level record is exact, and the fixture's recorded source hashes match the live explicit package. The legacy explicit package remains the live authority; candidate authority remains `none`; promotion eligibility remains `false`.

### TL;DR

Counter is now genuinely dual-authored and continuously comparable, but it is not promoted or migrated away from the explicit package.

## 8. Normalized high-level definition: Contract Workbench

### Current source

```text
mcel_apps/contract-workbench/application.js
mcel_apps/contract-workbench/generated/mcel.application.normalized.json
mcel_apps/contract-workbench/contracts/*.js
mcel_apps/contract-workbench/forward-specification.json
```

### Current tooling

```text
tools/mcel_application_definition.py
main_computer/mcel_application_definition_normalizer.py
runtime projection, browser catalog, acceptance, observation, and proof tools
```

### Migration role

Workbench is the strongest live precursor to an IR compiler. It already provides deterministic normalization and generated contracts for:

```text
canonical, local, derived, and provisional state
seven intents
keyed collections
capability streams
cancellation
latest-per-item concurrency
fourteen browser scenarios
intent-complete proof
```

### Known gap

The normalized definition retains application behavior as function hashes. That is adequate for current deterministic generation, but not sufficient for final inspectable IR semantics.

### Initial migration result

```text
state: dual-authored precursor
IR coverage: structurally broad; constrained behavior incomplete
DSL coverage: current high-level source is not yet the final official DSL
retirement: not applicable; this path should evolve into or be imported by the v1 compiler
```

### TL;DR

Workbench proves breadth; the IR migration must replace opaque behavior without regressing its proof.

## 9. Other requirements-registry applications

### Calculator

```text
source: pretty_docs/mcel-calculator-requirements.md
adapter: calculator-semantic-adapter.js
surface: calculator surface policy
migration concern: deterministic calculation authority versus model/symbolic helper evidence
state: legacy-compiled
```

### File Explorer

```text
source: pretty_docs/mcel-file-explorer-requirements.md
adapter: file-explorer-semantic-adapter.js
surface: file-explorer.surface.primary
migration concern: read-only browsing, mounted-path authority, preview limits, and prohibited mutation
state: legacy-compiled
```

### Website Builder

```text
source: pretty_docs/mcel-website-builder-requirements.md
adapter: website-builder-semantic-adapter.js
surface: website-builder.surface.preview
migration concern: source/generated/runtime/deployment boundaries, publish lanes, remote effects, and Git Tools handoff
state: legacy-compiled
```

### MCEL Lab

```text
source: pretty_docs/mcel-lab-blueprint-studio.md
adapter: mcel-lab-semantic-adapter.js
blueprint tooling: mcel-app-blueprints-core.js, specimen planner, annotations
migration concern: blueprint/source provenance, findings, annotations, and scope-limited self-hosting behavior
state: legacy-compiled hybrid
```

### TL;DR

These applications broaden the IR beyond editors: deterministic computation, read-only resources, deployment, and self-hosting inspection all need explicit homes.

## 10. Legacy surface-only application set

The surface registry currently classifies these applications as legacy and not conformance-required:

```text
ai-control
astrometric
chat-console
conductor
email
layout-builder
onlyoffice
spreadsheet
spreadsheet-smoke
task-manager
terminal
wallet
webgl
worker
```

These are inventory obligations, not claims that each application already has complete MCEL requirements.

For each app, a later inventory pass must determine:

```text
current page and runtime entry
whether a requirements document exists
whether a semantic adapter exists
whether a surface helper exists
which state and effects are consequential
whether the app should migrate, merge, remain legacy, or retire
```

No app may be silently dropped merely because its current policy says `legacy`.

### TL;DR

Legacy means “not yet mapped,” not “safe to forget.”

## 11. Per-feature migration ledger

Every migrated application must maintain records at feature granularity:

| Field | Meaning |
| --- | --- |
| `featureId` | Stable semantic feature identity |
| `documentationSource` | Current intent/requirements authority |
| `legacySource` | Current executable definition source |
| `legacyCompiler` | Current parser, adapter, extractor, or generator |
| `irNodes` | Proposed or generated IR nodes |
| `dslSource` | Future official DSL declaration |
| `comparison` | `exact`, `semantically-equivalent`, `intentional-versioned-delta`, `incomplete`, or `conflicting` |
| `evidenceImpact` | Acceptance, browser, receipt, lifecycle, repository, and proof evidence to renew |
| `status` | Current migration state |

Example:

```json
{
  "featureId": "git-tools.feature.governed-push",
  "documentationSource": "pretty_docs/mcel-git-tools-requirements.md",
  "legacySource": "git-tools-semantic-adapter.js",
  "legacyCompiler": "requirements-registry-plus-semantic-adapter",
  "irNodes": [
    "intent:git-tools.push-current-branch",
    "effect:git-tools.push.remote-mutation",
    "claim:git-tools.push.receipt"
  ],
  "dslSource": null,
  "comparison": "incomplete",
  "evidenceImpact": [
    "acceptance",
    "browser-observation",
    "operation-receipt",
    "recovery"
  ],
  "status": "legacy-compiled"
}
```

### TL;DR

Applications migrate feature by feature, because one app may contain both exact mappings and unresolved gaps.

Expression migration for every family is governed by `pretty_docs/mcel-constrained-expression-model.md`: current callbacks must be classified as core expressions, registered domain operators, explicit capability effects, surface/proof projections, or migration-only opaque gaps. `pretty_docs/mcel-official-vanilla-javascript-dsl.md` supplies the concrete `mcel.dsl.v1` source forms for those classifications and includes migration slices for Counter, Workbench, Git Tools, Code Editor, and Document Editor. `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md` requires legacy and DSL front ends to retain their best authored-source binding while converging on the same canonical semantic diagnostic codes and repair stages. `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` specifies how those sources are inventoried into migration descriptors, imported without mutation, compared per feature, staged as candidates, promoted atomically, and retained for rollback until retirement gates pass.

## 12. Required check in every DSL or IR pass

Every documentation and later implementation pass must answer:

```text
Which applications and definition families use this semantic feature?
Which documentation records describe it?
Which current compiler or adapter implements it?
How does the IR represent it?
How will the official DSL author it?
Does the low-level projection retain it?
Which evidence is invalidated?
What inventory and migration-ledger entries change?
```

Allowed per-layer results:

```text
unchanged-and-compatible
updated
mapping-added
known-gap
temporarily-dual-authored
blocked
ready-for-retirement
```

A pass is incomplete when it changes the IR or DSL meaning but leaves affected existing definitions unclassified.

### TL;DR

Every pass checks all three authoring levels and all affected applications, even when only one file needs changing.

## 13. Non-loss rules

The reorganization must not lose:

```text
requirements IDs and source bindings
semantic intent IDs
prohibited behavior
state authority
stable resource and item identity
confirmation policy
capability and external-effect authority
effect ownership, terminal dispositions, and cleanup obligations
receipts
uncertainty, recovery, and compensation classifications
surface region and ownership semantics
layout constraints with semantic meaning
acceptance bindings
browser-observation obligations
truth-gate status and evidence provenance
```

A legacy representation may be wrapped, imported, generated, versioned, or retired after equivalence. It may not simply disappear.

### TL;DR

If the IR has no place for a current consequential fact, the IR is incomplete—not the application definition.

## 14. Initial migration priority

Documentation should proceed in this order:

1. Counter increment/reset/prohibition paper import.
2. Workbench add-contract constrained-expression import.
3. Workbench request-quote lifecycle/effect import.
4. Git Tools governed-push feature mapping.
5. Code Editor save/project-edit feature mapping.
6. Document Editor edit/reload/export and surface mapping.
7. Calculator, File Explorer, Website Builder, and MCEL Lab coverage review.
8. Legacy surface-only triage.

This ordering tests progressively harder semantics while preserving real application families early.

### TL;DR

Use acid slices to define the IR, then immediately prove it against the applications most likely to expose missing semantics.

## 15. Retirement gate for any current definition path

A legacy compiler, adapter, extractor, or hand-authored explicit contract may retire only when:

1. Every required feature has a complete migration-ledger record.
2. Legacy and DSL front ends produce exact or approved semantically equivalent IR.
3. The generated low-level projection contains no unexplained manual semantic edits.
4. Acceptance and browser evidence pass against the DSL-generated application.
5. Every consequential effect reconciles to a terminal disposition.
6. Repository/source binding proves the authored DSL and generated IR relationship.
7. A rollback path preserves the previous compiler and evidence baseline.
8. The migration inventory marks the path `ready-for-retirement` and a reviewed pass changes it to `retired`.

### TL;DR

Retirement follows equivalence and evidence; it never precedes them.

## 16. Completion criteria for this inventory

This inventory is complete enough for DSL implementation planning only when:

```text
every requirements-registry app has a detailed or concise record
every required surface-registry app is classified
Git Tools, Code Editor, and Document Editor have feature-level initial ledgers
Counter and Workbench have complete paper-import records
all legacy surface-only apps have a triage decision or explicit unresolved status
all current compiler/extractor paths have owners and retirement conditions
all records link to the IR domains that preserve their meaning
```

## Benchmark obligation

`pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` turns this inventory into a required migration corpus. Contract Counter, Contract Workbench, Git Tools, Code Editor, and Document Editor have explicit benchmark cases, and the remaining definition families stay visible as migration obligations rather than disappearing behind the first successful DSL examples.

### TL;DR

The inventory supplies the applications and legacy meanings that the benchmark must preserve.

## Final rule

> The final v1 DSL is allowed to replace authoring compilers only after the MCEL Application IR demonstrates that it preserves the meaning, effects, surfaces, evidence obligations, and source provenance of the applications those compilers currently serve.
