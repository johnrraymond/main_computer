# P5.4d — Explicit legacy Worker REST transport policy

This is a **behavioral compatibility and safety change**, not a Hub protocol
removal. It changes only the foreground Captain/Data Worker-pull selection
when a Hub identity does **not** advertise authenticated live sessions.

## Effective policy

| Hub identification | Captain/Data default | With `--allow-legacy-worker-rest` |
| --- | --- | --- |
| Verified Stable Hub live-session service | Use authenticated live session | Still use authenticated live session |
| Verified Dev-Test FDB live-session service | Use authenticated live session | Still use authenticated live session |
| Unrecognized advertised service | Refuse before REST registration | Allow retained legacy REST register/heartbeat/poll/result |
| Identity probe failed / unreachable | Refuse before REST registration | Refuse; no silent retry |
| NanoJev with non-live identity | Refuse | Refuse; always live-session only |

The flag is intentionally **not** a global setting and is **not** persisted.
It must be supplied on each explicit compatibility command. The Worker page's
selected network, Chat Console settings and production Worker supervisor are
unchanged. No change to `hub-register-worker`, which is already an explicitly
named REST operation, or to the old Hub HTTP endpoints.

## Operator acceptance (Windows)

```powershell
python -m tools.hub_worker_transport_policy_verify
python -m tools.hub_worker_retirement_decision
python -m pytest -q `
  tests/test_hub_worker_explicit_rest_p54d.py `
  tests/test_hub_worker_retirement_decision_p54c.py `
  tests/test_hub_worker_client_p53h.py `
  tests/test_hub_cli_live_session.py `
  tests/test_captain_smoke_engage_command.py
```

To use a **confirmed compatible legacy Hub** intentionally, append
`--allow-legacy-worker-rest` to either the `captain smoke john luc picard engage computer`
command or `data engage computer --agent` command. Do not use this flag with
Stable/Dev-Test Hubs; they already use authenticated live sessions.

**Important limitation:** The pre-existing identity probe fails when its
`/api/hub/v1/hub-identity` request fails. The explicit option does **not**
turn an identity-probe error into permission to use REST. Supporting
older Hub implementations without that identity endpoint would require a
separate authenticated compatibility/discovery contract and test evidence.

No live Hub, paid request or deployed Worker was tested by this patch's
isolated regressions. Deployment and external-consumer absence are unverified;
no implementation is approved for deletion.
