# MCEL P3.6 — Windows process-source recovery (2026-11-01)

## Observed failure

One Windows bounded P3 Frob recorded `TaskManagerService.snapshot(...).processes == []`; a separate hosted P3.6 check at 1100 CSS pixels failed to find the requester's independently known PID and creation generation. Four other responsive browser checks passed. These observations do **not** establish that the OS lacked processes; they indicate the service sometimes failed to return authoritative process evidence.

## Source-level change

`TaskManagerService._process_rows()` preserves the existing `Get-CimInstance Win32_Process` producer as first choice. If that source returns no usable result, the service tries `psutil.process_iter` as a second, actual OS-backed producer. For decimal PID searches, a CIM result with no exact matching PID and creation timestamp also triggers the fallback. `psutil` produces an offset-aware UTC `created_at` usable for existing process-generation validation. No process-control authority, server lifecycle, scheduling, monitoring polling policy, or presentation semantics change.

Opt-in `process_diagnostics=True` reports actual serving `sourceKind` and `status`, and when fallback was attempted, `primaryStatus`, `primarySourceKind`, `fallbackStatus`, `fallbackUsed`, and `primaryMissingRequestedPid`. The ordinary snapshot shape is unchanged. A provider failure never fabricates rows, and the P3/P3.6 Frob still refuses to pass identity validation when both sources fail.

The hosted probe now records `osPidObservation` and (if available) `_mcel_task_process_producer` from a read-only diagnostic HTTP observation when PID identity verification fails. Its acceptance test remains strict and uses no mocked transport.

## Windows validation

From the installed Main Computer checkout:

```powershell
python -m tools.mcel_p36_windows_process_probe
python -m tools.mcel_p3_operational_completion_frob
python -m tools.mcel_p36_acceptance_frob --mode observe --host-url http://127.0.0.1:8765
```

The producer probe must identify the current PID and creation timestamp. In its `producer` evidence, `sourceKind: psutil-create-time-fallback` means CIM did not supply the requested identity and `psutil` did. `primaryStatus` distinguishes timeouts from command errors, empty CIM results, and other causes. Restart the Python viewport host after applying the patch to load the updated service; the installer does not restart it automatically.

`P3_WINDOWS_HOST` must not pass merely because fallback exists; the actual HTTP response must contain the independently known live PID and generation. Even when all those checks pass, real side effects, production parity, human design and promotion require separate evidence.
