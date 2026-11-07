# Internal Compute Credit Deposit Indexer

`HubCreditLedger.record_deposit()` retains idempotent bookkeeping for trusted,
verified deposit events. The internal `HubCreditIndexer` implementation can be
used by isolated test fixtures to verify ledger accounting, but no public HTTP
endpoint accepts a caller-supplied receipt as proof of payment.

Production credit funding must use the Hub's verified bridge-completion path,
which checks the authoritative on-chain deposit and deployment metadata before
crediting any account. A missing bridge deployment or invalid receipt is a
failure, not a reason to fall back to manual credit creation.

The legacy indexer status endpoint, when available to administrators, reports
only internal ledger status. It does not expose a funding or minting operation.

## Deterministic internal ledger testing

Tests may create disposable `CreditDeposit` and `ChainEventRef` records directly
through internal code. The event identity is derived from:

```
chain_id + contract_address + tx_hash + log_index
```

Repeat events must be idempotent. This is an internal accounting test seam, not
an authorization mechanism and not a public network service.
