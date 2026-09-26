# Hub-Chain rectifier operator surface

Status: operator companion to `hub-chain.md`

Source reviewed: `hub-chain.md` SHA-256
`17c8a602b1d17156d8ed1544b430be8c03251a17093932bd26e2ff938d9d24e1`

## Public commands

```powershell
python .\hub_chain_rectify_harness.py inspect
python .\hub_chain_rectify_harness.py rectify
```

`--network` may be added with `mainnet` as the default when implemented.

## `inspect`

Read-only. It reports:

```text
canonical Chain consumer contract generation/hash
accepted Hubs
per-Hub adopted Chain contract generation/hash
current/stale/unverifiable/blocked classification
RPC reachability
chain-ID verification
explicit core requirement verification
advertised application-contract status (non-blocking unless promoted)
```

## `rectify`

Meaning:

> Bring accepted Hubs that require Chain rectification onto the currently accepted Chain consumer contract without changing Hub membership or Chain authority.

The rectifier chooses stale Hubs from observed state. Operators do not supply RPC URLs, contract addresses, validator topology, operation IDs, or Coolify UUIDs.

## First-birth bootstrap behavior

Before first Hub mutation, `add-hub` may materialize the missing Chain v1 consumer-contract artifact from canonical network chain identity/RPC plus deployment-owned `runtime/deployments/<network>/latest.json` contract identities. The deployment manifest wins over checked-in contract discovery; `main_computer/config/<network>_contracts.json` is used only when the deployment manifest is absent.

The manifest network/chain ID must agree with the selected network. Hub birth blocks if the RPC is unavailable, reports the wrong chain ID, or an application contract explicitly promoted into `required_contract_keys` fails validation. The current core Hub requirement set contains no application contracts, so missing code at advertised addresses such as `hub_credit_bridge_escrow` is reported as stale feature evidence rather than blocking Hub birth.

Operators supply none of those values.

## Safety boundary

Rectification may not mutate Mother/chain topology, validators, RPC services, or deployed smart contracts. A stale advertised feature contract does not authorize Hub Control to redeploy it.

## Retry behavior

A rerun re-inspects adoption and continues from observed state. Already-current Hubs are not blindly rewritten.

## Initial YAGNI boundary

Do not initially expose:

```text
--hub
--rpc-url
--contract-address
--contract-generation
--contract-file
--force-all
```
