# MCEL Promise Closure — Patch 06a: Complete Collection Fidelity

Date: 2026-10-30. Scope: generic read-side semantic collection renderer.

## Observed failure

After Patch 06, the File Explorer hosted Wave 5 report showed 181 backend directory records and only 100 rendered records in **both** presentation modes. Roots, identity/source hashes, refresh completion, read receipts, permission state and browser checks all passed. The new exact-entry-parity checks correctly failed. The source was complete; the generic shell contained `data.slice(0,100)` in `update` for every collection.

## Repair

Do not silently discard the 101st and subsequent records. The renderer now consumes the complete delivered array and retains the Patch 06 keyed-identity reconciliation and no-remount focus protections. The application-specific File Explorer adapter, backend, source DSL, permissions and Wave 5 hosted acceptance thresholds are unchanged. Real pagination or virtualization, if later needed for enormous lists, must be an explicit semantic contract preserving all entries, not hidden truncation.

## Verification

`pytest -q tests/test_mcel_semantic_collection_full_fidelity.py tests/test_mcel_promise_closure.py tests/test_mcel_promise_wave5.py` validates 99, 100, 101, 181 and 300 rows plus row identity, focus, selections beyond index 100, updates, changed keys, and teardown. Run the existing localhost hosted Wave 5 Frob against the real Windows directory, then refresh the closure certificate. Only these two failed Wave 5 checks should change from FAIL to PASS for an unchanged source dataset.

```powershell
python -m tools.mcel_promise_wave5_frob --host-url "http://127.0.0.1:8765/applications/file-explorer"
python -m tools.mcel_promise_closure_frob
```

Patch 06a does not claim improved aesthetics, authoring economy, consequential effects or full promise closure. It fixes a genuine shared data-fidelity defect.
