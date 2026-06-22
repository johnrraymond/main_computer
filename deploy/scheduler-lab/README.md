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

## Run the integrated Hub-earned payout e2e smoke

Use this mode when the goal is to prove the real Hub payout lifecycle while the
Docker scheduler lab is active:

```text
Hub HTTP requester credits
  -> worker-pull request
  -> worker lease and result submission
  -> current-run WorkerEarning in FDB
  -> payout request
  -> mock backend settlement
```

This is intentionally different from the standalone seeded payout stress lab.
`--payout-lab-source hub-earned-credits` means the payout lab must consume
current-run `WorkerEarning` records created through normal Hub HTTP routes. It
must not drain stale balances, seed synthetic balances, or pass before a
current-run worker earning has settled.

Prerequisites:

```text
local FoundationDB container is running and .foundationdb/docker.cluster exists
local dev chain / Anvil RPC is listening on http://127.0.0.1:18545
runtime/deployments/dev/latest.json exists for the dev-chain deployment
main_computer/config/dev_contracts.json points at the dev contracts
```

From the repository root, run the integrated smoke with the local dev Hub port
instead of the default/manual port:

```powershell
python exp-fdb-hub.py `
  --host 0.0.0.0 `
  --port 18870 `
  --cluster-file .foundationdb/docker.cluster `
  --namespace main-computer-exp-fdb-local-dev-smoke `
  --network-key dev `
  --network-display-name "Main Computer Local Dev" `
  --network-kind dev `
  --bridge-backend dev-chain `
  --dev-chain-deployment-path runtime/deployments/dev/latest.json `
  --contracts-path main_computer/config/dev_contracts.json `
  --enable-smoke-bridge `
  --chain-id 42424242 `
  --chain-rpc-url http://127.0.0.1:18545 `
  --docker `
  --docker-role all `
  --nodes 12 `
  --docker-workers 8 `
  --docker-requesters 4 `
  --docker-duration-seconds 30 `
  --lab-execution process `
  --http-timeout-seconds 5 `
  --b2bfailures 0 `
  --payout-lab `
  --payout-lab-source hub-earned-credits `
  --payout-lab-source-wait-seconds 90 `
  --payout-lab-source-min-accounts 1 `
  --payout-lab-wallets 800 `
  --payout-lab-requests 200 `
  --payout-lab-concurrency 32 `
  --payout-lab-settlement-workers 4 `
  --payout-lab-failure-rate 0.15 `
  --payout-lab-after-broadcast-crash-rate 0.10
```

In `hub-earned-credits` mode, `--payout-lab-requests 200` is an upper cap. The
deterministic probe should create one current-run worker earning, so a successful
short smoke normally reports `request_count: 1` and `source_account_count: 1`.

Expected probe log lines:

```text
Payout e2e probe: issuing requester credits through Hub HTTP.
Payout e2e probe: registering deterministic worker through Hub HTTP.
Payout e2e probe: submitting worker-pull request through Hub HTTP.
Payout e2e probe: submitting deterministic worker result through Hub HTTP.
Payout e2e probe: worker earning created for run scheduler-e2e-...
```

Expected payout summary fields:

```json
{
  "ok": true,
  "source": "hub-earned-credits",
  "source_account_count": 1,
  "request_count": 1,
  "rejected_count": 0,
  "lost_payout_count": 0,
  "duplicate_chain_settlement_count": 0,
  "pending_credit_wei": "0",
  "wallet_lock_count": 0
}
```

Also confirm that `settled_credit_wei` equals `accepted_credit_wei`. That proves
the current-run Hub worker earning was accepted for payout and fully settled by
the mock backend.


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


## Wallet-authenticated multi-session lab mode

Use this mode when the lab is meant to exercise the same wallet-authenticated
identity boundary that a deployed Hub will require. In this mode, each lab node
gets or derives a dev wallet, asks the Hub for a wallet-signed multi-session key,
and then uses that key on worker registration, heartbeat, poll, result, and
worker-pull requester submissions.

```bash
python -m tools.scheduler_lab.run_lab \
  --auth-mode multisession-wallet \
  --request-mode worker_pull_v0
```

For deployed or strict local Hub runs, enable Hub-side enforcement:

```bash
MAIN_COMPUTER_HUB_REQUIRE_MULTISESSION_AUTH=1
```

When enforcement is enabled, the Hub rejects unsigned worker-pull requester
spend and unsigned worker routes. Requester `account_id` is derived from the
wallet attached to the active multi-session key, so caller-supplied account labels
cannot spend another wallet-backed bridge balance. The scheduler lab funds
wallet accounts through `/api/hub/v1/credits/wallet-funding/import` in
`multisession-wallet` mode instead of admin-issuing credits to arbitrary lab
account names.

The multi-session key remains the current bearer credential for the lab. Treat
it like a secret in logs and deployment wiring. A later hardening pass should add
a separate session secret/hash or proof-of-possession envelope while preserving
this sign-once, use-many-times flow.

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
