# Mother operation-functionality specification

Status: operation-first functional companion to `mother-o.md`

Sources:

```text
mother.md
SHA-256: f140d2b2f27979757146d1baf53820fcfb8bcdde30e18518a295eee2b26c2364

mother-o.md
SHA-256: 39c676c61b8a09ea2a4194cc315a56a9bda1d753dc1deadeeab0136546c56211
```

## 1. Purpose and authority

This document specifies the functionalities that compose every operation in
`mother-o.md`.

Its organizing unit is the operation:

```text
operation
  stage
    ordered functionality
      operation-specific use
```

This is why the file is `mother-o-f.md`, not `mother-f.md`. A functionality is
not complete merely because it exists somewhere in the implementation. It MUST
be placed in every operation that uses it, at the correct stage and dependency
position, with the correct inputs, outputs, verification, rollback behavior, and
authority effect.

`mother.md` governs architectural and safety semantics. `mother-o.md` governs
the operator-visible operation catalog. If this document conflicts with either,
the higher-level source governs.

## 2. Functional decomposition rules

### 2.1 Functionality boundary

A functionality is a bounded, independently verifiable capability that:

- accepts defined inputs;
- establishes defined outputs or postconditions;
- declares whether it reads, mutates live state, or changes authority;
- declares its failure result;
- declares its prestate and restoration contract when reversible;
- can be traced to one or more operations and tests.

A script, module, API route, journal entry, or protocol message is not
automatically a functionality. Those are possible implementations or evidence
carriers for a functional contract.

### 2.2 Reuse rule

A shared functionality keeps the same stable ID wherever it appears. Its row is
repeated under every operation that uses it so the operation remains readable
and complete.

The operation-specific placement MAY narrow:

- the inputs;
- the desired state;
- the participant set;
- the ordering dependencies;
- the verification assertions;
- the direction of rollback.

It MUST NOT weaken the canonical functionality contract.

### 2.3 No hidden-functionality rule

An operation MUST NOT:

- invoke a functionality absent from its functional pipeline;
- silently replace one functionality with a nearby capability;
- discover a new desired state during `do`;
- widen its participant or mutation scope after `prep`;
- perform repair through an unrelated operation;
- omit a required verification or rollback functionality because a lower-level
  API returned success.

### 2.4 Functional status

| Status | Meaning |
|---|---|
| `specified` | The governing source defines enough behavior to implement and test the functionality |
| `surface-open` | Functional behavior is defined, but public command spelling or options remain open |
| `contract-open` | The governing source requires the capability but does not yet define enough behavior to implement it safely |
| `conditional` | Required only when the prepared operation meets the stated condition |

### 2.5 Authority classes

| Class | Effect |
|---|---|
| `read-only` | Reads and reports; creates no authoritative or live mutation |
| `ledger-only` | Writes Mother operation, scope, lock, or planning records without mutating live infrastructure |
| `live-reversible` | Changes live infrastructure under captured prestate and an armed rollback frame |
| `local-authority` | Changes the active local authority or generation pointer |
| `replicated-authority` | Changes the authoritative network lineage or replica membership |
| `derived-local` | Replaces replay-derived local output without changing authority |

## 3. Stable functionality registry

The registry defines canonical names. Operation sections below define placement
and context.

### 3.1 Observation and classification

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-OBS-001` | Read a stable committed local head tuple |
| `MOTHER-OF-OBS-002` | Load and validate the head authorization bundle |
| `MOTHER-OF-OBS-003` | Locate the newest valid checkpoint and replay the committed lineage |
| `MOTHER-OF-OBS-004` | Compare replayed state with committed projections |
| `MOTHER-OF-OBS-005` | Collect and verify expected-replica reports |
| `MOTHER-OF-OBS-006` | Inventory Coolify services, identities, hosts, and lifecycle markers |
| `MOTHER-OF-OBS-007` | Probe guards and runtime process state |
| `MOTHER-OF-OBS-008` | Probe validator identity, QBFT membership, and block progress |
| `MOTHER-OF-OBS-009` | Probe owned RPC route graphs and backend eligibility |
| `MOTHER-OF-OBS-010` | Probe Hub/FDB participants and topology epochs |
| `MOTHER-OF-OBS-011` | Inspect active operation, scopes, stage, and participant evidence |
| `MOTHER-OF-OBS-012` | Inspect provisional frames, promoted rollback layers, and restorable range |
| `MOTHER-OF-OBS-013` | Inspect reservation, cancellation, finalization, acknowledgement, and release state |
| `MOTHER-OF-OBS-014` | Classify sealed state and contradictions |
| `MOTHER-OF-OBS-015` | Route the condition to allowed next commands |
| `MOTHER-OF-OBS-016` | Verify required schemas and capabilities |
| `MOTHER-OF-OBS-017` | Run the operation-relevant live assertion set |
| `MOTHER-OF-OBS-018` | Export immutable raw evidence with hashes and secret redaction |

### 3.2 Planning and operation control

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-CTL-001` | Parse and validate explicit operator intent |
| `MOTHER-OF-CTL-002` | Validate operation mode and operation-specific options |
| `MOTHER-OF-CTL-003` | Run the full clean-state and reachability barrier |
| `MOTHER-OF-CTL-004` | Freeze predecessor head, authority generation, and replica sets |
| `MOTHER-OF-CTL-005` | Calculate the complete desired state |
| `MOTHER-OF-CTL-006` | Calculate ordered functionality dependencies |
| `MOTHER-OF-CTL-007` | Declare and acquire logical mutation scopes |
| `MOTHER-OF-CTL-008` | Detect conflicts with active operations or local-adoption work |
| `MOTHER-OF-CTL-009` | Build the ordered rollback and restoration contract |
| `MOTHER-OF-CTL-010` | Freeze schema and capability requirements |
| `MOTHER-OF-CTL-011` | Write the immutable prepared operation record |
| `MOTHER-OF-CTL-012` | Publish the current-operation projection and allowed commands |
| `MOTHER-OF-CTL-013` | Revalidate frozen preconditions before dispatch |
| `MOTHER-OF-CTL-014` | Advance operation state durably |
| `MOTHER-OF-CTL-015` | Preserve request identity and idempotent retry state |
| `MOTHER-OF-CTL-016` | Release operation scopes only after terminal proof |
| `MOTHER-OF-CTL-017` | Calculate candidate authority and participant sets without freezing or acquiring them |

### 3.3 Invocation and transport

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-XPORT-001` | Resolve the exact participant and approved private endpoint |
| `MOTHER-OF-XPORT-002` | Enforce target, method, path, and non-public exposure policy |
| `MOTHER-OF-XPORT-003` | Dispatch or resume a durable idempotent participant request |
| `MOTHER-OF-XPORT-004` | Resolve durable accepted, running, succeeded, failed, or unknown request state |
| `MOTHER-OF-XPORT-005` | Distinguish transport failure from authoritative target rejection or result |

### 3.4 Journaling, authority, and finalization

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-AUTH-001` | Acquire or resume the exact full-set successor reservation |
| `MOTHER-OF-AUTH-002` | Construct and validate the full-set successor certificate |
| `MOTHER-OF-AUTH-003` | Cancel an uncommitted successor through full-set fenced cancellation |
| `MOTHER-OF-AUTH-004` | Construct the exact pending-action successor entry |
| `MOTHER-OF-AUTH-005` | Construct and persist the entry authorization bundle |
| `MOTHER-OF-AUTH-006` | Atomically commit the local entry-and-bundle head pair |
| `MOTHER-OF-AUTH-007` | Replicate and verify an authoritative head and immutable closure |
| `MOTHER-OF-AUTH-008` | Persist finalization-prepared intent and immutable closure evidence |
| `MOTHER-OF-AUTH-009` | Construct the exact finalization successor entry from pre-certificate facts |
| `MOTHER-OF-AUTH-010` | Atomically commit the finalization entry-and-bundle head pair |
| `MOTHER-OF-AUTH-011` | Resynchronize lagging participants to the exact committed finalization head |
| `MOTHER-OF-AUTH-012` | Collect replay-verified durable participant acknowledgements |
| `MOTHER-OF-AUTH-013` | Construct the full-set acknowledgement certificate |
| `MOTHER-OF-AUTH-014` | Validate terminal membership activation or retirement evidence |
| `MOTHER-OF-AUTH-015` | Release D026 reservations and authority-protocol fencing after terminal proof |
| `MOTHER-OF-AUTH-016` | Reconcile ambiguous head status from durable local evidence |
| `MOTHER-OF-AUTH-017` | Obtain and accept bootstrap authority for the exact birth entry |
| `MOTHER-OF-AUTH-018` | Construct the exact certified rollback-progress or rollback-completed successor from verified restored state |
| `MOTHER-OF-AUTH-019` | Construct the exact pending-action progress successor from a verified promoted phase |

### 3.5 Prestate and rollback

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-RB-001` | Capture complete typed prestate |
| `MOTHER-OF-RB-002` | Persist an armed provisional rollback frame |
| `MOTHER-OF-RB-003` | Checkpoint before mutating dispatch |
| `MOTHER-OF-RB-004` | Verify the complete postcondition and promote the frame |
| `MOTHER-OF-RB-005` | Resolve or restore an armed provisional frame |
| `MOTHER-OF-RB-006` | Restore promoted frames in strict LIFO order |
| `MOTHER-OF-RB-007` | Verify restored prestate and active invariants |
| `MOTHER-OF-RB-008` | Journal rollback progress and failures |
| `MOTHER-OF-RB-009` | Close promoted frames during finalization preparation |
| `MOTHER-OF-RB-010` | Release rollback ownership after verified terminal state |

### 3.6 Replica membership and birth

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-MEM-001` | Calculate and freeze current, prospective, transition, desired, retiring, and successor-authority sets |
| `MOTHER-OF-MEM-002` | Create an immutable prospective-host staging generation |
| `MOTHER-OF-MEM-003` | Acquire or resume the durable enrollment or bootstrap lock |
| `MOTHER-OF-MEM-004` | Transfer and verify private state and the complete recovery closure |
| `MOTHER-OF-MEM-005` | Produce immutable enrollment or bootstrap readiness evidence |
| `MOTHER-OF-MEM-006` | Commit the canonical readiness root |
| `MOTHER-OF-MEM-007` | Obtain prospective-host transition-certificate acceptance |
| `MOTHER-OF-MEM-008` | Persist and validate the membership commit-in-progress decision |
| `MOTHER-OF-MEM-009` | Activate a prospective replica after full acknowledgement |
| `MOTHER-OF-MEM-010` | Retire a reachable replica after full acknowledgement |
| `MOTHER-OF-MEM-011` | Cancel uncommitted readiness and tombstone the exact generation |
| `MOTHER-OF-MEM-012` | Claim the synthetic-predecessor network-birth generation |
| `MOTHER-OF-MEM-013` | Roll bootstrap participants into ordinary successor authority |
| `MOTHER-OF-MEM-014` | Preserve born-network continuity through zero-validator state |

### 3.7 Identity and service lifecycle

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-ID-001` | Read and validate `/runtime/state/mother/identity.private.yaml` as the canonical private-state identity source |
| `MOTHER-OF-ID-002` | Resolve the exact validator identity from `networks.<network>.validators.<node>` through `networks.<network>.nodes.<node>.validator_ref` |
| `MOTHER-OF-ID-003` | Install reserved identity without regeneration |
| `MOTHER-OF-ID-004` | Verify private/public identity derivation and ownership |
| `MOTHER-OF-ID-005` | Preserve private recovery material across replication and recovery |
| `MOTHER-OF-SVC-001` | Resolve the exact target service and immutable service identity |
| `MOTHER-OF-SVC-002` | Capture service, volume, environment, runtime, and marker prestate |
| `MOTHER-OF-SVC-003` | Create or repair the prepared Coolify service |
| `MOTHER-OF-SVC-004` | Establish a healthy private candidate |
| `MOTHER-OF-SVC-005` | Detach, disable, archive, or remove the prepared service |
| `MOTHER-OF-SVC-006` | Verify final service and runtime policy |
| `MOTHER-OF-SVC-007` | Restore captured service and runtime prestate |
| `MOTHER-OF-SVC-008` | Enforce private standby and route gating until validator eligibility is proven |

### 3.8 QBFT, RPC, and Hub/FDB topology

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-QBFT-001` | Observe and calculate the exact current and desired validator sets |
| `MOTHER-OF-QBFT-002` | Perform initial validator bootstrap |
| `MOTHER-OF-QBFT-003` | Reactivate a validator on preserved born-network material |
| `MOTHER-OF-QBFT-004` | Execute a prepared live soft-vote transition |
| `MOTHER-OF-QBFT-005` | Execute a prepared hard in-place QBFT transition |
| `MOTHER-OF-QBFT-006` | Verify complete validator-set agreement and required block progress |
| `MOTHER-OF-QBFT-007` | Restore captured QBFT configuration, data, markers, and process mode |
| `MOTHER-OF-RPC-001` | Capture the owned current RPC route graph and backend eligibility |
| `MOTHER-OF-RPC-002` | Calculate the exact desired owned RPC route graph |
| `MOTHER-OF-RPC-003` | Apply the prepared typed RPC route transition |
| `MOTHER-OF-RPC-004` | Verify every affected RPC host and public-service invariant |
| `MOTHER-OF-RPC-005` | Restore the captured typed RPC route prestate |
| `MOTHER-OF-HUB-001` | Capture current Hub/FDB topology and participant state |
| `MOTHER-OF-HUB-002` | Calculate the exact desired Hub/FDB topology and epoch |
| `MOTHER-OF-HUB-003` | Apply the prepared Hub/FDB transition |
| `MOTHER-OF-HUB-004` | Verify every affected node at the desired topology epoch |
| `MOTHER-OF-HUB-005` | Restore captured Hub/FDB prestate |

### 3.9 Local adoption, recovery, and reseal

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-SYNC-001` | Acquire and retain the exclusive local-adoption scope |
| `MOTHER-OF-SYNC-002` | Pin the local prestate and unanimously agreed remote candidate |
| `MOTHER-OF-SYNC-003` | Download the complete candidate closure into immutable staging |
| `MOTHER-OF-SYNC-004` | Replay and verify the staged candidate and projections |
| `MOTHER-OF-SYNC-005` | Persist activation-prepared evidence outside the swappable generation |
| `MOTHER-OF-SYNC-006` | Atomically switch the active-generation pointer |
| `MOTHER-OF-SYNC-007` | Reconcile pointer-determined activation after interruption |
| `MOTHER-OF-SYNC-008` | Discard staging and preserve the old active pointer |
| `MOTHER-OF-REC-001` | Resolve the recovery descriptor and expected replica set |
| `MOTHER-OF-REC-002` | Prove unanimous lineage, state, private material, and recovery closure |
| `MOTHER-OF-REC-003` | Download and verify every recovery object |
| `MOTHER-OF-REC-004` | Restore the complete local Mother state root |
| `MOTHER-OF-REC-005` | Replay recovered journals and rebuild projections |
| `MOTHER-OF-REC-006` | Verify recovered state against guards and live assertions |
| `MOTHER-OF-REC-007` | Create and activate the replacement head identity and epoch |
| `MOTHER-OF-REC-008` | Replicate and acknowledge replacement-head activation |
| `MOTHER-OF-RSL-001` | Collect every base-authority report and immutable invalid-head evidence |
| `MOTHER-OF-RSL-002` | Prove a common authority base and replay-valid selected predecessor |
| `MOTHER-OF-RSL-003` | Calculate observed, valid, and narrowly superseded network-head sets |
| `MOTHER-OF-RSL-004` | Enumerate and dispose every unresolved obligation safely |
| `MOTHER-OF-RSL-005` | Construct the prepared authority-reseal intent |
| `MOTHER-OF-RSL-006` | Construct the successor authoritative checkpoint |
| `MOTHER-OF-RSL-007` | Construct and unanimously accept the authority-reset proposal |
| `MOTHER-OF-RSL-008` | Construct the authority-reseal certificate from the full proposal-acceptance set |
| `MOTHER-OF-RSL-009` | Freeze and validate the D029+D028 readiness contract and structural composition |
| `MOTHER-OF-RSL-010` | Build and persist the authority-reseal authorization bundle |
| `MOTHER-OF-RSL-011` | Atomically commit the authority-reseal entry-and-bundle head |
| `MOTHER-OF-RSL-012` | Replicate, acknowledge, complete the reseal forward, and prove committed-fence rollover |
| `MOTHER-OF-RSL-013` | Prepare full-set D029 cancellation while retaining every active D029 fence |
| `MOTHER-OF-RSL-014` | Collect full-set completed-certificate acceptances at the correct pure-D029 or D029+D028 boundary |
| `MOTHER-OF-RSL-015` | Commit or abort D029 cancellation only after any composed D028 cancellation is terminal |

Release responsibility is single-owned by layer: `MOTHER-OF-AUTH-015`
releases D026 reservations and authority-protocol fencing only; `MOTHER-OF-RB-010`
closes rollback-frame ownership only; `MOTHER-OF-RSL-012` completes D029/D028
reseal protocol ownership only; and `MOTHER-OF-CTL-016` releases logical mutation
scopes and current-operation ownership only after the lower-layer terminal proof
exists.

### 3.10 Migration, rotation, and projections

| Functionality ID | Canonical responsibility |
|---|---|
| `MOTHER-OF-MIG-001` | Inventory source schemas, destination schemas, and required capabilities |
| `MOTHER-OF-MIG-002` | Preserve original bytes, hashes, and audit evidence |
| `MOTHER-OF-MIG-003` | Apply the declared deterministic migration |
| `MOTHER-OF-MIG-004` | Validate the complete migrated object graph |
| `MOTHER-OF-MIG-005` | Construct the migrated checkpoint or state object |
| `MOTHER-OF-MIG-006` | Replicate and verify the migrated result on the full expected set |
| `MOTHER-OF-MIG-007` | Commit the migrated authority at its defined boundary |
| `MOTHER-OF-MIG-008` | Cancel staging or restore the pre-migration authority before commit |
| `MOTHER-OF-ROT-001` | Freeze the affected identity and exposure scope |
| `MOTHER-OF-ROT-002` | Calculate dependent services, contracts, routes, and governance bindings |
| `MOTHER-OF-ROT-003` | Observe dependencies and declare the complete private/public prestate capture contract |
| `MOTHER-OF-ROT-004` | Generate or reserve replacement identity material |
| `MOTHER-OF-ROT-005` | Distribute and install replacement material safely |
| `MOTHER-OF-ROT-006` | Rebind every dependent component |
| `MOTHER-OF-ROT-007` | Retire superseded credentials or identities |
| `MOTHER-OF-ROT-008` | Verify replacement identity and dependency closure |
| `MOTHER-OF-ROT-009` | Commit rotation authority at its defined boundary |
| `MOTHER-OF-ROT-010` | Restore captured identity and dependency bindings before commit |
| `MOTHER-OF-ROT-011` | Capture complete current private and dependent public prestate immediately before mutation |
| `MOTHER-OF-PRJ-001` | Pin the complete authoritative local head tuple |
| `MOTHER-OF-PRJ-002` | Replay the pinned lineage into a new projection generation |
| `MOTHER-OF-PRJ-003` | Write, hash, flush, and verify the projection manifest |
| `MOTHER-OF-PRJ-004` | Re-read and compare the authoritative head before publication |
| `MOTHER-OF-PRJ-005` | Atomically publish the complete projection generation pointer |
| `MOTHER-OF-PRJ-006` | Discard stale output and enforce bounded retry |

## 4. Operation: `diagnose`

Operation ID: `MOTHER-OP-DIAGNOSE`

Class: read-only

Outcome: a complete diagnosis report that explains current authority, topology,
operation state, contradictions, blockers, and exact allowed next commands.

### 4.1 Functional pipeline

| Order | Functionality | Use in `diagnose` | Authority | Failure result |
|---:|---|---|---|---|
| 1 | `MOTHER-OF-OBS-001` | Read the stable local head tuple | read-only | Report unreadable or unprovable local head |
| 2 | `MOTHER-OF-OBS-002` | Validate the head's authorization bundle | read-only | Report invalid or missing bundle |
| 3 | `MOTHER-OF-OBS-003` | Replay from the newest valid checkpoint | read-only | Report corrupt or discontinuous lineage |
| 4 | `MOTHER-OF-OBS-004` | Compare replay with committed projections | read-only | Classify projection or authority contradiction |
| 5 | `MOTHER-OF-OBS-005` | Collect every expected replica's replay-derived report | read-only | Identify unreachable, incompatible, or divergent replica |
| 6 | `MOTHER-OF-XPORT-001`, `MOTHER-OF-XPORT-002`, `MOTHER-OF-XPORT-004`, and `MOTHER-OF-XPORT-005` | Query remote private status endpoints and preserve transport/result distinctions | read-only | Report unavailable transport separately from target evidence |
| 7 | `MOTHER-OF-OBS-006` | Inventory services, hosts, identities, and markers | read-only | Report partial inventory |
| 8 | `MOTHER-OF-ID-001` and `MOTHER-OF-ID-004` | Validate canonical private-state shape and derivation without exposing secrets | read-only | Report unreadable or mismatched identity evidence |
| 9 | `MOTHER-OF-OBS-007` | Probe guards and runtime processes | read-only | Report unreachable or contradictory runtime |
| 10 | `MOTHER-OF-OBS-008` | Probe validator set and block progress | read-only | Report consensus uncertainty or drift |
| 11 | `MOTHER-OF-OBS-009` | Probe RPC routes and eligibility | read-only | Report route drift |
| 12 | `MOTHER-OF-OBS-010` | Probe Hub/FDB topology | read-only | Report topology drift |
| 13 | `MOTHER-OF-OBS-011` | Inspect the active operation and scopes | read-only | Report ledger inconsistency |
| 14 | `MOTHER-OF-OBS-012` | Inspect provisional and promoted rollback state | read-only | Report incomplete recovery closure |
| 15 | `MOTHER-OF-OBS-013` | Inspect reservation and finalization progress | read-only | Report incomplete or contradictory participant evidence |
| 16 | `MOTHER-OF-OBS-016` | Report schema and capability compatibility | read-only | Block unsafe mutations without blocking evidence output |
| 17 | `MOTHER-OF-OBS-014` | Classify the total state | read-only | Return `wedged` when proof is insufficient |
| 18 | `MOTHER-OF-OBS-015` | Render exact allowed next commands | read-only | Fail closed if command legality cannot be proven |

### 4.2 Functional output

The diagnosis output MUST distinguish:

- authoritative state from observed live state;
- `local-current` from `local-stale-network-agrees`;
- `network-replica-mismatch` from lost local authority or `wedged`;
- projection-only damage from lineage damage;
- pre-commit failure from post-commit forward-completion work;
- node membership from replica membership;
- rollback-capable states from rollback-closed states.

No diagnose functionality MAY acquire a mutation lock, create a lifecycle
operation, refresh state, repair projections, copy remote authority, or mutate
live infrastructure.

## 5. Operation: `plan`

Operation ID: `MOTHER-OP-PLAN`

Class: read-only planner; normally invoked inside an operation's `prep`

Status: `surface-open` as a standalone public command

Outcome: a candidate operation plan without operation ownership or live
mutation.

### 5.1 Functional pipeline

| Order | Functionality | Use in `plan` | Authority |
|---:|---|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Supply the current proven facts | read-only |
| 2 | `MOTHER-OF-CTL-001` | Parse the proposed operator intent | read-only |
| 3 | `MOTHER-OF-CTL-002` | Validate proposed mode and options | read-only |
| 4 | `MOTHER-OF-OBS-016` | Check required schemas and capabilities | read-only |
| 5 | `MOTHER-OF-CTL-017` | Calculate the candidate predecessor and participant sets without freezing them | read-only |
| 6 | `MOTHER-OF-CTL-005` | Calculate the candidate desired state | read-only |
| 7 | `MOTHER-OF-CTL-006` | Order the required functionalities | read-only |
| 8 | `MOTHER-OF-CTL-008` | Detect active-operation conflicts | read-only |
| 9 | `MOTHER-OF-CTL-009` | Calculate rollback requirements | read-only |
| 10 | `MOTHER-OF-OBS-015` | Report feasibility, risks, blockers, and next command | read-only |

`plan` MUST NOT write the prepared operation record, acquire scopes, claim a
successor, stage a host, or mutate live state. `prep` repeats every fact that
MUST be frozen and is not permitted to trust an old plan blindly.

## 6. Operation: evidence inspection/export

Operation ID: `MOTHER-OP-EVIDENCE-EXPORT`

Class: read-only

Status: `surface-open`

Outcome: a content-addressed evidence package that preserves inspectable bytes
without asserting that unknown state is valid.

### 6.1 Functional pipeline

| Order | Functionality | Use in evidence export | Authority |
|---:|---|---|---|
| 1 | `MOTHER-OF-OBS-001` through `MOTHER-OF-OBS-005` where safely supported | Identify authoritative and conflicting evidence roots | read-only |
| 2 | `MOTHER-OF-OBS-011` through `MOTHER-OF-OBS-013` | Identify active operational evidence | read-only |
| 3 | `MOTHER-OF-OBS-016` | Record known, unknown, and unsupported schemas without guessing | read-only |
| 4 | `MOTHER-OF-OBS-018` | Export original bytes, hashes, locators, and redacted metadata | read-only |

The package MUST NOT expose private keys, normalize unknown objects into a new
schema, discard hash-invalid evidence, or claim an authoritative winner.

## 7. Operation: `add-node`

Operation ID: `MOTHER-OP-ADD-NODE`

Class: authoritative distributed node lifecycle

Modes: `initial`, `reactivate`, `soft`, `hard`

Outcome: one complete node is created or repaired, assigned its reserved
identity, admitted through the prepared QBFT path, added to RPC and Hub/FDB
topology, and finalized as one distributed action.

### 7.1 `prep` functionalities

| Order | Functionality | Operation-specific placement | Status |
|---:|---|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Establish current authority and topology | specified |
| 2 | `MOTHER-OF-CTL-001` | Freeze network, service, host, and add intent | specified |
| 3 | `MOTHER-OF-CTL-002` | Require exactly one valid add mode | specified |
| 4 | `MOTHER-OF-CTL-003` | Require clean current replicas and eligible prospective host | specified |
| 5 | `MOTHER-OF-OBS-016` | Prove every required schema and capability | specified |
| 6 | `MOTHER-OF-MEM-001` | Freeze all membership and authority sets | specified |
| 7 | `MOTHER-OF-CTL-004` | Freeze predecessor head and authority generation | specified |
| 8 | `MOTHER-OF-SVC-001` | Resolve exact target service identity, host, ports, and reservations | specified |
| 9 | `MOTHER-OF-ID-001` | Validate canonical private-state source | specified |
| 10 | `MOTHER-OF-ID-002` | Resolve the exact pre-reserved validator identity | specified |
| 11 | `MOTHER-OF-QBFT-001` | Freeze current and desired validator sets | specified |
| 12 | `MOTHER-OF-RPC-001` and `MOTHER-OF-RPC-002` | Freeze current and desired owned route graphs | specified |
| 13 | `MOTHER-OF-HUB-001` and `MOTHER-OF-HUB-002` | Freeze current and desired Hub/FDB topology | specified |
| 14 | `MOTHER-OF-CTL-005` | Calculate the complete post-add state | specified |
| 15 | `MOTHER-OF-CTL-006` | Freeze stage and functionality ordering | specified |
| 16 | `MOTHER-OF-CTL-007` and `MOTHER-OF-CTL-008` | Own every affected service, identity, network, route, topology, and membership scope | specified |
| 17 | `MOTHER-OF-CTL-009` | Define restoration for every possible live mutation | specified |
| 18 | `MOTHER-OF-CTL-010` and `MOTHER-OF-CTL-011` | Freeze compatibility and write immutable prepared record | specified |
| 19 | `MOTHER-OF-CTL-012` | Publish active operation and legal next commands | specified |

### 7.2 `do` functionalities

| Order | Functionality | Operation-specific placement | Condition |
|---:|---|---|---|
| 1 | `MOTHER-OF-CTL-013` | Revalidate frozen head, replicas, host, and topology | always |
| 1a | `MOTHER-OF-XPORT-001`, `MOTHER-OF-XPORT-002`, `MOTHER-OF-XPORT-003`, `MOTHER-OF-XPORT-004`, and `MOTHER-OF-XPORT-005` | Resolve participants and preserve durable idempotent request/result semantics for every remote step | always |
| 2 | `MOTHER-OF-MEM-012` | Claim the exact true network-birth generation | `initial` only |
| 3 | `MOTHER-OF-MEM-002`, `MOTHER-OF-MEM-003`, `MOTHER-OF-MEM-004`, `MOTHER-OF-MEM-005`, and `MOTHER-OF-MEM-006` | Stage, lock, transfer, verify, and accept a host generation | prospective or bootstrap host |
| 4 | `MOTHER-OF-ID-005` | Preserve and verify private recovery material in the staged generation | prospective or bootstrap host |
| 5 | `MOTHER-OF-AUTH-004` | Construct the exact pending-action entry before any claim or certificate | always |
| 6 | `MOTHER-OF-AUTH-017` | Obtain and accept bootstrap authority for the exact first entry | `initial` only |
| 7 | `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` | Reserve and certify the exact already-constructed successor | post-birth only |
| 8 | `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008` | Accept the transition and persist membership decision | birth or enrollment |
| 9 | `MOTHER-OF-AUTH-005` | Construct the authorization bundle from the entry and post-entry evidence | always |
| 10 | `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` | Commit and replicate pending action before live mutation | always |
| 11 | `MOTHER-OF-MEM-013` | Roll birth participants into ordinary successor ownership | `initial` only |
| 12 | `MOTHER-OF-SVC-002`, `MOTHER-OF-RB-001`, `MOTHER-OF-RB-002`, `MOTHER-OF-RB-003` | Capture and arm service/identity prestate | always |
| 13 | `MOTHER-OF-SVC-003` | Create or repair the exact prepared service | always |
| 14 | `MOTHER-OF-ID-003` and `MOTHER-OF-ID-004` | Install and verify reserved identity | always |
| 15 | `MOTHER-OF-SVC-004` | Prove healthy private-candidate state | always |
| 15a | `MOTHER-OF-SVC-008` | Keep the candidate private and in standby until validator eligibility is proven | always |
| 16 | `MOTHER-OF-RB-004` | Promote verified service/identity frame | always |
| 16a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate service/identity phase progress | always |
| 17 | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Arm validator-topology prestate | always |
| 18 | `MOTHER-OF-QBFT-002` | Establish first validator from born-network material | `initial` only |
| 19 | `MOTHER-OF-QBFT-003` | Start validator on preserved born-network material | `reactivate` only |
| 20 | `MOTHER-OF-QBFT-004` | Execute exact prepared live admission vote | `soft` only |
| 21 | `MOTHER-OF-QBFT-005` | Execute exact prepared offline in-place topology change | `hard` only |
| 22 | `MOTHER-OF-QBFT-006` and `MOTHER-OF-RB-004` | Verify validator set and promote frame | always |
| 22a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate validator phase progress | always |
| 23 | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Arm RPC prestate | always |
| 24 | `MOTHER-OF-RPC-003` and `MOTHER-OF-RPC-004` | Publish eligible backend and verify every affected host | always |
| 25 | `MOTHER-OF-RB-004` | Promote verified RPC frame | always |
| 25a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate RPC phase progress | always |
| 26 | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Arm Hub/FDB prestate | always |
| 27 | `MOTHER-OF-HUB-003` and `MOTHER-OF-HUB-004` | Publish and verify desired topology | always |
| 28 | `MOTHER-OF-RB-004` | Promote verified Hub/FDB frame | always |
| 28a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate Hub/FDB phase progress | always |
| 29 | `MOTHER-OF-OBS-017` | Run complete distributed post-`do` assertions | always |
| 30 | `MOTHER-OF-CTL-014` and `MOTHER-OF-CTL-012` | Enter ready-to-finalize or remediation-required | always |

For `initial`, `MOTHER-OF-AUTH-004` constructs the birth-plus-pending-action
entry as the sequence-1 `initial-network-birth` checkpoint. Its predecessor
entry and predecessor authorization-bundle hashes are null. The bootstrap
certificate and non-null `bootstrap-birth` bundle are created afterward and
therefore do not participate in the checkpoint entry hash.

### 7.3 `finalize` functionalities

| Order | Functionality | Operation-specific placement |
|---:|---|---|
| 1 | `MOTHER-OF-CTL-013` | Revalidate exact operation, head, sets, receipts, and scopes |
| 2 | `MOTHER-OF-SVC-006` | Verify service and installed identity |
| 2a | `MOTHER-OF-ID-005` | Verify private-state and private-recovery closure on every required participant |
| 3 | `MOTHER-OF-QBFT-006` | Verify desired validator set and required block progress |
| 4 | `MOTHER-OF-RPC-004` | Verify desired routes on every affected host |
| 5 | `MOTHER-OF-HUB-004` | Verify desired topology on every affected node |
| 6 | `MOTHER-OF-OBS-017` | Verify complete operation postconditions |
| 7 | `MOTHER-OF-RB-009` and `MOTHER-OF-AUTH-008` | Close frames and persist finalization intent |
| 8 | `MOTHER-OF-AUTH-009` | Construct the exact finalization successor entry |
| 9 | `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` | Reserve and certify that exact successor |
| 9a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern certificate, acceptance, replication, acknowledgement, and release requests |
| 10 | `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008` | Bind transition acceptance and decision when enrollment/birth applies |
| 11 | `MOTHER-OF-AUTH-005` | Build the finalization authorization bundle |
| 12 | `MOTHER-OF-AUTH-010` | Atomically commit the final head and close rollback |
| 13 | `MOTHER-OF-AUTH-011` | Replicate or resynchronize exact final head |
| 14 | `MOTHER-OF-AUTH-012` and `MOTHER-OF-AUTH-013` | Collect acknowledgements and construct certificate |
| 15 | `MOTHER-OF-MEM-009` | Activate prospective replica when a prospective host is present |
| 16 | `MOTHER-OF-AUTH-014` and `MOTHER-OF-AUTH-015` | Complete membership and release D026 reservations and authority-protocol fencing |
| 17 | `MOTHER-OF-RB-010` and `MOTHER-OF-CTL-016` | Close rollback ownership, then release logical scopes and current-operation ownership after terminal proof |

### 7.4 `rollback` functionalities

Rollback order is the reverse of verified live mutation:

| Order | Functionality | Restoration effect |
|---:|---|---|
| 1 | `MOTHER-OF-AUTH-003` or `MOTHER-OF-MEM-011` | Cancel uncommitted successor or prospective readiness when applicable |
| 2 | `MOTHER-OF-RB-005` | Resolve any armed provisional frame |
| 3 | `MOTHER-OF-HUB-005` | Restore prior Hub/FDB topology |
| 4 | `MOTHER-OF-RPC-005` | Restore prior RPC routes |
| 5 | `MOTHER-OF-QBFT-007` | Restore prior validator topology and process mode |
| 6 | `MOTHER-OF-SVC-007` | Restore or remove only the service state created by this operation |
| 7 | `MOTHER-OF-RB-006` through `MOTHER-OF-RB-008` | Drive strict LIFO restoration and record evidence |
| 8 | `MOTHER-OF-AUTH-018`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, conditional `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate the exact verified rollback state after a pending-action head exists |
| 9 | `MOTHER-OF-MEM-011` | Tombstone uncommitted enrollment or birth generation when no accepted transition requires certified compensation |
| 10 | `MOTHER-OF-RB-010` and `MOTHER-OF-CTL-016` | Release scopes only after complete restoration and full-set rolled-back-head proof |

For `initial`, rollback before the first local head commit restores the unborn
bootstrap prestate. After the first head commit, rollback preserves the birth
identity, genesis, lineage, private state, and replica set while restoring the
node infrastructure through a certified compensating successor.

## 8. Operation: `remove-node`

Operation ID: `MOTHER-OP-REMOVE-NODE`

Class: authoritative distributed node lifecycle

Modes: `soft`, `hard`

Conditional option: `--allow-zero-validators`

Outcome: one complete node is withdrawn from Hub/FDB and RPC topology, removed
from QBFT, and detached or removed as one reversible action. Replica membership
remains unchanged.

### 8.1 `prep` functionalities

| Order | Functionality | Operation-specific placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Establish authority and live dependencies |
| 2 | `MOTHER-OF-CTL-001` and `MOTHER-OF-CTL-002` | Freeze target, removal policy, mode, and zero-validator authorization |
| 3 | `MOTHER-OF-CTL-003` and `MOTHER-OF-OBS-016` | Require clean full-set state and compatible participants |
| 4 | `MOTHER-OF-MEM-001` | Freeze unchanged replica membership |
| 5 | `MOTHER-OF-CTL-004` | Freeze predecessor authority |
| 6 | `MOTHER-OF-SVC-001` | Resolve exact target service and validator identity |
| 7 | `MOTHER-OF-ID-001`, `MOTHER-OF-ID-004`, and `MOTHER-OF-ID-005` | Prove target identity while preserving canonical private material |
| 8 | `MOTHER-OF-QBFT-001` | Calculate surviving validator set |
| 9 | `MOTHER-OF-MEM-014` | Preserve born-network state if the desired validator set is empty |
| 10 | `MOTHER-OF-RPC-001` and `MOTHER-OF-RPC-002` | Calculate routes without target |
| 11 | `MOTHER-OF-HUB-001` and `MOTHER-OF-HUB-002` | Calculate topology without target |
| 12 | `MOTHER-OF-CTL-005` through `MOTHER-OF-CTL-009` | Freeze desired state, order, scopes, conflicts, and rollback |
| 13 | `MOTHER-OF-CTL-010` through `MOTHER-OF-CTL-012` | Freeze compatibility, record operation, and publish allowed commands |

### 8.2 `do` functionalities

| Order | Functionality | Operation-specific placement |
|---:|---|---|
| 1 | `MOTHER-OF-CTL-013` | Revalidate exact target, predecessor, replicas, and dependencies |
| 1a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Resolve participants and preserve durable idempotent request/result semantics |
| 2 | `MOTHER-OF-AUTH-004` | Construct the exact pending-removal successor entry |
| 3 | `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` | Reserve and certify that exact successor |
| 4 | `MOTHER-OF-AUTH-005` | Build the authorization bundle |
| 5 | `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` | Commit and replicate pending removal before live mutation |
| 6 | `MOTHER-OF-HUB-001`, `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Capture and arm Hub/FDB prestate |
| 7 | `MOTHER-OF-HUB-003`, `MOTHER-OF-HUB-004`, `MOTHER-OF-RB-004` | Withdraw target, verify survivors, and promote frame |
| 7a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate Hub/FDB phase progress |
| 8 | `MOTHER-OF-RPC-001`, `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Capture and arm RPC prestate |
| 9 | `MOTHER-OF-RPC-003`, `MOTHER-OF-RPC-004`, `MOTHER-OF-RB-004` | Withdraw target routes, verify public service, and promote frame |
| 9a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate RPC phase progress |
| 10 | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Capture and arm QBFT prestate while target remains running |
| 11 | `MOTHER-OF-QBFT-004` or `MOTHER-OF-QBFT-005` | Execute the prepared soft or hard removal |
| 12 | `MOTHER-OF-QBFT-006` and `MOTHER-OF-RB-004` | Verify desired set and promote frame |
| 12a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate QBFT phase progress |
| 13 | `MOTHER-OF-SVC-002`, `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Capture and arm service prestate |
| 14 | `MOTHER-OF-ID-005` | Reprove canonical private material remains retained before service removal |
| 15 | `MOTHER-OF-SVC-005`, `MOTHER-OF-SVC-006`, `MOTHER-OF-RB-004` | Apply exact removal policy, verify it, and promote frame |
| 15a | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate service-removal phase progress |
| 16 | `MOTHER-OF-OBS-017` | Verify the complete surviving network |
| 17 | `MOTHER-OF-CTL-014` and `MOTHER-OF-CTL-012` | Enter ready-to-finalize or remediation-required |

### 8.3 `finalize` functionalities

`remove-node finalize` uses:

1. `MOTHER-OF-CTL-013`;
2. `MOTHER-OF-SVC-006`;
3. `MOTHER-OF-ID-005`;
4. `MOTHER-OF-QBFT-006`, or `MOTHER-OF-MEM-014` for an authorized
   zero-validator result;
5. `MOTHER-OF-RPC-004`;
6. `MOTHER-OF-HUB-004`;
7. `MOTHER-OF-OBS-017`;
8. `MOTHER-OF-RB-009`;
9. `MOTHER-OF-AUTH-008`;
10. `MOTHER-OF-AUTH-009`;
11. `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002`;
12. `MOTHER-OF-AUTH-005`;
13. `MOTHER-OF-AUTH-010` through `MOTHER-OF-AUTH-013`;
14. `MOTHER-OF-AUTH-015`;
15. `MOTHER-OF-RB-010` and `MOTHER-OF-CTL-016`.

It MUST freshly prove that replica membership did not change.

Every participant-facing finalization step uses `MOTHER-OF-XPORT-001` through
`MOTHER-OF-XPORT-005`.

### 8.4 `rollback` functionalities

Rollback restores:

1. any unresolved provisional service frame;
2. service/runtime prestate through `MOTHER-OF-SVC-007`;
3. QBFT prestate through `MOTHER-OF-QBFT-007`;
4. RPC prestate through `MOTHER-OF-RPC-005`;
5. Hub/FDB prestate through `MOTHER-OF-HUB-005`;
6. remaining frames through `MOTHER-OF-RB-005` through
   `MOTHER-OF-RB-010`;
7. any uncommitted successor through `MOTHER-OF-AUTH-003`.

After a pending-action head exists, rollback uses
`MOTHER-OF-AUTH-018`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`,
conditional `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008`,
`MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and
`MOTHER-OF-AUTH-007` to commit and replicate the verified compensating state.
It does not attempt to cancel an already-committed pending-action head.

Rollback MUST NOT de-enroll the host or create a new genesis.

### 8.5 Compatibility alias binding

`add-validator` and `remove-validator`, if exposed, do not define additional
functional pipelines:

```text
add-validator
  → MOTHER-OP-ADD-NODE

remove-validator
  → MOTHER-OP-REMOVE-NODE
```

The alias MUST invoke the complete operation with the same functionalities,
ordering, scopes, authority, rollback, and verification. It MUST NOT select only
the QBFT functionality rows.

## 9. Operation: `restore-service`

Operation ID: `MOTHER-OP-RESTORE-SERVICE`

Class: authoritative service repair

Status: `surface-open` for the complete option set

Outcome: the exact saved Coolify service is repaired or recreated without
changing QBFT membership, genesis, validator identity, RPC authority, or Hub/FDB
authority except where an explicitly prepared service-health dependency requires
verification.

### 9.1 `prep` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Prove that service repair is the correct layer |
| 2 | `MOTHER-OF-CTL-001` through `MOTHER-OF-CTL-003` | Freeze repair intent and clean-state barrier |
| 3 | `MOTHER-OF-SVC-001` | Resolve exact saved service identity |
| 4 | `MOTHER-OF-ID-001`, `MOTHER-OF-ID-002`, `MOTHER-OF-ID-004` | Resolve and verify expected identity without rotating it |
| 5 | `MOTHER-OF-OBS-006` and `MOTHER-OF-OBS-007` | Observe existing service, volume, environment, runtime, and marker state without capturing rollback prestate |
| 6 | `MOTHER-OF-CTL-005` and `MOTHER-OF-CTL-006` | Define exact restored service state and ordered repair |
| 7 | `MOTHER-OF-CTL-007` through `MOTHER-OF-CTL-012` | Acquire scope, define rollback, and record operation |

### 9.2 `do` functionalities

1. `MOTHER-OF-CTL-013` revalidates the service and authoritative identity.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern every remote
   service, guard, and participant request.
3. `MOTHER-OF-AUTH-004` constructs the exact pending-repair successor entry.
4. `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` reserve and certify that exact
   successor.
5. `MOTHER-OF-AUTH-005` builds its authorization bundle.
6. `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` commit and replicate the
   pending repair action.
7. `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` arm the service repair frame.
8. `MOTHER-OF-SVC-003` creates or repairs only the prepared service.
9. `MOTHER-OF-ID-003` installs the already-authoritative reserved identity when
   installation is required.
10. `MOTHER-OF-SVC-004`, `MOTHER-OF-ID-004`, and `MOTHER-OF-SVC-006` verify the
   private candidate, identity, volumes, and runtime policy.
11. `MOTHER-OF-RB-004` promotes the verified frame.
12. `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`,
    `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`,
    `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` commit and replicate the
    verified service-repair phase.
13. `MOTHER-OF-OBS-017` proves that no forbidden topology or membership change
   occurred.

### 9.3 `finalize` and `rollback`

Finalize uses `MOTHER-OF-RB-009`, `MOTHER-OF-AUTH-008`,
`MOTHER-OF-AUTH-009`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`,
`MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-010` through
`MOTHER-OF-AUTH-013`, `MOTHER-OF-AUTH-015`, and `MOTHER-OF-CTL-016`, in the shared finalization
order. Every participant-facing step also uses `MOTHER-OF-XPORT-001` through
`MOTHER-OF-XPORT-005`.

Rollback uses `MOTHER-OF-SVC-007` and the generic rollback chain. If the
operation created a new unfinalized service, rollback removes only that service.
It MUST NOT delete a pre-existing volume unless the prepared prestate proves the
operation created it.

## 10. Operation: `reseal-qbft`

Operation ID: `MOTHER-OP-RESEAL-QBFT`

Class: authoritative in-place QBFT repair

Outcome: selected existing services contain and run the exact prepared QBFT
configuration and agree on the desired validator set.

### 10.1 `prep` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Prove QBFT repair is the correct layer |
| 2 | `MOTHER-OF-CTL-001` and `MOTHER-OF-CTL-002` | Freeze selected existing services and hard-repair intent |
| 3 | `MOTHER-OF-CTL-003`, `MOTHER-OF-OBS-016`, `MOTHER-OF-MEM-001` | Prove clean replicas, capabilities, and unchanged membership |
| 4 | `MOTHER-OF-QBFT-001` | Freeze desired validator set independently of service count |
| 5 | `MOTHER-OF-OBS-006`, `MOTHER-OF-OBS-007`, and `MOTHER-OF-OBS-008` | Observe process, volume, config, data, and lifecycle markers; actual rollback prestate remains a `do` functionality |
| 6 | `MOTHER-OF-CTL-005` through `MOTHER-OF-CTL-012` | Freeze desired state, order, scopes, rollback, and operation record |

### 10.2 `do` functionalities

1. `MOTHER-OF-CTL-013` revalidates selected services and authority.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern every remote
   validator, guard, and participant request.
3. `MOTHER-OF-AUTH-004` constructs the exact pending-reseal successor entry.
4. `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` reserve and certify that exact
   successor.
5. `MOTHER-OF-AUTH-005` builds its authorization bundle.
6. `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` commit and replicate the
   pending repair action.
7. `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` capture and arm the complete
   selected-service QBFT prestate.
8. `MOTHER-OF-QBFT-005` stops only validator subprocesses, writes the identical
   prepared configuration in place, clears only captured stale markers, and
   restarts the validators.
9. `MOTHER-OF-QBFT-006` verifies validator-set agreement and block progress.
10. `MOTHER-OF-RB-004` promotes the verified frame.
11. `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`,
    `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`,
    `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` commit and replicate the
    verified QBFT-repair phase.
12. `MOTHER-OF-OBS-017` proves service identity and non-QBFT topology were not
   replaced.

### 10.3 `finalize` and `rollback`

Finalize uses `MOTHER-OF-QBFT-006`, `MOTHER-OF-RB-009`,
`MOTHER-OF-AUTH-008`, `MOTHER-OF-AUTH-009`, `MOTHER-OF-AUTH-001`,
`MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, and
`MOTHER-OF-AUTH-010` through `MOTHER-OF-AUTH-013`, followed by
`MOTHER-OF-AUTH-015`. Every participant-facing
step also uses `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005`.

Rollback uses `MOTHER-OF-QBFT-007` followed by
`MOTHER-OF-RB-005` through `MOTHER-OF-RB-010`. It restores only captured
configuration, data, markers, and process mode. It does not invent prestate.

## 11. Operation: `rpc-propagate`

Operation ID: `MOTHER-OP-RPC-PROPAGATE`

Class: authoritative route repair

Status: `surface-open` for the full CLI option set

Outcome: the damaged owned RPC route graph is reconciled to the explicitly
prepared graph without changing validator, service, Hub/FDB, or replica
authority.

### 11.1 Functional pipeline

| Stage | Order | Functionality | Placement |
|---|---:|---|---|
| `prep` | 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Prove route-only repair is appropriate |
| `prep` | 2 | `MOTHER-OF-CTL-001` through `MOTHER-OF-CTL-004` | Freeze intent, barrier, predecessor, and participants |
| `prep` | 3 | `MOTHER-OF-RPC-001` | Capture all owned routes and eligible backends |
| `prep` | 4 | `MOTHER-OF-RPC-002` | Calculate the exact desired owned graph |
| `prep` | 5 | `MOTHER-OF-CTL-005` through `MOTHER-OF-CTL-012` | Freeze plan, scopes, rollback, and operation record |
| `do` | 1 | `MOTHER-OF-CTL-013` | Revalidate head, backends, and route ownership |
| `do` | 1a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern every remote route and participant request |
| `do` | 2 | `MOTHER-OF-AUTH-004` | Construct the exact pending-route successor entry |
| `do` | 3 | `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` | Reserve and certify that exact successor |
| `do` | 4 | `MOTHER-OF-AUTH-005` | Build the authorization bundle |
| `do` | 5 | `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` | Commit and replicate pending route repair |
| `do` | 6 | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-003` | Arm typed route prestate |
| `do` | 7 | `MOTHER-OF-RPC-003` | Apply exact route transition |
| `do` | 8 | `MOTHER-OF-RPC-004` and `MOTHER-OF-RB-004` | Verify all affected hosts and promote frame |
| `do` | 9 | `MOTHER-OF-AUTH-019`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Commit and replicate verified route-repair progress |
| `finalize` | 1 | `MOTHER-OF-RPC-004` and `MOTHER-OF-OBS-017` | Reprove route and non-route invariants |
| `finalize` | 2 | `MOTHER-OF-RB-009`, `MOTHER-OF-AUTH-008`, `MOTHER-OF-AUTH-009`, `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-010` through `MOTHER-OF-AUTH-013`, and `MOTHER-OF-AUTH-015` | Commit and acknowledge repair in the shared finalization order |
| `rollback` | 1 | `MOTHER-OF-RPC-005` and `MOTHER-OF-RB-005` through `MOTHER-OF-RB-010` | Restore typed route prestate |

This operation MUST NOT be a mandatory cleanup step after a successful
`add-node` or `remove-node`; those operations already contain RPC functionality.

## 12. Operation: `sync-state`

Operation ID: `MOTHER-OP-SYNC-STATE`

Class: staged local adoption

Outcome: the local machine adopts the exact already-authoritative generation
held unanimously by every expected replica without changing head identity,
epoch, sequence, entry, bundle, state, membership, or lineage.

### 12.1 `prep` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Require `local-stale-network-agrees` |
| 2 | `MOTHER-OF-OBS-016` | Verify candidate schema and recovery capabilities |
| 3 | `MOTHER-OF-XPORT-001`, `MOTHER-OF-XPORT-002`, `MOTHER-OF-XPORT-004`, and `MOTHER-OF-XPORT-005` | Query every expected replica and distinguish transport from candidate evidence |
| 4 | `MOTHER-OF-CTL-008` | Reject conflicting adoption, mutation, recovery, reseal, or projection repair |
| 5 | `MOTHER-OF-SYNC-001` | Acquire exclusive local-adoption scope |
| 6 | `MOTHER-OF-SYNC-002` | Pin exact old local pointer/head and unanimous remote candidate |
| 7 | `MOTHER-OF-CTL-011`, `MOTHER-OF-CTL-012`, `MOTHER-OF-CTL-014` | Persist adoption plan and enter `sync-prepared` |

### 12.2 `do` functionalities

1. `MOTHER-OF-CTL-013` revalidates the local prestate and frozen remote
   candidate.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern candidate
   download and replica revalidation requests.
3. `MOTHER-OF-SYNC-003` downloads the complete immutable recovery closure into
   staging.
4. `MOTHER-OF-SYNC-004` verifies journals, checkpoints, private state, pending
   action, rollback closure, and derived projections.
5. `MOTHER-OF-ID-005` verifies the complete adopted private-recovery closure.
6. `MOTHER-OF-CTL-014` enters `sync-ready-to-activate` only after every staged
   byte and directory is durable.

### 12.3 `finalize` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | `MOTHER-OF-CTL-013` | Reprove old pointer, scope ownership, and unanimous candidate |
| 1a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Requery the frozen replicas using durable request/result semantics |
| 2 | `MOTHER-OF-SYNC-004` | Reverify complete staged generation |
| 3 | `MOTHER-OF-SYNC-005` | Persist activation-prepared evidence independently of the generation tree |
| 4 | `MOTHER-OF-SYNC-006` | Atomically switch and flush active-generation pointer |
| 5 | `MOTHER-OF-SYNC-007` | Reconcile operation state from the durable pointer |
| 6 | `MOTHER-OF-CTL-016` | Release local-adoption scope in `sync-committed` |

### 12.4 `rollback` functionalities

Before pointer activation:

1. `MOTHER-OF-SYNC-008` discards the staged candidate;
2. `MOTHER-OF-CTL-014` records `sync-rolled-back`;
3. `MOTHER-OF-CTL-016` releases local-adoption scope.

After pointer activation, rollback is closed. `MOTHER-OF-SYNC-007` completes
forward. `sync-state` terminates in `sync-committed`; it does not enter
`finalized-replication-pending`.

## 13. Operation: `recover-head`

Operation ID: `MOTHER-OP-RECOVER-HEAD`

Class: replacement-local-head authority recovery

Outcome: a lost or unprovable local Mother state root is reconstructed from one
unanimous compatible replica lineage and activated under a new head identity and
epoch.

### 13.1 `prep` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | `MOTHER-OF-REC-001` | Resolve descriptor and discover expected replicas |
| 2 | `MOTHER-OF-XPORT-001`, `MOTHER-OF-XPORT-002`, `MOTHER-OF-XPORT-004`, and `MOTHER-OF-XPORT-005` | Query every descriptor participant through approved private status paths |
| 3 | `MOTHER-OF-OBS-005` | Contact every expected replica |
| 4 | `MOTHER-OF-OBS-002`, `MOTHER-OF-OBS-003`, `MOTHER-OF-OBS-016` | Validate each reported lineage and compatibility |
| 5 | `MOTHER-OF-REC-002` | Prove exact agreement on lineage, state, pending action, private material, and recovery closure |
| 6 | `MOTHER-OF-CTL-007` and `MOTHER-OF-CTL-008` | Acquire exclusive recovery scope and reject conflicts |
| 7 | `MOTHER-OF-CTL-011` and `MOTHER-OF-CTL-012` | Freeze recovery candidate and publish allowed commands |

`prep` does not choose the first host, a majority, the newest timestamp, or the
highest generation.

### 13.2 `do` functionalities

1. `MOTHER-OF-CTL-013` revalidates the frozen replica reports.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern every recovery
   download and participant request.
3. `MOTHER-OF-REC-003` downloads and verifies every immutable object.
4. `MOTHER-OF-REC-004` restores canonical private state, metadata, recovery
   manifests, journals, checkpoints, operation state, rollback state, and head
   metadata.
5. `MOTHER-OF-ID-005` verifies recovered private state and private-recovery
   closure.
6. `MOTHER-OF-REC-005` replays every recovered journal and rebuilds projections.
7. `MOTHER-OF-REC-006` checks recovered authority against guards and live facts
   without rewriting the lineage.
8. `MOTHER-OF-CTL-014` records readiness for replacement-head activation.

An unfinished recovered action remains unfinished.

### 13.3 `finalize` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | `MOTHER-OF-REC-002` and `MOTHER-OF-REC-006` | Reprove full-set agreement, compatibility, and live assertions |
| 1a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern activation and acknowledgement requests |
| 2 | `MOTHER-OF-REC-007` | Create new head ID, increment epoch, and append replacement-head activation |
| 3 | `MOTHER-OF-REC-008` | Replicate activation and require every expected acknowledgement |
| 4 | `MOTHER-OF-CTL-016` | Release recovery scope and permit ordinary mutation |

### 13.4 Open functional boundary

`mother.md` defines staged recovery and the activation boundary but does not
fully specify the operator-visible abort or rollback contract before activation.
That boundary is `contract-open`.

Implementation MUST NOT infer that an unprovable old local state root is safe to
restore. The missing contract MUST distinguish at least:

- disposal of an unactivated staged candidate;
- preservation of immutable forensic evidence;
- handling of a locally restored but not yet authority-activated state root;
- scope release after safe abort;
- crash recovery before and after replacement-head activation.

## 14. Operation: `reseal-state`

Operation ID: `MOTHER-OP-RESEAL-STATE`

Class: authority-restoring reseal and rectification

Outcome: all reachable base-authority replicas accept one explicit replacement
authority checkpoint that displaces only identified divergent network heads and
preserves or carries every unresolved obligation.

### 14.1 `prep` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Establish divergence or wedged state |
| 2 | `MOTHER-OF-CTL-001` | Freeze selected predecessor, reason, and optional membership intent |
| 3 | `MOTHER-OF-XPORT-001`, `MOTHER-OF-XPORT-002`, `MOTHER-OF-XPORT-004`, and `MOTHER-OF-XPORT-005` | Query every base-authority replica through approved private paths |
| 4 | `MOTHER-OF-RSL-001` | Collect every base-authority report and invalid-head evidence |
| 5 | `MOTHER-OF-RSL-002` | Prove common authority base and selected replay-valid predecessor |
| 6 | `MOTHER-OF-RSL-003` | Build observed, valid, and narrowly superseded head sets |
| 7 | `MOTHER-OF-RSL-004` | Preserve, carry as remediation-required, or block on every obligation |
| 8 | `MOTHER-OF-ID-005` | Prove private state and recovery closure are preserved or explicitly carried |
| 9 | `MOTHER-OF-MEM-001` | Freeze the current, prospective, transition, desired, retained, retiring, and successor-authority sets when membership composition is requested |
| 10 | `MOTHER-OF-RSL-009` | Freeze the readiness contract: prospective participant set, expected generation identity, required recovery closure and schemas, readiness receipt contract/version, expected membership sets, and structural legality of D029+D028 composition |
| 11 | `MOTHER-OF-CTL-007` through `MOTHER-OF-CTL-010` | Acquire logical scopes and freeze rollback/capabilities before any prospective participant mutation |
| 12 | `MOTHER-OF-CTL-011` and `MOTHER-OF-CTL-012` | Record the frozen operation plan and legal next commands |

`prep` freezes the readiness contract, not a readiness result. It MUST NOT
create a prospective generation, transfer private recovery material, acquire a
prospective readiness fence, commit readiness receipts, construct the D029
prepared intent, or dispatch an authority-reset proposal.

Any unreachable base-authority replica, absent common base, absent
replay-valid predecessor, or structurally illegal membership composition blocks
the operation during `prep`.

### 14.2 `do` functionalities

| Order | Functionality | Placement |
|---:|---|---|
| 1 | `MOTHER-OF-CTL-013` | Revalidate base authority, reports, selected predecessor, obligations, frozen membership sets, readiness contract, and owned scopes |
| 1a | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern every staging, proposal, acceptance, and participant request |
| 2 | `MOTHER-OF-MEM-002` through `MOTHER-OF-MEM-006` | When inclusion is requested, stage prospective hosts, transfer the private/recovery closure, obtain readiness receipts, and commit the canonical prospective-readiness root |
| 3 | `MOTHER-OF-RSL-005` | Construct the prepared D029 intent from pre-entry facts, including the actual committed readiness root when D028 applies |
| 4 | `MOTHER-OF-RSL-006` | Construct exact successor authoritative checkpoint binding prepared-intent hash |
| 5 | `MOTHER-OF-RSL-007` | Construct proposal binding intent and successor entry; obtain unanimous proposal acceptance and D029 fences |
| 6 | `MOTHER-OF-RSL-008` | Construct the authority-reseal certificate from the full proposal-acceptance set |
| 7 | `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008` | When membership changes, obtain D028 transition acceptances for that exact D029 certificate and persist the D028 commit-in-progress decision |
| 8 | `MOTHER-OF-RSL-014` | Obtain full base-authority completed-certificate acceptances; membership-mode acceptances bind the exact D028 transition-acceptance root and transition-decision hash |
| 9 | `MOTHER-OF-CTL-014` | Enter ready-to-finalize with all future-object cycles excluded |

The successor checkpoint does not contain the proposal hash, certificate hash,
authorization-bundle hash, certificate-acceptance root, or post-entry membership
roots. For membership-changing D029+D028, completed-certificate acceptance MUST
not occur until the D028 transition-acceptance root and D028
commit-in-progress decision are durable.

### 14.3 `finalize` functionalities

1. `MOTHER-OF-CTL-013` revalidates proposal, certificate, selected predecessor,
   obligation disposition, completed-certificate acceptances, and optional D028
   transition evidence.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern final
   revalidation, replication, acknowledgement, activation, retirement, fence
   rollover, and release requests.
3. `MOTHER-OF-RSL-010` constructs and persists the authorization bundle after the
   D029 certificate-acceptance root and any D028 roots are durable.
4. `MOTHER-OF-RSL-011` atomically commits the entry-and-bundle head.
5. `MOTHER-OF-RSL-012` replicates, acknowledges, activates or retires any
   membership participants, proves D029 fence rollover, and completes reseal
   protocol ownership.
6. `MOTHER-OF-AUTH-016` reconciles ambiguous results from the durable local head.
7. `MOTHER-OF-CTL-016` releases logical mutation scopes and current-operation
   ownership only after terminal proof.

### 14.4 `rollback` functionalities

Before selecting a cancellation branch, the controller MUST reconcile ambiguous
proposal dispatch against every base-authority replica. Absence of a local
proposal acceptance is not proof that no remote acceptance exists.

Before the atomic entry-and-bundle pointer commit:

1. when prospective readiness exists and reconciliation proves that no
   base-authority replica accepted the D029 proposal, `MOTHER-OF-MEM-011`
   cancels and tombstones D028 readiness, readiness receipts, and readiness
   locks while preserving immutable evidence; no D029 cancellation certificate
   is required;
2. when any base-authority replica accepted the D029 proposal,
   `MOTHER-OF-RSL-013` prepares full-set D029 cancellation and proves that no
   base-authority replica accepted the completed D029 certificate;
3. for membership-changing D029+D028 after proposal acceptance, the active D029
   fence remains installed while the local D028 commit-in-progress decision is
   converted to cancellation-authorized using that exact D029
   cancellation-prepare certificate;
4. `MOTHER-OF-MEM-011` then completes the full D028 cancellation protocol,
   including terminal cancellation of every transition acceptance, readiness
   receipt, readiness lock, and prospective generation;
5. only after any composed D028 cancellation is terminal,
   `MOTHER-OF-RSL-015` commits or aborts D029 cancellation and tombstones the
   D029 proposal, certificate attempt, and authority fence;
6. if any completed-certificate acceptance exists, cancellation is prohibited
   and the exact operation completes forward;
7. `MOTHER-OF-RB-008` records cancellation and rollback evidence;
8. `MOTHER-OF-CTL-016` releases logical mutation scopes and current-operation
   ownership only after every required cancellation protocol is terminal.

No D026 successor, unrelated D029 proposal, operation-scope release, or
network-scope release MAY begin while prospective readiness, a D029 fence, D028
transition acceptance, or either cancellation protocol remains nonterminal.
After the pointer commit, rollback to a divergent lineage is prohibited and
`MOTHER-OF-RSL-011` completes forward.

## 15. Operation: ordinary replica enrollment

Operation ID: `MOTHER-OP-REPLICA-ENROLL`

Class: authoritative membership change

Status: `surface-open`

Outcome: a reachable prospective host becomes an active Mother replica through
ordinary non-divergent successor authority and the membership protocol.

This is the standalone form. Enrollment composed into `add-node` remains inside
`MOTHER-OP-ADD-NODE`.

### 15.1 `prep` functionalities

1. The applicable `MOTHER-OF-OBS-001`–`017` pipeline proves local-current,
   non-divergent authority.
2. `MOTHER-OF-CTL-001` freezes the exact host-inclusion intent.
3. `MOTHER-OF-CTL-003` proves all current replicas are clean and the prospective
   host is eligible.
4. `MOTHER-OF-MEM-001` freezes every membership set and hash.
5. `MOTHER-OF-CTL-004` freezes predecessor authority.
6. `MOTHER-OF-CTL-005` and `MOTHER-OF-CTL-006` calculate the membership-only
   successor and ordered pipeline.
7. `MOTHER-OF-CTL-007` through `MOTHER-OF-CTL-012` acquire scopes, freeze
   rollback, and record the operation.

### 15.2 `do` functionalities

1. `MOTHER-OF-CTL-013` revalidates current and prospective hosts.
2. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` govern every staging,
   authority, and participant request.
3. `MOTHER-OF-MEM-002` through `MOTHER-OF-MEM-006` stage, lock, transfer,
   verify, and accept readiness.
4. `MOTHER-OF-ID-005` verifies the copied private recovery material.
5. `MOTHER-OF-AUTH-004` constructs the exact pending-membership successor.
6. `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` obtain ordinary current-replica
   authority for that exact successor.
7. `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008` obtain prospective transition
   acceptance and persist the decision.
8. `MOTHER-OF-AUTH-005` constructs the authorization bundle.
9. `MOTHER-OF-AUTH-006` and `MOTHER-OF-AUTH-007` commit and replicate the
   membership pending action.
10. `MOTHER-OF-OBS-017` verifies unchanged node, validator, RPC, and Hub/FDB
   topology unless separately included in the prepared operation.

### 15.3 `finalize` and `rollback`

Finalize uses:

1. `MOTHER-OF-RB-009`;
2. `MOTHER-OF-AUTH-008`;
3. `MOTHER-OF-AUTH-009`;
4. `MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002`;
5. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005`;
6. `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008`;
7. `MOTHER-OF-AUTH-005`;
8. `MOTHER-OF-AUTH-010` through `MOTHER-OF-AUTH-013`;
9. `MOTHER-OF-MEM-009`;
10. `MOTHER-OF-AUTH-014` and `MOTHER-OF-AUTH-015`;
11. `MOTHER-OF-CTL-016`.

Rollback before local membership finalization uses:

1. `MOTHER-OF-AUTH-003`;
2. `MOTHER-OF-MEM-011`;
3. `MOTHER-OF-RB-008`;
4. `MOTHER-OF-CTL-016`.

After local membership finalization, rollback is closed and activation completes
forward.

## 16. Operation: ordinary replica retirement

Operation ID: `MOTHER-OP-REPLICA-RETIRE`

Class: authoritative membership change

Status: `surface-open`

Outcome: a reachable current replica participates in and acknowledges the exact
membership-changing successor, records retirement, and no longer belongs to the
active replica set.

### 16.1 Functional pipeline

| Stage | Functionality placement |
|---|---|
| `prep` | Diagnose; freeze exclusion intent; require retiring-host reachability; freeze current, transition, desired, retiring, and successor-authority sets; freeze predecessor; acquire scopes; define rollback; record operation |
| `do` | Revalidate all participants; obtain current full-set successor reservation and certificate; obtain transition acceptance from the retiring host; persist membership decision; commit and replicate pending membership action |
| `finalize` | Certify and commit exact finalization successor; replicate to every transition participant; collect full acknowledgement; invoke `MOTHER-OF-MEM-010`; release reservations and scopes |
| `rollback` | Cancel the uncommitted successor and membership decision; retain the host as a current replica; release only after full-set cancellation proof |

The concrete IDs are:

```text
prep:
  MOTHER-OF-OBS-001 through MOTHER-OF-OBS-017 as applicable
  MOTHER-OF-CTL-001
  MOTHER-OF-CTL-003 through MOTHER-OF-CTL-012
  MOTHER-OF-MEM-001

do:
  MOTHER-OF-CTL-013
  MOTHER-OF-XPORT-001 through MOTHER-OF-XPORT-005
  MOTHER-OF-AUTH-004
  MOTHER-OF-AUTH-001
  MOTHER-OF-AUTH-002
  MOTHER-OF-MEM-007
  MOTHER-OF-MEM-008
  MOTHER-OF-AUTH-005
  MOTHER-OF-AUTH-006
  MOTHER-OF-AUTH-007

finalize:
  MOTHER-OF-RB-009
  MOTHER-OF-AUTH-008
  MOTHER-OF-AUTH-009
  MOTHER-OF-AUTH-001
  MOTHER-OF-AUTH-002
  MOTHER-OF-XPORT-001 through MOTHER-OF-XPORT-005
  MOTHER-OF-MEM-007
  MOTHER-OF-MEM-008
  MOTHER-OF-AUTH-005
  MOTHER-OF-AUTH-010 through MOTHER-OF-AUTH-013
  MOTHER-OF-MEM-010
  MOTHER-OF-AUTH-014
  MOTHER-OF-AUTH-015
  MOTHER-OF-CTL-016

rollback:
  MOTHER-OF-AUTH-003
  MOTHER-OF-MEM-011
  MOTHER-OF-RB-008
  MOTHER-OF-CTL-016
```

An unreachable current replica blocks retirement. Removing its last node does
not satisfy any retirement functionality. Retirement does not prove that copied
private material was erased. Loss of trust invokes
`MOTHER-OP-IDENTITY-ROTATION`.

## 17. Operation: schema migration

Operation ID: `MOTHER-OP-SCHEMA-MIGRATION`

Class: authoritative migration

Status: `contract-open`

Outcome: durable state is transformed from explicitly declared source schemas
to explicitly declared destination schemas while preserving original evidence
and full-set replayability.

### 17.1 Required functional pipeline

| Stage | Order | Functionality | Defined result |
|---|---:|---|---|
| `prep` | 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Current authority and active obligations known |
| `prep` | 2 | `MOTHER-OF-MIG-001` | Exact source/destination schema and capability inventory |
| `prep` | 3 | `MOTHER-OF-MIG-002` | Immutable audit closure of original bytes and hashes |
| `prep` | 4 | `MOTHER-OF-CTL-007` through `MOTHER-OF-CTL-012` | Exclusive scopes, rollback requirements, and operation record |
| `do` | 1 | `MOTHER-OF-MIG-003` | Deterministically transformed candidate |
| `do` | 2 | `MOTHER-OF-MIG-004` | Complete candidate validation |
| `do` | 3 | `MOTHER-OF-MIG-005` | Migrated checkpoint or complete state object |
| `do` | 4 | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern every full-set migration request |
| `do` | 5 | `MOTHER-OF-MIG-006` | Full-set replicated and verified candidate |
| `finalize` | 1 | `MOTHER-OF-MIG-007` | Committed migrated authority |
| `rollback` | 1 | `MOTHER-OF-MIG-008` | Staging canceled or original authoritative bytes restored before commit |

### 17.2 Missing contract

Before implementation, the governing requirements MUST still define:

- the public operation name and arguments;
- whether migration commits through an ordinary entry-and-bundle successor, a
  generation pointer, or another explicit authority transaction;
- exact current and transition participant sets;
- how mixed-version readers behave during staging;
- the migration-specific irreversible boundary;
- exact rollback and crash-reconciliation states;
- whether private-state schema migration composes with the same transaction.

Migration cannot be treated as implemented until `MOTHER-OF-MIG-007` is fully
specified. It MUST NOT run implicitly during startup, diagnosis, replay,
`sync-state`, or `recover-head`.

## 18. Operation: identity or secret rotation

Operation ID: `MOTHER-OP-IDENTITY-ROTATION`

Class: authoritative corrective action

Status: `contract-open`

Outcome: exposed or explicitly selected private identity material is replaced,
all dependencies are rebound, and superseded material is retired without
pretending remote copies were erased.

### 18.1 Required functional pipeline

| Stage | Order | Functionality | Required purpose |
|---|---:|---|---|
| `prep` | 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Establish authority, topology, and active obligations |
| `prep` | 2 | `MOTHER-OF-ROT-001` | Freeze exact identities, secrets, exposure reason, and affected hosts |
| `prep` | 3 | `MOTHER-OF-ROT-002` | Build complete dependency graph |
| `prep` | 4 | `MOTHER-OF-ROT-003` | Observe dependencies and declare the exact later prestate capture |
| `prep` | 5 | `MOTHER-OF-CTL-007` through `MOTHER-OF-CTL-012` | Acquire scopes, freeze rollback, and record intent |
| `do` | 1 | `MOTHER-OF-ROT-011` | Capture complete current private and dependent public prestate |
| `do` | 2 | `MOTHER-OF-ROT-004` | Generate or reserve replacement material |
| `do` | 3 | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern every remote distribution and rebinding request |
| `do` | 4 | `MOTHER-OF-ROT-005` | Distribute and install replacement material |
| `do` | 5 | `MOTHER-OF-ROT-006` | Rebind dependent services, validators, contracts, routes, or governance |
| `do` | 6 | `MOTHER-OF-ROT-008` | Verify replacement and dependency closure |
| `finalize` | 1 | `MOTHER-OF-ROT-007` | Retire superseded credentials without asserting erasure |
| `finalize` | 2 | `MOTHER-OF-ROT-009` | Commit rotation authority and close rollback |
| `rollback` | 1 | `MOTHER-OF-ROT-010` | Restore captured bindings before the irreversible boundary |

### 18.2 Missing contract

`mother.md` requires a separate explicit rotation action but does not yet define:

- identity classes that can be rotated together;
- dependency ordering for validator, wallet, contract, governance, and service
  identities;
- whether a born network MAY rotate genesis-bound material;
- private-state versioning and replication during rotation;
- dual-key transition behavior;
- revocation evidence;
- operation-specific rollback limitations after replacement material has been
  disclosed;
- the exact authoritative commit boundary.

The listed rotation functionalities are required placeholders, not permission
for an implementation to invent these semantics.

## 19. Operation: `repair-projections`

Operation ID: `MOTHER-OP-REPAIR-PROJECTIONS`

Class: non-authoritative derived-local maintenance

Status: `surface-open` for CLI spelling

Outcome: one complete projection generation derived from the unchanged pinned
local authority is atomically published.

### 19.1 One-shot functional pipeline

| Order | Functionality | Placement |
|---:|---|---|
| 1 | Applicable `MOTHER-OF-OBS-001`–`017` pipeline | Prove authority agrees and damage is projection-only |
| 2 | `MOTHER-OF-CTL-008` | Reject active adoption, recovery, reseal, or conflicting work |
| 3 | `MOTHER-OF-PRJ-001` | Pin journal identity, sequence, entry, bundle, state, head ID, and epoch |
| 4 | `MOTHER-OF-PRJ-002` | Replay exact lineage into a temporary immutable generation |
| 5 | `MOTHER-OF-PRJ-003` | Manifest, hash, verify, and flush every projection |
| 6 | `MOTHER-OF-PRJ-004` | Re-read and compare the complete head tuple |
| 7 | `MOTHER-OF-PRJ-005` | Atomically publish one generation pointer and flush metadata |
| 8 | `MOTHER-OF-PRJ-006` | Discard stale output or stop after bounded retries |

There is no `prep`/`do`/`finalize` operation ledger and no rollback stack.
Before publication, temporary output is disposable. After publication, the new
generation is complete and derived from the same authoritative head.

No functionality in this operation MAY change journal entries, bundles, heads,
private state, head authority, topology, pending actions, rollback rights, or
remote lineage.

## 20. Lifecycle control: `rollback`

Control ID: `MOTHER-CTL-ROLLBACK`

Class: operation control

Outcome: the active operation returns to its proven pre-operation state, or
remains explicitly nonterminal with exact unresolved evidence.

### 20.1 Functional pipeline

| Order | Functionality | Placement |
|---:|---|---|
| 1 | `MOTHER-OF-OBS-011` | Resolve the active operation and owner |
| 2 | `MOTHER-OF-AUTH-016` | Determine whether the irreversible local commit already occurred |
| 3 | `MOTHER-OF-OBS-012` | Resolve provisional frame and requested pop range |
| 4 | `MOTHER-OF-OBS-016` | Verify restore capabilities and schemas |
| 5 | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Govern every distributed cancellation and restore request |
| 6 | `MOTHER-OF-AUTH-003`, `MOTHER-OF-MEM-011`, `MOTHER-OF-RSL-013`, or `MOTHER-OF-RSL-015` | Cancel the exact uncommitted authority protocol at the applicable prepare or terminal-commit boundary |
| 7 | `MOTHER-OF-RB-005` | Restore or reconcile the provisional frame |
| 8 | `MOTHER-OF-RB-006` | Pop selected promoted frames in strict LIFO order |
| 9 | Operation-specific restore functionality | Restore QBFT, RPC, Hub/FDB, service, membership, or staged state |
| 10 | `MOTHER-OF-RB-007` | Verify complete restored prestate and invariants |
| 11 | `MOTHER-OF-AUTH-018` | Construct exact rollback-progress or rollback-completed successor when a pending-action head exists |
| 12 | `MOTHER-OF-AUTH-001`, `MOTHER-OF-AUTH-002`, conditional `MOTHER-OF-MEM-007` and `MOTHER-OF-MEM-008`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-006`, and `MOTHER-OF-AUTH-007` | Certify, commit, and replicate the verified compensating state |
| 13 | `MOTHER-OF-RB-008` | Persist every rollback result or failure |
| 14 | `MOTHER-OF-RB-010` and `MOTHER-OF-CTL-016` | Release rollback and operation scopes after full-set terminal proof |

### 20.2 Range semantics

| Operator form | Functional selection |
|---|---|
| `--all` | Resolve provisional frame, then restore every promoted frame |
| `--count <n>` | Resolve provisional frame, then restore the newest `n` promoted frames |
| `--through <layer-id>` | Resolve provisional frame, then restore through the named promoted layer |

Rollback MUST refuse when the active local head proves the operation's
irreversible commit. A failed restore remains `rollback-failed` or
`remediation-required`; the stack item is not erased.

If the requested range is partial, the certified rollback-progress successor
keeps the pending action open and the operation remains
`remediation-required`. Only `--all`, with no provisional or promoted frames
remaining and the exact rolled-back head proven on every expected replica, MAY
enter `rolled-back` and release the successor reservation.

## 21. Lifecycle control: retry/resume

Control ID: `MOTHER-CTL-RETRY-RESUME`

Class: operation control

Outcome: the exact existing operation advances without new intent, new scope,
new prestate, or a second competing successor.

### 21.1 `do` retry functionalities

1. `MOTHER-OF-OBS-011` resolves the exact active operation.
2. `MOTHER-OF-CTL-015` restores the original request and idempotency identity.
3. `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` reconcile every remote
   request from durable target state rather than transport output.
4. `MOTHER-OF-AUTH-016` reconciles durable authority and claim state.
5. `MOTHER-OF-CTL-013` revalidates frozen preconditions.
6. `MOTHER-OF-RB-005` inspects any unresolved provisional frame.
7. If desired poststate already holds, `MOTHER-OF-RB-004` freshly verifies and
   promotes it.
8. If a recognized partial state holds, the same prepared mutation is retried
   with the same frame.
9. If neither prestate nor recognized partial/desired state can be proven, retry
   refuses and reports rectification.

### 21.2 `finalize` retry functionalities

Before local commit:

- reuse `MOTHER-OF-AUTH-008` and the same exact
  `MOTHER-OF-AUTH-009` successor;
- cancel through `MOTHER-OF-AUTH-003` only when durable evidence permits;
- retain rollback until cancellation is terminal.

After local commit:

- use `MOTHER-OF-AUTH-016` to prove the exact committed successor;
- use `MOTHER-OF-AUTH-011` through `MOTHER-OF-AUTH-015` to resynchronize,
  acknowledge, activate or retire, and release D026 reservations and
  authority-protocol fencing;
- never reopen rollback or create an opposite successor.

## 22. Shared staged-operation functionality

The following placement applies to every ordinary authoritative staged
operation unless its operation section defines a stricter specialized path.

### 22.1 Common `prep`

```text
diagnose
→ interpret intent
→ validate options
→ clean-state barrier
→ freeze authority and participants
→ calculate desired state
→ order functionalities
→ acquire scopes
→ define rollback
→ freeze compatibility
→ write prepared operation
→ publish allowed commands
```

Canonical IDs:

```text
MOTHER-OF-OBS-001 through MOTHER-OF-OBS-017 as applicable
MOTHER-OF-CTL-001 through MOTHER-OF-CTL-012
MOTHER-OF-MEM-001 when membership MAY change
```

### 22.2 Common pending-action opening

Every ordinary authoritative operation MUST establish its pending action in this
acyclic order before live mutation:

```text
construct exact successor entry
→ acquire exact full-set reservation
→ construct full-set certificate
→ obtain prospective transition acceptance when applicable
→ persist membership decision when applicable
→ build authorization bundle from the entry and post-entry evidence
→ atomically commit the local entry-and-bundle head
→ replicate and verify the exact authoritative head
```

Canonical IDs:

```text
MOTHER-OF-AUTH-004
MOTHER-OF-AUTH-001
MOTHER-OF-AUTH-002
MOTHER-OF-MEM-007 when applicable
MOTHER-OF-MEM-008 when applicable
MOTHER-OF-AUTH-005
MOTHER-OF-AUTH-006
MOTHER-OF-AUTH-007
```

For true network birth, `MOTHER-OF-AUTH-017` replaces the ordinary
`MOTHER-OF-AUTH-001` and `MOTHER-OF-AUTH-002` authority path for the first
entry. The first entry still exists and is hashed before bootstrap authority is
certified.

No entry MAY contain a hash of a certificate, acceptance root, decision record,
or authorization bundle that can exist only after that entry is hashed.

### 22.3 Common live mutation step

```text
capture complete typed prestate
→ persist armed provisional frame
→ checkpoint
→ dispatch exact prepared mutation
→ verify complete postcondition
→ promote frame
```

Canonical IDs:

```text
MOTHER-OF-RB-001
MOTHER-OF-RB-002
MOTHER-OF-RB-003
operation-specific mutation functionality
operation-specific verification functionality
MOTHER-OF-RB-004
```

After a meaningful phase is verified and promoted, the operation commits its
new reversible pending state before starting the next phase:

```text
MOTHER-OF-AUTH-019
→ MOTHER-OF-AUTH-001
→ MOTHER-OF-AUTH-002
→ MOTHER-OF-AUTH-005
→ MOTHER-OF-AUTH-006
→ MOTHER-OF-AUTH-007
```

This successor advances replicated pending state; it does not advance finalized
topology or close rollback.

Older script-boundary phase lists for service, validator, RPC, and Hub/FDB work
execute inside this common pending-action authority shell. They are not
standalone authority paths and MUST NOT bypass the pending-action opening,
successor fencing, rollback-frame, or finalization contracts.

No live mutation functionality MAY run before the pending action is
authoritative and replicated to the required set.

### 22.4 Common ordinary finalization

```text
revalidate
→ verify operation postconditions
→ close rollback frames
→ persist finalization intent
→ construct exact successor
→ obtain full-set certificate
→ bind membership evidence when applicable
→ build authorization bundle
→ atomically commit local final head
→ close rollback
→ replicate exact head
→ collect acknowledgements
→ build acknowledgement certificate
→ activate or retire membership
→ release reservations and scopes
```

Canonical IDs:

```text
MOTHER-OF-CTL-013
MOTHER-OF-OBS-017
MOTHER-OF-XPORT-001 through MOTHER-OF-XPORT-005
MOTHER-OF-RB-009
MOTHER-OF-AUTH-008
MOTHER-OF-AUTH-009
MOTHER-OF-AUTH-001
MOTHER-OF-AUTH-002
MOTHER-OF-MEM-007 when applicable
MOTHER-OF-MEM-008 when applicable
MOTHER-OF-AUTH-005
MOTHER-OF-AUTH-010
MOTHER-OF-AUTH-011
MOTHER-OF-AUTH-012
MOTHER-OF-AUTH-013
MOTHER-OF-MEM-009 or MOTHER-OF-MEM-010 when applicable
MOTHER-OF-AUTH-014 when membership changes
MOTHER-OF-AUTH-015
MOTHER-OF-RB-010
MOTHER-OF-CTL-016
```

### 22.5 State-transition functionality

`MOTHER-OF-CTL-014` owns durable operation-stage changes, but it MAY publish a
state only after the functionality that establishes that state is durable:

| Established evidence | Published state |
|---|---|
| Prepared record and scope ownership | `prepared` |
| Exact successor acquisition started | `reserving-successor` |
| Partial or split acquisition evidence | `reservation-incomplete` |
| Authoritative pending action committed and live execution begun | `doing` |
| Unverified or failed live frame | `remediation-required` |
| Every live functionality verified and promoted | `do-complete-pending-finalize` |
| Finalization-prepared evidence durable | `finalizing` |
| Pre-commit finalization attempt safely canceled or failed | `finalize-failed` |
| Exact final entry-and-bundle head locally committed | `finalized-replication-pending` |
| Full acknowledgement and terminal release proven | `finalized` |
| Rollback begun | `rolling-back` |
| Restoration incomplete or unverified | `rollback-failed` |
| Complete prestate restoration and release proven | `rolled-back` |

`MOTHER-OF-AUTH-016` supplies the durable head-status proof used when an
interruption makes the apparent stage ambiguous.

`sync-state` uses the same `MOTHER-OF-CTL-014` capability with its specialized
states and pointer-determined commit semantics.

## 23. Operation-to-functionality domain matrix

This matrix is a coverage check. Detailed ordering remains in the operation
sections.

| Operation | Observe | Plan/control | Transport | Authority | Rollback | Membership | Identity/service | QBFT | RPC | Hub/FDB | Adoption/recovery | Migration/rotation | Projection |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `diagnose` | ✓ | — | read | — | inspect | inspect | inspect | inspect | inspect | inspect | inspect | inspect | inspect |
| `plan` | ✓ | ✓ | read | — | plan | plan | plan | plan | plan | plan | plan | plan | plan |
| evidence export | ✓ | — | read | — | evidence | evidence | evidence | evidence | evidence | evidence | evidence | evidence | evidence |
| `add-node` | ✓ | ✓ | ✓ | ✓ | ✓ | conditional | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `remove-node` | ✓ | ✓ | ✓ | ✓ | ✓ | unchanged | ✓ | ✓ | ✓ | ✓ | — | — | — |
| `restore-service` | ✓ | ✓ | ✓ | ✓ | ✓ | unchanged | ✓ | verify | verify | verify | — | — | — |
| `reseal-qbft` | ✓ | ✓ | ✓ | ✓ | ✓ | unchanged | verify | ✓ | verify | verify | — | — | — |
| `rpc-propagate` | ✓ | ✓ | ✓ | ✓ | ✓ | unchanged | verify | verify | ✓ | verify | — | — | — |
| `sync-state` | ✓ | ✓ | ✓ | preserve | specialized | unchanged | adopted | adopted | adopted | adopted | ✓ | — | rebuild |
| `recover-head` | ✓ | ✓ | ✓ | replacement | open before activation | preserve | recover | recover | recover | recover | ✓ | — | rebuild |
| `reseal-state` | ✓ | ✓ | ✓ | reseal | pre-commit | conditional | preserve | preserve | preserve | preserve | ✓ | — | rebuild |
| replica enroll | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | recovery transfer | unchanged | unchanged | unchanged | — | — | — |
| replica retire | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | unchanged | unchanged | unchanged | unchanged | — | — | — |
| schema migration | ✓ | ✓ | ✓ | open | open | preserve | migrate if scoped | migrate if scoped | migrate if scoped | migrate if scoped | — | ✓ | rebuild |
| identity rotation | ✓ | ✓ | ✓ | open | open | preserve/change only if specified | ✓ | conditional | conditional | conditional | — | ✓ | rebuild |
| `repair-projections` | ✓ | conflict check | none | none | disposable staging | none | none | none | none | none | none | — | ✓ |
| `rollback` | inspect | control | ✓ | cancel only | ✓ | conditional | conditional | conditional | conditional | conditional | specialized | conditional | staged cleanup |
| retry/resume | inspect | control | ✓ | reuse exact | preserve | preserve | preserve | preserve | preserve | preserve | specialized | preserve | preserve |

## 24. Requirement-to-functionality coverage

This table proves that the top-level requirement index in `mother.md` reaches
the operation/functionality layer. It is a traceability map, not a replacement
for the owning requirement text.

| Requirement | Primary functionality coverage | Primary operation coverage |
|---|---|---|
| `MOTHER-REQ-001` | Document-wide normative language and functional status rules | All operations |
| `MOTHER-REQ-002` | `MOTHER-OF-OBS-001` through `MOTHER-OF-OBS-018` | `diagnose`, `plan`, evidence export |
| `MOTHER-REQ-003` | `MOTHER-OF-ID-001` through `MOTHER-OF-ID-005`, `MOTHER-OF-REC-004` | Node lifecycle, `sync-state`, `recover-head`, `reseal-state` |
| `MOTHER-REQ-004` | `MOTHER-OF-OBS-001` through `MOTHER-OF-OBS-004`, `MOTHER-OF-REC-005`, `MOTHER-OF-PRJ-002` | All operations that read authority |
| `MOTHER-REQ-005` | `MOTHER-OF-AUTH-004` through `MOTHER-OF-AUTH-010`, `MOTHER-OF-RSL-006` through `MOTHER-OF-RSL-011` | All authoritative staged operations |
| `MOTHER-REQ-006` | `MOTHER-OF-CTL-007`, `MOTHER-OF-CTL-008`, `MOTHER-OF-AUTH-001` | All authoritative staged operations |
| `MOTHER-REQ-007` | `MOTHER-OF-RB-009`, `MOTHER-OF-AUTH-005`, `MOTHER-OF-AUTH-008` through `MOTHER-OF-AUTH-010` | Ordinary finalization and `reseal-state` |
| `MOTHER-REQ-008` | `MOTHER-OF-CTL-003`, `MOTHER-OF-MEM-002` through `MOTHER-OF-MEM-008` | `add-node`, replica enrollment, network birth |
| `MOTHER-REQ-009` | `MOTHER-OF-RB-001` through `MOTHER-OF-RB-010`, `MOTHER-OF-AUTH-018`, `MOTHER-OF-AUTH-019` | Every reversible staged operation and `rollback` |
| `MOTHER-REQ-010` | `MOTHER-OF-RPC-001` through `MOTHER-OF-RPC-005` | `add-node`, `remove-node`, `rpc-propagate` |
| `MOTHER-REQ-011` | `MOTHER-OF-HUB-001` through `MOTHER-OF-HUB-005` | `add-node`, `remove-node` |
| `MOTHER-REQ-012` | Complete `MOTHER-OP-ADD-NODE` pipeline | `add-node` |
| `MOTHER-REQ-013` | Complete `MOTHER-OP-REMOVE-NODE` pipeline | `remove-node` |
| `MOTHER-REQ-014` | `MOTHER-OF-QBFT-001` through `MOTHER-OF-QBFT-007` | `add-node`, `remove-node`, `reseal-qbft` |
| `MOTHER-REQ-015` | `MOTHER-OF-OBS-016`, `MOTHER-OF-CTL-010` | Every staged operation, retry, rollback, and recovery |
| `MOTHER-REQ-016` | `MOTHER-OF-ID-005`, `MOTHER-OF-SYNC-003`, `MOTHER-OF-SYNC-004`, `MOTHER-OF-REC-002` through `MOTHER-OF-REC-005` | Enrollment, `sync-state`, `recover-head`, `reseal-state` |
| `MOTHER-REQ-017` | `MOTHER-OF-REC-001` through `MOTHER-OF-REC-008` | `recover-head` |
| `MOTHER-REQ-018` | `MOTHER-OF-SYNC-001` through `MOTHER-OF-SYNC-008` | `sync-state` |
| `MOTHER-REQ-019` | `MOTHER-OF-PRJ-001` through `MOTHER-OF-PRJ-006` | `repair-projections` |
| `MOTHER-REQ-020` | `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Every operation that contacts remote participants |
| `MOTHER-REQ-021` | `MOTHER-OF-CTL-007`, `MOTHER-OF-CTL-008`, `MOTHER-OF-CTL-016` | Every scoped staged operation |
| `MOTHER-REQ-022` | `MOTHER-OF-RB-009`, `MOTHER-OF-AUTH-008` through `MOTHER-OF-AUTH-010` | Every ordinary authoritative finalization |
| `MOTHER-REQ-023` | `MOTHER-OF-AUTH-001` through `MOTHER-OF-AUTH-007`, `MOTHER-OF-AUTH-011` through `MOTHER-OF-AUTH-019` | Every ordinary successor, retry, cancellation, rollback, and release |
| `MOTHER-REQ-024` | `MOTHER-OF-MEM-001` through `MOTHER-OF-MEM-014`, `MOTHER-OF-AUTH-017` | Enrollment, retirement, `add-node initial`, zero-validator continuity |
| `MOTHER-REQ-025` | `MOTHER-OF-AUTH-010` through `MOTHER-OF-AUTH-015`, `MOTHER-OF-XPORT-001` through `MOTHER-OF-XPORT-005` | Every post-commit finalization completion |
| `MOTHER-REQ-026` | `MOTHER-OF-RSL-001` through `MOTHER-OF-RSL-015`, `MOTHER-OF-AUTH-008` through `MOTHER-OF-AUTH-015`, `MOTHER-OF-MEM-002`, `MOTHER-OF-MEM-003` when membership changes | `reseal-state` authority restoration |

## 25. Functional coverage and acceptance gaps

### 25.1 Surface-open items

The functionality is defined, but the public interface remains to be frozen:

| Gap ID | Operation | Missing surface |
|---|---|---|
| `MOTHER-OF-GAP-001` | `plan` | Whether a standalone command is public |
| `MOTHER-OF-GAP-002` | evidence export | Command name, selection filters, and package format |
| `MOTHER-OF-GAP-003` | `restore-service` | Complete options and saved-service selector |
| `MOTHER-OF-GAP-004` | `rpc-propagate` | Complete CLI options and target selection |
| `MOTHER-OF-GAP-005` | replica enrollment | Canonical standalone operation name and options |
| `MOTHER-OF-GAP-006` | replica retirement | Canonical standalone operation name and options |
| `MOTHER-OF-GAP-007` | `repair-projections` | Canonical CLI spelling |

### 25.2 Contract-open items

These are not merely naming gaps:

| Gap ID | Operation | Missing functional contract |
|---|---|---|
| `MOTHER-OF-GAP-008` | `recover-head` | Safe abort/rollback and crash states before activation |
| `MOTHER-OF-GAP-009` | schema migration | Authority transaction, commit boundary, rollback, mixed-version behavior, and private-state composition |
| `MOTHER-OF-GAP-010` | identity/secret rotation | Identity classes, dependency ordering, transition model, replication, revocation, rollback, and commit boundary |

An implementation MUST NOT mark an affected operation complete by selecting
convenient behavior for a `contract-open` item.

## 26. Traceability and acceptance requirements

Every implementation unit and test MUST be traceable through:

```text
mother.md requirement or design contract
  → mother-o.md operation
    → mother-o-f.md functionality
      → mother-o-f-m.md module and public seam
        → traced contract test
          → implementation unit
            → retained execution evidence
```

Contract tests are executable verification of the documented module contract.
They MUST NOT become an independent requirements source. When a test needs an
answer that the governing documents do not provide, work stops and the highest
affected `mother*.md` contract is corrected before the test or implementation
continues.

For every functionality occurrence in an operation, acceptance evidence MUST
prove:

1. the functionality ran in the correct stage;
2. all declared predecessor functionalities completed;
3. inputs matched the frozen operation record;
4. output and postconditions were verified independently of transport success;
5. authority did not advance before its documented boundary;
6. prestate and rollback evidence existed before reversible mutation;
7. failure produced the correct operation state and allowed commands;
8. retry reused the same operation, request identity, and rollback frame;
9. finalization or rollback released scopes only after terminal proof;
10. no functionality belonging to another operation was invoked implicitly.

## 27. Implementation rule

`mother-o-f-m.md` is the canonical source for module layout, package paths, and
public module seams. This document remains canonical for operation, stage, and
functionality placement.

An implementation MAY group private helpers differently inside the module
boundaries defined by `mother-o-f-m.md`, but it MUST NOT move a declared module
path, change a public seam, or reorder this functional composition without
updating `mother-o-f-m.md` and the traced contract tests that verify it.

No API registry participates in this authority chain. A future interface
inventory MAY exist only as a disposable derived report and MUST NOT override
the governing documents, traced tests, or implemented public contracts.

One module MAY implement several functionalities. One functionality MAY span
several modules or APIs only as declared by `mother-o-f-m.md`. Neither fact changes:

- which operation owns the functionality;
- which stage MAY invoke it;
- its ordering dependencies;
- its authority class;
- its prestate and rollback requirements;
- its verification contract;
- its test obligations.

The operation is the unit of user intent and authority ownership. The
functionality is the unit of composable, testable behavior.
