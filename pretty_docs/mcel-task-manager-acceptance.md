# Canonical Task Manager — operational acceptance

The official semantic JavaScript source is `mcel_apps/task-manager/application.js`. Its `semantic-task-manager-generated-p3` app ID and opt-in route are unchanged. The legacy production Task Manager remains installed. The host-bound package is structurally valid but deliberately not certified for promotion while controller fidelity, complete live parity, and human review remain open.

## Permanent commands

```powershell
python -m tools.mcel_task_manager_acceptance bounded
python -m tools.mcel_task_manager_acceptance observe --host-url http://127.0.0.1:8765
python -m tools.mcel_task_manager_acceptance effects --host-url http://127.0.0.1:8765 --ack-isolated-effects --carry-report <observe-report.json>
python -m tools.mcel_task_manager_acceptance lifecycle --ack-isolated-effects
python -m tools.mcel_task_manager_acceptance parity --host-url http://127.0.0.1:8765 --ack-isolated-effects
python -m tools.mcel_task_manager_acceptance certify --review <review.json> --carry-report <report.json>
python -m tools.mcel_task_manager_acceptance diagnose
```

`bounded` produces a local prerequisite report. Other modes default to unique per-run directories under `runtime/reports/mcel-task-manager-acceptance/` and reject an existing `report.json`. Carrying evidence requires exact source and artifact hashes and host verification. None of these commands automatically promotes the application.

## Safety and evidence

`observe` is read-only. It verifies the actual hosted browser, five widths, real HTTP and independent PID creation time, with exact matching of the PID-filtered HTTP request. `effects` requires explicit acknowledgement and affects only child processes the harness created, checking parentage, PID and generation. `lifecycle` uses a separate ephemeral port and temporary control root to start, restart and stop a disposable HTTP service. Its internal Python executor verifies child PID, parentage, executable, nonce and health; the real TaskManagerService idempotency journal is exercised. **This does not establish the target-selection fidelity of the production PowerShell controller**; that requirement remains blocked until independently exercised against an owned Windows controller. No live production server lifecycle effects are authorized.

`parity` opens the incumbent and generated routes in real Playwright contexts and captures available read-only interactions. Missing or unexecuted UI workflows remain UNVERIFIED: process actions, lifecycle, scheduling, AI, receipts, and human usability cannot be promoted by source inventory, HTTP status alone or screenshots. All ten workflow categories must have matched UI/backend/OS evidence before parity PASS. `certify` does not execute effects or authorize promotion; an independently completed human review and separate reversible promotion decision are required.

## Repository hygiene

The earlier P3/P3.6 tools, duplicated test modules, obsolete instructions and fixture source have been consolidated into this stable package and the canonical application. Historic report schema labels are allowed as historical data, but are not accepted as current source authority. No compatibility wrappers or phase-specific entrypoints are retained. The production Task Manager stays available for rollback until independent readiness and explicit promotion.


## Workflow parity (permanent acceptance contract)

Use `python -m tools.mcel_task_manager_acceptance parity --host-url http://127.0.0.1:8765`
for **read-only** comparisons of the two actual hosted pages. The workflow report
records independent outcomes for monitoring parameters, four views, real manual
and timer-driven refresh, server/all-process inspection, and five-viewport
navigation. Incomplete scenarios remain BLOCKED, not PASS. It retains the
production incumbent and does not submit a schedule mutation, AI invocation,
termination, kill or server lifecycle action to 8765.

`--ack-isolated-effects` without a separately verified acceptance host is
refused, never interpreted as permission to mutate the production host.
The isolated Python lifecycle executor is valuable evidence of local service
transitions, but its report MUST retain controllerFidelity=UNVERIFIED until
Windows tests prove the real `control-main-computer.ps1` never selects foreign
PID-file entries, stale generations or foreign listeners. The reported
consequential UI scenarios are BLOCKED until the isolated authority-backed
browser host and independently observed OS/store effects are available.

The canonical semantic Task Manager now uses explicit `choose-all-process`
and `choose-schedule` local-state selections. Delete and run schedule actions
consume a selected ID, rather than requiring an operator to copy it manually.
These are semantic contracts, not authored layout. The shared selected-record
renderer can resolve identities from any collection bound to the same selection
state. The generated host fingerprint changes, so restart the normal viewport
host after installing this patch and rerun bounded and hosted verification.

Human design and production promotion remain separate gates. Do not treat
screenshots, an emitted receipt, or a passing direct-service lab as proof of
complete functional parity; keep the handcrafted Task Manager available.

## Refresh parity: request timing and paused state

The refresh comparison validates two manual HTTP overviews from each interface,
then verifies that neither application **initiates** a matching new overview
request during a full paused timer interval. A response completing after pause
may belong to a request sent earlier; its arrival alone is not a pause
violation. The runner subscribes to Playwright `request` events for its
PASS/FAIL decision, matches URL, HTTP method, query, limit and view, and retains
`pausedCompletedResponses` and `resumedCompletedResponses` only as timing
diagnostics. Any newly initiated matching request during pause remains a FAIL.
The test also requires correlated periodic requests from both interfaces after
resuming, or reports BLOCKED instead of inferring success. A confirmed legacy
pause defect is an incumbent finding; it must not be silently excused or copied
into MCEL's generated runtime.
