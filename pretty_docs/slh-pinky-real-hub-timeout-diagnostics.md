# SLH/SEH real-Hub Pinky Promise timeout diagnostics

This document describes an **instrumentation-only** change. It does not increase credit, change the 0.1-credit assessment, retry paid execution, alter FoundationDB transaction settings, or mark a failed laboratory run as accepted.

## Reproduce

From the Main Computer repository root on Windows, with the smoke FoundationDB infrastructure available:

```powershell
python -m tools.slh_seh_lab.run_lab run --pinky-integrated --json
```

The command creates a new directory under `runtime/reports/slh-seh-lab/`. For a failed run, `report.json` now also includes `paid_cycle_trace_path`, `last_paid_cycle_event`, and the Hub log file paths when available.

## Read evidence

Replace the directory with the `report_path` from the last output:

```powershell
$dir = '.\runtime\reports\slh-seh-lab\<RUN_ID>'
Get-Content "$dir\paid-cycle-diagnostic.jsonl" | ForEach-Object { $_ | ConvertFrom-Json } | Format-Table timestamp,request_id,stage,status,duration_ms,error_type
Select-String -Path "$dir\smoke-hub3.log" -Pattern 'work_request_diagnostic|stable-hub-fdb-diagnostic|worker_selection_|worker live-session ended|Exception occurred' | Select-Object -Last 100
```

The paid-cycle journal is append-only and flushes each stage in the actual on-disk report directory. It records before/after the paid submission, receipt polling, replay, claim, settlement, and preliminary Hub and funding operations. A failed stage records the Python exception class and duration. The client-side timed-out request can remain **uncertain**: do not automatically retry with a new request ID or declare a failed payment solely because the HTTP response was lost.

For disposable Hub processes launched by `run --pinky-integrated`, both `MAIN_COMPUTER_STABLE_HUB_TRACE_WORK_REQUESTS=1` and `MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS=1` are automatically set in the **child** environment. The server emits `work_request_diagnostic` phase markers with request/session IDs, monotonic elapsed time and phase duration. Slow (250 ms or more) and failed FoundationDB `atomic`, `load`, `read_meta`, and `read_chunks` operations emit `stable-hub-fdb-diagnostic` with thread ID, duration and error code (such as 1031). No authorization values, request bodies, prompts, responses, wallet signing keys, raw FDB keys, or store documents are logged.

When `--use-existing-hubs` is selected, the harness **does not** change those processes' environment. Server-side diagnostics are opt-in at Hub process startup; the client-side timeline still works. Both environment switches default to off in production.

## Interpretation

Compare the matching `slh-paid-*` request ID in the paid-cycle journal and `smoke-hub3.log`. A last `market_select.begin` with no matching `end` indicates blocking inside selection. A long `idempotency_lookup`, `owner_lookup`, `capacity_reserve`, or `durable_accept` points toward the shared FDB document. A `worker_offer.begin` without an `end` indicates that the Hub was waiting for the Worker's authorization-acceptance frame. FDB `1031` diagnostics identify which store operation timed out; they do **not** establish the underlying reason for FDB transaction delay.

This instrumentation does not itself satisfy real three-Hub economic acceptance. The existing fail-closed Pinky Promise gate remains mandatory.

## November 1 diagnostic finding and isolated rerun

The recorded `smoke-hub3.log` shows 128-chunk reads of one shared FDB worker-session document, repeated 375–1,156 ms chunk reads, and an 11,406 ms atomic operation during `slh-paid-888d3f89f4ab76410e17630d`. Its preceding paid-cycle trace had already confirmed `worker_earning_settle` for a *different* paid request. The additional timed-out request originated from the legacy `_local_demo()` run after the integrated Pinky workflow, not from a proven failed Pinky sweep.

`run --pinky-integrated` now skips this redundant second paid demo and writes `pinky-integrated-checkpoint.json` with an independently inspectable result. Ordinary `run` still executes the legacy demonstration. This is a **lab isolation and reporting correction**, not a FoundationDB performance repair: the shared document's growth and repeated whole-document FDB reads remain a separately measurable architectural bottleneck. The real smoke/FDB integrated run must still pass to establish acceptance.

## Pre-Hub TLS/SEH handshake and lifecycle instrumentation

A run can fail **before any Hub subprocess exists**, because the real SLH/SEH
service lifecycle preflight runs first. In that case `hub_log_paths: []` is
expected and says nothing about Hub health. The earlier `paid-cycle-diagnostic`
only begins after Hub setup; it cannot diagnose these preflight failures.

For `run --pinky-integrated`, `lab-stage-diagnostic.jsonl` is now written in
the same report folder, beginning before the first disposable service preflight.
The runner flushes one `begin` event before each potentially blocking operation
and an `ok` or `error` event afterward, with elapsed milliseconds. Stages include:
service lifecycle, worker WebSocket handshake (direct or SEH and loopback port),
TLS pin verification, device authorization, heartbeat, scheduler preflight,
Hub child startup/readiness, FDB durability and ledger opening, actual real-process
paid work, aggregate Reserve sweep, and eventual cleanup.

On failure `report.json` exposes `lab_stage_trace_path`,
`lab_stage_events_recorded`, `first_failed_lab_stage`,
`unfinished_lab_stage`, `failure_before_hub_launch`,
`lab_runtime_log_path`, and a source-location-only `failure_stack`.
No secrets, HTTP bodies, authentication proofs, worker prompts, or private keys
are recorded. A missing `ok` does not establish whether remote work committed;
retain the prior no-blind-retry policy for paid requests.

To inspect a failing run:

```powershell
$dir = Split-Path '<REPORT_PATH_FROM_OUTPUT>'
Get-Content "$dir\lab-stage-diagnostic.jsonl" | ConvertFrom-Json |
  Select-Object -Last 30 timestamp,stage,state,elapsed_ms,transport,endpoint_port,error_type
Get-Content "$dir\lab-runtime.log" -Tail 60
```

Do not interpret a handshake failure before `cluster.child_spawn` as a three-Hub
FoundationDB failure. This patch is diagnostic-only; it does not change any
handshake timeouts, public Worker selection, Pinky obligations or payments.
