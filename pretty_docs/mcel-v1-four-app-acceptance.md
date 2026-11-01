# MCEL V1 Four-App Reference Qualification and Acceptance

**Normative V1 target; qualification not yet complete.** Governed by [V1 Product Contract](mcel-v1-product-contract.md). The four-app suite evaluates the shared [HTML+JS authoring format](mcel-v1-authoring-format.md), [semantic vocabulary](mcel-v1-semantic-vocabulary.md), [compiler/layout/runtime](mcel-v1-compiler-layout-runtime-contract.md). Do not treat existing host-bound implementations or semantic-only probes as certified substitutes.

## Suite boundary

The **only** V1 qualification apps in this suite are **File Manager/File Explorer**, **Task Manager**, **Code Editor**, and **Calculator**. **Website Builder and MCEL Lab are expressly excluded** from this reference suite (their historical documentation and applications are not deleted). These four exercise distinct classes of interaction, not an assertion that the universe of application patterns is exhausted.

## Per-application required parity

| Reference app | Semantic structure MCEL must understand | Real behavior and safety to preserve | Structural/design challenge |
| --- | --- | --- | --- |
| File Manager / File Explorer | Hierarchical locations, scoped directory-entry collection, selected-entry inspection, search/query controls, file commands and receipts | Real root discovery, directory navigation, scoped identities, searching, authorized supported read/preview and file operations, exact data parity; **do not invent unsupported write paths** | Navigation/workspace/inspection coordination on wide and narrow hosts |
| Task Manager | Primary monitoring workarea, process records/measurements, multiple perspectives, status and governed commands | Real live process observations, refresh, notebook perspectives, selection identity and existing authorized terminate/kill/server operations with exact permissions and receipts; **no quiet read-only downgrade** | Live update density, continuity and accessible consequential actions |
| Code Editor | Document workspace, navigation, editing/diagnostics, optional auxiliary results and commands | Real Monaco editing, document identity, dirty lifecycle, save/restore, supported Aider planning and reviewed-patch/source-write authorization; distinguish available diagnostics from proposed features | Editable persistent work, focus, command/auxiliary coordination |
| Calculator | Inputs, expressions, results, mode control, supported graphical or numerical representations | Actual calculation/computation, supported modes, operation outcomes/receipts and legitimate Mathics/backend/graphing functions where proven in baseline; no fabricated capabilities | Computation-first interface, not a mandatory collection/table shell |

**Baseline obligation:** Before changing an app, inventory the actual working implementation's supported operations, typed inputs/results, effects, risk/authority, data identity, states and browser paths. If a candidate source `application.js` is missing from an older repository snapshot, reconstruct from verified current app behavior; never claim historical source recovery. Unsupported or prohibited operations are not made available simply to satisfy a table row.

## Shared source conformance

For every app, there must be an authored `application.html` and `application.js` using the **same** official format. The semantic HTML must preserve region parentage and meaningful order, role/task/content semantics and preferences, with no app-specific outer layout tree, bespoke pane CSS or authored finished table/control DOM. The JavaScript must retain actual operational semantics and authority. Together they produce one validated canonical IR with complete cross-file references. Any unsupported source capability is reported as `INCOMPLETE`—never replaced by mock behavior and mislabeled success.

## Vocabulary and synthesis conformance

Every semantic term used must be recognized, have well-defined provenance, and influence a legitimate design, constraint, diagnostic or verification consumer. Positive and ablation tests should demonstrate when a term matters. When the author's intent is ambiguous, the compiler/engine reports the unmet decision or chooses a provably safe candidate; it does not invent functional coordination. Pattern experts operate by semantic constraints, not hardcoded app IDs. Structural diversity is evaluated where app semantics differ; pairwise non-isomorphic interfaces are *evidence*, not an unconditional artificial requirement.

## Live functional gates

For each app, execute real user actions and validate the corresponding real-state changes, returned data, denied effects, successful/refused operation receipts, stable record identities and read/result bindings. Assert exact record counts and keys where a source supplies them, not merely visually sampled or truncated data. Preserve state across refresh, view switch and resize, with no unrequested mode change, lost edits, lost selection or focus reset. No uncontrolled runtime layout self-repair or ongoing speculative redesign.

## Browser and visual gates

Run independent browser acceptance on the real generated routes at widths **320, 390, 560, 1100, 1272**. Test navigation, keyboard order, input/editor interaction, command accessibility, responsive layout, scroll ownership, region containment, meaningful visible records, text readability, status announcements and accessibility. Record images and geometry alongside human evaluation of whether the workarea has appropriate prominence and whether the application is actually usable—not merely whether it fits on screen. Host/container width must govern geometry decisions.

## Determinism and refactor gates

Repeated compilation with identical semantic inputs/policy version yields equivalent IR, structure and accepted design. Semantic-preserving source refactors should leave functional identity and relevant layout decisions stable. A meaningful author intent change may alter the design, but only with traceable reasons, preserved operational authority and successful reacceptance. No cross-app, app-name-specific layout shortcuts.

## Acceptance statuses and promotion

- `SOURCE_PASS`: valid pair of authored HTML+JS and all source/IR references survive.
- `STRUCTURE_PASS`: one shared synthesizer produces legitimate patterns and layouts for the app semantics.
- `RUNTIME_PASS`: real required operations, effects and state/lifecycle pass hosted checks.
- `DESIGN_ACCEPTED`: responsive, accessible and practical expert-design review completed with evidence.
- `V1_APP_CERTIFIED`: all preceding gates green on a promoted candidate with a verified rollback plan.
- `V1_SUITE_CERTIFIED`: all four individually certified under the same V1 contract and no outstanding release-blocking regressions.

An app that reaches only source-compilation or semantic role probe remains **INCOMPLETE**. Any required inability to execute authorized operations, identity loss, inaccessible action, broken selection, or invalid design prevents promotion. Preserve old working versions until candidate proof is complete. No comparative author-time/labor gate.

## Current versus target

Current host-bound Calculator/Code Editor, File Explorer/Task Manager candidate IR, and 07B.1c role probes provide useful implementation evidence, but the two-file format, complete semantic-to-structure synthesis and four-app end-to-end V1 certification remain **target obligations until independently demonstrated**. `mcel-status-and-roadmap.md` and generated proof are authoritative for achieved current status. [Paired source specimens](mcel-v1-reference-authoring-examples.md) illustrate desired source, not certified apps.
