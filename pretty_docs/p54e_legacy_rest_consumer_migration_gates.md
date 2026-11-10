# P5.4e — Legacy Worker REST Consumer Census and Migration Gates

## Decision

**No deletion, migration, or operational compatibility change is authorized.**
P5.4d made the *Captain/Data* fallback explicit; it did not retire direct
registration callers, legacy Hub HTTP routes, the local Worker HTTP server,
or older operator and laboratory clients.

The bounded read-only audit (`python -m tools.hub_legacy_rest_consumer_gate`)
checks the in-repository source references and requires known classifications.
It does not contact a Hub, deploy anything, spawn a Worker, mutate data, or
certify that external consumers are absent. It does not recurse through runtime,
patching, installed packages, or exported-out source trees.

## Direct consumers and their distinct obligations

| Consumer | Current evidence | Retirement gate |
|---|---|---|
| Worker page's legacy offer registration | `viewport_routes_energy.py` defines `_handle_worker_offer_register`, accepts `body["hub_url"]`, and calls `_post_worker_registration_to_hub` on `/api/hub/v1/workers/register`; `viewport_route_dispatch.py` exposes `/api/applications/worker/register-offer` | Determine operational callers, bind to authoritative selected network or explicitly scope to a compatibility mode, and prove real Worker registration parity |
| Worker page's **Work now** registration | Separate handler checks `selectedNetwork`, checks the selected profile's Hub URL, and uses a different registration pathway | Preserve existing network checks; do not treat the offer-registration finding as proof the Work now path is defective |
| Hub admin page | HTML registration form posts to `/api/hub/v1/workers/register` | Retain or replace admin registration with an independently accepted administrative workflow |
| `hub-register-worker` CLI | `cli.py` calls `register_worker_with_hub` | Preserve until explicit CLI replacement and registration/accounting parity |
| `hub-worker` CLI / Docker dev profile | Local HTTP Worker server + published port in `docker-compose.dev.yml` | Maintain old HTTP/encrypted-session acceptance or approve an explicit replacement |
| Captain/Data Worker pull | Live sessions for recognized Stable/Dev-Test; `--allow-legacy-worker-rest` required for unidentified compatible service after successful identity probe | Retain opt-in until legacy compatibility deliberately retired; NanoJev remains live-only |
| Scheduler lab and phase/bridge smoke tests | Register, heartbeat, poll, and result REST calls remain in `tools/scheduler_lab/hub_client.py` and several smoke scripts | Migrate oracle semantics, including lease, pricing, accounting and failure/recovery behavior, **before** changing implementation |
| Temporal and chaos laboratories | Source calls to old REST routes (some are negative probes of Dev-Test admission) | Distinguish deliberate denial tests from working legacy-server consumers and preserve their assertions |
| Legacy Hub HTTP handler | Implements REST registration/heartbeat/poll/results | Retain until all supported clients migrated and no external consumers verified |
| FDB Dev-Test Hub | Explicitly rejects legacy REST Worker endpoints | Preserve rejection and authenticated live sessions; denial is not evidence all other clients are gone |

## Security and routing observation

The Worker-page **offer registration** handler is an outstanding review item: it
accepts a Hub URL from the request body and passes it to legacy registration.
The separate **Work now** handler already validates its destination against
the selected network. The P5.4e audit does **not** change either path, and the
mere presence of the offer-registration route does not establish that the
current UI invokes it. Before changing behavior, trace JS/UI callers and
perform a bounded network-selection acceptance test. No cross-network
registration should occur through an unintended fallback.

The optional Docker `hub-worker` profile publishes port 8771 with a default
wildcard host bind. If that profile is used, evaluate the intended exposure
separately; this census does not change deployment networking.

## Completion requirements before *any* deletion

1. Inventory and explicitly decide each remaining CLI, admin, Viewport, Docker,
   scheduler, chaos, temporal, phase and bridge caller.
2. Build and pass equivalent replacements or obtain explicit retirement
   authorization for abandoned workflows, preserving credit and lease semantics.
3. Check external clients and existing deployments rather than inferring absence
   from repository source alone.
4. Verify Dev-Test and Stable authenticated live-session protocols remain intact,
   and re-run P5.2/P5.3 and payment-security acceptance.
5. Approve concrete deleted symbols/endpoints individually. The read-only gate
   cannot approve deletion by itself.

## Windows verification

```powershell
python -m tools.hub_legacy_rest_consumer_gate
python -m pytest -q tests/test_hub_legacy_rest_consumers_p54e.py
python -m tools.hub_worker_transport_policy_verify
python -m tools.hub_worker_retirement_decision
```

A successful P5.4e run means the *known source references are classified*, not
that the legacy Worker REST interface has been retired.
