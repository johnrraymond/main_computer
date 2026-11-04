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

The refresh comparison first disables both UI-owned automatic timers,
then validates two manual HTTP overviews from each interface. It waits for the
legacy two-stage manual refresh and any coalesced replay to drain before opening
a clean paused observation window. An undrained manual request returns BLOCKED,
not an inferred timer failure. Only then does it verify that neither application
**initiates** a matching new overview request during a full paused interval.
A response completing after pause
may belong to a request sent earlier; its arrival alone is not a pause
violation. The runner subscribes to Playwright `request` events for its
PASS/FAIL decision, matches URL, HTTP method, query, limit and view, and retains
`pausedCompletedResponses` and `resumedCompletedResponses` only as timing
diagnostics. Any newly initiated matching request during pause remains a FAIL.
The test also requires correlated periodic requests from both interfaces after
resuming, or reports BLOCKED instead of inferring success. A confirmed legacy
pause defect is an incumbent finding; it must not be silently excused or copied
into MCEL's generated runtime.


## Read-only scenario isolation and generated responsive acceptance (November 2026)

The hosted parity runner now opens a fresh pair of actual pages for each of
`refresh`, `inspection`, and `responsive`, closing the previous pair. The
first two read-only workflows still compare parameters and notebook modes.
The responsive report records `generatedAcceptance` and `legacyComparison`
separately at widths 320, 390, 560, 1100, and 1272. A legacy pointer failure
can leave `legacyComparison=BLOCKED` without falsely failing a generated
interface whose real pointer navigation, focus, visibility, selection, and
horizontal overflow all pass. In that case `responsive=PASS` applies **only
to generated functional acceptance**; `comparisonComplete=false` makes clear
that pairwise comparison did not pass. A generated obstruction is never a PASS.

The responsive report identifies the failed tab and locator before measuring
its hit target. Inspection pauses both UI-controlled monitoring timers and
selects stable PID-plus-creation-time keys; the all-process candidate must
also match a real HTTP producer row. The chosen identity cannot change during
a retry, and visible selected details must include the same PID and creation
time. The old automatic refresh settings are restored in a `finally` block.

Refresh remains gated on actual correlated HTTP request initiation, never on
an inferred timer tick. A temporary in-page observer preserves the original
refresh function's arguments, receiver, and returned value, records invocation
counts alongside the legacy timer's armed/in-flight/queued state, and restores
the original function. When a timer invocation is coalesced behind an
outstanding request, the runner may allow one additional foreground window;
without observed HTTP activity the outcome remains BLOCKED, with explicit
`legacyResumeClassification` diagnostics. These read-only observations grant
no consequential permissions and do not replace separate production promotion.

## Interface-replacement operational integration (bounded, no external lab)

Run `python -m tools.mcel_task_manager_acceptance integration` to execute five
real generated-browser-to-TaskManagerService contract scenarios, without touching
production state or Windows services. The campaign verifies the generated semantic
source and generic runtime, the real read/capability port, actual Task Manager HTTP
route methods, local CSRF authority, process generations, durable schedule storage,
receipts, operator confirmation, AI prompt/result contracts, and rejection paths.
The HTTP service is ephemeral on a random loopback port with temporary state.
The OS process interface only admits a synthetic test-owned PID, the Windows server
executor is explicitly injected into `TaskManagerService` with a private control
root, and the AI-provider boundary returns a deterministic test response. A
Playwright transport bridge forwards request bodies through real HTTP when CI
Chromium forbids loopback networking; it never forges backend JSON replies.

`PASS_BOUNDED_INTEGRATION` is evidence of *generated interface-to-backend contract
compatibility*, not direct proof of Windows controller target selection, a real
provider invocation, unrestricted OS effects, live UI pairwise consequential
parity, human usability, or production readiness. The five legacy `parity` rows
remain BLOCKED until those stronger criteria are independently satisfied or a
separate interface replacement decision explicitly accepts the shared backend
limits. This command cannot authorize production promotion. All five read-only
workflows retain their independent passing Windows evidence.
