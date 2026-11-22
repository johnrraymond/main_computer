# MCEL Worker — live functional acceptance

The isolated `--mode isolated --profile full` test remains unchanged and proves
25/25 declared intents in 29 cases. This profile verifies **actual** effects
against the native Worker page, not the legacy `/applications/worker` route.

## Safety and prerequisites

* Testnet only: `http://127.0.0.1:8765/applications/mcel-semantic-worker`.
* Real localhost application must already be running. The runner does not
  start services, containers, production Hubs, or any other application.
* For wallet gates, prepare a disposable MetaMask account using the existing
  `python -m tools.worker_browser_acceptance install` and
  `python -m tools.worker_browser_acceptance cache-wallet` workflows. No real
  personal wallet, private-key flags, injected provider, or mock RPC is accepted.
* Individual gates require explicit approval flags. Work cycle has a maximum
  **two Testnet credits**; there is no automatic reserve payout or price retry.
  A read-only Captain funding preflight must succeed before any paid request.
* A failed/expired work-cycle run can leave a remote operation in progress.
  Reconcile the Worker/Hub and Captain ledger before any rerun. A process
  timeout is not evidence that the override was cleared.
* Mutating checks attempt restoration and refuse certification if restoration
  cannot be verified. Evidence contains categorical booleans, not wallet
  secrets, signed payloads, prompts, or provider messages.

## Commands

Inspect plan (no network effects):

```powershell
python -m tools.mcel_worker_full_functional_acceptance --mode live --action plan
```

Run one authorized gate at a time:

```powershell
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate wallet-msk --authorize-wallet-signing
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate offer-registration --authorize-wallet-signing --authorize-worker-offer
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate work-cycle --authorize-wallet-signing --authorize-worker-offer --authorize-captain-spend --authorize-settings-change
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate requester-enforcement --authorize-settings-change
```

The runner stores `runtime/reports/mcel-worker-live-functional/report.json` and
one deliberately redacted `live-<gate>-evidence.json` per gate. Only exact
schema, same native target, matching source fingerprint, complete postconditions,
and verified cleanup can count. Running the four commands separately preserves
other verified gates only when their evidence is still valid for the same code.

## Gate postconditions

* **Wallet/MSK**: real installed extension, connected disposable test account,
  exact Testnet chain and RPC, fresh authenticated MSK request and active Hub
  key. A previously active key does not certify fresh wallet signing.
* **Offer registration**: the native Register button reaches the existing signer;
  independently read back a Testnet Hub-accepted registration.
* **Work cycle**: save and later restore bounded offer settings, start a verified
  Work Now override, prove a live acknowledged Testnet Worker offer and price,
  run a capped real Captain request, and correlate its completed work to this
  Worker's live session before finishing the override.
* **Requester enforcement**: use the native Requester form to test disabled
  spending and a zero daily budget against the actual backend decision engine.
  A denial for some other reason (unreachable Hub, absent wallet, etc.) does
  **not** certify daily-budget enforcement. The current backend does not
  provide an isolated zero-daily-budget verifier, so this gate deliberately
  remains **INCOMPLETE** after its read-only refusal check. It does **not**
  temporarily enable paid overflow or claim that limits were enforced. A
  separate backend enforcement fix and atomic live verifier are needed before
  requester spending can be certified.

The live profile defaults to a 1,200-second overall gate deadline because a
real Captain work cycle can take several minutes beyond wallet setup. A timeout
leaves the work-cycle cleanup **unverified** and requires manual reconciliation.

No test changes the canonical Worker route. Four verified gates can produce
`PASS_LIVE_FUNCTIONAL` and `ELIGIBLE_FOR_REVIEW`, not an automatic cutover.
