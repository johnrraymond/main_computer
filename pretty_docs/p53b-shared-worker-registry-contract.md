# P5.3b — Shared Worker registry contract extraction

## Scope and causal purpose

P5.3a blocked undeclared inherited legacy HTTP routes. P5.3b extracts
**shared Worker registry models and pure helpers** from the monolithic
`main_computer.hub` into `main_computer.hub_registry_shared`, establishing
one owned implementation used by the legacy JSON registry and the Dev-Test
FoundationDB registry.

Moved **verbatim** (without economic or transport policy changes):

- Worker/upstream dataclasses (`HubWorker`, `HubUpstream`), Worker slot/lease
  constants, worker-pull capability interpretation, Worker identity cleanup;
- fixed-call and token-based credit/pricing normalization, price representation
  in integer wei and displayed credits;
- Phase 9 offer construction and deterministic offer identifiers;
- HTTPS/loopback Worker endpoint transport validation.

`main_computer.hub` re-exports all moved names. Existing callers retain their
old imports and `HubRegistry` class. The FDB state adapter imports models and
pure helpers directly from the shared module, but **still subclasses
`HubRegistry` in `main_computer.hub`**. The legacy registry and the FDB
registry still own their original storage implementations and admission/
accounting mutations. There is **no database migration**. Existing on-disk
JSON and FDB data formats are not changed.

The new shared module imports no Hub HTTP handler, Hub registry storage,
FoundationDB native client, Stable Hub server, or Worker supervisor. HTTP
resource limits already live in `main_computer.hub_resource_limits`; those
remain untouched. Inherited HTTP server classes and the legacy registry
implementation are **not yet extracted**, and must not be deleted.

## Local acceptance commands (no payments or Hub starts)

```powershell
python -m tools.hub_registry_shared_verify
python -m pytest -q `
  tests/test_hub_registry_shared_p53b.py `
  tests/test_dev_test_hub_p53_legacy_boundary.py `
  tests/test_hub_interface_p52.py `
  tests/test_dev_test_hub_p3_isolation.py `
  tests/test_chat_console_remote_worker_modal_contract.py `
  tests/test_startup_hub_flags_independent.py `
  tests/test_worker_hub_identity_health.py `
  tests/test_slh_seh_verify_battery.py
```

The first check only compares Python class/helper identity and inheritance.
The second suite checks the historical pricing/offer serialization vectors,
transport validation, Worker registry compatibility, P5.3a HTTP boundaries,
P5.2 routing, and existing Dev-Test/SLH-SEH test contracts.

On Linux, this selection passed **202 tests and skipped 4 Windows-specific
checks**. Run the same selection on Windows to verify those four.

Some broader old tests fail on the untouched source as well (for example,
missing local bridge signer/deployment and old Worker ID assertions). These
must be handled independently; do not change the Hub's authorization or
accounting behavior to satisfy stale test expectations.

## Explicit deferrals

The next distinct extraction gate is the implementation of `HubRegistry`
(which today manages legacy JSON storage and supplies inherited methods
used by the FDB registry) and the `HubHttpServer`/`HubServerHandler` base
classes. They require protocol and state compatibility proof. Removing or
rewriting them now would be unsafe. P5.3b makes **no changes** to Worker-page
routing, Chat Console/plugged-in Console settings, MSK authorization, Stable
Hub production protocols, SLH/SEH startup, Mother identity, Windows boot
policy, or FoundationDB accounting.
