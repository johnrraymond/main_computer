# SLH paid Worker transport differential diagnosis

## Purpose

The integrated 64-Worker Windows stress lab sometimes loses its *original*
Worker during the preliminary paid exchange, before any additional stress
Workers are started. The Worker records an EOF after its result, while the
Organization Service and SEH relay report later connection losses. An EOF alone
does not establish which peer initiated shutdown.

The **diagnose-paid-transport** lane deliberately removes the 64-Worker fleet,
policy churn, and unrelated stress preflights. It runs a single enrolled Worker
and repeated **real paid jobs** through the real Organization Service and the
loopback Stable Hub, using a distinct disposable SLH organization in each mode:

- `direct`: the Worker connects straight to the Organization Service's TLS
  Worker listener, with no SEH relay or connector.
- `seh`: the same Worker protocol connects through the local SEH adapter, relay,
  and Organization Service's outbound SEH connector.

Both modes verify real local-ledger charging, earning, and wallet-authorized
settlement on each cycle. Neither uses external bridge settlement. The Worker
uses a probe-only TLS setting (`suppress_ragged_eofs=False`) to distinguish
unexpected truncated TLS shutdowns from ordinary EOF behavior; normal Worker
configuration is unchanged.

## Windows commands

```powershell
python -m tools.slh_seh_lab.run_lab diagnose-paid-transport `
  --transport both --cycles 16 --idle-seconds 0.5 --json
```

Run an idle-only control with no paid jobs:

```powershell
python -m tools.slh_seh_lab.run_lab diagnose-paid-transport `
  --transport both --cycles 0 --idle-seconds 15 --json
```

Runs are confined to a disposable localhost Organization Service, the local
SEH (when selected), and the in-memory lab Stable Hub. Existing `verify-local`
and `stress-integrated` commands are unchanged. Up to 128 paid cycles per mode
are supported; each cycle generates independent synthetic funding and verifies
terminal ledger settlement and Worker retention.

## How to read the results

- `BOTH_PATHS_PASSED_THIS_RUN` indicates only that both paths survived this
  specific run; intermittent failures remain possible.
- `SEH_PATH_FAILED_DIRECT_PASSED_THIS_RUN` narrows this run to a difference
  introduced by the SEH-mediated path, but does not establish root cause.
- `BOTH_PATHS_FAILED_OR_SETUP_INCOMPLETE` requires inspection of each mode's
  `last_stage`: distinguish setup failure from paid-result disconnect.
- `SINGLE_PATH_ONLY_NO_COMPARISON` means no differential inference is possible.

Every mode produces `paid-transport-direct.json` or
`paid-transport-seh.json` in the run's report folder. Each file is updated
*before and after every paid job* and preserves the precise stage of the first
failure. Failure evidence includes structured Worker socket/TLS exception
classes, local adapter events where present, and Organization Service gateway
connection events. Raw keys, auth tokens, request markers, result content and
untrusted exception messages are deliberately excluded.

**Limitation:** This is application-level instrumentation, not a Windows TCP
ETW capture. Neither a first `socket_recv_eof` nor WebSocket close code 1000
proves the sender of a TCP FIN. An SSL `SSLEOFError` in strict probe mode is
stronger evidence of TLS truncation, but still requires correlated gateway,
relay, or OS events to identify the initiating close operation.
