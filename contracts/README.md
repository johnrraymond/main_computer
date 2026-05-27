# Main Computer Governance Contracts

## Alpha-Beta Lockout

`AlphaBetaLockout.sol` is the first contract-level expression of Main Computer governance. It models a four-member council split into two compartments for each proposal.

Alpha is the requesting compartment. Two council members are named as Alpha, lock a request by its `payloadHash`, and cannot later answer that same request.

Beta is the answering compartment. Two different council members are named as Beta. Each Beta member may answer once with `YES` or `NO`.

The contract classifies the two Beta answers as harmonic state:

- `ALLOW`: Beta answered `YES + YES`. The proposal may advance, but nothing is executed by this contract.
- `HOLD_AGAINST`: Beta answered `NO + NO`. The proposal is blocked.
- `SPLIT`: Beta answered `YES + NO` or `NO + YES`.
- `PHASE_CHANGE`: the final stored state for a split answer. A 2-by-2 polarized case must not execute directly.

This is not a treasury multisig. It does not transfer reserves, manage wallets, sign transactions, bridge assets, or move native currency.

Reserve movement is intentionally not implemented yet. The contract detects and classifies 2-of-4 governance states; it does not directly execute them.

The default state favors non-movement: proposals begin as `BETA_PENDING`, coherent negative answers block, split answers become `PHASE_CHANGE`, and no state contains reserve-drain logic.

## X-LAG Contract Reserve v0

`src/XLagBridgeReserve.sol` is the first contract-enforced bridge reserve surface. It keeps office identity and payout authority in contract state, not in local passcodes.

The four offices are:

- O0 Captain: command intent
- O1 First Officer: belay authority
- O2 Second Officer: second/check authority
- O3 Third Officer: second/check authority

Payout flow:

- Captain proposes payout intent.
- Second Officer or Third Officer seconds/checks the payout.
- First Officer may belay.
- Any current office may contest.
- Belayed or contested payouts cannot execute.
- If captain intent, beta second, delay, no belay, no contest, and sufficient balance are all present, anyone may call execution.

Office reset flow:

- Any current office may propose a reset.
- Reset requires three distinct current office approvals.
- Any current office may contest.
- Contested resets cannot execute.
- Reset execution is delayed.
- The new address must be nonzero and unique.

The backend never stores private keys, never signs transactions, and never moves native funds. Browser wallet users submit contract calls directly. Use separate browser profiles or windows to test O0-O3 because one wallet session usually exposes one active office account at a time.

## Hub Credit Bridge Escrow v0

`src/HubCreditBridgeEscrow.sol` is the active contract-side surface for the paid hub/worker marketplace.

It intentionally does **not** mint an ERC-20 token and does **not** emit one public chain transaction per AI request. Users deposit native value into escrow. The hub/bridge credits the user's internal Compute Credit account from the `CreditDeposited(...)` event, tracks request holds/charges privately, and only touches the chain again when aggregate spend must be rectified or unused escrow must be released.

This contract is C1 scope only:

- accepts native escrow deposits for a user/account
- emits a deposit receipt with account, payer, amount units, and memo
- lets the configured bridge controller rectify aggregate internal spend
- lets the bridge controller release reconciled unused escrow back to a recipient
- treats duplicate rectification/withdrawal ids as idempotent no-ops
- does not settle workers per request
- does not represent Compute Credits as a transferable token
- does not replace `XLagBridgeReserve`

Expected backend flow:

```text
HubCreditBridgeEscrow.CreditDeposited
  -> hub contract indexer
  -> internal account credit ledger
  -> user can spend Compute Credits on hub AI work
  -> bridge rectifies aggregate internal spend when needed
  -> bridge releases reconciled unused escrow on withdrawal
```

Worker payout and reserve movement remain separate phases.

## Hub Compute Credit settlement Phase 0

The hub/worker paid-work settlement schema is documented in:

```text
pretty_docs/hub-compute-credit-settlement-phase0.md
```

Phase 0 defines backend accounting and privacy objects only:

- `ChainEventRef`
- `HubCreditAccount`
- `CreditDeposit`
- `HubCreditTransaction`
- `HubCreditHold`
- `RequestCharge`
- `WorkerEarning`
- `WorkerSettlementBatch`
- `RequestReceipt`
- `WorkerQualityReport`

The chain remains a coarse funding and aggregate-settlement surface. Per-request
charges, exact worker earnings, quality reports, and request-to-worker mappings
remain inside the hub backend until later phases explicitly expose aggregate
settlement roots or payout claims.

