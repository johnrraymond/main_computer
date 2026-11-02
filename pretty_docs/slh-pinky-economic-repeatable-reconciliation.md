# SLH Pinky Promise — repeatable emulated economic acceptance

Command (requires the established, isolated three-Hub smoke topology and a running FoundationDB cluster):

```powershell
python -m tools.slh_seh_lab.run_lab run --pinky-integrated --pinky-repeat 3 --json
```

The existing PowerShell harness also accepts `-RealHubs -PinkyRepeat 3`. `--pinky-integrated` defaults to **3 complete cycles**; `--pinky-repeat 1` tests one. The maximum is 10. Ordinary `run`, other diagnostics, production Hub topology, and all external funds remain untouched.

## What is real and what is emulated

Each cycle creates a fresh SLH organization and private/local service process, an authenticated Worker, a fresh buyer account with a Hub-issued multisession authorization, and new organization and buyer wallet identities. The Worker answers an emulated `mock-model` job, **without calling any AI provider or loading a model**. The synthetic buyer receives two initial credits **only in the preexisting validated smoke/FDB namespace**.

The real Stable Hub routes the buyer's paid job over SEH to the managed SLH Worker and commits the charge and Wallet-authorized earning settlement into FDB. The organization then restarts its own service in direct private mode, completes two authenticated emulated requests, incurs two 0.1-credit obligations, and sweeps 0.2 credits from its newly funded organization wallet to the *same* Reserve account in FoundationDB.

Each cycle additionally proves the existing signed Hub sweep replay and the next no-op sweep, so a repeated cycle cannot be mistaken for an extra payment. No external chain transfer, Coolify public deployment, production AI inference, or private-over-SEH capability is claimed.

## Reconciliation gates

The `pinky-economic-reconciliation.json` receipt records individual cycles and their IDs (public charge, earning, paid request/session, two private requests, signed sweep, Hub receipt), plus the following independent FDB/accounting equalities per cycle:

- Buyer starts with exactly 2 credits and ends with 1. Its available and spent deltas both show exactly 1 paid credit; no hold remains.
- Organization wallet begins empty, earns exactly 1 credit from the Worker settlement, and the public paid-job worker account matches the organization wallet used by Pinky.
- Exactly two successful private requests create exactly 0.2 credits of outstanding obligations. After the sweep, both are marked paid and the outstanding amount is zero.
- Organization wallet decreases exactly 0.2 credits, while Reserve increases by exactly 0.2 credits; replay checks still pass.
- Buyer, organization and Reserve accounts are distinct. All per-cycle and cross-cycle identifiers remain distinct; the Reserve and Hub identities stay constant, and successive Reserve before/after balances are continuous.

Expected three-cycle totals: **3.0 credits** bought and earned, **0.6 credits** transferred to the Reserve, leaving each fresh organization wallet with **0.8 credits**. These are *expected checks*, not invented receipt contents: each balance is read from the actual Hub ledger before or after the transaction.

## Artifacts and failure semantics

- `pinky-economic-cycle-01.json`, `02.json`, `03.json`: individual, secret-free balance and ID receipts.
- `pinky-economic-reconciliation.json`: cumulative totals, uniqueness checks, continuity checks, status, and all individual receipts.
- `pinky-integrated-checkpoint.json`: updated after every completed cycle, so a later exception cannot hide prior completed evidence.
- Existing paid-cycle timeline, preflight stage timeline, Hub logs, and `report.json` remain available.

If one cycle fails accounting gates or violates cross-cycle uniqueness, remaining cycles are not executed; the run is a **FAIL**. If an HTTP, Worker, or FDB call raises midway, the latest completed-cycle reconciliation file is still present, and `report.json` links it. An incomplete run cannot become PASS. Checkpoints never authorize or perform payments; only the existing Hub and organizational sweep services can move credits.

The fresh wallet/org/worker identities provide **per-cycle logical isolation within the shared smoke/FDB namespace**. This does *not* claim a separate FoundationDB namespace per cycle or prove cross-Hub handoff. Full Windows/FDB repeatability acceptance remains to be performed after installation.
