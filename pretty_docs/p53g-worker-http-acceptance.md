# P5.3g — Legacy Worker-side HTTP ingress extraction

This patch moves only the legacy Worker-side HTTP listener and handler class
bodies from `main_computer/hub.py` into `main_computer/hub_worker_http.py`.
Both classes preserve their **exact P5.3f source** and remain importable from
`main_computer.hub`, with the same class identities. The legacy Worker HTTP
routes, ephemeral encrypted sessions and bounded resource handling are unchanged.

This Worker ingress is the historical Dev/Test HTTP protocol; it is **not**
the Stable Hub's authenticated Worker live-session transport. The following
remain in `hub.py` with source-identical function bodies: `register_worker_with_hub`,
`_post_hub_worker_json`, `_worker_pull_response_payload`,
`serve_hub_worker_pull`, and `serve_hub_worker`.

No changes to the Worker page, Chat Console, P5.2 selected network, Dev-Test
Hub legacy-route allowlist, FoundationDB, wallet/MSK authorization, credit
accounting, Stable Hub deployments, SLH/SEH, or Windows boot policy.

## Read-only verifier

```powershell
python -m tools.hub_worker_http_verify
python -m tools.hub_http_legacy_handler_verify
python -m tools.hub_http_shared_verify
```

The first verifier checks exact source fingerprints of the two moved classes,
the five retained Worker client functions, historical class and constant
identities, resource-limited bases, and absence of imports back into `hub.py`
or FDB/Stable Hub. No connection or payment is attempted.

## Focused Windows regression

```powershell
python -m pytest -q `
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
  tests/test_slh_seh_verify_battery.py
```

Expected Windows count: **254 passing** (250 passed and 4 Windows-only skips
in local Linux execution). The new P5.3g tests use bounded local loopback
sockets, never live Stable Hubs or paid requests.

## Financial-route inventory

```powershell
python -m pytest -q `
  tests/test_financial_authority_closure.py::test_every_hub_post_dispatch_has_a_documented_authority_policy `
  tests/test_financial_authority_closure.py::test_no_previous_deleted_financial_path_reentered_handler
```

The Worker-side POST dispatcher source owner in the financial test is updated
from `hub.py` to `hub_worker_http.py`, leaving its literal authority policy
and route cases unchanged.

## Remaining boundary

P5.3h can separately review the retained Worker registration and pull
client functions. Do not delete `main_computer/hub.py`: its public facade,
`serve_hub` entry point, and compatibility imports still have callers.
