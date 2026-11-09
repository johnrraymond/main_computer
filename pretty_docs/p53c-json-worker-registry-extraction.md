# P5.3c — Extract the legacy JSON Worker registry implementation

## Change and dependency boundary

The Worker/upstream `HubRegistry` implementation and its timestamp helper have
moved **without changing their bodies** from `main_computer/hub.py` to
`main_computer/hub_registry_json.py`. The old module re-exports both names, so
legacy consumers importing `main_computer.hub.HubRegistry` see **exactly the
same class object**. The Dev-Test `ExperimentalFoundationDbRegistry` now imports
its base class from the extracted module, no longer importing the entire legacy
Hub HTTP server just to inherit registry normalization and Worker semantics.

The extracted module imports the P5.3b Worker/offer/pricing contract, credit
precision normalizer, security-profile constant and standard-library JSON,
clock, threading and hashing helpers. It imports **no** legacy Hub HTTP server,
FoundationDB client, Stable Hub implementation, Worker supervisor, or paid
requester implementation. The P5.3c test/verifier compare the extracted class source
against a cross-Python-version-stable fingerprint of the P5.3b original; there were no Worker admission,
pricing, storage format, lease, stale detection, audit or mutation changes.

There is **no database or JSON migration**. The JSON registry still uses
`hub_workers.json`; the Dev-Test subclass still owns the same FoundationDB keys,
registry adapter, and accounting. The Hub HTTP server class hierarchy in
`hub.py` and `exp_fdb_hub.py` has not been changed. P5.3a's explicit inherited
HTTP route allowlist and P5.2's shared Worker-selected network remain intact.

## Read-only proof

```powershell
python -m tools.hub_registry_json_verify
```

No network, live Hub probe, account mutation, or FoundationDB transaction occurs.

## Acceptance commands

```powershell
python -m pytest -q `
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

Observed on Linux: 210 passed, four Windows-only tests skipped. The four
Windows startup tests should execute on Windows.

Additional optional legacy-registry and FDB-adapter regression checks:

```powershell
python -m pytest -q `
  tests/test_exp_fdb_hub_shared_state.py `
  tests/test_exp_fdb_hub_manual_entrypoint.py `
  tests/test_worker_registration_subpatch.py `
  -k 'not test_legacy_hub_payout_http_routes_and_smoke_callers_are_absent and not test_bridge_audit_records_work_and_payout_failure_recovery_events'
```

The two omitted tests require the text `Retired: legacy Hub payout HTTP routes
were deleted` inside a smoke script. The referenced smoke script is
byte-for-byte identical to the unmodified P5.3a baseline; neither failure is
caused by P5.3c. Their maintenance belongs in a separate task.

## Outside scope / next gate

No Worker page, Chat Console, request routing, MSK authorization, credits,
ledger, FoundationDB writes, stable mainnet/testnet protocol, SLH/SEH startup,
Mother state, Windows boot setup or deployment was changed. The remaining
legacy HTTP server and handler inheritance must be handled by a separate
P5.3d boundary and independently tested before any deletion of `hub.py`.

## Windows verification correction

The original test and read-only verifier hashed `ast.dump()` of the registry
class. Python 3.12 and 3.13 can produce different AST serializations for the
same source. The verification now hashes the **exact class source segment**
from the P5.3b baseline. This does not alter registry implementation or
storage behavior.

The P5.3a Dev-Test HTTP boundary also now drains **at most 4 KiB** of an
undeclared POST request body, with a one-second deadline, before emitting
404. A small unconsumed HTTP/1.1 body could cause Windows to abort the TCP
connection before the client received the 404. Invalid JSON is not parsed,
processed or accepted. Oversized/malformed bodies are not drained and the
connection is marked for closure; the route remains forbidden. No live Hub
requests, FoundationDB writes, or credit operations are involved in tests.
