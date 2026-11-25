# Main Computer 0.1.0 — Release Patch Plan

Revision 3 · November 24, 2026 · R01 complete; R02 next

Governing document: [Release acceptance gate](release-acceptance-0.1.0.md). Historical and current proof: [Evidence register](release-acceptance-0.1.0-evidence.md).

## Objective and baseline

Release a secure distributed compute economy with Mainnet/Testnet, Stable Hub, ORG-SLH, SEH, Worker and Requester working together. Do not redesign wallet ownership or repeat the security migration. The resident ORG Mainnet wallet is CURRENT, MSK authorization is present, financial readiness is READY, and all seven read-only preflight assertions passed. Isolated ORG MSK and bidirectional passthrough passed 16/16; deployed-SEH dual employment passed 26/26. The public mock requester is sufficient. The resident wallet has zero credits, marketplace participation is disabled and no Workers are connected. These are operating conditions, not defects in startup readiness.

Each patch is separately reviewable and uses relative repository paths. Preserve the installed signer, wallet DB, identity authority, authorization state and pending ledger records. Use the current source snapshot including applied patches; do not apply replacements built from an older snapshot without checking for intervening changes. Do not ship private state or raw Markdown at ZIP root. R01 is complete on operator-reported Windows and deployed SEH evidence. R02–R06 remain planned. Bind the evidence to the release candidate before final certification.

## Patch sequence

### R01 — Close evidence gaps and normalize acceptance reports (no paid work)

**Status:** COMPLETE; P0-09, P0-13, P0-14. **Target:** `tools/slh_seh_lab/dual_employment.py`, `tools/slh_seh_lab/org_msk_passthrough.py`, related acceptance-contract tests.

- Remove only the obsolete `GREAT_LIBRARY_REQUESTER_PATH_NOT_VERIFIED` blocker. Require all 26 actual employment assertions, verified deployed transport and clean postflight for PASS. Keep the mock requester and synthetic settlement metadata. Regress true assertion failures and cleanup failures.
- Archive the operator's resident preflight before a normal authorized ORG-SLH restart. After startup, rerun the same diagnostic; compare public wallet address, Mainnet binding, authorization presence, readiness and unchanged marketplace/spending policy. Add a read-only comparison mode or helper to the existing test, not a separate browser/auth framework. Verify actual service restart, not merely two reads from one process. Never compare or print MSK secrets.
- Inspect Windows ACLs of the canonical signer directory/file using metadata only; verify no unauthorized access. Confirm existing signer recovery and renewal/outage tests remain green. No environment-variable provisioning should be necessary on the resident lane.

**Implemented R01 commands (run on Windows):**

```powershell
python -m tools.slh_seh_lab.org_msk_passthrough --resident --restart-baseline
```

Require `BASELINE_READY`. Perform the normal authorized ORG-SLH restart when idle and safe; allow financial readiness to become READY. Then run:

```powershell
python -m tools.slh_seh_lab.org_msk_passthrough --resident --verify-restart
python -m tools.slh_seh_lab.dual_employment
```

Require `PASS_RESTART` and `SEH_EMPLOYMENT PASS employment=PASS stage=complete`. Baseline and comparison evidence are retained separately under `runtime/reports/slh-org-msk-passthrough/`. The test does not stop ORG-SLH, change policy or spend credits; the dual-employment lane uses synthetic funds. A failed Windows ACL check remains a blocker, not an excuse to bypass or silently repair credentials. The prior live 26/26 deployed employment evidence remains valid in its historical scope.

Focused regressions:

```powershell
python -m pytest -q tests/test_slh_resident_restart_acceptance.py tests/test_slh_organization_signer.py tests/test_slh_financial_readiness.py tests/test_slh_org_msk_resident_preflight.py tests/test_slh_seh_dual_employment.py tests/test_slh_org_msk_passthrough_acceptance.py
```

**Implementation validation:** 107 passed, 1 skipped (actual Windows ACL fixture on Linux); isolated MSK/passthrough 16/16 PASS. **Exit achieved:** Windows suite 20 passed, 1 skipped; `BASELINE_READY`, `PASS_RESTART`, and corrected deployed `SEH_EMPLOYMENT PASS employment=PASS stage=complete` (26/26) observed. See E-013B and E-009B. R01 does not close P0-13's separate Testnet readiness or certify real settlement.

### R02 — Network-scoped release preflight and Hub integrity

**Priority:** NEXT; P0-01/02/04/13. **Target:** existing network-client smoke, Mother observations, Stable Hub lab, resident diagnostic.

- Verify current Mainnet/Testnet identities, genesis/contract manifests, advancing finalized blocks, actual deployed Hub topology, network-specific health/routing and storage evidence. Chain ID alone is insufficient (local test can share Testnet ID).
- Extend resident diagnostic to explicitly select and verify the intended network and instance in the test report. Present mode is hardcoded to Mainnet. Testnet must use a separately scoped ORG state/wallet binding; do not repoint or clear the resident Mainnet binding. Reuse canonical registry and automatic signer implementation. No manual endpoint or identity ID input in normal test execution.
- Run isolated FDB crash/recovery and cross-Hub reservation/accounting tests. Do not disrupt public Hubs. Collect candidate deployment fingerprints.

**Existing entry points:**

```powershell
python scripts/smoke_hub_network_client.py --network testnet --skip-level4
python scripts/smoke_hub_network_client.py --network mainnet --skip-level4
python -m tools.stable_hub_lab.run_lab --verify-pathway-local --pathway-timeout 600
python -m tools.stable_hub_lab.crash_recovery --topology deploy/hub-topology/smoke-topology.json --timeout 110
```

Start with the two deployed read-only network smokes. The source currently requires the network deployment manifest and its funded smoke-client wallet record; if these are missing, diagnose discovery rather than create or fund an account automatically. Successful reports are written to `runtime/hub/hub_network_client_smoke_testnet.json` and `runtime/hub/hub_network_client_smoke_mainnet.json`. These checks alone do not prove finalized-block progress, every Hub, or the candidate topology. Review FDB lab runbook and isolated namespace before executing recovery tests. The smoke skips paid work. **Exit:** independently verified network/Hub observations, recovery evidence, separate Testnet financial readiness. No production funds moved.

### R03 — Resident Worker enrollment, connection and public earning

**Priority:** P0-07/08/09/10/14. **Depends on:** R01, relevant R02 preflight. **Target:** existing MCEL Worker identity/live acceptance, managed Worker runtime, ORG marketplace controller and lab helpers. Inspect actual candidate before changing any route.

- Reuse secure HTTPS session and automatic LMS administrator discovery. Use an approved disposable Worker identity for the live test; keep ordinary members intact. Verify actual canonical Worker → deployed SEH primary organizational tunnel → resident ORG → Worker Bank registration and account mapping. If the persistent managed Connect UI is missing, implement only that missing bridge; do not claim the lab proves it.
- Record prior marketplace/scheduler policy and obtain operator authorization before enabling public offers. Offer only bounded, eligible test capacity. Require Hub acknowledgement and prove the public offer belongs to the resident ORG wallet, not the individual Worker wallet.
- Use a protocol-conforming mock public requester with explicitly bounded funding and an independently checked result. First certify Testnet. For Mainnet, require separate authorization for the payer, offer, maximum liability, allowed model and duration; use the actual resident ORG wallet as earning beneficiary. No auto-funding or fallback between networks. Compare request, session, Worker identity, connection generation, owning Hub, charge and earning receipts. Verify claimed/settled spendable credit only at its real authoritative boundary.
- Withdraw the canary offer, restore policy, remove only disposable enrollment and revoke the test session. Record unsettled obligations, holds and cleanup. Keep the organization wallet itself persistent.

**Existing baseline commands:** `python -m tools.mcel_worker_identity_live_acceptance --approval-path --authorize-disposable-enrollment` (local approval only); `python -m tools.slh_seh_lab.dual_employment` (isolated ORG/deployed SEH); existing Worker live `wallet-msk`, `offer-registration`, `work-cycle` gates (see governing document). Extend these, not a parallel harness. **Exit:** actual resident managed registration and network-specific public earning verified. A synthetic job or merely opening a WebSocket is not a financial pass.

### R04 — Resident ORG requester passthrough and private employment

**Priority:** P0-09/11/14. **Depends on:** R03 or independently authorized funding, current wallet readiness. **Target:** `main_computer/slh/public_requests.py`, existing ORG MSK passthrough lab, requester integration and SEH relay where required. Fix only exposed functional gaps.

- Enroll a disposable requester; verify requester role and organization spending policy, default deny, price and daily/budget limits, request ownership and credential confidentiality. Grant a minimal explicit spending allowance. Submit through ORG-SLH (not directly to the Hub). Prove the actual organizational account debit, verified computation, delivery, exact charge and idempotent replay. Verify unauthorized requests are denied for the intended reason, not simply because the wallet has no funds.
- Use the approved SEH-connected Worker for private ORG computation. Verify acknowledged withdrawal before private reservation, correct result, one qualifying Pinky obligation, readvertisement and no leaked private payload. The mock requester remains sufficient for public employment. Retain the successful R03 Worker connection when testing same-generation dual employment.
- If remote requester passthrough over SEH is shipped, exercise its actual approved data lane. Existing direct ORG TLS requester proof does not certify this. Do not widen the SEH management allowlist; preserve authentication and role enforcement.
- Reconcile all holds and restore test-specific grants/settings, respecting concurrent operator revisions. Do not retry ambiguous submissions using a fresh request ID.

**Existing isolated control:** `python -m tools.slh_seh_lab.org_msk_passthrough` (16 assertions). **Exit:** real network ORG spending, private computation and accounting, authorized/denied boundaries proven. Keep P0-11 backend requester guard checks in addition to ORG gateway policy tests; they are distinct surfaces.

### R05 — Settlement, bridge, wallet transfers and Pinky Promise

**Priority:** P0-03/12. **Depends on:** verified network and wallet authority, actual earned/funded balance, explicit monetary authorization. **Target:** `main_computer/slh/organization_wallet.py`, `main_computer/slh/pinky_sweep.py`, existing Hub settlement/bridge and wallet tests, governing Pinky policy. No new wallet architecture.

- Reconcile actual credit earnings, claims, payout settlement, available/held/spent/earned balances. Verify restart durability and exactly-once settlement. Distinguish ledger credits, bridged credits and on-chain assets; a claim or pending batch is not a completed bridge.
- Establish authoritative production Pinky price and qualifying event (document says 0.1 credit; disposable test used 0.2). Do not silently change production price or treat fixture rate as policy. Verify one qualifying private obligation, no double assessment, sweep/Reserve transfer, authoritative receipt and insufficient-credit behavior. No public-earning percentage deduction invented by the test.
- Certify advertised bridge directions and wallet transfer operations with exact network/contract/recipient, amounts, fees and before/after authoritative balances. Run bounded Testnet first, then a separately approved minimal Mainnet canary if those capabilities are enabled for release. Never assume Testnet proof certifies Mainnet. No uncontrolled transfer, chain switch or spending from personal accounts.

**Exit:** actual receipts for all advertised economic effects; no irreconcilable holds or ambiguous transfers. If Mainnet paid canary is not authorized, record an explicit release restriction rather than certifying unrestricted economics.

### R06 — Executable 0.1.0 release gate and packaging

**Priority:** P0 aggregation; begin schema work early, finish after R01–R05. **Target:** `tools/release_diagnosis.py` and proposed `tools/release_acceptance.py` (new entry point, not yet implemented).

- Reuse existing tests and reports. Add a gate inventory keyed by P0-01 through P0-14 and subgate IDs. Bind evidence to source/build/deployment fingerprints, network, scope, test outcome, authorized effects, cleanup and report hash. Keep `PASS_PREFLIGHT_ONLY`, isolated PASS, deployed-channel PASS and funded-live PASS distinct. Preserve historical evidence without silently rewriting it. Fail closed on missing or stale affected evidence.
- Default to read-only planning/aggregation; make mutation and monetary authorizations explicit and bounded. The aggregator must never silently enable offers, issue a transfer, redeploy, rotate signer authority, or change networks. Report the next actionable blocker and existing command, not a generic incomplete status. Store versioned redacted evidence and an explicit final GO/NO-GO decision.
- Verify clean installation/update on supported Windows, durable wallet/identity and ledger state preservation, actual release package hash and rollback. Respect existing test freshness policies; recertify only affected paths. Tag exactly the accepted candidate after operator approval.

**Proposed command (not implemented):**

```powershell
python -m tools.release_acceptance --version 0.1.0
```

**Exit:** all applicable P0 gates pass on the candidate; no unresolved critical defect; package verified; explicit operator release approval. The documentation patch does not authorize or execute any of these future operations.

## Immediate next action

R01 is complete: preserve its baseline/restart and corrected deployed employment evidence. Proceed to R02, beginning with the existing read-only Mainnet/Testnet network smokes, then fill only the gaps those results expose. R03 follows the relevant R02 preflight. The wallet is already initialized. Do not generate it again. Marketplace remains disabled until an explicitly approved employment test. No manual Hub configuration is needed.
