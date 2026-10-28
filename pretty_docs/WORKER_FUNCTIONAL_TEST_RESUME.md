# Browser Worker functional test — resumption gate (2026-10-26)

## What has actually passed

The native-Windows browser acceptance harness has exercised real MetaMask,
Testnet selection, an active multi-session key, successful Work Now registration,
and a live Worker WebSocket. This is **not** certification of the Captain→Worker
work cycle. The latest live Captain flow received `worker_not_live` on its
one-credit attempt, then `requester_funds_unavailable` (HTTP 402) on the bounded
two-credit retry. The browser Worker did not demonstrate a correlated completed
request and successful execution result.

The relevant account previously showed approximately 3 completed bridge credits,
1 spent credit, 1.024 credit held by an unfinished accepted session, and 0.976
available. These are historical observations, **not** a fresh balance check.
The next step is read-only verification of the actual live account and session.

**Identified test-price defect:** The Worker default market price is 1,024
output tokens at 0.001 credit per token = **1.024 credits**. The old browser
harness submitted a request with a one-credit ceiling, explaining why a live
Worker could fail the first market selection. The patched harness determines
the exact requested wei from the Worker's configured offer, uses a two-credit
maximum on the *first* dispatch when necessary, and avoids a second paid retry.
This does not make any held credits spendable.


## Live-test prerequisites (do not skip)

1. Verify the deployed Testnet Hub generation, rather than assuming the local
   source is running in Coolify. The October 26 source contains a socket-close
   hold-release repair, but local tests and the lab do not deploy it.
2. The current patch makes owner close, market close, and unfinished-session
   payout release one FoundationDB transaction. A precommit crash cannot
   persist only half of that transition. An abrupt process death *before*
   close begins may still require an independent recovery procedure; this
   patch is **not** a full crash-recovery or historical-hold repair.
3. Reconcile the pre-existing `held` credit against the exact accepted session
   and payout ledger. Do not manufacture a refund, delete a pending bridge
   record, or bridge more credits because an aggregate balance is low.
4. Before the paid test, confirm a functioning local AI model, running viewport,
   Worker testnet connection, and a Hub credit balance sufficient for the
   Worker's advertised price.

Read-only local inspection:

```text
python tools/worker_browser_acceptance.py captain-pending
python tools/worker_browser_acceptance.py plan --mode work-cycle
python tools/worker_captain_funding_preflight.py
```

The funding preflight identifies the real Testnet *Captain* wallet via the
Captain CLI's `--prepare-only` mode, then GETs its public Hub account balance.
It does not use the separate disposable MetaMask Worker wallet. The default
check uses the Worker's default 1.024-credit price; during browser acceptance,
that price is calculated from the live Worker offer. It reports exact wei
values for `available`, `held`, and `bridge_completed`, and refuses another
Captain request when the unreserved balance and permitted cumulative-bridge
headroom cannot fund the price. `held` credit is never counted as available.
An aggregate hold balance alone does not identify the historical session, so
a failed preflight calls for session-specific FDB inspection through approved
Hub control-plane tooling, not arbitrary refunds or a new deposit.


A missing `game_projects/webgl-demo/game.json` when importing `app_control`
inside an exported `main_computer_test` archive is a known export-fixture
limitation; verify the actual repository's game manifest before treating this
as a live viewport defect.

## Offline patch regression

```text
python -m pytest -q tests/test_stable_hub_fdb_atomic_accounting.py tests/test_worker_captain_funding_preflight.py
node --test tools/worker_browser_acceptance/worker_work_cycle.test.mjs
python -m pytest -q tests/test_stable_hub_hardening.py -k "closed_socket_releases_unearned_accepted_hold_without_charge or closing_socket_after_success_does_not_refund_charged_work"
python -m pytest -q tests/test_worker_browser_acceptance_harness.py
```

The new fake-FoundationDB crash test aborts the transaction after all
owner/market/hold/session writes but before commit. It verifies that the
full document is unchanged, then retries, sees one atomic terminal failure
and release, and verifies repeat close produces no second refund.

These tests do not validate a live Testnet deployment. Stress against the
`smoke-topology.json` requires its Hub cluster and FoundationDB installed.

## Resume the browser-backed functional cycle

After deployment state and the old credit hold have been independently
verified/reconciled, start the actual Main Computer application on the Windows
workstation in one terminal:

```text
python -m main_computer.app_control --host 127.0.0.1 --port 8765 run
```

From a second terminal, use the one existing test harness, not manual wallet
or Hub request steps:

```text
python tools/worker_browser_acceptance.py run --mode work-cycle --authorize-worker-offer --authorize-captain-spend
```

This explicitly authorizes testnet work and credit-bearing Captain dispatch,
subject to the new read-only wallet balance preflight. When the Worker's exact
offer price exceeds one credit, the first Captain command has a two-credit
ceiling and does not send a lower-price speculative request. The Captain
itself enforces a separate cumulative bridge-funding cap, if funding is
actually authorized. No new deposit occurs during the funding preflight. If a request was accepted but is
not terminal, **do not rerun to generate a different smoke ID**: recover the
exact request/session first. Do not fund Mainnet or deploy to Mainnet.

Final evidence resides in
`runtime/reports/worker-browser-acceptance/acceptance.json`.
Require `ok: true`, `captainRequest.state: completed`, and matching request
and session IDs in `workDelivery` with `offerObserved` and `resultObserved`
true. Successful registration and Work Now HTTP 200 are only prerequisites.
Separate ledger charge, earning, and terminal settlement verification is still
necessary for full credit-accounting certification.

## Patch: offer-aware Captain price and read-only funding gate

The Worker UI defaults to 1,024 target tokens at 0.001 credit/token, or 1.024
credits. A one-credit Captain ceiling can exclude this otherwise live Worker
from the market. The work-cycle harness now parses both UI offer fields as
exact decimal credit-wei and selects the smallest authorized integer Captain
price ceiling (1 or 2). Offers exceeding 2 credits cause a refusal, not an
unbounded authorization.

Before submitting, it executes a **read-only** account preflight using the
verified Testnet Captain wallet and Hub balance endpoint. If existing available
credits cover the offered price, it submits with `--no-bridge` even when the
Captain price ceiling is 2. If insufficient but a bounded top-up remains
possible within the historical two-credit cumulative bridge cap, it follows
Captain's existing bounded-bridge rules. If neither can fund the quote, it
refuses before any Captain request or deposit.

Run the preflight independently (read-only; no wallet address in the output):

```
python -m tools.worker_captain_funding_preflight --required-credit-wei 1024000000000000000 --ceiling-credits 2
```

The result contains the live `availableCreditWei` and
`bridgeCompletedCreditWei` and whether `bridgeFundingRequired` is necessary.
This is a point-in-time check, not a reservation. A competing request or Hub
price change can still prevent admission. Testnet accounting and deployed-Hub
reconciliation remain prerequisites to full functional certification. Do not
re-run the paid test if the read-only preflight refuses; reconcile the exact
old accepted session and held credit first. The Python preflight and the
Worker price tests are offline-testable without Hub connectivity.

```
python -m pytest -q tests/test_worker_captain_funding_preflight.py
node --test tools/worker_browser_acceptance/worker_work_cycle.test.mjs
```

When Worker offer price is known, a `worker_not_live` 409 causes a single refusal
not another paid attempt. The older unpriced retry remains available only for
callers without a price preflight, so it cannot bypass the new ledger gate.


### Test-only affordable ACK offer

The native browser `work-cycle` harness now temporarily reduces the Worker's
**target output tokens** to at most 512 before Work-now registration. It does
**not** reduce the configured credits-per-token rate. At the default rate of
0.001 credits/token, 512 tokens advertises a maximum reservation of 0.512
credits. This is sufficient for the short nonce-ACK smoke task and fits within
the verified October 26 Captain balance of 0.976 credits. The normal 1024
output-token offer remains unchanged outside this test.

The harness waits for the Worker's real local settings POST to return an exact
matching `sellerTargetTokens` value, then registers the actual temporary offer,
verifies its total price against the read-only Captain funding preflight, and
submits at most one Captain request. It requests Work-now shutdown in `finally`
and restores the original target token budget via the same verified local
settings API even on failure. If restoration cannot be verified, the run
fails. An abrupt computer shutdown can interrupt `finally`; then restore the
previous target-token setting manually before running again.

To check the 512-token reservation without spending credits:

```text
python -m tools.worker_captain_funding_preflight --required-credit-wei 512000000000000000 --ceiling-credits 1
```

Only after checking the preflight, authorize the single Testnet work-cycle:

```text
python tools/worker_browser_acceptance.py run --mode work-cycle --authorize-worker-offer --authorize-captain-spend
```

The Hub ledger must still show a completed charge, the same request/session
IDs, and the real browser Worker result; merely affording the request does not
constitute acceptance.

### Stable Hub market-offer synchronization gate (October 26)

A Work-now invocation for an already-registered wallet previously reused
`signedWorkerConnection.worker` even when the current UI offer changed from
1,024 to 512 tokens. Authentication and WebSocket ping/pong continued to pass,
but the Stable Hub still had the old 1.024-credit market price. A Captain
request priced for 0.512 credits could therefore return HTTP 409
`worker_not_live` because no market candidate met its price ceiling.

The Worker backend now compares the **effective market offer** (ring, models,
capabilities and exact credit price) before reusing a Hub registration. An
offer change must take the actual authenticated WebSocket registration path,
which republishes the price and obtains a fresh Hub-issued connection ID.
For an unchanged effective offer, Work-now may reuse the registered session.

The live-session status exposes a narrow `market_proof` derived only from the
Hub's authenticated `hub.auth.accepted` market record: acknowledgment,
price in exact credit-wei, ring-3 support, chat-completions capability and
available capacity. Private worker IDs, wallets, multi-session authorizations
and connection identifiers remain redacted. The browser acceptance harness
requires this Hub-originated market proof to match the temporary 512-token
UI price **before** it runs the read-only funding preflight or issues the
Captain request. Missing or contradictory proof fails closed; a live socket
alone is not market eligibility.

Expected evidence before the Captain submission:

```text
Worker acceptance: worker.work-cycle.test-target-tokens.512
Worker acceptance: worker.work-cycle.live-market-price-verified
Worker acceptance: worker.work-cycle.offer-price-credit-wei.512000000000000000
Worker acceptance: captain.read-only-funding-preflight.existing_credits_sufficient
```

If the Captain still receives `worker_not_live` after verified market price,
ring, capability and capacity, inspect cross-Hub directory visibility and
ownership/lease continuity. Do not respond with a larger paid retry; the
known price has already been checked. Full end-to-end certification still
requires actual browser work delivery, completion with matching IDs, a
charge and a Worker earning verified against the Hub ledger.

## Testnet reserve siphon for a shortfall (October 27)

The Captain's ordinary bridge budget remains capped at **2 cumulative credits**.
That cap was already exhausted in the October 27 check (`bridge_completed = 3.0`,
`available = 0.464`); the temporary 512-token offer costs **0.512 credits**.
The new, independently authorized path does **not** reset or weaken the old
cap, issue administrative stipend credits, release held credits, or modify a
Hub/escrow ledger directly.

Only when the Worker is *already verified live at the exact price*, the Hub's
wallet-specific balance preflight reports `insufficient_credit_under_cumulative_cap`,
and the operator supplied `--authorize-reserve-siphon`, the harness requests
one small, guarded native payout from the **Testnet XLagBridgeReserve** to the
Mother-verified **Captain** wallet. The Captain submits a normal `depositFor`
escrow transaction, and the Hub completes and credits the specific deposit
using its existing chain-backed bridge completion endpoint. The harness
then performs the original read-only preflight **again**, and only submits
work once that independent check says the offered price is affordable.

Safety constraints: Testnet chain ID 42424241 and canonical Testnet Hub only;
XLag address from Mother's current contract registry; Captain and O2 officers
resolved from Mother's verified private state; both officer addresses
validated against the live reserve contract. The transfer uses the contract's
unaltered `proposePayout` -> `secondPayout` -> delay -> `executePayout`
governance (including belay/contest/expiry); no unsigned admin faucet or direct
mint. Maximum 0.2 credit-wei equivalent **bridged per attempt**, plus 0.005
native units reserved for the Captain's transaction fees; maximum 0.5 credits
reserved in the local UTC-day journal. The transfer is `shortfall + 0.025`
credits (unless this exceeds the 0.2-credit per-attempt limit), not a full-credit
refill. Larger deficits require operator investigation rather than more loops.
The daily journal is a local safety limit, *not* a chain-enforced lifetime cap.

To see the required credit without paying anything:

```
python -m tools.worker_captain_funding_preflight --required-credit-wei 512000000000000000 --ceiling-credits 1
python -m tools.worker_captain_reserve_siphon --required-credit-wei 512000000000000000
```

To explicitly authorize the reserve pathway for **one** live Worker functional test:

```
python tools/worker_browser_acceptance.py run --mode work-cycle --authorize-worker-offer --authorize-captain-spend --authorize-reserve-siphon
```

The old work-cycle command without the reserve authorization remains read-only
on a preflight funding denial. No Mainnet reserve call is permitted.

The siphon uses an exclusive recovery lock and an fsynced on-disk journal under
`runtime/state/captain/reserve-siphon/`. An interrupted or uncertain transfer
**leaves `active.json` in place**, disabling further reserve payouts until
an operator reconciles the contract proposal, transaction receipts, pending
Captain escrow deposit, Hub completion, and actual available balance.
**Do not delete the lock or retry a second siphon to repair uncertainty.**
Pending bridge deposits remain governed by Captain's existing recovery records
under `runtime/state/captain/pending-bridge-deposits/`. Neither a reserve
payout nor a successful test retroactively settles the older held 1.024 credits;
those must be independently reconciled against their original session.

Offline verification:

```
python -m pytest -q tests/test_worker_captain_reserve_siphon.py tests/test_worker_captain_funding_preflight.py tests/test_worker_browser_acceptance_harness.py
node --test tools/worker_browser_acceptance/worker_work_cycle.test.mjs
```

**Live funding and worker delivery have not been executed by this source patch.**
Require the observed Testnet proposal execution receipt, deposit completion,
Hub available-credit increase, and matching completed request/session/worker
result before declaring the functional test passed.
