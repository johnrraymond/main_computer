# Stable Hub FoundationDB chunk-transaction performance correction

**Scope:** Stable Hub's existing `FoundationDbStableWorkerSessionStore` (`main_computer/stable_hub_worker_sessions.py`), not SLH's Pinky Promise pricing or wallet policy.

## Observed failure (October 31, 2026 Windows laboratory)

The real `run --pinky-integrated` smoke-FDB laboratory failed during a Hub paid-work POST after a 25,015 ms client timeout. The Hub trace for request `slh-paid-c3f056d36c66e01e9f3b71fd` shows successful requester authorization, then repeated 128-chunk document loads, three stale-worker selections, and an 8,125 ms atomic operation during the third failed selection. A separate preflight TLS pin **rejection** is incorrectly labeled the `first_failed_lab_stage` although the preflight continued successfully; it is not the terminal paid-work error.

The prior implementation awaited each of 128 chunk reads before *starting* the next. Its save method rewrote every chunk of the entire document, even when only one Worker heartbeat, lease, or account field changed. The common metadata key was also rewritten to preserve cross-Hub conflict detection.

## Corrected behavior

- Begin all existing chunk reads before awaiting their results. Verify that each expected chunk exists; never silently accept truncated data.
- Within one `store.atomic()` callback, retain the chunks read from that transaction and write only chunks whose bytes changed. The metadata key is still rewritten on every save and remains the cross-process conflict sentinel.
- Continue deleting surplus chunks when the serialized document shrinks. Existing single-key legacy snapshots still migrate on the next atomic save.
- Reset the chunk snapshot on every optimistic FDB retry and after the transaction ends. A second load/save in the same transaction sees the preceding writes rather than stale cached bytes.
- Preserve the unchanged whole-document format and accounting semantics. There is no historical state deletion, no changed Pinky Promise pricing, and no additional wallet transfer.

## Verification

Run on the repository root:

```powershell
python -m pytest -q tests/test_stable_hub_fdb_atomic_accounting.py
python -m pytest -q tests/test_stable_hub_worker_live_session.py tests/test_stable_hub_worker_market.py tests/test_stable_hub_owner_acceptance.py
python -m tools.slh_seh_lab.run_lab run --pinky-integrated --json
```

The fake-FDB regression tests verify parallel read issuance, changed-chunk writes, remaining conflict metadata, old-document migration, shrinking documents, two sequential writes in one transaction, and cross-Hub retry/no-double-spend invariants. The final command is the required real Windows three-Hub FoundationDB acceptance test, which cannot be replaced by an in-memory pass.

**Known limitation:** This is an incremental, backwards-compatible performance correction. The full JSON document is still deserialized on every directory operation and the metadata key remains a single concurrency hotspot. If the real FDB run still shows large or timed-out transactions, collect the same Hub stage logs and then consider a separately specified key-partitioned storage migration with migration/rollback and cross-Hub equivalence tests. Do not raise timeouts or erase persistent FDB data as a substitute.
