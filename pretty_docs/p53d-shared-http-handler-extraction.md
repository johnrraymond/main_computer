# P5.3d — Shared bounded HTTP/JSON handler extraction

This change extracts the *unmodified* 405-line `_JsonHandler` implementation
from `main_computer/hub.py` to `main_computer/hub_http_shared.py`, without
changing Hub HTTP routes, payment checks, FDB storage, Worker registration,
requester policy, or deployment topology.

The legacy `main_computer.hub._JsonHandler` import is retained by an identity
alias. The new name `HubJSONRequestHandler` points at the very same class.
The legacy Hub handler, older Worker HTTP handler, and the FoundationDB
Dev-Test handler therefore inherit exactly the same request header/body
admission, bounded JSON decoding, response serialization, worker route
admission diagnostics, and request event streaming implementation.

`HubHttpServer`, `HubServerHandler`, and `HubWorkerHandler` remain in
`main_computer.hub`. In particular, `ExperimentalFoundationDbHubServerHandler`
still inherits from `HubServerHandler`; its legacy route allowlist from P5.3a
continues to apply unchanged. **This patch does not remove that inheritance**
and does not claim full Dev-Test/legacy HTTP separation.

## Read-only check

```powershell
python -m tools.hub_http_shared_verify
```

All booleans must be `true` and `ok` must be `true`. This command imports the
classes and hashes the exact extracted source. It opens no Hub connection,
executes no FDB transaction, and cannot charge credits.

## Focused regression acceptance

```powershell
python -m pytest -q `
  tests/test_hub_http_shared_p53d.py `
  tests/test_hub_resource_limits.py `
  tests/test_hub_registry_json_p53c.py `
  tests/test_hub_registry_shared_p53b.py `
  tests/test_dev_test_hub_p53_legacy_boundary.py `
  tests/test_hub_interface_p52.py `
  tests/test_dev_test_hub_p3_isolation.py `
  tests/test_chat_console_remote_worker_modal_contract.py `
  tests/test_startup_hub_flags_independent.py `
  tests/test_worker_hub_identity_health.py `
  tests/test_slh_seh_verify_battery.py
```

The direct HTTP tests create a disposable loopback server and do not run a
Hub or use real Worker resources. They cover ordinary GET/POST responses,
invalid JSON, and oversized declared bodies.

## Remaining dependency

Moving the shared HTTP/JSON base does not move the legacy Hub server,
`HubDispatcher`, or the compatibility route handler. A later extraction must
explicitly preserve their initialization, route allowlist, and financial
behavior before removing imports of `main_computer.hub` from the Dev-Test
server. Do not delete the old handler or change production Hub deployments
based on this check alone.
