# Hub Compute Credit R2B-0 Bridge Escrow Contract Smoke

R2B-0 is a pre-RPC safety step. It does not add hub RPC syncing, request
charging, worker settlement batching, or UI integration.

The goal is to verify the active `HubCreditBridgeEscrow` contract and its
bridge-controlled custody flow before the hub starts decoding chain logs in R2B.

The contract model is:

```text
user deposits escrow once
hub/bridge credits the internal Compute Credit account
requests spend privately inside the bridge-side ledger
bridge rectifies aggregate internal spend when needed
bridge releases reconciled unused escrow
```

This deliberately avoids one public chain transaction per AI request.

## Smoke command

From the repository root:

```powershell
python scripts/smoke_hub_credit_bridge_escrow_container.py
```

The script:

1. Finds the repository root.
2. Statically validates the `CreditDeposited`, `SpendRectified`, and
   `WithdrawalReleased` event field order, types, and indexed fields.
3. Confirms the expected `HubCreditBridgeEscrow` unit tests are present.
4. Runs the repo's container-aware Foundry wrapper:

```powershell
python tools/build_contracts.py --repo-root <repo> --project contracts --test
```

`tools/build_contracts.py` uses local `forge` when available and otherwise falls
back to the Foundry Docker image. This keeps contract verification aligned with
the normal containerized Forge workflow.

## Useful options

Static/event-shape check only:

```powershell
python scripts/smoke_hub_credit_bridge_escrow_container.py --skip-forge
```

Run with a clean contract build:

```powershell
python scripts/smoke_hub_credit_bridge_escrow_container.py --clean
```

Disable Docker fallback:

```powershell
python scripts/smoke_hub_credit_bridge_escrow_container.py --no-docker
```

## Smoke report

The script writes:

```text
runtime/contract_smoke/hub_credit_bridge_escrow_smoke.json
runtime/contract_smoke/hub_credit_bridge_escrow_build_report.json
```

## Contract acceptance behavior

The contract smoke must prove:

```text
requester deposits 100 credits worth of escrow
bridge rectifies 5 credits of aggregate spend
contract withdrawable amount becomes 95
bridge rectifies another 0.5 credits of aggregate spend
contract withdrawable amount becomes 94.5
bridge releases 94.5 back to requester
duplicate rectification id does not double-count
duplicate withdrawal id does not double-pay
non-bridge wallet cannot rectify spend
non-bridge wallet cannot release withdrawal
```

In the dev-chain smoke, one Compute Credit is represented with 18-decimal native
base units. Production economics can change later without changing the accounting
shape.
