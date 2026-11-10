# P5.5c — Persistent Stable Hub Transport Acceptance

## Objective

Verify the existing authenticated Worker WebSocket against a **real isolated Stable Hub HTTP/WebSocket server**, not a mocked protocol response. This is a tests-only gate; the backend-owned Worker connection remains persistent *while normal policy permits accepting work* and disconnects when policy forbids it.

## Boundaries

- Use `create_stable_hub_server` on loopback with port `0` and memory-backed MSK/Worker stores. Never connect to the deployed Mainnet, Testnet, SLH, SEH, or production FoundationDB.
- Issue a wallet-signed MSK on the local Hub and authenticate through the actual `worker.auth` transport. Validate `hub.auth.accepted`, initial `hub.ping`/`worker.pong` and subsequent keepalive.
- Check that repeated registration reconciles and volatile activity changes retain **one socket and one market slot**.
- A changed price forces safe socket replacement and closure of the original Hub slot. An active job defers replacement.
- Terminate the real TCP connection server-side and verify the existing backend socket owner detects failure and the actual periodic supervisor thread reauthenticates a fresh socket without a manual reconcile. The test fixes local eligibility to isolate transport recovery; P5.5a/P5.5b cover availability policy.
- Invoke the production `not_accepting` path to close a real session without reauthentication. An invalid MSK must not publish capacity or enable REST fallback.

## Commands

```powershell
python -m pytest -q tests/test_worker_stable_hub_persistent_p55c.py
python -m pytest -q `
  tests/test_worker_stable_hub_persistent_p55c.py `
  tests/test_worker_runtime_supervisor.py `
  tests/test_worker_supervisor_recovery_p55b.py `
  tests/test_worker_offer_lifecycle_p55a.py
```

## Acceptance limit

This verifies real localhost WebSocket transport, the memory-backed Stable Hub worker-market records, and deterministic supervisor reconciliation. It is **not** evidence of live production Mainnet availability, payment settlement, SLH/SEH routing, real MSK propagation between deployments, or completed paid AI jobs. No production code or deployment changes are authorized by this patch.

## Evidence and known pre-existing test debt

The P5.5c suite has nine tests. The integrated selection with P5.4h/P5.5a/P5.5b and Worker supervisor tests has 131 tests. The existing Worker live-session focused tests (`-k "keepalive or fingerprint or runtime_close or runtime_sync"`) contribute four additional isolated tests. Run these on Windows for acceptance.

An older, separate `tests/test_stable_hub_worker_live_session.py` group includes fixtures with no `market.models`, which the current Stable Hub model contract rejects. That failure reproduces on the unchanged pre-P5.5c snapshot; those legacy fixture updates are explicitly **not** bundled into this tests-only patch and the suite is not counted as passing.
