# SLH P4 — Periodic Reserve Sweep Verification

**Scope:** A periodic, aggregate, wallet-signed credit transfer using the existing Stable Hub payout ledger, not one payment per internal request. The signing file is never copied to a Worker. No request body, prompt or response is sent with an accounting sweep.

## Implementation and configuration

`PinkySweep` runs when the private-request service is available. On Organization Service startup and every 300 seconds thereafter it reads verified wallet balances, forms a batch of whole pending P3 obligations (0.1 credit each), signs a Hub-specific instruction using the exact organization wallet key, transfers credits through `/api/hub/v1/credits/pinky-reserve/sweep`, and records the returned authoritative receipt locally. A pending sweep is retried **unchanged** after an outage. The Hub atomically debits the organization and credits the configured Reserve wallet account.

An authorized administrator must set the following in the Organization Service environment: `MAIN_COMPUTER_RESERVE_WALLET_ADDRESS` (real Reserve-controlled wallet address) and `SLH_PINKY_SWEEP_SIGNER_FILE` (path to the protected organization-wallet signing key). The Stable Hub must have the **same** `MAIN_COMPUTER_RESERVE_WALLET_ADDRESS`. Never put a key or demo address in code, documentation commands, logs or a Worker. The key must correspond to the P1-bound wallet; a marketplace multisession key does not grant spending authority. Without these values transfers fail closed while obligations remain due.

The internal Hub credit receipt confirms an account-to-account transfer. It is **not** proof of payment into an XLag on-chain smart contract. If the chosen Reserve requires that, a separate explicit bridge capability is necessary.

## Windows tests (PowerShell, from repository root)

```powershell
python -m pytest -q tests/test_slh_pinky_periodic_sweep.py
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3.ps1
python -m pytest -q tests/test_slh_operational_service.py
python -m tools.slh_seh_lab.run_lab verify-local --json
```

These tests prove unit/integration correctness and local lab behavior but do not remove the sixth P0 laboratory-exit blocker. Verify production signing-key custody, actual Reserve account ownership, persisted three-Hub transaction semantics and crash/finality scenarios separately before operational acceptance.


## Integration with real Hub processes

P4's focused SQLite/ledger tests are not the same as persistent-Hub
acceptance. See [Shared-Hub Pinky Integration](slh-pinky-shared-hub-integration.md)
for the combined real Organization Service / SEH / three-Hub smoke FDB test.
This additionally verifies that settled public earnings actually appear in
the organizational wallet before a periodic Reserve sweep spends them.
