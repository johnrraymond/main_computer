# Hub Compute Credit Settlement Phase 0

## Purpose

This document defines the Phase 0 accounting and privacy objects for paid hub
work. It is a design/schema phase only. It does not charge requests, index chain
events, deploy contracts, or settle workers yet.

The core product rule is:

```text
Compute Credits are prepaid service credits used to buy AI work on the hub.
Workers earn Compute Credits for completed work.
Compute Credits are not a public ERC-20 token in this phase.
```

## Privacy boundary

The hub is the privacy boundary between users, workers, and public chain
observers.

```text
Public chain sees:
  deposits
  aggregate settlement totals
  reserve/governance movement

Hub admin sees:
  user account ledger
  exact request charges
  exact worker earnings
  request-to-worker mapping
  quality reports

User sees:
  their deposits
  their holds
  their request charges/refunds
  their report receipts
  no raw worker identity

Worker sees:
  their aggregate earnings
  payout status
  no user identity by default
```

A public observer should not be able to reconstruct which worker handled each
request from settlement values or public events.

## Phase 0 objects

The backend schema lives in `main_computer/hub_credit_models.py`.

### ChainEventRef

Stable on-chain event identity.

```text
chain_id
contract_address
tx_hash
log_index
block_number
event_uid
```

Future indexers must use this as the idempotency key for imports.

### HubCreditAccount

Internal service-credit account.

```text
account_id
owner_address
available_credits
held_credits
spent_credits
earned_credits
metadata
```

This is not an on-chain token balance.

### CreditDeposit

On-chain purchase/deposit observed by the hub.

```text
deposit_id
account_id
payer_address
payment_asset
payment_amount_base_units
credits_granted
chain_event
status
memo
```

A deposit creates an internal ledger transaction only once per `ChainEventRef`.

### HubCreditTransaction

Append-only internal ledger entry.

Supported transaction types:

```text
deposit_indexed
hold_created
hold_released
request_charged
worker_earned
refund_issued
batch_settled
admin_adjustment
```

### HubCreditHold

A pre-dispatch reservation against a user balance.

```text
hold_id
account_id
request_id
credits
status
expires_at
released_at
charged_at
```

A request should not dispatch paid work unless it has a sufficient hold.

### RequestCharge

Final request charge.

```text
charge_id
account_id
request_id
hold_id
charged_credits
released_credits
worker_earning_id
```

Unused hold value is released. Completed work converts the appropriate portion
of the hold into a charge and worker earning.

### WorkerEarning

Exact internal earning for a worker.

```text
earning_id
worker_node_id
request_id
credits
worker_commitment
status
batch_id
```

The private representation contains the raw worker id and request id. Public
representations must use `worker_commitment`.

### WorkerSettlementBatch

Aggregate worker settlement window.

```text
batch_id
window_start
window_end
total_credits_exact
total_credits_published
dust_credits
worker_count
batch_root
status
```

Phase 0 defines value truncation but does not publish batches.

### RequestReceipt

User-facing completion receipt.

```text
request_id
account_id
charged_credits
worker_commitment
report_token
completed_at
model
```

The receipt lets the user report quality problems without revealing raw worker
identity to the user or the public.

### WorkerQualityReport

Report case linked to a request receipt.

```text
report_id
request_id
account_id
worker_commitment
report_token_hash
rating
reason
status
admin_notes
```

The hub can reveal the worker internally because it retains the private
request-to-worker mapping.

## Privacy helpers

### Worker commitment

```text
worker_commitment = HMAC(epoch_salt, worker_node_id | request_id)
```

The commitment is stable for the request and report window but should not reveal
the worker id without the hub's epoch salt.

### Report token

```text
report_token = HMAC(hub_secret, account_id | request_id | worker_commitment | ledger_version)
```

The token is opaque to the user and public observers. The hub stores/verifies
its digest and maps reports internally.

### Settlement truncation

Worker earnings can be rounded down before public settlement:

```text
exact earning:       1,237 credits
public settlement:   1,200 credits
dust rollover:          37 credits
```

The dust remains in the internal ledger and rolls into a later batch. This
reduces request-count/value reconstruction risk.

## Non-goals for Phase 0

Phase 0 intentionally does not implement:

```text
purchase event indexing
user balance mutation
request quote endpoints
request holds on live dispatch
worker payout claims
ERC-20 deposits
permit deposits
Merkle payout claims
admin authentication
```

Those are follow-up phases.

## Next phases

### Phase 1: internal ledger API

Add read/write storage and API endpoints for balances, transactions, deposits,
holds, request charges, worker earnings, and admin issuance in a local dev mode.

### Phase 2: request holds and charges

Connect hub request submission to quote, hold, charge, refund, and worker earning
logic. Failed/cancelled/expired requests must release holds.

### Phase 3: purchase indexer

Index `HubCreditBridgeEscrow.CreditDeposited` events into internal deposits
using `ChainEventRef` idempotency.

### Phase 4: worker settlement batches

Aggregate worker earnings by window, truncate public settlement values, carry
dust forward, and record batch metadata.

### Phase 5: quality reporting

Issue request receipts, accept user reports with report tokens, score workers,
and allow admin removal/drain flows.

### Phase 6: chain settlement

Move batched worker payouts through the controlled reserve/settlement contract
surface.
