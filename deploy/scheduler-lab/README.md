# Scheduler / Hub Worker Lab Container

This directory is the manual testing container for the experimental Hub/FDB path.

It does **not** start or replace the normal Hub. It runs simulated workers and requesters
that call a Hub over HTTP. For the FDB experiment, point it at the manually started
`exp-fdb-hub.py`.

## Intended local topology

```text
local FoundationDB container
  -> exp-fdb-hub.py on the host
  -> scheduler-lab worker/requester container
```

The lab traffic uses Hub HTTP endpoints. The fake workers and requesters do not talk
to FoundationDB directly.

## Start the local FDB container

From the repository root:

```bash
python scripts/smoke_foundationdb_credit_ledger_primitives.py --keep-container
```

That smoke is expected to leave `.foundationdb/docker.cluster` available for the
manual experimental Hub.

## Start the manual experimental Hub

Use a host bind address reachable from Docker. On Linux, a service bound only to
`127.0.0.1` may not be reachable through `host.docker.internal`, so bind to
`0.0.0.0` for the lab run:

```bash
python exp-fdb-hub.py \
  --host 0.0.0.0 \
  --port 8870 \
  --cluster-file .foundationdb/docker.cluster \
  --namespace main-computer-exp-fdb
```

## Run the worker/requester lab container

```bash
docker compose -f deploy/scheduler-lab/docker-compose.worker-lab.yml --profile worker-lab up --build
```

By default this generates `/lab-output/120-First-Post.jsonl` inside the container,
which means 120 node rows: 100 workers and 20 requesters.

The host-side output appears in:

```text
deploy/scheduler-lab/output/
```

Useful output files:

```text
scheduler-lab-events.jsonl
scheduler-lab-summary.json
120-First-Post.jsonl
```

## Run only workers or only requesters

```bash
LAB_ROLE=workers docker compose -f deploy/scheduler-lab/docker-compose.worker-lab.yml --profile worker-lab up --build
LAB_ROLE=requesters docker compose -f deploy/scheduler-lab/docker-compose.worker-lab.yml --profile worker-lab up --build
```

## Generate a node grid without running the lab

```bash
python -m tools.scheduler_lab.smoke_hub_lab_node_list_builder deploy/scheduler-lab/output/120-First-Post.jsonl
python -m tools.scheduler_lab.smoke_hub_lab_node_list_builder deploy/scheduler-lab/output/120-First-Post.html
python -m tools.scheduler_lab.smoke_hub_lab_node_list_builder deploy/scheduler-lab/output/120-First-Post.csv
```

The leading integer in the filename determines total node rows. For example,
`240-scale-test.jsonl` creates 240 nodes, preserving the default 5:1 worker to
requester ratio unless `--workers` or `--requesters` is supplied.

## Notes about the first version

The first simulator is deliberately HTTP-only and standard-library-only.

It supports the current worker-pull endpoints:

```text
POST /api/hub/v1/workers/register
POST /api/hub/v1/workers/heartbeat
POST /api/hub/v1/workers/poll
POST /api/hub/v1/workers/results
POST /api/hub/v1/requests
```

When `REQUEST_MODE=worker_pull_v0`, the current Hub requires funded/holdable
request accounts. If the experimental Hub has not implemented or pre-funded the
test accounts yet, requester submissions may fail while worker registration and
heartbeat pressure still run. That is expected for early container wiring.

Use `REQUEST_MODE=registration_only` when you only want worker registration,
heartbeat, and poll pressure without requester submissions.
