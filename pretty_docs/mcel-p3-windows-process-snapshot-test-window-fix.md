# MCEL P3 / P2 Windows bounded process snapshot test correction

## Cause

The P2 authentic-source browser test assumed the local pytest process ID must occur in
`TaskManagerService.snapshot(include_all=True, limit=100).processes`. This assertion is
invalid: the service caps the result at 100 rows and sorts Win32 CIM processes before
truncation. High-numbered PIDs can be omitted from a correct snapshot.

## Correction

Retain the genuine unfiltered operating-system snapshot and fail if it contains no
processes. If the current PID is absent, obtain a second authentic service snapshot
using `query=str(os.getpid())`. Populate the all-processes browser query with the same
value and verify the adapter sends it. Preserve creation timestamp, stable process key,
`isCurrentProcess`, and telemetry assertions. Do not increase the production limit,
substitute fake PID rows, skip the test, or grant any new capability.

## Evidence and limits

The focused P2 module passed 10/10 tests on the reconstructed Linux checkout; the
combined P3 Frob subsequently returned `PASS_BOUNDARY_ONLY` with its controlled
gates passing. This does not certify a Windows-host run. Run
`python -m tools.mcel_p3_operational_completion_frob` on Windows after applying the patch.

This patch modifies one test and adds this explanation. No operational runtime,
backend, DSL, or authority behavior changes.
