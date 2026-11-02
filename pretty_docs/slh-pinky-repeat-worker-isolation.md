# Pinky Promise: Repeatable lab Worker selection isolation

The real three-Hub integrated laboratory uses the same durable smoke FoundationDB namespace for repeated earn/spend cycles. Every cycle uses a disposable SLH organization, Worker, wallet and requester. Prior versions nevertheless advertised the same generic `chat.completions` capability for every emulated Worker. A lingering offer from an earlier cycle or previous smoke run could therefore be selected for the next sale. Recovery then needed to reconcile old Worker ownership and could consume the bounded paid-work POST timeout.

Each **integrated** economic cycle now mints a unique, nonsecret `slh.lab.pinky.<random>` *additional* marketplace capability. The emulated Worker advertises both `chat.completions` and its unique capability; its funded buyer explicitly requests both. The Stable Hub uses its existing normal capability-matching market selection. Any older Worker lacking the new token cannot be offered this cycle's work, irrespective of price or lingering status. The test still uses a real registered Worker, actual SEH relay, real Hub request/settlement, organizational private requests, and the authoritative FoundationDB Reserve transfer.

The acceptance report checks that the selected Worker's persisted market record advertised this same capability and that no two cycles reuse an isolation capability. Lack of this proof fails the economic reconciliation. The emulated Worker continues to return a marker; **no AI service is called**. Ordinary non-integrated marketplace tests keep their pre-existing generic market capability and routing.

A `terminal_failed_lab_stage` field distinguishes a fatal stage from the separately retained `first_failed_lab_stage` (which may describe an intentionally rejected TLS-pin probe during preflight).

This change prevents cross-cycle market selection contamination. It is not evidence that FoundationDB's shared document has no remaining latency or write-contention problems. The Windows three-Hub run remains the required acceptance test:

```
python -m tools.slh_seh_lab.run_lab run --pinky-integrated --pinky-repeat 3 --json
```

A complete PASS requires three independent 1-credit earnings, six 0.1-credit Pinky Promise obligations, a cumulative 0.6-credit Reserve transfer, exact ledger reconciliation, and distinct buyer, org, Worker and cycle-capability identities.
