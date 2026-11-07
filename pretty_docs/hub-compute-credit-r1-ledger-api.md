# Hub Compute Credit R1 Ledger API

## Purpose

R1 introduces a live internal Compute Credit ledger for the hub API and admin
control site. It does not index chain events, charge requests, or settle workers
yet.

The ledger is intentionally off-chain. On-chain escrow deposit receipts and worker
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
GET  /api/hub/v1/credits/deposits?account_id=...
```

`/api/hub/v1/admin/bootstrap` now includes a `credits` section so the built-in
admin/control site can show the Compute Credit ledger summary.

## Credit issuance boundary

The legacy Hub no longer exposes a credit-minting HTTP endpoint. Credit
balances may still be initialized directly inside isolated test fixtures via
`HubCreditLedger.issue()`. Production issuance must use separately authorized
financial flows; no HTTP route can mint credits on behalf of an arbitrary
caller.

## Internal deposit event idempotency

`HubCreditLedger.record_deposit()` supports idempotent `CreditDeposit`
bookkeeping for trusted verified events. Event identity uses:

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
