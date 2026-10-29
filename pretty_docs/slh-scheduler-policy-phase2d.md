# Secure Local Hub Scheduler — Phase 2D

**Status:** Backend policy and marketplace-session reconciliation implemented. This is not the SLH administration UI, private B2SelfB work submission, or verified earnings accounting.

## Purpose and governing rule

The administrator chooses how the SLH spends organizational idle compute to earn credits. The SLH must preserve the configured private idle holdout before advertising surplus worker capacity to a Stable Hub. **A public work offer is accepted only after the SLH has atomically reserved a real local worker.** Already accepted public work is never preempted to satisfy a subsequent policy change.

The scheduler treats the physical worker as the resource identity. A public session is a revocable advertisement; the marketplace wallet is the economic identity, and may be shared by multiple independent public sessions.

## Policies

The policy is persisted in the organization's existing SLH SQLite database, and the administrator edits it through authenticated loopback APIs. The managed gateway reads it at every reconciliation and offer.

| Field | Default | Meaning |
|---|---|---|
| `mode` | `private_first` | `private_first`, `percentage`, `dedicated`, or `round_robin` |
| `max_public_percent` | `75` | Upper limit on the share of healthy worker slots advertised publicly (floor-rounded) |
| `min_private_idle_percent` | `25` | Minimum target of healthy, non-advertised idle worker slots (ceiling-rounded) |
| `dedicated_public` | `[]` | Named worker identities prioritized for public work and never reserved for private work |
| `dedicated_private` | `[]` | Named worker identities never advertised to the public Hub |
| `rotation_seconds` | `300` | Round-robin eligibility rotation interval, affecting new public advertisements only |
| `credit_cap` | `null` | Optional credit target for the named period; currently requires a trusted earning meter |
| `credit_period` | `day` | `day`, `week`, or `month`; accounting gate is not connected yet |

`dedicated` mode advertises only workers listed in `dedicated_public`. Other modes allow additional eligible shared workers, but prioritize dedicated-public workers. The percent constraints are limits, not promises that all eligible workers must be sold. If dedicated-public assignments exceed the available resource budget, no new public sessions are opened; the reported reason is `DEDICATED_PUBLIC_EXCEEDS_AVAILABLE_CAPACITY`.

For `round_robin`, the scheduler rotates which shared worker's capacity is advertised at each interval, without interrupting a running session. Work that has already been offered and reserved stays assigned until completion/failure. The scheduler creates new Stable Hub sessions for selected workers and gracefully drains sessions removed from the desired set.

The holdout is based on *healthy* workers only; disconnected and revoked workers do not count. Reservations for private workloads reduce what may be advertised. Since SLH private requester routing is not implemented, the private reservation API is currently available to backend/harness code rather than the requester UI. An idle reserve is a target; it cannot guarantee the required GPU/model capability or overcome a sudden device failure.

## Earnings cap: intentionally not yet active

The SLH has no trustworthy link from Stable Hub finalized worker earnings to a continuously updated, per-organization credit meter. Consequently, when the administrator sets `credit_cap`, the scheduler **stops opening new public worker sessions** and reports `CREDIT_CAP_REQUIRES_VERIFIED_METER`. It does **not** invent a credit count from job numbers or advertise falsely precise earned credits. Existing paid work is allowed to finish. Integrating authoritative earning and pending-commitment totals is a subsequent accounting integration milestone, not a completed feature in Phase 2D.

## Administrator API

New authenticated, loopback-only SLH operations (port `8897`):

- `GET /v1/admin/scheduler/policy`: current policy and optimistic-concurrency revision.
- `PATCH /v1/admin/scheduler/policy`: `{ "expected_revision": 0, "changes": { ... } }`. Requires a valid admin session, validates fields and conflicts, increments the revision, and audits the change.
- `GET /v1/admin/scheduler/status`: latest gateway heartbeat and policy; includes freshness/staleness information, actual measured idle count, public session IDs, reasons for blocked capacity, and explicit `credit_meter: NOT_CONNECTED`.

The admin API process and worker gateway share the existing SQLite state directory. Never change the policy by editing the database or file manually. No direct remote admin API exposure is enabled by this patch.

## Operator commands

With the SLH admin API already running and the administrator's local unencrypted development authenticator enrolled as `admin-pc`:

```powershell
python -m tools.slh_scheduler_admin show --admin-device admin-pc
python -m tools.slh_scheduler_admin status --admin-device admin-pc
```

Create a `scheduler-policy.json` file with only fields to modify, for example:

```json
{
  "mode": "round_robin",
  "max_public_percent": 60,
  "min_private_idle_percent": 25,
  "rotation_seconds": 300,
  "dedicated_public": ["designated-worker-1"],
  "dedicated_private": ["private-worker-2"]
}
```

Review first (no mutation):

```powershell
python -m tools.slh_scheduler_admin set --admin-device admin-pc --policy-file .\scheduler-policy.json
```

Apply (audited policy change):

```powershell
python -m tools.slh_scheduler_admin set --admin-device admin-pc --policy-file .\scheduler-policy.json --execute-mutations
```

Existing `python -m tools.slh_managed_gateway serve --enable-marketplace --network dev --wallet-file ...` automatically enforces the scheduling policy. It periodically reconciles public worker slots (`--scheduler-interval` defaults to 2 seconds) and reacts to registered and disconnected managed workers. The existing wallet signing key and MSK remain confined to the SLH; workers stay walletless.

## Harness gates

`python -m unittest tests.test_slh_scheduler_policy tests.test_slh_managed_gateway -v`

- Typed policy validation and mutually exclusive dedicated assignments.
- Admin-protected policy endpoints, revisions, persistence, and audit events.
- Healthy capacity and private holdout calculations.
- Dedicated workers, percentage limits, and round-robin rotation.
- No interruption of in-flight public work while reducing public capacity.
- Exclusive public/private capacity reservations; fail-closed new offers on draining sessions.
- Credit cap policy explicitly fails closed until actual accounting evidence exists.
- Existing managed-worker authentication, marketplace MSK, and independent upstream sessions remain compatible.

The existing worker-gateway tests establish Stable Hub session registration with shared wallet identity, not paid end-to-end earnings settlement. Do not treat scheduler tests as settlement proof.
