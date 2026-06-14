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
- failure path: requester offers credits, catalog selects a ring, protected
  bridge-credit hold is created, Temporal workflow is intentionally failed, and
  the hold is released.

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
