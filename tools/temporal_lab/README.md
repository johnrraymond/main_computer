# Local Temporal bootstrap

This folder prepares the scheduler-lab Temporal replacement experiments.

The first script only starts a local Temporal development server in its own
Docker container and verifies that it is ready. It does not replace the current
scheduler-lab runner yet.


## Stage 1 fake-token scheduler lab

The Stage 1 lab adds a parallel Temporal workflow path without replacing the
current hub worker endpoints yet. It proves the next scheduler shape:

- requester creates a fake-token job
- requester offers credits for that job
- the price/ring catalog derives the worker-pool ring and Temporal task queue
- worker nodes choose which ring `0..3` they connect to
- activity emits fake tokens over time
- JSONL evidence is written locally for inspection

Install the Temporal Python SDK for the live worker/requester commands:

```bash
python -m pip install -r tools/temporal_lab/requirements-temporal.txt
```

### Credits-on-offer ring routing

Requester business input is only the credits on offer. The requester does not
choose a partition and does not supply a separate required-credit value.

Worker/node input is the ring it wants to serve:

```text
ring 0
ring 1
ring 2
ring 3
```

Ring IDs are opaque pool labels. Do not infer price order from the numbers. The
active price catalog defines each ring's `credits_per_token` and `service_rank`.
Routing chooses the highest `service_rank` that the offered credits can afford.

The local demo catalog is intentionally explicit:

| Ring | Demo label | Credits per token | Service rank | Task queue |
| --- | --- | ---: | ---: | --- |
| `3` | base/free | `0` | `0` | `scheduler-lab-fake-tokens-ring-3` |
| `2` | standard | `1` | `1` | `scheduler-lab-fake-tokens-ring-2` |
| `0` | elevated | `2` | `2` | `scheduler-lab-fake-tokens-ring-0` |
| `1` | top | `4` | `3` | `scheduler-lab-fake-tokens-ring-1` |

For a five-token request, the default demo routing is:

| Credits offered | Routed ring |
| ---: | --- |
| `0` | `3` |
| `5` | `2` |
| `10` | `0` |
| `20` | `1` |

This is just the lab catalog. A later price-set patch can externalize these
entries into FDB or another catalog source.

### Run one worker and one request

Terminal 1:

```bash
python -m tools.temporal_lab.local_temporal up --pull
```

Terminal 2, matching a five-token request with `--credits-offered 5`:

```bash
python -m tools.temporal_lab.worker --ring 2
```

Terminal 3:

```bash
python -m tools.temporal_lab.requester \
  --request-id req-001 \
  --account-id account-local \
  --token-count 5 \
  --token-interval-seconds 1 \
  --credits-offered 5
```

The default event log is:

```text
runtime/temporal_lab/events.jsonl
```

A successful run writes events shaped like:

```json
{"event":"start","request_id":"req-001","ring":2,"partition":"ring-2","worker_id":"worker-pid-1234"}
{"event":"token","request_id":"req-001","ring":2,"seq":1,"text":"tok-001"}
{"event":"progress","request_id":"req-001","ring":2,"tokens":1}
{"event":"done","request_id":"req-001","ring":2,"tokens":5,"result":{"ok":true}}
```

### Promotion smoke

Start workers for all demo rings:

```bash
python -m tools.temporal_lab.worker --ring 3
python -m tools.temporal_lab.worker --ring 2
python -m tools.temporal_lab.worker --ring 0
python -m tools.temporal_lab.worker --ring 1
```

Then vary only the credits on offer:

```bash
python -m tools.temporal_lab.requester --request-id req-free --token-count 5 --credits-offered 0
python -m tools.temporal_lab.requester --request-id req-standard --token-count 5 --credits-offered 5
python -m tools.temporal_lab.requester --request-id req-elevated --token-count 5 --credits-offered 10
python -m tools.temporal_lab.requester --request-id req-top --token-count 5 --credits-offered 20
```

### Run a small multi-ring lab

```bash
python -m tools.temporal_lab.run_lab \
  --rings 3,2,0,1 \
  --requests 6 \
  --token-count 3 \
  --token-interval-seconds 1 \
  --credits-offered 3
```

### Protected Temporal bridge-credit flow smoke

After the protected bridge-credit pretest passes, run the end-to-end protected
Temporal flow smoke. It connects the public request/ring decision to a real
`HubCreditLedger` hold and then settles/releases that hold based on workflow
outcome.

Start Temporal first:

```bash
python -m tools.temporal_lab.local_temporal up --pull
```

Then run:

```bash
python scripts/smoke_protected_temporal_flow.py
```

The smoke performs two protected requests:

- success path: requester offers credits, catalog selects a ring, protected
  bridge-credit hold is created, Temporal fake-token workflow completes, and
  the hold is charged.
- clean failure path: requester offers credits, catalog selects a ring,
  protected bridge-credit hold is created, the fake-token activity returns a
  business failure result, and the hold is released without printing an
  expected traceback.

Bare live smoke uses a unique per-run task queue derived from the selected ring.
That keeps stale retries from older shared-queue smoke runs out of the normal
output while still using Temporal workflow and worker infrastructure. To force
the old hard exception path, opt in explicitly:

```bash
python scripts/smoke_protected_temporal_flow.py --exercise-temporal-exception-path
```

For dependency-light local checks without a live Temporal server:

```bash
python scripts/smoke_protected_temporal_flow.py --execution-mode direct-activity
```

The report is written to:

```text
runtime/temporal_lab/protected_temporal_flow_report.json
```


### Fast non-Docker contract checks

These tests do not require a live Temporal server or the Temporal SDK:

```bash
python -m pytest -q \
  tests/test_temporal_lab_local.py \
  tests/test_temporal_lab_models.py \
  tests/test_temporal_lab_event_log.py \
  tests/test_temporal_lab_workflow_contract.py
```


## Start Temporal locally

```bash
python -m tools.temporal_lab.local_temporal up --pull
```

Defaults:

- container: `main-computer-temporal-dev`
- image: `temporalio/temporal:latest`
- gRPC frontend: `localhost:7233`
- Web UI: `http://localhost:8233`
- namespace: `scheduler-lab`
- task queue hint: `scheduler-lab-fake-tokens`
- storage: in-memory Temporal dev-server SQLite
- public exposure: none; ports bind to `127.0.0.1`

The bootstrap intentionally uses Temporal's in-memory dev server by default.
This is the safest local path on Docker Desktop/Windows. The persisted SQLite
dev-server mode can fail before Temporal starts with:

```text
unable to create SQLite admin DB: unable to open database file: out of memory (14)
```

## Recover from a failed older bootstrap

If a previous run left behind a stopped container created with persisted
SQLite, the patched `up` command recreates it automatically. You can also clean
it manually:

```bash
docker rm -f main-computer-temporal-dev
docker volume rm -f main-computer-temporal-dev-data
python -m tools.temporal_lab.local_temporal up --pull
```

## Check status

```bash
python -m tools.temporal_lab.local_temporal status
```

## Print worker/requester environment values

```bash
python -m tools.temporal_lab.local_temporal env
```

The script prints both shell and PowerShell environment commands:

```bash
export TEMPORAL_ADDRESS="localhost:7233"
export TEMPORAL_NAMESPACE="scheduler-lab"
export TEMPORAL_TASK_QUEUE="scheduler-lab-fake-tokens"
```

## View logs

```bash
python -m tools.temporal_lab.local_temporal logs --follow
```

## Stop Temporal

```bash
python -m tools.temporal_lab.local_temporal down
```

To remove any optional persisted local Temporal SQLite state too:

```bash
python -m tools.temporal_lab.local_temporal down --delete-data
```

## Optional persisted dev state

Persistence is off by default. If you need the local dev-server state to
survive container removal, opt in explicitly:

```bash
python -m tools.temporal_lab.local_temporal up --persist
```

With `--persist`, the script mounts:

- Docker volume: `main-computer-temporal-dev-data`
- container database file: `/data/temporal.db`

If persisted mode fails on your machine, remove the container and rerun without
`--persist`.

## Useful overrides

```bash
python -m tools.temporal_lab.local_temporal up \
  --container-name main-computer-temporal-dev \
  --namespace scheduler-lab \
  --task-queue scheduler-lab-fake-tokens \
  --grpc-port 7233 \
  --ui-port 8233
```

Use `--public-bind` only in an isolated local network. The default bind is
localhost-only.

## After reboot: bring the local test lab back up

Run these from the repository root in PowerShell after Docker Desktop is up and
your virtualenv is active:

```powershell
.\.venv\Scripts\Activate.ps1

python -m tools.temporal_lab.local_temporal up --pull
python -m tools.temporal_lab.local_temporal status

python .\scripts\smoke_foundationdb_credit_ledger_primitives.py --keep-container
```

The Temporal command starts the local dev server on `localhost:7233`. The
FoundationDB smoke starts or refreshes the `main-computer-foundationdb-smoke`
Docker container on port `4550`, writes `.foundationdb/docker.cluster`, verifies
the host Python client can talk to it, and leaves the container running for the
Hub/FDB smokes.

Then run the desired acceptance smoke:

```powershell
python .\scripts\smoke_temporal_fdb_hub_node_market.py
python .\scripts\smoke_temporal_fdb_hub_multi_hub.py
```

The Hub-backed smokes auto-start `exp-fdb-hub.py` by default. The multi-Hub
smoke needs fresh Hub ports; if `8870` or `8871` is already occupied by a stale
Hub from an earlier run, stop that process or pass alternate ports.

When a lab dependency is missing, the smokes now print the relevant bring-up
commands instead of failing with only a low-level connection error.

## Protected Temporal + FDB node-market smoke

After the clean protected Temporal flow is passing, the next golden-path smoke
models a small scheduler market with worker-specific Temporal task queues:

```powershell
python .\scripts\smoke_temporal_fdb_node_market.py
```

Bare mode uses the experimental FoundationDB credit ledger/registry, live
Temporal, and 50 simulated worker nodes. It now emits progress lines to stdout
as each long-running phase starts and completes, including FDB bootstrap,
worker registration, Temporal worker startup, workflow progress, settlement,
and report writing. Each node registers a ring, advertised price, task queue,
and keepalive. The hub-side smoke filters workers with:

```text
worker.ring <= requester.requested_ring
worker.price_credits <= requester.max_price_credits
```

Then it leases the next eligible worker, creates a protected credit hold, starts
the workflow on that worker's task queue, observes the fake token stream, and
charges the hold on success.

The default scenario intentionally demonstrates a ring-2 request being serviced
by ring-1 workers when their advertised price fits the request offer.

Use `--quiet` to suppress progress lines when you only want the final summary.

The smoke now actively prepares FoundationDB before worker registration. A cluster file is only treated as a hint: the smoke probes FDB with a real ledger/registry operation. If the file is missing or the existing FDB server is stale/unhealthy, bare mode runs the existing primitive FDB bring-up smoke automatically:

```powershell
python .\scripts\smoke_foundationdb_credit_ledger_primitives.py --keep-container
```

You can control that behavior:

```powershell
python .\scripts\smoke_temporal_fdb_node_market.py --fdb-start-mode auto
python .\scripts\smoke_temporal_fdb_node_market.py --fdb-start-mode always
python .\scripts\smoke_temporal_fdb_node_market.py --fdb-start-mode never
```

`auto` is the default: use healthy FDB if available, otherwise start/refresh it through the existing smoke helper and re-probe. `always` runs the helper before probing. `never` only probes and fails if FDB is unavailable. The default per-operation storage bound is 15 seconds:

```powershell
python .\scripts\smoke_temporal_fdb_node_market.py --storage-operation-timeout-seconds 15
```

Use `0` only when you deliberately want unbounded FDB client waits while debugging.

For dependency-light local checks without FDB or Temporal:

```powershell
python .\scripts\smoke_temporal_fdb_node_market.py `
  --execution-mode direct-activity `
  --ledger-backend json `
  --nodes 10 `
  --requests 3 `
  --token-interval-seconds 0
```

## Hub-backed Temporal + FDB node-market smoke

The lower-level smoke above proves the direct Temporal/FDB registry and ledger path. The Hub-backed smoke drives the requester/worker lifecycle through the Hub HTTP API instead:

```powershell
python .\scripts\smoke_temporal_fdb_hub_node_market.py
```

By default the smoke targets the experimental FDB Hub port `http://127.0.0.1:8870` and auto-starts `exp-fdb-hub.py` when nothing is listening there. It passes a run-specific FDB namespace and hub runtime directory so the local acceptance run is isolated. Use `--hub-start-mode never` only when you want to start the Hub manually; the failure message will print the exact `python exp-fdb-hub.py ...` command to run.

It fails early unless `/api/hub/v1/status` and `/api/hub/v1/credits` report FoundationDB backends. The smoke registers workers through the Hub, funds the requester by minting mock chain-lite funds, confirming a bridge deposit into Hub credits, quotes/submits requester jobs through the Hub, polls leases as workers, executes the fake-token Temporal work on worker-specific task queues, posts results back to the Hub, and verifies charges, worker earnings, duplicate result idempotency, request events, worker distribution, and a mock chain-lite payout that locks one worker wallet while value leaves the Hub bridge/escrow.

The bridge/payout portion is deliberately chain-shaped but not a real chain integration yet. It exercises these mock-chain Hub endpoints against the experimental FDB credit ledger:

```text
POST /api/hub/v1/bridge/mock-chain/mint
POST /api/hub/v1/bridge/deposits
POST /api/hub/v1/bridge/deposits/confirm
POST /api/hub/v1/bridge/payouts
POST /api/hub/v1/bridge/payouts/confirm
GET  /api/hub/v1/bridge/mock-chain/wallets
GET  /api/hub/v1/bridge/wallet-locks
GET  /api/hub/v1/bridge/audit
```

The intent is to keep normal request settlement on fast Hub bridged credits while modeling the bridge boundary where deposits enter Hub escrow and payouts move value back to the chain-side wallet. A later dev-chain backend can replace the mock-chain calls without changing the golden-path shape.


For a dependency-light Hub contract pass against a local JSON Hub, use direct activity and explicitly allow the non-FDB backend:

```powershell
python .\scripts\smoke_temporal_fdb_hub_node_market.py `
  --hub-url http://127.0.0.1:8000 `
  --execution-mode direct-activity `
  --allow-json-hub `
  --node-count 10 `
  --request-count 3 `
  --token-interval-seconds 0
```

## Multi-Hub shared-state smoke

The multi-Hub smoke starts two `exp-fdb-hub.py` processes against the same run-specific FoundationDB namespace. It is meant to verify the real control-plane model: multiple Hub frontends absorb requester/worker chatter while FoundationDB remains the source of truth for quotes, requests, leases, wallet locks, bridge accounting, audit events, and idempotency.

```powershell
python .\scripts\smoke_temporal_fdb_hub_multi_hub.py
```

By default Hub A uses `http://127.0.0.1:8870` and Hub B uses `http://127.0.0.1:8871`. In `auto` mode both ports must be free so the smoke can start known-good Hub processes with the same namespace. If either port is already listening, stop the existing Hub or pass different ports:

```powershell
python .\scripts\smoke_temporal_fdb_hub_multi_hub.py `
  --hub-a-url http://127.0.0.1:8872 `
  --hub-b-url http://127.0.0.1:8873
```

The smoke verifies these cross-Hub invariants:

```text
workers register through both Hubs
quotes and submits cross Hubs
workers poll leases through both Hubs
results complete through both Hubs
duplicate result replay through the opposite Hub does not charge twice
a payout request through Hub A is rejected while Hub B has active leased work
a wallet lock created by Hub A is visible to Hub B
Hub B excludes the locked wallet from a new quote
Hub B can confirm the payout created by Hub A
Hub A can be stopped and Hub B can still read final FDB-backed state
bridge audit readback and reconciliation still pass
```

This does not make Hub process memory authoritative. The safe production shape remains: all money, locks, leases, request state, and idempotency decisions live in shared durable state, while Hub processes act as stateless HTTP/control-plane frontends.

