# Hub Compute Credit R2B-0 Contract Smoke

R2B-0 is a pre-RPC safety step. It does not add hub RPC syncing, event cursors,
request charging, worker earnings, settlement batching, or a new vault contract.

The goal is to verify that the existing `HubCreditSale` contract and its
`CreditPurchased` receipt event are still stable before the hub starts decoding
chain logs in R2B.

## Smoke command

From the repository root:

```powershell
python scripts/smoke_hub_credit_sale_container.py
```

The script:

1. Finds the repository root.
2. Statically validates the `HubCreditSale.CreditPurchased` event field order,
   types, and indexed fields.
3. Confirms the expected `HubCreditSale` unit tests are present.
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
python scripts/smoke_hub_credit_sale_container.py --skip-forge
```

Run with a clean Foundry build first:

```powershell
python scripts/smoke_hub_credit_sale_container.py --clean
```

Disable Docker fallback, useful when confirming local `forge` is installed:

```powershell
python scripts/smoke_hub_credit_sale_container.py --no-docker
```

## Reports

The script writes:

```text
runtime/contract_smoke/hub_credit_sale_smoke.json
runtime/contract_smoke/hub_credit_sale_build_report.json
```

These files are runtime diagnostics and should not be treated as source files.

## Expected event shape

R2B log decoding should continue to target:

```text
CreditPurchased(bytes32,address,address,uint256,uint256,string)
```

with fields:

```text
bytes32 indexed purchaseId
address indexed account
address indexed payer
uint256 creditsGranted
uint256 amountPaidWei
string memo
```

This smoke check intentionally does not compute or import ledger credits. R2A
already proves the normalized receipt import path. R2B-0 only verifies that the
contract-side receipt source is still ready for a future RPC/event-sync patch.
