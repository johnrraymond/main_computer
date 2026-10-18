# Mainnet chain reset and contract redeploy runbook

Status: updated 2026-10-17. For existing mainnet contract redeploy only; **not a chain creation or reset procedure**. See `pretty_docs/mother-network-operations.md` for current authority and network inventory.

Use this when the named `mainnet` chain has been deliberately reset or replaced
and the operator needs to publish a fresh set of deployment facts.

This runbook is intentionally about **local operator authority**. A real chain
cannot erase old contracts from its history. A dead/replaced chain can abandon
old local deployment manifests, generate or reuse a new funded deployer, deploy a
new contract set, and make the app point at the new public deployment files.

## Source of truth

Mother's verified `runtime/state/mother/identity.private.yaml`, its metadata,
and its private recovery manifest own network identity and controller bindings.
Do not restore `runtime/state/main_computer.private.yaml` as deployment authority.
Private keys must never enter public deployment artifacts.

Contract addresses are public deployment facts. They belong in public deployment
and config artifacts, not in private YAML:

```text
runtime/deployments/mainnet/latest.json
runtime/deployments/mainnet/runs/<run_id>/deployment.json
main_computer/config/mainnet_contracts.json
```

Wallet credit balances are also not private-state authority. They are chain or
application ledger facts and should not be preserved as private YAML fields.

Do not synchronize deployment manifests back into private identity state.

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

## Establish a verified, already-running chain first

The previous instructions that invoked `tools/coolify_qbft_network.py` are
retired: that executor was deleted. **This runbook cannot create, reset, or
repair Besu services.** Never attempt to reconstruct it with old Allfather or
Coolify cluster scripts.

Inspect Mother's current controller binding and deployment-plan state:

```powershell
python -m tools.mother_coolify controllers
python -m tools.mother_deploy plan --network mainnet
```

These are read-only orientation checks, not proof of live chain health or
permission to mutate. Independently verify the **intended** RPC endpoint,
chain ID, observed validator membership, and advancing block height. A plan
alone is not live deployment evidence. If the chain is down or the intended
RPC route is unknown, stop; use a separately reviewed, authorized Mother
repair operation before deploying any contracts.

## Identity authority: Mother

The legacy `mainnet-operator.py prepare-keys` command has been retired. Do not
create, rotate, or reassign officer keys through
`runtime/state/main_computer.private.yaml` or by editing Mother YAML by hand.
Mother's committed identity is `runtime/state/mother/identity.private.yaml`,
accompanied by its metadata and recovery manifest. Its verified reader checks
the predecessor-bound identity generation.

For an existing mainnet, obtain the approved deployer secret through the
existing secure operator workflow. Set `MAINNET_DEPLOYER_PRIVATE_KEY` in your
process environment, not as a command-line literal. Obtain the four officer
addresses from the verified Mother identity. Never print the deployer key.

The `reserve-starter` identity writer is a generation-one operation and **must
not** be used to rotate identities in an existing mainnet. A network-specific
verified state transition is required for any subsequent identity change.

The B-only testnet was registered by `tools/mother_identity.py register-testnet`
in verified Mother generation 23 as of 2026-10-17. Repeating registration is
not a deployment procedure; do not re-register an existing network to obtain
Besu containers. Mother automatically uses its verified predecessor binding
for an authorized successor; manual generation acknowledgment is optional.

The contract operator below remains a separate explicit operator command. It
accepts `--offices` as four approved addresses and a deployer-key environment
variable; it does not read either Mother or legacy private state itself.
**Do not use Hub Control-generated contract deployment commands that still
extract credentials from the legacy private-state YAML** until that remaining
caller is migrated to verified Mother identity. This manual contract path must
use independently verified addresses and a securely supplied deployer key.

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

## Verify public deployment results

After contract deployment, inspect the public deployment manifests:

```powershell
Get-Content .\main_computer\config\mainnet_contracts.json
Get-Content .\runtime\deployments\mainnet\latest.json
```

Do not run the deleted legacy synchronizer or manually rewrite Mother's
committed identity to synchronize contract addresses. Changes to Mother state
require a predecessor-bound, verified Mother state transition.

## Validator-RPC canary funding proof

When the Mother validator-RPC canary is used after steady-state deployment, the
funding step is gated by the Mother-owned shared RPC route:

```text
https://mainnet-rpc.greatlibrary.io
```

Do not infer per-node RPC names from Allfather examples. For the Mother mainnet
path, `mainneta-rpc1.greatlibrary.io` and `mainnetc-rpc1.greatlibrary.io` are
not the canonical live route unless a staged Mother route manifest explicitly
declares them. The shared hostname can have multiple DNS records, and every
active backend for that hostname must answer the mainnet RPC contract.

Before canary funding, verify the route:

```powershell
curl.exe https://mainnet-rpc.greatlibrary.io `
  -H 'Content-Type: application/json' `
  --data-raw '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
```

Expected:

```json
{"jsonrpc":"2.0","id":1,"result":"0x28757b0"}
```

`0x28757b0` is chain ID `42424240`. If the response is `no available server`,
the hostname reached the routing layer but no healthy Traefik/proxy backend was
available. Fix Mother-owned route/backend registration before funding. If DNS
fails, fix the shared hostname. If the JSON-RPC result is a different chain ID,
the route is serving the wrong network.

Completion requires an observed on-chain postcondition through the shared route:

```text
canary balance == exact required funding amount
```

The Mother route preflight must prove:

```text
eth_chainId == 42424240
eth_getBalance(canary) succeeds
```

before any captain key is bound or any transfer is attempted. A temporary helper
container is not required to prove the shared RPC route. The Mother mainnet
canary path must not depend on the Foundry helper image, `cast`, or Coolify
runtime-log scraping just to prove route availability.

No funding release should be repeated blindly. The evidence state
`unchanged-before-funder-start` means the captain key was not bound to a funder
and no transfer was attempted. Fix the route, route backend, or balance
classification problem first, then restage a new funding transaction from the
canary transaction.

A successful funding evidence document must show the shared route chain-id
preflight, balance precondition, transfer/reconciliation decision, and
post-funding exact balance verification passing before any canary execution
release is authorized.

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

Recheck the live network through an approved Mother observation and the
intended RPC endpoint; do not call the deleted `coolify_qbft_network.py` tool.

The goal is to move from a degraded single-observed-validator deployment to the
documented mainnet shape: multiple validators, separated RPC surface, and a Hub
that consumes public deployment/config artifacts rather than private-state
contract mirrors.
