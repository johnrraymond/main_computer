# MCEL Status and Roadmap

This document is the canonical human-readable statement of current MCEL status for this repository snapshot. It does not replace generated registry or truth-audit output. When prose disagrees with generated evidence, the generated evidence wins.

## Authority order

Use MCEL information in this order:

1. Generated requirements-registry and repository truth-audit output.
2. This status and roadmap document.
3. `pretty_docs/mcel-system-guide.md`.
4. App requirements, semantic-adapter, surface, evidence, and contract documents.
5. Historical patch notes and design boundaries.

Requirements prose records product intent. Adapter registries record executable semantic coverage. Runtime and acceptance evidence record proof. None of those layers may silently stand in for another.

## Current generated baseline

The current snapshot produces the following requirements-registry baseline:

```text
schema: mcel-requirements-registry-v1
blocks: 304
apps: calculator, code-editor, file-explorer, git-tools, mcel-lab, website-builder
errors: 0
warnings: 0
strict_schema_ready: True
```

The repository truth audit currently runs in report-only mode across 21 registered app surfaces. With no repository-bound runtime or acceptance evidence supplied, it reports:

```text
blocking_apps: 0
truth_status_counts:
  partially-implemented: 14
  verification-incomplete: 7
declared_level_counts:
  legacy: 14
  runtime-baseline: 1
  semantic-runtime: 6
runtime_surface_proven: 0
semantic_runtime_proven: 0
promotion_ready: []
runtime_evidence_binding: absent
acceptance_evidence_binding: absent
```

This is not a failure of the registry. It means declared maturity and implementation presence are visible, while current repository-bound proof is absent.

## Declared application maturity

The app-surface registry currently declares:

| Declared level | Applications |
| --- | --- |
| `semantic-runtime` | Calculator, Code Editor, File Explorer, Git Tools, MCEL Lab, Website Builder |
| `runtime-baseline` | Document Editor |
| `legacy` | AI Control, Astrometric, Chat Console, Conductor, Email, Layout Builder, OnlyOffice, Spreadsheet, Spreadsheet Smoke, Task Manager, Terminal, Wallet, WebGL, Worker |

All six requirements-registry apps have registered domain adapters whose registry snapshots currently report `fullApplicationSemanticReady`. That is adapter coverage, not repository-bound runtime or acceptance proof. In the current audit, each of those six apps remains `verification-incomplete` because runtime and acceptance evidence are missing.

Document Editor remains intentionally parked at `runtime-baseline`. Its current live surface still uses the Patch 22b-era Pretty Docs library, document page, and Document AI companion. Patch 24a documents a later outline/modal-picker/docked-companion target; that target is not implemented in this snapshot. Patch 43 corrected the maturity declaration without claiming the target UI or a full semantic adapter.

## Implemented authority layers

The current repository contains these active layers:

| Layer | Current authority |
| --- | --- |
| Documentation-first product contracts | `tools/mcel_requirements_registry.py` and the parsed app requirements docs |
| Adapter readiness | `mcel-domain-adapter-registry.js` |
| Surface enrollment and declared maturity | `mcel-app-surface-registry.js` |
| Runtime conformance policy | `mcel-app-surface-conformance.js` |
| Aggregated app truth | `mcel-app-truth-gate.js` |
| Repository-wide reporting | `main_computer/mcel_truth_audit.py` |
| Runtime evidence provenance | repository-bound FLOG evidence inputs |
| Acceptance evidence | `main_computer/mcel_acceptance_runner.py` and binding catalog |
| Epistemic claim states | `mcel.epistemic-status.v1` |
| Read-only observation envelope | `mcel.observation-bundle.v1` |
| DOM/authored-accessibility observation producer | `mcel-browser-observation-producer.js` |
| SCM mutation isolation | canonical instances, revision-bound operation envelopes, duplicate refusal, and overlapping-operation refusal in `mcel-scm.js` |
| Reviewed project mutation boundary | hash-guarded multi-file `modify`/`create` transactions, isolated validation, overlay dry-run, explicit reviewed apply, receipts, and best-effort rollback in `main_computer/mcel_project_edit_transaction.py` |

## Documented MCEL milestones

The current docs record these relevant milestones:

- Patch 25a established the app truth gate.
- Patch 25b added FLOG and MCEL Lab consumers without creating a second aggregation authority.
- Patch 26 added the repository truth-audit consumer.
- Patch 27a bound evidence to repository provenance.
- Patch 28a added acceptance-evidence binding.
- Patch 32 extracted reusable semantic-adapter helpers from proven adapters.
- Patch 33 froze the shared semantic-adapter toolkit contract.
- Patches 37 through 42 expanded and promoted the Git Tools semantic adapter.
- Patch 43 parked Document Editor at `runtime-baseline` until its requirements and semantic adapter are truth-auditable.
- The read-only browser observation producer established deterministic DOM and authored-accessibility capture into repository-bound observation bundles using static fixtures.
- The producer hardening pass added exact surface-binding validation and deterministic capture ceilings before any additional lens or live-browser integration.
- Deterministic evidence-workflow hardening separated canonical and scoped reports, added `mcel-evidence-scope-v1`, and protected canonical evidence behind explicit overwrite intent.

Patch numbers are historical labels, not current truth. Use generated outputs and the authority order above for present-state decisions.

## Reviewed project edit transaction

The repository now contains `main_computer/mcel_project_edit_transaction.py`, a project-neutral mutation boundary for an already reviewed set of complete replacement files. It supports multi-file `modify` and `create` operations, exact before hashes, isolated staging, no-shell validation commands, directly rooted changed-files overlays, `new_patch.py --dry-run`, explicit reviewed apply, apply receipts, and best-effort rollback after ordinary write failures.

This is infrastructure, not full-application semantic editing. It does not choose edits, bind semantic nodes to source ownership, expose Code Editor save or reviewed-patch endpoints, apply MCEL Lab repairs, support delete or rename, or claim filesystem-level crash atomicity. Its existence must not promote any application maturity or satisfy runtime and acceptance evidence by itself.

The detailed contract and limitations are documented in `pretty_docs/mcel-project-edit-transaction.md`.

## Authorized next code candidate

The next authorized MCEL code candidate is MCEL Lab semantic-form provenance and conformance closure.

This is a closure candidate for an existing read-only inspector, not a new semantic-form feature or an implementation-status inference system. Its bounded implementation may:

- preserve exact requirements-document source file and line ranges on compact `form_primitives` payload entries;
- display primitive provenance and the unambiguous label `Contract status` in the existing semantic-form viewer;
- make the static shell's aspect selector include the already supported Form aspect;
- retire the stale finding that claims parsed form primitives are not rendered;
- extend the existing read-only MCEL Lab deployed-route fixture to select the Form aspect and verify primitive counts, kind grouping, provenance binding, clipping, and overlap at the authorized viewports;
- update only directly coupled registry plumbing, generated browser payload, viewer markup or styling, tests, and documentation.

Completion requires all of the following:

- all 40 registered form primitives retain exact `source.file`, `source.start_line`, and `source.end_line` provenance in the Lab payload;
- MCEL Lab renders 9 primitive cards in 8 ordered groups, including 2 context cards;
- every rendered primitive card exposes a contract-status label and documentation source without claiming implementation proof;
- an app with no parsed form primitives retains a truthful empty state;
- the Form aspect has no internally clipped primitive cards or sibling overlaps at 1280×720, 1440×900, and the 900×900 stacked layout;
- the stale `mcel-lab.finding.form-primitives-not-yet-first-class-ui` finding is deprecated and linked to executable proof;
- the requirements registry remains at 304 blocks with 0 errors, 0 warnings, and `strict_schema_ready: True`;
- canonical runtime and acceptance evidence remain exact, and no declared application maturity is changed.

Authorization ends at provenance, viewer consistency, stale-finding cleanup, and read-only conformance proof. It does not authorize per-primitive `observed`, `missing`, or `unknown` implementation badges, because no primitive-level runtime-observable binding currently exists. It also does not authorize source mutation, repair application, active browser exploration, browser mutation, or application maturity promotion.

The completed evidence-workflow hardening remains authoritative: unfiltered runs write canonical evidence, filtered runs default to deterministic scoped directories, reports declare `mcel-evidence-scope-v1`, and partial evidence cannot replace canonical reports without `--overwrite-canonical`.

The read-only browser producer in `mcel-browser-observation-producer.js` still requires a matching app, route, surface, and locator descriptor; proves that the locator resolves uniquely to the supplied attached root; and records the binding result in provenance. Capture continues to use the versioned `mcel.browser-observation.capture-limits.v1` policy with default limits and hard ceilings for elements, tree depth, facts, attributes, text payload, and state markers. Limit-driven omissions remain explicit, deterministic, and recorded as partial capture rather than silently presented as complete.

Redaction remains a non-functional contract stub: `mcel.redaction-policy.stub.v1` reports `not-implemented` and `redactedFactCount: 0`. It performs no masking and provides no sensitive-data protection. The producer continues to emit no verifying claims.

Layout, visual, source, transition, ridge, and general live-browser collection by the observation producer remain deferred. The authorized semantic-form closure may alter only requirements provenance, existing viewer consistency, read-only Form-aspect conformance evidence, and directly coupled tests or documentation.


## Not authorized

The following remain out of scope until separately specified, policy-gated, and tested:

- active browser exploration;
- autonomous clicks, typing, navigation, or workflow execution;
- autonomous or unreviewed source, state, runtime, filesystem, Git, publish, or network mutation;
- direct source mutation from MCEL Lab observations, annotations, or repair proposals;
- repair proposal application;
- semantic intent assignment from appearance alone;
- promotion of `observed` or `inferred` claims to `verified`;
- maturity promotion based only on a browser capture.

SCM operation guards remove one mutation-safety prerequisite. They do not authorize active exploration by themselves.

## Specified application-scaffolding program

`pretty_docs/mcel-application-scaffolding.md` specifies the live deterministic structural generator, a versioned canonical package, a golden generated fixture, and the future runnable Contract Counter reference application. The program is intended to expose and close the missing application spine by working forward from the desired MCEL 1.0 package and backward from existing requirements, adapter, SCM, surface, layout, observation, acceptance, provenance, and truth authorities.

Current status:

```text
specification: documented
generator core: implemented
structural package validator: implemented
golden fixture: implemented
checked-in browser-mountable reference application: implemented
repository package discovery: implemented
browser-safe package catalog: implemented
browser-safe package loading and semantic projection: implemented
adapter-to-SCM application bridge: implemented
generic semantic surface projection: implemented
package-local acceptance discovery: implemented
operation-linked browser observation: implemented
app-oriented proof command: implemented
```

Wave 2 is complete. `tools/mcel_create_app.py` creates the deterministic canonical package under `mcel_apps/`, refuses unsafe identifiers and collisions, supports write-free dry runs and machine-readable results, validates the generated package structurally, and is locked to a byte-equivalent Contract Counter golden fixture.

Wave 3A is complete. `tools/mcel_application_packages.py` now discovers direct-child packages, validates every declared path against repository-bound package contents, rejects unsafe paths, symlinks, duplicate identities, and directory/manifest/blueprint disagreement, and emits deterministic per-package and catalog fingerprints.

Wave 3B is complete. `tools/mcel_application_package_browser_catalog.py` deterministically generates and checks the browser-safe `mcel-application-package-catalog.js` artifact from the validated repository catalog. Browser-side MCEL tooling can inspect Contract Counter package metadata through `McelApplicationPackages`, while package modules remain unexecuted, the app-surface registry remains unenrolled, and Contract Counter remains `structural-only`.

Wave 4 is complete. `mcel-application-runtime.js` compiles declared application intents into SCM-controlled transitions and exposes immutable application instances through the MCEL facade. Contract Counter increment, reset, stale, duplicate, prohibited, undeclared-write, and failed-postcondition paths are executable.

Wave 5A is complete. `tools/mcel_application_runtime_projection.py` generates and checks a browser-safe package projection containing only executable contracts and runtime assets. `MCEL.mountApplicationPackage()` verifies source-package, catalog, and projection fingerprints, loads declared modules, validates semantic surface ridges and layout-region declarations, binds controls to intents, projects committed state, renders truthful receipts, and supports deterministic unmount.

Wave 6A is complete. Generated packages declare `tests.acceptanceBindings`; package validation and repository package authority reject mismatched identities, unknown contracts, unsupported runners, selector traversal, and selectors outside the package tests root. `mcel_acceptance_runner.py` combines legacy central bindings with validated package-local contracts and bindings, executes Contract Counter through the shared SCM runtime, writes app-scoped evidence, and binds that evidence to the package fingerprint.

Wave 6B is complete. `mcel-package-host.html` loads a validated projected package in a generic browser host. `McelApplicationOperationObserver` compares committed SCM state, visible semantic-node values, surface identity, and the visible operation receipt, while nesting the existing read-only browser observation bundle. `mcel_application_observation_runner.py` runs the operation in Playwright Chromium, proves all five required surface-conformance layers, and writes app-scoped evidence bound to package, projection, catalog, and repository fingerprints.

Wave 7 is complete. `mcel_app_prove.py` runs the package-local acceptance and Chromium observation authorities, verifies exact package/catalog/projection/repository alignment, evaluates package-local requirements and adapter readiness, and invokes `McelAppTruthGate`. Normalized-definition applications receive an applicable, numerical intent-complete convergence result. Legacy packages without a normalized authoring definition, including the frozen Contract Counter canary, report `intentCompleteProof: legacy-evidence` and do not claim vacuous `0 / 0` intent coverage; their established package-local acceptance and Chromium evidence remain truth-gate authorities. Contract Counter is the canonical `semantic-runtime-proven` template fixture. No later scaffolding code wave is authorized by this status entry; template upgrade support remains deferred.

Contract Operations Workbench is now the first fully dynamic, asynchronous `semantic-runtime-proven` MCEL application. Its stable human-owned `application.js` is normalized into seven explicit contracts; the shared runtime executes local, derived, provisional, and canonical state, dynamic projection, keyed collections, capability streams, cancellation, and concurrency; 14 Chromium scenarios prove the complete surface and two-instance isolation; package acceptance is enforceable; and `mcel_app_prove.py` now performs operation-kind-specific intent-complete convergence before truth-gate promotion. See `pretty_docs/mcel-forward-specification-acid-app.md` for the completed acid-test record.


The AI application-authoring program remains documentation-governed. Its bounded Wave 1 IR kernel is now implemented in `main_computer/mcel_application_ir.py`, with `main_computer/schemas/mcel.application-ir.v1.schema.json`, `tools/mcel_application_ir.py`, and the Counter fixture/tests. This structural kernel validates stable IDs and references, normalizes deterministic JSON, computes separate semantic/source-binding fingerprints, emits canonical diagnostics, and checks declared versus structural write authority. It does not implement the DSL, runtime projections, promotion, evidence reuse, or application migration. `pretty_docs/mcel-ai-authoring-language-executive-overview.md` records the high-level AI authoring problem through concrete examples and TL;DR rules. `pretty_docs/mcel-ai-authoring-semantic-boundary.md` fixes the governing direction: one official valid-vanilla-JavaScript syntax; one declaration per independent semantic decision; mechanical repetition generated by MCEL; and proof that accounts for every consequential side effect. `pretty_docs/mcel-application-ir-and-compiler-migration.md` now fixes the stable center: requirements-driven existing apps, scaffolded explicit packages, the current normalized high-level `application.js`, and the future DSL are compiler front ends that must converge on one deterministic and comparable MCEL Application IR. It defines per-feature migration ledgers, migration states, semantic comparison layers, generated/authored boundaries, evidence renewal, and the retirement gate for legacy compilers. `pretty_docs/mcel-application-ir-schema-and-normalization.md` now specifies the first concrete `mcel.application-ir.v1` shape, stable semantic IDs, constrained behavior records, effect obligations, proof claims, deterministic normalization, separate semantic and source-binding fingerprints, and worked Counter/add-contract/request-quote slices. `pretty_docs/mcel-existing-application-definition-migration-inventory.md` records the requirements-registry, semantic-adapter, surface-led, scaffolded, normalized, blueprint, and legacy application families, including detailed preservation records for Git Tools, Code Editor, and Document Editor. `pretty_docs/mcel-constrained-expression-model.md` now specifies the typed inspectable behavior graph, core and domain operators, static analysis, normalization, proof use, and migration quarantine for current opaque callbacks. `pretty_docs/mcel-consequential-effects-and-proof-accounting.md` now specifies effect ownership, runtime instances, evidence classes, terminal dispositions, cardinality, cleanup and retained residue, uncertainty, recovery and compensation, cross-authority proof reconciliation, and the migration treatment of opaque legacy effects. `pretty_docs/mcel-official-vanilla-javascript-dsl.md` now fixes the one proposed `mcel.dsl.v1` source form: strict CommonJS vanilla JavaScript using the compiler-provided `@mcel/app` module, one `defineApp` root, explicit semantic IDs, constrained builder callbacks, static app-local modules, surface/layout declarations, capability lifecycles, effect policy, and ordered cross-authority proof scenarios. `pretty_docs/mcel-compiler-diagnostics-and-repair-protocol.md` now specifies stable diagnostic codes and keys, semantic paths, authored-source provenance, safe repair classes, dependency-aware repair order, candidate-versus-last-proven truth, evidence invalidation, narrow reruns, reviewable repair plans and receipts, and common canonical diagnostics across legacy and DSL front ends. `pretty_docs/mcel-scaffolder-generated-projection-and-compatibility.md` now specifies versioned scaffold modes, exact authored/generated paths, machine-readable ownership, candidate staging outside the live package, legacy-source descriptors and importers, IR-to-package projections, feature-level compatibility reports, generated-file drift, evidence-gated atomic promotion, rollback, and application-specific preservation rules. `pretty_docs/mcel-ai-application-authoring-cycle.md` now specifies the migration-aware AI state machine from requirements and current-definition inventory through model, intent, effect, surface, layout, scenario, compile, repair, compatibility, project, acceptance, observation, accounting, proof, promotion, and modification. `pretty_docs/mcel-ai-authoring-pattern-catalog.md` now provides the complete example-first vocabulary for recurring canonical, local, derived, keyed-collection, refusal, async, cancellation, concurrency, Git, filesystem, export, isolation, workflow, and feature-change tasks. `pretty_docs/mcel-semantic-change-and-evidence-impact.md` defines deterministic semantic change sets, typed dependency closure, earliest-stage re-entry, projection and evidence invalidation, audited evidence reuse, conservative fallback, and worked changes across Workbench, Git Tools, Code Editor, and Document Editor. `pretty_docs/mcel-ai-authoring-and-migration-benchmark.md` now specifies the controlled corpus, hard semantic and migration gates, repeated-session protocol, repair and proof-independence cases, evidence metrics, and measurable economy thresholds. `pretty_docs/mcel-ai-authoring-documentation-completeness-review.md` now completes that final review. It finds the documentation set internally consistent and complete enough for a bounded first implementation wave limited to the `mcel.application-ir.v1` structural kernel: records/schema, stable IDs and references, reference resolution, deterministic normalization, semantic/source-binding fingerprints, canonical diagnostics, and fixture-based Counter validation. The review does not claim that the DSL, compiler, migrations, benchmark, or DSL v1 are implemented or proven, and it does not authorize runtime changes, scaffolder-default changes, application promotion, evidence reuse, or legacy-compiler retirement. That bounded Wave 1 implementation is now present and tested. Wave 2A is also present as a standard-library-only constrained-expression kernel: it validates typed expression contexts, references, operand/result compatibility, canonical/provisional write boundaries, deterministic normalization, expression fingerprints, and versioned pure domain-operator records against the Counter IR. Counter-bounded Wave 2B is now implemented in `main_computer/mcel_dsl_runtime.js`, `main_computer/mcel_dsl_compiler.py`, and `tools/mcel_dsl_compile.py`. The official DSL fixture compiles into valid candidate IR with the exact legacy Counter semantic fingerprint and a distinct DSL source-binding fingerprint, and may be staged only under `runtime/state/mcel/compiler-candidates`. Wave 3 is now implemented in `main_computer/mcel_counter_legacy_importer.py`, `main_computer/mcel_counter_compatibility.py`, `tools/mcel_counter_legacy_import.py`, and `tools/mcel_counter_compatibility.py`. It derives IR directly from the checked-in explicit Counter package, verifies repository-bound fixture source hashes, compares live, fixture, and DSL semantics feature by feature, and writes compatibility reports under `runtime/reports/mcel-application-compatibility/apps/contract-counter` when requested. Exact Wave 3 compatibility classifies Counter as `dual-authored` while retaining `legacy-explicit-package` as live authority, `none` as candidate authority, and `false` promotion eligibility. Wave 4 is now implemented in `main_computer/mcel_counter_candidate_projection.py` and `tools/mcel_counter_candidate_projection.py`. It deterministically projects the canonical Counter IR into seven isolated explicit contracts plus `mcel.runtime.json`, stages a complete package shadow under the candidate workspace, detects generated-file drift, imports the candidate package back into IR, and verifies exact contract bytes, package fingerprint, catalog fingerprint, runtime-projection fingerprint, and semantic round-trip equivalence. The live Counter package remains unchanged, candidate promotion and evidence reuse remain false, and the complete DSL surface remains unproven beyond Counter.

## Verification commands

Use these commands to re-establish the documentation baseline:

```bat
python tools/mcel_requirements_registry.py
python tools/mcel_requirements_registry.py --report
python tools/mcel_application_packages.py
python tools/mcel_application_runtime_projection.py --check
python tools/mcel_application_package_browser_catalog.py --check
python main_computer/mcel_acceptance_runner.py --app contract-counter --check
python main_computer/mcel_application_observation_runner.py --app contract-counter --check
python main_computer/mcel_app_prove.py --app contract-counter --check
python main_computer/mcel_truth_audit.py
python -m pytest -q tests/test_mcel_documentation.py tests/test_mcel_documentation_authority.py tests/test_mcel_requirements_registry.py tests/test_mcel_application_scaffolding_documentation.py tests/test_mcel_create_app.py tests/test_mcel_application_packages.py tests/test_mcel_application_package_browser_catalog.py tests/test_mcel_application_runtime_projection.py tests/test_mcel_application_runtime.py tests/test_mcel_app_blueprint_contracts.py tests/test_mcel_lab_phase2_shell.py tests/test_mcel_lab_semantic_form_inspection.py tests/test_mcel_lab_deployed_conformance.py tests/test_mcel_app_truth_gate.py tests/test_mcel_truth_audit.py tests/test_mcel_observation_bundle.py tests/test_mcel_browser_observation_producer.py tests/test_mcel_document_editor_surface.py tests/test_mcel_document_editor_layout_fit.py
```

A clean update must preserve registry validity, avoid accidental changes to machine-readable requirements, keep the truth audit report-only unless explicitly configured otherwise, and leave exactly one MCEL code-authorization section in this document. That section currently authorizes only the bounded MCEL Lab deployed runtime and acceptance evidence candidate described above.

## AI authoring implementation Wave 5

Counter Wave 5 is implemented in `main_computer/mcel_counter_candidate_evidence.py` and `tools/mcel_counter_candidate_evidence.py`. It runs the generated package in an isolated candidate repository workspace, regenerates candidate runtime/browser projections, executes fresh acceptance and Chromium observation, reconciles declared canonical-write effects, and composes the existing application proof. Reports are written under `runtime/reports/mcel-compiler-candidates/contract-counter/<source-binding-fingerprint>`.

The live explicit Counter package remains authoritative. Candidate promotion, evidence reuse, live-package mutation, and legacy-path retirement remain false.

## AI authoring implementation Wave 6

Counter Wave 6 is implemented in `main_computer/mcel_counter_promotion_rehearsal.py` and `tools/mcel_counter_promotion_rehearsal.py`. It creates a source-binding-specific promotion plan and rollback bundle, rehearses `application.js` as DSL authority plus `mcel.generated.json` as derived-file ownership, reruns the full Counter semantic/runtime proof chain in an isolated promoted repository copy, and requires exact rollback restoration.

A passing rehearsal reports `promotionEligible: true`, `postPromotionTruthStatus: semantic-runtime-proven`, `rollbackRestoration: exact`, `liveApplicationChanged: false`, and `promotionExecuted: false`. The live explicit Counter package remains authoritative until a separate execution wave is explicitly authorized.

## AI authoring implementation Wave 7

Counter Wave 7 is implemented in `main_computer/mcel_counter_promotion.py` and `tools/mcel_counter_promotion.py`. It is the first mutating AI-authoring migration wave. Execution requires a fresh passing Wave 6 rehearsal and applies only the exact plan and payload bound to independently earned `semantic-runtime-proven` candidate evidence.

Before changing the live package, the executor acquires an exclusive promotion lock, verifies every live before-hash and staged after-hash, creates a durable transaction directory, and backs up the complete protected Counter/shared-MCEL authority source boundary. It then installs `application.js`, `mcel.generated.json`, the DSL-authoritative `mcel.app.json`, and the seven deterministic contract projections using sibling temporary files and atomic replacement.

The promoted live package must pass DSL compilation, exact three-way compatibility, package validation, runtime/browser projection, acceptance, Chromium observation, effect accounting, repository binding, semantic round-trip, and application proof. The terminal successful state is `sourceAuthority: mcel.dsl.v1`, `derivedArtifactAuthority: mcel.counter.explicit-projection.v1`, `legacyPackageAuthority: retired`, `truthStatus: semantic-runtime-proven`, and `rollbackAvailable: true` with the semantic fingerprint unchanged.

Any failed post-apply stage triggers automatic protected-boundary restoration. A later explicit rollback is also available by transaction id or `latest`, but it fails closed if protected MCEL sources have changed since promotion, preventing rollback from erasing subsequent authoritative work.

## AI authoring implementation Wave 8

Counter Wave 8 is implemented in `main_computer/mcel_counter_ir_native_proof.py`, `tools/mcel_counter_ir_native_proof.py`, and the promoted-app branch of `main_computer/mcel_app_prove.py`. The promoted `application.js` is compiled directly into canonical MCEL Application IR. The proof authority verifies the `mcel.generated.json` ownership contract and all seven generated contract hashes, then executes fresh Node and Chromium operation probes under an `ir-native` evidence namespace.

The authority reconciles the four declared canonical-write effects, proves the increment, stale-revision, reset, and prohibited direct-set scenarios from their IR claims, and requires every declared intent and invariant to converge. Counter no longer receives the non-numerical `legacy-evidence` classification. Its app proof now reports:

```text
intentCompleteProof: ir-native
intent coverage: 3 / 3
scenario evidence: 4 / 4
generated ownership: exact
effect accounting: closed
legacy evidence required: false
truth status: semantic-runtime-proven
```

The canonical Counter semantic fingerprint is unchanged. The source-binding fingerprint compiled from the live package path is recorded separately from the promoted candidate source-binding fingerprint retained as transaction lineage; semantic authority comes from the live DSL compilation and exact generated ownership, not from pretending those location-sensitive bindings are identical.

Wave 8 remains Counter-bounded. It does not generalize the native proof adapter to other application families, retire their current proof paths, change runtime behavior, or alter Counter semantics.

## AI authoring implementation Wave 9

Wave 9 extracts the standard MCEL application-authoring authority surface from the Counter proving sequence. The generic modules `mcel_app_compile.py`, `mcel_app_project.py`, `mcel_app_promote.py`, and `mcel_app_ir_native_proof.py` discover applications by `appId`, resolve their authoritative package source, dispatch registered mechanics profiles, and emit application-neutral reports.

Contract Counter is the first registered `AppAuthoringProfile`. Its profile contributes only the deterministic `mcel.counter.explicit-projection.v1` mechanics and its runtime scenario driver. Generic authorities retain control of package discovery, DSL compilation, candidate placement, promotion/rollback dispatch, evidence binding, ownership checks, IR-native report identity, and failure semantics. The profile therefore does not become a parallel Counter truth authority.

The standard commands are:

```text
python tools/mcel_app_compile.py --app contract-counter
python tools/mcel_app_project.py --app contract-counter
python tools/mcel_app_promote.py --app contract-counter --check
python tools/mcel_app_ir_native_prove.py --app contract-counter --check --write-report
```

`mcel_app_prove.py` now routes every `dsl-authoritative` package through `run_app_ir_native_intent_proof`. Counter's previous proof command is retained only as a compatibility wrapper. A passing Counter result reports `genericPipeline: true`, `counterSpecificExecutionPathRequired: false`, `intentCompleteProof: ir-native`, `legacyEvidenceRequired: false`, and the unchanged semantic fingerprint.

Wave 9 does not claim that every application already has a mechanics profile. Unsupported applications fail with an explicit profile diagnostic rather than falling back to Counter behavior or legacy proof. The next expansion unit is registration of another application family through the same generic authority surface.

## AI authoring implementation Wave 10

Wave 10 registers `contract-workbench` as the second application profile in the generic authoring pipeline. The live package remains `legacy-explicit-package` authority and promotion is explicitly disabled. A repository-derived Application IR fixture and a strict CommonJS `@mcel/app` candidate now converge on the same semantic fingerprint through an explicit migration bridge.

The Workbench candidate preserves the complete second-application shape: 12 states, 7 operations, 1 streamed/cancellable capability, 18 declared effects, 14 acceptance scenarios, 7 browser observations, dynamic keyed collections, provisional state, supersession, cancellation, and multi-instance isolation. Its isolated package shadow regenerates the existing nine normalized-definition artifacts exactly, imports back to the same Application IR, and runs fresh acceptance, Chromium observation, and application proof in a candidate repository workspace.

Wave 10 did not claim that the full Workbench source had already been rewritten into constrained DSL expressions. It exposed 26 callback regions as `legacy.opaque-function` migration debt and kept projection completeness false. Wave 11 now closes that debt while leaving live authority and promotion unchanged.

## AI authoring implementation Wave 11

Wave 11 replaces all 26 active Workbench callback records with versioned pure `domain.call` expressions from `mcel.workbench.constrained-expression-profile.v1`. Each call declares explicit state, input, or compiler-context arguments and an exact result type. The prior callback hash is retained only as a semantic-identity compatibility binding, allowing `sha256-mcel-application-ir-semantics-v1` to remain unchanged while the constrained-expression analyzer reports pure, deterministic, registered behavior and zero opaque migration warnings.

Workbench candidate generation now uses `mcel.workbench.portable-ir-projection.v1`, a checked-in profile containing the deterministic normalized/runtime contract projection and exact file hashes. The projector no longer uses the live normalized-definition compiler as its generation source. It still compares the live legacy package to the native DSL/IR candidate, stages only an isolated package, runs fresh evidence, and keeps promotion disabled.

Required Wave 11 state:

```text
authoring frontend: mcel.dsl.v1
native domain calls: 26
opaque callback regions: 0
migration warnings: 0
portable IR projection complete: true
semantic compatibility: exact
candidate truth status: semantic-runtime-proven
live authority: legacy-explicit-package
promotion executed: false
```

Standard commands:

```text
python tools/mcel_app_compile.py --app contract-workbench
python tools/mcel_app_project.py --app contract-workbench --write-candidate
python tools/mcel_app_promote.py --app contract-workbench --check
python tools/mcel_app_portability.py --app contract-workbench --check --write-report
```

## AI authoring implementation Wave 12

Wave 12 adds the generic Contract Workbench promotion rehearsal. The generic `mcel_app_promote.py --rehearse` dispatch binds the exact native Workbench DSL candidate, portable projection, and independently earned candidate evidence. It produces a file-by-file promotion plan and rollback bundle, then applies the proposed authority transition only inside a disposable repository copy.

The rehearsed package shape declares `mcel.dsl.v1` source authority, `mcel.workbench.portable-ir-projection.v1` derived-artifact authority, and exact generated ownership for the normalized definition plus seven explicit contracts. Workbench package tests are authority-aware: the current legacy package still executes the historical definition, while the rehearsed DSL-authoritative shape validates canonical IR and generated artifacts instead of treating the DSL source as the retired executable-definition API.

Post-promotion rehearsal gates include exact DSL/IR compatibility, generated ownership, package and browser projection, nine package-local acceptance tests, fourteen Chromium scenarios, seven observation contracts, seven-intent IR-native completeness, eighteen-effect closure, streamed/cancellable capability closure, exact repository binding, and `semantic-runtime-proven` application proof. Rollback must restore the original package, catalog, runtime projection, and protected source snapshot exactly.

Required Wave 12 state:

```text
application: contract-workbench
promotion rehearsal: pass
post-promotion truth status: semantic-runtime-proven
intent completeness: ir-native
intent coverage: 7 / 7
scenario evidence: 14 / 14
effect accounting: 18 / 18 closed
capability accounting: closed
rollback rehearsal: pass
rollback restoration: exact
live repository changed: false
promotion executed: false
promotion eligible: true
```

Wave 12 remains non-mutating. A later separately authorized wave is required to execute the Workbench authority transition.

## AI authoring implementation Wave 13

Wave 13 adds the actual transactional Contract Workbench authority transition behind the generic `mcel_app_promote.py --execute` dispatch. Execution is permitted only after a fresh Wave 12 rehearsal returns exact rollback restoration and `promotionEligible: true`. The command verifies the exact promotion plan, candidate evidence binding, all live before-hashes, and all staged payload hashes before changing any source file.

The transaction installs the native Workbench `application.js` as `mcel.dsl.v1` authority, installs `mcel.generated.json`, changes `mcel.app.json` to `dsl-authoritative`, and replaces the eight portable projection artifacts plus the three authority-aware package tests with the exact rehearsed bytes. The seven explicit contracts and normalized application definition become derived artifacts owned by `mcel.workbench.portable-ir-projection.v1`.

Post-promotion commit gates require:

```text
source authority: mcel.dsl.v1
generated ownership: exact
semantic fingerprint: unchanged
package validation: pass
acceptance: 9 / 9
browser scenarios: 14 / 14
observation contracts: 7 / 7
intent completeness: ir-native
intent coverage: 7 / 7
effect accounting: 18 / 18 closed
streamed capability accounting: closed
cancellable capability accounting: closed
repository binding: exact
truth status: semantic-runtime-proven
```

Before application, the command creates a durable transaction directory containing the plan, staged promotion bytes, protected-source hashes, full protected-source backup, original Workbench package tree, and original package/catalog/runtime fingerprints. Any post-apply failure automatically restores that snapshot. A committed transaction can later be rolled back with `--rollback latest` or its exact transaction identifier, but rollback refuses to overwrite any later protected Counter, Workbench, or shared MCEL source change.

Required Wave 13 terminal state:

```text
application: contract-workbench
promotion executed: true
source authority: mcel.dsl.v1
derived artifact authority: mcel.workbench.portable-ir-projection.v1
legacy package authority: retired
truth status: semantic-runtime-proven
semantic fingerprint: unchanged
rollback available: true
```

## AI authoring implementation Wave 14

Wave 14 de-materializes promoted MCEL packages. Counter and Workbench keep only durable authored files beneath `mcel_apps`; generated contracts, normalized definitions, and generated-ownership manifests are reconstructed as an in-memory logical package. Package validation, runtime projection, acceptance, observation, compatibility, and proof consume the logical file map rather than requiring compiler output beside `application.js`.

The checked-in browser package tree and browser package catalog are removed. Browser-compatible packages now materialize on demand beneath ignored `runtime/build/mcel/web`, and the viewport serves those generated assets without repopulating `main_computer/web/applications` or `mcel_apps`.

Required Wave 14 state:

```text
promoted application source trees: authored files only
generated contracts in mcel_apps: absent
checked-in browser package projections: absent
logical generated package overlay: valid
runtime build root: runtime/build/mcel/web
runtime build reproducible after deletion: true
Counter semantic fingerprint: unchanged
Workbench semantic fingerprint: unchanged
```

The centralized Workbench portable-projection snapshot remains a compatibility backend and is not claimed as eliminated by this wave.
