# Hub-Chain rectification architecture

Status: architectural baseline for the Chain dependency edge consumed by Hubs.

## Purpose

Chain/Mother tooling owns chain truth. Hub Control owns Hub membership. Hub-Chain rectification owns only the adoption edge between them.

```text
Chain authority -> Chain consumer contract -> Hub-Chain rectifier -> accepted Hubs
```

The rectifier may update chain-facing Hub runtime projection. It may not add/remove Hubs, validators, RPC nodes, or smart-contract deployments.

## Core Hub dependency versus application features

A Hub's core dependency on the chain is smaller than the set of application contracts that may exist on that chain.

For Hub lifecycle, the chain dependency is satisfied when the canonical RPC is reachable and reports the expected chain ID. The current Hub can start and expose its core service without a live `hub_credit_bridge_escrow` deployment; bridge/credit behavior is a feature dependency and must not become an accidental prerequisite for Hub birth.

Therefore the current v1 Hub Chain consumer contract has no mandatory application-contract keys. Deployment manifests may advertise `hub_credit_bridge_escrow`, `alpha-beta-lockout`, `xlag-bridge-reserve`, and other contract identities, but missing code for those advertised contracts is evidence about feature availability, not evidence that the chain itself is unavailable.

If a future Hub runtime genuinely cannot start or operate its core service without a particular contract, that key may be explicitly promoted into `required_contract_keys`. Only then does missing address/code become a Hub lifecycle blocker.

## First-birth publication bridge

The chain side does not yet have a dedicated durable consumer-contract publisher in the legacy deployment path. First Hub birth therefore materializes the v1 Chain consumer contract from canonical network/private chain identity plus the deployment-owned `runtime/deployments/<network>/latest.json` manifest when that manifest exists.

The manifest is validated for network and chain identity. Its deployed contract records are retained as advertised feature identities and are probed with `eth_getCode` for evidence. The core blocking proof is live JSON-RPC reachability plus `eth_chainId` equality.

The checked-in `main_computer/config/<network>_contracts.json` file is only a bootstrap fallback when no deployment manifest exists. It must not override deployment-owned identities. This bootstrap publisher is not permission for Hub Control to mutate chain authority.

## Canonical contract

The chain side must publish one durable consumer contract per network, targeted at:

```text
runtime/state/chain/<network>/consumer-contract.json
```

The contract contains only Hub-consumable truth, such as:

```text
schema/version
network
chain ID
usable RPC endpoint
required application-contract keys, currently empty
advertised deployed contract identities
protocol/API compatibility
finality/confirmation requirements when Hub behavior needs them
contract generation
contract-source provenance
content hash
```

Chain topology and the Chain consumer contract are different objects. A validator change that does not alter Hub-consumed RPC truth need not force Hub rectification.

## Advertised contract evidence

Advertised contract identities are still useful. Hub Control probes them and records each as:

```text
verified
missing-code
unverifiable
```

Those results are visible operator evidence and can drive feature-specific rectification later. They do not block current Hub birth unless the contract key is explicitly listed in `required_contract_keys`.

This keeps a stale application deployment record from being confused with loss of the chain itself.

## Per-Hub adoption state

For each accepted Hub, classify chain adoption as:

```text
current
stale
unverifiable
blocked
```

`current` requires the Hub to have adopted the frozen Chain consumer contract and to report the expected RPC/chain identity. Feature-contract status is separate evidence unless promoted into the core requirement set.

## Authority rule

Hub code must not scrape arbitrary Mother runtime state and elevate it into Hub authority. The Chain consumer contract is the explicit boundary object.

## Safety

Hub birth/rectification stops before mutation if the canonical Chain contract is missing, malformed, incompatible, the RPC cannot be reached, the RPC reports the wrong chain ID, or an explicitly required application contract fails validation.
