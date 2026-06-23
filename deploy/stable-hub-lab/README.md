# Stable Hub Lab

This lab is the low-entropy, production-shaped Hub contract harness.

It is intentionally separate from `exp-fdb-hub.py` and the scheduler lab. The dev topology reuses the local FoundationDB cluster file at `.foundationdb/docker.cluster`, but the stable Hub contract is topology-driven and MSK-first.

## Validate the topology

```bash
python -m tools.stable_hub_lab.run_lab --topology deploy/stable-hub-lab/dev-topology.json --validate-only
```

## Start the local stable Hub cluster

```bash
python -m tools.stable_hub_lab.run_lab --topology deploy/stable-hub-lab/dev-topology.json --serve-cluster --check-after-start
```

## MSK smoke

With the cluster running, issue an MSK on one concrete Hub and validate it on another:

```bash
python -m tools.stable_hub_lab.run_lab --topology deploy/stable-hub-lab/dev-topology.json --smoke-msk --request-hub-id dev-hub1 --validate-hub-id dev-hub3
```

The lab creates and reuses a throwaway dev wallet at `.main-computer/stable-hub-lab/msk-smoke-wallet.json` if no `--wallet` is supplied. If `--wallet` points at a missing path, the lab creates that wallet too. The signed MSK request message contains high-entropy `user_slug`. The Hub adds its own high-entropy `hub_slug`, stores the full signed request, and returns `msk_<user_slug>_<hub_slug>` as the MSK id.

## Worker live sessions

Workers use a long-lived WebSocket session, not REST polling:

```text
/api/hub/v1/workers/live-session
```

The first message on the open socket is `worker.auth` with `worker_id` and a `multisession_key_id`. The owner Hub validates the MSK, keeps the socket open, writes the worker owner record to shared storage, sends `hub.ping`, and expects `worker.pong` on the same socket.

## Contract

- Authentication: `multisession-wallet`
- Worker connection: long-lived MSK-authenticated WebSocket session
- Heartbeat: ping/pong over the live connection
- Availability source: live worker session owner
- Routing: entry Hub reserves with worker-home Hub and returns a concrete execution Hub URL

## Troubleshooting

`--check-after-start` verifies HTTP health and identity only. The first MSK or worker live-session smoke also touches the shared FoundationDB-backed stores. If a smoke POST times out, the Hub may have accepted the request and then blocked in an FDB read/write before stdlib logging printed the request line. The stable Hub sets bounded FDB transaction timeouts for these stores so storage failures should return a clear HTTP 503 instead of hanging indefinitely.
