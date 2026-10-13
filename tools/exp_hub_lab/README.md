# Experimental Hub Lab — Stable Hub parity

The **Experimental Hub Lab** now runs `main_computer.stable_hub` via the existing
`tools.stable_hub_lab.run_lab` implementation. The old experimental `exp-fdb-hub.py`
server and its protocol are **not** started by these commands. Historical
Experimental Hub test helpers remain available to regression tests, but no
financial rules or request handling are copied from them into this lab.

The default topology is `deploy/hub-topology/smoke-topology.json`, with three
loopback Hubs and a dedicated `main-computer-hub-smoke` FoundationDB namespace.
The lab runner refuses non-local URLs and non-dev/smoke network topologies;
it must never point at live mainnet or testnet services.

## Run

Use two terminals in the repository root, with the project's Python environment
and local FoundationDB cluster configured:

**Terminal 1 — serve real Stable Hub processes:**

```powershell
python -m tools.exp_hub_lab.run_lab --serve-cluster --check-after-start
```

**Terminal 2 — exercise the running Hubs:**

```powershell
python -m tools.exp_hub_lab.run_lab --check-cluster
python -m tools.exp_hub_lab.run_lab --verify-interfaces
python -m tools.exp_hub_lab.run_lab --verify
```

`--verify` runs Stable Hub's real wallet/MSK, worker WebSocket, owner handoff,
accepted work session, and continuation tests against the running smoke Hubs.
`--verify-interfaces` checks the real Stable Hub identity and topology and
read-only credit balance, charge history, bridge status, and Temporal health
endpoints, **without** completing deposits, sending transactions, or creating
paid requests. A missing bridge deployment is reported as
`bridge_configured=false`, not counted as a successful funded transaction.

**Offline Patch 1–3 regressions (no local Hub or real chain required):**

```powershell
python -m tools.exp_hub_lab.run_lab --verify-suite
```

This executes the actual Stable Hub atomic FoundationDB simulator, escrow
bridge adapter, Captain, optional Temporal, and Stable Hub lab tests. It is
not proof of live FoundationDB race safety, live escrow funding, or Temporal
server crash recovery. For longer live-session stress experiments, the Stable
Hub runner also accepts `--verify-stress`, though historical stress expectations
must be checked against the current credit-hold policy.

The old `--verify-full-e2e` Experimental Hub dev-chain settlement command is
**intentionally unsupported** by the Stable Hub lab. It would falsely equate
the retired Experimental Hub payout protocol with the present Stable Hub's
incomplete real-chain refund pathway. That safety boundary remains unchanged.

For structured output, pass `--json` to checks or the offline suite. The
command's exit status is nonzero on failure.

## Actual Experimental Hub handoff parity lane (P0 / P1)

The default commands above deliberately run **Stable Hub**. To compare the
real Experimental Hub implementation against it, launch a **second** three-Hub
cluster in another terminal. The Experimental lane uses localhost ports
**8970, 8971, 8972**, namespace `main-computer-exp-handoff-parity`, and
**mock-chain** credit issuance. The Stable Hub retains 8870–8872 and its own
isolated FDB namespace. No development Hub shutdown or blockchain transfers
are required.

```powershell
# Terminal 1: keep the Stable Hub cluster running
python -m tools.exp_hub_lab.run_lab --serve-cluster --check-after-start

# Terminal 2: run the REAL Experimental Hub HTTP server in an isolated lane
python -m tools.exp_hub_lab.run_lab --serve-exp-cluster

# Terminal 3: measure the two implementations separately
python -m tools.exp_hub_lab.run_lab --verify
python -m tools.exp_hub_lab.run_lab --verify-exp-handoff
python -m tools.exp_hub_lab.run_lab --compare-handoff
```

The Experimental lane now **requires wallet multi-session-key authorization**.
Stop any older servers on 8970–8972 and restart the three-Hub cluster after
applying the P1 patch; verification fails closed against optional-auth servers.
The Experimental verifier issues **mock** credits inside its isolated namespace
and exercises the real Experimental Hub server: a worker WebSocket connects to
Hub C, work requests enter A/B/C, remote requests forward to C, and the
requester follows C's continuation URL. The comparison fails if either actual
runtime fails. It compares **handoff behavior**, not equivalence of funding,
payment timing, Temporal durability, or on-chain settlement.

Authenticated cross-Hub reconnection and stale-owner lease fencing have
separate two-Hub local HTTP/in-memory regression tests in
`tests/test_exp_hub_live_session_keepalive.py`. P1's real Experimental lane
requires signed worker and requester MSKs on all three Hubs; the acceptance
runner refuses to claim parity when optional authentication is configured.

## P1: Actual Experimental Hub concurrent accounting stress

Keep `--serve-exp-cluster` running on **8970–8972**. Run the real
Experimental Hub (not the Stable Lab alias) with ten independent scenarios,
three in flight at a time:

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-stress --timeout 30
```

For a short first probe or structured evidence:

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-stress --verify-exp-stress-runs 1 --verify-exp-stress-parallel 1 --json
python -m tools.exp_hub_lab.run_lab --verify-exp-stress --verify-exp-stress-runs 10 --verify-exp-stress-parallel 3 --timeout 30 --json
```

Each scenario creates an isolated funded requester account and an authenticated
worker with a unique market capability. Three requests enter different Hubs.
The worker first connects to Hub C and then reconnects to Hub A using the
same signed wallet/MSK. The verifier checks owner-Hub continuation, a repeated
successful result, an injected failure followed by a late success, financial
holds/charges/releases, and no remaining hold after the scenario. Scenario
threads run concurrently; requests within each scenario run sequentially to
permit exact before/after accounting proof. Failures are retained in the
per-run JSON report and cause a nonzero exit code.

This test uses the real three Experimental processes and their FoundationDB
namespace when run on the workstation. It requires strict requester/worker MSKs
and rejects any cluster advertising optional authentication.

**P1 in-flight disconnect and retry acceptance (separate command):**

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-recovery --timeout 30
```

The recovery run accepts work with an outstanding credit hold, drops the
worker WebSocket **before any terminal message**, checks that the hold is
released without a charge, retries the same request against another Hub and
checks the session ID is unchanged, then reconnects the authenticated worker
on a different owner Hub and completes the next request. The worker also
reconnects on the new Hub *before* the old socket closes, proving the old owner
cannot clear the newer lease epoch. It fails closed if any invariant is violated. It does not claim to simulate a process crash,
FoundationDB coordinator outage, blockchain settlement, or Temporal recovery;
those remain P2. The stale-owner epoch fencing regression must also pass.

To run the local proof and regression checks:

```powershell
python -m pytest -q tests/test_exp_hub_lab_stress_p1.py tests/test_exp_fdb_request_claim_p1.py tests/test_exp_hub_lab_dual_runtime_handoff.py tests/test_exp_hub_live_session_keepalive.py
```

## P2-A: Experimental Temporal supervision (opt-in)

Experimental now uses **the same** `StableHubTemporalRuntime` and
`WorkSessionWorkflow` as Stable; its accepted-session directory is the FDB
outbox. Temporal never dispatches paid worker offers, modifies credits, or
replays inference. This is **P2-A only**; blockchain payout/settlement changes
belong to P2-B.

Temporal is disabled by default. The Experimental runtime is enabled only when
`MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS` is set; it does **not** inherit the Stable
Hub's `MAIN_COMPUTER_STABLE_TEMPORAL_ADDRESS`. All Experimental Hubs in the
three-Hub cluster should have the same environment setting. Ensure the Temporal
server has a registered namespace **`main-computer-exp-handoff-parity`** for the
isolated `--serve-exp-cluster` profile; it is derived from the network key. The
`temporalio` Python SDK is required in the Hub process. If the Temporal SDK,
server, namespace, or reconciling worker is unavailable, the Experimental owner
Hub returns **503 `temporal_unavailable` for new paid work**, before creating a
new request, a hold, an accepted session or an offer. Idempotent reads/retries
of existing accepted sessions remain permitted. `/health` reports Temporal
configuration/readiness independently on each Hub.

```powershell
# In the terminal used to start Experimental. First start Temporal and ensure
# its namespace exists; an unregistered namespace must fail closed.
$env:MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS = '127.0.0.1:7233'
python -m tools.exp_hub_lab.run_lab --serve-exp-cluster
```

Generate *new* paid work with the standard P1 verifier; record one terminal
session ID from `requests[*].response.session_id` in its `--json` output:

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-handoff --json
```

Once the session has become terminal **and** its Temporal workflow has reached
COMPLETED, capture a read-only FDB/Temporal baseline:

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-temporal `
  --temporal-address 127.0.0.1:7233 `
  --temporal-session-id '<session_id>' `
  --temporal-checkpoint runtime/exp-hub-lab/p2a-temporal-checkpoint.json `
  --json
```

Stop and restart the Experimental Hub process (and optionally the Temporal
worker/server) against the **same FDB namespace and Temporal namespace**.
Do **not** delete the checkpoint. Repeat the exact command. The first run
returns `baseline-created`; the second returns `post-restart-compared` only
if FDB's terminal accepted-session record is identical and Temporal still
reports a completed workflow. A failure is nonzero. Do not use this check on
an unfinished session. This probe is read-only; it does not independently
verify the Experimental credit ledger or prove every process-kill timing.

Then repeat `--verify-exp-stress` and `--verify-exp-recovery` to confirm P1
remains intact. The focused offline P2-A tests can be run with:

```powershell
python -m pytest -q tests/test_exp_hub_temporal_p2a.py
```

When Temporal is disabled, ordinary Experimental P1 work continues unchanged.
P2-A does **not** introduce a new financial recovery authority: a disconnected
worker's unfinalized reserved hold is still owned by the existing Hub accounting
paths, not by Temporal.

## Integrated Experimental Docker lab (P1 protocol + P2-A baseline)

The original `main_computer.exp_fdb_hub.serve_exp_fdb_hubs()` still owns the
complete startup/shutdown of the Experimental Hubs and the **existing** Docker
Compose `worker-lab` service. `--serve-exp-docker-lab` reuses this orchestrator;
it does **not** create an alternate Hub implementation, a competing ledger, or
a different Docker service.

Start the local FoundationDB smoke container as usual and start **persistent**
Temporal with the repository launcher (do not replace it with the fake-token
scheduler queue):

```powershell
python -m tools.temporal_lab.local_temporal up `
  --namespace main-computer-exp-handoff-parity `
  --persist
$env:MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS = '127.0.0.1:7233'
python -m tools.exp_hub_lab.run_lab --serve-exp-docker-lab `
  --docker-workers 10 --docker-requesters 2 `
  --docker-live-concurrency 2
```

The command starts Hubs 8970/8971/8972 in the isolated Experimental FDB
namespace with **strict wallet/MSK authorization** and mock-chain credits,
then starts the existing Compose worker-lab image. Docker reaches the Windows
host over `host.docker.internal`; the Windows-hosted Hubs keep 127.0.0.1
owner-Hub URLs in FDB. Only the Docker test client's network transport maps
that host-loopback URL to the Docker gateway. Running without Docker does not
alter existing P1 verifier URLs.

**Development security boundary:** The integrated mode explicitly binds the
three Hub listeners on `0.0.0.0`, because Docker Desktop host-gateway forwarding
may not reach a listener bound exclusively to host loopback. Use only on an
isolated development machine/network, with host firewall rules blocking
untrusted inbound access to ports 8970–8972. Never use this mode with real
funded networks or expose it over a public interface. Strict MSK auth remains
mandatory. The default `--serve-exp-cluster` stays loopback-only.

`live_session_v1` replaces legacy REST worker polling with the P1-tested
WebSocket `worker.auth`/offer/accept/result/fail/pong protocol and wallet-MSK
signed paid HTTP requests. The existing worker/requester **node list generator**
provides the cohorts and deterministic roster. Each generated worker runs one
independent, strictly authenticated, three-request P1 verification scenario:
success, failure, retry/replay, reconnection and financial-hold proof; distinct
requester accounts are issued for every scenario. The number of in-flight
scenarios is controlled by `--docker-live-concurrency`. This is an integrated
correctness load lane, **not** the old long-lived 120-process timing simulation:
the legacy distribution-specific timing and arbitrary faults are not represented
by this mode, and `--docker-duration-seconds` does not bound it. The legacy
`worker_pull_v0` mode remains opt-in for historical comparisons and must never
count as modern P1/P2-A acceptance.

Artifacts are retained under `runtime/scheduler-lab/exp-fdb/`:

- `<run-id>-live-nodes.jsonl`: generated node roster, no private keys
- `<run-id>-live-events.jsonl`: authenticated scenarios, proof bits, session IDs
- `<run-id>-live-summary.json`: totals, failures, zero-hold/invariant gate
- `<run-id>-p2a-temporal-checkpoint.json`: read-only baseline if Temporal is configured

A scenario missing a required proof or a failed Temporal/FDB preflight makes
the Docker process fail. The parent Hub launcher propagates its nonzero exit
status, shuts down the lab Hubs, and leaves FDB and persistent Temporal intact.

If the configured Temporal supervisor is healthy, the parent also checks the
first completed session against **actual** FDB and a completed Temporal workflow
and creates a P2-A checkpoint before shutting down. This is *baseline creation*,
not restart acceptance. To prove restart durability, leave the persistent
Temporal volume and FDB untouched, bring up `--serve-exp-cluster` with the
same `MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS`, then run:

```powershell
python -m tools.exp_hub_lab.run_lab --verify-exp-temporal `
  --temporal-address 127.0.0.1:7233 `
  --temporal-session-id '<first session_id in the live-events JSONL>' `
  --temporal-checkpoint '<matching run-id>-p2a-temporal-checkpoint.json' --json
```

`post-restart-compared` is the only acceptable P2-A restart result. For genuine
Temporal-server crash recovery, restart the persistent Temporal container as
well before that comparison. Do **not** remove its Docker volume.

## P2-B: opt-in local development blockchain (`--live-chain`)

The real Experimental Hub parity lane continues to run its **mock** bridge by
**default**. `--live-chain` is supported only when starting the Experimental
three-Hub cluster (`--serve-exp-cluster`) or the existing integrated Docker lab
(`--serve-exp-docker-lab`). It is not a production network flag. It reads the
existing private `runtime/deployments/dev/latest.json` deployment manifest and
requires `environment=dev`, a **loopback** host RPC, the deployed
`HubCreditBridgeEscrow` and `XLagBridgeReserve` contracts with bytecode on the
expected chain, and smoke signer metadata. An explicit RPC override is possible
using `--live-chain-rpc http://127.0.0.1:<port>`. The preflight fails **before**
starting paid Hub work if the chain, RPC, or contract verification fails.

Start your **existing local dev blockchain**, FoundationDB and persistent
Temporal first. Stop any earlier Experimental Hubs on 8970–8972, then run:

```powershell
$env:MAIN_COMPUTER_EXP_TEMPORAL_ADDRESS = '127.0.0.1:7233'
python -m tools.exp_hub_lab.run_lab `
  --serve-exp-docker-lab `
  --live-chain `
  --docker-workers 2 `
  --docker-requesters 1 `
  --docker-live-concurrency 1
```

The original launcher brings up three Experimental Hubs and its Docker worker
lab. In this opt-in lane, the Hubs use the actual dev-chain bridge backend,
with the host-reachable RPC rather than the deployment's Docker-only RPC name.
Once authenticated Docker scenarios pass, the same running Hubs execute the
existing real-chain end-to-end verifier: another signed worker/requester
handoff, an FDB worker claim and settlement batch, a real
`XLagBridgeReserve` payout transaction, a verified receipt and a repeated
idempotent FDB chain-execution record. The full proof is written to
`runtime/scheduler-lab/exp-fdb/<run-id>-p2b-live-chain-report.json`. The command
returns nonzero if **any** stage fails. Normal no-flag operation does **not**
write to the blockchain.

```powershell
# Same three Experimental Hubs, without Docker-generated work or auto-payout:
python -m tools.exp_hub_lab.run_lab --serve-exp-cluster --live-chain
```

**Scope and limitations:** The Docker P1 scenarios still use their existing
isolated FDB test-credit issuance. The follow-up real-chain verification runs
its own authenticated scenario and executes a real **outgoing** dev-chain
payout. Accordingly, this is not yet end-to-end on-chain funding of each
Docker requester and it is not production settlement safety. In particular,
settlement obligations are not yet made exclusive across all payout rails,
and interrupted on-chain submission/unknown-receipt reconciliation is still
P2-B work. This flag **never** authorizes a remote/testnet/mainnet RPC, and it
must not be used with live funds. Do not interpret a green startup preflight
alone as evidence of payout correctness; inspect the P2-B report.

The current P1 signed-authentication test helpers use chain ID **42424242**.
The live-chain preflight requires this value until P1 MSK helpers support a
configurable dev-chain identity.
