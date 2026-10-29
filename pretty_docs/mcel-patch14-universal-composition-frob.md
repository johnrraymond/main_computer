# Patch 14.3 — Universal Semantic Composition Frob

**Status:** Experimental, offline verified, opt-in only. **No production promotion.**

## Design objective

Test the hypothesis that **one profile** (`mcel.layout.universal.v1`) can produce a useful, deterministic organizational structure from canonical computer semantics and application purpose. This is a partial *read-only* vertical slice; it is not proof that the universal profile supports all possible complete applications or outperforms custom designs.

Authors supply (1) a canonical observation binding, and (2) purpose (`monitor`, `compare`, `inspect`, `navigate`, `manage`). No authored region locations, field tiers, or display templates are necessary for this experiment. The existing Patch 11–13 knowledge compiler, interpreter, v2 semantic collection, presentation recipes and Patch 14.0–14.2 canonical layout authority are reused.

## New modules

- `main_computer/mcel_knowledge/universal_layout.py`: pure, read-only semantic capability extractor and one-profile composer. No DOM, network, application-ID branching, effects, runtime optimizer or alternate Application IR.
- `main_computer/web/applications/scripts/mcel-universal-semantic-layout-lab.js`: explicitly opted-in `mount(target, plan, {lab:true})` host. **Assembles regions** but delegates actual rendering to `McelSemanticCollectionWorkspace` and layout/continuity to `McelSemanticLayoutInference.mountCanonicalLayoutTree`. The existing generic inspector node is moved only at initial mount, before interaction; it is never recreated for every update.
- `tools/mcel_universal_semantic_compose.py`: compile/resolve/compose a source-shaped JSON snapshot to a serializable plan without UI.
- `tools/mcel_patch14_universal_composition_frob.py`: equal-budget semantic/blind candidates, strong existing baseline for Task Manager, real Chromium measurements, frozen deterministic selection and independently labeled gates.

### Usage

From the repository root:

```powershell
python -m tools.mcel_universal_semantic_compose `
  --observation task-manager.processes --purpose monitor `
  --snapshot tools/fixtures/mcel_computer_knowledge/task_manager_snapshot.v1.json `
  --output runtime/reports/mcel-universal-task-manager.json

python -m tools.mcel_patch14_universal_composition_frob
```

The Frob writes `runtime/reports/mcel-patch14-universal-composition.json` with browser candidate evidence and explicit PASS/PARTIAL/UNVERIFIED statuses.

### One profile, capabilities not templates

The composer determines facts from registered **semantic concepts and property types**, not from application names or raw producer field names. The shared compositional recipe vocabulary is reused, including `summary-strip`, `comparable-collection`, and `context-inspector`. Optional recipes are absent when their preconditions are absent. **No runtime authorization is inferred from declared operations.**

The initial scope is read-only data organization; activity, tool actions, and hierarchy require verified supporting semantics before those capabilities may be composed. For File Explorer, pathname strings alone do not establish parent-child relationships; the current catalog does not supply verified hierarchy, so the Frob honestly produces a comparative collection, not a pretend explorer tree. For Worker, PID/lease relations with `candidate-only` certainty cannot confer ownership or capability.

A plan separates stable profile identity (`mcel.layout.universal.v1`) from purpose/concept-specific `decision.name`, `semanticSignature`, and the transient `mcel.layout-tree.v1` region tree. Volatile row values and source bindings do not alter the decision. Prior committed structural decisions are retained if the signature matches. Actual browser allocation continues to rely on measured incumbent feasibility and Patch 14 continuity. **No automatic re-selection of profile during data churn.**

Exact `mcelCanonicalRecord`, source presence states, measurement units and provenance are retained in the existing inspector; numeric v2-unsupported timestamps and structured values stay deferred but recoverable. The 240px comparable-record hard minimum is enforced by the canonical region tree; this was a real failure discovered and repaired during the Patch 14.3 Frob.

## Experiment and controls

- **Strong baseline (Task Manager):** the existing authored v2 compatibility overlay, not a weakened bare field list. File Explorer and Worker use the existing generic v2 projection; they lack corresponding authored data-view contracts.
- **Blind control:** four candidates created by code-unit-stable hash ordering of property keys; no concept-type weighting in ordering.
- **Semantic variants:** four candidates created from typed/purpose-guided field ordering. The frozen policy always chooses candidate zero, not the per-cell best-of-N oracle.
- **Browser:** the *same existing MCEL v2 collection renderer* measures 320, 560 and 1100 px views, source recoverability, 240px readable record floor, count and horizontal overflow. The semantic winner additionally composes and measures its canonical regions. 3 domains × 3 widths × (1 baseline + 4 blind + 4 semantic) = **81 browser render cases**.

**Important limitations of the comparison:** Candidate variations currently explore field priority/order, not unrestricted full-profile structural search. The blind arm does not create competing full-app region compositions, so this is a scaffold for a later equal-structural-budget comparison. The source schemas are all known to the experiment; the independent File Explorer and Worker domains prove code generality, not sealed holdout generalization. Browser metric agreement **does not independently certify design quality**, and no ChatGPT/Gemma/human judgments have been run. G6 and G7 remain **UNVERIFIED** until an independent evaluator uses the retained evidence. Do not treat geometry scores as proof of semantic superiority.

Task Manager's monitoring summary uses only observed-subset state counts and measurement availability (never a whole-system population claim). It may increase the distance to the first row, especially on narrow screens; the Frob records this cost. Factual provenance and stable structure take precedence over manufactured quality claims.

## Acceptance boundaries

- **G0–G3:** existing machinery; semantic composition; cross-domain application; deterministic frozen selection.
- **G4:** PARTIAL — fixture-level structural commitment and continuity, not full hosted producer lifecycle certification.
- **G5:** correctness with v2 exact recovery, browser geometry and role-safe uncertainty. No enabled effects.
- **G6:** UNVERIFIED — no independently calibrated semantic advantage established.
- **G7:** UNVERIFIED — independent human/critic evaluation not yet performed.
- **G8:** PARTIAL — region authoring removed for this slice; total engineering LOC/person-hours not measured.

The Frob's engineering `PASS` is **not** the completed MCEL Promise and **not** a claim that a universal profile is sufficient for all applications.

### Known restrictions

1. No integrated operational workflow, schedules, hierarchical navigation, search toolbar or active intents are constructed without verified corresponding semantic capabilities.
2. No hosted Windows production adoption, user profile persistence/migration, new hints, action authority, or Website Builder changes.
3. Existing producer-specific record generation identity rules still gate DOM continuity. This Frob deliberately does not claim canonical row retention for fixture data without verified generation.
4. Different compositions for distinct purposes are *one profile with different structural decisions*, not separate templates.
5. Full unhinted layout discovery and independent task-performance advantage remain research questions, not certified outputs of this cut.
