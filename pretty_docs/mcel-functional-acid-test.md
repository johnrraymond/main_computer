# MCEL functional acid test — observed execution versus unverified coverage

## Purpose

MCEL's main promise is **minimal authoring effort, attractive defaults, maximum valid flexibility, and correct composition**. The acid test exists to investigate *every* MCEL element and library module against that promise, not to certify the existence of a registry or the number of passing unit tests. This is a growing functional-proof system, not another Task Manager example.

The Task Manager notebook is **frozen after Phase 3**. It is an existing read-only projection specimen used by the first browser test. The runner does not alter Task Manager application sources, MCEL Lab or Website Builder.

## One command

```text
python tools/mcel_functional_acid.py --mode core
```

Output under `runtime/reports/mcel-functional-acid/`:

- `report.json`: **separately** reports executed suites/checks/PNG observations (`counts`), registry-element proof coverage (`coverage.elements`), source discovery (`coverage.sourceInventory`), and visual review gaps (`coverage.visual`)
- `report.md`: human-readable status and outstanding per-element functionality
- `screenshots/`: fresh Chromium PNGs of the element-library showcase and generated notebook at desktop and mobile viewport sizes

`--mode full` executes the entire `tests/test_mcel_*.py` corpus and captures one PNG **per currently showcased element**, in addition to full-screen images. Run this when checking broad integration or after modifying element definitions. Existing external authorities are not bypassed; the full corpus may require services and fixtures absent from exported snapshots.

`--mode inventory` performs source discovery without claiming functional success. This is useful before beginning a patch. `--chromium-executable PATH` can select a Chromium binary.

The global MCEL element/Chromium suite must **not depend** on the frozen Task Manager notebook being present in a repository export. When `mcel_experiments/task-manager-notebook/application.js` or its read-only fixture is absent, notebook projection and notebook-dependent transition integration remain explicitly `UNVERIFIED`; independently runnable compiler/IR suites and the Chromium element showcase still execute. If a notebook specimen **is** supplied but its compilation fails, this is `FAIL`, not an omitted obligation. This does not authorize losing the notebook source or deleting its proof requirement. The default `--output` is the report **directory**, containing `report.json`, `report.md`, and `screenshots/`.

### Legacy report-directory recovery

Early versions incorrectly used `runtime/reports/mcel-functional-acid/report.json/`
**as a directory**. If that directory remains, a normal run now moves it (with
its existing screenshots and prior reports intact) to
`runtime/reports/mcel-functional-acid/report-json-legacy/`. If that archive
name already exists, the runner chooses the next available numeric suffix.
The repaired run writes a fresh `report.json` **file** and records the archive
path in `legacyOutputPreservedAt`. No manual cleanup is needed, and the runner
never deletes or overwrites earlier evidence. If the directory cannot be moved,
the runner refuses immediately with an explicit filesystem error.

Expected CLI exit codes:

- **0 / PASS** — all required independently executed checks and tracked element behavioral coverage are admitted; not expected yet.
- **1 / FAIL** — an executed check fails, a required executable authority errors, or a measured regression is observed. Inspect the check details before attributing the failure to MCEL functionality.
- **2 / INCOMPLETE** — independent observations may pass, but coverage is insufficient. This does **not** claim missing implementations.

## Active-source inventory and exhaustive registry API probes (this patch)

The source discovery excludes `tools/patching/reports/new_patch_runs/` *before*
traversing it. Historical extracted bundles and undo copies are not active
source files. The index is still name-based and does not claim complete
architectural reachability. Do not turn source-file totals into functional
obligations. The active source count may increase when new test files are added.

In Chromium the runner now loads `tools/mcel_element_contract_probe.js` alongside
the **actual** MCEL public registry, executing eight operations **separately for
every registered element** (and automatically including newly registered IDs):

1. Exact lookup and rejection of an unknown ID.
2. Family enumeration, including a negative family control.
3. Kind enumeration.
4. Serialized contract identity, metadata and policy preservation.
5. Nested props isolation and children serialization.
6. Detached catalog mutation isolation.
7. Duplicate-registration refusal without overwriting the definition.
8. Unknown-element serialization fallback.

The batch reports `per-element-semantic-contracts` with the registry roster,
probe count, and failed IDs. The report embeds all eight observed assertions
under each element's `semanticContractProbes`. A failed assertion is a **FAIL**
that takes precedence over outstanding visual and runtime evidence.

**This is an executed semantic API contract test for every declaration, NOT a
claim that all 141 elements work as widgets or controllers.** It does not prove
actual renderer generation, click/keyboard transitions, child composition
rejection, effect isolation, accessibility, or visual quality. Each element's
`missingBehavioralWitnesses` names the additional execution classes needed,
determined from its declared interaction, children, fixtures, and risk policy.
`functionalBehavior` remains `UNVERIFIED` until independent element-specific
runtime evidence exists. A green per-element semantic contract MUST NOT
promote that separate status, even when the element has a showcase card.

The browser assertion `showcase-subset-consistency` verifies only that the
**declared showcase subset** matches observed cards. It is expressly not a
141/141 completeness verdict; the 50 unshown elements appear separately in
`coverage.elements.showcaseNotObserved`.

The test suite includes a browser fault injection that corrupts the real
registry's `serializeElement` operation for exactly one element and requires
the affected contract to fail, and a second control proving that even 141
successful semantic API contracts cannot promote the global result to PASS.
This is intentionally a fail-closed first pass toward comprehensive behavioral
witnesses, not an artificial victory over the 141 uncovered behaviors.

Version 2 uses `UNVERIFIED` for absent evidence and reserves `NOT_IMPLEMENTED` for an independently diagnosed missing implementation. The runner never infers missing implementation from a missing test, file, or registry renderer. The old 252/348 roll-up is deliberately not preserved: its numbers combined fundamentally different unit types.

`counts` now tallies **only suites, independently observed browser checks, and PNG regression comparisons**; it does not aggregate one PASS per registry entry or one missing-feature count per module. `coverage.elements` reports registry metadata serialization, showcase DOM presence, and independently proved behavior in separate denominators. `coverage.sourceInventory` reports discovered files, never 1:1 functional obligations. Architectural promises remain assigned to `tools/promise_coverage/mcel_promises.py` and do not contaminate acid-test counts.

## Evidence contract

1. **Source and registry inventory.** Discover MCEL-named scripts (including nested application integrations), CSS, Python modules, HTML entrypoints, and tools, plus every registered `element.*` definition at Chromium execution time. Record file SHA-256. This is deliberately a **name-based discovery index**, not an exhaustive dependency graph or a 1:1 list of functional contracts. Source filenames and related tests are hints only.
2. **Semantic and compiler proofs.** Reuse actual `pytest` authority for DSL, IR, state transitions, source serialization/repair, layout, and operational safeguards. Do not count a clean compile as a functioning widget.
3. **Real browser function.** Chromium executes original source modules, uses the existing element acid-test showcase, performs concrete tab selection/keyboard transitions and refusals, measures container widths and selected-panel counts, and watches for browser exceptions and unintended network requests.
4. **Per-element evidence.** Each registry definition's `registrySerialization` proves only metadata round-trip shape; `showcasePresence` means a declared catalog DOM card exists. Cards in hidden panels may have zero geometry, and no visibility or functionality is inferred from hidden DOM nodes. The showcase's self-reported `status: pass` is treated as **untrusted metadata**; the harness checks actual IDs, uniqueness and visible-card geometry independently. An empty renderer object does not prove implementation. `functionalBehavior=UNVERIFIED`, `implementationStatus=UNKNOWN` until an independent, element-scoped witness establishes behavior or absence. The 50 currently unshown definitions remain gaps, not automatic `NOT_IMPLEMENTED` claims.
5. **PNG + geometry.** Browser screenshots are stored with SHA-256 and viewport-specific names. DOM containment checks detect only the specific horizontal overflow, active-selection and panel relationships actually measured. They do not certify all child overflow, contrast, aesthetics, or runtime behavior. A screenshot is evidence, not its own visual oracle.
6. **Visual baselines.** With `--baseline-dir DIRECTORY`, compare PNGs pixel-for-pixel against previously reviewed baselines. Missing baselines are `UNVERIFIED`; differences are `FAIL`. To capture **candidates** for human review, explicitly run `--approve-baselines --baseline-dir DIRECTORY`. This captures images but does *not* mark visual quality proven in that run. After reviewing their appearance and accepting them, subsequent normal comparisons detect regressions. Baseline images are host/browser/font-specific and should not be casually copied into a different rendering environment. Do not include automatic baseline changes in source patches.
7. **Visual quality is a separate judgment.** Identical screenshots can still be unattractive, inaccessible, or poorly composed. A baseline is only a regression control. Human visual evaluation, contrast and accessible semantics require their own eventual checks; this remains `UNVERIFIED` and is not counted as an observed failure.
8. **Evidence boundaries.** This runner does not alter app release status, assert that MCEL fulfills all platform promises, or override `main_computer/mcel_truth_audit.py`. No invented passes, downgraded promises or fixture-based consequential effects.

## The path to full coverage

The current iteration is deliberately **incomplete**. A successful harness installation makes gaps visible; it cannot reasonably create independent behavior and visual tests for every declared component in one patch. Further work must add a distinct rendered/model/controller test for each actual component responsibility, with explicit valid behavior, invalid behavior, composition, appearance, and effect boundaries. The report must only promote a component after running an appropriate new witness—not because someone renamed its status. The long-term final test should include all applicable rendering modes, layouts, states, viewports, keyboard interactions, responsive configurations, data states, effect refusals, and design variants.

Existing documents under `pretty_docs/` remain the semantic and authoring specification. Extend this functional suite every time a new MCEL capability is implemented. The four governing development principles are stated in `pretty_docs/mcel-authoring-principles.md`.

## Credibility negative controls

The harness regressions deliberately supply a showcase that reports `pass` while omitting a declared card; `assess_showcase` must refuse it. It also refuses duplicated registry/showcase IDs, references to unregistered elements, and zero-size **visible** cards. An intentionally absent notebook specimen remains `UNVERIFIED` while the library tests continue; a present invalid notebook compilation is `FAIL`. Browser and suite failures override unverified coverage. A synthetic element with registry metadata and a catalog card must not receive a behavioral `PASS` without a separate executed witness.

**Freeze boundary:** This repair changes only the acid-test runner, its tests, and this documentation. Task Manager, Website Builder and production MCEL source remain unchanged.

## Element implementation/evidence audit (v4)

This release adds an **implementation/evidence audit for every live registered element**,
not a new application, reimplementation of a renderer, or inferred functionality.
Run `python tools/mcel_functional_acid.py --mode core` to execute it as part of
the cumulative acid test. The machine-readable result for each element is in
`report.json` under `elements[].implementationAudit`.

The audit records the exact declaration location in `mcel-elements-core.js`, the
registered renderer *descriptor* (which is not an executable renderer), optional
state/interaction/accessibility/fixture obligations, literal references in
existing tests, potential runtime implementation files, and the behavioral
evidence still needed. It does not interpret filenames, mentions in documentation,
or showcase DOM cards as passing behavioral tests.

Current source snapshot: 141/141 declaration locations traced; 139 elements have
literal references in existing MCEL tests, **but these may be catalog assertions**;
only the five tab-workspace elements have specifically identified focused
runtime-test candidates in this patch. Two IDs had no existing literal test
references (`element.network.message-thread` and `element.compute.graph-surface`).
This is a source-navigation result; those numbers must not be added to `PASS`.

### Executed first cross-element runtime witness

The general MCEL tab renderer (`mcel-tab-workspace.js`) and its typed transition
executor (`mcel-view-transition.js`) are run in disposable Chromium, with real
clicks, ArrowRight focus movement, selection and ARIA relationships, hidden-panel
persistence, route synchronization, and invalid-transition/effect refusal.
Five *bounded* witnesses are mapped to the five declared tab-family roles:
`tabbed-workspace`, `tab-list`, `tab`, `tab-panel`, and `tab-controller`.
Each includes its exact required observations and a status. Breaking an ARIA
relationship deliberately in an independent browser control must produce FAIL.

The fallback sample in `tools/fixtures/mcel_acid_tab_ir.json` is a **frozen,
validated compiled IR fixture**, not another authored DSL or application runtime.
It allows general tab-renderer checking when the optional Task Manager notebook
is absent from an export. When the authoritative notebook source is available,
the runner prefers the freshly compiled DSL. No Task Manager production file is
modified, and no backend APIs are invoked.

These five scoped passes do **not** promote `elements[].functionalBehavior` to
PASS: the registry's renderer descriptor is not itself a runtime binding, and
a passing click/keyboard path does not prove every declared state, layout,
composition, error state, and safety obligation. All 141 elements retain their
full-contract `UNVERIFIED` status until the respective complete independent
contracts are witnessed. `coverage.elements.boundedRuntimeWitnessPassed` and
`coverage.elements.independentBehaviorProven` are separate counters.

The remaining evidence gaps include individual behavior and visual states across
the other element families, actual registry-to-runtime renderer binding, per-ID
accessibility and responsiveness, high-risk effect-refusal tests, and visual
approval. There are no automatic `NOT_IMPLEMENTED` inferences from these gaps.

## Core composition + toolkit public-boundary diagnosis (v5)

This increment begins actual core and toolkit **behavior verification**, not
another registry inventory. It adds `tools/mcel_core_toolkit_runtime_probe.js`,
executed in disposable Chromium **against the original public MCEL APIs**,
plus fault-injection regression tests. The checked contracts are selected by
stable IDs; absence of a registered ID makes the check fail. The test does not
invent a renderer, trigger production effects, or change Task Manager/Website
Builder.

### Observed problems (these are FAIL, not UNVERIFIED)

1. **Six core container declarations** (`core.app`, `region`, `panel`,
   `toolbar`, `workflow`, `collection`) supply `allowedChildren`. The public
   `McelElementRegistry.serializeElement()` accepts a legal child, **but also
   preserves a registered forbidden child**. The browser witness checks both,
   and records legal/forbidden IDs for reproduction. **Scope:** registration
   serializer boundary only; this does *not* prove other MCEL DSL validation
   stages lack composition enforcement. Whether the serializer should reject,
   normalize, or delegate validation is a design decision to be made when
   implementing the fix. The currently advertised containment policy is not
   enforced at this public serialization boundary.
2. **Toolkit view resolver:** `McelToolkitCore.evaluateView()` refuses missing
   required capabilities and explicit `mustReject` choices. However, it
   reports `eligible: true` when required capabilities exist but
   `requiredPrimitives` are absent. A corresponding reason incorrectly says
   "Eligible." This is a concrete contract-gating defect at the public view
   resolver. The witness requires **both** required capabilities and primitives
   for eligibility.

These observed failures intentionally cause the cumulative acid-test run to
return **FAIL / exit code 1** until corrected; preserving FAIL is essential.
Do not downgrade or rename the checks to obtain a green report. The next
production fix should follow the issue evidence and require these exact
negative controls to pass. All mutations used by the probe's *regression*
controls occur in isolated Chromium pages only.

### Bounded positive witnesses (not full widget certification)

- `element.toolkit.foundation-token`: filtering primitives by element ID and
  defensive catalog reads using the real toolkit API.
- `element.toolkit.collection-view`: existing typed view-recipe retrieval and
  defensive reads; no list/table rendering claimed.
- `element.toolkit.contract-pattern`: real contract requirements, explicit
  rejection, and known-eligible case exercised through the toolkit evaluator;
  no view rendering or persistent state claimed.

The six core failures and four toolkit observations are added to individual
`elements[].boundedRuntimeWitness` / `boundedRuntimeEvidence` records and to
`checks[].evidence.witnesses`. **Full** `functionalBehavior` certification
remains `UNVERIFIED` across the registry; these scoped witnesses do not prove
interaction, all layout variants, renderer bindings, safety, or aesthetics.
The test index now maps these ten IDs to the actual public source files and
associated browser tests, without interpreting source references as proofs.

The existing five tab-workspace witnesses and desktop/mobile screenshot
capture remain intact. The core registry descriptors have no independently
bound renderer here, so their visual and responsive contract dimensions remain
explicitly unverified; generating pretty test-only HTML would not prove MCEL
can render them. Screenshots cannot self-approve aesthetic quality.

Run the entire acid test with `python tools/mcel_functional_acid.py --mode core`.
For narrow tests, `python -m pytest -q tests/test_mcel_core_toolkit_runtime_probe.py
 tests/test_mcel_element_behavior_audit.py` (one command on one line).
