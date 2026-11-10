# P5.3i — Legacy Hub Facade Audit and Helper Extraction

This patch is an audit and source-preserving extraction, **not a deletion of
legacy protocols**. The remaining five simple `hub.py` helper functions now live
in `main_computer/hub_facade_helpers.py`; `hub.py` still re-exports the exact
same callable objects. `serve_hub` and `serve_hub_worker` remain defined in
`hub.py` to preserve their existing CLI and test monkeypatch seams.

A frozen list of **139 historical module exports** is checked by
`python -m tools.hub_facade_verify`. That count deliberately includes
incidental imported names because narrowing the public compatibility surface
without first proving all downstream callers would violate the contract.

## Source-ownership inventory after P5.3i

| Owner | Responsibilities |
| --- | --- |
| `hub.py` | Compatibility exports; `serve_hub`, `serve_hub_worker` entry points |
| `hub_facade_helpers.py` | Stable ID, message serialization, Hub display identity helpers |
| `hub_registry_shared.py` / `hub_registry_json.py` | Shared Worker contract and legacy JSON registry |
| `hub_http_shared.py` / `hub_http_runtime.py` | Bounded HTTP mechanics, server and dispatcher |
| `hub_http_legacy_handler.py` / `hub_http_legacy_support.py` | Legacy compatibility HTTP routes and support |
| `hub_worker_http.py` | Legacy Worker-facing HTTP listener and routes |
| `hub_worker_client.py` | Legacy registration, heartbeat, poll, result client functions |
| `exp_fdb_hub.py` / `exp_fdb_hub_state.py` | FDB Dev-Test Hub route and state ownership |
| `stable_hub.py` and related files | Production Stable Hub; not changed by P5.3i |

The P5.3a route allowlist still decides which legacy HTTP requests the
Dev-Test Hub may inherit. The P5.2 Worker selected-network contract remains
unchanged. Production Stable Hub, SLH/SEH, and Mother are untouched.

## Windows acceptance

```powershell
python -m tools.hub_facade_verify
python -m tools.hub_worker_client_verify
python -m pytest -q `
  tests/test_hub_facade_p53i.py `
  tests/test_hub_worker_client_p53h.py `
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
  tests/test_hub_cli_live_session.py `
  tests/test_captain_smoke_engage_command.py
```

The verifier is read-only and performs no Hub request, Worker launch,
FoundationDB transaction, or payment. Test servers and transports are faked
where necessary. No redeployment or Windows boot changes are required.

## Next boundary: P5.4

Do a complete reachability and acceptance review before deleting any exports,
legacy Worker protocol, or Dev-Test compatibility route. A passing source
fingerprint proves that the implementation was not rewritten; it does **not**
prove a removed endpoint is unused or that live paid Worker execution works.
