# Crypto network Coolify testnet runbook

Status: operator runbook, updated 2026-07-06.

This document records the tested Coolify API path for operating the remote
Besu/QBFT `testnet`. Remote Coolify deploys are API-driven. They do not require
an SSH-shaped deploy command, and the runbook should not teach `--single-host` as
a normal path.

The current low-resource remote testnet can run in two shapes:

```text
one-validator steady state
  validator-rpc-1
    roles: validator + rpc
    direct RPC: http://<TESTNET_MACHINE_IP>:30010
    public RPC: https://testnet-rpc.greatlibrary.io

two-validator rehearsal state
  validator-rpc-1
    roles: validator + rpc
  validator-1
    roles: validator only
```

Use the tool to move between those shapes. Do not manually delete validator
containers before the tool has voted them in or out of QBFT consensus.

## Source of truth

Use `runtime/state/main_computer.private.yaml` for remote Coolify placement and
logical QBFT instances:

```yaml
networks:
  testnet:
    display_name: Main Computer Testnet
    kind: testnet
    chain_id: 42424241
    rpc: https://testnet-rpc.greatlibrary.io
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 30010
          p2p_host_port: 30321
        validator-1:
          coolify_host: A
          roles: [validator]
          p2p_host_port: 30311
```

The operator names logical instances. The deployer infers the Coolify host from
`qbft.instances.<instance>.coolify_host`.

## Observe the chain

The canonical health check is:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet
```

Healthy one-validator output should have this shape:

```text
ok: true
canonical_rpc: ok
consensus: ok
peer_count: 0
validator_count: 1
validators:
  <validator-rpc-1 address>
```

Healthy two-validator output should have this shape:

```text
ok: true
canonical_rpc: ok
consensus: ok
peer_count: 1
validator_count: 2
validators:
  <validator-rpc-1 address>
  <validator-1 address>
```

`peer_count` is not the validator count. A one-node chain normally has
`peer_count: 0`. A two-node chain normally has `peer_count: 1`.

A running Besu container is not automatically a consensus validator. The only
source of truth for validator membership is the QBFT validator set in
`observe-chain`.

To bypass the public route and check the direct validator RPC:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet `
  --rpc-url http://<TESTNET_MACHINE_IP>:30010 `
  --chain-observation-timeout-s 60
```

Use this when the public RPC temporarily returns `502 Bad Gateway` during or soon
after a Coolify redeploy.

## Initial one-node deploy

Deploy only the current one-node service topology:

```powershell
python .\tools\coolify_qbft_network.py apply testnet `
  --instances validator-rpc-1
```

Then observe:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet
```

## Add `validator-1`

Adding a validator has two layers:

```text
1. Coolify starts the validator-1 Besu container.
2. QBFT accepts validator-1's address into the validator set.
```

The first layer is not enough. `validator-1` is not a consensus validator until
`observe-chain` shows its address in `validators`.

Plan first:

```powershell
python .\tools\coolify_qbft_network.py mutate testnet `
  --add validator-1 `
  --plan `
  --observe-chain
```

Expected plan shape:

```text
ok: true
mutation: add-validator
affected_instances: ["validator-1"]
requires_ack: ["consensus-validator-change"]
```

Apply with generous timeouts. Remote Besu startup and Coolify service updates can
take several minutes on the current test host:

```powershell
python .\tools\coolify_qbft_network.py mutate testnet `
  --add validator-1 `
  --apply `
  --ack-consensus-change `
  --rpc-timeout-s 60 `
  --chain-observation-timeout-s 600 `
  --config-export-timeout-s 600
```

Successful apply phases include:

```text
slurp-current-config
verify-current-validator-set
select-deploy-service-set
seed-new-node-bootstrap
deploy-service
verify-node-identity
propose-validator-vote
wait-validator-set
verify-block-production
commit-topology
```

Then verify:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet
```

Expected result:

```text
ok: true
peer_count: 1
validator_count: 2
```

If the add command deployed the container but failed at
`propose-validator-vote`, do not delete the service. Wait for Besu/direct RPC to
settle, check the direct RPC, and rerun the same add command. The add path retries
the QBFT vote and treats an already-updated validator set as success.

## Retire `validator-1`

Retiring a validator must be done through the tool. The safe sequence is:

```text
1. Verify the current validator set.
2. Identify validator-1's address from the live host.
3. Submit the QBFT removal vote.
4. Wait until validator-1 is absent from the validator set.
5. Redeploy the host without validator-1.
6. Verify block production through canonical RPC.
```

Plan first:

```powershell
python .\tools\coolify_qbft_network.py mutate testnet `
  --retire validator-1 `
  --plan `
  --observe-chain
```

Expected plan shape:

```text
ok: true
mutation: retire-validator
affected_instances: ["validator-1"]
requires_ack: ["consensus-validator-change"]
validator_count: 2
```

Apply:

```powershell
python .\tools\coolify_qbft_network.py mutate testnet `
  --retire validator-1 `
  --apply `
  --ack-consensus-change `
  --rpc-timeout-s 60 `
  --chain-observation-timeout-s 600 `
  --config-export-timeout-s 600
```

Successful apply phases include:

```text
verify-current-validator-set
verify-node-identity
propose-validator-vote
propose-validator-vote-sidecar
wait-validator-set-removal
stop-coolify-service
verify-block-production
verify-retired-direct-rpc-unreachable
commit-topology
```

Then verify:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet
```

Expected result:

```text
ok: true
peer_count: 0
validator_count: 1
validators:
  <validator-rpc-1 address only>
```

Do not retire `validator-rpc-1` from this low-resource topology. It is both the
remaining validator and the RPC backend.

## Contract deployment

Contract deployment is explicit and separate:

```powershell
python .\tools\coolify_qbft_network.py deploy-contracts testnet
```

Deploy contracts after a chain reset, service deletion that recreates the chain
data volume, or any operation that starts a new block history. A block number
that drops from a previous high value back near zero is a reset signal.

Do not redeploy contracts merely because you added or retired `validator-1` if
the chain history and data volume were preserved. Validator membership changes do
not invalidate existing contract addresses.

The deployment writes the app-facing config:

```text
runtime\deployments\testnet\latest.json
main_computer\config\testnet_contracts.json
```

## Coolify health and helper containers

Coolify reports aggregate stack health, while the chain reports consensus/RPC
health. They are related but not identical.

The generated compose uses one-shot helpers for bootstrap, runtime import, key
init, validator vote, and retire cleanup. In steady state, inactive helper names
are repushed as harmless parked services with logs like:

```text
qbft-validator-vote parked healthy; repush compose to run this helper when needed
qbft-retire-orphan-cleanup parked healthy; repush compose to run this helper when needed
```

That is expected. When a helper is needed again, the tool repushes the same
service name with the real helper command.

Besu can be slow on the current host. During a redeploy, Coolify may temporarily
show `Degraded (unhealthy)` and the public RPC may temporarily return `502 Bad
Gateway`. Do not delete the stack just because of a transient 502. First check:

```powershell
python .\tools\coolify_qbft_network.py observe-chain testnet `
  --rpc-url http://<TESTNET_MACHINE_IP>:30010 `
  --chain-observation-timeout-s 60
```

If direct RPC is healthy, wait for the public route to settle and rerun canonical
`observe-chain`.

The public-entry config service should be running in steady state. If it exits,
the public route may still work briefly from existing Traefik config, but the
Coolify stack may be degraded. Check that service's logs before resetting
anything.

## Recovery rules

Use these rules when the system looks confusing:

```text
Running container + not in validators:
  Besu started, but the QBFT add vote has not succeeded yet.

validator_count: 1, peer_count: 1:
  Two Besu nodes are peered, but only one is in consensus.

canonical RPC 502, direct RPC ok:
  Public route/proxy is settling or unhealthy; chain may still be alive.

deploy-contracts ok, observe-chain ok:
  Chain and app-facing contract config are usable.

block number reset:
  Treat previous contract deployment metadata as stale and redeploy contracts.
```

Do not manually delete validator containers to fix consensus membership. Use
`mutate --add` or `mutate --retire` so the tool can vote first and redeploy
second.

## Deployment rules

* `--instances` selects logical QBFT nodes from private state.
* The Coolify host is inferred from the selected instance.
* `networks.<network>.rpc` is the default external RPC URL when set.
* `--host` is only a low-level compose/debug override.
* `--single-host` is deprecated compatibility plumbing and is not the remote
  Coolify deploy path.
* Omission from a selected instance list never means "delete this node."
* Consensus changes require `--ack-consensus-change`.
* A successful Coolify deploy does not prove QBFT membership. Verify with
  `observe-chain`.
