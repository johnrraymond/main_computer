# P5.4c — Legacy Worker client retirement decision

**Decision: retain the old Worker REST protocol and its CLI commands for now.**

P5.4a established that legacy interfaces still have source-level callers. P5.4b
classified the surfaces. This narrower P5.4c audit identifies precisely which
Worker commands and transport branches depend on them. It neither approves a
deletion nor alters the runtime.

## Worker interfaces are not equivalent

| Surface | Actual behavior | Disposition |
|---|---|---|
| `hub-worker` CLI | Starts the separate local HTTP Worker server and can register it with a legacy Hub | Retain, including Docker dev profile |
| `hub-register-worker` CLI | Explicit REST registration of a running Worker endpoint | Retain for current operator/smoke compatibility |
| Captain/Data `serve_hub_worker_pull` | Probes Hub identity; authenticated live session on recognized Stable/Dev-Test service, otherwise older REST registration + heartbeat/poll/results | Retain both branches pending migration decision |
| Old Hub's `/api/hub/v1/workers/*` REST routes | Legacy registry's register, heartbeat, poll, results implementation | Retain for legacy consumers; Dev-Test FDB Hub continues to reject them |
| Worker page and Worker supervisor | Separate modern selected-network and live-session orchestration | Protected; no changes |

## Critical selector behavior found

`main_computer.hub_cli_live_session.hub_uses_live_sessions()` retrieves a Hub
identity and returns `True` for the advertised `main_computer.exp_fdb_hub` and
`main_computer.stable_hub` services. `serve_hub_worker_pull` then uses the
authenticated WebSocket transport, not REST registration.

An identity *request failure* raises before registration. However, an identity
response advertising an unrecognized service currently returns `False`, and a
normal non-NanoJev Worker takes the **legacy REST branch** to that same URL.
NanoJev explicitly rejects that branch. These are current observed behaviors,
not proof that an unrecognized identity is an authorized legacy Hub.

This matters for subsequent policy: if legacy REST is retired, a deliberate
explicit opt-in for legacy transports may be preferable to implicit selection.
Such a behavior change must be proposed and tested separately, including
supported operator workflows and the actual legacy Hub identity response.

## Evidence and retirement prerequisites

The in-tree evidence includes CLI entry points, `hub_worker_client.py`,
`hub_cli_live_session.py`, `hub_worker_http.py`, `hub_http_legacy_handler.py`,
`exp_fdb_hub.py`, and `docker-compose.dev.yml`. External clients and deployments
have **not** been inspected or certified absent.

Before any REST or CLI deletion: inventory real deployment and external
consumers; decide whether legacy Hub support remains a product requirement;
replace all CLI, Docker, lab and smoke workflows being retired; verify both
Dev-Test and Stable Hub live-session admission, authorization, execution and
settlement separately; preserve the Worker page's selected-network policy.

## Bounded verification

```powershell
python -m tools.hub_worker_retirement_decision
python -m pytest -q tests/test_hub_worker_retirement_decision_p54c.py
```

The verifier checks specific source files, not the whole repository tree. Tests
use fake identity services and stop before network/registration; they cannot
create Workers or paid requests. There are no production-code modifications.

**Next decision:** retain the current legacy Hub REST compatibility indefinitely,
or authorize a *separate* opt-in legacy-transport migration with backward
compatibility tests. No functional removal is warranted by P5.4c alone.
