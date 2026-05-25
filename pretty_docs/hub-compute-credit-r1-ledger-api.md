# Hub Compute Credit R1 Ledger API

## Purpose

R1 introduces a live internal Compute Credit ledger for the hub API and admin
control site. It does not index chain events, charge requests, or settle workers
yet.

The ledger is intentionally off-chain. On-chain purchase receipts and worker
settlement batches will connect to it in later phases.

## Runtime storage

The hub stores the R1 ledger under the configured hub root:

```text
runtime/hub/compute_credits/ledger.json
```

The existing legacy worker payout queue remains separate under:

```text
runtime/hub/energy_credits/
```

## API endpoints

```text
GET  /api/hub/v1/credits
GET  /api/hub/v1/credits/accounts
GET  /api/hub/v1/credits/balance?account_id=...
GET  /api/hub/v1/credits/transactions?account_id=...
GET  /api/hub/v1/credits/purchases?account_id=...
POST /api/hub/v1/credits/admin/issue
```

`/api/hub/v1/admin/bootstrap` now includes a `credits` section so the built-in
admin/control site can show the Compute Credit ledger summary.

## Admin issue endpoint

`POST /api/hub/v1/credits/admin/issue` is a development/admin accounting
endpoint. It creates an `admin_adjustment` transaction and increases an internal
account balance.

Example payload:

```json
{
  "account_id": "user-one",
  "credits": 100,
  "memo": "manual test issue",
  "owner_address": "0xabc..."
}
```

This is not a public purchase flow. The later purchase indexer will import
`CreditDeposited` contract events and record `deposit_indexed` transactions.

## Idempotent deposit import

`HubCreditLedger.record_deposit()` already supports idempotent `CreditDeposit`
imports. The future chain indexer should use:

```text
chain_id + contract_address + tx_hash + log_index
```

through `ChainEventRef.event_uid`, which feeds the stable `deposit_id`.

## Not implemented in R1

```text
request quotes
credit holds
request charging
worker earnings
quality reports
contract event indexing
batch settlement
auth/token protection
```

Those are later phases.
