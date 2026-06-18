# Hub Chain Architecture: Devnet, Testnet, and Mainnet

This is the working architecture note for the chain path that the Hub is chasing.

It is intentionally README-like: it explains the current local developer chain, the
local Besu/QBFT lab, the testnet shape we want next, and the eventual mainnet
shape. It is a planning document, not a claim that all of this is already wired
into the Hub.

## What we are trying to build

Main Computer needs three chain experiences that share the same mental model but
serve different purposes:

```text
fast local development  ->  local devnet
mainnet-like rehearsal  ->  QBFT testnet
real production chain   ->  QBFT mainnet
```

The important architectural rule is:

```text
the Hub consumes a chain profile and RPC endpoint;
the Hub is not the blockchain and should not own consensus.
```

The Hub can be deployed near a chain, near an RPC node, or near a website, but
validators should remain separate from Hub application responsibilities.

## Current local devnet

The current developer chain is the easy path used by the Hub, worker, viewport,
and contract smokes today.

It is the Foundry/Anvil path:

```text
contracts/                 Solidity contracts and Foundry project
tools/build_contracts.py    builds/tests contracts with Forge or Foundry Docker
tools/dev-chain-reset.py    starts/resets the local Anvil dev chain and deploys contracts
```

The defaults in the current tooling are:

```text
chain id: 42424242
RPC URL:  http://127.0.0.1:18545
runtime:  runtime/deployments/dev/latest.json
```

`tools/dev-chain-reset.py` is the lifecycle tool for this devnet. It starts or
replaces the local Anvil chain, deploys the configured contracts, and publishes
the app-facing deployment runtime.

The local devnet writes machine-local runtime files such as:

```text
runtime/deployments/dev/latest.json
runtime/dev-chain/latest.json
runtime/dev-chain/latest.env
runtime/deployments/hub-admin-wallet.json
```

Those files are runtime state, not source code. They should stay out of Git.

### What the Hub and worker currently look at

The key file is:

```text
runtime/deployments/dev/latest.json
```

That file is the local golden path for contract/RPC discovery. The blockchain
service and local viewport-facing APIs verify this file before using bridge
contracts. The worker UI intentionally reloads the bridge escrow address from
the local viewport instead of trusting stale browser storage.

In practical terms:

```text
Anvil dev chain
  -> deployed contracts
  -> runtime/deployments/dev/latest.json
  -> blockchain service / viewport APIs
  -> worker wallet funding UI and Hub credit bridge flow
```

The worker path is currently dev-chain-specific. It expects the wallet to be on
the dev chain and checks that the deployment runtime, chain id, RPC, and bridge
contract line up.

### Why we keep the devnet

The Anvil devnet is still valuable even if we later run our own QBFT chain.

Use the devnet for:

```text
fast Hub development
contract iteration
worker/hub UI work
simple bridge escrow smokes
local debugging
```

It is not meant to prove validator topology, peer behavior, or mainnet
resilience.

## Current local QBFT lab

The local QBFT lab is the architecture rehearsal path.

It is currently driven by:

```text
tools/smoke_besu_qbft_one_validator.py
```

The name is historical. The script now brings up a four-validator local Besu/QBFT
lab.

Current defaults:

```text
chain id:       42424241
fee model:      London / EIP-1559 and Shanghai/PUSH0 from genesis, with a real base fee
Docker network: smoke-besu-qbft-network
Docker subnet:  172.28.241.0/24
runtime:        runtime/smoke-besu-qbft-four-validators
```

Current validator containers:

```text
smoke-besu-qbft-validator-1  ->  http://127.0.0.1:30001
smoke-besu-qbft-validator-2  ->  http://127.0.0.1:30002
smoke-besu-qbft-validator-3  ->  http://127.0.0.1:30003
smoke-besu-qbft-validator-4  ->  http://127.0.0.1:30004
```

Common commands:

```powershell
python tools/smoke_besu_qbft_one_validator.py up
python tools/smoke_besu_qbft_one_validator.py deploy
python tools/smoke_besu_qbft_one_validator.py monitor
python tools/smoke_besu_qbft_one_validator.py check
python tools/smoke_besu_qbft_one_validator.py down
python tools/smoke_besu_qbft_one_validator.py smoke
```

The `deploy` command is the bridge into the golden app path. It delegates the
contract deployment and runtime publication to the same deployment machinery as
`tools/dev-chain-reset.py`, but in external-chain mode against the running QBFT
RPC node. External-chain deployment performs an EIP-1559 preflight and refuses
to proceed if the chain does not expose `baseFeePerGas`; it should not fall back
to legacy transactions or a zero-base-fee compatibility chain. The app-facing
output is still:

```text
runtime/deployments/dev/latest.json
```

For the local QBFT testnet, that publication records:

```text
environment: test
chain id:    42424241
RPC URL:     http://127.0.0.1:30010
source:      qbft-smoke-testnet-deploy
```

What the local QBFT lab has proven so far:

```text
Besu can generate a QBFT genesis locally
four validator containers can share one genesis
validators can peer over a Docker network
each validator can see the other three peers
blocks are produced
the QBFT validator set reports four validators
the network continues producing blocks with one validator stopped
a dedicated non-validator RPC node can expose app/tool traffic on 30010
the QBFT testnet can publish the same runtime deployment shape as the dev chain
```

That last point is the important architectural proof. It shows why four
validators matter: the chain can continue when one validator is down.

### Why the QBFT lab exists

The QBFT lab is not a replacement for the fast Anvil devnet yet.

It exists to answer mainnet-shape questions:

```text
Can we run multiple validators?
Can they peer reliably?
Can blocks keep advancing?
What does validator failure look like?
How should RPC access be separated from validators?
What will a remote testnet need?
```

## The testnet we are aiming for

The near-term testnet is now the promoted version of the local QBFT lab: same
golden deployment runtime, but backed by Besu/QBFT instead of Anvil.

The current local target shape is:

```text
4 QBFT validator nodes
1 non-validator RPC node
Hub and tools connect to the RPC node
validators do not serve as the public application RPC surface
```

Local version:

```text
validator-1
validator-2
validator-3
validator-4
      |
      v
non-validator RPC node  ->  http://127.0.0.1:30010
      |
      v
Hub / viewport / tools
```

Run the Hub against the dev or test profile with the actual repo entrypoint:

```powershell
python -m main_computer.cli hub --network dev
python -m main_computer.cli hub --network test
```

The test profile listens on `http://127.0.0.1:8780` and talks to the QBFT
non-validator RPC node at `http://127.0.0.1:30010`. The dev profile listens on
`http://127.0.0.1:8770` and talks to the Anvil dev-chain RPC at
`http://127.0.0.1:18545`.


The client verification path is now network-aware too:

```powershell
python .\scripts\smoke_hub_network_client.py --network dev
python .\scripts\smoke_hub_network_client.py --network test
```

That smoke is intentionally outside the Hub. It checks the selected Hub profile,
the chain RPC identity, EIP-1559 and Shanghai/PUSH0 readiness, deployed contract
code, the per-network funded smoke wallet, and a real paid worker/credit/claim
flow through the Hub API. This is the first complete local proof that dev and
test can use the same golden pathway.

Current low-resource remote testnet bring-up version:

```text
validator-1 + operator RPC  ->  http://<TESTNET_MACHINE_IP>:30010
      |
      v
Hub server / Hub site / operator tools
```

The current test machine is too small for four Besu services plus a dedicated
RPC sidecar, so the default `testnet` Coolify seed intentionally deploys one
Besu/QBFT validator that also serves the operator RPC port. This proves the
Hub/RPC/contract path but is not fault-tolerant.

Promoted remote testnet target, once the host capacity exists:

```text
validator host 1
validator host 2
validator host 3
validator host 4
      |
      v
testnet RPC host
      |
      v
Hub server / Hub site / operator tools
```

The promoted remote testnet should use the same basic topology as the eventual
mainnet, but with testnet economics, test keys, and lower operational stakes.

## The mainnet we are aiming for

Mainnet should not be a single server pretending to be a network.

The target mainnet is:

```text
one EVM-compatible chain
one chain id
one genesis
one validator set
multiple validator hosts
separate RPC/indexer hosts
Hub as an application consumer of the chain
```

A healthier mainnet shape looks like this:

```text
validator host 1
validator host 2
validator host 3
validator host 4
      |
      v
private RPC / indexer host  ->  Hub server
      |
      v
public RPC host(s)          ->  Hub site / browser wallets
```

The Hub should not hold validator keys. The Hub should not be required for
validators to produce blocks. The Hub should not be the only path into the chain.

## Chain profiles

The long-term interface between the Hub and any chain should be a chain profile,
not hardcoded assumptions.

A chain profile should eventually describe:

```text
network key
display name
environment: dev, testnet, or mainnet
chain id
RPC URLs or env-var references
block explorer URL, if any
native currency metadata
bridge contract addresses
minimum confirmation policy
enabled/disabled state
```

Conceptually:

```json
{
  "network_key": "local_qbft_testnet",
  "display_name": "Main Computer Local QBFT Testnet",
  "environment": "testnet",
  "chain_id": 42424241,
  "public_rpc_url": "http://127.0.0.1:30010",
  "contracts": {
    "hub_credit_bridge_escrow": "0x..."
  },
  "enabled": true
}
```

The current Anvil devnet already has a partial version of this idea through:

```text
runtime/deployments/dev/latest.json
```

The future should generalize that shape so the Hub can choose among:

```text
local Anvil devnet
local QBFT testnet
remote QBFT testnet
mainnet
```

## Operator model

Contract deployment should be an operator action, not a Hub startup side effect.

The intended model is:

```text
operator machine
  -> connects to chosen RPC
  -> deploys/verifies contracts
  -> writes or publishes deployment profile

Hub server
  -> consumes verified deployment profile
  -> serves safe public network config to Hub site
  -> keeps private/admin/operator details server-side
```

The passcode or operator credential should authorize profile publication to the
Hub. It should not replace blockchain signing keys or validator keys.

## What is not the plan

We are not trying to connect several separate blockchains together.

Wrong mental model:

```text
blockchain A + blockchain B + blockchain C + bridge glue
```

Right mental model:

```text
one chain
multiple validators
separate RPC nodes
Hub consumes RPC/profile
```

We are also not trying to make the Hub the chain controller. The Hub may run near
the chain and may verify chain state, but consensus belongs to validators.

## Roadmap

### Phase 1: Keep the Anvil devnet

Keep using the current Foundry/Anvil path for fast Hub and worker development.

Acceptance:

```text
tools/build_contracts.py --test works
tools/dev-chain-reset.py publishes runtime/deployments/dev/latest.json
Hub/worker can use the dev deployment runtime
```

### Phase 2: Operate the local QBFT lab

Use the local Besu/QBFT lab to understand validator behavior.

Acceptance:

```text
4 validators start
monitor shows blocks advancing
peer counts are healthy
one validator can be stopped and blocks continue
```

### Phase 3: Add a non-validator RPC node

Move application traffic away from validator RPC ports.

Acceptance:

```text
4 validators keep producing blocks
1 non-validator RPC node follows the chain
Hub/tools can read through the RPC node
validator RPC ports are no longer the main app surface
```

### Phase 4: Deploy contracts to the local QBFT lab

Deploy `HubCreditBridgeEscrow` and related contracts to the QBFT lab.

Acceptance:

```text
contracts deploy to QBFT chain
deployment profile is written
contract bytecode is verified through RPC
basic bridge escrow calls work against QBFT
```

### Phase 5: Generalize chain profiles

Create a clean profile model for devnet, testnet, and mainnet.

Acceptance:

```text
Hub can describe available networks
Hub site can choose wallet network
Hub server can validate the configured chain
mainnet can exist as disabled/not-configured until ready
```

### Phase 6: Remote QBFT testnet

Move the QBFT topology from local Docker to remote hosts.

Acceptance:

```text
validators run on separate hosts
RPC node is separate from validators
operator deployment flow works remotely
Hub consumes remote testnet profile
```

### Phase 7: Mainnet hardening

Prepare production operations.

Acceptance:

```text
validator keys are separated
Hub does not hold validator keys
public RPC is separate from private RPC/indexer
monitoring and recovery paths exist
upgrade and governance policy is documented
```

## Current practical rule

For now:

```text
Use Anvil devnet for speed.
Use QBFT lab for architecture truth.
Do not merge QBFT deeply into Hub until the RPC-node and contract-deployment path is proven.
```
