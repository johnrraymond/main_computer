# SLH Pinky Promise P3 — Fixed-Price Assessment and Windows Verification

**Scope:** P3 is a durable obligation journal for completed **direct** intra-organization secure-model work. It does **not** move funds, reserve money, debit a Worker, contact Stable Hub, claim Reserve receipt, or satisfy full laboratory exit.

## Governing rules

- `fixed_per_request_v1`: 1-credit reference price (1,000 output-token reference), **0.1 credit per successfully completed distinct eligible request**, irrespective of actual tokens or public job earnings.
- Exact amounts: `reference_credit_wei=1000000000000000000`, `amount_credit_wei=100000000000000000`; no floating-point accumulation or live quote.
- One SQLite transaction commits both `SUCCEEDED` terminal receipt and one `ASSESSED` obligation. Failure rolls back both. SQLite restart changes `DISPATCHED` to `OUTCOME_UNKNOWN` and does **not** assess an uncertain result.
- Obligations key on organization, requester device, and request ID. Replays and concurrent duplicates do not create another assessment.
- Previously completed P2 `p2-pre-assessment` rows are **not retroactively charged** during migration. Old in-flight P2 requests recovered as uncertain cannot be silently assessed.
- Product default is enabled. An authenticated **administrator** may disable P3 for *future admissions* only by acknowledging consequences and supplying `expected_revision`; previously admitted requests retain their pinned policy and prior obligations persist. Enabling again creates a new revision. No setting change transfers or cancels credits.
- Private prompts and Worker response bodies are not stored in the ledger. Only approved hashed fulfillment metadata is retained.
- P3's `ASSESSED` state is an unpaid organization liability; **not** a confirmed spendable balance, reserved debit, bridge submission, or Main Computer Reserve payment. Those transitions are P4.

## Admin API (TLS Organization Service only)

- `GET /v1/admin/pinky-promise/policy`: read effective enabled state, policy revision, fixed values.
- `PATCH /v1/admin/pinky-promise/policy` with exactly `{ "enabled": false, "expected_revision": 1, "acknowledge_consequences": true }`: explicitly disable for requests admitted after the policy commit. Stale revision fails with `PINKY_POLICY_REVISION_CONFLICT`.
- `GET /v1/accounting/obligations`: read count, exact assessed total and up to 100 receipt records (with `has_more`; further pagination is future work). Requires administrator session; does not disclose prompt/response. Full ledger remains authoritative in SQLite.

The policy and ledger APIs return **501** when the private-request service is unavailable; they do not pretend SEH-private routing or a billing service exists when it is disabled.

## Windows test commands

From the repository root after installing P0, P1, P2, the Windows harness fix, and P3:

```powershell
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3.ps1
```

Or run P3 independently:

```powershell
python -m pytest -q tests/test_slh_pinky_obligations.py
```

Optional separate real-process verification:

```powershell
python -m tools.slh_seh_lab.run_lab verify-local --json
.\tools\slh_seh_lab\verify_pinky_p0_p1_p2_p3.ps1 -TransportRegression
```

The full exit still intentionally reports `NOT_ACCEPTED` because P4–P6 and historical resilience gates remain outstanding. Never interpret a local P3 test PASS as a real Reserve transfer.

## Continuation: P4 periodic sweep

P3 remains the immutable exactly-once assessment layer. P4 aggregates those assessments every five minutes, uses the authoritative Stable Hub ledger to transfer funds into the configured Reserve account, and marks a batch paid locally only after a matching receipt. See [P4 testing](slh-pinky-p4-testing.md). P3 alone still does not debit a wallet.
