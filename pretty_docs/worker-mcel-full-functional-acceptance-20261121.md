# Native MCEL Worker — Full Functional Acceptance

This test runner follows the SLH *per-declared-intent* inventory/isolated acceptance model and the Task Manager *one subprocess, one timeout, one report* model. It tests the **native MCEL Worker candidate**, not the legacy `/applications/worker` page.

## Commands

From repository root:

```powershell
python -m tools.mcel_worker_full_functional_acceptance --mode inventory
python -m tools.mcel_worker_full_functional_acceptance --mode isolated --profile smoke --timeout 120
python -m tools.mcel_worker_full_functional_acceptance --mode isolated --profile full --timeout 120
```

To re-run a single check:

```powershell
python -m tools.mcel_worker_full_functional_acceptance --mode isolated --case marketplace-action-ui
```

A default report is written automatically to `runtime/reports/mcel-worker-full-functional/report.json`. Reports are regenerated for each run. `--resume` only reuses a passing check when the original source/test fingerprint is unchanged. `--max-cases` supports checkpointed runs without disguising an incomplete set as a pass.

## Coverage model

The compiled `mcel_apps/worker/application.js` is the authority for the inventory of declared intents, including future additions. Every behavioral case references a specific existing or newly added pytest assertion. A passing static contract does **not** count as execution evidence. Consequential and read-only operations require browser/UI and adapter evidence; locally selected navigation requires browser evidence. Unmapped operations remain `NOT_TESTED`.

The suite checks Hub radio selection and rollback, identity discovery and recovery, separation of selection from enrollment, Theme/Form/Chrome, wallet/MSK integration and mismatch guards, seller/Requester settings, existing signed registration bridge, Work Now and Finish, Pause Selling, explicit Hub retry/disconnect, and backend observation boundaries.

The two functional lanes are:

- `adapter`: the actual typed MCEL Worker operation and existing backend/signer adapter, using disposable or stubbed transport
- `ui`: actual generated native Worker page in Chromium, connected to a disposable backend and signer stub

`contract` and `isolated-effect` tests are also retained in the report but **cannot substitute for UI or adapter evidence**.

No production Hub, live MetaMask wallet, real paid registration, real job, or spending mutation is made by this runner. A mocked browser pass means that the UI/adapter path works under its fixture, not that the deployed marketplace has accepted the result.

## Cutover

The report's `cutover.status` remains `BLOCKED` even after an entirely green isolated run. The four additional live acceptance gates must be recorded separately using a disposable wallet and an explicitly authorized test Hub:

1. Real MetaMask connection, MSK request/revocation, signature/authorization.
2. Signed offer registration verified against the Hub's accepted state.
3. Work Now accepting/executing a real job, countdown and Finish/drain.
4. Actual requester authorization/budget enforcement and refusal of disallowed spending.

Do not treat the legacy browser acceptance program as native acceptance merely because the old code remains available: its route and DOM selection still target the legacy Worker. The canonical Worker route is **not** changed by this patch.

## Snapshot limitation

Some existing Worker route tests import `main_computer.viewport_pages`, which requires `game_projects/webgl-demo/game.json`. Several exported repository snapshots omit the `game_projects` assets. These specific tests fail during collection in such snapshots, without exercising the Worker. The runner does not silently fabricate those assets, skip affected tests, or label them successful. Run against a complete checkout or interpret this as a test-environment blocker.

A newly added Chromium retry/disconnect test intentionally checks that the **displayed active Worker session** reconciles after disconnect, independently of the persisted Hub Choice. Failure means that the underlying network request may succeed while the native on-screen state is stale. This should be fixed before cutover rather than changing the test to inspect only the backend.

## Isolated coverage closure (November 21)

The five previously untested intents now have explicit execution evidence in
`tests/test_mcel_worker_remaining_functional_coverage.py`:

- `pause-selling-adapter` verifies that the existing port disables only seller/rental
  availability, preserves wallet/Requester/Hub settings, and refuses a missing
  saved-state postcondition.
- `retry-hub-adapter` verifies the selected Hub and requested ring, the verified
  destination result, and refusals for SLH, backend rejection, and an unfulfilled
  retry postcondition.
- `refresh-policy-ui` and `refresh-runtime-ui` click the generated MCEL buttons
  in Chromium, observe changed state, and assert no settings POST occurred.
- `save-policy-ui` changes the generated policy form, clicks Apply, verifies
  the precise changed-fields set and resulting state, and checks that Hub and
  Requester configuration are not overwritten.

The full isolated suite now contains 29 cases for 25 declared intents. **A
complete isolated run is still not live cutover certification.** All four
live acceptance gates above remain independent and are not auto-promoted.
