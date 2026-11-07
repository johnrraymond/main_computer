# Hub Resource Lab — R0–R4

Run from the repository root using the project's Python environment:

```powershell
python -m tools.hub_resource_lab.run_lab boundaries --output runtime/reports/hub-resource-lab/boundaries.json
python -m tools.hub_resource_lab.run_lab admission --output runtime/reports/hub-resource-lab/admission.json
```

`boundaries` exercises R1–R3 bounded HTTP bodies, headers, read deadlines,
RFC 6455 frame/message sizes, and malformed WebSocket frames. `admission`
exercises the initial R3–R4 limits against **real loopback sockets** on the
legacy and Stable Hubs: absolute slow-header deadlines, maximum concurrent
connections, refused request accounting, independent unauthenticated Worker
WebSocket admission, and release/recovery. The experimental FDB Hub shares the
legacy socket admission class; its WebSocket upgrade gate is separately covered
by `tests/test_hub_resource_admission.py` without requiring live FoundationDB.

The lab always uses temporary Hub state, ephemeral loopback ports, and tight
resource ceilings. It never connects to production or performs chain actions.

## Runtime ceilings (Patch 2D)

All overrides are positive integers and can only *reduce* defaults. A value of
zero or an increase above a hard ceiling prevents startup.

| Environment variable suffix (prefix `MAIN_COMPUTER_HUB_`) | Default |
|---|---:|
| `HTTP_BODY_MAX_BYTES` | 2097152 |
| `HTTP_HEADER_MAX_BYTES` | 65536 |
| `HTTP_BODY_DEADLINE_SECONDS` | 15 |
| `WS_FRAME_MAX_BYTES` | 2097152 |
| `WS_MESSAGE_MAX_BYTES` | 2097152 |
| `WS_PREAUTH_DEADLINE_SECONDS` | 8 |
| `WS_FRAME_DEADLINE_SECONDS` | 60 |
| `HTTP_HEADER_DEADLINE_SECONDS` | 8 |
| `HTTP_KEEPALIVE_IDLE_SECONDS` | 20 |
| `MAX_CONNECTIONS` | 320 |
| `MAX_HEADER_CONNECTIONS` | 32 |
| `MAX_CONNECTIONS_PER_PEER` | 320 |
| `MAX_WEBSOCKET_CONNECTIONS` | 256 |
| `MAX_UNAUTH_WEBSOCKET_CONNECTIONS` | 16 |

Independent limits compose by taking the smaller applicable ceiling. The peer
address seen by a Hub may be a reverse proxy, so do not tighten the per-peer
limit without verifying that the proxy and all multiplexed Workers retain their
required capacity.

A single bounded watchdog enforces the wall-clock deadline to parse each HTTP
request header, including HTTP/1.1 keepalive requests. Admission occurs before
new request threads are created. Rejected sockets receive a best-effort HTTP
503 and are closed; they are never dispatched to business logic. The Stable Hub
and experimental FDB Hub additionally require a separate WebSocket admission
slot before writing a 101 response. A pending Worker authentication slot is
released only after successful authentication or socket termination.

**Not yet certified:** R5 paid-job availability and throughput under sustained
hostile load, R6 crash/failover/financial invariants, R7 SLH/SEH relay limits,
and production deployment. These are required for final network-resource
acceptance; an R0–R4 PASS is not R0–R7 certification.

## Patch 2E — SLH/SEH relay isolation (R7) and mixed paid traffic (R5)

**Prerequisite:** Apply Patch 2A–2C and then Patch 2D. Patch 2E is incremental
and preserves those Hub transport and financial-authority protections.

```powershell
python -m tools.hub_resource_lab.run_lab relay --output runtime/reports/hub-resource-lab/r7-relay.json
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 2 --requests 2 --concurrency 1 --strict-acceptance --json
python -m pytest -q tests/test_hub_resource_relay.py tests/test_seh_stream_write_queues.py
```

`relay` is a disposable loopback-only R7 lab. It creates one real authenticated
SEH outbound control tunnel, a local TCP echo gateway and separate Worker-side
TCP sockets through the actual relay. It checks normal duplex routing,
overflow of a malicious Worker stream, survival of the other stream, complete
SLH disconnect, signed SLH reconnect, and new Worker traffic after recovery.
It also tests a blocked per-stream writer with an aggregate, thread-safe,
queued-plus-in-flight byte reservation. The 8 MiB budget is shared among *all*
SEH outgoing client writers, and separately among *all* SLH gateway writers.
The existing 2 MiB per-stream byte and 64-item limits still apply. On budget
exhaustion the offending stream fails closed; unrelated streams retain their
own slots. WebSocket receive queues are capped at four messages per connection.
The budget is **not** a process-wide RSS limit or a guarantee of total memory
usage; protocol receive buffers and socket buffers have separate ceilings.

The `stress-integrated` lane uses a disposable in-memory Stable Hub, a real
organization-service child, an authenticated SLH/SEH tunnel, real Worker sockets,
and synthetic credits. Its R5 campaign concurrently sends an unauthorized
HTTP request declaring a 2 MiB+1 body (expected HTTP 413) and an oversized
SEH client frame (expected stream close), while legitimate paid jobs run.
The Hub's ledger is then checked for exactly one charge and earning per
successful paid session, no duplicate charges on signed replay, and correctly
released holds. Worker reconnection, session withdrawal, and relay isolation
are also checked. A claim may create a **pending** settlement batch; the lab
never asks a Worker to finalize its own settlement or fabricates proof.

Strict mode requires every paid request to be admitted and to succeed. If a
small fleet is offered more concurrent jobs than it can accept, capacity
rejections are expected; the lab reports them separately from HTTP transport
errors. For a 64-Worker capacity experiment the extra malicious SEH client
probe is excluded from the oversized-frame assertion because the relay has
no spare stream slot by design.

**R6 limitation:** The integrated lane exercises Worker churn, relay
reconnection, hold state, exactly-once charges and replay. It does not prove
Hub process kill, durable FDB recovery, or cross-Hub failover. The existing
`tools.hub_crash_lab.run_lab --mode failover` requires the *already configured*
local FoundationDB cluster file (`.foundationdb/docker.cluster`) and never
initializes or resets FoundationDB itself. Absence of that cluster means
`R6=INCOMPLETE`, not PASS. The full R0–R7 promotion gate therefore remains
incomplete pending the FDB crash/failover campaign and deployed-service review.
