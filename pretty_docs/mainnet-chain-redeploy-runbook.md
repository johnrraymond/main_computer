# Mainnet chain reset and contract redeploy runbook

Status: operator runbook, stabilized 2026-07-08.

Use this when the named `mainnet` chain has been deliberately reset or replaced
and the operator needs to publish a fresh set of deployment facts.

This runbook is intentionally about **local operator authority**. A real chain
cannot erase old contracts from its history. A dead/replaced chain can abandon
old local deployment manifests, generate or reuse a new funded deployer, deploy a
new contract set, and make the app point at the new public deployment files.

## Source-of-truth split

`runtime/state/main_computer.private.yaml` is private operator state. It should
contain only things that are private or operationally sensitive:

```text
Coolify API tokens
private RPC/operator host topology
wallet private keys and generated private wallet material
manual operator placement choices
```

Contract addresses are public deployment facts. They belong in public deployment
and config artifacts, not in private YAML:

```text
runtime/deployments/mainnet/latest.json
runtime/deployments/mainnet/runs/<run_id>/deployment.json
main_computer/config/mainnet_contracts.json
```

Wallet credit balances are also not private-state authority. They are chain or
application ledger facts and should not be preserved as private YAML fields.

After a deployment or cleanup run, `sync_private_state.py` should keep the
private file focused on secrets/topology and should not reintroduce contract
addresses or wallet credit mirrors.

## Safety checklist

Before deploying:

```text
the target chain is intentionally the chain you want to write to
the deployer private key is the intended funded deployer
the deployer account has gas on the target chain
the office wallet addresses are the intended new governance/operator addresses
the RPC URL reports the expected chain id
the chain has advanced at least one block
```

For the current mainnet-shaped network, the expected chain id is:

```text
42424240
0x28757b0
```

A single live validator/RPC node can be enough for an emergency contract
deployment on a deliberately reset chain, but it is a degraded network state.
Persistent mainnet operation should converge to multiple observed validators and
a non-zero peer count.

## Bring up the chain surface

Apply the Coolify-managed QBFT service that exposes the operator RPC. For a
minimal one-instance bring-up:

```powershell
python .\tools\coolify_qbft_network.py apply mainnet `
  --allow-mainnet `
  --instances validator-rpc-1
```

Then inspect live reality:

```powershell
python .\tools\coolify_qbft_network.py observe-chain mainnet --allow-mainnet
```

The observation command uses private state only as a decoder/template. The live
truth comes from RPC and Coolify observations. A degraded report is expected if
only `validator-rpc-1` is deployed; it should still report the expected chain id
and advancing blocks before contract deployment proceeds.

## Reuse existing private keys

If the intended new private keys already exist, do not run key preparation again.
Set the deployer key environment variable from the private YAML or from your
operator secret store:

```powershell
$env:MAINNET_DEPLOYER_PRIVATE_KEY = "<private deployer key>"
```

Build the office list from the private state when those wallet entries are the
intended governance wallets:

```powershell
$offices = python -c "import yaml; s=yaml.safe_load(open('runtime/state/main_computer.private.yaml', encoding='utf-8')); w=s['networks']['mainnet']['wallets']; print(','.join(w[r]['address'] for r in ('captain','o1','o2','o3')))"
```

## Generate missing private keys

Only use this when keys are missing or a deliberate key rotation is intended:

```powershell
python .\tools\mainnet-operator.py prepare-keys --network mainnet
```

The key-preparation command is intentionally idempotent. It preserves existing
wallet entries. To rotate a role, clear that role deliberately first, then run
`prepare-keys`.

Generated public wallet summaries are written under:

```text
runtime/deployments/mainnet/key-material/
```

Private keys remain private operator state and must not be committed.

## Deploy contracts

Dry-run first:

```powershell
python .\tools\mainnet-operator.py deploy-contracts `
  --target-environment mainnet `
  --chain-id 42424240 `
  --rpc-url https://mainnet-rpc.greatlibrary.io `
  --container-rpc-url https://mainnet-rpc.greatlibrary.io `
  --external-docker-network bridge `
  --private-key-env MAINNET_DEPLOYER_PRIVATE_KEY `
  --offices $offices `
  --dry-run
```

Then execute:

```powershell
python .\tools\mainnet-operator.py deploy-contracts `
  --target-environment mainnet `
  --chain-id 42424240 `
  --rpc-url https://mainnet-rpc.greatlibrary.io `
  --container-rpc-url https://mainnet-rpc.greatlibrary.io `
  --external-docker-network bridge `
  --private-key-env MAINNET_DEPLOYER_PRIVATE_KEY `
  --offices $offices `
  --yes
```

A successful deployment verifies code at each deployed address and writes:

```text
runtime/deployments/mainnet/runs/<run_id>/deployment.json
runtime/deployments/mainnet/latest.json
main_computer/config/mainnet_contracts.json
```

The deployment also updates the dev-chain compatibility pointer under
`runtime/dev-chain/` because the current implementation shares the external-chain
deployment machinery with `tools/dev-chain-reset.py`. The app-facing mainnet
source of truth remains the network-scoped `runtime/deployments/mainnet/` and
`main_computer/config/mainnet_contracts.json` files.

## Clean private state after deployment

After the deploy has written public manifests, sync the private file without live
probing:

```powershell
python .\tools\sync_private_state.py --no-live-check --write
```

Then check that the private YAML did not regain public contract state or wallet
credit mirrors:

```powershell
Select-String -Path .\runtime\state\main_computer.private.yaml `
  -Pattern "contracts|AlphaBetaLockout|XLagBridgeReserve|HubCreditBridgeEscrow|credits"
```

The public files should contain the deployed contract addresses:

```powershell
Get-Content .\main_computer\config\mainnet_contracts.json
Get-Content .\runtime\deployments\mainnet\latest.json
```

## Post-deploy network hardening

A successful contract deployment is not the same as a healthy persistent
mainnet. After the emergency/minimal path works, bring the template and live
Coolify reality back together:

```text
all intended validator services are observed in Coolify
the selected RPC reports the expected chain id
blocks continue advancing
peer count is non-zero for a multi-host topology
the validator count matches the intended validator-role services
Besu image tags are pinned before long-lived operation
public RPC exposure has TLS, rate-limit, and method policy
```

Re-run:

```powershell
python .\tools\coolify_qbft_network.py observe-chain mainnet --allow-mainnet
```

The goal is to move from a degraded single-observed-validator deployment to the
documented mainnet shape: multiple validators, separated RPC surface, and a Hub
that consumes public deployment/config artifacts rather than private-state
contract mirrors.
