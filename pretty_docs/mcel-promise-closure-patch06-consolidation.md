# MCEL Promise Closure — Patch 06: Cross-Application Evidence and Truth Gate

Date: 2026-10-30. Opt-in/read-side scope; no additional application and no new dangerous effects.

## Governing result

Patches 01–05 have shown bounded working semantic interfaces on Task Manager and File Explorer, and both passed Windows-hosted tests reported by the operator. That is a substantial *architectural* result, not the full product promise. Patch 04's actual first-render quality did **not** meet the operator's preferences. Comparable authoring time, independent decisions, wiring, repairs and labor have **not** been measured. Do not re-label either failure as success.

## What Patch 06 implements

1. Correct generic keyed collection reconciliation: if row identity changes but visible cell text stays the same, MCEL must update the actual row target. Previously the change detector compared only rendered cell values. Retained keys still preserve DOM/focus on value-only changes. This applies across all applications, not File Explorer specifically.
2. Strengthen the Wave 5 hosted gate: explicitly click the generated *Refresh directory* button; require the observed version to advance, the generic intent/effect receipt to report completion, and the semantic adapter to return an actual successful read receipt. The former `all([])` receipt test could pass vacuously. Require exact ordered root and entry identity parity, not just counts. A previously reported Wave 5 hosted PASS does not automatically satisfy these stronger tests.
3. Add `tools.mcel_promise_closure_frob` to consolidate current source-bound Wave 4/5 report files and repository provenance, preserving missing source/host evidence as blocked. The certificate never promotes user-disliked first renders, unknown labor savings, greenfield authoring or consequential mutations to PASS.
4. Include a reproducible source and author-effort inventory. Line counts are metadata, **not** an estimate of author labor.

## Run on the actual Windows machine

From the repository root, with localhost running:

```powershell
python -m pytest -q tests/test_mcel_promise_closure.py tests/test_mcel_promise_wave4.py tests/test_mcel_promise_wave5.py
python -m tools.mcel_promise_wave4_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes"
python -m tools.mcel_promise_wave5_frob --host-url "http://127.0.0.1:8765/applications/file-explorer"
python -m tools.mcel_promise_closure_frob
```

Output: `runtime/reports/mcel-promise-closure/report.json`. The closure command exits with code **2** when the complete promise remains unproven; this is the expected bounded result, not an installer failure.

The supplied repository export omits both author-owned experiment `application.js` files and generated runtime reports. Those files are **not** manufactured from the compiled artifacts. Tests requiring the missing original DSL sources are blocked in the export, not counted as passes or regressions. The local working tree reportedly has the sources; run the full sequence there.

## Still open — do not misrepresent as completed

- *Independent visual acceptance:* operator disliked Patch 04. Quality needs genuine improvement, not just a geometry PASS.
- *Authoring economy:* equivalent-scope decision, CSS, wiring, correction and time comparison is required. A two-profile toggle with no second source establishes a narrower incremental cost only.
- *Greenfield public authoring:* a new-app authoring demonstration is a separate requirement of the original principles; duplicating existing migrations does not prove it. Do this as a controlled future evaluation, **not** as a third app in this patch.
- *Consequential authority and receipts:* File Explorer remains read-only, and Task Manager's generated shell admits Refresh but not server/process/scheduler mutations. One advancing snapshot is not a unique HTTP request receipt.
- *Production promotion:* candidate and proven states, independent verification and rollback must remain separate. No automatic promotion from this certificate.

**Interpretation:** Consolidation makes the unfinished promise mechanically identifiable and closes two concrete correctness/evidence defects. It does **not** close the whole MCEL promise, because some obligations require actual new authoring capability, comparison trials and user-visible design improvements.
