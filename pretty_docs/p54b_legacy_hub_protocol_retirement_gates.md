# P5.4b — Legacy Hub Protocol Retirement Gates (read-only)

**Decision: no deletions approved.** This report classifies the remaining Hub surfaces by their actual in-repository interfaces, not by age or file name. P5.4a source scanning alone cannot establish that any protocol is unused by external clients or deployment tooling.

## Evidence basis

Source reconstruction: `main_computer_test-20261108-165657.zip`, followed by P5.3a–P5.3i and P5.4a scanner correction. The user's current Windows inventory reported 3,026 scanned files, zero parse failures and 16 historically imported Hub symbols. The reconstructed snapshot is not guaranteed to include every unshared local change in the Windows checkout.

The new verifier inspects exact source anchors in nine bounded surfaces; it does **not** walk the repository, connect to a Hub, launch a Worker, mutate FoundationDB or use paid compute. It does not claim to have tested external callers or live service traffic.

## Interface decisions

| Interface | Current in-tree evidence | P5.4b disposition |
|---|---|---|
| Old `hub` CLI | Explicit dev/test network validation and `serve_hub` call | Retain until the compatibility command has an approved replacement or retirement decision |
| `dev-test-hub` CLI | Explicit local Dev-Test entry point and FDB handler | Protected, retained |
| `hub-worker`, `hub-register-worker`, pull-client | Present in `cli.py`, `hub_worker_client.py`; command and harness use remain | Retain until compatible commands and harnesses are migrated |
| Legacy Worker REST register/heartbeat/poll/results | Endpoints implemented in `hub_http_legacy_handler.py`; FDB Dev-Test handler explicitly rejects them | Blocked on Dev-Test but still implemented on the old Hub; **not approved for deletion** |
| Live Worker sessions | Present on Dev-Test and Stable Hub live-session routes | Protected operational contract, retained |
| Dev-Test encrypted chat session start/chat | HubProvider calls these endpoints and Dev-Test allowlist explicitly permits them | Retain pending provider replacement and regression acceptance |
| Dev-Test `/remote-overflow/safe-chat` | Permitted by Dev-Test allowlist and backed by provider simulation | Retain as explicitly labeled simulation; not paid-worker execution |
| MSK, wallet funding, authorization | Dev-Test route allowance and Stable Hub source references | Protected financial and authorization contracts |
| Historical `hub.py` facade | CLI and live-session code still import it; two server entry points remain | Retain compatibility imports pending explicit consumer migration |

## Critical distinctions

1. **Blocked on the FDB Dev-Test Hub does not mean removable from the legacy Hub.** It still implements legacy Worker REST, with in-repository CLI and smoke/lab consumers. Some external users may also depend on the endpoints.
2. **Real Worker live sessions are a different protocol.** The Dev-Test and Stable Hub endpoints are not substitutes for every legacy test or CLI behavior until consumers are explicitly migrated.
3. **Dev-Test safe-chat is simulation.** It cannot be counted as proof of a real paid Worker request, and deleting it would break existing compatibility behavior.
4. **Imports and literal route strings are conservative evidence.** They are useful for identifying consumers and acceptance obligations, but do not demonstrate live traffic or absence of other clients.

## Running the new gate

```powershell
python -m tools.hub_legacy_protocol_gate
python -m pytest -q tests/test_hub_legacy_protocol_gate_p54b.py
```

The JSON report includes a classification and retirement prerequisite for each surface. `removal_approved` remains empty; missing evidence causes a failed result rather than relaxing a deletion decision.

## Next gate: explicit retirement policy

P5.4c should begin with the standalone legacy **Worker REST + `hub-worker` / `hub-register-worker` / pull-client** lifecycle. Enumerate exact test, CLI, lab, Docker and external consumers and decide which require migration and which are intentionally retained. Only after replacement paths are proven should a separate code-removal patch be drafted. The Dev-Test Hub, public Stable Hub, financial/MSK contracts, shared Worker settings and Chat Console remain out of scope.

If the older interfaces still serve supported operator or development use, retaining the now-separated modules is a valid outcome.
