# Worker Registration — Isolated Subpatch

## Scope

This patch fixes the first visible Worker page registration blocker without changing money handling, contract authority, capacity, settlement, or model execution.

1. `worker.js`: the `worker_pull_v0` offer no longer requires the `worker-node-id` and `worker-endpoint` controls removed from the page. It submits no fabricated callback URL or node ID. The page uses the backend-returned node ID in its successful registration message.
2. `worker.js`: **Finish Work now** sends a minimal action payload and does not rebuild a seller offer or require an active key. The server already checks that the request came from a local viewport client.
3. `viewport_routes_energy.py`: registration supplies a backend-configured default node identity if the page does not provide one. Callback execution modes still require an endpoint; worker-pull does not.
4. `hub.py`: the Hub worker registry accepts an endpointless `worker_pull_v0` registration, retains that record after reload, and still requires valid transport for callback workers.

## Verify locally — no network write or deposit

From the repository root:

```text
python -m pytest -q tests/test_worker_registration_subpatch.py tests/test_worker_app_layout_contract.py
node --check main_computer/web/applications/scripts/worker.js
```

The first command validates the browser-generated offer in Chromium (when installed), backend identity, callback restrictions, Hub registry persistence, Work-now Finish, and existing Worker layout constraints. A test will be marked SKIPPED if Chromium is not installed. Inspect that count rather than interpreting a skip as proof.

The test fixture isolates missing `game_projects/webgl-demo` assets in exported snapshots; no production game resources are changed.

## Manual follow-up

Open the Worker page, select **testnet**, enable **Accept paid jobs**, and inspect **Register / Update Offer**. It should no longer complain about missing node-id/endpoint fields. This operation can create a Hub registration and therefore is not part of the read-only local tests.

For live Hub registration, deploy the Hub registry change through the existing official Hub harness before exercising callbackless registration against that Hub. Do not use direct Coolify edits or SSH.

## Deliberately excluded

Network escrow funding fixes, wallet deposit recovery, capacity admission, model identity enforcement, settings durability, autonomous job recovery, and multi-network Fleet status. Those are not certified by this subpatch. No native credits should be transferred merely to verify it.
