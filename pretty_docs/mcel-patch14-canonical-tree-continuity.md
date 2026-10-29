# Patch 14.2 — Canonical-Tree Deterministic Continuity Lab

**Status:** Implemented, offline-verified, opt-in only. **Not** production-promoted. **Not** a learned/AI layout planner or user-profile system.

## Purpose

Extend the Patch 14.0 pure layout-decision substrate and Patch 14.1 collection continuity experiment to **the existing MCEL canonical recursive layout tree**. The canonical tree remains `mcel.layout-tree.v1`; the existing renderer, independent browser measurement, and atomic apply/rollback path remain authoritative.

When an application establishes a layout, ordinary content or geometric measurement changes must not automatically authorize a new structural arrangement. When the existing choice fails hard constraints, prefer a small *named-region* repair over unnecessary global reconfiguration, provided the alternative remains sufficiently close to the best valid candidate.

## New opt-in integration

Load `mcel-layout-continuity.js` before `mcel-semantic-layout-inference.js` and supply the current canonical tree as before:

```js
const mounted = McelSemanticLayoutInference.mountCanonicalLayoutTree(host, normalizedIr, {
  continuity: "lab",
  observeResize: true,
  observeMutations: true,
  // Optional: previousState: aCompatibleSerializedCommittedState
  // Optional: continuityPolicy: {epsilon: 8000, entryMarginPx: 8}
});
if (mounted.status === "pass") {
  const receipt = mounted.refresh(); // explicit synchronous re-evaluation
  const state = mounted.getCommittedState(); // JSON-serializable, instance-local
  const reason = mounted.getLastDecision(); // includes changed named regions
  mounted.unmount(); // restores original host styles and disconnects observers
}
```

Without `continuity:"lab"`, the existing canonical-tree candidate ranking and apply behavior remain unchanged. Neither Tree IR nor application runtime operations are modified. A missing continuity module causes explicit lab requests to refuse, never silently use another policy.

### Decision, state, and measurement

- The pure `McelLayoutContinuity.planTree({tree,evaluation,previous,policy})` has no DOM or clock access. It consumes **already-measured** candidates with `failures` and measured quality from the production canonical-tree evaluator.
- A stable, named region is a canonical tree `node.id` (e.g. `workspace`, `workbench`). The layout's machine name is `mcel.layout.tree/<encoded-root-id>`; these are authored tree names, **not yet user-configured portable aliases**.
- A committed tree state (`mcel.tree-committed-layout-state.v1`) contains the policy version, canonical tree signature, discrete decision, allowed candidate choices, and commit ordinal. Measurements are not part of the decision key.
- Restored choices are checked against the current tree's enumerated allowed options and must actually describe the saved decision. A changed tree signature resets the incumbent; incompatible/tampered data is refused. There is no implicit migration yet.
- The original canonical ranking is used for **first mount**. Subsequent mounts with an incumbent use the pure continuity selector; feasible incumbent layouts are retained if their measured height is within **8px** (8000 milli-units) of the best candidate. That tolerance is an experimental **lab policy**, not a universal validated quality scale.
- A now-infeasible incumbent is not kept. Near-quality alternatives are compared by number of *changed named regions*, then other structural distance and stable code-unit tie-breaks. This minimizes **structural decisions**, not every visual displacement.
- New layouts require 8px additional measured control-width slack (entry margin) when a feasible incumbent exists. This hysteresis gate applies only where control-width slack was actually measured, and it never overrides hard failures or holds an infeasible incumbent.
- If bounded candidate generation would omit the incumbent, it is **separately measured** through the same cloned-browser hard gates, even when `maxCandidates:1`. A truncated search is not a justification for losing the incumbent.
- A candidate is applied only after the existing fresh-preview verification. Failed measurements or application leave the prior committed state unchanged; the renderer's rollback path protects its previous inline styles.
- Opt-in `MutationObserver` observes subtree child-list/text changes and coalesces them to an animation-frame commit. `ResizeObserver` uses the existing width-change threshold. For byte-for-byte deterministic replay, record the **ordered committed observations**, not browser callback timing.

## Acceptance experiment

```powershell
python -m tools.mcel_patch14_canonical_tree_continuity_frob
```

The Frob runs existing canonical recursive layout, semantic inference, collection representation, Patch 14.0 and 14.1 regressions plus the new tests:

- Pure selector: named regions, deterministic decision serialization, 1,000 content-only update rounds, no structural switches, local single-region repair when an incumbent fails, hard-gate refusal, state tampering refusal, and entry-width hysteresis.
- Real Chromium: opt-in mount preserves existing initial canonical layout; changing content retains DOM identity, focus, and region x/y placements within **0.25px**; a narrow viewport triggers a feasible structural reduction without overflow.
- Serialized state: remount retains the previous choice even when capped search omits it; invalid subtree removal refuses without overwriting committed state; automatic mutation batching updates read-only layout without losing focus.
- JS syntax checks, no browser errors, no new IR or other renderer, and no application operational execution.

The report is written to `runtime/reports/mcel-patch14-canonical-tree-continuity.json` and detailed Chromium measurements to `runtime/reports/mcel-patch14-canonical-tree-browser-evidence.json`.

## Limits and next gates

1. This experiment is **not** hosted Windows application certification and does not promote production policy. Existing Task Manager, File Explorer, Worker, and Website Builder are unchanged.
2. Node IDs are stable authored region names; **persistent named user profiles, semantic-property aliasing, migrations, and the hint ladder** are deferred to Patch 14.3.
3. The selector measures height with an experimental fixed tolerance; **independent human-calibrated quality and semantic/blind-search ablations** are deferred to the intelligent Patch 14 Frob.
4. Local region-delta minimization is a preference over verified candidates, **not** a guarantee that no other CSS element ever moves. True element displacement is measured separately.
5. Browser-frame scheduling affects *when* a snapshot is committed; replay determinism requires the recorded sequence of committed observations.
6. Externally owned DOM reparenting can drop focus. This patch does not take ownership of application nodes to compensate.
7. A single-character content change that makes a previous layout fail hard correctness constraints legitimately forces reflow, regardless of hysteresis.

**Acceptance condition:** an established canonical layout remains a deterministic, independently reverified incumbent until it loses hard feasibility or a sufficiently better validated alternative justifies the smallest available structural transition.
