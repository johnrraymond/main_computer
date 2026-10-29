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

`start.bat` now starts the SLH worker gateway (loopback port 8898) as a child of
`ServiceSupervisor`, just like the app, executor, applications, and blockchain
services. The gateway owns its existing configured SEH connection, including
reconnection. If SLH has not been bootstrapped yet, the supervised child stays
alive waiting for local administrator bootstrap instead of restarting repeatedly.

`stop.bat` stops the supervised SLH alongside the other Main Computer services.
Repeated `start.bat` runs also reclaim an old manually started gateway listening
on 8898 through the existing Windows port-ownership scan. No separate
`tools.slh_managed_gateway serve` or `slh_seh_connector run` command is needed.
The public marketplace remains disabled unless configured separately; enabling
a configured SEH does not authorize marketplace spending.

Verify with `Get-NetTCPConnection -LocalPort 8898 -State Listen`,
`Get-Content runtime/service_supervisor/state.json`, and the deployed SEH's
`/readyz`. A listening SLH gateway and a connected SEH are separate checks.
