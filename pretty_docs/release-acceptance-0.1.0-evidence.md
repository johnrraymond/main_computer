# Main Computer 0.1.0 — Acceptance Evidence Register

Revision 4 · November 24, 2026 · R01 closed; R02 blocked on public preflight

[Governing gates](release-acceptance-0.1.0.md) · [Patch plan](release-acceptance-0.1.0-patch-plan.md)

## Evidence rules

This is a durable index of operator-reported acceptance results and inspected patch contracts, not a new execution report. Preserve the original JSON/log files under their runtime paths, copying redacted versions into the candidate evidence directory before subsequent tests overwrite them. No source commit, runtime hashes or raw report attachments were supplied for the newest runs; candidate binding remains outstanding. A pass is valid for its stated test scope, not for a larger untested workflow. Do not store secrets, MSKs, cookies, private state, or unnecessary personal information in release evidence. The public resident wallet address is retained here as an operational comparison anchor; redact it from publicly distributed release materials if unnecessary.

## E-013A — Resident Mainnet financial startup (latest observation)

Command: `python -m tools.slh_seh_lab.org_msk_passthrough --resident`

Report: `runtime/reports/slh-org-msk-passthrough/resident.json`  
Schema: `mcel.slh.org-msk-passthrough.v1`  
Scope: `resident-org-read-only-preflight`  
Status: `PASS_PREFLIGHT_ONLY` · stage `read-only-complete` · blockers `[]` · missing `[]`

| Assertion | Result |
| --- | --- |
| resident_lms_default_discovered | PASS |
| resident_admin_verified | PASS |
| resident_marketplace_status_verified | PASS |
| resident_authorization_present | PASS |
| resident_mainnet_wallet_current | PASS |
| resident_financial_readiness_verified | PASS |
| resident_test_session_revoked | PASS |

| Observation | Value |
| --- | --- |
| Wallet public address | `0x42cafb2ffcb6e806827f318a9538ba0f7f776249` |
| Wallet state | CURRENT |
| Network | mainnet |
| Financial readiness | READY |
| Authorization present | true |
| Marketplace enabled | false |
| Connected Workers | 0 |
| Worker account check | NOT_TESTED_NO_CONNECTED_WORKERS |
| Available credit (wei) | 0 |
| Held credit (wei) | 0 |
| Spent credit (wei) | 0 |
| Bridge completed credit (wei) | 0 |
| LMS inventory | AVAILABLE; 3 identities |

**Interpretation:** P0-13 resident Mainnet initial readiness subgate PASS. Startup provisioned/established wallet authority as reported after the automatic-wallet patch. The diagnostic itself reads and authenticates; it does not issue MSKs or submit jobs. No connected Worker account correlation or funded transaction is proven. The zero balance and disabled employment are not financial initialization failures. At the time of this initial observation, restart proof and Windows ACL inspection were still required; both are now resolved by E-013B. Separate Testnet readiness and live network financial effects remain. Do not infer an earned balance field that was not returned.

### Superseded failures (retain for regression history)

| Earlier failure | Resolution evidence |
| --- | --- |
| `resident-admin-auth` RuntimeError | LMS-discovery/signing fix; administrator and cleanup assertions now pass |
| UNBOUND, MSK absent, network UNKNOWN | Current Mainnet wallet and authorization present |
| `PINKY_SWEEP_CONFIGURATION_REQUIRED` | Automatic create-once signer provisioning; financial readiness READY |
| `RESIDENT_MAINNET_WALLET_NOT_CURRENT`, `RESIDENT_MSK_NOT_PRESENT`, `RESIDENT_FINANCIAL_STARTUP_NOT_READY` | Latest blockers empty |

These are no longer current blockers for the observed instance. A recurrence on a later candidate must reopen the affected gate.

## E-014A — ORG MSK and bidirectional passthrough

Command: `python -m tools.slh_seh_lab.org_msk_passthrough`  
Report: `runtime/reports/slh-org-msk-passthrough/report.json`  
Operator result: `ORG_MSK PASS stage=complete` — 16/16 assertions.

| Assertion | Result |
| --- | --- |
| org_requester_role_verified | PASS |
| msk_issue_admin_only | PASS |
| org_fresh_msk_signed_and_accepted_by_hub | PASS |
| org_signing_origin_verified | PASS |
| org_wallet_bound | PASS |
| org_worker_public_result_and_credit | PASS |
| org_public_request_anonymous_denied | PASS |
| org_public_request_worker_role_denied | PASS |
| org_public_spending_admin_only | PASS |
| org_public_spending_default_denied | PASS |
| org_public_price_limit_denied | PASS |
| org_public_forged_msk_denied | PASS |
| org_requester_public_result_and_debit | PASS |
| org_requester_replay_no_double_charge | PASS |
| org_public_budget_exhaustion_denied | PASS |
| credentials_not_returned | PASS |

Corroborating observations: `worker_authorization_origin: ORG_SERVICE`; issuance, requester submit/result HTTP 200; anonymous requester HTTP 401 and Worker-role HTTP 403; one public charge and one earning, each 1 credit (10^18 credit-wei). Real ORG-SLH and Stable Hub software, disposable wallets and synthetic funds. Direct ORG TLS requester lane. This proves the signing and passthrough implementation; it does not establish live Mainnet/Testnet settlement or remote requester relay.

## E-009A — Deployed SEH dual employment

Command: `python -m tools.slh_seh_lab.dual_employment`  
Report: `runtime/reports/slh-seh-dual-employment/report.json`  
Transport: `CANONICAL_DEPLOYED_WSS_ISOLATED_CHANNEL`  
Operator result: all 26 employment assertions true, `employment_status: PASS`; outer `status: BLOCKED` solely for `GREAT_LIBRARY_REQUESTER_PATH_NOT_VERIFIED`.

The operator explicitly accepted the protocol-conforming mock requester as sufficient. P0-09 employment evidence therefore passes its intended scope; do not claim actual Great Library application integration. R01 has corrected the obsolete harness decision while preserving the original report and limitations. A fresh successful run now returns `PASS` with `great_library_status: REPRESENTED_BY_PROTOCOL_MOCK`.

Evidence includes enrollment and unapproved refusal for both identities, isolated tunnel authentication, single Worker registration, public/private jobs on the same generation, public withdrawal before private reservation, exact Pinky obligation and replay safety, readvertisement, reconnect/re-registration, revocation, and uninterrupted primary tunnel. Health/readiness/lab-capabilities HTTP 200; postflight ready. Synthetic public debit and earning each 1 credit; lab ledger settlement made 1 credit available; private obligation 0.2 credit, exactly once. No chain bridge or Reserve settlement; production price not established. Deterministic computation, not production model inference; explicit reconnect, not proof of automatic production Worker recovery. No canonical Worker UI or production organization enrollment in this isolated channel.

## Other retained acceptance evidence

| Gate | Operator-reported proof | Remaining limitation |
| --- | --- | --- |
| P0-05 | Secure smoke 9/9; anonymous HTTP 401; trusted HTTPS and challenge/response; secure session revoked | Candidate and persistent ordinary-session restart proof |
| P0-05/08 | Canonical Worker identity selection PASS via secure automated test | Not enrollment or persistent Worker connection by itself |
| P0-06 | SLH protected reads PASS with no supplied admin ID | Lifecycle/negative and candidate-bound evidence |
| P0-08 | Canonical Worker local SLH approval 16 assertions true; signed request, Pending, approval, auth, revocation and cleanup; disposable LMS identity retained | Actual resident persistent managed Worker registration via SEH UI not yet verified |
| P0-08 | Isolated direct SLH and SEH enrollment-to-connected-Worker and revocation tests: 3 passed including probe safety | Not resident Mainnet registration |
| P0-10 | Earlier Testnet work-cycle PASS (reported stage workerDelivered) | Recertify against current canonical build |
| P0-11 | Live requester-enforcement PASS, `stage=noPaidSubmission`, policy read and guard verify HTTP 200 | Positive paid request and current regression evidence |
| P1 | Calculator, File Manager, Code Editor, Task Manager, SLH and Worker read-only live tests passed | Mutating app workflows not inferred |

## Patch provenance and regression history

The following archives were inspected for the updated architecture. These are historical delivered artifacts, not a candidate manifest or a guarantee that every file is installed.

| Archive (November 24, 2026 unless stated) | Scope / prior reported validation |
| --- | --- |
| `mcel_org_msk_passthrough_implementation_20261124.zip` | ORG signing and requester gateway; 16 functional assertions, 102 targeted regressions |
| `mcel_org_resident_lms_auth_fix_20261124.zip` | Resident authentication via LMS; 49 regressions |
| `mcel_org_slh_financial_startup_20261124.zip` | Startup, readiness, renewal/recovery; 106 regressions and 16 functional assertions |
| `mcel_org_slh_automatic_wallet_provisioning_20261124.zip` | Create-once signer, protected storage, address anchor, legacy import; 140 passed, one reported SEH metadata failure, isolated passthrough 16/16 |

The reported metadata failure (obsolete `slh_id` expectation) still needs triage against current candidate; do not dismiss it in final certification without confirming scope. The wallet patch's Windows ACL behavior requires actual Windows verification. R01 fresh regression evidence follows; actual resident Windows inspection is still outstanding.

## R01 implementation verification (Nov 24)

Source: `main_computer_test-20261124-155306.zip` layered with the previously accepted automatic-wallet provisioning patch and release documentation. The export lacked those files; the existing provisioning logic is restored, not redesigned. No resident configuration or private state was touched.

- 107 focused regression tests PASS; 1 skipped because actual Windows ACL metadata requires Windows. Includes automatic wallet creation, MSK acquisition/reuse/renewal, recovery and revocation, restart comparison rejection of same-process/changed-wallet/changed-policy/unsafe ACL evidence, and the corrected local real-relay dual-employment verdict.
- Actual isolated ORG-SLH/Stable Hub passthrough run: 16/16 PASS, `worker_authorization_origin: ORG_SERVICE`. Synthetic credits only.
- `--resident --restart-baseline` records `resident-restart-before.json`; `--resident --verify-restart` records `resident-restart-after.json` and requires `PASS_RESTART`. Both inspect actual process identity, fresh readiness, public binding, unchanged administrative policies, and Windows signer ACLs; no credential contents or paid actions.
- Actual resident Windows restart/ACL evidence and a corrected deployed relay rerun have not been collected for R01. Historical deployed employment 26/26 and Mainnet preflight 7/7 remain unchanged.

### R01 resident restart follow-up (Nov 24)

- Resident `--restart-baseline` and `--verify-restart` returned `BLOCKED` at `resident-restart-evidence` with `OPERATION_NOT_ADMITTED`. No valid baseline was established. This is an admission failure, not evidence that the wallet changed or the signer ACLs were unsafe.
- Root cause reproduced: the local pinned `OrganizationWorkerClient` allowlist omitted `GET /v1/org/admin/marketplace/requester-policy`, although the ORG service already implements that read with administrator authorization. The R01 follow-up admits only this exact bodyless GET; writes, arbitrary paths and missing authentication remain refused. No server authorization or remote SEH allowlist changes.
- 98 targeted regression tests passed after the fix. The new regression reproduced the exact refusal with the old allowlist. These are isolated tests, not actual Windows ACL or restart evidence.
- Rerun `--resident --restart-baseline` and require `BASELINE_READY` before performing a normal idle ORG-SLH restart and running `--resident --verify-restart`. P0-13 restart/ACL subgate remains open until `PASS_RESTART`. Prior Mainnet readiness and deployed dual-employment evidence are unchanged.

### R01 Windows directory ACL follow-up (Nov 24)

- Following the policy-read admission correction, the operator's resident `--restart-baseline` passed five of six checks: resident preflight, stable verified process, fresh financial readiness, signer file ACL, and binding/policy observation. Only `windows_signer_directory_private` failed. No valid restart baseline was established. This is not a wallet/MSK binding failure; the exact rejected ACL attribute was not recorded by that version of the probe.
- Source review: wallet provisioning previously hardened the directory only on initial creation using a partial `icacls` principal cleanup; existing wallets did not reconcile Windows ACLs before loading the signer. The correction verifies both nodes and, only in the authorized provisioning/readiness lane, reconciles the existing wallet-directory DACL to the same trusted SID policy (operator, SYSTEM, Administrators), preserving key bytes and ownership. Unknown owners, unsafe paths, unverified resulting ACLs, and insecure existing key-file ACLs fail closed. No recursive ACL reset, wallet replacement, or spending policy change.
- The read-only restart probe now reports bounded ACL failure reason codes in its JSON and console output. The acceptance predicate remains unchanged in substance; no ACL failure is converted to a pass.
- 99 focused regression tests passed; two Windows-only tests were skipped on Linux. Actual NTFS/PowerShell reconciliation must still be verified on the resident Windows installation. Apply the patch and restart ORG-SLH to load the new code, then capture a valid `BASELINE_READY`, perform a separate normal restart, and require `PASS_RESTART`. Historical Mainnet readiness and dual-employment evidence remains scoped as before.

### R01 exact DACL replacement follow-up (Nov 24)

- Resident post-repair baseline remains BLOCKED. The new report identifies `UNEXPECTED_PRINCIPAL` on the wallet directory; the protected signer file still passes. Financial readiness is BLOCKED (`ORG_SIGNER_ACL_UNVERIFIED`), with the previous wallet balance observation STALE (`OBSERVATION_EXPIRED`). The organizational wallet, Mainnet binding, MSK presence, and disabled marketplace/requester policies remain observed. A fresh readiness timestamp is not a READY verdict.
- Replace the incremental rule-purge implementation with a fresh protected DACL constructed from SDDL and written via the Windows DirectorySecurity Access section only. Read back every raw ACE, reject inherit-only grants and unsupported ACEs, verify the same owner, and report bounded categories of unexpected principals without exposing account identifiers. Do not change the protected key file, wallet identity, Hub, or spending policy. The specific unexpected principal and reason the first repair left it behind are not established by the resident report.
- Run the disposable NTFS repair tests first (`python -m pytest -q tests/test_slh_wallet_acl_reconciliation.py`), then restart resident ORG-SLH and require `BASELINE_READY`, a separate restart, and `PASS_RESTART`. Focused regression run: 145 passed, 4 skipped (Windows-only). Actual Windows results remain pending; do not certify from Linux-only simulations.

### R01 Windows raw ACL readback follow-up (Nov 24)

- The operator ran the disposable NTFS suite: 15 passed, 3 failed, 1 skipped. All three failures occurred during initial wallet provisioning, before the test injected an extra ACE or attempted its repair. The raw PowerShell ACL reader raised `System.InvalidCastException` in its `ForEach-Object` block, wrapped as `ORG_SIGNER_ACL_OPERATION_FAILED`. This establishes a readback defect, not a verified failure of the new protected-DACL replacement. No valid resident restart baseline exists.
- Correction: read every raw ACE by indexed `RawAcl` access and convert ACE type and flags to integers before bitwise tests. Keep the exact DACL replacement, trusted principals, unknown-owner and unsupported-ACE refusals, and independent file/directory verification intact. Add a standalone NTFS readback test before wallet provisioning, including detection of an explicitly injected inherit-only ACE. The traceback does not identify which expression caused the cast exception; this change removes both pipeline enumeration and enum-valued bitwise operations from that block.
- Focused Linux regression: 78 passed, 5 skipped (Windows-specific). The actual PowerShell/NTFS fix remains unverified. First rerun `python -m pytest -q tests/test_slh_wallet_acl_reconciliation.py` on Windows. Only after its Windows cases pass, restart ORG-SLH to load the corrected reader, require `BASELINE_READY`, then perform a separate restart and require `PASS_RESTART`. No production wallet replacement, manual ACL changes, or release-gate relaxation.

### R01 Windows unmapped-SID fixture follow-up (Nov 24)

- Operator NTFS run: 19 passed, 1 failed, 1 skipped. The Windows ACL reader, Users-group ACE repair, and Creator Owner ACE repair passed. The only failure was fixture setup: `icacls /grant` rejected the deliberately unmapped SID with Win32 error 1332 before injecting an ACE. This was not a production repair failure.
- The operator then ran a disposable-wallet diagnostic using direct SDDL insertion of the same unmapped SID. All seven emitted checks passed, including actual SID injection, unexpected-principal detection, key-file privacy, directory repair, unchanged wallet/key bytes/file ACL/directory owner, and overall PASS. This is actual Windows NTFS evidence for the previously unexercised repair case.
- Update only the failing fixture to use that verified direct SDDL insertion and require independent readback of the injected SID before invoking repair. Production code and acceptance policy are unchanged. The full corrected Windows pytest run and resident baseline/restart comparison remain pending. Do not mark P0-13 closed until `PASS_RESTART` is recorded.

## E-013B — Resident Mainnet restart and Windows ACL acceptance (R01 closure)

Operator results, November 24, 2026 (November 25 UTC):

- `python -m pytest -q tests/test_slh_wallet_acl_reconciliation.py`: 20 passed, 1 skipped in 263.47 seconds. The previously failed unmapped-SID fixture is resolved. The skip reason was not included in the posted output.
- `python -m tools.slh_seh_lab.org_msk_passthrough --resident --restart-baseline`: `BASELINE_READY`; all six baseline checks passed. Report: `runtime/reports/slh-org-msk-passthrough/resident-restart-before.json`.
- `python -m tools.slh_seh_lab.org_msk_passthrough --resident --verify-restart`: `PASS_RESTART`; all five comparison checks and all six current checks passed. Report: `runtime/reports/slh-org-msk-passthrough/resident-restart-after.json`.
- Verified process replacement: PID 36736 (creation time 1795568928.7589304) to PID 13880 (creation time 1795569226.2232232). Stable process within each observation.
- Same wallet address as E-013A, chain 42424240, cluster `main-computer-mainnet-hubs`, Hub `mainneta-hub1`, URL `https://mainnet-hub.greatlibrary.io`, trust `SYSTEM_TLS`.
- Both directory and key file ACL checks true on Windows, before and after. Mainnet wallet CURRENT, financial readiness READY, authorization present, all seven resident checks true and no blockers or missing checks at both observations.
- Marketplace remained disabled (revision 1); requester remained disabled (revision 0, zero budget and maximum); all returned balances remained zero and no Workers connected.

**Decision:** PASS resident Mainnet startup, actual restart persistence and ACL subgates. Exact MSK reuse/renewal was tested in isolated fixtures; resident report verifies current authorization, not secret equivalence or a fresh issuance event. Separate Testnet financial readiness, funded operations, and candidate provenance remain open. No further R01 wallet implementation required.

## E-009B — Corrected deployed SEH dual employment (R01 closure)

Operator command: `python -m tools.slh_seh_lab.dual_employment`.

Final console: `SEH_EMPLOYMENT PASS employment=PASS stage=complete reason=verified report=runtime\reports\slh-seh-dual-employment\report.json`.

All 26 assertions passed: isolated relay target; worker/requester pending, unauthorized refusal and approval; managed listener; authenticated isolated tunnel; single Worker registration; public eligibility; public job/accounting and same Worker; earned credit available; correct private computation and same Worker; exclusive reservation after Hub close; replay/exact obligation; readvertisement; public drain; old Worker unregister; tunnel reconnect; same identity reregister; revoked Worker unavailable; connection/challenge refusal; primary tunnel still connected. Preflight healthz, readyz and lab-capabilities each HTTP 200.

**Decision:** PASS P0-09 in its defined scope: actual deployed SEH isolated WSS channel, disposable ORG, protocol-conforming mock requester, deterministic compute and synthetic in-memory Hub accounting. No direct Great Library application requirement. The posted console establishes the final verdict and all assertions; the latest raw JSON was not supplied, so preserve the actual runtime report rather than reconstruct its fields. This is not proof of funded Mainnet/Testnet work, primary-tunnel Worker UI execution, remote requester passthrough, automatic reconnect or on-chain settlement. The historical Great Library blocker is superseded.

**R01 final disposition:** COMPLETE. R02 is next. These are operator-reported results recorded from the conversation, not independently rerun by the documentation patch. Candidate provenance remains to be bound.

## Open release evidence queue

| Next | Gate/subgate | Status |
| --- | --- | --- |
| R01 | Resident same-wallet restart, Windows ACL | PASS — E-013B |
| R01 | Correct dual employment report decision | PASS deployed 26/26 — E-009B |
| R02 | Current deployed network/Hub identity, routing and isolated crash durability | NOT_RUN for candidate |
| R02 | Separate Testnet ORG financial readiness | NOT_RUN |
| R03 | Resident canonical managed Worker registration via deployed SEH; organizational account mapping | NOT_RUN |
| R03/R04 | Actual Testnet public earning, ORG requester debit and private employment | NOT_RUN for resident path |
| R03/R04 | Actual Mainnet public earning and requester debit | NOT_RUN; requires bounded authorization |
| R04 | Remote SEH requester passthrough if shipped | NOT_RUN |
| R05 | Actual settlement, bridges, transfers, Pinky Reserve and policy reconciliation | NOT_RUN for required live scope |
| R06 | Candidate fingerprint, consolidated release gate, package, operator approval | NOT_RUN |

**Release decision: NOT YET CERTIFIED.** Do not count partial readiness as a complete economic release, nor repeat already verified implementation work without cause. R01 is complete; the next action is R02 current network/Hub verification, followed by resident employment/financial evidence.


### R02 initial public-network smoke correction (2026-11-24)

Operator evidence: Testnet smoke stopped on the deployment manifest RPC URL `http://198.199.75.153:30010` vs the canonical `https://testnet-rpc.greatlibrary.io`; Mainnet reached `mainneta-hub1` but its retired `/api/hub/v1/health` route returned HTTP 404. Neither result certifies a chain or Hub outage. Snapshot comparison also finds Testnet deployment manifest escrow and Reserve addresses differ from the committed public contract registry. This is an unresolved manifest reconciliation issue, not evidence that either address set is deployed correctly.

R02 correction: public read-only smoke now uses `/health`, `/api/hub/v1/hub-identity`, `/api/hub/v1/topology`, canonical HTTPS RPC, and current public contract config. It records chain/Hub identity, routing contract advertisement, runtime bytecode presence and hashes, and manifest discrepancies independently. No private wallet or funded balance is required for a read-only public preflight. A manifest mismatch still blocks overall acceptance, but does not prevent canonical live observations. No manifest, network configuration, Hub, or private state is changed. Reports (including blocked results): `runtime/hub/hub_network_client_smoke_mainnet.json` and `runtime/hub/hub_network_client_smoke_testnet.json`.

Focused regression suite: 11 passed (including both observed failure cases); broader `test_hub_networks.py` collection blocked by missing `game_projects/webgl-demo/game.json` in supplied snapshot. Live rerun pending. R02 remains open: this preflight does not prove all concrete Hubs healthy, actual routing, contract artifact equivalence, FDB recovery, Testnet organizational readiness, or real settlement. R01 closure is unchanged.


### E-001B / E-002B — R02 public-network smoke rerun (operator-reported, November 24, 2026)

Both corrected commands ran with `--skip-level4`. Edge timestamps are November 25 UTC; the operator's local date is November 24. Reports: `runtime/hub/hub_network_client_smoke_testnet.json` and `runtime/hub/hub_network_client_smoke_mainnet.json`. Overall `BLOCKED canonical_live=False` on both networks.

| Assertion | Mainnet | Testnet | Evidence boundary |
| --- | --- | --- | --- |
| canonical_endpoints | PASS | PASS | HTTPS profile check |
| hub | FAIL | FAIL | Stable Hub network kind mismatch |
| chain | FAIL | FAIL | `eth_chainId` denied by edge, no chain result |
| contract_registry | PASS | PASS | Local registry loaded |
| contracts | NOT RUN | NOT RUN | `NOT_RUN_CHAIN_OR_REGISTRY_UNVERIFIED` |
| manifest | PASS | FAIL | Testnet manifest differs from canonical public registry |

Cloudflare HTTP 403 / error 1010, `browser_signature_banned`, `owner_action_required: true`, `retryable: false`:

- Testnet: ray `a5fcf5f4fbe39bf9`, `2026-11-25T01:43:08Z`.
- Mainnet: ray `a5fcf5ff7e206289`, `2026-11-25T01:43:10Z`.

Source audit (not a new live test): Hub Control `render_runtime_topology()` emits `network.kind`; Stable Hub public identity reads only `network_kind`. This can produce a null public kind even with correct chain and network key. Actual identity payloads are needed to confirm the observed deployment has this exact defect. Earlier Mainnet 404 was a retired route assumption, no longer the current failing stage. The old SLH facade's public probe still contains that legacy route. See [current API contract](stable-hub-public-network-api.md).

Snapshot Testnet manifest contains `http://198.199.75.153:30010`, escrow `0x2279B7A0a67DB372996a5FaB50D91eAA73d2eBe6` and Reserve `0xa513E6E4b8f2a923D98304ec87F64353C4D5C853`; canonical registry contains HTTPS Testnet RPC, escrow `0x000000000000000000000000000000000000e5c0` and Reserve `0x000000000000000000000000000000000000c0de`. These are compared records, not newly verified chain addresses. Do not resolve by blind overwrite.

**Disposition:** P0-01 and P0-02 blocked; P0-04 and Testnet P0-13 remain outstanding. No conclusion of chain outage, incorrect contract runtime, or failed settlement. R01 and previous scoped passes unchanged. Documentation-only update; no tests executed, deployments changed, or funds spent.
