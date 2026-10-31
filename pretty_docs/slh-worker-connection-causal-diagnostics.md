# SLH Worker Connection Causal Diagnostics (October 30, 2026)

## Finding, not assumption

The 64-Worker Windows causal probe first found eight Workers missing **after the
paid-work/settlement phase and before the reserve change**. The local SEH adapter
recorded eight `worker_tcp_eof_first` observations and the Worker clients reported
`reader_error`. Neither label identifies who initiated the closure. A normal
relay close code (`1000`) is a downstream observation, not proof of voluntary
Worker departure. Consequently, changing the reserve policy is **not proven** to
cause the loss, and more buffering alone is **not proven** to repair it.

## Additional evidence in this patch

Each managed Worker retains its last 48 safe events (socket connected, remote
WebSocket close code, socket EOF, reader exception class/errno/SSL reason,
keepalive error, explicit local close). The local Worker-to-SEH adapter retains
its last 2,048 accepted/EOF/exception/close observations. Both include the
Worker's ephemeral **loopback port** and machine timestamps (`unix_ns`,
`monotonic_ns`). No TLS bytes, authentication material, private keys, payloads,
exception messages or untrusted close-reason strings enter these records.

On a missing-Worker failure, `integrated-stress-events.json` includes
`connection_timelines.workers` and `connection_timelines.adapter_events`, joined
by `local_adapter_port`. Events are recorded before a subsequent teardown.
The CLI error includes only a truncated summary; always examine the full JSON
file to recover the untruncated traces.

Time ordering can distinguish *first observed* local events; it does not by
itself establish the ultimate cause in a remote process. A Worker read failure
followed by adapter TCP EOF points toward the Worker/client-TLS side, while an
adapter relay WebSocket shutdown preceding the Worker read failure points toward
SEH. Unexpected EOF, receive frame timeout, keepalive send failure and local
explicit close are now separate events.

## Next experiments

Run the same `stress-integrated --causal-probe` on Windows with 64 Workers, 64
requests, concurrency 8 and seed 1729. Compare the timestamped Worker events
against the adapter's same-port events for every missing Worker. If an origin
remains ambiguous, add a matched upstream relay/Organization Service stream
identifier and corresponding close-decision audit; do **not** guess from a
normal close code or remove acceptance checks.

This is diagnostic instrumentation, **not** a claimed Worker-retention fix.

## Organization Service gateway-side close audit

The `GET /v1/org/admin/marketplace/connection-diagnostics` endpoint is
**administrator-authenticated**, uses a bounded in-memory event ring, and
returns no WebSocket contents, authorization keys, raw exception messages, or
peer IP addresses. It records connection registration, message-loop end,
exception class, safe WebSocket close codes (including received/sent direction),
Worker send failures, and unregistration. Events retain `device_id`, timestamps,
and the gateway-side loopback peer port for cross-boundary diagnosis.

The integrated stress `--causal-probe` report joins gateway events to the
missing Worker IDs alongside the existing Worker-client and local-SEH-adapter
events. The endpoint exists only in the separately running Organization Service;
it grants no new authority to the Local Service, and is read-only.

**Interpretation:** a TCP EOF at the adapter and the Worker does not identify
which party initiated closure. Compare gateway `gateway_handler_exception`,
`gateway_receive_loop_finished`, and `gateway_worker_unregistered` timestamps
and close directions to the Worker `socket_recv_eof` event before attributing
cause. A gateway exception alone also does not prove the gateway initiated the
closure; the underlying received/sent WebSocket direction is essential.

The endpoint accepts optional comma-separated `device_ids` and returns at most
128 matching events; the in-memory ring retains 1024 recent events. This
prevents the diagnostic API itself from exceeding the lab's HTTP response
budget. Marketplace `recent_decisions` similarly publishes only the last eight
records while keeping the previous 100 in memory. Both safeguards are separate
from the transport defect and do not change Worker allocation, accounting, or
reconciliation behavior.

## Full four-boundary stream causality (October 30 audit)

The 64-Worker Windows causal probe has repeatedly lost Workers **before** a
private-reserve change. Worker TCP EOF, adapter EOF, SEH relay WebSocket `1000`,
and gateway abnormal close `1006` establish where the connection failed, but do
not establish who initiated the shutdown. The close codes must not be treated
as an explanation.

The integrated stress failure's `connection_timelines` now contains:

- `workers` and `adapter_events`: Worker TCP port, EOF, reader exception, and
  explicit adapter-initiated close or shutdown.
- `seh_relay_stream_events`: relay client acceptance, explicit relay-origin
  stream close instructions, receive exceptions, and WebSocket close-frame
  direction (`received_close_before_sent` when the library exposes it).
- `slh_gateway_stream_events`: stream opening, local gateway TCP EOF, socket
  write/read errors, SLH-origin close instructions, and SEH-origin close
  instructions. These events are retained when the tunnel reconnects.
- `gateway_events`: registration, handler exceptions and cleanup, tagged by
  Worker ID and the gateway-side ephemeral TCP port.
- `slh_tunnel_lifecycle`: tunnel disconnect/reconnect and classified errors.

The correlation is: Worker ephemeral localhost port -> local adapter's SEH
client TCP port -> relay connection -> hashed ephemeral stream ID -> SLH
connector's gateway TCP port -> Organization Service Worker ID. No plaintext
TLS records, Worker payloads, credentials, signing material, or raw exception
messages are included in any audit event. Event rings are bounded.

**Analysis rule:** Prefer an explicit close *request* before a dependent EOF.
Cross-process timestamp ordering is supplementary, not conclusive by itself;
thread scheduling can reorder observation. If `received_close_before_sent`
is absent, do not infer direction from `1000` or `1006`. If a shared tunnel
reconnects, inspect the retired connector events and tunnel lifecycle before
attributing losses to individual Workers.

This is diagnosis, not a production reliability fix. The acceptance requirement
remains that every Worker not deliberately disconnected stays registered through
paid requests, policy changes, and recovery. The 64-Worker Windows failure
remains unresolved until an initiating event is identified and a targeted
repair passes repeated Windows stress tests.
