# P5.3e — Shared Hub HTTP runtime extraction

The existing `HubDispatcher` and `HubHttpServer` classes now live in
`main_computer/hub_http_runtime.py` instead of the legacy `hub.py` module.
The legacy module **re-exports the exact same class objects** so import paths
and public constructors remain valid. The dispatcher implementation is
source-identical to P5.3d. HTTP server diagnostic methods are source-identical.
The only constructor difference is an optional `handler_cls` parameter:

- Legacy Hub callers omit it and retain the original `HubServerHandler`.
- The Dev-Test Hub selects `ExperimentalFoundationDbHubServerHandler`
  explicitly when constructing its server, rather than setting it immediately
  after base initialization.

This separates server setup and dispatcher ownership from the legacy route
implementation. The large legacy `HubServerHandler` and the Dev-Test handler
**remain in their original modules**. Further route extraction requires a
separate verification patch; P5.3e is not a deletion authorization.

No payment authorization, ledger, FDB transaction, Worker routing, Worker page,
Chat Console, network selection, SLH/SEH, or production Hub deployment changes.

## Read-only ownership verification

```powershell
python -m tools.hub_http_runtime_verify
python -m tools.hub_http_shared_verify
```

## Focused acceptance

```powershell
python -m pytest -q `
  tests/test_hub_http_runtime_p53e.py `
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

The new tests exercise both default legacy dispatch and explicit Dev-Test
handler selection, including local loopback HTTP; neither route uses paid
work, live FDB, or remote Hub endpoints. The next boundary is the giant legacy
HTTP route handler. Preserve its Dev-Test compatibility allowlist.
