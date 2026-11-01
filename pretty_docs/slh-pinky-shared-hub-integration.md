# SLH / SEH — Shared-Hub Pinky Promise Integration Laboratory

**Status:** Executable `run --pinky-integrated` lane. This is not the legacy `verify-local` in-memory laboratory and does not waive the other full laboratory-exit blockers.

## Objective and authority

Exercise the **same disposable SLH organization and wallet** against the **real three-Node Stable Hub smoke cluster backed by FoundationDB**, selected by the Stable/Experimental Hub laboratory's canonical `deploy/hub-topology/smoke-topology.json`. No production org SLH identity, mainnet funds, or arbitrary external Hub URLs are used. All three Hub identities and their durable execution backend must pass the established preflight. Paid jobs and Pinky sweeps are executed against smoke-hub3; cross-Hub work ownership handoff is **not** proven here.

One temporary public requester is funded through the existing lab account-funding operation, exactly as in the established paid-work laboratory. That request executes on a real enrolled organizational Worker through the **real SEH relay**, and the Hub records, claims, and settles the Worker's one-credit earning. The Hub settlement atomically makes that earned credit spendable in the **organization wallet**. No test code may directly fund the organization's wallet: the initial balance for its fresh wallet must be exactly 1 credit from paid computation.

Because P2 deliberately does **not** support private execution through SEH, the laboratory first drains public advertisement, stops the public Worker, and restarts the **same persisted organization** with its direct private-worker listener. The authenticated organization wallet and Hub remain the same. A newly enrolled direct Worker serves two authenticated private secure-model requests, generating **exactly 0.2 credits** in the P3 obligation journal. An authenticated administrative sweep triggers P4 through the Organization API; the production Pinky periodic sweep thread remains in use. Hub account balances must change by -0.2 and +0.2 respectively. A repeated signed Hub sweep and a second SLH sweep must not debit again. A local Requester cannot view the payment ledger. Prompts and signing keys are not written into the resulting report.

This is a *two-phase transport test on one organization*, not a claim that private-over-SEH has been implemented. The Reserve receipt is an **internal Hub credit transfer**, not an external/on-chain XLag payment.

## Run against the actual Hub laboratory infrastructure

From the repository root on the Windows laboratory host, with the configured FoundationDB development/smoke cluster available and the smoke Hub ports **free**, execute:

```powershell
python -m tools.slh_seh_lab.run_lab run --pinky-integrated --json
```

This command launches and verifies all three real smoke Hub child processes, supplying an ephemeral Reserve account to the disposable Hub processes and the temporary SLH. It never needs an organization's production signing key. Its temporary private key is kept in the disposable laboratory directory, never in the report. An unsuccessful real-Hub invocation must report `FAIL` and return a nonzero process status.

To use **already running** verified smoke Hubs instead, those Hubs must already be configured with the same Reserve address (`MAIN_COMPUTER_RESERVE_WALLET_ADDRESS`), and the operator must supply it explicitly:

```powershell
python -m tools.slh_seh_lab.run_lab run --use-existing-hubs --pinky-integrated --pinky-reserve-wallet 0xYOUR_PRECONFIGURED_SMOKE_RESERVE_ADDRESS --json
```

Incorrect or missing Reserve configuration fails closed. Never point either command at the production network or at the separate `dev-topology.json` deployment: this lane intentionally authorizes only the isolated smoke namespace. This is **Stable Hub smoke FDB infrastructure**, not the independent Experimental Hub's full acceptance suite.

### Evidence required for `PASS`

- Verified Hub service/cluster IDs for all three nodes, and durable FoundationDB execution preflight
- Paid request charged once, executed by a Worker through an authenticated local SEH relay, and the earning claimed/settled
- Exactly **1 credit** in the previously empty organizational credit wallet, created by paid work rather than lab funding
- Same persisted SLH organization restarted into supported direct private mode
- Two successful authenticated local requests, each with one fixed **0.1-credit** obligation, and replay without re-execution
- One authenticated aggregate Reserve sweep, actual Hub wallet debit and Reserve credit of **0.2 credits**
- Hub receipt replay and second SLH sweep do **not** double-spend; Requester cannot inspect accounting
- No simulated in-memory ledger, foreign namespace, synthetic organization-wallet funding, or a forged result may satisfy the lane

The integrated mode runs the verified real-process public-work plus Pinky cycle **once**; it does not then launch the separate legacy `_local_demo()` paid-work demonstration against the same growing FDB document. The legacy demonstration remains in ordinary `run` mode and is reported as `slh.status=NOT_RUN` in integrated mode, not as a pass. The integrated mode saves `pinky-integrated-checkpoint.json` before final report assembly, recording the verified three-Hub identity and whether real paid-work plus the Reserve sweep passed. A failed or missing Pinky proof produces `FAIL` and `pinky_internal_reserve_sweep=NOT_VERIFIED`; no in-memory or second-demo result may substitute. `pinky_internal_reserve_sweep=CONFIRMED_IN_FDB` means only the tested local Hub ledger transfer, never an external on-chain transfer.

Evidence is returned in `report.json` under `pinky_integrated` with opaque identifiers, credit-wei amounts, distinct transport disclosures, and the verified Hub identity. Existing limits remain: synthetic *public requester* lab funding, local loopback SEH, no public WSS, no live Coolify or external credit bridge, no private-over-SEH, no cross-Hub handoff. The original five resilience blockers plus the full laboratory-exit Pinky gate remain governed by `lab_exit.py`; a standalone successful integrated lane does not automatically certify overall `ACCEPTED`.

## Local regression tests

```powershell
python -m pytest -q tests/test_slh_pinky_shared_hub_lab.py tests/test_stable_hub_fdb_atomic_accounting.py
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3_p4.ps1
```

Or run the bundled PowerShell wrapper `tools/slh_seh_lab/verify_pinky_shared_hub.ps1 -RealHubs` to run both regression groups and then the real three-Hub economic cycle. The wrapper does not call the in-memory Hub laboratory as a substitute for the real Hub test.
