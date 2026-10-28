# Stable Hub — end-to-end pathway assurance audit

**Snapshot:** `main_computer_test-20261027-115807.zip`, reviewed October 27, 2026.  This document distinguishes *implemented safeguards*, *automated tests*, *observed live results*, and *unproven failure boundaries*. It does not certify Testnet or deployed Hub behavior by inference from a local test. No balance, real-world deposit, or payout was changed during the review.

## Patch 2 implementation (October 27): owner-death recovery and fencing

The Patch 2 code introduces a durable heartbeat-expiration survivor sweep,
strict `(worker_id, connection_id, lease_epoch)` compare-and-fence inside a
single FDB transaction, and transactional recovery of all unfinished sessions
and held credits for the fenced connection. A CLOSED owner with outstanding
sessions is retried. `worker.pong` cannot reopen a CLOSED owner and terminal
billing rejects a fenced connection inside the terminal payment transaction.
The dedicated tests cover stale and recent heartbeats, generation changes,
missing credit evidence, idempotence and injected cleanup failure/rollback.

**Certification state:** local unit and regression results are distinct from
real owner-process death. The isolated process-kill test must return
`STABLE_HUB_CRASH_RECOVERY_PASS` on a workstation with FoundationDB before
owner-death recovery can be marked as observed. It starts and kills only its
own disposable local Hubs in a unique namespace. The 90-second default timeout
balances ordinary Worker keepalives against detection delay, but cannot
perfectly distinguish a dead process from a prolonged network partition or
clock skew. It is not a consensus failure detector. Actual deployment fingerprint,
Testnet credits, and on-chain payout are separate unverified boundaries.

Run the offline gate, isolated destructive campaign, Worker-cycle integration,
and 25-run stress commands in the Stable Hub Lab runbook.

## Patch 1 superseding finding: durable execution authorization

As of the Patch 1 implementation (October 27, 2026), the older ordering
findings B and C below are **historical**. Stable Hub no longer exposes the
task in its preparation offer. It requires an updated Worker execution protocol,
records a funded accepted session atomically, and sends a separately bound
`hub.work.authorized` message before local computation may begin. The
Worker's authorization handler and the Hub terminal handler both enforce
session/request/run and socket bindings.

The new negative tests prove that no computation starts from a preparation
message; wrong, duplicate or post-close authorization cannot start it; early
terminals have no charge; and an underfunded Captain cannot obtain execution
authorization. Cross-Hub and stress harnesses are updated for the authorized
payload. Test results below describe the **pre-Patch-1** baseline unless
explicitly identified as a Patch 1 regression result.

**Remaining certification gaps:** isolated real process-kill proof of Patch 2,
prolonged network-partition and clock-skew behavior, and verification of the
actual deployed version and external Testnet/on-chain settlement. Full pathway
certification therefore remains **NO**.

## Certification status

- Browser Worker Testnet work cycle: **observed PASS** on October 26, including authenticated market proof, Captain completion, Worker result verification, and test-offer restoration. The terminal evidence does not independently certify final on-chain settlement.
- Local Worker-cycle campaign: **observed PASS** with price refresh, strict market rejection, successful accounting, ordinary disconnect, and hold release.
- Three-Hub integrated local stress: **observed PASS 25/25** after connection-generation isolation; 100 attempts, 75 accepted, 50 completed, 25 failed, 25 market rejections, 75 holds, 50 charges, 50 earnings, zero observed accounting violations, and a longest offer wait of 2.360 seconds.
- **Full-pathway certification: NO.** A successful stress campaign is not a process-kill/failover or protocol-ordering proof.


## October 27 local assurance failure: contract reconciliation

The first `--verify-pathway-local` run reported an import error in an AST-extracted
WebSocket client test and eight legacy assertions. The repairs do **not** modify
the Hub's deployed protocol or imply that the open production boundaries above
are resolved. They update tests to enforce the **current** authenticated contract:

- The AST-only client test must inject `credit_decimal_text_to_wei`, imported by
  the actual viewport module. Production source already has the import.
- Normalized Worker market profiles expose both `models` and `model`; a test
  comparing full dictionaries must include those fields.
- A `worker.auth` client-supplied Worker label is not a privileged identity.
  For actual WebSocket workers the Hub issues a new `conn_*` identity and
  binds the offer, accepted session and market record to that connection.
- The terminal handler likewise ignores a client-provided `worker_id` label
  for authorization. A matching authenticated socket and the durable request,
  run and connection bindings are required. Tests deliberately forge the label
  on the **same** authenticated socket and verify one terminal charge; this
  does not permit a different socket to complete the session. A separate
  regression authenticates a second live socket, submits a forged terminal for
  the first socket's session and verifies rejection, no premature charge,
  then exactly one legitimate completion.
- An offer that times out before Worker acknowledgment has **no accepted
  session and no hold**. Retrying its request ID is allowed, and the regression
  verifies rejection of a late stale acceptance and exactly one eventual paid
  completion. Once a session is accepted, duplicate replay instead resolves to
  the existing record and cannot double-charge.
- With today's protocol, an underfunded Captain request may reach the Worker
  offer/accept phase before the Hub checks reserve availability. The regression
  now acknowledges the offer, asserts HTTP 402, checks zero resulting holds or
  charges, and keeps prevention of unpaid computation explicitly **OPEN**.
- Stable Hub hold creation is in `payout_holds` and
  `stable.work.payout.held` events. Unlike the older EXP ledger, it does not
  create a `hold_created` transaction or `hub.hold.created` bridge event. The
  tests check the actual durable hold, event, charge, and Worker earning.

The local gate also emits start/finish progress for every lane in text mode.
JSON mode stays a single parseable document. Per-lane timeouts are not proof
of pathway correctness: inspect and resolve the named lane failure.

The known P0 concerns below remain open. In particular, a green local test
for HTTP 402 is a **billing** proof, not proof that the Worker did not start.

## Pathway coverage matrix

| Step / boundary | Existing protection and source | Evidence / test | Status / missing proof |
| --- | --- | --- | --- |
| Hub identity, FDB namespace, deployment configuration | `stable_hub_topology.py`, Hub `/hub-identity`, smoke identity checks | `test_stable_hub_topology`, `test_stable_hub_runtime`, `--check-cluster` | Covered for local topology; deployed identity/version still independent |
| Wallet signature, MSK issuance, chain binding, replay | `stable_hub_msk.py`, stable request handler | `test_stable_hub_multisession_keys`, `test_stable_hub_hardening` | Local tests; actual browser wallet and Testnet observed separately |
| WebSocket upgrade, auth, ping/pong, local owner | `stable_hub.py`, `stable_hub_worker_sessions.py` | `test_stable_hub_worker_live_session`, real Worker Testnet | Normal socket lifecycle covered; abrupt owner process death **open** |
| Worker offer publication and reconnection generation | Worker Work Now runtime, Hub market directory | Worker-cycle price-refresh regression, 25-run stress | Current local tests passed |
| Request market matching and ring/capability/price | `StableHubWorkerMarketDirectory` | `test_stable_hub_worker_market`, HTTP 409 price tests | Covered for tested constraints and stale candidates |
| Ownership, capacity, local/remote handoff | owner directory, lease checks, cross-Hub forward | `test_stable_hub_owner_acceptance`, `test_stable_hub_hub_to_hub_handoff`, stress | Covered for observed reconnect; owner **crash fencing is open** |
| Request idempotency and duplicate payload | request index/hold constraints | `test_stable_hub_hardening`, FDB atomic tests | Accepted-session duplicate and mismatched work tested; pre-acceptance timeout permits an uncharged retry |
| Offer delivery and Worker acceptance | `LiveWorkerSession.offer_work_and_wait_for_acceptance` | owner timeout tests, delayed-dispatch test, real Worker | **Open:** acceptance reply precedes durable paid-session commit |
| Credit reservation and insufficient balance | payout ledger `create_hold`; atomic accept transaction | HTTP 402 scenario, FDB concurrent accounting | Financial rejection covered; **open:** Worker may compute before reservation succeeds |
| Worker computation, timeouts, streaming delta | `viewport_routes_energy.py` Worker executor; Hub stream | `test_worker_live_session_local_ai_execution`, terminal/stream tests | Normal/failure behavior tested; pre-commit terminal race **open** |
| Terminal result/session/request/run/connection binding | `_handle_worker_terminal_message` | `test_stable_hub_hardening` | Forgery/replay tests present; fast-result ordering **open** |
| Charge exactly once and Worker earning | `spend_request_credit`, atomic terminal | FDB atomic tests, payout tests, local stress | Tested under ordinary and injected ledger faults |
| Ordinary WebSocket disconnect and refund | `close_worker_connection_and_settle` atomic callback | Worker-cycle campaign; atomic rollback test | Covered if cleanup executes; **open** when process dies first, and when close transaction repeatedly fails |
| Continuation/status/stream read | accepted work session directory | `test_stable_hub_hardening`, handoff and dev verification | Local happy/error tests; long-lived disconnect/resume and access limits need focused review |
| Worker claim, batch, payout bridge | payout ledger directory | `test_stable_settlement_ownership_p3`, payout/crash-lab claim/batch faults | Ledger idempotence covered; real external bridge and on-chain settlement not certified |
| Verified deposit accounting | chain deposit completion adapter | `test_stable_hub_credit_bridge_patch2`, bridge lookup/historical tests | Verified/idempotent credit acceptance tested; not a live chain campaign |
| Hub restart and cross-Hub FDB persistence | crash lab (reserve, charge, claim, batch, failover) | `tools/hub_crash_lab/run_lab.py` and `test_hub_crash_lab` | Crash modes available; user has not supplied completed current crash-campaign output; **accepted-session owner death** not injected |
| Concurrency, capacity, stale Worker isolation | atomic market reservation and run/generation capabilities | 25/25 stress; FDB atomic tests | Tested in this workload, not unbounded races or network partitions |

## Historical P0 diagnosis (B and C addressed by Patch 1; A remains OPEN)

### A. Owner Hub killed after accepted hold, before disconnect cleanup

1. Isolate a disposable FDB namespace; register real socket and execute acceptance until `hold.status=held` and session `accepted`.
2. Hard-kill the **owner process**, without a clean WebSocket handler or `close_worker_connection_and_settle`.
3. Confirm a surviving Hub can detect a truly dead owner using a **fenced process incarnation/lease**, not merely stale `last_pong_at` or a guessed timeout.
4. Atomically retire that exact owner generation, mark its unfinished sessions terminal, release only proven unearned holds, and free capacity.
5. Retry recovery after a simulated precommit abort, after the commit but before the response, and after a new Worker reconnection. Require one release, no charge, no earning; never damage the new socket or previously successful session.

**Current source issue:** cleanup is invoked from the WebSocket handler's `finally`, and the startup server setup has no visible owner-death reconciliation loop. A process kill bypasses the handler. The existing crash lab tests hold/charge/settlement faults but not this transition. A failure in close settlement is logged for an FDB conflict, without durable scheduled retry demonstrated by the current tests.

**Required architecture:** bounded owner-incarnation heartbeats, authenticated cross-Hub liveness/fencing and a durable, retried, transactional orphan reconciler. Do not release credits on age alone or reuse an old connection ID.

### B. Worker's `worker.work.result` arrives before accepted-session creation

The Worker sends `worker.work.accepted` and immediately starts `_complete_work_offer` in another thread (`viewport_routes_energy.py`). On the Hub, `offer_work_and_wait_for_acceptance()` returns **before** `_record_acceptance()` creates the hold and accepted-session record (`stable_hub.py`). The terminal handler currently requires `get_session(session_id)` to exist. This admits an ordering in which a fast result receives `hub.error: accepted work session does not exist` despite a valid offer.

**Required test:** use a barrier to delay the Hub's acceptance transaction after the Worker acknowledgment while the Worker emits a valid terminal result. Verify that the result is not lost, the Worker never starts unreserved billable work, and exactly one terminal record/charge/earning results. Inject a failure to reserve and ensure there is no charge, no earning, and no previously executed billable work.

**Required protocol improvement:** make an explicit *durable paid-session ready* acknowledgment gate Worker execution, or otherwise implement an authenticated, bounded terminal staging/retry contract with corresponding billing guarantees. Do not rely on echo execution being slow enough.

### C. Reserve funds before the Worker begins computing

In the current request handler, the Hub first sends the offer and waits for `worker.work.accepted`; only then does it call `create_hold` in `_record_acceptance`. The Worker immediately starts its executor after acknowledging. A Captain who is underfunded can therefore cause a Worker to start computing before the Hub returns HTTP 402. No invalid debit is made, but protection against **unpaid computation** has not been established.

**Required proof:** force available balance to be less than the advertised offer, instrument actual Worker executor start, submit a matchable request, and assert that the Worker executor did not begin before a durable reservation. A two-phase reserve/accept/execute protocol must release the provisional hold on timeouts, rejection, or disconnect.

## P1: remaining adverse-condition campaigns

- Repeat `worker.close` while FDB is unavailable or returning persistent conflicts; prove eventual reconciliation (not just rollback of one transaction).
- Kill/partition the owner during cross-Hub handoff and after a forwarded acceptance is committed but before the entry Hub's response; require idempotent replay and a usable continuation URL.
- Test Captain polling interruptions, duplicate completion, lost terminal acknowledgment, and streamed deltas reordered or repeated; prove monotonic terminal state and no double billing.
- Stress concurrent Workers/requests with slow FDB, socket loss, multiple same-wallet connections, and saturation. A 25-run passing campaign establishes a useful baseline, not a statistical guarantee of every race.
- Confirm active deployments run the tested source revision, and separately audit real Testnet ledger and on-chain/bridge settlement events. Keep those tests out of the disposable local lab.

## Run the broader offline gate

```powershell
python -m tools.stable_hub_lab.run_lab --verify-pathway-local
```

This invokes six explicit groups of existing tests: (1) identity/MSK/transport, (2) market/ownership/handoff/idempotency, (3) Worker runtime and browser test contract, (4) FDB accounting/claims/integrated regressions, (5) bridge accounting, and (6) crash-lab harness tests. It returns nonzero if **any** group fails, times out, or contains missing test files; it reports all group results instead of stopping at the first failure. Use `--json` to preserve machine-readable proof and `--pathway-timeout 600` for slow Windows hosts.

**Interpretation:** even six green groups report `full_pathway_certified: false`, because the P0 scenarios above have not been proven. This gate does **not** start a local Hub cluster or kill real processes; follow the main Stable Hub runbook for smoke traffic and all isolated crash-lab modes. Never run it as a substitute for live Testnet Worker acceptance.

## Local completion checklist

1. Broad offline pathway test gate, all six groups green.
2. `--verify-worker-cycle` on correct three-Hub local cluster, exact offer and payout proof.
3. `--verify-stress --verify-stress-runs 25` (prefer repeated campaigns); no unexpected HTTP 409 or invariant violation.
4. Crash lab `baseline`, `restart`, `inflight`, `precommit`, `settlement`, `failover` modes. Record outputs, not just process health.
5. New process-death orphan recovery, terminal-before-commit, and unfunded-computation regressions, with runtime repairs.
6. Separate deployment fingerprint/Testnet ledger/on-chain bridge audit before calling a deployed production network certified.

Until item 5 is implemented and tested, describe the Hub as **functionally passed and stress-validated, with critical recovery and ordering boundaries open**.
