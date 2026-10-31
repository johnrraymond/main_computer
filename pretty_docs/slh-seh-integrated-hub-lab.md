# Secure Local Hub / Secure External Hub — Integrated Hub Lab

**Purpose:** Give an operator **one command** to start the known local smoke Hub topology, exercise real SLH worker connections and SEH real runtime transport, check the scheduler, and clean up lab-owned processes. This lab uses the same `deploy/hub-topology/smoke-topology.json` as the Stable and Experimental Hub lab runners. It does not start legacy experimental economics or alter mainnet/testnet.

## Quick start

From the repository root in its intended Python environment:

```powershell
python -m tools.slh_seh_lab.run_lab run
```

The command owns three local Stable Hub child processes on ports **8870, 8871, 8872**, using the existing smoke FoundationDB namespace `main-computer-hub-smoke`. It tests the SLH/SEH paths and terminates **only the children it started**, leaving existing deployments alone. Local FoundationDB and the normal Stable Hub dependencies must already be available, exactly as for the Stable Hub lab. It will **refuse** if one of those ports is occupied; it will not kill the port owner. It neither clears nor recreates the FoundationDB namespace. This lab is unsuitable for a dirty/production smoke database if complete isolation is required; use the preexisting smoke-lab rules.

When the Stable smoke cluster is already running in another terminal:

```powershell
python -m tools.slh_seh_lab.run_lab run --use-existing-hubs
```

This first checks the identity of all three Hubs and leaves those existing processes running afterward. It creates test-only wallet/MSK and live worker records **plus a synthetic, disposable requester credit deposit, charge, worker earning, claim and local settlement** in the existing smoke namespace; no mainnet or testnet side effects. These accounting records remain in the smoke FoundationDB namespace for audit; the runner does not wipe shared test state.

To perform a fast real-socket test without FoundationDB or external Hubs:

```powershell
python -m tools.slh_seh_lab.run_lab verify-local
```

This starts one genuine Stable Hub HTTP/WebSocket server with **in-memory** signing-key and worker-session stores, plus a disposable SLH organizational database, four enrolled walletless worker clients and an opaque TCP relay. It tests actual Worker/SLH sockets, shared-wallet Stable Hub registration, and an accepted paid request whose real result is produced by a managed walletless worker. This is **not** a three-node FDB test.

To inspect the topology and acceptance plan without launching anything:

```powershell
python -m tools.slh_seh_lab.run_lab plan --json
```

## Production two-service acceptance (no Hub or Coolify required)

Patch 2A changes the SLH from a directly launched gateway into **two real
services**. The lab now checks that boundary before running its legacy
paid-marketplace integration lane. To test only the real SLH services:

```powershell
python -m tools.slh_seh_lab.run_lab verify-services --json
```

`verify-services` runs a real localhost Local Service and a **separate TLS
Organization Service process** under the Local Service's control. It uses only
isolated temporary state and localhost ports: it never reads the installed
SLH's organizational directory, wallets or identities and never talks to
mainnet/testnet. It checks the local Authenticator API, staged P-256 bootstrap,
explicit TOTP deferral, denied early activation, remote TLS admin access,
local/organization API separation, enrollment queue and approval, a real
Worker WebSocket session, and a real authenticated SEH relay that forwards
another pinned-TLS Worker session. It also kills **only its own lab Organization
child** to prove recovery, checks SEH reconnection after a controlled service
restart, and verifies saved enabled/disabled states across local-control
restarts. Every gate is written under `two_service_lab.gates` in `report.json`.

The normal `verify-local`, `run`, and `verify-deployed` commands also require
**a distinct real-Organization-process paid-work lane** and preserve the
existing in-process paid-work regression lane. The real-process lane uses the
localhost service for provisioning and enabling the Organization Service,
then its authenticated TLS organization management API to configure scheduling
and reconcile public Hub sessions. Its single walletless Worker connects
**through the local SEH relay**, and the real Stable Hub accepts the request,
charges synthetic lab credit, records the Worker earning and settles it in its
local ledger. The lab reports this evidence separately under
`real_process_paid_work`, with the prior in-process path retained under `slh`.
A failure of either paid lane fails the overall command.

The new organization-facing admin endpoints
`GET /v1/org/admin/marketplace/status` and
`POST /v1/org/admin/marketplace/reconcile` operate on the actual marketplace
proxy and scheduler inside the Organization Service process. They require a
valid organizational admin session and do not expose the upstream MSK in their
status responses. The reconciliation call takes a Hub URL and an
MSK authorization; it is **not yet** a durable marketplace connection setting
or an automatic periodic reconciliation scheduler. Configuring that ongoing
organizational behavior remains future work.

A passing `verify-services` alone does **not** test Stable Hub settlement,
external bridge funding, remote Coolify, public DNS/WSS, actual Windows boot
registration, or TOTP. These must not be inferred from this result. The
full `verify-local`/`run` lab requires both the new real-process paid-work
acceptance and the existing in-process regression checks. A trimmed source
export that lacks `game_projects/webgl-demo/game.json` may prevent importing
the existing Worker runtime; the fixture is not recreated or silently included
in this patch. A PASS from `verify-services` must not be substituted for a
full paid-work PASS.

## What is exercised

| Lane | Actual assertion |
| --- | --- |
| Administrator and device enrollment | Temporary SLH organization initialized; four real signing keys enrolled as walletless workers |
| Managed Worker connections | Four encrypted, authenticated Worker-to-SLH live sessions (not HTTP stubs) |
| **SLH-owned configured SEH connection** | Enabled SLH gateway automatically authenticates the outbound tunnel on startup, reconnects after the lab closes it, and disconnects on gateway shutdown; no standalone connector CLI is started |
| SEH real runtime transport | One Worker reaches SLH through a **loopback-only opaque TCP relay**; worker still validates SLH's TLS fingerprint; wrong fingerprint is refused |
| Marketplace MSK | A disposable lab-only marketplace wallet obtains a real signed MSK from the selected Stable Hub |
| Public capacity | SLH opens two independent public live Worker sessions under that one account, after applying 50% public and 25% private reserve policy |
| Private execution | SLH reserves a nonadvertised worker, transmits authorized work, collects terminal result and releases capacity |
| **Paid work through the actual Organization Service** | Real localhost control starts a separate Organization process; authenticated organizational marketplace API advertises its Worker through a real local SEH relay, and Stable Hub accepts, executes, charges and settles one synthetic-credit job; reports `real_process_paid_work` separately |
| **Paid Stable Hub requester cycle** | Separate disposable requester wallet acquires real MSK; is given **synthetic smoke-only credits**; submits one paid request through Stable Hub; SLH proxy must forward authorization to a walletless managed Worker (including `echo` input) |
| **Payout accounting** | Real Hub records exactly one requester charge and one wallet-owned worker earning, holds resolve to `charged`, request replay is idempotent, worker wallet performs claim and ledger settlement; no external bridge is claimed |
| Scheduler drain | Setting public allocation to zero closes both live marketplace sessions without leaving stale advertisements |
| SEH deployment contract | Existing Coolify deployment controller checked against its in-memory fake; no API calls to a real Coolify server |

The runner creates ephemeral SLH worker identities and a disposable marketplace wallet **only in memory**. No wallet secrets are placed in the report. A JSON report is saved per run under `runtime/reports/slh-seh-lab/<run-id>/report.json`, alongside Hub child logs when the runner owns the cluster. Failed acceptance gates return a nonzero exit code. The runner waits for Known Device session logout and checks the SLH authority database after cleanup; failures are reported rather than counted as successful Worker registration. The lab waits for WebSocket authentication handlers to finish before deleting disposable SLH state and checks SQLite integrity during cleanup; a detected session-logout/database problem fails the run rather than counting registration as full success. Use `--json` for machine-readable output.

## What does not pass yet

**Live Coolify/public SEH deployment: NOT TESTED.** The lab now exercises the deployable SEH runtime, SLH signing authentication, outbound tunnel and real paid-worker path **locally**. This does not establish remote DNS, public WSS/TLS, Coolify availability, or operation across an actual internet connection. See [SEH real relay deployment](seh-real-relay-deployment-and-acceptance.md).

**External bridge payout: NOT TESTED.** Paid requester admission, protected passthrough, Hub-side charging, wallet-owned earnings, claim and **local ledger settlement** now have separate passing gates. These use explicitly synthetic requester funds in in-memory or the isolated smoke FDB namespace; they do **not** prove real token bridge funding, on-chain transfers, or production settlement. A normal Worker has an echo shortcut; the SLH marketplace proxy is prohibited from taking it because it would bypass the physical worker. The lab now checks that the managed worker itself produced the terminal result.

The lab's in-memory lane does not prove cross-Hub FDB propagation or persistence under process death; the `run` lane verifies all three real smoke Hubs before exercising one Hub's worker path. Continue using the Stable/Experimental Hub labs for **cross-Hub handoff, external bridge payouts and crash recovery**. The SLH lab exercises its own Hub-owned worker earnings and local ledger settlement on one selected Hub, not cross-Hub owner handoff.

## Protection and failure behavior

The `run` command accepts only a three-node, loopback, `dev/smoke` topology with the existing dedicated smoke FDB namespace. It will not connect to public hubs, touch the user's initialized SLH or `admin-pc` identity, change existing scheduler policies, use a production wallet, deploy Coolify, or erase existing FDB data. All temporary device credentials are removed after the run. Failing Hub identity preflight prevents MSK issuance. A missing game manifest in a trimmed code export may block loading the legacy Worker runtime; it is a source-export completeness issue, not proof of Hub failure.

To rerun tests separately (if needed):

```powershell
python -m unittest tests.test_slh_seh_lab -v
```

The lab provides a backend integration path before building the SLH/SEH administration UI. A real internet/Coolify deployment and external credit-bridge payout remain separate acceptance gates. A paid lab transaction cannot be counted as bridge-funded production payment.

## Live local Coolify acceptance: `verify-deployed`

The **deployed** acceptance lane uses the **existing Coolify-managed SEH** on
`127.0.0.1:18080`; unlike `run` and `verify-local`, it does **not** create an
in-process SEH substitute. Run the same three-Hub smoke topology with:

```powershell
python -m tools.slh_seh_lab.run_lab verify-deployed --org-id my-company --slh-id my-slh
```

It uses Website Builder's existing *local Coolify* authentication and service
lookup, so no user-provided key or dashboard click is required. On the **first**
run after installing this patch, it automatically rebuilds and redeploys the
**same** Coolify SEH service to install isolated lab-channel support. That
initial upgrade briefly disconnects the primary SLH tunnel; the existing SLH
connector must be running so it reconnects automatically (or the organization's configured SLH gateway must already have automatic SEH management enabled). Subsequent runs
reuse a compatible, healthy Coolify service without redeployment. A service
name/UUID from the Coolify API and an SEH-specific capability probe are required
before reuse; an unrelated process merely occupying port 18080 is not enough.

The runner opens a **separate SLH-authenticated, randomly named lab channel** through the SLH gateway's **automatic configured SEH supervisor** (not by running the connector CLI). This proves the same connection lifecycle as a configured organizational gateway. It is never configured to take ownership of the real organizational tunnel.

The runner opens its paid-work acceptance connections on that isolated channel
through the deployed SEH. The organization's production SLH tunnel is *not*
a prerequisite for this isolated paid-work acceptance test: `/healthz` proves
that the deployed relay is running, while `/readyz` reports whether the separate
production SLH connector currently happens to be connected. A 503 from
`/readyz` must not prevent isolated paid-work verification. Its
independent temporary managed gateway, four enrolled device keys, scheduler,
marketplace wallet and requester live only in disposable lab state. It never
unregisters live organization workers, copies live wallet keys, replaces the
primary tunnel, or edits the real organization's scheduler. The first public
worker job is explicitly constrained to the SEH-relayed managed worker. All
credit funding and settlement occur in the validated smoke FoundationDB
namespace, with no external bridge payout.

If the primary tunnel was connected **before** the test, an additional mandatory
gate checks that it is connected again afterward; losing it is reported as
`LOST_DURING_VERIFICATION` and makes the overall result FAIL **after** the paid
lane produces its diagnostic evidence. If the primary was already disconnected
at test entry, its availability is reported separately as
`NOT_CONNECTED_AT_START` (not a passing preservation gate), and the isolated paid
lane can still pass. The lab also proves that its own channel can authenticate
again after cleanup, that the wrong pinned TLS leaf
is rejected, and that the Stable Hub records one charge and one wallet-owned
earning. It writes the regular `report.json` and `lab-runtime.log`. The runner
may start and stop its own three smoke Hub children on ports `8870–8872`; use
`--use-existing-hubs` only for a separately verified running smoke cluster.
It does not run any new tests against mainnet, testnet or a public SEH endpoint.

A successful `verify-deployed` run establishes **Coolify-managed loopback SEH
connectivity and paid Stable Hub work through that instance**. It does **not**
prove public WSS/DNS, a remote internet deployment, external blockchain bridge
settlement, or uninterrupted active jobs during a Coolify upgrade. A real live
Coolify deployment must still be verified on the host running Docker; the local
Python regression suite cannot establish that on its own.

## Persistent SLH -> SEH connection (gateway owned)

When the SLH managed-worker gateway starts, it checks its own state directory for
`seh-connection.json`. **Absent or disabled** means no outbound connection.
An **enabled** configuration starts one authenticated outbound connection through
the gateway's lifecycle and retries after an interrupted connection, including
when the SEH is unavailable at SLH startup. Stopping the gateway also closes
its tunnel. The separate `tools.slh_seh_connector run` is no longer required
for this mode and **must not be run concurrently for the same primary tunnel**.

The local `deploy-local-coolify` command now automatically writes this public
route configuration after successful SEH health verification. It does not
store any signing keys in that JSON; it uses the existing SLH service identity
from `%LOCALAPPDATA%\MainComputer\slh`. Restart the SLH managed gateway
**once** after first configuring the SEH to activate the setting. Afterwards,
all connection and reconnection work belongs to the gateway.

Both the ordinary `run` and `verify-local` lanes assert
`seh_configured_autoconnect_on_slh_start`,
`seh_configured_autoreconnect_after_drop`, and
`seh_configured_disconnect_on_slh_stop`. The `verify-deployed` lane uses the
**Coolify-managed SEH** for those same gates, but only through the disposable
authenticated lab channel. These assertions cannot be satisfied by the lab
calling the standalone connector CLI.

The live organization's tunnel is still reported independently as `PRESERVED`,
`LOST_DURING_VERIFICATION`, or `NOT_CONNECTED_AT_START`. An isolated lab PASS
does **not** establish that a separate, already-running organization's SLH
service was restarted with its persistent autoconnect configuration.

## Normal Main Computer startup (SLH lifecycle)

The Main Computer supervisor now starts the **SLH localhost control service**
(`127.0.0.1:8897`). If initial organizational setup has not been completed,
this service remains available for local bootstrap and **does not start** the
organization-facing gateway. Completing first-admin bootstrap also **does not
enable organizational serving**. An authenticated administrator must configure
and explicitly enable the separately spawned TLS Organization Service. The
Local Service owns child-process start, stop and recovery, and preserves the
administrator's enabled/disabled preference across restarts. The Worker
listener and optional SEH connection are governed by separate enablement
settings. Neither worker-serving nor outbound SEH connectivity is implied by
local supervisor startup.

A separate, default-selected Windows boot-start option is specified for the
future organization setup UI, but its UI integration is not yet implemented.
The SLH Lab's `verify-services` lane proves process lifecycle through the
localhost API; it does not claim an operating-system reboot or Windows task
registration has been tested.

## Automatic Organization marketplace participation and decision stress

The real Organization Service owns the public-marketplace controller alongside
its enrolled-Worker scheduler. Marketplace participation is **disabled by
 default**, even when the Organization Service itself and Worker listener are
 enabled. Administrator-configured marketplace enablement, an upstream Hub URL,
 and the permitted scheduler capacity policy are distinct. The scheduler's
 existing private reserve, public percentage, dedicated assignments, rotation,
 and credit-cap fail-closed rules remain authoritative. Enabling participation
 must not override these constraints.

Authenticated organization administrator APIs provide:

- `GET /v1/org/admin/marketplace/config` — view participation policy,
  credential presence, and reconciliation health without exposing secrets.
- `PATCH /v1/org/admin/marketplace/config` — change enablement, Hub URL, and
  reconciliation interval using an expected configuration revision.
- `POST /v1/org/admin/marketplace/authorization` — install a Hub-issued
  multisession authorization; the authorization itself is never echoed.
- `GET /v1/org/admin/marketplace/status` — inspect actual public sessions,
  managed Workers, and nonsecret automatic-controller observations.
- `POST /v1/org/admin/marketplace/reconcile` — force a serialized reconciliation
  using **saved** participation settings; this is for diagnostics, not a
  prerequisite for normal operation. Disabled participation cannot be bypassed.

The Organization Service stores nonsecret participation choices separately from
its Hub authorization under the service-owned organizational state directory.
Hub authorization is not returned by status or configuration APIs. These paths
are service-owned local persistence, not UI or Worker settings. Private
credential encryption/dongle controls remain a separate future capability.

The controller wakes on authenticated Worker registration/disconnection and
scheduler-policy edits, and checks periodically for missed changes, completed
jobs, or lost upstream sessions. Automatic and manual reconcile calls are
serialized. Disabling participation, changing Hub destinations, or replacing
Hub authorization drains old advertisements without canceling authorized
in-flight jobs; new sessions are opened only under the resulting valid state.
On a restart, the service reloads saved participation policy and authorization
and reconciles when Workers return. It never advertises solely because a
scheduler policy happens to permit public slots.

`verify-local` and `run` must retain the separate real-process paid-work lane,
legacy paid-work regression lane, and two-service lifecycle lane, and also
require the bounded deterministic Decision Stress Frob. The routine stress lane
checks 1,500 seeded scheduler decisions across 16-, 64-, and 256-Worker pools
with Worker churn, lost authorization, private/public jobs, policy conflict,
credit limits, and rotation, plus parallel reconciler operations. A separate
`stress-decisions --json` command tests 9,000 decisions and 1,000 parallel
reconcile operations. Seeds and the first failing event prefix must be
reportable for reproduction. The production scheduler is used for large
simulated fleets; the real-process paid-work lane independently proves TLS,
SEH transport, automatic opening, policy-change draining, concurrent manual
calls, and synthetic-ledger settlement. Simulation is not represented as
thousands of real network Worker connections.

These tests do not validate deployed public WSS, cloud Coolify, actual bridge
settlement, OS-level boot registration, or unbounded endurance. They must not
modify a configured organization's live Worker sessions or secrets.

## Real network and paid-work integrated stress (separate opt-in lane)

The `stress-integrated` command runs against a disposable **real in-memory
Stable Hub HTTP server**, real Local Service, **separate Organization Service
process**, real local SEH relay, TLS-enrolled Worker sockets and the existing
Hub ledger. It does **not** instantiate a fake SLH scheduler, credit bridge,
or public SEH. It performs a baseline paid transaction, then concurrently
submits additional paid requests through actual network sessions. The same
Organization Service must automatically maintain public Worker sessions.

```powershell
# Bounded starting point (actual Worker sockets, not a simulated fleet)
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 8 --requests 16 --concurrency 4 --seed 1729 --json
# Progressively probe the real connection ceiling on the host being tested
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 16 --requests 32 --concurrency 8 --seed 1729 --json
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 64 --requests 64 --concurrency 8 --seed 1729 --json
python -m tools.slh_seh_lab.run_lab stress-integrated --workers 256 --requests 256 --concurrency 16 --seed 1729 --json
```

The maximum requested scale is **256 actual network Workers**, not proof that
256 connections already pass. Start small and increase only after the previous
scale succeeds; the larger tiers can expose operating-system socket/thread
limits, upstream Hub limits, or teardown stalls. `verify-local` remains a
bounded, separate baseline and does not claim to have run this opt-in workload.

The integrated stress lane checks that Workers are enrolled by the real
Organization API, authenticate through the local SEH relay, and are
advertised without calling manual reconcile; that overlapping paid work
produces a unique session per accepted request, matching Worker output,
correct single charges and earnings, wallet-authorized claim and settlement,
and replay-safe accounting. Refused requests are **counted as refused**, not
misreported as completed paid jobs; at least half of bounded requests must
be accepted. It then changes private reserve, disconnects/reconnects Workers,
fully disables and re-enables public participation, and requires the
advertised capacity to converge without a manual reconcile. Local Service
and Organization Service crash-recovery tests remain in `verify-services`;
this new stress lane does **not** claim to exercise a process crash during
settlement or live public internet failures.

The report includes the seed, workload profile, acceptance and refusal counts,
settlement gates, latency distribution, and decision events. A redacted
`integrated-stress-events.json` file is written progressively beside
`report.json`; it remains useful if a high-scale run times out. Random
identifiers and OS scheduling are not deterministically replayed byte for
byte; the seed controls the test's generated markers and scenario choices.
No Hub token, worker private key, or authenticator secret should enter these
reports. All funding is synthetic lab credit, and settlements are strictly
local ledger settlements. Cloud Coolify, public WSS and real bridge payouts
remain NOT TESTED.


## Network stream isolation at the SEH capacity boundary

The single SEH relay currently supports **64 simultaneous client streams**.
A Worker connection above that limit must be refused without disconnecting
existing Workers or destroying the authenticated SLH-SEH tunnel. Likewise,
a failed local TCP connect, failed per-stream write, or invalid per-stream
payload must close only that stream. The SLH's marketplace may temporarily
withdraw or re-advertise capacity while the connector and relay finish closing
streams; the integrated lab waits for the *actual relay client count* to drain
before reconnecting previously approved Workers.

`stress-integrated` does not execute the `verify-local` single-Worker eight-step
policy-reversal preflight before starting its fleet. Both are independent tests.
The integrated lane tests the real network path, checks that surviving Workers
remain registered after churn, and requires continued SEH tunnel connectivity.
It records per-phase connection progress and treats transport failures as failures,
not as valid Hub capacity refusals.

Profiles above 64 real Worker connections are explicitly rejected for one SEH;
validating 256 simultaneous network Workers requires a separately designed
multi-SEH topology and is **not** established by the scheduler-only 256-Worker
decision stress. This does not change the scheduler's supported simulated fleet sizes.

### Slow WebSocket close isolation and churn diagnostics

The SEH control tunnel must never wait for an individual Worker WebSocket
close handshake. A stream close is dispatched independently, with a bounded
server WebSocket close timeout and duplicate-close suppression. This preserves
healthy multiplexed streams during Worker churn. The integrated stress lane
checks the **exact surviving device set**, not only the count; failures report
missing and unexpected device IDs, tunnel state, relay client count and
pending close handshakes. No authorization or secret material is recorded.

### Diagnosing partial Worker loss with an intact SEH tunnel

A healthy authenticated SEH control tunnel does **not** prove every individual
Worker WebSocket remains connected. If the lab expects 48 survivors and observes
40, it must fail even when the tunnel is up and paid work has already settled.
The lab now asserts that a private-reserve change alone never drops Worker
registrations, checks actual Worker row identities before and after intentional
churn, and verifies that departed device identities really disappeared from the
Organization Service's list of Worker rows. Previously, the disconnection wait
compared a name against a list of dictionaries and could pass prematurely.

On loss, the progressive trace includes the exact missing device IDs and a
redacted per-device connection classification (`client_alive`,
`client_close_reason`, `client_error_class`) from the actual test-owned Worker
clients. The local SEH relay additionally tracks aggregate WebSocket close
codes without recording frames or credential material. Interpret `1006` and
unknown closure codes as diagnostic symptoms, not by themselves the proven root
cause. Do not convert missing Workers to acceptable capacity loss or simply
extend timeouts: an unexpected Worker disconnect is an acceptance failure.

### Worker WebSocket frame-boundary hardening

Worker live-session reads preserve a partially received WebSocket frame across
short socket timeouts. An idle read may time out normally, but a header,
extended-length field, or payload already in progress may not be discarded
and reinterpreted as a fresh header. Incomplete frames fail after a bounded
15-second interval. The lab records a remote WebSocket close code, when
available, without copying any peer-provided reason text or credentials.
This protects protocol framing under multiplexed SEH congestion; a successful
local stress run does not by itself certify Windows reliability.


### Worker-stream write backpressure and closure evidence

The SEH tunnel carries multiple Worker TCP streams. Each stream uses a bounded,
ordered, nonblocking write queue on the SLH and relay sides. The queue defaults
to 64 pending chunks with a separate 2 MiB byte budget, rather than the former
eight-chunk-only budget, which could close healthy streams during short bursts
of small TLS records. A persistently blocked stream still fails independently.
This does not change the 64-client limit or grant a Worker new authority.

SLH-to-SEH close messages now include a fixed, non-secret cause classification:
`local_eof`, `local_read_error`, `write_error`, `write_queue_full`, or
`write_byte_budget`. The SEH returns WebSocket code 1013 on queue saturation,
1011 on read/write failures, and 1000 on local EOF; it counts these causes
without storing Worker credentials or payloads. Integrated stress records these
aggregate counters after paid settlement and reserve-policy updates, and on
unexpected Worker loss. A clean 1000 code alone does *not* prove who initiated
an upstream TLS shutdown; the classified cause supplies additional evidence.

For the default 64-Worker integration test, all underlying Worker connections
must remain registered after the private-reserve policy changes. Runs stalled
in marketplace disable/re-enable are not successes even if paid execution and
Worker churn completed; report them as independent convergence failures.

### Worker-retention causal probe (optional; real connections, no production mutation)

If the integrated stress lane reports missing Workers only at the post-paid
reserve-change check, it has **not** established that reconciliation caused
the loss: the Workers may have disconnected during the paid work phase.
Run the following on the same Windows machine and repository revision:

```powershell
python -m tools.slh_seh_lab.run_lab stress-integrated `
  --workers 64 --requests 64 --concurrency 8 --seed 1729 `
  --causal-probe --json
```

The opt-in probe adds six checkpoints: immediately before a **pre-paid**
reserve reduction, after that reduction, after restoration to full capacity,
immediately before paid jobs, immediately after paid settlement and **before**
the regular reserve reduction, and after that reduction. (The existing
post-reserve Worker-retention check is also preserved.) Every checkpoint logs
the exact registered Worker identifiers and relay close-code counters to the
existing `integrated-stress-events.json`, including on failure.

Interpret the **first failed checkpoint**:

- `after_prepaid_reserve_change`: reserve adjustment can trigger Worker loss
  even without paid-job load. Follow the managed gateway / SEH stream-close
  chain rather than the Stable Hub payout ledger.
- `immediately_before_paid_requests` or `paid_settled_before_reserve_change`:
  the Workers were already lost *before* the contested post-paid reserve
  change; investigate the connection, keepalive, or paid-job data path.
- `paid_settled_after_reserve_change`: Workload alone did not lose Workers in
  this run. Focus on the policy transition and concurrent session draining.

One passing run does not establish a persistent root cause. In particular,
WebSocket close code `1000` represents a normal close handshake, **not** the
identity of its initiator. The test deliberately does not change production
scheduler, Worker, or SEH transport code. Do not claim a transport fix on
its evidence alone; repair the first proven initiating close path and rerun
all existing acceptance lanes and repeated Windows 64-Worker stress.

The causal probe also records adapter `first_close_causes` counters. A
`worker_tcp_eof_first` observation means the *local Worker-to-adapter TCP*
side ended before the adapter observed the relay WebSocket ending; a
`relay_websocket_ended_first` observation means the reverse was observed.
These are observation-order categories, **not** definitive remote intent;
use them with the Worker last-error classification and SLH stream-close
counters to locate the initiating path. Normal `1000` close codes alone
never identify the initiator. The counters are process-local test evidence,
not exposed through public administrator endpoints.
