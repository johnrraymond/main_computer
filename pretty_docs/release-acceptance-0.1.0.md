# Main Computer 0.1.0 — Release Acceptance Gate

**Status:** Proposed release policy; release candidate not yet certified  
**Revision:** 6 · November 25, 2026 — R01 accepted; R02 read-only network and isolated recovery subgates passed  
**Owner:** Release authority / operator  
**Recommended repository path:** `pretty_docs/release-acceptance-0.1.0.md`

**Companion records:** [Release patch plan](release-acceptance-0.1.0-patch-plan.md) · [Acceptance evidence register](release-acceptance-0.1.0-evidence.md). The latter supersedes older financial-readiness status claims; historical lab runbooks remain historical.

## 1. Release objective

Main Computer 0.1.0 establishes a working, secure distributed compute economy. The release must demonstrate that Mainnet and Testnet operate correctly, that their Stable Hubs route and account for work, that an organization can enroll and manage Workers through SLH and SEH, and that authorized Requesters can purchase or obtain computation subject to enforceable financial and access constraints.

The primary release test is an operational pathway, not a collection of application-rendering checks. A Worker must be able to enter the system, authenticate, perform work, and be accounted for correctly. The organization must be able to allocate the same Worker to public marketplace work or private ORG work without conflicting reservations.

A protocol-conforming disposable marketplace requester is sufficient to represent the Great Library in the employment test. The actual Great Library application is not a dependency of the 0.1.0 employment release gate. A deterministic computation with independently verified output is sufficient for the dual-employment transport and scheduling gate; a synthetic accounting fixture does not, by itself, certify real credit settlement.

## 2. Release decision

- **P0 — mandatory:** Every applicable gate in §3 must pass for the release candidate. A failure, blocked test, unverified assertion, missing evidence, or failed cleanup blocks release. A test cannot pass solely because an HTTP response is successful or the UI renders.
- **P1 — secondary:** Application functionality and packaging in §4. Failures may be deferred only if the affected functionality is explicitly identified as experimental, disabled, or outside the advertised release scope, and the failure cannot compromise a P0 pathway.
- **P2 — deferred:** New functionality, optimization, scale expansion and optional integrations in §4. Not prerequisites for 0.1.0.

A PASS is attached to a defined environment, scope, source/deployment fingerprint and observed postconditions. Isolated lab, deployed-relay lab, live Testnet and live Mainnet evidence are not interchangeable. Retain valid evidence for unchanged components when their dependencies and contracts are unchanged; rerun affected gates after changes. Respect the existing harness's own evidence freshness requirements. Never change a test expectation simply to make a failing implementation pass.

**Release rule:** `GO = all applicable P0 gates PASS + release package verified + no unresolved critical defects + explicit release approval`.

This document defines the proposed criteria; it does not itself execute tests, authorize spending, promote deployments, or certify the current build.

## 3. P0 release gates

### P0-01 — Mainnet and Testnet chain integrity

**Pass conditions:** Both deployed chains independently report the expected network identity, correct genesis/contract deployment, advancing finalized blocks and the authoritative validator topology. Mother/Coolify observations agree with live node and RPC observations; deployed image/source identity and runtime manifests are current. Verify RPC and contract reads, transaction fee readiness and absence of unintended cross-network fallback. Confirm expected quorum resilience in Testnet or an isolated equivalent, not by disrupting Mainnet.

| Network | Expected chain ID | Required environment |
| --- | --- | --- |
| Mainnet | 42424240 | Deployed public Mainnet |
| Testnet | 42424241 | Deployed public Testnet |

Local `test` shares Testnet's chain ID in the repository configuration. Therefore the chain ID alone does not establish network identity: check genesis, configured RPC, deployment manifest, and Hub identity together. Dev and local Test are not substitutes for either deployed network.

**Evidence:** Mother deployment observations, RPC/validator/block observations, verified contract manifest, Coolify deployment identity, network-specific reports. Use the current authoritative deployed topology rather than assuming the checked-in example lists all active Hubs or validators.

**Current evidence:** Both canonical public smokes now return `PASS_READ_ONLY canonical_live=True`. Chain IDs, block reads, PUSH0 gas estimation, registry, deployed runtime presence/hashes and manifests passed. The Testnet manifest was repaired; the new Mother pre-cleanup verifier passed independently against both live chains without writes. See E-001C/E-002C and E-001D. Remaining: candidate binding, finalized-block progression, authoritative validator/quorum evidence and any required artifact equivalence not already established. Runtime presence/hash is not compiled-artifact equivalence.

**Existing starting points:** `tools.mother_deploy`, `tools.mother_coolify`, `scripts/smoke_hub_network_client.py`; the Mainnet and Testnet topology files under `deploy/hub-topology/`.

### P0-02 — Stable Hub availability and routing

**Pass conditions:** Each active Hub for each network is reachable through its intended public endpoint and identifies the correct network, cluster, version, storage namespace and protocol contract. The entry Hub can locate an eligible Worker owned by another Hub where the topology supports it; only the Worker-owning execution Hub executes the authorized session. Live WebSocket heartbeats drive availability; an expired or withdrawn Worker is no longer selectable. No cross-network offers, requests, MSKs or credits are accepted. No stale or incompatible Hub is silently treated as healthy.

**Current public API:** [Stable Hub public network API](stable-hub-public-network-api.md). The prior network-kind defect is resolved on all three deployed Hubs. Both public smokes pass; sequential redeployments and direct concrete-Hub identity checks pass. Actual cross-Hub work routing, remaining active retired-route consumers and candidate evidence are still open. Use controller-attested private configuration, not public disclosure.

**Evidence:** Per-Hub identity and health receipts, real routing correlation (request/session/connection/lease), and isolated multi-Hub tests backed by FoundationDB (FDB). Run the deployed-network wiring smoke for both networks; test paid execution separately under P0-03.

### P0-03 — Credits, paid work and settlement

**Pass conditions:** Testnet must demonstrate an authorized bounded paid request from funding through delivery, exact debit, exact earning, hold release and the supported claim/settlement pathway. Verify the contract/bridge interfaces against authoritative balances and transaction receipts for any bridge operation advertised in 0.1.0. Replay must not duplicate a charge, earning, payout or transfer; failed or canceled work must reconcile without inventing a refund or spendable earning. A settled earning must become available only at the correct settlement boundary.

For Mainnet, repeat the identity, funding/bridge configuration, ledger and settlement readiness checks read-only. **If 0.1.0 is released with live Mainnet paid work or settlement enabled, require a separately authorized, minimal Mainnet canary proving the actual advertised financial path.** A Testnet transaction or synthetic lab settlement cannot certify real Mainnet funds. If the canary is not approved, restrict the release's Mainnet economic capabilities and describe that limitation explicitly; do not label an unrestricted economic release as verified.

**Evidence:** Network-specific wallet and credit snapshots, authorized spending cap, request/charge/earning/claim IDs, hold state, and settlement/bridge receipt as applicable. Use dedicated test or release-canary accounts; never use ordinary personal funds automatically. For the resident ORG financial canary, use its existing verified wallet under explicit operator authorization, not a substitute disposable wallet. Record network-scoped funding, bridge, earnings, spends, and transfer evidence. The zero-credit Mainnet readiness PASS is not a funded-work PASS.

### P0-04 — Hub accounting durability and recovery

**Pass conditions:** In a disposable FDB namespace, show that abrupt owner-Hub process death during funded accepted work causes a surviving Hub to fence the exact dead connection and reconcile the hold once. No late completion from a fenced generation can bill; committed charges and earnings are preserved; missing or ambiguous records fail closed. Verify multi-Hub concurrent reservation, idempotent replay, restart recovery, and absence of orphaned holds. Restart and resumption must preserve committed ledger data.

**Evidence:** Actual process-death lab and FDB transaction evidence, plus Worker-cycle and integrated-market regressions. Run fault injection on isolated processes only; do not kill deployed Mainnet or Testnet Hubs to collect release evidence.

**Current evidence:** 35/35 accounting/recovery regressions and actual isolated FDB owner-Hub process-death campaign PASS (E-004A). Exactly one hold released, requester restored, no charge or earning. This closes that failure scenario, not every restart/concurrent routing or cluster-loss scenario.

**Existing tools:** `tools.stable_hub_lab.crash_recovery`, `tools.stable_hub_lab.run_lab`, and the Stable Hub lab runbook.

### P0-05 — Local security and canonical Worker surface

**Pass conditions:** The official MCEL Worker route is canonical and usable through the trusted HTTPS gateway. Anonymous protected requests are rejected. Legitimate proof-file challenge/response produces a secure, revocable session; protected shell and API access succeeds. Functional tests use this same mechanism with disposable sessions and discover LMS identities without manual ID entry. The parent shell and mounted Worker must match the expected application and build. The normal user session must survive ordinary browser/MC restarts according to the established persistence policy; revoking a test session must not revoke unrelated sessions. No TLS bypass, secret logging or unauthenticated functional test exception is allowed.

**Evidence:** Secure functional smoke, canonical Worker identity live report, and a persistence/restart check on the release candidate. The browser must not substitute a candidate URL for the canonical route.

### P0-06 — ORG-SLH authority and lifecycle

**Pass conditions:** ORG-SLH starts, observes, authenticates, stops and restarts consistently. LMS owns local identity inventory and configuration access; a local identity is not automatically an organizational member. The default administrator identity is discovered and its authority independently verified. Protected Pending/identity reads succeed only with appropriate authority. Unauthorized administrative changes are refused. Verify the identity approval/revocation lifecycle, no unauthorized elevation or removal of protected administrators, and accurate connection/deployment state in the MCEL SLH interface. Stop must not permit a surviving launcher to restart the supposedly stopped service.

**Evidence:** Live read-only and protected SLH tests, disposable mutation tests, restart/stop lifecycle evidence, and the live canonical Worker approval report under P0-08.

### P0-07 — Deployed SEH transport and lifecycle

**Pass conditions:** Canonical deployment discovery, TLS service health, readiness and primary tunnel connectivity are verified. SEH relays the appropriate authentication and work traffic without becoming an independent identity, scheduling or accounting authority. A disposable isolated authenticated channel through the deployed SEH must be usable without interrupting the primary organizational tunnel. Test reconnection, stale connection removal and revocation refusal. Demonstrate deployment lifecycle (including failure/refusal and recovery) in a disposable rehearsal, with a current read-only observation of the primary deployment.

**Evidence:** Deployed SEH preflight/postflight, deployed-channel authentication and reconnect records, and existing lifecycle acceptance evidence. A local mock relay does not qualify as deployed-SEH proof.

### P0-08 — Worker enrollment, approval and connection

**Pass conditions:** Starting with a selected local LMS identity on the canonical MCEL Worker, unapproved access is refused, the signed enrollment request appears in SLH Pending, an authorized administrator approves it, the Worker verifies its approval and authenticates, and revoked access is refused. Test direct SLH and SEH-relayed paths. Verify that an approved managed Worker can register with Worker Bank and that revoked identities cannot re-register. Local selection, a pending request or an approval record alone does not establish an active Worker connection.

**Required evidence:** Live canonical Worker → local SLH approval test plus actual deployed-SEH relayed enrollment, authentication, Worker Bank and revocation evidence. A disposable organization and managed Worker on the deployed isolated channel are sufficient for the relay/Worker Bank part of this gate. If the canonical Worker UI's remote Connect/registration operation is shipped, additionally exercise that actual UI operation; do not infer its wiring from the managed-worker lab.

**Cleanup:** Revoke and remove disposable organizational records. Revoke the test browser session. Record the retained disposable LMS identity if local deletion is not supported; ensure it has no organizational privileges. Never revoke an existing real Worker as a cleanup shortcut.

### P0-09 — Dual employment (public marketplace + private ORG)

**Pass conditions:** One enrolled physical/logical managed Worker, with the same identity and connection generation for both jobs, connects through the actual deployed SEH and performs independently verified computation for a public marketplace requester and for the ORG. A protocol-conforming mock/disposable requester represents the Great Library. Require real SLH/SEH/Hub protocol handling, not a mocked authorization or transport. Synthetic funding and an in-memory Hub are acceptable for this gate.

The Worker must be exclusively reserved for each job, removed from public availability before private execution, and readvertised afterward. Verify one public charge and one earning, the organization's ownership and availability of the earned lab credit after settlement, exactly one private Pinky Promise obligation under the fixture's configured policy, replay safety, reconnect/re-registration, revocation and primary-tunnel continuity. Private work must not be submitted as a public marketplace job or disclose its private payload to the public Hub.

**Required report:** `runtime/reports/slh-seh-dual-employment/report.json`. All 26 existing assertions must pass, together with deployed transport verification and cleanup/postflight. The historical report's `employment_status: PASS` remains valid within its scope. R01 removed the obsolete `GREAT_LIBRARY_REQUESTER_PATH_NOT_VERIFIED` blocker. The corrected deployed run on November 24 reported `SEH_EMPLOYMENT PASS employment=PASS stage=complete reason=verified` with all 26 assertions passing. The contract retains `great_library_status: REPRESENTED_BY_PROTOCOL_MOCK`; the latest console output did not separately print that field. Direct Great Library integration is not a release requirement for this gate. A local-relay PASS does not certify deployed transport.

**Required harness correction before final aggregation:** Remove the Great Library application integration requirement from the overall dual-employment decision. Preserve `great_library_status: NOT_TESTED` (or explicitly label the mock representative), the funding/settlement limitations, and every substantive employment assertion. Add a regression establishing that all valid mock-requester employment evidence yields overall `PASS`. Do not suppress any actual failure. Until the correction is installed, preserve and interpret the original report rather than rewriting it.

### P0-10 — Canonical Worker live marketplace operation

**Pass conditions:** The promoted MCEL Worker can connect its real disposable Testnet wallet, establish fresh MSK authorization, register an eligible offer, maintain a live acknowledged connection and execute a bounded actual paid Testnet job. Correlate the result to that Worker's session. Validate delivery, settings restoration, offer withdrawal/termination and cleanup. Test the actual supported model/executor path with a verifiable result; the deterministic SEH lab alone does not certify the production model runtime. Reject stale/wrong-network wallet or expired authorization. No paid request may exceed the explicitly approved test cap.

**Existing harness:** `tools.mcel_worker_full_functional_acceptance`, live gates `wallet-msk`, `offer-registration`, `work-cycle`. Run against canonical Worker after the secure functional migration. Review the current plan before authorizing any mutation or spend.

### P0-11 — Requester authorization and spending enforcement

**Pass conditions:** The actual backend, not UI state, owns spending policy and authorizes paid submission. Verify remote-disabled refusal, per-request ceilings, zero/exhausted daily budget, cumulative spending, atomic concurrent reservation, forged authorization and replay refusal, ask-before-spending policy, local-capacity policy, and durable reconciliation on restart. Uncertain spending remains reserved until authoritative resolution. Denials must identify the intended policy rather than merely an unreachable Hub or missing wallet. An authorized bounded positive request must succeed through the actual supported requester path (may share evidence with P0-03/P0-10 if it exercises the same boundary); the no-paid-submission guard verifier alone does not certify successful paid requester execution.

**Evidence:** Live `requester-enforcement` report, adversarial backend regression tests, and the correlated positive Testnet request/ledger evidence. Preserve user settings and release holds correctly. Unauthorized Mainnet spending must never be part of the test.

### P0-12 — Organizational economy and Pinky Promise settlement

**Pass conditions:** ORG marketplace earnings belong to the organization wallet; private authorized computation creates the obligation only when qualifying service successfully completes. No obligation for failed, canceled or duplicate requests or mere public earning. Verify exactly-once obligation and sweep accounting, reserve transfer and durable reconciliation, insufficient-credit behavior, and policy disable/enable behavior. The current authoritative policy must agree with the configured production assessment; do not assume a disposable fixture amount is the production price. Private prompts/results must remain private.

**Evidence:** Existing Pinky Promise integration and settlement tests, including the approved actual Hub-ledger transfer pathway on Testnet if Reserve settlement is enabled for release. Record which monetary effects are synthetic and which are actual. Mainnet settlement is subject to the bounded canary rule in P0-03.

**Policy discrepancy to resolve:** The supplied `slh-pinky-promise-economy.md` describes policy v1 as 0.1 credit per qualifying completed local request. The latest dual-employment report records 0.2 credit for its disposable request. This is not a dual-employment failure, but the release must explicitly establish the intended production policy and verify the assessed amount against it before claiming correct production economics.

### P0-13 — Automatic ORG-SLH wallet provisioning and financial readiness

**Pass conditions:** On initial resident ORG startup, establish a cryptographically generated organizational wallet once, following the Hub administrator account pattern. Preserve the same account and protected signer across ordinary restarts and upgrades. Import a matching explicitly configured legacy signer where applicable; if existing durable authority cannot be recovered, fail closed rather than generating a replacement wallet. No manual environment variable, wallet address, Hub URL, MSK or admin ID is required for ordinary startup. The signer stays within ORG-SLH; never export it to Workers or Requesters.

Discover the configured network from the canonical network registry (Mainnet default for an unconfigured resident). Verify Hub, cluster, chain, TLS and wallet identity. Obtain or renew an MSK, establish/reconcile wallet binding, and read authoritative balances. Continuous maintenance must recover from temporary Hub outages and expired MSKs while respecting explicit revocation. A failure must block new marketplace admission, not disable local organizational services. Financial readiness must not require positive funding or enable marketplace participation or requester spending against policy. Verify Windows private-state protections without opening or printing the signer.

**Evidence:** `tests/test_slh_organization_signer.py`, `tests/test_slh_financial_readiness.py`, and `python -m tools.slh_seh_lab.org_msk_passthrough --resident`. The resident probe is read-only and checks Mainnet specifically; it is not a Testnet probe or proof of issuing a fresh MSK during that invocation. Require `--resident --restart-baseline` and `--resident --verify-restart` to yield `BASELINE_READY` and `PASS_RESTART` respectively. These modes verify a changed process creation time, old process termination, same public wallet/Hub binding, fresh READY state, unchanged marketplace and requester policy, and actual Windows signer directory/file ACL metadata. They do not restart the service or read signer/MSK contents. The baseline and comparison reports are separate; verify expiry, revocation and outage recovery in isolated fixtures. Confirm a separately scoped Testnet ORG binding without repointing the resident Mainnet wallet or silently switching networks. Verify local service continuity when financially blocked.

**Current evidence (Nov 24):** `PASS_PREFLIGHT_ONLY`, all seven resident checks true, zero blockers; Mainnet binding `CURRENT`, authorization present, financial readiness `READY`. Available, held, spent and bridged credits all zero. Marketplace disabled, zero connected Workers. Resident Mainnet startup, restart persistence and Windows ACL protection are now PASS: 20 Windows regression tests passed, one skipped; `BASELINE_READY` followed by `PASS_RESTART` with actual process replacement and unchanged wallet, Hub binding and policies. Separate Testnet readiness and candidate binding remain outstanding. The earlier `PINKY_SWEEP_CONFIGURATION_REQUIRED`, `UNBOUND` and missing-MSK results are resolved for this observed resident instance, not active blockers.

### P0-14 — ORG-owned MSK and bidirectional marketplace passthrough

**Pass conditions:** The ORG-SLH independently signs and obtains Hub-verified MSKs for its wallet. An enrolled Worker can earn marketplace credits through ORG-SLH; an enrolled Requester can purchase public marketplace work through ORG-SLH using that same organizational wallet authority. Neither receives the organizational signing key or MSK. Administrator authority controls signing operations and spending grants. Default-denied spending, request ownership, price/budget constraints, role separation, forged credentials, duplicate submissions, exact debit/earning and conservative treatment of uncertain liabilities must be verified at backend boundaries. A protocol-conforming mock requester is sufficient; no Great Library application dependency is imposed.

**Evidence:** `python -m tools.slh_seh_lab.org_msk_passthrough` and `runtime/reports/slh-org-msk-passthrough/report.json`; all 16 isolated assertions must pass, including `worker_authorization_origin: ORG_SERVICE`. This is real software/signing with synthetic funds. Require the network-specific live evidence in P0-03 and P0-12 for real economics; require a resident approved Worker account match, actual public and private employment and outbound requester evidence for the release scope. Remote ORG requester traffic over SEH was not exercised by the direct ORG TLS requester test. If remote requester passthrough is shipped, explicitly prove it using the existing relay architecture; do not open the management allowlist or expose local administration as a shortcut.

**Current evidence (Nov 24):** All 16 assertions passed on the operator's machine; resident Mainnet preflight passed separately. Live resident Worker account matching was not exercised because no Worker was connected. Real network transactions, actual live settlement and remote requester relay are not inferred from these results.

## 4. Secondary release scope

| Priority | Area | Acceptance disposition |
| --- | --- | --- |
| P1 | Wallet | User and ORG wallets correctly distinguished; network-specific addresses, available/held/bridged balances and any advertised transfer operations verified. Any wallet defect compromising P0 accounting or authorization is a P0 blocker. |
| P1 | MCEL application shell, File Manager, Code Editor, Calculator, Task Manager, Chat Console | Secure launch, correct application identity, fundamental advertised workflows, no unrelated privilege escalation. Existing read-only passes count for those checks only. |
| P1 | Installation and operator experience | Clean setup on the supported release platform; predictable configuration, useful errors, documented startup/stop, update and recovery. Routine testing must not require hand-entered identity IDs or repeated authentication-key uploads. |
| P2 | Direct Great Library application integration | Not required for employment certification; protocol-compatible mock requester is sufficient. |
| P2 | Additional apps, visual polish, large-scale stress targets, expanded network capacity | Defer unless a defect undermines a P0 pathway or an explicitly shipped promise. |

## 5. Existing execution entry points

Run from the repository root in the normal virtual environment. These are existing entry points identified in the supplied snapshot, subsequent patches or recorded successful commands. They are not a new release orchestrator. The November 21 Worker runbook predates the secure HTTPS and canonical-route migration; use the patched versions. For any command that performs wallet signing, spends credits, modifies organization state or injects failures, review the test scope, isolation and authorization first.

### Phase A — source and security preflight

```powershell
python -m tools.release_diagnosis --write-report
python -m tools.mcel_secure_functional_smoke
python -m tools.mcel_worker_identity_live_acceptance
python -m tools.mcel_functional_live_acceptance --app slh --protected-reads
```

### Phase B — deployed networks and Hub

```powershell
python scripts/smoke_hub_network_client.py --network testnet --skip-level4
python scripts/smoke_hub_network_client.py --network mainnet --skip-level4
python -m tools.stable_hub_lab.run_lab --verify-pathway-local --pathway-timeout 600
python -m tools.stable_hub_lab.crash_recovery --topology deploy/hub-topology/smoke-topology.json --timeout 110
```

Use current Mother observations and the Mainnet/Testnet topology configuration to complete P0-01/P0-02. The corrected public-network smoke with `--skip-level4` is read-only, needs no funded smoke wallet, and is not P0-03. Its latest results pass on both networks using the established shared RPC client. The standalone pre-cleanup verifier also passes on both networks; the harness integration and resume guards pass tests, but a complete subsequent Mother deployment has not yet exercised the new stage. Run actual paid and settlement tests under explicit bounded authorization. Follow `pretty_docs/stable-hub-lab-runbook.md` for the separate FDB smoke cluster and crash campaign; do not reuse a production namespace.

### Phase C — SLH/SEH and managed Worker employment

```powershell
python -m pytest -q tests/test_mcel_worker_slh_seh_enrollment_e2e.py
python -m tools.mcel_slh_dangerous_functional_acceptance --mode isolated
python -m tools.mcel_worker_identity_live_acceptance --approval-path --authorize-disposable-enrollment
python -m tools.slh_seh_lab.dual_employment --preflight-only
python -m tools.slh_seh_lab.dual_employment
```

The approval command creates a disposable local LMS identity and modifies only its organizational enrollment; the dual-employment lab uses a disposable ORG and synthetic funds. Verify teardown. Use the existing Pinky Promise and credit-recovery tests for P0-12; validate the current live settlement entry point before authorizing transfers.

### Phase C2 — ORG financial initialization and passthrough

```powershell
python -m pytest -q tests/test_slh_organization_signer.py tests/test_slh_financial_readiness.py tests/test_slh_public_request_gateway.py tests/test_slh_org_msk_resident_preflight.py
python -m tools.slh_seh_lab.org_msk_passthrough
python -m tools.slh_seh_lab.org_msk_passthrough --resident
```

Run the following R01 commands on Windows. The first saves a baseline; perform the normal operator-authorized ORG-SLH restart only after verifying no work or unsettled operation will be interrupted. Then run the second:

```powershell
python -m tools.slh_seh_lab.org_msk_passthrough --resident --restart-baseline
# Normal ORG-SLH restart, then wait until startup reports READY.
python -m tools.slh_seh_lab.org_msk_passthrough --resident --verify-restart
```

Evidence: `runtime/reports/slh-org-msk-passthrough/resident-restart-before.json` and `resident-restart-after.json`. A failed baseline is saved separately and cannot overwrite the successful baseline. `PASS_RESTART` requires both fresh preflight observations, actual process replacement, unchanged wallet/Hub binding and policies, and both Windows ACL checks. No automatic stop, restart, policy change or spending is performed. Exact MSK reuse/renewal is exercised in the isolated startup tests, not by reading live secret material. The current resident probe does not issue MSKs or jobs and cannot prove Testnet readiness. The existing isolated lab issues MSKs against disposable Hub software and uses synthetic credits. Neither command authorizes actual spending. See the patch plan for the proposed network-scoped canary extension, financial evidence and safety conditions.

### Phase D — live Testnet Worker and Requester

```powershell
python -m tools.mcel_worker_full_functional_acceptance --mode live --action plan
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate wallet-msk --authorize-wallet-signing
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate offer-registration --authorize-wallet-signing --authorize-worker-offer
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate work-cycle --authorize-wallet-signing --authorize-worker-offer --authorize-captain-spend --authorize-settings-change
python -m tools.mcel_worker_full_functional_acceptance --mode live --action run --gate requester-enforcement --authorize-settings-change
```

These are separate, explicitly authorized live gates. The documented work-cycle cap is two Testnet credits; verify the current plan and cap before running. Wallet preparation uses the established disposable MetaMask setup. If a timeout occurs after submission, reconcile the request, override and ledger before retrying. Complete any positive requester-path evidence not covered by the work-cycle gate. A Mainnet canary requires separate approval; no automatic Mainnet mutation is part of these commands.

## 6. Baseline evidence and outstanding work

The following is the historical acceptance baseline from the supplied conversation and repository materials, not a release-candidate run. `PASS` here describes the reported test scope only. Source and deployed versions still need to be bound to the 0.1.0 release candidate.

| Gate | Evidence already available | Remaining for release candidate |
| --- | --- | --- |
| P0-01 Chain | Both public read-only smokes PASS; runtime hashes match across networks; manifests and standalone pre-cleanup verification PASS | Candidate binding, finalized-block progress, validator topology/quorum and remaining provenance evidence |
| P0-02 Hub routing | Hub Control inspection and redeployment/runtime verification PASS; all three identities correct; both public smokes PASS | Active consumer audit, actual deployed cross-Hub routing/ownership and candidate binding |
| P0-03 Credits | Testnet work-cycle previously passed; synthetic public debit/earning/claim/settlement passed; resident Mainnet balance/preflight PASS | Actual Testnet bridge/settlement evidence; authorized Mainnet financial canary if enabled |
| P0-04 Recovery | 35/35 regressions; real isolated FDB process-death campaign PASS | Candidate binding and review of other required concurrency/restart scenarios |
| P0-05 Security | Nine secure smoke checks PASS; canonical live Worker identity test PASS | Release-candidate repeat and session persistence/restart proof |
| P0-06 SLH authority | Live protected reads PASS with automatic administrator discovery; disposable approval/revocation PASS | Lifecycle and administrator-negative coverage on candidate |
| P0-07 SEH transport | Deployed healthz/readyz/lab-capabilities HTTP 200; isolated authenticated channel/reconnect and primary continuity PASS | Bind evidence to candidate; lifecycle rehearsal evidence |
| P0-08 Enrollment | Live canonical local SLH approval: 16 recorded assertions true; direct/relayed disposable enrollment tests PASS | Correlate release candidate; exercise shipped remote UI registration operation if not yet verified |
| P0-09 Dual employment | All 26 assertions PASS on actual deployed WSS isolated channel; mock requester accepted by operator | Corrected deployed rerun PASS 26/26; bind evidence to candidate; rerun if affected |
| P0-10 Worker | Earlier wallet/MSK, offer registration and work-cycle passes reported | All live gates against current canonical Worker; actual supported model path and cleanup |
| P0-11 Requester | Backend guard live PASS with no paid submission; adversarial tests reported passed | Current guard regression and positive paid requester correlation |
| P0-12 ORG economy | Synthetic earnings available; one Pinky obligation; replay PASS; resident Mainnet wallet ready but unfunded | Verify production price/policy, actual earnings/spending, applicable Reserve settlement, bridge and transfer receipts |
| P0-13 ORG startup readiness | Seven live resident Mainnet preflight checks PASS; isolated signer and maintenance tests passed; live restart and Windows ACL PASS | Testnet readiness; candidate binding |
| P0-14 ORG MSK/passthrough | 16/16 isolated functional assertions PASS on operator machine; ORG_SERVICE authorization origin verified | Actual resident Worker account correlation and network-specific earnings/spending; remote requester relay if shipped |

**Current decision: NOT YET CERTIFIED.** R01 is complete. R02 public network identity, canonical RPC, deployed runtime presence, manifest consistency and isolated owner-Hub crash recovery subgates pass. The standalone manifest rectifier and Mother pre-cleanup verifier are accepted in their tested scopes; full live harness execution is deferred to the next routine deployment. R02 remains open for active consumer review, remaining chain/topology/routing evidence and isolated Testnet ORG financial readiness. Resident Mainnet readiness, ORG passthrough and deployed SEH employment remain valid scoped passes. R03–R05 funded employment, spending and settlement and R06 candidate binding/package/approval remain open. Do not repeat passing tests unless their dependencies change. No funded execution is inferred from read-only or synthetic evidence.

## 7. Release evidence, packaging and final approval

Use one release candidate (for example, `0.1.0-rc.1`) with a recorded source commit, clean or explicitly enumerated working-tree changes, client artifact hash, Hub/SLH/SEH deployment identities and contract manifest hashes. Capture each gate's test command, start/end time, environment and network, evidence path, exit status, assertion outcomes, authorized spending and observed ledger delta, cleanup result, and responsible reviewer. Store only redacted evidence; private keys, proof files, MSK secrets, cookies, user data, runtime balances that are not needed for certification, and local browser profiles must not be included in the release archive.

Use `tools.release_diagnosis --write-report` as an existing source/package preflight; its candidate report is not a substitute for this functional gate matrix. Verify the actual release package on a clean supported installation, its version, dependency requirements, startup/stop, configuration compatibility, and ability to recover or roll back without losing funds, identity authority or durable accounting. Production deployment changes remain separate, authorized operations.

Maintain a consolidated release report (proposed location `release_reports/0.1.0-rc.N/`) with one entry per gate, evidence links and an explicit `PASS`, `FAIL`, `BLOCKED`, or `NOT_RUN`. A gate with an approved conditional restriction must state exactly what is disabled and why; it cannot claim the restricted feature was tested. Known secondary defects must have an owner and a disposition. No release if there is unresolved unauthorized access, wrong-network operation, incorrect credit accounting, double execution/billing, loss of durable state, irreconcilable holds, or unverified required cleanup.

**Final decision record:**

| Item | Value |
| --- | --- |
| Release candidate / source fingerprint | Pending |
| Mainnet and Testnet deployment fingerprints | Pending |
| P0 gates passed / applicable | Pending |
| P1 exceptions / disabled capabilities | Pending |
| Release package verification | Pending |
| Mainnet paid canary authorization and outcome | Pending / restricted until authorized |
| Operator approval and date | Pending |
| Release decision | NOT YET CERTIFIED |

Once the evidence is complete and the operator approves, tag and publish the exact accepted build as `0.1.0`. Verify post-release health and record the deployed version. A later change to a core component reopens only affected gates, plus any integration gates whose assumptions changed.

## 8. Basis and next implementation task

This document is based on the November 23 repository snapshot `main_computer_test-20261123-103953.zip`, subsequent secure-functional, Worker approval and SEH dual-employment patches through November 24, and the operator's posted live acceptance results. Revision 2 additionally uses `main_computer_test-20261123-235635(6).zip`, the MSK/passthrough implementation, resident LMS auth fix, financial-startup and automatic-wallet provisioning patches dated November 24, and the latest posted resident and isolated acceptance results. The repository references include `pretty_docs/hub-chain-testnet-mainnet-architecture.md`, `pretty_docs/stable-hub-pathway-assurance.md`, `pretty_docs/stable-hub-lab-runbook.md`, `pretty_docs/worker-mcel-live-functional-acceptance-20261121.md`, `pretty_docs/seh-real-relay-deployment-and-acceptance.md`, `pretty_docs/slh-pinky-promise-economy.md`, and `tools/release_diagnosis.py`. Historical runbooks must be interpreted in light of the subsequent patches. No new live network checks were performed in drafting this document.

**Next implementation:** Follow the [release patch plan](release-acceptance-0.1.0-patch-plan.md). Preserve the seven-check Mainnet preflight and 16-check ORG passthrough evidence. R01 restart comparison and corrected deployed dual-employment checks have passed. Preserve the completed R02-A/B/C and isolated recovery evidence; finish the open R02-D items in the patch plan using existing harnesses and integrate evidence into release diagnosis. No automatic spending, Hub reconfiguration, wallet replacement, or production fault injection is authorized by this document.


**Revision 3 implementation evidence:** R01 is based on `main_computer_test-20261124-155306.zip` plus the previously accepted wallet-provisioning patch (the export lacks that patch and the release documents). 107 targeted tests passed on Linux; one actual-Windows ACL fixture skipped. Isolated ORG MSK/passthrough passed 16/16; local real-relay dual employment passed with the corrected final result. Actual resident restart/ACL and corrected deployed SEH rerun remain pending; full P0-13 remains open for Testnet readiness.


**Revision 4 closure:** Based on `main_computer_test-20261124-171737.zip` and the operator-posted Windows restart report and deployed SEH console results. R01 is complete. P0-09 passes its defined mock-requester, deployed-isolated-channel scope; P0-13 Mainnet readiness/persistence/ACL subgates pass. P0-13 remains open for separate Testnet readiness; funded settlement, full release candidate fingerprint and remaining gates are not certified. Earlier R01 pending statements above are historical. No new live tests were executed while recording this closure.


**Revision 6 (November 25):** Based on `main_computer_test-20261125-153007.zip`, subsequent targeted patches and operator-posted acceptance results. See E-001C/E-002C, E-001D and E-004A. Earlier blocked public preflight observations are historical, not current blockers. No new live tests were executed while recording this revision.
