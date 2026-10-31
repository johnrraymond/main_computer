# SLH–SEH Worker connection first-close forensic audit

## Purpose and acceptance boundary

The Windows integrated test intermittently loses individual registered Workers **during or after paid execution, before marketplace reserve changes**. A WebSocket `1000` from the local relay is often downstream cleanup; `1006` in the organization gateway means an abnormal closure, not a documented initiating component. An EOF observed on the Worker and adapter is not sufficient to identify who sent the original FIN.

This patch is **diagnostic instrumentation, not a production connection fix**. Never classify its successful unit tests as an end-to-end reliability result. The full `stress-integrated` test continues to fail on unexpected Worker loss.

## Evidence captured

The existing `--causal-probe` stress lane now preserves a joined, timestamped timeline for the missing Worker across the Worker socket, local SEH adapter, SEH relay, SLH tunnel stream, and Organization Service gateway. It captures new first-action events:

- Worker: an explicit close request, successful or failed WebSocket close frame, call sites that close the socket after an explicit request, keepalive error, or reader exception; send failures and paid reply send stage.
- Organization Service gateway: paid offer and authorization send stages, Worker acceptance/result/failure receipt, socket-level exception and handler cleanup.
- SLH tunnel: classified invocation of a local gateway TCP shutdown, received SEH close instruction, tunnel disconnection, and writer-initiated close.
- SEH relay: stream-level invalid-frame and backpressure closures, received close instructions, client receive errors, close-frame direction where available.
- Stress lab: on an unexpected loss immediately after the preliminary paid transaction, sample the remote gateway audit *again* after a short post-failure window. This is for evidence collection only; the failure remains a failure. The event report now also preserves stream state if worker churn finishes but SEH drain convergence does not.

`connection_timelines.causal_summary` orders safe events by the common monotonic clock on the lab machine. It reports `EXPLICIT_CLOSE_PRECEDED_OBSERVED_FAILURE` only when a recorded explicit close comes before the first reported transport error/EOF. Otherwise it explicitly reports `ORIGIN_NOT_ESTABLISHED`. **Both are investigative assessments; temporal order does not prove network FIN ownership.** Read the complete correlated trace before authorizing a production fix.

## Run

```powershell
python -m tools.slh_seh_lab.run_lab stress-integrated `
  --workers 64 --requests 64 --concurrency 8 --seed 1729 `
  --causal-probe --json
```

The terminal JSON includes `event_trace_path`. The full, *untruncated* `integrated-stress-events.json` has the checkpoint rows and, on failure, the `connection_timelines` object with `causal_summary`, `workers`, `adapter_events`, `seh_relay_stream_events`, `slh_gateway_stream_events`, `gateway_events`, and `slh_tunnel_lifecycle`. The Organization Service connection audit is administrator-authenticated and does not expose the raw credential material.

Check the first logged explicit close **that predates** the first EOF/connection error. Compare its stream tag to SLH/relay records and its socket-port correlation to the Worker and gateway records. If there is no explicit close before the failure, the report must say so; use it as a signal to inspect OS/socket-level activity rather than assume a source.

## Privacy and limits

Rings are bounded. Allowed fields include event names, numeric ports, hashed ephemeral stream tags, monotonic/UTC nanosecond timestamps, exception *classes*, safe OS error numbers, and numeric WebSocket close codes. No job input/output, tokens, private keys, certificate material, raw exception messages, raw addresses, WebSocket bodies, or authentication proofs are recorded.

This tests local lab SEH and synthetic ledger settlement only. Public WSS, live Coolify integration, external bridge settlement, and 64-Worker Windows reliability remain separate acceptance conditions.
