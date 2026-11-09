# P5.3a — Dev-Test Hub inherited HTTP route boundary

## Scope

The FoundationDB Dev-Test Hub (`main_computer.exp_fdb_hub`) still inherits
`HubServerHandler` and `HubHttpServer` from `main_computer.hub` and its FDB
state adapters still depend on `HubRegistry`, `HubUpstream`, `HubWorker`,
`EnergyCreditLedger` and helper functions. This patch does not remove or
reimplement those dependencies. It establishes an **explicit allowlist** for
legacy routes that can reach the inherited HTTP handler.

`main_computer.dev_test_hub_legacy_routes.inherited_legacy_hub_route(method,
path)` is the source of truth for inherited HTTP route ownership. The Dev-Test
handler continues to handle its own `/health`, Hub identity, topology,
WebSocket Worker live sessions, paid work-request endpoint and session streams
before consulting the allowlist. Its existing explicit rejection of retired
Worker REST registration/heartbeat/poll/results endpoints also remains intact.

For approved inherited routes, the same original handler executes: no change
to credential checks, payload bounds, Worker supervision, legacy local
accounting, remote-overflow simulation, or routing selection. The GET allowlist
intentionally retains routes whose historical handler **returns 403** because
private reads lack authorization. These are not newly public APIs.

Unknown GET and POST routes receive 404 directly instead of falling through to
future additions to `HubServerHandler`. This prevents silent expansion of the
Dev-Test API when someone changes legacy code later.

## Verification commands

From the Main Computer repository root:

```powershell
python -m tools.dev_test_hub_inheritance_verify
python -m pytest -q `
  tests/test_dev_test_hub_p53_legacy_boundary.py `
  tests/test_hub_interface_p52.py `
  tests/test_dev_test_hub_p3_isolation.py `
  tests/test_chat_console_remote_worker_modal_contract.py `
  tests/test_startup_hub_flags_independent.py `
  tests/test_worker_hub_identity_health.py `
  tests/test_slh_seh_verify_battery.py
```

The verifier never contacts, deploys or mutates a Hub, and cannot spend credits.
Tests include actual loopback HTTP routing through the FDB Dev-Test handler
using a disposable in-memory test server. Test-only assets omitted from the
snapshot are not part of the patch.

## Subsequent work (not included)

The next P5.3 phase is **shared infrastructure extraction**: isolate the
inherited registry/state models, HTTP resource-policy base, and dispatch
contracts behind explicitly owned modules without changing their behavior.
Do not delete or relocate the old Hub yet. Only afterward should unused legacy
endpoints and modules be considered for retirement through a separately gated
patch. Neither this patch nor the next extraction should change Stable Hub,
SLH, SEH, Mother, Windows-boot SLH policy, or payment economics.
