# SLH → SEH → Stable Hub: Laboratory Exit Acceptance

## Governing rule

A successful 64-Worker run is an integration result, **not** a laboratory-exit certificate. The laboratory is accepted only after every required test is implemented, executed, and supported by preserved evidence. A passing local Hub transaction cannot establish public-WSS operation, a real external bridge, or cloud availability.

The `lab-exit` runner is **fail-closed**. It currently reports `NOT_ACCEPTED` even when all implemented lanes pass because essential fault-injection, persistent crash/restart, cross-Hub handoff, production computation, and live credential-rotation lanes are not yet implemented. These blockers are deliberate, explicit, and cannot be turned into PASS by a command-line waiver. The available tests can be run and audited now.

## Commands

Preview the executable checks without asserting exit acceptance:

```powershell
python -m tools.slh_seh_lab.run_lab lab-exit --exit-profile smoke --json
```

Run the full laboratory audit. It requires **ten** independent 64-Worker runs (different seeds), a 65th-connection refusal test, concurrency 16 and 32, a continuously connected Worker performing paid jobs for at least 3,600 seconds, a direct-TLS control, and the isolated, real three-Hub FoundationDB smoke lane:

```powershell
python -m tools.slh_seh_lab.run_lab lab-exit --exit-profile full --json
```

Full acceptance refuses `--exit-repeats` under 10 and `--exit-soak-seconds` under 3,600. To run other tests even after an earlier failure, add `--exit-continue-on-failure`; this changes execution order only and **does not** waive failures. Repeats default to ten. The longer paid soak holds one connection open across many real synthetic-funded paid transactions; a series of separate brief connections does not satisfy the duration gate.

Each executable test starts in its own child process and has a bounded timeout. The runner preserves a per-lane `child-output.log`, the child lab's `report.json` and `integrated-stress-events.json` or paid transport JSON, plus an atomically updated `exit-evidence/exit-report.json`. The journal survives a crash or forcibly terminated lane. If a child exceeds its timeout, the runner stops only that subprocess tree; it does not terminate existing Hub/Coolify services. **A timed-out test never passes merely because it previously emitted a success event.**

The exit report's overall `accepted` is false until every required lane and capability has independent passing evidence. A `NOT_ACCEPTED` result returns a nonzero process exit code by design.

## Strict integrated acceptance

The original stress suite allowed as few as half the paid requests to be accepted. The new opt-in `--strict-acceptance` mode requires **all** requested jobs to be accepted, all to settle successfully, no unexpected Worker client disconnection, zero transport errors, no unwanted Worker registration losses, and the existing exactly-once ledger and lifecycle checks. The standard stress command retains its old experimental overload semantics; only the strict mode is eligible for laboratory-exit evidence.

The opt-in `--capacity-probe` requires exactly 64 live clients at the SEH relay, connects a 65th actual WebSocket client, requires its closure code `1013` (busy/unavailable), and rechecks that the original 64 Workers remain attached. The same run still has to complete the full paid, reserve, churn, reconnect, and disable/re-enable lifecycle to pass.

For isolated continuous paid testing, use `diagnose-paid-transport --transport seh --cycles 16 --soak-seconds 3600 --tls-io-mode serialized --json`. It retains the **same Worker connection** for at least the specified duration and checks paid settlement plus registration after every cycle. A bounded recent checkpoint history is preserved in the trace alongside cumulative cycle count and elapsed time. Direct TLS can be examined separately.

## Mandatory remaining work, currently blocked

1. Kill Worker, Organization Service and SEH at distinct in-flight paid stages; prove recovery, no double charge, no duplicate execution, and terminal reconciliation.
2. Restart a persistent three-Hub FDB smoke cluster while paid activity is in flight; prove durable, exactly-once recovery (not merely live identity and a successful transaction).
3. Exercise cross-Hub ownership handoff and settlement after a Hub failure, not just the chosen third Hub.
4. Execute representative production compute tasks on real Workers and validate result fidelity; the integrated lane currently uses an echo executor.
5. Exercise live device credential rotation/revocation while authenticated Worker connections and jobs are active.

Existing targeted service-restart, enrollment, and authorization tests remain useful evidence but **do not** substitute for these absent full-system scenarios. The `required_gaps` section enumerates them as `BLOCKED_UNIMPLEMENTED` until implementation. Full Windows acceptance must run on Windows; a Linux PASS does not substitute for the platform where the TLS failure occurred.

## Additional mandatory Pinky Promise economic acceptance (TARGET / NOT IMPLEMENTED)

The economic architecture is defined in [SLH Pinky Promise: Idle-Compute Funding and Local Secure Model Access](slh-pinky-promise-economy.md). **Marketplace earnings** fund the organization wallet; they are not charged 10% automatically. A **successfully fulfilled qualifying local secure-model request** on another organizational Worker creates an obligation of **0.1 credit**: 10% of the fixed **1-credit public-market request reference** (1,000 reference output tokens). This per-request assessment does not require a live price quote or change with intra-request output-token count. It is settled from the organization wallet to the Main Computer Reserve only after authorized delay/finality.

The laboratory-exit runner does **not yet have** an implemented local-request-to-Reserve settlement lane. This is an additional explicit exit blocker, not something demonstrated by the existing public paid-work/synthetic-settlement tests. A future fail-closed gate must prove authenticated Worker and requester routing; policy version plus the fixed **1-credit reference / 0.1-credit assessment**; one successful request billed once; ten distinct completed requests assessed **1 credit** in aggregate; repeated work with different output-token counts still billed at the same per-request rate; settled organization-wallet funding; authorized periodic aggregate transfer with a Hub-ledger Reserve receipt; outstanding obligations on insufficient balance; replay/crash/retry idempotence; optional administrator opt-out affecting only future usage; and private-data nondisclosure. No reported PASS may claim this economic contract until these tests actually run. The executable runner retains P0's sixth mandatory fail-closed gate; focused P4 tests do not clear it.

## Scope and constraints

The runner uses only the checked local dev/smoke three-Hub topology and synthetic requester funding. The FDB lane uses the existing smoke namespace, not production state. It cannot assert public Internet WSS, a real external bridge payment, or remote Coolify deployment. It never requests a reusable administrator secret, overwrites an established SLH identity, or deploys to the Internet. Local lab acceptance precedes those later deployment and live-bridge acceptance stages.

The trimmed source export may omit `game_projects/webgl-demo/game.json`, which the viewport imports when loading the Worker runtime. The laboratory-runner patch does **not** invent that missing production asset; source packaging completeness should be checked in the actual repository. Test-only import fixtures used during patch verification are not distributed.


## Administrator session renewal during a paid soak

The Organization Service intentionally expires known-device administrator bearer
sessions after 900 seconds. A one-hour paid-soak must not confuse an expired
**lab control-plane bearer** with a lost Worker connection, nor silently extend
production token validity. The diagnostic driver reauthenticates its lab
administrator using the existing signed P-256 challenge/response every 600
seconds, updates only the lab HTTP control token, and leaves the authenticated
Worker TLS/WebSocket connection running. Trace checkpoints record only the
renewal count and whether the Worker remains alive; bearer tokens, signatures,
and authorization material are excluded.

Full acceptance requires the same Worker's connection to survive at least
3600 seconds, verifies paid ledgers throughout, and demands evidence that the
short-lived administrative bearer was refreshed. Authentication errors must be
reported as control failures rather than mislabeled Worker losses. The exit
report includes structured, redacted child failure classifications and trace
locations even when the child process exits nonzero.

## P4 periodic Reserve sweep (implementation, acceptance still blocked)

The payment rule is now **one periodic batch**, not a Reserve transfer for each request. The SLH accumulates fixed 0.1-credit obligations, then uses a configured, wallet-signed Stable Hub credit-account transfer to move a batch to the Reserve-controlled Hub wallet. Insufficient balance leaves outstanding debt, and retries reuse the exact sweep ID. A local confirmed receipt establishes a Hub-ledger credit transfer, not external blockchain finality. The full exit gate remains blocked until the complete local economic cycle, crash recovery, authorization failures and persistent three-Hub deployment are tested on Windows.

### Independent shared-Hub Pinky integration proof

The executable real three-Hub smoke/FDB Pinky Promise lane is available via
`python -m tools.slh_seh_lab.run_lab run --pinky-integrated --json`.
It proves an earned organization wallet balance, two fulfilled private requests,
the exact 0.2-credit aggregate Reserve transfer and idempotent replay using
the same disposable organization across a supported SEH-to-direct restart.
See [shared-Hub Pinky testing](slh-pinky-shared-hub-integration.md).
A `verify-local` PASS must never substitute for this evidence. This integrated
lane **does not itself remove** the full `lab_exit.py` acceptance blocker or
claim an on-chain Reserve transaction, cross-Hub owner handoff, private-SEH
inference, production deployment, or the remaining laboratory resilience
requirements. Those remain separate mandatory gates.
