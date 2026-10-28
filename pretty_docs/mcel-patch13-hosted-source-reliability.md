# MCEL Patch 13 Stage 4 — Hosted Process Source Reliability Repair

## Why the previous hosted acceptance was INCOMPLETE

The user-hosted Frob reported valid offline DOM/selection/inspector continuity,
while its hosted Windows source contained zero processes and zero generation-qualified
rows. The hosted browser therefore had `no-row` and could not test retention.

The Windows producer uses a PowerShell `Get-CimInstance Win32_Process` query.
Previously it used the same 1.5-second deadline as lightweight network queries.
Timeouts, launch/command failure, malformed JSON, and empty stdout all became
`[]`. The semantic path could not distinguish an unsuccessful enumeration from
a genuinely empty selected subset. A separate local Task Manager snapshot returned
five process rows, so a reliable assertion about the hosted producer could not
be made from the zero-row result.

## Corrective scope

This repair does **not** promote the semantic consumer or affect operation endpoints.
It preserves the normal Task Manager API's existing behavior. Only explicit
`mcel_semantic_shadow: true` requests:

1. Use a **five-second bounded deadline** for the Windows CIM process query.
2. Add `_mcel_task_process_producer` to the served response, identifying the
   **actual producer path**, process count before filtering, matching/served
   counts, and execution status (`ok`, `timeout`, `command-error`,
   `launch-error`, `empty-output`, `invalid-json`, `invalid-output-shape`,
   or `empty-cim-result`). No raw process command lines are copied into the
   diagnostics.
3. Refuse the semantic shadow on a failed producer observation. An empty result
   from a failed command is never certified as an empty OS enumeration.
4. Issue generation evidence only from successful, source-identified
   observations. Missing rows continue to fail the hosted gate.
5. Report producer status and timing in the hosted Frob when certification is
   incomplete, instead of only `no-row`.

The existing Task Manager UI and controls retain ownership. A refused shadow
keeps last known semantic rows. An empty selected subset with a successful CIM
query remains valid *bounded* data, but cannot prove hosted DOM continuity.

## Install and verification

Apply the manifest-free patch through `python new_patch.py` and **restart the
Main Computer server** to load the changed Windows producer and route modules.
Then run:

```powershell
python -m pytest -q tests/test_mcel_patch13_hosted_source_reliability.py `
  tests/test_mcel_patch13_continuity.py `
  tests/test_mcel_task_manager_patch10_producer.py

python -m tools.mcel_patch13_continuity_frob `
  --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
```

Inspect `runtime/reports/mcel-patch13-continuity.json` under
`hosted.evidence.source` for `producerStatus`, `producerElapsedMs`,
`producerTimeoutSeconds`, `discoveredCount`, and `matchingCount`.

- `producerStatus: timeout` indicates the five-second read-only CIM deadline
  was exceeded. The test remains **INCOMPLETE**; there is no automatic pass.
- `producerStatus: ok` with `discoveredCount > 0` and `matchingCount: 0`
  indicates the selected Task Manager filter matched no processes. The test
  still requires actual rows; enabling `include_all` for diagnostic sampling is
  not equivalent to proving the ordinary bounded view works.
- `producerStatus: ok` with positive qualified rows permits the hosted DOM
  retention test to run. The test must observe the same node, focus, and
  inspector after a real second refresh to certify **lab-only** continuity.

## Acceptance evidence and limits

On the exported repository in the development container, 116 targeted tests
and eight additional subtests passed, including seven new failure-injection
checks and the existing Patch 10 producer fidelity test. The broader legacy
`test_task_manager.py` module could not collect because that export omitted
`game_projects/webgl-demo/game.json`. No hosted Windows server was available
in the development container: **hosted continuity is still unverified until
re-run on the real machine**. Production promotion remains disabled.
