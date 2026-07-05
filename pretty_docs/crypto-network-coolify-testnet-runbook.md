# Crypto network Coolify testnet runbook

Status: operator draft, updated 2026-07-05.

This document records the forward Coolify API path for deploying the
Besu/QBFT testnet. Remote Coolify deploys are API-driven. They do not require an
SSH-shaped deploy command, and the runbook should not teach `--single-host` as a
normal path.

## Source of truth

Use `runtime/state/main_computer.private.yaml` for remote Coolify placement and
logical QBFT instances:

```yaml
networks:
  testnet:
    display_name: Main Computer Testnet
    kind: testnet
    chain_id: 42424241
    remote_coolify_hosts: [A, B]
    rpc: https://testnet-rpc.greatlibrary.io
    qbft:
      instances:
        validator-rpc-1:
          coolify_host: A
          roles: [rpc, validator]
          rpc_host_port: 30010
          p2p_host_port: 30321
```

The operator names the logical instance. The deployer infers the Coolify host
from `qbft.instances.<instance>.coolify_host`.

## One-node testnet deploy

Deploy just the current one-node service topology:

```powershell
python .\tools\coolify_qbft_network.py apply testnet `
  --instances validator-rpc-1
```

Contract deployment is explicit and separate:

```powershell
python .\tools\coolify_qbft_network.py deploy-contracts testnet
```

Do not add `--host A`; the selected instance already contains that mapping. Do
not add an SSH target; Coolify create/update/deploy uses the Coolify HTTP API
context from private state.

## Read-only checks

Render the selected plan:

```powershell
python .\tools\coolify_qbft_network.py plan testnet `
  --instances validator-rpc-1
```

Render the selected compose:

```powershell
python .\tools\coolify_qbft_network.py compose testnet `
  --instances validator-rpc-1
```

Discover the deployed topology:

```powershell
python .\tools\coolify_qbft_network.py discover-topology testnet `
  --instances validator-rpc-1
```

Discover and verify Hub against the observed chain surface:

```powershell
python .\tools\coolify_qbft_network.py discover-topology testnet `
  --instances validator-rpc-1 `
  --verify-hub
```

## Deployment rules

* `--instances` selects logical QBFT nodes from private state.
* The Coolify host is inferred from the selected instance.
* `networks.<network>.rpc` is the default external RPC URL when set.
* `--host` is only a low-level compose/debug override.
* `--single-host` is deprecated compatibility plumbing and is not the remote
  Coolify deploy path.
* Deleting an old Coolify service before redeploy is acceptable when changing to
  the canonical service name, but omission from a selected instance list never
  means "delete this node."
