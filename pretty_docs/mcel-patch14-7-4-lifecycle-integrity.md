# MCEL Patch 14.7.4 — source identity and logical interaction integrity

**Scope:** Corrects the 14.7–14.7.3 lab contract. No change to production Task Manager or permission to perform source-side operations.

## 1. Source transition contract

The collection classifies each prior/current source membership independently of DOM-node continuity:

| Event | Classification | Retirement allowed? |
| --- | --- | --- |
| A key was absent, now present | `added` | No |
| A key remains present; verified generation matches | `continuous` | No |
| A key remains present; generation absent/unknown | `continuity-unverified` | **No** |
| A key remains present; verified generation changed | `identity-replaced` | **No** |
| A previously present key is missing from the current **supplied** snapshot | `source-absent` | Yes, per element policy |

The unverified case deliberately **remounts** the row rather than assuming the process instance is continuous, but cannot create an absent-source retirement snapshot. Repeated identical snapshots therefore do not generate retired ghosts. Generation replacement also remounts and invalidates old presentation ownership without claiming source disappearance.

An absent key means *absent from the observed snapshot*, **not proven terminated in underlying reality**. Collection consumers must separately attest completeness/freshness if they wish to make stronger domain claims.

## 2. Logical element interaction ownership

`McelElementRetirement.create` accepts an optional `interactionRegions(row)` callback. It returns zero or more DOM presentation surfaces belonging to the *same logical element*—for example, the floating card anchored to a retained row. The retirement engine uniformly evaluates native selection intersection, focused descendants, `:hover`, and `aria-expanded` across all connected regions, filtered through the **element's own** `pauseWhen` policy. This keeps hover/focus protections independent of which physical surface holds the pointer and does not override explicit host scroll-fade dismissal.

Legacy `interactionFlags` remains supported for special adapters. No Task Manager domain selectors are introduced into the retirement engine.

## 3. Rejected-update cleanup

If canonical layout installation rejects an update, the collection cancels only retirement entries created by that failed update, then reconciles the previous source snapshot. Previously accepted retirements keep their clocks. Rejected updates do not leave new retired ghosts in the rollback view.

This is a targeted rollback safeguard. It does not certify whole-application atomicity for arbitrary host failures, nested virtualized scrolling, or asynchronous data producers.

## 4. Reproduce the cross-lifecycle test

```powershell
python -m tools.mcel_patch14_7_4_lifecycle_integrity_frob
python -m pytest -q tests/test_mcel_patch14_7_4_lifecycle_integrity.py
```

The Frob tests eight real Chromium scenarios: unchanged unverified observation, continuous verified generation, hover protection while floating, native selection, seven repeat updates, same-key generation replacement, hover-release retirement reset, and layout failure injection/rollback. The output is `runtime/reports/mcel-patch14-7-4-lifecycle-integrity/report.json`.

## 5. Acceptance boundary

Previously shipped 14.7 tests and the independent 14.7 audit must pass. This patch remains an **opt-in read-only semantic lab repair**; it is not authority to promote the implementation into the production Task Manager. Remaining unverified areas include nested/virtualized hosts, complete lifecycle transaction atomicity beyond these injected failures, source observation completeness, and unbounded retention resource behavior.
