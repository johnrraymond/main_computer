# P5.3f — Legacy HTTP route-handler ownership

## Scope

`HubServerHandler` has been moved, source-identically, from `main_computer/hub.py`
to `main_computer/hub_http_legacy_handler.py`. Its wallet/MSK authorization,
Dev-Test safe-chat simulation, Worker legacy REST, and other route logic were
**not** modified. Selected legacy support declarations (exception identities,
local-chain constants, manifest loader, and message parser) now have a small
standalone owner at `main_computer/hub_http_legacy_support.py`.

The `main_computer.hub` compatibility facade re-exports the original class
identity. The Dev-Test Hub imports its base handler directly and remains
restricted by the P5.3a inherited-route allowlist. The extracted HTTP server
runtime injects this handler by default; Dev-Test still explicitly supplies
its own subclass.

The legacy Worker-side HTTP handler and server, legacy worker-pull commands,
all storage and FoundationDB behavior, and Stable Hub/SLH/SEH deployments are
unchanged. No production Hub or payment operation is part of verification.

## Read-only integrity verifiers

```powershell
python -m tools.hub_http_legacy_handler_verify
python -m tools.hub_http_runtime_verify
python -m tools.hub_http_shared_verify
python -m tools.dev_test_hub_inheritance_verify
```

The first command asserts exact source fingerprint of the original P5.3e
2,528-line class, compatibility identities, MSK method presence, single class
ownership, and no legacy facade imports in the Dev-Test handler.

## Regression checks

```powershell
python -m pytest -q `
  tests/test_hub_http_legacy_handler_p53f.py `
  tests/test_financial_authority_closure.py `
  tests/test_hub_http_runtime_p53e.py `
  tests/test_hub_http_shared_p53d.py `
  tests/test_dev_test_hub_p53_legacy_boundary.py `
  tests/test_hub_interface_p52.py `
  tests/test_chat_console_remote_worker_modal_contract.py
```

The financial-authority test now reads the real class source location so its
route authorization inventory still fails if an unknown POST route is added.
Some pre-existing unrelated legacy integration tests are outside this extraction
and do not establish production paid execution acceptance.
