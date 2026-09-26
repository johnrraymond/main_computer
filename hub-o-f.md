# Hub operation-functionality specification

Status: functional companion to `hub-o.md`

Sources:

```text
hub.md
SHA-256: dc1def412907396cc63b5456a284d298284c3176e7559a80a70c2724ce8a6be7

hub-o.md
SHA-256: a47ed7f246be3fa2888b0372a94b3c60c383af59fed240660794e1d3f605df17
```

## 1. Purpose

This document decomposes the public Hub lifecycle operations into bounded, testable functionalities. It does not freeze Python file names; module ownership belongs in `hub-o-f-m.md`.

## 2. Functional rules

Every functionality must declare whether it is read-only, writes harness/operation state, mutates Hub deployment state, writes accepted Hub authority, or only emits derived evidence.

No implementation may:

- reinterpret Hub identity or placement after `prep`;
- change FDB or Chain authority;
- use dependency rectification as an implicit membership mutation;
- update accepted Hub authority before operation proof succeeds;
- treat container creation or transport success as Hub verification;
- silently delegate new Hub authority to legacy mixed Hub/FDB placement files.

## 3. Shared lifecycle functionalities

### HUB-OF-OBS-001 — Read accepted Hub state

Read the canonical accepted Hub document for one network. Distinguish `unborn`, `accepted-empty`, and non-empty `accepted` states.

### HUB-OF-OBS-002 — Observe accepted Hub deployments

Resolve each accepted Hub to its deployment target and inspect deployment identity/status without mutating it.

### HUB-OF-OBS-003 — Verify Hub runtime identity

Prove that the observed runtime identifies as the frozen Hub/network rather than only proving that a container is running.

### HUB-OF-OBS-004 — Observe adopted dependency contracts

Read or prove the FDB and Chain contract generation/hash currently adopted by each Hub.

### HUB-OF-OBS-005 — Produce topology verification

Combine membership, placement, runtime identity, and dependency-use observations into one read-only verification result.

### HUB-OF-PLC-001 — Parse logical Hub identity

Validate the Hub ID and derive placement intent without accepting arbitrary host/controller arguments from the operator.

### HUB-OF-PLC-002 — Resolve physical placement

Resolve the logical placement against canonical private infrastructure and freeze controller/host facts.

### HUB-OF-DEP-001 — Load canonical FDB consumer contract

Load and validate the exact FDB contract that a newly added Hub must consume.

### HUB-OF-DEP-002 — Load canonical Chain consumer contract

Load and validate the exact Chain contract that a newly added Hub must consume.

### HUB-OF-RUN-001 — Render Hub runtime projection

Compose Hub-owned deployment settings with the frozen FDB and Chain contracts. The projection is derived runtime configuration, not authority.

### HUB-OF-OPS-001 — Freeze operation target

Persist operation ID, prestate, logical Hub ID, physical placement, target cardinality, destructive authority, dependency contract generations/hashes, and runtime projection digest.

### HUB-OF-OPS-002 — Resume exact operation

Recognize already-completed stages and continue without changing the frozen target.

### HUB-OF-VER-001 — Prove Hub running

Prove the expected Hub deployment exists and reaches the required runtime health boundary.

### HUB-OF-VER-002 — Prove FDB consumption

Prove the Hub is using and can successfully consume the frozen FDB contract.

### HUB-OF-VER-003 — Prove Chain consumption

Prove the Hub is using and can successfully consume the frozen Chain contract.

### HUB-OF-AUTH-001 — Advance accepted Hub state

Sole functionality allowed to advance accepted Hub generation. Requires operation-specific proof and compare-against-prestate semantics.

## 4. `inspect`

Ordered functionality:

```text
HUB-OF-OBS-001
HUB-OF-OBS-002
HUB-OF-OBS-003
HUB-OF-OBS-004
HUB-OF-OBS-005
```

`inspect` is read-only. `accepted-empty` is a valid verified state. `unborn` means no accepted Hub authority exists yet.

## 5. `add-hub prep`

### HUB-OF-ADD-000 — Accept unborn as first-birth prestate

If no accepted Hub state exists, normalize the lifecycle prestate to generation `0`, Hub count `0`, and verified status `unborn`. Only `add-hub` may consume that prestate.

### HUB-OF-DEP-000 — Freeze both dependency contracts before first mutation

The add plan must resolve the current usable FDB contract and Chain contract, validate the chain RPC/chain ID plus Hub-required deployed-contract identities, classify optional/advertised contract probes as non-blocking evidence, and freeze both hashes/generations into the operation before Coolify mutation.

### HUB-OF-RUN-000 — Materialize runtime projection inside the Hub image

The deployment passes the frozen FDB cluster connection, FDB namespace, stable one-Hub topology, chain ID, RPC URL, and contract references as runtime projection. `run-exp-fdb-hub.py` materializes `fdb.cluster` and the topology file before launching the normal Hub runtime.

### HUB-OF-VER-000 — Prove both dependency edges

The public Hub observer must prove the requested Hub identity, expected FoundationDB storage projection, successful FDB-backed status response, and exact chain ID/RPC projection. Finalization is forbidden without this combined proof.


Ordered functionality:

```text
HUB-OF-OBS-001
HUB-OF-OBS-005
HUB-OF-PLC-001
HUB-OF-PLC-002
HUB-OF-DEP-001
HUB-OF-DEP-002
HUB-OF-RUN-001
HUB-OF-OPS-001
```

Required checks include:

- requested Hub identity is not already accepted;
- no conflicting unmanaged deployment occupies the frozen deployment identity;
- FDB consumer contract is present and valid;
- Chain consumer contract is present and valid;
- target Hub count is exactly current + 1;
- `accepted-empty -> one Hub` is marked as rebirth but uses the same operator intent.

No live mutation occurs in `prep`.

## 6. `add-hub do`

Ordered functionality:

```text
HUB-OF-OPS-002
render exact frozen deployment
create/update exact deployment
HUB-OF-VER-001
HUB-OF-VER-002
HUB-OF-VER-003
persist deployment result
```

The live deployer must consume the frozen runtime projection. It may not reread newer dependency contracts during `do`.

## 7. `add-hub operation-inspect`

Independently prove:

```text
expected deployment exists
Hub identity matches
Hub is healthy
frozen FDB contract is adopted and usable
frozen Chain contract is adopted and usable
```

The proof must be usable by `finalize` without trusting the original mutation API response.

## 8. `add-hub finalize`

Ordered functionality:

```text
re-read accepted prestate
prove prestate unchanged
re-run/consume operation proof
HUB-OF-AUTH-001
```

Accepted generation advances by exactly one and the requested Hub appears exactly once at the frozen host.

## 9. `remove-hub prep`

Required checks:

- requested Hub is accepted exactly once;
- exact deployment identity is frozen;
- target Hub count is exactly current - 1;
- ordinary removal leaves at least one accepted Hub;
- `1 -> 0` requires `--allow-full-deletion`;
- destructive authority is frozen into operation state.

Dependency contract values are not changed by Hub removal.

## 10. `remove-hub do`

For ordinary removal, delete only the exact frozen Hub deployment and prove it is absent. Surviving Hub deployments must not be reinterpreted or silently rectified.

For `1 -> 0`, delete the final exact deployment and prove absence. Do not invent migration work merely to justify terminal deletion.

## 11. `remove-hub operation-inspect`

Prove the target deployment is absent and the resulting accepted-target membership can be safely finalized. For non-terminal removal, surviving Hubs remain observable and are not part of the deletion effect.

## 12. `remove-hub finalize`

Advance accepted Hub generation by exactly one only after removal proof succeeds. Full deletion produces an accepted-empty topology with zero Hubs.

## 13. Retry and idempotence

Every mutating stage must recognize:

```text
already prepared
already deployed
already removed
already proven
already finalized
```

Retries must continue the frozen operation rather than create duplicate deployments or new operation identities.

## 14. Evidence

Each stage keeps command, stdout, stderr, normalized JSON result, and harness state beneath the harness run directory. Operator console output remains compact and human-readable.

## 15. Open implementation boundary in this patch

The public harness and internal command seam are implemented and tested. The following live functionalities remain deliberately open for the next patch:

```text
placement implementation
accepted Hub state writer
Coolify Hub lifecycle adapter
runtime projection builder
Hub observer
FDB consumer-contract reader
Chain consumer-contract reader
operation store
live add/remove protocols
```

The internal CLI must fail explicitly rather than falling back to the legacy mixed deployers until those functionalities exist.
