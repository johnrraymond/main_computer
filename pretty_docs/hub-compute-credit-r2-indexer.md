# Hub Compute Credit R2A Indexer

R2A adds the first funding bridge between an on-chain funding receipt and the
internal Compute Credit ledger.

This phase is intentionally narrow:

- no credit card processing
- no fiat checkout
- no RPC/event polling
- no paid request charging
- no worker earnings
- no settlement batching
- no new vault contract

The hub accepts a normalized receipt-shaped payload, validates it, converts it
to a `ChainEventRef` plus `CreditDeposit`, and records it through
`HubCreditLedger.record_deposit(...)`.

## Endpoints

```text
GET  /api/hub/v1/credits/indexer
POST /api/hub/v1/credits/purchases/import
```

The status endpoint reports the R2A mode, the event family this import is
shaped for, and the current ledger totals.

The import endpoint is for local-dev/operator use until the R6 auth patch
protects admin/import routes.

## Normalized import payload

```json
{
  "chain_id": 42424242,
  "contract_address": "0x1111111111111111111111111111111111111111",
  "tx_hash": "0x2222222222222222222222222222222222222222222222222222222222222222",
  "log_index": 0,
  "block_number": 123,
  "account_id": "user-one",
  "payer_address": "0x3333333333333333333333333333333333333333",
  "payment_asset": "native",
  "payment_amount_base_units": 1000000000000000000,
  "credits_granted": 100,
  "memo": "dev-chain funding receipt"
}
```

`account_id` is the hub account to credit. `payer_address` is the wallet that
funded the receipt. The default first asset is `native`.

## Idempotency

The same chain event must not credit an account twice.

R2A uses the existing `ChainEventRef.event_uid` and `CreditDeposit.deposit_id`
path. The idempotency input is:

```text
chain_id + contract_address + tx_hash + log_index
```

A second import of the same event returns the existing deposit and does not add
credits or create another transaction.

## Validation

The importer rejects malformed normalized receipts before touching the ledger.

Required checks include:

- positive `chain_id`
- 20-byte `contract_address`
- 32-byte `tx_hash`
- non-negative `log_index`
- non-negative `block_number`
- non-empty hub `account_id`
- 20-byte `payer_address`
- `payment_asset` of `native` or a 20-byte token address
- positive `payment_amount_base_units`
- positive `credits_granted`


## Operator smoke test

After starting the local hub, run the scripted R2A smoke check:

```powershell
python scripts/smoke_r2a_credit_import.py --hub-url http://127.0.0.1:8770
```

The script uses a unique fake transaction hash by default so it can be run more
than once against the same local ledger. It verifies:

- the R2A indexer status endpoint is alive
- the first normalized receipt import succeeds
- the duplicate import returns `idempotent=true`
- the duplicate import does not change account balance
- the duplicate import does not change purchase or transaction counts
- the purchase appears in `/api/hub/v1/credits/purchases`
- the `deposit_indexed` transaction appears in `/api/hub/v1/credits/transactions`

This is still a local-dev/operator smoke path only. It does not perform RPC
sync, credit-card processing, fiat checkout, request charging, or worker
settlement.

## Verification

```powershell
python -m py_compile main_computer/hub_credit_indexer.py main_computer/hub_credit_ledger.py main_computer/hub.py
python -m pytest tests/test_hub_credit_models.py tests/test_hub_credit_ledger.py tests/test_hub_credit_indexer.py tests/test_hub.py -q
python tools/build_contracts.py --project contracts --test
```

## Next phase

R2B can later connect this importer to RPC/event sync for
`HubCreditSale.CreditPurchased`. The R2A endpoint is deliberately useful first
as a deterministic, testable import seam.


## Pre-R2B contract smoke

Before adding RPC sync, run the contract-side smoke harness:

```powershell
python scripts/smoke_hub_credit_sale_container.py
```

This verifies the `HubCreditSale.CreditPurchased` event shape and then delegates
to the repo's container-aware Foundry wrapper:

```powershell
python tools/build_contracts.py --project contracts --test
```

It is a pre-RPC check only. It does not index logs, maintain cursors, charge
requests, or create a vault contract.
