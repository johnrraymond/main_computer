# MCEL Patch 07B.1b — Layout-free semantic authoring feasibility probe

## Purpose and hard limit

Test whether **the existing validated MCEL application IR** can express independent model types and bounded selection/read dependencies for a File Explorer-like workflow, without any authored presentation plan. This is an **IR fixture**, not an application.js, a new DSL, a production File Explorer modification, an approved interface design, or a working frontend. Production runtime, authoring compiler, catalog validator, and canonical application IR validator are unchanged.

There are two required outputs: (1) real semantic relationships supported by existing contracts and explicitly typed model references, and (2) precise facts the existing contracts do *not* establish. Success is `PASS_SEMANTIC_PROBE_ONLY`, **not** structural synthesis or `synthesisReady=true`.

## Changes

- `tests/fixtures/mcel_semantic_synthesis/file_explorer_semantic_only.ir.json`: isolated, layout-free canonical IR fixture with three knowledge-model declarations. The model metadata is derived from the existing knowledge-to-IR bridge; the fixture does **not** import catalog operations. Two `renderer-local` states contain finite, typed string enums; two `viewTransition` intents update them using the already validated bounded input-to-state contract. Two **declaration-only**, nonexecutable external-read intents declare which selection state they read. These use no effects, execution bindings, runtime methods, surfaces, layouts, or selectors.
- `main_computer/mcel_knowledge/semantic_graph.py`: extend the existing pure graph extractor with explicit `state-typed-as-model` and `input-typed-as-model` edges from `schema.of.ref`, and a **mechanically-derived** `bounded-typed-view-selection` edge *only when* both input and state refer to the same existing typed model and a validated `viewTransition` connects them. Reject malformed model type references instead of interpreting arbitrary text. None of these edges imply UI coordination or runtime authority.
- `tests/test_mcel_promise_wave7b1b.py`: 14 behavioral, ablation, anti-layout-leakage, invalid-typing, authority, and determinism tests.
- `tools/mcel_promise_wave7b1b_frob.py`: machine-readable authoring feasibility report and negative controls.

## Why the fixture uses *finite* selections

The current canonical IR's checked `viewTransition` contract requires **the same finite, nonempty string enum** in the input and renderer-local state, with `invalidInput: refuse`. It cannot establish that an arbitrary, runtime-discovered file ID is a legal value. The fixture uses fictitious `fixture-root-alpha/beta` and `fixture-entry-one/two` tokens only to prove a bounded semantic relationship. These are not catalog record IDs, actual file paths, or assertions that arbitrary directory entries can be selected.

`schema.of: {ref: "model:..."}` is an **experimental semantic type convention inside the existing open state/input schema objects**. The existing canonical IR validator resolves the reference and verifies that it exists, while the new graph extractor applies additional validation requiring a valid typed-model reference and finite string enum. This does **not** mean every existing JS compiler frontend emits `schema.of` or that such typing has been formally promoted to the canonical authoring contract. No compiler or schema extension is claimed.

## Exactly what is established

All three explicit catalog observation references join: `file-explorer.roots`, `file-explorer.entries`, and `file-explorer.read-preview`. The read-preview model's existence **does not** establish that a read operation produces or displays it.

The fixture establishes these bounded relationships:

1. `select-root` reads typed root input and applies the validated renderer-local selection transition into `selected-root`.
2. `list-directory` declares a read dependency on `selected-root`.
3. `select-entry` receives a typed entry input, declares a root read dependency, and applies the validated renderer-local transition into `selected-entry`.
4. `read-preview` declares read dependencies on `selected-root` and `selected-entry`.

The graph mechanically derives dataflow from those shared-state read/write declarations. Deleting `read-preview`'s reads removes precisely the corresponding dataflow links. Deleting a selection transition and its viewWrite removes its typed-selection edge and downstream writer-to-reader link. Mismatched model typing prevents the typed-selection edge. This is genuine semantic sensitivity, but **it is not yet a task model or interface structure**.

## Remaining missing semantics (must not be invented)

1. **Dynamic selection:** the validated `viewTransition` contract only admits a finite enum. A production File Explorer needs runtime-discovered model record identities and validated selection/reference semantics.
2. **Read results:** the fixture cannot establish that `list-directory` produces/updates the entry collection, nor that `read-preview` produces/updates the preview model. Their `reads` declarations express input preconditions only; they do not bind output results to downstream consumers.
3. **Task coordination:** state dataflow and a model-typed selection do not automatically prove an inspector, navigation tree, UI hierarchy, task priority, spatial adjacency, or interface grouping.
4. **Executable authority:** the catalog remains observation-only and its operations remain declared-only. The fixture's read intents have no runtime binding/effect and must not be used to authorize file access.
5. **Actual JavaScript authoring:** no real File Explorer `application.js` was available in the selected Oct 30 snapshot. This fixture exercises validated IR, not the end-to-end JavaScript compiler or real application lifecycle.

**Stop condition for 07B.2:** do not treat this fixture as complete enough to automatically synthesize a functioning File Explorer. Before proposing a coordinated tree, establish the dynamic-selection and read-result/consumer contracts as **semantics**, not layout hints, and specify the task-level rules that warrant coordination. Preserve the existing canonical IR and avoid a second DSL.

## Verification

```powershell
python -m unittest tests.test_mcel_promise_wave7b1a tests.test_mcel_promise_wave7b1b -v
python -m tools.mcel_promise_wave7b1a_frob
python -m tools.mcel_promise_wave7b1b_frob
```

The 07B.1b Frob emits `runtime/reports/mcel-promise-wave7b1b/report.json`, including fixture fingerprint, generated File Explorer artifact fingerprint, graph provenance, the unchanged production File Explorer insufficiency result, ablation evidence, and unresolved semantic contracts. Browser/hosted verification remains `NOT_RUN` because no production interface changed and this fixture has no renderer.

No authoring-time savings metric, aesthetic score, or uncontrolled live layout mutation is part of acceptance.
