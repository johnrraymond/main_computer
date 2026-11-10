# P5.5b — Worker Supervisor Recovery Acceptance

## Purpose and boundary

Prove that P5.4h's authenticated Worker offer and P5.5a's availability guarantees
survive supervisor restart, reconnect, network switching, MSK authorization
failures, and stale-session cleanup. This is an **isolated test-only patch**; it
neither alters production Hub/Worker/SLH/SEH logic nor contacts remote Hubs,
FoundationDB, or financial systems. The existing P5.5a test fixture is reused
rather than copied, so P5.5a is a prerequisite.

## New test: `tests/test_worker_supervisor_recovery_p55b.py`

1. Four network-specific supervisor-restart cases: submit an offer while busy;
   verify saved pending intent and no registration; instantiate another real
   `WorkerRuntimeSupervisor`; allow normal idle; verify one authenticated
   registration to the original canonical Dev/Test/Testnet/Mainnet Hub; verify
   the next reconcile does not duplicate registration or set Work now.
2. Two MSK failures (expired/revoked, modeled by the authorization boundary):
   pending offers cannot register, remain pending, and produce the supervisor
   diagnostic; after reauthorization a later reconcile publishes once. These
   tests exercise failure handling, **not** key-store expiry scheduling itself.
3. Network switch after a deferred offer: supervisor refuses to replay the old
   Dev offer against Mainnet. Only a freshly submitted Mainnet offer may send
   an authenticated registration.
4. Hub rejection: failed registration closes the attempted socket, records a
   failed status with no Worker ID, and does not silently retry on periodic
   reconciliation; explicit resubmission is required.
5. Local availability changes during Hub acceptance: the new accepting
   session is closed and the offer remains pending; normal idle becoming
   permissible again allows supervisor synchronization.
6. Network selection changes during Hub authentication: the original Hub
   session is closed and no market registration is reported as accepted.
7. Real local live-session registry cleanup: idle sockets are popped and
   closed; active-job sockets are preserved with `close_deferred`.
8. Two cached-status proofs: an allegedly CONNECTED status lacking a live
   heartbeat or reporting `alive=false` must be displayed as RECONNECTING
   and non-accepting.
9. Real Worker runtime heartbeat recovery: a failed heartbeat produces FAILED
   and non-accepting, the next successful live-session heartbeat restores
   CONNECTED, and no new market registration is submitted.

## Explicit non-claims

The Dev/Test and Mainnet/Testnet registration doubles do not establish live
remote Hub availability or real MSK revocation propagation. The runtime FSM
heartbeat test uses an injected deterministic WebSocket transport response,
not a production network connection. These are tests of application recovery
behavior; actual isolated Stable Hub transport acceptance belongs in P5.5c,
SLH/SEH in P5.5d and accounting in P5.5e.

## Acceptance commands (Windows, repository root)

```powershell
python -m pytest -q tests/test_worker_supervisor_recovery_p55b.py

python -m pytest -q `
  tests/test_worker_supervisor_recovery_p55b.py `
  tests/test_worker_offer_lifecycle_p55a.py `
  tests/test_worker_offer_authenticated_p54h.py `
  tests/test_worker_app_registration_proxy.py `
  tests/test_hub_offer_protocol_decision_p54g.py `
  tests/test_hub_worker_offer_routing_p54f.py `
  tests/test_hub_legacy_rest_consumers_p54e.py `
  tests/test_hub_worker_explicit_rest_p54d.py `
  tests/test_hub_interface_p52.py `
  tests/test_worker_runtime_supervisor.py
```

Expected results on the local source set: **14 tests** for P5.5b alone;
**122 tests** for the combined selection. Windows remains to be verified.
The existing Windows temporary-directory cleanup fix to
`tests/test_worker_app_registration_proxy.py` should remain applied.

## Source changes

- New: `tests/test_worker_supervisor_recovery_p55b.py`
- New: `pretty_docs/p55b_worker_supervisor_recovery_acceptance.md`
- **No** changes to `main_computer/`, CLI, Docker, Stable Hub, Dev-Test Hub,
  SLH, SEH, Mother state, Worker supervisor, FoundationDB, or accounting.
