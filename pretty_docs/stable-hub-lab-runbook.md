# Stable Hub Lab — local operator runbook

**Scope:** Windows 11 development workstation, a three-Hub local smoke cluster, and the separate Stable/Experimental FoundationDB crash lab. **No Mainnet/Testnet deployment, Coolify mutation, or on-chain payment is part of this procedure.** Run commands from the `main_computer` repository root, with the intended Python virtual environment active.

## The two labs are different

| Tool | Purpose | Processes / ports | Database |
| --- | --- | --- | --- |
| `python -m tools.stable_hub_lab.run_lab` | Start/check the three Stable smoke Hubs; run Worker-cycle regressions, integrated market, WebSocket, credit, and payout tests | **8870**, **8871**, **8872** by default | `.foundationdb/docker.cluster`, namespace `main-computer-hub-smoke` |
| `python -m tools.hub_crash_lab.run_lab` | Inject crashes into disposable Stable and Experimental Hub processes; verify committed/rolled-back FDB records | Stable **18870–18872**, Experimental **18970–18972** by default | Same local FDB *cluster file*, but a fresh **isolated namespace per trial** |

The smoke-cluster server and the smoke-cluster verifier are **two processes in two terminals**. `--verify-stress` does **not** start the Hubs. The crash lab starts and stops *its own* isolated Hub child processes; do not aim it at live Hubs.

## Preconditions and isolation

1. Open a terminal in the `main_computer` repository root. Activate the project's normal `.venv`; run commands from the repository root.
2. Ensure local FoundationDB is reachable using `.foundationdb/docker.cluster` and the Python FoundationDB client is installed. The smoke cluster expects FDB to exist; the crash runner can start/reuse the **known local Docker FDB coordinator** when its cluster file has the supported `docker:docker@127.0.0.1:<port>` format. Neither lab should reset an existing database. Do not substitute a production FDB cluster file.
3. Keep the development Hubs off ports **8870–8872**. If Main Computer itself is needed, start it with the supported switch:

   ```powershell
   .\start.bat --no-dev-hub
   ```

   The switch suppresses Main Computer's managed development Hub startup. It does **not** start the Stable Hub Lab. It does not deliberately tear down Docker Compose stacks. Do not run ordinary `start.bat` while the smoke cluster is using those ports.
4. Run the Stable smoke stress and crash fault campaigns **sequentially**, not simultaneously. They use separate HTTP ports and FDB namespaces but share a local FDB coordinator and machine resources. Start each phase with clean, identifiable Hub processes; never terminate an unknown process or delete FDB data to clear a port conflict.

### Optional: validate topology without starting anything

```powershell
python -m tools.stable_hub_lab.run_lab --topology deploy/hub-topology/smoke-topology.json --validate-only
```

The checked-in topology describes `main-computer-smoke-hub`, namespace `main-computer-hub-smoke`, three `smoke-hub*` identities, and loopback ports **8870–8872**. It is intentionally **not** the `main-computer-dev-stable-hub` cluster.

## Part A — Stable Hub smoke and integrated stress

### Terminal 1: start the cluster and leave it running

```powershell
python -m tools.stable_hub_lab.run_lab --topology deploy/hub-topology/smoke-topology.json --serve-cluster --check-after-start --startup-timeout 60
```

Do **not** close Terminal 1 after it says `Stable Hub cluster running. Press Ctrl+C to stop.` That process supervises all three smoke Hub child processes. `--check-after-start` verifies their identities and health before declaring the cluster ready.

**Required startup proof** (all three must succeed):

```text
Stable Hub cluster check: ok
  smoke-hub1 http://127.0.0.1:8870: ok cluster=main-computer-smoke-hub namespace=main-computer-hub-smoke ...
  smoke-hub2 http://127.0.0.1:8871: ok cluster=main-computer-smoke-hub namespace=main-computer-hub-smoke ...
  smoke-hub3 http://127.0.0.1:8872: ok cluster=main-computer-smoke-hub namespace=main-computer-hub-smoke ...
Stable Hub cluster running. Press Ctrl+C to stop.
```

### Terminal 2: independently verify the correct Hubs

```powershell
python -m tools.stable_hub_lab.run_lab --topology deploy/hub-topology/smoke-topology.json --check-cluster --startup-timeout 60
```

Before sending test traffic, confirm **all three** report `ok`, the right `smoke-hub1/2/3` identities, and `main-computer-smoke-hub`. A successful HTTP `/health` response from a *different* Hub is **not** an acceptable result.

### Terminal 2: run 25 integrated stress iterations

```powershell
python -m tools.stable_hub_lab.run_lab --topology deploy/hub-topology/smoke-topology.json --verify-stress --verify-stress-runs 25
```

The command exercises integrated market eligibility/rejection, Worker reconnection, session acceptance/completion/failure, reservations, charges, Worker earnings, and the lab's ledger invariants. It uses test actors and the smoke database namespace, **not** Captain's production/Testnet credit balance.

**Market isolation after the October 26 Worker-cycle regression:** Each stress
iteration now generates a unique `stable_stress_*` capability. Its signed
Worker offer advertises that capability, and *every* Captain request requires
it, including the deliberately underpriced rejection probe. On authentication,
the lab checks the Hub's echoed connection ID, wallet account, price, and
capabilities. Every accepted `hub.work.offer` must carry this exact request ID,
scenario capability, and the Hub-issued connection ID for the socket that
received it. This prevents an older, cheaper `python`/`echo` Worker left in the
durable smoke market from silently satisfying the test. It does **not** weaken
market eligibility or suppress a live-worker disconnect failure.

The local regression `test_integrated_stress_isolated_from_stale_cheaper_market_worker`
creates a lower-priced **live directory record with no socket** and verifies
that the new stress session still executes using its own authenticated Worker.
This regression is part of `--verify-worker-cycle-local` as well as the
integrated stress test suite.

**Pass criterion:** `Stable Hub integrated market stress runs: ok`, `Runs: 25/25 ok`, `all runs ok: yes`, `long-running stable golden path: yes`, and **zero invariant violations**. A successful 25-run campaign has produced an illustrative aggregate of 100 requests, 75 accepted sessions, 50 completed, 25 failed, 75 holds, 50 charges, and 50 Worker earnings. The exact counts depend on the implemented workload; use the tool's PASS and invariants as the authoritative gate.

For machine-readable evidence, append `--json`. Do not treat a failed prerequisite or an incomplete run as 25 successful iterations.

### October 2026 Worker functional-test regression gate

The browser-connected Testnet Worker functional test passed after discovering **two
independent failure classes**: an outdated 1.024-credit offer excluded the Worker
from a request capped at 1 credit (`worker_not_live`, HTTP 409), and a Captain
with 0.976 available credits could not reserve 1.024 credits (HTTP 402). The
Worker's successful 0.512-credit ACK cycle subsequently proved real execution.
Stable Hub Lab must preserve *all* of those distinctions rather than accepting
"WebSocket connected" as proof that paid work can proceed.

**Run the targeted offline regressions first (no smoke Hubs required):**

```powershell
python -m tools.stable_hub_lab.run_lab --verify-worker-cycle-local
```

This runs five tests: the real in-memory Hub HTTP/WebSocket market refresh,
price-ceiling rejection, successful completion, and disconnect-refund scenario;
the separate HTTP **402** insufficient-funds scenario (0.976 available versus
1.024 required) with no hold/charge/earning; the non-loopback/Testnet
fail-closed guard; and the atomic FoundationDB-store fault-injection regression
for Worker disconnect. Require `Stable Hub Worker cycle local regressions: ok`
and **5 passed**. The injected crash test checks rollback before commit through
an FDB-compatible test store; it is **not** an operating-system process kill.

**Then, with the smoke cluster still running in Terminal 1, run the live local
Hub campaign from Terminal 2:**

```powershell
python -m tools.stable_hub_lab.run_lab --topology deploy/hub-topology/smoke-topology.json --verify-worker-cycle --startup-timeout 20
```

`--verify-worker-cycle` automatically re-runs the four offline regressions
before sending any smoke-cluster traffic. A failed preflight prevents the live
campaign. It connects an actual WebSocket Worker and authenticated requester to
the **local** Hub network, using throwaway development wallets and a unique
market capability to avoid matching another Worker. It exercises, in order:

1. Hub acknowledgment of the original **1.024**-credit ring-3 offer; HTTP **409**
   for a 1-credit maximum, with no credit movement.
2. Worker socket closure and fresh authenticated registration **using the same
   wallet account** with a **different Hub-issued connection ID**, verifying
   the Hub's acknowledgment of the new **0.512**-credit offer.
3. HTTP **409** for an intentionally too-low 0.5-credit maximum; no credit
   movement. This proves price matching is a constraint, not a liveness test.
4. An exactly priced **0.512**-credit Captain request: offer on the correct
   Worker's socket, matching request/session/run IDs, successful Worker result,
   exactly one charged hold and one Worker earning.
5. A second accepted request that is deliberately abandoned by closing its
   Worker socket. Require terminal failure, released hold, **no second charge or
   earning**, zero additional held credits, and the same available/spent totals
   as immediately after the successful result.

The CLI prints a proof item per condition. **Every proof must be `yes`** and the
summary must say `Stable Hub Worker cycle regressions: ok`. Capture the JSON
result for review by adding `--json`; it includes request/session IDs and the
before/after account amounts. This campaign can expend **0.512 development
credits per invocation** and return 0.512 held credits for the abandoned
request. It does not spend or bridge real Testnet credits. It **refuses**
non-loopback Hub URLs or Testnet/Mainnet topology; never point it at deployed
Hubs. It requires at least 1.024 available *local dev credits* to exercise both
acceptances concurrently; a new local dev account receives the normal dev
starting balance when its first request is accepted. It does not auto-fund a
previously depleted lab wallet. If funds are insufficient, rotate to a new
throwaway **local dev** wallet rather than modifying the credit ledger.

These are independent from the existing 25-run `--verify-stress` campaign:
that stress exercises payout claim, settlement, bridge idempotency, reconnect
handoff, and general accounting; the new campaign specifically catches the
price/connection-registration, 402/409 distinction, and disconnect-held-credit
regressions uncovered by the browser Worker. **Run both**, not one instead of
the other.

### After the stress run

Keep Terminal 1 up only while additional **smoke-cluster** checks are needed. Stop it with **Ctrl+C** when finished and confirm its children exit. Do not mistake a subsequent `connection refused` for a regression if the cluster has intentionally been stopped.

## Part B — FoundationDB crash/failover campaign

Run this phase when the smoke stress phase is finished. The crash runner checks the Stable source fingerprint and runs its baseline tests, then launches **lab-owned** Hub processes on different ports with isolated FDB namespaces. It deliberately kills those processes; do not point it at deployed services. Its local-only policy refuses non-loopback addresses and unexpected port occupants. The supported local Docker FDB coordinator may be started/reused, never reset.

Each command below runs against the local `.foundationdb/docker.cluster` file. Execute **one command at a time**. A PASS in one mode does not certify another mode.

### 1. Baseline regression gate

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode baseline --cluster-file .foundationdb/docker.cluster
```

Expect `Stable/Experimental crash lab: PASS` and `phase: baseline`. This also runs automatically as a preflight for other modes.

### 2. Restart durability: 10 rounds per lane

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode restart --rounds 10 --cluster-file .foundationdb/docker.cluster
```

Verifies completed-session accounting is preserved over process restarts. This is **after completion**, not a crash during an unfinished Worker result.

### 3. Post-commit/in-flight fault injection: both hold and charge

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode inflight --fault both --rounds 10 --cluster-file .foundationdb/docker.cluster
```

`--fault both` exercises **reserve** and **charge** for each selected Hub lane. The crash occurs after the target FDB commit but before the handler responds. Expect PASS in each lane/fault entry; committed financial records must survive exactly once.

### 4. Precommit rollback: both hold and charge

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode precommit --fault both --rounds 10 --cluster-file .foundationdb/docker.cluster
```

The process is killed while the transaction is still active. The interrupted hold/charge and earnings must **not** appear as committed records after restart.

### 5. Claim and batch settlement idempotency

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode settlement --fault both --rounds 10 --cluster-file .foundationdb/docker.cluster
```

**Important:** Settlement requires `--fault claim`, `--fault batch`, or `--fault both`. Omitting it uses the generic default `charge` and fails **before** running settlement tests with `Settlement mode needs --fault claim, batch or both`. This is a command/configuration failure, **not** a payout failure. Successful settlement-fault runs verify durable, idempotent claim/batch obligations, **not an on-chain payout**.

### 6. Surviving-Hub failover

```powershell
python -m tools.hub_crash_lab.run_lab --hub both --mode failover --fault charge --rounds 10 --cluster-file .foundationdb/docker.cluster
```

After a charge commits, a peer Hub must read that record while the owner is still dead. The lab then restarts the owner and checks preservation. Optionally rerun with `--fault both` to include **reserve**; the Experimental legacy hold-list endpoint is unavailable, so reserve verification there uses direct FDB evidence rather than the same peer HTTP proof.

**PASS condition for each crash command:** `Stable/Experimental crash lab: PASS` and PASS for the requested Stable and Experimental lanes/faults. A partially completed round or a healthy restart alone is insufficient. If a mode fails, preserve its evidence and fix that failure before moving on to deployment decisions.

### Locate evidence

- Latest crash summary: `runtime/hub-crash-lab/last-run.json` (**overwritten** by each mode).
- Detailed per-run evidence: `runtime/hub-crash-lab/<run_id>/` (logs, trial markers, event reports, isolated topologies).
- Stable smoke verification results: displayed by `--verify-stress`, or available as JSON with `--json`. Keep the console output or save JSON before changing the test environment.
- Supporting implementation: `tools/stable_hub_lab/run_lab.py`, `tools/hub_crash_lab/run_lab.py` and `tools/hub_crash_lab/README.md`.

## Known failures and what to do

| Symptom | Meaning | Safe response |
| --- | --- | --- |
| `WinError 10061` posting to `127.0.0.1:8870` | `--verify-stress` connected to no listening Hub | Start `--serve-cluster` in **Terminal 1**, leave it running, check identities from Terminal 2, then rerun. |
| `smoke-hub2` identifies as `dev-hub1` / `main-computer-dev-stable-hub` | Another development Hub owns the expected port | Do not mix clusters. Start Main Computer with `--no-dev-hub`, stop only **known managed processes**, and restart/check the smoke cluster. Do not delete FDB data. |
| All three `/health` requests time out | Smoke Hubs are unavailable or unresponsive (not proof of an accounting regression) | Stop concurrent heavy lab workloads, inspect Terminal 1, then cleanly restart the **smoke lab** and rerun `--check-cluster`. |
| `bad_wallet_address` or `Paid overflow needs a valid connected wallet address...` during MSK test | Validation reached a route or implementation that rejected its request; previously observed with wrong-Hub routing | Check each Hub's `/api/hub/v1/hub-identity` via `--check-cluster` **before** changing wallet tests. If all identities pass and error persists, investigate the MSK handler. |
| `Settlement mode needs --fault claim, batch or both` | Incorrect CLI arguments; settlement did not run | Rerun with `--fault both`. |
| Crash lab port already occupied | A lab fault-injection port is not isolated | Identify the occupying process. Do not kill arbitrary processes; let the runner refuse instead of mixing services. |
| `worker_not_live` (409) while Worker socket is connected | The market may have a stale offer, insufficient ring/capability, or a price ceiling below the advertised amount | Run `--verify-worker-cycle`; inspect authenticated `hub.auth.accepted.market` **and** the Hub-issued connection ID, not just WebSocket state. |
| `worker_owner_not_local` (409), `worker live session closed before accepting work`, during 25-run stress | The Hub selected a market lease and attempted the live offer; the socket closed before acceptance. This is *not* the same branch as a missing local socket. Competing generic-capability records in the durable smoke market may have diverted selection, or the intended socket may really have terminated. | Apply the run-scoped-capability stress regression and rerun the **local smoke** campaign. If it recurs, do not reset FDB or broaden the price cap: inspect Terminal 1 `worker live-session ended` lines (connection ID, `last_message_type`, `reason`) and `worker_selection_*` lines; compare the selected connection ID and scenario capability against the request's authenticated Hub echo. A genuine intended-connection loss is a Hub liveness defect requiring further investigation. |
| `requester_funds_unavailable` (402) with an eligible Worker | Funds cannot be reserved even though market selection succeeded | Run `--verify-worker-cycle-local` for the insufficient-credit gate. Inspect actual available/held/spent records; do not refund without session evidence or raise an external bridge cap. |
| Worker reconnects but Hub echoes the previous offer | New UI settings were not republished at authenticated registration | Require the old/new price and distinct connection IDs in `--verify-worker-cycle`; fix publication/registration, not Captain price by guess. |
| Closed Worker still has an unfinished held-credit reservation | Disconnect settlement is missing, delayed, or failed | Require the abandoned-session terminal failure and released hold in `--verify-worker-cycle`, plus atomic precommit rollback in the local gate. |
| `worker_owner_not_local` with `worker live session closed before accepting work` during stress | Hub selected a local socket but it closed while awaiting acknowledgment; the failure may be genuine or caused by a client-side dispatch deadline, not the original stale-market selection issue | Check `work.offer.received` elapsed time and the connection ID in the error, compare against the smoke Hub log `worker live-session ended ... reason=...`; do not downgrade ownership checks. The stress harness now gives dispatch/HTTP timeouts independent of `--startup-timeout` and tests delayed market selection. |
| Stress PASS but accounting fix still uncertain | Golden-path tests may not cover a new crash boundary | Add an explicit crash injection at that boundary; do not overstate certification. |

## What a green lab does—and does not—certify

**Stress deadline regression (October 2026):** the integration harness formerly reused `--startup-timeout` (default 10 seconds) as both the Captain POST timeout and the Worker WebSocket offer-read timeout. That is not a valid bound for the full remote Hub selection/dispatch/offer handshake, and was equal to the Hub's own 10-second worker-acceptance window. A slow FDB operation could make the *test client* close first, yielding HTTP 409 `worker_owner_not_local`. The stress client now uses at least 30 seconds or three times the setup timeout for the dispatch/read and HTTP POST phases, checks for early Hub refusals every 250 milliseconds, and records `work.offer.received` elapsed time and the exact Hub-issued connection ID. The console summary now displays the longest offer wait, and an unsuccessful 25-run campaign identifies the failed run number. The in-memory delayed-selection regression forces dispatch past the shorter setup deadline without spending any Testnet credits. This prevents a lab-induced socket closure; it does **not** exempt any real Hub socket-lifecycle error from investigation. If a 409 persists, compare the failure's connection ID and elapsed time with the owner Hub's `worker live-session ended` log reason.

**Green Worker-cycle regression gate** supports the explicit offer/price refresh, market rejection, successful paid work, ordinary disconnect refund, and local precommit rollback checks listed above. It does **not** emulate browser wallet/RPC/MSK signing UX, assert deployed Hub version, or reconcile real Testnet credits. **Green smoke stress** supports normal integrated routing, market selection, session accounting, payout invariants, and Worker reconnection under the tested traffic. **Green crash modes** support only their listed commit/rollback/restart/failover boundaries, with the proof in each report.

**October 2026 disconnect/credit-hold change (current source):** `close_worker_connection_and_settle` now uses **one shared-store atomic transaction** to close the Worker owner and market record, fail unfinished accepted sessions, and release their unearned credit holds. `test_worker_close_and_hold_release_are_one_fdb_commit_with_crash_rollback` injects an exception after the transaction's writes but **before commit**, verifies no partial owner closure/hold release persisted, then verifies one committed cleanup and idempotent repetition. The new `--verify-worker-cycle` network campaign also observes an unfinished accepted session's release on ordinary WebSocket closure.

**Remaining limit:** the current crash lab modes do not inject a real process kill during this *specific* disconnect transaction, nor prove that a Hub killed **before it can begin any disconnect cleanup** will recover the orphaned live-session lease and release its hold after restart/failover. That is a separate recovery gate. Do not infer it from either the atomic unit test or 25/25 normal stress. A future process-kill scenario must use lab-owned Hubs/FDB namespace, distinguish precommit rollback from postcommit idempotence, and require terminal session/account reconciliation after restart without double charge or earning.

Finally, these local labs do **not** reconcile legacy Testnet credit holds, mutate a deployed Coolify Hub, certify cross-Hub takeover of unfinished Worker execution, or broadcast blockchain settlements. Deployment and existing-ledger repair require separate authorized procedures.

## Quick command checklist

1. If running the Main Computer app, use `.\start.bat --no-dev-hub`.
2. **Terminal 1:** `--serve-cluster --check-after-start --startup-timeout 60`; leave running.
3. **Terminal 2:** `--check-cluster --startup-timeout 60`; require all three smoke identities.
4. **Terminal 2:** `--verify-worker-cycle-local`; require 5/5, then `--verify-worker-cycle --startup-timeout 20`; require every named proof and successful economic cleanup.
5. **Terminal 2:** `--verify-stress --verify-stress-runs 25`; require 25/25 and zero invariant violations.
6. Stop smoke-cluster processes cleanly; run `baseline`, `restart`, `inflight --fault both`, `precommit --fault both`, `settlement --fault both`, `failover --fault charge` sequentially in the crash lab.
7. Review `last-run.json`, preserve per-run evidence, and verify **newly introduced fault boundaries** separately before deployment.
