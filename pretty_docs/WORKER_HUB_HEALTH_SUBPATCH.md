# Worker Hub Health — Stable Hub Identity Subpatch

## Scope

Fix only the Worker page's false `Hub unreachable (HTTP 404)` caused by probing `/api/hub/status` on a Stable Hub. Preserve the already-installed Worker registration-only subpatch. No funding, wallet, registration, worker execution, Hub deployment, or mutation code is changed.

The local viewport uses the read-only Stable Hub endpoint `/api/hub/v1/hub-identity`. The selected network and chain ID must match the Hub identity before the Worker page reports it reachable. Legacy `/api/hub/status` remains a compatibility fallback for old local/test hubs on HTTP 404, but not for selected mainnet/testnet.

## Apply

From `C:\Users\subsi\main_computer`, after applying the registration-only patch:

```powershell
python -m pytest -q tests/test_worker_hub_identity_health.py tests/test_worker_registration_subpatch.py tests/test_worker_app_layout_contract.py
python -m compileall -q main_computer/viewport_routes_energy.py
```

Restart the local viewport service, reload the Worker application with `Ctrl+Shift+R`, select Testnet, and click **Retry Hub**. The Active Hub Session should report Hub reachable/connected and identify the testnet Hub, not `HTTP 404`. `CONNECTED` on this panel proves a verified identity response, **not** an authenticated worker WebSocket or successful worker registration.

If the page still reports unreachable, inspect the local viewport `POST /api/applications/worker/network-session/select` or `/api/applications/worker/network-session` response and report its `session.connection_error` and `session.hub_status`, without exposing private keys.

## Safety and limits

The test code mocks all Hub HTTP calls; it moves no credits and registers no workers. The actual app check is a read-only identity request. Stable Hub registration API compatibility is a separate issue and is not corrected by this patch. No Hub redeployment is required.
