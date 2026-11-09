# P5.3h — Legacy Worker client extraction

This is a source-preserving ownership extraction, not a change to Worker
registration or execution. The legacy Worker REST-pull client now lives in
`main_computer/hub_worker_client.py` and is still re-exported through
`main_computer.hub` with the same callable identities.

Extracted unchanged: Worker API headers and error formatting,
`register_worker_with_hub`, `_post_hub_worker_json`,
`_worker_pull_response_payload`, and `serve_hub_worker_pull`.
`serve_hub` and the legacy Worker HTTP serving entrypoint stay in the facade.
The Stable Hub authenticated live-session delegation remains exactly as
before; no change to payment, Worker network selection, credit valuation,
Worker-page settings, Chat Console, FoundationDB, SLH or SEH.

The former P5.3g verifier has been updated from checking that the functions
are defined *inside* the facade to checking source-equivalence, identity,
and ownership of the extracted module. The older Captain test stubs now
accept the existing CLI `network` keyword and check the unchanged
`X-Main-Computer-Client: main-computer-worker-cli` header. No production
behavior was changed to satisfy those tests.

## Read-only verification (Windows PowerShell)

```powershell
python -m tools.hub_worker_client_verify
python -m tools.hub_worker_http_verify
python -m tools.hub_http_legacy_handler_verify
```

Each verifier reads source/import metadata only; no Hub contact, Worker launch,
FoundationDB transaction or payment.

## Regression tests

```powershell
python -m pytest -q `
  tests/test_hub_worker_client_p53h.py `
  tests/test_captain_smoke_engage_command.py `
  tests/test_hub_worker_http_p53g.py `
  tests/test_hub_http_legacy_handler_p53f.py `
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
  tests/test_slh_seh_verify_battery.py `
  tests/test_hub_cli_live_session.py
```

Acceptance proves source preservation, import identity, request headers,
registration payload and credit representation, insecure-transport rejection,
REST-pull state transitions, and delegation to the established authenticated
Stable Hub live-session Worker. Calls in tests use fake transports and mock
Worker functions. This is not proof of a live paid request or production Hub
availability.
