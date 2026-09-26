# Hub-FDB rectification functionality specification

Status: functional companion to `hub-fdb-o.md`

Sources:

```text
hub-fdb.md SHA-256: 7dc80ff74aaff04ef3e955799e9e326242ac48a56d1f562212bcd924f48f6a09
hub-fdb-o.md SHA-256: 07264e046906b35819a843aeb994a2ec04515b2eab6f2d897608799a7ec91a72
```

## Functional chain

### HUB-FDB-OF-CON-001 — Load canonical FDB consumer contract

Read, schema-validate, integrity-check, and compatibility-check the authoritative FDB consumer contract. During first-birth migration only, if that durable artifact is absent, deterministically materialize v1 from canonical non-empty FDB accepted state and then consume the resulting artifact.

### HUB-FDB-OF-HUB-001 — Load accepted Hub membership

Read canonical Hub accepted state. This functionality is read-only and cannot alter membership.

### HUB-FDB-OF-OBS-001 — Observe Hub FDB adoption

For each accepted Hub, determine the adopted FDB contract generation/hash and independently test usable FDB access.

### HUB-FDB-OF-CLS-001 — Classify rectification state

Produce `current`, `stale`, `unverifiable`, or `blocked` for each accepted Hub.

### HUB-FDB-OF-PLAN-001 — Freeze rectification target

Freeze the exact canonical contract and exact Hub set that requires mutation. Do not reread a newer target during execution.

### HUB-FDB-OF-RUN-001 — Render FDB-facing Hub projection

Derive `fdb.cluster`, namespace, compatibility/runtime values, and any transport configuration strictly from the frozen FDB contract.

### HUB-FDB-OF-DEP-001 — Apply Hub runtime update

Update only the FDB-facing runtime projection for the target Hub. Membership remains unchanged.

### HUB-FDB-OF-VER-001 — Prove FDB contract adoption

Prove both identity and use:

```text
Hub reports/records frozen contract generation/hash
Hub successfully performs the required FDB observer/probe
```

### HUB-FDB-OF-STATE-001 — Record adoption evidence

Record the adopted contract generation/hash and verification evidence without copying FDB authority into Hub topology.

## `inspect` pipeline

```text
CON-001 -> HUB-001 -> OBS-001 -> CLS-001
```

Read-only.

## `rectify` pipeline

```text
CON-001 -> HUB-001 -> OBS-001 -> CLS-001
-> PLAN-001 -> RUN-001 -> DEP-001 -> VER-001 -> STATE-001
```

Per-Hub execution is retryable and idempotent. Partial success must remain visible.

## Forbidden effects

No functionality in this chain may:

- add/remove Hubs;
- change accepted Hub generation for membership;
- add/remove FDB services;
- change FDB coordinators;
- publish a new FDB consumer contract;
- infer FDB truth from stale Hub runtime values.
