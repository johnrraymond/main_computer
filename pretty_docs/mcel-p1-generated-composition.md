# MCEL P1 — Generated whole-application composition vertical slice

## Scope and truthful proof status

This is the P1.0 + P1 **source-to-generated-DOM** proof, NOT a production migration or full Task Manager parity. The official DSL has an optional closed purpose on `dsl.surface.define`; the JS/Python relationship sets now both admit `acts-on`, `informs`, `accompanies`. The old paired HTML compiler/runtime route is deliberately retained.

A layout-free read-only Task Manager process monitor lives at `tools/fixtures/mcel_p1_task_monitor/application.js`. Its only external operation is the existing task overview read (`intent:refresh`). There is no auto-refresh from semantic purpose, no terminate/kill or server/schedule operations, and no new backend authority. The application displays a selected identity **echo**, not an inspector containing fetched process detail.

## Pipeline

1. `compile_dsl_application` emits `mcel.application-ir.v1` containing the semantic `surface.purpose` and real read/selection bindings. No accompanying `application.html`, `htmlBindings`, or authored layout tree is accepted.
2. `mcel_knowledge/semantic_composition.py` derives a closed, deterministic `mcel.generated-composition.v1` region tree from validated IR node kinds, purpose and verified collection-selection state relationship. Each region has a recipe, member references, provenance reasons, stable ordinal, and canonical source fingerprint. No app ID, labels, process field names, backend records or HTML templates determine the structure.
3. `mcel_semantic_generated_host.py` revalidates the compiled operation allowlist and synthesizer output, embeds the IR and composition in the isolated test page, and refuses even a decoy sibling HTML file.
4. `mcel-semantic-operational-runtime.js` accepts `options.composition` as an opt-in alternative to the original HTML hierarchy; it verifies source hash match, generated recipe shapes, node ownership, and no elevated capabilities before touching the DOM. The original path remains unchanged when `composition` is omitted.
5. The existing `mcel-semantic-read-ports.js` uses a **separate exact app ID allowlist** for the single read-only operation; it routes to the existing `/api/applications/task/overview`. The local-only route is `/applications/mcel-semantic-task-monitor-generated` and leaves the production Task Manager route unchanged.

## Verification

`python -m tools.mcel_p1_composition_frob` writes `runtime/reports/mcel-p1-composition/report.json`. The new test covers declared purpose and vocabulary parity, no hidden HTML, pure policy determinism and name invariance, purpose intervention, missing selection contract, tampered composition, unauthorized effects, actual browser DOM creation, selection, and responsive widths 320, 390, 560, 1100 and 1272. Browser network responses are controlled mocks; a real backend service is **not** claimed.

The export lacks some package catalog material necessary for the repository-wide `conftest.py` autogeneration hook. The new test suite therefore runs with `--confcutdir=tests`, which bypasses that unrelated snapshot-only hook. Do not claim this is equivalent to passing the complete repository test suite on Windows.

## What remains before the MCEL promise is fulfilled

The vertical slice derives a whole application **hierarchy** and projects its generated regions into the existing `McelSemanticLayoutInference.inferWorkspaceLayout` / `applyWorkspaceLayout` engine for responsive geometry. Browser tests assert that the engine accepts authority and preserves selection and the frozen composition on resize. This is not yet independently evaluated expert-profile selection or a full canonical tree certification. It also does not establish live producer evidence or complete four-tab Task Manager, complex inline expansion, synchronized scroll, human expert quality, or operational parity. Those remain explicit next acceptance gates; do not call this full P1 production success.

A proper P1 follow-up must expand beyond the first shared workspace inference into canonical-tree certification and validate live snapshots, expand monitoring summary and detail without fabricated data, and test uninterrupted selection, focus, scroll, and responsive continuity. The subsequent P2 applies reusable inline expansion and coordinated scrolling across the four reference apps.

## Rollback

All new capabilities are optional. Remove the new local-only route, P1 host, fixture, synthesizer and allowlist entry, and revert the additive purpose/relationship additions and optional runtime composition path. Existing paired-HTML candidate and production application routes remain available throughout. No source writes or process-control actions were introduced.
