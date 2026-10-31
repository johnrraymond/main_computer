# MCEL Patch 07B.1a — Layout-blind semantic dependency audit

## Scope and claim

07B.1a is a **diagnostic feasibility experiment** toward MCEL's promise of autonomously synthesizing an appropriate application interface structure from semantic JavaScript. It is NOT a synthesizer, design selector, renderer, or change to authoring/runtime behavior. No abstract-interface tree is produced or accepted, and no online or offline design correction is performed.

This patch uses the 2026-10-30 `main_computer_test-20261030-160718(1).zip` as its baseline. The Claude adversarial review reported a later `...162919.zip` not available in this session. Before applying to a newer tree, check for drift.

## New module and boundaries

`main_computer/mcel_knowledge/semantic_graph.py` exports:

- `extract_validated_graph(normalized_ir)`: **pure** structural projection; caller must supply already validated/normalized canonical `mcel.application-ir.v1`. It reads only `schema`, `models`, `states`, `derivations`, `intents`, `capabilities`, and `effects`. It does not inspect `surfaces`, `layouts`, `semanticSurface`, `applicationShell`, labels, positioning, CSS, DOM, source bindings, or the knowledge catalog.
- `audit_application(raw_ir, catalog=None)`: validates with the **existing** `validate_application_ir` before extraction. Important distinction: the validator inspects the whole canonical IR, including existing presentation fields, to certify validity. The graph **extractor** is presentation-independent, not the prerequisite validator.
- `audit_catalog_join(graph, catalog)`: accepts only explicit model `observationRef` IDs whose typed concept and cardinality match the independently registered knowledge observation. Matching names, app IDs, routes, or inferred record shapes do not establish a join. The join never imports catalog operations, mutations, or capability authority.

The graph contains typed nodes (model, field, state, intent, intent input, derivation, capability, effect), typed declared edges with normalized JSON pointer provenance, and mechanically-derived shared-state **dataflow** edges. Shared state *does not* imply navigation, task priority, selection-to-details, or UI coordination. Explicit dependency edges do not create executable authority. No control widgets, task nodes, selection relationships, inspector patterns, or identities are fabricated.

`projectionFingerprint` is computed separately from the preexisting canonical IR `semanticFingerprint`: the latter deliberately includes surfaces/layouts and is not suitable for layout-ablation proofs. The projection fingerprints intrinsic semantic facts/edges, not evidence addresses, source paths, timestamps, cosmetic labels, or positional structures.

### Audit result interpretation

- `SEMANTICS_INSUFFICIENT`: no declared intent read/write/viewWrite dependencies; **not** a synthesis PASS.
- `DEPENDENCIES_EXTRACTED`: one or more declared data dependencies are present. **Still not evidence of an abstract interface or established task coordination** (`synthesisReady=false`).
- `BLOCKED_UNJOINED_SOURCES`: catalog contains observations not explicitly joined through typed model `observationRef` in this executable IR.
- `JOINED_MODELS_ONLY`: model→catalog observation crosswalk established. Does **not** authorize catalog operations or assert any workflow dependency.
- `FAIL_CONTRACT`: canonical IR invalid or an extraction contract is violated. Never fall back to presentation information.

## Baseline facts verified

| Case | Expected output | Meaning |
|---|---|---|
| Generated File Explorer candidate IR | `SEMANTICS_INSUFFICIENT`, catalog `BLOCKED_UNJOINED_SOURCES` | Six intents, no models, states, derivations, or read/write relations. Root→entries→preview behavior cannot legitimately be inferred from the authored application shell. |
| Generated Task Manager notebook candidate IR | `DEPENDENCIES_EXTRACTED` | Genuine state and input/viewWrite dependencies exist. These prove a dataflow, **not** a complete interface organization. |
| Reference Counter fixture | `DEPENDENCIES_EXTRACTED` | Intents read/write canonical state; shared-state dataflow emerges mechanically. No selection/inspector inference. |
| Knowledge IR from `compile_knowledge_ir` | `JOINED_MODELS_ONLY` | Explicit catalog observation models have semantic identity; catalog operations remain nonexecutable and no task relation is invented. |

Generated candidate artifacts cite source paths under `mcel_experiments/` but the author-owned File Explorer JavaScript source is not present in the baseline snapshot. The Frob explicitly reports `NOT_RUN_SOURCE_MISSING`. That is not a source-authorship PASS. No hosted/browser evidence is claimed; existing production behavior is unchanged.

## Contract tests and negative controls

`tests/test_mcel_promise_wave7b1a.py` includes a dictionary access trap that raises if the extractor reads the presentation/layout sections; deletion and poisoning of the old shell; cosmetic name changes; deterministic normalized output under dict/declaration order variation; a legal read-set ablation; unjoinable catalog model references; no fabricated identity, authority, selection, task, or inspector; and fail-closed malformed IR. Tests also verify that shared-state dependencies are classified as dataflow only.

### Commands

```powershell
python -m unittest tests.test_mcel_promise_wave7b1a -v
python -m tools.mcel_promise_wave7b1a_frob
python -m pytest -q tests/test_mcel_promise_wave7b1a.py
```

Frob output: `runtime/reports/mcel-promise-wave7b1a/report.json` containing case-specific graphs, provenance, source-truth limitations, and machine-readable gate checks. `PASS_AUDIT_ONLY` means the *auditor* behaves as specified; it **never** means a coordinated interface was synthesized.

## Next gates, intentionally deferred

1. **07B.1b — semantic authoring probe:** build a semantic-only fixture (not new runtime authoring source) whose declared states, typed intent inputs, read/write and view-write contracts establish actual selection and dependency relationships. Validate it against existing IR authority constraints; correct the authoring contract only where evidence proves insufficiency. Do not manually give it a UI tree.
2. **07B.2 — structural synthesis:** only once enough *valid* semantic relations exist, select and compose generic patterns into provenance-backed abstract interface trees. Do not repurpose the single-observation `universal_layout.py` as a graph synthesizer or treat shared-state dataflow as a verified UI arrangement.
3. **07B.3 — adapter to existing layout engine:** demonstrate real working application fidelity after synthesizing the tree; do not fabricate render/hosted PASS reports.
4. **Later — deterministic promotion/refactor:** stable semantics → stable approved structure, with confined changes on actual author refactors. No live redesign.

No comparison of authoring time/labor is an acceptance gate.
